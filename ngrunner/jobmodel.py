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
               "filed" says the same. Only the ACT lane and "filed" and "blocked" events count as
               touching a job for the digest, so a nightly re-fetch that found nothing keeps a
               night quiet.
    pending    "list..." waits on the job list; "fetch_2" is the second fetch; other "fetch..."
               kinds are the first or a retry; "refetch..." (filed jobs) doesn't show. A "file..."
               operation with a note is a refused filing: BLOCKED, with the refusal as its note,
               unless the note says "Old folder": then there is nothing to fix, so OBSERVED. One
               that ended "failed" or "expired", with nothing else open, is NOT_RUN.
    cards      Open ones, oldest first. An answer is the pipeline's to act on.
    dev        Open items, by job. A code in templates.ALARMS is that alarm standing, so close it
               when the alarm clears. urgent turns the tray red. told_jim_at, set by the night's
               work, puts the item on the next digest's For you line, so it is told once.
    outbox     ref "digest:<day>" marks a digest, whose time is what "since the last digest"
               means. Written over 5 minutes ago with no receipt turns the tray red.
    state      "paused", as interlock.is_paused reads it, so the digest and the filing never
               disagree. "fab_exported_at" (a time) and "toast_setting" ("on" or "off"), when
               set, decide their alarms whatever the DEV lane says.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any

from ngrunner.board.views import (
    BoardView, CardView, EventView, HealthView, JobRow, ListItem, Tick, TrayState, tray_tooltip,
)
from ngrunner.cards import Card, age_days, card_from_record, reminder_due
from ngrunner.clock import Clock, fmt_clock, fmt_date, fmt_day, pacific_day
from ngrunner.config import Settings
from ngrunner.digest import DigestView, JobLine
from ngrunner.fab import install_date
from ngrunner.interlock import is_paused
from ngrunner.messages import Message, templates
from ngrunner.messages.lint import ABOUT_ITSELF, check_line, lint
from ngrunner.store import (
    CardRecord, DevRecord, EventRecord, FabRowRecord, JobRecord, PendingRecord, Store,
    get_list_state, get_state, get_state_time, governing_render, list_cards, list_dev, list_events,
    list_fab_rows, list_jobs, list_outbox, list_pending, list_renders, outbox_without_receipt,
)

COULD_NOT_VERIFY = "could not verify"  # an install date that doesn't read as one date for sure
FILED_EVENT = "filed"
DIGEST_REF = "digest:"                 # messages.outbox.outbox_file's ref on a digest
EXPORTED_AT_KEY = "fab_exported_at"    # state keys
TOASTS_KEY = "toast_setting"
BLOCKED_EVENT = "blocked"
OLD_FOLDER_WORDS = "old folder"        # in a refusal's note: its folder is in NG Jobs Old
_TOLD_ELSEWHERE = ("dev_told_jim",)    # the night's note of a stuck job: For you says it already

_NO_RECEIPT_FOR = timedelta(minutes=5)
_EXPORT_STALE_AFTER = timedelta(minutes=30)
_DEV_TOLD_AFTER = timedelta(hours=48)
_INSTALL_SOON_DAYS = 5
_LIST_STALE_AFTER = timedelta(hours=1)

_TICKS = (
    ("row", "On the Fab Schedule"),
    ("job", "Matched to Salesforce"),
    ("summary", "Fetched and checked twice"),
    ("filed", "Filed"),
    ("branch", "Closet or garage"),
)
_NOT_ON_THE_BOARD = ("seen_at_install", "gone")  # a row with no job in these: nothing will happen
_ROW_WAITS = ("new", "waiting_list", "matched")  # a row in these moves on by itself

PAUSED_STEP = "Paused: nothing is filed until you resume it at the PC"
LIST_STEP = "Waiting for a fresh job list"
NEW_ROW_STEP = "Waiting for the next Fab export, in case this row is still being typed"
NEXT_CHECK_STEP = "Starts at NG Runner's next check"
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


