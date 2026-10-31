"""
The IC Conversion workbook's cells, laid out from the conversion JSON, and its formulas evaluated in
Python.

Why it exists: Jim's chat writes <Job>_IC_Conversion.xlsx beside the JSON, and his BUDGET.md says the
workbook renders the JSON and is not the truth. Measured on the pairs (2026-10-31), 23 of the 27
current workbooks are his shared writer's format: four sheets, Parts (mm), Material Takeoff, Verticals
and PULLED classified, with the same headers and labels, and three formulas, the SUMs of the Parts
total row. This lays out the same cells from the JSON conversion.py makes, in NG Runner's own code;
none of his is copied.

Checked in Python, never in LibreOffice (design: "workbook checked in Python, with no LibreOffice on
the PC"). Every formula written here is evaluated here (evaluate), and so are his: his files are saved
by openpyxl with no cached values, so a total in one of them has a value only once evaluated. The
evaluator takes the formulas his workbooks hold, and anything else is Unevaluable, never a number.
named() reads a book's named cells, ours or his, by their labels and headers: that is what is
compared, never bytes.

Never a guess: a line the takeoff could not verify is never a Parts row. It is listed under COULD NOT
VERIFY, marked so on PULLED classified, and counted on a NOT COUNTED row under the total. A bucket with
no sheet size says "could not verify" where the sheets would be. What the converter doesn't make (the
verticals, the runs, the client's details) says so, or stays blank.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

PARTS, TAKEOFF, VERTICALS, PULLED = "Parts (mm)", "Material Takeoff", "Verticals", "PULLED classified"
PART_HEADERS = ("Qty", "Type", "Grain", "Design", "Wall", "IC description", "Material", "Mount",
                "Radius", "IC a (in)", "IC b (in)", "IC a (mm)", "IC b (mm)", "BUILT a (mm)",
                "BUILT b (mm)", "BUILT a (imp)", "BUILT b (imp)", "SqFt", "EB LF", "Review")
PART_WIDTHS = (5, 13, 6, 30, 5, 44, 22, 6, 6, 8, 8, 9, 9, 10, 10, 10, 10, 8, 8, 40)
VERTICAL_HEADERS = ("Design", "Wall", "#", "Mount", "IC line", "Product", "Height mm", "Depth mm",
                    "Elev", "X mm (left face)", "X imp")
PULLED_HEADERS = ("Section", "Qty", "Item #", "Description", "Bucket", "Family", "Note")
RED = "C00000"
COULD_NOT_VERIFY = "could not verify"
UNPLACED_HEADER = "COULD NOT VERIFY (never filled in)"
NOT_MADE_VERTICALS = "not made by ng_convert: the verticals come from the drawings"
TOTAL_LABEL = "TOTAL BOARD PARTS"


# ----- the cells


@dataclass(frozen=True)
class Formula:
    """A formula as Excel stores it, with its leading =."""

    text: str


@dataclass(frozen=True)
class Cell:
    value: Any = None
    bold: bool = False
    colour: str | None = None          # the font's colour, RRGGBB


@dataclass
class Sheet:
    title: str
    cells: dict[tuple[int, int], Cell] = field(default_factory=dict)
    widths: dict[str, float] = field(default_factory=dict)

    def put(self, row: int, col: int, value: Any, *, bold: bool = False, colour: str | None = None) -> None:
        if value == "":
            value = None                    # an .xlsx keeps no empty string: the cell is blank
        if value is None and not bold and colour is None:
            self.cells.pop((row, col), None)
        else:
            self.cells[(row, col)] = Cell(value, bold, colour)

    def append(self, values: Sequence[Any], *, bold: bool = False) -> int:
        row = self.max_row + 1
        for col, value in enumerate(values, 1):
            if value is not None:
                self.put(row, col, value, bold=bold)
        if not any(v is not None for v in values):
            self.cells.setdefault((row, 1), Cell(None))     # a blank row still takes its place
        return row

    def cell(self, row: int, col: int) -> Cell:
        return self.cells.get((row, col), Cell())

    def value(self, row: int, col: int) -> Any:
        return self.cell(row, col).value

    @property
    def max_row(self) -> int:
        return max((r for r, _ in self.cells), default=0)

    @property
    def max_column(self) -> int:
        return max((c for _, c in self.cells), default=0)


@dataclass
class Book:
    sheets: list[Sheet] = field(default_factory=list)

    def sheet(self, title: str) -> Sheet:
        for sheet in self.sheets:
            if sheet.title == title:
                return sheet
        raise KeyError(title)

    def has(self, title: str) -> bool:
        return any(s.title == title for s in self.sheets)


def dump(book: Book) -> dict[str, Any]:
    """The book as JSON-ready data, so another Python (the tools', with openpyxl) can save it."""
    def value(v: Any) -> Any:
        return {"formula": v.text} if isinstance(v, Formula) else v
    return {"sheets": [{"title": s.title, "widths": s.widths,
                        "cells": [[r, c, value(cell.value), cell.bold, cell.colour]
                                  for (r, c), cell in sorted(s.cells.items())]} for s in book.sheets]}


def load(data: Mapping[str, Any]) -> Book:
    """dump's inverse."""
    book = Book()
    for s in data["sheets"]:
        sheet = Sheet(s["title"], widths=dict(s.get("widths") or {}))
        for r, c, value, bold, colour in s["cells"]:
            if isinstance(value, dict):
                value = Formula(value["formula"])
            sheet.cells[(r, c)] = Cell(value, bold, colour)
        book.sheets.append(sheet)
    return book


def column_letter(col: int) -> str:
    letters = ""
    while col:
        col, rest = divmod(col - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def column_number(letters: str) -> int:
    number = 0
    for ch in letters.upper():
        number = number * 26 + ord(ch) - 64
    return number


def coordinate(row: int, col: int) -> str:
    return f"{column_letter(col)}{row}"


# ----- the layout


def layout(conv: Mapping[str, Any]) -> Book:
    """The workbook's cells from one conversion JSON (conversion.py's shape). Reads conv, never
    changes it."""
    return Book([_parts(conv), _takeoff(conv), _verticals(conv), _pulled(conv)])


def _parts(conv: Mapping[str, Any]) -> Sheet:
    sheet = Sheet(PARTS)
    sheet.append(PART_HEADERS, bold=True)
    sheet.put(1, 3, "Grain", bold=True, colour=RED)
    parts = list(conv.get("parts") or [])
    for p in parts:
        built = p.get("build_mm") or [None, None]
        sheet.append([p.get("qty"), p.get("type"), p.get("grain") or None, p.get("design") or None,
                      p.get("wall") or None, p.get("desc"), p.get("material"), p.get("mount") or None,
                      "RAD" if p.get("radius") else None, *p.get("ic_in", [None, None]),
                      *p.get("ic_mm", [None, None]), *built, imperial(built[0]), imperial(built[1]),
                      p.get("sqft"), p.get("eb_lf"), p.get("review") or None])
    total = sheet.max_row + 1
    last = total - 1
    for col in (1, 18, 19):
        letter = column_letter(col)
        sheet.put(total, col, Formula(f"=SUM({letter}2:{letter}{last})") if parts else 0, bold=True)
    sheet.put(total, 6, f"{TOTAL_LABEL} ({conv.get('total_board_parts')})", bold=True)
    unplaced = list(conv.get("unplaced") or [])
    if unplaced:
        pieces = sum(int(u.get("qty") or 0) for u in unplaced)
        sheet.put(total + 1, 6, f"NOT COUNTED: {_count(pieces, 'piece')} on {_count(len(unplaced), 'line')}"
                                f" {COULD_NOT_VERIFY} ({TAKEOFF})", bold=True, colour=RED)
    sheet.widths = {column_letter(i): w for i, w in enumerate(PART_WIDTHS, 1)}
    return sheet


def _takeoff(conv: Mapping[str, Any]) -> Sheet:
    sheet = Sheet(TAKEOFF)
    rows: list[tuple[Any, Any]] = [
        (f"{conv.get('job')} — MATERIAL TAKEOFF", None), ("Client", conv.get("client")),
        ("Designer", conv.get("designer")), ("Designs", " / ".join(conv.get("designs") or []) or None),
        ("Branch", _branch_words(conv)), ("IC colour", conv.get("ic_color")),
        ("Material rule", conv.get("material_rule")), (None, None), ("WALLS (IC WALL/AREA tables)", None)]
    for design, walls in (conv.get("walls") or {}).items():
        rows.append((f"  {design}", " | ".join(_wall(w) for w in walls)))
    buckets = list(conv.get("buckets") or [])
    rows += [(None, None), (f"PANEL STOCK ({_stock(buckets)}, +10%)", None)]
    for b in buckets:
        sheets = b.get("sheets_with_waste")
        rows += [(f"  {b.get('material')} {b.get('thk_label')} — parts", b.get("parts")),
                 ("  — SqFt / SqM", f"{b.get('sqft')} / {b.get('sqm')}"),
                 ("  — Calc Sheets (SqFt/32 raw)", b.get("calc_sheets_raw")),
                 ("  — SHEETS TO ORDER", COULD_NOT_VERIFY if sheets is None else sheets),
                 ("  — sheet math", b.get("sheet_math"))]
    rows.append(("Schedule paste block", schedule(buckets)))
    unplaced = list(conv.get("unplaced") or [])
    if unplaced:
        rows += [(None, None), (UNPLACED_HEADER, None)]
        rows += [(f"  {i}", unplaced_words(u)) for i, u in enumerate(unplaced, 1)]
    edgeband = conv.get("edgeband") or {}
    rows += [(None, None), ("EDGE BANDING", None)]
    rows += [(f"  {b.get('material')} 1mm — tape {b.get('tape')}", b.get("edgeband_lf")) for b in buckets]
    rows += [("  Rule", edgeband.get("rule")), (None, None), ("VERTICAL RUNS", None)]
    runs = conv.get("runs") or {}
    rows += [(f"  {k}", v) for k, v in runs.items()] if runs else [("  —", NOT_MADE_VERTICALS)]
    rows += [(None, None), ("FLAGS", None)]
    rows += [(f"  {i}", text) for i, text in enumerate(conv.get("flags") or [], 1)]
    estimate = conv.get("estimate") or {}
    rows += [(None, None), ("ESTIMATE", None)]
    rows += [(f"  {k}", v) for k, v in (estimate.get("designs") or {}).items()]
    if estimate.get("discount"):
        rows.append((f"  {estimate.get('discount_label')}", estimate.get("discount")))
    rows.append(("  Total", estimate.get("total")))
    for label, value in rows:
        sheet.append([label, value], bold=bool(label) and value is None)
    sheet.widths = {"A": 50, "B": 130}
    return sheet


def _verticals(conv: Mapping[str, Any]) -> Sheet:
    sheet = Sheet(VERTICALS)
    sheet.append(VERTICAL_HEADERS, bold=True)
    verticals = list(conv.get("verticals") or [])
    for v in verticals:
        sheet.append([v.get("design"), v.get("wall"), v.get("n"), v.get("mount"), v.get("ic"),
                      v.get("product"), v.get("H"), v.get("D"), v.get("Elev"), v.get("X"),
                      imperial(v.get("X"))])
    if not verticals:
        sheet.append([NOT_MADE_VERTICALS])
    return sheet


def _pulled(conv: Mapping[str, Any]) -> Sheet:
    sheet = Sheet(PULLED)
    sheet.append(PULLED_HEADERS, bold=True)
    for r in conv.get("pulled_classified") or []:
        sheet.append([r.get("section") or None, r.get("qty"), r.get("item"), r.get("description"),
                      r.get("bucket") or None, r.get("family") or None, r.get("class_note") or None])
    sheet.widths = {"D": 48, "G": 50}
    return sheet


def schedule(buckets: Iterable[Mapping[str, Any]]) -> str:
    """The schedule paste block: '<sheets> - <thk> <material>', most sheets first; a bucket with no
    sheet size last, as could not verify."""
    listed = list(buckets)
    sized = sorted((b for b in listed if b.get("sheets_with_waste") is not None),
                   key=lambda b: -b["sheets_with_waste"])
    words = [f"{b['sheets_with_waste']} - {b.get('thk_label')} {b.get('material')}" for b in sized]
    words += [f"{COULD_NOT_VERIFY} - {b.get('thk_label')} {b.get('material')}"
              for b in listed if b.get("sheets_with_waste") is None]
    return " | ".join(words)


def unplaced_words(u: Mapping[str, Any]) -> str:
    """Where to find the line on the summary, what it says, and why it isn't counted."""
    item = f" of item {u['item']}" if u.get("item") else ""
    said = f" ({u['description']})" if u.get("description") else ""
    return f"Page {u.get('page')}: {u.get('qty')}{item}{said}, {u.get('reason')}"


def _stock(buckets: Sequence[Mapping[str, Any]]) -> str:
    sizes = {tuple(b["sheet_in"]) for b in buckets if b.get("sheet_in")}
    if sizes == {(49.0, 97.0)} and all(b.get("sheet_in") for b in buckets):
        return "49 x 97"
    return "sheet by bucket"


def _branch_words(conv: Mapping[str, Any]) -> str | None:
    kind = conv.get("job_type")
    if not kind:
        return None
    designs = len(conv.get("designs") or [])
    return f"{str(kind).upper()} - {_count(designs, 'design')} (from page 1's design categories)"


def _wall(w: Mapping[str, Any]) -> str:
    text = f'W{w.get("wall")} {_number(w.get("len_in"))}" x {_number(w.get("height_in"))}"'
    return text if w.get("ang") is None else f"{text} Ang {_number(w.get('ang'))}"


def _number(value: Any) -> str:
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def _count(n: int, thing: str) -> str:
    return f"{n} {thing}{'s' * (n != 1)}"


def imperial(mm: Any) -> str | None:
    """mm to inches to the nearest 1/16, as his sheets print them: 2132 -> '83 15/16"'."""
    if not isinstance(mm, (int, float)) or isinstance(mm, bool):
        return None
    sixteenths = round(abs(mm) / 25.4 * 16)
    whole, num = divmod(sixteenths, 16)
    den = 16
    while num and num % 2 == 0:
        num, den = num // 2, den // 2
    text = f"{whole} {num}/{den}" if num else f"{whole}"
    return f'{"-" if mm < 0 else ""}{text}"'


# ----- the formulas, evaluated in Python


class Unevaluable(Exception):
    """A formula this evaluator doesn't take: no number is made up for it."""


_TOKEN = re.compile(r"""\s*(?:
    (?P<num>\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)
  | (?P<ref>(?:(?:'(?:[^']|'')+'|[A-Za-z_][\w.]*)!)?\$?[A-Z]{1,3}\$?\d+)
  | (?P<name>[A-Z][A-Z0-9.]*)(?=\()
  | (?P<op>[-+*/^(),:])
)""", re.VERBOSE)
_REF = re.compile(r"(?:(?P<sheet>'(?:[^']|'')+'|[A-Za-z_][\w.]*)!)?\$?(?P<col>[A-Z]{1,3})\$?(?P<row>\d+)")


def evaluate(book: Book) -> dict[tuple[str, str], float]:
    """Every formula cell's value: (sheet title, "A1") -> number. Raises Unevaluable on a formula
    it doesn't take, or one reading text or itself."""
    out: dict[tuple[str, str], float] = {}
    for sheet in book.sheets:
        for (row, col), cell in sorted(sheet.cells.items()):
            if isinstance(cell.value, Formula):
                out[(sheet.title, coordinate(row, col))] = _Evaluator(book).cell(sheet.title, row, col)
    return out


def value_of(book: Book, title: str, row: int, col: int) -> Any:
    """A cell's value, a formula's evaluated."""
    found = book.sheet(title).value(row, col)
    return _Evaluator(book).cell(title, row, col) if isinstance(found, Formula) else found


class _Evaluator:
    def __init__(self, book: Book) -> None:
        self.book = book
        self.busy: set[tuple[str, int, int]] = set()

    def cell(self, title: str, row: int, col: int) -> float:
        value = self.book.sheet(title).value(row, col)
        if not isinstance(value, Formula):
            if value is None:
                return 0.0
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise Unevaluable(f"{title}!{coordinate(row, col)} is not a number")
            return float(value)
        key = (title, row, col)
        if key in self.busy:
            raise Unevaluable("a formula reads itself")
        self.busy.add(key)
        try:
            return _Parser(self, title, value.text).parse()
        finally:
            self.busy.discard(key)

    def numbers(self, title: str, first: tuple[int, int], last: tuple[int, int]) -> list[float]:
        """The numbers in a range, as SUM takes them: text and blanks count nothing."""
        out = []
        for row in range(min(first[0], last[0]), max(first[0], last[0]) + 1):
            for col in range(min(first[1], last[1]), max(first[1], last[1]) + 1):
                value = self.book.sheet(title).value(row, col)
                if isinstance(value, Formula):
                    out.append(self.cell(title, row, col))
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    out.append(float(value))
        return out


class _Parser:
    def __init__(self, ev: _Evaluator, title: str, text: str) -> None:
        if not text.startswith("="):
            raise Unevaluable("not a formula")
        self.ev, self.title = ev, title
        self.tokens = self._tokens(text[1:])
        self.at = 0

    @staticmethod
    def _tokens(text: str) -> list[tuple[str, str]]:
        out, at = [], 0
        while at < len(text):
            if text[at:].strip() == "":
                break
            found = _TOKEN.match(text, at)
            if found is None or found.end() == at:
                raise Unevaluable(f"can't read {text[at:at + 12]!r}")
            kind = found.lastgroup
            out.append((kind, found[kind]))
            at = found.end()
        return out

    def parse(self) -> float:
        value = self.expr()
        if self.at != len(self.tokens):
            raise Unevaluable("left over")
        return value

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def take(self, op: str | None = None) -> tuple[str, str]:
        token = self.peek()
        if token is None or (op is not None and token != ("op", op)):
            raise Unevaluable(f"expected {op}")
        self.at += 1
        return token

    def expr(self) -> float:
        value = self.term()
        while (t := self.peek()) in (("op", "+"), ("op", "-")):
            self.take()
            value = value + self.term() if t[1] == "+" else value - self.term()
        return value

    def term(self) -> float:
        value = self.power()
        while (t := self.peek()) in (("op", "*"), ("op", "/")):
            self.take()
            right = self.power()
            if t[1] == "/" and right == 0:
                raise Unevaluable("division by zero")
            value = value * right if t[1] == "*" else value / right
        return value

    def power(self) -> float:
        value = self.unary()
        while self.peek() == ("op", "^"):
            self.take()
            value = value ** self.unary()
        return value

    def unary(self) -> float:
        if self.peek() == ("op", "-"):
            self.take()
            return -self.unary()
        if self.peek() == ("op", "+"):
            self.take()
            return self.unary()
        return self.primary()

    def primary(self) -> float:
        kind, text = self.take()
        if kind == "num":
            return float(text)
        if kind == "ref":
            if self.peek() == ("op", ":"):
                raise Unevaluable("a range outside a function")
            title, row, col = self._ref(text)
            return self.ev.cell(title, row, col)
        if kind == "name":
            return self.call(text)
        if (kind, text) == ("op", "("):
            value = self.expr()
            self.take(")")
            return value
        raise Unevaluable(f"unexpected {text!r}")

    def _ref(self, text: str) -> tuple[str, int, int]:
        found = _REF.fullmatch(text)
        if found is None:
            raise Unevaluable(f"not a reference: {text}")
        sheet = found["sheet"]
        title = self.title if sheet is None else sheet.strip("'").replace("''", "'")
        if not self.ev.book.has(title):
            raise Unevaluable(f"no sheet {title!r}")
        return title, int(found["row"]), column_number(found["col"])

    def args(self) -> list[list[float]]:
        """Each argument as a list of numbers: a range gives its numbers, anything else one."""
        self.take("(")
        out: list[list[float]] = []
        if self.peek() == ("op", ")"):
            self.take()
            return out
        while True:
            token = self.peek()
            nxt = self.tokens[self.at + 1] if self.at + 1 < len(self.tokens) else None
            if token and token[0] == "ref" and nxt == ("op", ":"):
                self.take()
                self.take(":")
                kind, end = self.take()
                if kind != "ref":
                    raise Unevaluable("a range's end is not a cell")
                title, r1, c1 = self._ref(token[1])
                _, r2, c2 = self._ref(end if "!" in end else end)
                out.append(self.ev.numbers(title, (r1, c1), (r2, c2)))
            else:
                out.append([self.expr()])
            if self.peek() == ("op", ","):
                self.take()
                continue
            self.take(")")
            return out

    def call(self, name: str) -> float:
        args = self.args()
        if name == "SUM":
            return math.fsum(v for arg in args for v in arg)
        single = [arg[0] if len(arg) == 1 else None for arg in args]
        if None in single:
            raise Unevaluable(f"{name} takes no range")
        if name == "ABS" and len(single) == 1:
            return abs(single[0])
        if name == "ROUND" and len(single) == 2:
            return _round_half_up(single[0], int(single[1]))
        if name == "ROUNDUP" and len(single) == 2:
            scale = 10 ** int(single[1])
            return math.copysign(math.ceil(round(abs(single[0]) * scale, 9)) / scale, single[0])
        if name == "CEILING" and len(single) == 2 and single[1] > 0:
            return math.ceil(round(single[0] / single[1], 9)) * single[1]
        raise Unevaluable(f"{name} is not a function this evaluator takes")


def _round_half_up(value: float, places: int) -> float:
    return float(Decimal(repr(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


# ----- the named cells, ours or his


_BUCKET = re.compile(r"^  (?P<name>.+) — parts$")
_SQFT_SQM = re.compile(r"^\s*(?P<sqft>[\d.]+)\s*/\s*(?P<sqm>[\d.]+)\s*$")


def named(book: Book) -> dict[str, Any]:
    """The cells that carry the takeoff, read by their headers and labels: the Parts total row's
    board parts, area and tape (evaluated), each part row, each bucket's numbers, the tape rows,
    the schedule block, the estimate total and PULLED classified's counts. A cell a book doesn't
    hold is left out, never filled."""
    found: dict[str, Any] = {}
    if book.has(PARTS):
        found.update(_named_parts(book))
    if book.has(TAKEOFF):
        found.update(_named_takeoff(book.sheet(TAKEOFF)))
    if book.has(PULLED):
        found["pulled"] = _named_pulled(book.sheet(PULLED))
    return found


def _headers(sheet: Sheet) -> dict[str, int]:
    return {str(sheet.value(1, c)).strip(): c for c in range(1, sheet.max_column + 1)
            if sheet.value(1, c) is not None}


def _named_parts(book: Book) -> dict[str, Any]:
    sheet = book.sheet(PARTS)
    cols = _headers(sheet)
    found: dict[str, Any] = {}
    label_col = cols.get("IC description")
    total = next((r for r in range(2, sheet.max_row + 1) if label_col
                  and str(sheet.value(r, label_col) or "").startswith(TOTAL_LABEL)), None)
    if total is not None:
        for name, header in (("board_parts", "Qty"), ("sqft", "SqFt"), ("tape", "EB LF")):
            if header in cols:
                try:
                    found[name] = value_of(book, PARTS, total, cols[header])
                except Unevaluable:
                    found[name] = None
    last = (total or sheet.max_row + 1) - 1
    rows = []
    wanted = {"qty": "Qty", "type": "Type", "a_in": "IC a (in)", "b_in": "IC b (in)",
              "built_a": "BUILT a (mm)", "built_b": "BUILT b (mm)", "material": "Material",
              "sqft": "SqFt", "eb_lf": "EB LF", "desc": "IC description", "review": "Review",
              "mount": "Mount", "radius": "Radius"}
    for r in range(2, last + 1):
        qty = sheet.value(r, cols["Qty"]) if "Qty" in cols else None
        if isinstance(qty, (int, float)) and not isinstance(qty, bool):
            rows.append({k: sheet.value(r, cols[h]) for k, h in wanted.items() if h in cols})
    found["parts"] = rows
    return found


def _named_takeoff(sheet: Sheet) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {}
    tapes: dict[str, Any] = {}
    found: dict[str, Any] = {}
    current: dict[str, Any] | None = None
    section = ""
    for r in range(1, sheet.max_row + 1):
        label, value = sheet.value(r, 1), sheet.value(r, 2)
        if not isinstance(label, str):
            continue
        if not label.startswith(" ") and value is None:
            section = label
        if (m := _BUCKET.match(label)) and section.startswith("PANEL STOCK"):
            current = buckets.setdefault(m["name"], {})
            current["parts"] = value
        elif label == "  — SqFt / SqM" and current is not None:
            m2 = _SQFT_SQM.match(str(value or ""))
            current["sqft"], current["sqm"] = (float(m2["sqft"]), float(m2["sqm"])) if m2 else (None, None)
        elif label == "  — Calc Sheets (SqFt/32 raw)" and current is not None:
            current["calc_sheets_raw"] = value
        elif label == "  — SHEETS TO ORDER" and current is not None:
            current["sheets"] = value
        elif label == "Schedule paste block":
            found["schedule"] = value
        elif section == "EDGE BANDING" and label.startswith("  ") and label != "  Rule"                 and isinstance(value, (int, float)) and not isinstance(value, bool):
            tapes[label.strip()] = value
        elif label == "  Total" and section == "ESTIMATE":
            found["estimate_total"] = value
    found["buckets"] = buckets
    found["edgeband"] = tapes
    return found


def _named_pulled(sheet: Sheet) -> dict[str, int]:
    cols = _headers(sheet)
    rows = pieces = board = 0
    for r in range(2, sheet.max_row + 1):
        qty = sheet.value(r, cols.get("Qty", 2))
        if not isinstance(qty, (int, float)) or isinstance(qty, bool):
            continue
        rows += 1
        pieces += int(qty)
        if sheet.value(r, cols.get("Bucket", 5)) == "board":
            board += int(qty)
    return {"rows": rows, "pieces": pieces, "board": board}
