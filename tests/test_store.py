"""
The store's database itself (build spec section 4): opening and migrating it, refusing one it
can't use, transactions and backups. Every database lives under tmp_path; tests go through
ngrunner.store's public interface (the table round trips are in test_store_tables.py).
"""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ngrunner.store import (
    LATEST, MIGRATIONS, Store, StoreError, add_card, allocate_card_id, ensure_job, get_state,
    list_cards, list_jobs, migrate, open_store, set_state,
)

T0 = datetime(2026, 10, 30, 13, 30, tzinfo=timezone.utc)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "ngrunner.db"


# ----- opening and migrating


def test_a_new_store_is_made_at_the_latest_schema_and_works(db_path: Path) -> None:
    store = open_store(db_path)

    assert store.schema_version() == LATEST >= 1
    set_state(store, "paused", "no")
    assert get_state(store, "paused") == "no"


def test_an_empty_file_is_migrated_like_a_new_store(db_path: Path) -> None:
    db_path.write_bytes(b"")

    store = open_store(db_path)

    assert store.schema_version() == LATEST
    set_state(store, "paused", "yes")
    assert get_state(store, "paused") == "yes"


def test_opening_again_keeps_the_data_and_changes_nothing(db_path: Path) -> None:
    set_state(open_store(db_path), "install_time", "kept")

    again = open_store(db_path)

    assert again.schema_version() == LATEST
    assert get_state(again, "install_time") == "kept"


def test_a_relative_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="full path"):
        open_store(Path("ngrunner.db"))


MAKE_A = ("CREATE TABLE a (id INTEGER PRIMARY KEY)",)  # fails if run twice
ADD_NOTE = ("ALTER TABLE a ADD COLUMN note TEXT",)     # fails before MAKE_A, and if run twice


def test_migrations_run_in_order_and_only_the_missing_ones(db_path: Path) -> None:
    store = Store(db_path)

    assert migrate(store, (MAKE_A,)) == 1
    assert migrate(store, (MAKE_A, ADD_NOTE)) == 2  # MAKE_A again would fail: it is skipped
    assert migrate(store, (MAKE_A, ADD_NOTE)) == 2  # nothing left to run
    assert store.schema_version() == 2


def test_a_migration_that_fails_changes_nothing(db_path: Path) -> None:
    store = Store(db_path)

    with pytest.raises(StoreError):
        migrate(store, (MAKE_A, ("CREATE TABLE b (id INTEGER)", "THIS IS NOT SQL")))

    assert store.schema_version() == 0
    assert migrate(store, (MAKE_A, ADD_NOTE)) == 2  # table a was never left behind


# ----- databases the store refuses, each left exactly as it was


NEWER = ("CREATE TABLE IF NOT EXISTS from_the_future (id INTEGER PRIMARY KEY)",)


def test_a_database_from_a_newer_version_is_refused_and_left_as_it_was(db_path: Path) -> None:
    assert migrate(Store(db_path), (*MIGRATIONS, NEWER)) == LATEST + 1
    before = db_path.read_bytes()

    with pytest.raises(StoreError, match=f"newer version.*schema {LATEST + 1}.*up to {LATEST}"):
        open_store(db_path)

    assert db_path.read_bytes() == before
    assert Store(db_path).schema_version() == LATEST + 1


def test_a_backup_from_a_newer_version_is_refused_and_left_as_it_was(tmp_path: Path) -> None:
    newer = Store(tmp_path / "newer.db")
    migrate(newer, (*MIGRATIONS, NEWER))
    copy = tmp_path / "copy.db"
    newer.backup_to(copy)  # a single file, without write-ahead logging
    before = copy.read_bytes()

    with pytest.raises(StoreError, match="newer version"):
        open_store(copy)

    assert copy.read_bytes() == before  # not even switched to write-ahead logging


def test_a_file_that_is_not_a_database_is_refused_and_left_as_it_was(db_path: Path) -> None:
    junk = b"Testerson_101426 notes, not a database\r\n" * 200
    db_path.write_bytes(junk)

    with pytest.raises(StoreError, match="damaged or is not a database"):
        open_store(db_path)

    assert db_path.read_bytes() == junk


def test_a_damaged_database_is_refused_and_left_as_it_was(db_path: Path) -> None:
    store = open_store(db_path)
    with store.transaction() as tx:
        for n in range(300):
            set_state(tx, f"filler_{n}", "Fakeley " * 20)
    data = bytearray(db_path.read_bytes())
    half = len(data) // 2 // 4096 * 4096  # a page boundary: the header and schema stay sound
    assert half >= 4 * 4096
    data[half:] = b"\xff" * (len(data) - half)  # the second half lost, as on a failing disk
    db_path.write_bytes(bytes(data))

    with pytest.raises(StoreError, match="damaged"):
        open_store(db_path)

    assert db_path.read_bytes() == bytes(data)


def test_a_folder_in_the_databases_place_is_refused(db_path: Path) -> None:
    db_path.mkdir()

    with pytest.raises(StoreError, match="could not be opened"):
        open_store(db_path)


# ----- connections and transactions


def test_the_database_file_uses_write_ahead_logging(db_path: Path) -> None:
    open_store(db_path)
    raw = sqlite3.connect(db_path)
    try:
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"  # a property of the file
    finally:
        raw.close()


def test_a_transaction_commits_its_changes_together(db_path: Path) -> None:
    store = open_store(db_path)

    with store.transaction() as tx:
        set_state(tx, "paused", "yes")
        set_state(tx, "last_digest_day", "2026-10-30")

    assert get_state(store, "paused") == "yes"
    assert get_state(store, "last_digest_day") == "2026-10-30"