def _read(store: Store, now: datetime) -> _Facts:
    jobs, rows = list_jobs(store), list_fab_rows(store)
    names = {job.studio_id: job.name for job in jobs}
    texts = {row.id: row.text for row in rows}
    # A card about neither a job nor a row can't be shown or acted on; add_card allows one.
    records = tuple(c for c in list_cards(store) if c.studio_id or c.fab_row_id)
    dev, events = tuple(list_dev(store)), tuple(list_events(store))
    return _Facts(
        now=now,
        jobs=_job_facts(store, jobs, rows, records, dev, events),
        free_rows=tuple(r for r in rows
                        if r.studio_id is None and r.state not in _NOT_ON_THE_BOARD),
        cards=tuple(card_from_record(c, job=names.get(c.studio_id or ""),
                                     row=texts.get(c.fab_row_id or 0)) for c in records),
        records=records,
        dev=dev,
        events=events,
        names=names,
        overdue=_no_receipt(store, now),
    )


def _job_facts(
    store: Store,
    jobs: Iterable[JobRecord],
    rows: Iterable[FabRowRecord],
    cards: Iterable[CardRecord],
    dev: Iterable[DevRecord],
    events: Iterable[EventRecord],
) -> tuple[_JobFacts, ...]:
    """Grouped once, not searched per job: every page and tray update reads all the jobs, and
    jobs and their nightly re-fetches only pile up. Finished operations are left out but for
    those that ended badly, for the same reason."""
    filed = {e.studio_id: e.at for e in events if e.kind == FILED_EVENT and e.lane != "DEV"}
    rows_of, cards_of, dev_of = _by_job(rows), _by_job(cards), _by_job(dev)
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
            filed_at=filed.get(job.studio_id) or max(
                (r.updated_at for r in rows_of.get(job.studio_id, ()) if r.state == "filed"),
                default=None),
        )
        for job in jobs
    )


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
    facts = _read(store, clock.now())
    return BoardView(
        jobs=_board_rows(store, facts, paused=paused),
        your_list=tuple(item for _, item in _your_list(facts)),
        cards=tuple(_card_view(card, facts.now) for card in facts.cards),
        health=replace(health, alarms=tuple(dict.fromkeys(
            (*_alarms(store, facts, paused=paused), *health.alarms)))),
        tray=_tray(facts, unwell=False, paused=paused, shadow=shadow),
        paused=paused,
        shadow=shadow,
    )


def _board_rows(store: Store, facts: _Facts, *, paused: bool) -> tuple[JobRow, ...]:
    rows = [_job_row(store, j, facts.now, paused=paused) for j in facts.jobs]
    rows += [_free_row(row, _row_cards(row, facts), paused=paused) for row in facts.free_rows]
    return tuple(sorted(rows, key=_board_order))


def _board_order(row: JobRow) -> tuple[bool, date, str]:
    """What is still moving first, soonest install first; filed jobs after."""
    filed = any(tick.key == "filed" and tick.state == "done" for tick in row.ticks)
    day = row.install if isinstance(row.install, date) else date.max
    return filed, day, row.name.casefold()


def _job_row(store: Store, j: _JobFacts, now: datetime, *, paused: bool) -> JobRow:
    overlay = _overlay(j, paused=paused)
    return JobRow(
        studio_id=j.job.studio_id,
        name=j.job.name,
        install=_install(*_install_cells(j)),
        ticks=_job_ticks(store, j, overlay),
        next_step=_next_step(j, overlay, now),
        overlay=overlay,
    )


def _free_row(row: FabRowRecord, cards: tuple[CardRecord, ...], *, paused: bool) -> JobRow:
    """A Fab row with no job yet: a row waiting on its which-job card still shows."""
    asking = bool(cards) or row.state in ("asked", "none")
    return JobRow(
        studio_id=None,
        name=row.text,
        install=_install(row.install, row.first_seen),
        ticks=_ticks(row="done", job="blocked" if asking else "todo"),
        next_step=_free_step(row, cards, paused=paused),
        overlay="PAUSED" if paused else ("WAITING" if not asking else None),
    )


