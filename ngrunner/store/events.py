"""
The events log and the DEV lane (build spec section 4; design 4, "Silence and failure" and
"Noise").

Events are NG Runner's history: one line per thing that happened, in plain words, each in a
lane: ACT (something for Jim), LOG (recorded, not raised) or DEV (for Sulaiman only). The
board's History page and the digest's "jobs touched" lines read them; they are only ever added.

DEV items are the DEV lane's open problems, Sulaiman's to fix, which Jim never sees until one
is over 48 hours old (then he is told once, with the manual fallback). An alarm is raised on
every tick while its condition holds, so an open item with the same code and text is returned
instead of opening a second: one standing problem, one item.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ngrunner.store.db import (
    Db, code_value, iso, key_text, one_of, session, text_value, time_of, time_or_none,
    whole_number,
)

LANES = ("ACT", "LOG", "DEV")
DEV_STATES = ("open", "closed")


@dataclass(frozen=True)
class EventRecord:
    id: int
    at: datetime
    studio_id: str | None
    kind: str
    text: str
    lane: str


@dataclass(frozen=True)
class DevRecord:
    """One DEV item: code is a short lowercase name for the problem, text says it in words;
    told_jim_at is when Jim was told of it (None: not yet)."""

    id: int
    at: datetime
    code: str
    text: str
    state: str
    told_jim_at: datetime | None


# ----- events


def log_event(
    db: Db, *, at: datetime, kind: str, text: str, lane: str = "LOG", studio_id: str | None = None
) -> EventRecord:
    """Add one event to the log (kind: a short lowercase name, like filed)."""
    values = (
        iso(at, "at"), None if studio_id is None else key_text(studio_id, "a Studio project ID"),
        code_value(kind, "an event's kind", "filed"), text_value(text, "an event's text"),
        one_of(lane, LANES, "a lane"),
    )
    with session(db, write=True) as conn:
        cursor = conn.execute(
            "INSERT INTO events (at, studio_id, kind, text, lane) VALUES (?, ?, ?, ?, ?)", values
        )
        return _event(conn, int(cursor.lastrowid))


def list_events(
    db: Db,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    studio_id: str | None = None,
    lane: str | None = None,
) -> list[EventRecord]:
    """Events in time order, from since (included) to until (not included), only the job's
    and the lane's, when given."""
    clauses, params = [], []
    for clause, value in (
        ("at >= ?", None if since is None else iso(since, "since")),
        ("at < ?", None if until is None else iso(until, "until")),
        ("studio_id = ?",
         None if studio_id is None else key_text(studio_id, "a Studio project ID")),
        ("lane = ?", None if lane is None else one_of(lane, LANES, "a lane")),
    ):
        if value is not None:
            clauses.append(clause)  # fixed text from this list; values as parameters
            params.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with session(db, write=False) as conn:
        rows = conn.execute(f"SELECT * FROM events{where} ORDER BY at, id", params).fetchall()
    return [_event_record(r) for r in rows]


# ----- the DEV lane


def open_dev(db: Db, *, at: datetime, code: str, text: str) -> DevRecord:
    """Open a DEV item; while an open one with the same code and text stands, that one is
    returned unchanged instead."""
    values = (iso(at, "at"), code_value(code, "a DEV code", "fab_export_stale"),
              text_value(text, "a DEV item's text"))
    with session(db, write=True) as conn:
        found = conn.execute(
            "SELECT * FROM dev WHERE state = 'open' AND code = ? AND text = ? ORDER BY id",
            values[1:],
        ).fetchone()
        if found is not None:
            return _dev_record(found)
        cursor = conn.execute(
            "INSERT INTO dev (at, code, text, state) VALUES (?, ?, ?, 'open')", values
        )
        return _dev(conn, int(cursor.lastrowid))


def get_dev(db: Db, dev_id: int) -> DevRecord | None:
    with session(db, write=False) as conn:
        return _dev(conn, whole_number(dev_id, "a DEV item id"))


def list_dev(db: Db, *, state: str | None = "open") -> list[DevRecord]:
    """DEV items in state (None: every state), oldest first."""
    sql, params = "SELECT * FROM dev", []
    if state is not None:
        sql += " WHERE state = ?"
        params.append(one_of(state, DEV_STATES, "a DEV item's state"))
    with session(db, write=False) as conn:
        rows = conn.execute(sql + " ORDER BY at, id", params).fetchall()
    return [_dev_record(r) for r in rows]


def close_dev(db: Db, dev_id: int) -> bool:
    """Close an open DEV item. True when done; False when it was closed already. No such
    item: LookupError."""
    return _change_dev(db, dev_id, "state = 'closed'", (), "state = 'open'")


def mark_dev_told_jim(db: Db, dev_id: int, *, at: datetime) -> bool:
    """Record when Jim was told of a DEV item. True the first time; False after (the first
    time stays). No such item: LookupError."""
    return _change_dev(db, dev_id, "told_jim_at = ?", (iso(at, "at"),), "told_jim_at IS NULL")


def _change_dev(db: Db, dev_id: int, sets: str, params: tuple[object, ...], only: str) -> bool:
    did = whole_number(dev_id, "a DEV item id")
    with session(db, write=True) as conn:
        # sets and only are fixed text from this module; values are parameters
        cursor = conn.execute(f"UPDATE dev SET {sets} WHERE id = ? AND {only}", (*params, did))
        if cursor.rowcount == 0 and _dev(conn, did) is None:
            raise LookupError("there is no DEV item with that id in the store")
        return cursor.rowcount == 1


# ----- rows in and out


def _event(conn: sqlite3.Connection, eid: int) -> EventRecord:
    return _event_record(conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone())


def _event_record(row: sqlite3.Row) -> EventRecord:
    return EventRecord(row["id"], time_of(row["at"]), row["studio_id"], row["kind"], row["text"],
                       row["lane"])


def _dev(conn: sqlite3.Connection, did: int) -> DevRecord | None:
    row = conn.execute("SELECT * FROM dev WHERE id = ?", (did,)).fetchone()
    return None if row is None else _dev_record(row)


def _dev_record(row: sqlite3.Row) -> DevRecord:
    return DevRecord(row["id"], time_of(row["at"]), row["code"], row["text"], row["state"],
                     time_or_none(row["told_jim_at"]))
