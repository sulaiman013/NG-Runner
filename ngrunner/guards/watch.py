r"""
The nest guard at work: watching NG Jobs, judging what is posted, and telling whoever needs to know
(P1b build spec 5.4, and the rulings in docs/decisions.md).

Why it looks like this. It mirrors the pipeline: nothing is held in memory between ticks, so a stop
at any point resumes without a second card or message, and each material's changes share one
transaction with the tell that says so. But G: can stall for minutes and a tick must never wait, so
reading is a pass on a worker (guards/scan.py) with one at a time, and what it found is taken up on
the scheduler's thread as soon as it is back. A guard read holds up no filing, and a guard failure
stops no filing: the supervisor calls this beside pipeline.tick, not inside it.

What it must never do:
- Alarm at install. The first scan only lists, and marks every program seen at install. It waits for
  a pass that listed everything, since a program in a folder it couldn't read would look new later
  and go red. Without this, 65 real old jobs go red at once, on the first start after an upgrade too.
- Read a program before it settles. A file acts once two polls agree on its size and time.
- Conclude anything from what it couldn't see. A pass cut short, or a folder it couldn't list, says
  nothing. A program is gone only after two polls, and its row stays, so one that comes back unchanged
  is what it was and never a new post. A listing that loses many folders at once is not believed
  for 2 hours: a cloud drive starting up shows an empty folder, and that must not clear a DO NOT CUT.
- Say it can be cut. A clean read stays NOT CHECKED, as only Jim's own checks can say more.

Who hears what: George's email says DO NOT CUT at once, at any hour, and says when it has gone. Jim
sees the red tray, a toast, Your list and the digest, and one card per material about a size. In
shadow mode the messenger sends all of it to Sulaiman, marked, and shows no toast.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ngrunner.guards.kept import (
    LOCK_AT, TAKEOFFS, AskedProgram, SizeQuestion, declined_hashes, done_redo,
    forget_question, keep_question, labels_of, labels_seen, place_key, question_of, redo_places,
)
from ngrunner.guards.lockcheck import take_lock_facts
from ngrunner.guards.nest_state import (
    MaterialEvidence, NestState, Verdict, decide, expected_sheet, past_size_programs, sheet_of,
)
from ngrunner.guards.scan import Key, Listed, Place, Plan, Scan, rows_by_place, scan, stat_words
from ngrunner.guards.takeoffs import take_takeoffs
from ngrunner.guards.tap import Tap
from ngrunner.intake.pipeline import (
    add_guard_card, no_ng_jobs, queue_tell, send_tells, stopping,
)
from ngrunner.interlock import GARAGE_SUFFIX
from ngrunner.messages import templates
from ngrunner.store import (
    JobRecord, NestRecord, ProgramRecord, Store, Transaction, add_nest, add_program, clear_state,
    cleared_programs, close_dev, get_card, get_nest, get_state, get_state_time, list_dev, list_jobs,
    list_nests, list_programs, log_event, open_dev, set_state, set_state_time, update_nest,
    update_program, withdraw_card,
)

_log = logging.getLogger("ngrunner.guards")

_PASS = 0                               # the one read at a time
FIRST_SCAN = "guards.first_scan_at"     # set once: from then on a program not seen is a new post
_FIRST_SCAN_BEGAN = "guards.first_scan_began"
_MISSING = "guards.missing"             # JSON: programs one listing failed to find
_GONE = "guards.gone"                   # JSON: programs gone for good, until they come back
_HELD_SINCE = "guards.held_since"       # a listing that lost many folders at once
_ASKED_PREFIX = "guards.asked."         # the open sheet size card of a material

TRIAL_NESTS = 5                         # build spec 4: lines say "(trial)" until this many have run
LOCK_EVERY = timedelta(minutes=15)      # as often as the tracker writes a snapshot
UNFINISHED_AFTER = timedelta(minutes=10)
FIRST_SCAN_LATE = timedelta(minutes=10)
HOLD_FOR = timedelta(hours=2)
_HOLD_AT_LEAST = 5                      # hold a listing that loses more than this many folders,
_HOLD_SHARE = 4                         # or more than 1 in this many, in one pass
GUARD_FAILED = "guard_failed"
GUARD_STUCK = "guard_stuck"
UNFINISHED = "program_unfinished"
HOLD_CODE = "ng_jobs_looks_empty"

_FEATURES: tuple[tuple[str, Callable[[str, str], bool]], ...] = (
    # What a folder's name can say of design 5's "trust per feature". REA, bridges and stacked towers
    # need a .des, so they can't hold a verdict on trial until one can be read.
    ("garage", lambda folder, material: folder.casefold().endswith(GARAGE_SUFFIX.casefold())),
    ("tafisa quarter", lambda folder, material: material.casefold().startswith("1-4 tafisa")),
)


# ----- one tick


def tick(ctx: Any) -> None:
    """Take what the last pass found, and start the next one. It never waits on G:."""
    take_finished(ctx)
    for _key in ctx.guard_reads.overran():
        open_dev(ctx.store, at=ctx.clock.now(), code=GUARD_STUCK,
                 text="A read of NG Jobs for the guards ran for over 15 minutes and was given up.")
    if not _stopped(ctx) and not ctx.guard_reads.busy() and ctx.guard_reads.free() > 0:
        plan = _plan(ctx)
        ctx.guard_reads.start(_PASS, lambda: _pass(plan))
        take_finished(ctx)      # with a runner that works at once


def take_finished(ctx: Any) -> bool:
    """What a pass found, as soon as it is back. True when there was any, so the tray looks."""
    finished = ctx.guard_reads.finished()
    for plan, found in finished:
        _apply(ctx, plan, found)
    if finished:
        send_tells(ctx)
    return bool(finished)


def _stopped(ctx: Any) -> bool:
    return ctx.guard_reads.stopped or stopping(ctx)


def _pass(plan: Plan) -> tuple[Plan, Scan]:
    """On the worker. Whatever goes wrong here comes back as a fact, never as a dead thread."""
    try:
        return plan, scan(plan)
    except Exception as exc:  # noqa: BLE001 - a guard's failure must not take anything else with it
        _log.exception("A pass over NG Jobs failed")
        return plan, Scan(failed=type(exc).__name__)


def _plan(ctx: Any) -> Plan:
    store, now = ctx.store, ctx.clock.now()
    programs = list_programs(store)
    return Plan(
        ng_jobs=Path(ctx.settings.ng_jobs),
        first_scan=get_state(store, FIRST_SCAN) is None,
        known={(p.folder, p.material, p.name): p for p in programs},
        missing_before=_keys(store, _MISSING),
        gone=_keys(store, _GONE),
        redo=redo_places(store),
        takeoffs=json.loads(get_state(store, TAKEOFFS) or "{}"),
        tracker_folder=ctx.settings.tracker_folder,
        lock_folders=_lock_folders(ctx, now),
        labels_seen=labels_seen(store),
        stop=lambda: _stopped(ctx),
    )


def _keys(store: Store, state_key: str) -> frozenset[Key]:
    return frozenset(tuple(key) for key in json.loads(get_state(store, state_key) or "[]"))


# ----- what a pass found


def _apply(ctx: Any, plan: Plan, found: Scan) -> None:
    now = ctx.clock.now()
    if found.failed:
        open_dev(ctx.store, at=now, code=GUARD_FAILED,
                 text=f"A pass over NG Jobs for the guards failed ({found.failed}).")
        return
    if found.stopped:
        return
    if not found.reachable:
        no_ng_jobs(ctx, now)
        return
    _close_dev(ctx.store, GUARD_STUCK)
    if plan.first_scan:
        _first_scan(ctx, found, now)
        _close_dev(ctx.store, GUARD_FAILED)
        return
    for what in found.other_failures:
        open_dev(ctx.store, at=now, code=GUARD_FAILED, text=f"The guards' read of {what} failed.")
    clean = [not found.other_failures, _take_programs(ctx, plan, found, now),
             _on_its_own(ctx, "takeoffs", lambda: take_takeoffs(ctx, plan, found, now)),
             _on_its_own(ctx, "lock pre-flight", lambda: take_lock_facts(ctx, plan, found, now))]
    if all(clean):      # only a clean pass closes it, or it would be raised anew every minute
        _close_dev(ctx.store, GUARD_FAILED)


def _on_its_own(ctx: Any, what: str, work: Callable[[], None]) -> bool:
    """One material's or one part's failure costs only that: a DEV item names what, and never its
    text, which can quote a job's files. True when it went well."""
    try:
        work()
        return True
    except Exception as exc:  # noqa: BLE001 - one problem must not stop the rest of the guards
        _log.exception("The guards' %s failed", what)
        open_dev(ctx.store, at=ctx.clock.now(), code=GUARD_FAILED,
                 text=f"The guards' {what} failed ({type(exc).__name__}).")
        return False


