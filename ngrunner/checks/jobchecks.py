r"""
The checks at work: when Jim says a job's sections are done, or clicks Check now, his crosscheck runs on
a staged copy of the job, off the tick's thread, and what it found becomes items on the board, each to fix
with a ready chat line, or could not verify with why (P3; design 2, 3.4, 3.5 and 4).

How Jim says "sections done" (design 4, "The close prompt"). When he closes a job in Mozaik, a toast asks:
the first time "[Opened fine, sections done: check it] [Opened fine, not done yet] [It showed an error]",
later "Sections done? [Yes, check it] [Not yet]", at most once per job every 3 hours. Ignoring it means
"not yet": it never checks on silence, and an unanswered prompt is pinned to Your list, turns the tray
amber and goes in the digest. Check now is always on the board. These are the defaults the design gives,
taken as they are.

Group 2, built on what is there and marked so: a close is MozaikJob.LCK going from a job's folder, as
the guards keep it (guards/kept.py, held_locks). ASSUMPTION: Mozaik holds that lock while a job is open and
removes it on close; no real .LCK has been read, and the Mozaik lab settles it. So does how long after a
close the rooms are whole: here a check waits while the lock is there, and a job that changes while it
is checked is stale from the start.

A run is bound to what it ran on (design 3.5, "Results are bound to room hashes"): the sha256 of the
very bytes of each room, the IC Conversion's and the toolkit copy's. Each minute a pass on the same
worker compares those with the files now (size and time first, the bytes when they differ), and a run
whose files changed is stale for good. A stale run never counts: the board says to check again, its
items leave Your list and the digest, and counts() is false, which is what a lock must ask (P4).

A settle is Jim saying an item is not a problem for this job, or that he already told his chat. It is
bound to the item's fingerprint and to the rooms' design identities and the IC Conversion's hash that
its run ran on, so Mozaik saving the same design again keeps it, and any real change asks again. An item
that must never be silenced (a line bore off the panel) or that is no finding (could not verify) can't
be settled. A settle for all jobs (P4's second round; design 3.3 and section 6) must name its condition in
Jim's words, and stands for every finding of its kind (checker and what) on any job, through new runs and
design changes; it goes on NG Runner's one settled list (checks/settled.py), which the chat reads alongside
each job's notes, and those notes say which of the job's items it settles.

The audit of a chat-built job: when a job's folder appears in Mozaik Shared after install with its rooms
settled, it is audited once, before sections go in (findings.AUDIT_CHECKERS). What was there at the first
look is never audited, as 65 old jobs would all be.

Trial (design 5, "Trust per feature"): a run says "(trial)" until 15 runs of its kind have been made
(the P3 trial is "15 checks compared with chat"), and while its job has a feature no earlier run had:
garage, REA or a bridge, as the rooms' product names show them. Tafisa 1/4 backs and stacked towers
can't be told from a room's product names, so they don't hold a run on trial; that is said here.

Who hears: a check Jim asked for is a toast at his PC (shadow mode: Sulaiman's email, marked, and no
toast). An audit nobody asked for is on the board and in the digest. A crash or a run over 15 minutes
goes to DEV, and so does a checker that could not verify for want of something on this PC (its library,
the reference job).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ngrunner.checks.crosscheck import TEMPLATE_JOB, run_crosscheck
from ngrunner.checks.findings import (
    CNV, FIX, MOZAIK, Findings, Item, findings_of, from_json, to_json,
)
from ngrunner.checks import settled as settled_list
from ngrunner.checks.readers import NO_LIBRARY, NO_REFERENCE, STOPPED
from ngrunner.clock import to_iso
from ngrunner.guards.bak import DesignUnreadable
from ngrunner.guards.design import room_identity
from ngrunner.guards.facts import COULD_NOT_VERIFY, FAILED, PASSED
from ngrunner.guards.kept import held_locks, nest_files_said
from ngrunner.guards.observer import LOCK_FILE
from ngrunner.guards.reads import Unreadable, read_shared
from ngrunner.interlock import GARAGE_SUFFIX
from ngrunner.messages import templates
from ngrunner.store import (
    CheckRunRecord, JobRecord, PromptRecord, SettleRecord, Store, StoreError, add_prompt, add_settle, answer_prompt as
    store_answer_prompt, ask_check, back_to_asked, close_dev, finish_check, get_check_run, get_job,
    get_state, list_check_runs, list_dev, list_jobs, list_prompts, list_settles, log_event, mark_stale,
    mark_told, not_run_check, open_dev, set_state, start_check,
)
from ngrunner.toasts import Choice, ToastMessage
from ngrunner.toolkit import Snapshot, ToolkitError, adopted_snapshot

_log = logging.getLogger("ngrunner.checks")

TRIAL_CHECKS = 15                       # design 7, P3: "15 checks compared with chat"
PROMPT_EVERY = timedelta(hours=3)       # design 4: a later close asks at most once per job every 3 hours
CHECK_OVERRUN = timedelta(minutes=15)   # design 3.4: a check's limit
PROMPT_ARG = "check-prompt"             # a close prompt's toast buttons: "check-prompt <id> <n>"
CHECK_FAILED, CHECK_STUCK = "check_failed", "check_stuck"
CHECK_UNVERIFIED, MOZAIK_ERROR = "check_unverified", "mozaik_error"
SETTLED_LIST_FAILED = "settled_list_failed"
WAITING = "Waiting for Mozaik to close the job"
_SEEN = "checks.seen"                   # JSON: the folders at the first look, and those held open
_FRESH = 0                              # the worker's key when it runs no check: run ids start at 1
_ASKED_BY_JIM = ("check_now", "sections_done")
_ON_THIS_PC = (NO_LIBRARY, NO_REFERENCE, STOPPED)     # a checker short of something here: Sulaiman's
_PRODUCT = re.compile(rb'ProdName="([^"]*)"')
_ROOM = re.compile(r"Room(\d+)\.des", re.IGNORECASE)

NO_MOZAIK_FOLDER = "Its job folder isn't in Mozaik Shared, so there is nothing to check yet"
NO_CONVERSION = ("Its NG Jobs folder has no IC Conversion yet, so crosscheck has nothing to check the"
                 " job against")
CHECK_ERROR = "The check stopped with an error, which is in NG Runner's log"
CHECK_HUNG = "The check ran for over 15 minutes and was given up"


# ----- what the worker is handed, and what it hands back


@dataclass(frozen=True)
class Plan:
    """One run, made on the scheduler's thread from the store alone: every file is read on the worker."""

    run_id: int
    kind: str
    folders: tuple[str, ...]
    mozaik_jobs: Path
    ng_jobs: Path
    snapshot: Snapshot
    work: Path
    garage: bool


