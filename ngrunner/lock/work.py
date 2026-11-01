r"""
P4 at work in the running app: Jim's three lock clicks, the batch after them, the leases, and the fixes,
from the board's buttons to the drop and the write (design 2, 3.4 and 5; decisions 7a to 7c, 8 and 9).

Why it is built this way:
- Everything P4 writes goes to NG Runner's own copies on this PC until P4 goes live (config.shadow_roots):
  a batch into a test folder, a fix into a copy of the room, a lease and a job sheet into a copy of the
  job's folder. The P4 interlock is built on the folders NG Runner is configured with and the tracker's
  folder (settings.tracker_folder), so it refuses anything below them whatever roots a commit is handed,
  and writes.py's P4 rules refuse the real tracker and Mozaik Shared by name besides. So nothing here can
  reach the tracker, Mozaik Shared or G:. Each message goes to Sulaiman alone, marked SHADOW, and none
  is a toast.
- No read of Drive waits on the scheduler's thread. A click's files (the rooms, the IC Conversion, the
  nests, the tracker's snapshot), the after-drop step's, a fix's plan, its staging and its survival
  check are read, and Jim's scripts run, on a worker of their own, one thing at a time; what they find is
  acted on here, where the store is written and every commit is made.
- Only Jim's click locks anything (decision 8). A click is first held to check-before-lock on what the
  store says (the board shows the same), so a refusal says at once exactly what is missing; Jim may confirm
  "lock without check", which is logged, and waives only the check. Then its files are read and it is
  held to the gate again on the bytes there now, before anything is dropped.
- A batch is judged on each new snapshot (drop.read_after on the worker, drop.judge_after here). Doubt is
  never re-dropped: only when the after-drop step says a missing row may be re-sent does the board offer
  [Re-send only the missing row], and resend_missing still waits for a snapshot taken after Jim asked.
- A shadow batch, which nothing ingests, is compared instead with the chat's own rows for its job once they
  show on the real tracker's snapshot (read only), and closes as matched or differing; with none after 7
  days, as not compared (lock/shadow.py). It blocks only a second drop for its own job (drop.in_flight).
  Each closing goes to Sulaiman, marked SHADOW, and is P4's "5 locks match the chat".
- A fix is planned from the real room (read only) when a check that counts finds a notch on the wrong
  face, and becomes a card in the plan's words. Only Jim's approval of that very plan applies it: into a
  fresh copy of the room, under a lease and the P4 interlock, which waits while Mozaik runs, while the
  job's folder is open or changed in the last two minutes, and for two polls two minutes apart. If the
  room was saved meanwhile the plan is made again: the same finding applies, one gone closes, another asks
  again. Undo is the same door backwards. After the copy is saved again, the fix's own check says whether
  it survived, with Mozaik's sessions as the observer saw them: "Mozaik undid" is urgent with one redo,
  "You changed it in Mozaik" a line in the log.
- A lease is taken for a fix or an Undo, renewed while one waits, and released when none does, on Pause,
  and by release-all. A lapsed lease is taken again, never renewed (leases.py).

In shadow nothing saves NG Runner's copies but NG Runner, so on a real PC a fix's survival waits, and
nothing ingests the test folder: the day simulation drives both, and P4's go-live makes them real. Only a
live batch the tracker hasn't taken in 30 minutes is a DEV item: a shadow batch waits for the chat instead.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ngrunner import config
from ngrunner.checks.jobchecks import JobCheck, job_checks
from ngrunner.clock import Clock, to_iso
from ngrunner.guards.bak import DesignUnreadable
from ngrunner.guards.design import room_identity
from ngrunner.guards.kept import mozaik_sessions
from ngrunner.guards.observer import LOCK_FILE, OPTIMIZER_LOCK, UNS_FILE
from ngrunner.guards.observer import mozaik_running as observed_running
from ngrunner.guards.opt import OptUnreadable, read_opt
from ngrunner.guards.reads import Unreadable, read_shared
from ngrunner.guards.tracker import TrackerSnapshot, TrackerUnreadable, read_tracker
from ngrunner.interlock import GARAGE_SUFFIX, P4Interlock, p4_interlock
from ngrunner.lock import kept, leases
from ngrunner.lock import shadow as shadow_batches
from ngrunner.lock.clicks import ClickResult, finish_lock, lock, lock_design
from ngrunner.lock.drop import LOCKED, AfterRead, judge_after, read_after, resend_missing
from ngrunner.lock.fixes import FixPlan, FixRefused, Survival, apply_fix, plan_fix, replan, survival, undo_fix
from ngrunner.lock.gate import CLICKS, GateResult, lock_gate
from ngrunner.lock.job_sheet import JOB_SHEET_SUFFIX, MATCHES, JobSheetRun, make_job_sheet, write_job_sheet
from ngrunner.lock.leases import ACTIVE, LeaseResult, lease_held, release_lease, renew_lease, take_lease
from ngrunner.lock.names import NO_NAMES, lock_names, tracker_name_of_nest
from ngrunner.lock.notch import NOTCH_FACE
from ngrunner.messages import templates
from ngrunner.store import (
    JobRecord, NestRecord, Store, StoreError, get_card, get_job, list_cards, list_dev, list_jobs, list_nests,
    list_programs, log_event, open_dev, set_state,
)
from ngrunner.store.locks import (
    BatchRecord, get_batch, get_fix, list_batches, list_fixes, list_leases, record_survival, update_batch,
)
from ngrunner.toolkit import Snapshot, adopted_snapshot
from ngrunner.writes import Roots

_log = logging.getLogger("ngrunner.lock")

WORKER = "ngrunner-lock"
OVERRUN = timedelta(minutes=15)
FIX_WAITS = timedelta(hours=8)          # design 3.4: a fix waiting for Mozaik, 8 hours, then DEV
BATCH_WAITS = timedelta(minutes=30)     # design 3.4: a lock batch, 30 minutes, then DEV and the board
LOCK_FAILED, LOCK_STUCK = "lock_failed", "lock_stuck"
FIX_NOT_PLANNED, FIX_GAVE_UP, BATCH_SLOW = "fix_not_planned", "fix_gave_up", "lock_batch_waiting"
NO_JOB_FOLDER = "Its job folder isn't in Mozaik Shared, so there is nothing to lock yet"
_NOTCH = ("notch_check", "notch_face")
_ROOM_TAG = "R"
_SHEET_ASKED = "lock.sheet_asked"
_ROOM = re.compile(r"Room(\d+)\.des", re.IGNORECASE)
_OPEN = ("dropped", "archived", "first_ok")
_FINAL = ("This fix was undone already", "Undo is refused", "Undo is offered only", "This fix isn't the one",
          "Putting the values back", "The patch", "The file isn't")


def pc_name() -> str:
    """The lease's holder: this PC's name, never a person's or a job's."""
    return (os.environ.get("COMPUTERNAME") or socket.gethostname() or "this PC").strip()


# ----- what the P4 interlock asks of NG Runner


@dataclass(frozen=True)
class _Real:
    """The folders NG Runner is configured with, which P4's build never writes: roots from the settings
    (config.roots) and the tracker's folder. lease_held is NG Runner's own record of its leases."""

    store: Store
    roots: Roots
    clock: Clock
    tracker: Path | None

    def lease_held(self, job: str) -> bool:
        return lease_held(self.store, job, self.clock.now())


def interlock_for(ctx: Any, *, releasing: bool = False,
                  running: Callable[[], bool | None] = observed_running) -> P4Interlock:
    """The P4 interlock on ctx's real folders and tracker folder; releasing: for leases being released."""
    real = _Real(ctx.store, ctx.roots, ctx.clock, ctx.settings.tracker_folder)
    return p4_interlock(real, mozaik_running=running, releasing=releasing)


