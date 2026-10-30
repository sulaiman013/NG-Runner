"""
The outbox: every message NG Runner sends, and how far it got (build spec sections 3 and 4;
design 4, "Silence and failure": receipts).

NG Runner never sends mail itself: it writes a signed outbox file to the control folder and
the Apps Script sends it, then writes a receipt back. A row's id is its envelope's id (the file
is outbox/<id>.json) and its seq the envelope's sequence number for the outbox stream, both
unique for good. States: "queued" (row made, file not written yet), "written" (in the control
folder), then "sent" or "failed" once a receipt says so. A message written over 5 minutes ago
with no receipt turns the tray red and sends urgent items to Pushover directly (fallback_at).

recipient is a role (config.ROLES), never an address: the Apps Script resolves roles to
addresses in its own settings, so no address is stored here or written into a file.
"""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from ngrunner.config import ROLES
from ngrunner.store.db import (
    Db, dumps, iso, loads, one_of, optional_text, session, text_value, time_of, time_or_none,
    whole_number,
)

CHANNELS = ("email", "slack", "pushover")
OUTBOX_STATES = ("queued", "written", "sent", "failed")
_ID = re.compile(r"[0-9a-f]{32}")  # an envelope id (signing.new_id)


@dataclass(frozen=True)
class OutboxRecord:
    """One message: lines are its numbered lines, as sent; ref names what it is about (a card
    ID, a Studio project ID) for the board; the times say how far it got."""

    id: str
    seq: int
    channel: str
    recipient: str
    subject: str
    lines: tuple[str, ...]
    urgent: bool
    ref: str | None
    created_at: datetime
    written_at: datetime | None
    receipt_at: datetime | None
    fallback_at: datetime | None
    state: str


def add_outbox(
    db: Db,
    *,
    id: str,
    seq: int,
    channel: str,
    recipient: str,
    subject: str,
    lines: Sequence[str],
    urgent: bool,
    created_at: datetime,
    ref: str | None = None,
) -> OutboxRecord:
    """Record a message before its file is written (state "queued"). id is its envelope id
    (32 lowercase hex), seq its envelope seq (take it with next_seq(db, "outbox") in the same
    transaction); a seq or id already used is refused (StoreError)."""
    if not isinstance(urgent, bool):
        raise ValueError("urgent must be True or False")
    values = (
        _id(id), whole_number(seq, "a sequence number", at_least=1),
        one_of(channel, CHANNELS, "a channel"), one_of(recipient, ROLES, "a recipient's role"),
        text_value(subject, "a subject"), _lines(lines), int(urgent),
        optional_text(ref, "a reference"), iso(created_at, "created_at"),
    )
    with session(db, write=True) as conn:
        conn.execute(
            "INSERT INTO outbox (id, seq, channel, recipient, subject, lines, urgent, ref,"
            " created_at, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued')",
            values,
        )
        return _message(conn, values[0])


def get_outbox(db: Db, outbox_id: str) -> OutboxRecord | None:
    with session(db, write=False) as conn:
        return _message(conn, _id(outbox_id))


def list_outbox(db: Db, *, state: str | None = None) -> list[OutboxRecord]:
    """Messages in seq order; only those in state, when given."""
    sql, params = "SELECT * FROM outbox", []
    if state is not None:
        sql += " WHERE state = ?"
        params.append(one_of(state, OUTBOX_STATES, "an outbox state"))
    with session(db, write=False) as conn:
        rows = conn.execute(sql + " ORDER BY seq", params).fetchall()
    return [_record(r) for r in rows]


def mark_outbox_written(db: Db, outbox_id: str, *, at: datetime) -> bool:
    """The message's file is in the control folder. True when recorded; False when it was not
    queued any more. No such message: LookupError."""
    return _change(db, outbox_id, "state = 'written', written_at = ?", (iso(at, "at"),),
                   "state = 'queued'")


def record_receipt(db: Db, outbox_id: str, *, at: datetime, ok: bool) -> bool:
    """The Apps Script's receipt: sent (ok) or failed. Taken once, for a message queued or
    written (its file may be in before its row says so); a second receipt changes nothing and
    gives False. No such message: LookupError."""
    if not isinstance(ok, bool):
        raise ValueError("ok must be True or False")
    return _change(db, outbox_id, "state = ?, receipt_at = ?",
                   ("sent" if ok else "failed", iso(at, "at")), "state IN ('queued', 'written')")


def mark_outbox_fallback(db: Db, outbox_id: str, *, at: datetime) -> bool:
    """The message also went straight to Pushover. True the first time; False after. No such
    message: LookupError."""
    return _change(db, outbox_id, "fallback_at = ?", (iso(at, "at"),), "fallback_at IS NULL")


def outbox_without_receipt(db: Db, *, written_before: datetime) -> list[OutboxRecord]:
    """Messages written at or before written_before (now minus 5 minutes) with no receipt yet,
    in seq order."""
    with session(db, write=False) as conn:
        rows = conn.execute(
            "SELECT * FROM outbox WHERE state = 'written' AND written_at <= ? ORDER BY seq",
            (iso(written_before, "written_before"),),
        ).fetchall()
    return [_record(r) for r in rows]


def _change(db: Db, outbox_id: str, sets: str, params: tuple[object, ...], only: str) -> bool:
    oid = _id(outbox_id)
    with session(db, write=True) as conn:
        # sets and only are fixed text from this module; values are parameters
        cursor = conn.execute(f"UPDATE outbox SET {sets} WHERE id = ? AND {only}", (*params, oid))
        if cursor.rowcount == 0 and _message(conn, oid) is None:
            raise LookupError("there is no outbox message with that id in the store")
        return cursor.rowcount == 1


def _id(value: object) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("an outbox id is an envelope id: 32 lowercase hex characters")
    return value


def _lines(value: object) -> str:
    if not isinstance(value, list | tuple) or not value:
        raise ValueError("a message needs its lines, as a list of texts")
    return dumps([text_value(line, "a message line") for line in value])


def _message(conn: sqlite3.Connection, oid: str) -> OutboxRecord | None:
    row = conn.execute("SELECT * FROM outbox WHERE id = ?", (oid,)).fetchone()
    return None if row is None else _record(row)


def _record(row: sqlite3.Row) -> OutboxRecord:
    return OutboxRecord(
        id=row["id"],
        seq=row["seq"],
        channel=row["channel"],
        recipient=row["recipient"],
        subject=row["subject"],
        lines=tuple(loads(row["lines"])),
        urgent=bool(row["urgent"]),
        ref=row["ref"],
        created_at=time_of(row["created_at"]),
        written_at=time_or_none(row["written_at"]),
        receipt_at=time_or_none(row["receipt_at"]),
        fallback_at=time_or_none(row["fallback_at"]),
        state=row["state"],
    )
