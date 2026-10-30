r"""
NG Runner's database: one SQLite file in its own data folder (AppPaths.db,
%LOCALAPPDATA%\NGRunner\ngrunner.db), holding what it knows about jobs, Fab rows, renders,
cards, messages and its own writes (build spec section 4).

Why this shape: the supervisor, the board server and the tray run in threads of one process,
and the self-test and support tools open the same file from other processes. So nothing holds a
connection open. Every store function opens its own short-lived connection, with WAL (readers
never wait for a writer), busy_timeout 5 s (a writer waits its turn instead of failing) and
foreign keys on, and every write takes the write lock as it starts (BEGIN IMMEDIATE), so two
writers never deadlock trying to upgrade a read. The few steps that change several tables at
once run in Store.transaction() and pass the transaction to the same functions: they all
commit together, or none does.

Every time is stored as clock.to_iso() text (UTC, with its +00:00 offset) and read back with
from_iso(); a naive time is refused before anything is written. That text sorts in time order
(fixed width to the second, and the "+" of the offset sorts before the "." of a fraction), so
SQL compares times as text.

Failures of the database come back as StoreError, the reason in plain words; a value that
breaks a function's contract is refused with ValueError, a missing row with LookupError.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from ngrunner.clock import from_iso, to_iso

BUSY_TIMEOUT_MS = 5000
_OPEN = threading.local()  # .keys: the stores this thread has a transaction open on
_SAVEPOINT = "store_call"


class StoreError(Exception):
    """The database could not do what was asked; the message says why, in plain words."""


# ----- the store and its transactions


@dataclass(frozen=True)
class Store:
    """NG Runner's database file. Make it with open_store(), which creates or upgrades the
    tables; Store(path) alone checks nothing. Holds no connection, so one Store is shared by
    every thread, and other processes may open the same file."""

    path: Path

    def __post_init__(self) -> None:
        if not Path(self.path).is_absolute():
            raise StoreError("NG Runner's database must be given as a full path with a drive")
        object.__setattr__(self, "path", Path(self.path))

    @contextmanager
    def transaction(self) -> Iterator[Transaction]:
        """with store.transaction() as tx: pass tx, not the store, to each store function in
        the block. Its changes commit together when the block ends, or are all undone if it
        raises. The write lock is held from the start, so keep the block short and do no slow
        work (network, files) inside it. A second transaction on the same store in the same
        thread, or a store function given the store instead of tx inside the block, is
        refused: it would wait for this one and fail. So commit_write never runs inside one:
        its owned() lookups (owned_sha with the store) would be refused, and so would it."""
        key = _key(self.path)
        if key in _open_keys():
            raise StoreError(
                "A transaction is already open on NG Runner's database in this thread;"
                " pass it on instead of starting another."
            )
        with plain_errors():
            conn = _connect(self.path)
        try:
            with plain_errors():
                conn.execute("BEGIN IMMEDIATE")
            tx = Transaction(self, conn)
            _open_keys().add(key)
            try:
                yield tx
                if tx._broken:
                    raise StoreError(tx._broken)
            except BaseException:
                tx._finish()
                rollback_quietly(conn)
                raise
            finally:
                _open_keys().discard(key)
            tx._finish()
            with plain_errors():
                conn.execute("COMMIT")
        finally:
            conn.close()

    def schema_version(self) -> int:
        """The schema version the file is at (PRAGMA user_version; 0 before any migration, and
        for a file not there yet, which reading this makes, empty)."""
        with session(self, write=False) as conn:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])

    def backup_to(self, path: Path) -> None:
        """Copy the whole database, as one consistent moment, to a new file at path, with
        SQLite's online backup API, while other threads and processes go on using it. The copy
        is a single self-contained file (rollback journal, no -wal file beside it), checked
        with PRAGMA integrity_check before this returns; open_store() opens it like the
        original. path must not exist yet, and must be inside the database's own folder
        (NG Runner's data folder): anything that leaves it goes through commit_write
        (AGENTS.md rule 1). A failed copy is removed again; raises StoreError.

        Its one caller is the supervisor's copy before an upgrade, when schema_version() is above
        0 (a new file has nothing to keep) and below LATEST: open_store migrates at once, and an
        older version then refuses the file, so a fallback needs a copy from before. Everything
        else that copies the database uses backup.snapshot, which gives the bytes with no temp
        file."""
        target = Path(path)
        reason = _backup_target_problem(self.path, target)
        if reason:
            raise StoreError(reason)
        if _key(self.path) in _open_keys():
            raise StoreError("A backup can't be made inside one of the store's own transactions.")
        try:
            with open(target, "xb"):  # made here, so nothing already there is ever overwritten
                pass
        except OSError as exc:
            raise StoreError(f"The backup file could not be made ({_os_why(exc)}).") from exc
        try:
            with plain_errors():
                _copy(self.path, target)
        except BaseException:
            _remove_quietly(target)
            raise


class Transaction:
    """An open transaction on the store (see Store.transaction). Store functions given it run
    inside it, each one all or nothing (a savepoint), so a function that fails leaves the rest
    of the transaction as it was. It belongs to the thread that opened it and ends with the
    with block."""

    def __init__(self, store: Store, conn: sqlite3.Connection) -> None:
        self.store = store
        self._conn: sqlite3.Connection | None = conn
        self._thread = threading.get_ident()
        self._broken: str | None = None

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StoreError("This transaction is over; start a new one with store.transaction().")
        if threading.get_ident() != self._thread:
            raise StoreError("A transaction can only be used in the thread that started it.")
        if self._broken:
            raise StoreError(self._broken)
        return self._conn

    def _finish(self) -> None:
        self._conn = None


Db = Store | Transaction  # what every store function takes first


# ----- sessions: one function's SQL, all or nothing


@contextmanager
def session(db: Db, *, write: bool) -> Iterator[sqlite3.Connection]:
    """The connection one store function runs its SQL on (for the table modules, not for
    callers). Given the Store: a new connection and transaction (BEGIN IMMEDIATE to write,
    BEGIN to read one consistent moment), committed when the block ends, rolled back if it
    raises, then closed. Given a Transaction: a savepoint inside it. Database errors become
    StoreError; other errors pass through unchanged, after the rollback."""
    with plain_errors():
        if isinstance(db, Transaction):
            with _savepoint(db) as conn:
                yield conn
            return
        if not isinstance(db, Store):
            raise TypeError("a store function needs the Store, or a Transaction from it")
        if _key(db.path) in _open_keys():
            raise StoreError(
                "A store function was given the store inside one of its own transactions;"
                " pass the transaction instead."
            )
        conn = _connect(db.path)
        try:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield conn
            except BaseException:
                rollback_quietly(conn)
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()


@contextmanager
def _savepoint(tx: Transaction) -> Iterator[sqlite3.Connection]:
    conn = tx._connection()
    conn.execute(f"SAVEPOINT {_SAVEPOINT}")
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:
            conn.execute(f"ROLLBACK TO {_SAVEPOINT}")
            conn.execute(f"RELEASE {_SAVEPOINT}")
        else:  # SQLite rolled the whole transaction back itself (a full disk, an I/O error)
            tx._broken = (
                "NG Runner's database undid this whole transaction after an error,"
                " so nothing in it was saved."
            )
        raise
    conn.execute(f"RELEASE {_SAVEPOINT}")


def connect(path: Path) -> sqlite3.Connection:
    """A new connection with the store's settings, outside any transaction (autocommit: the
    store says BEGIN and COMMIT itself). For the table modules and the schema runner."""
    return _connect(path)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000, autocommit=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS:d}")
        conn.execute("PRAGMA foreign_keys = ON")
    except BaseException:
        conn.close()
        raise
    return conn


def rollback_quietly(conn: sqlite3.Connection) -> None:
    try:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass  # closing the connection undoes whatever is left


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _open_keys() -> set[str]:
    keys = getattr(_OPEN, "keys", None)
    if keys is None:
        keys = _OPEN.keys = set()
    return keys


# ----- backups


def _copy(source_path: Path, target: Path) -> None:
    source = _connect(source_path)
    try:
        dest = sqlite3.connect(str(target), autocommit=True)
        try:
            source.backup(dest)
            dest.execute("PRAGMA journal_mode = DELETE")  # one file, nothing beside it
            verdict = [row[0] for row in dest.execute("PRAGMA integrity_check")]
        finally:
            dest.close()
    finally:
        source.close()
    if verdict != ["ok"]:
        raise StoreError("The backup copy did not pass SQLite's integrity check.")


def _backup_target_problem(db_path: Path, target: Path) -> str | None:
    if not target.is_absolute():
        return "The backup must be given as a full path with a drive."
    folder = [part.casefold() for part in Path(os.path.realpath(db_path.parent)).parts]
    where = [part.casefold() for part in Path(os.path.realpath(target.parent)).parts]
    if where[: len(folder)] != folder:
        return (
            "A backup is made only inside NG Runner's own data folder; copies that leave it"
            " go through commit_write."
        )
    if os.path.lexists(target):
        return "A file is already at the backup path, so it was not overwritten."
    return None


def _remove_quietly(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass  # in use or gone: nothing else is touched


# ----- plain reasons


@contextmanager
def plain_errors() -> Iterator[None]:
    try:
        yield
    except sqlite3.Error as exc:
        raise StoreError(_why(exc)) from exc


_REASONS = (
    (("SQLITE_BUSY", "SQLITE_LOCKED"),
     "NG Runner's database stayed busy for over 5 seconds (another part of NG Runner held it)"),
    (("SQLITE_NOTADB", "SQLITE_CORRUPT"),
     "NG Runner's database is damaged or is not a database, so it can't be used;"
     " restore it from a backup"),
    (("SQLITE_CANTOPEN",),
     "NG Runner's database could not be opened (its folder may be missing, or the file locked)"),
    (("SQLITE_FULL",), "The disk is full, so NG Runner's database could not be changed"),
    (("SQLITE_READONLY",), "NG Runner's database is read-only, so it could not be changed"),
    (("SQLITE_IOERR",), "The disk failed while NG Runner was using its database"),
)


def _why(exc: sqlite3.Error) -> str:
    name = getattr(exc, "sqlite_errorname", None) or ""
    for prefixes, reason in _REASONS:
        if name.startswith(prefixes):
            return f"{reason}."
    if name.startswith("SQLITE_CONSTRAINT"):
        return f"NG Runner's database refused a change that breaks its rules ({exc})."
    return f"NG Runner's database failed ({exc})."


def _os_why(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


# ----- values in and out (for the table modules)


def iso(when: datetime, what: str = "a time") -> str:
    """A time as stored: to_iso() text. A naive time is refused (ValueError), as is a non-time."""
    if not isinstance(when, datetime):
        raise ValueError(f"{what} must be a timezone-aware datetime")
    return to_iso(when)


def iso_or_none(when: datetime | None, what: str = "a time") -> str | None:
    return None if when is None else iso(when, what)


def time_of(text: str) -> datetime:
    return from_iso(text)


def time_or_none(text: str | None) -> datetime | None:
    return None if text is None else from_iso(text)


def dumps(value: Any) -> str:
    """JSON as stored: compact, keys sorted, non-ASCII kept as written."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads(text: str) -> Any:
    return json.loads(text)


