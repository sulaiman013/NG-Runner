"""
The IC summary's lines: the PULLED parts list, each design's parts list and the estimate total.

Why it exists: Jim's chat types every job's parts into a script by hand, and the takeoff starts
from the summary itself (docs/p2/build-spec.md, sections 3.1 and 5.1). This module turns pypdf's
text of the summary into rows and judges nothing: which rows are board parts is parts.py's work.

It reads pypdf's text, as pinned, one cell per line. Two layouts exist (Jim's BUILD 48-55): the
landscape one prints two lists side by side, quantity first; the portrait one prints one list,
item first. The layout comes from the header after PULLED, never from the page's shape, and a
header this module doesn't know raises SummaryUnreadable instead of a guessed reading. PULLED
pages are found by the word PULLED near the top, since newer renders move the title.

A client's details can't travel from here. Page 1 is never read, nor the lines above a parts
list's header (its "Project:" line holds the job's name), nor the Estimate page past the figure
after Total. A row's description stays out of its repr: what reaches a takeoff is parts.py's codes
and numbers.
"""
from __future__ import annotations

import io
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

import pypdf

Layout = Literal["qty_first", "item_first"]


class SummaryUnreadable(Exception):
    """The summary can't be read as measured. Its text is plain words, with no path or name."""


@dataclass(frozen=True)
class SummaryLine:
    """One row of a parts list. page counts from 0 (page 1 is 0, and never read). column is 0 in
    the one-list layout; in the two-list layout, 0 is the left list and 1 the right. dims is a
    dimension line printed under the row (a rod's 39 3/4"W x 1"H), kept apart from desc."""

    section: str
    item: str
    qty: int
    desc: str = field(repr=False)
    page: int
    column: int
    dims: str = field(default="", repr=False)


# ----- the page's text