@dataclass(frozen=True)
class Fresh:
    """A finished run to compare with the files now."""

    run_id: int
    folders: tuple[str, ...]
    mozaik_jobs: Path
    ng_jobs: Path
    rooms: Mapping[str, str]
    stats: Mapping[str, Sequence[int]]
    conversion_sha256: str


@dataclass(frozen=True)
class Ran:
    """What a run came to. outcome: done, waiting (Mozaik has the job open) or not_run, with words."""

    run_id: int
    outcome: str
    words: str = ""
    error: bool = False
    findings: Findings | None = None
    rooms: Mapping[str, str] = field(default_factory=dict)
    identities: Mapping[str, str] = field(default_factory=dict)
    stats: Mapping[str, Sequence[int]] = field(default_factory=dict)
    snapshot_sha256: str = ""
    conversion_sha256: str = ""
    features: tuple[str, ...] = ()
    changed: bool = False


@dataclass(frozen=True)
class Done:
    """One pass of the worker: the run it made, if any, and the finished runs whose files changed."""

    ran: Ran | None
    stale: tuple[int, ...] = ()


# ----- what the board, the digest and Your list read


@dataclass(frozen=True)
class ShownItem:
    item: Item
    settle: SettleRecord | None = None


@dataclass(frozen=True)
class JobCheck:
    """A job's check as it stands. run: its newest run. state: asked, running, waiting, passed, failed,
    could_not_verify, stale or not_run. items: the newest run's, settled or not; open: those still to
    act on (none for a stale run). counts: a finished run whose files are those on disk, as far as the
    last pass saw. prompt: a close prompt pinned to Your list."""

    run: CheckRunRecord | None
    state: str
    line: str
    items: tuple[ShownItem, ...] = ()
    open: tuple[ShownItem, ...] = ()
    to_fix: int = 0
    unverified: int = 0
    settled: int = 0
    counts: bool = False
    prompt: PromptRecord | None = None
    trial: bool = False
    audit: bool = False


def counts(run: CheckRunRecord, *, rooms: Mapping[str, str], conversion_sha256: str) -> bool:
    """Whether a run's result stands for the job as it is now: finished, never stale, and run on exactly
    these rooms (name: the sha256 of its bytes now) and this IC Conversion. What a lock asks (P4)."""
    return (run.state == "done" and run.stale_at is None and dict(rooms) == dict(run.rooms)
            and conversion_sha256 == run.conversion_sha256)


def job_checks(store: Store, now: datetime) -> dict[str, JobCheck]:
    """Each job's check, by Studio ID, from the store alone."""
    runs: dict[str, CheckRunRecord] = {}
    for run in list_check_runs(store):
        runs[run.studio_id] = run                       # oldest first, so the newest stands
    settles: dict[str, list[SettleRecord]] = {}
    every = list_settles(store)
    for settle in every:
        if settle.scope == "job":
            settles.setdefault(settle.studio_id, []).append(settle)
    all_jobs = settled_list.all_jobs(every)
    prompts = _pinned(store, runs)
    out = {}
    for studio_id in {*runs, *prompts}:
        out[studio_id] = _job_check(runs.get(studio_id), settles.get(studio_id, ()), prompts.get(studio_id),
                                    now, all_jobs)
    return out


