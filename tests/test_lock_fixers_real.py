"""
Every fixer on every real room (P4, "Fixers as diffs, backup, inverse-patch Undo"): Jim's notch_side.py,
top_line.py, wm_eb.enforce_room and eb_tops.py, each with its own check, from a copy of his toolkit in the
tools' Python, in a Job Object, on staged copies of the product rooms of Jobs A to J (by case letter), the
40 jobs added for P5 (P01 to P40) and the 6 jobs sent with Jim's answers of 2026-10-31 (Ans1 to Ans6, in
the order their folders sort).

For each room and fixer: what its check flags, the plan or why it was refused, RULES section 6's byte
gate on the fixed room, that the fixed room is the room with only the plan's elements changed (read by the
fixer on either side), that the inverse patch gives the exact original bytes, and the plan written through
commit_write into a shadow copy and undone to the exact original bytes, with the backup equal to the room.
The counts are pinned as measured on 2026-11-01. One room per new fixer goes through the whole life, the
P4 interlock and the survival check included, as tests/test_lock_fixes_real.py does for the notch.

Each job's rooms and nests are copied under tmp_path; nothing is written in .local-fixtures, Mozaik Shared or
G:. Marked fixtures: they skip without the toolkit, the tools' Python or a set of jobs. Real jobs are named
by tag only, and nothing of Jim's output is shown.
"""
from __future__ import annotations

import hashlib
import shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from conftest import FIXTURES
from helpers_toolkit import CASES, job_folder, stage_tree, tools_python
from ngrunner.clock import FakeClock
from ngrunner.interlock import p4_interlock
from ngrunner.lock.fixers import FIXERS, fixer_named
from ngrunner.lock.fixes import FixRefused, Said, apply_fix, plan_fix, survival, undo_fix
from ngrunner.lock.patch import apply_patch, des_problem, inverse
from ngrunner.store import open_store
from ngrunner.store.locks import get_fix
from ngrunner.toolkit import run_tool, take_snapshot
from ngrunner.writes import Roots

pytestmark = pytest.mark.fixtures

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)

# Measured 2026-11-01: the rooms each fixer's check flags, every one planned, with how many values it changes.
PLANNED = {
    "notch_face": {
        "A/Room1": 4, "J/Room2": 10, "P02/Room2": 2, "P03/Room1": 8, "P05/Room1": 2, "P07/Room1": 2, "P07/Room2": 4,
        "P07/Room3": 6, "P10/Room1": 2, "P12/Room1": 4, "P12/Room2": 6, "P13/Room1": 2, "P13/Room2": 2,
        "P13/Room3": 4, "P16/Room1": 2, "P16/Room2": 2, "P16/Room3": 8, "P18/Room1": 8, "P18/Room2": 4,
        "P21/Room1": 8, "P22/Room1": 2, "P23/Room2": 4, "P23/Room3": 2, "P24/Room2": 2, "P25/Room1": 2,
        "P27/Room1": 2, "P29/Room3": 2, "P32/Room1": 8, "P33/Room1": 2, "P33/Room2": 4, "P33/Room3": 2,
        "P36/Room2": 8, "Ans1/Room1": 8, "Ans3/Room1": 2, "Ans4/Room1": 4, "Ans4/Room2": 2},
    "top_line": {
        "J/Room2": 7, "P02/Room1": 2, "P02/Room2": 1, "P02/Room3": 1, "P03/Room1": 6, "P07/Room2": 3, "P07/Room3": 5,
        "P12/Room1": 4, "P12/Room2": 4, "P16/Room1": 5, "P16/Room3": 4, "P18/Room2": 4, "P18/Room3": 1,
        "P21/Room1": 8, "P24/Room2": 3, "Ans1/Room1": 6, "Ans2/Room1": 6, "Ans2/Room2": 2, "Ans3/Room1": 1},
    "wm_banding": {
        "J/Room2": 7, "P02/Room1": 10, "P02/Room2": 3, "P02/Room3": 3, "P03/Room1": 20, "P05/Room1": 8,
        "P10/Room1": 6, "P11/Room1": 15, "P12/Room1": 12, "P12/Room2": 16, "P13/Room1": 6, "P13/Room2": 6,
        "P13/Room3": 8, "P16/Room1": 13, "P16/Room2": 13, "P16/Room3": 12, "P18/Room1": 25, "P18/Room2": 20,
        "P18/Room3": 5, "P21/Room1": 40, "P22/Room1": 6, "P23/Room2": 8, "P23/Room3": 14, "P24/Room1": 3,
        "P24/Room2": 9, "P25/Room1": 6, "P27/Room1": 2, "P29/Room3": 6, "P32/Room1": 22, "P36/Room2": 40,
        "Ans1/Room1": 20, "Ans2/Room1": 24, "Ans2/Room2": 8, "Ans3/Room1": 3, "Ans4/Room1": 9, "Ans4/Room2": 9},
    "countertop_banding": {
        "A/Room1": 1, "H/Room1": 6, "J/Room3": 4, "J/Room4": 4, "P02/Room1": 8, "P02/Room2": 2, "P02/Room3": 4,
        "P03/Room1": 6, "P15/Room1": 2, "P18/Room2": 8, "P22/Room1": 2, "P23/Room3": 8, "P24/Room1": 2,
        "P30/Room6": 4, "P30/Room7": 4, "P33/Room2": 2, "P34/Room1": 1, "P36/Room2": 34, "Ans1/Room1": 6,
        "Ans4/Room2": 4},
}
ROOMS = 114                     # product rooms: 20 of Jobs A to J, 86 of P01 to P40, 8 of Ans1 to Ans6


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ----- the real jobs, by tag


