"""
The observer: whether Mozaik is running, who a job's lock files name, and the owner log (P1b build spec
5.12). It looks and never touches: a lock is never deleted, and nothing gates on what it sees in P1b.

No real .UNS or .LCK byte has been read here, so these are written from Jim's lines:
    FAILURES 12, SCHEMA 41    MozaikJob.UNS is 7 bytes, JDELA\\r\\n
    FAILURES 49               Mozaik swaps UNS to LCK on open; a crash leaves the LCK
    build spec 3.6            OptimizeJob.LCK while the Optimizer runs
What goes in a .LCK is not in his docs at all (build spec 8, step 1), so anything goes and nothing breaks.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from ngrunner.guards.observer import LOCK_FILE, OPTIMIZER_LOCK, UNS_FILE, Locks, mozaik_running, note_owners, read_locks
from ngrunner.guards.reads import CloudOnly
from ngrunner.store import list_dev, list_owner_log, open_store

NOW = datetime(2026, 10, 30, 22, 0, tzinfo=timezone.utc)
FOLDER = "Testerson_101426"


def job_dir(tmp_path: Path, **files: bytes) -> Path:
    path = tmp_path / FOLDER
    path.mkdir(exist_ok=True)
    for name, data in {"uns": UNS_FILE, "lck": LOCK_FILE, "opt": OPTIMIZER_LOCK}.items():
        if name in files:
            (path / data).write_bytes(files[name])
    return path


def age(path: Path, minutes: float) -> None:
    when = (NOW - timedelta(minutes=minutes)).timestamp()
    os.utime(path, (when, when))


class Psutil:
    """Just what mozaik_running needs of psutil."""

    def __init__(self, names=(), raises: Exception | None = None) -> None:
        self.names, self.raises = names, raises

    def process_iter(self, attrs):
        assert attrs == ["name"]
        if self.raises:
            raise self.raises
        return [SimpleNamespace(info={"name": name}) for name in self.names]


# ----- is Mozaik running


@pytest.mark.parametrize("names, running", [
    (("explorer.exe", "Mozaik.exe"), True),
    (("Optimize.exe",), True),
    (("mozaik.EXE",), True),
    (("explorer.exe", "NotMozaik.exe", "Mozaik.exe.bak"), False),
    ((None, "explorer.exe"), False),
    ((), None),
    ((None, None), None),
], ids=["mozaik", "the optimizer", "any case", "others", "a name it can't read", "nothing listed",
        "no names at all"])
def test_mozaik_running_says_yes_no_or_that_it_cant_tell(names, running) -> None:
    assert mozaik_running(psutil=Psutil(names)) is running


def test_processes_that_cant_be_read_at_all_say_it_cant_tell_never_no() -> None:
    assert mozaik_running(psutil=Psutil(raises=OSError("denied"))) is None


def test_the_names_can_be_given_for_when_jims_pc_shows_others() -> None:
    assert mozaik_running(names=("Other.exe",), psutil=Psutil(("Other.exe",))) is True
    assert mozaik_running(names=("Other.exe",), psutil=Psutil(("Mozaik.exe",))) is False


def test_it_reads_real_processes_on_this_pc_and_finds_no_mozaik() -> None:
    # Mozaik is not installed here (build spec section 1).
    assert mozaik_running() is False


# ----- reading the lock files



def test_the_uns_is_read_as_his_docs_give_it(tmp_path: Path) -> None:
    locks = read_locks(job_dir(tmp_path, uns=b"JDELA\r\n"), now=NOW)

    assert locks == Locks(uns_owner="JDELA", lck_owner=None, opt_lck_owner=None, lck_age=None)


def test_an_open_job_has_a_lck_with_its_age(tmp_path: Path) -> None:
    # FAILURES 49: Mozaik swaps UNS to LCK on open.
    path = job_dir(tmp_path, lck=b"JDELA\r\n")
    age(path / LOCK_FILE, 90)

    locks = read_locks(path, now=NOW)

    assert (locks.uns_owner, locks.lck_owner, locks.opt_lck_owner) == (None, "JDELA", None)
    assert locks.lck_age == timedelta(minutes=90)


def test_the_optimizers_lock_is_read_too_with_its_own_age(tmp_path: Path) -> None:
    # Build spec 3.6: OptimizeJob.LCK while the Optimizer runs.
    path = job_dir(tmp_path, opt=b"GEORGE\r\n")
    age(path / OPTIMIZER_LOCK, 5)

    locks = read_locks(path, now=NOW)

    assert locks.opt_lck_owner == "GEORGE"
    assert locks.opt_lck_age == timedelta(minutes=5)
    assert locks.lck_age is None


def test_a_job_with_neither_uns_nor_lck_says_so_and_nothing_breaks(tmp_path: Path) -> None:
    # FAILURES 18-20: a job can have neither.
    assert read_locks(job_dir(tmp_path), now=NOW) == Locks(None, None, None, None)


def test_a_job_folder_that_isnt_there_is_the_same_and_nothing_breaks(tmp_path: Path) -> None:
    assert read_locks(tmp_path / "none here", now=NOW) == Locks(None, None, None, None)


@pytest.mark.parametrize("data, owner", [
    (b"", ""),
    (b"\r\n", ""),
    (b"  JDELA  \r\n", "JDELA"),
    (b"JDELA", "JDELA"),
    (b"jd\xff\xfeela\r\n", "jd��ela"),
    (b"J\x00D\x00\r\x00\n\x00", "JD"),
    (b"JDELA\r\n2026-10-30 09:42\r\n", "JDELA 2026-10-30 09:42"),
    (b"A" * 500, "A" * 80),
], ids=["empty", "blank line", "spaces", "no line end", "not text", "nul bytes", "two lines", "very long"])
def test_whatever_a_lock_holds_reads_as_some_string_and_never_breaks(tmp_path: Path, data, owner) -> None:
    # What is in a .LCK isn't in his docs (build spec 8, step 1), so nothing here may rest on it.
    assert read_locks(job_dir(tmp_path, lck=data), now=NOW).lck_owner == owner


def test_a_lock_only_in_the_cloud_is_there_with_no_owner_and_says_so(tmp_path: Path, monkeypatch) -> None:
    path = job_dir(tmp_path, lck=b"JDELA\r\n", uns=b"JDELA\r\n")

    def read(given: Path) -> bytes:
        raise CloudOnly(f"{given.name} is only in the cloud.", 362)

    locks = read_locks(path, now=NOW, read=read)

    assert (locks.uns_owner, locks.lck_owner) == (None, None)
    assert set(locks.unread) == {UNS_FILE, LOCK_FILE}


def test_a_lock_with_a_clock_ahead_of_ours_has_no_negative_age(tmp_path: Path) -> None:
    path = job_dir(tmp_path, lck=b"JDELA\r\n")
    age(path / LOCK_FILE, -30)

    assert read_locks(path, now=NOW).lck_age == timedelta(0)


# ----- it never touches a lock


def test_reading_leaves_every_lock_exactly_as_it_was(tmp_path: Path) -> None:
    path = job_dir(tmp_path, uns=b"JDELA\r\n", lck=b"JDELA\r\n", opt=b"JDELA\r\n")
    for name in (UNS_FILE, LOCK_FILE, OPTIMIZER_LOCK):
        age(path / name, 60 * 48)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in path.iterdir()}

    read_locks(path, now=NOW)

    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in path.iterdir()} == before


def test_nothing_in_the_observer_can_remove_or_write_a_file() -> None:
    # Design F1, F2: LCKs are never deleted. A check of the source, as a test can't prove a negative.
    source = Path(__file__).parent.parent / "ngrunner" / "guards" / "observer.py"
    code = source.read_text(encoding="utf-8")
    for word in ("unlink", "remove(", "rmtree", "os.rename", "os.replace", "write_bytes", "write_text",
                 "commit(", 'open('):
        assert word not in code, word


# ----- the owner log


def test_each_string_read_is_logged_with_its_file_and_time(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ngrunner.db")

    note_owners(store, folder=FOLDER, locks=Locks("JDELA", "JDELA", None, None), at=NOW)

    assert [(r.at, r.folder, r.file, r.owner) for r in list_owner_log(store)] == [
        (NOW, FOLDER, UNS_FILE, "JDELA"), (NOW, FOLDER, LOCK_FILE, "JDELA")]


def test_a_lock_that_isnt_there_or_cant_be_read_logs_nothing(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ngrunner.db")

    note_owners(store, folder=FOLDER, locks=Locks(None, None, None, None, unread=(LOCK_FILE,)), at=NOW)

    assert list_owner_log(store) == []


def test_the_first_string_seen_raises_nothing(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ngrunner.db")

    note_owners(store, folder=FOLDER, locks=Locks("JDELA", None, None, None), at=NOW)
    note_owners(store, folder="Fakeley_102026", locks=Locks("JDELA", None, None, None), at=NOW)

    assert list_dev(store) == []


def test_any_string_other_than_the_first_seen_goes_to_dev_at_once(tmp_path: Path) -> None:
    # Decision 4: does George open shared jobs? The first sign of anyone else goes to Sulaiman.
    store = open_store(tmp_path / "ngrunner.db")
    note_owners(store, folder=FOLDER, locks=Locks("JDELA", None, None, None), at=NOW)

    note_owners(store, folder="Fakeley_102026", locks=Locks(None, "GEORGE", None, None), at=NOW)

    item, = list_dev(store)
    assert item.code == "lock_owner_other"
    assert "GEORGE" in item.text and "JDELA" in item.text and "Fakeley_102026" in item.text
    assert not item.urgent


def test_the_same_other_string_is_one_dev_item_while_it_stands(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ngrunner.db")
    note_owners(store, folder=FOLDER, locks=Locks("JDELA", None, None, None), at=NOW)

    for _ in range(3):
        note_owners(store, folder=FOLDER, locks=Locks(None, "GEORGE", None, None), at=NOW)

    assert len(list_dev(store)) == 1
    assert len(list_owner_log(store)) == 4


def test_an_empty_lock_is_logged_since_it_is_a_thing_to_know_and_names_no_other_owner(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ngrunner.db")
    note_owners(store, folder=FOLDER, locks=Locks("", None, None, None), at=NOW)
    note_owners(store, folder=FOLDER, locks=Locks("JDELA", "", None, None), at=NOW)

    assert [r.owner for r in list_owner_log(store)] == ["", "JDELA", ""]
    assert list_dev(store) == []                            # JDELA is the first owner it has seen


def test_the_owner_of_the_optimizers_lock_counts_too(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ngrunner.db")
    note_owners(store, folder=FOLDER, locks=Locks("JDELA", None, None, None), at=NOW)

    note_owners(store, folder=FOLDER, locks=Locks(None, None, "GEORGE", None), at=NOW)

    assert [d.code for d in list_dev(store)] == ["lock_owner_other"]
