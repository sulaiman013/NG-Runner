"""
Jobs and their aliases (build spec section 4; design 3.3, "Job"; GLOSSARY: Job, Alias).

A job's identity is its Studio project ID: it survives renames, so it is the key everywhere
else in the store. Its name is the Opportunity Name, frozen at intake (when the summary is
filed); a later name is recorded as an alias and never written over the frozen one. A stored
job carries the name and aliases the matcher reads (match.JobLike), so list_jobs() can be
handed to match_file_to_job and match_by_folder as it is.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from ngrunner.store.db import (
    KEEP, Db, Keep, dumps, iso, key_text, loads, one_of, optional_text, session, text_value,
    time_of,
)

MODES = ("auto", "observed", "held")
BRANCHES = ("closet", "garage")


@dataclass(frozen=True)
class AliasRecord:
    """A later name of a job, and when NG Runner first saw it."""

    studio_id: str
    name: str
    seen_at: datetime


@dataclass(frozen=True)
class JobRecord:
    """One job.

    mode: "auto" (NG Runner files it), "observed" (found at install, or started in chat: watch
        and check only) or "held" (Jim holds it: nothing is written for it).
    name_frozen: the name is final (set at filing); later names are aliases.
    branch: "closet", "garage", or None while not known.
    install: the Fab row's two Install Date cells as shown (columns D and E), or None.
    folder: its NG Jobs folder name, once known.
    aliases: its later names, oldest first.
    """

    studio_id: str
    name: str
    mode: str
    name_frozen: bool
    branch: str | None
    designer: str | None
    install: tuple[str, str] | None
    folder: str | None
    created_at: datetime
    updated_at: datetime
    aliases: tuple[str, ...] = ()


# ----- jobs


def ensure_job(
    db: Db,
    studio_id: str,
    name: str,
    *,
    now: datetime,
    mode: str = "auto",
    designer: str | None = None,
    install: Sequence[str] | None = None,
) -> JobRecord:
    """The job with this Studio project ID, made now if the store doesn't have it (name not
    frozen, no branch or folder yet). A job that is already there is returned unchanged,
    whatever else is passed, so matching a row to a job twice never changes it."""
    row = (
        key_text(studio_id, "a Studio project ID"), text_value(name, "a job name"),
        optional_text(designer, "a designer"), install_text(install),
        one_of(mode, MODES, "a job's mode"), iso(now, "now"),
    )
    with session(db, write=True) as conn:
        conn.execute(
            "INSERT INTO jobs (studio_id, name, designer, install_text, mode, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (studio_id) DO NOTHING",
            (*row, row[-1]),
        )
        return _job(conn, row[0])


def get_job(db: Db, studio_id: str) -> JobRecord | None:
    """The job with this Studio project ID, with its aliases; None when there is none."""
    with session(db, write=False) as conn:
        return _job(conn, key_text(studio_id, "a Studio project ID"))


def list_jobs(db: Db) -> list[JobRecord]:
    """Every job, oldest first, each with its aliases."""
    with session(db, write=False) as conn:
        names: dict[str, list[str]] = {}
        for alias in conn.execute("SELECT studio_id, name FROM aliases ORDER BY seen_at, name"):
            names.setdefault(alias["studio_id"], []).append(alias["name"])
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at, studio_id").fetchall()
    return [_record(row, names.get(row["studio_id"], ())) for row in rows]


def update_job(
    db: Db,
    studio_id: str,
    *,
    now: datetime,
    name: str | Keep = KEEP,
    mode: str | Keep = KEEP,
    branch: str | None | Keep = KEEP,
    designer: str | None | Keep = KEEP,
    install: Sequence[str] | None | Keep = KEEP,
    folder: str | None | Keep = KEEP,
) -> JobRecord:
    """Change only the fields given (None clears one; KEEP, the default, leaves it), stamp
    updated_at, and return the job as it now is. A new name for a job whose name is frozen is
    refused (ValueError): record it with add_alias. No such job: LookupError."""
    sid = key_text(studio_id, "a Studio project ID")
    changes = _changes(name, mode, branch, designer, install, folder)
    changes["updated_at"] = iso(now, "now")
    with session(db, write=True) as conn:
        current = _job(conn, sid)
        if current is None:
            raise LookupError("there is no job with that Studio project ID in the store")
        if current.name_frozen and changes.get("name", current.name) != current.name:
            raise ValueError(
                "the job's name is frozen at intake; record the new name with add_alias"
            )
        # column names come from _changes (this code), values are parameters
        sets = ", ".join(f"{column} = ?" for column in changes)
        conn.execute(f"UPDATE jobs SET {sets} WHERE studio_id = ?", (*changes.values(), sid))
        return _job(conn, sid)


def freeze_name(db: Db, studio_id: str, *, now: datetime) -> JobRecord:
    """Make the job's name final (at filing; design 3.3). Freezing a frozen name changes
    nothing. No such job: LookupError."""
    sid = key_text(studio_id, "a Studio project ID")
    at = iso(now, "now")
    with session(db, write=True) as conn:
        conn.execute(
            "UPDATE jobs SET name_frozen = 1, updated_at = ?"
            " WHERE studio_id = ? AND name_frozen = 0",
            (at, sid),
        )
        job = _job(conn, sid)
    if job is None:
        raise LookupError("there is no job with that Studio project ID in the store")
    return job


def _changes(
    name: object, mode: object, branch: object, designer: object, install: object, folder: object
) -> dict[str, object]:
    changes: dict[str, object] = {}
    if name is not KEEP:
        changes["name"] = text_value(name, "a job name")
    if mode is not KEEP:
        changes["mode"] = one_of(mode, MODES, "a job's mode")
    if branch is not KEEP:
        changes["branch"] = None if branch is None else one_of(branch, BRANCHES, "a job's branch")
    if designer is not KEEP:
        changes["designer"] = optional_text(designer, "a designer")
    if install is not KEEP:
        changes["install_text"] = install_text(install)
    if folder is not KEEP:
        changes["folder"] = None if folder is None else text_value(folder, "a folder name")
    return changes


# ----- aliases


def add_alias(db: Db, studio_id: str, name: str, *, seen_at: datetime) -> None:
    """Record a later name of a job. A name already recorded keeps its first seen_at. A job
    the store doesn't have is refused (StoreError: its foreign key)."""
    row = (key_text(studio_id, "a Studio project ID"), text_value(name, "an alias"),
           iso(seen_at, "seen_at"))
    with session(db, write=True) as conn:
        conn.execute(
            "INSERT INTO aliases (studio_id, name, seen_at) VALUES (?, ?, ?)"
            " ON CONFLICT (studio_id, name) DO NOTHING",
            row,
        )


