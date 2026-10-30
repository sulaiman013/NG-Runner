"""
The store's tables, as ordered migrations (build spec section 4).

Why migrations: NG Runner is updated in place on Jim's PC (design section 6, "Updates") and
keeps its database across versions, so each version must open a database made by any earlier
one. The schema version is PRAGMA user_version, 0 for a new or empty file: migration n takes a
database from version n-1 to n. An upgrade runs in one transaction with its version bumps, so
it happens completely or not at all, even if NG Runner stops half-way, and the write lock taken
first means two processes opening a new store never both migrate it. A database newer than the
code is refused, never touched: the launcher may have fallen back to an older version, and an
older version must not write into tables it doesn't know.

Adding a migration: append a new step, never edit or reorder one that has shipped. SQLite can't
change a column's constraints in place (that is a table rebuild), so value sets that grow with
each phase (states, modes, kinds, lanes) are checked in Python, not by CHECK constraints.
Tables are STRICT: a value of the wrong type is refused, never silently converted.
"""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Sequence

from ngrunner.store.db import (
    BUSY_TIMEOUT_MS, Store, StoreError, connect, plain_errors, rollback_quietly,
)

# Version 1: the P1a tables. Columns follow build spec section 4, in its order.
_V1 = (
    """CREATE TABLE IF NOT EXISTS jobs (
        studio_id    TEXT PRIMARY KEY NOT NULL,
        name         TEXT NOT NULL,
        name_frozen  INTEGER NOT NULL DEFAULT 0,
        branch       TEXT,
        designer     TEXT,
        install_text TEXT,
        folder       TEXT,
        mode         TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS aliases (
        studio_id TEXT NOT NULL REFERENCES jobs (studio_id),
        name      TEXT NOT NULL,
        seen_at   TEXT NOT NULL,
        PRIMARY KEY (studio_id, name)
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS fab_rows (
        id            INTEGER PRIMARY KEY,
        first_seen    TEXT NOT NULL,
        text          TEXT NOT NULL,
        designer      TEXT NOT NULL,
        install_text  TEXT,
        fab_date      TEXT,
        original_text TEXT NOT NULL,
        sheet_row     INTEGER,
        state         TEXT NOT NULL,
        studio_id     TEXT REFERENCES jobs (studio_id),
        reason        TEXT,
        updated_at    TEXT NOT NULL
    ) STRICT""",
    "CREATE INDEX IF NOT EXISTS fab_rows_by_state ON fab_rows (state)",
    """CREATE TABLE IF NOT EXISTS opportunities (
        studio_id TEXT PRIMARY KEY NOT NULL,
        name      TEXT NOT NULL,
        stage     TEXT NOT NULL,
        owner     TEXT NOT NULL,
        client    TEXT
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS list_state (
        synced_at  TEXT,
        fetched_at TEXT NOT NULL,
        job_count  INTEGER NOT NULL
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS renders (
        id            INTEGER PRIMARY KEY,
        studio_id     TEXT NOT NULL REFERENCES jobs (studio_id),
        fetched_at    TEXT NOT NULL,
        url           TEXT,
        sha256        TEXT,
        page_count    INTEGER,
        creation_date TEXT,
        fingerprint   TEXT,
        layout        TEXT,
        check_ok      INTEGER NOT NULL,
        missing       TEXT NOT NULL DEFAULT '[]',
        project_name  TEXT,
        cache_path    TEXT,
        governing     INTEGER NOT NULL DEFAULT 0
    ) STRICT""",
    "CREATE INDEX IF NOT EXISTS renders_by_job ON renders (studio_id, fetched_at)",
    # one governing render per job, whoever writes it
    "CREATE UNIQUE INDEX IF NOT EXISTS renders_one_governing ON renders (studio_id)"
    " WHERE governing = 1",
    """CREATE TABLE IF NOT EXISTS pending (
        id          INTEGER PRIMARY KEY,
        kind        TEXT NOT NULL,
        studio_id   TEXT,
        fab_row_id  INTEGER,
        due_at      TEXT NOT NULL,
        deadline_at TEXT,
        attempts    INTEGER NOT NULL DEFAULT 0,
        state       TEXT NOT NULL,
        note        TEXT
    ) STRICT""",
    "CREATE INDEX IF NOT EXISTS pending_by_due ON pending (state, due_at)",
    # one open operation of a kind per job or row, so a restart never queues it twice
    "CREATE UNIQUE INDEX IF NOT EXISTS pending_one_open"
    " ON pending (kind, ifnull(studio_id, ''), ifnull(fab_row_id, 0)) WHERE state = 'open'",
    """CREATE TABLE IF NOT EXISTS runs (
        id             INTEGER PRIMARY KEY,
        studio_id      TEXT,
        stage          TEXT NOT NULL,
        trigger        TEXT NOT NULL,
        started_at     TEXT NOT NULL,
        ended_at       TEXT,
        outcome        TEXT,
        reason         TEXT,
        app_version    TEXT NOT NULL,
        read_hashes    TEXT NOT NULL DEFAULT '{}',
        written_hashes TEXT NOT NULL DEFAULT '{}'
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS cards (
        id             TEXT PRIMARY KEY NOT NULL,
        studio_id      TEXT,
        fab_row_id     INTEGER,
        kind           TEXT NOT NULL,
        text           TEXT NOT NULL,
        choices        TEXT NOT NULL,
        default_choice TEXT,
        created_at     TEXT NOT NULL,
        state          TEXT NOT NULL,
        answer         TEXT,
        answer_words   TEXT,
        answered_by    TEXT,
        answered_at    TEXT,
        channel        TEXT,
        reminded_at    TEXT NOT NULL DEFAULT '[]'
    ) STRICT""",
    "CREATE INDEX IF NOT EXISTS cards_by_state ON cards (state)",
    """CREATE TABLE IF NOT EXISTS card_numbers (
        prefix TEXT PRIMARY KEY NOT NULL,
        last   INTEGER NOT NULL
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS events (
        id        INTEGER PRIMARY KEY,
        at        TEXT NOT NULL,
        studio_id TEXT,
        kind      TEXT NOT NULL,
        text      TEXT NOT NULL,
        lane      TEXT NOT NULL
    ) STRICT""",
    "CREATE INDEX IF NOT EXISTS events_by_time ON events (at)",
    """CREATE TABLE IF NOT EXISTS writes (
        id      INTEGER PRIMARY KEY,
        at      TEXT NOT NULL,
        ok      INTEGER NOT NULL,
        reason  TEXT,
        paths   TEXT NOT NULL,
        before  TEXT NOT NULL,
        after   TEXT NOT NULL,
        backups TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS owned (
        path_key TEXT PRIMARY KEY NOT NULL,
        sha256   TEXT NOT NULL,
        at       TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS outbox (
        id          TEXT PRIMARY KEY NOT NULL,
        seq         INTEGER NOT NULL UNIQUE,
        channel     TEXT NOT NULL,
        recipient   TEXT NOT NULL,
        subject     TEXT NOT NULL,
        lines       TEXT NOT NULL,
        urgent      INTEGER NOT NULL,
        ref         TEXT,
        created_at  TEXT NOT NULL,
        written_at  TEXT,
        receipt_at  TEXT,
        fallback_at TEXT,
        state       TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS seqs (
        stream TEXT PRIMARY KEY NOT NULL,
        last   INTEGER NOT NULL
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS seen_ids (
        stream TEXT NOT NULL,
        id     TEXT NOT NULL,
        PRIMARY KEY (stream, id)
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS dev (
        id          INTEGER PRIMARY KEY,
        at          TEXT NOT NULL,
        code        TEXT NOT NULL,
        text        TEXT NOT NULL,
        state       TEXT NOT NULL,
        told_jim_at TEXT
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS state (
        key   TEXT PRIMARY KEY NOT NULL,
        value TEXT NOT NULL
    ) STRICT""",
)

