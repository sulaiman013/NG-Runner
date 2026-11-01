"""
P5a's commit (ngrunner/build/commit.py and writes.py's "P5 shadow" rules): a written room goes through
commit_write with an interlock, into NG Runner's own copy of Mozaik Shared only, and only when every gate
on it passed (AGENTS.md rules 1, 2 and 8).

The real folders are fake ones under tmp_path, standing for the folders NG Runner is configured with; the
shadow copy is another. Mozaik's process list is a fake. The last section breaks each safety rule on
purpose, its first lock taken out, and shows the next one catching it. The job and its room are made up.
"""
from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

import helpers_build as hb
import ngrunner.toolkit as toolkit
from ngrunner.build import commit as cm
from ngrunner.build import gates as gt
from ngrunner.build import writer as wr
from ngrunner.build.room import unverified
from ngrunner.interlock import PAUSED, PAUSED_REASON
from ngrunner.store import Store, open_store, set_state
from ngrunner.writes import FileWrite, Roots, commit_write

JOB = "Testerson_101426"
MEASURED = {"2": gt.Measured((gt.DrawnVertical(0.75, 82.5, 2.0), gt.DrawnVertical(30.4, 82.0, 37.5),
                              gt.DrawnVertical(60.05, 82.5, 2.0)))}


@dataclass
class _Ctx:
    store: Store
    roots: Roots


class _Mozaik:
    def __init__(self) -> None:
        self.running: bool | None = False

    def __call__(self) -> bool | None:
        return self.running


@pytest.fixture
def setup(tmp_path: Path):
    real = Roots(tmp_path / "real" / "NG Jobs", tmp_path / "real" / "Mozaik Shared", tmp_path / "real" / "control",
                 tmp_path / "data")
    for folder in real.__dict__.values():
        if folder is not None:
            Path(folder).mkdir(parents=True, exist_ok=True)
    shadow = cm.p5_shadow_roots(tmp_path / "data")
    ctx = _Ctx(open_store(tmp_path / "data" / "ngrunner.db"), real)
    mozaik = _Mozaik()
    return ctx, shadow, mozaik, cm.p5_interlock(ctx, running=mozaik)


def gated(tmp_path: Path, *, model=None, measured=MEASURED, number: int = 1) -> cm.Gated:
    lib = hb.library(tmp_path / "lib")
    model = model or hb.model()
    room = wr.write_room(model, number, hb.template(), lib, hb.settings())
    assert isinstance(room, wr.WrittenRoom)
    return cm.Gated(room, gt.gate_room(room, model, hb.WALLS, hb.template(), lib, measured))


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# ----- a room that passed every gate


def test_a_room_that_passed_every_gate_is_written_into_the_shadow_copy_only(setup, tmp_path: Path) -> None:
    ctx, shadow, _, interlock = setup
    room = gated(tmp_path)

    result = cm.commit_rooms(JOB, [room], roots=shadow, interlock=interlock)

    assert result.ok, result.reason
    target = shadow.mozaik_shared / "Jobs" / JOB / "Room1.des"
    assert target.read_bytes() == room.room.content and result.written == (target,)
    assert shadow.mozaik_shared.parent.name == "P5 shadow" and shadow.mozaik_shared.name == "Mozaik copy"
    assert not list(ctx.roots.mozaik_shared.rglob("*.des"))


def test_a_room_ng_runner_wrote_can_be_written_again_at_the_hash_it_left(setup, tmp_path: Path) -> None:
    _, shadow, _, interlock = setup
    first = gated(tmp_path)
    assert cm.commit_rooms(JOB, [first], roots=shadow, interlock=interlock).ok
    again = gated(tmp_path, model=hb.model(name="Testerson - Primary again"))

    result = cm.commit_rooms(JOB, [again], roots=shadow, interlock=interlock,
                             expected={"Room1.des": sha(first.room.content)})

    assert result.ok, result.reason


def test_a_room_it_didnt_write_is_never_replaced(setup, tmp_path: Path) -> None:
    _, shadow, _, interlock = setup
    folder = shadow.mozaik_shared / "Jobs" / JOB
    folder.mkdir(parents=True)
    (folder / "Room1.des").write_bytes(b"Jim's room")

    result = cm.commit_rooms(JOB, [gated(tmp_path)], roots=shadow, interlock=interlock,
                             expected={"Room1.des": sha(b"Jim's room")})

    assert not result.ok and "not NG Runner's own file" in result.reason
    assert (folder / "Room1.des").read_bytes() == b"Jim's room"