def _job_check(run: CheckRunRecord | None, settles: Sequence[SettleRecord], prompt: PromptRecord | None,
               now: datetime, all_jobs: Sequence[SettleRecord] = ()) -> JobCheck:
    if run is None:
        return JobCheck(None, "none", "", prompt=prompt)
    audit = run.kind == "audit"
    if run.state in ("asked", "running"):
        state = "waiting" if run.state == "asked" and run.words == WAITING else run.state
        return JobCheck(run, state, templates.check_state_words(state=state), prompt=prompt, audit=audit)
    if run.state == "not_run":
        return JobCheck(run, "not_run", templates.check_state_words(state="not_run", at=run.finished_at,
                                                                     now=now, words=run.words or ""),
                        prompt=prompt, audit=audit)
    found = from_json(run.result or {"code": COULD_NOT_VERIFY, "items": [], "verdicts": [], "left_out": []})
    shown = tuple(ShownItem(item, _settle_of(item, run, settles, all_jobs)) for item in found.items)
    if run.stale_at is not None:
        return JobCheck(run, "stale", templates.check_state_words(state="stale", at=run.finished_at, now=now,
                                                                  audit=audit),
                        items=shown, prompt=prompt, trial=run.trial, audit=audit)
    open_ = tuple(s for s in shown if s.settle is None and s.item.kind in (FIX, MOZAIK, CNV))
    to_fix = sum(1 for s in open_ if s.item.kind in (FIX, MOZAIK))
    unverified = sum(1 for s in open_ if s.item.kind == CNV)
    settled = sum(1 for s in shown if s.settle is not None)
    state = {PASSED: "passed", FAILED: "failed"}.get(found.code, "could_not_verify")
    line = templates.check_state_words(state=state, at=run.finished_at, now=now, trial=run.trial, audit=audit,
                                       to_fix=to_fix, unverified=unverified, settled=settled)
    return JobCheck(run, state, line, shown, open_, to_fix, unverified, settled, counts=True, prompt=prompt,
                    trial=run.trial, audit=audit)