# Version 2: what the parts of P1a turned out to need from each other. SQLite adds a NOT NULL
# column only with a default, so where that default is no answer the next statement fills the
# rows already there.
_V2 = (
    # Which job each of a card's choices means, parallel to choices. Nothing recorded it before
    # this, so an older card names no job: an answer to it can never link one.
    "ALTER TABLE cards ADD COLUMN choice_ids TEXT NOT NULL DEFAULT '[]'",
    "UPDATE cards SET choice_ids ="
    " (SELECT json_group_array(NULL) FROM json_each(cards.choices))",
    # When a row's text last changed. An edit before this kept no time of its own, so an edited
    # row takes its last update, the latest that edit can have been: a list must be fresher still.
    "ALTER TABLE fab_rows ADD COLUMN text_changed_at TEXT NOT NULL DEFAULT ''",
    "UPDATE fab_rows SET text_changed_at ="
    " CASE WHEN text = original_text THEN first_seen ELSE updated_at END",
    # Which job a DEV item is about, and whether it turns the tray red.
    "ALTER TABLE dev ADD COLUMN studio_id TEXT",
    "ALTER TABLE dev ADD COLUMN urgent INTEGER NOT NULL DEFAULT 0",
    # The pypdf version that read a render's fingerprint. Not known for an older one, so it is
    # read again before it is compared.
    "ALTER TABLE renders ADD COLUMN pypdf TEXT",
)

