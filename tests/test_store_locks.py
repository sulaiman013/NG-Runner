"""
P4's tables and their migration (ngrunner.store.locks): leases, tracker batches, fixes and Jim's lock
clicks. The upgrade is tested on a database the store's own earlier schema made and filled, never on a
copy of a real one. Job names are made up.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_store import every_row, filled_database
from ngrunner.store import LATEST, MIGRATIONS, open_store
from ngrunner.store.locks import (
    BATCH_STATES, LOCK_TABLES, add_batch, add_fix, add_lock_click, get_batch, get_fix, get_lease,
    list_batches, list_fixes, list_leases, list_lock_clicks, mark_fix_undone, put_lease,
    record_survival, rows_dropped_before, update_batch,
)

Z = timezone.utc
T0 = datetime(2026, 10, 31, 17, 0, tzinfo=Z)
T1, T2 = T0 + timedelta(minutes=10), T0 + timedelta(minutes=20)
JOB, OTHER = "Testerson_101426", "Sampleton_101526"
ROWS = [["TAB", "JOB", "FIELD1", "FIELD2", "FIELD3", "FIELD4", "FIELD5", "FIELD6"],
        ["JOBSUMMARY", JOB, "", "🔲 In progress", ""]]


@pytest.fixture
def store(tmp_path: Path):
    return open_store(tmp_path / "ngrunner.db")


# ----- the migration


def test_p4s_migration_comes_after_every_earlier_one_and_adds_only_its_tables(tmp_path: Path) -> None:
    path = tmp_path / "ngrunner.db"
    filled_database(path, LATEST - 1)
    before = every_row(path)

    open_store(path)

    after = every_row(path)
    assert set(after) - set(before) == LOCK_TABLES
    assert {table: after[table] for table in before} == before
    assert "leases" in "\n".join(MIGRATIONS[LATEST - 1])


def test_p4s_tables_are_strict(store) -> None:
    with closing(sqlite3.connect(store.path)) as raw:
        strict = {row[1]: row[5] for row in raw.execute("PRAGMA table_list")}
    assert all(strict[table] == 1 for table in LOCK_TABLES)


# ----- leases


def test_a_lease_is_stored_replaced_and_listed(store) -> None:
    put_lease(store, folder=JOB, holder="NG Runner on PC1", state="active", taken_at=T0,
              expires_at=T0 + timedelta(hours=2), sha256="a" * 64)
    put_lease(store, folder=OTHER, holder="NG Runner on PC1", state="active", taken_at=T0,
              expires_at=T0 + timedelta(hours=2), sha256="b" * 64)
    released = put_lease(store, folder=JOB, holder="NG Runner on PC1", state="released", taken_at=T0,
                         expires_at=T0 + timedelta(hours=2), released_at=T1, sha256="c" * 64)

    assert get_lease(store, JOB) == released and released.released_at == T1
    assert [lease.folder for lease in list_leases(store, state="active")] == [OTHER]
    assert get_lease(store, "Nobody_010101") is None


@pytest.mark.parametrize("bad", [dict(state="held"), dict(holder=""), dict(sha256="nope"),
                                 dict(expires_at=T0 - timedelta(seconds=1))])
def test_a_lease_that_breaks_its_contract_is_refused(store, bad) -> None:
    fields = dict(folder=JOB, holder="NG Runner on PC1", state="active", taken_at=T0,
                  expires_at=T0 + timedelta(hours=2), sha256="a" * 64)
    fields.update(bad)
    with pytest.raises(ValueError):
        put_lease(store, **fields)
    assert get_lease(store, JOB) is None


# ----- tracker batches


def test_a_batch_is_stored_moved_and_found_by_its_rows(store) -> None:
    made = add_batch(store, id="20261031T170000Z-a1b2c3", folder=JOB, kind="lock_design",
                     verb="append", material=None, rows=ROWS, file="tracker_inbox_20261031T170000Z-a1b2c3.csv",
                     dropped_at=T0)
    assert made.state == "dropped" and made.rows == ROWS

    assert update_batch(store, made.id, state="first_ok", first_ok_at=T1, first_taken=T1)
    moved = get_batch(store, made.id)
    assert (moved.state, moved.first_ok_at, moved.first_taken) == ("first_ok", T1, T1)
    assert rows_dropped_before(store, ROWS)
    assert not rows_dropped_before(store, [*ROWS, ["JOBSUMMARY", OTHER, "", "x", ""]])
    assert [b.id for b in list_batches(store, open_only=True)] == [made.id]
    update_batch(store, made.id, state="confirmed", confirmed_at=T2)
    assert list_batches(store, open_only=True) == []


def test_a_batch_state_outside_the_list_is_refused(store) -> None:
    made = add_batch(store, id="b1", folder=JOB, kind="lock_design", verb="append", material=None,
                     rows=ROWS, file="tracker_inbox_b1.csv", dropped_at=T0)
    with pytest.raises(ValueError):
        update_batch(store, made.id, state="maybe")
    assert "confirmed" in BATCH_STATES and get_batch(store, made.id).state == "dropped"


# ----- fixes and clicks


def test_a_fix_is_stored_undone_and_given_its_survival(store) -> None:
    fix = add_fix(store, folder=JOB, room="Room1.des", fixer="notch_face", fingerprint="f" * 64,
                  base_sha256="a" * 64, result_sha256="b" * 64,
                  patch=[{"offset": 10, "old": "46", "new": "54"}],
                  elements=[{"cab": "7", "id": "2", "before": "False", "after": "True"}],
                  programs={"3-4 White": ["c" * 64]}, backups=["C:/backup/Room1.des"], applied_at=T0)
    assert get_fix(store, fix.id) == fix and fix.state == "applied"

    assert record_survival(store, fix.id, code="held", words="It held.", at=T1)
    assert mark_fix_undone(store, fix.id, at=T2)
    assert not mark_fix_undone(store, fix.id, at=T2)          # once only
    again = get_fix(store, fix.id)
    assert (again.state, again.undone_at, again.survival) == ("undone", T2, "held")
    assert [f.id for f in list_fixes(store, folder=JOB)] == [fix.id]


def test_jims_lock_clicks_are_kept_with_lock_without_check(store) -> None:
    add_lock_click(store, folder=JOB, click="lock_design", material=None, without_check=True, at=T0,
                   batch_id="b1")
    add_lock_click(store, folder=JOB, click="finish_lock", material="3-4 White", without_check=False,
                   at=T1, batch_id="b2")

    clicks = list_lock_clicks(store, folder=JOB)
    assert [(c.click, c.material, c.without_check) for c in clicks] == [
        ("lock_design", None, True), ("finish_lock", "3-4 White", False)]
    with pytest.raises(ValueError):
        add_lock_click(store, folder=JOB, click="unlock", material=None, without_check=False, at=T2)
