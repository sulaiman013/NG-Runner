"""
The job model: what the store's facts add up to, for the board, the tray and the digest
(integration spec section 5; design 3.4: facts, clicks and pending work; no single state).

Nothing here writes. Each call reads the store once and hands the board and the digest their
plain values, so a restart, a crash or a second window can never leave them out of step with
what really happened: there is no stored "status" to go stale. That is also why the rules live
in one module, and why the words do too: the same line reads on the board and in the digest.

What each fact means here, which is the contract with whoever writes it:
    fab_rows   state, as store.fab_rows says. reason: a plain sentence the board may show, so it
               says why a row is stuck ("that job has left the job list"); a bare code is ignored.
               seen_at_install and gone rows with no job never show: nothing will happen to them.
    jobs       mode "observed" gets no writes (ruling 12c). branch, once known. "held" is unused.
    events     A "filed" event with the job's Studio ID says when it was filed; a row in state
               "filed" says the same, and pipeline.filed_note what its line adds: the design and
               page counts, and "designs not cross-checked". A "blocked" event says when a refusal
               began. Only the ACT lane and "filed" and "blocked" events count as touching a job for
               the digest, so a nightly re-fetch that found nothing keeps a night quiet. "refiled"
               is a new time for a filed job. A "trial_only" event says it was filed only aside in
               the shadow run, so it is not in NG Jobs, whatever came before, until the next "filed".
    pending    "list..." waits on the job list; "fetch_2" is the second fetch; other "fetch..."
               kinds are the first or a retry; "refetch..." (filed jobs) doesn't show, nor does
               "takeoff" (a filed summary's takeoff, made in a moment). A "file..."
               operation with a note is a refused filing: BLOCKED, with the refusal as its note,
               unless the note says "Old folder": then there is nothing to fix, so OBSERVED. One
               that ended "failed" or "expired", with nothing else open, is NOT_RUN.
    cards      Open ones, oldest first. An answer is the pipeline's to act on. A sheet size card
               about a folder with no job has neither ID nor row, and is named from what it asked
               (guards.kept), so it shows like any other.
    nests      One row per material, the guard's last verdict. DO NOT CUT is red and on Your list.
               posted_at None means nothing was posted since install, so it never alarms. A posted
               NOT CHECKED goes in the digest and the heartbeat, and turns the tray red only once a
               toolkit snapshot is adopted: today every new post reads so, and a red that never
               clears teaches people to ignore red (Sulaiman's ruling).
    dev        Open items, by job. A code in templates.ALARMS is that alarm standing, so close it
               when the alarm clears. urgent turns the tray red. told_jim_at, set by the night's
               work, puts the item on the next digest's For you line, so it is told once.
               pipeline.FETCH_STUCK goes on Your list at once; a code in
               pipeline.CLOSED_BY_FILING says nothing of a job that has filed.
    outbox     ref "digest:<day>" marks a digest, whose time is what "since the last digest"
               means. Written over 5 minutes ago with no receipt turns the tray red.
    takeoffs   pipeline.job_takeoffs: a filed job's takeoff of the summary it is filed on now.
               Made and complete, its sheets and tape go on its line and in the digest, labelled
               an estimate; made and not complete, a "could not verify N lines" note and no list,
               since a partial list is worse than none, and when guards/takeoffs.py sent the
               chat's list instead (the guards' takeoffs state says "source": "chat"), that it
               did; "in_chat" and "failed" say why there is none, and the chat's takeoff is used.
    state      "paused", as interlock.is_paused reads it, so the digest and the filing never
               disagree. "fab_exported_at" (a time) and "toast_setting" ("on" or "off"), when
               set, decide their alarms whatever the DEV lane says.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ngrunner.board.views import (
    BoardView, CardView, EventView, HealthView, JobRow, ListItem, MaterialRow, TakeoffView, Tick,
    TrayState, tray_tooltip,
)
from ngrunner.cards import Card, age_days, card_from_record, reminder_due
from ngrunner.clock import Clock, fmt_clock, fmt_date, fmt_day, from_iso, pacific_day
from ngrunner.config import Settings
from ngrunner.digest import DigestView, JobLine
from ngrunner.fab import install_date
from ngrunner.guards.headsup import UNREADABLE_NOTE
from ngrunner.guards.kept import folder_of_card, nest_files_said, saved_preflights, takeoffs_said
from ngrunner.guards.observer import OPTIMIZER_LOCK
from ngrunner.interlock import GARAGE_SUFFIX
from ngrunner.intake.pipeline import (
    CLOSED_BY_FILING, FETCH_STUCK, TRIAL_ONLY_EVENT, all_filed_facts, filed_facts, filed_note_of,
    job_takeoffs,
)
from ngrunner.interlock import is_paused
from ngrunner.messages import Message, templates
from ngrunner.messages.lint import ABOUT_ITSELF, check_line, lint
from ngrunner.store import (
    CardRecord, DevRecord, EventRecord, FabRowRecord, JobRecord, NestRecord, PendingRecord, Store,
    TakeoffRecord,
    get_list_state, get_state, get_state_time, governing_render, list_cards, list_dev, list_events,
    list_fab_rows, list_jobs, list_nests, list_outbox, list_pending, list_renders, list_snapshots,
    outbox_without_receipt,
)

COULD_NOT_VERIFY = "could not verify"  # an install date that doesn't read as one date for sure
FILED_EVENT = "filed"
DIGEST_REF = "digest:"                 # messages.outbox.outbox_file's ref on a digest
EXPORTED_AT_KEY = "fab_exported_at"    # state keys
TOASTS_KEY = "toast_setting"
BLOCKED_EVENT = "blocked"
REFILED_EVENT = "refiled"
OLD_FOLDER_WORDS = "old folder"        # in a refusal's note: its folder is in NG Jobs Old
PAUSED_WORDS = "paused"                # in a refusal's note: only the pause said no
_TOLD_ELSEWHERE = ("dev_told_jim",)    # the night's note of a stuck job: For you says it already

_NO_RECEIPT_FOR = timedelta(minutes=5)
_EXPORT_STALE_AFTER = timedelta(minutes=30)
_DEV_TOLD_AFTER = timedelta(hours=48)
_BLOCKED_COUNTS_AFTER = timedelta(hours=1)
_INSTALL_SOON_DAYS = 5
_LIST_STALE_AFTER = timedelta(hours=1)
_ALARM_COUNTS_AFTER = timedelta(hours=1)
_BOARD_KEEPS = timedelta(days=30)      # as long as a filed job is re-fetched: nothing happens after
_ON_YOUR_LIST = ("ng_jobs_unreachable",)    # an alarm that holds up new rows, once it has stood
_TRAY_SAYS = ("fab_export_stale", "job_list_stale", "board_failed")   # never "nothing is waiting"

_TICKS = (
    ("row", "On the Fab Schedule"),
    ("job", "Matched to Salesforce"),
    ("summary", "Fetched and checked twice"),
    ("filed", "Filed"),
    ("branch", "Closet or garage"),
    ("nest", "Nest"),
)
_NOT_CHECKED_SHOWN = 10                # every post reads so until the toolkit is here
_ASKS_SOMETHING = ("do_not_cut", "save_nest")    # a material that keeps its row whatever else
_NOT_ON_THE_BOARD = ("seen_at_install", "gone")  # a row with no job in these: nothing will happen
_ROW_WAITS = ("new", "waiting_list", "matched")  # a row in these moves on by itself

PAUSED_STEP = "Paused: nothing is filed until you resume it at the PC"
LIST_STEP = "Waiting for a fresh job list"
NEW_ROW_STEP = "Waiting for the next Fab export, in case this row is still being typed"
NEXT_CHECK_STEP = "Starts at NG Runner's next check"
NG_JOBS_STEP = "NG Jobs can't be read, so this row waits; check that this PC can reach that folder"
TRIAL_ONLY_STEP = "Trial run only, not in NG Jobs"
ASKED_STEP = "Waiting for the full job name in the row"
NO_JOB_STEP = "No job found. Waiting for the full job name in the row, or for add job"
OBSERVED_STEP = ("Its folder was there at install, so NG Runner only watches it; work it in chat"
                 " as before")

# What a refused filing means, by the words in its reason, and the one-line fix (design 3.4).
_BLOCKS = (
    (("paused",), "NG Runner was paused", "none needed, it tries again at its next check"),
    (("reach",), "its folder can't be reached right now",
     "check that this PC can reach the NG Jobs folder"),
    (("changed", "moved", "replaced", "already exists"), "its folder changed during the filing",
     "check what is in the job's folder"),
)
_BLOCK_ELSE = ("NG Runner's safety check refused to file it",
               "send Sulaiman a support bundle from the tray")


# ----- one read of the store


@dataclass(frozen=True)
class _JobFacts:
    """What the store holds about one job: its open work, in the order it comes due."""

    job: JobRecord
    rows: tuple[FabRowRecord, ...]
    cards: tuple[CardRecord, ...]
    pending: tuple[PendingRecord, ...]
    ended: tuple[PendingRecord, ...]
    dev: tuple[DevRecord, ...]
    filed_at: datetime | None
    blocked_since: datetime | None
    facts: dict[str, Any] | None = None     # pipeline.filed_facts, read once for all the jobs
    trial_only: bool = False
    nests: tuple[NestRecord, ...] = ()      # its materials, as the guard last read them
    folders: tuple[str, ...] = ()           # the names its NG Jobs folder can go by
    takeoff: TakeoffRecord | None = None    # NG Runner's takeoff of the summary filed now
    chats_list: bool = False                # its order heads-up is the chat's takeoff instead


@dataclass(frozen=True)
class _Facts:
    now: datetime
    jobs: tuple[_JobFacts, ...]
    free_rows: tuple[FabRowRecord, ...]
    cards: tuple[Card, ...]
    records: tuple[CardRecord, ...]
    dev: tuple[DevRecord, ...]
    events: tuple[EventRecord, ...]
    names: dict[str, str]
    overdue: bool
    standing: frozenset[str] = frozenset()  # the codes of the alarms the store says stand
    nests: tuple[NestRecord, ...] = ()      # every material with something posted or from before
    toolkit_adopted: bool = False           # a posted NOT CHECKED means something only then
    lock: dict[str, dict[str, Any]] = field(default_factory=dict)      # the last pre-flights
    held: dict[str, list[Any]] = field(default_factory=dict)           # lock files, by folder
    takeoffs: dict[str, dict[str, Any]] = field(default_factory=dict)  # by folder
    card_folders: dict[str, str] = field(default_factory=dict)         # cards with no job or row
    ng_jobs: Path | None = None


def _read(store: Store, now: datetime, settings: Settings | None = None) -> _Facts:
    jobs, rows = list_jobs(store), list_fab_rows(store)
    names = {job.studio_id: job.name for job in jobs}
    texts = {row.id: row.text for row in rows}
    # A card about neither a job, a Fab row nor a folder can't be shown or acted on; add_card allows
    # one. It costs only itself: the supervisor's command box goes on working for the rest.
    open_cards = list_cards(store)
    card_folders = {c.id: folder for c in open_cards if not (c.studio_id or c.fab_row_id)
                    and (folder := folder_of_card(store, c.id)) is not None}
    records = tuple(c for c in open_cards if c.studio_id or c.fab_row_id or c.id in card_folders)
    dev, events = tuple(list_dev(store)), tuple(list_events(store))
    nests = tuple(n for n in list_nests(store) if n.state != "not_posted")
    return _Facts(
        now=now,
        jobs=_job_facts(store, jobs, rows, records, dev, events, nests),
        free_rows=tuple(r for r in rows
                        if r.studio_id is None and r.state not in _NOT_ON_THE_BOARD),
        cards=tuple(card_from_record(c, job=names.get(c.studio_id or "") or card_folders.get(c.id),
                                     row=texts.get(c.fab_row_id or 0)) for c in records),
        records=records,
        dev=dev,
        events=events,
        names=names,
        overdue=_no_receipt(store, now),
        standing=_standing(store, now, dev),
        nests=nests,
        toolkit_adopted=any(snapshot.adopted for snapshot in list_snapshots(store)),
        lock=saved_preflights(store),
        held={folder.casefold(): said["held"]
              for folder, said in (nest_files_said(store) or {}).items() if said.get("held")},
        takeoffs=takeoffs_said(store),
        card_folders=card_folders,
        ng_jobs=None if settings is None else settings.ng_jobs,
    )


def _job_facts(
    store: Store,
    jobs: Iterable[JobRecord],
    rows: Iterable[FabRowRecord],
    cards: Iterable[CardRecord],
    dev: Iterable[DevRecord],
    events: Iterable[EventRecord],
    nests: Iterable[NestRecord] = (),
) -> tuple[_JobFacts, ...]:
    """Grouped once, not searched per job: every page and tray update reads all the jobs, and
    jobs and their nightly re-fetches only pile up. Finished operations are left out but for
    those that ended badly, for the same reason."""
    filed: dict[str | None, datetime] = {}
    trial_only: set[str | None] = set()
    for event in events:                       # oldest first, so the last word on a job stands
        if event.lane == "DEV":
            continue
        if event.kind in (FILED_EVENT, REFILED_EVENT):
            filed[event.studio_id] = event.at
            trial_only.discard(event.studio_id)
        elif event.kind == TRIAL_ONLY_EVENT:
            filed.pop(event.studio_id, None)
            trial_only.add(event.studio_id)
    facts_of = all_filed_facts(store)
    takeoffs_of = job_takeoffs(store, facts_of)
    chats = {folder.casefold() for folder, said in takeoffs_said(store).items()
             if said.get("source") == "chat"}
    rows_of, cards_of, dev_of = _by_job(rows), _by_job(cards), _by_job(dev)
    refusals_of = _by_job(e for e in events
                          if e.kind in (FILED_EVENT, BLOCKED_EVENT) and e.lane != "DEV")
    open_of = _by_job(list_pending(store))
    ended_of = _by_job(sorted((*list_pending(store, state="failed"),
                               *list_pending(store, state="expired")), key=lambda p: p.due_at))
    return tuple(
        _JobFacts(
            job=job,
            rows=tuple(rows_of.get(job.studio_id, ())),
            cards=tuple(cards_of.get(job.studio_id, ())),
            pending=tuple(open_of.get(job.studio_id, ())),
            ended=tuple(ended_of.get(job.studio_id, ())),
            dev=tuple(dev_of.get(job.studio_id, ())),
            filed_at=None if job.studio_id in trial_only else filed.get(job.studio_id) or max(
                (r.updated_at for r in rows_of.get(job.studio_id, ()) if r.state == "filed"),
                default=None),
            blocked_since=_blocked_since(refusals_of.get(job.studio_id, ())),
            facts=facts_of.get(job.studio_id),
            trial_only=job.studio_id in trial_only,
            nests=tuple(n for n in nests if _folder_is_this_jobs(n, job)),
            folders=_folders_of(job),
            takeoff=takeoffs_of.get(job.studio_id),
            chats_list=any(name.casefold() in chats for name in _folders_of(job)),
        )
        for job in jobs
    )


def _folders_of(job: JobRecord) -> tuple[str, ...]:
    return tuple(dict.fromkeys(name for name in (job.folder, job.name, job.name + GARAGE_SUFFIX)
                               if name))


def _folder_is_this_jobs(nest: NestRecord, job: JobRecord) -> bool:
    """By its name as well: a post can come before its Fab row, and a row after it."""
    return nest.studio_id == job.studio_id or nest.folder.casefold() in {
        name.casefold() for name in _folders_of(job)}


def _blocked_since(events: Iterable[EventRecord]) -> datetime | None:
    """When a job's refusals began, from its "filed" and "blocked" events, oldest first: the first
    "blocked" since it was last filed, and since the last refusal that was only the pause. A reason
    that changes doesn't restart it, as the job has still not been filed all that time."""
    since = None
    for event in events:
        if event.kind == FILED_EVENT or _only_the_pause(event.text):
            since = None
        elif since is None:
            since = event.at
    return since


