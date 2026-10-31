"""
The tracker inbox: the rows a takeoff becomes on Jim's Materials Tracker, and whether they landed.

Why it exists: no script of Jim's writes the inbox (docs/p2/build-spec.md, section 1 item 7); the
chat types each batch by hand. BUILD 817-837 gives the format: `ingestInbox()` in Tracker_Ingest.gs
reads `tracker_inbox.csv` from line 2 on, so a file with no header silently loses its first
directive and is still archived as ingested. So the header is always the first row here, and a
batch without one is refused. Its verbs and columns are BUILD's, checked against the 21 archived
inbox files (tests/test_tracker_inbox.py).

Client data never goes in. BUILD's customer column is left blank on every row, the job summary's
note too, and nothing is read from a takeoff but its buckets and its "could not place" list. Whether
the tracker takes a blank customer well is UNVERIFIED (section 10).

What a bucket is called on the tracker is Jim's rule, not this module's: RULES names colours by
their Tafisa name and says every material must match the Pricing block character for character.
So the caller passes names(colour, thickness), and a bucket it can't name refuses the whole batch,
never a guess. Two buckets under one name make one row with their sums, since the ingest's _SET
finds a row by job and material; two tape colours under one tape name are refused for the same
reason. Calc Sheets is area / 32, what the tracker's Calc Sheets holds (ruling R6), never the
sheets with waste.

Nothing here writes to Drive. write_inbox() writes one new file at the path its caller gives and
nothing else, and refuses anything inside the tracker's own folder: P2 never drops a batch there
(that waits for P4's Lock, through writes.commit_write with inbox_bytes()).

landed() reads a snapshot through guards/tracker.py, so it sees what the pre-flight sees: display
values, compared to within half their last shown place, and no Customer or Notes. A snapshot taken
before the drop judges nothing, since calling its rows missing would invite a second drop.
"""
from __future__ import annotations

import csv
import io
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple, Protocol

from ngrunner.clock import utc
from ngrunner.guards.tracker import (
    SheetRow,
    TapeRow,
    TrackerSnapshot,
    plain_material,
    split_tape_name,
)

# BUILD 817-837: line 1 of every batch, and each verb's columns (verb and job included).
HEADER = ("TAB", "JOB", "FIELD1", "FIELD2", "FIELD3", "FIELD4", "FIELD5", "FIELD6")
ROW_WIDTHS = {"SHEETGOODS": 8, "EDGEBAND": 7, "JOBSUMMARY": 5,
              "SHEETGOODS_SET": 7, "EDGEBAND_SET": 6, "JOBSUMMARY_SET": 4}
SQM_PER_SQFT = 0.09290304
SHEET_BASIS_SQFT = 32.0             # a 4x8 sheet: Calc Sheets is area / 32 (R6)
TRACKER_FOLDER = "NG Conversion program Claude"


class InboxRefused(Exception):
    """A batch that can't be made or written as asked. Its text is plain, with no path."""


class TrackerNames(NamedTuple):
    """What a bucket is called on the tracker, exactly as the Pricing block spells it. tape is ""
    for a bucket that takes none (1/4 backs); tape_colour is the Edgeband tab's EB Colour cell."""

    sheet: str
    tape: str
    tape_colour: str


class BucketFacts(Protocol):
    """What the inbox reads of a contract 5.3 Bucket, and nothing else."""

    @property
    def colour(self) -> str: ...
    @property
    def thk_in(self) -> float: ...
    @property
    def sqft(self) -> float: ...
    @property
    def calc_sheets_32(self) -> float: ...
    @property
    def tape_lf(self) -> float: ...


class TakeoffFacts(Protocol):
    """What the inbox reads of a contract 5.3 Takeoff: its buckets and what it could not place."""

    @property
    def buckets(self) -> Sequence[BucketFacts]: ...
    @property
    def unplaced(self) -> Sequence[object]: ...


