"""
P4's rules in the write chokepoint (design 3.2, "Allowed, by phase"): a room's .des fix, a tracker
batch and the job sheet, written only as shadow copies until P4 goes live.

Every root is a fake folder under tmp_path; the real tracker, Mozaik Shared and G: are never touched.
A folder named like the real ones stands in for them, as the rules refuse those by name, and a drive
that reads as a cloud drive is made by patching how the drive's kind is read. Job names are made up.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import ngrunner.toolkit as toolkit
from ngrunner.lock import P4_LIVE, P4_SHADOW
from ngrunner.writes import FileWrite, Roots, commit_write

JOB = "Testerson_101426"
ROOM = b"15\r\n<?xml version=\"1.0\"?>\r\n<Room Hide=\"False\" />\r\n"
FIXED = ROOM.replace(b'Hide="False"', b'Hide="True" ')
BATCH = b"TAB,JOB,FIELD1,FIELD2,FIELD3,FIELD4,FIELD5,FIELD6\r\nJOBSUMMARY,X,,y,\r\n"


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def clear(paths: list[Path]) -> str | None:
    return None


@pytest.fixture
def shadow(tmp_path: Path) -> Roots:
    """P4's shadow roots: copies on this PC's own disk, never named like the real folders."""
    r = Roots(ng_jobs=tmp_path / "NG Jobs shadow", mozaik_shared=tmp_path / "Mozaik shadow",
              control=tmp_path / "control", app_data=tmp_path / "appdata",
              tracker=tmp_path / "tracker test")
    for folder in (r.ng_jobs, r.mozaik_shared, r.control, r.app_data, r.tracker):
        folder.mkdir()
    return r


def room(roots: Roots, name: str = "Room1.des", content: bytes = ROOM) -> Path:
    """A room Mozaik wrote: NG Runner doesn't own it."""
    path = roots.mozaik_shared / "Jobs" / JOB / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def write(roots: Roots, *writes: FileWrite, phase: str = P4_SHADOW):
    return commit_write(list(writes), roots=roots, interlock=clear, phase=phase)


# ----- a room's .des fix


def test_a_room_fix_replaces_a_room_it_does_not_own_at_the_hash_it_read(shadow: Roots) -> None:
    target = room(shadow)

    result = write(shadow, FileWrite(target, FIXED, sha(ROOM)))

    assert result.ok, result.reason
    assert target.read_bytes() == FIXED
    assert len(result.backups) == 1 and result.backups[0].read_bytes() == ROOM


def test_a_room_that_changed_since_it_was_read_is_not_fixed(shadow: Roots) -> None:
    target = room(shadow, content=ROOM + b"saved again\r\n")

    result = write(shadow, FileWrite(target, FIXED, sha(ROOM)))

    assert not result.ok and "changed since it was read" in result.reason
    assert target.read_bytes() == ROOM + b"saved again\r\n"


def test_a_fix_never_makes_a_room_that_isnt_there(shadow: Roots) -> None:
    target = shadow.mozaik_shared / "Jobs" / JOB / "Room9.des"
    target.parent.mkdir(parents=True)

    result = write(shadow, FileWrite(target, FIXED, None))

    assert not result.ok and "only fixes a room that is there" in result.reason
    assert not target.exists()


@pytest.mark.parametrize("parts", [
    ("Jobs", JOB, "JobDat.dat"),
    ("Jobs", JOB, "Room1.bak"),
    ("Jobs", JOB, "3-4 Egger Classic White.opt"),
    ("Jobs", JOB, "3-4 Egger Classic White", "Room1.des"),
    ("Jobs", "Room1.des"),
    ("_NGRunner_holding", JOB, "Room1.des"),
    ("Room1.des",),
])
def test_in_mozaik_shared_only_a_rooms_des_in_its_job_folder_is_written(shadow: Roots, parts) -> None:
    target = shadow.mozaik_shared.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(ROOM)

    result = write(shadow, FileWrite(target, FIXED, sha(ROOM)))

    assert not result.ok
    assert target.read_bytes() == ROOM


def test_p1a_still_writes_nothing_in_mozaik_shared(shadow: Roots) -> None:
    target = room(shadow)

    result = write(shadow, FileWrite(target, FIXED, sha(ROOM)), phase="P1a")

    assert not result.ok and "Mozaik Shared" in result.reason
    assert target.read_bytes() == ROOM


# ----- a tracker batch


def test_a_tracker_batch_goes_in_as_a_new_file_in_the_folder_given(shadow: Roots) -> None:
    target = shadow.tracker / "tracker_inbox_20261031T130000Z-a1b2c3.csv"

    result = write(shadow, FileWrite(target, BATCH, None))

    assert result.ok, result.reason
    assert target.read_bytes() == BATCH


def test_a_tracker_batch_is_written_once_and_never_again(shadow: Roots) -> None:
    target = shadow.tracker / "tracker_inbox_20261031T130000Z-a1b2c3.csv"
    assert write(shadow, FileWrite(target, BATCH, None)).ok

    result = write(shadow, FileWrite(target, BATCH + BATCH, sha(BATCH)))

    assert not result.ok and "only once" in result.reason
    assert target.read_bytes() == BATCH


@pytest.mark.parametrize("name", ["tracker_inbox.csv", "ingested_tracker_inbox_1.csv",
                                  "tracker_snapshot_SheetGoods.csv", "tracker_inbox_a b.csv",
                                  "tracker_inbox_1.txt"])
