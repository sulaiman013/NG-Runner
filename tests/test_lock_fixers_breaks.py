"""
The fixers' safety rules, each broken on purpose (P4; RULES section 6; decisions 7a to 7e): a fix that
touches a byte besides its elements' values, a fix its own check still flags, an Undo that doesn't give
back the exact bytes, and a write outside NG Runner's own shadow copies. For each rule: the guard refuses
the violation for every new fixer, and with the guard taken away (monkeypatched out) the property this
suite checks every plan against catches it, so a change that loses the guard can't go unseen.

Synthetic rooms and fakes of Jim's scripts (tests/helpers_fixers.py). Names are made up.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import helpers_fixers as h
from ngrunner.clock import FakeClock
from ngrunner.lock import fixes
from ngrunner.lock.banding import COUNTERTOP_BANDING, WmBanding
from ngrunner.lock.fixes import FixRefused, apply_fix, plan_fix, undo_fix
from ngrunner.lock.patch import Edit, Patch, apply_patch, des_problem
from ngrunner.lock.topline import TOP_LINE
from ngrunner.store import open_store
from ngrunner.store.locks import get_fix
from ngrunner.toolkit import Snapshot
from ngrunner.writes import Roots
from test_lock_fixers import only_its_elements_changed

JOB = "Testerson_101426"
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Case:
    """One new fixer, its fake, and the room before and after its fix."""

    name: str
    fixer: Any
    run: Any
    wrong: bytes
    fixed: bytes

    def with_output(self, out: bytes, *, still_flags: bool = False) -> Case:
        """The same fixer, whose script now writes out (and whose check, still_flags, names the fixed room)."""
        if self.name == "top_line":
            check = {out: h.top_check(2, h.TOP_HIT_AT_928)} if still_flags else None
            return replace(self, run=h.FakeTopLine(fixed=out, check=check))
        if self.name == "wm_banding":
            jim = h.FakeWm(fixed=out, still_flags=still_flags)
            return replace(self, fixer=WmBanding(call=jim.call), run=jim.run)
        lines = [h.TOPS_LINE] if still_flags else None
        jim = h.FakeEbTops(fixes={self.wrong: out})
        if lines:
            original = jim.__call__

            def call(snapshot, script, args, **kw):
                jim.lines = lines if Path(args[-1]).read_bytes() == out else None
                return original(snapshot, script, args, **kw)

            return replace(self, run=call)
        return replace(self, run=jim)


def cases() -> list[Case]:
    wm = h.FakeWm()
    return [Case("top_line", TOP_LINE, h.FakeTopLine(), h.TOP_WRONG, h.TOP_FIXED),
            Case("wm_banding", WmBanding(call=wm.call), wm.run, h.WM_WRONG, h.WM_FIXED),
            Case("countertop_banding", COUNTERTOP_BANDING, h.FakeEbTops(), h.TOPS_WRONG, h.TOPS_FIXED)]


IDS = [c.name for c in cases()]


@pytest.fixture
def env(tmp_path: Path):
    roots = Roots(tmp_path / "NG Jobs", tmp_path / "Mozaik copy", tmp_path / "control", tmp_path / "data")
    job = roots.mozaik_shared / "Jobs" / JOB
    for folder in (roots.ng_jobs, job, roots.control, roots.app_data):
        folder.mkdir(parents=True)
    toolkit = tmp_path / "toolkit"
    (toolkit / "scripts").mkdir(parents=True)
    (toolkit / "scripts" / "crosscheck.py").write_text(h.CROSSCHECK, encoding="utf-8")
    return dict(roots=roots, room=job / "Room1.des", store=open_store(roots.app_data / "n.db"), clock=FakeClock(T0),
                work=tmp_path / "work", snapshot=Snapshot(toolkit, "0" * 64, T0, 1))


def plan(env, case: Case):
    env["room"].write_bytes(case.wrong)
    return plan_fix(case.fixer, env["snapshot"], env["room"], folder=JOB, work=env["work"], run=case.run, nests={})


def extra_byte(data: bytes) -> bytes:
    """The same room with one byte more changed, outside anything a fixer names: its annotations' count."""
    return data.replace(b'<Annotations Count="1" />', b'<Annotations Count="9" />')


# ----- a fix that touches a byte besides its elements


@pytest.mark.parametrize("case", cases(), ids=IDS)
def test_a_fix_touching_an_extra_byte_is_refused(env, case: Case) -> None:
    with pytest.raises(FixRefused, match="more than"):
        plan(env, case.with_output(extra_byte(case.fixed)))


@pytest.mark.parametrize("case", cases(), ids=IDS)
def test_without_that_guard_the_extra_byte_is_caught_by_the_property_every_plan_is_held_to(env, case: Case,
                                                                                         monkeypatch) -> None:
    sneaky = extra_byte(case.fixed)
    assert only_its_elements_changed(case.fixer, case.wrong, plan(env, case))          # a true plan holds it
    monkeypatch.setattr(fixes, "_substituted", lambda fixer, data, elements: sneaky)     # the guard, gone

    made = plan(env, case.with_output(sneaky))

    assert made is not None and not only_its_elements_changed(case.fixer, case.wrong, made)


@pytest.mark.parametrize("case", cases(), ids=IDS)
def test_without_the_byte_gate_a_broken_room_is_caught_by_the_gate_held_to_every_plan(env, case: Case,
                                                                                     monkeypatch) -> None:
    broken = case.fixed.replace(b"\r\n</Room>", b"\n</Room>")                          # a bare LF
    with pytest.raises(FixRefused, match="CRLF"):
        plan(env, case.with_output(broken))

    monkeypatch.setattr(fixes, "des_problem", lambda base, result: None)               # the gate, gone
    monkeypatch.setattr(fixes, "_substituted", lambda fixer, data, elements: broken)
    made = plan(env, case.with_output(broken))
    assert des_problem(case.wrong, apply_patch(made.patch, case.wrong)) is not None


