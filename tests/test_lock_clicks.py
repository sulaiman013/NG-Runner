"""
Jim's three lock clicks (decision 7c, questionnaire 32; design 5, "Tracker integrity"): Lock design adds
the calculated rows as in progress, Finish lock sets one material's nest numbers once its nest passes,
and Lock does both. Each is check-before-lock first, its rows built by tracker_inbox, and one drop.

Nothing reaches the real tracker inbox: the drop folder is a test folder under tmp_path. Two tests show
the real drop path refused: by the chokepoint's P4 rules (a folder named like the tracker's own), and by
the P4 interlock (the folder NG Runner is configured with). Job names and materials are made up.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from helpers_lock import CONVERSION, job_check
from ngrunner.clock import FakeClock
from ngrunner.guards.tracker import read_tracker
from ngrunner.interlock import p4_interlock
from ngrunner.lock.clicks import finish_lock, lock, lock_design
from ngrunner.lock.drop import IN_PROGRESS, LOCKED
from ngrunner.store import open_store
from ngrunner.store.locks import list_batches, list_lock_clicks
from ngrunner.tracker_inbox import HEADER
from ngrunner.writes import Roots
from test_lock_drop import BACKS, JOB, TAPE, WHITE, archive, snapshot
from test_tracker_inbox import TAKEOFF, Takeoff, names

T0 = datetime(2026, 10, 31, 13, 0, tzinfo=UTC)
NEST_WHITE, NEST_BACKS = "3-4 Egger Classic White", "1-4 Egger Classic White"
SHA = {"Room0.des": "a" * 64, "Room1.des": "b" * 64}
IDENTITY = {"Room0.des": "c" * 64, "Room1.des": "d" * 64}


@dataclass(frozen=True)
class Nest:
    material: str
    state: str = "nest_checked"
    room_hashes: dict = field(default_factory=lambda: dict(IDENTITY))


def Check(**kw):
    """P3's check as job_checks() gives it; by default it passed, on these very rooms."""
    return job_check(**{"rooms": SHA, "identities": IDENTITY, **kw})


def make_env(tmp_path: Path, tracker: Path) -> dict:
    roots = Roots(tmp_path / "NG Jobs", tmp_path / "Mozaik copy", tmp_path / "control", tmp_path / "data",
                  tracker=tracker)
    for folder in (roots.ng_jobs, roots.mozaik_shared, roots.control, roots.app_data, tracker):
        folder.mkdir(parents=True, exist_ok=True)
    return dict(store=open_store(roots.app_data / "ngrunner.db"), roots=roots,
                interlock=lambda paths: None, clock=FakeClock(T0), tmp=tmp_path)


@pytest.fixture
def env(tmp_path: Path) -> dict:
    return make_env(tmp_path, tmp_path / "tracker test")


def snap(env, name: str = "s", **kw):
    return read_tracker(snapshot(env["tmp"] / name, taken=env["clock"].now() - timedelta(minutes=5), **kw))


def facts(**kw) -> dict:
    out = dict(nests=(Nest(NEST_WHITE), Nest(NEST_BACKS)), check=Check(), room_sha256=SHA,
               room_identity=IDENTITY, conversion_sha256=CONVERSION, without_check=False)
    out.update(kw)
    return out


def act(env) -> dict:
    return {k: env[k] for k in ("store", "roots", "interlock", "clock")}


def design(env, **kw):
    return lock_design(JOB, takeoff=TAKEOFF, names=names, snap=snap(env), **facts(**kw), **act(env))


# ----- Lock design


def test_lock_design_drops_the_calculated_rows_as_in_progress(env) -> None:
    result = design(env)

    assert result.ok, result.words
    batch = result.drop.batch
    assert batch.kind == "lock_design" and batch.verb == "append"
    assert batch.rows[-1] == ["JOBSUMMARY", JOB, "", IN_PROGRESS, ""]
    assert [c.click for c in list_lock_clicks(env["store"], folder=JOB)] == ["lock_design"]
    assert (env["roots"].tracker / batch.file).is_file()


def test_lock_design_is_refused_without_a_current_check_and_says_what_is_missing(env) -> None:
    result = design(env, check=None)

    assert not result.ok and result.drop is None
    assert [m.code for m in result.gate.missing] == ["no_check"]
    assert "No check" in result.words
    assert list(env["roots"].tracker.iterdir()) == [] and list_lock_clicks(env["store"]) == []


def test_lock_without_check_goes_ahead_and_is_kept_as_such(env) -> None:
    result = design(env, check=None, without_check=True)

    assert result.ok, result.words
    click, = list_lock_clicks(env["store"], folder=JOB)
    assert click.without_check and click.batch_id == result.drop.batch.id


def test_a_takeoff_with_a_line_it_could_not_place_never_reaches_the_tracker(env) -> None:
    result = lock_design(JOB, takeoff=Takeoff(TAKEOFF.buckets, unplaced=("p4 line 3",)), names=names,
                         snap=snap(env), **facts(), **act(env))

    assert not result.ok and "could not" in result.words
    assert list(env["roots"].tracker.iterdir()) == []