def shadow(ctx: Any) -> Roots:
    return config.shadow_roots(ctx.paths)


def release_interlock(store: Store, roots: Roots, clock: Clock, tracker: Path | None) -> P4Interlock:
    """The interlock a lease is released through: the real folders still refused, and a pause no bar, as
    a lease is a file Mozaik never reads and nothing else goes through it."""
    return p4_interlock(_Real(store, roots, clock, tracker), releasing=True)


def release_leases(store: Store, paths: Any, roots: Roots, tracker: Path | None, clock: Clock,
                   *, holder: str | None = None) -> list[LeaseResult]:
    """Release every lease NG Runner holds: Pause from the tray, the board or a signed file."""
    return leases.release_all(store=store, roots=config.shadow_roots(paths), clock=clock,
                              holder=holder or pc_name(), interlock=release_interlock(store, roots, clock, tracker))


# ----- the board's buttons: answered at once, from the store alone


def preview(store: Store, job: JobRecord, click: str, material: str | None, now: datetime,
            *, without_check: bool = False, checks: Mapping[str, JobCheck] | None = None,
            nests: Sequence[NestRecord] | None = None) -> GateResult:
    """Check-before-lock on what the store says now: the board's buttons, and a click's first answer. The
    rooms and the IC Conversion are as the check's last look found them, which the click reads again.
    checks and nests: as the board read them once for every row (job_checks, job_nests)."""
    check = (checks if checks is not None else job_checks(store, now)).get(job.studio_id)
    nests = list(nests) if nests is not None else job_nests(store, job)
    run = None if check is None else check.run
    identity = dict(run.identities) if run is not None else _nest_identity(nests)
    return lock_gate(click, nests=nests, check=check, room_sha256=dict(run.rooms) if run else {},
                     room_identity=identity, conversion_sha256=run.conversion_sha256 if run else None,
                     material=material, without_check=without_check)


def ask_lock(ctx: Any, studio_id: str, click: str, material: str | None, without_check: bool) -> str:
    """Jim's Lock design, Finish lock or Lock. A refusal says exactly what is missing; "lock without check"
    goes ahead only where the check is all that is missing, and is logged."""
    store, now = ctx.store, ctx.clock.now()
    job = get_job(store, studio_id)
    if click not in CLICKS or (click == "finish_lock") != bool(material) or job is None:
        return templates.NOT_A_LOCK
    if material and material not in {nest.material for nest in job_nests(store, job)}:
        return templates.LOCK_GONE
    folder = folder_of(job)
    if any(r["kind"] == "click" and r["folder"] == folder for r in kept.requests(store)):
        return templates.LOCK_BUSY
    label = templates.lock_label(click, material)
    gate = preview(store, job, click, material, now, without_check=without_check)
    if not gate.allowed:
        blocking = [m.words for m in gate.missing if not (without_check and m.waivable)]
        said = templates.lock_refused(folder, label=label, missing=blocking)
        log_event(store, at=now, kind="lock_refused", lane="ACT", studio_id=studio_id, text=said)
        return said
    said = templates.lock_asked(folder, label=label, without_check=gate.without_check)
    with store.transaction() as tx:
        kept.add_request(tx, kind="click", at=now, studio_id=studio_id, folder=folder, click=click,
                         material=material, without_check=gate.without_check)
        log_event(tx, at=now, kind="lock_without_check" if gate.without_check else "lock_asked", lane="ACT",
                  studio_id=studio_id, text=said)
    return said


def ask_resend(ctx: Any, batch_id: str) -> str:
    """[Re-send only the missing row]: only where the after-drop step said it may be offered."""
    store, now = ctx.store, ctx.clock.now()
    batch = get_batch(store, batch_id)
    said = None if batch is None else kept.after_of(store, batch.id)
    if batch is None or batch.state != "doubt" or not (said or {}).get("can_resend"):
        return templates.LOCK_GONE
    if any(r["kind"] == "resend" and r["batch"] == batch.id for r in kept.requests(store)):
        return templates.LOCK_BUSY
    with store.transaction() as tx:
        kept.add_request(tx, kind="resend", at=now, batch=batch.id, folder=batch.folder)
        log_event(tx, at=now, kind="resend_asked", lane="ACT", studio_id=_studio_of(tx, batch.folder),
                  text=templates.resend_asked(batch.folder))
    return templates.resend_asked(batch.folder)


def ask_undo(ctx: Any, fix_id: int) -> str:
    """Undo of a fix written: it waits on the interlock like the fix did."""
    store, now = ctx.store, ctx.clock.now()
    fix = get_fix(store, fix_id)
    if fix is None or fix.state != "applied":
        return templates.LOCK_GONE
    if any(p["kind"] == "undo" and p.get("fix") == fix.id for p in kept.pending(store)):
        return templates.LOCK_BUSY
    with store.transaction() as tx:
        kept.add_pending(tx, kind="undo", at=now, folder=fix.folder, fix=fix.id)
        log_event(tx, at=now, kind="undo_asked", lane="ACT", studio_id=_studio_of(tx, fix.folder),
                  text=templates.undo_asked(fix.folder))
    return templates.undo_asked(fix.folder)


