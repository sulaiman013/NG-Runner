"""
The store's database itself (build spec section 4): opening and migrating it, refusing one it
can't use, transactions and backups. Every database lives under tmp_path; tests go through
ngrunner.store's public interface (the table round trips are in test_store_tables.py).
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ngrunner.clock import to_iso
from ngrunner.match import FabRow, Opportunity
from ngrunner.store import (
    LATEST, MIGRATIONS, Store, StoreError, add_alias, add_card, add_clearance, add_fab_row,
    add_nest, add_outbox, add_pending, add_program, add_render, add_snapshot, allocate_card_id,
    answer_card, cleared_programs, ensure_job, get_card, get_heads_up, get_job, get_nest,
    get_program, get_render, get_state, list_cards, list_dev, list_fab_rows, list_jobs,
    list_nests, list_owner_log, list_programs, list_snapshots, log_event, log_owner, migrate,
    next_seq, open_dev, open_store, record_heads_up, render_fingerprint, replace_opportunities,
    set_state, start_run,
)

T0 = datetime(2026, 10, 30, 13, 30, tzinfo=timezone.utc)
T1, T2 = T0 + timedelta(minutes=10), T0 + timedelta(minutes=20)
ISO_T0, ISO_T1, ISO_T2 = (to_iso(t) for t in (T0, T1, T2))


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


# ----- version 2, on a database version 1 made


def version_1_database(path: Path, *rows: tuple[str, tuple[object, ...]]) -> None:
    """A database as version 1 left it, holding these rows (raw SQL: no store function writes
    the old shape any more)."""
    assert migrate(Store(path), MIGRATIONS[:1]) == 1
    with closing(sqlite3.connect(path)) as raw, raw:
        for statement, values in rows:
            raw.execute(statement, values)


def test_the_first_migration_has_not_changed_since_it_shipped() -> None:
    shipped = "f325fd0fa849d8ec5fbfc3de1d0fc1b196d6490ee46aa509240fb8722bb5bb5f"

    assert hashlib.sha256("\n".join(MIGRATIONS[0]).encode("utf-8")).hexdigest() == shipped


def test_cards_from_version_1_have_no_job_behind_any_choice(db_path: Path) -> None:
    version_1_database(db_path, (
        "INSERT INTO cards (id, kind, text, choices, created_at, state)"
        " VALUES (?, 'which_job', 'Which job is this row?', ?, ?, 'open')",
        ("TES-1", '["Testerson_101426","Testerson_102026","None of these"]', ISO_T0),
    ))

    store = open_store(db_path)

    assert store.schema_version() == LATEST
    card = get_card(store, "TES-1")
    assert card.choices == ("Testerson_101426", "Testerson_102026", "None of these")
    assert card.choice_ids == (None, None, None)  # parallel, and never a guess at a job


def test_fab_rows_from_version_1_keep_when_their_text_can_last_have_changed(db_path: Path) -> None:
    insert = ("INSERT INTO fab_rows (first_seen, text, designer, original_text, state, updated_at)"
              " VALUES (?, ?, 'AB', ?, ?, ?)")
    version_1_database(
        db_path,
        (insert, (ISO_T0, "Testerson", "Testerson", "asked", ISO_T1)),
        (insert, (ISO_T0, "Fakeley", "Fakley", "new", ISO_T2)),
    )

    as_typed, edited = list_fab_rows(open_store(db_path))

    assert (as_typed.first_seen, as_typed.text_changed_at) == (T0, T0)  # its first text still
    # Edited some time up to its last update, and version 1 kept no time: the latest it can be.
    assert (edited.first_seen, edited.original_text, edited.text_changed_at) == (T0, "Fakley", T2)
    assert edited.to_match() == FabRow("Fakeley", "AB", T2)


def test_dev_items_from_version_1_name_no_job_and_are_not_urgent(db_path: Path) -> None:
    version_1_database(db_path, (
        "INSERT INTO dev (at, code, text, state) VALUES (?, 'no_receipt', 'No receipt.', 'open')",
        (ISO_T0,),
    ))

    store = open_store(db_path)

    item = list_dev(store)[0]
    assert (item.code, item.studio_id, item.urgent) == ("no_receipt", None, False)
    assert open_dev(store, at=T1, code="no_receipt", text="No receipt.") == item  # still standing


def test_renders_from_version_1_have_no_pypdf_version_so_their_fingerprints_are_read_again(
    db_path: Path,
) -> None:
    version_1_database(
        db_path,
        ("INSERT INTO jobs (studio_id, name, mode, created_at, updated_at)"
         " VALUES ('101', 'Testerson_101426', 'auto', ?, ?)", (ISO_T0, ISO_T0)),
        ("INSERT INTO renders (id, studio_id, fetched_at, fingerprint, check_ok, cache_path)"
         " VALUES (1, '101', ?, ?, 1, ?)", (ISO_T0, "1" * 64, str(db_path.parent / "r.pdf"))),
    )

    store = open_store(db_path)

    assert get_render(store, 1).pypdf is None
    assert render_fingerprint(store, 1, pypdf="6.21.4", read=lambda path: "2" * 64) == "2" * 64


# ----- version 4, the guards' tables, on a database an earlier version made


GUARD_TABLES = {"programs", "nests", "clearances", "owner_log", "heads_ups", "snapshots"}
SHIPPED = {
    1: "f325fd0fa849d8ec5fbfc3de1d0fc1b196d6490ee46aa509240fb8722bb5bb5f",
    2: "05b016f42d28a460810997b6bce62d62322762831b670cfd43684aa06a988167",
    3: "ea9d620982fd9c3b7b961d1cae07bed9f8b1b0866f8adfcfc0b334f0a8ff0e78",
    4: "8e00178507be9ec2e723c5a6b1b8617390023606021199ed6c80a5b4339d6ea3",   # the guards'
}
TAKEOFF_TABLES = {"takeoffs"}   # version 5 (tests/test_store_takeoffs.py)
CHECK_TABLES = {"check_runs", "settles", "check_prompts"}   # version 6 (tests/test_store_checks.py)
LOCK_TABLES = {"leases", "tracker_batches", "fixes", "lock_clicks"}   # version 7 (tests/test_store_locks.py)
FOLDER, MATERIAL, PROGRAM = "Testerson_101426", "3-4 Classic White", "TESS01.TAP"
SHA = "a" * 64


@pytest.mark.parametrize("version", sorted(SHIPPED))
def test_a_migration_that_shipped_has_not_changed(version: int) -> None:
    # The real shadow run's database went through these, so they can only ever be appended to.
    text = "\n".join(MIGRATIONS[version - 1])

    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == SHIPPED[version]


def filled_database(path: Path, version: int) -> Store:
    """A database as that earlier version left it, built by its own schema and holding rows in
    every table group, written by the store's own functions (version 2 on: they write columns
    version 1 didn't have)."""
    assert migrate(Store(path), MIGRATIONS[:version]) == version
    store = Store(path)
    with store.transaction() as tx:
        ensure_job(tx, "101", FOLDER, now=T0, designer="AB")
        add_alias(tx, "101", "Testerson_Fakeley_101426", seen_at=T1)
        add_fab_row(tx, first_seen=T0, text="Testerson", designer="AB")
        replace_opportunities(tx, [Opportunity(FOLDER, "101", "AB", "Ready to Order")],
                              synced_at=T0, fetched_at=T1)
        add_pending(tx, "fetch_1", due_at=T1, studio_id="101")
        start_run(tx, stage="file", trigger="fab_row", started_at=T0, app_version="0.0.1")
        add_card(tx, prefix="TES", kind="closet_or_garage", text="Its designs disagree.",
                 choices=("Closet", "Garage"), created_at=T0, studio_id="101")
        log_event(tx, at=T0, kind="filed", text="Filed.")
        open_dev(tx, at=T0, code="no_receipt", text="No receipt.")
        add_outbox(tx, id="b" * 32, seq=next_seq(tx, "outbox", start=1), channel="email",
                   recipient="jim", subject=f"Filed as {FOLDER}", lines=("Filed.",), urgent=False,
                   created_at=T0)
        set_state(tx, "installed_at", "kept")
        add_render(tx, studio_id="101", fetched_at=T0, check_ok=True, sha256=SHA, page_count=3)
    return store