def list_aliases(db: Db, studio_id: str) -> list[AliasRecord]:
    """A job's later names, oldest first."""
    with session(db, write=False) as conn:
        rows = conn.execute(
            "SELECT * FROM aliases WHERE studio_id = ? ORDER BY seen_at, name",
            (key_text(studio_id, "a Studio project ID"),),
        ).fetchall()
    return [AliasRecord(r["studio_id"], r["name"], time_of(r["seen_at"])) for r in rows]


# ----- rows in and out


def install_text(install: Sequence[str] | None) -> str | None:
    """The two Install Date cells as stored: a JSON list of the two texts, exactly as shown."""
    if install is None:
        return None
    cells = tuple(install) if isinstance(install, list | tuple) else ()
    if len(cells) != 2 or not all(isinstance(cell, str) for cell in cells):
        raise ValueError("install must be the two Install Date cells, as text")
    return dumps(list(cells))


def install_cells(text: str | None) -> tuple[str, str] | None:
    if text is None:
        return None
    first, second = loads(text)
    return first, second


def _job(conn: sqlite3.Connection, sid: str) -> JobRecord | None:
    row = conn.execute("SELECT * FROM jobs WHERE studio_id = ?", (sid,)).fetchone()
    if row is None:
        return None
    aliases = conn.execute(
        "SELECT name FROM aliases WHERE studio_id = ? ORDER BY seen_at, name", (sid,)
    ).fetchall()
    return _record(row, [a["name"] for a in aliases])


def _record(row: sqlite3.Row, aliases: Sequence[str]) -> JobRecord:
    return JobRecord(
        studio_id=row["studio_id"],
        name=row["name"],
        mode=row["mode"],
        name_frozen=bool(row["name_frozen"]),
        branch=row["branch"],
        designer=row["designer"],
        install=install_cells(row["install_text"]),
        folder=row["folder"],
        created_at=time_of(row["created_at"]),
        updated_at=time_of(row["updated_at"]),
        aliases=tuple(aliases),
    )
