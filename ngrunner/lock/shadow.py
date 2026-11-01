"""
A shadow batch against the chat's own rows: how a batch NG Runner dropped into its own test folder closes,
and what P4's "5 locks match the chat" counts (docs/decisions.md, "P5's reader, and Phases 3 and 4 wired
into the app").

Why it exists: in shadow nothing ingests NG Runner's test folder, so a shadow batch would wait for an
archive that never comes. Meanwhile the chat goes on locking the same job on the real tracker by hand, so
the real tracker's snapshot (read only: NG Runner never writes the real tracker) shows what the chat put
there. A shadow batch closes when those rows appear:
- matched: every row of the batch is on the chat's side, by the same material name, with the same numbers
  to within half their last shown place, and a whole-job batch (Lock design, Lock) has no row the chat
  has and it hasn't;
- differing: anything else, each row named in plain words with both sides' numbers;
- not compared: no chat rows for the job within 7 days of the drop.

When the chat's rows count as there: any row of the job's on the real tracker, taken after the drop. A
batch that carries nest numbers or Jim's LOCKED (Finish lock, Lock) waits until the chat's own Job Summary
reads LOCKED, since the chat's nest numbers come with its lock. A Lock design's In progress is never held
against the chat's status: by the time its rows are compared the chat may have locked the job, which says
nothing about the rows.

The tally counts jobs, not batches: a job counts as matched when its shadow batches made from NG Runner's
own takeoff matched and none differed. A batch made from the chat's own IC Conversion (lock/names.py's
fallback) is the chat's numbers under the chat's names, so it proves nothing and is never counted; nor is a
batch from before the takeoff source was kept, a live batch, or a re-send.

Pure: it reads a snapshot it is given and the batch's rows, and writes nothing.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from ngrunner.clock import utc
from ngrunner.guards.tracker import SheetRow, TapeRow, TrackerSnapshot, plain_material, split_tape_name
from ngrunner.lock.drop import LOCKED
from ngrunner.store.locks import BatchRecord

SHADOW_WAITS = timedelta(days=7)
EXIT_LOCKS = 5                       # the design's P4 exit: 5 locks match the chat
MATCHED, DIFFERING, NOT_COMPARED = "matched", "differing", "not_compared"
CLOSED = (MATCHED, DIFFERING, NOT_COMPARED)
WAITING = "Waiting for the chat's own rows on the tracker to compare with"
GAVE_UP = "Not compared: the chat's own rows for this job didn't come within 7 days"
_SHOWN_LINES = 3
_WHOLE_JOB = ("lock_design", "lock")
_HALF_PLACE = 0.05 + 1e-9
# Each verb's material column and its numbers: (column, the snapshot row's field, the tracker's label).
_SHEETS = {"SHEETGOODS": (3, ((6, "calc_sheets", "Calc Sheets"), (7, "optimizer_sheets", "Optimizer Sheets"))),
           "SHEETGOODS_SET": (2, ((5, "calc_sheets", "Calc Sheets"), (6, "optimizer_sheets", "Optimizer Sheets")))}
_TAPE = {"EDGEBAND": (3, ((4, "calc_feet", "Calc Lin Ft"), (5, "optimizer_feet", "Optimizer Lin Ft"))),
         "EDGEBAND_SET": (2, ((3, "calc_feet", "Calc Lin Ft"), (4, "optimizer_feet", "Optimizer Lin Ft")))}
_STATUS = {"JOBSUMMARY": 3, "JOBSUMMARY_SET": 2}


@dataclass(frozen=True)
class Compared:
    """state: matched or differing. lines: each difference, in plain words; none when matched."""

    state: str
    words: str
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class Tally:
    """Jobs, by how their shadow batches from NG Runner's own takeoff closed (or wait)."""

    matched: int = 0
    differing: int = 0
    not_compared: int = 0
    waiting: int = 0


# ----- comparing one batch


def compare(batch: BatchRecord, snap: TrackerSnapshot) -> Compared | None:
    """The batch against the chat's own rows for its job on the real tracker's snapshot; None while
    there is nothing to compare it with yet."""
    if snap.taken <= utc(batch.dropped_at):
        return None
    job = batch.folder.strip()
    sheets = [r for r in snap.sheets if r.job.strip() == job]
    tape = [r for r in snap.tape if r.job.strip() == job]
    jobs = [r for r in snap.jobs if r.job.strip() == job]
    if not (sheets or tape or jobs):
        return None
    rows = [list(row) for row in batch.rows[1:]]
    if _needs_the_chats_lock(rows) and not any(" ".join(r.status.split()) == LOCKED for r in jobs):
        return None
    lines: list[str] = []
    ours: dict[bool, set[str]] = {True: set(), False: set()}
    for row in rows:
        if row[0] in _SHEETS:
            ours[True].add(row[_SHEETS[row[0]][0]].strip())
            lines += _row(row, _SHEETS[row[0]], sheets)
        elif row[0] in _TAPE:
            ours[False].add(row[_TAPE[row[0]][0]].strip())
            lines += _row(row, _TAPE[row[0]], tape)
    if batch.kind in _WHOLE_JOB:
        lines += [f"{_shown(r.material)}: the chat has a row NG Runner's batch has not"
                  for r in _distinct(sheets) if r.material.strip() not in ours[True]]
        lines += [f"{_shown(r.material)}: the chat has a row NG Runner's batch has not"
                  for r in _distinct(tape) if r.material.strip() not in ours[False]]
    if not lines:
        return Compared(MATCHED, "Matches the chat's own rows on the tracker")
    shown = "; ".join(lines[:_SHOWN_LINES])
    more = len(lines) - _SHOWN_LINES
    return Compared(DIFFERING, f"Differs from the chat's own rows: {shown}" + (f"; and {more} more" if more > 0 else ""),
                    tuple(lines))