# ----- what the worker is handed and hands back


@dataclass(frozen=True)
class ClickRead:
    """A click's files, read on the worker. problem: a reason nothing can be judged."""

    request: Mapping[str, Any]
    rooms: Mapping[str, str] = field(default_factory=dict)
    identities: Mapping[str, str] = field(default_factory=dict)
    conversion_sha256: str | None = None
    conversion: bytes | None = None
    snap: TrackerSnapshot | None = None
    sheets: Mapping[str, int] = field(default_factory=dict)
    problem: str | None = None


# ----- P4 at work: one per supervisor


class LockWork:
    """runner: where files are read and Jim's scripts run, one at a time (a FetchRunner). running: whether
    Mozaik runs at this PC (the observer's). Only each pending fix's interlock is kept in memory, so after
    a restart a fix waits its two minutes again."""

    def __init__(self, runner: Any, *, running: Callable[[], bool | None] = observed_running,
                 holder: str | None = None) -> None:
        self.runner = runner
        self._running = running
        self._holder = holder or pc_name()
        self._interlocks: dict[int, P4Interlock] = {}
        self._key = 0
        self._turn = 0

    # -- the tick, on the scheduler's thread

    def tick(self, ctx: Any) -> None:
        self.take_finished(ctx)
        now = ctx.clock.now()
        for _key in self.runner.overran():
            open_dev(ctx.store, at=now, code=LOCK_STUCK,
                     text="A read for a lock or a fix ran for over 15 minutes and was given up.")
        for step in (self._act_on_cards, self._keep_leases, self._pending, self._batch_limits):
            self._guarded(ctx, step, now)
        if getattr(self.runner, "stopped", False) or self.runner.busy() or self.runner.free() <= 0:
            return
        job = self._next(ctx, now)
        if job is not None:
            self._key += 1
            self.runner.start(self._key, job)
            self.take_finished(ctx)

    def take_finished(self, ctx: Any) -> bool:
        done = self.runner.finished()
        for kind, found in done:
            self._guarded(ctx, lambda c, n, kind=kind, found=found: getattr(self, f"_took_{kind}")(c, found),
                          ctx.clock.now())
        return bool(done)

    def _guarded(self, ctx: Any, step: Callable[[Any, datetime], None], now: datetime) -> None:
        try:
            step(ctx, now)
        except StoreError:
            raise
        except Exception as exc:  # noqa: BLE001 - one P4 step's failure must not stop the rest
            _log.exception("a P4 step failed")
            open_dev(ctx.store, at=now, code=LOCK_FAILED, text=f"A step of the lock work failed ({type(exc).__name__}).")

    # -- what the worker does next: Jim's clicks first, then each other kind in turn

    def _next(self, ctx: Any, now: datetime) -> Callable[[], tuple[str, Any]] | None:
        store = ctx.store
        for request in kept.requests(store):
            work = self._request_work(ctx, request)
            if work is not None:
                return work
            kept.drop_request(store, request["id"])
        others = (self._stage_work, self._sheet_work, self._after_work, self._survival_work, self._plan_work)
        for n in range(len(others)):
            work = others[(self._turn + n) % len(others)](ctx, now)
            if work is not None:
                self._turn = (self._turn + n + 1) % len(others)
                return work
        return None

    def _request_work(self, ctx: Any, request: Mapping[str, Any]) -> Callable[[], tuple[str, Any]] | None:
        store, settings = ctx.store, ctx.settings
        snapshots = settings.tracker_folder
        if request["kind"] == "resend":
            return lambda: ("resend", (dict(request), *_read_snapshot(snapshots)))
        job = get_job(store, request["studio_id"])
        if job is None:
            return None
        nests = {n.material: n.opt_sha256 for n in job_nests(store, job)}
        plan = dict(request=dict(request), mozaik_jobs=Path(settings.mozaik_shared) / "Jobs",
                    ng_jobs=Path(settings.ng_jobs), folders=folders_of(job), snapshots=snapshots, nests=nests)
        return lambda: ("click", _read_click(**plan))

    def _stage_work(self, ctx: Any, now: datetime) -> Callable[[], tuple[str, Any]] | None:
        for item in kept.pending(ctx.store):
            if item["kind"] == "apply" and (not item.get("staged") or item.get("replan")):
                plan = kept.plan_from(item["plan"])
                if not item.get("replan") and _unjudged(ctx.store, plan.folder, plan.room):
                    continue                     # its copy holds a fix Jim's next save hasn't judged yet
                real = _job_dir(Path(ctx.settings.mozaik_shared) / "Jobs", (plan.folder,))
                copy = shadow(ctx).mozaik_shared / "Jobs" / plan.folder
                item_id = item["id"]
                if item.get("replan"):
                    snapshot = adopted_snapshot(ctx.store, ctx.paths.toolkit)
                    work = ctx.paths.staging / "fixes"
                    return lambda: ("plan", _plan(snapshot, real, plan.folder, plan.room, work,
                                                  {"pending": item_id}))
                return lambda: ("stage", (item_id, _stage_room(real, copy, plan.room)))
        return None

    def _sheet_work(self, ctx: Any, now: datetime) -> Callable[[], tuple[str, Any]] | None:
        asked = sorted(kept.names_in(ctx.store, _SHEET_ASKED))
        if not asked:
            return None
        folder = asked[0]
        snapshot = adopted_snapshot(ctx.store, ctx.paths.toolkit)
        job = _job_of(ctx.store, folder)
        install = None if job is None else job.install
        settings, work = ctx.settings, ctx.paths.staging / "jobsheet"
        return lambda: ("sheet", (folder, *_make_sheet(snapshot, Path(settings.mozaik_shared) / "Jobs",
                                                       Path(settings.ng_jobs), folder, install, work)))

    def _after_work(self, ctx: Any, now: datetime) -> Callable[[], tuple[str, Any]] | None:
        open_ = list_batches(ctx.store, open_only=True)
        snapshots = ctx.settings.tracker_folder
        if not open_ or snapshots is None:
            return None
        roots = shadow(ctx)
        return lambda: ("after", [(b, read_after(b, roots=roots, snapshots=snapshots)) for b in open_])

    def _survival_work(self, ctx: Any, now: datetime) -> Callable[[], tuple[str, Any]] | None:
        store = ctx.store
        snapshot = adopted_snapshot(store, ctx.paths.toolkit)
        if snapshot is None:
            return None
        waiting = {p.get("fix") for p in kept.pending(store)}
        for fix in list_fixes(store):
            if fix.state != "applied" or fix.survival is not None or fix.id in waiting:
                continue
            room = shadow(ctx).mozaik_shared / "Jobs" / fix.folder / fix.room
            try:
                data = room.read_bytes()            # NG Runner's own copy, on this PC's disk
            except OSError:
                continue
            if hashlib.sha256(data).hexdigest() == fix.result_sha256:
                continue
            sessions, work, at = mozaik_sessions(store), ctx.paths.staging / "fixes", ctx.clock.now()
            return lambda fix=fix, data=data: ("survival", (fix.id, survival(
                fix, NOTCH_FACE, snapshot, room_bytes=data, room_name=fix.room, work=work, sessions=sessions,
                store=store, at=at, keep=False)))
        return None

    def _plan_work(self, ctx: Any, now: datetime) -> Callable[[], tuple[str, Any]] | None:
        store = ctx.store
        snapshot = adopted_snapshot(store, ctx.paths.toolkit)
        if snapshot is None:
            return None
        planned = kept.names_in(store, kept.PLANNED)
        for studio_id, check in sorted(job_checks(store, now).items()):
            if not check.counts or check.run is None:
                continue
            for room in _notch_rooms(check):
                key = f"{check.run.id}|{room}"
                if key in planned:
                    continue
                job = get_job(store, studio_id)
                if job is None:
                    continue
                real = _job_dir(Path(ctx.settings.mozaik_shared) / "Jobs", folders_of(job))
                work = ctx.paths.staging / "fixes"
                kept.add_name(store, kept.PLANNED, key)
                return lambda real=real, room=room, job=job: ("plan", _plan(
                    snapshot, real, folder_of(job), room, work, {"studio_id": job.studio_id}))
        return None

    # -- what came back, on the scheduler's thread

    def _took_click(self, ctx: Any, read: ClickRead) -> None:
        store, now = ctx.store, ctx.clock.now()
        request = read.request
        kept.drop_request(store, request["id"])
        job = get_job(store, request["studio_id"])
        if job is None:
            return
        label = templates.lock_label(request["click"], request.get("material"))
        result = self._click(ctx, job, read)
        ok, words = (result.ok, result.words) if isinstance(result, ClickResult) else (False, result)
        folder = request["folder"]
        said = templates.lock_result(folder, label=label, ok=ok, words=words, at=now,
                                     without_check=bool(request.get("without_check")))
        log_event(store, at=now, kind="lock_dropped" if ok else "lock_refused", lane="ACT",
                  studio_id=job.studio_id, text=f"{folder}: {label}: {said.lines[0]}")
        _tell(ctx, said)
        if ok and isinstance(result, ClickResult) and _locks_the_job(result):
            kept.add_name(store, _SHEET_ASKED, folder)

    def _click(self, ctx: Any, job: JobRecord, read: ClickRead) -> ClickResult | str:
        store, now = ctx.store, ctx.clock.now()
        request = read.request
        if read.problem:
            return read.problem
        if read.snap is None:
            return "The tracker snapshot could not be read"
        made = lock_names(job_takeoffs_of(store).get(job.studio_id), read.conversion, read.snap)
        if isinstance(made, str):
            return made                              # no whole takeoff, or a name not in Pricing: which one
        takeoff, names = made.takeoff, made.names
        check = job_checks(store, now).get(job.studio_id)
        nests = job_nests(store, job)
        common = dict(takeoff=takeoff, names=names, snap=read.snap, nests=nests, check=check,
                      room_sha256=dict(read.rooms), room_identity=dict(read.identities),
                      conversion_sha256=read.conversion_sha256, without_check=bool(request.get("without_check")),
                      store=store, roots=shadow(ctx), interlock=interlock_for(ctx, running=self._running),
                      clock=ctx.clock, source=made.source)
        folder = request["folder"]
        if request["click"] == "lock_design":
            return lock_design(folder, **common)
        if request["click"] == "finish_lock":
            material = request["material"]
            tracker_name = tracker_name_of_nest(material, takeoff, names)
            if tracker_name is None:
                return NO_NAMES
            if material not in read.sheets:
                return f"The nest of {templates.material_words(material)} could not be read again, so its sheets aren't known"
            return finish_lock(folder, material, tracker_name=tracker_name, sheets=read.sheets[material], **common)
        sheets: dict[str, float] = {}
        for nest in nests:
            tracker_name = tracker_name_of_nest(nest.material, takeoff, names)
            if tracker_name is None:
                return NO_NAMES
            if nest.material not in read.sheets:
                return (f"The nest of {templates.material_words(nest.material)} could not be read again, so its"
                        " sheets aren't known")
            sheets[tracker_name] = sheets.get(tracker_name, 0) + read.sheets[nest.material]
        return lock(folder, sheets=sheets, **common)

    def _took_resend(self, ctx: Any, found: tuple[Mapping[str, Any], TrackerSnapshot | None, str | None]) -> None:
        store, now = ctx.store, ctx.clock.now()
        request, snap, problem = found
        batch = get_batch(store, request["batch"])
        if batch is None or batch.state != "doubt":
            kept.drop_request(store, request["id"])
            return
        if snap is None:
            return                                   # the next minute reads it again
        result = resend_missing(batch, fresh=snap, pressed_at=kept.since(request, "at"), store=store,
                                roots=shadow(ctx), interlock=interlock_for(ctx, running=self._running),
                                clock=ctx.clock)
        if not result.ok and "waiting for it" in (result.reason or ""):
            return                                   # a snapshot after the press hasn't come yet
        kept.drop_request(store, request["id"])
        label = templates.BATCH_LABELS["resend"]
        words = (f"Re-sent only the missing rows as batch {result.batch.id}" if result.ok
                 else result.reason or "Nothing was sent again")
        log_event(store, at=now, kind="resent" if result.ok else "resend_refused", lane="ACT",
                  studio_id=_studio_of(store, batch.folder), text=f"{batch.folder}: {label}: {words}.")
        _tell(ctx, templates.lock_result(batch.folder, label=label, ok=result.ok, words=words, at=now,
                                         without_check=False))

    def _took_after(self, ctx: Any, found: Sequence[tuple[BatchRecord, AfterRead]]) -> None:
        store, now = ctx.store, ctx.clock.now()
        for batch, seen in found:
            batch = get_batch(store, batch.id) or batch
            if batch.state not in _OPEN:
                continue                              # closed meanwhile: after 7 days, say
            before = kept.after_of(store, batch.id) or {}
            taken = None if seen.snap is None else to_iso(seen.snap.taken)
            fresh = (taken != before.get("taken") or (seen.archived is not None) != before.get("archived", False)
                     or seen.set_aside is not None)
            if not fresh and before:
                continue                              # nothing new since it was last judged
            if batch.shadow and seen.archived is None and seen.set_aside is None:
                self._compare(ctx, batch, seen, taken, before, now)
                continue
            judged = judge_after(batch, seen, store=store, now=now)
            said = {"state": judged.state, "words": judged.words, "missing": list(judged.missing),
                    "differs": list(judged.differs), "can_resend": judged.can_resend, "taken": taken,
                    "archived": seen.archived is not None, "told": before.get("told")}
            if judged.state in ("confirmed", "doubt") and before.get("told") != judged.state:
                label = templates.BATCH_LABELS[batch.kind]
                log_event(store, at=now, kind=f"batch_{judged.state}", lane="ACT",
                          studio_id=_studio_of(store, batch.folder),
                          text=f"{batch.folder}: {label}: {templates._ended(judged.words)}")
                _tell(ctx, templates.batch_result(batch.folder, label=label, state=judged.state,
                                                  words=judged.words, at=now))
                said["told"] = judged.state
            kept.keep_after(store, batch.id, said)

    def _compare(self, ctx: Any, batch: BatchRecord, seen: AfterRead, taken: str | None,
                 before: Mapping[str, Any], now: datetime) -> None:
        """A shadow batch against the chat's own rows on the real tracker: closed when they are there."""
        compared = None if seen.snap is None else shadow_batches.compare(batch, seen.snap)
        if compared is None:
            unreadable = templates._clause(seen.unreadable or "The tracker snapshot could not be read")
            words = shadow_batches.WAITING if seen.snap is not None else (
                f"{unreadable}, so the batch is not compared yet")
            kept.keep_after(ctx.store, batch.id, {"state": batch.state, "words": words, "missing": [], "differs": [],
                                                  "can_resend": False, "taken": taken, "archived": False,
                                                  "told": before.get("told")})
            return
        self._close_shadow(ctx, batch, compared.state, compared.words, now, lines=compared.lines, taken=taken)

    def _close_shadow(self, ctx: Any, batch: BatchRecord, state: str, words: str, now: datetime, *,
                      lines: Sequence[str] = (), taken: str | None = None) -> None:
        """A shadow batch closed: matched, differing or not compared. A line in the log, and Sulaiman told."""
        store = ctx.store
        update_batch(store, batch.id, state=state, words=words, closed_at=now)
        label = templates.BATCH_LABELS[batch.kind]
        log_event(store, at=now, kind=f"batch_{state}", lane="LOG", studio_id=_studio_of(store, batch.folder),
                  text=f"{batch.folder}: {label}: {templates._ended(words)}")
        _tell(ctx, templates.shadow_compared(batch.folder, label=label, state=state, words=words, at=now))
        kept.keep_after(store, batch.id, {"state": state, "words": words, "lines": list(lines), "missing": [],
                                          "differs": [], "can_resend": False, "taken": taken, "archived": False,
                                          "told": state})

    def _took_plan(self, ctx: Any, found: Mapping[str, Any]) -> None:
        store, now = ctx.store, ctx.clock.now()
        pending_id = found.get("pending")
        if pending_id is not None:
            self._replanned(ctx, pending_id, found)
            return
        studio_id, folder, room = found["studio_id"], found["folder"], found["room"]
        if found.get("refused"):
            text = f"{folder}: a fix of {room} could not be planned: {found['refused']}"
            if not any(i.code == FIX_NOT_PLANNED and i.text == text for i in list_dev(store)):
                open_dev(store, at=now, code=FIX_NOT_PLANNED, studio_id=studio_id, text=text)
            return
        plan: FixPlan | None = found.get("plan")
        if plan is None or _asked_before(store, plan.fingerprint):
            return
        self._card(ctx, studio_id, plan, now)

    def _card(self, ctx: Any, studio_id: str, plan: FixPlan, now: datetime, *, redo_of: int | None = None) -> None:
        from ngrunner.intake.pipeline import add_guard_card

        store = ctx.store
        spec = templates.fix_card(plan.folder, words=plan.words, renest=plan.renest)
        with store.transaction() as tx:
            card = add_guard_card(tx, spec, now, studio_id=studio_id)
            kept.keep_plan(tx, card.id, plan, studio_id=studio_id, redo_of=redo_of)

    def _replanned(self, ctx: Any, pending_id: int, found: Mapping[str, Any]) -> None:
        """The room was saved since the plan Jim approved: the same finding applies, one gone closes, and
        another asks again (design 5, "Approvals and survival")."""
        store, now = ctx.store, ctx.clock.now()
        item = next((p for p in kept.pending(store) if p["id"] == pending_id), None)
        if item is None:
            return
        old = kept.plan_from(item["plan"])
        if found.get("refused"):
            self._give_up(ctx, item, f"The fix could not be planned again: {found['refused']}", now)
            return
        fresh: FixPlan | None = found.get("plan")
        outcome = replan(old, fresh)
        if outcome == "same" and fresh is not None:
            kept.update_pending(store, pending_id, plan=kept.plan_json(fresh), staged=False, replan=False)
            return
        kept.drop_pending(store, pending_id)
        self._interlocks.pop(pending_id, None)
        studio_id = _studio_of(store, old.folder)
        if outcome == "gone":
            log_event(store, at=now, kind="fix_gone", lane="ACT", studio_id=studio_id,
                      text=f"{old.folder}: {old.room} no longer needs the fix: closed as already fixed.")
        elif fresh is not None and studio_id is not None:
            log_event(store, at=now, kind="fix_changed", lane="ACT", studio_id=studio_id,
                      text=f"{old.folder}: {old.room} changed since the fix was approved, so it is asked again.")
            self._card(ctx, studio_id, fresh, now)

    def _took_stage(self, ctx: Any, found: tuple[int, str | None]) -> None:
        pending_id, problem = found
        item = next((p for p in kept.pending(ctx.store) if p["id"] == pending_id), None)
        if item is None:
            return
        if problem:
            self._give_up(ctx, item, problem, ctx.clock.now())
            return
        kept.update_pending(ctx.store, pending_id, staged=True)

    def _took_survival(self, ctx: Any, found: tuple[int, Survival]) -> None:
        store, now = ctx.store, ctx.clock.now()
        fix_id, result = found
        fix = get_fix(store, fix_id)
        if fix is None or result.code == "waiting" or fix.survival is not None:
            return
        record_survival(store, fix.id, code=result.code, words=result.words, at=now)
        studio_id = _studio_of(store, fix.folder)
        if result.code == "you_changed":
            log_event(store, at=now, kind="you_changed", lane="LOG", studio_id=studio_id,
                      text=templates.survival_line(fix.folder, words=result.words))
            return
        log_event(store, at=now, kind=f"fix_{result.code}", lane="ACT", studio_id=studio_id,
                  text=f"{fix.folder}: {templates._ended(result.words)}")
        _tell(ctx, templates.survival_message(fix.folder, code=result.code, words=result.words, at=now))
        if result.code == "mozaik_undid" and result.redo and str(fix.id) not in kept.redo_of(store) \
                and studio_id is not None:
            spec = templates.redo_card(fix.folder, words=result.words)
            from ngrunner.intake.pipeline import add_guard_card

            plan = next((kept.plan_from(said) for said in kept.all_plans(store).values()
                         if said["fingerprint"] == fix.fingerprint), None)
            if plan is None:
                return
            with store.transaction() as tx:
                card = add_guard_card(tx, spec, now, studio_id=studio_id)
                kept.keep_plan(tx, card.id, plan, studio_id=studio_id, redo_of=fix.id)

    def _took_sheet(self, ctx: Any, found: tuple[str, JobSheetRun, bool]) -> None:
        store, now = ctx.store, ctx.clock.now()
        folder, made, there = found
        written = False
        if made.code == MATCHES and not there:
            result = write_job_sheet(folder, made, store=store, roots=shadow(ctx),
                                     interlock=interlock_for(ctx, running=self._running), clock=ctx.clock)
            written = result.ok
        kept.keep_sheet(store, folder, {"code": made.code, "items": [i.words for i in made.items],
                                        "words": templates.job_sheet_words(made.code, written=written,
                                                                           why=made.words),
                                        "at": to_iso(now), "written": written})
        set_state(store, _SHEET_ASKED, json.dumps(sorted(kept.names_in(store, _SHEET_ASKED) - {folder})))

    # -- Jim's answers to P4's cards, leases, the waiting fixes and the 30-minute limit

    def _act_on_cards(self, ctx: Any, now: datetime) -> None:
        store = ctx.store
        acted = kept.names_in(store, kept.ACTED)
        for card in list_cards(store, state="answered"):
            if card.kind not in ("fix", "fix_undone") or card.id in acted:
                continue
            said = kept.plan_of(store, card.id)
            with store.transaction() as tx:
                kept.add_name(tx, kept.ACTED, card.id)
                if said is None:
                    continue
                folder = said["folder"]
                if card.answer == card.choices[0]:
                    kept.add_pending(tx, kind="apply", at=now, folder=folder, card=card.id,
                                     plan={k: v for k, v in said.items() if k not in ("studio_id", "redo_of", "card")},
                                     redo_of=said.get("redo_of"))
                    log_event(tx, at=now, kind="fix_approved", lane="ACT", studio_id=said.get("studio_id"),
                              text=f"{folder}: card {card.id} approved; the fix waits until the room is clear"
                                   " to write.")
                else:
                    log_event(tx, at=now, kind="fix_left", lane="ACT", studio_id=said.get("studio_id"),
                              text=f"{folder}: card {card.id}: left as it is, as Jim chose.")

    def _keep_leases(self, ctx: Any, now: datetime) -> None:
        """Renewed while a fix or an Undo waits on the job; released once none does."""
        at_work = kept.folders_at_work(ctx.store)
        for record in list_leases(ctx.store, state=ACTIVE):
            args = dict(store=ctx.store, roots=shadow(ctx), clock=ctx.clock, holder=self._holder)
            if record.folder in at_work:
                renew_lease(record.folder, interlock=interlock_for(ctx, running=self._running), **args)
            else:
                release_lease(record.folder, interlock=interlock_for(ctx, releasing=True), **args)

    def _pending(self, ctx: Any, now: datetime) -> None:
        for item in kept.pending(ctx.store):
            if item["kind"] == "apply" and (not item.get("staged") or item.get("replan")):
                continue                             # the worker stages it first
            self._try(ctx, item, now)

    def _try(self, ctx: Any, item: Mapping[str, Any], now: datetime) -> None:
        store = ctx.store
        if now - kept.since(item) > FIX_WAITS:
            self._give_up(ctx, item, "It waited 8 hours for the room to be clear to write", now)
            return
        folder = item["folder"]
        check = self._interlocks.setdefault(item["id"], interlock_for(ctx, running=self._running))
        roots = shadow(ctx)
        if not lease_held(store, folder, now):
            took = take_lease(folder, store=store, roots=roots, interlock=check, clock=ctx.clock,
                              holder=self._holder)
            if not took.ok:
                return                               # paused, say: the next minute tries again
        programs = {}
        for program in list_programs(store, folder=folder):
            programs.setdefault(program.material, []).append(program.sha256 or "")
        if item["kind"] == "apply":
            plan = kept.plan_from(item["plan"])
            room = roots.mozaik_shared / "Jobs" / plan.folder / plan.room
            if check([room]) is not None:
                return                               # waiting: Mozaik, a lock, two polls, quiet, a lease
            result = apply_fix(plan, approved=plan.fingerprint, programs=programs, store=store, roots=roots,
                               interlock=check, clock=ctx.clock)
            if result.ok and result.fix is not None:
                if item.get("redo_of") is not None:
                    kept.note_redo(store, result.fix.id, int(item["redo_of"]))
                self._done(ctx, item, "written", "; ".join(plan.words), now)
            elif "saved since the fix was planned" in (result.reason or ""):
                kept.update_pending(store, item["id"], replan=True)
            elif result.reason:
                self._give_up(ctx, item, result.reason, now)
            return
        fix = get_fix(store, int(item["fix"]))
        if fix is None:
            kept.drop_pending(store, item["id"])
            return
        room = roots.mozaik_shared / "Jobs" / fix.folder / fix.room
        if check([room]) is not None:
            return
        result = undo_fix(fix, NOTCH_FACE, programs=programs, store=store, roots=roots, interlock=check,
                          clock=ctx.clock)
        if result.ok:
            self._done(ctx, item, "undone", f"The fix on {fix.room} is undone: the room is as it was", now)
        elif any((result.reason or "").startswith(words) for words in _FINAL):
            self._done(ctx, item, "refused", result.reason or "Undo is refused", now)
        elif result.reason:
            self._give_up(ctx, item, result.reason, now)

    def _done(self, ctx: Any, item: Mapping[str, Any], done: str, words: str, now: datetime) -> None:
        store = ctx.store
        kept.drop_pending(store, item["id"])
        self._interlocks.pop(item["id"], None)
        folder = item["folder"]
        log_event(store, at=now, kind=f"fix_{done}", lane="ACT", studio_id=_studio_of(store, folder),
                  text=f"{folder}: {templates._ended(words)}")
        _tell(ctx, templates.fix_result(folder, done=done, words=words, at=now))
        self._keep_leases(ctx, now)

    def _give_up(self, ctx: Any, item: Mapping[str, Any], why: str, now: datetime) -> None:
        store = ctx.store
        kept.drop_pending(store, item["id"])
        self._interlocks.pop(item["id"], None)
        folder = item["folder"]
        what = "fix" if item["kind"] == "apply" else "Undo"
        open_dev(store, at=now, code=FIX_GAVE_UP, studio_id=_studio_of(store, folder),
                 text=f"{folder}: a {what} was given up: {templates._clause(why)}.")
        log_event(store, at=now, kind="fix_given_up", lane="ACT", studio_id=_studio_of(store, folder),
                  text=f"{folder}: the {what} was not done: {templates._clause(why)}.")
        _tell(ctx, templates.fix_result(folder, done="refused", words=f"The {what} was not done: {why}", at=now))
        self._keep_leases(ctx, now)

    def _batch_limits(self, ctx: Any, now: datetime) -> None:
        """A live batch the tracker hasn't taken in 30 minutes is one DEV item; a shadow batch with no chat
        rows to compare with after 7 days closes as not compared, and is never a DEV item."""
        store = ctx.store
        told = kept.names_in(store, kept.WAITING_TOLD)
        for batch in list_batches(store, open_only=True):
            if batch.shadow:
                if shadow_batches.gives_up(batch, now):
                    self._close_shadow(ctx, batch, shadow_batches.NOT_COMPARED, shadow_batches.GAVE_UP, now)
                continue
            if batch.state == "dropped" and now - batch.dropped_at > BATCH_WAITS and batch.id not in told:
                label = templates.BATCH_LABELS[batch.kind]
                open_dev(store, at=now, code=BATCH_SLOW, studio_id=_studio_of(store, batch.folder),
                         text=templates.batch_waiting(batch.folder, label=label, since=batch.dropped_at, now=now))
                kept.add_name(store, kept.WAITING_TOLD, batch.id)


