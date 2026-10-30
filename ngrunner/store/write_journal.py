r"""
The write journal: crash recovery for the record of NG Runner's own files (integration spec
section 3; docs/decisions.md, "The write chokepoint").

Why: commit_write renames a commit's files into place, reads them back, and only then records
their hashes in owned. A crash in between would leave files NG Runner wrote but no longer counts
as its own, and it would refuse to overwrite them for good. So a row goes in here before the
first rename: the paths, the hashes they are to hold, state pending. The row leaves pending in
the same transaction that records the hashes (done), or when the commit ends with nothing
changed (aborted). A row still pending while no commit is running is a commit that never ended.

settle_pending_writes runs at start-up and settles those rows. A path holding exactly its intended
hash is adopted into owned. Any other path is left alone, so a file someone else changed is never
adopted, and nothing is ever deleted. The row is then closed and the case logged to DEV. A path it
can't see keeps its row pending: closing it would strand a file NG Runner could not overwrite.

StoreRegistry is what commit_write's registry= takes. The commit lock lives here because writes.py
imports this package, and a settle needs it too: while a commit runs, its own row is pending.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ngrunner.clock import Clock
from ngrunner.store.db import (
    Db, Store, StoreError, dumps, iso, loads, one_of, session, sha256_value, time_of, whole_number,
)
from ngrunner.store.events import open_dev
from ngrunner.store.write_record import owned_sha, record_owned

JOURNAL_STATES = ("pending", "done", "aborted")
COMMIT_LOCK = threading.Lock()  # one commit at a time in this process; no settle during one

_NAME_SURROGATE = 0x20000000  # as in writes.py: the reparse-tag bit of links
_ADOPT, _LEAVE, _WAIT = "adopt", "leave", "wait"


@dataclass(frozen=True)
class PendingWrite:
    """One commit's row: the paths it was to write and the sha256 each was to hold."""

    id: int
    at: datetime
    paths: tuple[Path, ...]
    hashes: tuple[str, ...]
    state: str


@dataclass(frozen=True)
class Settled:
    """What a settle did. adopted: paths that now count as NG Runner's own. left: paths left alone
    (nothing is ever deleted). waiting: paths it could not see, their rows still pending."""

    adopted: tuple[Path, ...] = ()
    left: tuple[Path, ...] = ()
    waiting: tuple[Path, ...] = ()


# ----- the registry commit_write is given


class StoreRegistry:
    """commit_write's registry=: the store's owned table, with the journal. commit_write decides
    which paths get a row (NG Jobs and control\\shadow), so every path given here gets one. A
    database failure raises StoreError, which commit_write turns into its plain reason."""

    def __init__(self, store: Store, clock: Clock) -> None:
        self._store = store
        self.clock = clock

    def owned(self, path: Path) -> str | None:
        """The sha256 NG Runner last wrote at path, or None."""
        return owned_sha(self._store, path)

    def begin(self, planned: Mapping[Path, str]) -> int:
        """Before the first rename: note the paths and the hashes they are to hold. Returns the
        journal id for record and abort."""
        paths, hashes = _columns(planned)
        with session(self._store, write=True) as conn:
            cursor = conn.execute(
                "INSERT INTO pending_writes (at, paths, hashes, state)"
                " VALUES (?, ?, ?, 'pending')",
                (iso(self.clock.now(), "at"), dumps(paths), dumps(hashes)),
            )
            return int(cursor.lastrowid)

    def record(self, journal_id: int, written: Mapping[Path, str]) -> None:
        """After the read-back: the hashes go into owned and the row to done, together."""
        with self._store.transaction() as tx:
            record_owned(tx, written, at=self.clock.now())
            _close(tx, journal_id, "done")

    def abort(self, journal_id: int) -> None:
        """The commit ended with nothing changed: close its row."""
        _close(self._store, journal_id, "aborted")


def list_pending_writes(db: Db, *, state: str = "pending") -> list[PendingWrite]:
    """The journal's rows in that state (pending by default), oldest first."""
    wanted = one_of(state, JOURNAL_STATES, "a journal state")
    with session(db, write=False) as conn:
        rows = conn.execute(
            "SELECT * FROM pending_writes WHERE state = ? ORDER BY id", (wanted,)
        ).fetchall()
    return [_row(r) for r in rows]


def _close(db: Db, journal_id: int, state: str) -> None:
    with session(db, write=True) as conn:
        conn.execute(
            "UPDATE pending_writes SET state = ? WHERE id = ? AND state = 'pending'",
            (state, whole_number(journal_id, "a journal id")),
        )


