"""
P4's tables (docs/p2/build-spec.md, section 2; design 3.3): leases, tracker batches, fixes and Jim's
lock clicks.

Why each holds what it holds:
- leases: the lease NG Runner last wrote in each job's NG Jobs folder, and the file's sha256, so "Release
  all jobs to chat" knows which to release and that the file is still the one it wrote. The file in the
  folder is what Jim's chat reads; this is NG Runner's own note of it.
- tracker_batches: every batch NG Runner dropped, with its rows exactly, so the same rows are never
  dropped twice ("never re-drop on doubt") and what the snapshots said after it can be told. rows hold a
  job's name and materials, never a customer: the inbox writer leaves that column blank.
- fixes: a fix as written: the room, the hash it was planned on and the hash it made, the byte patch
  (offsets with old and new bytes, as hex), the elements it touched with their values before and after,
  the programs there when it was written (Undo is refused once a newer one exists) and the backups
  commit_write made. Then whether it was undone, and what the survival check said.
- lock_clicks: Jim's Lock design, Finish lock and Lock, each with whether it was a "lock without check".

New tables only, so nothing an older row holds changes. Each function takes the Store or a Transaction.
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ngrunner.store.db import (
    Db, dumps, iso, iso_or_none, key_text, loads, one_of, optional_text, session, sha256_value,
    text_value, time_of, time_or_none,
)

LOCK_TABLES = frozenset({"leases", "tracker_batches", "fixes", "lock_clicks"})
LEASE_STATES = ("active", "released")
BATCH_KINDS = ("lock_design", "finish_lock", "lock", "resend")
BATCH_VERBS = ("append", "set")
# dropped: waiting for the ingest to archive it. archived: archived, waiting for a snapshot after it.
# first_ok: one snapshot shows every row. confirmed: a second, 10 minutes on, agrees. doubt: a row is
# missing or differs, or the ingest set it aside: an item for Jim, never a re-drop. resent: its missing
# rows went in a batch of their own.
BATCH_STATES = ("dropped", "archived", "first_ok", "confirmed", "doubt", "resent")
_OPEN_BATCHES = ("dropped", "archived", "first_ok")
FIX_STATES = ("applied", "undone")
CLICKS = ("lock_design", "finish_lock", "lock")


# ----- leases


@dataclass(frozen=True)
class LeaseRecord:
    folder: str
    holder: str
    state: str
    taken_at: datetime
    expires_at: datetime
    released_at: datetime | None
    sha256: str


def put_lease(db: Db, *, folder: str, holder: str, state: str, taken_at: datetime,
              expires_at: datetime, sha256: str, released_at: datetime | None = None) -> LeaseRecord:
    """The lease NG Runner has just written for folder, in place of the one before."""
    if expires_at <= taken_at:
        raise ValueError("a lease expires after it is taken")
    values = (key_text(folder, "a job's folder"), text_value(holder, "a lease's holder"),
              one_of(state, LEASE_STATES, "a lease's state"), iso(taken_at, "taken_at"),
              iso(expires_at, "expires_at"), iso_or_none(released_at, "released_at"),
              sha256_value(sha256, "the lease file's sha256"))
    with session(db, write=True) as conn:
        conn.execute("INSERT OR REPLACE INTO leases (folder, holder, state, taken_at, expires_at,"
                     " released_at, sha256) VALUES (?, ?, ?, ?, ?, ?, ?)", values)
        row = conn.execute("SELECT * FROM leases WHERE folder = ?", (values[0],)).fetchone()
    return _lease(row)


def get_lease(db: Db, folder: str) -> LeaseRecord | None:
    with session(db, write=False) as conn:
        row = conn.execute("SELECT * FROM leases WHERE folder = ?", (folder,)).fetchone()
    return None if row is None else _lease(row)


def list_leases(db: Db, *, state: str | None = None) -> list[LeaseRecord]:
    query, params = "SELECT * FROM leases", ()
    if state is not None:
        query, params = query + " WHERE state = ?", (one_of(state, LEASE_STATES, "a lease's state"),)
    with session(db, write=False) as conn:
        rows = conn.execute(query + " ORDER BY folder", params).fetchall()
    return [_lease(row) for row in rows]


def _lease(row: sqlite3.Row) -> LeaseRecord:
    return LeaseRecord(row["folder"], row["holder"], row["state"], time_of(row["taken_at"]),
                       time_of(row["expires_at"]), time_or_none(row["released_at"]), row["sha256"])


# ----- tracker batches


@dataclass(frozen=True)
class BatchRecord:
    """One batch dropped. rows: the batch exactly as written, header first. first_taken: when the
    snapshot that first showed every row was taken, which the confirming one must be 10 minutes after."""

    id: str
    folder: str
    kind: str
    verb: str
    material: str | None
    rows: list[list[str]]
    rows_sha256: str
    file: str
    state: str
    dropped_at: datetime
    archived_at: datetime | None
    first_ok_at: datetime | None
    first_taken: datetime | None
    confirmed_at: datetime | None
    words: str | None
    resend_of: str | None


def rows_sha256(rows: Sequence[Sequence[str]]) -> str:
    return hashlib.sha256(dumps([list(row) for row in rows]).encode("utf-8")).hexdigest()


def add_batch(db: Db, *, id: str, folder: str, kind: str, verb: str, material: str | None,
              rows: Sequence[Sequence[str]], file: str, dropped_at: datetime,
              resend_of: str | None = None) -> BatchRecord:
    """A batch NG Runner has just dropped, waiting for the ingest."""
    if not rows or not all(isinstance(cell, str) for row in rows for cell in row):
        raise ValueError("a batch holds its rows, each a list of text cells")
    values = (key_text(id, "a batch's id"), key_text(folder, "a job's folder"),
              one_of(kind, BATCH_KINDS, "a batch's kind"), one_of(verb, BATCH_VERBS, "a batch's verb"),
              optional_text(material, "a material"), dumps([list(row) for row in rows]),
              rows_sha256(rows), text_value(file, "a batch's file"), iso(dropped_at, "dropped_at"),
              optional_text(resend_of, "the batch it re-sends"))
    with session(db, write=True) as conn:
        conn.execute("INSERT INTO tracker_batches (id, folder, kind, verb, material, rows, rows_sha256,"
                     " file, state, dropped_at, resend_of) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'dropped', ?, ?)",
                     values)
        row = conn.execute("SELECT * FROM tracker_batches WHERE id = ?", (values[0],)).fetchone()
    return _batch(row)


_BATCH_TIMES = ("archived_at", "first_ok_at", "first_taken", "confirmed_at")


def update_batch(db: Db, batch_id: str, *, state: str, words: str | None = None,
                 **times: datetime) -> bool:
    """Move a batch on. False when there is no such batch."""
    unknown = set(times) - set(_BATCH_TIMES)
    if unknown:
        raise ValueError(f"a batch has no time called {', '.join(sorted(unknown))}")
    sets = ["state = ?", "words = COALESCE(?, words)"]
    params: list[Any] = [one_of(state, BATCH_STATES, "a batch's state"), optional_text(words, "words")]
    for name, when in times.items():
        sets.append(f"{name} = ?")
        params.append(iso(when, name))
    with session(db, write=True) as conn:
        cursor = conn.execute(f"UPDATE tracker_batches SET {', '.join(sets)} WHERE id = ?",
                              (*params, batch_id))
    return cursor.rowcount == 1


def get_batch(db: Db, batch_id: str) -> BatchRecord | None:
    with session(db, write=False) as conn:
        row = conn.execute("SELECT * FROM tracker_batches WHERE id = ?", (batch_id,)).fetchone()
    return None if row is None else _batch(row)


def list_batches(db: Db, *, folder: str | None = None, open_only: bool = False) -> list[BatchRecord]:
    """Oldest first. open_only: those the ingest or the snapshots haven't settled yet."""
    clauses, params = [], []
    if folder is not None:
        clauses.append("folder = ?")
        params.append(folder)
    if open_only:
        clauses.append(f"state IN ({', '.join('?' * len(_OPEN_BATCHES))})")
        params += list(_OPEN_BATCHES)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with session(db, write=False) as conn:
        rows = conn.execute(f"SELECT * FROM tracker_batches{where} ORDER BY dropped_at, id",
                            params).fetchall()
    return [_batch(row) for row in rows]