def _free_step(row: FabRowRecord, cards: tuple[CardRecord, ...], *, paused: bool) -> str:
    if cards:
        return _card_step(cards[0])
    if paused:
        return PAUSED_STEP
    if row.state == "waiting_list":
        return LIST_STEP
    if row.state == "new":
        return NEW_ROW_STEP
    return _plain(row.reason) or (NO_JOB_STEP if row.state == "none" else ASKED_STEP)


def _row_cards(row: FabRowRecord, facts: _Facts) -> tuple[CardRecord, ...]:
    return tuple(c for c in facts.records if c.fab_row_id == row.id and c.studio_id is None)


# ----- ticks


def _ticks(*, row: str = "todo", job: str = "todo", summary: str = "todo", filed: str = "todo",
           branch: str = "todo", branch_label: str | None = None) -> tuple[Tick, ...]:
    states = {"row": row, "job": job, "summary": summary, "filed": filed, "branch": branch}
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
    )


def _summary_tick(store: Store, j: _JobFacts, kinds: set[str]) -> str:
    if j.filed_at or _agreed(store, j.job.studio_id):
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
    if j.ended:
        return _plain(j.ended[-1].note) or "a step stopped before it finished"
    return "a problem is with Sulaiman" if j.dev else None


def _next_step(j: _JobFacts, overlay: str | None, now: datetime) -> str:
    refusal = _refusal(j)
    if overlay == "BLOCKED" and refusal is not None:
        return _blocked_step(refusal)
    if j.cards:
        return _card_step(j.cards[0])
    if j.filed_at:
        return f"Filed {fmt_date(j.filed_at)} {fmt_clock(j.filed_at)}"
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
    """A card's question, to go on in a line: "IC changed, which render governs?". The remake card
    is a "row changed" card with its own choices (templates.remake_card)."""
    if tuple(card.choices) == templates.REMAKE_CHOICES:
        return "is this a remake?"
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


def _first_lower(text: str) -> str:
    """Lowercase its first letter, unless it starts a capitalised word like IC."""
    return text[0].lower() + text[1:] if text[1:2].islower() else text


# ----- cards and Your list


def _card_view(card: Card, now: datetime) -> CardView:
    return CardView(card.id, templates.card_subject(card), card.text, card.choices,
                    age_days(card, now))


def _your_list(facts: _Facts) -> list[tuple[datetime, ListItem]]:
    """Open cards, and DEV items over 48 hours old that name a job, one line per job, oldest
    first, with when each was raised. A DEV item about no job leaves nothing to work in chat, so
    it stays with Sulaiman. Nothing pins items in P1a; a later phase's pins join here."""
    items = [(card.created_at, ListItem(
        f"{templates.card_subject(card)}: {_ended(_question(card))} Card {card.id}.",
        job=card.job, age_days=age_days(card, facts.now), card_id=card.id,
    )) for card in facts.cards]
    for sid, raised in _stuck(facts).items():
        name = facts.names[sid]
        items.append((raised, ListItem(
            _ended(templates.stuck(name)), job=name,
            age_days=(facts.now - raised) // timedelta(days=1),
        )))
    return sorted(items, key=lambda item: item[0])


def _stuck(facts: _Facts) -> dict[str, datetime]:
    """The jobs with an open DEV item over 48 hours old, and when the oldest was raised."""
    stuck: dict[str, datetime] = {}
    for item in facts.dev:  # oldest first, so the first per job is its oldest
        if item.studio_id and facts.now - item.at > _DEV_TOLD_AFTER:
            stuck.setdefault(item.studio_id, item.at)
    return stuck


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
    """The tray: grey when paused; red when NG Runner is unwell; amber with the count of Your
    list; green otherwise. unwell is what only the supervisor can see. No receipt for 5 minutes
    and an urgent DEV item are in the store, so they count whatever it passes. An unreadable
    store is the supervisor's to show, as no view can be read then."""
    return _tray(_read(store, clock.now()), unwell=unwell, paused=paused, shadow=shadow)


def _tray(facts: _Facts, *, unwell: bool, paused: bool, shadow: bool) -> TrayState:
    count = len(_your_list(facts))
    colour, words = _tray_words(facts, count, unwell=unwell, paused=paused)
    return TrayState(colour, count, tray_tooltip(words, shadow=shadow))