def _by_job(records: Iterable[Any]) -> dict[str | None, list[Any]]:
    groups: dict[str | None, list[Any]] = {}
    for record in records:
        groups.setdefault(record.studio_id, []).append(record)
    return groups


def _no_receipt(store: Store, now: datetime) -> bool:
    return bool(outbox_without_receipt(store, written_before=now - _NO_RECEIPT_FOR))


# ----- the board


def board_view(
    store: Store,
    settings: Settings | None,
    clock: Clock,
    *,
    health: HealthView,
    paused: bool,
    shadow: bool,
) -> BoardView:
    """Everything the board shows apart from History. settings may be None (not set up yet): a
    job's facts are all in the store. health is the supervisor's, for what only it can see (the
    folders, the toast setting); the alarms the store holds are added to it, so the Health page
    says what the digest says."""
    facts = _read(store, clock.now(), settings)
    return BoardView(
        jobs=_board_rows(store, facts, paused=paused),
        your_list=tuple(item for _, item in _your_list(facts, paused=paused)),
        cards=tuple(_card_view(card, facts.now) for card in facts.cards),
        health=replace(health, alarms=tuple(dict.fromkeys(
            (*_alarms(store, facts, paused=paused), *health.alarms)))),
        tray=_tray(facts, unwell=False, paused=paused, shadow=shadow),
        paused=paused,
        shadow=shadow,
    )


