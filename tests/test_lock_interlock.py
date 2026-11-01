"""
The P4 interlock (ngrunner.interlock.p4_interlock): what commit_write asks before a P4 write, and again
just before the renames (design 5, "Mozaik interlock").

The real folders here are fake ones under tmp_path, standing for the folders NG Runner is configured
with; P4's build writes only into other folders, its shadow copies. Mozaik's process list is a fake.
Job names are made up.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ngrunner.clock import FakeClock
from ngrunner.interlock import PAUSED, PAUSED_REASON, p4_interlock
from ngrunner.lock import P4_SHADOW
from ngrunner.store import Store, open_store, set_state
from ngrunner.writes import FileWrite, Roots, commit_write

JOB = "Testerson_101426"
ROOM = b"15\r\n<?xml version=\"1.0\"?>\r\n<Room />\r\n"


@dataclass
class _Ctx:
    store: Store
    roots: Roots
    clock: FakeClock
    tracker: Path | None = None
    leases: set[str] = field(default_factory=set)

    def lease_held(self, job: str) -> bool:
        return job in self.leases


class _Mozaik:
    """Mozaik's process list: running True, False, or None when it can't be read."""

    def __init__(self) -> None:
        self.running: bool | None = False

    def __call__(self) -> bool | None:
        return self.running


@pytest.fixture
def setup(tmp_path: Path):
    real = Roots(tmp_path / "real" / "NG Jobs", tmp_path / "real" / "Mozaik Shared",
                 tmp_path / "real" / "control", tmp_path / "data",
                 tracker=tmp_path / "real" / "NG Conversion program Claude")
    shadow = Roots(tmp_path / "shadow" / "NG Jobs", tmp_path / "shadow" / "Mozaik copy",
                   tmp_path / "shadow" / "control", tmp_path / "data",
                   tracker=tmp_path / "shadow" / "tracker test")
    for folder in {*real.__dict__.values(), *shadow.__dict__.values()}:
        Path(folder).mkdir(parents=True, exist_ok=True)
    clock = FakeClock(datetime(2026, 10, 31, 13, 0, tzinfo=UTC))
    ctx = _Ctx(open_store(tmp_path / "data" / "ngrunner.db"), real, clock, tracker=real.tracker,
               leases={JOB})
    return ctx, shadow


def job_folder(roots: Roots, job: str = JOB, *, uns: bool = True, age_s: float = 600,
               clock: FakeClock | None = None) -> Path:
    """A closed Mozaik job: its room and its closed marker, last touched age_s ago."""
    folder = roots.mozaik_shared / "Jobs" / job
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Room1.des").write_bytes(ROOM)
    if uns:
        (folder / "MozaikJob.UNS").write_bytes(b"JDELA\r\n")
    if clock is not None:
        settle(folder, clock, age_s)
    return folder


def settle(folder: Path, clock: FakeClock, age_s: float) -> None:
    stamp = (clock.now() - timedelta(seconds=age_s)).timestamp()
    for entry in folder.iterdir():
        os.utime(entry, (stamp, stamp))


def room_of(folder: Path) -> Path:
    return folder / "Room1.des"


def polled_twice(check, targets: list[Path], clock: FakeClock) -> str | None:
    """Two polls two minutes apart, as the design asks: the first only starts the wait."""
    first = check(targets)
    clock.advance(timedelta(seconds=121))
    return check(targets) if first is not None else first


# ----- a shadow room, cleared


def test_a_shadow_room_is_clear_after_two_polls_two_minutes_apart(setup) -> None:
    ctx, shadow = setup
    folder = job_folder(shadow, clock=ctx.clock)
    check = p4_interlock(ctx, mozaik_running=_Mozaik())

    first = check([room_of(folder)])
    ctx.clock.advance(timedelta(seconds=60))
    early = check([room_of(folder)])
    ctx.clock.advance(timedelta(seconds=61))
    settle(folder, ctx.clock, 600)

    assert "2 minutes" in first and "2 minutes" in early
    assert check([room_of(folder)]) is None


# ----- the real folders wait for go-live


@pytest.mark.parametrize("where", ["ng_jobs", "mozaik_shared", "control", "tracker"])
def test_nothing_below_the_real_folders_is_written_in_p4s_build(setup, where: str) -> None:
    ctx, _ = setup
    target = Path(getattr(ctx.roots, where)) / JOB / "Room1.des"

    assert "until P4 goes live" in p4_interlock(ctx, mozaik_running=_Mozaik())([target])


# ----- Mozaik


def test_no_room_is_written_while_mozaik_runs(setup) -> None:
    ctx, shadow = setup
    folder = job_folder(shadow, clock=ctx.clock)
    mozaik = _Mozaik()
    check = p4_interlock(ctx, mozaik_running=mozaik)
    assert polled_twice(check, [room_of(folder)], ctx.clock) is None

    mozaik.running = True
    assert "Mozaik is running" in check([room_of(folder)])
    mozaik.running = None
    assert "can't tell whether Mozaik is running" in check([room_of(folder)])


@pytest.mark.parametrize("lock", ["MozaikJob.LCK", "OptimizeJob.LCK"])
def test_a_room_whose_job_has_a_lock_file_waits_and_the_two_polls_start_again(setup, lock) -> None:
    ctx, shadow = setup
    folder = job_folder(shadow, clock=ctx.clock)
    check = p4_interlock(ctx, mozaik_running=_Mozaik())
    check([room_of(folder)])
    ctx.clock.advance(timedelta(seconds=100))
    (folder / lock).write_bytes(b"JDELA\r\n")

    assert "open in Mozaik" in check([room_of(folder)])
    (folder / lock).unlink()
    settle(folder, ctx.clock, 600)
    ctx.clock.advance(timedelta(seconds=30))
    assert "2 minutes" in check([room_of(folder)])      # the wait began again when the lock went


def test_a_job_with_no_closed_marker_gets_no_write(setup) -> None:
    ctx, shadow = setup
    folder = job_folder(shadow, uns=False, clock=ctx.clock)

    assert "MozaikJob.UNS" in polled_twice(p4_interlock(ctx, mozaik_running=_Mozaik()),
                                           [room_of(folder)], ctx.clock)


def test_a_folder_changed_in_the_last_two_minutes_waits(setup) -> None:
    ctx, shadow = setup
    folder = job_folder(shadow, clock=ctx.clock)
    check = p4_interlock(ctx, mozaik_running=_Mozaik())
    assert polled_twice(check, [room_of(folder)], ctx.clock) is None

    settle(folder, ctx.clock, 30)

    assert "changed in the last 2 minutes" in check([room_of(folder)])


def test_ng_runners_own_write_and_its_temp_files_dont_count_as_a_change(setup) -> None:
    ctx, shadow = setup
    folder = job_folder(shadow, clock=ctx.clock)
    check = p4_interlock(ctx, mozaik_running=_Mozaik())
    assert polled_twice(check, [room_of(folder)], ctx.clock) is None

    room_of(folder).write_bytes(ROOM + b"fixed\r\n")
    check.note_own(room_of(folder))
    (folder / "Room1.des.x1y2.ngr-tmp").write_bytes(b"staged")

    assert check([room_of(folder)]) is None


def test_a_room_needs_ng_runners_lease_on_its_job(setup) -> None:
    ctx, shadow = setup
    folder = job_folder(shadow, "Sampleton_101526", clock=ctx.clock)

    reason = polled_twice(p4_interlock(ctx, mozaik_running=_Mozaik()), [room_of(folder)], ctx.clock)

    assert "no lease" in reason


def test_a_pause_stops_every_p4_write(setup) -> None:
    ctx, shadow = setup
    set_state(ctx.store, PAUSED, "yes")
    batch = shadow.tracker / "tracker_inbox_1.csv"

    assert p4_interlock(ctx, mozaik_running=_Mozaik())([batch]) == PAUSED_REASON


def test_a_batch_and_a_job_sheet_dont_wait_for_mozaik(setup) -> None:
    ctx, shadow = setup
    mozaik = _Mozaik()
    mozaik.running = True
    check = p4_interlock(ctx, mozaik_running=mozaik)

    assert check([shadow.tracker / "tracker_inbox_1.csv",
                  shadow.ng_jobs / JOB / f"{JOB} CNC Job Sheet.docx"]) is None


# ----- through the chokepoint


def test_a_fix_is_refused_and_the_room_untouched_when_mozaik_starts_mid_commit(setup) -> None:
    ctx, shadow = setup
    folder = job_folder(shadow, clock=ctx.clock)
    mozaik = _Mozaik()
    check = p4_interlock(ctx, mozaik_running=mozaik)
    assert polled_twice(check, [room_of(folder)], ctx.clock) is None
    calls = []

    def starts_after_the_first_call(paths: list[Path]) -> str | None:
        calls.append(paths)
        answer = check(paths)
        mozaik.running = True
        return answer

    result = commit_write([FileWrite(room_of(folder), b"fixed", hashlib.sha256(ROOM).hexdigest())],
                          roots=shadow, interlock=starts_after_the_first_call, phase=P4_SHADOW)

    assert len(calls) == 2
    assert not result.ok and "Mozaik is running" in result.reason
    assert room_of(folder).read_bytes() == ROOM


# ----- decision 4 (who opens jobs) and 7d (a .bak that may hold unsaved work)


def test_a_lock_naming_anyone_but_the_first_owner_stops_every_room_write(setup) -> None:
    from ngrunner.store import log_owner
    ctx, shadow = setup
    folder = job_folder(shadow, clock=ctx.clock)
    check = p4_interlock(ctx, mozaik_running=_Mozaik())
    log_owner(ctx.store, at=ctx.clock.now(), folder=JOB, file="MozaikJob.UNS", owner="TESTER")
    assert polled_twice(check, [room_of(folder)], ctx.clock) is None

    log_owner(ctx.store, at=ctx.clock.now(), folder="Sampleton_101526", file="MozaikJob.LCK", owner="OTHER")

    assert "decision 4" in check([room_of(folder)])


def bak_pair(folder: Path, *, same_design: bool, larger: bool) -> None:
    """Room1.bak beside Room1.des: the same design saved again (a counter Mozaik moves on a save), or
    another design."""
    design = (b'15\r\n<?xml version="1.0" encoding="utf-8"?>\r\n<Room Name="Made up" IdTagCount="1">\r\n'
              b'  <Product ProdName="FM 84" CabNo="1" Width="19.304" Height="2000" />\r\n</Room>\r\n')
    backup = design.replace(b'IdTagCount="1"', b'IdTagCount="2"') if same_design else \
        design.replace(b'Height="2000"', b'Height="2100"')
    (folder / "Room1.des").write_bytes(design)
    (folder / "Room1.bak").write_bytes(backup + (b"<!-- -->" * 8 if larger else b""))
    assert (backup != design) and (larger or len(backup) <= len(design))


@pytest.mark.parametrize("same_design, larger, blocked", [
    (False, True, True),       # RULES 81 fires and the designs differ: may hold unsaved work
    (True, True, False),       # RULES 81 fires, but it is the same design saved again
    (False, False, False),     # RULES 81 doesn't fire: an older, smaller backup
])
def test_a_room_whose_backup_may_hold_unsaved_work_is_not_written(setup, same_design, larger,
                                                                  blocked) -> None:
    ctx, shadow = setup
    folder = job_folder(shadow, clock=ctx.clock)
    bak_pair(folder, same_design=same_design, larger=larger)
    settle(folder, ctx.clock, 600)

    reason = polled_twice(p4_interlock(ctx, mozaik_running=_Mozaik()), [room_of(folder)], ctx.clock)

    assert (reason is not None and "unsaved work" in reason) == blocked, reason


# ----- Pause and release-all hand every lease back, through the pause


def test_a_lease_is_released_through_a_pause_and_nothing_else_goes_through_that_door(setup) -> None:
    from ngrunner.lock.leases import LEASE_NAME

    ctx, shadow = setup
    set_state(ctx.store, PAUSED, "yes")
    lease = shadow.ng_jobs / JOB / LEASE_NAME
    folder = job_folder(shadow, clock=ctx.clock)
    releasing = p4_interlock(ctx, mozaik_running=_Mozaik(), releasing=True)

    assert p4_interlock(ctx, mozaik_running=_Mozaik())([lease]) == PAUSED_REASON
    assert releasing([lease]) is None
    assert releasing([room_of(folder)]) is not None                     # never a room
    assert releasing([shadow.tracker / "tracker_inbox_1.csv"]) is not None
    assert releasing([ctx.roots.ng_jobs / JOB / LEASE_NAME]) is not None   # nor the real NG Jobs
