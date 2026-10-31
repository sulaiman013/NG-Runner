"""
Heads-ups: the takeoff each job's order heads-up last went out on (build spec section 4 and 5.8).

Why it exists: one message per content of a job's IC Conversion JSON, so a restart or a re-sync of
the same file sends nothing, and a real change sends one "revised". A takeoff that could not be read
is recorded too, with no message, so its one DEV item isn't raised again each minute.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ngrunner.store.db import (
    Db, iso_or_none, optional_text, session, sha256_value, text_value, time_or_none,
)


@dataclass(frozen=True)
class HeadsUpRecord:
    """A job's latest takeoff by hash; sent_at and outbox_id are None when no message went out."""

    folder: str
    json_sha256: str
    sent_at: datetime | None
    outbox_id: str | None


def record_heads_up(
    db: Db,
    *,
    folder: str,
    json_sha256: str,
    sent_at: datetime | None = None,
    outbox_id: str | None = None,
) -> HeadsUpRecord:
    """Note the takeoff this job's heads-up last went out on, in place of any earlier one."""
    values = (text_value(folder, "a folder name"), sha256_value(json_sha256, "a takeoff's sha256"),
              iso_or_none(sent_at, "sent_at"), optional_text(outbox_id, "an outbox id"))
    with session(db, write=True) as conn:
        conn.execute(
            "INSERT INTO heads_ups (folder, json_sha256, sent_at, outbox_id) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (folder) DO UPDATE SET json_sha256 = excluded.json_sha256,"
            " sent_at = excluded.sent_at, outbox_id = excluded.outbox_id",
            values,
        )
        return _heads_up(conn, values[0])


def get_heads_up(db: Db, folder: str) -> HeadsUpRecord | None:
    with session(db, write=False) as conn:
        return _heads_up(conn, text_value(folder, "a folder name"))


def list_heads_ups(db: Db) -> list[HeadsUpRecord]:
    """Every job's latest, in order of folder."""
    with session(db, write=False) as conn:
        rows = conn.execute("SELECT * FROM heads_ups ORDER BY folder").fetchall()
    return [_record(r) for r in rows]


def _heads_up(conn: sqlite3.Connection, folder: str) -> HeadsUpRecord | None:
    row = conn.execute("SELECT * FROM heads_ups WHERE folder = ?", (folder,)).fetchone()
    return None if row is None else _record(row)


def _record(row: sqlite3.Row) -> HeadsUpRecord:
    return HeadsUpRecord(folder=row["folder"], json_sha256=row["json_sha256"],
                         sent_at=time_or_none(row["sent_at"]), outbox_id=row["outbox_id"])