def _board_rows(store: Store, facts: _Facts, *, paused: bool) -> tuple[JobRow, ...]:
    rows = [_job_row(store, j, facts, paused=paused) for j in facts.jobs
            if _still_on_the_board(j, facts.now)]
    away = "ng_jobs_unreachable" in facts.standing
    rows += [_free_row(row, _row_cards(row, facts), paused=paused, away=away)
             for row in facts.free_rows]
    rows += _folder_rows(facts)
    return tuple(sorted(rows, key=_board_order))


def _folder_rows(facts: _Facts) -> list[JobRow]:
    """A folder in NG Jobs no job has yet, once a guard has something to say about it: what was
    there at install stays off the board, or 65 old jobs would crowd it."""
    taken = {name.casefold() for j in facts.jobs for name in j.folders}
    nests_of: dict[str, list[NestRecord]] = {}
    for nest in facts.nests:
        if nest.folder.casefold() not in taken:
            nests_of.setdefault(nest.folder, []).append(nest)
    cards_of: dict[str, list[CardRecord]] = {}
    for card in facts.records:
        if card.id in facts.card_folders and facts.card_folders[card.id].casefold() not in taken:
            cards_of.setdefault(facts.card_folders[card.id], []).append(card)
    unread = [folder for folder, said in facts.takeoffs.items()
              if said.get("bad") and folder.casefold() not in taken]
    rows = []
    for folder in sorted({*nests_of, *cards_of, *unread}):
        nests = nests_of.get(folder, [])
        if (any(nest.posted_at or nest.state in _ASKS_SOMETHING for nest in nests)
                or folder in cards_of or folder in unread):
            cards = cards_of.get(folder, ())
            rows.append(JobRow(
                studio_id=None, name=folder, install=None,
                ticks=_ticks(nest=_nest_tick(nests)),
                next_step=_card_step(cards[0]) if cards else templates.FOLDER_STEP,
                materials=_material_rows(nests, facts),
                notes=_notes((folder,), facts),
                lock=_lock_lines((folder,), facts)))
    return rows