def read_pages(data: bytes) -> tuple[str, ...]:
    """pypdf's text of every page, in order."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        return tuple(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # noqa: BLE001 - pypdf raises many kinds on a file it can't read
        raise SummaryUnreadable("The IC summary could not be read as a PDF.") from exc


def _lines(page: str) -> list[str]:
    return [line.strip() for line in page.split("\n") if line.strip()]


# ----- the header

# Each list's header, as pypdf gives it. The landscape page prints two lists side by side.
_HEADERS: dict[Layout, tuple[str, ...]] = {
    "qty_first": ("QTY", "ITEM #", "DESCRIPTION", "DIMENSIONS"),
    "item_first": ("ITEM #", "QUANTITY", "DESCRIPTION", "DIMENSIONS"),
}
_LISTS: dict[Layout, int] = {"qty_first": 2, "item_first": 1}
_PULLED = "PULLED"
_NEAR_THE_TOP = 300      # characters: PULLED is found here (build spec 3.1)


def _is_pulled_page(page: str) -> bool:
    return any(line.strip() == _PULLED for line in page[:_NEAR_THE_TOP].split("\n"))


def _pulled_header(lines: Sequence[str]) -> tuple[Layout, int]:
    """The layout and the index of the first line after the PULLED header."""
    if _PULLED not in lines:
        raise SummaryUnreadable("The PULLED parts list has no header.")
    start = lines.index(_PULLED)
    for name, fields in _HEADERS.items():
        header = ((_PULLED, *fields) * _LISTS[name])
        if tuple(lines[start:start + len(header)]) == header:
            return name, start + len(header)
    raise SummaryUnreadable("The PULLED parts list has a header this reader doesn't know.")


def layout(page: str) -> Layout:
    """Which layout a PULLED page prints: from the order of QTY or QUANTITY after PULLED."""
    return _pulled_header(_lines(page))[0]


# ----- rows

# The sections Studio prints between rows (build spec 3.1, and those read on the 52 real pairs).
SECTIONS = frozenset({
    "CTO", "Components", "Fronts", "Hardware", "Accessories", "Trim", "Aluminum Doors & Drawers",
    "Studio Activated Custom Parts", "User Activated Custom Parts", "Third Party Parts",
    "Marketplace", "Cabinet", "Cabinet Fronts", "Accents", "Custom", "Glass Shelf", "Slatwall",
})
_QTY = re.compile(r"\d{1,4}")
# A dimension line: 28.858"W x 5.906"H, 29 5/8"W x 1"H, 0"H x 54 1/16"W.
_DIMS = re.compile(r'\d[\d./ ]*"[WHDL](?: x \d[\d./ ]*"[WHDL])*')
# A row printed inside the description of the row before it (landscape only): a quantity, then an
# IC item code, then more text. The whitespace before the quantity keeps 3/4 FL from splitting.
_INLINE_ROW = re.compile(r"(?<!\S)(\d{1,4}) (\d{8,10}|[A-Z]{2,5}(?:-[A-Z0-9]{1,4})?) (?=\S)")
# The store's footer ends every page. It is found by its shape, never its words (the repo is public,
# and an address's words can be a client's name): the page number last, then, going up, lines with
# a phone number, a web address or a city's state and ZIP, a street (a number, then words), and
# the store's name just above that street. Only the end of a page is read this way, so a row's
# description is never cut: the landscape footer is one line holding a phone number.
_PAGE_NUMBER = re.compile(r"Page \d+ of \d+")
_FOOTER_LINE = re.compile(r"\(\d{3}\) \d{3}-\d{4}|\bwww\.\S+|, [A-Z]{2} \d{5}(?:-\d{4})?\b")
_STREET = re.compile(r"\d{1,6}(?: [A-Za-z.]+)+")
_NAME_WORDS = 4


def _without_footer(lines: list[str]) -> list[str]:
    end = len(lines)
    if end and _PAGE_NUMBER.fullmatch(lines[end - 1]):
        end -= 1
    street = False
    while end and (_FOOTER_LINE.search(lines[end - 1]) or _STREET.fullmatch(lines[end - 1])):
        street = bool(_STREET.fullmatch(lines[end - 1]))
        end -= 1
    if street and end and _is_store_name(lines[end - 1]):
        end -= 1
    return lines[:end]


def _is_store_name(text: str) -> bool:
    return not any(c.isdigit() for c in text) and len(text.split()) <= _NAME_WORDS


@dataclass
class _Row:
    section: str
    item: str
    qty: int
    desc: str
    page: int
    column: int
    dims: list[str] = field(default_factory=list)

    def done(self) -> SummaryLine:
        return SummaryLine(self.section, self.item, self.qty, self.desc, self.page, self.column,
                           " ".join(self.dims))


def _read_rows(body: Sequence[str], kind: Layout, page: int) -> list[SummaryLine]:
    """The rows of one page's list, from the line after its header to its footer. An item cell
    printed over two lines (Custom Cut, a wrapped Marketplace item) comes before its row starts;
    such lines after a section join the next row's item. Any line left over raises."""
    rows: list[_Row] = []
    section, current, slot, wrapped = "", None, 0, []
    i = 0
    while i < len(body):
        text = body[i]
        if text in SECTIONS and not wrapped:
            section, current, slot = text, None, 0
            i += 1
            continue
        start = _row_start(body, i, kind)
        if start is not None:
            item, qty, used = start
            if kind == "qty_first" and wrapped:
                raise SummaryUnreadable(f"A line on page {page + 1} of the parts list could not"
                                        " be read as part of a row.")
            item = " ".join([*wrapped, item])
            desc = body[i + 2] if used == 3 else ""
            pieces = _split_inline(desc) if kind == "qty_first" else [desc]
            current, slot = _new_rows(rows, pieces, section, item, qty, page, kind, slot)
            wrapped = []
            i += used
            continue
        if current is None:
            wrapped.append(text)
        elif _DIMS.fullmatch(text):
            current.dims.append(text)
        else:
            pieces = _split_inline(text) if kind == "qty_first" else [text]
            first = pieces[0] if isinstance(pieces[0], str) else ""
            if first:
                current.desc = f"{current.desc} {first}".strip()
            current, slot = _new_rows(rows, pieces[1:], section, "", 0, page, kind, slot,
                                      current=current)
        i += 1
    if wrapped:
        raise SummaryUnreadable(f"A line on page {page + 1} of the parts list could not be read"
                                " as part of a row.")
    return [row.done() for row in rows]


def _row_start(body: Sequence[str], i: int, kind: Layout) -> tuple[str, int, int] | None:
    """(item, qty, lines used) when a row starts at line i: qty, item, description in the
    landscape layout; item, qty, description in the portrait one. A row at the end of a page may
    have no description (Studio prints it on the next page, or not at all)."""
    if i + 1 >= len(body):
        return None
    first, second = body[i], body[i + 1]
    third = body[i + 2] if i + 2 < len(body) else None
    used = 3 if third is not None else 2
    if third is not None and (third in SECTIONS or _DIMS.fullmatch(third)):
        return None
    if kind == "qty_first":
        # After a quantity comes its item, even one that reads like a section (Marketplace).
        if _QTY.fullmatch(first) and not _QTY.fullmatch(second) and not _DIMS.fullmatch(second):
            return second, int(first), used
        return None
    if (_QTY.fullmatch(second) and not _QTY.fullmatch(first) and not _DIMS.fullmatch(first)
            and first not in SECTIONS):
        return first, int(second), used
    return None


