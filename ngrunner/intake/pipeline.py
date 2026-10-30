r"""
The filing pipeline: from a new Fab row to a verified IC summary in the job's folder (integration
spec section 1; design 3.4 and 3.5).

Why it looks like this. The PC sleeps, NG Runner restarts and updates, and Drive and Studio come
and go, so nothing here is held in memory between ticks. tick(ctx) does one pass, and every step
reads what to do from the store and records what it did there, so a stop at any point resumes
without a second file, message or card:
- A row's progress is its fab_rows state, and work still to do is a pending row: match_row (it has
  waited its one export), list_rematch, fetch_1, fetch_2, refetch, refetch_2, file, file_new.
- A step that changes several things does them in one store.transaction(), after checking for what
  an earlier pass already made, since add_card and add_outbox don't de-duplicate. commit_write and
  anything slow stay outside a transaction.
- A message is queued as a tell_* pending row, with its words, in the transaction that decides to
  send it. The messenger's send has its own transaction and writes a file, so it runs after, and a
  ref unique to that message means a send that was cut short is never made twice.
- Files are made by commit_write and their hashes kept in the store. A commit cut short leaves the
  job's file operation open, and the next pass finds what is there: its own files with the same
  fingerprint are finished by writing only what is missing, and nothing else is ever overwritten.

A real fetch takes up to 95 s, so it never runs on the tick's thread: ctx.fetches runs it, a couple
at a time, and the next tick takes the result. Tests and a day simulation may pass InlineFetches.

When something can't be verified, the pipeline asks or waits. It never guesses and never files:
a job links only through a Studio ID that the list or a card's choice_ids holds, a list with no
time never replaces one, and a summary found in a folder is never overwritten unless Jim says so,
and then only if commit_write finds it is still NG Runner's own.

What leaves the PC (decision 10): cards and messages name jobs and dates only. Agreement.reason
can quote IC design names, so it goes to the event log and DEV only; Fetched.reason is safe.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol

from ngrunner.cards import Command, card_prefix, parse_command
from ngrunner.clock import Clock, fmt_clock, fmt_date, from_iso, next_at, pacific_day, to_iso
from ngrunner.config import LAYOUTS, AppPaths, Settings
from ngrunner.fab import ExportRow, RowChange, diff, install_date, read_edits, read_export
from ngrunner.intake.branch import branch_of
from ngrunner.intake.fetch import Agreement, Fetched, FetchTarget, Render, agree, fetch_render
from ngrunner.intake.summary_check import SummaryCheck, check_summary, content_fingerprint
from ngrunner.interlock import GARAGE_SUFFIX, INSTALL_FOLDERS, SHADOW, is_paused, p1a_interlock
from ngrunner.joblist import JobListError
from ngrunner.match import (
    FabRow,
    Opportunity,
    match_fab_row,
    match_file_to_job,
    normalize_job_file,
    slugify,
)
from ngrunner.messages import Message, templates
from ngrunner.signing import EnvelopeError, key_from_text
from ngrunner.store import (
    CardRecord,
    Db,
    FabRowRecord,
    JobRecord,
    ListState,
    OpportunityRecord,
    PendingRecord,
    RenderRecord,
    Store,
    StoreRegistry,
    Transaction,
    accept_envelope,
    add_alias,
    add_card,
    add_fab_row,
    add_pending,
    add_render,
    clear_state,
    close_dev,
    ensure_job,
    finish_pending,
    freeze_name,
    get_fab_row,
    get_job,
    get_job_list,
    get_list_state,
    get_opportunity,
    get_pending,
    get_render,
    get_state,
    get_state_time,
    governing_render,
    last_accepted_seq,
    list_cards,
    list_dev,
    list_fab_rows,
    list_jobs,
    list_outbox,
    list_pending,
    list_renders,
    log_event,
    open_dev,
    owned_sha,
    past_deadline_pending,
    record_commit,
    render_fingerprint,
    replace_opportunities,
    retry_pending,
    set_governing,
    set_state,
    set_state_time,
    update_fab_row,
    update_job,
    withdraw_card,
)
from ngrunner.writes import CommitResult, FileWrite, Roots, commit_write

_log = logging.getLogger("ngrunner.pipeline")

# ----- pending kinds: the job model reads these (ngrunner/jobmodel.py's contract)

MATCH_ROW = "match_row"        # a new row has waited its one export: match it
LIST_REMATCH = "list_rematch"  # a matched or filed row's text changed: match again on a fresh list
FETCH_1, FETCH_2 = "fetch_1", "fetch_2"
REFETCH, REFETCH_2 = "refetch", "refetch_2"   # the nightly re-fetch of a filed job, and its check
FILE, FILE_NEW = "file", "file_new"           # with a note: refused, so the job is BLOCKED
TELL_WHICH_JOB, TELL_FILED = "tell_which_job", "tell_filed"
_TELL = "tell_"
_FETCHES = frozenset({FETCH_1, FETCH_2, REFETCH, REFETCH_2})
_FIRST_FETCHES = (FETCH_1, FETCH_2)
_NIGHTLY = frozenset({REFETCH, REFETCH_2})

# ----- state keys

FAB_EXPORTED_AT = "fab_exported_at"     # exported_at of the last accepted export (supervisor reads)
LIST_CLOCK_OFFSET = "list.clock_offset_s"
_FAB_ROWS = "fab.rows"                  # the last accepted export's rows, as they came
_FAB_ID = "fab.last_id"
_FAB_HELD = "fab.held"                  # an export held until the next one says the same
_FAB_UNREADABLE = "fab.unreadable_since"
_PUSH_ASKED = "list.push_asked_at"
_LIST_POLLED = "list.polled_at"
_ACTED = "cards.acted"                  # the answered cards already acted on
_WATCHED = "filed.watched_at"
_LISTING = "notes.listing_items"        # jobs whose _NGRunner.json lists open cards

STREAM = "fab_export"
BARBARA = "barbara"

# ----- times

_TWO_FETCHES = timedelta(minutes=10)
_EARLY_RETRY = timedelta(minutes=30)
_BACKOFF = tuple(timedelta(minutes=m) for m in (5, 15, 30, 60))   # then hourly
_FETCH_LIMIT = timedelta(hours=24)
_REFUSED_RETRY = timedelta(minutes=5)
_HOLD = timedelta(minutes=5)            # a filing waiting on an answer looks again
_WATCH_AGAIN = timedelta(hours=1)       # OBSERVED, and an Old folder: nothing to do but look
_PUSH_EVERY = timedelta(minutes=5)
_LIST_AGE_ALARM = timedelta(hours=1)
_LIST_REFRESH = timedelta(hours=1)      # with nothing waiting, keep the list this fresh
_LIST_POLL_EVERY = timedelta(minutes=10)
_EXPORT_OLD = timedelta(minutes=30)
_UNREADABLE_FOR = timedelta(minutes=10)
_RECENTLY_GONE = timedelta(hours=24)
_NIGHT = time(1, 30)
_REFETCH_FOR = timedelta(days=30)
_WATCH_EVERY = timedelta(minutes=10)
_CONFIRM_TRIES = 6                      # a nightly confirmation gives up until the next night
_HELD_AT_LEAST = 5                      # hold an export when more than this many rows, or
_HELD_SHARE = 0.10                      # this share of them, are gone or new

_CHOICE_GONE = ("That job has left the job list, so nothing was linked. Type the full job name in"
                " the row.")
_OBSERVED_REASON = "Its folder was there when NG Runner was installed, so NG Runner only watches it"
_OLD_REASON = "Its folder is in the Old folder of NG Jobs, so NG Runner wrote nothing"
_NOT_REACHABLE = "NG Jobs isn't reachable right now, so nothing was written"
_NOTE = "NG Runner filed this IC summary. Chat sessions read this file before working the job."


# ----- running fetches off the tick's thread


class FetchRunner(Protocol):
    """What tick needs to run fetches: work is started by a key, and its result taken later."""

    stopped: bool

    def busy(self) -> set[int]: ...

    def free(self) -> int: ...

    def start(self, key: int, work: Callable[[], Any]) -> None: ...

    def finished(self) -> list[Any]: ...

    def stop(self) -> None: ...


class BackgroundFetches:
    """A couple of fetches at a time, each on a daemon thread, so a stop never waits for Studio.
    A fetch that dies with the process just leaves its pending row due."""

    def __init__(self, limit: int = 2) -> None:
        self._limit = limit
        self._lock = threading.Lock()
        self._running: set[int] = set()
        self._done: list[tuple[int, Any]] = []
        self.stopped = False

    def busy(self) -> set[int]:
        with self._lock:
            return self._running | {key for key, _ in self._done}

    def free(self) -> int:
        with self._lock:
            return 0 if self.stopped else self._limit - len(self._running)

    def start(self, key: int, work: Callable[[], Any]) -> None:
        with self._lock:
            self._running.add(key)
        threading.Thread(target=self._run, args=(key, work), daemon=True,
                         name=f"ngrunner-fetch-{key}").start()

    def _run(self, key: int, work: Callable[[], Any]) -> None:
        result: Any = None
        try:
            result = work()
        except Exception:  # noqa: BLE001 - its pending row stays due, so the next tick tries again
            _log.exception("A fetch stopped with an error")
        finally:
            with self._lock:
                self._running.discard(key)
                self._done.append((key, result))

    def finished(self) -> list[Any]:
        with self._lock:
            done, self._done = self._done, []
        return [result for _, result in done if result is not None]

    def stop(self) -> None:
        with self._lock:
            self.stopped = True


class InlineFetches:
    """Fetches done at once, on the caller's thread: for tests and a day simulation, where nothing
    needs to go on meanwhile and the clock must not move during a fetch."""

    def __init__(self) -> None:
        self._done: list[Any] = []
        self.stopped = False

    def busy(self) -> set[int]:
        return set()

    def free(self) -> int:
        return 0 if self.stopped else 2

    def start(self, key: int, work: Callable[[], Any]) -> None:
        self._done.append(work())

    def finished(self) -> list[Any]:
        done, self._done = self._done, []
        return done

    def stop(self) -> None:
        self.stopped = True


def _never() -> bool:
    return False


# ----- what a tick is given


@dataclass
class Ctx:
    """Everything a tick touches, so the supervisor and a day simulation can pass fakes.

    studio_http     a requests.Session or anything with its get(); None makes one per fetch.
    render_client   joblist.RenderClient, or None when there is no export key yet: a board item,
                    never a crash. Rows wait, and the old-list alarm says so.
    messenger       ngrunner.messenger.Messenger: send(message, *, to, channel, ref). It redirects
                    in shadow mode, and holds a message through quiet hours.
    mode            "shadow" or "live": where a filing lands.
    fetches         where fetches run (default: off this thread, a couple at a time).
    stop_requested  asked between steps; a tick stops there, leaving what is due still due.
    """

    store: Store
    settings: Settings
    secrets: Any
    clock: Clock
    roots: Roots
    paths: AppPaths
    studio_http: Any
    render_client: Any
    messenger: Any
    mode: str
    fetches: FetchRunner = field(default_factory=BackgroundFetches)
    stop_requested: Callable[[], bool] = _never

    def commit(self, writes: list[FileWrite]) -> CommitResult:
        """The one way the pipeline writes: commit_write with the P1a interlock and the store's
        write record, then a row for the commit, so no caller forgets either."""
        result = commit_write(writes, roots=self.roots, interlock=p1a_interlock(self),
                              registry=StoreRegistry(self.store, self.clock), clock=self.clock)
        record_commit(self.store, writes, result, at=self.clock.now())
        return result


def stop_work(ctx: Ctx) -> None:
    """No new fetch starts. One under way dies with the process, and its pending row stays due."""
    ctx.fetches.stop()


def _stopping(ctx: Ctx) -> bool:
    return ctx.fetches.stopped or ctx.stop_requested()


# ----- one pass


def tick(ctx: Ctx) -> None:
    """One idempotent pass over the pipeline. It never waits on a fetch. Each step reads its work
    from the store; a step that fails opens a DEV item and the rest still run, so one bad row can't
    stop the others. Anything but an Exception (a stop, a crash) goes through: the store holds
    only what was finished, and the next tick carries on from there."""
    for step in _STEPS:
        if _stopping(ctx):
            return
        _guarded(ctx, step.__name__, lambda step=step: step(ctx))


def _guarded(ctx: Ctx, where: str, work: Callable[[], None]) -> None:
    try:
        work()
    except Exception as exc:  # noqa: BLE001 - one step's or row's problem must not stop the rest
        _log.exception("The pipeline's %s failed", where)
        try:
            open_dev(ctx.store, at=ctx.clock.now(), code="pipeline_step",
                     text=f"{where.strip('_').replace('_', ' ')}: {type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001 - a store that can't be written is the supervisor's
            _log.exception("A DEV item could not be opened")


def _each[T](ctx: Ctx, where: str, items: Iterable[T], work: Callable[[T], None]) -> None:
    """Work on each item, stopping between them on a stop request."""
    for item in items:
        if _stopping(ctx):
            return
        _guarded(ctx, where, lambda item=item: work(item))


# ----- step 1: the Fab export


def _read_fab(ctx: Ctx) -> None:
    now = ctx.clock.now()
    key = _signing_key(ctx, now)
    if key is None:
        return
    export = _read_export_file(ctx, key, now)
    if export is not None:
        _take_export(ctx, export, key, now)
    _export_age_alarm(ctx, now)


def _signing_key(ctx: Ctx, now: datetime) -> bytes | None:
    """The key that opens control-folder files. Missing or unusable: a board item, not a crash."""
    text = ctx.secrets.get("signing_key")
    try:
        key = key_from_text(text) if text else None
    except EnvelopeError:
        key = None
    if key is None:
        open_dev(ctx.store, at=now, code="no_signing_key",
                 text="NG Runner has no usable signing key, so it can't read the Fab export.")
    else:
        _clear_dev(ctx.store, "no_signing_key")
    return key


def _read_export_file(ctx: Ctx, key: bytes, now: datetime) -> Any:
    """The export if it reads. Not there yet is the old-export alarm's to say. A half-synced file
    is read again next minute, and goes to DEV only if it is still unreadable 10 minutes later."""
    try:
        data = (ctx.roots.control / "fab" / "export.json").read_bytes()
    except OSError:
        return None
    try:
        export = read_export(data, key=key)
    except EnvelopeError:
        since = get_state_time(ctx.store, _FAB_UNREADABLE)
        if since is None:
            set_state_time(ctx.store, _FAB_UNREADABLE, now)
        elif now - since >= _UNREADABLE_FOR:
            open_dev(ctx.store, at=now, code="fab_unreadable",
                     text="The Fab export has not been readable for 10 minutes.")
        return None
    if get_state(ctx.store, _FAB_UNREADABLE) is not None:
        clear_state(ctx.store, _FAB_UNREADABLE)
        _clear_dev(ctx.store, "fab_unreadable")
    return export


def _take_export(ctx: Ctx, export: Any, key: bytes, now: datetime) -> None:
    """One export, whole, in one transaction. The same file read again is not a replay."""
    store = ctx.store
    if (export.seq == last_accepted_seq(store, STREAM) and export.id == get_state(store, _FAB_ID)):
        return
    previous_at = get_state_time(store, FAB_EXPORTED_AT)
    folders = None
    if get_state(store, _FAB_ROWS) is None:
        folders = _scan_folders(ctx)
        if folders is None:                        # the first export needs to see what is there
            _no_ng_jobs(ctx, now)
            return
    edits = _read_edit_log(ctx, key, previous_at)
    with store.transaction() as tx:
        accepted = accept_envelope(tx, STREAM, seq=export.seq, id=export.id)
        if accepted:
            set_state(tx, _FAB_ID, export.id)
            set_state_time(tx, FAB_EXPORTED_AT, export.exported_at)
            _apply_export(tx, export.rows, edits, folders, now)
    if not accepted:
        open_dev(store, at=now, code="fab_replay",
                 text="A Fab export older than the last one accepted was read, and ignored.")
    else:
        _clear_dev(store, "fab_replay")


def _read_edit_log(ctx: Ctx, key: bytes, since: datetime | None) -> tuple[Any, ...]:
    """The edits made since the previous export. Only a hint: unreadable means none."""
    try:
        edits = read_edits((ctx.roots.control / "fab" / "edits.json").read_bytes(), key=key).edits
    except (OSError, EnvelopeError):
        return ()
    return tuple(e for e in edits if since is None or e.at > since)


def _apply_export(tx: Transaction, rows: Sequence[ExportRow], edits: Sequence[Any],
                  folders: list[str] | None, now: datetime) -> None:
    previous = _rows_from(get_state(tx, _FAB_ROWS))
    if previous is None:
        _first_export(tx, rows, folders or [], now)
        return
    changes = diff(previous, rows, edits, unchanged=True)
    moved = sum(c.kind in ("new", "gone") for c in changes)
    emptied = bool(_named(previous)) and not _named(rows)
    if emptied or moved > max(_HELD_AT_LEAST, _HELD_SHARE * len(_named(previous))):
        held = _rows_from(get_state(tx, _FAB_HELD))
        if held is None or diff(held, rows):       # not what the last export said: hold this one
            _hold_export(tx, rows, moved, now)
            return
    clear_state(tx, _FAB_HELD)
    _clear_dev(tx, "fab_held")
    _apply_changes(tx, changes, now)
    set_state(tx, _FAB_ROWS, _rows_json(rows))


def _first_export(tx: Transaction, rows: Sequence[ExportRow], folders: list[str],
                  now: datetime) -> None:
    """Every row is seen at install and nothing starts. A first export with no rows is a cut read,
    never the baseline: the next one is the first."""
    seen = diff(None, rows)
    if not seen:
        _hold_export(tx, rows, 0, now)
        return
    clear_state(tx, _FAB_HELD)
    _clear_dev(tx, "fab_held")
    for change in seen:
        _add_row(tx, change.row, "seen_at_install", now)
    set_state(tx, _FAB_ROWS, _rows_json(rows))
    set_state(tx, INSTALL_FOLDERS, json.dumps(sorted(folders)))
    log_event(tx, at=now, kind="installed",
              text=f"Read the first Fab export: {_count(len(seen), 'row')} seen at install,"
                   f" {_count(len(folders), 'folder')} found, and nothing started.")


def _hold_export(tx: Transaction, rows: Sequence[ExportRow], moved: int, now: datetime) -> None:
    set_state(tx, _FAB_HELD, _rows_json(rows))
    open_dev(tx, at=now, code="fab_held",
             text="A Fab export changed too many rows at once, or had none, so it is held until"
                  " the next export says the same.")
    log_event(tx, at=now, kind="fab_held",
              text=f"Held a Fab export where {_count(moved, 'row')} were gone or new.")


def _apply_changes(tx: Transaction, changes: Sequence[RowChange], now: datetime) -> None:
    """Rows are found by the sheet row the previous export gave them, all looked up before any is
    changed, since rows move about."""
    rows = {r.sheet_row: r for r in list_fab_rows(tx)
            if r.state != "gone" and r.sheet_row is not None}
    for change in changes:
        if change.kind == "new":
            _row_new(tx, change.row, now)
            continue
        was = change.row if change.kind == "gone" else change.previous
        record = rows.get(was.sheet_row) if was is not None else None
        if record is None:                         # nothing known of it: it starts as new
            if change.kind != "gone":
                _add_row(tx, change.row, "new", now)
        elif change.kind == "same":
            _row_same(tx, record, change.row, now)
        elif change.kind == "edited":
            _row_edited(tx, record, change, now)
        elif change.kind == "gone":
            _row_gone(tx, record, now)


def _add_row(tx: Transaction, row: ExportRow, state: str, now: datetime) -> FabRowRecord:
    return add_fab_row(tx, first_seen=now, text=row.job, designer=row.designer, install=row.install,
                       fab_date=row.fab_date, sheet_row=row.sheet_row, state=state)


def _row_new(tx: Transaction, row: ExportRow, now: datetime) -> None:
    """A new row waits for a later export. One identical to a recently gone row is that row coming
    back, not a new job: it returns to the state it had, and one that was asking asks again, whose
    email's ref keeps Barbara from a second one."""
    back = _recently_gone(tx, row, now)
    if back is None:
        _add_row(tx, row, "new", now)
        return
    key = _gone_key(back.id)
    state = json.loads(get_state(tx, key) or "{}").get("state", "new")
    asks_again = state in ("asked", "none", "new", "waiting_list")
    update_fab_row(tx, back.id, now=now, state="new" if asks_again else state,
                   sheet_row=row.sheet_row, fab_date=row.fab_date, reason=None)
    clear_state(tx, key)
    if asks_again:
        add_pending(tx, MATCH_ROW, due_at=now, fab_row_id=back.id)
    log_event(tx, at=now, kind="row_back", studio_id=back.studio_id,
              text=f'Fab row "{_spaced(back.text)}" came back to the Fab Schedule.')


