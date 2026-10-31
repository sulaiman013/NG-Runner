"""
The takeoffs table and its migration (docs/p2/build-spec.md, section 4): one row per filed summary
and rules version, holding NG Runner's own takeoff of it, through ngrunner.store's public functions
only. The upgrade is tested on a database the store's own earlier schema made and filled, never on
a copy of a real one. Job names are made up.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_store import every_row, filled_database
from ngrunner.store import (
    LATEST, MIGRATIONS, TAKEOFF_STATES, Store, StoreError, TakeoffRecord, add_clearance,
    add_card, add_nest, add_program, add_snapshot, add_takeoff, answer_card, ensure_job,
    get_heads_up, get_takeoff, list_takeoffs, log_owner, migrate, open_store, record_heads_up,
)

Z = timezone.utc
T0 = datetime(2026, 10, 31, 17, 0, tzinfo=Z)
T1, T2 = T0 + timedelta(minutes=10), T0 + timedelta(minutes=20)
SID, JOB = "101", "Testerson_101426"
RENDER_A, RENDER_B = "a" * 64, "b" * 64
RULES_1, RULES_2 = "1" * 64, "2" * 64
RESULT = {"buckets": [{"colour": "WH", "thk_in": 0.75, "parts": 8, "sqft": 30.33,
                       "sheet_in": [49.0, 97.0], "sheets_with_waste": 2, "calc_sheets_32": 0.9478,
                       "tape_lf": 26.0}],
          "board_parts": 8, "tape_lf": 26.0, "estimate_total": None, "unplaced": []}


@pytest.fixture
def store(tmp_path: Path) -> Store:
    made = open_store(tmp_path / "ngrunner.db")
    ensure_job(made, SID, JOB, now=T0)
    return made


def made(store: Store, **changes: object) -> TakeoffRecord:
    fields: dict[str, object] = dict(studio_id=SID, render_sha256=RENDER_A, rules_sha256=RULES_1,
                                     state="made", branch="closet", complete=True,
                                     could_not_verify=0, result=RESULT, at=T0)
    fields.update(changes)
    return add_takeoff(store, **fields)  # type: ignore[arg-type]


# ----- round trips


def test_a_takeoff_is_stored_and_read_back_whole(store: Store) -> None:
    record = made(store)

    assert record == TakeoffRecord(SID, RENDER_A, RULES_1, "made", "closet", True, 0, RESULT, T0)
    assert get_takeoff(store, SID, RENDER_A) == record
    assert get_takeoff(store, SID, RENDER_B) is None
    assert get_takeoff(store, "999", RENDER_A) is None


def test_a_takeoff_that_could_not_verify_keeps_its_count(store: Store) -> None:
    record = made(store, complete=False, could_not_verify=3)

    assert (record.complete, record.could_not_verify) == (False, 3)


def test_a_job_that_stays_in_chat_and_a_takeoff_that_failed_hold_no_takeoff(store: Store) -> None:
    garage = made(store, state="in_chat", branch="garage", complete=False, result=None)
    failed = made(store, render_sha256=RENDER_B, state="failed", branch="", complete=False,
                  result=None)

    assert (garage.state, garage.branch, garage.result) == ("in_chat", "garage", None)
    assert (failed.state, failed.result) == ("failed", None)
    assert TAKEOFF_STATES == ("made", "in_chat", "failed")


def test_each_filed_summary_has_its_own_row_and_a_new_rules_version_another(store: Store) -> None:
    first = made(store)
    second = made(store, render_sha256=RENDER_B, at=T1)
    again = made(store, rules_sha256=RULES_2, at=T2, could_not_verify=1, complete=False)

    assert list_takeoffs(store, SID) == [first, second, again]
    assert get_takeoff(store, SID, RENDER_A) == again        # the newest for that summary
    assert get_takeoff(store, SID, RENDER_B) == second


def test_making_the_same_takeoff_again_replaces_it_rather_than_adding_one(store: Store) -> None:
    made(store)
    record = made(store, at=T1, complete=False, could_not_verify=2)

    assert list_takeoffs(store) == [record]


def test_takeoffs_are_listed_by_job(store: Store) -> None:
    ensure_job(store, "202", "Fakeley_101426", now=T0)
    ours = made(store)
    theirs = made(store, studio_id="202", at=T1)

    assert list_takeoffs(store) == [ours, theirs]
    assert list_takeoffs(store, "202") == [theirs]


# ----- what is refused, with nothing stored


@pytest.mark.parametrize("changes", [
    {"state": "partial"},
    {"state": "made", "result": None},
    {"state": "in_chat", "result": RESULT, "complete": False},
    {"state": "failed", "complete": True, "result": None},
    {"could_not_verify": -1},
    {"could_not_verify": True},
    {"complete": "yes"},
    {"render_sha256": "not a hash"},
    {"rules_sha256": "A" * 64},
    {"branch": "attic"},
    {"result": ["not", "a", "mapping"]},
    {"at": datetime(2026, 10, 31, 17, 0)},
], ids=lambda changes: ",".join(changes))
def test_a_takeoff_that_breaks_the_contract_is_refused(store: Store, changes: dict) -> None:
    with pytest.raises(ValueError):
        made(store, **changes)

    assert list_takeoffs(store) == []


def test_a_complete_takeoff_that_says_it_could_not_verify_lines_is_refused(store: Store) -> None:
    with pytest.raises(ValueError):
        made(store, complete=True, could_not_verify=2)


def test_a_takeoff_of_a_job_the_store_does_not_have_is_a_plain_store_error(store: Store) -> None:
    with pytest.raises(StoreError, match="NG Runner's database"):
        made(store, studio_id="999")


def test_the_takeoffs_table_is_strict_like_every_other(tmp_path: Path) -> None:
    open_store(tmp_path / "ngrunner.db")

    with closing(sqlite3.connect(tmp_path / "ngrunner.db")) as raw:
        strict = {row[1]: row[5] for row in raw.execute("PRAGMA table_list")}

    assert strict["takeoffs"] == 1


# ----- the upgrade: a database the guards' version made, with rows in every table


GUARDS_VERSION = 4
FOLDER, MATERIAL, PROGRAM = JOB, "3-4 Classic White", "TESS01.TAP"


def guards_database(path: Path) -> Store:
    """The database as the guards' version left it: built by its own schema (MIGRATIONS[:4]) and
    filled through the store's own functions, the guards' tables included."""
    old = filled_database(path, GUARDS_VERSION)
    with old.transaction() as tx:
        add_program(tx, folder=FOLDER, material=MATERIAL, name=PROGRAM, size=10, mtime=T0,
                    seen_at=T0)
        add_nest(tx, folder=FOLDER, material=MATERIAL, studio_id="101", state="not_checked",
                 basis="programs", updated_at=T0)
        log_owner(tx, at=T0, folder=FOLDER, file="MozaikJob.UNS", owner="JDELA")
        record_heads_up(tx, folder=FOLDER, json_sha256=RENDER_A, sent_at=T0, outbox_id="c" * 32)
        add_snapshot(tx, taken_at=T0, sha256=RENDER_B)
        card = add_card(tx, prefix="TES", kind="sheet_size", text="A program is past its size.",
                        choices=("I added this sheet on purpose", "Re-nest it"), created_at=T0,
                        studio_id="101")
    answer_card(old, card.id, answer="I added this sheet on purpose", answer_words="1",
                answered_by="jim", answered_at=T1, channel="board")
    add_clearance(old, folder=FOLDER, material=MATERIAL, program_sha256=RENDER_A, card_id=card.id,
                  at=T1)
    return old


def test_the_guards_version_is_the_one_before_the_takeoffs() -> None:
    assert LATEST == GUARDS_VERSION + 1 == len(MIGRATIONS)


def test_a_database_from_the_guards_version_upgrades_with_every_row_intact(tmp_path: Path) -> None:
    path = tmp_path / "ngrunner.db"
    old = guards_database(path)
    assert old.schema_version() == GUARDS_VERSION
    before = every_row(path)
    assert all(before[table] for table in ("jobs", "cards", "renders", "programs", "nests",
                                           "heads_ups", "snapshots", "clearances", "owner_log"))

    store = open_store(path)

    after = every_row(path)
    assert store.schema_version() == LATEST
    assert set(after) - set(before) == {"takeoffs"}
    assert {table: after[table] for table in before} == before    # nothing old changed at all
    assert after["takeoffs"] == []
    assert get_heads_up(store, FOLDER).json_sha256 == RENDER_A


def test_an_upgraded_database_takes_a_takeoff_of_a_job_it_already_had(tmp_path: Path) -> None:
    path = tmp_path / "ngrunner.db"
    guards_database(path)

    store = open_store(path)

    record = made(store)
    assert get_takeoff(store, SID, RENDER_A) == record


def test_a_takeoffs_migration_that_fails_leaves_the_guards_database_as_it_was(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ngrunner.db"
    guards_database(path)
    before = every_row(path)
    broken = (*MIGRATIONS[:GUARDS_VERSION], (*MIGRATIONS[GUARDS_VERSION], "THIS IS NOT SQL"))

    with pytest.raises(StoreError):
        migrate(Store(path), broken)

    assert Store(path).schema_version() == GUARDS_VERSION
    assert every_row(path) == before
    assert open_store(path).schema_version() == LATEST            # and it still upgrades


def test_the_guards_version_refuses_a_database_with_takeoffs_and_keeps_its_backup(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "NGRunner"
    (folder / "backup").mkdir(parents=True)
    path, copy = folder / "ngrunner.db", folder / "backup" / "before-the-takeoffs.db"
    guards_database(path)

    Store(path).backup_to(copy)
    assert open_store(path).schema_version() == LATEST

    assert migrate(Store(copy), MIGRATIONS[:GUARDS_VERSION]) == GUARDS_VERSION
    with pytest.raises(StoreError, match="newer version"):
        migrate(Store(path), MIGRATIONS[:GUARDS_VERSION])


def test_a_takeoffs_table_read_before_the_upgrade_is_a_plain_store_error(tmp_path: Path) -> None:
    old = guards_database(tmp_path / "ngrunner.db")

    with pytest.raises(StoreError, match="NG Runner's database failed"):
        list_takeoffs(old)
