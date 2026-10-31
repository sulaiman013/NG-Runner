"""
Jim's real regression on a staged copy of his real toolkit, and a real adoption through to the nest
checks (ruling R1; P2 exit item "3 frozen jobs reproduced"; design 6, Updates).

His tree is laid out from the fixtures in a folder of the test's own (helpers_toolkit.py), copied as NG
Runner copies it, and his scripts run in the tools' Python, isolated, in a Job Object: never in
.local-fixtures, never in place and never on G:. Marked fixtures: they skip when the toolkit or the
tools' Python is absent. Real jobs are named by case letter only.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from helpers_toolkit import job_folder, ng_job, stage_tree, tools_python
from ngrunner import toolkit
from ngrunner.clock import FakeClock
from ngrunner.guards.design import room_identity
from ngrunner.guards.facts import BR_CAM_NEST, DESIGN_MATCHES, FAILED, PASSED, SHEET_SIZE, CheckedFiles
from ngrunner.guards.nest_state import MaterialEvidence, NestState, decide
from ngrunner.guards.nestfiles import ToCheck
from ngrunner.guards.opt import read_opt
from ngrunner.guards.tap import read_tap
from ngrunner.store import open_store
from ngrunner.toolkit import (
    ADOPTED, BASELINE, REFUSED, hash_of_tree, record_adoption, run_regression, take_snapshot, try_adoption,
)

pytestmark = pytest.mark.fixtures

NOW = datetime(2026, 10, 31, 9, 30, tzinfo=timezone.utc)
MATERIAL = "3-4 Egger Classic White"


@pytest.fixture(scope="module")
def tree(tmp_path_factory) -> Path:
    tools_python()
    return stage_tree(tmp_path_factory.mktemp("jims"))


def regress(snapshot):
    return run_regression(snapshot, python=tools_python())


# ----- the 3 frozen jobs, reproduced


def test_jims_regression_reproduces_his_3_frozen_jobs_on_a_staged_copy(tree: Path, tmp_path: Path) -> None:
    copy = take_snapshot(tree, tmp_path / "data" / "toolkit" / "copy", clock=FakeClock(NOW))
    jobs = json.loads((copy.path / BASELINE).read_text(encoding="utf-8"))

    said = regress(copy)

    assert said.clean and said.sha256 == copy.sha256, said.words
    assert len(jobs) == 3 and all(set(job) == {"gates", "checkers", "takeoff", "schedule"} for job in jobs.values())
    assert hash_of_tree(copy.path) == copy.sha256                      # it wrote nothing into the copy
    assert not list(copy.path.rglob("__pycache__"))                    # not even bytecode


def test_a_frozen_job_that_moved_is_found_and_refused(tree: Path, tmp_path: Path) -> None:
    moved = stage_tree(tmp_path / "moved")
    baseline = json.loads((moved / BASELINE).read_text(encoding="utf-8"))
    first = sorted(baseline)[0]
    baseline[first]["checkers"]["op_schema_check"] = "this is not what it says"
    (moved / BASELINE).write_text(json.dumps(baseline, indent=1, ensure_ascii=False), encoding="utf-8")

    tried = try_adoption(moved, tmp_path / "data" / "toolkit", adopted=None, clock=FakeClock(NOW), regress=regress)

    assert tried.outcome == REFUSED and "moved 1 thing" in tried.words
    assert first not in tried.words                                   # his items name jobs: never repeated


def test_a_changed_checker_moves_his_frozen_jobs_and_is_refused(tree: Path, tmp_path: Path) -> None:
    changed = stage_tree(tmp_path / "changed")
    # A line regression's checker layer captures. (outset_check's PASS line is printed outside its
    # check(), so regression never sees it: a change there moves nothing, measured 2026-10-31.)
    script = changed / "scripts" / "drill_depth_check.py"
    text = script.read_text(encoding="utf-8")
    assert text.count("every bore depth matches the adjacent section heights") == 1
    script.write_text(text.replace("every bore depth matches", "every bore depth fits"), encoding="utf-8")

    tried = try_adoption(changed, tmp_path / "data" / "toolkit", adopted=None, clock=FakeClock(NOW), regress=regress)

    assert tried.outcome == REFUSED and tried.words


# ----- adopted for real, and the nest checks running from it


@pytest.fixture(scope="module")
def adopted(tree: Path, tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    store = open_store(data / "ngrunner.db")
    tried = try_adoption(tree, data / "toolkit", adopted=None, clock=FakeClock(NOW), regress=regress)
    record_adoption(store, tried)
    return store, data, tried


def to_check(case: str, material: str, stage: Path) -> tuple[ToCheck, MaterialEvidence]:
    """A real nest and its real programs (read from NG Jobs, never written) as the guards would hand
    them to the checks, and the evidence decide would get from them."""
    job = job_folder(case)
    opt = job / f"{material}.opt"
    programs = ng_job(case) / material
    if not programs.is_dir():
        pytest.skip(f"Case {case}'s programs aren't in NG Jobs")
    taps = tuple(read_tap(p.name, p.read_bytes()) for p in sorted(programs.iterdir()) if p.suffix.casefold() == ".tap")
    rooms = tuple(sorted((p.name, room_identity(p.name, p.read_bytes())) for p in job.glob("Room*.des")))
    facts = read_opt(opt.read_bytes())
    files = CheckedFiles(facts.sha256, tuple((t.name, t.sha256) for t in taps), rooms)
    evidence = MaterialEvidence(material=material, taps=taps, seen_at_install=False, opt=facts, room_hashes=rooms)
    return ToCheck(job.name, material, opt, job, programs, files), evidence


def test_a_real_adoption_is_on_record_and_is_the_copy_the_guards_use(adopted) -> None:
    store, data, tried = adopted

    assert tried.outcome == ADOPTED, tried.words
    copy = toolkit.adopted_snapshot(store, data / "toolkit")
    assert copy is not None and copy.sha256 == tried.sha256 == hash_of_tree(copy.path)


@pytest.mark.parametrize("case, sheet, cams, state", [
    ("F", PASSED, PASSED, NestState.NEST_CHECKED),       # a clean nest that is its saved design
    ("D", PASSED, FAILED, NestState.DO_NOT_CUT),         # the real positive for doubled holes
])
def test_the_nest_checks_run_from_the_adopted_copy_and_can_reach_nest_checked(adopted, case: str, sheet: str,
                                                                               cams: str, state: NestState,
                                                                               monkeypatch) -> None:
    from ngrunner.guards import watch

    store, data, _ = adopted
    monkeypatch.setenv("NGR_TOOLS_PYTHON", str(tools_python()))
    job, evidence = to_check(case, MATERIAL, data)
    ctx = type("Ctx", (), {"store": store, "paths": type("Paths", (), {"toolkit": data / "toolkit",
                                                                       "staging": data / "staging"})()})()
    before = {p: p.stat().st_mtime_ns for p in (job.opt_path, *job.program_dir.iterdir())}

    tools, design = watch.nest_checks(ctx)(job)

    assert [(t.tool, t.code) for t in tools] == [(SHEET_SIZE, sheet), (BR_CAM_NEST, cams)]
    assert all(t.checked == job.files for t in tools)
    assert design.code == DESIGN_MATCHES and design.checked == job.files    # guards/design.py's, for real
    verdict = decide(replace(evidence, tools=tuple(tools), design=design))
    assert verdict.state == state
    assert decide(replace(evidence, tools=tuple(tools))).state != NestState.NEST_CHECKED
    assert {p: p.stat().st_mtime_ns for p in before} == before        # read, never written