def _recently_gone(tx: Transaction, row: ExportRow, now: datetime) -> FabRowRecord | None:
    for record in reversed(list_fab_rows(tx, states=("gone",))):
        was = ExportRow(1, record.text, record.designer, record.install or ("", ""), "")
        if now - record.updated_at <= _RECENTLY_GONE and not diff((was,), (row,)):
            return record
    return None


def _row_same(tx: Transaction, record: FabRowRecord, row: ExportRow, now: datetime) -> None:
    """A later export still shows it, so a new row has waited its export, and acts."""
    if record.sheet_row != row.sheet_row or (record.fab_date or "") != row.fab_date:
        update_fab_row(tx, record.id, now=now, sheet_row=row.sheet_row, fab_date=row.fab_date)
    if record.state == "new":
        add_pending(tx, MATCH_ROW, due_at=now, fab_row_id=record.id)


def _row_edited(tx: Transaction, record: FabRowRecord, change: RowChange, now: datetime) -> None:
    row, previous = change.row, change.previous
    text_changed = _spaced(row.job) != _spaced(previous.job)
    fields: dict[str, Any] = {"text": row.job} if text_changed else {}
    update_fab_row(tx, record.id, now=now, designer=row.designer, install=row.install,
                   fab_date=row.fab_date, sheet_row=row.sheet_row, **fields)
    if record.state in ("new", "waiting_list", "asked", "none"):
        _finish_ops(tx, MATCH_ROW, "done", fab_row_id=record.id)
        _withdraw(tx, record.id, "which_job")
        update_fab_row(tx, record.id, now=now, state="new", reason=None)
    elif record.state == "seen_at_install":
        log_event(tx, at=now, kind="row_edited",
                  text=f'Fab row "{_spaced(previous.job)}", seen at install, was edited.')
    elif record.state in ("matched", "filed") and record.studio_id is not None:
        _edited_with_a_job(tx, record, row, previous, text_changed, now)


def _edited_with_a_job(tx: Transaction, record: FabRowRecord, row: ExportRow, previous: ExportRow,
                       text_changed: bool, now: datetime) -> None:
    """New text is matched again once a list read after it comes. Only the install date or the
    designer changing on a filed row whose install date has passed may be a remake, so it asks."""
    sid = record.studio_id
    if text_changed:
        add_pending(tx, LIST_REMATCH, due_at=now, studio_id=sid, fab_row_id=record.id,
                    note=previous.job)
        return
    job = get_job(tx, sid)
    if record.state == "filed" and _install_has_passed(previous, now):
        if job is not None and not _open_card(tx, sid, "row_changed"):
            spec = templates.remake_card(job.name, row=row.job)
            _add_card(tx, spec, now, studio_id=sid, fab_row_id=record.id)
        return
    update_job(tx, sid, now=now, designer=row.designer, install=row.install)
    log_event(tx, at=now, kind="row_edited", studio_id=sid,
              text=f'Fab row "{_spaced(row.job)}" had its install date or designer changed.')


def _install_has_passed(row: ExportRow, now: datetime) -> bool:
    """Only a date that reads for sure has passed: "could not verify" is no reason to ask."""
    when = install_date(row.install, near=now)
    return when is not None and when < pacific_day(now)


