"""
The notch-face fixer on the real rooms (P4, "Fixers as diffs ... on copies of Jobs A to J's .des"):
Jim's notch_side.py and notch_check.py run from a copy of his toolkit, in the tools' Python, in a Job
Object, on staged copies. Of the 30 real rooms, two hold panels with the notch on the wrong face: Case
A's Room 1 (2 panels) and Case J's Room 2 (5).

Each case's rooms and nests are copied into a shadow Mozaik folder under tmp_path; nothing is written
in .local-fixtures, Mozaik Shared or G:. The plan is a diff, applied to the shadow copy through
commit_write and the P4 interlock, undone to the exact original bytes, and the checks run again.

Marked fixtures: they skip when the toolkit or the tools' Python is absent. Real jobs are named by case
letter only; the shadow folders get made-up names, and no output of Jim's is shown.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from helpers_toolkit import job_folder, stage_tree, tools_python
from ngrunner.clock import FakeClock
from ngrunner.guards.design import room_identity
from ngrunner.interlock import p4_interlock
from ngrunner.lock.fixes import apply_fix, plan_fix, survival, undo_fix
from ngrunner.lock.notch import NOTCH_FACE
from ngrunner.lock.patch import apply_patch
from ngrunner.store import open_store
from ngrunner.store.locks import get_fix
from ngrunner.toolkit import run_tool, take_snapshot
from ngrunner.writes import Roots

pytestmark = pytest.mark.fixtures

NOW = datetime(2026, 10, 31, 13, 0, tzinfo=UTC)
# Measured 2026-10-31: the room, the panels Jim's check flags there, and those flipped by hand in a nest.
CASES = {"A": ("Room1.des", ("9", "11"), ("R1C11",)),
         "J": ("Room2.des", ("1", "3", "5", "13", "15"), ())}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(scope="module")
def copy(tmp_path_factory):
    tools_python()
    tree = stage_tree(tmp_path_factory.mktemp("jims"))
    return take_snapshot(tree, tmp_path_factory.mktemp("data") / "toolkit" / "copy", clock=FakeClock(NOW))


@dataclass
class Shadow:
    roots: Roots
    folder: str
    job: Path
    room: Path
    original: bytes
    real: Path
    nests: dict[str, bytes]
    clock: FakeClock
    store: object
    work: Path

    def lease_held(self, job: str) -> bool:
        return job == self.folder


def shadow_of(case: str, tmp: Path) -> Shadow:
    """The case's rooms, their backups and its nests, copied into a shadow Mozaik folder of a made-up
    name, so the interlock's backup rule (decision 7d) runs on the real pairs too."""
    real = job_folder(case)
    roots = Roots(tmp / "NG Jobs shadow", tmp / "Mozaik shadow", tmp / "control", tmp / "data")
    folder = f"Case{case}_103126"
    job = roots.mozaik_shared / "Jobs" / folder
    for place in (roots.ng_jobs, job, roots.control, roots.app_data):
        place.mkdir(parents=True)
    nests = {}
    for path in sorted(real.iterdir()):
        if path.suffix.casefold() in (".des", ".bak", ".opt"):
            shutil.copyfile(path, job / path.name)
        if path.suffix.casefold() == ".opt":
            nests[path.stem] = path.read_bytes()
    room = job / CASES[case][0]
    return Shadow(roots, folder, job, room, room.read_bytes(), real / CASES[case][0], nests,
                  FakeClock(NOW), open_store(roots.app_data / "n.db"), tmp / "work")


def flags(copy, data: bytes, name: str, work: Path) -> dict[str, str]:
    found = NOTCH_FACE.flags(copy, data, room_name=name, work=work, run=run_tool, python=tools_python())
    assert found is not None
    return found


