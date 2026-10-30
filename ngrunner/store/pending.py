"""
Pending operations: work NG Runner has promised itself, each with a time it is due and, for
most, a time limit (build spec section 4; design 3.4, "Pending operations, each with a time
limit": a fetch has 24 hours, then a card).

Why rows and not timers: the PC sleeps, NG Runner restarts and updates, and a timer dies with
the process. A row says what to do and when, so the scheduler simply asks what is due each
tick, and a restart carries on where it stopped. An operation is queued at most once while it
is open (one per kind for each job and row), so a step that runs again after a crash finds the
operation it queued before instead of adding a second.

States: "open" until it ends as "done", "failed" or "expired" (past its limit: wave 2 then
raises the card or DEV item).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ngrunner.store.db import (
    KEEP, Db, Keep, code_value, iso, iso_or_none, key_text, one_of, optional_text, session,
    time_of, time_or_none, whole_number,
)

PENDING_STATES = ("open", "done", "failed", "expired")
_ENDS = PENDING_STATES[1:]


@dataclass(frozen=True)
class PendingRecord:
    """One operation. studio_id and fab_row_id say what it is for (either, both or neither);
    attempts counts retries; note is a plain line for the board and DEV."""

    id: int
    kind: str
    studio_id: str | None
    fab_row_id: int | None
    due_at: datetime
    deadline_at: datetime | None
    attempts: int
    state: str
    note: str | None


def add_pending(
    db: Db,
    kind: str,
    *,
    due_at: datetime,
    deadline_at: datetime | None = None,
    studio_id: str | None = None,
    fab_row_id: int | None = None,
    note: str | None = None,
) -> PendingRecord:
    """Queue an operation ("fetch_2", "list_wait"...: a short lowercase name). While an open
    operation of the same kind for the same job and row is already there, that one is
    returned unchanged and nothing is added."""
    values = (
        _kind(kind), None if studio_id is None else key_text(studio_id, "a Studio project ID"),
        None if fab_row_id is None else whole_number(fab_row_id, "a Fab row id", at_least=1),
        iso(due_at, "due_at"), iso_or_none(deadline_at, "deadline_at"),
        optional_text(note, "a note"),
    )
    with session(db, write=True) as conn:
        found = conn.execute(
            "SELECT * FROM pending WHERE state = 'open' AND kind = ? AND studio_id IS ?"
            " AND fab_row_id IS ?",
            values[:3],
        ).fetchone()
        if found is not None:
            return _record(found)
        cursor = conn.execute(
            "INSERT INTO pending (kind, studio_id, fab_row_id, due_at, deadline_at, state, note)"
            " VALUES (?, ?, ?, ?, ?, 'open', ?)",
            values,
        )
        return _pending(conn, int(cursor.lastrowid))


def get_pending(db: Db, pending_id: int) -> PendingRecord | None:
    with session(db, write=False) as conn:
        return _pending(conn, whole_number(pending_id, "a pending operation id"))


def due_pending(db: Db, now: datetime) -> list[PendingRecord]:
    """Open operations due at or before now, soonest first."""
    with session(db, write=False) as conn:
        rows = conn.execute(
            "SELECT * FROM pending WHERE state = 'open' AND due_at <= ? ORDER BY due_at, id",
            (iso(now, "now"),),
        ).fetchall()
    return [_record(r) for r in rows]


def past_deadline_pending(db: Db, now: datetime) -> list[PendingRecord]:
    """Open operations whose time limit is at or before now (they go to DEV and the board),
    in deadline order."""
    with session(db, write=False) as conn:
        rows = conn.execute(
            "SELECT * FROM pending WHERE state = 'open' AND deadline_at IS NOT NULL"
            " AND deadline_at <= ? ORDER BY deadline_at, id",
            (iso(now, "now"),),
        ).fetchall()
    return [_record(r) for r in rows]


def list_pending(
    db: Db,
    *,
    state: str | None = "open",
    kind: str | None = None,
    studio_id: str | None = None,
    fab_row_id: int | None = None,
) -> list[PendingRecord]:
    """Operations in state (None: every state), only of kind, job and row when given; soonest
    due first."""
    clauses, params = [], []
    for column, value in (
        ("state", None if state is None else one_of(state, PENDING_STATES, "a pending state")),
        ("kind", None if kind is None else _kind(kind)),
        ("studio_id", None if studio_id is None else key_text(studio_id, "a Studio project ID")),
        ("fab_row_id", None if fab_row_id is None else whole_number(fab_row_id, "a Fab row id")),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")  # column names from this list, values as parameters
            params.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with session(db, write=False) as conn:
        rows = conn.execute(f"SELECT * FROM pending{where} ORDER BY due_at, id", params).fetchall()
    return [_record(r) for r in rows]


def retry_pending(
    db: Db, pending_id: int, *, due_at: datetime, note: str | None | Keep = KEEP
) -> bool:
    """Count one more attempt and make the operation due again at due_at. True when done; False
    when it is no longer open (nothing changes). No such operation: LookupError."""
    params: list[object] = [iso(due_at, "due_at")]
    sets = "attempts = attempts + 1, due_at = ?"
    if note is not KEEP:
        sets += ", note = ?"
        params.append(optional_text(note, "a note"))
    return _change_open(db, pending_id, sets, params)


def finish_pending(
    db: Db, pending_id: int, state: str, *, note: str | None | Keep = KEEP
) -> bool:
    """End an open operation as "done", "failed" or "expired". True when done; False when it
    had already ended (nothing changes). No such operation: LookupError."""
    params: list[object] = [one_of(state, _ENDS, "how a pending operation ends")]
    sets = "state = ?"
    if note is not KEEP:
        sets += ", note = ?"
        params.append(optional_text(note, "a note"))
    return _change_open(db, pending_id, sets, params)


def _change_open(db: Db, pending_id: int, sets: str, params: list[object]) -> bool:
    pid = whole_number(pending_id, "a pending operation id")
    with session(db, write=True) as conn:
        # sets is built by this module's callers from fixed text; values are parameters
        cursor = conn.execute(
            f"UPDATE pending SET {sets} WHERE id = ? AND state = 'open'", (*params, pid)
        )
        if cursor.rowcount == 0 and _pending(conn, pid) is None:
            raise LookupError("there is no pending operation with that id in the store")
        return cursor.rowcount == 1


def _kind(value: object) -> str:
    return code_value(value, "a pending operation's kind", "fetch_2")


def _pending(conn: sqlite3.Connection, pid: int) -> PendingRecord | None:
    row = conn.execute("SELECT * FROM pending WHERE id = ?", (pid,)).fetchone()
    return None if row is None else _record(row)


def _record(row: sqlite3.Row) -> PendingRecord:
    return PendingRecord(
        id=row["id"],
        kind=row["kind"],
        studio_id=row["studio_id"],
        fab_row_id=row["fab_row_id"],
        due_at=time_of(row["due_at"]),
        deadline_at=time_or_none(row["deadline_at"]),
        attempts=row["attempts"],
        state=row["state"],
        note=row["note"],
    )