def real_jobs() -> list[tuple[str, Path]]:
    """Every set of real jobs this PC has, by tag. Skips when none of the three is here."""
    jobs = [(case, job_folder(case)) for case in CASES]
    p5 = FIXTURES / "reader" / "p5jobs"
    jobs += [(tag.name, next(tag.iterdir())) for tag in sorted(p5.glob("P[0-9][0-9]")) if tag.is_dir()]
    answers = FIXTURES / "answers20261031"
    folders = sorted(p for p in answers.iterdir() if p.is_dir() and not p.name.startswith("_")) \
        if answers.is_dir() else []
    jobs += [(f"Ans{n}", folder / "mozaik") for n, folder in enumerate(folders, 1)]
    return jobs


@dataclass
class Found:
    """One fixer on one room: what its check flagged, its plan or refusal, and what the plan did."""

    flagged: int | None
    refused: str | None = None
    elements: int = 0
    tags: tuple[str, ...] = ()
    renest: int = 0
    hand_flipped: tuple[str, ...] = ()
    attributes: tuple[str, ...] = ()
    problems: list[str] = field(default_factory=list)


def _flagged(said: Any) -> int | None:
    if said is None:
        return None
    if isinstance(said, Said):
        return sum(said.flagged.values())
    return sum(1 for v in said.values() if v == "wrong")


def _room(tag: str, job: Path, name: str, copy: Any, tmp: Path, python: Path) -> dict[str, Found]:
    """Every fixer on one room of a shadow copy of its job, one after another."""
    roots = Roots(tmp / "NG Jobs shadow", tmp / "Mozaik shadow", tmp / "control", tmp / "data")
    folder = f"Real{tag}_110126"
    shadow = roots.mozaik_shared / "Jobs" / folder
    for place in (roots.ng_jobs, shadow, roots.control, roots.app_data):
        place.mkdir(parents=True)
    nests = {}
    for path in sorted(job.iterdir()):
        if path.suffix.casefold() in (".des", ".opt"):
            shutil.copyfile(path, shadow / path.name)
        if path.suffix.casefold() == ".opt":
            nests[path.stem] = path.read_bytes()
    room, work = shadow / name, tmp / "work"
    base = room.read_bytes()
    store, clock = open_store(roots.app_data / "n.db"), FakeClock(NOW)
    found: dict[str, Found] = {}
    for fixer in FIXERS:
        said = Found(_flagged(fixer.flags(copy, base, room_name=name, work=work, run=run_tool, python=python)))
        found[fixer.name] = said
        try:
            plan = plan_fix(fixer, copy, room, folder=folder, work=work, nests=nests, python=python)
        except FixRefused as exc:
            said.refused = str(exc)
            continue
        if plan is None:
            continue
        said.elements, said.tags, said.renest = len(plan.elements), plan.tags, len(plan.renest)
        said.hand_flipped, said.attributes = plan.hand_flipped, tuple(sorted({e.attribute for e in plan.elements}))
        fixed = apply_patch(plan.patch, base)
        if des_problem(base, fixed):
            said.problems.append("byte gate")
        if not _only_elements(fixer, base, fixed, plan.elements):
            said.problems.append("touched more than its elements")
        if apply_patch(inverse(plan.patch), fixed) != base:
            said.problems.append("inverse")
        applied = apply_fix(plan, approved=plan.fingerprint, programs={}, store=store, roots=roots,
                            interlock=lambda paths: None, clock=clock)
        if not applied.ok or room.read_bytes() != fixed:
            said.problems.append(f"not written: {applied.reason}")
            continue
        if [Path(b).read_bytes() for b in applied.fix.backups] != [base]:
            said.problems.append("backup")
        undone = undo_fix(get_fix(store, applied.fix.id), fixer, programs={}, store=store, roots=roots,
                          interlock=lambda paths: None, clock=clock)
        if not undone.ok or room.read_bytes() != base:
            said.problems.append(f"undo: {undone.reason}")
    return found