# ----- on the worker: everything that reads a file or runs a script


def _read_click(*, request: Mapping[str, Any], mozaik_jobs: Path, ng_jobs: Path, folders: Sequence[str],
                snapshots: Path | None, nests: Mapping[str, str | None]) -> ClickRead:
    """A click's files: the rooms and the IC Conversion as they are now, the nests' sheets, the snapshot."""
    job = _job_dir(mozaik_jobs, folders)
    if job is None or not job.is_dir():
        return ClickRead(request, problem=NO_JOB_FOLDER)
    try:
        rooms = {name: read_shared(job / name) for name in _rooms_in(job)}
    except (Unreadable, OSError):
        return ClickRead(request, problem="A room could not be read, so the job's design isn't known now")
    conversion_path = _conversion(ng_jobs, folders)
    conversion = None
    if conversion_path is not None:
        try:
            conversion = read_shared(conversion_path)
        except (Unreadable, OSError):
            return ClickRead(request, problem="The IC Conversion could not be read")
    sheets = {}
    for material, sha in nests.items():
        try:
            data = read_shared(job / f"{material}.opt")
        except (Unreadable, OSError):
            continue
        if sha is not None and hashlib.sha256(data).hexdigest() != sha:
            continue                                 # not the nest that was checked: no number from it
        try:
            sheets[material] = read_opt(data).physical_sheets
        except OptUnreadable:
            continue
    snap, problem = _read_snapshot(snapshots)
    return ClickRead(request, rooms={n: hashlib.sha256(d).hexdigest() for n, d in rooms.items()},
                     identities={n: _identity(n, d) for n, d in rooms.items()},
                     conversion_sha256=None if conversion is None else hashlib.sha256(conversion).hexdigest(),
                     conversion=conversion, snap=snap, sheets=sheets, problem=None if snap else problem)