def _row_gone(tx: Transaction, record: FabRowRecord, now: datetime) -> None:
    set_state(tx, _gone_key(record.id), json.dumps({"state": record.state}))
    _finish_ops(tx, MATCH_ROW, "done", fab_row_id=record.id)
    _finish_ops(tx, LIST_REMATCH, "done", fab_row_id=record.id)
    _withdraw(tx, record.id, "which_job")
    update_fab_row(tx, record.id, now=now, state="gone", sheet_row=None)
    log_event(tx, at=now, kind="row_gone", studio_id=record.studio_id,
              text=f'Fab row "{_spaced(record.text)}" left the Fab Schedule.')


def _export_age_alarm(ctx: Ctx, now: datetime) -> None:
    at = get_state_time(ctx.store, FAB_EXPORTED_AT)
    if at is None or now - at > _EXPORT_OLD:
        open_dev(ctx.store, at=now, code="fab_export_stale",
                 text=templates.ALARMS["fab_export_stale"])
    else:
        _clear_dev(ctx.store, "fab_export_stale")


def _rows_json(rows: Sequence[ExportRow]) -> str:
    return json.dumps([[r.sheet_row, r.job, r.designer, list(r.install), r.fab_date] for r in rows],
                      ensure_ascii=False)


def _rows_from(text: str | None) -> list[ExportRow] | None:
    if text is None:
        return None
    return [ExportRow(sheet, job, designer, (install[0], install[1]), fab_date)
            for sheet, job, designer, install, fab_date in json.loads(text)]


def _named(rows: Iterable[ExportRow]) -> list[ExportRow]:
    return [r for r in rows if r.job.strip()]


def _withdraw(tx: Transaction, row_id: int, kind: str) -> None:
    for card in list_cards(tx, fab_row_id=row_id):
        if card.kind == kind:
            withdraw_card(tx, card.id)


def _finish_ops(tx: Transaction, kind: str, state: str, *, studio_id: str | None = None,
                fab_row_id: int | None = None, note: Any = None) -> None:
    for op in list_pending(tx, kind=kind, studio_id=studio_id, fab_row_id=fab_row_id):
        finish_pending(tx, op.id, state, note=note)


# ----- step 2: the job list


def _refresh_list(ctx: Ctx) -> None:
    """Keep the list as fresh as what waits on it needs. Poll each tick while it isn't, ask for a
    push at most once per 5 minutes, and never replace a list with an empty or timeless one."""
    now, store = ctx.clock.now(), ctx.store
    needs = _list_needs(store)
    state = get_list_state(store)
    if needs and not _fresh(state, needs):
        state = _poll_list(ctx, state, now)
        if not _fresh(state, needs):
            _ask_for_push(ctx, now)
    elif _quiet_poll_due(store, state, now):
        _poll_list(ctx, state, now)
    _list_alarm(ctx, needs, get_list_state(store), now)


def _list_needs(db: Db) -> list[datetime]:
    """Each waiting row's last text change: the list must have been read at or after it."""
    ids = [op.fab_row_id for kind in (MATCH_ROW, LIST_REMATCH)
           for op in list_pending(db, kind=kind)]
    rows = [get_fab_row(db, row_id) for row_id in ids if row_id is not None]
    return [row.text_changed_at for row in rows if row is not None]


def _fresh(state: ListState | None, needs: Sequence[datetime]) -> bool:
    return (state is not None and state.synced_at is not None
            and all(state.synced_at >= need for need in needs))


def _quiet_poll_due(db: Db, state: ListState | None, now: datetime) -> bool:
    polled = get_state_time(db, _LIST_POLLED)
    if polled is not None and now - polled < _LIST_POLL_EVERY:
        return False
    return state is None or now - state.fetched_at >= _LIST_REFRESH


def _poll_list(ctx: Ctx, state: ListState | None, now: datetime) -> ListState | None:
    store = ctx.store
    set_state_time(store, _LIST_POLLED, now)
    if ctx.render_client is None:
        open_dev(store, at=now, code="no_export_key",
                 text="NG Runner has no export key, so it can't read the job list.")
        return state
    _clear_dev(store, "no_export_key")
    try:
        listing = ctx.render_client.export()
    except JobListError as error:
        if not error.transient:
            open_dev(store, at=now, code="job_list", text=str(error))
        return state
    _clear_dev(store, "job_list")
    return _store_list(store, listing, state)


def _store_list(store: Store, listing: Any, state: ListState | None) -> ListState | None:
    """A list with no time or no jobs never replaces one, nor does an older one."""
    if listing.synced_at is None or not listing.opportunities:
        return state
    if state is not None and state.synced_at is not None and listing.synced_at < state.synced_at:
        return state
    with store.transaction() as tx:
        fresh = replace_opportunities(tx, listing.opportunities, synced_at=listing.synced_at,
                                      fetched_at=listing.fetched_at, clients=listing.clients)
        offset = listing.clock_offset
        set_state(tx, LIST_CLOCK_OFFSET, "" if offset is None else str(offset.total_seconds()))
    return fresh


def _ask_for_push(ctx: Ctx, now: datetime) -> None:
    asked = get_state_time(ctx.store, _PUSH_ASKED)
    if ctx.render_client is None or (asked is not None and now - asked < _PUSH_EVERY):
        return
    set_state_time(ctx.store, _PUSH_ASKED, now)
    ctx.render_client.request_push()


def _list_alarm(ctx: Ctx, needs: Sequence[datetime], state: ListState | None,
                now: datetime) -> None:
    """The stored list is older than the oldest waiting row, and over an hour old."""
    old = False
    if needs:
        oldest = min(needs)
        if state is None or state.synced_at is None:
            old = now - oldest > _LIST_AGE_ALARM
        else:
            old = state.synced_at < oldest and now - state.synced_at > _LIST_AGE_ALARM
    if old:
        open_dev(ctx.store, at=now, code="job_list_stale", text=templates.ALARMS["job_list_stale"])
    else:
        _clear_dev(ctx.store, "job_list_stale")


# ----- step 3: matching


def _match_rows(ctx: Ctx) -> None:
    now, store = ctx.clock.now(), ctx.store
    ops = list_pending(store, kind=MATCH_ROW)
    if not ops:
        return
    state, listed = get_job_list(store)
    folders = _scan_folders(ctx)
    if folders is None:
        _no_ng_jobs(ctx, now)
        return
    _clear_dev(store, "ng_jobs_unreachable")
    known = [job.studio_id for job in list_jobs(store)]
    opportunities = [o.to_match() for o in listed]
    synced_at = None if state is None else state.synced_at

    def one(op: PendingRecord) -> None:
        row = None if op.fab_row_id is None else get_fab_row(store, op.fab_row_id)
        if row is None or row.state not in ("new", "waiting_list"):
            finish_pending(store, op.id, "done")
            return
        result = match_fab_row(row.to_match(), opportunities, (*known, *folders), synced_at,
                               sold_stages=ctx.settings.sold_stages)
        if result.status == "stale_list":
            update_fab_row(store, row.id, now=now, state="waiting_list")
        elif result.status == "matched":
            with store.transaction() as tx:
                finish_pending(tx, op.id, "done")
                _link(tx, row, result.job.studio_id, result.job.name, now)
        else:                                      # ambiguous, or none: a card and one email
            with store.transaction() as tx:
                finish_pending(tx, op.id, "done")
                _ask_which_job(tx, row, result.candidates, now)

    _each(ctx, "match_rows", ops, one)


def _link(tx: Transaction, row: FabRowRecord, studio_id: str, name: str, now: datetime) -> None:
    """Link a row to the job with this Studio ID, and start the job's work unless it is under way.
    A job already filed is not filed again: the row is simply told."""
    job = get_job(tx, studio_id)
    if job is None:
        mode = "observed" if _found_at_install(tx, name) else "auto"
        job = ensure_job(tx, studio_id, name, now=now, mode=mode, designer=row.designer,
                         install=row.install)
    if filed_facts(tx, studio_id) is not None:
        update_fab_row(tx, row.id, now=now, state="filed", studio_id=studio_id, reason=None)
        _queue_filed(tx, job, row, now)
        return
    update_fab_row(tx, row.id, now=now, state="matched", studio_id=studio_id, reason=None)
    _start_job(tx, studio_id, now)


def _start_job(tx: Transaction, studio_id: str, now: datetime) -> None:
    """The first fetch, unless the job's work is under way or Jim is settling what is there."""
    under_way = any(list_pending(tx, kind=kind, studio_id=studio_id)
                    for kind in (FETCH_1, FETCH_2, FILE, FILE_NEW))
    if not under_way and get_state(tx, _ic_key(studio_id)) is None:
        add_pending(tx, FETCH_1, due_at=now, deadline_at=now + _FETCH_LIMIT, studio_id=studio_id)


def _ask_which_job(tx: Transaction, row: FabRowRecord, candidates: Sequence[Opportunity],
                   now: datetime) -> None:
    """The card for Jim and one email for Barbara, whose correction re-runs the match. Barbara gets
    one email per row text, however often the row comes back to this."""
    install = _install_of(row, now)
    if not _open_row_card(tx, row.id, "which_job"):
        spec = templates.which_job_card(row.text, designer=row.designer, install=install,
                                        candidates=candidates)
        _add_card(tx, spec, now, fab_row_id=row.id)
    email = templates.which_job_email(row.text, designer=row.designer, install=install,
                                      candidates=candidates)
    _queue_tell(tx, TELL_WHICH_JOB, email, to=BARBARA, fab_row_id=row.id, now=now,
                ref=f"which_job:{row.id}:{_short_hash(row.text)}")
    update_fab_row(tx, row.id, now=now, state="asked", reason=None)


def _rematch_rows(ctx: Ctx) -> None:
    """A matched or filed row with new text: on a list read after the change, matched again in
    memory with its own job left out of ng_jobs. Anything but that job raises "Row changed"."""
    now, store = ctx.clock.now(), ctx.store
    ops = list_pending(store, kind=LIST_REMATCH)
    if not ops:
        return
    state, listed = get_job_list(store)
    folders = _scan_folders(ctx)
    if folders is None:
        _no_ng_jobs(ctx, now)
        return
    opportunities = [o.to_match() for o in listed]
    synced_at = None if state is None else state.synced_at

    def one(op: PendingRecord) -> None:
        row = None if op.fab_row_id is None else get_fab_row(store, op.fab_row_id)
        job = None if op.studio_id is None else get_job(store, op.studio_id)
        if row is None or job is None:
            finish_pending(store, op.id, "done")
            return
        known = [j.studio_id for j in list_jobs(store) if j.studio_id != job.studio_id]
        others = [f for f in folders if not _folder_is_jobs(f, job)]
        result = match_fab_row(row.to_match(), opportunities, (*known, *others), synced_at,
                               sold_stages=ctx.settings.sold_stages)
        if result.status == "stale_list":
            return
        with store.transaction() as tx:
            finish_pending(tx, op.id, "done")
            if result.status == "matched" and result.job.studio_id == job.studio_id:
                log_event(tx, at=now, kind="row_edited", studio_id=job.studio_id,
                          text=f'Fab row "{_spaced(row.text)}" still reads as {job.name}.')
            elif not _open_card(tx, job.studio_id, "row_changed"):
                spec = templates.row_changed_card(job.name, was=op.note or row.original_text,
                                                  now_reads=row.text, candidates=result.candidates)
                _add_card(tx, spec, now, studio_id=job.studio_id, fab_row_id=row.id)

    _each(ctx, "rematch_rows", ops, one)


def _folder_is_jobs(folder: str, job: JobRecord) -> bool:
    names = {name.casefold() for name in (job.folder, job.name, job.name + GARAGE_SUFFIX) if name}
    return folder.casefold() in names


def _found_at_install(db: Db, name: str) -> bool:
    """Whether a folder there at install is this job's: its name, closet or garage, or its run."""
    job = normalize_job_file(name)
    for folder in json.loads(get_state(db, INSTALL_FOLDERS) or "[]"):
        found = normalize_job_file(folder)
        if found.base == job.base and _run_of(found.variant).startswith(_run_of(job.variant)):
            return True
    return False


def _run_of(variant: str | None) -> str:
    return "+".join(token for token in (variant or "").split("+") if token != "gar")