def _only_elements(fixer: Any, base: bytes, fixed: bytes, elements: Any) -> bool:
    """fixed is base with only these elements' values changed, each read by the fixer on either side."""
    def masked(data: bytes, value: str) -> bytes | None:
        spans = [(fixer.locate(data, e), getattr(e, value)) for e in elements]
        if any(span is None or data[span[0]:span[1]] != want.encode("utf-8") for span, want in spans):
            return None
        out, at = [], 0
        for (start, end), _ in sorted(spans):
            out += [data[at:start], b"|"]
            at = end
        return b"".join([*out, data[at:]])

    before = masked(base, "before")
    return before is not None and before == masked(fixed, "after")


@pytest.fixture(scope="module")
def copy(tmp_path_factory):
    tools_python()
    tree = stage_tree(tmp_path_factory.mktemp("jims"))
    return take_snapshot(tree, tmp_path_factory.mktemp("data") / "toolkit" / "copy", clock=FakeClock(NOW))


@pytest.fixture(scope="module")
def results(copy, tmp_path_factory) -> dict[str, dict[str, Found]]:
    """Every fixer on every real product room, by tag/room: Jim's scripts run eight rooms at a time."""
    jobs = real_jobs()
    tasks = [(tag, job, path.name) for tag, job in jobs for path in sorted(job.glob("Room*.des"))
             if path.name.casefold() != "room0.des"]
    if len(tasks) != ROOMS:
        pytest.skip(f"not every set of real jobs is on this PC ({len(tasks)} product rooms of {ROOMS})")
    python = tools_python()
    base = tmp_path_factory.mktemp("rooms")

    def one(n: int) -> tuple[str, dict[str, Found]]:
        tag, job, name = tasks[n]
        return f"{tag}/{name.removesuffix('.des')}", _room(tag, job, name, copy, base / str(n), python)

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(one, range(len(tasks))))


# ----- what each fixer did on every real room


@pytest.mark.parametrize("fixer", [f.name for f in FIXERS])
def test_every_room_its_check_flags_is_planned_and_no_other(results, fixer: str) -> None:
    flagged = {room: found[fixer].flagged for room, found in results.items() if found[fixer].flagged}
    planned = {room: found[fixer].elements for room, found in results.items() if found[fixer].elements}

    assert all(found[fixer].flagged is not None for found in results.values())     # every check read
    assert [room for room, found in results.items() if found[fixer].refused] == []
    assert planned == PLANNED[fixer]
    assert set(flagged) == set(planned)


@pytest.mark.parametrize("fixer", [f.name for f in FIXERS])
def test_every_plan_passes_the_byte_gate_touches_only_its_elements_and_undoes_to_the_exact_bytes(
        results, fixer: str) -> None:
    problems = {room: found[fixer].problems for room, found in results.items() if found[fixer].problems}
    assert problems == {}


def test_what_each_fixer_changes_is_the_attribute_it_is_about(results) -> None:
    changed = {name: set() for name in PLANNED}
    for found in results.values():
        for name, said in found.items():
            changed[name] |= set(said.attributes)
    assert changed == {"notch_face": {"Hide"}, "top_line": {"Elev"}, "wm_banding": {"EBand", "EBandLock"},
                       "countertop_banding": {"EBand", "EBandLock"}}


