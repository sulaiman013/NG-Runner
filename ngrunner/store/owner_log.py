"""
The owner log: every user string seen in a MozaikJob.UNS, MozaikJob.LCK or OptimizeJob.LCK (build
spec section 4 and 5.12).

Why it exists: whether anyone but Jim opens shared Mozaik jobs is not known yet (decision 4), and
a lock file names who has a job only for as long as it is there. Rows are only ever added. A string
is kept as it was read, even an empty one, since an empty lock file is itself a thing to know.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ngrunner.store.db import Db, iso, session, text_value, time_of


@dataclass(frozen=True)
class OwnerRecord:
    """One sighting: when, in which job folder, in which file, and who it named."""

    id: int
    at: datetime
    folder: str
    file: str
    owner: str


def log_owner(db: Db, *, at: datetime, folder: str, file: str, owner: str) -> OwnerRecord:
    """Add one sighting of an owner string."""
    values = (iso(at, "at"), text_value(folder, "a folder name"), text_value(file, "a file's name"),
              text_value(owner, "an owner", empty=True))
    with session(db, write=True) as conn:
        cursor = conn.execute(
            "INSERT INTO owner_log (at, folder, file, owner) VALUES (?, ?, ?, ?)", values
        )
        row = conn.execute("SELECT * FROM owner_log WHERE id = ?",
                           (int(cursor.lastrowid),)).fetchone()
        return _record(row)


def list_owner_log(db: Db, *, folder: str | None = None) -> list[OwnerRecord]:
    """Sightings in the order they were seen; only the folder's, when given."""
    where, params = "", ()
    if folder is not None:
        where, params = " WHERE folder = ?", (text_value(folder, "a folder name"),)
    with session(db, write=False) as conn:
        rows = conn.execute(f"SELECT * FROM owner_log{where} ORDER BY id", params).fetchall()
    return [_record(r) for r in rows]


def _record(row: sqlite3.Row) -> OwnerRecord:
    return OwnerRecord(id=row["id"], at=time_of(row["at"]), folder=row["folder"], file=row["file"],
                       owner=row["owner"])