Piece = str | tuple[int, str]


def _split_inline(text: str) -> list[Piece]:
    """A description, then (qty, item) and the description of each row printed inside it."""
    pieces: list[Piece] = []
    rest = text
    while (found := _INLINE_ROW.search(rest)) is not None:
        pieces.append(rest[:found.start()].strip())
        pieces.append((int(found[1]), found[2]))
        rest = rest[found.end():]
    pieces.append(rest.strip())
    return pieces


def _new_rows(rows: list[_Row], pieces: Sequence[Piece], section: str, item: str, qty: int,
              page: int, kind: Layout, slot: int, *, current: _Row | None = None,
              ) -> tuple[_Row | None, int]:
    """Opens the rows the pieces hold: the first piece is the description of (item, qty), unless
    `current` is given, when the pieces start with a (qty, item) found inside a line."""
    step = 1 if kind == "qty_first" else 0
    if current is None:
        current = _Row(section, item, qty, str(pieces[0]), page, slot % 2 if step else 0)
        rows.append(current)
        slot += step
        pieces = pieces[1:]
    for k in range(0, len(pieces) - 1, 2):
        found_qty, found_item = pieces[k]  # type: ignore[misc]
        current = _Row(section, found_item, found_qty, str(pieces[k + 1]), page,
                       slot % 2 if step else 0)
        rows.append(current)
        slot += step
    return current, slot


# ----- the PULLED list


def pulled_lines(pages: Sequence[str]) -> tuple[SummaryLine, ...]:
    """Every row of the PULLED list, in page order. Raises SummaryUnreadable with no PULLED page,
    an unknown header, two layouts in one summary, or a page whose rows can't be read."""
    found: list[SummaryLine] = []
    layouts: set[Layout] = set()
    for number, page in enumerate(pages):
        if number == 0 or not _is_pulled_page(page):
            continue
        lines = _without_footer(_lines(page))
        kind, start = _pulled_header(lines)
        layouts.add(kind)
        rows = _read_rows(lines[start:], kind, number)
        if not rows:
            raise SummaryUnreadable(f"Page {number + 1} of the PULLED parts list holds no rows.")
        found.extend(rows)
    if not layouts:
        raise SummaryUnreadable("The IC summary has no PULLED parts list.")
    if len(layouts) > 1:
        raise SummaryUnreadable("The PULLED parts list changes layout between pages.")
    return tuple(found)


# ----- the design lists

_DESIGN = re.compile(r"Design: (?P<name>.+)")


def design_lines(pages: Sequence[str]) -> Mapping[str, tuple[SummaryLine, ...]]:
    """Each design's parts list (the pages headed DIMENSIONS, not a wall's COLOR list), by the
    design's name as printed. The names hold a client's name, so they stay with the caller."""
    found: dict[str, list[SummaryLine]] = {}
    for number, page in enumerate(pages):
        if number == 0 or _is_pulled_page(page):
            continue
        for name, kind, body in _design_lists(_without_footer(_lines(page))):
            found.setdefault(name, []).extend(_read_rows(body, kind, number))
    return {name: tuple(lines) for name, lines in found.items()}


def _design_lists(lines: list[str]) -> Iterator[tuple[str, Layout, list[str]]]:
    design = next((m["name"].strip() for line in lines[:8] if (m := _DESIGN.match(line))), None)
    if design is None:
        return
    for kind, fields in _HEADERS.items():
        header = fields * _LISTS[kind]
        for start in range(len(lines) - len(header) + 1):
            if tuple(lines[start:start + len(header)]) == header:
                yield design, kind, lines[start + len(header):]
                return


# ----- the estimate total

_MONEY = re.compile(r"-?\$(?P<amount>[0-9][0-9,]*\.[0-9]{2})")


def estimate_total(pages: Sequence[str]) -> float | None:
    """The figure after the line Total on the Estimate page; None without one. Only the lines
    after the word Estimate are looked at, and nothing after the figure: the client's name,
    address and contact details sit above it."""
    for number, page in enumerate(pages):
        if number == 0:
            continue
        lines = _lines(page)
        if "Estimate" not in lines:
            continue
        after = lines[lines.index("Estimate") + 1:]
        if "DESIGN" not in after[:4] or after[after.index("DESIGN") + 1:][:1] != ["TOTAL"]:
            continue
        if "Total" not in after:
            return None
        at = after.index("Total")
        found = _MONEY.fullmatch(after[at + 1]) if at + 1 < len(after) else None
        if found is None:
            return None
        amount = float(found["amount"].replace(",", ""))
        return -amount if after[at + 1].startswith("-") else amount
    return None
