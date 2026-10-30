r"""
The write record: one row per commit_write call, and the files NG Runner owns (build spec
section 4; design 3.3, "Write"; AGENTS.md: Before filing goes live, item 4).

writes keeps what each commit asked for and how it ended: its paths, the hash each file held
before (None: a new file) and the hash it was to hold after, its backups, and the reason when
it was refused. owned is the record of NG Runner's own files that replaces owned_files.json,
commit_write's JSON registry: the sha256 NG Runner last wrote at each path. A file is NG
Runner's to overwrite only while it still holds exactly that (GLOSSARY: Owned file).

Path keys are built exactly as writes.py builds its registry keys today, str(path).casefold()
(writes._key), so wave 2 can swap the JSON registry for this table: owned_sha(store, path) is
the owned(path) commit_write asks, and record_owned(store, written, at=...) is its
record(written) with written = {path: sha256}. Record the paths commit_write reports
(CommitResult.written), which are spelled from its resolved roots, as its registry does.
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from ngrunner.store.db import (
    Db, dumps, iso, loads, optional_text, session, sha256_value, time_of,
)


class FileWriteLike(Protocol):
    """What record_commit reads of each write: writes.FileWrite."""

    @property
    def path(self) -> Path: ...

    @property
    def content(self) -> bytes: ...

    @property
    def expected_hash(self) -> str | None: ...


class CommitResultLike(Protocol):
    """What record_commit reads of the result: writes.CommitResult."""

    @property
    def ok(self) -> bool: ...

    @property
    def reason(self) -> str | None: ...

    @property
    def backups(self) -> Iterable[Path]: ...


@dataclass(frozen=True)
class WriteRecord:
    """One commit. before: the sha256 each file held before (None: a new file); after: the
    sha256 of what each was to hold, which it holds only if ok."""

    id: int
    at: datetime
    ok: bool
    reason: str | None
    paths: tuple[Path, ...]
    before: tuple[str | None, ...]
    after: tuple[str, ...]
    backups: tuple[Path, ...]


# ----- writes: one row per commit


def record_commit(
    db: Db, writes: Sequence[FileWriteLike], result: CommitResultLike, *, at: datetime
) -> WriteRecord:
    """Record one commit_write call: the writes it was given and the CommitResult it gave."""
    if not isinstance(result.ok, bool):
        raise ValueError("a commit's result must say ok as True or False")
    paths = [_full(w.path) for w in writes]
    before = [None if w.expected_hash is None else sha256_value(str(w.expected_hash).lower())
              for w in writes]
    after = [hashlib.sha256(_bytes(w.content)).hexdigest() for w in writes]
    values = (iso(at, "at"), int(result.ok), optional_text(result.reason, "a reason"),
              dumps(paths), dumps(before), dumps(after),
              dumps([_full(b) for b in result.backups]))
    with session(db, write=True) as conn:
        cursor = conn.execute(
            "INSERT INTO writes (at, ok, reason, paths, before, after, backups)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        row = conn.execute("SELECT * FROM writes WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _record(row)


def list_write_records(db: Db, *, since: datetime | None = None) -> list[WriteRecord]:
    """Commits in the order they were made; from since (included), when given."""
    sql, params = "SELECT * FROM writes", []
    if since is not None:
        sql += " WHERE at >= ?"
        params.append(iso(since, "since"))
    with session(db, write=False) as conn:
        rows = conn.execute(sql + " ORDER BY at, id", params).fetchall()
    return [_record(r) for r in rows]


# ----- owned: the files NG Runner wrote, and what it wrote there


def path_key(path: Path) -> str:
    """The owned table's key for path: str(path).casefold(), exactly writes._key today."""
    return str(path).casefold()


def owned_sha(db: Db, path: Path) -> str | None:
    """The sha256 NG Runner last wrote at path, or None: commit_write's owned(path)."""
    with session(db, write=False) as conn:
        row = conn.execute(
            "SELECT sha256 FROM owned WHERE path_key = ?", (path_key(_full(path)),)
        ).fetchone()
    return None if row is None else row["sha256"]


def record_owned(db: Db, written: Mapping[Path, str], *, at: datetime) -> None:
    """Record that NG Runner wrote these files ({path: sha256 of what it wrote}), replacing
    what was recorded for them; commit_write's record(written)."""
    when = iso(at, "at")
    rows = [(path_key(_full(path)), sha256_value(digest), when) for path, digest in written.items()]
    with session(db, write=True) as conn:
        conn.executemany(
            "INSERT INTO owned (path_key, sha256, at) VALUES (?, ?, ?)"
            " ON CONFLICT (path_key) DO UPDATE SET sha256 = excluded.sha256, at = excluded.at",
            rows,
        )


# ----- values in and out


def _full(path: object) -> str:
    if not isinstance(path, Path | str) or not Path(path).is_absolute():
        raise ValueError("a written path must be a full path")
    return str(path)


def _bytes(content: object) -> bytes:
    if not isinstance(content, bytes | bytearray):
        raise ValueError("a write's content must be bytes")
    return bytes(content)


def _record(row: sqlite3.Row) -> WriteRecord:
    return WriteRecord(
        id=row["id"],
        at=time_of(row["at"]),
        ok=bool(row["ok"]),
        reason=row["reason"],
        paths=tuple(Path(p) for p in loads(row["paths"])),
        before=tuple(loads(row["before"])),
        after=tuple(loads(row["after"])),
        backups=tuple(Path(p) for p in loads(row["backups"])),
    )