class Keep(Enum):
    """The default of an update's fields: leave the stored value as it is (None clears it)."""

    KEEP = "keep"


KEEP = Keep.KEEP


_CODE = re.compile(r"[a-z][a-z0-9_]{0,39}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def code_value(value: object, what: str, example: str) -> str:
    """A short lowercase name used as a code (a kind, a stage, a stream), like example."""
    if not isinstance(value, str) or not _CODE.fullmatch(value):
        raise ValueError(f"{what} must be a short lowercase name, like {example}")
    return value


def sha256_value(value: object, what: str = "a sha256") -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{what} must be 64 lowercase hex characters")
    return value


def key_text(value: object, what: str) -> str:
    """An identifier such as a Studio project ID: non-blank text with no space around it."""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{what} must be non-blank text with no spaces around it")
    return value


def text_value(value: object, what: str, *, empty: bool = False) -> str:
    """value as text, refused (ValueError) when it is not a str, or blank unless empty=True."""
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"{what} must be {'text' if empty else 'non-blank text'}")
    return value


def optional_text(value: object, what: str) -> str | None:
    return None if value is None else text_value(value, what, empty=True)


def one_of(value: object, allowed: tuple[str, ...], what: str) -> str:
    if value not in allowed:
        raise ValueError(f"{what} must be one of: {', '.join(allowed)}")
    return str(value)


def whole_number(value: object, what: str, *, at_least: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{what} must be a whole number")
    if at_least is not None and value < at_least:
        raise ValueError(f"{what} must be at least {at_least}")
    return value