def _material_rows(nests: Iterable[NestRecord], facts: _Facts) -> tuple[MaterialRow, ...]:
    return tuple(MaterialRow(
        material=nest.material, state=nest.state, note="; ".join(nest.reasons), at=nest.posted_at,
        file=None if facts.ng_jobs is None else facts.ng_jobs / nest.folder / nest.material,
        shown=templates.material_words(nest.material),
        trial=nest.trial and nest.posted_at is not None) for nest in nests)


def _notes(folders: Iterable[str], facts: _Facts) -> tuple[str, ...]:
    """A takeoff that won't read, and who the job's lock files name and for how long (build spec
    5.12). The lock's age is worked out here, so it goes on with nothing new read."""
    folders = tuple(folders)
    bad = any(facts.takeoffs.get(folder, {}).get("bad") for folder in folders)
    held = [templates.lock_held(file == OPTIMIZER_LOCK, owner,
                                max(facts.now - from_iso(since), timedelta(0)))
            for folder in folders for file, owner, since in facts.held.get(folder.casefold(), ())]
    return (*((UNREADABLE_NOTE,) if bad else ()), *held)


def _lock_lines(folders: Iterable[str], facts: _Facts) -> tuple[str, ...]:
    return tuple(item["text"] for folder in folders
                 for item in facts.lock.get(folder, {}).get("items", ()))


def _still_on_the_board(j: _JobFacts, now: datetime) -> bool:
    """A job stays while anything is open on it or its row is on the sheet, and for 30 days after.
    Then nothing more happens to it, and every page would go on reading it: jobs only pile up. Its
    folder, its events in History and "add job" still know it."""
    if j.cards or j.dev or any(r.state != "gone" for r in j.rows):
        return True
    if any(nest.state in _ASKS_SOMETHING for nest in j.nests):
        return True
    if any(not p.kind.startswith("refetch") for p in j.pending):
        return True
    return now - (j.filed_at or j.job.updated_at) <= _BOARD_KEEPS


def _board_order(row: JobRow) -> tuple[bool, date, str]:
    """What is still moving first, soonest install first; filed jobs after."""
    filed = any(tick.key == "filed" and tick.state == "done" for tick in row.ticks)
    day = row.install if isinstance(row.install, date) else date.max
    return filed, day, row.name.casefold()


def _job_row(store: Store, j: _JobFacts, facts: _Facts, *, paused: bool) -> JobRow:
    overlay = _overlay(j, paused=paused)
    return JobRow(
        studio_id=j.job.studio_id,
        name=j.job.name,
        install=_install(*_install_cells(j)),
        ticks=_job_ticks(store, j, overlay),
        next_step=_next_step(store, j, overlay, facts.now),
        overlay=overlay,
        materials=_material_rows(j.nests, facts),
        notes=(*_notes(j.folders, facts), *_takeoff_notes(j)),
        lock=_lock_lines(j.folders, facts),
        takeoff=_takeoff_view(j),
    )


def _takeoff_view(j: _JobFacts) -> TakeoffView | None:
    """A filed job's takeoff on its line: the list only when it is complete, as no one orders from
    a partial list; otherwise its state, and why, in one line."""
    record = j.takeoff
    if record is None:
        return None
    if record.state == "made":
        if not record.complete or record.result is None:
            return (TakeoffView("chat", (templates.CHATS_TAKEOFF,)) if j.chats_list
                    else TakeoffView("unverified"))
        return TakeoffView("made", templates.takeoff_lines(record.result["buckets"]),
                           templates.TAKEOFF_ESTIMATE)
    return TakeoffView(record.state, (templates.takeoff_made_words(state=record.state,
                                                                   branch=record.branch),))


def _takeoff_notes(j: _JobFacts) -> tuple[str, ...]:
    """How many lines the takeoff could not verify: counted, never quoted."""
    record = j.takeoff
    if record is None or record.state != "made" or record.complete:
        return ()
    return (templates.could_not_verify_lines(record.could_not_verify),)


def _free_row(row: FabRowRecord, cards: tuple[CardRecord, ...], *, paused: bool,
              away: bool = False) -> JobRow:
    """A Fab row with no job yet: a row waiting on its which-job card still shows."""
    asking = bool(cards) or row.state in ("asked", "none")
    return JobRow(
        studio_id=None,
        name=row.text,
        install=_install(row.install, row.first_seen),
        ticks=_ticks(row="done", job="blocked" if asking else "todo"),
        next_step=_free_step(row, cards, paused=paused, away=away),
        overlay="PAUSED" if paused else ("WAITING" if not asking else None),
    )


def _free_step(row: FabRowRecord, cards: tuple[CardRecord, ...], *, paused: bool,
               away: bool = False) -> str:
    if cards:
        return _card_step(cards[0])
    if paused:
        return PAUSED_STEP
    if away and row.state in ("new", "waiting_list"):
        return NG_JOBS_STEP
    if row.state == "waiting_list":
        return LIST_STEP
    if row.state == "new":
        return NEW_ROW_STEP
    return _plain(row.reason) or (NO_JOB_STEP if row.state == "none" else ASKED_STEP)


def _row_cards(row: FabRowRecord, facts: _Facts) -> tuple[CardRecord, ...]:
    return tuple(c for c in facts.records if c.fab_row_id == row.id and c.studio_id is None)


# ----- ticks


def _ticks(*, row: str = "todo", job: str = "todo", summary: str = "todo", filed: str = "todo",
           branch: str = "todo", branch_label: str | None = None,
           nest: str = "todo") -> tuple[Tick, ...]:
    states = {"row": row, "job": job, "summary": summary, "filed": filed, "branch": branch,
              "nest": nest}
    labels = dict(_TICKS)
    if branch_label:
        labels["branch"] = branch_label
    return tuple(Tick(key, labels[key], states[key]) for key, _ in _TICKS)


def _job_ticks(store: Store, j: _JobFacts, overlay: str | None) -> tuple[Tick, ...]:
    kinds = {card.kind for card in j.cards}
    return _ticks(
        row="done" if j.rows else "todo",
        job="done",
        summary=_summary_tick(store, j, kinds),
        filed="done" if j.filed_at else ("blocked" if overlay == "BLOCKED" else "todo"),
        branch="done" if j.job.branch else ("blocked" if "closet_or_garage" in kinds else "todo"),
        branch_label=j.job.branch.capitalize() if j.job.branch else None,
        nest=_nest_tick(j.nests),
    )


def _nest_tick(nests: Iterable[NestRecord]) -> str:
    """Done only when every material is Nest checked; any DO NOT CUT blocks it."""
    states = {nest.state for nest in nests}
    if "do_not_cut" in states:
        return "blocked"
    return "done" if states == {"nest_checked"} else "todo"