def rows_dropped_before(db: Db, rows: Sequence[Sequence[str]]) -> bool:
    """Whether a batch of exactly these rows was ever dropped, whatever became of it."""
    with session(db, write=False) as conn:
        row = conn.execute("SELECT 1 FROM tracker_batches WHERE rows_sha256 = ? LIMIT 1",
                           (rows_sha256(rows),)).fetchone()
    return row is not None


def _batch(row: sqlite3.Row) -> BatchRecord:
    return BatchRecord(row["id"], row["folder"], row["kind"], row["verb"], row["material"],
                       loads(row["rows"]), row["rows_sha256"], row["file"], row["state"],
                       time_of(row["dropped_at"]), time_or_none(row["archived_at"]),
                       time_or_none(row["first_ok_at"]), time_or_none(row["first_taken"]),
                       time_or_none(row["confirmed_at"]), row["words"], row["resend_of"])


# ----- fixes


@dataclass(frozen=True)
class FixRecord:
    id: int
    folder: str
    room: str
    fixer: str
    fingerprint: str
    base_sha256: str
    result_sha256: str
    patch: list[dict[str, Any]]
    elements: list[dict[str, Any]]
    programs: dict[str, list[str]]
    backups: list[str]
    state: str
    applied_at: datetime
    undone_at: datetime | None
    survival: str | None
    survival_at: datetime | None
    survival_words: str | None