# ----- the rows


def inbox_rows(job: str, takeoff: TakeoffFacts, *, verb: Literal["append", "set"],
               names: Callable[[str, float], TrackerNames | None], status: str,
               optimizer: Mapping[str, float] | None = None) -> list[list[str]]:
    """The batch for one job, header first. "append" adds rows for a job with none yet (Lock
    design); "set" fills existing ones (Finish lock), where a blank cell means "leave it alone".
    optimizer holds nest numbers by tracker name; without one that column stays blank. Raises
    InboxRefused rather than write a batch that is partly right."""
    if verb not in ("append", "set"):
        raise InboxRefused("A tracker batch either adds rows or sets them")
    job = _one_line(job, "The job name")
    status = _one_line(status, "The job's status") if status.strip() else ""
    if verb == "append" and not status:
        raise InboxRefused("New tracker rows need the job's status")
    if takeoff.unplaced:
        raise InboxRefused(f"The takeoff has {_count(len(takeoff.unplaced), 'line')} it could not"
                           " place, so its numbers can't go on the tracker")
    sheets, tapes = _totals(takeoff, names)
    given = dict(optimizer or {})
    unknown = set(given) - set(sheets) - {tape for tape, _ in tapes}
    if unknown:
        raise InboxRefused(f"{_count(len(unknown), 'nest number')} named a material the"
                           " takeoff does not have")
    adding = verb == "append"
    rows = [list(HEADER)]
    for name, (sqft, calc) in sheets.items():
        rows.append(_row("SHEETGOODS", job, adding, name, f"{sqft * SQM_PER_SQFT:.4f}",
                         f"{sqft:.2f}", f"{calc:.2f}", _optimizer(given.get(name), "sheets")))
    for (name, colour), feet in tapes.items():
        rows.append(_row("EDGEBAND", job, adding, name, f"{feet:.1f}",
                         _optimizer(given.get(name), "feet"), colour))
    if adding or status:
        rows.append(_row("JOBSUMMARY", job, adding, status, ""))   # the note stays blank
    return rows


def _row(tab: str, job: str, adding: bool, *fields: str) -> list[str]:
    """BUILD's row: an append has a customer column, left blank; a _SET has none."""
    return [tab, job, "", *fields] if adding else [f"{tab}_SET", job, *fields]


def _totals(takeoff: TakeoffFacts, names: Callable[[str, float], TrackerNames | None]
            ) -> tuple[dict[str, tuple[float, float]], dict[tuple[str, str], float]]:
    """Area and calc sheets by sheet name, feet by tape name and colour, in bucket order."""
    if not takeoff.buckets:
        raise InboxRefused("The takeoff has no board to put on the tracker")
    sheets: dict[str, tuple[float, float]] = {}
    tapes: dict[tuple[str, str], float] = {}
    for bucket in takeoff.buckets:
        named = names(bucket.colour, bucket.thk_in)
        if named is None or not named.sheet.strip():
            raise InboxRefused(f"No tracker name is known for the {_inches(bucket.thk_in)} board"
                               f" in colour {bucket.colour}")
        sqft, calc = sheets.get(named.sheet, (0.0, 0.0))
        sheets[named.sheet] = (sqft + _number(bucket.sqft), calc + _number(bucket.calc_sheets_32))
        feet = _number(bucket.tape_lf)
        if feet <= 0:
            continue
        if not named.tape.strip():
            raise InboxRefused(f"The {_inches(bucket.thk_in)} board in colour {bucket.colour}"
                               " has tape but no tracker name for it")
        key = (named.tape, named.tape_colour)
        tapes[key] = tapes.get(key, 0.0) + feet
    shared = [name for name, _ in tapes if sum(1 for other, _ in tapes if other == name) > 1]
    if shared:
        raise InboxRefused("Two tape colours share one tracker tape name, so the tracker could"
                           " not tell their rows apart")
    return sheets, tapes


