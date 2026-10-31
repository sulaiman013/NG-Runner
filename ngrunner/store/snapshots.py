"""
Toolkit snapshots: each copy taken of Jim's toolkit, by hash, and what became of it (build spec
section 4 and 5.13).

Why it exists: the checks run from a copy that passed its own regression, never from the folder Jim
is changing, so what is adopted must be on record: a verdict is only as good as the scripts behind
it. The harness that fills this is group 2 work, and adoption needs the real toolkit (group 3).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ngrunner.store.db import (
    KEEP, Db, Keep, iso, optional_text, session, sha256_value, time_of, whole_number,
)


@dataclass(frozen=True)
class SnapshotRecord:
    """One copy: when it was taken, the hash over its files, whether it was adopted, and the result
    of checking it, in plain words."""

    id: int
    taken_at: datetime
    sha256: str
    adopted: bool
    result: str | None


def add_snapshot(
    db: Db, *, taken_at: datetime, sha256: str, adopted: bool = False, result: str | None = None
) -> SnapshotRecord:
    """Record one snapshot taken."""
    values = (iso(taken_at, "taken_at"), sha256_value(sha256, "a snapshot's sha256"),
              _adopted(adopted), optional_text(result, "a snapshot's result"))
    with session(db, write=True) as conn:
        cursor = conn.execute(
            "INSERT INTO snapshots (taken_at, sha256, adopted, result) VALUES (?, ?, ?, ?)", values
        )
        return _snapshot(conn, int(cursor.lastrowid))


def get_snapshot(db: Db, snapshot_id: int) -> SnapshotRecord | None:
    with session(db, write=False) as conn:
        return _snapshot(conn, whole_number(snapshot_id, "a snapshot id"))


def list_snapshots(db: Db) -> list[SnapshotRecord]:
    """Snapshots in the order they were taken."""
    with session(db, write=False) as conn:
        rows = conn.execute("SELECT * FROM snapshots ORDER BY taken_at, id").fetchall()
    return [_record(r) for r in rows]


def update_snapshot(
    db: Db,
    snapshot_id: int,
    *,
    adopted: bool | Keep = KEEP,
    result: str | None | Keep = KEEP,
) -> SnapshotRecord:
    """Change only the fields given (None clears the result; KEEP, the default, leaves it) and
    return the snapshot as it now is. No such snapshot: LookupError."""
    sid = whole_number(snapshot_id, "a snapshot id")
    changes: dict[str, object] = {}
    if adopted is not KEEP:
        changes["adopted"] = _adopted(adopted)
    if result is not KEEP:
        changes["result"] = optional_text(result, "a snapshot's result")
    with session(db, write=True) as conn:
        if _snapshot(conn, sid) is None:
            raise LookupError("there is no snapshot with that id in the store")
        if changes:
            # column names from this function, values as parameters
            sets = ", ".join(f"{column} = ?" for column in changes)
            conn.execute(f"UPDATE snapshots SET {sets} WHERE id = ?", (*changes.values(), sid))
        return _snapshot(conn, sid)


def _adopted(value: object) -> int:
    if not isinstance(value, bool):
        raise ValueError("adopted must be True or False")
    return int(value)


def _snapshot(conn: sqlite3.Connection, sid: int) -> SnapshotRecord | None:
    row = conn.execute("SELECT * FROM snapshots WHERE id = ?", (sid,)).fetchone()
    return None if row is None else _record(row)


def _record(row: sqlite3.Row) -> SnapshotRecord:
    return SnapshotRecord(id=row["id"], taken_at=time_of(row["taken_at"]), sha256=row["sha256"],
                          adopted=bool(row["adopted"]), result=row["result"])