# ----- Finish lock


def finish(env, material: str, nest: str, sheets: float, **kw):
    rows = dict(sheets=[(JOB, WHITE, "8.2", "", "40.00"), (JOB, BACKS, "0.22", "", "65.00")],
                tape=[(JOB, TAPE, "376.2", "", "0.27")], jobs=[(JOB, IN_PROGRESS)])
    rows.update(kw.pop("rows", {}))
    return finish_lock(JOB, nest, tracker_name=material, sheets=sheets, takeoff=TAKEOFF, names=names,
                       snap=snap(env, f"f{len(list_batches(env['store']))}", **rows), **facts(**kw),
                       **act(env))


def test_finish_lock_is_refused_until_its_nest_reads_nest_checked(env) -> None:
    result = finish(env, WHITE, NEST_WHITE, 9, nests=(Nest(NEST_WHITE, state="do_not_cut"),))

    assert not result.ok and [m.code for m in result.gate.missing] == ["nest_not_checked"]
    assert "DO NOT CUT" in result.words
    assert list(env["roots"].tracker.iterdir()) == []


def test_finish_lock_sets_one_material_and_the_last_one_locks_the_job(env) -> None:
    first = finish(env, WHITE, NEST_WHITE, 9)
    assert first.ok, first.words
    assert first.drop.batch.rows == [list(HEADER),
                                     ["SHEETGOODS_SET", JOB, WHITE, "24.3778", "262.40", "8.20", "9"]]
    archive(env, first.drop.batch)
    first_batch = list_batches(env["store"])[0]
    from ngrunner.store.locks import update_batch
    update_batch(env["store"], first_batch.id, state="confirmed", confirmed_at=T0)

    last = finish(env, BACKS, NEST_BACKS, 1)

    assert last.ok, last.words
    assert last.drop.batch.rows == [
        list(HEADER), ["SHEETGOODS_SET", JOB, BACKS, "0.6652", "7.16", "0.22", "1"],
        ["EDGEBAND_SET", JOB, TAPE, "376.2", "376.2", WHITE], ["JOBSUMMARY_SET", JOB, LOCKED, ""]]


# ----- Lock


def test_lock_on_a_job_with_no_rows_adds_them_all_nested_and_locked(env) -> None:
    result = lock(JOB, sheets={WHITE: 9, BACKS: 1}, takeoff=TAKEOFF, names=names, snap=snap(env),
                  **facts(), **act(env))

    assert result.ok, result.words
    assert result.drop.batch.rows == [
        list(HEADER), ["SHEETGOODS", JOB, "", WHITE, "24.3778", "262.40", "8.20", "9"],
        ["SHEETGOODS", JOB, "", BACKS, "0.6652", "7.16", "0.22", "1"],
        ["EDGEBAND", JOB, "", TAPE, "376.2", "376.2", WHITE], ["JOBSUMMARY", JOB, "", LOCKED, ""]]


def test_lock_needs_every_nest_checked_and_a_number_for_every_material(env) -> None:
    refused = lock(JOB, sheets={WHITE: 9, BACKS: 1}, takeoff=TAKEOFF, names=names, snap=snap(env),
                   **facts(nests=(Nest(NEST_WHITE), Nest(NEST_BACKS, state="not_checked"))), **act(env))
    short = lock(JOB, sheets={WHITE: 9}, takeoff=TAKEOFF, names=names, snap=snap(env), **facts(),
                 **act(env))

    assert not refused.ok and [m.code for m in refused.gate.missing] == ["nest_not_checked"]
    assert not short.ok and "nest number" in short.words
    assert list(env["roots"].tracker.iterdir()) == []


# ----- the real drop path waits for go-live


def test_the_real_tracker_folder_is_refused_by_p4s_rules(tmp_path: Path) -> None:
    real = tmp_path / "CNC operations" / "NG Conversion program Claude"
    env = make_env(tmp_path, real)

    result = design(env)

    assert not result.ok and "tracker's own folder" in result.words
    assert list(real.iterdir()) == [] and list_batches(env["store"]) == []


def test_the_configured_tracker_folder_is_refused_by_the_p4_interlock(tmp_path: Path) -> None:
    env = make_env(tmp_path, tmp_path / "tracker test")

    @dataclass
    class Ctx:
        store: object
        roots: Roots
        clock: FakeClock
        tracker: Path

        def lease_held(self, job: str) -> bool:
            return True

    configured = Ctx(env["store"], env["roots"], env["clock"], tracker=env["roots"].tracker)
    env["interlock"] = p4_interlock(configured, mozaik_running=lambda: False)

    result = design(env)

    assert not result.ok and "until P4 goes live" in result.words
    assert list(env["roots"].tracker.iterdir()) == []
