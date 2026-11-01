"""
P5a's plan reader on the real material, read-only: every real room's plan view read and compared with the
room Jim built from it (tools/reader_eval/plans.py), and Jim's frozen jobs rebuilt with their plans as read
(tools/reader_eval/built.py), as his regression compare does. No model is called. Results measured on
2026-11-01 are pinned (docs/decisions.md, "P5a plan reader"), so a change either way fails, never fitted to.

His scripts are read as text, never imported or run, and only the shared ones are named: the per-job build
scripts are named for clients, and the repo is public.
"""
from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import pytest

from ngrunner.build import plan as pr
from tools.reader_eval import built, cases, pairing, plans, prep

pytestmark = pytest.mark.fixtures

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(os.environ.get("NGR_FIXTURES", ROOT / ".local-fixtures"))
SCRIPTS = Path(os.environ.get("NGR_TOOLKIT", FIXTURES / "toolkit" / "20261031")) / "NG_Automation_scripts" / "scripts"


@pytest.fixture(scope="module")
def results() -> list[plans.PlanResult]:
    if not (cases.reader_dir() / "cases" / "index.json").is_file():
        pytest.skip("no collected summaries: run python -m tools.reader_eval.run collect")
    try:
        return plans.score_all()
    except cases.CaseError as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def frozen() -> dict[str, list[built.RoomResult]]:
    """X, Y and Z rebuilt as slice 1 rebuilds them, with each room's plan as read instead of its own chain."""
    if set(cases.collected()) & set(cases.WORKED_CASES) != set(cases.WORKED_CASES):
        pytest.skip("the frozen jobs' summaries are not collected")
    preps = {c: prep.prepare(cases.summary(c)) for c in cases.WORKED_CASES}
    rooms = built.load_rooms(preps)
    pairs = {c: pairing.pair(c, preps[c].crops, rooms[c]).pairs for c in preps}
    found: dict[str, list[built.RoomResult]] = {}
    for r in built.build_frozen(pairs, {c: p.crops for c, p in preps.items()}, {c: cases.summary(c) for c in preps},
                                rooms, read_plans=True):
        found.setdefault(r.case, []).append(r)
    return found


# ----- every real room


@pytest.mark.parametrize("kind, counts", [
    ("frozen", {"right": 6}),
    ("A-J", {"right": 11, "turned": 3, "could not verify": 2, "as cut differs": 2}),
    ("P5", {"right": 67, "could not verify": 7, "as cut differs": 9, "no room": 3}),
    ("answers", {"as cut differs": 2}),
])
def test_the_plan_reader_on_each_set_of_real_rooms(results: list[plans.PlanResult], kind: str,
                                                   counts: dict[str, int]) -> None:
    """2026-11-01. Nothing is wrong; the turned rooms are Jim's, built a half turn from the plan as drawn."""
    assert Counter(r.verdict for r in results if r.set == kind) == Counter(counts)


def test_no_real_room_is_read_wrong(results: list[plans.PlanResult]) -> None:
    assert [f"{r.case}/{r.design + 1}" for r in results if r.verdict == "wrong"] == []


def test_could_not_verify_is_an_angled_wall_an_unreadable_or_crowded_label_or_strips_not_the_tables(
        results: list[plans.PlanResult]) -> None:
    reasons = Counter(plans.reason(r.detail) for r in results if r.verdict == "could not verify")
    assert reasons == Counter({"a contested wall's label unreadable": 3, "a wall drawn at an angle": 3,
                               "labels too near to settle equal walls": 1, "strips not the table's walls": 2})


def test_the_turned_rooms_are_a_half_turn_of_the_whole_room(results: list[plans.PlanResult]) -> None:
    turned = [r for r in results if r.verdict == "turned"]
    assert len(turned) == 3 and all(r.detail.startswith("turned 180 degrees") for r in turned)


def test_rooms_where_jim_built_other_walls_are_still_read_when_their_plan_reads(results: list[plans.PlanResult]) -> None:
    differs = [r for r in results if r.verdict == "as cut differs"]
    assert (len(differs), sum(r.reading.read for r in differs)) == (13, 8)


# ----- Jim's frozen jobs, rebuilt with their plans as read


@pytest.mark.parametrize("job, rooms, panels", [("X", 1, 14), ("Z", 4, 16)])
def test_frozen_x_and_z_still_reproduce_with_the_plan_as_read(frozen: dict[str, list[built.RoomResult]], job: str,
                                                              rooms: int, panels: int) -> None:
    results = frozen[job]
    assert len(results) == rooms
    for r in results:
        assert r.walls == [] and not any(k.endswith("wrong") for k in r.wall_values)
        assert {w.angle.source for w in r.model.walls} == {"plan"} and r.model.joints.settled
        assert r.model.findings == ()
    found = [p for r in results for w in r.panels for p in w.panels]
    assert len(found) == panels and {p.status for p in found} == {"right"}


def test_frozen_zs_two_rooms_out_of_table_order_are_read_in_jims_order(frozen: dict[str, list[built.RoomResult]]) -> None:
    orders = [tuple(w.name for w in r.model.walls) for r in frozen["Z"]]
    assert sum(o != tuple(sorted(o)) for o in orders) == 2


def test_frozen_y_walls_are_right_with_the_plan_as_read(frozen: dict[str, list[built.RoomResult]]) -> None:
    """Y's chain starts at (0, 12.7), shifted as gen_rooms.py 82-85 shifts it; its walls are otherwise right."""
    (r,) = frozen["Y"]
    assert r.walls == [] and r.wall_values["chain shifted"] == 1
    assert not any(k.endswith("wrong") for k in r.wall_values)


# ----- the ties to Jim's files


def _source(name: str) -> str:
    path = SCRIPTS / name
    if not path.is_file():
        pytest.skip("no local toolkit: set NGR_TOOLKIT")
    return path.read_text(encoding="utf-8")


def test_the_angles_are_gen_rooms_pys_y_flip() -> None:
    """gen_rooms.py 77: pixel right 0, left 180, down 270, up 90."""
    source = _source("gen_rooms.py")
    assert "ang=(0 if dx>0 else 180) if abs(dx)>abs(dy) else (270 if dy>0 else 90)" in source
    chain = pr._Chain((0, 1, 2, 3), (0, 1, 0, 1), ("H", "H", "V", "V"), (), False)
    assert pr.angles(chain) == [0.0, 180.0, 270.0, 90.0]


def test_the_walls_are_his_grey_strips_traced_between_free_ends() -> None:
    """gen_rooms.py 24 masks the walls' grey and 56-68 traces the chain from a free end; the reader finds the
    fill colour itself, and the commonest real one is inside his band."""
    source = _source("gen_rooms.py")
    assert "wall=isg&(gray>150)&(gray<193)" in source and "frees=[nd for nd,d in deg.items() if d==1]" in source
    assert 150 < sum((183, 181, 179)) / 3 < 193


# ----- no client in the repo (AGENTS.md rule 5)


def test_the_plan_readers_files_name_no_job() -> None:
    try:
        jobs = list(cases.job_names().values()) + plans.answers_names()
    except cases.CaseError:
        pytest.skip("no case key here")
    words = {w.casefold() for job in jobs for w in (job, job.split("_")[0], job.split(" ")[0]) if len(w) >= 3}
    files = ("ngrunner/build/plan.py", "tools/reader_eval/plans.py", "tests/helpers_plan.py",
             "tests/test_build_plan.py", "tests/test_build_plan_real.py")
    assert [f for f in files if any(w in (ROOT / f).read_text(encoding="utf-8").casefold() for w in words)] == []
