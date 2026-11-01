"""
Preparing an IC summary for the reader evaluation (docs/p2/build-spec.md 5.7; design 3.6).

Why: a model may only ever see a wall's drawing and that wall's parts lines, never a page, a name or
an address (Jim's consent, 2026-10-31, as build-spec section 9 records it). So this module takes
from each elevation page exactly two things, and proves they are clean:

  the crop   the page's one large embedded raster, at its native pixels (pypdf decodes it; nothing
             renders the page). Claude takes at most 2,576 px a side and 4,784 visual tokens; a
             raster over that loses columns from its right edge, right of the wall box (the
             duplicate ruler), never scaled and never into the box (TooLarge otherwise).
  the parts  the wall's parts table, read by position: quantity and description only. Item codes
             and colours are dropped as not needed; the page header ("Design: <name>", "Project:
             <name>"), the Notes block, the WALL/AREA page and the store footer are never read into
             it.

Containment, in code: a page is refused (a Refusal, never a crop) when any text span, any
annotation or any form overlaps its drawing, when it holds more than one large raster or none, or
when the raster can't fit by cropping. A text span's box is drawn deliberately wide: every glyph
taken as one em, from 0.35 em below the baseline to 1.05 em above, so a near miss refuses rather
than passes.

The deny list is the second guard (payload.py scans every payload with it): the values on page 1
(the client, the address, the email, the phones, the designer, the project and its designs), every
"Project:" and "Design:" header, and the store footer, as words. It never shows its values: its
repr is a count, and a hit reports only the kind of field it came from.
"""
from __future__ import annotations

import hashlib
import io
import math
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from PIL import Image
from pypdf import PdfReader
from pypdf.generic import DictionaryObject

MAX_SIDE = 2576  # px, Claude's high-resolution limit (vision docs, 2026-10-31)
MAX_VISUAL_TOKENS = 4784  # ceil(w/28) x ceil(h/28) at most, or Claude scales the image down
PATCH = 28
DRAWING_MIN = 144.0  # points: a drawing is at least 2 in across; the logo is under 1 in
GLYPH_EM, BELOW_EM, ABOVE_EM = 1.0, 0.35, 1.05  # a text span's box, wider than any glyph
DARK = 170  # grey level under which a pixel is ink (as Jim's elev_panels.py reads a drawing)
BOX_COLUMN = 0.38  # a column this dark over the raster's height is the wall box (elev_panels.py)
BOX_MARGIN = 4  # px kept right of the wall box when the right ruler is cropped off
FOOTER_BAND = 110.0  # points from the page's foot: the store footer and "Page k of N" sit below it

_PAGE_NUMBER = re.compile(r"^Page \d+ of \d+$")
_LABEL = re.compile(r"^[A-Z][A-Z /#&()-]+$")
_WALL_AREA = re.compile(r'^(?P<name>[^:]*[^:\s]) *: *(?P<w>\d+(?:\.\d+)?(?: \d+/\d+)?)" X '
                        r'(?P<h>\d+(?:\.\d+)?(?: \d+/\d+)?)"$')
_VT = re.compile(r"^(?P<rad>VT RAD|VT|Custom Vert)\b.*?\b(?P<depth>\d+(?:\.\d+)?)x(?P<height>\d+(?:\.\d+)?)x")
_COLUMNS = {"ITEM #": "item", "QTY": "qty", "QUANTITY": "qty", "DESCRIPTION": "desc", "COLOR": "color"}


_PREPARED: set[str] = set()  # sha256 of every crop this process proved contained


def was_prepared(sha256: str) -> bool:
    """True only for a crop that _contain produced in this process: payload.py sends nothing else."""
    return sha256 in _PREPARED


class TooLarge(Exception):
    """The raster can't be brought within Claude's limits without cutting into the wall."""


# ----- geometry


@dataclass(frozen=True)
class Box:
    """An axis-aligned box in PDF user space (points, origin bottom left)."""
    x0: float
    y0: float
    x1: float
    y1: float

    def overlaps(self, other: Box) -> bool:
        return self.x0 < other.x1 and other.x0 < self.x1 and self.y0 < other.y1 and other.y0 < self.y1

    @staticmethod
    def around(points: Iterable[tuple[float, float]]) -> Box:
        xs, ys = zip(*points)
        return Box(min(xs), min(ys), max(xs), max(ys))