def _number(value: float) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise InboxRefused("The takeoff holds a number that is not a size")
    return float(value)


def _optimizer(value: float | None, unit: str) -> str:
    if value is None:
        return ""
    number = _number(value)
    if unit == "sheets":
        return str(int(number)) if number.is_integer() else f"{number:.2f}"
    return f"{number:.1f}"


def _one_line(text: str, what: str) -> str:
    """A cell that is one line of text, with its edges trimmed."""
    if not text.strip() or any(ch in text for ch in "\r\n\t\v\f"):
        raise InboxRefused(f"{what} must be one line of text")
    return text.strip()


def _inches(thk_in: float) -> str:
    shown = {0.25: '1/4"', 0.375: '3/8"', 0.75: '3/4"', 1.0: '1"', 1.125: '1 1/8"'}
    return shown.get(thk_in, "odd thickness")


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


# ----- the file


def inbox_bytes(rows: Sequence[Sequence[str]]) -> bytes:
    """The batch as BUILD's file: UTF-8 with no BOM, CRLF, quoted only where it must be. This
    is what Python's csv writes, and what 14 of the 21 archives hold byte for byte."""
    text = io.StringIO(newline="")
    csv.writer(text, lineterminator="\r\n").writerows(rows)
    return text.getvalue().encode("utf-8")


def write_inbox(path: Path, rows: Sequence[Sequence[str]]) -> None:
    """Write the batch as a new file at path, and nothing else anywhere. Refuses a path inside
    the tracker's own folder, a file already there, a folder that isn't, and a batch the ingest
    would lose a row of or skip."""
    if not path.is_absolute():
        raise InboxRefused("The inbox file needs a full path")
    if any(part.casefold() == TRACKER_FOLDER.casefold() for part in path.parts):
        raise InboxRefused("Phase 2 never writes into the tracker's own folder")
    _check_batch(rows)
    try:
        with path.open("x", encoding="utf-8", newline="") as out:
            csv.writer(out, lineterminator="\r\n").writerows(rows)
    except FileExistsError:
        raise InboxRefused("An inbox file is already there; it was left as it is") from None
    except OSError:
        raise InboxRefused("The inbox file could not be written there") from None


def _check_batch(rows: Sequence[Sequence[str]]) -> None:
    if not rows or tuple(rows[0]) != HEADER:
        raise InboxRefused("A tracker batch must start with its header row, or the ingest loses"
                           " its first row")
    if len(rows) < 2:
        raise InboxRefused("A tracker batch needs at least one row to apply")
    for row in rows[1:]:
        if not row or ROW_WIDTHS.get(row[0]) != len(row):
            raise InboxRefused("A row of the batch is not one the tracker's ingest reads")


# ----- did it land


State = Literal["landed", "missing", "differs"]


@dataclass(frozen=True)
class RowLanding:
    """One row of a batch on one snapshot. line counts the header as line 1. why is "" when the
    row landed, and plain words otherwise."""

    line: int
    tab: str
    material: str
    state: State
    why: str


@dataclass(frozen=True)
class Landing:
    job: str
    taken: datetime
    early: bool                      # the snapshot predates the drop, so nothing was judged
    rows: tuple[RowLanding, ...]

    @property
    def all_landed(self) -> bool:
        return not self.early and bool(self.rows) and all(r.state == "landed" for r in self.rows)


# Where each verb keeps its material and numbers: (material, (column, reader field, label)...).
_SHEET_COLUMNS = {"SHEETGOODS": (3, ((6, "calc_sheets", "Calc Sheets"),
                                     (7, "optimizer_sheets", "Optimizer Sheets"))),
                  "SHEETGOODS_SET": (2, ((5, "calc_sheets", "Calc Sheets"),
                                         (6, "optimizer_sheets", "Optimizer Sheets")))}