def _needs_the_chats_lock(rows: Sequence[Sequence[str]]) -> bool:
    """A batch that fills a nest number or sets LOCKED is of the chat's lock, not of its takeoff."""
    for row in rows:
        if row[0] in _STATUS and " ".join(row[_STATUS[row[0]]].split()) == LOCKED:
            return True
        columns = _SHEETS.get(row[0]) or _TAPE.get(row[0])
        optimizer = None if columns is None else columns[1][1][0]
        if optimizer is not None and row[optimizer].strip():
            return True
    return False


def _row(row: Sequence[str], columns: tuple[int, tuple[tuple[int, str, str], ...]],
         theirs: Sequence[SheetRow | TapeRow]) -> list[str]:
    material = row[columns[0]]
    shown = _shown(material)
    found = [r for r in theirs if r.material.strip() == material.strip()]
    if not found:
        return [f"{shown}: the chat has no row by that name"]
    if len(found) > 1:
        return [f"{shown}: the chat has {len(found)} rows for it"]
    lines = []
    for index, field, label in columns[1]:
        given = _number(row[index])
        if given is None:
            continue                                  # a cell the batch leaves blank says nothing
        held = getattr(found[0], field)
        if held is None or abs(held - given) > _HALF_PLACE:
            lines.append(f"{shown}: {label} {_plain(held)} on the chat's row, {_plain(given)} on NG Runner's")
    return lines


def _distinct(rows: Iterable[SheetRow | TapeRow]) -> list[SheetRow | TapeRow]:
    seen: dict[str, SheetRow | TapeRow] = {}
    for row in rows:
        seen.setdefault(row.material.strip(), row)
    return list(seen.values())


# ----- closing with no chat rows, and the tally


def gives_up(batch: BatchRecord, now: datetime) -> bool:
    """A shadow batch still open 7 days after its drop closes as not compared."""
    return batch.shadow and batch.state not in (*CLOSED, "confirmed", "doubt", "resent") \
        and utc(now) - utc(batch.dropped_at) > SHADOW_WAITS


def tally(batches: Iterable[BatchRecord]) -> Tally:
    """P4's "5 locks match the chat", by job: only shadow batches made from NG Runner's own takeoff, and
    never a re-send."""
    jobs: dict[str, set[str]] = {}
    for batch in batches:
        if batch.shadow and batch.source == "ng_runner" and batch.kind != "resend":
            jobs.setdefault(batch.folder.strip().casefold(), set()).add(batch.state)
    count = {MATCHED: 0, DIFFERING: 0, NOT_COMPARED: 0, "waiting": 0}
    for states in jobs.values():
        if DIFFERING in states:
            count[DIFFERING] += 1
        elif MATCHED in states:
            count[MATCHED] += 1
        elif NOT_COMPARED in states:
            count[NOT_COMPARED] += 1
        elif states - set(CLOSED):
            count["waiting"] += 1
    return Tally(count[MATCHED], count[DIFFERING], count[NOT_COMPARED], count["waiting"])


def tally_words(t: Tally) -> str:
    """The tally as Health shows it: "2 of 5 jobs' shadow locks match the chat; 1 differs"."""
    rest = [words for n, words in ((t.differing, f"{t.differing} differ{'s' if t.differing == 1 else ''}"),
                                   (t.not_compared, f"{t.not_compared} not compared"),
                                   (t.waiting, f"{t.waiting} waiting for the chat's rows")) if n]
    head = f"{t.matched} of {EXIT_LOCKS} jobs' shadow locks match the chat"
    return f"{head}; {', '.join(rest)}" if rest else head


# ----- words


def _shown(material: str) -> str:
    """A tracker name as a person reads it, as the drop and the inbox say it."""
    tape = split_tape_name(material)
    return f"{plain_material(tape[0])} tape {tape[1]}" if tape else plain_material(material)


def _number(cell: str) -> float | None:
    try:
        number = float(cell.replace(",", "").strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _plain(number: float | None) -> str:
    return "nothing" if number is None else f"{number:g}"