def _read_snapshot(folder: Path | None) -> tuple[TrackerSnapshot | None, str | None]:
    if folder is None:
        return None, "NG Runner has no tracker folder, so the tracker can't be read"
    try:
        return read_tracker(folder), None
    except TrackerUnreadable as exc:
        return None, str(exc)


def _plan(snapshot: Snapshot | None, real: Path | None, folder: str, room: str, work: Path,
          extra: Mapping[str, Any]) -> dict[str, Any]:
    """Jim's fixer on a copy of the real room, read only, and the job's nests read for hand flips."""
    found: dict[str, Any] = {**extra, "folder": folder, "room": room}
    if snapshot is None or real is None:
        return {**found, "refused": "no copy of Jim's toolkit is adopted, or the job's folder isn't there"}
    try:
        nests = {p.stem: read_shared(p) for p in sorted(real.glob("*.opt"))}
        found["plan"] = plan_fix(NOTCH_FACE, snapshot, real / room, folder=folder, work=work, nests=nests)
    except FixRefused as exc:
        found["refused"] = str(exc).rstrip(".")
    except (Unreadable, OSError):
        found["refused"] = "a nest could not be read"
    return found


def _stage_room(real: Path | None, copy: Path, room: str) -> str | None:
    """NG Runner's own copy of a room, and of the marker files beside it, as the real job folder has them
    now, times and all: the room a shadow fix is written into. It writes only in NG Runner's own folder."""
    if real is None:
        return "Its job folder isn't in Mozaik Shared"
    try:
        copy.mkdir(parents=True, exist_ok=True)
        for name in (room, UNS_FILE, LOCK_FILE, OPTIMIZER_LOCK):
            source, target = real / name, copy / name
            if not source.exists():
                if name == room:
                    return f"{room} isn't in the job's folder"
                target.unlink(missing_ok=True)
                continue
            data, stat = read_shared(source), os.stat(source)
            temp = target.with_name(target.name + ".stage")
            temp.write_bytes(data)
            os.replace(temp, target)
            os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    except (Unreadable, OSError):
        return "The room could not be copied for the fix"
    return None