def _close_dev(store: Store, code: str, starts: str = "") -> None:
    for item in list_dev(store):
        if item.code == code and item.text.startswith(starts):
            close_dev(store, item.id)


# ----- the first scan


def _first_scan(ctx: Any, found: Scan, now: datetime) -> None:
    """Everything there now was there at install. Only a pass that listed all of it counts."""
    store = ctx.store
    if not found.is_whole():
        began = get_state_time(store, _FIRST_SCAN_BEGAN)
        if began is None:
            set_state_time(store, _FIRST_SCAN_BEGAN, now)
        elif now - began >= FIRST_SCAN_LATE:
            open_dev(store, at=now, code="first_scan_waiting",
                     text="The guards have not been able to list all of NG Jobs yet, so nothing"
                          " is watched.")
        return
    jobs = _jobs_by_folder(store)
    with store.transaction() as tx:
        for folder, materials in found.folders.items():
            for material, listed in materials.items():
                _mark_seen_at_install(tx, (folder, material), listed, jobs, now)
        set_state(tx, TAKEOFFS, json.dumps(
            {folder: {"seen": stat_words(listed), "read": stat_words(listed)}
             for folder, listed in found.takeoffs_listed.items()}))
        set_state_time(tx, FIRST_SCAN, now)
    _close_dev(store, "first_scan_waiting")


