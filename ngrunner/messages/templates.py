"""
The words of every message, in one place (design section 4; build spec 6.5; P1b build spec 5.6).

Each template is a function of plain facts that returns a Message, or, for a question to Jim, a
CardSpec that becomes a card and then a Message through card_message(). Wording lives only
here, so the lint can hold all of it to Jim's format at test time. At runtime, vetted() checks
each message once more: one that fails (a reason from elsewhere carried a path, say) goes out as
its kind's fixed template instead, and the problems are returned for a DEV item, so nothing
waits on Sulaiman.

The nest guard's board notes live here too, since a DO NOT CUT says the same words on the board and
in its message. No message ever says OK to cut (the lint refuses it): the strongest thing NG Runner
can say is "Nest checked", and until the toolkit is here it can't say even that.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from fractions import Fraction
from typing import Any

from ngrunner.cards import Card, CardSpec, Command
from ngrunner.clock import fmt_clock, fmt_day, pacific_day
from ngrunner.convert import rules as takeoff_rules
from ngrunner.match import Opportunity
from ngrunner.messages import Message
from ngrunner.messages.lint import ABOUT_ITSELF, CLEAN, check_line, lint, redact, subject_line

NONE_OF_THESE = "None of these"

# Each card's short question: its message title after the ID and the job (or Fab row).
QUESTIONS = {
    "which_job": "Which job is this row?",
    "closet_or_garage": "Closet or garage?",
    "fetch_failed": "Could not get a complete IC summary",
    "ic_changed": "IC changed: which render governs?",
    "row_changed": "Row changed: which job?",
    "remake": "Is this a remake?",
    "design_mismatch": "The report and Studio's proposal differ",
    "sheet_size": "A program is past its sheet size",
    "fix": "A fix is ready: approve it?",
    "fix_undone": "Mozaik undid a fix: redo it?",
}


# ----- Jim's 6:30 AM digest (digest.compose decides what goes in)


def digest_title(day: date) -> str:
    """NG Runner · Fri 10/30"""
    return f"NG Runner · {fmt_day(day)}"


def digest(day: date, *, for_you: Sequence[str] = (), jobs: Sequence[tuple[str, str]] = (),
           cards: Sequence[tuple[Card, int]] = (), open_items: int = 0,
           oldest_days: int | None = None) -> Message:
    """The digest: the owner items on one For you line, one numbered line per job touched
    ((job, what happened)), then the open cards back today ((card, days open)), then Your
    list."""
    lines = [f"For you: {' '.join(_sentence(item) for item in for_you)}"] if for_you else []
    items = [f"{job}: {_ended(text)}" for job, text in jobs]
    items += [f"Card {card.id}, open {_count(age, 'day')}: {_card_back(card)}"
              for card, age in cards]
    lines += _numbered(items)
    lines.append(f"Your list: {_your_list(open_items, oldest_days)}.")
    return Message("digest", digest_title(day), tuple(lines))


def quiet_digest(day: date, *, open_items: int = 0, oldest_days: int | None = None) -> Message:
    """A quiet night's digest: one line."""
    if open_items:
        your_list = _your_list(open_items, oldest_days)
        line = f"Nothing new since the last list. Your list: {your_list}."
    else:
        line = "Nothing new since the last list, and nothing is waiting on you."
    return Message("quiet_digest", digest_title(day), (line,))


# What goes on the For you line, each one plain sentence (digest.DigestView.owner).

ALARMS = {   # P1a alarms, for the For you line (design 4, "Silence and failure")
    "fab_export_stale": "The Fab Schedule export is over 30 minutes old, so new rows are not"
                        " being seen",
    "job_list_stale": "The job list is over an hour old and older than the oldest waiting row,"
                      " so that row can't be matched yet",
    "ng_jobs_unreachable": "NG Jobs can't be read, so new rows wait and nothing is filed; check"
                           " that this PC can reach the NG Jobs folder",
    "board_failed": "The board could not start, so cards can't be answered there; NG Runner tries"
                    " again every minute",
    "tray_failed": "The tray icon could not start, so NG Runner shows no colour at the PC; it tries"
                   " again every minute",
    "toasts_off": "Windows notifications are off for NG Runner, so its pop-ups don't show",
    "paused": "NG Runner is paused, so nothing is filed until you resume it at the PC",
    "no_receipts": "Messages are not being confirmed sent, so some may not reach you; the board"
                   " still shows everything",
}


def install_soon(job: str, install: date) -> str:
    """A job installing within 5 days with no IC summary filed yet."""
    return f"{job} installs {fmt_day(install)} and has no IC summary filed yet"


def stuck(job: str) -> str:
    """A DEV item older than 48 hours, with the manual fallback (design 4)."""
    return f"{job} has waited on NG Runner for over 2 days; work it in chat as before"


def fetch_stuck(job: str) -> str:
    """Your list, at once: a fetch of this job hung, and if it can hang so can the next, so all
    filing may be held up. NG Runner has freed its place and goes on trying."""
    return f"{job}: a fetch ran for over 15 minutes and was given up; NG Runner keeps trying"


def missed_digest(*, due: datetime, sent: datetime) -> str:
    """A digest sent late, named (integration spec section 6: missed windows catch up, by name)."""
    return f"Missed the {fmt_clock(due)} digest: the PC was off. Sent at {fmt_clock(sent)}"


def _your_list(open_items: int, oldest_days: int | None) -> str:
    if not open_items:
        return "nothing open"
    if oldest_days is None:
        return f"{open_items} open"
    oldest = "under a day" if oldest_days < 1 else _count(oldest_days, "day")
    return f"{open_items} open, oldest {oldest}"


_CARD_BACK = {   # an open card, as it comes back in the digest
    "which_job": "which job is {row}?",
    "closet_or_garage": "is {job} a closet or a garage?",
    "fetch_failed": "still no complete IC summary for {job}.",
    "ic_changed": "the IC summary of {job} changed; which render governs?",
    "row_changed": "{row} changed after {job} was filed; which job is it?",
    "remake": "is {row} a remake of {job}?",
    "design_mismatch": "the report of {job} and Studio's proposal still list different designs.",
    "sheet_size": "{job} still has a program past its sheet size; did you add that sheet on"
                  " purpose?",
    "fix": "a fix for {job} waits for your OK.",
    "fix_undone": "Mozaik undid a fix of {job}; redo it?",
}


_CARD_BACK_WITHHELD = "could not be shown in this list; it is on the board."


def _card_back(card: Card) -> str:
    """What follows "Card <ID>, open <age>: " in the digest. A row or a job the lint would stop
    (an address, a phone number, a path, a hash, a key) would cost Jim the whole list through
    vetted(), so that one card is shown by its ID alone, in fixed words."""
    line = _CARD_BACK[card.kind].format(row=_fab_row(card.row or ""), job=card.job)
    return _CARD_BACK_WITHHELD if check_line(line) else line


# ----- Barbara's emails


def filed(job: str, *, row: str, designer: str = "", install: date | None = None,
          garage: bool = False, designs: int | None = None, pages: int | None = None) -> Message:
    """Barbara's "Filed as <Job>: 3 designs, 19 pages": her row is now that job, and its IC summary
    is filed. The counts let a person who knows the job spot a missing room at no cost, and are
    left out when they can't be read, never guessed. Counts only, never a design's name."""
    branch = " (garage)" if garage else ""
    counts = filed_counts(designs, pages)
    said = f": {counts}" if counts else ""
    return Message("filed", f"Filed as {job}{said}",
                   (f"{_row(row, designer, install)} is filed as {job}{branch}{said}.",))


def filed_counts(designs: int | None, pages: int | None) -> str:
    """A filed job's counts, as every place shows them: "3 designs, 19 pages". Empty when either
    can't be read, since a count that is a guess is worse than none."""
    if designs is None or not pages:
        return ""
    return f"{_count(designs, 'design')}, {_count(pages, 'page')}"


def which_job_email(row: str, *, designer: str = "", install: date | None = None,
                    candidates: Sequence[Opportunity] = ()) -> Message:
    """Barbara's "Which job is this row?": she corrects the name in her own cell. The subject
    names the row, so two rows' emails never thread together; a very long row is cut there, and
    kept whole in the body."""
    about = _row(row, designer, install)
    subject = subject_line(f"Which job is {_fab_row(row)}?")
    if not candidates:
        return Message("which_job_email", subject, (
            f"{about} matches no job in Salesforce, so nothing is filed yet. Type the full job"
            " name in the row, as Salesforce spells it.",))
    return Message("which_job_email", subject, (
        f"{about} could be more than one job, so nothing is filed yet. Type the full job name"
        " in the row; these jobs match:",
        *_numbered(_candidate(o) for o in candidates),
    ))


# ----- Jim's cards: the questions that need his judgment


