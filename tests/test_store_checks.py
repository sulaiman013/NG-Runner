"""
The check tables and their migration (P3: checks on "sections done" and on Check now, settles, and the
close prompt), through ngrunner.store's public functions only. The upgrade is tested on a database the
store's own earlier schema made and filled, never on a copy of a real one. Job names are made up.

What the store holds for a check run is what binds it: the very bytes of the rooms it ran on (sha256),
their design identities, the IC Conversion's hash and the toolkit copy's. A settle holds the finding's
fingerprint and the identities and conversion it was made on, so a later change can ask again.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_store import every_row, filled_database
from ngrunner.store import (
    CHECK_KINDS, CHECK_STATES, CHECK_TRIGGERS, LATEST, MIGRATIONS, SETTLE_HOWS, CheckRunRecord,
    Store, StoreError, add_prompt, add_settle, answer_prompt, ask_check, back_to_asked, ensure_job,
    finish_check, get_check_run, list_check_runs, list_prompts, list_settles, mark_stale,
    mark_told, migrate, not_run_check, open_store, start_check,
)

Z = timezone.utc
T0 = datetime(2026, 10, 31, 17, 0, tzinfo=Z)
T1, T2, T3 = (T0 + timedelta(minutes=n) for n in (10, 20, 30))
SID, JOB = "101", "Testerson_101426"
ROOMS = {"Room0.des": "a" * 64, "Room1.des": "b" * 64}
IDENTITIES = {"Room0.des": "c" * 64, "Room1.des": "d" * 64}
STATS = {"Room0.des": [120, 1000], "Room1.des": [340, 2000], "conversion": [55, 3000]}
RESULT = {"verdicts": [{"checker": "notch_check", "code": "failed", "advisory": False, "why": "fault"}],
          "items": [{"n": 1, "kind": "fix", "what": "notch_face", "tags": ["R1C3"],
                     "words": "R1C3: the notch is on the wrong face", "chat": None,
                     "fingerprint": "e" * 64, "settles": True}],
          "left_out": ["hinge_check"]}


@pytest.fixture
def store(tmp_path: Path) -> Store:
    made = open_store(tmp_path / "ngrunner.db")
    ensure_job(made, SID, JOB, now=T0)
    return made


def asked(store: Store, **changes: object) -> CheckRunRecord:
    fields: dict[str, object] = dict(studio_id=SID, folder=JOB, kind="check", trigger="check_now", at=T0)
    fields.update(changes)
    return ask_check(store, **fields)  # type: ignore[arg-type]


def finished(store: Store, run_id: int, **changes: object) -> bool:
    fields: dict[str, object] = dict(at=T1, code="failed", words="Jim's crosscheck found something to fix.",
                                     snapshot_sha256="1" * 64, conversion_sha256="2" * 64, rooms=ROOMS,
                                     identities=IDENTITIES, stats=STATS, result=RESULT,
                                     features=("bridge",), trial=True)
    fields.update(changes)
    return finish_check(store, run_id, **fields)  # type: ignore[arg-type]


# ----- a run, asked to done


def test_a_check_is_asked_started_and_finished_with_what_it_ran_on(store: Store) -> None:
    run = asked(store)
    assert (run.state, run.kind, run.trigger, run.asked_at) == ("asked", "check", "check_now", T0)
    assert run.code is None and run.rooms == {} and run.result is None

    assert start_check(store, run.id, at=T0)
    assert finished(store, run.id)

    done = get_check_run(store, run.id)
    assert (done.state, done.code, done.finished_at) == ("done", "failed", T1)
    assert done.started_at == T0
    assert done.rooms == ROOMS and done.identities == IDENTITIES and done.stats == STATS
    assert done.snapshot_sha256 == "1" * 64 and done.conversion_sha256 == "2" * 64
    assert done.result == RESULT and done.features == ("bridge",) and done.trial is True
    assert done.stale_at is None and done.told_at is None


def test_a_job_has_one_open_check_at_a_time_and_asking_again_returns_it(store: Store) -> None:
    first = asked(store)
    again = asked(store, trigger="sections_done", at=T1)

    assert again == first
    assert len(list_check_runs(store, SID)) == 1
    start_check(store, first.id, at=T1)
    assert asked(store, at=T2) == get_check_run(store, first.id)     # running is open too

    finished(store, first.id)
    later = asked(store, at=T3)
    assert later.id != first.id and later.state == "asked"


def test_a_run_moves_only_forward_and_each_step_says_whether_it_was_made(store: Store) -> None:
    run = asked(store)

    assert not finished(store, run.id)                  # never started
    assert start_check(store, run.id, at=T0)
    assert not start_check(store, run.id, at=T0)        # already running
    assert back_to_asked(store, run.id)                 # waiting for Mozaik to close the job
    assert get_check_run(store, run.id).state == "asked"
    assert start_check(store, run.id, at=T1)
    assert finished(store, run.id)
    assert not finished(store, run.id)
    assert not not_run_check(store, run.id, at=T2, words="Too late.")
    assert get_check_run(store, run.id).state == "done"


def test_a_run_that_could_not_run_says_why_and_holds_no_result(store: Store) -> None:
    run = asked(store)

    assert not_run_check(store, run.id, at=T1, words="No copy of Jim's toolkit is adopted yet.")

    record = get_check_run(store, run.id)
    assert (record.state, record.words, record.code, record.result) == (
        "not_run", "No copy of Jim's toolkit is adopted yet.", None, None)


def test_a_finished_run_goes_stale_once_and_is_told_once(store: Store) -> None:
    run = asked(store)
    start_check(store, run.id, at=T0)
    finished(store, run.id)

    assert mark_stale(store, run.id, at=T2)
    assert not mark_stale(store, run.id, at=T3)         # its first time stands
    assert get_check_run(store, run.id).stale_at == T2
    assert mark_told(store, run.id, at=T2)
    assert not mark_told(store, run.id, at=T3)
    assert get_check_run(store, run.id).told_at == T2


def test_only_a_finished_run_can_go_stale(store: Store) -> None:
    run = asked(store)

    assert not mark_stale(store, run.id, at=T1)
    assert get_check_run(store, run.id).stale_at is None


def test_runs_are_listed_oldest_first_by_job_and_by_state(store: Store) -> None:
    ensure_job(store, "202", "Fakeley_101426", now=T0)
    ours = asked(store)
    theirs = asked(store, studio_id="202", folder="Fakeley_101426", kind="audit", trigger="audit", at=T1)
    start_check(store, ours.id, at=T1)

    assert [r.id for r in list_check_runs(store)] == [ours.id, theirs.id]
    assert [r.id for r in list_check_runs(store, "202")] == [theirs.id]
    assert [r.id for r in list_check_runs(store, states=("asked",))] == [theirs.id]
    assert CHECK_KINDS == ("check", "audit")
    assert CHECK_TRIGGERS == ("check_now", "sections_done", "audit")
    assert CHECK_STATES == ("asked", "running", "done", "not_run")


@pytest.mark.parametrize("changes", [
    {"code": "clean"},
    {"code": None},
    {"rooms": {}},
    {"identities": {"Room1.des": "d" * 64}},             # not the same rooms as ran
    {"rooms": {"Room1.des": "not a hash"}, "identities": {"Room1.des": "d" * 64}},
    {"snapshot_sha256": "A" * 64},
    {"conversion_sha256": None},
    {"result": ["not", "a", "mapping"]},
    {"features": ("attic",)},
    {"trial": "yes"},
    {"at": datetime(2026, 10, 31, 17, 0)},
], ids=lambda changes: ",".join(changes))
def test_a_finished_run_that_breaks_the_contract_is_refused(store: Store, changes: dict) -> None:
    run = asked(store)
    start_check(store, run.id, at=T0)

    with pytest.raises(ValueError):
        finished(store, run.id, **changes)

    assert get_check_run(store, run.id).state == "running"


@pytest.mark.parametrize("changes", [
    {"kind": "inspection"},
    {"trigger": "whenever"},
    {"folder": " "},
    {"at": datetime(2026, 10, 31, 17, 0)},
], ids=lambda changes: ",".join(changes))
def test_a_check_asked_badly_is_refused(store: Store, changes: dict) -> None:
    with pytest.raises(ValueError):
        asked(store, **changes)

    assert list_check_runs(store) == []


def test_a_check_of_a_job_the_store_does_not_have_is_a_plain_store_error(store: Store) -> None:
    with pytest.raises(StoreError, match="NG Runner's database"):
        asked(store, studio_id="999")


# ----- settles: bound to a finding and to what it was made on


def test_a_settle_keeps_jims_words_the_finding_and_what_it_was_made_on(store: Store) -> None:
    run = asked(store)
    start_check(store, run.id, at=T0)
    finished(store, run.id)

    settle = add_settle(store, studio_id=SID, run_id=run.id, fingerprint="e" * 64, identities=IDENTITIES,
                        conversion_sha256="2" * 64, how="told_chat", words="I already told chat",
                        channel="board", at=T2)

    assert list_settles(store, SID) == [settle]
    assert (settle.fingerprint, settle.identities, settle.conversion_sha256) == (
        "e" * 64, IDENTITIES, "2" * 64)
    assert (settle.how, settle.words, settle.channel, settle.at) == (
        "told_chat", "I already told chat", "board", T2)
    assert SETTLE_HOWS == ("not_a_problem", "told_chat")


@pytest.mark.parametrize("changes", [
    {"how": "forever"},
    {"fingerprint": "not a hash"},
    {"identities": {}},
    {"conversion_sha256": "x"},
    {"words": ""},
    {"run_id": 999},
], ids=lambda changes: ",".join(changes))
def test_a_settle_that_breaks_the_contract_is_refused(store: Store, changes: dict) -> None:
    run = asked(store)
    start_check(store, run.id, at=T0)
    finished(store, run.id)
    fields: dict[str, object] = dict(studio_id=SID, run_id=run.id, fingerprint="e" * 64,
                                     identities=IDENTITIES, conversion_sha256="2" * 64,
                                     how="not_a_problem", words="Not a problem", channel="board", at=T2)
    fields.update(changes)

    with pytest.raises((ValueError, StoreError)):
        add_settle(store, **fields)  # type: ignore[arg-type]

    assert list_settles(store) == []


def test_a_settle_for_all_jobs_keeps_the_kind_of_finding_it_stands_for(store: Store) -> None:
    """P4's second round: a settle's scope. One for all jobs names its checker and what it found, which is how
    it is known on any job; its words are the condition Jim gave."""
    from ngrunner.store import SETTLE_SCOPES

    run = asked(store)
    start_check(store, run.id, at=T0)
    finished(store, run.id)
    common = dict(studio_id=SID, run_id=run.id, fingerprint="e" * 64, identities=IDENTITIES,
                  conversion_sha256="2" * 64, how="not_a_problem", channel="board", at=T2)

    mine = add_settle(store, words="Not a problem for this job", **common)
    every = add_settle(store, words="When the outset is on a finished end", scope="all_jobs",
                       checker="outset_check", what="outset", **common)

    assert (mine.scope, mine.checker, mine.what) == ("job", None, None)
    assert (every.scope, every.checker, every.what) == ("all_jobs", "outset_check", "outset")
    assert SETTLE_SCOPES == ("job", "all_jobs") and list_settles(store) == [mine, every]
    for bad in (dict(scope="everything"), dict(scope="all_jobs"), dict(scope="all_jobs", checker="outset_check"),
                dict(scope="job", checker="outset_check", what="outset")):
        with pytest.raises(ValueError):
            add_settle(store, words="When it is fine", **{**common, **bad})


# ----- the close prompt


def test_a_close_prompt_is_answered_once_with_jims_words(store: Store) -> None:
    prompt = add_prompt(store, studio_id=SID, folder=JOB, closed_at=T0, first=True)

    assert list_prompts(store, SID, open_only=True) == [prompt]
    assert answer_prompt(store, prompt.id, answer="Yes, check it", answer_words="1", answered_at=T1,
                         channel="toast")
    assert not answer_prompt(store, prompt.id, answer="Not yet", answer_words="2", answered_at=T2,
                             channel="board")

    record, = list_prompts(store, SID)
    assert (record.answer, record.answer_words, record.answered_at, record.channel) == (
        "Yes, check it", "1", T1, "toast")
    assert record.first is True and list_prompts(store, SID, open_only=True) == []


# ----- the tables, and the upgrade from the takeoffs' version


def test_the_check_tables_are_strict_like_every_other(tmp_path: Path) -> None:
    open_store(tmp_path / "ngrunner.db")

    with closing(sqlite3.connect(tmp_path / "ngrunner.db")) as raw:
        strict = {row[1]: row[5] for row in raw.execute("PRAGMA table_list")}

    assert strict["check_runs"] == strict["settles"] == strict["check_prompts"] == 1


TAKEOFFS_VERSION = 5


def test_the_takeoffs_version_is_the_one_before_the_checks() -> None:
    assert MIGRATIONS[TAKEOFFS_VERSION][0].lstrip().startswith("CREATE TABLE IF NOT EXISTS check_runs")
    assert LATEST == len(MIGRATIONS) >= TAKEOFFS_VERSION + 1      # P4's version 7 comes after


def test_a_database_from_the_takeoffs_version_upgrades_with_every_row_intact(tmp_path: Path) -> None:
    path = tmp_path / "ngrunner.db"
    old = filled_database(path, TAKEOFFS_VERSION)
    assert old.schema_version() == TAKEOFFS_VERSION
    before = every_row(path)

    store = open_store(path)

    after = every_row(path)
    assert store.schema_version() == LATEST
    p4_tables = {"leases", "tracker_batches", "fixes", "lock_clicks"}       # version 7, after these
    assert set(after) - set(before) - p4_tables == {"check_runs", "settles", "check_prompts"}
    assert {table: after[table] for table in before} == before    # nothing old changed at all
    run = ask_check(store, studio_id="101", folder=JOB, kind="audit", trigger="audit", at=T1)
    assert run.state == "asked"


def test_a_database_from_the_takeoffs_version_migrates_in_one_step(tmp_path: Path) -> None:
    path = tmp_path / "ngrunner.db"
    assert migrate(Store(path), MIGRATIONS[:TAKEOFFS_VERSION]) == TAKEOFFS_VERSION

    assert migrate(Store(path)) == LATEST