# ----- a room that didn't pass is never written


def test_a_blocked_room_is_not_written_and_the_values_that_block_it_are_named(setup) -> None:
    _, shadow, _, interlock = setup
    blocked = wr.BlockedRoom(2, (("wall 2", "run start", "the run is drawn 0.78 in from the wall's start"),))

    result = cm.commit_rooms(JOB, [blocked], roots=shadow, interlock=interlock)

    assert not result.ok
    assert result.reason == "Room2.des not written: wall 2: run start: the run is drawn 0.78 in from the wall's start"
    assert not list(shadow.mozaik_shared.rglob("*.des"))


def test_a_room_whose_gate_6_could_not_verify_is_not_written(setup, tmp_path: Path) -> None:
    _, shadow, _, interlock = setup
    room = gated(tmp_path, measured=None)

    result = cm.commit_rooms(JOB, [room], roots=shadow, interlock=interlock)

    assert not result.ok and "gate 6 could not verify: wall 2: its drawing was not measured" in result.reason
    assert not list(shadow.mozaik_shared.rglob("*.des"))


def test_one_room_that_didnt_pass_stops_the_whole_job(setup, tmp_path: Path) -> None:
    _, shadow, _, interlock = setup
    good = gated(tmp_path)
    bad = gated(tmp_path, measured=None, number=2)

    result = cm.commit_rooms(JOB, [good, bad], roots=shadow, interlock=interlock)

    assert not result.ok and "Room2.des" in result.reason and not list(shadow.mozaik_shared.rglob("*.des"))


def test_a_room_with_another_rooms_gates_is_not_written(setup, tmp_path: Path) -> None:
    _, shadow, _, interlock = setup
    one, two = gated(tmp_path), gated(tmp_path, number=2)

    result = cm.commit_rooms(JOB, [cm.Gated(two.room, one.gates)], roots=shadow, interlock=interlock)

    assert not result.ok and result.reason == "Room2.des: its gates are Room1.des's"


def test_a_committed_rooms_colour_source_is_kept_in_ng_runners_own_data_and_nowhere_else(setup, tmp_path) -> None:
    from datetime import UTC, datetime

    from ngrunner.build import colour as co

    ctx, shadow, _, interlock = setup
    at = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    result = cm.commit_rooms(JOB, [gated(tmp_path)], roots=shadow, interlock=interlock, store=ctx.store, at=at)

    assert result.ok, result.reason
    held = co.recorded_sources(ctx.store)
    key = f"{JOB}\\Room1.des"
    assert list(held) == [key] and held[key]["from_job"] == hb.JOB
    written = (shadow.mozaik_shared / "Jobs" / JOB / "Room1.des").read_bytes()
    assert hb.JOB.encode() not in written  # the room names no source


def test_a_refused_commit_records_no_colour_source(setup, tmp_path) -> None:
    from datetime import UTC, datetime

    from ngrunner.build import colour as co

    ctx, shadow, _, interlock = setup
    room = gated(tmp_path, measured=None)
    result = cm.commit_rooms(JOB, [room], roots=shadow, interlock=interlock, store=ctx.store,
                             at=datetime(2026, 11, 1, 9, 0, tzinfo=UTC))
    assert not result.ok and co.recorded_sources(ctx.store) == {}


# ----- the chokepoint's P5 rules (writes.py)


@pytest.mark.parametrize("rel, words", [
    (("Jobs", JOB, "Room0.des"), "Room<n>.des, n of 1 or more"),
    (("Jobs", JOB, "JobDat.dat"), "Room<n>.des"),
    (("Room1.des",), "Room<n>.des"),
    (("Jobs", JOB, "sub", "Room1.des"), "Room<n>.des"),
])
def test_in_mozaik_shared_p5_writes_only_a_designs_room(setup, rel, words: str) -> None:
    _, shadow, _, interlock = setup
    result = commit_write([FileWrite(shadow.mozaik_shared.joinpath(*rel), b"x", None)], roots=shadow,
                          interlock=interlock, phase=cm.P5_SHADOW)
    assert not result.ok and words in result.reason


