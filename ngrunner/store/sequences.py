"""
Sequences and the replay guard for the control folder's signed envelopes (build spec section
3, "Sequences and replay"; section 4: seqs, seen_ids).

Each sender numbers its envelopes per stream (one stream per kind: heartbeat, outbox...),
counting up by 1. A receiver acts on an envelope only if its seq is above the last it accepted
on that stream and its id is new; anything else is a replay, or the same file read again, and
is ignored. Both counters live in one table, so the store keeps them apart itself: next_seq
counts what NG Runner sends and accept_seq what it receives, each under its own key ("out:" and
"in:" before the stream name), and they never mix, even when one name is used both ways.

Everything runs under the write lock, so two threads or processes never get the same seq, and a
seq or id is accepted once. accept_envelope applies both rules in one step: it records the seq
and the id together, or neither.
"""
from __future__ import annotations

import sqlite3

from ngrunner.store.db import Db, code_value, key_text, session, whole_number

_OUT, _IN = "out:", "in:"


def next_seq(db: Db, stream: str) -> int:
    """The next sequence number NG Runner sends on stream: 1, then 2, 3... never twice."""
    key = _OUT + _stream(stream)
    with session(db, write=True) as conn:
        return int(conn.execute(
            "INSERT INTO seqs (stream, last) VALUES (?, 1)"
            " ON CONFLICT (stream) DO UPDATE SET last = last + 1 RETURNING last",
            (key,),
        ).fetchone()[0])


def last_sent_seq(db: Db, stream: str) -> int:
    """The last sequence number sent on stream; 0 before the first."""
    return _last(db, _OUT + _stream(stream))


def accept_seq(db: Db, stream: str, seq: int) -> bool:
    """True, and recorded as the last accepted, when seq is above the last one accepted on
    stream (any seq from 1 on a new stream); False, with nothing changed, otherwise."""
    key, number = _IN + _stream(stream), _seq(seq)
    with session(db, write=True) as conn:
        return _accept(conn, key, number)


def last_accepted_seq(db: Db, stream: str) -> int:
    """The last sequence number accepted on stream; 0 before the first."""
    return _last(db, _IN + _stream(stream))


def seen_id(db: Db, stream: str, id: str) -> bool:
    """Record id as seen on stream. True the first time (new: act on it), False every time
    after (seen: ignore it)."""
    values = (_stream(stream), key_text(id, "an envelope id"))
    with session(db, write=True) as conn:
        return _first_sight(conn, *values)


def accept_envelope(db: Db, stream: str, *, seq: int, id: str) -> bool:
    """Both replay rules at once: True when seq is above the last accepted on stream and id is
    new there; then both are recorded. False otherwise, and neither is recorded."""
    name, number, envelope_id = _stream(stream), _seq(seq), key_text(id, "an envelope id")
    with session(db, write=True) as conn:
        known = conn.execute(
            "SELECT 1 FROM seen_ids WHERE stream = ? AND id = ?", (name, envelope_id)
        ).fetchone()
        if known is not None or not _accept(conn, _IN + name, number):
            return False
        return _first_sight(conn, name, envelope_id)


def _accept(conn: sqlite3.Connection, key: str, number: int) -> bool:
    row = conn.execute(
        "INSERT INTO seqs (stream, last) VALUES (?, ?) ON CONFLICT (stream) DO UPDATE"
        " SET last = excluded.last WHERE excluded.last > seqs.last RETURNING last",
        (key, number),
    ).fetchone()
    return row is not None


def _first_sight(conn: sqlite3.Connection, stream: str, envelope_id: str) -> bool:
    cursor = conn.execute(
        "INSERT INTO seen_ids (stream, id) VALUES (?, ?) ON CONFLICT (stream, id) DO NOTHING",
        (stream, envelope_id),
    )
    return cursor.rowcount == 1


def _last(db: Db, key: str) -> int:
    with session(db, write=False) as conn:
        row = conn.execute("SELECT last FROM seqs WHERE stream = ?", (key,)).fetchone()
    return 0 if row is None else int(row["last"])


def _stream(value: object) -> str:
    return code_value(value, "a stream", "heartbeat")


def _seq(value: object) -> int:
    return whole_number(value, "a sequence number", at_least=1)