def test_only_a_batch_with_its_own_name_goes_in_the_tracker_folder(shadow: Roots, name) -> None:
    target = shadow.tracker / name

    result = write(shadow, FileWrite(target, BATCH, None))

    assert not result.ok
    assert not target.exists()


def test_p1a_writes_nothing_in_the_tracker_folder(shadow: Roots) -> None:
    target = shadow.tracker / "tracker_inbox_20261031T130000Z-a1b2c3.csv"

    result = write(shadow, FileWrite(target, BATCH, None), phase="P1a")

    assert not result.ok and "tracker" in result.reason
    assert not target.exists()


# ----- the real places wait for go-live


def test_the_real_tracker_folder_is_refused_until_p4_goes_live(tmp_path: Path) -> None:
    real = tmp_path / "CNC operations" / "NG Conversion program Claude"
    roots = Roots(tmp_path / "NG Jobs shadow", tmp_path / "Mozaik shadow", tmp_path / "control",
                  tmp_path / "appdata", tracker=real)
    for folder in (roots.ng_jobs, roots.mozaik_shared, roots.control, roots.app_data, real):
        folder.mkdir(parents=True)
    target = real / "tracker_inbox_20261031T130000Z-a1b2c3.csv"

    shadow_try = write(roots, FileWrite(target, BATCH, None))
    live_try = write(roots, FileWrite(target, BATCH, None), phase=P4_LIVE)

    assert not shadow_try.ok and "tracker's own folder" in shadow_try.reason
    assert not live_try.ok and "not built yet" in live_try.reason
    assert list(real.iterdir()) == []


def test_the_real_mozaik_shared_is_refused_until_p4_goes_live(tmp_path: Path) -> None:
    roots = Roots(tmp_path / "NG Jobs shadow", tmp_path / "Mozaik Shared", tmp_path / "control",
                  tmp_path / "appdata")
    for folder in (roots.ng_jobs, roots.mozaik_shared, roots.control, roots.app_data):
        folder.mkdir()
    target = room(roots)

    shadow_try = write(roots, FileWrite(target, FIXED, sha(ROOM)))
    live_try = write(roots, FileWrite(target, FIXED, sha(ROOM)), phase=P4_LIVE)

    assert not shadow_try.ok and "real Mozaik Shared" in shadow_try.reason
    assert not live_try.ok and "not built yet" in live_try.reason
    assert target.read_bytes() == ROOM


def test_p4s_build_writes_nothing_on_a_cloud_or_network_drive(shadow: Roots, monkeypatch) -> None:
    drive = str(shadow.tracker)[:3]
    real_volume = toolkit._volume
    monkeypatch.setattr(toolkit, "_volume",
                        lambda root: (3, "FAT32") if root.upper() == drive.upper() else real_volume(root))
    target = shadow.tracker / "tracker_inbox_20261031T130000Z-a1b2c3.csv"

    result = write(shadow, FileWrite(target, BATCH, None))

    assert not result.ok and "this PC's own disk" in result.reason
    assert not target.exists()


# ----- the job sheet, and what P1a already wrote


def test_the_job_sheet_goes_in_only_when_there_is_none(shadow: Roots) -> None:
    target = shadow.ng_jobs / JOB / f"{JOB} CNC Job Sheet.docx"

    first = write(shadow, FileWrite(target, b"sheet", None))
    again = write(shadow, FileWrite(target, b"another sheet", sha(b"sheet")))

    assert first.ok, first.reason
    assert not again.ok and "only once" in again.reason
    assert target.read_bytes() == b"sheet"


def test_a_job_sheet_jim_made_is_never_replaced(shadow: Roots) -> None:
    target = shadow.ng_jobs / JOB / f"{JOB} CNC Job Sheet.docx"
    target.parent.mkdir()
    target.write_bytes(b"Jim's sheet")

    result = write(shadow, FileWrite(target, b"sheet", sha(b"Jim's sheet")))

    assert not result.ok
    assert target.read_bytes() == b"Jim's sheet"


def test_a_job_sheet_under_another_name_is_refused(shadow: Roots) -> None:
    target = shadow.ng_jobs / JOB / "Sampleton_101526 CNC Job Sheet.docx"

    result = write(shadow, FileWrite(target, b"sheet", None))

    assert not result.ok
    assert not target.exists()


def test_p4_keeps_what_p1a_writes(shadow: Roots) -> None:
    target = shadow.ng_jobs / JOB / f"{JOB}_summary.pdf"

    result = write(shadow, FileWrite(target, b"%PDF", None))

    assert result.ok, result.reason


def test_a_tracker_folder_inside_ng_jobs_is_refused(tmp_path: Path) -> None:
    roots = Roots(tmp_path / "NG Jobs shadow", tmp_path / "Mozaik shadow", tmp_path / "control",
                  tmp_path / "appdata", tracker=tmp_path / "NG Jobs shadow" / "tracker test")
    for folder in (roots.ng_jobs, roots.mozaik_shared, roots.control, roots.app_data, roots.tracker):
        folder.mkdir(parents=True, exist_ok=True)

    result = write(roots, FileWrite(roots.tracker / "tracker_inbox_1.csv", BATCH, None))

    assert not result.ok and "overlap" in result.reason