def _scan_folders(ctx: Ctx) -> list[str] | None:
    """Every folder name in NG Jobs and NG Jobs\\Old, read-only; None when NG Jobs can't be read."""
    try:
        names = _folders_in(ctx.roots.ng_jobs)
    except OSError:
        return None
    try:
        names += _folders_in(ctx.roots.ng_jobs / "Old")
    except FileNotFoundError:
        pass                                       # no Old folder yet
    except OSError:
        return None
    return [name for name in names if name.casefold() != "old"]


def _folders_in(folder: Path) -> list[str]:
    with os.scandir(folder) as entries:
        return [entry.name for entry in entries if entry.is_dir(follow_symlinks=False)]


def _no_ng_jobs(ctx: Ctx, now: datetime) -> None:
    open_dev(ctx.store, at=now, code="ng_jobs_unreachable",
             text="NG Jobs can't be read right now, so rows wait: what is there can't be verified.")


# ----- Jim's answers


def _act_on_answers(ctx: Ctx) -> None:
    """An answer is recorded when Jim gives it, and acted on here, once: the acting and its mark
    share one transaction. A job is linked only through the card's choice_ids."""
    now, store = ctx.clock.now(), ctx.store
    acted = set(json.loads(get_state(store, _ACTED) or "[]"))
    new = [card for card in list_cards(store, state="answered") if card.id not in acted]

    def one(card: CardRecord) -> None:
        with store.transaction() as tx:
            _act(tx, card, now)
            done = set(json.loads(get_state(tx, _ACTED) or "[]")) | {card.id}
            set_state(tx, _ACTED, json.dumps(sorted(done)))

    _each(ctx, "act_on_answers", new, one)


def _act(tx: Transaction, card: CardRecord, now: datetime) -> None:
    log_event(tx, at=now, kind="card_answered", studio_id=card.studio_id,
              text=f"Card {card.id} was answered: {card.answer}.")
    if card.kind == "which_job":
        _answer_which_job(tx, card, now)
    elif card.kind == "closet_or_garage":
        _answer_branch(tx, card, now)
    elif card.kind == "fetch_failed":
        _answer_fetch_failed(tx, card, now)
    elif card.kind == "ic_changed":
        _answer_ic_changed(tx, card, now)
    elif card.kind == "row_changed":
        if card.choices == templates.REMAKE_CHOICES:
            _answer_remake(tx, card, now)
        else:
            _answer_row_changed(tx, card, now)


def _choice_id(card: CardRecord) -> str | None:
    return card.choice_ids[card.choices.index(card.answer)]


def _answer_which_job(tx: Transaction, card: CardRecord, now: datetime) -> None:
    row = None if card.fab_row_id is None else get_fab_row(tx, card.fab_row_id)
    if row is None or row.state != "asked":
        return
    chosen = _choice_id(card)
    if chosen is None:
        update_fab_row(tx, row.id, now=now, state="none", reason=None)
        log_event(tx, at=now, kind="row_none",
                  text=f'Fab row "{_spaced(row.text)}" is none of the jobs offered, as Jim said.')
        return
    opportunity = get_opportunity(tx, chosen)
    if opportunity is None:
        update_fab_row(tx, row.id, now=now, reason=_CHOICE_GONE)
        log_event(tx, at=now, kind="choice_gone", lane="ACT",
                  text=f'The job chosen for Fab row "{_spaced(row.text)}" has left the job list.')
        return
    _link(tx, row, chosen, opportunity.name, now)


def _answer_branch(tx: Transaction, card: CardRecord, now: datetime) -> None:
    if card.studio_id is None or card.answer is None:
        return
    update_job(tx, card.studio_id, now=now, branch=card.answer.casefold())
    _wake_filing(tx, card.studio_id, now)


def _answer_fetch_failed(tx: Transaction, card: CardRecord, now: datetime) -> None:
    """Keep trying: another 24 hours. Stop trying: nothing more, until add job."""
    sid = card.studio_id
    if sid is None:
        return
    keep = card.choices.index(card.answer) == 0
    for kind in _FIRST_FETCHES:
        for op in list_pending(tx, kind=kind, studio_id=sid):
            if keep:
                finish_pending(tx, op.id, "expired")
                add_pending(tx, kind, due_at=now, deadline_at=now + _FETCH_LIMIT, studio_id=sid,
                            note=op.note)
            else:
                finish_pending(tx, op.id, "failed", note="Jim chose to stop trying to fetch it")


def _answer_row_changed(tx: Transaction, card: CardRecord, now: datetime) -> None:
    row = None if card.fab_row_id is None else get_fab_row(tx, card.fab_row_id)
    if row is None:
        return
    chosen = _choice_id(card)
    was = card.studio_id
    if chosen is None:
        if card.choices.index(card.answer) == 0:   # Keep <job>
            _wake_filing(tx, was, now)
            return
        update_fab_row(tx, row.id, now=now, state="none", studio_id=None, reason=None)
        log_event(tx, at=now, kind="row_none", studio_id=was,
                  text=f'Fab row "{_spaced(row.text)}" is none of the jobs offered, as Jim said.')
    else:
        opportunity = get_opportunity(tx, chosen)
        if opportunity is None:
            update_fab_row(tx, row.id, now=now, reason=_CHOICE_GONE)
            log_event(tx, at=now, kind="choice_gone", lane="ACT", studio_id=was,
                      text=f'The job chosen for Fab row "{_spaced(row.text)}" has left the list.')
            return
        _link(tx, row, chosen, opportunity.name, now)
    if was is not None:
        _release_job(tx, was, now)


def _release_job(tx: Transaction, studio_id: str, now: datetime) -> None:
    """A job left with no row and not filed stops: nothing has asked for it any more."""
    if filed_facts(tx, studio_id) is not None or _rows_of(tx, studio_id):
        return
    for kind in (*_FIRST_FETCHES, FILE):
        _finish_ops(tx, kind, "failed", studio_id=studio_id,
                    note="Its Fab row now names another job")
    log_event(tx, at=now, kind="job_released", studio_id=studio_id,
              text="Its Fab row now names another job, so its fetching stopped.")


def _answer_remake(tx: Transaction, card: CardRecord, now: datetime) -> None:
    row = None if card.fab_row_id is None else get_fab_row(tx, card.fab_row_id)
    sid = card.studio_id
    if row is None or sid is None:
        return
    if card.choices.index(card.answer) == 0:
        log_event(tx, at=now, kind="remake", lane="ACT", studio_id=sid,
                  text="A remake: work it in chat as before. NG Runner files nothing new for it.")
    else:
        update_job(tx, sid, now=now, designer=row.designer, install=row.install)
    _wake_filing(tx, sid, now)


def _wake_filing(tx: Transaction, studio_id: str | None, now: datetime) -> None:
    if studio_id is None:
        return
    for op in list_pending(tx, kind=FILE, studio_id=studio_id):
        retry_pending(tx, op.id, due_at=now)


def _answer_ic_changed(tx: Transaction, card: CardRecord, now: datetime) -> None:
    sid = card.studio_id
    question = None if sid is None else _load(tx, _ic_key(sid))
    if sid is None or question is None:
        return
    if card.choices.index(card.answer) == 1:       # File the new one
        add_pending(tx, FILE_NEW, due_at=now, studio_id=sid)
        return
    declined = _load(tx, _declined_key(sid)) or []
    set_state(tx, _declined_key(sid), json.dumps([*declined, question["fingerprint"]]))
    clear_state(tx, _ic_key(sid))
    job = get_job(tx, sid)
    if question.get("first") and job is not None:
        _mark_filed(tx, job, _facts_of_found(question, now), now, no_write=True)


# ----- steps 4 and 5: the two fetches


def _raise_deadline_cards(ctx: Ctx) -> None:
    """Past 24 hours from the first attempt: the card, once, carrying the last Fetched.reason. An
    attempt due now goes first, so a PC that slept gets its chance before anyone is asked."""
    now, store = ctx.clock.now(), ctx.store
    busy = ctx.fetches.busy()
    for op in past_deadline_pending(store, now):
        if (op.kind not in _FIRST_FETCHES or not op.attempts or op.due_at <= now or op.id in busy
                or op.studio_id is None):
            continue
        job = get_job(store, op.studio_id)
        if job is not None:
            with store.transaction() as tx:
                _fetch_failed_card(tx, job, since=_since_of(op, now), reason=op.note or "", now=now)


def _card_if_past_limit(tx: Transaction, op: PendingRecord, job: JobRecord, at: datetime,
                        reason: str, now: datetime) -> None:
    """An attempt that failed past the limit raises the card at once, however the ticks fall."""
    if op.deadline_at is not None and at >= op.deadline_at:
        _fetch_failed_card(tx, job, since=_since_of(op, now), reason=reason, now=now)


def _since_of(op: PendingRecord, now: datetime) -> datetime:
    return op.deadline_at - _FETCH_LIMIT if op.deadline_at is not None else now


def _fetch_failed_card(tx: Transaction, job: JobRecord, *, since: datetime, reason: str,
                       now: datetime) -> None:
    if not _open_card(tx, job.studio_id, "fetch_failed"):
        spec = templates.fetch_failed_card(job.name, since=since, reason=reason)
        _add_card(tx, spec, now, studio_id=job.studio_id)


def _start_fetches(ctx: Ctx) -> None:
    """Start what is due, a couple at a time. Nightly re-fetches go singly, behind the rest."""
    now, store = ctx.clock.now(), ctx.store
    busy = ctx.fetches.busy()
    due = [op for op in list_pending(store) if op.kind in _FETCHES and op.due_at <= now]
    due.sort(key=lambda op: (op.kind in _NIGHTLY, op.due_at, op.id))
    nightly = any(p is not None and p.kind in _NIGHTLY
                  for p in (get_pending(store, key) for key in busy))

    def one(op: PendingRecord) -> None:
        nonlocal nightly
        if op.id in busy or ctx.fetches.free() <= 0 or (op.kind in _NIGHTLY and nightly):
            return
        job = None if op.studio_id is None else get_job(store, op.studio_id)
        if job is None:
            finish_pending(store, op.id, "failed")
            return
        nightly = nightly or op.kind in _NIGHTLY
        target = _target(ctx, job)
        ctx.fetches.start(op.id, lambda: _fetch(ctx, op, target))

    _each(ctx, "start_fetches", due, one)


def _target(ctx: Ctx, job: JobRecord) -> FetchTarget:
    store, sid = ctx.store, job.studio_id
    opportunity = get_opportunity(store, sid)
    return FetchTarget(sid, get_state(store, _fetch_name_key(sid)) or job.name,
                       None if opportunity is None else opportunity.client,
                       _layout_of(store, ctx.settings, sid), _known_url(store, sid))


def _layout_of(db: Db, settings: Settings, studio_id: str) -> str:
    """One layout per job: the one a summary in its folder made it keep, else its first fetch's,
    whatever the setting says now, else the setting."""
    kept = get_state(db, _layout_key(studio_id))
    if kept:
        return kept
    return next((r.layout for r in list_renders(db, studio_id) if r.layout), settings.report_layout)


def _known_url(db: Db, studio_id: str) -> str | None:
    return next((r.url for r in reversed(list_renders(db, studio_id)) if r.sha256 and r.url), None)


@dataclass(frozen=True)
class _Outcome:
    """A finished fetch, for the next tick. at: when it finished; path: its cached bytes."""

    op_id: int
    target: FetchTarget
    fetched: Fetched
    at: datetime
    path: Path | None


def _fetch(ctx: Ctx, op: PendingRecord, target: FetchTarget) -> _Outcome:
    """Runs off the tick's thread: Studio, then the cache in NG Runner's own data folder. It
    touches the store for nothing."""
    fetched = fetch_render(target, http=ctx.studio_http, base_url=ctx.settings.studio_url)
    path = None
    if fetched.ok and fetched.data is not None and fetched.check is not None:
        path = _cache(ctx.paths, target.studio_id, fetched.check.details["sha256"], fetched.data)
    return _Outcome(op.id, target, fetched, ctx.clock.now(), path)


def _cache(paths: AppPaths, studio_id: str, sha256: str, data: bytes) -> Path:
    folder = paths.renders / studio_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha256}.pdf"
    temp = folder / f"{sha256}.{threading.get_ident()}.tmp"
    temp.write_bytes(data)
    os.replace(temp, path)
    return path