def which_job_card(row: str, *, designer: str = "", install: date | None = None,
                   candidates: Sequence[Opportunity] = ()) -> CardSpec:
    """Jim's "Which job is this row?", beside Barbara's email. Its choices are the candidates
    in order, then None of these, and choice_ids holds each candidate's Studio project ID, so a
    later answer links the job it meant. Nothing is filed until Jim or Barbara settles it."""
    about = _row(row, designer, install)
    if candidates:
        text = (f"{about} could be more than one job; nothing is filed until you pick one, or"
                " Barbara types the full job name in the row.")
    else:
        text = (f"{about} matches no job in Salesforce; nothing is filed until Barbara types the"
                ' full job name in the row, or you add it with "add job" and its name.')
    return CardSpec("which_job", job=None, row=row, text=text,
                    choices=(*_labels(candidates), NONE_OF_THESE),
                    choice_ids=(*(o.studio_id for o in candidates), None))


def closet_or_garage_card(job: str, *, reason: str) -> CardSpec:
    """Jim's "Closet or garage?": the designs don't settle the branch, so only this job
    waits. reason says why, in plain words (from intake.branch)."""
    why = _sentence(reason) if reason.strip() else "Its designs don't settle closet or garage."
    return CardSpec("closet_or_garage", job=job, row=None, choices=("Closet", "Garage"),
                    text=f"{why} Nothing is filed for {job} until you choose; other jobs carry"
                         " on.")


def fetch_failed_card(job: str, *, since: datetime, reason: str) -> CardSpec:
    """Jim's "Could not get a complete IC summary for <Job>": a day of fetches never gave a
    complete pair (design 3.4: fetch, 24 h, then a card). reason is the last fetch's, in plain
    words. Keeping on trying is what happens meanwhile."""
    return CardSpec(
        "fetch_failed", job=job, row=None, choices=("Keep trying", "Stop trying"),
        default_choice="Keep trying",
        text=f"Could not get a complete IC summary for {job} since {_when(since)}:"
             f" {_clause(reason) or 'Studio gave no reason'}. NG Runner keeps trying until you"
             " choose.",
    )


def design_counts(report: int, proposal: int, *, differ: bool = True) -> str:
    """What the report and Studio's proposal list, in counts, never a design's name (decision 10).
    Equal counts that differ mean the names differ, which must be said or it reads as agreement."""
    same = ", with different names" if differ and report == proposal else ""
    return (f"the report lists {_count(report, 'design')} and Studio's proposal lists"
            f" {proposal}{same}")


def ic_changed_card(job: str, *, filed_at: datetime, filed_pages: int, new_at: datetime,
                    new_pages: int, designs: str = "", changed: str = "") -> CardSpec:
    """Jim's "IC changed: which render governs?": two agreeing fetches differ from the filed
    IC summary. Nothing is re-filed by itself; keeping the filed one is what happens meanwhile.
    designs: what Studio's proposal said of the new one, as design_counts gives it, since Jim
    can't see a lost room from a page count. changed: what differs, as report_diff words it, since
    a catalog update can raise this on many jobs at once."""
    also = f" {designs};" if designs else ""
    differs = f" {changed}" if changed else ""
    return CardSpec(
        "ic_changed", job=job, row=None, choices=("Keep the filed one", "File the new one"),
        default_choice="Keep the filed one",
        text=f"The IC summary fetched {_when(new_at)} ({_count(new_pages, 'page')}) differs from"
             f" the one filed {fmt_day(filed_at)} ({_count(filed_pages, 'page')});{also} nothing"
             f" is re-filed until you choose.{differs}",
    )


def row_changed_card(job: str, *, was: str, now_reads: str,
                     candidates: Sequence[Opportunity] = ()) -> CardSpec:
    """Jim's "Row changed: which job?": Barbara edited a row after it was filed, and it now
    reads as another job. Its choices: Keep <job>, then each candidate not named job, in order,
    then None of these; choice_ids holds each candidate's Studio project ID. Nothing new is filed
    until Jim says which it is."""
    others = [o for o in candidates if o.name != job]
    return CardSpec(
        "row_changed", job=job, row=now_reads,
        choices=(f"Keep {job}", *_labels(others), NONE_OF_THESE),
        choice_ids=(None, *(o.studio_id for o in others), None),
        text=f'{_fab_row(was)} now reads "{_typed(now_reads)}", after it was filed as {job};'
             " nothing new is filed until you choose.",
    )


# The remake card's choices, in order: the first says it is a remake.
REMAKE_CHOICES = ("A remake", "The same job")


def remake_card(job: str, *, row: str) -> CardSpec:
    """Jim's "Is this a remake of <Job>?": only the install date or designer changed on a filed
    row whose old install date has passed, so it may be a remake, which must not be absorbed as an
    edit. Nothing is filed until Jim says which it is."""
    return CardSpec(
        "remake", job=job, row=row, choices=REMAKE_CHOICES,
        text=f"Is this a remake of {job}? {_fab_row(row)} has a new install date or designer, and"
             " its old install date has passed; nothing is filed until you choose.",
    )


# The design card's choices, in order: the first queues a fresh fetch, the second files what is
# there, and an event says Jim chose to.
DESIGN_MISMATCH_CHOICES = ("Fetch it again", "File it as it is")


def design_mismatch_card(job: str, *, report: int, proposal: int) -> CardSpec:
    """Jim's "The report and Studio's proposal differ": the designs on the report's page 1 aren't
    the ones on Studio's Proposal tab, so nothing is filed, only this job waits, and NG Runner
    looks again every 30 minutes."""
    return CardSpec(
        "design_mismatch", job=job, row=None, choices=DESIGN_MISMATCH_CHOICES,
        text=f"{job}: {design_counts(report, proposal)}.",
    )


# The sheet size card's choices, in order: the first clears that one program, by its hash, and is
# the only answer store.add_clearance takes. Its choice_ids are None: it means no other job.
SHEET_SIZE_CHOICES = ("I added this sheet on purpose", "Re-nest it")


def sheet_size_card(job: str, *, material: str, programs: Sequence[tuple[str, str | None]],
                    expected: str) -> CardSpec:
    """Jim's "A program is past its sheet size", one card per material however many programs: one
    real folder has 7. programs: (name, the sheet it reads as), read from its cutting moves; a sheet
    of None is one NG Runner doesn't know. Mixed sizes are legal, so only Jim can say it was on
    purpose, and a file's name can't: one such program was re-posted with a plain name. It stays DO
    NOT CUT meanwhile."""
    return CardSpec(
        "sheet_size", job=job, row=None, choices=SHEET_SIZE_CHOICES,
        text=f"{_programs_of(programs, material)} {_sheets_of(programs, expected)}. It stays DO NOT"
             " CUT until you choose, and a re-post asks again.",
    )


def _programs_of(programs: Sequence[tuple[str, str | None]], material: str) -> str:
    """Program TESS07 of 3/4 Classic White; 7 programs of it, programs TESS01-07; or, with names
    the lint would stop, 7 programs of it."""
    names = [name for name, _ in programs]
    said, shown = _programs_said(names), _material(material)
    if len(names) == 1:
        return f"{said[0].upper()}{said[1:]} of {shown}"
    counted = _count(len(names), "program")
    return f"{counted} of {shown}" if said == counted else f"{counted} of {shown}, {said},"


def _sheets_of(programs: Sequence[tuple[str, str | None]], expected: str) -> str:
    sheets = list(dict.fromkeys(on for _, on in programs))
    if len(sheets) == 1:
        return _sheet_clause(sheets[0], expected, singular=len(programs) == 1)
    on_each = [on for _, on in programs]
    each = [f"{on_each.count(on)} on {on}" if on else
            f"{on_each.count(on)} past every sheet NG Runner knows" for on in sheets]
    return f"are past this colour's {expected}: {', '.join(each)}"


def card_message(card: Card) -> Message:
    """A card as a message for Jim's phone or email: its ID, job and question, the one-line
    text, then the choices numbered, so "TES-2 2" or "TES-2 3rd" answers it at the PC."""
    return Message("card", f"{card.id} · {card_subject(card)} · {QUESTIONS[card.kind]}",
                   (card.text, *_numbered(card.choices)))


def card_subject(card: Card) -> str:
    """What a card is about, as a person reads it: its job, or the Fab row it asks about. A
    which-job card has no job yet, so this is what the board shows in its place."""
    return card.job or _fab_row(card.row or "")


# ----- the nest guard: what a verdict says on the board (guards.nest_state.decide)

POSTED_BEFORE = "Posted before NG Runner; not checked"
SAVE_THE_NEST = "Save the nest in the Optimizer so I can check it"
NO_PROGRAMS_YET = "No programs read yet; the nest itself is not checked"
NEST_UNREADABLE = "Could not read the nest file"
NEST_SETTLING = "The nest file is still changing, so it is not read yet"
NEST_CHANGED = "The nest, a program or a room changed since it was checked; checking it again"
_NOT_CHECKED = "the nest itself is not checked"
FOLDER_STEP = "No Fab row names this folder, so NG Runner only reads its programs"