# ----- a fix its own check still flags


@pytest.mark.parametrize("case", cases(), ids=IDS)
def test_a_fix_its_own_check_still_flags_is_refused(env, case: Case) -> None:
    with pytest.raises(FixRefused, match="own check"):
        plan(env, case.with_output(case.fixed, still_flags=True))


@pytest.mark.parametrize("case", cases(), ids=IDS)
def test_without_that_guard_the_check_on_the_fixed_room_still_names_it(env, case: Case, monkeypatch) -> None:
    flagging = case.with_output(case.fixed, still_flags=True)
    monkeypatch.setattr(type(case.fixer), "cleared", lambda self, before, after, elements: True)   # the guard, gone

    made = plan(env, flagging)

    said = flagging.fixer.flags(env["snapshot"], apply_patch(made.patch, case.wrong), room_name="Room1.des",
                                work=env["work"], run=flagging.run)
    assert flagging.fixer.names(said, made.elements)


# ----- an Undo that doesn't give back the exact bytes


def written(env, case: Case):
    made = plan(env, case)
    result = apply_fix(made, approved=made.fingerprint, programs={}, store=env["store"], roots=env["roots"],
                       interlock=lambda paths: None, clock=env["clock"])
    assert result.ok, result.reason
    return get_fix(env["store"], result.fix.id)


_inverse = fixes.inverse


def wrong_inverse(patch: Patch) -> Patch:
    """An inverse that puts back something else besides: the version line's first byte."""
    good = _inverse(patch)
    return Patch(good.base_sha256, good.result_sha256, (Edit(0, b"1", b"2"), *good.edits))


@pytest.mark.parametrize("case", cases(), ids=IDS)
def test_an_undo_that_wouldnt_give_back_the_exact_bytes_is_refused_and_writes_nothing(env, case: Case,
                                                                                    monkeypatch) -> None:
    record = written(env, case)
    monkeypatch.setattr(fixes, "inverse", wrong_inverse)

    result = undo_fix(record, case.fixer, programs={}, store=env["store"], roots=env["roots"],
                      interlock=lambda paths: None, clock=env["clock"])

    assert not result.ok and env["room"].read_bytes() == case.fixed


@pytest.mark.parametrize("case", cases(), ids=IDS)
def test_without_the_patchs_own_hash_check_a_wrong_undo_is_caught_by_the_exact_bytes_held_to_every_undo(
        env, case: Case, monkeypatch) -> None:
    record = written(env, case)

    def unchecked(patch: Patch, data: bytes) -> bytes:                                 # the guard, gone
        out, at = [], 0
        for edit in sorted(patch.edits, key=lambda e: e.offset):
            out += [data[at:edit.offset], edit.new]
            at = edit.offset + len(edit.old)
        return b"".join([*out, data[at:]])

    monkeypatch.setattr(fixes, "inverse", wrong_inverse)
    monkeypatch.setattr(fixes, "apply_patch", unchecked)
    result = undo_fix(record, case.fixer, programs={}, store=env["store"], roots=env["roots"],
                      interlock=lambda paths: None, clock=env["clock"])

    assert result.ok and env["room"].read_bytes() != case.wrong


@pytest.mark.parametrize("case", cases(), ids=IDS)
def test_undo_after_a_save_that_changed_a_fixed_value_is_refused(env, case: Case) -> None:
    record = written(env, case)
    element = fixes.Element(**record.elements[0])
    start, end = case.fixer.locate(case.fixed, element)
    changed = case.fixed[:start] + b"7" + case.fixed[end:]                             # Jim changed it in Mozaik
    env["room"].write_bytes(changed)

    result = undo_fix(record, case.fixer, programs={}, store=env["store"], roots=env["roots"],
                      interlock=lambda paths: None, clock=env["clock"])

    assert not result.ok and "no longer" in result.reason and env["room"].read_bytes() == changed


# ----- a write outside NG Runner's own shadow copies


@pytest.mark.parametrize("case", cases(), ids=IDS)
def test_a_fix_aimed_at_a_folder_named_mozaik_shared_is_refused_and_writes_nothing(env, case: Case,
                                                                                 tmp_path: Path) -> None:
    made = plan(env, case)
    real = Roots(tmp_path / "NG Jobs", tmp_path / "Mozaik Shared", tmp_path / "control", tmp_path / "data")
    room = real.mozaik_shared / "Jobs" / JOB / "Room1.des"
    room.parent.mkdir(parents=True)
    room.write_bytes(case.wrong)

    result = apply_fix(made, approved=made.fingerprint, programs={}, store=env["store"], roots=real,
                       interlock=lambda paths: None, clock=env["clock"])

    assert not result.ok and room.read_bytes() == case.wrong


@pytest.mark.parametrize("case", cases(), ids=IDS)
def test_a_fix_into_the_folder_ng_runner_is_configured_with_is_refused_by_the_p4_interlock(env, case: Case,
                                                                                          tmp_path: Path) -> None:
    from ngrunner.interlock import p4_interlock

    made = plan(env, case)

    @dataclass
    class Real:                                       # the folders NG Runner is configured with: these very ones
        store: Any
        roots: Roots
        clock: FakeClock
        tracker: None = None

        def lease_held(self, job: str) -> bool:
            return True

    check = p4_interlock(Real(env["store"], env["roots"], env["clock"]), mozaik_running=lambda: False)
    result = apply_fix(made, approved=made.fingerprint, programs={}, store=env["store"], roots=env["roots"],
                       interlock=check, clock=env["clock"])

    assert not result.ok and env["room"].read_bytes() == case.wrong