def _take_fetches(ctx: Ctx) -> None:
    _each(ctx, "take_fetches", ctx.fetches.finished(), lambda outcome: _after_fetch(ctx, outcome))


def _after_fetch(ctx: Ctx, outcome: _Outcome) -> None:
    store = ctx.store
    op = get_pending(store, outcome.op_id)
    if op is None or op.state != "open" or op.studio_id is None:
        return                                     # answered while it ran
    job = get_job(store, op.studio_id)
    if job is None:
        return
    if op.kind in _NIGHTLY:
        _after_refetch(ctx, op, job, outcome)
    elif not outcome.fetched.ok:
        _fetch_refused(ctx, op, job, outcome)
    elif not outcome.fetched.check.ok:
        _early_render(ctx, op, job, outcome)
    elif op.kind == FETCH_1:
        _first_fetched(ctx, op, job, outcome)
    else:
        _second_fetched(ctx, op, job, outcome)


def _add_outcome(tx: Transaction, outcome: _Outcome) -> RenderRecord:
    """One row per fetch: what the check read, or for a refused fetch, why."""
    fetched, sid, layout = outcome.fetched, outcome.target.studio_id, outcome.target.layout
    if not fetched.ok or fetched.check is None:
        return add_render(tx, studio_id=sid, fetched_at=outcome.at, check_ok=False, url=fetched.url,
                          layout=layout, missing=(fetched.reason,))
    check = fetched.check
    return add_render(tx, studio_id=sid, fetched_at=outcome.at, check_ok=check.ok, url=fetched.url,
                      sha256=check.details.get("sha256"), page_count=check.page_count,
                      creation_date=check.details.get("creation_date"),
                      fingerprint=check.details.get("fingerprint"), layout=layout,
                      missing=tuple(check.missing), project_name=check.project_name,
                      cache_path=outcome.path, pypdf=check.details.get("pypdf"))


def _fetch_refused(ctx: Ctx, op: PendingRecord, job: JobRecord, outcome: _Outcome) -> None:
    fetched, now, sid = outcome.fetched, ctx.clock.now(), job.studio_id
    with ctx.store.transaction() as tx:
        _add_outcome(tx, outcome)
        if fetched.code == "too_big":
            open_dev(tx, at=now, code="too_big", text=fetched.reason, studio_id=sid)
            finish_pending(tx, op.id, "failed", note=fetched.reason)
            return
        if fetched.code == "wrong_job" and _renamed(tx, job, fetched, now):
            retry_pending(tx, op.id, due_at=now, note=None)
            return
        retry_pending(tx, op.id, due_at=outcome.at + _backoff(op.attempts), note=fetched.reason)
        _card_if_past_limit(tx, op, job, outcome.at, fetched.reason, now)
        if fetched.code == "wrong_job":
            open_dev(tx, at=now, code="wrong_job", text=fetched.reason, studio_id=sid)
            _fetch_failed_card(tx, job, since=_since_of(op, now), reason=fetched.reason, now=now)


def _renamed(tx: Transaction, job: JobRecord, fetched: Fetched, now: datetime) -> bool:
    """A wrong_job whose printed name, by slug, is the list's current name for this Studio ID is
    the same job renamed: add the alias, fetch under the new name, and log it. The Studio ID is the
    identity, and the check against the list keeps this from being a guess. The folder is never
    renamed. Before filing the new name is the one that freezes, so the old becomes the alias."""
    opportunity = get_opportunity(tx, job.studio_id)
    if (opportunity is None or not fetched.found_name
            or slugify(fetched.found_name) != slugify(opportunity.name)):
        return False
    sid, new = job.studio_id, opportunity.name
    if job.name_frozen:
        add_alias(tx, sid, new, seen_at=now)
        set_state(tx, _fetch_name_key(sid), new)
    else:
        add_alias(tx, sid, job.name, seen_at=now)
        update_job(tx, sid, now=now, name=new)
    log_event(tx, at=now, kind="renamed", studio_id=sid,
              text=f"{job.name} is now {new} in Studio and Salesforce: the same job, renamed.")
    return True


def _backoff(attempts: int) -> timedelta:
    return _BACKOFF[min(attempts, len(_BACKOFF) - 1)]


def _early_render(ctx: Ctx, op: PendingRecord, job: JobRecord, outcome: _Outcome) -> None:
    """Studio hasn't finished: every 30 minutes, keeping any first fetch (second_incomplete)."""
    note = f"The IC summary for {job.name} is not complete yet."
    with ctx.store.transaction() as tx:
        _add_outcome(tx, outcome)
        retry_pending(tx, op.id, due_at=outcome.at + _EARLY_RETRY, note=note)
        _card_if_past_limit(tx, op, job, outcome.at, note, ctx.clock.now())


def _first_fetched(ctx: Ctx, op: PendingRecord, job: JobRecord, outcome: _Outcome) -> None:
    with ctx.store.transaction() as tx:
        render = _add_outcome(tx, outcome)
        set_state(tx, _first_key(job.studio_id), str(render.id))
        finish_pending(tx, op.id, "done")
        add_pending(tx, FETCH_2, due_at=outcome.at + _TWO_FETCHES, deadline_at=op.deadline_at,
                    studio_id=job.studio_id)


def _second_fetched(ctx: Ctx, op: PendingRecord, job: JobRecord, outcome: _Outcome) -> None:
    store, sid, now = ctx.store, job.studio_id, ctx.clock.now()
    first = _first_render(store, sid)
    agreement = _agreement(first, outcome)         # reads files, so before the transaction
    code = "first_incomplete" if agreement is None else agreement.code
    with store.transaction() as tx:
        render = _add_outcome(tx, outcome)
        if agreement is not None and code != "agree":
            log_event(tx, at=now, kind="fetches_differ", studio_id=sid, text=agreement.reason)
        if code == "agree":
            _agreed(tx, op, job, first, render, agreement, now)
        elif code == "layouts_differ":             # a person looks; no loop
            open_dev(tx, at=now, code="layouts_differ", text=agreement.reason, studio_id=sid)
            finish_pending(tx, op.id, "failed", note="Its two fetches came in different layouts")
        elif code == "too_soon":
            retry_pending(tx, op.id, due_at=first.fetched_at + _TWO_FETCHES)
        else:
            if code in ("first_incomplete", "differ"):
                set_state(tx, _first_key(sid), str(render.id))   # the second counts as fetch 1
            if code == "same_bytes":
                open_dev(tx, at=now, code="same_bytes", text=agreement.reason, studio_id=sid)
            wait = _EARLY_RETRY if code == "second_incomplete" else _TWO_FETCHES
            note = _SECOND_FETCH_NOTES[code].format(job=job.name)
            retry_pending(tx, op.id, due_at=outcome.at + wait, note=note)
            _card_if_past_limit(tx, op, job, outcome.at, note, now)


# Why a second fetch goes round again, in words safe for the 24-hour card: no design names.
_SECOND_FETCH_NOTES = {
    "first_incomplete": "The first fetch of {job} could not be used, so it was fetched again.",
    "differ": "The IC summary for {job} changed between two fetches.",
    "same_bytes": "Two fetches of {job} were one file, so neither counts.",
    "second_incomplete": "The IC summary for {job} is not complete yet.",
}


def _first_render(db: Db, studio_id: str) -> RenderRecord | None:
    pointer = get_state(db, _first_key(studio_id))
    return None if not pointer else get_render(db, int(pointer))


def _agreement(first: RenderRecord | None, outcome: _Outcome) -> Agreement | None:
    """agree() on the job's first fetch and this one; None when there is no first to compare,
    or its cached file is gone, which counts as no first fetch."""
    if first is None or first.cache_path is None or outcome.path is None:
        return None
    try:
        layout = outcome.target.layout
        return agree(Render(first.cache_path, first.fetched_at, first.layout or layout),
                     Render(outcome.path, outcome.at, layout))
    except OSError:
        return None


def _agreed(tx: Transaction, op: PendingRecord, job: JobRecord, first: RenderRecord,
            second: RenderRecord, agreement: Agreement, now: datetime) -> None:
    """Two agreeing fetches: this render governs, and the job is ready to file."""
    sid = job.studio_id
    finish_pending(tx, op.id, "done")
    set_governing(tx, second.id)
    clear_state(tx, _first_key(sid))
    set_state(tx, _ready_key(sid), json.dumps({"first": first.id, "second": second.id,
                                               "fingerprint": agreement.fingerprint}))
    add_pending(tx, FILE, due_at=now, studio_id=sid)
    for card in list_cards(tx, studio_id=sid):
        if card.kind == "fetch_failed":
            withdraw_card(tx, card.id)
    _clear_dev(tx, "same_bytes", sid)
    _clear_dev(tx, "wrong_job", sid)
    log_event(tx, at=now, kind="fetches_agreed", studio_id=sid, text=agreement.reason)


# ----- the nightly re-fetch


def _after_refetch(ctx: Ctx, op: PendingRecord, job: JobRecord, outcome: _Outcome) -> None:
    now = ctx.clock.now()
    if op.kind == REFETCH_2:
        _confirm_change(ctx, op, job, outcome, now)
        return
    store, sid, fetched = ctx.store, job.studio_id, outcome.fetched
    changed = False
    if fetched.ok and fetched.check is not None and fetched.check.ok:
        changed = _differs_from_governing(store, sid, fetched.check)
    with store.transaction() as tx:
        render = _add_outcome(tx, outcome)
        if fetched.code == "wrong_job":
            if _renamed(tx, job, fetched, now):
                retry_pending(tx, op.id, due_at=now, note=None)
                return
            open_dev(tx, at=now, code="wrong_job", text=fetched.reason, studio_id=sid)
        if changed:
            set_state(tx, _first_key(sid), str(render.id))
            add_pending(tx, REFETCH_2, due_at=outcome.at + _TWO_FETCHES, studio_id=sid)
        _next_night(tx, op, sid, now)


def _next_night(tx: Transaction, op: PendingRecord, studio_id: str, now: datetime) -> None:
    """Once a night for 30 days from filing, with no deadline: nothing re-files by itself."""
    facts = filed_facts(tx, studio_id)
    if facts is None or now >= from_iso(facts["at"]) + _REFETCH_FOR:
        finish_pending(tx, op.id, "done")
    else:
        retry_pending(tx, op.id, due_at=next_at(now, _NIGHT), note=None)


def _differs_from_governing(store: Store, studio_id: str, check: SummaryCheck) -> bool:
    """A real change: not the filed content, not one Jim already chose to keep, and no card out
    already. A fingerprint read by another pypdf version is read again first, and one that can't
    be is "could not verify": no card."""
    if any(card.kind == "ic_changed" for card in list_cards(store, studio_id=studio_id)):
        return False
    if check.details["fingerprint"] in (_load(store, _declined_key(studio_id)) or []):
        return False
    governing = governing_render(store, studio_id)
    if governing is None:
        return False
    stored = render_fingerprint(store, governing.id, pypdf=check.details["pypdf"],
                                read=content_fingerprint)
    return stored is not None and stored != check.details["fingerprint"]


def _confirm_change(ctx: Ctx, op: PendingRecord, job: JobRecord, outcome: _Outcome,
                    now: datetime) -> None:
    """The second of the night's fetches: only two agreeing ones raise the card."""
    store, sid, fetched = ctx.store, job.studio_id, outcome.fetched
    first = _first_render(store, sid)
    complete = fetched.ok and fetched.check is not None and fetched.check.ok
    agreement = _agreement(first, outcome) if complete else None
    still = complete and _differs_from_governing(store, sid, fetched.check)
    with store.transaction() as tx:
        render = _add_outcome(tx, outcome)
        if not complete or agreement is None or agreement.code == "first_incomplete":
            if complete and op.attempts < _CONFIRM_TRIES:
                set_state(tx, _first_key(sid), str(render.id))
                retry_pending(tx, op.id, due_at=outcome.at + _TWO_FETCHES)
            else:
                _end_confirming(tx, op, sid)       # the next night looks again
            return
        code = agreement.code
        if code == "agree":
            if still:
                _ic_changed_at_night(tx, job, render, agreement, first, now)
            _end_confirming(tx, op, sid)
        elif code == "layouts_differ":
            open_dev(tx, at=now, code="layouts_differ", text=agreement.reason, studio_id=sid)
            _end_confirming(tx, op, sid)
        elif op.attempts >= _CONFIRM_TRIES:
            _end_confirming(tx, op, sid)
        elif code == "too_soon":
            retry_pending(tx, op.id, due_at=first.fetched_at + _TWO_FETCHES)
        elif code == "second_incomplete":
            retry_pending(tx, op.id, due_at=outcome.at + _EARLY_RETRY)
        else:                                      # differ or same_bytes
            if code == "differ":
                set_state(tx, _first_key(sid), str(render.id))
            else:
                open_dev(tx, at=now, code="same_bytes", text=agreement.reason, studio_id=sid)
            retry_pending(tx, op.id, due_at=outcome.at + _TWO_FETCHES)