# ----- settling at start-up


def settle_pending_writes(store: Store, *, at: datetime) -> Settled:
    """Settle every commit that never ended (see the module's docstring). Safe to call again: the
    supervisor runs it at start-up, and again later while anything is waiting. Reading the files is
    slow work, so a transaction is refused (StoreError). If a commit is running right now, nothing
    is settled this time: all that is pending comes back as waiting. Never blocks on a commit."""
    if not isinstance(store, Store):
        raise StoreError("Writes can't be settled inside one of the store's transactions.")
    pending = list_pending_writes(store)
    if not pending:
        return Settled()
    if not COMMIT_LOCK.acquire(blocking=False):
        return Settled(waiting=tuple(path for row in pending for path in row.paths))
    try:
        settled = [_settle_row(store, row, at) for row in list_pending_writes(store)]
    finally:
        COMMIT_LOCK.release()
    return Settled(
        adopted=tuple(p for s in settled for p in s.adopted),
        left=tuple(p for s in settled for p in s.left),
        waiting=tuple(p for s in settled for p in s.waiting),
    )


def _settle_row(store: Store, row: PendingWrite, at: datetime) -> Settled:
    found: dict[str, list[Path]] = {_ADOPT: [], _LEAVE: [], _WAIT: []}
    for path, digest in zip(row.paths, row.hashes, strict=True):
        found[_look(path, digest)].append(path)
    adopted, left, waiting = found[_ADOPT], found[_LEAVE], found[_WAIT]
    hashes = dict(zip(row.paths, row.hashes, strict=True))
    with store.transaction() as tx:
        record_owned(tx, {path: hashes[path] for path in adopted}, at=at)
        if not waiting:
            _close(tx, row.id, "done" if adopted else "aborted")
            open_dev(tx, at=at, code="write_settled", text=_what_happened(adopted, left))
    return Settled(tuple(adopted), tuple(left), tuple(waiting))


def _look(path: Path, digest: str) -> str:
    """What to do about one path. Absent from a folder NG Runner can see: the rename never
    happened, or was put back. Its folder out of sight, or any other failure to look: it can't
    tell, so wait."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return _LEAVE if os.path.isdir(path.parent) else _WAIT
    except OSError:
        return _WAIT
    if _is_link(st) or not stat.S_ISREG(st.st_mode):
        return _LEAVE
    try:
        with open(path, "rb") as f:
            held = hashlib.file_digest(f, "sha256").hexdigest()
    except OSError:  # locked by a sync client, or gone this moment: look again next time
        return _WAIT
    return _ADOPT if held == digest else _LEAVE


def _is_link(st: os.stat_result) -> bool:
    """As writes._is_link, kept apart so that the chokepoint stays self-contained. A link was put
    there by someone else, so it is never adopted; OneDrive's placeholders are not links."""
    if stat.S_ISLNK(st.st_mode):
        return True
    if not getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    tag = getattr(st, "st_reparse_tag", 0)
    return not tag or bool(tag & _NAME_SURROGATE)


def _what_happened(adopted: list[Path], left: list[Path]) -> str:
    """For the DEV lane. Names only: a full path would fail the messages' lint."""
    lines = ["NG Runner stopped part-way through writing files, and checked them on restarting."]
    if adopted:
        lines.append(f"These hold what it meant to write, so they are its own: {_names(adopted)}.")
    if left:
        lines.append(f"These do not, so it left them alone: {_names(left)}.")
    return " ".join(lines)


def _names(paths: list[Path]) -> str:
    return ", ".join(path.name for path in paths)


# ----- values in and out


def _columns(planned: Mapping[Path, str]) -> tuple[list[str], list[str]]:
    if not planned:
        raise ValueError("a journal row must name at least one path")
    paths, hashes = [], []
    for path, digest in planned.items():
        if not isinstance(path, Path | str) or not Path(path).is_absolute():
            raise ValueError("a written path must be a full path")
        paths.append(str(path))
        hashes.append(sha256_value(digest))
    return paths, hashes


def _row(row: sqlite3.Row) -> PendingWrite:
    return PendingWrite(
        id=row["id"],
        at=time_of(row["at"]),
        paths=tuple(Path(p) for p in loads(row["paths"])),
        hashes=tuple(loads(row["hashes"])),
        state=row["state"],
    )