def _mark_seen_at_install(tx: Transaction, place: Place, listed: Mapping[str, Listed],
                          jobs: Mapping[str, JobRecord], now: datetime) -> None:
    if not listed:
        return                                   # an empty folder is no material yet
    folder, material = place
    for name, stat in listed.items():
        add_program(tx, folder=folder, material=material, name=name, size=stat.size,
                    mtime=stat.mtime, seen_at=now, state="seen_at_install")
    verdict = decide(MaterialEvidence(material=material, taps=(), seen_at_install=True))
    add_nest(tx, folder=folder, material=material, state=verdict.state.value, basis=verdict.basis,
             reasons=verdict.reasons, updated_at=now, studio_id=_studio_id(jobs, folder))


def _jobs_by_folder(store: Store) -> dict[str, JobRecord]:
    jobs: dict[str, JobRecord] = {}
    for job in list_jobs(store):
        for name in (job.folder, job.name, job.name + GARAGE_SUFFIX):
            if name:
                jobs[name.casefold()] = job
    return jobs


def _studio_id(jobs: Mapping[str, JobRecord], folder: str) -> str | None:
    job = jobs.get(folder.casefold())
    return None if job is None else job.studio_id


# ----- programs, one material at a time


def _take_programs(ctx: Any, plan: Plan, found: Scan, now: datetime) -> bool:
    store = ctx.store
    skip = found.unlisted | _not_believed(ctx, plan, found, now)
    places = {(key[0], key[1]) for key in plan.known}
    places |= {(folder, material) for folder, materials in found.folders.items()
               for material in materials}
    jobs = _jobs_by_folder(store)
    rows_of = rows_by_place(plan.known)
    clean = True
    for place in sorted(places):
        if place[0] not in skip:
            clean &= _on_its_own(ctx, "read of one material", lambda place=place: _one_material(
                ctx, plan, found, place, rows_of.get(place, {}), jobs, now))
    _note_missing(store, plan, found, skip)
    return clean