def _end_confirming(tx: Transaction, op: PendingRecord, studio_id: str) -> None:
    finish_pending(tx, op.id, "done")
    clear_state(tx, _first_key(studio_id))


def _ic_changed_at_night(tx: Transaction, job: JobRecord, render: RenderRecord,
                         agreement: Agreement, first: RenderRecord, now: datetime) -> None:
    facts = filed_facts(tx, job.studio_id)
    if facts is None or _open_card(tx, job.studio_id, "ic_changed"):
        return
    spec = templates.ic_changed_card(job.name, filed_at=from_iso(facts["at"]),
                                     filed_pages=facts["pages"], new_at=render.fetched_at,
                                     new_pages=render.page_count or 0)
    _add_card(tx, spec, now, studio_id=job.studio_id)
    set_state(tx, _ic_key(job.studio_id), json.dumps({
        "first": False, "render": render.id, "fingerprint": agreement.fingerprint,
        "folder": facts["folder"], "where": facts["where"], "branch": facts["branch"],
        "fetched": [to_iso(first.fetched_at), to_iso(render.fetched_at)]}))
    log_event(tx, at=now, kind="ic_changed", studio_id=job.studio_id, text=agreement.reason)


# ----- step 6: filing


@dataclass(frozen=True)
class _Found:
    """A summary already in the job's folder: where, what it reads as, and its fingerprint."""

    path: Path
    where: str     # "ng_jobs", "old" or "shadow"
    check: SummaryCheck
    fingerprint: str


def _file_ready(ctx: Ctx) -> None:
    """While paused nothing is even tried, so a pause never reads as a refusal."""
    if is_paused(ctx.store):
        return
    now = ctx.clock.now()
    ops = [op for op in list_pending(ctx.store, kind=FILE) if op.due_at <= now]
    _each(ctx, "file_ready", ops, lambda op: _file_one(ctx, op, now))
    ops = [op for op in list_pending(ctx.store, kind=FILE_NEW) if op.due_at <= now]
    _each(ctx, "file_new", ops, lambda op: _file_new_one(ctx, op, now))


def _file_one(ctx: Ctx, op: PendingRecord, now: datetime) -> None:
    store, sid = ctx.store, op.studio_id
    job = None if sid is None else get_job(store, sid)
    ready = None if sid is None else _load(store, _ready_key(sid))
    if job is None or ready is None:
        finish_pending(store, op.id, "failed", note="Nothing was ready to file")
        return
    if _a_question_holds(store, sid):
        retry_pending(store, op.id, due_at=now + _HOLD, note=None)
        return
    render = get_render(store, ready["second"])
    data = _cached(render)
    if render is None or data is None:             # no agreed pair any more: fetch one again
        _start_over(ctx, op, job, now, "Its cached IC summary is gone, so it is fetched again.")
        return
    check = check_summary(data)
    branch = _branch_for(ctx, job, check, now)
    if branch is None:
        retry_pending(store, op.id, due_at=now + _HOLD, note=None)
        return
    folder = job.folder or _folder_name(job.name, branch)
    if not ctx.roots.ng_jobs.is_dir():
        _refused(ctx, op, _NOT_REACHABLE, now)
        return
    try:
        found = _summary_in_folders(ctx, job, folder)
    except (OSError, ValueError) as exc:
        _refused(ctx, op, f"The summary in its folder can't be read ({type(exc).__name__})", now)
        return
    pair = _Pair(ready["first"], render, data, ready["fingerprint"], branch, folder)
    if found is not None:
        _compare_with_folder(ctx, op, job, pair, found, now)
    elif job.mode == "observed":
        _observed_waits(ctx, op, job, now)
    elif _only_in_old(ctx, folder):
        _refused(ctx, op, _OLD_REASON, now, retry=_WATCH_AGAIN)
    else:
        _commit_new(ctx, op, job, pair, now)


def _a_question_holds(db: Db, studio_id: str) -> bool:
    """A row of this job whose new text isn't settled yet: nothing new is filed until it is."""
    return bool(list_pending(db, kind=LIST_REMATCH, studio_id=studio_id)) or _open_card(
        db, studio_id, "row_changed")


def _cached(render: RenderRecord | None) -> bytes | None:
    """The render's bytes, if the cache still holds exactly what was fetched."""
    if render is None or render.cache_path is None:
        return None
    try:
        data = render.cache_path.read_bytes()
    except OSError:
        return None
    return data if _sha(data) == render.sha256 else None


def _start_over(ctx: Ctx, op: PendingRecord, job: JobRecord, now: datetime, why: str) -> None:
    with ctx.store.transaction() as tx:
        finish_pending(tx, op.id, "done")
        clear_state(tx, _ready_key(job.studio_id))
        clear_state(tx, _first_key(job.studio_id))
        add_pending(tx, FETCH_1, due_at=now, deadline_at=now + _FETCH_LIMIT,
                    studio_id=job.studio_id)
        log_event(tx, at=now, kind="fetch_again", studio_id=job.studio_id, text=why)


def _branch_for(ctx: Ctx, job: JobRecord, check: SummaryCheck, now: datetime) -> str | None:
    """Closet or garage, asked about each row's text and the Opportunity Name alike. Unclear raises
    the card, and None says only this job waits. An answer already given stands."""
    store, sid = ctx.store, job.studio_id
    if job.branch:
        return job.branch
    opportunity = get_opportunity(store, sid)
    texts = [r.text for r in _rows_of(store, sid)] + [job.name if opportunity is None
                                                      else opportunity.name]
    verdicts = [branch_of(check, text) for text in texts]
    unclear = next((v for v in verdicts if v.kind == "unclear"), None)
    if unclear is None:
        return verdicts[0].kind
    with store.transaction() as tx:
        if not _open_card(tx, sid, "closet_or_garage"):
            spec = templates.closet_or_garage_card(job.name, reason=unclear.reason)
            _add_card(tx, spec, now, studio_id=sid)
    return None


def _rows_of(db: Db, studio_id: str) -> list[FabRowRecord]:
    return [r for r in list_fab_rows(db) if r.studio_id == studio_id and r.state != "gone"]


def _folder_name(name: str, branch: str) -> str:
    if branch == "garage" and not name.casefold().endswith(GARAGE_SUFFIX.casefold()):
        return name + GARAGE_SUFFIX
    return name


def _resolved(root: Path) -> Path:
    """A root as commit_write spells it, so a path built on it reads the write record's own key."""
    return Path(os.path.realpath(root))


def _dir_of(ctx: Ctx, where: str, folder: str) -> Path:
    if where == "shadow":
        return _resolved(ctx.roots.control) / SHADOW / folder
    if where == "old":
        return _resolved(ctx.roots.ng_jobs) / "Old" / folder
    return _resolved(ctx.roots.ng_jobs) / folder


def _only_in_old(ctx: Ctx, folder: str) -> bool:
    return _dir_of(ctx, "old", folder).is_dir() and not _dir_of(ctx, "ng_jobs", folder).is_dir()


def _summary_in_folders(ctx: Ctx, job: JobRecord, folder: str) -> _Found | None:
    """A summary of this job in NG Jobs\\<folder>, then NG Jobs\\Old\\<folder>, then, in shadow
    mode, what an earlier pass of this run filed aside. Read-only."""
    places = ["ng_jobs", "old", *(("shadow",) if ctx.mode == SHADOW else ())]
    jobs, names = None, None
    for where in places:
        place = _dir_of(ctx, where, folder)
        if not place.is_dir():
            continue
        if jobs is None:
            jobs = list_jobs(ctx.store)
            names = [o.name for o in get_job_list(ctx.store)[1]]
        found = _summary_in(place, where, job, jobs, names or [], folder)
        if found is not None:
            return found
    return None


def _summary_in(place: Path, where: str, job: JobRecord, jobs: Sequence[JobRecord],
                names: Sequence[str], folder: str) -> _Found | None:
    """Its own name first: that file is the job's summary whatever it holds, so it is compared and
    never written over. Then any other PDF that is this job's: named as a summary and matched to
    it, or hand-named and carrying its PROJECT NAME. Other PDFs (an assembly print) are not."""
    own = f"{folder}_summary.pdf".casefold()
    with os.scandir(place) as entries:
        pdfs = sorted((e.name for e in entries
                       if e.is_file(follow_symlinks=False) and e.name.casefold().endswith(".pdf")),
                      key=lambda name: name.casefold() != own)
    own_names = {slugify(n) for n in (job.name, *job.aliases)}
    for name in pdfs:
        if name.casefold() != own:
            verdict = match_file_to_job(name, jobs, folder=folder, opportunity_names=names)
            if verdict.status != "matched" or verdict.job.studio_id != job.studio_id:
                continue
        check = check_summary(place / name)
        reading = normalize_job_file(name)
        named = name.casefold() == own or reading.kind in ("pipeline", "studio", "dated")
        if named or (check.project_name and slugify(check.project_name) in own_names):
            return _Found(place / name, where, check, content_fingerprint(place / name))
    return None


def _compare_with_folder(ctx: Ctx, op: PendingRecord, job: JobRecord, pair: _Pair, found: _Found,
                         now: datetime) -> None:
    """Like with like: another layout means a fetched pair in that layout, and the job keeps it.
    The same content is filed with no write, unless a crash cut NG Runner's own commit short.
    Different content is Jim's to settle, and is never overwritten."""
    layout = found.check.details.get("orientation")
    if layout in LAYOUTS and layout != _layout_of(ctx.store, ctx.settings, job.studio_id):
        _keep_layout(ctx, op, job, layout, now)
        return
    if found.fingerprint != pair.fingerprint:
        _ic_changed_first(ctx, op, job, pair, found, now)
        return
    if found.where != "old" and job.mode != "observed" and _is_own(ctx, found.path, pair.folder):
        there = replace(pair, data=found.path.read_bytes())    # the notes describe what is there
        if _commit_missing(ctx, op, job, there, found.where, now):
            _file_done(ctx, op, job, there, found.where, now, wrote=True)
        return
    facts = _facts_from_file(found, pair, job, now)
    with ctx.store.transaction() as tx:
        finish_pending(tx, op.id, "done")
        _mark_filed(tx, job, facts, now, no_write=True)


def _keep_layout(ctx: Ctx, op: PendingRecord, job: JobRecord, layout: str, now: datetime) -> None:
    sid = job.studio_id
    with ctx.store.transaction() as tx:
        set_state(tx, _layout_key(sid), layout)
        clear_state(tx, _ready_key(sid))
        clear_state(tx, _first_key(sid))
        finish_pending(tx, op.id, "done")
        add_pending(tx, FETCH_1, due_at=now, deadline_at=now + _FETCH_LIMIT, studio_id=sid)
        log_event(tx, at=now, kind="layout_kept", studio_id=sid,
                  text=f"The summary in its folder is {layout}, so {job.name} is fetched and kept"
                       f" in {layout}.")