def _apply(m: Sequence[float], u: float, v: float) -> tuple[float, float]:
    return m[0] * u + m[2] * v + m[4], m[1] * u + m[3] * v + m[5]


def text_box(text: str, size: float, tm: Sequence[float], cm: Sequence[float]) -> Box:
    """Where a text span can reach: every glyph one em wide, through its text matrix and the CTM."""
    w = max(len(text), 1) * GLYPH_EM * size
    corners = [(0.0, -BELOW_EM * size), (w, -BELOW_EM * size), (0.0, ABOVE_EM * size), (w, ABOVE_EM * size)]
    return Box.around(_apply(cm, *_apply(tm, u, v)) for u, v in corners)


def _unit_square(cm: Sequence[float]) -> Box:
    return Box.around(_apply(cm, u, v) for u, v in ((0, 0), (1, 0), (0, 1), (1, 1)))


# ----- fitting Claude's image budget


def visual_tokens(width: int, height: int) -> int:
    """Claude's cost of an image: one token per 28 x 28 patch (vision docs, 2026-10-31)."""
    return math.ceil(width / PATCH) * math.ceil(height / PATCH)


_INK = bytes(1 if level < DARK else 0 for level in range(256))


def fit_to_budget(img: Image.Image) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """
    The image unchanged when it is within Claude's limits; else with columns cut from its right
    edge, right of the wall box only. Returns the image and the pixels cut (left, top, right,
    bottom). Raises TooLarge when that would reach the box, or the height alone is over.
    """
    w, h = img.size
    if w <= MAX_SIDE and h <= MAX_SIDE and visual_tokens(w, h) <= MAX_VISUAL_TOKENS:
        return img, (0, 0, 0, 0)
    rows = math.ceil(h / PATCH)
    if h > MAX_SIDE or rows > MAX_VISUAL_TOKENS:
        raise TooLarge(f"{h} px high: over the limit whatever the width")
    keep = min(MAX_SIDE, PATCH * (MAX_VISUAL_TOKENS // rows))
    ink = img.convert("L").tobytes().translate(_INK)
    box_columns = [x for x in range(w) if ink[x::w].count(1) > BOX_COLUMN * h]
    if not box_columns:
        raise TooLarge("no wall box found, so nothing shows where cropping is safe")
    if keep < max(box_columns) + 1 + BOX_MARGIN:
        raise TooLarge(f"fitting means cutting at {keep} px, inside the wall box")
    return img.crop((0, 0, keep, h)), (0, 0, w - keep, 0)


# ----- names: kept as a key hash only


def name_key(name: str) -> str:
    """A design or room name compared as letters and digits only (Mozaik drops apostrophes)."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", name).casefold() if ch.isalnum())


def name_key_hash(name: str) -> str:
    return hashlib.sha256(name_key(name).encode("utf-8")).hexdigest()[:16]


def _clean(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


# ----- the deny list


_WORD = re.compile(r"[A-Za-z]{3,}|\d{3,}")
_PHONE = re.compile(r"\(?\b\d{3}\)?[\s.-]*\d{3}[\s.-]\d{4}\b")
# Words that name a kind of room or are plain English: a design name's own words, never a client's.
STOP = frozenset({
    "the", "and", "for", "with", "his", "her", "hers", "him", "master", "primary", "closet", "closets",
    "walk", "reach", "garage", "pantry", "laundry", "office", "bedroom", "bed", "bath", "room", "rooms",
    "kids", "kid", "guest", "entry", "hall", "linen", "mud", "high", "low", "final", "option", "rev",
    "design", "designs", "wall", "walls", "page", "project", "notes", "new", "old", "left", "right",
    "top", "bottom", "main", "upper", "lower", "front", "back", "side", "only", "not", "copy"})


class DenyList:
    """Words a payload must never carry, each remembered only with the kind of field it came from."""

    def __init__(self, entries: Iterable[tuple[str, str]] = ()) -> None:
        self._tokens: dict[str, frozenset[str]] = {}
        for kind, text in entries:
            for token in _WORD.findall(text):
                key = token.casefold()
                if key not in STOP:
                    self._tokens[key] = self._tokens.get(key, frozenset()) | {kind}

    def hits(self, text: str, *, ignore: frozenset[str] = frozenset()) -> tuple[str, ...]:
        """The kinds of field `text` repeats a word of, plus "email" and "phone"; () when clean.
        Words in `ignore` (casefolded) never hit: payload.py passes its own fixed vocabulary."""
        kinds: set[str] = set()
        for token in _WORD.findall(text):
            if token.casefold() not in ignore:
                kinds |= self._tokens.get(token.casefold(), frozenset())
        if "@" in text:
            kinds.add("email")
        if _PHONE.search(text):
            kinds.add("phone")
        return tuple(sorted(kinds))

    def __or__(self, other: DenyList) -> DenyList:
        merged = DenyList()
        for source in (self, other):
            for key, kinds in source._tokens.items():
                merged._tokens[key] = merged._tokens.get(key, frozenset()) | kinds
        return merged

    def __len__(self) -> int:
        return len(self._tokens)

    def __repr__(self) -> str:
        return f"DenyList({len(self)} words)"

    __str__ = __repr__


# ----- results


@dataclass(frozen=True)
class PartLine:
    qty: int | None
    desc: str

    def as_text(self) -> str:
        return f"{'?' if self.qty is None else self.qty} | {self.desc}"


@dataclass(frozen=True, repr=False)
class WallCrop:
    """One elevation page's drawing and its wall's parts lines, contained (module notes)."""
    page: int  # 1-based
    design: int  # the design's order in the summary, from 0
    design_key: str  # name_key_hash of the design's name: never the name
    wall: str  # the wall's name from its title ("Wall 2" -> "2")
    width_in: float | None  # from the design's WALL/AREA table
    height_in: float | None
    png: bytes
    pixels: tuple[int, int]
    native: tuple[int, int]
    trim: tuple[int, int, int, int]  # px cut: left, top, right, bottom
    sha256: str
    parts: tuple[PartLine, ...]
    table: tuple[tuple[str, float], ...] = ()  # the design's whole WALL/AREA table: (name, width in)
    # In memory only, never written or sent: the design's name key, and every design's in its summary,
    # for pairing a room Mozaik names differently (pairing.py).
    design_name_key: str = field(default="", repr=False, compare=False)
    summary_designs: tuple[str, ...] = field(default=(), repr=False, compare=False)

    @property
    def vt_lines(self) -> tuple[tuple[PartLine, re.Match[str]], ...]:
        return tuple((p, m) for p in self.parts if (m := _VT.match(p.desc)))

    @property
    def vt_count(self) -> int | None:
        """Verticals the parts list gives this wall; None when a VT line's quantity is unreadable."""
        lines = self.vt_lines
        return None if any(p.qty is None for p, _ in lines) else sum(p.qty or 0 for p, _ in lines)

    @property
    def vt_rad_count(self) -> int | None:
        lines = [(p, m) for p, m in self.vt_lines if m["rad"] == "VT RAD"]
        return None if any(p.qty is None for p, _ in lines) else sum(p.qty or 0 for p, _ in lines)

    @property
    def vt_heights(self) -> tuple[str, ...]:
        """The VT heights the parts list allows, as printed, smallest first."""
        return tuple(sorted({m["height"] for _, m in self.vt_lines}, key=float))

    def __repr__(self) -> str:
        return (f"WallCrop(page={self.page}, design={self.design}, wall={self.wall!r}, "
                f"width_in={self.width_in}, pixels={self.pixels}, trim={self.trim}, "
                f"sha256={self.sha256[:12]}, parts={len(self.parts)})")


@dataclass(frozen=True)
class Refusal:
    page: int
    code: str
    reason: str  # plain words, no names


@dataclass(frozen=True)
class SummaryPrep:
    crops: tuple[WallCrop, ...]
    refused: tuple[Refusal, ...]
    deny: DenyList = field(repr=False, compare=False)
    pages: int = 0
    elevation_pages: int = 0
    unlabelled_tables: int = 0  # tables with no "Wall <name>" over them: design and PULLED lists
    orphan_wall_tables: int = 0  # a wall's table that follows no elevation of that wall


# ----- reading one page


@dataclass(frozen=True)
class _Run:
    x: float
    y: float
    size: float
    text: str
    box: Box


@dataclass(frozen=True)
class _Drawn:
    name: str
    rect: Box
    subtype: str


def _resources(page: Any) -> DictionaryObject:
    res = page.get("/Resources")
    res = res.get_object() if res is not None else None
    return res if isinstance(res, DictionaryObject) else DictionaryObject()


def _read_page(page: Any) -> tuple[list[_Run], list[_Drawn], list[Box]]:
    """Text spans (with their boxes), what the page draws, and its annotations' rectangles."""
    raw: list[list[Any]] = []  # [x, y, size, text, tm, cm]: runs at one spot are one span
    drawn: list[_Drawn] = []
    xobjects = _resources(page).get("/XObject")
    xobjects = xobjects.get_object() if xobjects is not None else {}

    def on_text(text: str, cm: list[float], tm: list[float], _font: Any, font_size: float) -> None:
        if not text.strip():
            return
        x, y = _apply(cm, tm[4], tm[5])
        if raw and abs(raw[-1][0] - x) <= 0.5 and abs(raw[-1][1] - y) <= 0.5:
            raw[-1][3] += text
            return
        size = font_size * math.hypot(tm[0], tm[1]) * math.hypot(cm[0], cm[1])
        raw.append([x, y, size, text, list(tm), list(cm), font_size])

    def on_operator(operator: bytes, operands: Any, cm: list[float], _tm: Any) -> None:
        if operator == b"Do" and operands:
            name = str(operands[0])
            obj = xobjects.get(name)
            obj = obj.get_object() if obj is not None else None
            subtype = str(obj.get("/Subtype")) if isinstance(obj, DictionaryObject) else "?"
            drawn.append(_Drawn(name, _unit_square(cm), subtype))

    page.extract_text(visitor_text=on_text, visitor_operand_before=on_operator)
    runs = [_Run(round(x, 1), round(y, 1), round(size, 1), _clean(text), text_box(text, fs, tm, cm))
            for x, y, size, text, tm, cm, fs in raw if _clean(text)]
    annots: list[Box] = []
    for ref in page.get("/Annots") or []:
        annot = ref.get_object()
        rect = annot.get("/Rect") if isinstance(annot, DictionaryObject) else None
        if rect is not None and len(rect) == 4:
            x0, y0, x1, y1 = (float(v) for v in rect)
            annots.append(Box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
    return runs, drawn, annots


def _heading(runs: Sequence[_Run]) -> tuple[str, float, float, list[_Run]] | None:
    """A design page's "Design: <name>" heading, joined across wrapped lines: name, size, bottom, runs."""
    tops = [r for r in runs if r.text.startswith("Design:")]
    if not tops:
        return None
    top = max(tops, key=lambda r: (r.y, r.size))
    parts, last, used = [top.text.removeprefix("Design:")], top, [top]
    while below := [r for r in runs if abs(r.x - top.x) <= 1 and abs(r.size - top.size) <= 0.5
                    and 0 < last.y - r.y <= 1.6 * top.size and not r.text.startswith("Wall ")]:
        last = max(below, key=lambda r: r.y)
        parts.append(last.text)
        used.append(last)
    return _clean(" ".join(parts)), top.size, last.y, used


def _inches(text: str) -> float:
    whole, _, frac = text.partition(" ")
    value = float(whole)
    if frac:
        num, den = frac.split("/")
        value += int(num) / int(den)
    return value


def _footer_top(runs: Sequence[_Run]) -> float:
    """The top of the store footer: real footers sit below 100 pt in both layouts, and a client's
    COMPANY field on page 1 can name the store too, so only the bottom band is looked at."""
    marks = [r.y for r in runs if r.y < FOOTER_BAND
             and (_PAGE_NUMBER.match(r.text) or "inspired closets" in r.text.casefold())]
    return max(marks) if marks else -math.inf


def _tables(runs: Sequence[_Run], floor: float) -> list[tuple[str | None, list[PartLine]]]:
    """Each parts table on the page as (its "Wall <name>" label, its lines), top to bottom."""
    heads = sorted((r for r in runs if r.text.upper() == "ITEM #"), key=lambda r: (-r.y, r.x))
    header_rows: list[list[_Run]] = []
    for head in heads:
        if header_rows and abs(header_rows[-1][0].y - head.y) <= 1:
            header_rows[-1].append(head)
        else:
            header_rows.append([head])
    labels = [r for r in runs if r.text.startswith("Wall ") and r.size < 16]
    found: list[tuple[str | None, list[PartLine]]] = []
    for k, row in enumerate(header_rows):
        hy = row[0].y
        above = [r for r in labels if hy < r.y <= hy + 40]
        label = min(above, key=lambda r: r.y).text.removeprefix("Wall ").strip() if above else None
        if k + 1 < len(header_rows):
            ny = header_rows[k + 1][0].y
            next_labels = [r.y for r in labels if ny < r.y <= ny + 40]
            bottom = max(next_labels) if next_labels else ny
        else:
            bottom = floor
        lines: list[PartLine] = []
        groups = sorted(row, key=lambda r: r.x)
        for g, group in enumerate(groups):
            right = groups[g + 1].x - 3 if g + 1 < len(groups) else math.inf
            columns = sorted(((r.x, _COLUMNS[r.text.upper()]) for r in runs
                              if abs(r.y - hy) <= 1 and r.text.upper() in _COLUMNS
                              and group.x - 3 <= r.x < right), key=lambda c: c[0])
            body = [r for r in runs if bottom + 1 < r.y < hy - 1 and group.x - 3 <= r.x < right
                    and not r.text.startswith(("Design:", "Wall "))]
            lines += _rows(body, columns)
        found.append((label, lines))
    return found


def _rows(body: Sequence[_Run], columns: Sequence[tuple[float, str]]) -> list[PartLine]:
    rows: list[list[_Run]] = []
    for run in sorted(body, key=lambda r: (-r.y, r.x)):
        if rows and abs(rows[-1][0].y - run.y) <= 1:
            rows[-1].append(run)
        else:
            rows.append([run])
    lines: list[PartLine] = []
    for row in rows:
        cells: dict[str, str] = {}
        for run in row:
            col = [name for x, name in columns if x <= run.x + 3]
            if col:
                cells[col[-1]] = _clean(f"{cells.get(col[-1], '')} {run.text}")
        desc, qty_text, item = cells.get("desc", ""), cells.get("qty", ""), cells.get("item", "")
        if desc and not qty_text and not item and lines:  # a description wrapped onto its own line
            lines[-1] = PartLine(lines[-1].qty, _clean(f"{lines[-1].desc} {desc}"))
        elif desc:
            lines.append(PartLine(int(qty_text) if qty_text.isdigit() else None, desc))
    return lines


# ----- page 1 and headers: the deny list


def _page_one_entries(runs: Sequence[_Run]) -> list[tuple[str, str]]:
    labels = [r for r in runs if _LABEL.match(r.text) and not _PAGE_NUMBER.match(r.text)]
    if not labels:
        return [("page 1", r.text) for r in runs if not _PAGE_NUMBER.match(r.text)]
    label_x = min(r.x for r in labels)
    entries: list[tuple[str, str]] = []
    for run in runs:
        if run in labels or _PAGE_NUMBER.match(run.text):
            continue
        if run.x < label_x - 20:
            entries.append(("designer", run.text))
            continue
        owners = [lab for lab in labels if lab.y >= run.y - 1 and lab.x < run.x]
        owner = min(owners, key=lambda lab: lab.y).text if owners else ""
        entries.append(("design name" if owner == "DESIGNS IN PROJECT" else "page 1", run.text))
    return entries


# ----- the whole summary


def project_name(data: bytes) -> str | None:
    """Page 1's PROJECT NAME value, and nothing else from page 1; None when the label is absent."""
    runs, _, _ = _read_page(PdfReader(io.BytesIO(data)).pages[0])
    label = next((r for r in runs if r.text == "PROJECT NAME"), None)
    if label is None:
        return None
    beside = sorted((r for r in runs if abs(r.y - label.y) <= 1 and r.x > label.x + 1), key=lambda r: r.x)
    return " ".join(r.text for r in beside) or None


@dataclass
class _Elevation:
    page: int
    design: int
    design_key: str
    design_name_key: str
    wall: str
    runs: list[_Run]
    drawn: list[_Drawn]
    annots: list[Box]
    images: dict[str, Image.Image]
    parts: list[PartLine] = field(default_factory=list)


def prepare(data: bytes) -> SummaryPrep:
    """Every elevation page of the summary as a contained crop, or a Refusal saying why not."""
    reader = PdfReader(io.BytesIO(data))
    pages = list(reader.pages)
    entries: list[tuple[str, str]] = []
    design_order: dict[str, int] = {}
    wall_sizes: dict[tuple[str, str], tuple[float, float]] = {}
    wall_tables: dict[str, list[tuple[str, float]]] = {}
    design_names: dict[str, str] = {}
    elevations: list[_Elevation] = []
    unlabelled = orphans = 0
    for number, page in enumerate(pages, 1):
        runs, drawn, annots = _read_page(page)
        floor = _footer_top(runs)
        entries += [("footer", r.text) for r in runs if r.y <= floor + 0.5]
        entries += [("project", r.text) for r in runs if r.text.startswith("Project:")]
        entries += [("design name", r.text) for r in runs if r.text.startswith("Design:")]
        if number == 1:
            entries += _page_one_entries([r for r in runs if r.y > floor + 0.5])
            continue
        heading = _heading(runs)
        if heading is not None:
            name, size, bottom, used = heading
            entries += [("design name", r.text) for r in used]
            key = name_key_hash(name)
            design_order.setdefault(key, len(design_order))
            design_names.setdefault(key, name_key(name))
            table = next((r for r in runs if r.text == "WALL/AREA NAME (W X H)"), None)
            titles = [r for r in runs if r.size > size + 0.5 and r.y < bottom]
            if table is not None:
                for r in runs:
                    if abs(r.x - table.x) <= 2 and r.y < table.y and (m := _WALL_AREA.match(r.text)):
                        wall_sizes[(key, m["name"])] = (_inches(m["w"]), _inches(m["h"]))
                        wall_tables.setdefault(key, []).append((m["name"], _inches(m["w"])))
            elif titles and (title := max(titles, key=lambda r: r.y).text).startswith("Wall "):
                images = {im.name.rsplit(".", 1)[0]: im.image for im in page.images
                          if any(d.name.lstrip("/") == im.name.rsplit(".", 1)[0] for d in drawn
                                 if max(d.rect.x1 - d.rect.x0, d.rect.y1 - d.rect.y0) >= DRAWING_MIN)}
                elevations.append(_Elevation(number, design_order[key], key, design_names[key],
                                             title.removeprefix("Wall ").strip(), runs, drawn, annots, images))
        for label, lines in _tables(runs, floor):
            if label is None:
                unlabelled += 1
            elif elevations and label == elevations[-1].wall:
                elevations[-1].parts += lines
            else:
                orphans += 1
    crops: list[WallCrop] = []
    refused: list[Refusal] = []
    for elevation in elevations:
        result = _contain(elevation, wall_sizes.get((elevation.design_key, elevation.wall)),
                          tuple(wall_tables.get(elevation.design_key, ())), tuple(design_names.values()))
        (crops if isinstance(result, WallCrop) else refused).append(result)
    return SummaryPrep(tuple(crops), tuple(refused), DenyList(entries), len(pages), len(elevations),
                       unlabelled, orphans)


def _contain(e: _Elevation, size: tuple[float, float] | None, table: tuple[tuple[str, float], ...],
             designs: tuple[str, ...] = ()) -> WallCrop | Refusal:
    if any(d.subtype == "/Form" for d in e.drawn):
        return Refusal(e.page, "form_on_page", "The page draws a form, which prep does not read inside.")
    big = [d for d in e.drawn if d.subtype == "/Image"
           and max(d.rect.x1 - d.rect.x0, d.rect.y1 - d.rect.y0) >= DRAWING_MIN]
    if not big:
        return Refusal(e.page, "no_drawing", "The elevation page holds no drawing.")
    if len(big) > 1:
        return Refusal(e.page, "several_drawings", "The elevation page holds more than one drawing.")
    rect = big[0].rect
    if any(r.box.overlaps(rect) for r in e.runs):
        return Refusal(e.page, "text_over_drawing", "Text on the page overlaps the drawing.")
    if any(a.overlaps(rect) for a in e.annots):
        return Refusal(e.page, "annotation_over_drawing", "An annotation overlaps the drawing.")
    img = e.images.get(big[0].name.lstrip("/"))
    if img is None:
        return Refusal(e.page, "unreadable_drawing", "The drawing could not be decoded.")
    if img.mode != "RGB":
        flat = Image.new("RGB", img.size, "white")
        flat.paste(img, mask=img.getchannel("A") if "A" in img.getbands() else None)
        img = flat
    native = img.size
    try:
        fitted, trim = fit_to_budget(img)
    except TooLarge as exc:
        return Refusal(e.page, "too_large", f"The drawing is too large to send without scaling: {exc}.")
    buf = io.BytesIO()
    fitted.save(buf, format="PNG")
    png = buf.getvalue()
    _PREPARED.add(hashlib.sha256(png).hexdigest())
    return WallCrop(e.page, e.design, e.design_key, e.wall, size[0] if size else None,
                    size[1] if size else None, png, fitted.size, native, trim,
                    hashlib.sha256(png).hexdigest(), tuple(e.parts), table, e.design_name_key, designs)


# ----- enlarged tiles of a crop (design 3.6: the WIDTH/DEPTH strip and the verticals, enlarged)

TILE_ZOOM = 2.0
TILE_MAX = 12  # vertical tiles per wall at most; a wall with more gets none (too busy to tile)
RUN_FRACTION = 0.15  # a vertical is a column inked over at least this share of the wall box's height


@dataclass(frozen=True, repr=False)
class Tile:
    """An enlarged part of a contained crop: it adds no pixel the crop did not hold."""
    kind: str  # "strip" or "vertical"
    png: bytes
    sha256: str
    pixels: tuple[int, int]
    box: tuple[int, int, int, int]  # left, top, right, bottom in the crop's own pixels

    def __repr__(self) -> str:
        return f"Tile({self.kind}, box={self.box}, pixels={self.pixels}, sha256={self.sha256[:12]})"


def _enlarged(img: Image.Image, box: tuple[int, int, int, int]) -> tuple[Image.Image, tuple[int, int, int, int]]:
    part = img.crop(box)
    w, h = part.size
    zoom = min(TILE_ZOOM, MAX_SIDE / max(w, h))
    while zoom > 1.0 and visual_tokens(round(w * zoom), round(h * zoom)) > MAX_VISUAL_TOKENS:
        zoom -= 0.1
    zoom = max(zoom, 1.0)
    if visual_tokens(round(w * zoom), round(h * zoom)) > MAX_VISUAL_TOKENS:
        raise TooLarge("a tile is over the budget even unenlarged")
    return part.resize((round(w * zoom), round(h * zoom)), Image.Resampling.LANCZOS), box


def tiles(crop: WallCrop) -> tuple[Tile, ...]:
    """
    The crop's WIDTH/DEPTH strip (below the wall box) and one tile around each vertical, enlarged
    up to 2 times within Claude's limits. Each is a part of the crop, which containment already
    proved clean, and each is registered as prepared, so payload.py may send it. A crop whose wall
    box or verticals can't be found gets fewer tiles, never a guess.
    """
    img = Image.open(io.BytesIO(crop.png)).convert("RGB")
    w, h = img.size
    ink = img.convert("L").tobytes().translate(_INK)
    box_cols = [x for x in range(w) if ink[x::w].count(1) > BOX_COLUMN * h]
    if not box_cols:
        return ()
    xl, xr = min(box_cols), max(box_cols)
    span = xr - xl + 1
    full = [y for y in range(h) if ink[y * w + xl: y * w + xr + 1].count(1) >= 0.9 * span]
    if not full:
        return ()
    ytop, ybot = min(full), max(full)
    out: list[tuple[str, Image.Image, tuple[int, int, int, int]]] = []
    if h - ybot > 8:
        part, box = _enlarged(img, (0, ybot, w, h))
        out.append(("strip", part, box))
    height = ybot - ytop
    columns = []
    for x in range(xl + 2, xr - 1):
        col = ink[x::w][ytop:ybot]
        if max((len(run) for run in col.split(b"\x00")), default=0) >= RUN_FRACTION * height:
            columns.append(x)
    groups: list[list[int]] = []
    for x in columns:
        if groups and x - groups[-1][-1] <= max(4, w // 100):
            groups[-1].append(x)
        else:
            groups.append([x])
    if 0 < len(groups) <= TILE_MAX:
        half = max(40, span // 16)
        for g in groups:
            cx = (g[0] + g[-1]) // 2
            part, box = _enlarged(img, (max(0, cx - half), max(0, ytop - 4), min(w, cx + half), h))
            out.append(("vertical", part, box))
    made: list[Tile] = []
    for kind, part, box in out:
        buf = io.BytesIO()
        part.save(buf, format="PNG")
        png = buf.getvalue()
        sha = hashlib.sha256(png).hexdigest()
        _PREPARED.add(sha)
        made.append(Tile(kind, png, sha, part.size, box))
    return tuple(made)