def test_p5_writes_nothing_in_ng_jobs_or_the_control_folder(setup) -> None:
    _, shadow, _, interlock = setup
    for path in (shadow.ng_jobs / JOB / f"{JOB}_summary.pdf", shadow.control / "heartbeat.json"):
        result = commit_write([FileWrite(path, b"x", None)], roots=shadow, interlock=interlock, phase=cm.P5_SHADOW)
        assert not result.ok and "only rooms" in result.reason


def test_the_real_mozaik_shared_is_refused_by_name(setup, tmp_path: Path) -> None:
    ctx, shadow, _, _ = setup
    roots = dataclasses.replace(shadow, mozaik_shared=tmp_path / "elsewhere" / "Mozaik Shared")
    roots.mozaik_shared.mkdir(parents=True)
    result = commit_write([FileWrite(roots.mozaik_shared / "Jobs" / JOB / "Room1.des", b"x", None)], roots=roots,
                          interlock=lambda paths: None, phase=cm.P5_SHADOW)
    assert not result.ok and "real Mozaik Shared gets no room until P5 goes live" in result.reason


def test_a_shadow_copy_on_a_cloud_drive_is_refused(setup, monkeypatch: pytest.MonkeyPatch) -> None:
    _, shadow, _, interlock = setup
    monkeypatch.setattr(toolkit, "on_local_disk", lambda path: False)
    result = commit_write([FileWrite(shadow.mozaik_shared / "Jobs" / JOB / "Room1.des", b"x", None)], roots=shadow,
                          interlock=interlock, phase=cm.P5_SHADOW)
    assert not result.ok and "this PC's own disk only" in result.reason


def test_p5_live_has_no_rules_yet(setup, tmp_path: Path) -> None:
    _, shadow, _, interlock = setup
    result = cm.commit_rooms(JOB, [gated(tmp_path)], roots=shadow, interlock=interlock, phase=cm.P5_LIVE)
    assert not result.ok and "not built yet" in result.reason


# ----- the interlock


def test_the_interlock_waits_for_mozaik_a_pause_and_a_lock_file(setup, tmp_path: Path) -> None:
    ctx, shadow, mozaik, interlock = setup
    target = shadow.mozaik_shared / "Jobs" / JOB / "Room1.des"
    assert interlock([target]) is None
    mozaik.running = True
    assert "Mozaik is running" in interlock([target])
    mozaik.running = None
    assert "can't tell whether Mozaik is running" in interlock([target])
    mozaik.running = False
    target.parent.mkdir(parents=True)
    (target.parent / "MozaikJob.LCK").write_bytes(b"")
    assert "open in Mozaik" in interlock([target])
    (target.parent / "MozaikJob.LCK").unlink()
    set_state(ctx.store, PAUSED, "yes")
    assert interlock([target]) == PAUSED_REASON


def test_the_interlock_refuses_anything_below_the_real_folders(setup) -> None:
    ctx, _, _, interlock = setup
    for real, words in ((ctx.roots.mozaik_shared, "Mozaik Shared"), (ctx.roots.ng_jobs, "NG Jobs"),
                        (ctx.roots.control, "the control folder")):
        assert interlock([real / "Jobs" / JOB / "Room1.des"]) == cm.REAL_REASON.format(where=words)


# ----- break tests: each safety rule broken on purpose, and the next lock catching it