def notes_block(job: str, check: JobCheck | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """The check as <Job>_NGRunner.json carries it for Jim's chat (notes schema 4), and the settled list:
    each item of the job's newest run in NG Runner's own words, with its chat line and whether Jim settled
    it, and each settle that stands for that run, with how and his words as he gave them. counts: the run
    stands for the files on disk as the last look found them; a stale one says to check again. Nothing a
    checker printed is in it. (None, []) for a job never checked."""
    if check is None or check.run is None:
        return None, []
    run = check.run
    block = {"state": check.state, "kind": run.kind, "trial": bool(run.trial), "counts": bool(check.counts),
             "at": None if run.finished_at is None else to_iso(run.finished_at),
             "items": [{"n": shown.item.n, "kind": shown.item.kind, "checker": shown.item.checker,
                        "tags": list(shown.item.tags), "words": templates.check_item_words(shown.item),
                        "chat": templates.check_chat_line(job, shown.item), "settled": shown.settle is not None}
                       for shown in check.items]}
    settled = [_settled_entry(shown) for shown in check.items if shown.settle is not None]
    return block, settled


def _settled_entry(shown: ShownItem) -> dict[str, Any]:
    """A settled item as the job's notes carry it: this job's own, or one of the list for all jobs."""
    settle = shown.settle
    assert settle is not None
    said = {"item": shown.item.n, "tags": list(shown.item.tags), "words": templates.check_item_words(shown.item)}
    if settle.scope == settled_list.ALL_JOBS:
        return {**said, "scope": "all jobs", "how": settle.how, "said": templates.SETTLE_LABELS.get(settle.how, ""),
                "condition": settle.words, "at": to_iso(settle.at), "list": settled_list.WHERE}
    return {**said, "scope": "this job", "how": settle.how, "said": settle.words, "at": to_iso(settle.at)}


def _settle_of(item: Item, run: CheckRunRecord, settles: Iterable[SettleRecord],
               all_jobs: Iterable[SettleRecord] = ()) -> SettleRecord | None:
    """A settle of this job stands for an item only on the very finding, design and IC Conversion it was made
    on; one for all jobs stands for every finding of its kind, on any job."""
    if not item.settles:
        return None
    for settle in settles:
        if (settle.fingerprint == item.fingerprint and settle.identities == run.identities
                and settle.conversion_sha256 == run.conversion_sha256):
            return settle
    return next((s for s in all_jobs if (s.checker, s.what) == (item.checker, item.what)), None)


def _pinned(store: Store, runs: Mapping[str, CheckRunRecord]) -> dict[str, PromptRecord]:
    """Each job's newest close prompt, while unanswered and no check was asked since the close."""
    newest: dict[str, PromptRecord] = {}
    for prompt in list_prompts(store):
        newest[prompt.studio_id] = prompt
    return {sid: prompt for sid, prompt in newest.items()
            if prompt.answer is None and not (sid in runs and runs[sid].asked_at >= prompt.closed_at)}


# ----- asking, settling, answering: from the board, the command box and a toast


def ask(ctx: Any, studio_id: str, *, trigger: str = "check_now") -> str:
    """A check of one job, asked for. With no adopted toolkit it is on record as not run, saying so."""
    store, now = ctx.store, ctx.clock.now()
    job = get_job(store, studio_id)
    if job is None:
        return templates.NOT_A_JOB
    open_ = list_check_runs(store, studio_id, states=("asked", "running"))
    if open_:
        return templates.check_already_asked(job.name)
    run = ask_check(store, studio_id=studio_id, folder=job.folder or job.name, kind="check", trigger=trigger,
                    at=now)
    if adopted_snapshot(store, ctx.paths.toolkit) is None:
        start_check(store, run.id, at=now)
        not_run_check(store, run.id, at=now, words=templates.NO_TOOLKIT)
        return f"{templates.NO_TOOLKIT}."
    return templates.check_asked(job.name)


def settle(ctx: Any, studio_id: str, run_id: int, n: int, *, how: str, words: str, channel: str,
           scope: str = "job") -> str:
    """Jim's settle of item n of a run: only on a run that still counts, and an item that may be. scope
    "all_jobs": not a problem for any job, under the condition words give, which it must."""
    if scope == settled_list.ALL_JOBS and not words.strip():
        return templates.SETTLE_NEEDS_CONDITION
    store, now = ctx.store, ctx.clock.now()
    job, run = get_job(store, studio_id), get_check_run(store, run_id)
    if job is None or run is None or run.studio_id != studio_id or run.result is None:
        return templates.SETTLE_GONE
    if run.stale_at is not None or run.state != "done":
        return templates.SETTLE_STALE
    item = next((i for i in from_json(run.result).items if i.n == n), None)
    if item is None:
        return templates.SETTLE_GONE
    if not item.settles:
        return templates.SETTLE_NEVER
    if run.conversion_sha256 is None:
        return templates.SETTLE_STALE
    every = scope == settled_list.ALL_JOBS
    said = templates.settled_echo(job.name, n=n, how=how, all_jobs=every)
    with store.transaction() as tx:
        add_settle(tx, studio_id=studio_id, run_id=run.id, fingerprint=item.fingerprint,
                   identities=run.identities, conversion_sha256=run.conversion_sha256, how=how,
                   words=" ".join(words.split()) if every else words, channel=channel, at=now, scope=scope,
                   checker=item.checker if every else None, what=item.what if every else None)
        log_event(tx, at=now, kind="settled", lane="ACT", studio_id=studio_id, text=said)
    if every:
        _keep_list(ctx)
    return said


def _keep_list(ctx: Any) -> None:
    """NG Runner's one settled list, current with the store; a failure is said once, never stops the rest."""
    try:
        problem = settled_list.keep(ctx)
    except StoreError:
        raise
    except Exception:  # noqa: BLE001 - the list is written again next minute; a check must go on
        _log.exception("the settled list could not be kept")
        problem = "The settled list for all jobs could not be written"
    if problem and not any(i.code == SETTLED_LIST_FAILED for i in list_dev(ctx.store)):
        open_dev(ctx.store, at=ctx.clock.now(), code=SETTLED_LIST_FAILED,
                 text=f"The settled list for all jobs wasn't written: {templates._clause(problem)}.")
    elif not problem:
        for item in list_dev(ctx.store):
            if item.code == SETTLED_LIST_FAILED:
                close_dev(ctx.store, item.id)


def answer_prompt(ctx: Any, prompt_id: int, choice: str | int, *, words: str, channel: str) -> str:
    """Jim's answer to a close prompt, kept as he gave it: its label, or its number from a toast's
    button. A "check it" answer asks a check."""
    store, now = ctx.store, ctx.clock.now()
    prompt = next((p for p in list_prompts(store) if p.id == prompt_id), None)
    if prompt is None:
        return templates.PROMPT_ANSWERED
    choices = templates.CHECK_CLOSE_FIRST if prompt.first else templates.CHECK_CLOSE
    if isinstance(choice, int):
        choice = choices[choice - 1] if 1 <= choice <= len(choices) else ""
    if choice not in choices:
        return templates.PROMPT_NOT_A_CHOICE
    if not store_answer_prompt(store, prompt_id, answer=choice, answer_words=words, answered_at=now,
                               channel=channel):
        return templates.PROMPT_ANSWERED
    job = get_job(store, prompt.studio_id)
    name = job.name if job is not None else prompt.folder
    if choice in templates.CHECK_THESE:
        return ask(ctx, prompt.studio_id, trigger="sections_done")
    if choice == templates.CHECK_CLOSE_FIRST[2]:
        open_dev(store, at=now, code=MOZAIK_ERROR, studio_id=prompt.studio_id,
                 text=f"Jim says {name} showed an error when it opened in Mozaik.")
        log_event(store, at=now, kind="mozaik_error", lane="ACT", studio_id=prompt.studio_id,
                  text=templates.mozaik_error_echo(name))
        return templates.mozaik_error_echo(name)
    return templates.not_yet_echo(name)


NO_SUCH_JOB = templates.NOT_A_JOB
_PROMPT_CHOICE = re.compile(rf"{PROMPT_ARG} (?P<id>[1-9][0-9]*) (?P<n>[1-9])")


def prompt_choice(argument: str) -> tuple[int, int] | None:
    """A close prompt's toast button, "check-prompt <id> <n>", as (id, n); None for anything else."""
    found = _PROMPT_CHOICE.fullmatch(argument)
    return None if found is None else (int(found["id"]), int(found["n"]))


def check_command(store: Store, text: str) -> str | None:
    """The command box's "check <job>" (design 4, "How Jim answers"), read by cards.parse_command: the
    job's Studio ID, "" when no job is named so, and None when the text is no check command."""
    from ngrunner.cards import Command, parse_command

    parsed = parse_command(text, [])
    if not isinstance(parsed, Command) or parsed.kind != "check" or parsed.name is None:
        return None
    return job_named(store, parsed.name)


def job_named(store: Store, name: str) -> str:
    """The Studio ID of the one job a name names, whole and ignoring case: its frozen name, its folder or
    an alias, never a part of one. "" when none does, or more than one."""
    wanted = " ".join(name.split()).casefold()
    named = [job.studio_id for job in list_jobs(store)
             if wanted in {each.casefold() for each in (*_folders_of(job), *job.aliases)}]
    return named[0] if len(named) == 1 else ""


# ----- the tick: on the scheduler's thread, never waiting on a file


def tick(ctx: Any, runner: Any, *, toast: Callable[[ToastMessage], object] | None = None) -> None:
    """Take what the worker found, look for closes and newly built jobs, then start the next pass: the
    oldest check asked, and the freshness of every run that counts."""
    take_finished(ctx, runner, toast=toast)
    now = ctx.clock.now()
    _keep_list(ctx)
    for key in runner.overran():
        if key != _FRESH:
            _gave_up(ctx, key, now)
    in_flight = runner.busy()
    for run in list_check_runs(ctx.store, states=("running",)):
        if run.id not in in_flight:                     # its worker died with NG Runner: ask it again
            back_to_asked(ctx.store, run.id)
    _closes(ctx, now, toast)
    _audits(ctx, now)
    if getattr(runner, "stopped", False) or runner.busy() or runner.free() <= 0:
        return
    plan = _next_plan(ctx, now)
    fresh = _fresh_list(ctx)
    if plan is None and not fresh:
        return
    runner.start(plan.run_id if plan is not None else _FRESH, lambda: _work(plan, fresh))
    take_finished(ctx, runner, toast=toast)


def take_finished(ctx: Any, runner: Any, *, toast: Callable[[ToastMessage], object] | None = None) -> bool:
    """What the worker found, recorded on this thread. True when there was any."""
    finished = runner.finished()
    now = ctx.clock.now()
    for done in finished:
        if done.ran is not None:
            _record(ctx, done.ran, now, toast)
        for run_id in done.stale:
            mark_stale(ctx.store, run_id, at=now)
    return bool(finished)


def _next_plan(ctx: Any, now: datetime) -> Plan | None:
    store = ctx.store
    for run in list_check_runs(store, states=("asked",)):
        if not start_check(store, run.id, at=now):
            continue
        snapshot = adopted_snapshot(store, ctx.paths.toolkit)
        job = get_job(store, run.studio_id)
        if snapshot is None or job is None:
            not_run_check(store, run.id, at=now, words=templates.NO_TOOLKIT if job else templates.NOT_A_JOB)
            continue
        folders = _folders_of(job)
        return Plan(run.id, run.kind, folders, ctx.settings.mozaik_shared / "Jobs", ctx.settings.ng_jobs,
                    snapshot, ctx.paths.staging / "checks", _is_garage(job, folders))
    return None


def _fresh_list(ctx: Any) -> list[Fresh]:
    out = []
    jobs = {job.studio_id: job for job in list_jobs(ctx.store)}
    newest: dict[str, CheckRunRecord] = {}
    for run in list_check_runs(ctx.store, states=("done",)):
        newest[run.studio_id] = run
    for run in newest.values():
        if run.stale_at is None and run.conversion_sha256 and run.studio_id in jobs:
            out.append(Fresh(run.id, _folders_of(jobs[run.studio_id]), ctx.settings.mozaik_shared / "Jobs",
                             ctx.settings.ng_jobs, run.rooms, run.stats, run.conversion_sha256))
    return out


def _record(ctx: Any, ran: Ran, now: datetime, toast: Callable[[ToastMessage], object] | None) -> None:
    store = ctx.store
    run = get_check_run(store, ran.run_id)
    if run is None or run.state != "running":
        return                                          # given up on meanwhile: what it found is dropped
    if ran.outcome == "waiting":
        back_to_asked(store, run.id, words=WAITING)
        return
    if ran.outcome != "done" or ran.findings is None:
        not_run_check(store, run.id, at=now, words=ran.words or CHECK_ERROR)
        if ran.error:
            open_dev(store, at=now, code=CHECK_FAILED, studio_id=run.studio_id,
                     text="A check stopped with an error; NG Runner's log has it.")
        return
    trial = _on_trial(store, run, ran.features)
    finish_check(store, run.id, at=now, code=ran.findings.code, words=_run_words(ran.findings),
                 snapshot_sha256=ran.snapshot_sha256, conversion_sha256=ran.conversion_sha256, rooms=ran.rooms,
                 identities=ran.identities, stats=ran.stats, result=to_json(ran.findings),
                 features=ran.features, trial=trial)
    if ran.changed:
        mark_stale(store, run.id, at=now)
    done = get_check_run(store, run.id)
    view = job_checks(store, now).get(run.studio_id)
    if view is not None and view.line:
        log_event(store, at=now, kind="checked", lane="ACT", studio_id=run.studio_id, text=view.line)
    _environment(store, ran.findings, now)
    if run.trigger in _ASKED_BY_JIM and not ran.changed and done is not None and view is not None:
        _tell(ctx, done, view, now, toast)
    if run.trigger in _ASKED_BY_JIM:
        close_dev_of(store, CHECK_FAILED, run.studio_id)


def _tell(ctx: Any, run: CheckRunRecord, view: JobCheck, now: datetime,
          toast: Callable[[ToastMessage], object] | None) -> None:
    """A check Jim asked for: a toast at his PC; in shadow mode, Sulaiman's email, marked, and no toast."""
    job = get_job(ctx.store, run.studio_id)
    if job is None:
        return
    items = [shown.item for shown in view.open]
    message = templates.check_result(job.name, at=run.finished_at or now, trial=run.trial, audit=False,
                                     items=items, passed=view.state == "passed")
    if ctx.mode == "shadow":
        ctx.messenger.send(message, to="jim")
    elif toast is not None:
        checked = templates.vetted(message, now=now)
        toast(ToastMessage("check_result", checked.message.title, checked.message.lines))
    mark_told(ctx.store, run.id, at=now)


def _gave_up(ctx: Any, run_id: int, now: datetime) -> None:
    run = get_check_run(ctx.store, run_id)
    if run is not None and not_run_check(ctx.store, run_id, at=now, words=CHECK_HUNG):
        open_dev(ctx.store, at=now, code=CHECK_STUCK, studio_id=run.studio_id,
                 text="A check ran for over 15 minutes and was given up.")


def _environment(store: Store, found: Findings, now: datetime) -> None:
    """A checker that could not verify for want of something on this PC: one DEV item each, closed when
    it passes again."""
    for verdict in found.verdicts:
        text = f"The {templates.CHECKER_WORDS.get(verdict.checker, 'a')} check {verdict.why.rstrip('.')}."
        if verdict.code == COULD_NOT_VERIFY and verdict.why in _ON_THIS_PC:
            if not any(item.code == CHECK_UNVERIFIED and item.text == text for item in list_dev(store)):
                open_dev(store, at=now, code=CHECK_UNVERIFIED, text=text)
        elif verdict.code == PASSED:
            for item in list_dev(store):
                if item.code == CHECK_UNVERIFIED and item.text.startswith(
                        f"The {templates.CHECKER_WORDS.get(verdict.checker, 'a')} check "):
                    close_dev(store, item.id)


def close_dev_of(store: Store, code: str, studio_id: str) -> None:
    for item in list_dev(store):
        if item.code == code and item.studio_id == studio_id:
            close_dev(store, item.id)


def _on_trial(store: Store, run: CheckRunRecord, features: Sequence[str]) -> bool:
    """Trial until TRIAL_CHECKS runs of its kind are done, and while its job has a feature none of them had."""
    earlier = [r for r in list_check_runs(store, states=("done",)) if r.kind == run.kind and r.id != run.id]
    if len(earlier) < TRIAL_CHECKS:
        return True
    exercised = {feature for r in earlier for feature in r.features}
    return not set(features) <= exercised


def _run_words(found: Findings) -> str:
    return {PASSED: "Every check passed.", FAILED: "The checks found something to fix."}.get(
        found.code, "Some checks could not verify.")


# ----- the close prompt and the audit: from what the guards keep of Mozaik Shared


def _closes(ctx: Any, now: datetime, toast: Callable[[ToastMessage], object] | None) -> None:
    """A job whose MozaikJob.LCK went since the last look was closed: ask whether its sections are done."""
    said = nest_files_said(ctx.store)
    if said is None:
        return
    seen = _seen(ctx.store)
    held = sorted(folder for folder, locks in held_locks(ctx.store).items()
                  if any(lock.open_in_mozaik for lock in locks))
    closed = [folder for folder in seen.get("held", []) if folder not in held and folder in said]
    for folder in closed:
        job = _job_of(ctx.store, folder)
        if job is not None:
            _prompt(ctx, job, folder, now, toast)
    _keep_seen(ctx.store, {**seen, "held": held})


def _prompt(ctx: Any, job: JobRecord, folder: str, now: datetime,
            toast: Callable[[ToastMessage], object] | None) -> None:
    earlier = list_prompts(ctx.store, job.studio_id)
    if earlier and now - earlier[-1].closed_at < PROMPT_EVERY:
        return
    first = not earlier and not any(run.kind == "check" for run in list_check_runs(ctx.store, job.studio_id))
    prompt = add_prompt(ctx.store, studio_id=job.studio_id, folder=folder, closed_at=now, first=first)
    if ctx.mode == "shadow" or toast is None:
        return
    message = templates.vetted(templates.close_prompt(job.name, closed_at=now, first=first), now=now).message
    choices = templates.CHECK_CLOSE_FIRST if first else templates.CHECK_CLOSE
    toast(ToastMessage("close_prompt", message.title, message.lines,
                       tuple(Choice(label, f"{PROMPT_ARG} {prompt.id} {n}") for n, label in enumerate(choices, 1)),
                       sticky=True))


def _audits(ctx: Any, now: datetime) -> None:
    """A job folder that appeared in Mozaik Shared after the first look, its rooms settled: audit it once."""
    said = nest_files_said(ctx.store)
    if said is None:
        return
    seen = _seen(ctx.store)
    if "folders" not in seen:
        _keep_seen(ctx.store, {**seen, "folders": sorted(said)})
        return
    known = set(seen["folders"])
    for folder, kept in sorted(said.items()):
        if folder in known:
            continue
        rooms = kept.get("rooms") or {}
        if not rooms.get("read") or rooms.get("read") != rooms.get("seen"):
            continue                                    # no rooms yet, or still settling
        job = _job_of(ctx.store, folder)
        if job is not None and not list_check_runs(ctx.store, job.studio_id):
            ask_check(ctx.store, studio_id=job.studio_id, folder=folder, kind="audit", trigger="audit", at=now)
        known.add(folder)
    _keep_seen(ctx.store, {**_seen(ctx.store), "folders": sorted(known)})


def _seen(store: Store) -> dict[str, Any]:
    return json.loads(get_state(store, _SEEN) or "{}")


def _keep_seen(store: Store, seen: Mapping[str, Any]) -> None:
    """Written only when it changed: it is read every minute, and changes seldom."""
    text = json.dumps(dict(seen), sort_keys=True)
    if text != get_state(store, _SEEN):
        set_state(store, _SEEN, text)


def _job_of(store: Store, folder: str) -> JobRecord | None:
    for job in list_jobs(store):
        if folder.casefold() in {name.casefold() for name in _folders_of(job)}:
            return job
    return None


def _folders_of(job: JobRecord) -> tuple[str, ...]:
    return tuple(dict.fromkeys(name for name in (job.folder, job.name, job.name + GARAGE_SUFFIX) if name))


def _is_garage(job: JobRecord, folders: Sequence[str]) -> bool:
    return job.branch == "garage" or (job.folder or "").casefold().endswith(GARAGE_SUFFIX.casefold())


# ----- on the worker: everything that reads a file


def _work(plan: Plan | None, fresh: Sequence[Fresh]) -> Done:
    """Whatever goes wrong here comes back as a fact, never as a dead thread."""
    ran = None
    if plan is not None:
        try:
            ran = run_plan(plan)
        except Exception:  # noqa: BLE001 - a check's failure is its own, and goes to DEV
            _log.exception("A check stopped with an error")
            ran = Ran(plan.run_id, "not_run", CHECK_ERROR, error=True)
    try:
        stale = stale_runs(fresh)
    except Exception:  # noqa: BLE001 - a look that fails concludes nothing
        _log.exception("The checks' look at the files failed")
        stale = ()
    return Done(ran, stale)


def run_plan(plan: Plan) -> Ran:
    """Jim's crosscheck on a staged copy of the job, and what it ran on, read again in place after."""
    job = _find(plan.mozaik_jobs, plan.folders)
    if job is None:
        return Ran(plan.run_id, "not_run", NO_MOZAIK_FOLDER)
    if (job / LOCK_FILE).exists():
        return Ran(plan.run_id, "waiting", WAITING)
    conversion = _conversion(plan.ng_jobs, plan.folders)
    if conversion is None:
        return Ran(plan.run_id, "not_run", NO_CONVERSION)
    template = plan.mozaik_jobs / TEMPLATE_JOB
    try:
        result = run_crosscheck(plan.snapshot, job, conversion, plan.work,
                                template=template if template.is_dir() else None)
    except ToolkitError as exc:
        return Ran(plan.run_id, "not_run", str(exc).rstrip("."))
    staged = dict(result.rooms)
    try:
        now_rooms = {name: read_shared(job / name) for name in _rooms_in(job)}
        conversion_now = read_shared(conversion)
    except (Unreadable, OSError):
        now_rooms, conversion_now = {}, b""
    changed = ({name: _sha(data) for name, data in now_rooms.items()} != staged
               or _sha(conversion_now) != result.conversion_sha256)
    found = findings_of(result, kind=plan.kind, room_files={name: now_rooms[name] for name in staged
                                                            if name in now_rooms} or None,
                        garage=plan.garage)
    stats = {name: _stat(job / name) for name in staged}
    stats["conversion"] = _stat(conversion)
    return Ran(plan.run_id, "done", findings=found, rooms=staged,
               identities={name: _identity(name, now_rooms.get(name, b"")) for name in staged},
               stats={name: pair for name, pair in stats.items() if pair is not None},
               snapshot_sha256=result.snapshot_sha256, conversion_sha256=result.conversion_sha256,
               features=_features(now_rooms.values(), plan.garage), changed=changed)


def stale_runs(fresh: Sequence[Fresh]) -> tuple[int, ...]:
    """The runs whose files are not what they ran on: a room's bytes, the set of rooms, or the IC
    Conversion. A folder that can't be read concludes nothing."""
    stale = []
    for one in fresh:
        job = _find(one.mozaik_jobs, one.folders)
        conversion = _conversion(one.ng_jobs, one.folders)
        if job is None:
            continue
        try:
            names = _rooms_in(job)
            moved = set(names) != set(one.rooms) or any(
                _moved(job / name, one.rooms[name], one.stats.get(name)) for name in names)
            moved = moved or conversion is None or _moved(conversion, one.conversion_sha256,
                                                          one.stats.get("conversion"))
        except (OSError, Unreadable):
            continue
        if moved:
            stale.append(one.run_id)
    return tuple(stale)


def _moved(path: Path, sha256: str, stat: Sequence[int] | None) -> bool:
    now = _stat(path)
    if now is None:
        return True
    if stat is not None and list(now) == list(stat):
        return False
    return _sha(read_shared(path)) != sha256


def _find(folder: Path, names: Sequence[str]) -> Path | None:
    """The first of names in folder, as Windows spells it there, whatever the case."""
    try:
        with os.scandir(folder) as entries:
            there = {entry.name.casefold(): Path(entry.path) for entry in entries if entry.is_dir()}
    except OSError:
        return None
    return next((there[name.casefold()] for name in names if name.casefold() in there), None)


def _conversion(ng_jobs: Path, names: Sequence[str]) -> Path | None:
    """The chat's IC Conversion in the job's NG Jobs folder, or in NG Jobs\\Old, which is only read."""
    folder = _find(ng_jobs, names) or _find(ng_jobs / "Old", names)
    if folder is None:
        return None
    path = folder / f"{folder.name}_IC_Conversion.json"
    return path if path.is_file() else None


def _rooms_in(job: Path) -> list[str]:
    return sorted((name for name in os.listdir(job) if _ROOM.fullmatch(name)),
                  key=lambda name: int(_ROOM.fullmatch(name)[1]))


def _stat(path: Path) -> list[int] | None:
    try:
        found = path.stat()
    except OSError:
        return None
    return [found.st_size, found.st_mtime_ns]


def _identity(name: str, data: bytes) -> str:
    """A room's design identity; one that doesn't read as a design is its bytes' hash, so any save of it
    asks again."""
    try:
        return room_identity(name, data)
    except (DesignUnreadable, ValueError):
        return _sha(data)


def _features(rooms: Iterable[bytes], garage: bool) -> tuple[str, ...]:
    """What the rooms' product names show of design 5's features: a garage, REA, a bridge (BR)."""
    found = {"garage"} if garage else set()
    for data in rooms:
        for name in _PRODUCT.findall(data):
            words = set(name.decode("utf-8", "replace").split())
            if "BR" in words:
                found.add("bridge")
            if "REA" in words:
                found.add("rea")
            if "Garage" in words:
                found.add("garage")
    return tuple(sorted(found))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