def _make_sheet(snapshot: Snapshot | None, mozaik_jobs: Path, ng_jobs: Path, folder: str,
                install: tuple[str, str] | None, work: Path) -> tuple[JobSheetRun, bool]:
    """Jim's cnc_job_sheet on a staged copy, compared with the nests; and whether NG Jobs has one already."""
    from ngrunner.lock.job_sheet import COULD_NOT_VERIFY

    there = (ng_jobs / folder / f"{folder}{JOB_SHEET_SUFFIX}").exists()
    job, conversion = _job_dir(mozaik_jobs, (folder,)), _conversion(ng_jobs, (folder,))
    if snapshot is None:
        return JobSheetRun(COULD_NOT_VERIFY, (), None, templates.NO_TOOLKIT), there
    if job is None or conversion is None or not install or not all(cell.strip() for cell in install):
        return JobSheetRun(COULD_NOT_VERIFY, (), None, "the job, its IC Conversion or its install date isn't"
                                                       " there"), there
    return make_job_sheet(snapshot, job, conversion, work=work, day=install[0].strip(),
                          date=install[1].strip()), there


# ----- small pieces


def folder_of(job: JobRecord) -> str:
    return job.folder or job.name


def folders_of(job: JobRecord) -> tuple[str, ...]:
    return tuple(dict.fromkeys(name for name in (job.folder, job.name, job.name + GARAGE_SUFFIX) if name))