def test_break_a_write_outside_the_shadow_copy_is_refused_by_two_locks(setup, tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the commit at the real Mozaik Shared, as a bug in the roots would: the chokepoint refuses it by the
    folder's name. Take that lock out of writes.py, and the interlock refuses it: it is below the folder NG
    Runner is configured with, whatever it is called."""
    import ngrunner.writes as writes

    ctx, shadow, _, interlock = setup
    real = dataclasses.replace(shadow, mozaik_shared=ctx.roots.mozaik_shared)
    room = gated(tmp_path)

    first = cm.commit_rooms(JOB, [room], roots=real, interlock=interlock)
    monkeypatch.setitem(writes._PHASE_RULES, cm.P5_SHADOW,
                        dataclasses.replace(writes._PHASE_RULES[cm.P5_SHADOW], root_problem=lambda root: None))
    second = cm.commit_rooms(JOB, [room], roots=real, interlock=interlock)

    assert not first.ok and "real Mozaik Shared gets no room until P5 goes live" in first.reason
    assert not second.ok and second.reason.endswith(cm.REAL_REASON.format(where="Mozaik Shared"))
    assert not list(ctx.roots.mozaik_shared.rglob("*.des"))


def test_break_a_room_with_a_could_not_verify_value_written_anyway_is_caught_by_its_gates(
        setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The writer refuses a model holding a could-not-verify. Take that refusal out, and the room is written,
    but its "settled" gate fails and the commit refuses it."""
    _, shadow, _, interlock = setup
    model = hb.model()
    broken = dataclasses.replace(model, joints=dataclasses.replace(model.joints, status="could not verify",
                                                                   why="made up for the test"))
    lib = hb.library(tmp_path / "lib")
    assert isinstance(wr.write_room(broken, 1, hb.template(), lib, hb.settings()), wr.BlockedRoom)
    monkeypatch.setattr(wr, "blocking", lambda *a, **k: [])
    room = wr.write_room(broken, 1, hb.template(), lib, hb.settings())
    assert isinstance(room, wr.WrittenRoom)  # the first lock is out

    result = cm.commit_rooms(JOB, [cm.Gated(room, gt.gate_room(room, broken, hb.WALLS, hb.template(), lib, MEASURED))],
                             roots=shadow, interlock=interlock)

    assert not result.ok and "settled failed: room: joints: made up for the test" in result.reason
    assert not list(shadow.mozaik_shared.rglob("*.des"))


def test_break_a_gate_failure_ignored_is_caught_by_the_commit(setup, tmp_path: Path) -> None:
    """A gate that fails (a WM edge left unlocked): passed is False and the commit refuses. Drop the failing
    gate from the room's gates, as a bug that ignores it would, and the commit still refuses: the gate didn't
    run. Report it passed with its items still failing, and the commit refuses on the gate's own status."""
    _, shadow, _, interlock = setup
    good = gated(tmp_path)
    content = good.room.content.replace(b'EBand="1" EBandLock="True" SideName="Front"',
                                        b'EBand="1" EBandLock="False" SideName="Front"', 1)
    room = dataclasses.replace(good.room, content=content)
    lib = hb.library(tmp_path / "lib")
    gates = gt.gate_room(room, hb.model(), hb.WALLS, hb.template(), lib, MEASURED)
    assert gates.gate("WM banding").status == "failed"

    dropped = dataclasses.replace(gates, gates=tuple(g for g in gates.gates if g.name != "WM banding"))
    as_if_passed = dataclasses.replace(gates, gates=tuple(
        dataclasses.replace(g, status="passed") if g.name != "WM banding" else g for g in gates.gates))

    for g in (gates, dropped, as_if_passed):
        result = cm.commit_rooms(JOB, [cm.Gated(room, g)], roots=shadow, interlock=interlock)
        assert not result.ok and "WM banding" in result.reason
    assert not list(shadow.mozaik_shared.rglob("*.des"))


def test_break_a_could_not_verify_gate_is_never_read_as_passed(setup, tmp_path: Path) -> None:
    _, shadow, _, interlock = setup
    room = gated(tmp_path, measured={"2": gt.Measured(None, "elev_panels.py renders the page with poppler's pdftoppm, "
                                                             "which isn't on this PC")})
    assert room.gates.gate("gate 6").status == "could not verify" and not room.gates.passed
    result = cm.commit_rooms(JOB, [room], roots=shadow, interlock=interlock)
    assert not result.ok and "pdftoppm" in result.reason


def test_break_a_model_settled_by_hand_is_still_held_to_its_panels(setup, tmp_path: Path) -> None:
    """A model edited after it was built (its x moved, still "set") writes what it says; the panels gate holds
    the bytes to that model, and gate 6 to the drawing, which catches the move."""
    _, shadow, _, interlock = setup
    model = hb.model()
    s = model.schedules[1]
    moved = dataclasses.replace(s.panels[1], x_mm=dataclasses.replace(s.panels[1].x_mm, value=900.0))
    model = dataclasses.replace(model, schedules=(model.schedules[0], dataclasses.replace(
        s, panels=(s.panels[0], moved, s.panels[2])), model.schedules[2]))
    room = gated(tmp_path, model=model)
    assert room.gates.gate("gate 6").status == "failed"
    assert not cm.commit_rooms(JOB, [room], roots=shadow, interlock=interlock).ok


def test_an_unverified_fact_reads_as_could_not_verify() -> None:
    assert unverified("x").status == "could not verify" and not unverified("x").settled