def _summary_tick(store: Store, j: _JobFacts, kinds: set[str]) -> str:
    if j.filed_at:
        return "done"
    if "design_mismatch" in kinds:             # two renders agreeing doesn't settle it: Jim does
        return "blocked"
    if j.trial_only:                           # what agreed then was the trial run's
        return "blocked" if "fetch_failed" in kinds else "todo"
    if _agreed(store, j.job.studio_id):
        return "done"
    return "blocked" if "fetch_failed" in kinds else "todo"


def _agreed(store: Store, studio_id: str) -> bool:
    """A render governs, or the last two fetches read the same and are two real fetches:
    identical bytes mean something served one render twice (ruling on same_bytes)."""
    if governing_render(store, studio_id) is not None:
        return True
    last = list_renders(store, studio_id)[-2:]
    return (len(last) == 2 and all(r.check_ok and r.fingerprint for r in last)
            and last[0].fingerprint == last[1].fingerprint and last[0].sha256 != last[1].sha256)


# ----- the install date


def _install_cells(j: _JobFacts) -> tuple[tuple[str, str] | None, datetime]:
    """The cells of the job's newest row still on the sheet, else its last row, else the job's
    own. A date with no year is read near when that row appeared, so it can't jump a year as
    time goes by."""
    rows = [r for r in j.rows if r.state != "gone"] or list(j.rows)
    if rows:
        return rows[-1].install, rows[-1].first_seen
    return j.job.install, j.job.created_at


def _install(cells: tuple[str, str] | None, near: datetime) -> date | str | None:
    if cells is None or not any(cell.strip() for cell in cells):
        return None
    return install_date(cells, near=near) or COULD_NOT_VERIFY


# ----- overlays and the next step


def _overlay(j: _JobFacts, *, paused: bool) -> str | None:
    """One overlay per row, the one that most explains why it isn't moving. OBSERVED first: a
    pause changes nothing for a job that gets no writes. A pause next: what it stops can't clear
    until it ends, so a reason that is only the pause never reads as a fault."""
    refused = _refusal(j) is not None
    if j.job.mode == "observed" or _old_folder_note(j):
        return "OBSERVED"
    if paused and (refused or j.filed_at is None):
        return "PAUSED"
    if refused:
        return "BLOCKED"
    if j.filed_at:
        return None
    if _not_run_reason(j) is not None:
        return "NOT_RUN"
    return "WAITING" if _waiting(j) else None


def _file_notes(j: _JobFacts) -> list[PendingRecord]:
    return [p for p in j.pending if p.kind.startswith("file") and p.note]


def _refusal(j: _JobFacts) -> PendingRecord | None:
    return next((p for p in _file_notes(j) if OLD_FOLDER_WORDS not in (p.note or "").casefold()),
                None)


def _only_the_pause(note: str | None) -> bool:
    return PAUSED_WORDS in (note or "").casefold()


def _old_folder_note(j: _JobFacts) -> str | None:
    """A folder in NG Jobs Old gets no write and no second folder at the top: nothing to fix, as
    with a folder found at install, and Jim works it in chat as before (ruling 12d)."""
    return next((p.note for p in _file_notes(j) if OLD_FOLDER_WORDS in (p.note or "").casefold()),
                None)


def _waiting(j: _JobFacts) -> bool:
    """Resumes by itself. A card other than the fetch card waits on Jim, not on NG Runner, and
    the fetch card says it keeps trying."""
    if any(card.kind != "fetch_failed" for card in j.cards):
        return False
    return any(not p.kind.startswith("refetch") for p in j.pending) or any(
        r.state in _ROW_WAITS for r in j.rows)


def _not_run_reason(j: _JobFacts) -> str | None:
    """Why nothing more will happen by itself: a step ended badly, or a DEV item stands, with no
    work and no card left open."""
    if j.filed_at or j.cards or j.pending or {"new", "waiting_list"} & {r.state for r in j.rows}:
        return None
    if j.trial_only:
        return "its row left the Fab Schedule before NG Runner went live"
    if j.ended:
        return _plain(j.ended[-1].note) or "a step stopped before it finished"
    return "a problem is with Sulaiman" if j.dev else None


def _next_step(store: Store, j: _JobFacts, overlay: str | None, now: datetime) -> str:
    step = _step_of(store, j, overlay, now)
    return f"{TRIAL_ONLY_STEP}. {step}" if j.trial_only else step


def _step_of(store: Store, j: _JobFacts, overlay: str | None, now: datetime) -> str:
    refusal = _refusal(j)
    if overlay == "BLOCKED" and refusal is not None:
        return _blocked_step(refusal)
    if j.cards:
        return _card_step(j.cards[0])
    if j.filed_at:
        when = f"Filed {fmt_date(j.filed_at)} {fmt_clock(j.filed_at)}"
        note = filed_note_of(j.facts) if j.facts else ""
        return f"{when}, {note}" if note else when
    if overlay == "OBSERVED":
        reasons = (_plain(_old_folder_note(j)), *(_plain(r.reason) for r in j.rows))
        return next(filter(None, reasons), OBSERVED_STEP)
    if overlay == "PAUSED":
        return PAUSED_STEP
    if overlay == "NOT_RUN":
        return f"Not run: {_not_run_reason(j)}. To try again, type add job {j.job.name}"
    return _waiting_step(j, now)


def _waiting_step(j: _JobFacts, now: datetime) -> str:
    work = [p for p in j.pending if not p.kind.startswith("refetch")]
    if work:
        return _pending_step(work[0], now)
    states = {r.state for r in j.rows}
    if "waiting_list" in states:
        return LIST_STEP
    return NEW_ROW_STEP if "new" in states else NEXT_CHECK_STEP


def _pending_step(op: PendingRecord, now: datetime) -> str:
    when = _at(op.due_at, now)
    if "list" in op.kind:
        return LIST_STEP
    if op.kind == "fetch_2":
        return f"Second fetch {when}"
    if op.kind.startswith("fetch"):
        return f"Fetching again {when}" if op.attempts else f"First fetch {when}"
    if op.kind.startswith("file"):
        return f"Filing {when}"
    return f"Next step {when}"


def _at(due: datetime, now: datetime) -> str:
    if due <= now:
        return "now"
    if pacific_day(due) == pacific_day(now):
        return f"at {fmt_clock(due)}"
    return f"{fmt_day(due)} at {fmt_clock(due)}"


def _card_step(card: CardRecord) -> str:
    return f"Card {card.id}: {_question(card)}"