def job_nests(store: Store, job: JobRecord, nests: Sequence[NestRecord] | None = None) -> list[NestRecord]:
    """A job's materials, by its Studio ID or any name its folder goes by, posted ones only. nests: every
    material, read once for many jobs; else read here."""
    names = {name.casefold() for name in folders_of(job)}
    return [n for n in (list_nests(store) if nests is None else nests) if n.state != "not_posted"
            and (n.studio_id == job.studio_id or n.folder.casefold() in names)]


def job_takeoffs_of(store: Store) -> Mapping[str, Any]:
    from ngrunner.intake.pipeline import job_takeoffs

    return job_takeoffs(store)


def _job_of(store: Store, folder: str) -> JobRecord | None:
    return next((job for job in list_jobs(store) if folder.casefold() in
                 {name.casefold() for name in folders_of(job)}), None)


def _studio_of(store: Store, folder: str) -> str | None:
    job = _job_of(store, folder)
    return None if job is None else job.studio_id


def _nest_identity(nests: Sequence[NestRecord]) -> dict[str, str]:
    """With no check, the design the nests' passes rest on, so the board names no change that isn't one."""
    found: dict[str, str] = {}
    for nest in nests:
        found.update(nest.room_hashes)
    return found


def _notch_rooms(check: JobCheck) -> list[str]:
    """The rooms a check that counts names a notch on the wrong face in, still open (not settled)."""
    rooms = []
    for shown in check.open:
        item = shown.item
        if (item.checker, item.what) != _NOTCH or item.kind != "fix":
            continue
        for tag in item.tags:
            number = tag[1:].split("C")[0] if tag.startswith(_ROOM_TAG) else ""
            if number.isdecimal() and f"Room{int(number)}.des" not in rooms:
                rooms.append(f"Room{int(number)}.des")
    return rooms


