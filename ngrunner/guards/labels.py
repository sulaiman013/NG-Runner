"""
Do the labels the nest printed name parts that are in the saved design? A one-direction fallback.

Why it exists: the real match is the nest's parts against the design's cutlist, and it needs .opt
and .des files that can't be read yet (docs/p1b/build-spec.md, sections 1 and 5.9). Labels and the
parts catalog are readable now, so they can show one thing: a part nested but not in the design.

It is not the design's match, and it can't stand in for it. It sees one direction only, since the
catalog also lists drawer box parts that are never nested. Its catalog has a date with no time and
no material, and it holds no operations. So nothing here can ever set Nest checked or raise DO NOT
CUT: DesignMatch.proves_nest is fixed to False, and even all_found says the nest itself isn't
checked. A part that can't be found reads "could not verify" and adds one line to the pre-flight.

The catalog lists cabinets only: all 3,513 rows of it read R#C#. Room 0's labels read R0N#, so
Room 0 is compared by name and size. A label elsewhere with no cabinet (a counter top, R8N0) has
nothing in the catalog to be compared with, so it is counted as not compared, never as found or as
missing.

No design name is kept: on a label it holds the client's surname and often a person's name.
"""
from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from fractions import Fraction
from io import BytesIO, StringIO
from typing import ClassVar, Literal

from pypdf import PdfReader

MM_PER_INCH = 25.4
TOLERANCE_IN = 1 / 8            # the match's limit, on sizes held to the nearest 1/16in
_CATALOG_COLUMNS = frozenset({"Job", "Part", "Pieces", "JobModified"})


@dataclass(frozen=True)
class LabelPart:
    """One printed label: its part with any (1\\14) count taken off, in inches."""

    part: str
    name: str
    w_in: float
    l_in: float
    material: str
    sheet: str


@dataclass(frozen=True)
class Piece:
    """One piece of the parts catalog, in inches."""

    part: str
    name: str
    w_in: float
    l_in: float
    qty: int


Code = Literal["all_found", "some_missing", "could_not_read"]
_NOT_THE_NEST = "The nest itself is not checked."


@dataclass(frozen=True)
class DesignMatch:
    """What the labels show. missing holds part and name, never a design's. Whatever the code, this
    proves nothing about the nest."""

    code: Code
    missing: tuple[str, ...] = ()
    compared: int = 0
    not_compared: int = 0

    proves_nest: ClassVar[Literal[False]] = False

    @property
    def note(self) -> str:
        """The board's line."""
        if self.code == "some_missing":
            count = len(self.missing)
            noun = "label" if count == 1 else "labels"
            return f"Could not verify {count} {noun} against the parts catalog. {_NOT_THE_NEST}"
        if self.code == "could_not_read":
            return f"Could not verify the labels against the parts catalog. {_NOT_THE_NEST}"
        if self.not_compared:
            return (f"Every label compared is in the parts catalog; {self.not_compared} not"
                    f" compared. {_NOT_THE_NEST}")
        return f"Every label is in the parts catalog. {_NOT_THE_NEST}"


# ----- reading a labels file


_REMNANT = re.compile(r"Remnant\s")
_PART = re.compile(r"(?P<part>R(?P<room>[0-9]+)(?P<kind>[CN])[0-9]+)(?:\([0-9]+\\[0-9]+\))?")
_MEASURE = r"[0-9]+ [0-9]+/[0-9]+|[0-9]+/[0-9]+|[0-9]+"
_SIZE = re.compile(rf"(?P<name>.+?) (?P<w>{_MEASURE}) x (?P<l>{_MEASURE})")


def read_labels(data: bytes) -> tuple[LabelPart, ...]:
    """Every part in a labels PDF. A page that isn't an offcut and doesn't read as a label gives
    nothing for the whole file, never a partial list: the page lost might be the mismatch."""
    try:
        pages = [page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages]
    except Exception:  # noqa: BLE001 - pypdf raises many kinds on a bad file; all mean "can't read"
        return ()
    parts: list[LabelPart] = []
    for text in pages:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if any(_REMNANT.match(line) for line in lines):
            continue
        part = _read_page(lines)
        if part is None:
            return ()
        parts.append(part)
    return tuple(parts)


def _read_page(lines: Sequence[str]) -> LabelPart | None:
    """The part's line, its size on the next, material and edge band on the next, then the sheet."""
    at = [i for i, line in enumerate(lines) if _PART.fullmatch(line)]
    if len(at) != 1 or at[0] + 3 >= len(lines):
        return None
    part_line, size_line, material_line, sheet = lines[at[0]: at[0] + 4]
    size = _SIZE.fullmatch(size_line)
    material = material_line.split()
    if size is None or len(material) < 2:
        return None
    return LabelPart(part=_PART.fullmatch(part_line)["part"], name=size["name"],
                     w_in=_inches(size["w"]), l_in=_inches(size["l"]),
                     material=" ".join(material[:-1]), sheet=sheet)