def every_row(path: Path) -> dict[str, list[tuple[object, ...]]]:
    """Every row of every table, read with no store code in between."""
    with closing(sqlite3.connect(path)) as raw:
        tables = [row[0] for row in raw.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        return {table: raw.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in tables}


def test_a_database_from_before_the_guards_upgrades_with_every_row_intact(db_path: Path) -> None:
    filled_database(db_path, 3)
    before = every_row(db_path)
    assert before["jobs"] and before["cards"] and before["outbox"] and before["renders"]

    store = open_store(db_path)

    after = every_row(db_path)
    assert store.schema_version() == LATEST
    assert set(after) - set(before) == GUARD_TABLES | TAKEOFF_TABLES | CHECK_TABLES | LOCK_TABLES
    assert {table: after[table] for table in before} == before     # nothing old changed at all
    assert all(after[table] == [] for table in GUARD_TABLES | TAKEOFF_TABLES | CHECK_TABLES | LOCK_TABLES)   # new ones empty


@pytest.mark.parametrize("version", [2, 3])
def test_every_older_version_comes_up_to_the_latest_keeping_its_rows(
    db_path: Path, version: int,
) -> None:
    filled_database(db_path, version)
    jobs_before = every_row(db_path)["jobs"]

    store = open_store(db_path)

    assert store.schema_version() == LATEST
    assert every_row(db_path)["jobs"] == jobs_before
    assert get_job(store, "101").aliases == ("Testerson_Fakeley_101426",)
    assert GUARD_TABLES <= set(every_row(db_path))


def test_the_oldest_version_comes_all_the_way_up_too(db_path: Path) -> None:
    version_1_database(db_path, (
        "INSERT INTO jobs (studio_id, name, mode, created_at, updated_at)"
        " VALUES ('101', 'Testerson_101426', 'auto', ?, ?)", (ISO_T0, ISO_T0)))

    store = open_store(db_path)

    assert store.schema_version() == LATEST
    assert get_job(store, "101").name == "Testerson_101426"
    assert GUARD_TABLES <= set(every_row(db_path))


def test_an_upgraded_database_goes_on_working_for_what_it_already_did(db_path: Path) -> None:
    filled_database(db_path, 3)

    store = open_store(db_path)

    card, = list_cards(store)
    assert answer_card(store, card.id, answer="Closet", answer_words="1", answered_by="jim",
                       answered_at=T1, channel="board")
    assert allocate_card_id(store, "TES") == "TES-2"   # its numbers carried on, none reused
    assert get_state(store, "installed_at") == "kept"
    assert get_render(store, 1).sha256 == SHA
    set_state(store, "paused", "no")
    assert get_state(store, "paused") == "no"


def test_a_database_not_upgraded_yet_goes_on_working_for_what_it_already_did(db_path: Path) -> None:
    store = filled_database(db_path, 3)

    assert store.schema_version() == 3
    assert [job.name for job in list_jobs(store)] == [FOLDER]
    set_state(store, "paused", "yes")
    assert get_state(store, "paused") == "yes"
    assert store.schema_version() == 3                 # nothing but open_store migrates


def test_the_guards_tables_are_ready_to_use_once_upgraded(db_path: Path) -> None:
    filled_database(db_path, 3)

    store = open_store(db_path)

    add_program(store, folder=FOLDER, material=MATERIAL, name=PROGRAM, size=10, mtime=T0,
                seen_at=T0)
    assert get_program(store, FOLDER, MATERIAL, PROGRAM).state == "new"
    add_nest(store, folder=FOLDER, material=MATERIAL, studio_id="101", state="not_checked",
             basis="programs", updated_at=T0)
    assert get_nest(store, FOLDER, MATERIAL).studio_id == "101"   # the job from the old rows
    log_owner(store, at=T0, folder=FOLDER, file="MozaikJob.UNS", owner="JDELA")
    assert list_owner_log(store)[0].owner == "JDELA"
    record_heads_up(store, folder=FOLDER, json_sha256=SHA, sent_at=T0, outbox_id="c" * 32)
    assert get_heads_up(store, FOLDER).json_sha256 == SHA
    add_snapshot(store, taken_at=T0, sha256=SHA)
    assert not list_snapshots(store)[0].adopted


def test_a_clearance_can_name_a_card_from_before_the_upgrade(db_path: Path) -> None:
    store = filled_database(db_path, 3)
    made = add_card(store, prefix="TES", kind="sheet_size", text="A program is past its size.",
                    choices=("I added this sheet on purpose", "Re-nest it"), created_at=T0,
                    studio_id="101")
    answer_card(store, made.id, answer="I added this sheet on purpose", answer_words="1",
                answered_by="jim", answered_at=T1, channel="board")

    upgraded = open_store(db_path)

    add_clearance(upgraded, folder=FOLDER, material=MATERIAL, program_sha256=SHA, card_id=made.id,
                  at=T2)
    assert cleared_programs(upgraded, FOLDER, MATERIAL) == {SHA}


def test_the_guards_tables_are_strict_like_every_other(db_path: Path) -> None:
    open_store(db_path)

    with closing(sqlite3.connect(db_path)) as raw:
        strict = {row[1]: row[5] for row in raw.execute("PRAGMA table_list")}

    assert all(strict[table] == 1 for table in GUARD_TABLES)


def test_a_table_of_the_guards_before_the_upgrade_is_a_plain_store_error(db_path: Path) -> None:
    store = filled_database(db_path, 3)

    for read in (lambda: list_programs(store), lambda: list_nests(store)):
        with pytest.raises(StoreError, match="NG Runner's database failed"):
            read()


def test_a_guards_migration_that_fails_leaves_the_old_database_as_it_was(db_path: Path) -> None:
    filled_database(db_path, 3)
    before = every_row(db_path)
    broken = (*MIGRATIONS[:3], (*MIGRATIONS[3], "THIS IS NOT SQL"))

    with pytest.raises(StoreError):
        migrate(Store(db_path), broken)

    assert Store(db_path).schema_version() == 3
    assert every_row(db_path) == before                # none of the new tables stayed behind
    assert open_store(db_path).schema_version() == LATEST  # and it still upgrades


def test_opening_an_upgraded_database_again_changes_nothing(db_path: Path) -> None:
    filled_database(db_path, 3)
    open_store(db_path)
    add_snapshot(open_store(db_path), taken_at=T0, sha256=SHA)
    before = every_row(db_path)

    open_store(db_path)

    assert every_row(db_path) == before


def test_a_copy_from_before_the_guards_stays_usable_by_the_version_that_made_it(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "NGRunner"
    (folder / "backup").mkdir(parents=True)
    path, copy = folder / "ngrunner.db", folder / "backup" / "before-the-guards.db"
    filled_database(path, 3)

    Store(path).backup_to(copy)
    assert open_store(path).schema_version() == LATEST

    assert Store(copy).schema_version() == 3
    assert migrate(Store(copy), MIGRATIONS[:3]) == 3     # the older NG Runner goes on from the copy
    assert [job.name for job in list_jobs(Store(copy))] == [FOLDER]
    with pytest.raises(StoreError, match="newer version"):
        migrate(Store(path), MIGRATIONS[:3])             # and it refuses the upgraded file


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


def test_a_copy_taken_before_an_upgrade_stays_usable_by_the_older_version(
    data_folder: Path,
) -> None:
    path = data_folder / "ngrunner.db"
    assert migrate(Store(path), MIGRATIONS[:1]) == 1
    copy = data_folder / "backup" / "before-the-upgrade.db"

    Store(path).backup_to(copy)
    assert open_store(path).schema_version() == LATEST

    assert Store(copy).schema_version() == 1
    assert migrate(Store(copy), MIGRATIONS[:1]) == 1  # an older NG Runner can go on from the copy
    with pytest.raises(StoreError, match="newer version"):
        migrate(Store(path), MIGRATIONS[:1])          # and it refuses the upgraded file


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
