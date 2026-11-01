"""
What the guards keep in the state table, read back by name (guards/kept.py): here, the lock files that
say a job is open in Mozaik, which P3's close prompt and the board read. Nothing here reads a file.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from ngrunner.guards.kept import NEST_FILES, HeldLock, held_locks
from ngrunner.guards.observer import LOCK_FILE, OPTIMIZER_LOCK
from ngrunner.store import open_store, set_state

JOB, OTHER = "Testerson_101426", "Fakeley_101526"
SINCE = "2026-10-31T04:00:00Z"


def test_held_locks_names_each_folders_lock_files_with_owner_and_since(tmp_path) -> None:
    store = open_store(tmp_path / "ngrunner.db")
    set_state(store, NEST_FILES, json.dumps({
        JOB: {"opts": {}, "locks": {}, "held": [[LOCK_FILE, "JDELA", SINCE], [OPTIMIZER_LOCK, None, SINCE]]},
        OTHER: {"opts": {}, "locks": {}, "held": []},
        "Sampleton_101626": {"opts": {}, "locks": {}}}))

    held = held_locks(store)

    assert held == {JOB: (HeldLock(LOCK_FILE, "JDELA", SINCE), HeldLock(OPTIMIZER_LOCK, None, SINCE))}
    lock = held[JOB][0]
    assert (lock.file, lock.owner, lock.since) == (LOCK_FILE, "JDELA", SINCE)
    assert lock.open_in_mozaik and not held[JOB][1].open_in_mozaik


def test_before_the_first_listing_nothing_is_held(tmp_path) -> None:
    assert held_locks(open_store(tmp_path / "ngrunner.db")) == {}


def test_an_entry_not_in_the_kept_shape_is_left_out_rather_than_guessed(tmp_path) -> None:
    store = open_store(tmp_path / "ngrunner.db")
    set_state(store, NEST_FILES, json.dumps({JOB: {"held": [[LOCK_FILE], "x", [LOCK_FILE, "JDELA", SINCE]]}}))
    assert held_locks(store) == {JOB: (HeldLock(LOCK_FILE, "JDELA", SINCE),)}


# ----- Mozaik's sessions at this PC, as the observer saw them: what the survival check reads


def test_a_session_runs_from_the_first_pass_that_saw_mozaik_to_the_last_one_that_did(tmp_path) -> None:
    from datetime import timedelta

    from ngrunner.guards.kept import mozaik_sessions, note_mozaik_session

    store = open_store(tmp_path / "ngrunner.db")
    t0 = datetime(2026, 10, 31, 20, 0, tzinfo=UTC)
    minute = timedelta(minutes=1)
    for n, running in enumerate((False, True, True, None, True, False, False, True)):
        note_mozaik_session(store, running, t0 + n * minute)

    assert mozaik_sessions(store) == [(t0 + minute, t0 + 4 * minute), (t0 + 7 * minute, None)]


def test_cant_tell_neither_opens_nor_closes_a_session(tmp_path) -> None:
    from ngrunner.guards.kept import mozaik_sessions, note_mozaik_session

    store = open_store(tmp_path / "ngrunner.db")
    note_mozaik_session(store, None, datetime(2026, 10, 31, tzinfo=UTC))
    assert mozaik_sessions(store) == []