def _not_believed(ctx: Any, plan: Plan, found: Scan, now: datetime) -> set[str]:
    """Folders a listing lost, when it lost too many at once to be believed yet."""
    store = ctx.store
    known = {key[0] for key in plan.known} - _gone_folders(plan)
    lost = known - set(found.folders) - found.unlisted
    if len(lost) <= max(_HOLD_AT_LEAST, len(known) // _HOLD_SHARE):
        if get_state(store, _HELD_SINCE) is not None:
            _forget_hold(store)
        return set()
    since = get_state_time(store, _HELD_SINCE)
    if since is None:
        set_state_time(store, _HELD_SINCE, now)
        return lost
    if now - since >= HOLD_FOR:
        return set()            # believed: once they are gone the loss is small, and this forgets
    if now - since >= UNFINISHED_AFTER:
        open_dev(store, at=now, code=HOLD_CODE,
                 text=f"{len(lost)} folders have gone from NG Jobs at once. The guards wait 2 hours"
                      " before believing it.")
    return lost


def _forget_hold(store: Store) -> None:
    clear_state(store, _HELD_SINCE)
    _close_dev(store, HOLD_CODE)


def _gone_folders(plan: Plan) -> set[str]:
    """Folders whose every program has already gone: moved to Old, say."""
    rows: dict[str, list[Key]] = {}
    for key in plan.known:
        rows.setdefault(key[0], []).append(key)
    return {folder for folder, keys in rows.items() if all(key in plan.gone for key in keys)}


def _note_missing(store: Store, plan: Plan, found: Scan, skip: set[str]) -> None:
    """A program not listed once is missing; twice, gone. Listed again, it is neither."""
    missing, gone = set(), set()
    for key in plan.known:
        if key[0] in skip:
            missing |= {key} & plan.missing_before
            gone |= {key} & plan.gone
        elif key[2] in found.listed((key[0], key[1])):
            continue
        elif key in plan.gone or key in plan.missing_before:
            gone.add(key)
        else:
            missing.add(key)
    if missing != plan.missing_before:
        set_state(store, _MISSING, json.dumps(sorted(missing), ensure_ascii=False))
    if gone != plan.gone:
        set_state(store, _GONE, json.dumps(sorted(gone), ensure_ascii=False))


def _one_material(ctx: Any, plan: Plan, found: Scan, place: Place,
                  rows: Mapping[str, ProgramRecord], jobs: Mapping[str, JobRecord],
                  now: datetime) -> None:
    store = ctx.store
    toasts: list[Any] = []
    with store.transaction() as tx:
        _note_listing(tx, place, found.listed(place), rows, now)
        _note_reads(tx, found, place, rows, now)
        if place in found.to_judge:
            _judge(tx, plan, found, place, rows, jobs, now, toasts)
    for message in toasts:     # after the commit: a toast can't be taken back
        ctx.messenger.toast(message)


def _note_listing(tx: Transaction, place: Place, listed: Mapping[str, Listed],
                  rows: Mapping[str, ProgramRecord], now: datetime) -> None:
    """New programs, and new versions of old ones, wait for the next poll to agree."""
    folder, material = place
    for name, stat in listed.items():
        row = rows.get(name)
        if row is None:
            add_program(tx, folder=folder, material=material, name=name, size=stat.size,
                        mtime=stat.mtime, seen_at=now, state="new")
        elif (row.size, row.mtime) != (stat.size, stat.mtime):
            update_program(tx, folder, material, name, state="new", size=stat.size,
                           mtime=stat.mtime, seen_at=now, sha256=None, family=None, reach=None,
                           holes=None, near_pairs=None)


def _note_reads(tx: Transaction, found: Scan, place: Place, rows: Mapping[str, ProgramRecord],
                now: datetime) -> None:
    """What reading each one gave. One still not whole, or still unreadable, goes to DEV after 10
    minutes; its row stays new, so each pass tries again."""
    folder, material = place
    for name in found.listed(place):
        key = (folder, material, name)
        row = rows.get(name)
        if row is None:
            continue
        tap = found.taps.get(key)
        if tap is not None:
            _note_one_read(tx, place, row, tap)
        if (tap is None and key in found.unread) or (tap is not None and not tap.complete):
            if now - row.seen_at >= UNFINISHED_AFTER:
                open_dev(tx, at=now, code=UNFINISHED, text=_unfinished_words(place, name))
        elif tap is not None:
            _close_dev_of(tx, UNFINISHED, _unfinished_words(place, name))


def _unfinished_words(place: Place, name: str) -> str:
    return (f"{place[0]}: program {name} in {place[1]} has not finished arriving, or can't be read,"
            " after 10 minutes.")


def _close_dev_of(db: Any, code: str, text: str) -> None:
    for item in list_dev(db):
        if item.code == code and item.text == text:
            close_dev(db, item.id)


def _note_one_read(tx: Transaction, place: Place, row: ProgramRecord, tap: Tap) -> None:
    folder, material = place
    if _is_from_install(row, tap):
        update_program(tx, folder, material, row.name, sha256=tap.sha256)
    elif tap.complete:
        update_program(tx, folder, material, row.name, state="read", sha256=tap.sha256,
                       family=tap.family, reach=tap.reach, holes=tap.holes,
                       near_pairs=len(tap.near_pairs))


def _is_from_install(row: ProgramRecord, tap: Tap) -> bool:
    """There at the first scan and the same bytes since, as far as anything can say: the listing
    agreed for it to be read at all, and a hash once read must still match."""
    return row.state == "seen_at_install" and row.sha256 in (None, tap.sha256)


# ----- a verdict


def _judge(tx: Transaction, plan: Plan, found: Scan, place: Place,
           rows: Mapping[str, ProgramRecord], jobs: Mapping[str, JobRecord], now: datetime,
           toasts: list[Any]) -> None:
    folder, material = place
    listed = found.listed(place)
    taps = tuple(found.taps[(folder, material, name)] for name in sorted(listed)
                 if (folder, material, name) in found.taps)
    if listed and not taps:
        return                  # nothing read yet, and something is there: never read as nothing
    ev = _evidence(tx, place, listed, taps, rows, now)
    verdict = decide(ev)
    verdict = replace(verdict, trial=_on_trial(tx, folder, material))
    posted = _posted_at(taps, ev, listed, now)
    sid = _studio_id(jobs, folder)
    previous = get_nest(tx, folder, material)
    told = _tell(tx, plan, place, previous, verdict, sid, now, toasts)
    _keep_verdict(tx, place, previous, verdict, taps, posted, sid, told, now)
    _ask_on_sizes(tx, place, past_size_programs(ev), sid, now)
    if place in plan.redo:
        done_redo(tx, folder, material)


def _evidence(tx: Transaction, place: Place, listed: Mapping[str, Listed], taps: tuple[Tap, ...],
              rows: Mapping[str, ProgramRecord], now: datetime) -> MaterialEvidence:
    folder, material = place
    before = frozenset((tap.name, tap.sha256) for tap in taps
                       if _is_from_install(rows[tap.name], tap))
    all_old = bool(listed) and len(before) == len(taps) and all(
        name in rows and rows[name].state == "seen_at_install"
        and (rows[name].size, rows[name].mtime) == (stat.size, stat.mtime)
        for name, stat in listed.items())
    return MaterialEvidence(material=material, taps=taps, seen_at_install=all_old,
                            cleared=cleared_programs(tx, folder, material), before_install=before,
                            read_at=now, labels=labels_of(tx, folder))


def _on_trial(db: Any, folder: str, material: str) -> bool:
    """Until 5 real nests have had a verdict, then for a feature no earlier nest has exercised
    (build spec 4). Only a real post counts: not what was there at install."""
    others = [nest for nest in list_nests(db) if nest.posted_at is not None
              and (nest.folder, nest.material) != (folder, material)]
    if len(others) < TRIAL_NESTS:
        return True
    feature = _feature_of(folder, material)
    return feature is not None and not any(
        _feature_of(nest.folder, nest.material) == feature for nest in others)


def _feature_of(folder: str, material: str) -> str | None:
    return next((name for name, says in _FEATURES if says(folder, material)), None)


def _posted_at(taps: Iterable[Tap], ev: MaterialEvidence, listed: Mapping[str, Listed],
               now: datetime) -> datetime | None:
    """When the newest real post landed, by the file's own time: it says when it was posted even if
    NG Runner was off. None for a material nothing was posted to since install, which is what tells
    a NOT CHECKED worth saying from an old one."""
    new = [listed[tap.name].mtime for tap in taps
           if tap.complete and (tap.name, tap.sha256) not in ev.before_install]
    return min(max(new), now) if new else None


def _tell(tx: Transaction, plan: Plan, place: Place, previous: NestRecord | None, verdict: Verdict,
          sid: str | None, now: datetime, toasts: list[Any]) -> datetime | None:
    """George hears of a new fault at once, and when it has gone. What it returns is when he was
    told a DO NOT CUT that still stands. An answer's own message says what changed, so the verdict
    after a click tells nobody."""
    folder, material = place
    told = None if previous is None else previous.george_told_at
    if verdict.state is NestState.DO_NOT_CUT:
        standing = set(previous.reasons) if previous and previous.state == "do_not_cut" else set()
        if set(verdict.reasons) - standing:
            message = templates.do_not_cut(folder, material, reasons=verdict.reasons,
                                           trial=verdict.trial)
            queue_tell(tx, message, to="george", now=now, ref=f"do_not_cut:{_ref_of(place, now)}")
            log_event(tx, at=now, kind="do_not_cut", studio_id=sid,
                      text=f"DO NOT CUT {templates.material_words(material)}:"
                           f" {'; '.join(verdict.reasons)}.")
            toasts.append(message)
            return now
        return told
    if told is None or verdict.state is NestState.NOT_POSTED:
        return told
    if place not in plan.redo:
        queue_tell(tx, templates.fault_gone(folder, material, at=now), to="george", now=now,
                   ref=f"fault_gone:{_ref_of(place, now)}")
        log_event(tx, at=now, kind="fault_gone", studio_id=sid,
                  text=f"No longer DO NOT CUT {templates.material_words(material)}.")
    return None


def _ref_of(place: Place, now: datetime) -> str:
    """A message's own name, with no folder's name in it: a ref goes into logs."""
    return hashlib.sha256(f"{place[0]}|{place[1]}|{now.isoformat()}".encode()).hexdigest()[:24]


def _keep_verdict(tx: Transaction, place: Place, previous: NestRecord | None, verdict: Verdict,
                  taps: Iterable[Tap], posted: datetime | None, sid: str | None,
                  told: datetime | None, now: datetime) -> None:
    folder, material = place
    fields = {"state": verdict.state.value, "basis": verdict.basis, "reasons": verdict.reasons,
              "studio_id": sid, "manifest": {tap.name: tap.sha256 for tap in taps},
              "posted_at": posted, "trial": verdict.trial, "george_told_at": told}
    if previous is None:
        if verdict.state is not NestState.NOT_POSTED:
            add_nest(tx, folder=folder, material=material, updated_at=now, **fields)
    else:
        update_nest(tx, folder, material, now=now, **fields)


# ----- the one sheet size card of a material


def _ask_on_sizes(tx: Transaction, place: Place, past: Iterable[Tap], sid: str | None,
                  now: datetime) -> None:
    """One card per material, naming every program past its size that isn't cleared. A new or
    changed one means a new card, since an answer can only stand for what its card said; none left
    means no card. A program Jim answered "Re-nest it" about isn't asked about again."""
    folder, material = place
    key = place_key(_ASKED_PREFIX, folder, material)
    declined = declined_hashes(tx, folder, material)
    programs = tuple(AskedProgram(tap.name, tap.sha256, sheet_of(tap)) for tap in past
                     if tap.sha256 not in declined)
    asked = json.loads(get_state(tx, key) or "null")
    card = None if asked is None else get_card(tx, asked)
    if card is not None and card.state == "answered" and question_of(tx, card.id) is not None:
        return                   # answered, and not acted on yet: the next tick does, then a redo
    if card is not None and card.state == "open":
        question = question_of(tx, card.id)
        if question is not None and question.programs == programs:
            return
        withdraw_card(tx, card.id)
        forget_question(tx, card.id)
        set_state(tx, key, "null")
        log_event(tx, at=now, kind="sheet_size_moved", studio_id=sid,
                  text=f"What is past its size changed while card {card.id} was open.")
    if programs:
        expected = expected_sheet(material) or "not known"
        spec = templates.sheet_size_card(folder, material=material, expected=expected,
                                         programs=[(p.name, p.on) for p in programs])
        made = add_guard_card(tx, spec, now, studio_id=sid)
        keep_question(tx, made.id, SizeQuestion(folder, material, expected, programs))
        set_state(tx, key, json.dumps(made.id))


# ----- which folders the pre-flight reads


def _lock_folders(ctx: Any, now: datetime) -> tuple[str, ...]:
    """Folders worth a pre-flight: a job's, or one with a real post. Every 15 minutes, as the
    snapshot it reads is written no oftener."""
    store = ctx.store
    if ctx.settings.tracker_folder is None:
        return ()
    last = get_state_time(store, LOCK_AT)
    if last is not None and timedelta(0) <= now - last < LOCK_EVERY:
        return ()
    folders = {nest.folder for nest in list_nests(store) if nest.posted_at is not None}
    folders |= {job.folder or job.name for job in list_jobs(store)}
    return tuple(sorted(folders))
