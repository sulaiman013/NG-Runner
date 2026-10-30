"""
Sequences and the replay guard for the control folder's signed envelopes (build spec section
3, "Sequences and replay"; section 4: seqs, seen_ids).

Each sender numbers its envelopes per stream (one stream per kind: heartbeat, outbox...),
counting up by 1. A receiver acts on an envelope only if its seq is above the last it accepted
on that stream and its id is new; anything else is a replay, or the same file read again, and
is ignored. Both counters live in one table, so the store keeps them apart itself: next_seq
counts what NG Runner sends and accept_seq what it receives, each under its own key ("out:" and
"in:" before the stream name), and they never mix, even when one name is used both ways.

The Apps Script remembers its last number for good, so what NG Runner sends must stay above it
when its own memory goes. A new database starts each stream at the current Unix second (next_seq's
start); a restored one jumps every stream ahead (jump_after_restore).

Everything runs under the write lock, so two threads or processes never get the same seq, and a
seq or id is accepted once. accept_envelope applies both rules in one step: it records the seq
and the id together, or neither.
"""
from __future__ import annotations

import sqlite3

from ngrunner.store.db import Db, code_value, key_text, session, whole_number

_OUT, _IN = "out:", "in:"
# Far above what can have been used since the oldest backup kept: 30 days of one number a minute
# is 43,200, and no three letters of a surname get anywhere near 1000 cards in 30 days.
_SEQ_JUMP, _CARD_JUMP = 1_000_000, 1000


def next_seq(db: Db, stream: str, *, start: int = 1) -> int:
    """The next sequence number NG Runner sends on stream, never twice: a new stream begins at
    start, then counts up by 1, and a stream already there carries on whatever start says. The
    messenger and the heartbeat pass the current Unix second, int(clock.now().timestamp()), so a
    new database on a control folder already used is never ignored by the Apps Script: streams
    count far more slowly than seconds pass, so that second is above all they sent before."""
    key, first = _OUT + _stream(stream), whole_number(start, "a stream's start", at_least=1)
    with session(db, write=True) as conn:
        return int(conn.execute(
            "INSERT INTO seqs (stream, last) VALUES (?, ?)"
            " ON CONFLICT (stream) DO UPDATE SET last = last + 1 RETURNING last",
            (key, first),
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


def prune_seen_ids(db: Db, *, keep_last: int = 5000) -> int:
    """Forget all but the last keep_last ids seen on each stream, and say how many went (seen_ids
    grows by about 300 rows a day, so this runs nightly). Per stream, so a busy one never pushes
    out a quiet one's. A forgotten id is safe: accept_envelope's seq rule still refuses that
    envelope, and a receipt, which has only this guard, is taken once by record_receipt. Rowids
    follow the order ids were seen in, as nothing here ever vacuums the database."""
    keep = whole_number(keep_last, "how many seen ids to keep", at_least=1)
    with session(db, write=True) as conn:
        return conn.execute(
            "DELETE FROM seen_ids WHERE rowid IN (SELECT seen FROM ("
            "SELECT rowid AS seen, row_number() OVER (PARTITION BY stream ORDER BY rowid DESC)"
            " AS newest FROM seen_ids) WHERE newest > ?)",
            (keep,),
        ).rowcount


def jump_after_restore(db: Db | sqlite3.Connection) -> None:
    """Make a restored database safe to run on a new PC. A backup is older than the last files
    NG Runner sent and the last cards Jim saw, so every outgoing stream jumps by 1,000,000 (the
    Apps Script ignores any number it has seen) and every card number by 1000 (an ID must never
    come to mean another question). What NG Runner has received keeps its place. Also takes a
    plain connection, for backup.for_a_new_pc, whose database is in memory with no file for a
    Store: there the caller commits."""
    if isinstance(db, sqlite3.Connection):
        _jump(db)
        return
    with session(db, write=True) as conn:
        _jump(conn)


def _jump(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE seqs SET last = last + ? WHERE stream LIKE ?", (_SEQ_JUMP, _OUT + "%"))
    conn.execute("UPDATE card_numbers SET last = last + ?", (_CARD_JUMP,))


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