def _tray_words(facts: _Facts, count: int, *, unwell: bool, paused: bool) -> tuple[str, str]:
    if paused:
        return "grey", _ended(templates.ALARMS["paused"])
    if facts.overdue:
        return "red", _ended(templates.ALARMS["no_receipts"])
    if any(item.urgent for item in facts.dev):
        return "red", "NG Runner can't carry on until Sulaiman acts; the board still shows all."
    if unwell:
        return "red", "NG Runner is not well; the Health page on the board says why."
    if count:
        return "amber", f"{count} item is on Your list." if count == 1 else (
            f"{count} items are on Your list.")
    return "green", "NG Runner is running, and nothing is waiting on you."


# ----- alarms: the same ones on the board, the digest and DEV


def alarms(store: Store, clock: Clock, *, paused: bool | None = None) -> tuple[str, ...]:
    """The alarms that stand, in templates.ALARMS' words and order. The store can say by itself
    when NG Runner is paused (paused None reads it), when a message has had no receipt and when
    the job list is too old, and usually when the export is old or the toasts are off; any other
    stands while its DEV item is open, by its code."""
    return _alarms(store, _read(store, clock.now()), paused=paused)


def _alarms(store: Store, facts: _Facts, *, paused: bool | None) -> tuple[str, ...]:
    known = {
        "paused": is_paused(store) if paused is None else paused,
        "no_receipts": facts.overdue,
        "job_list_stale": _list_is_stale(store, facts.now),
    }
    exported = get_state_time(store, EXPORTED_AT_KEY)
    if exported is not None:
        known["fab_export_stale"] = facts.now - exported > _EXPORT_STALE_AFTER
    toasts = get_state(store, TOASTS_KEY)
    if toasts in ("on", "off"):
        known["toasts_off"] = toasts == "off"
    codes = {item.code for item in facts.dev} - set(known)
    codes |= {code for code, stands in known.items() if stands}
    return tuple(words for code, words in templates.ALARMS.items() if code in codes)


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
    facts = _read(store, now)
    day, since = pacific_day(now), _last_digest(store)
    items = _your_list(facts)
    return DigestView(
        now=now,
        owner=(*_installing_soon(facts, day), *_alarms(store, facts, paused=None),
               *_told_stuck(facts, since)),
        jobs=_touched(facts, since, paused=is_paused(store), day=day),
        cards=facts.cards,
        open_items=len(items),
        oldest_item=items[0][0] if items else None,
    )


def _told_stuck(facts: _Facts, since: datetime | None) -> list[str]:
    """Each DEV item the night's work marked told since the last digest: the digest is what tells
    Jim (the only message he gets in P1a), once, with the manual fallback. One not marked yet
    waits for the next digest, and is on Your list meanwhile."""
    names = dict.fromkeys(facts.names[i.studio_id] for i in facts.dev if i.studio_id
                          and i.told_jim_at is not None and _since(i.told_jim_at, since))
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


def _touched(facts: _Facts, since: datetime | None, *, paused: bool, day: date
             ) -> tuple[JobLine, ...]:
    """One line per job touched since the last digest, first touched first, then one per new
    which-job card. A card due back today comes back by itself (digest.compose), so a row's
    isn't said twice."""
    ids = [e.studio_id for e in facts.events if _touches(e, since)]
    ids += [c.studio_id for c in facts.records if c.studio_id and _since(c.created_at, since)]
    jobs = {j.job.studio_id: j for j in facts.jobs}
    lines = [_job_line(jobs[sid], facts.now, paused=paused, day=day)
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


def _job_line(j: _JobFacts, now: datetime, *, paused: bool, day: date) -> JobLine:
    return _fit_line(j.job.name, _digest_text(_next_step(j, _overlay(j, paused=paused), now)),
                     day=day)


_CARD_STEP = re.compile(r"Card (?P<id>\S+): (?P<question>.*)")


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
    for line in (JobLine(job, text), JobLine(job, "changed; see the board")):
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