def test_a_countertop_is_named_as_its_nest_names_it_and_its_nest_is_to_be_nested_again(results) -> None:
    tags = Counter(tag for found in results.values() for tag in found["countertop_banding"].tags)
    assert all(tag.endswith("N0") for tag in tags)
    renest = [room for room, found in results.items() if found["countertop_banding"].elements
              and not found["countertop_banding"].renest]
    assert len(renest) == 1                     # the one room whose job has no nest holding a countertop


def test_parts_flipped_by_hand_are_named_on_the_plan(results) -> None:
    flipped = {name: sum(1 for found in results.values() if found[name].hand_flipped) for name in PLANNED}
    assert flipped == {"notch_face": 30, "top_line": 15, "wm_banding": 28, "countertop_banding": 0}


# ----- one room per new fixer through its whole life


LIFE = {"top_line": ("J", "Room2.des"), "wm_banding": ("J", "Room2.des"), "countertop_banding": ("J", "Room3.des")}


@dataclass
class _Ctx:
    store: object
    roots: Roots
    clock: FakeClock
    folder: str
    tracker: None = None

    def lease_held(self, job: str) -> bool:
        return job == self.folder


@pytest.mark.parametrize("name", sorted(LIFE))
def test_a_real_fix_is_planned_written_through_the_interlock_judged_and_undone(copy, tmp_path, name: str) -> None:
    fixer = fixer_named(name)
    case, room_name = LIFE[name]
    real = job_folder(case) / room_name
    real_before = sha(real.read_bytes())
    roots = Roots(tmp_path / "NG Jobs shadow", tmp_path / "Mozaik shadow", tmp_path / "control", tmp_path / "data")
    folder = f"Case{case}_110126"
    job = roots.mozaik_shared / "Jobs" / folder
    for place in (roots.ng_jobs, job, roots.control, roots.app_data):
        place.mkdir(parents=True)
    nests = {}
    for path in sorted(job_folder(case).iterdir()):
        if path.suffix.casefold() in (".des", ".opt"):
            shutil.copyfile(path, job / path.name)
        if path.suffix.casefold() == ".opt":
            nests[path.stem] = path.read_bytes()
    room, work, python = job / room_name, tmp_path / "work", tools_python()
    original = room.read_bytes()
    store, clock = open_store(roots.app_data / "n.db"), FakeClock(NOW)

    plan = plan_fix(fixer, copy, room, folder=folder, work=work, nests=nests, python=python)
    assert plan is not None and plan.renest and room.read_bytes() == original
    fixed = apply_patch(plan.patch, original)

    real_places = Roots(tmp_path / "real NG Jobs", tmp_path / "real Mozaik Shared", tmp_path / "real control",
                        roots.app_data)
    check = p4_interlock(_Ctx(store, real_places, clock, folder), mozaik_running=lambda: False)
    (job / "MozaikJob.UNS").write_bytes(b"TESTER\r\n")                  # a made-up closed marker
    _settle(job, clock)
    check([room])
    clock.advance(timedelta(seconds=121))
    _settle(job, clock)
    applied = apply_fix(plan, approved=plan.fingerprint, programs={}, store=store, roots=roots, interlock=check,
                        clock=clock)
    assert applied.ok, applied.reason
    assert room.read_bytes() == fixed

    record = get_fix(store, applied.fix.id)
    judged = {code: survival(record, fixer, copy, room_bytes=data, room_name=room_name, work=work, sessions=sessions,
                             store=store, at=clock.now(), python=python, keep=False).code
              for code, data, sessions in (("waiting", fixed, ()),
                                           ("mozaik_undid", original, [(NOW - timedelta(hours=1), None)]))}
    assert judged == {"waiting": "waiting", "mozaik_undid": "mozaik_undid"}

    clock.advance(timedelta(seconds=121))
    _settle(job, clock)
    undone = undo_fix(record, fixer, programs={}, store=store, roots=roots, interlock=check, clock=clock)
    assert undone.ok, undone.reason
    assert room.read_bytes() == original
    assert _flagged(fixer.flags(copy, original, room_name=room_name, work=work, run=run_tool, python=python))
    assert sha(real.read_bytes()) == real_before                         # the real file was only read


def _settle(folder: Path, clock: FakeClock) -> None:
    import os

    stamp = (clock.now() - timedelta(minutes=10)).timestamp()
    for entry in folder.iterdir():
        os.utime(entry, (stamp, stamp))