def _asked_before(store: Store, fingerprint: str) -> bool:
    """A finding is asked about once: a card for it open or answered, or a fix of it written."""
    plans = kept.all_plans(store)
    for card_id, said in plans.items():
        card = get_card(store, card_id)
        if said["fingerprint"] == fingerprint and card is not None and card.state != "withdrawn":
            return True
    return any(fix.fingerprint == fingerprint for fix in list_fixes(store))


def _unjudged(store: Store, folder: str, room: str) -> bool:
    """A fix written into this room's copy that Jim's next save hasn't judged: staging the room again
    would read as his save."""
    return any(fix.folder == folder and fix.room == room and fix.state == "applied" and fix.survival is None
               for fix in list_fixes(store))


def _locks_the_job(result: ClickResult) -> bool:
    """A batch that sets the job's status to LOCKED: Lock, or the last Finish lock."""
    batch = result.drop.batch if result.drop is not None else None
    return batch is not None and any(row[0].startswith("JOBSUMMARY") and LOCKED in row for row in batch.rows[1:])


def _tell(ctx: Any, message: Any) -> None:
    """Every P4 message goes to Sulaiman alone, marked SHADOW, while P4 is in shadow: never a toast."""
    messenger = getattr(ctx, "messenger", None)
    if messenger is not None:
        messenger.send(templates.shadow(message), to="sulaiman")


def _job_dir(folder: Path, names: Sequence[str]) -> Path | None:
    try:
        with os.scandir(folder) as entries:
            there = {entry.name.casefold(): Path(entry.path) for entry in entries if entry.is_dir()}
    except OSError:
        return None
    return next((there[name.casefold()] for name in names if name.casefold() in there), None)


def _conversion(ng_jobs: Path, names: Sequence[str]) -> Path | None:
    folder = _job_dir(ng_jobs, names) or _job_dir(ng_jobs / "Old", names)
    if folder is None:
        return None
    path = folder / f"{folder.name}_IC_Conversion.json"
    return path if path.is_file() else None


def _rooms_in(job: Path) -> list[str]:
    return sorted((name for name in os.listdir(job) if _ROOM.fullmatch(name)),
                  key=lambda name: int(_ROOM.fullmatch(name)[1]))


def _identity(name: str, data: bytes) -> str:
    try:
        return room_identity(name, data)
    except (DesignUnreadable, ValueError):
        return hashlib.sha256(data).hexdigest()


