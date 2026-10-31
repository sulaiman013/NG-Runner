"""
Reading the tracker snapshots: the CSVs Jim's Materials Tracker exports to Drive every 15 minutes.

Why it exists: the lock pre-flight has to say what the tracker holds without touching it, and these
files are the only way to read it (docs/p1b/build-spec.md, section 3.5). They are display values
with title rows on top, so columns are found by their header's words and a tab that doesn't read as
measured raises TrackerUnreadable instead of giving part of an answer.

It keeps only what the pre-flight needs. The Customer and Notes columns are never read, so a
client's name can't travel from here to a board or a message. Materials and status words stay
exactly as typed, since the tracker's INDEX/MATCH compares them exactly: a trailing space is a
different key. plain_material() is the one place that makes a name fit for a person to read.
"""
from __future__ import annotations

import csv
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ngrunner.clock import utc


class TrackerUnreadable(Exception):
    """The snapshot isn't there or doesn't read as measured. Its text is plain, with no path."""


# ----- what a snapshot holds


@dataclass(frozen=True)
class SheetRow:
    """One row of the Sheet Goods tab. A number that isn't there (blank, a dash) is None."""

    job: str
    material: str
    calc_sheets: float | None
    optimizer_sheets: float | None
    price: float | None
    cost: float | None
    status: str


@dataclass(frozen=True)
class TapeRow:
    """One row of the Edgeband tab."""

    job: str
    material: str
    calc_feet: float | None
    optimizer_feet: float | None
    price: float | None
    cost: float | None
    status: str


@dataclass(frozen=True)
class JobRow:
    """One row of the Job Summary tab: its status as typed, emoji and all."""

    job: str
    status: str


@dataclass(frozen=True)
class PriceKey:
    """One line of a Pricing block, its name kept exactly."""

    material: str
    price: float | None


@dataclass(frozen=True)
class TrackerSnapshot:
    taken: datetime
    last_update: datetime | None
    sheets: tuple[SheetRow, ...]
    tape: tuple[TapeRow, ...]
    jobs: tuple[JobRow, ...]
    sheet_prices: tuple[PriceKey, ...]
    tape_prices: tuple[PriceKey, ...]

    def status_spellings(self) -> tuple[str, ...]:
        """Each different status on the Job Summary tab, compared exactly; blanks don't count."""
        return tuple(dict.fromkeys(row.status for row in self.jobs if row.status.strip()))

    def keys_spelled_twice(self) -> tuple[str, ...]:
        """Pricing names that appear more than once in a block once their edges are trimmed.
        INDEX/MATCH treats each spelling as its own key, so two can carry two prices."""
        twice: list[str] = []
        for block in (self.sheet_prices, self.tape_prices):
            counts = Counter(key.material.strip() for key in block)
            twice += [name for name, count in counts.items() if count > 1]
        return tuple(twice)

    def keys_without_price(self) -> tuple[str, ...]:
        """Pricing names with no number beside them."""
        return tuple(key.material for key in (*self.sheet_prices, *self.tape_prices)
                     if key.price is None)


# ----- reading


def read_tracker(folder: Path) -> TrackerSnapshot:
    """The snapshot in folder. Raises TrackerUnreadable rather than give part of one."""
    taken, last_update = _read_meta(folder)
    pricing = _rows(folder, "Pricing")
    return TrackerSnapshot(
        taken=taken,
        last_update=last_update,
        sheets=tuple(_sheet_rows(folder)),
        tape=tuple(_tape_rows(folder)),
        jobs=tuple(_job_rows(folder)),
        sheet_prices=_price_block(pricing, "SHEET GOODS"),
        tape_prices=_price_block(pricing, "EDGEBAND"),
    )


_SPOKEN = {"SheetGoods": "Sheet Goods", "Edgeband": "Edgeband", "JobSummary": "Job Summary",
           "Pricing": "Pricing", "meta": "time stamp"}
_CLOSING = frozenset({"TOTALS", "GRAND TOTAL"})


def _unreadable(tab: str, why: str) -> TrackerUnreadable:
    return TrackerUnreadable(f"The tracker's {_SPOKEN[tab]} snapshot {why}")


def _rows(folder: Path, tab: str) -> list[list[str]]:
    path = folder / f"tracker_snapshot_{tab}.csv"
    try:
        with path.open(encoding="utf-8-sig", newline="") as source:
            return list(csv.reader(source))
    except (OSError, UnicodeDecodeError, csv.Error):
        raise _unreadable(tab, "could not be read") from None


def _read_meta(folder: Path) -> tuple[datetime, datetime | None]:
    meta = {row[0]: row[1] for row in _rows(folder, "meta") if len(row) >= 2}
    taken = _time(meta.get("snapshot_taken", ""))
    if taken is None:
        raise _unreadable("meta", "has no time it was taken")
    return taken, _time(meta.get("sheet_lastupdate", ""))