def _ic_changed_first(ctx: Ctx, op: PendingRecord, job: JobRecord, pair: _Pair, found: _Found,
                      now: datetime) -> None:
    sid = job.studio_id
    question = {
        "first": True, "render": pair.render.id, "fingerprint": pair.fingerprint,
        "folder": pair.folder, "where": found.where, "branch": pair.branch,
        "fetched": _fetched_times(ctx.store, pair),
        "found_sha256": _sha_file(found.path),
        "found_layout": found.check.details.get("orientation"),
        "found_pages": found.check.page_count,
    }
    filed_at = datetime.fromtimestamp(found.path.stat().st_mtime, tz=UTC)
    with ctx.store.transaction() as tx:
        finish_pending(tx, op.id, "done")
        set_state(tx, _ic_key(sid), json.dumps(question))
        if not _open_card(tx, sid, "ic_changed"):
            spec = templates.ic_changed_card(job.name, filed_at=filed_at,
                                             filed_pages=found.check.page_count,
                                             new_at=pair.render.fetched_at,
                                             new_pages=pair.render.page_count or 0)
            _add_card(tx, spec, now, studio_id=sid)


def _observed_waits(ctx: Ctx, op: PendingRecord, job: JobRecord, now: datetime) -> None:
    """An OBSERVED job gets no writes, and its row says why. It keeps looking: a summary that
    arrives with the same content still marks it filed."""
    with ctx.store.transaction() as tx:
        retry_pending(tx, op.id, due_at=now + _WATCH_AGAIN, note=None)
        for row in _rows_of(tx, job.studio_id):
            if row.reason != _OBSERVED_REASON:
                update_fab_row(tx, row.id, now=now, reason=_OBSERVED_REASON)


def _is_own(ctx: Ctx, path: Path, folder: str) -> bool:
    return (path.name.casefold() == f"{folder}_summary.pdf".casefold()
            and owned_sha(ctx.store, path) == _sha_file(path))


def _refused(ctx: Ctx, op: PendingRecord, reason: str, now: datetime, *,
             retry: timedelta = _REFUSED_RETRY) -> None:
    """BLOCKED, with the reason, and tried again in case it clears: paused, unreachable or "folder
    changed". The job model reads the note."""
    with ctx.store.transaction() as tx:
        retry_pending(tx, op.id, due_at=now + retry, note=reason)
        if op.note != reason:
            log_event(tx, at=now, kind="blocked", studio_id=op.studio_id, text=reason)


def _commit_new(ctx: Ctx, op: PendingRecord, job: JobRecord, pair: _Pair, now: datetime) -> None:
    """The three files, in one commit: whichever are missing, and only those, since a crash can
    leave part of an earlier commit adopted into owned. Shadow mode files aside."""
    where = "shadow" if ctx.mode == SHADOW else "ng_jobs"
    if _commit_missing(ctx, op, job, pair, where, now):
        _file_done(ctx, op, job, pair, where, now, wrote=True)


def _commit_missing(ctx: Ctx, op: PendingRecord, job: JobRecord, pair: _Pair, where: str,
                    now: datetime) -> bool:
    place = _dir_of(ctx, where, pair.folder)
    facts = _facts_from_pair(ctx, pair, job, where, now)
    files = {f"{pair.folder}_summary.pdf": pair.data,
             f"{pair.folder}_NGRunner.json": _notes_bytes(facts, []),
             f"{pair.folder}_NGRunner_log.txt": _log_line(now, _filed_words(facts)).encode("utf-8")}
    writes = [FileWrite(place / name, content, None) for name, content in files.items()
              if not os.path.lexists(place / name)]
    if not writes:
        return True
    result = ctx.commit(writes)
    if not result.ok:
        _refused(ctx, op, result.reason or "The filing was refused", now)
    return result.ok


def _file_done(ctx: Ctx, op: PendingRecord, job: JobRecord, pair: _Pair, where: str, now: datetime,
               *, wrote: bool) -> None:
    facts = _facts_from_pair(ctx, pair, job, where, now, wrote=wrote)
    with ctx.store.transaction() as tx:
        finish_pending(tx, op.id, "done")
        _mark_filed(tx, job, facts, now, no_write=False)


@dataclass(frozen=True)
class _Pair:
    """The agreed pair to file, and where."""

    first_id: int
    render: RenderRecord
    data: bytes
    fingerprint: str
    branch: str
    folder: str


def _fetched_times(db: Db, pair: _Pair) -> list[str]:
    first = get_render(db, pair.first_id)
    return [to_iso(first.fetched_at if first is not None else pair.render.fetched_at),
            to_iso(pair.render.fetched_at)]


def _facts_from_pair(ctx: Ctx, pair: _Pair, job: JobRecord, where: str, now: datetime, *,
                     wrote: bool = True) -> dict[str, Any]:
    sid = job.studio_id
    return {"job": job.name, "at": to_iso(now), "folder": pair.folder, "where": where,
            "wrote": wrote, "branch": pair.branch, "sha256": _sha(pair.data),
            "layout": pair.render.layout or _layout_of(ctx.store, ctx.settings, sid),
            "pages": pair.render.page_count or 0, "fetched": _fetched_times(ctx.store, pair),
            "items": [], "replaced_by_hand": False, **_stat_of(_dir_of(
                ctx, where, pair.folder) / f"{pair.folder}_summary.pdf")}


def _facts_from_file(found: _Found, pair: _Pair, job: JobRecord, now: datetime) -> dict[str, Any]:
    return {"job": job.name, "at": to_iso(now), "folder": pair.folder, "where": found.where,
            "wrote": False, "branch": pair.branch, "sha256": _sha_file(found.path),
            "layout": found.check.details.get("orientation"), "pages": found.check.page_count,
            "fetched": [], "items": [], "replaced_by_hand": False}