def doubled_holes(holes: int, programs: Sequence[str]) -> str:
    """8 cam holes are doubled in programs TESS02 and TESS03"""
    verb = "hole is" if holes == 1 else "holes are"
    return f"{holes} cam {verb} doubled in {_programs_said(programs)}"


def past_its_size(count: int, on: str | None, expected: str) -> str:
    """A program is on 61x109 and this colour is 49x97. on is None for a reach past every sheet NG
    Runner knows, which is said as that, never as a guess at a sheet."""
    who = "A program" if count == 1 else f"{count} programs"
    return f"{who} {_sheet_clause(on, expected, singular=count == 1)}"


def nest_on_wrong_sheet(on: str, expected: str) -> str:
    """What sheet_size_check found, in NG Runner's words: The nest is on 61x109 and this colour is 49x97."""
    return f"The nest is on {on} and this colour is {expected}"


def nest_holes_doubled(holes: int) -> str:
    """What br_cam_check --nest found. Any two holes of one size: not only cams."""
    return f"{holes} {'hole is' if holes == 1 else 'holes are'} doubled in the nest"


def nest_checked_note(at: datetime | None, programs: Sequence[str]) -> str:
    """Nest checked 9:42 PM, programs TESS01-06"""
    return f"Nest checked{_at(at)}, {_programs_said(programs)}"


def programs_read_note(*, at: datetime | None, sheet: str | None, cleared: int = 0,
                       merged: int = 0) -> str:
    """What a clean read of the programs says, and no more. A fit is a lower bound: a small program
    fits any sheet, so it reads "nothing past 49x97", never "on 49x97" (P1b build spec 3.1, 5.2).
    sheet is None when no rule covers this material. Merged programs overlay several sheets, so
    their holes aren't compared, and "0 doubled holes" says so."""
    if sheet is None:
        size = "sheet size could not be verified"
    elif cleared:
        size = f"{cleared} cleared past {sheet}"
    else:
        size = f"nothing past {sheet}"
    holes = "0 doubled holes"
    if merged:
        holes += f" outside {_count(merged, 'merged program')}"
    return f"Programs read{_at(at)}, {size}, {holes}; {_NOT_CHECKED}"


def nest_names_missing(programs: Sequence[str]) -> str:
    """The nest names program TESS03, and it is not in the folder (RULES 724)."""
    are = "it is" if len(programs) == 1 else "they are"
    return f"The nest names {_programs_said(programs)}, and {are} not in the folder"


def programs_unread_note(programs: Sequence[str]) -> str:
    """Could not read program TESS02 yet; the nest itself is not checked"""
    return f"Could not read {_programs_said(programs)} yet; {_NOT_CHECKED}"


def lock_held(file_is_optimizer: bool, owner: str | None, age: timedelta) -> str:
    """Open in Mozaik by JDELA, lock 2 days old. For the board: it says who and how long, and leaves
    what to make of that to a person (design F1, F2)."""
    where = "In the Optimizer" if file_is_optimizer else "Open in Mozaik"
    who = f" by {owner}" if owner else ""
    return f"{where}{who}, lock {_how_old(age)} old"