def _question(card: Card | CardRecord) -> str:
    """A card's question, to go on in a line: "IC changed, which render governs?"."""
    question = templates.QUESTIONS.get(card.kind, "A question for you")
    return _first_lower(question).replace(": ", ", ")


def _blocked_step(refusal: PendingRecord) -> str:
    """The refusal's reason, in its own words when they are fit to show, and the fix."""
    note = (refusal.note or "").casefold()
    fallback, fix = next(((reason, fix) for words, reason, fix in _BLOCKS
                          if any(word in note for word in words)), _BLOCK_ELSE)
    return f"Blocked: {_plain(refusal.note) or fallback}. Fix: {fix}"


def _plain(text: str | None) -> str | None:
    """Words from elsewhere, when they are fit for Jim: a sentence, not a code, on one line, that
    passes the lint. None sends the caller to its fixed words."""
    if not text:
        return None
    line = " ".join(text.split()).rstrip(".")
    return line if " " in line and not check_line(line) else None


def settled_by_filing(store: Store, item: DevRecord) -> bool:
    """A DEV item about a job's fetching or filing, on a job that has filed: nothing waits on NG
    Runner, so the night doesn't tell Jim it does (supervisor._night_tell_jim)."""
    return item.code in CLOSED_BY_FILING and filed_facts(store, item.studio_id or "") is not None


def _first_lower(text: str) -> str:
    """Lowercase its first letter, unless it starts a capitalised word like IC."""
    return text[0].lower() + text[1:] if text[1:2].islower() else text


# ----- cards and Your list


def _card_view(card: Card, now: datetime) -> CardView:
    return CardView(card.id, templates.card_subject(card), card.text, card.choices,
                    age_days(card, now))