def _time(text: str) -> datetime | None:
    """2026-10-30T23:51:34Z. Text with no zone is no time at all: it can't be placed."""
    try:
        return utc(datetime.fromisoformat(text.strip()))
    except ValueError:
        return None


def _table(folder: Path, tab: str, wanted: Sequence[str]) -> list[dict[str, str]]:
    """The rows of a tab as header words to cells. Header cells have new lines in them (Calc\\n
    Sheets), so they are compared by their words. Title rows sit above the row whose first cell is
    Job; a closing row ends the tab, and a tab with none was cut short."""
    rows = _rows(folder, tab)
    start = next((i for i, row in enumerate(rows) if row and row[0].strip() == "Job"), None)
    if start is None:
        raise _unreadable(tab, "has no header row")
    header = [" ".join(cell.split()) for cell in rows[start]]
    if not set(wanted) <= set(header):
        raise _unreadable(tab, "has lost a column")
    end = next((i for i, row in enumerate(rows[start + 1:], start + 1)
                if row and row[0].strip() in _CLOSING), None)
    if end is None:
        raise _unreadable(tab, "was cut short")
    return [dict(zip(header, row)) for row in rows[start + 1:end]
            if row and row[0].strip()]


_NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")


def _number(cell: str | None) -> float | None:
    """$1,071.33 and 58.0% as numbers. A blank, a dash or words are no number."""
    text = (cell or "").strip().replace("$", "").replace(",", "").rstrip("%")
    return float(text) if _NUMBER.fullmatch(text) else None


def _is_void(material: str) -> bool:
    return material.lstrip().upper().startswith("VOID")


def _sheet_rows(folder: Path) -> list[SheetRow]:
    table = _table(folder, "SheetGoods", ("Job", "Material", "Calc Sheets", "Optimizer Sheets",
                                          "$/Sheet", "Material Cost ($)", "Sheet Status"))
    return [SheetRow(row["Job"], row.get("Material", ""), _number(row.get("Calc Sheets")),
                     _number(row.get("Optimizer Sheets")), _number(row.get("$/Sheet")),
                     _number(row.get("Material Cost ($)")), row.get("Sheet Status", ""))
            for row in table if not _is_void(row.get("Material", ""))]


def _tape_rows(folder: Path) -> list[TapeRow]:
    table = _table(folder, "Edgeband", ("Job", "Material", "Calc Lin Ft", "Optimizer Lin Ft",
                                        "$/LF", "EB Cost ($)", "Status"))
    return [TapeRow(row["Job"], row.get("Material", ""), _number(row.get("Calc Lin Ft")),
                    _number(row.get("Optimizer Lin Ft")), _number(row.get("$/LF")),
                    _number(row.get("EB Cost ($)")), row.get("Status", ""))
            for row in table if not _is_void(row.get("Material", ""))]


def _job_rows(folder: Path) -> list[JobRow]:
    return [JobRow(row["Job"], row.get("Status", ""))
            for row in _table(folder, "JobSummary", ("Job", "Status"))]


def _price_block(rows: list[list[str]], title: str) -> tuple[PriceKey, ...]:
    """A Pricing block: its title, a header that starts Material, then keys down to the hint line
    that starts with a triangle. Only these two blocks hold materials: the hardware and third-party
    lists below them aren't read."""
    start = next((i for i, row in enumerate(rows)
                  if row and row[0].strip().upper().startswith(title)), None)
    if start is None or start + 1 >= len(rows) or not _cell(rows[start + 1]).startswith("Material"):
        raise _unreadable("Pricing", f"has lost its {title.title()} block")
    keys: list[PriceKey] = []
    for row in rows[start + 2:]:
        name = _cell(row)
        if name.startswith("▲"):
            return tuple(keys)
        if name.strip():
            keys.append(PriceKey(name, _number(row[1]) if len(row) > 1 else None))
    raise _unreadable("Pricing", f"has lost the end of its {title.title()} block")


def _cell(row: list[str]) -> str:
    return row[0] if row else ""


# ----- names for a person


def plain_material(material: str) -> str:
    """A material as a person reads it. The lint refuses an underscore, as it looks like a setting's
    name, so Tafisa Spring Blossom_Ore reads Tafisa Spring Blossom Ore."""
    return " ".join(material.replace("_", " ").split())


_TAPE_NAME = re.compile(r'(?P<colour>\S.*?) [0-9]+mm — (?P<width>[0-9/-]+")(?: / [0-9/-]+")?')


def split_tape_name(name: str) -> tuple[str, str] | None:
    """Tafisa 1mm — 15/16" / 1" as ("Tafisa", '15/16"'), or None when it isn't in that form. The
    gauge goes because the lint refuses a bare mm, and the second width is the same tape's other
    name."""
    found = _TAPE_NAME.fullmatch(name.strip())
    return (found["colour"], found["width"]) if found else None