MIGRATIONS: tuple[tuple[str, ...], ...] = (_V1, _V2)
LATEST = len(MIGRATIONS)


def migrate(store: Store, steps: Sequence[Sequence[str]] = MIGRATIONS) -> int:
    """Bring the database up to len(steps), applying only the steps it lacks, in order, in one
    transaction; returns the version it is at. Running it again changes nothing. A database
    newer than len(steps) is refused (StoreError) and left exactly as it was."""
    with plain_errors():
        conn = connect(store.path)
        try:
            _refuse_newer(conn, len(steps))  # before anything is written, WAL mode included
            mode = _use_wal(conn)  # persists in the file
            if str(mode).lower() != "wal":
                raise StoreError(
                    "NG Runner's database can't use write-ahead logging on this disk; its data"
                    " folder must be on a local drive."
                )
            conn.execute("BEGIN IMMEDIATE")  # one migrator at a time, across processes
            try:
                version = _refuse_newer(conn, len(steps))  # again: another may have migrated
                for number in range(version + 1, len(steps) + 1):
                    for statement in steps[number - 1]:
                        conn.execute(statement)
                    # a pragma takes no parameters; number is an int from this loop
                    conn.execute(f"PRAGMA user_version = {number:d}")
            except BaseException:
                rollback_quietly(conn)
                raise
            conn.execute("COMMIT")
            return len(steps)
        finally:
            conn.close()


def _use_wal(conn: sqlite3.Connection) -> str:
    """Switch the file to write-ahead logging. That takes the file for itself, and it is the one
    lock SQLite answers at once instead of waiting busy_timeout for, so two processes opening a
    new store would otherwise see one of them fail. Waits as busy_timeout would."""
    deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
    while True:
        try:
            return str(conn.execute("PRAGMA journal_mode = WAL").fetchone()[0])
        except sqlite3.OperationalError as exc:
            busy = (getattr(exc, "sqlite_errorname", None) or "").startswith("SQLITE_BUSY")
            if not busy or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def check_readable(store: Store) -> None:
    """Refuse (StoreError) a file that is not a database, or a damaged one, before anything is
    written to it: PRAGMA quick_check must read every page and find them sound."""
    with plain_errors():
        conn = connect(store.path)
        try:
            verdict = [row[0] for row in conn.execute("PRAGMA quick_check")]
        finally:
            conn.close()
    if verdict != ["ok"]:
        raise StoreError(
            "NG Runner's database is damaged (SQLite's check found problems), so it can't be"
            " used; restore it from a backup."
        )


def _refuse_newer(conn: sqlite3.Connection, known: int) -> int:
    """The file's schema version; StoreError when it is newer than this code knows."""
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version > known:
        raise StoreError(_too_new(version, known))
    return version


def _too_new(version: int, known: int) -> str:
    return (
        f"NG Runner's database was last used by a newer version of NG Runner (schema {version};"
        f" this version knows up to {known}), so this version won't use it. Start the newer"
        " version, or restore a backup made by this one."
    )
