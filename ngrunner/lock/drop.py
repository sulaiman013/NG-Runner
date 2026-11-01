r"""
A tracker batch at Lock: the checks before a drop, the drop, and what the snapshots say after it
(design 5, "Tracker integrity"; BUILD 632-760, "Tracker inbox" and its three traps; decision 9).

Why so careful: a batch applied twice left a job "permanently un-lockable until the duplicates are
removed by hand", an "ingested_" archive was no proof a row applied, and a re-drop of rows that had
landed made duplicates (BUILD's traps 1 and 3). So:

Before a drop, all of these hold, or nothing is written (before_drop):
- no batch is in flight (in_flight): live, none of NG Runner's live batches is waiting for the ingest or
  its snapshots, and no tracker_inbox file of anyone's is waiting in the folder. In shadow, where nothing
  ingests NG Runner's test folder, only an open batch of the same job blocks: a shadow batch never blocks
  another job's drop, and it closes against the chat's own rows (lock/shadow.py), not by an ingest;
- the snapshot is under 20 minutes old;
- each material is in its Pricing block character for character, once, with a price;
- an append finds no row yet for its job and material, a set exactly one;
- every status is exactly one of Jim's two strings (decision 9): IN_PROGRESS or LOCKED;
- the batch has its header first, rows the ingest reads, and only the one job.
And the same rows are never dropped twice, whatever became of the first time.

The drop writes one new file, tracker_inbox_<id>.csv, through commit_write with P4's rules into the
folder the caller's roots name as the tracker's. In P4's build that is a test folder: the chokepoint
refuses the tracker's own folder by name, and the P4 interlock anything below the folder NG Runner is
configured with, until P4 goes live.

After a drop (after_drop: read_after reads the folder and the snapshot, which may wait on Drive, and
judge_after keeps what they say in the store, so the running app reads on a worker), from the folder
and each new snapshot:
1. Wait for the ingest's archive of this very batch, ingested_<its file name> (decision 9's idempotent
   ingest names it so). A DUPLICATE_ or SKIPPED_ archive, or a batch gone with no archive, is doubt.
2. Wait for a snapshot taken after the archive. Then every row of the batch must show as written
   (tracker_inbox.landed: one row per job and material, numbers within half their last shown place),
   no row with nest numbers may cost $0, and the totals must hold (totals.four_equalities).
3. "confirmed" only once a second snapshot at least 10 minutes after the first agrees.
Anything else is doubt: an item for Jim, and never a re-drop. Only rows an append added and a fresh
snapshot, taken after Jim asked, shows absent are sent again, alone (resend_missing): BUILD's trap 3.
"""
from __future__ import annotations

import os
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ngrunner.clock import Clock, utc
from ngrunner.guards.tracker import (
    TrackerSnapshot, TrackerUnreadable, plain_material, read_tracker, split_tape_name,
)
from ngrunner.lock import P4_LIVE, P4_SHADOW
from ngrunner.lock.totals import Totals, four_equalities, read_totals
from ngrunner.store import Store
from ngrunner.store.locks import (
    BatchRecord, add_batch, get_batch, list_batches, rows_dropped_before, update_batch,
)
from ngrunner.tracker_inbox import HEADER, ROW_WIDTHS, inbox_bytes, landed
from ngrunner.writes import FileWrite, Roots, commit_recorded

IN_PROGRESS = "🔲 In progress"     # Jim's two status strings, as his tracker spells them (decision 9)
LOCKED = "🔒 LOCKED"
STATUSES = (IN_PROGRESS, LOCKED)
SNAPSHOT_MAX_AGE = timedelta(minutes=20)
CONFIRM_AFTER = timedelta(minutes=10)
_INBOX_PREFIX = "tracker_inbox"
_SET_ASIDE = {"DUPLICATE_": "the ingest set the batch aside as a duplicate",
              "SKIPPED_": "the ingest skipped a row of the batch"}
# Each verb's material column, and whether it is a sheet row (else tape).
_MATERIAL = {"SHEETGOODS": (3, True), "SHEETGOODS_SET": (2, True), "EDGEBAND": (3, False),
             "EDGEBAND_SET": (2, False)}