def _your_list(facts: _Facts, *, paused: bool) -> list[tuple[datetime, ListItem]]:
    """Open cards, DEV items over 48 hours old that name a job, one line per job, and jobs BLOCKED
    for over an hour, oldest first, with when each was raised. A DEV item about no job leaves
    nothing to work in chat, so it stays with Sulaiman. Nothing pins items in P1a; a later phase's
    pins join here."""
    items = [(card.created_at, ListItem(
        f"{templates.card_subject(card)}: {_ended(_question(card))} Card {card.id}.",
        job=card.job, age_days=age_days(card, facts.now), card_id=card.id,
    )) for card in facts.cards]
    for item in facts.dev:     # what holds up new rows goes to Jim once it has stood an hour
        if item.code in _ON_YOUR_LIST and facts.now - item.at > _ALARM_COUNTS_AFTER:
            items.append((item.at, ListItem(_ended(templates.ALARMS[item.code]),
                                            age_days=(facts.now - item.at) // timedelta(days=1))))
    for sid, raised in _stuck(facts).items():
        name = facts.names[sid]
        items.append((raised, ListItem(
            _ended(templates.stuck(name)), job=name,
            age_days=(facts.now - raised) // timedelta(days=1),
        )))
    for item in facts.dev:     # all filing may be held up: it can't wait 48 hours like the rest
        if item.code == FETCH_STUCK and item.studio_id in facts.names:
            name = facts.names[item.studio_id]
            items.append((item.at, ListItem(
                _ended(templates.fetch_stuck(name)), job=name,
                age_days=(facts.now - item.at) // timedelta(days=1),
            )))
    for j in facts.jobs:
        blocked = _blocked_item(j, facts.now, paused=paused)
        if blocked is not None:
            items.append(blocked)
    items += [_do_not_cut_item(nest, facts) for nest in _do_not_cuts(facts)]
    items += [_save_nest_item(nest, facts) for nest in facts.nests if nest.state == "save_nest"]
    return sorted(items, key=lambda item: item[0])


def _do_not_cuts(facts: _Facts) -> list[NestRecord]:
    return [nest for nest in facts.nests if nest.state == "do_not_cut"]


def _save_nest_item(nest: NestRecord, facts: _Facts) -> tuple[datetime, ListItem]:
    return nest.updated_at, ListItem(
        _ended(_fit(templates.open_save_nest(nest.folder, nest.material),
                    "A nest needs saving in the Optimizer; see the board")),
        job=nest.folder, age_days=(facts.now - nest.updated_at) // timedelta(days=1))


def _do_not_cut_item(nest: NestRecord, facts: _Facts) -> tuple[datetime, ListItem]:
    raised = nest.posted_at or nest.updated_at
    return raised, ListItem(
        _ended(_fit(templates.open_do_not_cut(nest.folder, nest.material, reasons=nest.reasons,
                                              trial=nest.trial),
                    "A material is DO NOT CUT; see the board")),
        job=nest.folder, age_days=(facts.now - raised) // timedelta(days=1),
        file=None if facts.ng_jobs is None else facts.ng_jobs / nest.folder / nest.material)


def _blocked_item(j: _JobFacts, now: datetime, *, paused: bool) -> tuple[datetime, ListItem] | None:
    """A job BLOCKED for over an hour, so the tray goes amber: silence is never success. A pause
    doesn't count, nor a refusal only the pause caused: the tray is grey then, or it clears by
    itself. A refusal with no event to date it can't be shown to be recent, so it counts."""
    refusal = _refusal(j)
    if (refusal is None or _overlay(j, paused=paused) != "BLOCKED"
            or _only_the_pause(refusal.note)):
        return None
    since = j.blocked_since or now
    if j.blocked_since is not None and now - since <= _BLOCKED_COUNTS_AFTER:
        return None
    return since, ListItem(f"{j.job.name}: {_ended(_blocked_step(refusal))}", job=j.job.name,
                           age_days=(now - since) // timedelta(days=1))


def _stuck(facts: _Facts) -> dict[str, datetime]:
    """The jobs with an open DEV item over 48 hours old, and when the oldest was raised. An item
    about a job's fetching says nothing of a job that has filed, and amber that never clears teaches
    people to ignore amber."""
    stuck: dict[str, datetime] = {}
    filed = _filed_ids(facts)
    for item in facts.dev:  # oldest first, so the first per job is its oldest
        if (item.studio_id and item.code != FETCH_STUCK and facts.now - item.at > _DEV_TOLD_AFTER
                and not _settled(item, filed)):
            stuck.setdefault(item.studio_id, item.at)
    return stuck


def _filed_ids(facts: _Facts) -> set[str]:
    return {j.job.studio_id for j in facts.jobs if j.filed_at}


def _settled(item: DevRecord, filed: set[str]) -> bool:
    return item.code in CLOSED_BY_FILING and item.studio_id in filed


def _ended(text: str) -> str:
    return text if text.endswith(("?", ".", "!")) else f"{text}."


# ----- the tray


def tray_state(
    store: Store,
    settings: Settings | None,
    clock: Clock,
    *,
    unwell: bool,
    paused: bool,
    shadow: bool,
) -> TrayState:
    """The tray: red for DO NOT CUT, whatever else stands, and for a posted NOT CHECKED once a
    toolkit snapshot is adopted; grey when paused; red when NG Runner is unwell; amber with the count
    of Your list; green otherwise. A pause stops filing and no guard, so it hides neither. unwell is
    what only the supervisor can see. No receipt for 5 minutes and an urgent DEV item are in the
    store, so they count whatever it passes. An unreadable store is the supervisor's to show, as no
    view can be read then."""
    return _tray(_read(store, clock.now()), unwell=unwell, paused=paused, shadow=shadow)


def _tray(facts: _Facts, *, unwell: bool, paused: bool, shadow: bool) -> TrayState:
    count = len(_your_list(facts, paused=paused))
    colour, words = _tray_words(facts, count, unwell=unwell, paused=paused)
    return TrayState(colour, count, tray_tooltip(words, shadow=shadow))


def _tray_words(facts: _Facts, count: int, *, unwell: bool, paused: bool) -> tuple[str, str]:
    do_not_cut = _do_not_cuts(facts)
    if do_not_cut:
        return "red", _do_not_cut_words(do_not_cut)
    unchecked = _posted_not_checked(facts)
    if unchecked and facts.toolkit_adopted:
        return "red", _not_checked_words(unchecked)
    if paused:
        return "grey", _ended(templates.ALARMS["paused"])
    if facts.overdue:
        return "red", _ended(templates.ALARMS["no_receipts"])
    if any(item.urgent for item in facts.dev):
        return "red", "NG Runner can't carry on until Sulaiman acts; the board still shows all."
    if unwell:
        return "red", "NG Runner is not well; the Health page on the board says why."
    alarm = next((templates.ALARMS[code] for code in _TRAY_SAYS if code in facts.standing), None)
    if count:
        words = f"{count} item is on Your list." if count == 1 else (
            f"{count} items are on Your list.")
        return "amber", f"{words} {_ended(alarm)}" if alarm else words
    if alarm:
        return "amber", _ended(alarm)
    return "green", "NG Runner is running, and nothing is waiting on you."


def _do_not_cut_words(nests: list[NestRecord]) -> str:
    if len(nests) == 1:
        return _ended(_fit(f"DO NOT CUT {nests[0].folder}"
                           f" {templates.material_words(nests[0].material)}",
                           "DO NOT CUT: see the board"))
    return f"DO NOT CUT: {len(nests)} materials; see the board."


def _not_checked_words(nests: list[NestRecord]) -> str:
    return f"NOT CHECKED: {len(nests)} posted {'material' if len(nests) == 1 else 'materials'}."


def _posted_not_checked(facts: _Facts) -> list[NestRecord]:
    """What was posted since install and nothing has checked, newest first. An old job's NOT CHECKED
    has no time and says nothing new."""
    posted = [nest for nest in facts.nests
              if nest.state == "not_checked" and nest.posted_at is not None]
    return sorted(posted, key=lambda nest: nest.posted_at, reverse=True)


# ----- alarms: the same ones on the board, the digest and DEV


def alarms(store: Store, clock: Clock, *, paused: bool | None = None) -> tuple[str, ...]:
    """The alarms that stand, in templates.ALARMS' words and order. The store can say by itself
    when NG Runner is paused (paused None reads it), when a message has had no receipt and when
    the job list is too old, and usually when the export is old or the toasts are off; any other
    stands while its DEV item is open, by its code."""
    return _alarms(store, _read(store, clock.now()), paused=paused)


def _alarms(store: Store, facts: _Facts, *, paused: bool | None) -> tuple[str, ...]:
    codes = set(facts.standing)
    if is_paused(store) if paused is None else paused:
        codes.add("paused")
    if facts.overdue:
        codes.add("no_receipts")
    return tuple(words for code, words in templates.ALARMS.items() if code in codes)


def _standing(store: Store, now: datetime, dev: Iterable[DevRecord]) -> frozenset[str]:
    """The alarms the store says stand, apart from a pause and missing receipts, which every caller
    has to hand. What the state table can say decides, whatever the DEV lane says."""
    known = {"job_list_stale": _list_is_stale(store, now)}
    exported = get_state_time(store, EXPORTED_AT_KEY)
    if exported is not None:
        known["fab_export_stale"] = now - exported > _EXPORT_STALE_AFTER
    toasts = get_state(store, TOASTS_KEY)
    if toasts in ("on", "off"):
        known["toasts_off"] = toasts == "off"
    codes = {item.code for item in dev} - set(known) - {"paused", "no_receipts"}
    return frozenset(codes | {code for code, stands in known.items() if stands})


def _list_is_stale(store: Store, now: datetime) -> bool:
    """Older than the oldest waiting row, and over an hour old (or none, with a row waiting that
    long)."""
    waiting = list_fab_rows(store, states=("waiting_list",))
    if not waiting:
        return False
    oldest = min(row.text_changed_at for row in waiting)
    state = get_list_state(store)
    synced = None if state is None else state.synced_at
    if synced is None:
        return now - oldest > _LIST_STALE_AFTER
    return synced < oldest and now - synced > _LIST_STALE_AFTER


# ----- the digest


def digest_view(store: Store, settings: Settings | None, clock: Clock) -> DigestView:
    """What the 6:30 AM digest is made from (digest.compose arranges it). Once it is sent, the
    supervisor adds view.now to each of digest.reminders(view)'s reminded_at. A digest sent late:
    it adds templates.missed_digest to view.owner with dataclasses.replace."""
    now = clock.now()
    facts = _read(store, now, settings)
    day, since, paused = pacific_day(now), _last_digest(store), is_paused(store)
    items = _your_list(facts, paused=paused)
    return DigestView(
        now=now,
        owner=(*_nest_lines(facts), *_installing_soon(facts, day),
               *_alarms(store, facts, paused=None), *_told_stuck(facts, since)),
        jobs=_touched(store, facts, since, paused=paused, day=day),
        cards=facts.cards,
        open_items=len(items),
        oldest_item=items[0][0] if items else None,
    )


def _nest_lines(facts: _Facts) -> list[str]:
    """Every DO NOT CUT, then the newest posted NOT CHECKED. Until the toolkit is here every post
    reads so for as long as its job is in NG Jobs, so the list is held to a few and a count, or it
    would bury the rest of the line."""
    lines = [_fit(templates.open_do_not_cut(nest.folder, nest.material, reasons=nest.reasons,
                                            trial=nest.trial),
                  "A material is DO NOT CUT; see the board") for nest in _do_not_cuts(facts)]
    unchecked = _posted_not_checked(facts)
    lines += [_fit(templates.open_not_checked(nest.folder, nest.material, posted=nest.posted_at,
                                              now=facts.now),
                   templates.more_not_checked(1)) for nest in unchecked[:_NOT_CHECKED_SHOWN]]
    more = len(unchecked) - _NOT_CHECKED_SHOWN
    return [*lines, templates.more_not_checked(more)] if more > 0 else lines


def _told_stuck(facts: _Facts, since: datetime | None) -> list[str]:
    """Each DEV item the night's work marked told since the last digest: the digest is what tells
    Jim (the only message he gets in P1a), once, with the manual fallback. One not marked yet
    waits for the next digest, and is on Your list meanwhile."""
    filed = _filed_ids(facts)
    names = dict.fromkeys(facts.names[i.studio_id] for i in facts.dev if i.studio_id
                          and i.told_jim_at is not None and _since(i.told_jim_at, since)
                          and not _settled(i, filed))
    return [_fit(templates.stuck(name), "A job has waited on NG Runner for over 2 days; see Your"
                                        " list on the board") for name in names]


def _installing_soon(facts: _Facts, day: date) -> list[str]:
    """Jobs and new rows installing within 5 days with no IC summary filed yet, soonest first.
    A row from before NG Runner isn't on the board, so it can't ask for anything here either, nor
    can one Jim has said is none of the jobs."""
    soon = [(_install(*_install_cells(j)), j.job.name) for j in facts.jobs if not j.filed_at]
    soon += [(_install(r.install, r.first_seen), _quoted_row(r.text)) for r in facts.free_rows
             if r.state != "none" or _row_cards(r, facts)]     # "None of these" was his answer
    dated = [(when, name) for when, name in soon if isinstance(when, date)
             and 0 <= (when - day).days <= _INSTALL_SOON_DAYS]
    return [_fit(templates.install_soon(name, when),
                 f"A job installs {fmt_day(when)} and has no IC summary filed yet; see the board")
            for when, name in sorted(dated, key=lambda pair: pair[0])]


def _last_digest(store: Store) -> datetime | None:
    """When the last digest that wasn't refused was made: what "since the last digest" means."""
    return max((m.created_at for m in list_outbox(store)
                if (m.ref or "").startswith(DIGEST_REF) and m.state != "failed"), default=None)


def _touched(store: Store, facts: _Facts, since: datetime | None, *, paused: bool, day: date
             ) -> tuple[JobLine, ...]:
    """One line per job touched since the last digest, first touched first, then one per new
    which-job card. A card due back today comes back by itself (digest.compose), so a row's
    isn't said twice."""
    ids = [e.studio_id for e in facts.events if _touches(e, since)]
    ids += [c.studio_id for c in facts.records if c.studio_id and _since(c.created_at, since)]
    jobs = {j.job.studio_id: j for j in facts.jobs}
    lines = [_job_line(store, jobs[sid], facts.now, paused=paused, day=day)
             for sid in dict.fromkeys(ids) if sid in jobs]
    lines += [_fit_line(templates.card_subject(card), _card_digest(card), day=day)
              for card in facts.cards
              if card.job is None and _since(card.created_at, since)
              and reminder_due(card, facts.now) is None]
    return tuple(lines)


def _touches(event: EventRecord, since: datetime | None) -> bool:
    return (event.studio_id is not None and _since(event.at, since)
            and event.kind not in _TOLD_ELSEWHERE
            and (event.lane == "ACT"
                 or (event.lane == "LOG" and event.kind in (FILED_EVENT, BLOCKED_EVENT))))


def _since(when: datetime, since: datetime | None) -> bool:
    return since is None or when >= since


def _job_line(store: Store, j: _JobFacts, now: datetime, *, paused: bool, day: date) -> JobLine:
    """What happened, and for a filed job its takeoff. A takeoff clause the lint would stop costs
    only itself: the line goes out without it."""
    text = _digest_text(_next_step(store, j, _overlay(j, paused=paused), now))
    record = j.takeoff
    if j.filed_at and record is not None and record.state == "made" and record.result is not None:
        clause = templates.takeoff_digest(record.result["buckets"], complete=record.complete,
                                          could_not_verify=record.could_not_verify,
                                          chat=j.chats_list)
        line = _fit_line(j.job.name, f"{text.rstrip('.')}, {clause}", day=day)
        if line.job == j.job.name and line.text != _CHANGED:
            return line
    return _fit_line(j.job.name, text, day=day)


_CARD_STEP = re.compile(r"Card (?P<id>\S+): (?P<question>.*)")
_CHANGED = "changed; see the board"     # a job's line that didn't pass the lint


def _digest_text(step: str) -> str:
    """A next step as it follows "<job>: " in the digest: "closet or garage? Card FAK-1"."""
    card = _CARD_STEP.fullmatch(step)
    if card:
        return f"{_ended(card['question'])} Card {card['id']}"
    return _first_lower(step)


def _card_digest(card: Card) -> str:
    return f"{_ended(_question(card))} Card {card.id}"


def _fit_line(job: str, text: str, *, day: date) -> JobLine:
    """A digest line that passes the lint on its own, so one odd job name or reason costs only its
    own line and not the whole digest: its fixed words, then a line about NG Runner itself."""
    for line in (JobLine(job, text), JobLine(job, _CHANGED)):
        item = f"1. {line.job}: {_ended(line.text)}"
        message = Message("digest", templates.digest_title(day), (item,))
        if not lint(message):
            return line
    return JobLine(ABOUT_ITSELF, "a job changed, and its line did not pass the message check;"
                                 " see the board")


def _fit(text: str, fallback: str) -> str:
    return fallback if check_line(text) else text


def _quoted_row(text: str) -> str:
    """Fab row "<what she typed>", as templates quotes it, for the lint to know her words."""
    typed = " ".join(text.split()).replace('"', "”")
    return f'Fab row "{typed}"'


# ----- history


def history(store: Store, *, limit: int = 200) -> list[EventView]:
    """Recent events, newest first, without the DEV lane: Jim never sees that one (design 2)."""
    names = {job.studio_id: job.name for job in list_jobs(store)}
    shown = [event for event in reversed(list_events(store)) if event.lane != "DEV"]
    return [EventView(e.at, names.get(e.studio_id or ""), e.text, e.lane)
            for e in shown[:max(limit, 0)]]