def _how_old(age: timedelta) -> str:
    minutes = int(age.total_seconds() // 60)
    if minutes < 120:
        return _count(max(minutes, 1), "minute")
    if minutes < 48 * 60:
        return _count(minutes // 60, "hour")
    return _count(minutes // (24 * 60), "day")


def material_words(name: str) -> str:
    """A material folder as Jim reads it, for the board: 3/4 Classic White."""
    return _material(name)


# What goes on the digest's For you line, and so in the heartbeat's open risks: the watchdog repeats
# those if no digest goes, and the ones that start "Not checked" are George's (P1b build spec 5.7).


def open_do_not_cut(job: str, material: str, *, reasons: Sequence[str], trial: bool) -> str:
    """DO NOT CUT Testerson_101426 3/4 Classic White: 8 cam holes are doubled in program TESS02"""
    said = "; ".join(_first_lower(_clause(reason)) for reason in reasons)
    return f"DO NOT CUT {job} {_material(material)}{_trial(trial)}: {said}"


def open_not_checked(job: str, material: str, *, posted: datetime, now: datetime) -> str:
    """Not checked: Testerson_101426 3/4 Classic White, posted 11:40 PM"""
    when = _clock_on(posted, pacific_day(now))
    return f"Not checked: {job} {_material(material)}, posted {when}"


def open_save_nest(job: str, material: str) -> str:
    """Testerson_101426 3/4 Classic White: save the nest in the Optimizer so I can check it"""
    return f"{job} {_material(material)}: {_first_lower(SAVE_THE_NEST)}"


def more_not_checked(count: int) -> str:
    """Every post reads NOT CHECKED until the toolkit is here, so the list is held to a few."""
    are = "1 more material is" if count == 1 else f"{count} more materials are"
    return f"Not checked: {are} on the board"


# ----- the nest guard's messages (P1b build spec 5.6)


def do_not_cut(job: str, material: str, *, reasons: Sequence[str], trial: bool) -> Message:
    """Jim's phone and George's email, at once, any hour: quiet hours let it through by the first
    words of its title, so those stay exactly DO NOT CUT. reasons are the verdict's notes."""
    shown = _material(material)
    head = f"{job}: DO NOT CUT {shown}{_trial(trial)}."
    items = [f"{head} {_ended(reason)}" for reason in reasons] or [head]
    return Message("do_not_cut", f"DO NOT CUT · {job} · {shown}", _numbered(items), urgent=True)


def not_checked(job: str, material: str, *, posted: datetime, now: datetime) -> Message:
    """A nest posted while NG Runner was off: the digest, when it comes back. Not a verdict, since
    nothing has looked at it, so it never says trial."""
    shown = _material(material)
    when = _clock_on(posted, pacific_day(now))
    return Message("not_checked", f"NOT CHECKED · {job} · {shown}", _numbered(
        (f"{job}: NOT CHECKED {shown}, posted {when}. NG Runner is offline.",)))


def nest_checked(job: str, material: str, *, at: datetime, programs: Sequence[str], trial: bool,
                 open_items: int = 0) -> Message:
    """George's email, in business hours. "Jim still has N open items" only when true. It says what
    was checked and when, and leaves what to do about it to the people cutting."""
    shown = _material(material)
    items = [f"{job}: Nest checked {fmt_clock(at)} for {shown}{_trial(trial)},"
             f" {_programs_said(programs)}."]
    if open_items:
        items.append(f"{job}: Jim still has {_count(open_items, 'open item')}.")
    return Message("nest_checked", f"Nest checked · {job} · {shown}", _numbered(items))


def sheet_cleared(job: str, material: str, *, programs: Sequence[tuple[str, str | None]],
                  at: datetime) -> Message:
    """George's "Cleared by Jim": Jim said these programs' sheets were on purpose. Doubled holes, a
    failed check and the nest itself are untouched, so it says it clears the size and nothing
    else. programs: (name, the sheet it reads as)."""
    shown = _material(material)
    sheets = {on for _, on in programs}
    sheet = f" on {next(iter(sheets))}" if len(sheets) == 1 and None not in sheets else ""
    which = "that sheet" if len(programs) == 1 else "those sheets"
    return Message("sheet_cleared", f"Cleared by Jim · {job} · {shown}", _numbered((
        f"{job}: Cleared by Jim {fmt_clock(at)}, {_programs_said([n for n, _ in programs])} of"
        f" {shown}{sheet}: he added {which} on purpose. This clears the sheet size only;"
        f" {_NOT_CHECKED}.",)))


def fault_gone(job: str, material: str, *, at: datetime) -> Message:
    """George was told DO NOT CUT, and what is posted now no longer shows that fault. He is told at
    any hour, as he was of the alarm. It says only what was read, and leaves the rest to him."""
    shown = _material(material)
    return Message("fault_gone", f"No longer DO NOT CUT · {job} · {shown}", _numbered((
        f"{job}: No longer DO NOT CUT {shown} at {fmt_clock(at)}: the programs there now don't show"
        f" that fault. It reads NOT CHECKED; {_NOT_CHECKED}.",)))


def pass_gone(job: str, material: str, *, at: datetime) -> Message:
    """George was told a material is checked, and something it rests on has changed. He is told at any
    hour: NOT CHECKED is one of the two kinds that don't wait (design 4), and he may be cutting."""
    shown = _material(material)
    return Message("pass_gone", f"NOT CHECKED · {job} · {shown}", _numbered((
        f"{job}: NOT CHECKED {shown} at {fmt_clock(at)}: the nest, a program or a room changed since"
        " it was checked.",)))


_ESTIMATE = "(takeoff estimate; the nest decides)"


def order_heads_up(job: str, *, sheets: Sequence[tuple[int, str]],
                   tape: Sequence[tuple[float, str, str]], flags: Sequence[str] = (),
                   revised: bool = False, basis: str = "sheets with 10% waste") -> Message:
    """Barbara's and Will's heads-up, in business hours. sheets: (count, material), all on one line;
    tape: (feet, width as it reads, colour); flags: lines worth saying (P1b build spec 5.8). It
    takes only these, so no client, address or site can travel on it. basis names which of the
    tracker's two sheet counts this is. The title says "revised" when the takeoff changed."""
    items = []
    if sheets:
        listed = ", ".join(f"{_count(count, 'sheet')} {material}" for count, material in sheets)
        items.append(f"{job}: {listed} {_ESTIMATE}.")
    items += [f"{job}: {_feet(feet)} of {width} tape, {colour} {_ESTIMATE}."
              for feet, width, colour in tape]
    items += [f"{job}: {_ended(flag)}" for flag in flags]
    kind = "Revised order heads-up" if revised else "Order heads-up"
    return Message("heads_up", f"{kind} · {job} · {basis}", _numbered(items))


# ----- NG Runner's own takeoff (P3's takeoff on intake): its heads-up, and its lines elsewhere
#
# A bucket here is one entry of a stored takeoff's "buckets" (store/takeoffs.py): an IC colour
# code, a built thickness, and numbers. A colour is named from rules.COLOUR_BRANDS, which mirrors
# Jim's material_lookup: the brand when only one IC colour maps to it, else the brand and the IC
# code, as NG Runner has no name for a Tafisa colour of its own. RULES makes a white room AMWH
# when it holds anything 1in or thicker, and the takeoff can't tell rooms, so on such a job white
# is named as both, never as one. Nothing else of a row is said.

TAKEOFF_ESTIMATE = "takeoff estimate; the nest decides"     # guards/headsup.py's ESTIMATE too


def takeoff_material(colour: str, *, thick_white: bool = False) -> str:
    """Egger Classic White; Tafisa (IC AP) for a brand of many colours; IC colour ZZ for a code
    the rules have no brand for. thick_white: the takeoff has a white part 1in or thicker."""
    brand = takeoff_rules.COLOUR_BRANDS.get(colour)
    if brand is None:
        return f"IC colour {colour}"
    if thick_white and colour == takeoff_rules.WHITE:
        return f"AMWH or {brand} (IC {colour})"
    many = sum(1 for other in takeoff_rules.COLOUR_BRANDS.values() if other == brand) > 1
    return f"{brand} (IC {colour})" if many else brand


def _thickness_words(inches: float) -> str:
    """3/4, 1/4, 1\" or 1-1/8\": no mark on a plain fraction, as Jim's lines read."""
    fraction = Fraction(inches).limit_denominator(16)
    whole, rest = divmod(fraction, 1)
    if not rest:
        return f'{whole}"'
    return f'{whole}-{rest}"' if whole else str(rest)


def takeoff_lines(buckets: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """A takeoff's lines as the board shows them: sheets by colour and thickness, then tape. A
    bucket with no sheet count is left out, never guessed."""
    buckets = list(buckets)
    thick = thick_white(buckets)
    sheets = [f"{_count(b['sheets_with_waste'], 'sheet')} {_bucket_words(b, thick)}"
              for b in buckets if b.get("sheets_with_waste") is not None]
    tape = [f"{_feet(b['tape_lf'])} of tape for {_bucket_words(b, thick)}" for b in buckets
            if (b.get("tape_lf") or 0) > 0]
    return (*sheets, *tape)


def thick_white(buckets: Iterable[Mapping[str, Any]]) -> bool:
    """Whether a takeoff holds a white part built 1in or thicker (RULES: that room is AMWH)."""
    return any(b["colour"] == takeoff_rules.WHITE and b["thk_in"] >= takeoff_rules.ONE_INCH
               for b in buckets)


def could_not_verify_lines(count: int) -> str:
    """How many lines of a takeoff could not be verified. Counted, never quoted: a row's text can
    hold anything."""
    return f"could not verify {_count(count, 'line')}"


def takeoff_heads_up(job: str, *, buckets: Sequence[Mapping[str, Any]], flags: Sequence[str],
                     waste_percent: int, revised: bool) -> Message:
    """Barbara's and Will's heads-up from NG Runner's own complete takeoff, in the shape of P1b's
    (guards/headsup.py) so they read one form whichever source made it. Every bucket must have its
    sheet count: a takeoff that isn't complete goes out as takeoff_unverified, never as a list."""
    if any(b.get("sheets_with_waste") is None for b in buckets):
        raise ValueError("a bucket with no sheet count can't be listed: send takeoff_unverified")
    title = f"{job}: {'revised ' if revised else ''}order heads-up {_ESTIMATE}"
    first = (f"{'Revised t' if revised else 'T'}akeoff estimate; the nest decides. NG Runner's own"
             f" takeoff of the IC summary, counted as sheets with {waste_percent}% waste.")
    lines = takeoff_lines(buckets)
    sheets = [line for line in lines if " of tape for " not in line]
    items = [f"{job}: {', '.join(sheets)} {_ESTIMATE}."] if sheets else []
    items += [f"{job}: {line} {_ESTIMATE}." for line in lines if " of tape for " in line]
    items += [f"{job}: {_ended(flag)}" for flag in flags]
    return Message("heads_up", title, (first, *_numbered(items)))


def from_the_chats_takeoff(message: Message, *, could_not_verify: int | None = None) -> Message:
    """A heads-up made from the chat's IC Conversion JSON (guards/headsup.py's), saying so on its
    first line. could_not_verify: it went because NG Runner's own takeoff could not verify that
    many lines (Sulaiman's ruling, 2026-10-31). The list itself is the chat's alone."""
    source = "From the chat's takeoff"
    if could_not_verify is not None:
        source += (f", since NG Runner's own takeoff {could_not_verify_lines(could_not_verify)}")
    first, *rest = message.lines
    return replace(message, lines=(f"{_ended(first)} {source}.", *rest))


# A filed job's line when its order heads-up is the chat's takeoff: the board says which source.
CHATS_TAKEOFF = ("The order heads-up is the chat's takeoff, since NG Runner's own could not"
                 " verify every line")


def takeoff_unverified(job: str, *, lines: int, revised: bool) -> Message:
    """What goes instead of a list when NG Runner's takeoff isn't complete: never a partial list,
    and never a line's own text, only how many."""
    title = f"{job}: {'revised ' if revised else ''}order heads-up: could not verify the takeoff"
    return Message("heads_up", title, _numbered((
        f"{job}: {could_not_verify_lines(lines)} of its takeoff, so no order list is sent. Check"
        " the IC summary before ordering.",)))


_IN_CHAT_BRANCH = {"garage": "a garage job", "mixed": "a closet and garage job",
                   "unclear": "a job whose designs don't say closet or garage"}


def takeoff_made_words(*, state: str, complete: bool = False, could_not_verify: int = 0,
                       board_parts: int = 0, buckets: int = 0, tape_lf: float = 0.0,
                       branch: str = "") -> str:
    """The job's history line when its takeoff is made, or isn't."""
    if state == "in_chat":
        why = _IN_CHAT_BRANCH.get(branch, "not a closet")
        return f"Its takeoff stays in chat: {why}, until BUILD's garage rule is ported."
    if state != "made":
        return ("NG Runner could not read the IC summary for its takeoff, so the chat's takeoff is"
                " used.")
    if not complete:
        unverified = could_not_verify_lines(could_not_verify)
        return (f"Made its takeoff from the IC summary, but {unverified}, so no order list goes"
                " out from it.")
    return (f"Made its takeoff from the IC summary: {_count(board_parts, 'board part')} in"
            f" {_count(buckets, 'bucket')} and {_feet(tape_lf)} of tape {_ESTIMATE}.")


def takeoff_digest(buckets: Sequence[Mapping[str, Any]], *, complete: bool,
                   could_not_verify: int, chat: bool = False) -> str:
    """The clause a filed job's digest line adds: its sheets and tape, or how many lines could
    not be verified, and then whether the chat's takeoff went as the order heads-up instead."""
    if not complete:
        instead = ", so the order heads-up is the chat's takeoff" if chat else ""
        return f"takeoff: {could_not_verify_lines(could_not_verify)}{instead}"
    sheets = [line for line in takeoff_lines(buckets) if " of tape for " not in line]
    tape = sum(b.get("tape_lf") or 0 for b in buckets)
    return f"takeoff: {', '.join(sheets)} and {_feet(tape)} of tape {_ESTIMATE}"


def _bucket_words(bucket: Mapping[str, Any], thick: bool) -> str:
    material = takeoff_material(bucket["colour"], thick_white=thick)
    return f"{_thickness_words(bucket['thk_in'])} {material}"


def before_you_lock(job: str, *, items: Sequence[str]) -> Message:
    """What the pre-flight found, one line each. It says, and blocks nothing: Lock stays in chat."""
    return Message("before_you_lock", f"Before you lock · {job}",
                   _numbered(f"{job}: before you lock: {_ended(item)}" for item in items))


def program_names(names: Sequence[str]) -> str:
    """Program names as Jim reads them: no extension, in order, each once, a run of three or more
    together. TESS01-06, TESS02 and TESS03, TESS01-03 and TESS07. Two in a row stay two names, so a
    range always means at least three programs. An underscore reads as a space, which the lint
    passes: 21 of 541 real program names have one, most of them long-form names."""
    short = sorted({_PROGRAM_EXTENSION.sub("", name).replace("_", " ") for name in names})
    return _and(_runs(short))


_PROGRAM_EXTENSION = re.compile(r"\.tap$", re.IGNORECASE)
_NUMBERED_PROGRAM = re.compile(r"(?P<stem>.*?)(?P<number>[0-9]+)")
_LEADING_THICKNESS = re.compile(r"^([0-9])-([0-9])(?= )")
_WORD_JOINED = re.compile(r"(?<=[A-Za-z0-9])[-_](?=[A-Za-z])|_")


def _runs(names: Sequence[str]) -> list[str]:
    """names, sorted, with each run of three or more in a row as one TESS01-06."""
    shown: list[str] = []
    start = 0
    while start < len(names):
        end = start
        while end + 1 < len(names) and _follows(names[end], names[end + 1]):
            end += 1
        if end - start >= 2:
            shown.append(f"{names[start]}-{_NUMBERED_PROGRAM.fullmatch(names[end])['number']}")
        else:
            shown.extend(names[start:end + 1])
        start = end + 1
    return shown


def _follows(name: str, following: str) -> bool:
    """TESS02 follows TESS01: the same stem, the same width of number, and the next number."""
    one, other = _NUMBERED_PROGRAM.fullmatch(name), _NUMBERED_PROGRAM.fullmatch(following)
    return bool(one and other and one["stem"] == other["stem"]
                and len(one["number"]) == len(other["number"])
                and int(other["number"]) == int(one["number"]) + 1)


def _and(parts: Sequence[str]) -> str:
    if len(parts) < 2:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _programs_said(names: Sequence[str]) -> str:
    """programs TESS02 and TESS03. 65 of 551 real programs are hand-named, so a name may be one the
    lint stops, and that must not cost the whole message its job and material: then they are
    counted."""
    shown = program_names(names)
    if check_line(shown):
        return _count(len(names), "program")
    return f"{'program' if len(names) == 1 else 'programs'} {shown}"


def _sheet_clause(on: str | None, expected: str, *, singular: bool) -> str:
    if on is None:
        verb = "runs" if singular else "run"
        return f"{verb} past every sheet NG Runner knows, and this colour is {expected}"
    return f"{'is' if singular else 'are'} on {on} and this colour is {expected}"


def _material(name: str) -> str:
    """A material folder as Jim reads it: 3-4 Egger Classic White is 3/4 Egger Classic White. A
    hyphen or underscore joining words becomes a space, since real folders are named like Tafisa
    Spring Blossom_L543 and AraucoPrism-SF210-Antique-White-Suede, which the lint would stop as a
    code name and a key. A folder in NG Jobs can be named anything, so one the lint still stops
    reads "one material": a DO NOT CUT must reach people naming its job, and the board has the
    rest."""
    shown = _LEADING_THICKNESS.sub(r"\1/\2", " ".join(_WORD_JOINED.sub(" ", name).split()))
    return "one material" if check_line(shown) else shown


def _trial(trial: bool) -> str:
    return " (trial)" if trial else ""


def _at(at: datetime | None) -> str:
    return "" if at is None else f" {fmt_clock(at)}"


def _feet(feet: float) -> str:
    whole = round(feet)
    return "1 foot" if whole == 1 else f"{whole} feet"


# ----- P3's checks: what a check found, the close prompt, and their lines on the board and in the digest
#
# A check's items (ngrunner/checks/findings.py) hold codes, tags (R2C3), sizes and counts, never what a
# checker printed. The words are here. Each item names its parts by tag, at most 8 and then how many
# more, and one that Jim's chat fixes has the line he pastes into it, which names the job, so it reads
# on its own in a chat that has never seen the board.

CHECKER_WORDS = {   # each checker as Jim reads it: a checker's own name has an underscore
    "section_align": "section alignment", "section_span": "section span", "drill_depth_check": "bore depth",
    "outset_check": "outset", "part_geom_check": "part size", "br_cam_check": "bridge and cam",
    "op_schema_check": "operation format", "wm_variant_check": "WM variant",
    "notch_check": "notch face and WM banding", "eb_tops": "countertop banding", "top_line": "top line",
    "parm_check": "parameters", "panel_xcheck": "panel set", "ic_code_check": "IC codes",
    "hinge_check": "hinge count", "room_files": "room files", "sheet_size_check": "sheet size",
    "br_cam_check --nest": "nest cam",
}
# A fault's kind: what is wrong, and what Jim's chat is to do about it ({tags} the parts). None: only
# Jim in Mozaik can fix it, and the words say so.
_CHECK_FIXES = {
    "off_grid": ("the section sits off the bore grid", "put {tags} back on the bore grid"),
    "top_off": ("the section's top misses the panels' top line",
                "bring the tops of {tags} to the panels' top line"),
    "bore_pitch": ("bore holes on the vertical are closer together than the bore pitch",
                   "fix the bore rows on {tags}"),
    "faces_off": ("the bore rows on the vertical's two faces are out of step", "fix the bore rows on {tags}"),
    "stale_rows": ("a bore row is left from an old section height. Touch the section beside it in Mozaik,"
                   " as opening and saving won't clear it", None),
    "span": ("the section doesn't fill its bay from vertical to vertical",
             "make the sections on {tags} meet their verticals"),
    "bore_off_panel": ("a line bore runs off the panel. Fix it in Mozaik", None),
    "bores_stripped": ("the vertical has no line bores though a section is beside it",
                       "restore the line bores on {tags}"),
    "bore_depth": ("the line bore depth doesn't match the sections beside the vertical",
                   "fix the line bore depth on {tags}"),
    "part_size": ("a panel part doesn't match its product's depth or height",
                  "fix the parts of {tags} to match their products"),
    "bridge_cams": ("the bridge's cams are not as the bridge rule says", "rebuild the bridge cams on {tags}"),
    "doubled_holes": ("two holes sit on top of each other", "fix the doubled holes on {tags}"),
    "op_schema": ("an operation isn't written the way Mozaik writes it",
                  "rewrite the operations the operation format check names in {tags}"),
    "wm_variant": ("the WM panel is the wrong variant for the sections beside it",
                   "swap {tags} to the variant their sections need"),
    "wm_groove": ("the WM panel's groove doesn't match the product library", "fix the groove on {tags}"),
    "notch_face": ("the notch is on the wrong face", "put the notch on its line-bore face on {tags}"),
    "wm_banding": ("WM panel banding is not taped and locked on top, front and bottom",
                   "tape and lock the WM panel banding in {tags}"),
    "room_file": ("the room file isn't written the way Mozaik writes it",
                  "write the room file of {tags} again the way Mozaik writes it"),
}
_CHECK_UNVERIFIED = {   # could not verify: what wasn't checked, after "Could not verify <subject>: "
    "q1": "how the top is numbered waits for Jim's answer to Q1",
    "not_run": "Jim's crosscheck doesn't run his hinge check",
    "outset": "the outset check found {products} not at outset 0, which Jim's crosscheck doesn't flag",
    "banding": "the countertop banding check would change the countertop banding, which Jim's crosscheck"
               " doesn't flag",
    "top_line": "the top line check would move WM panels to the room's top line, which Jim's crosscheck"
                " doesn't flag",
    "op_by_hand": "an operation has no Mozaik-written example to compare with, so a person must check it",
    "variant_deferred": "the WM variant check waits for sections on the wall",
    "version": "the room file is in a version NG Runner hasn't seen",
}
_TAGS_SHOWN = 8
CHECK_CLOSE_FIRST = ("Opened fine, sections done: check it", "Opened fine, not done yet", "It showed an error")
CHECK_CLOSE = ("Yes, check it", "Not yet")
CHECK_THESE = frozenset({CHECK_CLOSE_FIRST[0], CHECK_CLOSE[0]})     # the answers that ask for a check
NO_TOOLKIT = "No copy of Jim's toolkit is adopted yet, so his checks can't run, and nothing was checked"
NOT_A_JOB = "NG Runner doesn't know that job, so nothing was checked."
SETTLE_LABELS = {"not_a_problem": "Not a problem for this job", "told_chat": "I already told chat"}


def check_item_words(item: Any) -> str:
    """One item, as the board and a message say it (findings.Item): its parts by tag, then what."""
    tags = _tags_said(item.tags)
    checker = CHECKER_WORDS.get(item.checker, "a")
    if item.kind == "could_not_verify":
        if item.what == "not_read":
            return f"Could not verify any check: {_clause(item.why)}"
        if item.what == "checker":
            return f"Could not verify the {checker}: the check {_clause(item.why)}"
        said = _CHECK_UNVERIFIED.get(item.what, f"the {checker} check could not be read")
        said = said.format(products="a product" if item.count == 1 else "products")
        return f"Could not verify {tags or f'the {checker}'}: {said}"
    if item.kind == "advisory":
        return (f"The IC codes differ from what is built in {_count(item.count or 1, 'place')}; advisory"
                " only, as Jim's rules win over the IC codes")
    if item.what == "parameters":
        verb = "differs" if item.count == 1 else "differ"
        return f"{_count(item.count, 'parameter')} {verb} from the template job's"
    if item.what in ("missing_part", "extra_part"):
        parts = sum(quan for _, _, quan in item.sizes) or 1
        where = ("on the IC summary {is_} not in Mozaik's cutlist" if item.what == "missing_part"
                 else "in Mozaik's cutlist {is_} not on the IC summary")
        return (f"{_count(parts, 'part')} {where.format(is_='is' if parts == 1 else 'are')}:"
                f" {_sizes_said(item.sizes)}")
    problem = _CHECK_FIXES.get(item.what, (f"the {checker} check found something to fix", ""))[0]
    return f"{tags}: {problem}" if tags else _upper_first(problem)


def check_chat_line(job: str, item: Any) -> str | None:
    """The line Jim pastes into his chat to fix item, naming the job; None when only Mozaik can fix it
    or it is no fault (could not verify, advisory)."""
    if item.kind != "fix":
        return None
    tags = _tags_said(item.tags)
    if item.what == "parameters":
        action = "set the job's parameters to the template job's"
    elif item.what == "missing_part":
        action = "add the parts the panel set check says are missing from Mozaik"
    elif item.what == "extra_part":
        action = "fix the extra parts the panel set check names"
    else:
        known = _CHECK_FIXES.get(item.what, (None, None))[1]
        action = known or f"fix what the {CHECKER_WORDS.get(item.checker, 'a')} check found on {{tags}}"
        if not tags:
            for joined in (" on {tags}", " in {tags}", " of {tags}"):
                action = action.replace(joined, "")
        action = action.format(tags=tags)
    return f"{job}: {action}, then run crosscheck"


def check_item_line(job: str, item: Any) -> str:
    """An item as one line of a message: what, then the chat line in quotes, or nothing more."""
    chat = check_chat_line(job, item)
    words = _ended(check_item_words(item))
    return f'{words} In chat: "{chat}".' if chat else words


def check_result(job: str, *, at: datetime, trial: bool, audit: bool, items: Sequence[Any],
                 passed: bool) -> Message:
    """What a check Jim asked for found: a toast at his PC, or Sulaiman's email in shadow mode. One
    numbered line per open item, could-not-verify ones included; exactly the clean line when every
    check passed and nothing is open. Advisory items are on the board only, as are settled ones."""
    title = f"{job} {'build checked' if audit else 'checked'} {fmt_clock(at)}{_trial(trial)}"
    shown = [item for item in items if item.kind != "advisory"]
    if passed and not shown:
        return Message("check_result", title, (CLEAN,))
    if not shown:
        return Message("check_result", title, (f"{job}: every finding is one you settled for this job.",))
    return Message("check_result", title, _numbered(check_item_line(job, item) for item in shown))


def check_state_words(*, state: str, at: datetime | None = None, now: datetime | None = None,
                      trial: bool = False, audit: bool = False, to_fix: int = 0, unverified: int = 0,
                      settled: int = 0, words: str = "") -> str:
    """The check's one line on the board. state: asked, running, waiting, passed, failed,
    could_not_verify, stale or not_run."""
    when = "" if at is None else (_clock_on(at, pacific_day(now)) if now is not None else fmt_clock(at))
    what = "Build checked" if audit else "Checked"
    if state == "asked":
        return "A check is asked for, and runs next"
    if state == "running":
        return "Checking now"
    if state == "waiting":
        return "Waiting for Mozaik to close the job, then it checks"
    if state == "not_run":
        return f"Not checked {when}: {_clause(words)}" if when else f"Not checked: {_clause(words)}"
    if state == "stale":
        return f"{what} {when}, and the job changed since; check it again"
    parts = [f"{to_fix} to fix"] if to_fix else []
    parts += [f"{unverified} could not verify"] if unverified else []
    parts += [f"{settled} settled"] if settled else []
    head = f"{what} {when}{_trial(trial)}"
    if state == "passed" and not parts:
        return f"{head}: every check passed"
    return f"{head}: {', '.join(parts) or 'could not verify'}"


def check_your_list(job: str, *, to_fix: int, at: datetime, trial: bool, now: datetime) -> str:
    """Your list: a job with something to fix from its check."""
    return (f"{job}: {to_fix} to fix from its check at {_clock_on(at, pacific_day(now))}{_trial(trial)};"
            " see the board")


def check_digest(*, at: datetime, trial: bool, audit: bool, to_fix: int, unverified: int,
                 passed: bool) -> str:
    """The check's clause on a job's digest line: checked 9:42 PM (trial): 2 to fix."""
    what = "build checked" if audit else "checked"
    if passed:
        return f"{what} {fmt_clock(at)}{_trial(trial)}, every check passed"
    parts = [f"{to_fix} to fix"] if to_fix else []
    parts += [f"{unverified} could not verify"] if unverified else []
    return f"{what} {fmt_clock(at)}{_trial(trial)}: {' and '.join(parts) or 'could not verify'}"


def close_prompt(job: str, *, closed_at: datetime, first: bool) -> Message:
    """The toast when Jim closes a job in Mozaik (design 4, "The close prompt"). Its buttons are
    CHECK_CLOSE_FIRST the first time, then CHECK_CLOSE; ignoring it means "not yet"."""
    return Message("close_prompt", f"{job} closed {fmt_clock(closed_at)}", ("Sections done?",))


def close_unanswered(job: str, *, closed_at: datetime, now: datetime) -> str:
    """Your list and the digest, while a close prompt waits: design 4's words."""
    return f"{job}: closed {_clock_on(closed_at, pacific_day(now))}, not checked. Sections done?"


def check_asked(job: str) -> str:
    return f"Checking {job} now: the board shows what it finds."


def check_already_asked(job: str) -> str:
    return f"{job} is already waiting for its check."


def settled_echo(job: str, *, n: int, how: str) -> str:
    if how == "told_chat":
        return f"Noted for {job}: you told chat about item {n}. It isn't asked again unless the job changes."
    return f"Settled for {job}: item {n} isn't asked again unless the job changes."


def settled_line(*, at: datetime, words: str, now: datetime) -> str:
    """A settled item on the board: when, and Jim's words as he gave them (a button's label)."""
    return f"Settled {_clock_on(at, pacific_day(now))}: {_clause(words)}"


SETTLE_GONE = "That item is no longer on the board, so nothing changed."
SETTLE_STALE = "The job changed since that check, so nothing was settled: check it again first."
SETTLE_NEVER = "That item can't be settled: it is something NG Runner must always say, so nothing changed."
PROMPT_ANSWERED = "That question is no longer open, so nothing changed."
PROMPT_NOT_A_CHOICE = "That isn't one of the question's answers, so nothing changed."


def mozaik_error_echo(job: str) -> str:
    """Jim's "It showed an error" on a close prompt: Sulaiman hears, and the job is his chat's as before."""
    return f"Noted: {job} showed an error in Mozaik. Sulaiman will look; work it in chat as before."


def not_yet_echo(job: str) -> str:
    return f"Noted: {job} isn't ready to check. Check now on the board when it is."


def _tags_said(tags: Sequence[str]) -> str:
    shown = list(tags[:_TAGS_SHOWN])
    if len(tags) > _TAGS_SHOWN:
        return f"{', '.join(shown)} and {len(tags) - _TAGS_SHOWN} more"
    return _and(shown) if shown else ""


def _sizes_said(sizes: Sequence[tuple[float, float, int]]) -> str:
    said = []
    for width, length, quan in sizes[:4]:
        one = f"{_inches(width)} by {_inches(length)}"
        said.append(f"{one}, {quan} of them" if quan > 1 else one)
    more = len(sizes) - 4
    return "; ".join(said) + (f"; and {_count(more, 'other size')}" if more > 0 else "")


def _inches(mm: float) -> str:
    """157.5 mm as 6 3/16" (157.5 mm): inches to the nearest 1/16, then the mm Jim's files hold."""
    whole, part = divmod(round(mm / 25.4 * 16), 16)
    fraction = Fraction(part, 16)
    inches = (f"{whole} {fraction}" if whole else f"{fraction}") if part else f"{whole}"
    return f'{inches}" ({mm:g} mm)'


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:]


# ----- P4's lock and fixes: the three lock clicks, the batch after it, a fix's card, Undo and survival
#
# P4 runs in shadow until it goes live: a batch, a fix, a lease and a job sheet go only to NG Runner's own
# copies on this PC (config.shadow_roots), so every message about them goes to Sulaiman alone, marked
# SHADOW, and none is a toast. What is missing for a lock comes from lock/gate.py, the drop's checks and
# the takeoff's rows, in their own plain words; the words here only frame them.

P4_SHADOW_NOTE = ("P4 runs in shadow: a batch, a fix, a lease and a job sheet go only to NG Runner's own copies"
                  " on this PC, never to the tracker, Mozaik Shared or NG Jobs")
LOCK_LABELS = {"lock_design": "Lock design", "finish_lock": "Finish lock", "lock": "Lock"}
LOCK_WITHOUT_CHECK = "Lock without check"
FIX_CHOICES = ("Fix it", "Leave it")
REDO_CHOICES = ("Redo the fix", "Leave it")
BATCH_LABELS = {"lock_design": "Lock design", "finish_lock": "Finish lock", "lock": "Lock",
                "resend": "Re-send of the missing rows"}
RESEND = "Re-send only the missing row"
NOT_A_LOCK = "That isn't one of the lock buttons, so nothing changed."
LOCK_GONE = "That is no longer on the board, so nothing changed."
LOCK_BUSY = "That is already asked for, so it wasn't asked again."


def lock_label(click: str, material: str | None = None) -> str:
    """A lock button's label: Lock design, Finish lock 3/4 Classic White, or Lock."""
    label = LOCK_LABELS[click]
    return f"{label} {_material(material)}" if material else label


def lock_refused(job: str, *, label: str, missing: Sequence[str]) -> str:
    """The board's answer to a lock click that its gate refuses: what is missing, each as the gate says."""
    said = "; ".join(_clause(words) for words in missing) or "it could not be verified"
    return f"{job}: {label} wasn't done. Missing: {said}."


def lock_asked(job: str, *, label: str, without_check: bool) -> str:
    """The board's answer to a lock click that goes ahead: the batch follows within minutes."""
    waived = ", without its check, as you chose" if without_check else ""
    return f"{job}: {label} asked{waived}. The board shows the batch once it is dropped."


def lock_result(job: str, *, label: str, ok: bool, words: str, at: datetime, without_check: bool) -> Message:
    """What a lock click came to, for Sulaiman while P4 is in shadow: dropped into NG Runner's own test
    folder, or refused with why."""
    waived = f" ({LOCK_WITHOUT_CHECK.lower()})" if without_check else ""
    title = f"{job}: {label}{waived} {fmt_clock(at)}"
    if ok:
        return Message("lock_result", title, (f"{_sentence(words)} Shadow: into NG Runner's own test folder,"
                                              " not the tracker.",))
    return Message("lock_result", title, (f"Not dropped: {_ended(_clause(words))}",))


def batch_result(job: str, *, label: str, state: str, words: str, at: datetime) -> Message:
    """A batch the snapshots confirmed, or left in doubt. Doubt is never re-dropped by itself, and is
    urgent: the tracker may not hold what the batch said."""
    title = f"{job}: {label} {'confirmed' if state == 'confirmed' else 'in doubt'} {fmt_clock(at)}"
    if state == "confirmed":
        return Message("batch_result", title, (_sentence(words),))
    return Message("batch_result", title, (f"{_sentence(words)} Nothing is sent again by itself; the board"
                                           " says what can be.",), urgent=True)


def batch_waiting(job: str, *, label: str, since: datetime, now: datetime) -> str:
    """A batch the tracker hasn't taken within 30 minutes (design 3.4, a lock batch's limit)."""
    return f"{job}: the {label} batch dropped {_clock_on(since, pacific_day(now))} still waits for the tracker"


def resend_asked(job: str) -> str:
    return (f"{job}: re-sending only the missing rows, once a snapshot taken after now shows them still"
            " missing.")


def fix_card(job: str, *, words: Sequence[str], renest: Sequence[str]) -> CardSpec:
    """A fix of Jim's fixer, planned on a copy, for him to approve (decision 7a: one click while on
    trial). Its text is the plan's own words. A fix on parts a nest holds is "Fix and re-nest", as the
    nest reads NOT CHECKED until it is nested again (decision 7b)."""
    first = f"Fix and re-nest {_and([_material(m) for m in renest])}" if renest else FIX_CHOICES[0]
    said = " ".join(_ended(_clause(line)) for line in words)
    return CardSpec("fix", job=job, row=None, choices=(first, FIX_CHOICES[1]),
                    text=f"{said} Shadow until P4 goes live: only NG Runner's own copy of the room is changed.")


def redo_card(job: str, *, words: str) -> CardSpec:
    """Mozaik undid a fix: one redo is offered, never more."""
    return CardSpec("fix_undone", job=job, row=None, choices=REDO_CHOICES,
                    text=f"{_ended(_clause(words))} One redo is offered; the fix is planned again first.")


def fix_result(job: str, *, done: str, words: str, at: datetime) -> Message:
    """A fix written, undone, or not done, for Sulaiman while P4 is in shadow. done: written, undone or
    refused."""
    title = f"{job}: fix {'not done' if done == 'refused' else done} {fmt_clock(at)}"
    note = "" if done == "refused" else " Shadow: in NG Runner's own copy of the room, not in Mozaik Shared."
    return Message("fix_result", title, (f"{_sentence(words)}{note}",))


def survival_message(job: str, *, code: str, words: str, at: datetime) -> Message:
    """What the fix's own check said after Jim's next save. "Mozaik undid" is urgent, with one redo."""
    title = f"{job}: after the save {fmt_clock(at)}"
    redo = " A redo is offered on the board, once." if code == "mozaik_undid" else ""
    return Message("survival", title, (f"{_sentence(words)}{redo}",), urgent=code == "mozaik_undid")


def survival_line(job: str, *, words: str) -> str:
    """"You changed it in Mozaik", as a line in the log."""
    return f"{job}: {_clause(words)}"


def lease_words(state: str, *, at: datetime, until: datetime, now: datetime) -> str:
    """A job's lease on the board: held until when, lapsed, or released."""
    day = pacific_day(now)
    if state == "active" and now < until:
        return f"NG Runner holds this job's lease until {_clock_on(until, day)}, so chat leaves its files alone"
    if state == "active":
        return f"NG Runner's lease lapsed {_clock_on(until, day)}, so chat may work the job"
    return f"Lease released {_clock_on(at, day)}: chat may work the job"


def job_sheet_words(code: str, *, written: bool = False, why: str = "") -> str:
    """The job sheet at lock: Jim's cnc_job_sheet against NG Runner's own reading of the nests."""
    if code == "matches":
        return ("The job sheet's counts match the nests" + ("; filed in NG Runner's own copy of the job"
                                                            " folder" if written else ""))
    if code == "mismatch":
        return "The job sheet's counts don't all match the nests, so it isn't filed; the items say which"
    return f"Could not verify the job sheet: {_clause(why) or 'it could not be made'}"


def undo_asked(job: str) -> str:
    return f"{job}: undoing the fix, once the room is clear to write."



# ----- Sulaiman's lines: the DEV lane (Jim never sees it) and the daily "ran" line


@dataclass(frozen=True)
class DevLine:
    """One DEV item: when it was raised, what happened in plain words, its job if any, and its
    code in the DEV lane, which stands in for its words when they can't be sent."""

    at: datetime
    text: str
    job: str | None = None
    code: str = ""


def dev(items: Sequence[DevLine], *, day: date) -> Message:
    """The open DEV items for Sulaiman, each naming its job or NG Runner itself. Each one's text
    comes from elsewhere, so it is made safe first (lint.redact), and a line that still fails
    the lint is replaced by its code alone, so one bad text never costs the others."""
    title = f"NG Runner DEV · {fmt_day(day)}"
    lines = [_dev_line(item, title=title, day=day) for item in items]
    return Message("dev", title, _numbered(lines) if lines else ("No DEV items are open.",))


def _dev_line(item: DevLine, *, title: str, day: date) -> str:
    when = _clock_on(item.at, day)
    whose = item.job or ABOUT_ITSELF
    coded = f"a DEV item ({item.code.replace('_', ' ')})" if item.code else "a DEV item"
    withheld = "did not pass the message check; its text is in NG Runner's database."
    for line in (f"{whose} at {when}: {_ended(redact(item.text))}",
                 f"{whose} at {when}: {coded} {withheld}",
                 f"{ABOUT_ITSELF} at {when}: {coded} {withheld}"):
        if not lint(Message("dev", title, (f"1. {line}",))):
            return line
    return f"{ABOUT_ITSELF} at {when}: a DEV item {withheld}"


def ran(day: date, *, version: str, new_rows: int, filed: int, cards_opened: int,
        cards_answered: int, writes_refused: int, dev_open: int) -> Message:
    """The daily "ran" line for Sulaiman; its absence is itself the alarm (design 2)."""
    return Message("ran", f"NG Runner ran · {fmt_day(day)}", (
        f"NG Runner {version} ran {fmt_day(day)}: {_count(new_rows, 'new row')}, {filed} filed,"
        f" {_count(cards_opened, 'card')} opened, {_count(cards_answered, 'card')} answered,"
        f" {_count(writes_refused, 'write')} refused, {_count(dev_open, 'DEV item')} open.",))


# ----- the urgent one: receipts have stopped, so it goes straight to Pushover if its keys exist


def cant_send(*, since: datetime, now: datetime, waiting: int) -> Message:
    """The urgent "NG Runner can't send messages": no receipt has come back for 5 minutes
    (design 4, "Silence and failure"). It goes straight to Pushover only if its keys were added,
    which P1a doesn't need (docs/decisions.md), so its words promise no phone."""
    are = "is" if waiting == 1 else "are"
    return Message("cant_send", "NG Runner can't send messages", (
        f"No message has been confirmed sent since {_clock_on(since, pacific_day(now))}, and"
        f" {waiting} {are} waiting. The board still shows everything.",), urgent=True)


# ----- echoes: what the command box answers once a command is done


def echo(command: Command) -> str:
    """One line for the board once command is done (say "Saved" only after it is saved). It
    never repeats a Studio project ID: those stay off every screen."""
    if command.kind == "answer":
        return f"Saved: {command.card_id}, {command.choice}."
    if command.kind == "pause":
        return "Paused: NG Runner files nothing until you resume it here."
    if command.kind == "resume":
        return "Resumed: NG Runner carries on where it stopped."
    if command.name:
        return f"Looking for {_spaced(command.name)} in the job list."
    return "Looking for that Studio project in the job list."


# ----- shadow mode: what would have gone to someone else goes to Sulaiman, marked

SHADOW = "SHADOW"


def shadow(message: Message) -> Message:
    """message redirected to Sulaiman (integration spec section 7): its kind and words as they
    are, its title marked. Redirect before vetted(), so he sees what would have gone out."""
    return replace(message, title=f"{SHADOW} {message.title}")


# ----- at runtime: the lint once more, and each kind's fixed template

# What goes out instead of a message that fails the lint: words fixed here, no outside data
# but the day (from NG Runner's own clock). Each says where the real thing is, or what to do.
FIXED = {
    "digest": ("{digest_title}", "Today's list did not pass NG Runner's message check, so it is"
               " on the board instead."),
    "quiet_digest": ("{digest_title}", "Today's list did not pass NG Runner's message check, so"
                     " it is on the board instead."),
    "card": ("A question for you", "NG Runner has a question for you on the board."),
    "filed": ("A Fab row is filed", "NG Runner filed one of your Fab rows, but could not name it"
              " in this email; there is nothing for you to do."),
    "which_job_email": ("Which job is this row?", "NG Runner could not tell which job one of your"
                        " Fab rows is, and could not name it in this email. Check your newest"
                        " rows, and type the full job name as Salesforce spells it."),
    "dev": ("NG Runner DEV · {day}", "NG Runner: a DEV note did not pass the message check; its"
            " text is in NG Runner's database."),
    "ran": ("NG Runner ran · {day}", "NG Runner ran, but its daily line did not pass the message"
            " check."),
    "cant_send": ("NG Runner can't send messages", "Messages are not being confirmed sent. The"
                  " board still shows everything."),
    # Its title keeps quiet hours letting it through, and George has no board of his own.
    "do_not_cut": ("DO NOT CUT", "NG Runner raised a DO NOT CUT, but could not name the job in this"
                   " message. Jim can see it on his board; check with him before cutting anything"
                   " newly posted."),
    "not_checked": ("NOT CHECKED", "A nest was posted that NG Runner has not checked, and it could"
                    " not name the job in this message. Jim can see it on his board."),
    "nest_checked": ("Nest checked", "NG Runner checked a nest, but could not name it in this"
                     " email. Jim can see which on his board."),
    "sheet_cleared": ("Cleared by Jim", "Jim cleared a sheet size, but NG Runner could not name it"
                      " in this email. He can see which on his board."),
    "pass_gone": ("NOT CHECKED", "A material that was checked has changed since, so it is not"
                  " checked now. NG Runner could not name it in this email; ask Jim which one."),
    "fault_gone": ("No longer DO NOT CUT", "A material that read DO NOT CUT no longer shows that"
                   " fault, but NG Runner could not name it in this email. Jim can see which on his"
                   " board."),
    "heads_up": ("Order heads-up", "NG Runner has an order heads-up, but could not list it in this"
                 " email; the job's takeoff has the numbers."),
    "before_you_lock": ("Before you lock", "NG Runner found something to settle before a lock, but"
                        " could not name it in this message; it is on the board."),
}
_ANY_OTHER = ("NG Runner", "NG Runner has a message for you on the board.")
_URGENT_KINDS = frozenset({"cant_send", "do_not_cut"})


@dataclass(frozen=True)
class Vetted:
    """What to send: the message itself, or its kind's fixed template when it failed the lint;
    problems (plain words, no message text) then open a DEV item."""

    message: Message
    problems: tuple[str, ...]

    @property
    def replaced(self) -> bool:
        return bool(self.problems)


def fixed(kind: str, *, day: date, urgent: bool = False) -> Message:
    """The fixed template for a kind of message (any unknown kind gets a plain pointer to the
    board). It is urgent when its kind is, or when the message it replaces was."""
    title, line = FIXED.get(kind, _ANY_OTHER)
    title = title.format(digest_title=digest_title(day), day=fmt_day(day))
    return Message(kind, title, (line,), urgent=urgent or kind in _URGENT_KINDS)


def vetted(message: Message, *, now: datetime) -> Vetted:
    """message once more through the lint, just before it is sent: nothing waits on Sulaiman,
    and nothing that fails goes out. What replaces a redirected message is marked as it was."""
    problems = tuple(lint(message))
    if not problems:
        return Vetted(message, ())
    replacement = fixed(message.kind, day=pacific_day(now), urgent=message.urgent)
    return Vetted(shadow(replacement) if message.title.startswith(f"{SHADOW} ") else replacement,
                  problems)


# ----- small pieces


def _clock_on(at: datetime, day: date) -> str:
    """9:42 PM on day itself, Thu 10/29 9:42 PM on any other."""
    return fmt_clock(at) if pacific_day(at) == day else _when(at)


def _row(text: str, designer: str, install: date | None) -> str:
    """Fab row "Testerson" (designer PE, installs Fri 11/6): what Barbara typed, and how she
    would find it again. Unknown parts are left out, never guessed, and so is a designer cell the
    lint would stop (N/A, TBD, None), which would cost her the whole email."""
    cell = _spaced(designer)
    details = [f"designer {cell}"] if cell and not check_line(f"designer {cell}") else []
    if install is not None:
        details.append(f"installs {fmt_day(install)}")
    shown = _fab_row(text)
    return f"{shown} ({', '.join(details)})" if details else shown


def _fab_row(text: str) -> str:
    return f'Fab row "{_typed(text)}"'


def _typed(text: str) -> str:
    """What Barbara typed, to quote: one line, and any double quote of hers made a curly one, so
    the lint can see where her words end (it holds them to fewer rules than NG Runner's own)."""
    return _spaced(text).replace('"', "”")


def _spaced(text: str) -> str:
    """Typed text on one line, its runs of spaces made one (Barbara's " 7 /1")."""
    return " ".join(text.split())


def _when(at: datetime) -> str:
    return f"{fmt_day(at)} {fmt_clock(at)}"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _clause(text: str) -> str:
    """A reason from elsewhere, to sit inside a sentence: one line, no closing stop."""
    return _spaced(text).rstrip(".")


def _first_lower(text: str) -> str:
    """Its first letter small, unless it starts a capitalised word like IC or a program's name."""
    return text[0].lower() + text[1:] if text[1:2].islower() or text[1:2] == " " else text


def _sentence(text: str) -> str:
    """A reason from elsewhere as a sentence of its own: a capital first, a stop last."""
    text = _spaced(text)
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return _ended(text)


def _ended(text: str) -> str:
    """text with a full stop, unless it already ends a sentence."""
    text = _spaced(text)
    return text if text.endswith((".", "?", "!")) else text + "."


def _candidate(opportunity: Opportunity) -> str:
    details = ", ".join(part for part in (opportunity.owner, opportunity.stage) if part)
    return f"{opportunity.name} ({details})" if details else opportunity.name


def _labels(candidates: Sequence[Opportunity]) -> list[str]:
    """One choice per candidate, in order (so choice n is candidates[n - 1]): its name, or,
    when two sales share a name, the name with its owner and stage, and then its place."""
    def key(text: str) -> str:
        return _spaced(text).casefold()

    names = [key(o.name) for o in candidates]
    labels = [o.name if names.count(key(o.name)) == 1 else _candidate(o) for o in candidates]
    keys = [key(label) for label in labels]
    return [label if keys.count(key(label)) == 1 else f"{label} #{place}"
            for place, label in enumerate(labels, start=1)]


def _numbered(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(f"{n}. {item}" for n, item in enumerate(items, start=1))
