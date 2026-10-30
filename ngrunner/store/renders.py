"""
Renders: one row per download of a job's IC summary from Studio (build spec section 4; design
3.3, "Render"; GLOSSARY: Render).

A job can have several renders, and one governs: the strict double fetch compares the latest
two, and "IC changed: which render governs?" is answered by setting one. The store keeps what
was learned about each fetch (its hash, page count, the content fingerprint, the layout, the
check's verdict and what it found missing) and where the bytes are cached on this PC; the PDF
itself is never in the database. creation_date is the PDF's own CreationDate text, kept
verbatim: it is the file's claim, not a time NG Runner measured.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ngrunner.config import LAYOUTS
from ngrunner.store.db import (
    Db, dumps, iso, key_text, loads, one_of, optional_text, session, sha256_value, time_of,
    whole_number,
)


@dataclass(frozen=True)
class RenderRecord:
    """One fetch. check_ok and missing are check_summary's verdict; layout is "landscape" or
    "portrait"; cache_path is where its bytes are kept on this PC (AppPaths.renders)."""

    id: int
    studio_id: str
    fetched_at: datetime
    url: str | None
    sha256: str | None
    page_count: int | None
    creation_date: str | None
    fingerprint: str | None
    layout: str | None
    check_ok: bool
    missing: tuple[str, ...]
    project_name: str | None
    cache_path: Path | None
    governing: bool


def add_render(
    db: Db,
    *,
    studio_id: str,
    fetched_at: datetime,
    check_ok: bool,
    url: str | None = None,
    sha256: str | None = None,
    page_count: int | None = None,
    creation_date: str | None = None,
    fingerprint: str | None = None,
    layout: str | None = None,
    missing: Sequence[str] = (),
    project_name: str | None = None,
    cache_path: Path | None = None,
) -> RenderRecord:
    """Record one fetch of a job's IC summary (not governing). sha256 is 64 lowercase hex
    characters; cache_path a full path. A job the store doesn't have is refused (StoreError:
    its foreign key)."""
    if not isinstance(check_ok, bool):
        raise ValueError("check_ok must be True or False")
    values = (
        key_text(studio_id, "a Studio project ID"), iso(fetched_at, "fetched_at"),
        optional_text(url, "a URL"), None if sha256 is None else sha256_value(sha256),
        None if page_count is None else whole_number(page_count, "a page count", at_least=0),
        optional_text(creation_date, "a creation date"),
        optional_text(fingerprint, "a fingerprint"),
        None if layout is None else one_of(layout, LAYOUTS, "a report layout"),
        int(check_ok), _missing(missing), optional_text(project_name, "a project name"),
        _path(cache_path),
    )
    with session(db, write=True) as conn:
        cursor = conn.execute(
            "INSERT INTO renders (studio_id, fetched_at, url, sha256, page_count, creation_date,"
            " fingerprint, layout, check_ok, missing, project_name, cache_path)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        return _render(conn, int(cursor.lastrowid))


def get_render(db: Db, render_id: int) -> RenderRecord | None:
    with session(db, write=False) as conn:
        return _render(conn, whole_number(render_id, "a render id"))


def list_renders(db: Db, studio_id: str) -> list[RenderRecord]:
    """A job's renders in the order they were fetched."""
    with session(db, write=False) as conn:
        rows = conn.execute(
            "SELECT * FROM renders WHERE studio_id = ? ORDER BY fetched_at, id",
            (key_text(studio_id, "a Studio project ID"),),
        ).fetchall()
    return [_record(r) for r in rows]


def set_governing(db: Db, render_id: int) -> RenderRecord:
    """Make this render the one that governs its job; any other render of the job stops
    governing, in the same transaction. No such render: LookupError."""
    rid = whole_number(render_id, "a render id")
    with session(db, write=True) as conn:
        render = _render(conn, rid)
        if render is None:
            raise LookupError("there is no render with that id in the store")
        conn.execute(
            "UPDATE renders SET governing = 0 WHERE studio_id = ? AND governing = 1 AND id <> ?",
            (render.studio_id, rid),
        )
        conn.execute("UPDATE renders SET governing = 1 WHERE id = ?", (rid,))
        return _render(conn, rid)


def governing_render(db: Db, studio_id: str) -> RenderRecord | None:
    """The render that governs the job, or None while none does."""
    with session(db, write=False) as conn:
        row = conn.execute(
            "SELECT * FROM renders WHERE studio_id = ? AND governing = 1",
            (key_text(studio_id, "a Studio project ID"),),
        ).fetchone()
    return None if row is None else _record(row)


def _missing(value: object) -> str:
    if not isinstance(value, list | tuple) or not all(isinstance(m, str) for m in value):
        raise ValueError("missing must be a list of sentences")
    return dumps(list(value))


def _path(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Path) or not value.is_absolute():
        raise ValueError("a cache path must be a full path")
    return str(value)


def _render(conn: sqlite3.Connection, rid: int) -> RenderRecord | None:
    row = conn.execute("SELECT * FROM renders WHERE id = ?", (rid,)).fetchone()
    return None if row is None else _record(row)


def _record(row: sqlite3.Row) -> RenderRecord:
    return RenderRecord(
        id=row["id"],
        studio_id=row["studio_id"],
        fetched_at=time_of(row["fetched_at"]),
        url=row["url"],
        sha256=row["sha256"],
        page_count=row["page_count"],
        creation_date=row["creation_date"],
        fingerprint=row["fingerprint"],
        layout=row["layout"],
        check_ok=bool(row["check_ok"]),
        missing=tuple(loads(row["missing"])),
        project_name=row["project_name"],
        cache_path=None if row["cache_path"] is None else Path(row["cache_path"]),
        governing=bool(row["governing"]),
    )
