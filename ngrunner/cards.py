"""
Cards: the questions NG Runner asks Jim, each with fixed choices and his answer kept verbatim.

A job that needs a person's judgment (which job a Fab row is, closet or garage, which render of
a changed IC summary governs) waits on a card instead of a guess (design section 4, "How Jim
answers"). A card's ID is the job's first three letters and a number from the store (TES-2),
never reused, so "TES-2 3rd" means one thing for ever. The command box is parsed strictly:
anything that isn't exactly a card answer, pause, resume, add job, check or lock is refused with an
example to copy, and the card stays with Jim. P3 adds "check <Job>", which asks for Check now on one job,
and P4 "lock <Job>", which is Jim's Lock on one job, held to the same gate as the board's button. A card
also keeps which job each of its choices means
(choice_ids), since the labels alone can't say: two sales can share a name, and the job list can
change before Jim answers. Wording lives in messages.templates; this module holds the model and
the grammar.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

# The P1a cards (design section 3.4 and the integration spec's filing pipeline), then P1b's: a
# program past its sheet size, which Jim's answer can clear (P1b build spec 5.6). Then P4's: a fix to
# approve (decision 7a), and the one redo of a fix Mozaik undid (design 5, "Approvals and survival").
CARD_KINDS = ("which_job", "closet_or_garage", "fetch_failed", "ic_changed", "row_changed",
              "remake", "design_mismatch", "sheet_size", "fix", "fix_undone")
STATES = ("open", "answered", "withdrawn")
REMIND_AT_DAYS = (1, 3, 7)         # open cards come back in the digest (design 4, "Timing")
NO_LETTERS_PREFIX = "ROW"          # for a Fab row typed with no letters at all
REJECTION = "I didn't understand. Try: {example}"
SAFE_EXAMPLE = "pause"             # with no open card: pausing is always safe (design 6)
_CARD_ID = re.compile(r"[A-Z]{1,3}-[1-9][0-9]*")
_TYPED_ID = re.compile(r"\s*(?P<id>[A-Za-z]{1,3}-[1-9][0-9]*)(?![A-Za-z0-9-])")
_ANSWER = re.compile(r"\s*(?P<id>[A-Za-z]{1,3}-[1-9][0-9]*)\s+(?P<words>\S.*?)\s*", re.DOTALL)
_ADD_JOB = re.compile(r"\s*add\s+job\s+(?P<target>\S.*?)\s*", re.IGNORECASE | re.DOTALL)
_CHECK = re.compile(r"\s*check\s+(?P<name>\S.*?)\s*", re.IGNORECASE | re.DOTALL)
_LOCK = re.compile(r"\s*lock\s+(?P<name>\S.*?)\s*", re.IGNORECASE | re.DOTALL)
_DIGITS = re.compile(r"[0-9]+")
_LEADING_NUMBER = re.compile(r"(?P<n>[0-9]+)(?:st|nd|rd|th)?(?![0-9])")


# ----- what a card asks


@dataclass(frozen=True)
class CardSpec:
    """A card before it has an ID: what templates produce for a question.

    job: the job's frozen name, when the card is about a known job.
    row: the Fab row's Job Name as Barbara typed it, when the card is about a row.
    text: one line explaining the question; choices: the fixed answers, in order (Jim may
        answer with a choice's number or its label); default_choice: what NG Runner does while
        nobody answers, or None when it simply waits.
    choice_ids: parallel to choices: the Studio project ID each choice means, or None for one
        that means no job ("None of these", "Keep <job>"). A job is linked only through this,
        never through a label. Left out, no choice means a job.
    """

    kind: str
    job: str | None
    row: str | None
    text: str
    choices: tuple[str, ...]
    default_choice: str | None = None
    choice_ids: tuple[str | None, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "choice_ids", _ids_of(self.choice_ids, self.choices))

    @property
    def prefix(self) -> str:
        """The prefix its ID is allocated under: card_prefix of its job, else of its row."""
        return card_prefix(self.job or self.row or "")


@dataclass(frozen=True)
class Card:
    """A card as the store keeps it (the cards table: the store's CardRecord, field for field),
    plus job and row for display: the job's name, the Fab row's text, read from the jobs and
    fab_rows tables by studio_id and fab_row_id. Answers are kept verbatim."""

    id: str
    kind: str
    job: str | None
    row: str | None
    text: str
    choices: tuple[str, ...]
    created_at: datetime
    default_choice: str | None = None
    studio_id: str | None = None
    fab_row_id: int | None = None
    state: str = "open"
    answer: str | None = None
    answer_words: str | None = None
    answered_by: str | None = None
    answered_at: datetime | None = None
    channel: str | None = None
    reminded_at: tuple[datetime, ...] = field(default=())
    choice_ids: tuple[str | None, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "choices", tuple(self.choices))
        object.__setattr__(self, "reminded_at", tuple(self.reminded_at))
        object.__setattr__(self, "choice_ids", _ids_of(self.choice_ids, self.choices))
        problem = _card_problem(self)
        if problem:
            raise ValueError(f"card {self.id!r}: {problem}")


# ----- prefixes and IDs


def card_prefix(name: str) -> str:
    """The first three letters of a job's name (or a Fab row's text), in capitals and without
    accents, so Jim can type it: Testerson_101426 -> TES, O'Testa -> OTE. A name with fewer
    letters keeps what it has; one with none gets ROW."""
    letters = [ch for ch in unicodedata.normalize("NFKD", name) if ch.isascii() and ch.isalpha()]
    return "".join(letters[:3]).upper() or NO_LETTERS_PREFIX


def new_card(
    spec: CardSpec,
    *,
    created_at: datetime,
    allocate: Callable[[str], int | str],
    studio_id: str | None = None,
    fab_row_id: int | None = None,
) -> Card:
    """A new open card. allocate(prefix) is the store's: it gives the next number for that
    prefix, or the whole ID as store.allocate_card_id does ("TES-2"), and never the same one
    twice, so IDs are never reused. (store.add_card allocates and inserts in one step; turn
    its record into a Card with card_from_record.)"""
    prefix = spec.prefix
    given = allocate(prefix)
    if isinstance(given, str):
        found = _CARD_ID.fullmatch(given)
        if not (found and given.startswith(f"{prefix}-")):
            raise ValueError(f"the store gave an ID that is not {prefix}- and a number from 1")
        card_id = given
    elif isinstance(given, int) and not isinstance(given, bool) and given >= 1:
        card_id = f"{prefix}-{given}"
    else:
        raise ValueError(f"the card number for {prefix} must be a whole number from 1")
    return Card(
        id=card_id, kind=spec.kind, job=spec.job, row=spec.row, text=spec.text,
        choices=spec.choices, created_at=created_at, default_choice=spec.default_choice,
        studio_id=studio_id, fab_row_id=fab_row_id, choice_ids=spec.choice_ids,
    )


def card_from_record(record: object, *, job: str | None = None, row: str | None = None) -> Card:
    """A card from the store's record of it (any object with the cards table's fields), with
    the job's name or the Fab row's text to show it by."""
    return Card(job=job, row=row, **{name: getattr(record, name) for name in _RECORD_FIELDS})


_RECORD_FIELDS = ("id", "kind", "text", "choices", "created_at", "default_choice", "studio_id",
                  "fab_row_id", "state", "answer", "answer_words", "answered_by", "answered_at",
                  "channel", "reminded_at", "choice_ids")


# ----- the command box


@dataclass(frozen=True)
class Command:
    """What Jim typed, understood.

    kind: "answer", "pause", "resume", "add_job", "check" or "lock". text: exactly what was typed
    (kept verbatim with the answer). For an answer: card_id, choice (the label as on the card) and
    words (his words after the card ID, as typed). For add_job: name or studio_id, as typed. For
    check and lock: name, its runs of spaces made one; which job it names, whole, is the store's to
    say (checks.jobchecks.job_named), never a guess here.
    """

    kind: str
    text: str
    card_id: str | None = None
    choice: str | None = None
    words: str | None = None
    name: str | None = None
    studio_id: str | None = None


@dataclass(frozen=True)
class Rejection:
    """Not a command. text is exactly "I didn't understand. Try: <example>"; the card stays
    with Jim."""

    text: str
    typed: str


def parse_command(text: str, open_cards: Sequence[Card]) -> Command | Rejection:
    """Read the command box strictly: "<card ID> <choice number or label>", "pause", "resume",
    "add job <name or Studio project ID>", "check <job>" (P3's Check now) or "lock <job>" (P4's Lock
    of the whole job). Case and runs of
    spaces don't matter; nothing else is guessed. A number that could mean two choices (2 when
    the choices are 2nd and 3rd) is refused, never picked."""
    cards = [card for card in open_cards if card.state == "open"]
    folded = _folded(text)
    if folded in ("pause", "resume"):
        return Command(folded, text)
    add = _ADD_JOB.fullmatch(text)
    if add:
        target = add["target"]
        if _DIGITS.fullmatch(target):
            return Command("add_job", text, studio_id=target)
        return Command("add_job", text, name=target)
    check = _CHECK.fullmatch(text)
    if check:
        return Command("check", text, name=" ".join(check["name"].split()))
    lock = _LOCK.fullmatch(text)
    if lock:
        return Command("lock", text, name=" ".join(lock["name"].split()))
    named = _named_card(text, cards)
    answer = _ANSWER.fullmatch(text)
    if named is not None and answer:
        choice = _choice(named, answer["words"])
        if choice is not None:
            return Command("answer", text, card_id=named.id, choice=choice,
                           words=answer["words"])
    return Rejection(REJECTION.format(example=_example(named or next(iter(cards), None))), text)


def _named_card(text: str, cards: Sequence[Card]) -> Card | None:
    typed = _TYPED_ID.match(text)
    if typed is None:
        return None
    return next((card for card in cards if card.id == typed["id"].upper()), None)


def _choice(card: Card, words: str) -> str | None:
    """The one choice words name, by label or by number; None when none or more than one."""
    wanted = _folded(words)
    named = {choice for choice in card.choices if _folded(choice) == wanted}
    if _DIGITS.fullmatch(wanted):
        number = int(wanted)
        if 1 <= number <= len(card.choices):
            named.add(card.choices[number - 1])
        named |= {choice for choice in card.choices if _reads_as(choice) == number}
    return next(iter(named)) if len(named) == 1 else None


def _reads_as(label: str) -> int | None:
    """The number a label starts with, if any: "2nd" and "2 shelves" read as 2."""
    found = _LEADING_NUMBER.match(_folded(label))
    return int(found["n"]) if found else None


def _example(card: Card | None) -> str:
    """A command that answers card, for the rejection to show: its first choice by label when
    that is one word, else by number."""
    if card is None:
        return SAFE_EXAMPLE
    first = card.choices[0]
    if " " not in first and _choice(card, first) == first:
        return f"{card.id} {first}"
    if _choice(card, "1") == first:
        return f"{card.id} 1"
    return f"{card.id} {first}"


# ----- answers and reminders


def answered(card: Card, choice: str, *, words: str, by: str, at: datetime,
             channel: str) -> Card:
    """card with Jim's answer recorded: the choice exactly as on the card, his own words
    verbatim (what he typed, or the button's label), who, when and through which channel
    (board, command box...). A card is answered once."""
    if card.state != "open":
        raise ValueError(f"card {card.id} is {card.state}, so it can't be answered again")
    if choice not in card.choices:
        raise ValueError(f"card {card.id} has no choice {choice!r}")
    return replace(card, state="answered", answer=choice, answer_words=words, answered_by=by,
                   answered_at=at, channel=channel)


def age_days(card: Card, now: datetime) -> int:
    """Whole days the card has been open: 0 until it is a day old."""
    return max(0, (now - card.created_at) // timedelta(days=1))


def reminder_due(card: Card, now: datetime) -> int | None:
    """The reminder an open card is due in a digest at now: 1, 3 or 7 (days), or None. Each
    comes once; a digest that was missed (the PC was off) brings a card back once, at the
    latest age it reached, not once per missed day. card.reminded_at holds the times it came
    back."""
    if card.state != "open":
        return None
    covered = max((age_days(card, at) for at in card.reminded_at), default=0)
    due = [days for days in REMIND_AT_DAYS if covered < days <= age_days(card, now)]
    return due[-1] if due else None


# ----- checks


def _card_problem(card: Card) -> str | None:
    if not _CARD_ID.fullmatch(card.id):
        return "its ID is not letters, a hyphen and a number (TES-2)"
    if card.kind not in CARD_KINDS:
        return f"{card.kind!r} is not a kind of card"
    if not (card.job or card.row):
        return "it is about neither a job nor a Fab row"
    if card.state not in STATES:
        return f"{card.state!r} is not a card state"
    if not _one_line(card.text):
        return "its text is not one plain line"
    return (_choices_problem(card.choices, card.default_choice)
            or _choice_ids_problem(card.choice_ids, card.choices) or _times_problem(card))


def _choices_problem(choices: Sequence[str], default: str | None) -> str | None:
    if not choices:
        return "it has no choices"
    if not all(_one_line(choice) for choice in choices):
        return "a choice is not one plain line"
    if len({_folded(choice) for choice in choices}) != len(choices):
        return "two choices read the same"
    if default is not None and default not in choices:
        return "its default is not one of its choices"
    return None


def _ids_of(ids: Sequence[str | None] | None, choices: Sequence[str]) -> tuple[str | None, ...]:
    """ids as a tuple; none given means that no choice points at a job."""
    if isinstance(ids, str):
        raise TypeError("choice_ids is a list with one entry per choice, not one string")
    return tuple(ids) if ids else (None,) * len(choices)


def _choice_ids_problem(ids: Sequence[str | None], choices: Sequence[str]) -> str | None:
    if len(ids) != len(choices):
        return "its choice IDs don't line up with its choices"
    if not all(one is None or _one_line(one) for one in ids):
        return "a choice's Studio project ID is not plain text"
    return None


def _times_problem(card: Card) -> str | None:
    times = [card.created_at, *card.reminded_at]
    if card.answered_at is not None:
        times.append(card.answered_at)
    if not all(isinstance(t, datetime) and t.utcoffset() is not None for t in times):
        return "a time on it is not timezone-aware"
    return None


def _one_line(text: object) -> bool:
    return (isinstance(text, str) and text == text.strip() and text != ""
            and not any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in text))


def _folded(text: str) -> str:
    """How typed text is compared: case and runs of spaces aside."""
    return " ".join(text.split()).casefold()
