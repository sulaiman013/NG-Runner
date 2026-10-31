"""
The tracker snapshots' totals rows, and BUILD's four equalities (BUILD 746-753; design 5, "After a
drop": one totals row, and the four equalities).

Why it exists: a batch can land row for row and still leave the tracker wrong in its headline number.
BUILD found two GRAND TOTAL rows doubling the total, and a row inserted outside its total's range
silently left out. So after a drop the snapshot's totals are read too: exactly one closing row on each
tab, no row below it, and

    Job Summary GRAND TOTAL  Sheet Mat  == Sheet Goods TOTALS  Material Cost
                             EB Cost    == Edgeband TOTALS     EB Cost
                             Labor      == Sheet Goods TOTALS  Labor Cost
                             Total Cost == Sheet Mat + EB Cost + Labor

each to the cent. guards/tracker.py reads the rows and stops at the first closing row, so it can't see
a second one: this reads the same files for their closing rows only, columns found by their header's
words, and nothing else (no customer, no note).
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from ngrunner.guards.tracker import TrackerUnreadable

_CLOSING = {"SheetGoods": "TOTALS", "Edgeband": "TOTALS", "JobSummary": "GRAND TOTAL"}
_COLUMNS = {"SheetGoods": ("Material Cost ($)", "Labor Cost ($)"), "Edgeband": ("EB Cost ($)",),
            "JobSummary": ("Sheet Mat ($)", "EB Cost ($)", "Labor ($)", "Total Cost ($)")}
_NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
_CENT = 0.005 + 1e-9


@dataclass(frozen=True)
class Totals:
    """Each tab's closing row as read: its numbers by header words (None: not a number), how many
    closing rows the tab has, and how many rows with a job sit below the first of them."""

    numbers: dict[str, dict[str, float | None]]
    closing_rows: dict[str, int]
    rows_below: dict[str, int]


def read_totals(folder: Path) -> Totals:
    """The totals rows of the snapshot in folder. Raises TrackerUnreadable for a tab with no header,
    a column lost, or no closing row at all."""
    numbers, closing, below = {}, {}, {}
    for tab, label in _CLOSING.items():
        rows = _rows(folder / f"tracker_snapshot_{tab}.csv", tab)
        start = next((i for i, row in enumerate(rows) if row and row[0].strip() == "Job"), None)
        if start is None:
            raise TrackerUnreadable(f"The tracker's {tab} snapshot has no header row")
        header = [" ".join(cell.split()) for cell in rows[start]]
        if not set(_COLUMNS[tab]) <= set(header):
            raise TrackerUnreadable(f"The tracker's {tab} snapshot has lost a totals column")
        ends = [i for i, row in enumerate(rows[start + 1:], start + 1)
                if row and row[0].strip() == label]
        if not ends:
            raise TrackerUnreadable(f"The tracker's {tab} snapshot has no totals row")
        first = rows[ends[0]]
        numbers[tab] = {name: _number(first[header.index(name)] if header.index(name) < len(first) else "")
                        for name in _COLUMNS[tab]}
        closing[tab] = len(ends)
        below[tab] = sum(1 for row in rows[ends[0] + 1:]
                         if row and row[0].strip() and row[0].strip() != label)
    return Totals(numbers, closing, below)


def four_equalities(totals: Totals) -> tuple[str, ...]:
    """What is wrong with the totals, in plain words; () when the four equalities hold on one totals
    row per tab with nothing below it."""
    problems = [f"The tracker's {tab} tab has {n} totals rows" for tab, n in totals.closing_rows.items()
                if n != 1]
    problems += [f"The tracker's {tab} tab has a row below its totals row"
                 for tab, n in totals.rows_below.items() if n]
    sheet, tape, summary = (totals.numbers[tab] for tab in ("SheetGoods", "Edgeband", "JobSummary"))
    pairs = (("Sheet material", summary["Sheet Mat ($)"], sheet["Material Cost ($)"]),
             ("Edgeband", summary["EB Cost ($)"], tape["EB Cost ($)"]),
             ("Labor", summary["Labor ($)"], sheet["Labor Cost ($)"]))
    for what, grand, tab_total in pairs:
        if grand is None or tab_total is None:
            problems.append(f"{what} has no total to compare")
        elif abs(grand - tab_total) > _CENT:
            problems.append(f"{what} on the Job Summary doesn't equal its own tab's total")
    parts = [summary["Sheet Mat ($)"], summary["EB Cost ($)"], summary["Labor ($)"]]
    total = summary["Total Cost ($)"]
    if total is None or None in parts:
        problems.append("The grand total could not be compared")
    elif abs(total - sum(p for p in parts if p is not None)) > _CENT:
        problems.append("The grand total isn't sheet material, edgeband and labor added up")
    return tuple(problems)


def _rows(path: Path, tab: str) -> list[list[str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as source:
            return list(csv.reader(source))
    except (OSError, UnicodeDecodeError, csv.Error):
        raise TrackerUnreadable(f"The tracker's {tab} snapshot could not be read") from None


def _number(cell: str) -> float | None:
    text = (cell or "").strip().replace("$", "").replace(",", "")
    return float(text) if _NUMBER.fullmatch(text) else None