def test_a_transaction_that_raises_leaves_nothing_behind(db_path: Path) -> None:
    store = open_store(db_path)
    set_state(store, "paused", "no")

    with pytest.raises(RuntimeError):
        with store.transaction() as tx:
            set_state(tx, "paused", "yes")
            set_state(tx, "last_digest_day", "2026-10-30")
            raise RuntimeError("stopped half-way")

    assert get_state(store, "paused") == "no"
    assert get_state(store, "last_digest_day") is None


def test_other_threads_see_a_transaction_only_once_it_commits(db_path: Path) -> None:
    store = open_store(db_path)
    set_state(store, "paused", "no")
    seen: list[str | None] = []

    with store.transaction() as tx:
        set_state(tx, "paused", "yes")
        reader = threading.Thread(target=lambda: seen.append(get_state(store, "paused")))
        reader.start()
        reader.join(10)

    assert seen == ["no"]  # read at once, from the last commit, without waiting for the writer
    assert get_state(store, "paused") == "yes"


def test_a_writer_in_another_thread_waits_for_a_transaction_instead_of_failing(
    db_path: Path,
) -> None:
    store = open_store(db_path)
    errors: list[BaseException] = []

    def write_meanwhile() -> None:
        try:
            set_state(store, "toasts", "on")
        except BaseException as exc:  # noqa: BLE001 - reported by the assertion below
            errors.append(exc)

    with store.transaction() as tx:
        set_state(tx, "paused", "yes")
        writer = threading.Thread(target=write_meanwhile)
        writer.start()
        time.sleep(0.5)  # the writer is now waiting on the lock this transaction holds
        assert writer.is_alive()
    writer.join(10)

    assert errors == []
    assert (get_state(store, "paused"), get_state(store, "toasts")) == ("yes", "on")


def test_the_store_itself_is_refused_inside_its_own_transaction(db_path: Path) -> None:
    store = open_store(db_path)

    with store.transaction() as tx:
        set_state(tx, "paused", "yes")
        with pytest.raises(StoreError, match="pass the transaction"):
            set_state(store, "toasts", "on")  # would wait 5 s for tx, then fail
        with pytest.raises(StoreError, match="already open"):
            with store.transaction():
                pass

    set_state(store, "toasts", "on")  # fine again once the transaction is over
    assert get_state(store, "paused") == "yes"


def test_a_transaction_belongs_to_the_thread_that_started_it(db_path: Path) -> None:
    store = open_store(db_path)
    errors: list[BaseException] = []

    def use(tx) -> None:
        try:
            set_state(tx, "paused", "yes")
        except StoreError as exc:
            errors.append(exc)

    with store.transaction() as tx:
        other = threading.Thread(target=use, args=(tx,))
        other.start()
        other.join(10)

    assert len(errors) == 1 and "thread" in str(errors[0])
    assert get_state(store, "paused") is None


def test_a_transaction_cannot_be_used_after_its_block(db_path: Path) -> None:
    store = open_store(db_path)
    with store.transaction() as tx:
        pass

    with pytest.raises(StoreError, match="over"):
        set_state(tx, "paused", "yes")
    assert get_state(store, "paused") is None


# ----- backups


@pytest.fixture
def data_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "NGRunner"
    (folder / "backup").mkdir(parents=True)
    return folder


def test_a_backup_opens_as_a_database_with_the_same_rows(data_folder: Path) -> None:
    store = open_store(data_folder / "ngrunner.db")
    ensure_job(store, "101", "Testerson_101426", now=T0)
    add_card(store, prefix="TES", kind="which_job", text="Which job is this row?",
             choices=("Testerson_101426", "None of these"), created_at=T0)
    set_state(store, "paused", "no")
    target = data_folder / "backup" / "ngrunner-20261030.db"

    store.backup_to(target)

    assert [p.name for p in target.parent.iterdir()] == [target.name]  # one file, nothing beside it
    copy = open_store(target)
    assert copy.schema_version() == LATEST
    assert list_jobs(copy) == list_jobs(store)
    assert list_cards(copy) == list_cards(store) and get_state(copy, "paused") == "no"
    assert allocate_card_id(copy, "TES") == "TES-2"  # the card numbers came along too


def test_a_backup_is_one_moment_and_does_not_wait_for_a_writer(data_folder: Path) -> None:
    store = open_store(data_folder / "ngrunner.db")
    set_state(store, "paused", "no")
    target = data_folder / "backup" / "copy.db"
    inside, done = threading.Event(), threading.Event()

    def hold_a_transaction() -> None:
        with store.transaction() as tx:
            set_state(tx, "paused", "yes")  # not committed while the backup runs
            inside.set()
            done.wait(10)

    writer = threading.Thread(target=hold_a_transaction)
    writer.start()
    inside.wait(10)
    try:
        store.backup_to(target)
    finally:
        done.set()
        writer.join(10)

    assert get_state(open_store(target), "paused") == "no"
    assert get_state(store, "paused") == "yes"


def test_a_backup_never_overwrites_a_file(data_folder: Path) -> None:
    store = open_store(data_folder / "ngrunner.db")
    target = data_folder / "backup" / "copy.db"
    target.write_bytes(b"an older backup")

    with pytest.raises(StoreError, match="already at the backup path"):
        store.backup_to(target)

    assert target.read_bytes() == b"an older backup"


def test_a_backup_outside_ng_runners_data_folder_is_refused(data_folder: Path) -> None:
    store = open_store(data_folder / "ngrunner.db")
    elsewhere = data_folder.parent / "elsewhere"
    elsewhere.mkdir()

    for target in (elsewhere / "copy.db", data_folder / ".." / "elsewhere" / "copy.db",
                   Path("copy.db")):
        with pytest.raises(StoreError, match="data folder|full path"):
            store.backup_to(target)

    assert list(elsewhere.iterdir()) == []