def _facts_of_found(question: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Jim kept the summary in the folder: it is filed as it is, and NG Runner wrote none of it."""
    return {"job": "", "at": to_iso(now), "folder": question["folder"], "where": question["where"],
            "wrote": False, "branch": question["branch"], "sha256": question.get("found_sha256"),
            "layout": question.get("found_layout"), "pages": question.get("found_pages") or 0,
            "fetched": [], "items": [], "replaced_by_hand": False}


def _mark_filed(tx: Transaction, job: JobRecord, facts: dict[str, Any], now: datetime, *,
                no_write: bool) -> None:
    """Everything that says the job is filed, together: its name frozen, folder and branch set, its
    rows filed and told, the facts its notes are kept from, and the 30 days of night re-fetches."""
    sid = job.studio_id
    facts = {**facts, "job": job.name}
    update_job(tx, sid, now=now, folder=facts["folder"], branch=facts["branch"])
    frozen = freeze_name(tx, sid, now=now)
    set_state(tx, _filed_key(sid), json.dumps(facts))
    clear_state(tx, _ready_key(sid))
    clear_state(tx, _first_key(sid))
    for card in list_cards(tx, studio_id=sid):
        if card.kind == "fetch_failed":
            withdraw_card(tx, card.id)
    for row in _rows_of(tx, sid):
        if row.state != "filed":
            update_fab_row(tx, row.id, now=now, state="filed", reason=None)
            _queue_filed(tx, frozen, row, now)
    add_pending(tx, REFETCH, due_at=next_at(now, _NIGHT), studio_id=sid)
    words = ("Its folder already holds this IC summary, so nothing was written."
             if no_write else _filed_words(facts))
    log_event(tx, at=now, kind="filed", studio_id=sid, text=words)


def _queue_filed(tx: Transaction, job: JobRecord, row: FabRowRecord, now: datetime) -> None:
    """Barbara's "Filed as <Job>", once per row. A job added by hand has no row, so no email."""
    message = templates.filed(job.name, row=row.text, designer=row.designer,
                              install=_install_of(row, now), garage=job.branch == "garage")
    _queue_tell(tx, TELL_FILED, message, to=BARBARA, fab_row_id=row.id, now=now,
                ref=f"filed:{row.id}")


def _filed_words(facts: dict[str, Any]) -> str:
    fetched = facts.get("fetched") or []
    apart = ""
    if len(fetched) == 2:
        gap = from_iso(fetched[1]) - from_iso(fetched[0])
        apart = f", checked on two fetches {int(gap.total_seconds() // 60)} minutes apart"
    return f"Filed the IC summary ({facts['layout']}, {_count(facts['pages'], 'page')}){apart}."


def _file_new_one(ctx: Ctx, op: PendingRecord, now: datetime) -> None:
    """Jim chose the new render, so it is filed, and only commit_write's own rule can stop it: a
    file is overwritten only while it still holds what NG Runner last wrote there."""
    store, sid = ctx.store, op.studio_id
    question = None if sid is None else _load(store, _ic_key(sid))
    job = None if sid is None else get_job(store, sid)
    render = None if question is None else get_render(store, question["render"])
    data = _cached(render)
    if job is None or question is None or render is None or data is None:
        finish_pending(store, op.id, "failed", note="The new IC summary is no longer cached")
        return
    if question["where"] == "old":
        _refused(ctx, op, _OLD_REASON, now, retry=_WATCH_AGAIN)
        return
    folder = question["folder"]
    place = _dir_of(ctx, question["where"], folder)
    facts = {"job": job.name, "at": to_iso(now), "folder": folder, "where": question["where"],
             "wrote": True, "branch": question["branch"], "sha256": _sha(data),
             "layout": render.layout, "pages": render.page_count or 0,
             "fetched": question["fetched"], "items": [], "replaced_by_hand": False}
    old_log = _read_or_none(place / f"{folder}_NGRunner_log.txt") or b""
    line = _log_line(now, f"Filed the new IC summary, as Jim chose. {_filed_words(facts)}")
    files = {f"{folder}_summary.pdf": data, f"{folder}_NGRunner.json": _notes_bytes(facts, []),
             f"{folder}_NGRunner_log.txt": old_log + line.encode("utf-8")}
    writes = [FileWrite(place / name, content, _sha_or_none(place / name))
              for name, content in files.items()]
    result = ctx.commit(writes)
    if not result.ok:
        _refused(ctx, op, result.reason or "The filing was refused", now,
                 retry=_backoff(op.attempts))
        return
    facts |= _stat_of(place / f"{folder}_summary.pdf")
    with store.transaction() as tx:
        finish_pending(tx, op.id, "done")
        set_governing(tx, render.id)
        clear_state(tx, _ic_key(sid))
        if filed_facts(tx, sid) is None:
            _mark_filed(tx, job, facts, now, no_write=False)
        else:
            set_state(tx, _filed_key(sid), json.dumps(facts))
            log_event(tx, at=now, kind="refiled", lane="ACT", studio_id=sid,
                      text="Filed the new IC summary, as Jim chose.")


# ----- section 4: keeping the notes in the job folder current


def _keep_notes_current(ctx: Ctx) -> None:
    """_NGRunner.json lists the job's open cards, and Jim's chat reads it first, so it is rewritten
    whenever a card opens or closes. While paused nothing is written; it catches up after."""
    store = ctx.store
    if is_paused(store):
        return
    now = ctx.clock.now()
    by_job: dict[str, list[CardRecord]] = {}
    for card in list_cards(store):
        if card.studio_id is not None:
            by_job.setdefault(card.studio_id, []).append(card)
    listing = set(json.loads(get_state(store, _LISTING) or "[]"))

    def one(sid: str) -> None:
        facts = filed_facts(store, sid)
        if facts is None or not facts.get("wrote"):
            return
        items = _items(by_job.get(sid, ()))
        retry_at = facts.get("retry_at")
        if items == facts.get("items", []) or (retry_at and now < from_iso(retry_at)):
            return
        _rewrite_notes(ctx, sid, facts, items, now)

    _each(ctx, "keep_notes_current", sorted(listing | set(by_job)), one)
    still = sorted(sid for sid in listing | set(by_job)
                   if ((filed_facts(store, sid) or {}).get("items")))
    if still != sorted(listing):
        set_state(store, _LISTING, json.dumps(still))


def _rewrite_notes(ctx: Ctx, sid: str, facts: dict[str, Any], items: list[dict[str, str]],
                   now: datetime, *, line: str | None = None,
                   new_facts: dict[str, Any] | None = None) -> bool:
    """Rewrite the owned notes through commit_write. A folder pruned from control\\shadow, or moved
    to Old, or a file someone removed, is not "not filed": nothing is written and nothing blocks."""
    store = ctx.store
    folder = facts["folder"]
    place = _dir_of(ctx, facts["where"], folder)
    notes = place / f"{folder}_NGRunner.json"
    log = place / f"{folder}_NGRunner_log.txt"
    root = ctx.roots.control if facts["where"] == "shadow" else ctx.roots.ng_jobs
    if not root.is_dir():
        return False                               # out of sight: the next tick looks again
    try:
        current: bytes | None = notes.read_bytes()
    except FileNotFoundError:
        current = None
    except OSError:
        return False
    updated = {**facts, **(new_facts or {}), "items": items}
    updated.pop("retry_at", None)
    if current is None:
        with store.transaction() as tx:
            set_state(tx, _filed_key(sid), json.dumps(updated))
            log_event(tx, at=now, kind="notes_gone", studio_id=sid,
                      text="Its notes file is no longer in its folder, so it was not rewritten.")
        return False
    writes = [FileWrite(notes, _notes_bytes(updated, items), _sha(current))]
    if line is not None:
        old = _read_or_none(log)
        writes.append(FileWrite(log, (old or b"") + _log_line(now, line).encode("utf-8"),
                                None if old is None else _sha(old)))
    result = ctx.commit(writes)
    if not result.ok:
        again = {**facts, "retry_at": to_iso(now + _WATCH_AGAIN)}
        with store.transaction() as tx:
            set_state(tx, _filed_key(sid), json.dumps(again))
            open_dev(tx, at=now, code="notes_refused", text=result.reason or "Refused",
                     studio_id=sid)
        return False
    with store.transaction() as tx:
        set_state(tx, _filed_key(sid), json.dumps(updated))
        _clear_dev(tx, "notes_refused", sid)
    return True


def _notes_bytes(facts: dict[str, Any], items: list[dict[str, str]]) -> bytes:
    """<folder>_NGRunner.json, schema 1: for machines (Jim's chat). It holds no address."""
    summary = {"file": f"{facts['folder']}_summary.pdf", "sha256": facts["sha256"],
               "layout": facts["layout"], "pages": facts["pages"], "fetched": facts["fetched"]}
    if facts.get("replaced_by_hand"):
        summary["replaced_by_hand"] = True
    record = {"schema": 1, "job": facts["job"], "branch": facts["branch"], "summary": summary,
              "filed_at": facts["at"], "items": items, "note": _NOTE}
    return (json.dumps(record, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def _items(cards: Iterable[CardRecord]) -> list[dict[str, str]]:
    return [{"card": card.id, "kind": card.kind,
             "question": templates.QUESTIONS.get(card.kind, card.kind),
             "opened": to_iso(card.created_at)} for card in cards]


def _log_line(now: datetime, text: str) -> str:
    return f"{fmt_date(now)} {fmt_clock(now)}  {text}\n"


def _watch_filed(ctx: Ctx) -> None:
    """A summary replaced by hand (RULES allow it on Jim's word) is logged and never overwritten.
    Its notes are rewritten to describe the file that is there. A stat tells whether to look."""
    store, now = ctx.store, ctx.clock.now()
    if is_paused(store):
        return
    last = get_state_time(store, _WATCHED)
    if last is not None and now - last < _WATCH_EVERY:
        return
    set_state_time(store, _WATCHED, now)

    def one(job: JobRecord) -> None:
        facts = filed_facts(store, job.studio_id)
        if facts is None or not facts.get("wrote") or facts.get("replaced_by_hand"):
            return
        path = _dir_of(ctx, facts["where"], facts["folder"]) / f"{facts['folder']}_summary.pdf"
        stat = _stat_of(path)
        if not stat or stat == {k: facts.get(k) for k in stat}:
            return                                 # gone (pruned, or moved to Old), or untouched
        if _sha_file(path) == facts["sha256"]:
            set_state(store, _filed_key(job.studio_id), json.dumps({**facts, **stat}))
            return
        _replaced_by_hand(ctx, job, facts, path, stat, now)

    _each(ctx, "watch_filed", list_jobs(store), one)


def _replaced_by_hand(ctx: Ctx, job: JobRecord, facts: dict[str, Any], path: Path,
                      stat: dict[str, int], now: datetime) -> None:
    check = check_summary(path)
    new = {"sha256": _sha_file(path), "replaced_by_hand": True, "fetched": [], **stat,
           "layout": check.details.get("orientation") or facts["layout"],
           "pages": check.page_count or facts["pages"]}
    if _rewrite_notes(ctx, job.studio_id, facts, facts.get("items", []), now, new_facts=new,
                      line="The IC summary was replaced by hand. NG Runner left it as it is."):
        log_event(ctx.store, at=now, kind="summary_replaced", studio_id=job.studio_id,
                  text="Its IC summary was replaced by hand, and NG Runner left it as it is.")


# ----- step 7: telling


def _queue_tell(tx: Transaction, kind: str, message: Message, *, to: str, ref: str, now: datetime,
                fab_row_id: int | None = None) -> None:
    """Note a message to send, in the transaction that decided it. Its ref says which message it
    is, so one sent before a crash isn't sent again."""
    op = add_pending(tx, kind, due_at=now, fab_row_id=fab_row_id, note=ref)
    set_state(tx, _tell_key(op.id), json.dumps({
        "to": to, "kind": message.kind, "title": message.title, "lines": list(message.lines),
        "urgent": message.urgent}, ensure_ascii=False))


def _send_tells(ctx: Ctx) -> None:
    store = ctx.store
    ops = [op for op in list_pending(store) if op.kind.startswith(_TELL)]
    if not ops:
        return
    sent = {record.ref for record in list_outbox(store)}
    held = getattr(ctx.messenger, "held", None)
    if held is not None:
        sent |= {entry.get("ref") for entry in held()}

    def one(op: PendingRecord) -> None:
        told = _load(store, _tell_key(op.id))
        if told is not None and op.note not in sent:
            message = Message(told["kind"], told["title"], tuple(told["lines"]), told["urgent"])
            ctx.messenger.send(message, to=told["to"], ref=op.note)
            sent.add(op.note)
        with store.transaction() as tx:
            finish_pending(tx, op.id, "done")
            clear_state(tx, _tell_key(op.id))

    _each(ctx, "send_tells", ops, one)


# ----- Add job


def add_job(ctx: Ctx, *, name: str | None = None, studio_id: str | None = None) -> str:
    """Add job (the board's button, or "add job <name or Studio ID>"): enters at step 3 with the
    text as an exact name, or at step 4 for a Studio ID found in the list. Typed text goes through
    cards.parse_command first, so digits mean a Studio ID. A job added by hand has no Fab row, so
    no "Filed as" email. Returns one plain line, which also goes to the event log."""
    now, store = ctx.clock.now(), ctx.store
    parsed = parse_command(f"add job {name}", ()) if name else None
    if isinstance(parsed, Command) and parsed.studio_id:
        name, studio_id = None, parsed.studio_id
    opportunity, problem = _opportunity_for(ctx, name, studio_id)
    if opportunity is None:
        log_event(store, at=now, kind="add_job", lane="ACT", text=problem)
        return problem
    with store.transaction() as tx:
        job = get_job(tx, opportunity.studio_id)
        if job is None:
            mode = "observed" if _found_at_install(tx, opportunity.name) else "auto"
            job = ensure_job(tx, opportunity.studio_id, opportunity.name, now=now, mode=mode)
        if filed_facts(tx, job.studio_id) is not None:
            words = f"{job.name} is already filed."
        else:
            _start_job(tx, job.studio_id, now)
            words = f"Added {job.name}: fetching its IC summary now."
        log_event(tx, at=now, kind="add_job", lane="ACT", studio_id=job.studio_id, text=words)
    return words


def _opportunity_for(ctx: Ctx, name: str | None, studio_id: str | None
                     ) -> tuple[OpportunityRecord | None, str]:
    store = ctx.store
    state, listed = get_job_list(store)
    if not listed or state is None or state.synced_at is None:
        return None, "The job list has not been read yet, so that job can't be added yet."
    if studio_id is not None:
        found = get_opportunity(store, studio_id)
        return found, "" if found else "That Studio project is not in the job list."
    typed = _spaced(name or "")
    # The typed text is an exact name, so the row's own time never makes the list look old.
    result = match_fab_row(FabRow(typed, "", state.synced_at), [o.to_match() for o in listed], (),
                           state.synced_at, sold_stages=ctx.settings.sold_stages)
    if result.status == "matched" and result.reason == "exact_name":
        return get_opportunity(store, result.job.studio_id), ""
    if result.status == "ambiguous" and len(result.candidates) > 1:
        return None, f"More than one job could be {typed}, so none was added."
    return None, f"No job in the list is named exactly {typed}."


# ----- what the board may read


def filed_facts(db: Db, studio_id: str) -> dict[str, Any] | None:
    """What NG Runner knows of a filed job: folder, where ("ng_jobs", "old" or "shadow"), wrote
    (its own notes are there), branch, sha256, layout, pages, fetched, items, replaced_by_hand and
    at. None until it is filed."""
    return _load(db, _filed_key(studio_id))


def blocked_reason(db: Db, studio_id: str) -> str | None:
    """Why a job's filing was refused, in plain words; None when it wasn't."""
    return next((op.note for kind in (FILE, FILE_NEW)
                 for op in list_pending(db, kind=kind, studio_id=studio_id) if op.note), None)


# ----- small pieces


def _add_card(tx: Transaction, spec: Any, now: datetime, *, studio_id: str | None = None,
              fab_row_id: int | None = None) -> CardRecord:
    card = add_card(tx, prefix=card_prefix(spec.job or spec.row or ""), kind=spec.kind,
                    text=spec.text, choices=spec.choices, created_at=now, studio_id=studio_id,
                    fab_row_id=fab_row_id, default_choice=spec.default_choice,
                    choice_ids=spec.choice_ids)
    log_event(tx, at=now, kind="card_opened", lane="ACT", studio_id=studio_id,
              text=f"Card {card.id}: {templates.QUESTIONS.get(spec.kind, 'a question')}")
    return card


def _open_card(db: Db, studio_id: str, kind: str) -> bool:
    return any(card.kind == kind for card in list_cards(db, studio_id=studio_id))


def _open_row_card(db: Db, row_id: int, kind: str) -> bool:
    return any(card.kind == kind for card in list_cards(db, fab_row_id=row_id))


def _clear_dev(db: Db, code: str, studio_id: str | None = None) -> None:
    """The standing problem has cleared (a code in templates.ALARMS is that alarm standing)."""
    for item in list_dev(db):
        if item.code == code and (studio_id is None or item.studio_id == studio_id):
            close_dev(db, item.id)


def _install_of(row: FabRowRecord, now: datetime) -> Any:
    return None if row.install is None else install_date(row.install, near=now)


def _load(db: Db, key: str) -> Any:
    text = get_state(db, key)
    return None if not text else json.loads(text)


def _read_or_none(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha(path.read_bytes())


def _sha_or_none(path: Path) -> str | None:
    data = _read_or_none(path)
    return None if data is None else _sha(data)


def _stat_of(path: Path) -> dict[str, int]:
    """Size and modified time, to tell cheaply whether a file may have changed; {} when gone."""
    try:
        st = os.stat(path)
    except OSError:
        return {}
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def _spaced(text: str) -> str:
    return " ".join(text.split())


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _short_hash(text: str) -> str:
    return _sha(_spaced(text).encode("utf-8"))[:12]


def _first_key(sid: str) -> str:
    return f"job.first.{sid}"


def _ready_key(sid: str) -> str:
    return f"job.ready.{sid}"


def _filed_key(sid: str) -> str:
    return f"job.filed.{sid}"


def _layout_key(sid: str) -> str:
    return f"job.layout.{sid}"


def _fetch_name_key(sid: str) -> str:
    return f"job.fetch_name.{sid}"


def _ic_key(sid: str) -> str:
    return f"job.ic.{sid}"


def _declined_key(sid: str) -> str:
    return f"job.declined.{sid}"


def _gone_key(row_id: int) -> str:
    return f"fab.gone.{row_id}"


def _tell_key(op_id: int) -> str:
    return f"tell.{op_id}"


_STEPS = (_take_fetches, _send_tells, _read_fab, _refresh_list, _act_on_answers, _match_rows,
          _rematch_rows, _raise_deadline_cards, _start_fetches, _take_fetches, _file_ready,
          _keep_notes_current, _watch_filed, _send_tells)