def _inches(text: str) -> float:
    return float(sum(Fraction(part) for part in text.split()))


# ----- reading the parts catalog


def read_catalog(data: bytes) -> tuple[Mapping[str, str], ...]:
    """parts_catalog.csv's rows, or nothing when it doesn't read as measured."""
    try:
        reader = csv.DictReader(StringIO(data.decode("utf-8-sig"), newline=""))
        if not _CATALOG_COLUMNS <= set(reader.fieldnames or ()):
            return ()
        return tuple(reader)
    except (UnicodeDecodeError, csv.Error):
        return ()


def catalog_pieces(rows: Iterable[Mapping[str, str]], job: str) -> tuple[Piece, ...]:
    """The job's pieces. A piece that can't be read is left out, never guessed: a label that needed
    it then reads "could not verify"."""
    pieces: list[Piece] = []
    for row in rows:
        if row.get("Job") == job:
            for text in (row.get("Pieces") or "").split(" | "):
                piece = _read_piece(row.get("Part", ""), text)
                if piece:
                    pieces.append(piece)
    return tuple(pieces)


def _read_piece(part: str, text: str) -> Piece | None:
    bits = text.strip().rsplit("~", 3)
    if len(bits) != 4 or not bits[0]:
        return None
    try:
        return Piece(part=part, name=bits[0], w_in=float(bits[1]) / MM_PER_INCH,
                     l_in=float(bits[2]) / MM_PER_INCH, qty=int(float(bits[3])))
    except (ValueError, OverflowError):     # int(float("inf")) is an overflow
        return None


def catalog_date(rows: Iterable[Mapping[str, str]], job: str) -> date | None:
    """The job's last design date as the catalog has it: a date with no time, from Jim's PC. None
    when the job has no row or no date that reads."""
    days = [day for row in rows if row.get("Job") == job
            if (day := _day(row.get("JobModified", ""))) is not None]
    return max(days, default=None)


def _day(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


# ----- the match


def labels_in_design(labels: Sequence[LabelPart], pieces: Sequence[Piece]) -> DesignMatch:
    """Each label against the catalog, one direction only: a key of part and sorted sizes, within
    1/8in. Room 0 goes by name and size. A label the catalog has no form for is not compared."""
    if not labels or not pieces:
        return DesignMatch("could_not_read")
    missing: list[str] = []
    compared = not_compared = 0
    for label in labels:
        if not _catalog_has_a_form_for(label.part):
            not_compared += 1
            continue
        compared += 1
        if not any(_is_this_piece(label, piece) for piece in pieces):
            missing.append(f"{label.part} {label.name}")
    if not compared:
        return DesignMatch("could_not_read", not_compared=not_compared)
    return DesignMatch("some_missing" if missing else "all_found",
                       tuple(dict.fromkeys(missing)), compared, not_compared)


def _catalog_has_a_form_for(part: str) -> bool:
    found = _PART.fullmatch(part)
    return found is not None and (found["kind"] == "C" or found["room"] == "0")


def _in_room_0(part: str) -> bool:
    found = _PART.fullmatch(part)
    return found is not None and found["room"] == "0"


def _is_this_piece(label: LabelPart, piece: Piece) -> bool:
    if _in_room_0(label.part):
        same = _in_room_0(piece.part) and piece.name == label.name
    else:
        same = piece.part == label.part
    return same and _same_size(label, piece)


def _same_size(label: LabelPart, piece: Piece) -> bool:
    ours = sorted((_sixteenths(label.w_in), _sixteenths(label.l_in)))
    theirs = sorted((_sixteenths(piece.w_in), _sixteenths(piece.l_in)))
    return (abs(ours[0] - theirs[0]) <= TOLERANCE_IN and abs(ours[1] - theirs[1]) <= TOLERANCE_IN)


def _sixteenths(inches: float) -> float:
    return round(inches * 16) / 16


# ----- which labels count


def newest_per_material(files: Iterable[tuple[datetime, Sequence[LabelPart]]]
                        ) -> tuple[LabelPart, ...]:
    """A job's newest file per material (when it was modified, its parts). Older jobs hold several
    overlapping files whose counts aren't clean, so each material is read from its newest. One
    unreadable file gives nothing: its material might be the one that doesn't match."""
    ordered = sorted(files, key=lambda item: item[0], reverse=True)
    if any(not parts for _, parts in ordered):
        return ()
    chosen: dict[str, list[LabelPart]] = {}
    for _, parts in ordered:
        here: dict[str, list[LabelPart]] = {}
        for part in parts:
            here.setdefault(part.material, []).append(part)
        for material, found in here.items():
            chosen.setdefault(material, found)
    return tuple(part for found in chosen.values() for part in found)