_OPTIMIZER = {"SHEETGOODS": 7, "SHEETGOODS_SET": 6, "EDGEBAND": 5, "EDGEBAND_SET": 4}
_STATUS = {"JOBSUMMARY": 3, "JOBSUMMARY_SET": 2}
_SETTLED = ("confirmed", "resent", "matched", "differing", "not_compared")


@dataclass(frozen=True)
class DropProblem:
    code: str
    words: str


@dataclass(frozen=True)
class DropResult:
    ok: bool
    batch: BatchRecord | None = None
    problems: tuple[DropProblem, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class AfterDrop:
    """state: the batch's state now (store.locks.BATCH_STATES). missing, differs: the materials (or
    "Job Summary") a snapshot showed so. can_resend: only the missing rows of an append, nothing else
    wrong: what "Re-send only the missing row" may offer."""

    state: str
    words: str
    missing: tuple[str, ...] = ()
    differs: tuple[str, ...] = ()
    can_resend: bool = False


@dataclass(frozen=True)
class AfterRead:
    """What after_drop reads of the files for one batch, and nothing else: when the ingest archived it
    (None while it waits), doubt the folder showed (set aside, or gone with no archive), and the
    snapshot with its totals, or why it could not be read."""

    archived: datetime | None = None
    set_aside: AfterDrop | None = None
    snap: TrackerSnapshot | None = None
    totals: Totals | None = None
    unreadable: str | None = None


# ----- before a drop


def before_drop(job: str, rows: Sequence[Sequence[str]], *, verb: str, snap: TrackerSnapshot, now: datetime,
                open_batches: int, waiting_files: Sequence[str], shadow: bool = False) -> tuple[DropProblem, ...]:
    """Everything that stands in the way of dropping rows for job now; () when nothing does. open_batches:
    how many batches in_flight() found; shadow: they are the job's own, in NG Runner's test folder."""
    found: list[DropProblem] = []
    if open_batches or waiting_files:
        whose = " for this job" if shadow else ""
        found.append(DropProblem("in_flight", f"A tracker batch{whose} is still in flight, so this one waits"))
    shape = _shape(job, rows, verb)
    if shape:
        return (*found, shape)
    age = utc(now) - snap.taken
    if age > SNAPSHOT_MAX_AGE or age < -timedelta(minutes=1):
        return (*found, DropProblem("snapshot_old", "The tracker snapshot is over 20 minutes old, so"
                                                    " nothing is dropped until a fresh one comes"))
    for row in rows[1:]:
        found += _pricing(row, snap) + _existing(job, row, snap) + _status(row)
    return tuple(found)


def _shape(job: str, rows: Sequence[Sequence[str]], verb: str) -> DropProblem | None:
    if not rows or tuple(rows[0]) != HEADER or len(rows) < 2 or any(
            not row or ROW_WIDTHS.get(row[0]) != len(row) for row in rows[1:]):
        return DropProblem("not_a_batch", "The batch isn't one the tracker's ingest reads")
    if any(row[0].endswith("_SET") != (verb == "set") for row in rows[1:]):
        return DropProblem("not_a_batch", "The batch mixes rows to add with rows to set")
    if any(row[1].strip() != job.strip() for row in rows[1:]):
        return DropProblem("other_job", "The batch holds rows of another job")
    return None


def _pricing(row: Sequence[str], snap: TrackerSnapshot) -> list[DropProblem]:
    if row[0] not in _MATERIAL:
        return []
    column, sheet = _MATERIAL[row[0]]
    material = row[column]
    keys = snap.sheet_prices if sheet else snap.tape_prices
    exact = [key for key in keys if key.material == material]
    shown = _shown(material)
    if material.strip() in snap.keys_spelled_twice():
        return [DropProblem("priced_twice", f"{shown} is in Pricing twice, so its price could not be"
                                            " verified")]
    if not exact:
        return [DropProblem("not_in_pricing", f"{shown} isn't in the tracker's Pricing exactly as the"
                                              " batch spells it")]
    if exact[0].price is None:
        return [DropProblem("no_price", f"{shown} has no price in the tracker's Pricing")]
    return []


def _existing(job: str, row: Sequence[str], snap: TrackerSnapshot) -> list[DropProblem]:
    tab = row[0]
    if tab in _MATERIAL:
        column, sheet = _MATERIAL[tab]
        material = row[column]
        there = [r for r in (snap.sheets if sheet else snap.tape)
                 if r.job.strip() == job.strip() and r.material.strip() == material.strip()]
        what = _shown(material)
    else:
        there = [r for r in snap.jobs if r.job.strip() == job.strip()]
        what = "the Job Summary"
    if not tab.endswith("_SET"):
        return [DropProblem("row_there", f"The tracker already has a row for {what} on this job")] \
            if there else []
    if not there:
        return [DropProblem("no_row", f"The tracker has no row for {what} on this job to set")]
    if len(there) > 1:
        return [DropProblem("rows_twice", f"The tracker has {len(there)} rows for {what} on this job")]
    return []


def _status(row: Sequence[str]) -> list[DropProblem]:
    if row[0] not in _STATUS:
        return []
    status = row[_STATUS[row[0]]]
    if row[0] == "JOBSUMMARY_SET" and status == "":
        return []                                  # a _SET's blank cell leaves the status alone
    if status in STATUSES:
        return []
    return [DropProblem("status_not_jims", "The batch's status isn't exactly one of Jim's two,"
                                           " In progress or LOCKED")]


# ----- the drop


def drop_batch(job: str, rows: Sequence[Sequence[str]], *, kind: str, verb: str, material: str | None,
               snap: TrackerSnapshot, store: Store, roots: Roots,
               interlock: Callable[[list[Path]], str | None], clock: Clock,
               resend_of: str | None = None, source: str | None = None, phase: str = P4_SHADOW) -> DropResult:
    """Drop rows for job as one new batch file in roots.tracker, once every check before a drop
    holds. Nothing is written when one doesn't. source: which takeoff the rows came from (kept with the
    batch). phase: P4_SHADOW until P4 goes live, and writes.py has no rules for any other yet."""
    rows = [list(row) for row in rows]
    shadow = phase == P4_SHADOW
    if rows_dropped_before(store, rows):
        return DropResult(False, reason=("These exact rows were dropped before, and NG Runner never"
                                         " drops a batch twice."))
    if roots.tracker is None:
        return DropResult(False, reason="No folder is set for the tracker's inbox, so nothing was dropped.")
    waiting = [] if shadow else _waiting(roots.tracker)
    if waiting is None:
        return DropResult(False, reason="The tracker's inbox folder could not be read, so nothing was dropped.")
    now = clock.now()
    flying = in_flight(list_batches(store, open_only=True), job, shadow=shadow)
    problems = before_drop(job, rows, verb=verb, snap=snap, now=now, open_batches=len(flying),
                           waiting_files=waiting, shadow=shadow)
    if problems:
        return DropResult(False, problems=problems, reason=problems[0].words)
    batch_id = f"{utc(now):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
    name = f"{_INBOX_PREFIX}_{batch_id}.csv"
    result = commit_recorded([FileWrite(roots.tracker / name, inbox_bytes(rows), None)], store=store,
                             clock=clock, roots=roots, interlock=interlock, phase=phase)
    if not result.ok:
        return DropResult(False, reason=result.reason)
    batch = add_batch(store, id=batch_id, folder=job, kind=kind, verb=verb, material=material, rows=rows,
                      file=name, dropped_at=now, resend_of=resend_of, shadow=shadow, source=source)
    return DropResult(True, batch)


def in_flight(open_batches: Sequence[BatchRecord], job: str, *, shadow: bool) -> list[BatchRecord]:
    """The open batches that stand in the way of a drop for job. Shadow: only the job's own, as nothing
    ingests NG Runner's test folder. Live: every live batch, whatever its job, as the ingest takes one
    batch at a time."""
    if shadow:
        return [b for b in open_batches if b.folder.strip().casefold() == job.strip().casefold()]
    return [b for b in open_batches if not b.shadow]


def _waiting(folder: Path) -> list[str] | None:
    """tracker_inbox files of anyone's still waiting for the ingest; None when it can't be listed."""
    try:
        return sorted(name for name in os.listdir(folder)
                      if name.startswith(_INBOX_PREFIX) and name.casefold().endswith(".csv"))
    except OSError:
        return None


# ----- after a drop


def after_drop(batch: BatchRecord, *, store: Store, roots: Roots, snapshots: Path,
               now: datetime) -> AfterDrop:
    """Where batch stands, from the drop folder and the snapshot in snapshots, kept in the store.
    A confirmed or resent batch, or a shadow batch closed against the chat's rows, is settled and stays
    as it is."""
    return judge_after(batch, read_after(batch, roots=roots, snapshots=snapshots), store=store, now=now)


def read_after(batch: BatchRecord, *, roots: Roots, snapshots: Path) -> AfterRead:
    """The files after_drop judges batch by: the drop folder, then the snapshot once the batch is
    archived. A shadow batch waiting in NG Runner's test folder, which nothing ingests, gets the real
    tracker's snapshot as well, read only, for the chat's own rows (lock/shadow.py). Reads only, so it may
    run where a read can wait."""
    if batch.state in _SETTLED:
        return AfterRead()
    archived = _archive(batch, roots.tracker)
    if isinstance(archived, AfterDrop):
        return AfterRead(set_aside=archived)
    if archived is None:
        if not batch.shadow:
            return AfterRead()
        try:
            return AfterRead(snap=read_tracker(snapshots))
        except TrackerUnreadable as exc:
            return AfterRead(unreadable=str(exc))
    try:
        return AfterRead(archived, snap=read_tracker(snapshots), totals=read_totals(snapshots))
    except TrackerUnreadable as exc:
        return AfterRead(archived, unreadable=str(exc))


def judge_after(batch: BatchRecord, seen: AfterRead, *, store: Store, now: datetime) -> AfterDrop:
    """Where batch stands, from what read_after read, kept in the store."""
    if batch.state in _SETTLED:
        return AfterDrop(batch.state, batch.words or "")
    if seen.set_aside is not None:
        return _keep(store, batch, seen.set_aside)
    archived = seen.archived
    if archived is None:
        return AfterDrop("dropped", "Waiting for the tracker to take the batch")
    if batch.archived_at is None:
        update_batch(store, batch.id, state="archived", archived_at=archived)
        batch = get_batch(store, batch.id) or batch
    if seen.snap is None or seen.totals is None:
        return AfterDrop(batch.state, f"{seen.unreadable or 'The tracker snapshot could not be read'}, so the"
                                      " batch is not judged yet")
    if seen.snap.taken <= archived:
        return AfterDrop(batch.state if batch.state != "dropped" else "archived",
                         "Waiting for a tracker snapshot taken after the batch was applied")
    return _judge(store, batch, seen.snap, four_equalities(seen.totals), now)


def _archive(batch: BatchRecord, folder: Path | None) -> datetime | None | AfterDrop:
    """When the ingest archived this batch; None while it waits; doubt when it was set aside or left."""
    if folder is None:
        return AfterDrop("doubt", "No folder is set for the tracker's inbox")
    for prefix, words in _SET_ASIDE.items():
        if (folder / f"{prefix}{batch.file}").exists():
            return AfterDrop("doubt", f"The batch was not applied: {words}")
    archived = folder / f"ingested_{batch.file}"
    try:
        return datetime.fromtimestamp(archived.stat().st_mtime, tz=UTC)
    except FileNotFoundError:
        pass
    except OSError:
        return None
    if (folder / batch.file).exists():
        return None
    return AfterDrop("doubt", "The batch left the inbox with no archive of its own name")


def _judge(store: Store, batch: BatchRecord, snap: TrackerSnapshot, totals: tuple[str, ...],
           now: datetime) -> AfterDrop:
    landing = landed(batch.folder, batch.rows, snap, dropped=batch.dropped_at)
    missing = tuple(r.material or "Job Summary" for r in landing.rows if r.state == "missing")
    differs = tuple(r.material or "Job Summary" for r in landing.rows if r.state == "differs")
    zero = _zero_cost(batch, snap)
    if missing or differs or totals or zero:
        words = _doubt_words(missing, differs, totals, zero)
        resend = bool(missing) and not (differs or totals or zero) and batch.verb == "append"
        return _keep(store, batch, AfterDrop("doubt", words, missing, differs + zero, resend))
    if batch.state != "first_ok" or batch.first_taken is None:
        update_batch(store, batch.id, state="first_ok", first_ok_at=now, first_taken=snap.taken,
                     words="Every row landed; waiting for a second snapshot 10 minutes on")
        return AfterDrop("first_ok", "Every row landed; waiting for a second snapshot 10 minutes on")
    if snap.taken - batch.first_taken < CONFIRM_AFTER:
        return AfterDrop("first_ok", "Every row landed; waiting for a second snapshot 10 minutes on")
    update_batch(store, batch.id, state="confirmed", confirmed_at=now,
                 words="Confirmed by two snapshots 10 minutes apart")
    return AfterDrop("confirmed", "Confirmed by two snapshots 10 minutes apart")


def _zero_cost(batch: BatchRecord, snap: TrackerSnapshot) -> tuple[str, ...]:
    """Rows the batch gave nest numbers to that the tracker costs at $0 or can't price."""
    found = []
    for row in batch.rows[1:]:
        if row[0] not in _MATERIAL or not row[_OPTIMIZER[row[0]]].strip():
            continue
        column, sheet = _MATERIAL[row[0]]
        for there in (snap.sheets if sheet else snap.tape):
            if there.job.strip() == batch.folder.strip() and there.material.strip() == row[column].strip():
                if not there.price or not there.cost:
                    found.append(row[column])
    return tuple(found)


def _doubt_words(missing: tuple[str, ...], differs: tuple[str, ...], totals: tuple[str, ...],
                 zero: tuple[str, ...]) -> str:
    parts = [f"{len(missing)} row{'s' if len(missing) != 1 else ''} missing" if missing else "",
             f"{len(differs)} row{'s' if len(differs) != 1 else ''} not as written" if differs else "",
             f"{len(zero)} row{'s' if len(zero) != 1 else ''} at $0" if zero else "",
             totals[0] if totals else ""]
    return "The tracker doesn't show the batch as written: " + "; ".join(p for p in parts if p)


def _keep(store: Store, batch: BatchRecord, after: AfterDrop) -> AfterDrop:
    if batch.state != after.state or batch.words != after.words:
        update_batch(store, batch.id, state=after.state, words=after.words)
    return after


# ----- re-send only the missing row


def resend_missing(batch: BatchRecord, *, fresh: TrackerSnapshot, pressed_at: datetime, store: Store,
                   roots: Roots, interlock: Callable[[list[Path]], str | None], clock: Clock) -> DropResult:
    """[Re-send only the missing row]: the rows of batch that fresh, a snapshot taken after Jim asked
    and after the archive, shows absent, dropped alone as a new batch. Nothing else is ever sent again."""
    if batch.verb != "append" or batch.state != "doubt":
        return DropResult(False, reason=("Only rows a batch added, and that the tracker shows missing,"
                                         " are ever sent again."))
    if batch.archived_at is None or fresh.taken <= utc(pressed_at) or fresh.taken <= batch.archived_at:
        return DropResult(False, reason=("A fresh snapshot, taken after you asked, must show the row"
                                         " absent first; waiting for it."))
    landing = landed(batch.folder, batch.rows, fresh, dropped=batch.dropped_at)
    if any(r.state == "differs" for r in landing.rows):
        return DropResult(False, reason="A row of the batch isn't as written, so nothing is sent again.")
    lines = {r.line for r in landing.rows if r.state == "missing"}
    if not lines:
        return DropResult(False, reason="The fresh snapshot shows every row, so nothing is missing now.")
    again = [list(HEADER)] + [list(row) for line, row in enumerate(batch.rows, start=1) if line in lines]
    result = drop_batch(batch.folder, again, kind="resend", verb="append", material=batch.material,
                        snap=fresh, store=store, roots=roots, interlock=interlock, clock=clock,
                        resend_of=batch.id, source=batch.source,
                        phase=P4_SHADOW if batch.shadow else P4_LIVE)
    if result.ok:
        update_batch(store, batch.id, state="resent", words=f"Its missing rows went again as {result.batch.id}")
    return result


def _shown(material: str) -> str:
    """A tracker name as a person reads it: a tape by its colour and width, as the lint refuses a bare
    mm, and any name without its underscores (tracker_inbox and the pre-flight say it the same way)."""
    tape = split_tape_name(material)
    return f"{plain_material(tape[0])} tape {tape[1]}" if tape else plain_material(material)