def settle(folder: Path, clock: FakeClock) -> None:
    stamp = (clock.now() - timedelta(minutes=10)).timestamp()
    for entry in folder.iterdir():
        os.utime(entry, (stamp, stamp))


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_real_wrong_face_notches_are_planned_applied_undone_and_checked_again(copy, tmp_path,
                                                                                    case: str) -> None:
    name, cabs, flipped = CASES[case]
    s = shadow_of(case, tmp_path)
    real_before = sha(s.real.read_bytes())

    # 1. The plan: Jim's fixer on a staged copy, as a diff, with the nests read for hand flips.
    plan = plan_fix(NOTCH_FACE, copy, s.room, folder=s.folder, work=s.work, nests=s.nests,
                    python=tools_python())
    assert plan is not None
    assert sorted({e.cab for e in plan.elements}, key=int) == list(cabs)
    assert len(plan.elements) == 2 * len(cabs)
    assert {(e.before, e.after) for e in plan.elements} == {("False", "True"), ("True", "False")}
    assert plan.hand_flipped == flipped and len(plan.renest) == 1
    fixed = apply_patch(plan.patch, s.original)
    assert len(fixed) == len(s.original) and fixed.count(b"\r\n") == s.original.count(b"\r\n")
    assert s.room.read_bytes() == s.original                      # planning wrote nothing

    # 2. Jim's check: every planned panel flagged before, none after.
    assert {cab for cab, v in flags(copy, s.original, name, s.work).items() if v == "wrong"} == set(cabs)
    assert not set(cabs) & set(flags(copy, fixed, name, s.work))

    # 3. Applied to the shadow copy through commit_write and the P4 interlock. With no closed marker,
    #    as both real folders are, the interlock refuses (decision 7e); with one, it writes.
    real_places = Roots(tmp_path / "real NG Jobs", tmp_path / "real Mozaik Shared", tmp_path / "real control",
                        s.roots.app_data)
    check = p4_interlock(_Ctx(s.store, real_places, s.clock, s), mozaik_running=lambda: False)
    settle(s.job, s.clock)
    check([s.room])
    s.clock.advance(timedelta(seconds=121))
    refused = apply_fix(plan, approved=plan.fingerprint, programs={}, store=s.store, roots=s.roots,
                        interlock=check, clock=s.clock)
    assert not refused.ok and "MozaikJob.UNS" in refused.reason
    (s.job / "MozaikJob.UNS").write_bytes(b"TESTER\r\n")             # a made-up closed marker
    settle(s.job, s.clock)
    applied = apply_fix(plan, approved=plan.fingerprint, programs={}, store=s.store, roots=s.roots,
                        interlock=check, clock=s.clock)
    assert applied.ok, applied.reason
    assert s.room.read_bytes() == fixed
    assert [Path(b).read_bytes() for b in applied.fix.backups] == [s.original]
    assert room_identity(name, fixed) != room_identity(name, s.original)   # its nest pass goes: re-nest

    # 4. The fix's survival, on real bytes: not saved yet, then put back while Mozaik was open.
    record = get_fix(s.store, applied.fix.id)
    assert survival(record, NOTCH_FACE, copy, room_bytes=fixed, room_name=name, work=s.work, sessions=(),
                    store=s.store, at=s.clock.now(), python=tools_python()).code == "waiting"
    undid = survival(record, NOTCH_FACE, copy, room_bytes=s.original, room_name=name, work=s.work,
                     sessions=[(NOW - timedelta(hours=1), None)], store=s.store, at=s.clock.now(),
                     python=tools_python())
    assert undid.code == "mozaik_undid" and undid.urgent

    # 5. Undo: the exact original bytes, and Jim's check flags the same panels again.
    s.clock.advance(timedelta(seconds=121))
    settle(s.job, s.clock)
    undone = undo_fix(record, NOTCH_FACE, programs={}, store=s.store, roots=s.roots, interlock=check,
                      clock=s.clock)
    assert undone.ok, undone.reason
    assert s.room.read_bytes() == s.original and sha(s.room.read_bytes()) == real_before
    assert room_identity(name, s.room.read_bytes()) == room_identity(name, s.original)
    assert {cab for cab, v in flags(copy, s.room.read_bytes(), name, s.work).items() if v == "wrong"} == set(cabs)

    # And the real file was only ever read.
    assert sha(s.real.read_bytes()) == real_before


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_other_real_rooms_of_those_jobs_get_no_plan(copy, tmp_path, case: str) -> None:
    s = shadow_of(case, tmp_path)
    for room in sorted(s.job.glob("Room*.des")):
        if room.name == CASES[case][0]:
            continue
        assert plan_fix(NOTCH_FACE, copy, room, folder=s.folder, work=s.work, nests=s.nests,
                        python=tools_python()) is None, room.name


@dataclass
class _Ctx:
    store: object
    roots: Roots
    clock: FakeClock
    shadow: Shadow
    tracker: None = None

    def lease_held(self, job: str) -> bool:
        return self.shadow.lease_held(job)