_TAPE_COLUMNS = {"EDGEBAND": (3, ((4, "calc_feet", "Calc Lin Ft"),
                                  (5, "optimizer_feet", "Optimizer Lin Ft"))),
                 "EDGEBAND_SET": (2, ((3, "calc_feet", "Calc Lin Ft"),
                                      (4, "optimizer_feet", "Optimizer Lin Ft")))}
_STATUS_COLUMN = {"JOBSUMMARY": 3, "JOBSUMMARY_SET": 2}
_SHOWN = 0.05 + 1e-9        # the snapshot shows these to one place at least: half of it


def landed(job: str, rows: Sequence[Sequence[str]], snap: TrackerSnapshot, *,
           dropped: datetime) -> Landing:
    """Each of job's rows in the batch, as the snapshot shows it: landed, missing or differs.
    The header and other jobs' rows are skipped. dropped is when the batch was dropped; a
    snapshot taken before it judges nothing."""
    if snap.taken <= utc(dropped):
        return Landing(job, snap.taken, True, ())
    judged = [_judge(line, list(row), job, snap)
              for line, row in enumerate(rows, start=1)
              if row and tuple(row[:2]) != ("TAB", "JOB") and len(row) > 1
              and row[1].strip() == job.strip()]
    return Landing(job, snap.taken, False, tuple(judged))


def _judge(line: int, row: list[str], job: str, snap: TrackerSnapshot) -> RowLanding:
    tab = row[0].strip().upper()
    if ROW_WIDTHS.get(tab) != len(row):
        return RowLanding(line, tab, "", "differs", "is not a row the tracker's ingest reads")
    if tab in _STATUS_COLUMN:
        return _judge_status(line, row, tab, job, snap)
    sheet = tab in _SHEET_COLUMNS
    column, numbers = (_SHEET_COLUMNS if sheet else _TAPE_COLUMNS)[tab]
    material = row[column]
    found: Sequence[SheetRow | TapeRow] = [
        r for r in (snap.sheets if sheet else snap.tape)
        if r.job.strip() == job.strip() and r.material.strip() == material.strip()]
    shown = _shown(material)
    if not found:
        return RowLanding(line, tab, material, "missing", f"has no row for {shown}")
    if len(found) > 1:
        return RowLanding(line, tab, material, "differs", f"has {len(found)} rows for {shown}")
    for index, field, label in numbers:
        given = _cell_number(row[index])
        if given is None:
            continue                  # blank: a _SET leaves that cell alone
        held = getattr(found[0], field)
        if held is None or abs(held - given) > _SHOWN:
            return RowLanding(line, tab, material, "differs",
                              f"{shown} shows {label} {_plain(held)}, the batch {_plain(given)}")
    return RowLanding(line, tab, material, "landed", "")


def _judge_status(line: int, row: list[str], tab: str, job: str,
                  snap: TrackerSnapshot) -> RowLanding:
    found = [r for r in snap.jobs if r.job.strip() == job.strip()]
    if not found:
        return RowLanding(line, tab, "", "missing", "has no Job Summary row")
    if len(found) > 1:
        return RowLanding(line, tab, "", "differs", f"has {len(found)} Job Summary rows")
    given = " ".join(row[_STATUS_COLUMN[tab]].split())
    if given and " ".join(found[0].status.split()) != given:
        return RowLanding(line, tab, "", "differs", "shows another status than the batch")
    return RowLanding(line, tab, "", "landed", "")


def _cell_number(cell: str) -> float | None:
    try:
        number = float(cell.replace(",", "").strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _shown(material: str) -> str:
    """A tracker name as a person reads it: a tape by its colour and width, as the lint refuses a
    bare mm, and any name without its underscores."""
    tape = split_tape_name(material)
    return f"{plain_material(tape[0])} tape {tape[1]}" if tape else plain_material(material)


def _plain(number: float | None) -> str:
    return "nothing" if number is None else f"{number:g}"