def add_fix(db: Db, *, folder: str, room: str, fixer: str, fingerprint: str, base_sha256: str,
            result_sha256: str, patch: Sequence[Mapping[str, Any]],
            elements: Sequence[Mapping[str, Any]], programs: Mapping[str, Sequence[str]],
            backups: Sequence[str], applied_at: datetime) -> FixRecord:
    """A fix commit_write has just written."""
    if not patch or not elements:
        raise ValueError("a fix holds its patch and the elements it touched")
    values = (key_text(folder, "a job's folder"), key_text(room, "a room"),
              key_text(fixer, "a fixer"), sha256_value(fingerprint, "a finding's fingerprint"),
              sha256_value(base_sha256, "the room's sha256 before"),
              sha256_value(result_sha256, "the room's sha256 after"),
              dumps([dict(edit) for edit in patch]), dumps([dict(e) for e in elements]),
              dumps({material: list(hashes) for material, hashes in programs.items()}),
              dumps([str(path) for path in backups]), iso(applied_at, "applied_at"))
    with session(db, write=True) as conn:
        cursor = conn.execute(
            "INSERT INTO fixes (folder, room, fixer, fingerprint, base_sha256, result_sha256, patch,"
            " elements, programs, backups, state, applied_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " 'applied', ?)", values)
        row = conn.execute("SELECT * FROM fixes WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _fix(row)


def mark_fix_undone(db: Db, fix_id: int, *, at: datetime) -> bool:
    """Once: False when it was undone already."""
    with session(db, write=True) as conn:
        cursor = conn.execute("UPDATE fixes SET state = 'undone', undone_at = ? WHERE id = ?"
                              " AND state = 'applied'", (iso(at, "at"), fix_id))
    return cursor.rowcount == 1


def record_survival(db: Db, fix_id: int, *, code: str, words: str, at: datetime) -> bool:
    with session(db, write=True) as conn:
        cursor = conn.execute(
            "UPDATE fixes SET survival = ?, survival_words = ?, survival_at = ? WHERE id = ?",
            (key_text(code, "a survival code"), text_value(words, "survival words"), iso(at, "at"),
             fix_id))
    return cursor.rowcount == 1


def get_fix(db: Db, fix_id: int) -> FixRecord | None:
    with session(db, write=False) as conn:
        row = conn.execute("SELECT * FROM fixes WHERE id = ?", (fix_id,)).fetchone()
    return None if row is None else _fix(row)


def list_fixes(db: Db, *, folder: str | None = None) -> list[FixRecord]:
    query, params = "SELECT * FROM fixes", ()
    if folder is not None:
        query, params = query + " WHERE folder = ?", (folder,)
    with session(db, write=False) as conn:
        rows = conn.execute(query + " ORDER BY id", params).fetchall()
    return [_fix(row) for row in rows]


def _fix(row: sqlite3.Row) -> FixRecord:
    return FixRecord(row["id"], row["folder"], row["room"], row["fixer"], row["fingerprint"],
                     row["base_sha256"], row["result_sha256"], loads(row["patch"]),
                     loads(row["elements"]), loads(row["programs"]), loads(row["backups"]),
                     row["state"], time_of(row["applied_at"]), time_or_none(row["undone_at"]),
                     row["survival"], time_or_none(row["survival_at"]), row["survival_words"])


# ----- Jim's lock clicks


@dataclass(frozen=True)
class LockClickRecord:
    id: int
    folder: str
    click: str
    material: str | None
    without_check: bool
    at: datetime
    batch_id: str | None


def add_lock_click(db: Db, *, folder: str, click: str, material: str | None, without_check: bool,
                   at: datetime, batch_id: str | None = None) -> LockClickRecord:
    if not isinstance(without_check, bool):
        raise ValueError("without_check must be True or False")
    values = (key_text(folder, "a job's folder"), one_of(click, CLICKS, "a lock click"),
              optional_text(material, "a material"), int(without_check), iso(at, "at"),
              optional_text(batch_id, "a batch's id"))
    with session(db, write=True) as conn:
        cursor = conn.execute("INSERT INTO lock_clicks (folder, click, material, without_check, at,"
                              " batch_id) VALUES (?, ?, ?, ?, ?, ?)", values)
        row = conn.execute("SELECT * FROM lock_clicks WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _click(row)


def list_lock_clicks(db: Db, *, folder: str | None = None) -> list[LockClickRecord]:
    query, params = "SELECT * FROM lock_clicks", ()
    if folder is not None:
        query, params = query + " WHERE folder = ?", (folder,)
    with session(db, write=False) as conn:
        rows = conn.execute(query + " ORDER BY id", params).fetchall()
    return [_click(row) for row in rows]


def _click(row: sqlite3.Row) -> LockClickRecord:
    return LockClickRecord(row["id"], row["folder"], row["click"], row["material"],
                           bool(row["without_check"]), time_of(row["at"]), row["batch_id"])
