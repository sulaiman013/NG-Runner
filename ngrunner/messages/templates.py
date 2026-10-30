"""
The words of every P1a message, in one place (design section 4; build spec 6.5).

Each template is a function of plain facts that returns a Message, or, for a question to Jim, a
CardSpec that becomes a card and then a Message through card_message(). Wording lives only
here, so the lint can hold all of it to Jim's format at test time. At runtime, vetted() checks
each message once more: one that fails (a reason from elsewhere carried a path, say) goes out as
its kind's fixed template instead, and the problems are returned for a DEV item, so nothing
waits on Sulaiman.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime

from ngrunner.cards import Card, CardSpec, Command
from ngrunner.clock import fmt_clock, fmt_day, pacific_day
from ngrunner.match import Opportunity
from ngrunner.messages import Message
from ngrunner.messages.lint import ABOUT_ITSELF, check_line, lint, redact, subject_line

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


def card_message(card: Card) -> Message:
    """A card as a message for Jim's phone or email: its ID, job and question, the one-line
    text, then the choices numbered, so "TES-2 2" or "TES-2 3rd" answers it at the PC."""
    return Message("card", f"{card.id} · {card_subject(card)} · {QUESTIONS[card.kind]}",
                   (card.text, *_numbered(card.choices)))


def card_subject(card: Card) -> str:
    """What a card is about, as a person reads it: its job, or the Fab row it asks about. A
    which-job card has no job yet, so this is what the board shows in its place."""
    return card.job or _fab_row(card.row or "")


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
}
_ANY_OTHER = ("NG Runner", "NG Runner has a message for you on the board.")
_URGENT_KINDS = frozenset({"cant_send"})


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
