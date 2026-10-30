"""
Cards and card numbers (build spec section 4; design 3.3, "Card"; GLOSSARY: Card).

A card is a question for Jim with fixed choices; his answer is recorded verbatim, with who
gave it, when and through which channel. A card's ID is its prefix and the next number for
that prefix (ROD-2). Numbers are never reused: card_numbers only counts up, under the write
lock, so two threads or processes never get the same ID, and a withdrawn card, or a number
handed out and never used, is skipped for good. Jim may still have an old card in an email or
a toast, so an ID must never come to mean another question.

States: "open", then "answered" or "withdrawn"; an answered or withdrawn card never changes.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from ngrunner.store.db import (
    Db, code_value, dumps, iso, key_text, loads, one_of, session, text_value, time_of,
    time_or_none, whole_number,
)

CARD_STATES = ("open", "answered", "withdrawn")
_PREFIX_MAX = 16


@dataclass(frozen=True)
class CardRecord:
    """One card. choices are the fixed answers, in order; answer is the one Jim chose and
    answer_words his own words, verbatim; reminded_at, when the digest brought it back."""

    id: str
    studio_id: str | None
    fab_row_id: int | None
    kind: str
    text: str
    choices: tuple[str, ...]
    default_choice: str | None
    created_at: datetime
    state: str
    answer: str | None
    answer_words: str | None
    answered_by: str | None
    answered_at: datetime | None
    channel: str | None
    reminded_at: tuple[datetime, ...]


# ----- card numbers


def allocate_card_id(db: Db, prefix: str) -> str:
    """The next card ID for prefix ("ROD" gives ROD-1, ROD-2...). A prefix is uppercase
    letters (cards.card_prefix makes it from the job name). Never the same ID twice, across
    threads and processes. add_card allocates its own; this is for an ID needed first."""
    checked = _prefix(prefix)
    with session(db, write=True) as conn:
        return _next_id(conn, checked)


def _next_id(conn: sqlite3.Connection, prefix: str) -> str:
    number = conn.execute(
        "INSERT INTO card_numbers (prefix, last) VALUES (?, 1)"
        " ON CONFLICT (prefix) DO UPDATE SET last = last + 1 RETURNING last",
        (prefix,),
    ).fetchone()[0]
    return f"{prefix}-{number}"


def _prefix(value: object) -> str:
    if not (isinstance(value, str) and 0 < len(value) <= _PREFIX_MAX and value.isalpha()
            and value == value.upper()):
        raise ValueError("a card prefix is uppercase letters, like ROD")
    return value


# ----- cards


def add_card(
    db: Db,
    *,
    prefix: str,
    kind: str,
    text: str,
    choices: Sequence[str],
    created_at: datetime,
    studio_id: str | None = None,
    fab_row_id: int | None = None,
    default_choice: str | None = None,
) -> CardRecord:
    """A new open card, with the next ID for prefix, given in the same transaction. choices
    are distinct non-blank texts; default_choice, when given, is one of them. A refused card
    uses no number."""
    options = _choices(choices)
    if default_choice is not None and default_choice not in options:
        raise ValueError("the default choice must be one of the card's choices")
    values = (
        None if studio_id is None else key_text(studio_id, "a Studio project ID"),
        None if fab_row_id is None else whole_number(fab_row_id, "a Fab row id", at_least=1),
        code_value(kind, "a card's kind", "which_job"), text_value(text, "a card's text"),
        dumps(list(options)), default_choice, iso(created_at, "created_at"),
    )
    checked = _prefix(prefix)
    with session(db, write=True) as conn:
        card_id = _next_id(conn, checked)
        conn.execute(
            "INSERT INTO cards (id, studio_id, fab_row_id, kind, text, choices, default_choice,"
            " created_at, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')",
            (card_id, *values),
        )
        return _card(conn, card_id)


def get_card(db: Db, card_id: str) -> CardRecord | None:
    with session(db, write=False) as conn:
        return _card(conn, key_text(card_id, "a card ID"))


def list_cards(
    db: Db,
    *,
    state: str | None = "open",
    studio_id: str | None = None,
    fab_row_id: int | None = None,
) -> list[CardRecord]:
    """Cards in state (None: every state), only the job's and the row's when given; oldest
    first."""
    clauses, params = [], []
    for column, value in (
        ("state", None if state is None else one_of(state, CARD_STATES, "a card's state")),
        ("studio_id", None if studio_id is None else key_text(studio_id, "a Studio project ID")),
        ("fab_row_id", None if fab_row_id is None else whole_number(fab_row_id, "a Fab row id")),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")  # column names from this list, values as parameters
            params.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with session(db, write=False) as conn:
        rows = conn.execute(
            f"SELECT * FROM cards{where} ORDER BY created_at, id", params
        ).fetchall()
    return [_record(r) for r in rows]


def answer_card(
    db: Db,
    card_id: str,
    *,
    answer: str,
    answer_words: str,
    answered_by: str,
    answered_at: datetime,
    channel: str,
) -> bool:
    """Record the answer to an open card: answer is one of its choices (else ValueError),
    answer_words exactly what was typed or clicked, answered_by a role ("jim"), channel where
    it came from ("board", "command", "email"). True when recorded; False when the card was no
    longer open (already answered or withdrawn: nothing changes). No such card: LookupError."""
    cid = key_text(card_id, "a card ID")
    values = (
        text_value(answer, "an answer"), text_value(answer_words, "the answer's words"),
        code_value(answered_by, "who answered", "jim"), iso(answered_at, "answered_at"),
        code_value(channel, "a channel", "board"),
    )
    with session(db, write=True) as conn:
        card = _card(conn, cid)
        if card is None:
            raise LookupError("there is no card with that ID in the store")
        if card.state != "open":
            return False
        if values[0] not in card.choices:
            raise ValueError("an answer must be one of the card's choices")
        conn.execute(
            "UPDATE cards SET state = 'answered', answer = ?, answer_words = ?, answered_by = ?,"
            " answered_at = ?, channel = ? WHERE id = ? AND state = 'open'",
            (*values, cid),
        )
        return True


def withdraw_card(db: Db, card_id: str) -> bool:
    """Withdraw an open card (its question no longer stands). True when done; False when it
    was no longer open. No such card: LookupError."""
    cid = key_text(card_id, "a card ID")
    with session(db, write=True) as conn:
        cursor = conn.execute(
            "UPDATE cards SET state = 'withdrawn' WHERE id = ? AND state = 'open'", (cid,)
        )
        if cursor.rowcount == 0 and _card(conn, cid) is None:
            raise LookupError("there is no card with that ID in the store")
        return cursor.rowcount == 1


def record_reminder(db: Db, card_id: str, *, at: datetime) -> None:
    """Note that the card was brought back to Jim at this time (the digest's 1, 3 and 7
    days). No such card: LookupError."""
    cid, when = key_text(card_id, "a card ID"), iso(at, "at")
    with session(db, write=True) as conn:
        row = conn.execute("SELECT reminded_at FROM cards WHERE id = ?", (cid,)).fetchone()
        if row is None:
            raise LookupError("there is no card with that ID in the store")
        conn.execute(
            "UPDATE cards SET reminded_at = ? WHERE id = ?",
            (dumps([*loads(row["reminded_at"]), when]), cid),
        )


def _choices(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple) or not value:
        raise ValueError("a card needs its choices, as a list of texts")
    options = tuple(text_value(choice, "a choice") for choice in value)
    if len(set(options)) != len(options):
        raise ValueError("a card's choices must differ from each other")
    return options


def _card(conn: sqlite3.Connection, cid: str) -> CardRecord | None:
    row = conn.execute("SELECT * FROM cards WHERE id = ?", (cid,)).fetchone()
    return None if row is None else _record(row)


def _record(row: sqlite3.Row) -> CardRecord:
    return CardRecord(
        id=row["id"],
        studio_id=row["studio_id"],
        fab_row_id=row["fab_row_id"],
        kind=row["kind"],
        text=row["text"],
        choices=tuple(loads(row["choices"])),
        default_choice=row["default_choice"],
        created_at=time_of(row["created_at"]),
        state=row["state"],
        answer=row["answer"],
        answer_words=row["answer_words"],
        answered_by=row["answered_by"],
        answered_at=time_or_none(row["answered_at"]),
        channel=row["channel"],
        reminded_at=tuple(time_of(t) for t in loads(row["reminded_at"])),
    )
