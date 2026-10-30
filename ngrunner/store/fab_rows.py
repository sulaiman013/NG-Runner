"""
Fab rows: what NG Runner knows of each row Barbara adds to the NG Fab Schedule's Sheet1 (build
spec section 4; design 3.3, "Fab row"; GLOSSARY: Fab row).

Rows have no IDs in the sheet and move about, so NG Runner keeps its own: a row is first seen
once, and Barbara's later edits update that same record (the Fab reader decides which edits
are the same row). Its first sight and original text never change, because the matcher's
freshness rule is measured from the first sight ("never match against a job list older than
the row's first appearance").

States: "seen_at_install" (there before NG Runner: never acted on), "new", "waiting_list" (for
a job list fresher than the row), "asked" (a card or Barbara's email is out), "matched",
"filed", "none" (no job found, asked) and "gone" (moved to Archive: logged, never acted on).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime

from ngrunner.match import FabRow
from ngrunner.store.db import (
    KEEP, Db, Keep, iso, key_text, one_of, optional_text, session, text_value, time_of,
    whole_number,
)
from ngrunner.store.jobs import install_cells, install_text

FAB_ROW_STATES = (
    "seen_at_install", "new", "waiting_list", "asked", "matched", "filed", "none", "gone",
)


@dataclass(frozen=True)
class FabRowRecord:
    """One Fab row as NG Runner knows it. text, designer, install (the two Install Date cells
    as shown), fab_date and sheet_row are as last read; original_text and first_seen as first
    read. studio_id is the job it was matched to; reason says why it is in its state."""

    id: int
    first_seen: datetime
    text: str
    designer: str
    install: tuple[str, str] | None
    fab_date: str | None
    original_text: str
    sheet_row: int | None
    state: str
    studio_id: str | None
    reason: str | None
    updated_at: datetime

    def to_match(self) -> FabRow:
        """The row as the matcher takes it (match_fab_row)."""
        return FabRow(self.text, self.designer, self.first_seen)


def add_fab_row(
    db: Db,
    *,
    first_seen: datetime,
    text: str,
    designer: str,
    install: Sequence[str] | None = None,
    fab_date: str | None = None,
    sheet_row: int | None = None,
    state: str = "new",
) -> FabRowRecord:
    """Record a row seen for the first time (state "new", or "seen_at_install" for the rows
    there at install); its text is also kept as original_text. designer may be "" (a blank
    cell), text may not."""
    values = (
        iso(first_seen, "first_seen"), text_value(text, "a Fab row's job text"),
        text_value(designer, "a Fab row's designer", empty=True), install_text(install),
        optional_text(fab_date, "a Fab date"), _sheet_row(sheet_row),
        one_of(state, FAB_ROW_STATES, "a Fab row's state"),
    )
    with session(db, write=True) as conn:
        cursor = conn.execute(
            "INSERT INTO fab_rows (first_seen, text, designer, install_text, fab_date,"
            " original_text, sheet_row, state, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*values[:5], values[1], values[5], values[6], values[0]),
        )
        return _row(conn, int(cursor.lastrowid))


def get_fab_row(db: Db, row_id: int) -> FabRowRecord | None:
    with session(db, write=False) as conn:
        return _row(conn, whole_number(row_id, "a Fab row id"))


def list_fab_rows(db: Db, *, states: Collection[str] | None = None) -> list[FabRowRecord]:
    """Rows in the order they were first seen; only those in states, when given."""
    sql, params = "SELECT * FROM fab_rows", []
    if states is not None:
        params = [one_of(s, FAB_ROW_STATES, "a Fab row's state") for s in states]
        sql += f" WHERE state IN ({', '.join('?' * len(params))})"  # one placeholder each
    with session(db, write=False) as conn:
        rows = conn.execute(sql + " ORDER BY first_seen, id", params).fetchall()
    return [_record(r) for r in rows]


def update_fab_row(
    db: Db,
    row_id: int,
    *,
    now: datetime,
    text: str | Keep = KEEP,
    designer: str | Keep = KEEP,
    install: Sequence[str] | None | Keep = KEEP,
    fab_date: str | None | Keep = KEEP,
    sheet_row: int | None | Keep = KEEP,
    state: str | Keep = KEEP,
    studio_id: str | None | Keep = KEEP,
    reason: str | None | Keep = KEEP,
) -> FabRowRecord:
    """Change only the fields given (None clears an optional one), stamp updated_at, and return
    the row as it now is. first_seen and original_text never change. A studio_id the store has
    no job for is refused (StoreError: its foreign key); no such row: LookupError."""
    rid = whole_number(row_id, "a Fab row id")
    changes = _changes(text, designer, install, fab_date, sheet_row, state, studio_id, reason)
    changes["updated_at"] = iso(now, "now")
    with session(db, write=True) as conn:
        # column names come from _changes (this code), values are parameters
        sets = ", ".join(f"{column} = ?" for column in changes)
        cursor = conn.execute(f"UPDATE fab_rows SET {sets} WHERE id = ?", (*changes.values(), rid))
        if cursor.rowcount == 0:
            raise LookupError("there is no Fab row with that id in the store")
        return _row(conn, rid)


def _changes(
    text: object, designer: object, install: object, fab_date: object, sheet_row: object,
    state: object, studio_id: object, reason: object,
) -> dict[str, object]:
    checks = {
        "text": lambda v: text_value(v, "a Fab row's job text"),
        "designer": lambda v: text_value(v, "a Fab row's designer", empty=True),
        "install_text": install_text,
        "fab_date": lambda v: optional_text(v, "a Fab date"),
        "sheet_row": _sheet_row,
        "state": lambda v: one_of(v, FAB_ROW_STATES, "a Fab row's state"),
        "studio_id": lambda v: None if v is None else key_text(v, "a Studio project ID"),
        "reason": lambda v: optional_text(v, "a reason"),
    }
    values = (text, designer, install, fab_date, sheet_row, state, studio_id, reason)
    given = dict(zip(checks, values))  # in the order of checks
    return {column: checks[column](value) for column, value in given.items() if value is not KEEP}


def _sheet_row(value: object) -> int | None:
    return None if value is None else whole_number(value, "a sheet row", at_least=1)


def _row(conn: sqlite3.Connection, rid: int) -> FabRowRecord | None:
    row = conn.execute("SELECT * FROM fab_rows WHERE id = ?", (rid,)).fetchone()
    return None if row is None else _record(row)


def _record(row: sqlite3.Row) -> FabRowRecord:
    return FabRowRecord(
        id=row["id"],
        first_seen=time_of(row["first_seen"]),
        text=row["text"],
        designer=row["designer"],
        install=install_cells(row["install_text"]),
        fab_date=row["fab_date"],
        original_text=row["original_text"],
        sheet_row=row["sheet_row"],
        state=row["state"],
        studio_id=row["studio_id"],
        reason=row["reason"],
        updated_at=time_of(row["updated_at"]),
    )
