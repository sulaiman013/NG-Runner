"""
Tests for the write chokepoint, commit_write() (design section 3.2), through its public
interface, and for the store's write journal and pruning. To stand in for a process that dies
or a step that fails, a few patch one private step. Every root is a fake folder under tmp_path;
real G: and OneDrive are never touched. Job names are synthetic.
"""
from __future__ import annotations

import _winapi
import ctypes
import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import ngrunner.writes as writes
from ngrunner.clock import FakeClock
from ngrunner.store import (
    JOURNAL_STATES, LATEST, MIGRATIONS, PrunedRenders, Settled, Store, StoreError, StoreRegistry,
    add_render, ensure_job, list_dev, list_pending_writes, list_renders, list_write_records,
    migrate, open_store, owned_sha, prune_renders, prune_write_records, record_commit,
    set_governing, settle_pending_writes, write_journal,
)
from ngrunner.writes import CommitResult, FileWrite, Roots, commit_recorded, commit_write

REPO = Path(__file__).resolve().parent.parent
JOB = "Testerson_101426"
LEASE = "_NG Runner is handling this job.txt"
LONG = "\\\\?\\"  # the Windows long-path prefix
NG, MZ, CTL, APP = "ng_jobs", "mozaik_shared", "control", "app_data"
ARABIC_DATE_JOB = "Old \u0661\u0660\u0661\u0664\u0662\u0666"  # six digits, but not 0-9


@pytest.fixture
def roots(tmp_path: Path) -> Roots:
    r = Roots(
        ng_jobs=tmp_path / "NG Jobs",
        mozaik_shared=tmp_path / "Mozaik Shared",
        control=tmp_path / "control",
        app_data=tmp_path / "appdata",
    )
    for folder in (r.ng_jobs, r.mozaik_shared, r.control, r.app_data):
        folder.mkdir()
    return r


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def clear(paths: list[Path]) -> str | None:
    """The Mozaik interlock when nothing is running: writing is safe."""
    return None


def write_new(roots: Roots, target: Path, content: bytes) -> None:
    """NG Runner creates a file (so it owns it from then on)."""
    result = commit_write([FileWrite(target, content, None)], roots=roots, interlock=clear)
    assert result.ok, result.reason


def leftovers(folder: Path) -> list[Path]:
    """Temp files a commit must never leave behind."""
    return list(folder.rglob("*.ngr-tmp"))


def tree(folder: Path) -> set[Path]:
    return set(folder.rglob("*"))


@pytest.fixture
def library(roots: Roots) -> Path:
    """A fake Mozaik product library: what a write that escapes would damage."""
    folder = roots.mozaik_shared / "Product Libraries"
    folder.mkdir()
    (folder / "parameters.dat").write_bytes(b"LIBRARY")
    return folder


def files_in(folder: Path) -> list[str]:
    """Every file below folder, without following links."""
    return sorted(
        str(Path(top, name).relative_to(folder))
        for top, _, names in os.walk(folder, followlinks=False)
        for name in names
    )


def junction(target: Path, link: Path) -> None:
    """A directory junction at link that leads to target (needs no admin rights)."""
    winapi = pytest.importorskip("_winapi")
    winapi.CreateJunction(str(target), str(link))


def watch_renames(monkeypatch: pytest.MonkeyPatch, before=None, after=None) -> dict:
    """Run before(src, dst) and after(src, dst) around every rename a commit makes, whether
    it uses os.rename or os.replace. Returns the real functions, for the hooks' own use."""
    real = {"rename": os.rename, "replace": os.replace}

    def wrap(name: str):
        def rename(src, dst) -> None:
            if before:
                before(Path(src), Path(dst))
            real[name](src, dst)
            if after:
                after(Path(src), Path(dst))

        return rename

    monkeypatch.setattr(os, "rename", wrap("rename"))
    monkeypatch.setattr(os, "replace", wrap("replace"))
    return real


def move_behind_junction(folder: Path, into: Path) -> None:
    """Someone moves folder into another place and leaves a junction to it at the old name."""
    moved = into / folder.name
    folder.rename(moved)
    junction(moved, folder)


def under(root: str, *parts: str):
    """A target below one of the fake roots."""
    return lambda r, t: getattr(r, root).joinpath(*parts)


def long_prefixed(root: str, *parts: str):
    return lambda r, t: Path(LONG + str(getattr(r, root).joinpath(*parts)))


# The P1a exit test: scripted forbidden writes, each refused for the stated reason. Every one
# is a brand-new file with the interlock clear, so only the path rules can refuse it.
FORBIDDEN = [
    # (case, target, words the reason must contain)
    # -- outside the roots, however it is spelled
    ("outside every root", lambda r, t: t / "elsewhere" / "notes.txt", "outside"),
    ("dot-dot climbs out of NG Jobs", under(NG, JOB, "..", "..", "x.txt"), "outside"),
    ("relative path", lambda r, t: Path(f"{JOB}_summary.pdf"), "full path"),
    ("drive-relative path", lambda r, t: Path(f"C:{JOB}_summary.pdf"), "full path"),
    ("NG Runner's own data folder", under(APP, "notes.json"), "own data folder"),
    # -- Mozaik Shared: none of it in P1a
    ("a Mozaik job's room", under(MZ, "Jobs", JOB, "Room1.des"), "Mozaik Shared"),
    ("the holding folder", under(MZ, "_NGRunner_holding", JOB, "Room1.des"), "Mozaik Shared"),
    ("long-path prefix into Mozaik Shared",
     long_prefixed(MZ, "Jobs", JOB, "JobDat.dat"), "Mozaik Shared"),
    # -- never in any phase: libraries, templates, Jim's toolkit and its regression fixtures
    ("Mozaik's Data library", under(MZ, "Data", "Materials.dat"), "never writes"),
    ("a product library",
     under(MZ, "Product Libraries", "NG Closets", "parameters.dat"), "never writes"),
    ("Jim's toolkit", under(MZ, "_NG_Automation", "scripts", "crosscheck.py"), "never writes"),
    ("toolkit templates", under(MZ, "_NG_Automation", "templates", "FM_84.xml"), "never writes"),
    ("regression fixtures",
     under(MZ, "_NG_Automation", "reference", "regression", "baseline.json"), "never writes"),
    ("a template job",
     under(MZ, "Jobs", "Lux Closet Template Claude", "Room1.des"), "never writes"),
    ("templates inside NG Runner's own folder",
     under(NG, "_NG Runner", "templates", "x.json"), "never writes"),
    ("a Data folder in the control folder", under(CTL, "Data", "x.json"), "never writes"),
    # -- NG Jobs: only NG Runner's own files, never a job's subfolders or chat outputs
    ("a TAP material folder", under(NG, JOB, "3-4 Egger Classic White", "notes.txt"), "subfolder"),
    ("the chat's crosscheck", under(NG, JOB, f"{JOB}_crosscheck.txt"), "chat"),
    ("the chat's report", under(NG, JOB, f"{JOB}_report.txt"), "chat"),
    ("the chat's actions", under(NG, JOB, f"{JOB}_actions.json"), "chat"),
    ("a chat-style name in control", under(CTL, "weekly_report.txt"), "chat"),
    ("IC Conversion JSON before P3", under(NG, JOB, f"{JOB}_IC_Conversion.json"), "P3"),
    ("IC Conversion workbook before P3", under(NG, JOB, f"{JOB}_IC_Conversion.xlsx"), "P3"),
    ("a summary named for another job",
     under(NG, JOB, "Otherson_101426_summary.pdf"), "only writes"),
    ("Jim's job sheet", under(NG, JOB, f"{JOB} CNC Job Sheet.docx"), "only writes"),
    ("a file directly in NG Jobs", under(NG, "notes.txt"), "directly in NG Jobs"),
    ("a folder that is not a job", under(NG, "Old", "Old_summary.pdf"), "job folder"),
    ("a date in Arabic-Indic digits",
     under(NG, ARABIC_DATE_JOB, f"{ARABIC_DATE_JOB}_summary.pdf"), "job folder"),
    # -- names never written anywhere
    ("Index.html", under(CTL, "Index.html"), "Installer App"),
    ("parts_catalog.csv", under(NG, "_NG Runner", "parts_catalog.csv"), "Installer App"),
    ("INDEX.HTML in capitals", under(NG, "_NG Runner", "INDEX.HTML"), "Installer App"),
    ("a g-code program", under(CTL, "TesS01.TAP"), "Mozaik's own"),
    ("a Mozaik lock file", under(NG, "_NG Runner", "MozaikJob.LCK"), "Mozaik's own"),
    ("a temp-file name", under(CTL, "heartbeat.json.ngr-tmp"), ".ngr-tmp"),
    # -- names Windows would silently change, or treat as devices or streams
    ("a trailing dot", under(NG, JOB, f"{JOB}_summary.pdf."), "dot or a space"),
    ("a trailing space", under(CTL, "heartbeat.json "), "dot or a space"),
    ("a folder with a trailing dot", under(CTL, "outbox.", "0001.json"), "dot or a space"),
    ("CON", under(CTL, "CON"), "device name"),
    ("nul.txt", under(CTL, "nul.txt"), "device name"),
    ("COM1 with an extension", under(NG, "_NG Runner", "COM1.json"), "device name"),
    ("an LPT9 folder", under(CTL, "LPT9", "0001.json"), "device name"),
    ("an alternate data stream", under(NG, "_NG Runner", "settled.json:hidden"), "character"),
    ("a trailing dot behind the long-path prefix",
     long_prefixed(CTL, "heartbeat.json."), "dot or a space"),
    ("the device namespace",
     lambda r, t: Path("\\\\.\\" + str(r.control / "heartbeat.json")), "full path"),
]


# ----- new files


def test_files_a_new_summary_into_a_new_job_folder(roots: Roots) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"

    result = commit_write(
        [FileWrite(target, b"%PDF-1.7 synthetic", None)], roots=roots, interlock=clear
    )

    assert result.ok, result.reason
    assert result.reason is None
    assert target.read_bytes() == b"%PDF-1.7 synthetic"
    assert result.written == (target.resolve(),)
    assert result.backups == ()
    assert leftovers(roots.ng_jobs) == []


def test_files_every_p1a_name_in_one_commit(roots: Roots) -> None:
    job, garage = roots.ng_jobs / JOB, roots.ng_jobs / f"{JOB}_Gar"
    targets = [
        job / f"{JOB}_summary.pdf",
        job / f"{JOB}_NGRunner.json",
        job / f"{JOB}_NGRunner_log.txt",
        job / LEASE,
        garage / f"{JOB}_Gar_summary.pdf",
        roots.ng_jobs / "_NG Runner" / "settled.json",
        roots.ng_jobs / "_NG Runner" / "history" / "2026-10.json",
        roots.control / "heartbeat.json",
        roots.control / "outbox" / "0001.json",
    ]

    result = commit_write(
        [FileWrite(t, t.name.encode(), None) for t in targets], roots=roots, interlock=clear
    )

    assert result.ok, result.reason
    assert [t.read_bytes() for t in targets] == [t.name.encode() for t in targets]


def test_the_long_path_prefix_is_normalised_not_refused(roots: Roots) -> None:
    target = roots.control / "heartbeat.json"

    result = commit_write(
        [FileWrite(Path(LONG + str(target)), b"beat", None)], roots=roots, interlock=clear
    )

    assert result.ok, result.reason
    assert target.read_bytes() == b"beat"
    assert result.written == (target.resolve(),)


@pytest.mark.parametrize("call", [1, 2], ids=["first safety check", "last safety check"])
def test_content_is_taken_once_so_a_buffer_changed_mid_commit_is_never_written(
    roots: Roots, call: int
) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    content = bytearray(b"%PDF original")
    calls = 0

    def interlock(paths: list[Path]) -> str | None:
        nonlocal calls
        calls += 1
        if calls == call:  # the caller reuses its buffer while the commit is still running
            content[:] = b"%PDF changed!"
        return None

    result = commit_write([FileWrite(target, content, None)], roots=roots, interlock=interlock)

    assert result.ok, result.reason
    assert target.read_bytes() == b"%PDF original"
    assert result.written == (target.resolve(),)
    again = commit_write(
        [FileWrite(target, b"%PDF v2", sha(b"%PDF original"))], roots=roots, interlock=clear
    )
    assert again.ok, again.reason  # its record of what it wrote matches the disk


def test_paths_compare_case_insensitively(roots: Roots) -> None:
    shouted = Path(str(roots.ng_jobs).upper()) / JOB.lower() / f"{JOB}_SUMMARY.pdf"

    result = commit_write([FileWrite(shouted, b"%PDF", None)], roots=roots, interlock=clear)

    assert result.ok, result.reason
    assert (roots.ng_jobs / JOB / f"{JOB}_summary.pdf").read_bytes() == b"%PDF"


# ----- temp files: a commit removes only its own


def leave_a_file(at: Path, victim: Path):
    at.write_bytes(b"half-written before a crash")
    return lambda: at.read_bytes() == b"half-written before a crash"


def leave_a_folder(at: Path, victim: Path):
    at.mkdir()
    return at.is_dir


def leave_a_junction(at: Path, victim: Path):
    victim.mkdir()
    (victim / "keep.txt").write_bytes(b"keep")
    junction(victim, at)
    return lambda: at.is_junction() and (victim / "keep.txt").read_bytes() == b"keep"


def leave_a_symlink(at: Path, victim: Path):
    victim.write_bytes(b"Jim's library")
    try:
        at.symlink_to(victim)
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or admin rights")
    return lambda: at.is_symlink() and victim.read_bytes() == b"Jim's library"


@pytest.mark.parametrize(
    "leave",
    [leave_a_file, leave_a_folder, leave_a_junction, leave_a_symlink],
    ids=["a crash leftover", "a folder", "a junction", "a symlink"],
)
def test_anything_left_at_a_temp_name_is_never_touched(roots: Roots, tmp_path: Path, leave) -> None:
    target = roots.control / "heartbeat.json"
    untouched = [
        leave(folder / name, tmp_path / f"victim of {folder.name}")
        for folder, name in [
            (roots.control, "heartbeat.json.ngr-tmp"),
            (roots.app_data, "owned_files.json.ngr-tmp"),
        ]
    ]

    result = commit_write([FileWrite(target, b"beat", None)], roots=roots, interlock=clear)

    assert result.ok, result.reason
    assert target.read_bytes() == b"beat"
    assert [still_there() for still_there in untouched] == [True, True]
    assert [p.name for p in leftovers(roots.control)] == ["heartbeat.json.ngr-tmp"]


def known_temp_names(monkeypatch: pytest.MonkeyPatch, first: str) -> None:
    """Make the random part of temp names predictable: first, then distinct others."""
    names = iter([first, *(f"{n:08x}" for n in range(1, 100))])
    monkeypatch.setattr(secrets, "token_hex", lambda nbytes=None: next(names))


def test_a_link_that_leads_nowhere_at_the_temp_name_is_never_followed(
    roots: Roots, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = roots.control / "heartbeat.json"
    link, evil = roots.control / "heartbeat.json.deadbeef.ngr-tmp", library / "evil.dat"
    try:
        link.symlink_to(evil)  # following it would create evil.dat in the library
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or admin rights")
    leads_to = os.readlink(link)
    known_temp_names(monkeypatch, first="deadbeef")

    result = commit_write([FileWrite(target, b"beat", None)], roots=roots, interlock=clear)

    assert not evil.exists()
    assert files_in(library) == ["parameters.dat"]
    assert result.ok, result.reason  # the name was taken, so it picked another
    assert target.read_bytes() == b"beat"
    assert link.is_symlink() and os.readlink(link) == leads_to
    assert [p.name for p in leftovers(roots.control)] == [link.name]


class OtherFileId:
    """What os.fstat says about a file, but with another file ID."""

    def __init__(self, st: os.stat_result) -> None:
        self._st = st

    def __getattr__(self, name: str):
        return getattr(self._st, name)

    @property
    def st_ino(self) -> int:
        return self._st.st_ino + 1


def test_a_new_file_its_drive_names_by_another_id_is_left_unwritten(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A drive whose open file and its name give different IDs (AGENTS.md: Before filing goes
    # live, item 3): NG Runner can't confirm that the file at the name is the one it made.
    real_fstat = os.fstat
    monkeypatch.setattr(os, "fstat", lambda fd: OtherFileId(real_fstat(fd)))
    target = roots.control / "heartbeat.json"

    result = commit_write([FileWrite(target, b"beat", None)], roots=roots, interlock=clear)

    assert not result.ok
    assert "could not be prepared" in result.reason
    assert not target.exists()
    (left,) = leftovers(roots.control)  # not provably its own, so never removed
    assert left.read_bytes() == b""  # and nothing was written into it


def test_a_refused_commit_leaves_temp_files_it_did_not_make_alone(roots: Roots) -> None:
    leftover = roots.control / "b.json.ngr-tmp"
    leftover.write_bytes(b"someone's half-written file")
    outbox = roots.control / "outbox"
    calls = 0

    def interlock(paths: list[Path]) -> str | None:
        nonlocal calls
        calls += 1
        if calls == 1:  # a file appears where the outbox folder was going to be made
            outbox.write_bytes(b"not a folder")
        return None

    result = commit_write(
        [FileWrite(outbox / "a.json", b"a", None), FileWrite(roots.control / "b.json", b"b", None)],
        roots=roots, interlock=interlock,
    )

    assert not result.ok
    assert "could not be prepared" in result.reason
    assert leftover.read_bytes() == b"someone's half-written file"
    assert outbox.read_bytes() == b"not a folder"
    assert not (roots.control / "b.json").exists()


def test_a_temp_file_that_cannot_be_written_is_removed_again(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    def disk_full(fd: int) -> None:
        raise OSError(28, "There is not enough space on the disk")

    monkeypatch.setattr(os, "fsync", disk_full)

    result = commit_write(
        [FileWrite(roots.ng_jobs / JOB / f"{JOB}_summary.pdf", b"%PDF", None)],
        roots=roots, interlock=clear,
    )

    assert not result.ok
    assert "could not be prepared (There is not enough space on the disk)" in result.reason
    assert not (roots.ng_jobs / JOB).exists()  # its temp file and the folder made for it


def test_stopping_while_a_temp_file_is_written_removes_it_then_stops(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    def stopped(fd: int) -> None:
        raise KeyboardInterrupt  # Ctrl+C, or NG Runner is being closed

    monkeypatch.setattr(os, "fsync", stopped)

    with pytest.raises(KeyboardInterrupt):
        commit_write(
            [FileWrite(roots.ng_jobs / JOB / f"{JOB}_summary.pdf", b"%PDF", None)],
            roots=roots, interlock=clear,
        )

    assert not (roots.ng_jobs / JOB).exists()  # its temp file and the folder made for it


# ----- overwriting NG Runner's own files


def test_overwrites_its_own_file_and_backs_up_the_old_content(roots: Roots) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_NGRunner.json"
    write_new(roots, target, b'{"items": 1}')

    result = commit_write(
        [FileWrite(target, b'{"items": 2}', sha(b'{"items": 1}'))], roots=roots, interlock=clear
    )

    assert result.ok, result.reason
    assert target.read_bytes() == b'{"items": 2}'
    (backup,) = result.backups
    assert backup.read_bytes() == b'{"items": 1}'
    assert backup.is_relative_to(roots.app_data / "backup")
    assert backup.name == f"{JOB}_NGRunner.json"


def test_one_commit_keeps_its_backups_together(roots: Roots) -> None:
    job = roots.ng_jobs / JOB
    items, lease = job / f"{JOB}_NGRunner.json", job / LEASE
    write_new(roots, items, b"items v1")
    write_new(roots, lease, b"lease v1")

    result = commit_write(
        [
            FileWrite(items, b"items v2", sha(b"items v1")),
            FileWrite(lease, b"lease v2", sha(b"lease v1")),
        ],
        roots=roots, interlock=clear,
    )

    assert result.ok, result.reason
    assert sorted(b.read_bytes() for b in result.backups) == [b"items v1", b"lease v1"]
    commit_folders = {b.relative_to(roots.app_data / "backup").parts[0] for b in result.backups}
    assert len(commit_folders) == 1


def test_a_file_stays_its_own_across_repeated_overwrites(roots: Roots) -> None:
    target = roots.control / "heartbeat.json"
    write_new(roots, target, b"beat 1")
    assert commit_write(
        [FileWrite(target, b"beat 2", sha(b"beat 1"))], roots=roots, interlock=clear
    ).ok

    result = commit_write(
        [FileWrite(target, b"beat 3", sha(b"beat 2"))], roots=roots, interlock=clear
    )

    assert result.ok, result.reason
    assert target.read_bytes() == b"beat 3"


def test_refuses_to_overwrite_a_file_it_did_not_write(roots: Roots) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_NGRunner.json"
    target.parent.mkdir()
    target.write_bytes(b"made by someone else")

    result = commit_write(
        [FileWrite(target, b"mine", sha(b"made by someone else"))], roots=roots, interlock=clear
    )

    assert not result.ok
    assert "not NG Runner's" in result.reason
    assert target.read_bytes() == b"made by someone else"
    assert result.written == ()


def test_refuses_to_overwrite_its_file_once_someone_else_changed_it(roots: Roots) -> None:
    target = roots.ng_jobs / "_NG Runner" / "settled.json"
    write_new(roots, target, b"settled v1")
    target.write_bytes(b"edited by a chat session")  # the caller re-read it, so its hash is current

    result = commit_write(
        [FileWrite(target, b"settled v2", sha(b"edited by a chat session"))],
        roots=roots, interlock=clear
    )

    assert not result.ok
    assert "not NG Runner's" in result.reason
    assert target.read_bytes() == b"edited by a chat session"


def test_the_owned_check_can_come_from_the_caller(roots: Roots) -> None:
    target = roots.control / "outbox" / "0007.json"
    target.parent.mkdir()
    target.write_bytes(b"queued")
    asked: list[Path] = []

    def owned(path: Path) -> str | None:
        asked.append(path)
        return sha(b"queued")

    result = commit_write(
        [FileWrite(target, b"sent", sha(b"queued"))], roots=roots, interlock=clear, owned=owned
    )

    assert result.ok, result.reason
    assert set(asked) == {target.resolve()}
    assert target.read_bytes() == b"sent"


# ----- backups and the ownership record stay inside NG Runner's data folder


def test_backups_never_go_through_a_junction(roots: Roots, library: Path) -> None:
    target = roots.control / "heartbeat.json"
    write_new(roots, target, b"beat 1")
    junction(library, roots.app_data / "backup")

    result = commit_write(
        [FileWrite(target, b"beat 2", sha(b"beat 1"))], roots=roots, interlock=clear
    )

    assert not result.ok
    assert "link or junction" in result.reason
    assert target.read_bytes() == b"beat 1"
    assert files_in(library) == ["parameters.dat"]


def test_a_backup_junction_made_during_the_safety_check_is_never_followed(
    roots: Roots, library: Path
) -> None:
    target = roots.control / "heartbeat.json"
    write_new(roots, target, b"beat 1")
    calls = 0

    def interlock(paths: list[Path]) -> str | None:
        nonlocal calls
        calls += 1
        if calls == 2:  # the backups are made next
            junction(library, roots.app_data / "backup")
        return None

    result = commit_write(
        [FileWrite(target, b"beat 2", sha(b"beat 1"))], roots=roots, interlock=interlock
    )

    assert not result.ok
    assert "link or junction" in result.reason
    assert target.read_bytes() == b"beat 1"
    assert files_in(library) == ["parameters.dat"]


def can_make_symlinks(folder: Path) -> bool:
    probe = folder / "symlink probe"
    try:
        probe.symlink_to(folder / "nowhere")
    except OSError:
        return False
    probe.unlink()
    return True


def test_a_link_that_leads_nowhere_at_a_backup_name_is_never_followed(
    roots: Roots, library: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not can_make_symlinks(tmp_path):
        pytest.skip("creating symlinks needs Developer Mode or admin rights")
    target, evil = roots.control / "heartbeat.json", library / "evil.dat"
    write_new(roots, target, b"beat 1")
    real_mkdir = os.mkdir
    planted: list[Path] = []

    def mkdir(path, *args, **kwargs) -> None:
        real_mkdir(path, *args, **kwargs)
        folder = Path(path)
        if folder.name == "control" and folder.parent.parent.name == "backup":
            planted.append(folder / target.name)  # just made: a link appears at the backup's name
            planted[-1].symlink_to(evil)

    monkeypatch.setattr(os, "mkdir", mkdir)

    result = commit_write(
        [FileWrite(target, b"beat 2", sha(b"beat 1"))], roots=roots, interlock=clear
    )

    assert planted, "the backup's folder was never made"
    assert not evil.exists()
    assert files_in(library) == ["parameters.dat"]
    assert not result.ok
    assert "backup could not be made" in result.reason
    assert target.read_bytes() == b"beat 1"
    assert planted[0].is_symlink()
    assert leftovers(roots.control) == []


def test_the_ownership_record_is_never_read_through_a_link(roots: Roots, tmp_path: Path) -> None:
    target = roots.control / "heartbeat.json"
    write_new(roots, target, b"beat 1")
    record, elsewhere = roots.app_data / "owned_files.json", tmp_path / "elsewhere.json"
    record.rename(elsewhere)
    try:
        record.symlink_to(elsewhere)
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or admin rights")
    kept = elsewhere.read_bytes()

    result = commit_write(
        [FileWrite(target, b"beat 2", sha(b"beat 1"))], roots=roots, interlock=clear
    )

    assert not result.ok
    assert "link or junction" in result.reason
    assert target.read_bytes() == b"beat 1"
    assert elsewhere.read_bytes() == kept


@pytest.mark.parametrize(
    "damaged",
    [b"[]", b"{not json", b'{"c:\\\\elsewhere\\\\x.json": 5}', b"\xff\xfe{}"],
    ids=["a list", "not JSON", "a value that is not a hash", "not UTF-8"],
)
def test_a_damaged_ownership_record_refuses_before_anything_appears(
    roots: Roots, monkeypatch: pytest.MonkeyPatch, damaged: bytes
) -> None:
    record = roots.app_data / "owned_files.json"
    record.write_bytes(damaged)
    targets = [roots.control / "heartbeat.json", roots.ng_jobs / JOB / f"{JOB}_summary.pdf"]
    asked: list[list[Path]] = []
    renamed_to: list[Path] = []
    watch_renames(monkeypatch, before=lambda src, dst: renamed_to.append(dst))

    result = commit_write(
        [FileWrite(t, b"new", None) for t in targets],
        roots=roots, interlock=lambda paths: asked.append(paths),
    )

    assert not result.ok
    assert "damaged" in result.reason
    assert asked == []
    assert renamed_to == []  # not even for a moment
    assert not targets[0].exists() and not (roots.ng_jobs / JOB).exists()
    assert leftovers(roots.control) == []
    assert record.read_bytes() == damaged


def test_a_junction_at_the_ownership_record_is_never_followed(
    roots: Roots, library: Path
) -> None:
    junction(library, roots.app_data / "owned_files.json")
    target = roots.control / "heartbeat.json"

    result = commit_write([FileWrite(target, b"beat", None)], roots=roots, interlock=clear)

    assert not result.ok
    assert "link or junction" in result.reason
    assert not target.exists()
    assert files_in(library) == ["parameters.dat"]


def test_a_data_folder_that_leads_into_mozaik_shared_is_refused(
    roots: Roots, library: Path
) -> None:
    inside = library / "ngr"
    inside.mkdir()
    roots.app_data.rmdir()
    junction(inside, roots.app_data)

    result = commit_write(
        [FileWrite(roots.control / "heartbeat.json", b"beat", None)], roots=roots, interlock=clear
    )

    assert not result.ok
    assert "overlap" in result.reason
    assert files_in(library) == ["parameters.dat"]


# ----- the caller's expected hash against what is on disk


def test_refuses_when_the_file_changed_since_the_caller_read_it(roots: Roots) -> None:
    target = roots.control / "status.json"
    target.write_bytes(b"version the caller never saw")

    result = commit_write(
        [FileWrite(target, b"new", sha(b"version the caller read"))],
        roots=roots, interlock=clear,
        owned=lambda path: sha(b"version the caller never saw"),
    )

    assert not result.ok
    assert "changed since" in result.reason
    assert target.read_bytes() == b"version the caller never saw"


def test_refuses_to_create_a_file_that_already_exists(roots: Roots) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    write_new(roots, target, b"%PDF first render")

    result = commit_write(
        [FileWrite(target, b"%PDF second render", None)], roots=roots, interlock=clear
    )

    assert not result.ok
    assert "already exists" in result.reason
    assert target.read_bytes() == b"%PDF first render"


def test_refuses_to_replace_a_file_that_is_gone(roots: Roots) -> None:
    target = roots.control / "status.json"

    result = commit_write(
        [FileWrite(target, b"new", sha(b"what was there"))], roots=roots, interlock=clear
    )

    assert not result.ok
    assert "gone" in result.reason
    assert not target.exists()


def test_an_expected_hash_must_be_a_sha256(roots: Roots) -> None:
    target = roots.control / "heartbeat.json"
    write_new(roots, target, b"beat")

    result = commit_write(
        [FileWrite(target, b"beat 2", "not-a-hash")], roots=roots, interlock=clear
    )

    assert not result.ok
    assert "sha256" in result.reason
    assert target.read_bytes() == b"beat"


# ----- the Mozaik interlock


def test_an_interlock_refusal_writes_nothing(roots: Roots) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    asked: list[list[Path]] = []

    def interlock(paths: list[Path]) -> str | None:
        asked.append(list(paths))
        return "Mozaik is running."

    result = commit_write([FileWrite(target, b"%PDF", None)], roots=roots, interlock=interlock)

    assert not result.ok
    assert "Mozaik is running." in result.reason
    assert asked[0] == [target.resolve()]
    assert not (roots.ng_jobs / JOB).exists()


def test_the_interlock_is_asked_again_just_before_the_renames(roots: Roots) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    answers = iter([None, "Mozaik started."])

    result = commit_write(
        [FileWrite(target, b"%PDF", None)], roots=roots, interlock=lambda paths: next(answers)
    )

    assert not result.ok
    assert "Mozaik started." in result.reason
    assert not target.exists()
    assert leftovers(roots.ng_jobs) == []
    assert not (roots.ng_jobs / JOB).exists()


def test_a_save_during_the_commit_is_caught_before_the_renames(roots: Roots) -> None:
    target = roots.control / "heartbeat.json"
    write_new(roots, target, b"beat 1")
    calls = 0

    def interlock(paths: list[Path]) -> str | None:
        nonlocal calls
        calls += 1
        if calls == 2:  # someone saves the file while the temp files are being written
            target.write_bytes(b"saved meanwhile")
        return None

    result = commit_write(
        [FileWrite(target, b"beat 2", sha(b"beat 1"))], roots=roots, interlock=interlock
    )

    assert not result.ok
    assert "changed since" in result.reason
    assert target.read_bytes() == b"saved meanwhile"
    assert leftovers(roots.control) == []


def test_a_crashing_interlock_refuses_instead_of_raising(roots: Roots) -> None:
    target = roots.control / "heartbeat.json"

    def interlock(paths: list[Path]) -> str | None:
        raise RuntimeError("process list unavailable")

    result = commit_write([FileWrite(target, b"beat", None)], roots=roots, interlock=interlock)

    assert not result.ok
    assert "safety check" in result.reason
    assert not target.exists()


def test_a_commit_that_forgets_the_interlock_fails_closed(roots: Roots) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"

    with pytest.raises(TypeError):
        commit_write([FileWrite(target, b"%PDF", None)], roots=roots)  # type: ignore[call-arg]

    assert not (roots.ng_jobs / JOB).exists()


def test_no_interlock_at_all_is_refused(roots: Roots) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"

    result = commit_write([FileWrite(target, b"%PDF", None)], roots=roots, interlock=None)

    assert not result.ok
    assert "safety check" in result.reason
    assert not (roots.ng_jobs / JOB).exists()


# ----- one commit at a time

# A commit started from inside a commit's own callback used to wait for itself forever. It
# runs in a child process, so a hang fails this test instead of freezing the whole run.
NESTED_COMMIT = r'''
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from ngrunner.writes import FileWrite, Roots, commit_write

base, via = Path(sys.argv[2]), sys.argv[3]
roots = Roots(*(base / name for name in ("NG Jobs", "Mozaik Shared", "control", "appdata")))
for folder in (roots.ng_jobs, roots.mozaik_shared, roots.control, roots.app_data):
    folder.mkdir()
status = roots.control / "status.json"
status.write_bytes(b"v1")
v1 = hashlib.sha256(b"v1").hexdigest()
inner = []

def nested(asked_by):
    if via == asked_by and not inner:
        inner.append(commit_write(
            [FileWrite(roots.control / "inner.json", b"x", None)],
            roots=roots, interlock=lambda paths: None))

def interlock(paths):
    nested("interlock")
    return None

def owned(path):
    nested("owned")
    return v1

outer = commit_write(
    [FileWrite(status, b"v2", v1)], roots=roots, interlock=interlock, owned=owned)
print(json.dumps({"outer": outer.ok, "inner": inner[0].ok, "why": inner[0].reason}))
'''


@pytest.mark.parametrize("via", ["interlock", "owned"])
def test_a_commit_started_from_inside_a_commit_is_refused_not_hung(
    tmp_path: Path, via: str
) -> None:
    script, base = tmp_path / "nested.py", tmp_path / "run"
    script.write_text(NESTED_COMMIT, encoding="utf-8")
    base.mkdir()
    command = [sys.executable, "-I", str(script), str(REPO), str(base), via]

    try:
        done = subprocess.run(command, capture_output=True, encoding="utf-8", timeout=30)
    except subprocess.TimeoutExpired:
        pytest.fail(f"commit_write hung when its own {via} callback started a commit")

    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert report["outer"] is True
    assert report["inner"] is False
    assert "inside" in report["why"]
    assert not (base / "control" / "inner.json").exists()


def test_a_commit_from_another_thread_waits_its_turn(roots: Roots) -> None:
    other = roots.control / "other.json"
    second: list[writes.CommitResult] = []
    waiter = threading.Thread(
        target=lambda: second.append(
            commit_write([FileWrite(other, b"2", None)], roots=roots, interlock=clear)
        ),
        daemon=True,
    )
    blocked: list[bool] = []

    def interlock(paths: list[Path]) -> str | None:
        if not blocked:  # the first commit's first check: start the second one now
            waiter.start()
            waiter.join(0.5)
            blocked.append(waiter.is_alive())
        return None

    first = commit_write(
        [FileWrite(roots.control / "first.json", b"1", None)], roots=roots, interlock=interlock
    )
    waiter.join(10)

    assert first.ok, first.reason
    assert blocked == [True]  # it waited instead of being refused or running alongside
    assert second and second[0].ok, second
    assert other.read_bytes() == b"2"


# ----- all or nothing


def test_a_failed_rename_rolls_the_whole_commit_back(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = roots.ng_jobs / JOB
    items, log, lease = job / f"{JOB}_NGRunner.json", job / f"{JOB}_NGRunner_log.txt", job / LEASE
    write_new(roots, items, b"items v1")
    calls = 0

    def fail_the_second(src: Path, dst: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError(13, "The file is in use by another program")

    watch_renames(monkeypatch, before=fail_the_second)

    result = commit_write(
        [
            FileWrite(items, b"items v2", sha(b"items v1")),
            FileWrite(log, b"log line", None),
            FileWrite(lease, b"lease", None),
        ],
        roots=roots, interlock=clear,
    )

    assert not result.ok
    assert "rolled back" in result.reason
    assert items.read_bytes() == b"items v1"
    assert not log.exists() and not lease.exists()
    assert leftovers(roots.ng_jobs) == []
    assert result.written == ()
    assert [b.read_bytes() for b in result.backups] == [b"items v1"]


def test_a_read_back_mismatch_rolls_back_and_leaves_foreign_bytes_alone(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = roots.ng_jobs / JOB
    items, log = job / f"{JOB}_NGRunner.json", job / f"{JOB}_NGRunner_log.txt"
    write_new(roots, items, b"items v1")

    def garble_the_log(src: Path, dst: Path) -> None:
        if dst.name == log.name:  # the drive hands back other bytes than were written
            dst.write_bytes(b"garbled")

    watch_renames(monkeypatch, after=garble_the_log)

    result = commit_write(
        [FileWrite(items, b"items v2", sha(b"items v1")), FileWrite(log, b"log line", None)],
        roots=roots, interlock=clear,
    )

    assert not result.ok
    assert "did not read back" in result.reason
    assert items.read_bytes() == b"items v1"
    assert log.read_bytes() == b"garbled"
    assert "left as found" in result.reason and "left in place" in result.reason
    assert leftovers(roots.ng_jobs) == []


@pytest.mark.parametrize(
    "record_fails", [False, True], ids=["caught at its rename", "and a rollback is forced"]
)
def test_a_file_that_appears_before_its_rename_is_left_alone(
    roots: Roots, record_fails: bool
) -> None:
    heartbeat, status = roots.control / "heartbeat.json", roots.control / "status.json"
    write_new(roots, heartbeat, b"beat 1")
    record = roots.app_data / "owned_files.json"
    calls = 0

    def owned(path: Path) -> str | None:
        nonlocal calls
        calls += 1
        if calls == 2:  # status.json was last seen absent a moment ago
            status.write_bytes(b"written by someone else meanwhile")
            if record_fails:  # recording what was written would fail, after the renames
                record.unlink()
                record.mkdir()
        return sha(b"beat 1")

    result = commit_write(
        [
            FileWrite(status, b"NG Runner's status", None),
            FileWrite(heartbeat, b"beat 2", sha(b"beat 1")),
        ],
        roots=roots, interlock=clear, owned=owned,
    )

    assert not result.ok
    assert "appeared" in result.reason
    assert status.read_bytes() == b"written by someone else meanwhile"
    assert heartbeat.read_bytes() == b"beat 1"
    assert result.written == ()


def test_a_rollback_never_deletes_a_file_it_did_not_put_there(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = roots.ng_jobs / JOB
    summary, log = job / f"{JOB}_summary.pdf", job / f"{JOB}_NGRunner_log.txt"

    def someone_saves_a_copy_then_the_log_fails(src: Path, dst: Path) -> None:
        if dst.name == log.name:
            copy = job / "copy being saved"
            copy.write_bytes(b"%PDF render")  # the same bytes, but not the file NG Runner wrote
            real["replace"](copy, summary)
            raise PermissionError(13, "The file is in use by another program")

    real = watch_renames(monkeypatch, before=someone_saves_a_copy_then_the_log_fails)

    result = commit_write(
        [FileWrite(summary, b"%PDF render", None), FileWrite(log, b"log line", None)],
        roots=roots, interlock=clear,
    )

    assert not result.ok
    assert "rolled back" in result.reason
    assert "left in place" in result.reason
    assert summary.read_bytes() == b"%PDF render"
    assert result.written == (summary,)


def test_a_file_edited_just_before_its_replace_is_left_alone(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    heartbeat, status = roots.control / "heartbeat.json", roots.control / "status.json"
    write_new(roots, status, b"status v1")

    def a_chat_session_saves_status(src: Path, dst: Path) -> None:
        if dst.name == heartbeat.name:  # the rename just before status.json's
            status.write_bytes(b"edited by a chat session")

    watch_renames(monkeypatch, before=a_chat_session_saves_status)

    result = commit_write(
        [FileWrite(heartbeat, b"beat", None), FileWrite(status, b"status v2", sha(b"status v1"))],
        roots=roots, interlock=clear,
    )

    assert not result.ok
    assert "changed" in result.reason
    assert status.read_bytes() == b"edited by a chat session"
    assert not heartbeat.exists()
    assert result.written == ()


def test_stopping_during_the_last_safety_check_cleans_up_then_stops(roots: Roots) -> None:
    status, summary = roots.control / "status.json", roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    write_new(roots, status, b"s1")
    calls = 0

    def interlock(paths: list[Path]) -> str | None:
        nonlocal calls
        calls += 1
        if calls == 2:  # the temp files are written: Ctrl+C, or NG Runner is being closed
            raise KeyboardInterrupt
        return None

    with pytest.raises(KeyboardInterrupt):
        commit_write(
            [FileWrite(status, b"s2", sha(b"s1")), FileWrite(summary, b"%PDF", None)],
            roots=roots, interlock=interlock,
        )

    assert status.read_bytes() == b"s1"
    assert not (roots.ng_jobs / JOB).exists()  # the job folder it made, and the temp in it
    assert leftovers(roots.control) == []
    assert commit_write([FileWrite(status, b"s3", sha(b"s1"))], roots=roots, interlock=clear).ok


def test_stopping_between_two_renames_puts_the_first_back_then_stops(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, other = roots.control / "status.json", roots.control / "other.json"
    write_new(roots, status, b"s1")

    def stop_before_the_second(src: Path, dst: Path) -> None:
        if dst.name == other.name:  # status.json already holds s2
            raise KeyboardInterrupt

    watch_renames(monkeypatch, before=stop_before_the_second)

    with pytest.raises(KeyboardInterrupt):
        commit_write(
            [FileWrite(status, b"s2", sha(b"s1")), FileWrite(other, b"o", None)],
            roots=roots, interlock=clear,
        )

    assert status.read_bytes() == b"s1"
    assert not other.exists()
    assert leftovers(roots.control) == []
    later = commit_write([FileWrite(status, b"s3", sha(b"s1"))], roots=roots, interlock=clear)
    assert later.ok, later.reason  # its record and the disk still agree that s1 is its own
    assert status.read_bytes() == b"s3"


def test_a_cleanup_keeps_a_folder_that_is_no_longer_the_one_it_made(roots: Roots) -> None:
    job = roots.ng_jobs / JOB
    calls = 0

    def interlock(paths: list[Path]) -> str | None:
        nonlocal calls
        calls += 1
        if calls == 2:  # NG Runner made the job folder; someone swaps in a folder of their own
            job.rename(roots.ng_jobs / f"{JOB}_moved")
            job.mkdir()
        return None

    result = commit_write(
        [FileWrite(job / f"{JOB}_summary.pdf", b"%PDF", None)], roots=roots, interlock=interlock
    )

    assert not result.ok
    assert "replaced" in result.reason
    assert job.is_dir()  # empty, but not the one this commit made


# ----- forbidden writes


@pytest.mark.parametrize(
    ("make", "why"), [case[1:] for case in FORBIDDEN], ids=[case[0] for case in FORBIDDEN]
)
def test_forbidden_writes_are_refused(roots: Roots, tmp_path: Path, make, why: str) -> None:
    before = tree(tmp_path)

    result = commit_write(
        [FileWrite(make(roots, tmp_path), b"x", None)], roots=roots, interlock=clear
    )

    assert not result.ok
    assert why in result.reason
    assert tree(tmp_path) == before


def test_a_junction_out_of_an_allowed_folder_is_refused(roots: Roots, tmp_path: Path) -> None:
    winapi = pytest.importorskip("_winapi")
    outside = tmp_path / "outside"
    outside.mkdir()
    settled_folder = roots.ng_jobs / "_NG Runner"
    settled_folder.mkdir()
    winapi.CreateJunction(str(outside), str(settled_folder / "escape"))

    result = commit_write(
        [FileWrite(settled_folder / "escape" / "settled.json", b"x", None)],
        roots=roots, interlock=clear
    )

    assert not result.ok
    assert "link or junction" in result.reason
    assert list(outside.iterdir()) == []


def a_junction_inside_the_job_folder(roots: Roots) -> Path:
    job = roots.ng_jobs / JOB
    job.mkdir()
    junction(roots.control, job / "ctl")
    return job / "ctl" / f"{JOB}_summary.pdf"


def a_job_folder_that_is_a_junction(roots: Roots) -> Path:
    junction(roots.control, roots.ng_jobs / JOB)
    return roots.ng_jobs / JOB / f"{JOB}_summary.pdf"


@pytest.mark.parametrize(
    "make", [a_junction_inside_the_job_folder, a_job_folder_that_is_a_junction]
)
def test_a_junction_from_one_root_into_another_is_refused(
    roots: Roots, tmp_path: Path, make
) -> None:
    target = make(roots)
    before = tree(tmp_path)

    result = commit_write([FileWrite(target, b"%PDF", None)], roots=roots, interlock=clear)

    assert not result.ok
    assert "link or junction" in result.reason
    assert tree(tmp_path) == before
    assert files_in(roots.control) == []


@pytest.mark.parametrize(
    ("root", "name", "lookalike"),
    [
        pytest.param(NG, "NG Jobs", "NG Job\u017f", id="long s"),
        pytest.param(CTL, "Strasse", "Stra\u00dfe", id="sharp s"),
        pytest.param(CTL, "Kontrol", "\u212aontrol", id="Kelvin sign"),
    ],
)
def test_a_lookalike_sibling_of_a_root_is_outside_it(
    tmp_path: Path, root: str, name: str, lookalike: str
) -> None:
    # NTFS keeps these names apart; Python's casefold() merges them.
    base = tmp_path / "drive"
    roots = replace(
        Roots(base / "NG Jobs", base / "Mozaik Shared", base / "control", base / "appdata"),
        **{root: base / name},
    )
    for folder in (roots.ng_jobs, roots.mozaik_shared, roots.control, roots.app_data):
        folder.mkdir(parents=True)
    sibling = base / lookalike
    try:
        sibling.mkdir()
    except FileExistsError:
        pytest.skip(f"this drive treats {name!r} and {lookalike!r} as one name")
    target = sibling / JOB / f"{JOB}_summary.pdf" if root == NG else sibling / "heartbeat.json"
    before = tree(tmp_path)

    result = commit_write([FileWrite(target, b"x", None)], roots=roots, interlock=clear)

    assert not result.ok
    assert "outside" in result.reason
    assert tree(tmp_path) == before


# ----- folders that change while a commit runs (the interlock can take minutes)


def test_a_junction_made_where_a_new_job_folder_goes_is_never_followed(
    roots: Roots, library: Path
) -> None:
    job = roots.ng_jobs / JOB
    calls = 0

    def interlock(paths: list[Path]) -> str | None:
        nonlocal calls
        calls += 1
        if calls == 1:  # planned and checked; now someone plants a junction there
            junction(library, job)
        return None

    result = commit_write(
        [FileWrite(job / f"{JOB}_summary.pdf", b"%PDF", None)], roots=roots, interlock=interlock
    )

    assert not result.ok
    assert "link or junction" in result.reason
    assert files_in(library) == ["parameters.dat"]


def test_a_job_folder_swapped_for_a_junction_after_planning_is_never_followed(
    roots: Roots, library: Path
) -> None:
    job = roots.ng_jobs / JOB
    job.mkdir()
    calls = 0

    def interlock(paths: list[Path]) -> str | None:
        nonlocal calls
        calls += 1
        if calls == 1:  # a sync client or chat session moves the folder; a junction appears
            job.rename(roots.ng_jobs / f"{JOB}_moved")
            junction(library, job)
        return None

    result = commit_write(
        [FileWrite(job / f"{JOB}_NGRunner.json", b"{}", None)], roots=roots, interlock=interlock
    )

    assert not result.ok
    assert "link or junction" in result.reason
    assert files_in(library) == ["parameters.dat"]


def test_a_folder_swapped_during_the_last_safety_check_stops_the_commit(
    roots: Roots, library: Path
) -> None:
    job = roots.ng_jobs / JOB
    job.mkdir()
    calls = 0

    def interlock(paths: list[Path]) -> str | None:
        nonlocal calls
        calls += 1
        if calls == 2:  # the temp files are written; the renames come next
            move_behind_junction(job, library)
        return None

    result = commit_write(
        [FileWrite(job / f"{JOB}_NGRunner.json", b"{}", None)], roots=roots, interlock=interlock
    )

    assert not result.ok
    assert "link or junction" in result.reason
    assert files_in(library) == ["parameters.dat"]


def test_a_folder_swapped_during_the_ownership_check_stops_the_commit(
    roots: Roots, library: Path
) -> None:
    job = roots.ng_jobs / JOB
    items = job / f"{JOB}_NGRunner.json"
    write_new(roots, items, b"items v1")
    calls = 0

    def owned(path: Path) -> str | None:
        nonlocal calls
        calls += 1
        if calls == 2:  # the last check before the renames
            move_behind_junction(job, library)
        return sha(b"items v1")

    result = commit_write(
        [FileWrite(items, b"items v2", sha(b"items v1"))],
        roots=roots, interlock=clear, owned=owned,
    )

    assert not result.ok
    assert "link or junction" in result.reason
    assert files_in(library) == sorted(["parameters.dat", f"{JOB}\\{JOB}_NGRunner.json"])
    assert (library / JOB / items.name).read_bytes() == b"items v1"


def test_a_folder_swapped_as_a_file_is_renamed_in_is_rolled_back(
    roots: Roots, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = roots.ng_jobs / JOB
    job.mkdir()
    real = {"rename": os.rename, "replace": os.replace}
    swapped: list[bool] = []

    def swap_then(name: str):
        def rename(src, dst) -> None:
            if not swapped:  # the last moment: after every check, before the OS rename
                swapped.append(True)
                move_behind_junction(job, library)
            real[name](src, dst)

        return rename

    monkeypatch.setattr(os, "rename", swap_then("rename"))
    monkeypatch.setattr(os, "replace", swap_then("replace"))

    result = commit_write(
        [FileWrite(job / f"{JOB}_summary.pdf", b"%PDF", None)], roots=roots, interlock=clear
    )

    assert not result.ok
    assert "rolled back" in result.reason
    assert files_in(library) == ["parameters.dat"]


def short_name(path: Path) -> str:
    """Windows' 8.3 short form of path, or '' when the call fails."""
    buf = ctypes.create_unicode_buffer(1024)
    return buf.value if ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024) else ""


def test_a_short_name_never_hides_a_protected_folder(roots: Roots, tmp_path: Path) -> None:
    library = roots.control / "Product Libraries"
    library.mkdir()
    short = short_name(library)
    if not short or Path(short).name == library.name:
        pytest.skip("this drive keeps no 8.3 short names")
    before = tree(tmp_path)

    result = commit_write(
        [FileWrite(Path(short) / "x.json", b"x", None)], roots=roots, interlock=clear
    )

    assert not result.ok
    assert "short name" in result.reason
    assert tree(tmp_path) == before


def test_a_hard_linked_file_is_replaced_without_changing_its_other_name(
    roots: Roots, tmp_path: Path
) -> None:
    outside = tmp_path / "lib.dat"
    outside.write_bytes(b"LIBRARY")
    target = roots.control / "status.json"
    os.link(outside, target)

    result = commit_write(
        [FileWrite(target, b"new", sha(b"LIBRARY"))],
        roots=roots, interlock=clear, owned=lambda path: sha(b"LIBRARY"),
    )

    assert result.ok, result.reason
    assert target.read_bytes() == b"new"
    assert outside.read_bytes() == b"LIBRARY"


def test_a_symlink_to_a_file_outside_is_refused(roots: Roots, tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"not NG Runner's")
    link = roots.control / "status.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or admin rights")

    result = commit_write(
        [FileWrite(link, b"x", sha(b"not NG Runner's"))],
        roots=roots, interlock=clear,
        owned=lambda path: sha(b"not NG Runner's"),
    )

    assert not result.ok
    assert "link or junction" in result.reason
    assert outside.read_bytes() == b"not NG Runner's"


def test_the_chokepoint_offers_no_way_to_delete(roots: Roots) -> None:
    target = roots.control / "heartbeat.json"
    write_new(roots, target, b"beat")

    result = commit_write([FileWrite(target, None, sha(b"beat"))], roots=roots, interlock=clear)

    assert not result.ok
    assert "never deletes" in result.reason
    assert target.read_bytes() == b"beat"
    public = [name.lower() for name in vars(writes) if not name.startswith("_")]
    assert [n for n in public if "delete" in n or "remove" in n or "unlink" in n] == []


def test_phases_without_write_rules_yet_are_refused(roots: Roots) -> None:
    target = roots.control / "heartbeat.json"

    result = commit_write(
        [FileWrite(target, b"beat", None)], roots=roots, interlock=clear, phase="P4"
    )

    assert not result.ok
    assert "P4" in result.reason
    assert not target.exists()


# ----- inputs refused before anything is touched; the chokepoint never raises


def test_the_same_file_twice_in_one_commit_is_refused(roots: Roots) -> None:
    target = roots.control / "heartbeat.json"
    twice = [FileWrite(target, b"a", None), FileWrite(Path(str(target).upper()), b"b", None)]

    result = commit_write(twice, roots=roots, interlock=clear)

    assert not result.ok
    assert "twice" in result.reason
    assert not target.exists()


def test_an_empty_commit_is_refused(roots: Roots) -> None:
    result = commit_write([], roots=roots, interlock=clear)

    assert not result.ok
    assert "Nothing to write" in result.reason


def test_overlapping_roots_are_refused(tmp_path: Path) -> None:
    ng_jobs = tmp_path / "NG Jobs"
    roots = Roots(
        ng_jobs=ng_jobs,
        mozaik_shared=tmp_path / "Mozaik Shared",
        control=ng_jobs / "_NG Runner" / "control",
        app_data=tmp_path / "appdata",
    )
    for folder in (roots.ng_jobs, roots.mozaik_shared, roots.control, roots.app_data):
        folder.mkdir(parents=True, exist_ok=True)

    result = commit_write(
        [FileWrite(roots.control / "heartbeat.json", b"beat", None)], roots=roots, interlock=clear
    )

    assert not result.ok
    assert "overlap" in result.reason
    assert not (roots.control / "heartbeat.json").exists()


@pytest.mark.parametrize(
    "spell",
    [
        pytest.param(lambda p: Path(p.name), id="relative"),
        pytest.param(lambda p: Path(p.drive + p.name), id="drive-relative"),
        pytest.param(lambda p: Path(str(p)[len(p.drive) :]), id="no drive"),
        pytest.param(lambda p: Path("\\\\.\\" + str(p)), id="device namespace"),
    ],
)
def test_a_root_must_be_a_full_path(roots: Roots, tmp_path: Path, spell) -> None:
    odd = replace(roots, control=spell(roots.control))
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    before = tree(tmp_path)

    result = commit_write([FileWrite(target, b"%PDF", None)], roots=odd, interlock=clear)

    assert not result.ok
    assert "full path" in result.reason
    assert tree(tmp_path) == before


def test_a_root_may_carry_the_long_path_prefix(roots: Roots) -> None:
    prefixed = replace(roots, control=Path(LONG + str(roots.control)))
    target = roots.control / "heartbeat.json"

    result = commit_write([FileWrite(target, b"beat", None)], roots=prefixed, interlock=clear)

    assert result.ok, result.reason
    assert target.read_bytes() == b"beat"


def test_a_root_may_itself_be_a_junction(roots: Roots, tmp_path: Path) -> None:
    real = tmp_path / "real control"
    real.mkdir()
    linked = replace(roots, control=tmp_path / "control link")
    junction(real, linked.control)

    result = commit_write(
        [FileWrite(linked.control / "heartbeat.json", b"beat", None)], roots=linked, interlock=clear
    )

    assert result.ok, result.reason
    assert (real / "heartbeat.json").read_bytes() == b"beat"
    assert result.written == ((real / "heartbeat.json").resolve(),)


def test_a_root_that_is_not_there_is_never_created(tmp_path: Path) -> None:
    roots = Roots(
        ng_jobs=tmp_path / "G" / "NG Jobs",  # Google Drive not running
        mozaik_shared=tmp_path / "Mozaik Shared",
        control=tmp_path / "control",
        app_data=tmp_path / "appdata",
    )

    result = commit_write(
        [FileWrite(roots.ng_jobs / JOB / f"{JOB}_summary.pdf", b"%PDF", None)],
        roots=roots, interlock=clear
    )

    assert not result.ok
    assert "not reachable" in result.reason
    assert not (tmp_path / "G").exists()


def test_a_crashing_ownership_lookup_refuses_instead_of_raising(roots: Roots) -> None:
    target = roots.control / "heartbeat.json"
    target.write_bytes(b"beat")

    def owned(path: Path) -> str | None:
        raise OSError("database is locked")

    result = commit_write(
        [FileWrite(target, b"beat 2", sha(b"beat"))], roots=roots, interlock=clear, owned=owned
    )

    assert not result.ok
    assert "who wrote" in result.reason
    assert target.read_bytes() == b"beat"


def test_a_folder_is_not_a_file_to_write(roots: Roots) -> None:
    folder = roots.control / "outbox"
    folder.mkdir()

    result = commit_write([FileWrite(folder, b"x", None)], roots=roots, interlock=clear)

    assert not result.ok
    assert "folder" in result.reason
    assert folder.is_dir()


# ----- the store's registry and its journal (integration spec section 3)

NOW = datetime(2026, 10, 30, 13, 30, tzinfo=timezone.utc)
V1, V2, LOG = b"items v1", b"items v2", b"log line"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def store(roots: Roots) -> Store:
    return open_store(roots.app_data / "ngrunner.db")


@pytest.fixture
def registry(store: Store, clock: FakeClock) -> StoreRegistry:
    return StoreRegistry(store, clock)


def journal(store: Store) -> dict[str, int]:
    return {state: len(list_pending_writes(store, state=state)) for state in JOURNAL_STATES}


def job_files(roots: Roots) -> tuple[Path, Path]:
    job = roots.ng_jobs / JOB
    return job / f"{JOB}_NGRunner.json", job / f"{JOB}_NGRunner_log.txt"


def test_with_a_registry_the_store_holds_the_record_and_no_json_is_kept(
    roots: Roots, store: Store, registry: StoreRegistry
) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"

    first = commit_write(
        [FileWrite(target, b"v1", None)], roots=roots, interlock=clear, registry=registry
    )
    second = commit_write(
        [FileWrite(target, b"v2", sha(b"v1"))], roots=roots, interlock=clear, registry=registry
    )

    assert first.ok and second.ok, (first.reason, second.reason)
    assert target.read_bytes() == b"v2"
    assert owned_sha(store, target) == sha(b"v2")
    assert not (roots.app_data / "owned_files.json").exists()
    assert journal(store) == {"pending": 0, "done": 2, "aborted": 0}


def test_with_a_registry_a_damaged_json_record_plays_no_part(
    roots: Roots, registry: StoreRegistry
) -> None:
    record = roots.app_data / "owned_files.json"
    record.write_bytes(b"{not json")
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"

    result = commit_write(
        [FileWrite(target, b"v1", None)], roots=roots, interlock=clear, registry=registry
    )

    assert result.ok, result.reason
    assert record.read_bytes() == b"{not json"


def test_with_a_registry_a_file_someone_else_wrote_is_still_not_overwritten(
    roots: Roots, registry: StoreRegistry
) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    target.parent.mkdir()
    target.write_bytes(b"Jim's copy")

    result = commit_write(
        [FileWrite(target, b"mine", sha(b"Jim's copy"))],
        roots=roots, interlock=clear, registry=registry,
    )

    assert not result.ok
    assert "not NG Runner's own" in result.reason
    assert target.read_bytes() == b"Jim's copy"


@pytest.mark.parametrize(
    ("make", "kept"),
    [
        (under(NG, JOB, f"{JOB}_summary.pdf"), True),
        (under(NG, JOB, LEASE), True),
        (under(NG, "_NG Runner", "settled.json"), True),
        (under(CTL, "shadow", JOB, f"{JOB}_NGRunner.json"), True),
        (under(CTL, "Shadow", JOB, f"{JOB}_summary.pdf"), True),
        (under(CTL, "heartbeat", "20261030", "1.json"), False),
        (under(CTL, "outbox", "1.json"), False),
        (under(CTL, "shadow.json"), False),
    ],
    ids=[
        "a job's summary", "a job's lease", "the settled list", "a shadow file",
        "a shadow file spelled in capitals", "a heartbeat", "an outbox file", "not in shadow",
    ],
)
def test_only_ng_jobs_and_shadow_paths_get_an_owned_row(
    roots: Roots, tmp_path: Path, store: Store, registry: StoreRegistry, make, kept: bool
) -> None:
    target = make(roots, tmp_path)

    result = commit_write(
        [FileWrite(target, b"x", None)], roots=roots, interlock=clear, registry=registry
    )

    assert result.ok, result.reason
    assert owned_sha(store, target) == (sha(b"x") if kept else None)
    assert journal(store) == {"pending": 0, "done": int(kept), "aborted": 0}


def test_a_commit_of_both_kinds_records_only_the_ones_that_get_a_row(
    roots: Roots, store: Store, registry: StoreRegistry
) -> None:
    heartbeat = roots.control / "heartbeat" / "1.json"
    shadow = roots.control / "shadow" / JOB / f"{JOB}_summary.pdf"

    result = commit_write(
        [FileWrite(heartbeat, b"beat", None), FileWrite(shadow, b"pdf", None)],
        roots=roots, interlock=clear, registry=registry,
    )

    assert result.ok, result.reason
    assert owned_sha(store, heartbeat) is None
    assert owned_sha(store, shadow) == sha(b"pdf")
    (row,) = list_pending_writes(store, state="done")
    assert row.paths == (shadow,) and row.hashes == (sha(b"pdf"),)


def test_with_a_registry_a_write_once_file_is_never_overwritten(
    roots: Roots, registry: StoreRegistry
) -> None:
    heartbeat = roots.control / "heartbeat.json"
    assert commit_write(
        [FileWrite(heartbeat, b"beat", None)], roots=roots, interlock=clear, registry=registry
    ).ok

    result = commit_write(
        [FileWrite(heartbeat, b"beat 2", sha(b"beat"))],
        roots=roots, interlock=clear, registry=registry,
    )

    assert not result.ok
    assert heartbeat.read_bytes() == b"beat"


@pytest.mark.parametrize("call", [1, 2], ids=["first safety check", "last safety check"])
def test_a_refused_commit_leaves_no_journal_row(
    roots: Roots, store: Store, registry: StoreRegistry, call: int
) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    calls = 0

    def interlock(paths: list[Path]) -> str | None:
        nonlocal calls
        calls += 1
        return "Mozaik is running" if calls == call else None

    result = commit_write(
        [FileWrite(target, b"x", None)], roots=roots, interlock=interlock, registry=registry
    )

    assert not result.ok
    assert journal(store) == {"pending": 0, "done": 0, "aborted": 0}


def test_the_journal_row_goes_in_before_the_first_rename(
    roots: Roots, store: Store, registry: StoreRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    items, log = job_files(roots)
    seen: list[list] = []
    watch_renames(monkeypatch, before=lambda src, dst: seen.append(list_pending_writes(store)))

    result = commit_write(
        [FileWrite(items, V1, None), FileWrite(log, LOG, None)],
        roots=roots, interlock=clear, registry=registry,
    )

    assert result.ok, result.reason
    (row,) = seen[0]
    assert row.paths == (items, log) and row.hashes == (sha(V1), sha(LOG))
    assert row.at == NOW and row.state == "pending"
    assert list_pending_writes(store) == []


def test_a_failed_rename_closes_its_journal_row_and_keeps_the_record(
    roots: Roots, store: Store, registry: StoreRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    items, log = job_files(roots)
    assert commit_write(
        [FileWrite(items, V1, None)], roots=roots, interlock=clear, registry=registry
    ).ok

    def fail_the_log(src: Path, dst: Path) -> None:
        if dst.name == log.name:
            raise PermissionError(13, "The file is in use by another program")

    watch_renames(monkeypatch, before=fail_the_log)

    result = commit_write(
        [FileWrite(items, V2, sha(V1)), FileWrite(log, LOG, None)],
        roots=roots, interlock=clear, registry=registry,
    )

    assert not result.ok
    assert items.read_bytes() == V1 and not log.exists()
    assert owned_sha(store, items) == sha(V1)
    assert journal(store) == {"pending": 0, "done": 1, "aborted": 1}


def test_a_journal_that_cannot_begin_refuses_before_any_rename(
    roots: Roots, store: Store, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Busy(StoreRegistry):
        def begin(self, planned):
            raise StoreError("NG Runner's database stayed busy for over 5 seconds.")

    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    renamed: list[Path] = []
    watch_renames(monkeypatch, before=lambda src, dst: renamed.append(dst))

    result = commit_write(
        [FileWrite(target, b"x", None)], roots=roots, interlock=clear, registry=Busy(store, clock)
    )

    assert not result.ok
    assert "could not note" in result.reason and "stayed busy" in result.reason
    assert renamed == []
    assert not (roots.ng_jobs / JOB).exists()


def test_a_record_that_fails_rolls_back_in_plain_words(
    roots: Roots, store: Store, clock: FakeClock, registry: StoreRegistry
) -> None:
    class Busy(StoreRegistry):
        def record(self, journal_id, written):
            raise StoreError("NG Runner's database stayed busy for over 5 seconds.")

    items, log = job_files(roots)
    assert commit_write(
        [FileWrite(items, V1, None)], roots=roots, interlock=clear, registry=registry
    ).ok

    result = commit_write(
        [FileWrite(items, V2, sha(V1)), FileWrite(log, LOG, None)],
        roots=roots, interlock=clear, registry=Busy(store, clock),
    )

    assert not result.ok
    assert "could not record" in result.reason and "stayed busy" in result.reason
    assert items.read_bytes() == V1 and not log.exists()
    assert owned_sha(store, items) == sha(V1) and owned_sha(store, log) is None
    assert journal(store) == {"pending": 0, "done": 1, "aborted": 1}


def test_a_failure_as_the_store_records_keeps_the_commit_when_the_record_went_in(
    roots: Roots, store: Store, clock: FakeClock
) -> None:
    class ClosesBadly(StoreRegistry):
        def record(self, journal_id, written):
            super().record(journal_id, written)
            raise StoreError("NG Runner's database could not be closed.")

    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"

    result = commit_write(
        [FileWrite(target, b"x", None)],
        roots=roots, interlock=clear, registry=ClosesBadly(store, clock),
    )

    assert result.ok, result.reason
    assert target.read_bytes() == b"x" and owned_sha(store, target) == sha(b"x")


def test_the_hashes_and_the_rows_end_state_change_together(
    store: Store, clock: FakeClock, registry: StoreRegistry, roots: Roots,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    row = registry.begin({target: sha(b"x")})

    def the_close_fails(*args) -> None:
        raise StoreError("NG Runner's database failed.")

    monkeypatch.setattr(write_journal, "_close", the_close_fails)
    with pytest.raises(StoreError):
        registry.record(row, {target: sha(b"x")})
    monkeypatch.undo()

    assert owned_sha(store, target) is None
    assert [r.id for r in list_pending_writes(store)] == [row]


def test_the_journal_arrives_by_migration_on_an_older_database(tmp_path: Path) -> None:
    path = tmp_path / "older.db"
    assert migrate(Store(path), MIGRATIONS[:2]) == 2

    upgraded = open_store(path)

    assert upgraded.schema_version() == LATEST
    assert list_pending_writes(upgraded) == []


@pytest.mark.parametrize(
    ("make", "why"), [case[1:] for case in FORBIDDEN], ids=[case[0] for case in FORBIDDEN]
)
def test_forbidden_writes_are_refused_with_a_registry_too(
    roots: Roots, tmp_path: Path, store: Store, registry: StoreRegistry, make, why: str
) -> None:
    before = tree(tmp_path)

    result = commit_write(
        [FileWrite(make(roots, tmp_path), b"x", None)],
        roots=roots, interlock=clear, registry=registry,
    )

    assert not result.ok
    assert why in result.reason
    assert tree(tmp_path) == before
    assert journal(store) == {"pending": 0, "done": 0, "aborted": 0}


# ----- a stop or a crash at each step, then the restart


class Crash(BaseException):
    """A stop chosen by the test: Ctrl+C or an exit, or, with no handlers run, the power going."""


STEPS = (
    "after the first rename",
    "before the second rename",
    "after the second rename",
    "in the read-back",
    "before the record",
    "after the record",
)
# What the two files hold after the restart, and whether the settle had a case to log.
AFTER_A_STOP = {  # a stop before the record step rolls back; from there on it can't know
    "after the first rename": (V1, None, False),
    "before the second rename": (V1, None, False),
    "after the second rename": (V1, None, False),
    "in the read-back": (V1, None, False),
    "before the record": (V2, LOG, True),
    "after the record": (V2, LOG, False),
}
AFTER_A_CRASH = {  # nothing rolls back: whatever was renamed in stays, and is adopted
    "after the first rename": (V2, None, True),
    "before the second rename": (V2, None, True),
    "after the second rename": (V2, LOG, True),
    "in the read-back": (V2, LOG, True),
    "before the record": (V2, LOG, True),
    "after the record": (V2, LOG, False),
}


def stop_at(
    step: str,
    monkeypatch: pytest.MonkeyPatch,
    store: Store,
    clock: FakeClock,
    items: Path,
    log: Path,
) -> StoreRegistry:
    """A registry for a commit that raises Crash at that step, once."""
    fired = []

    def fire(*args) -> None:
        if not fired:
            fired.append(True)
            raise Crash

    class Stopping(StoreRegistry):
        def record(self, journal_id, written):
            if step == "before the record":
                fire()
            super().record(journal_id, written)
            if step == "after the record":
                fire()

    if step == "after the first rename":
        watch_renames(monkeypatch, after=lambda src, dst: dst == items and fire())
    elif step == "before the second rename":
        watch_renames(monkeypatch, before=lambda src, dst: dst == log and fire())
    elif step == "after the second rename":
        watch_renames(monkeypatch, after=lambda src, dst: dst == log and fire())
    elif step == "in the read-back":
        monkeypatch.setattr(writes, "_read_back_all", fire)
    return Stopping(store, clock)


def dies_here(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process that dies runs no handlers: no rollback, no clean-up, no abort."""
    monkeypatch.setattr(writes, "_give_up", lambda *args: None)
    monkeypatch.setattr(writes, "_clean_up", lambda *args: None)


@pytest.mark.parametrize("hard", [False, True], ids=["a stop", "a crash"])
@pytest.mark.parametrize("step", STEPS)
def test_after_a_stop_at_any_step_the_restart_leaves_the_record_and_the_disk_agreeing(
    roots: Roots,
    store: Store,
    clock: FakeClock,
    registry: StoreRegistry,
    monkeypatch: pytest.MonkeyPatch,
    step: str,
    hard: bool,
) -> None:
    items, log = job_files(roots)
    assert commit_write(
        [FileWrite(items, V1, None)], roots=roots, interlock=clear, registry=registry
    ).ok
    stopping = stop_at(step, monkeypatch, store, clock, items, log)
    if hard:
        dies_here(monkeypatch)

    with pytest.raises(Crash):
        commit_write(
            [FileWrite(items, V2, sha(V1)), FileWrite(log, LOG, None)],
            roots=roots, interlock=clear, registry=stopping,
        )
    monkeypatch.undo()  # the restart: the real steps are back

    want_items, want_log, a_case = (AFTER_A_CRASH if hard else AFTER_A_STOP)[step]
    on_disk = tree(roots.ng_jobs)

    settle_pending_writes(store, at=clock.now())

    assert tree(roots.ng_jobs) == on_disk  # a settle adds nothing and deletes nothing
    assert items.read_bytes() == want_items
    assert (log.read_bytes() if log.exists() else None) == want_log
    assert owned_sha(store, items) == sha(want_items)
    assert owned_sha(store, log) == (sha(want_log) if want_log else None)
    assert list_pending_writes(store) == []
    assert [d.code for d in list_dev(store)] == (["write_settled"] if a_case else [])
    later = commit_write(
        [FileWrite(items, b"items v3", sha(want_items))],
        roots=roots, interlock=clear, registry=registry,
    )
    assert later.ok, later.reason  # nothing is stranded
    assert items.read_bytes() == b"items v3"


_DIES_MID_COMMIT = r"""
import os, sys
from pathlib import Path
from ngrunner.clock import SystemClock
from ngrunner.store import StoreRegistry, open_store
from ngrunner.writes import FileWrite, Roots, commit_write

base, items, log = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
roots = Roots(base / "NG Jobs", base / "Mozaik Shared", base / "control", base / "appdata")
registry = StoreRegistry(open_store(roots.app_data / "ngrunner.db"), SystemClock())
os.rename = lambda src, dst: os._exit(3)  # the log is new, so it goes in with os.rename
commit_write(
    [FileWrite(items, b"items v2", sys.argv[4]), FileWrite(log, b"log line", None)],
    roots=roots, interlock=lambda paths: None, registry=registry,
)
os._exit(0)
"""


def test_a_process_that_really_dies_between_two_renames_is_settled_at_the_next_start(
    roots: Roots, tmp_path: Path, store: Store, clock: FakeClock, registry: StoreRegistry
) -> None:
    items, log = job_files(roots)
    assert commit_write(
        [FileWrite(items, V1, None)], roots=roots, interlock=clear, registry=registry
    ).ok

    died = subprocess.run(
        [sys.executable, "-c", _DIES_MID_COMMIT, str(tmp_path), str(items), str(log), sha(V1)],
        cwd=REPO, capture_output=True, timeout=120,
    )

    assert died.returncode == 3, died.stderr
    assert items.read_bytes() == V2 and not log.exists()
    assert owned_sha(store, items) == sha(V1)  # the moment a stranded file would stay stranded

    settled = settle_pending_writes(store, at=clock.now())

    assert settled == Settled(adopted=(items,), left=(log,))
    assert owned_sha(store, items) == sha(V2)
    assert [p.name for p in leftovers(roots.ng_jobs)] != []  # its temp file is left, and reported
    later = commit_write(
        [FileWrite(items, b"items v3", sha(V2)), FileWrite(log, LOG, None)],
        roots=roots, interlock=clear, registry=registry,
    )
    assert later.ok, later.reason


# ----- the settle


def test_a_settle_adopts_only_a_path_holding_exactly_its_intended_hash(
    roots: Roots, store: Store, clock: FakeClock, registry: StoreRegistry
) -> None:
    job = roots.ng_jobs / JOB
    job.mkdir()
    same, changed, absent = job / f"{JOB}_summary.pdf", job / f"{JOB}_NGRunner.json", job / LEASE
    same.write_bytes(b"new")
    changed.write_bytes(b"someone else's")
    row = registry.begin({same: sha(b"new"), changed: sha(b"new"), absent: sha(b"new")})

    settled = settle_pending_writes(store, at=clock.now())

    assert settled == Settled(adopted=(same,), left=(changed, absent))
    assert owned_sha(store, same) == sha(b"new")
    assert owned_sha(store, changed) is None and owned_sha(store, absent) is None
    assert changed.read_bytes() == b"someone else's" and not absent.exists()
    assert [r.id for r in list_pending_writes(store, state="done")] == [row]
    (item,) = list_dev(store)
    assert item.code == "write_settled"
    for name in (same.name, changed.name, absent.name):
        assert name in item.text
    assert str(job) not in item.text  # file names, never a full path


def test_a_settle_that_adopts_nothing_closes_the_row_as_aborted(
    roots: Roots, store: Store, clock: FakeClock, registry: StoreRegistry
) -> None:
    (roots.ng_jobs / JOB).mkdir()
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    row = registry.begin({target: sha(b"new")})

    settled = settle_pending_writes(store, at=clock.now())

    assert settled == Settled(left=(target,))
    assert [r.id for r in list_pending_writes(store, state="aborted")] == [row]
    assert len(list_dev(store)) == 1


def test_a_file_someone_else_changed_after_a_crash_is_never_adopted(
    roots: Roots,
    store: Store,
    clock: FakeClock,
    registry: StoreRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items, log = job_files(roots)
    assert commit_write(
        [FileWrite(items, V1, None)], roots=roots, interlock=clear, registry=registry
    ).ok
    stopping = stop_at("after the second rename", monkeypatch, store, clock, items, log)
    dies_here(monkeypatch)
    with pytest.raises(Crash):
        commit_write(
            [FileWrite(items, V2, sha(V1)), FileWrite(log, LOG, None)],
            roots=roots, interlock=clear, registry=stopping,
        )
    monkeypatch.undo()
    items.write_bytes(b"edited by a chat session")

    settled = settle_pending_writes(store, at=clock.now())

    assert settled == Settled(adopted=(log,), left=(items,))
    assert owned_sha(store, items) == sha(V1)
    later = commit_write(
        [FileWrite(items, b"items v3", sha(b"edited by a chat session"))],
        roots=roots, interlock=clear, registry=registry,
    )
    assert not later.ok
    assert "not NG Runner's own" in later.reason
    assert items.read_bytes() == b"edited by a chat session"


def test_a_link_that_leads_to_the_intended_bytes_is_never_adopted(
    roots: Roots, tmp_path: Path, store: Store, clock: FakeClock, registry: StoreRegistry
) -> None:
    (roots.ng_jobs / JOB).mkdir()
    target, elsewhere = roots.ng_jobs / JOB / f"{JOB}_summary.pdf", tmp_path / "elsewhere.pdf"
    elsewhere.write_bytes(b"new")
    try:
        target.symlink_to(elsewhere)
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or admin rights")
    registry.begin({target: sha(b"new")})

    settled = settle_pending_writes(store, at=clock.now())

    assert settled == Settled(left=(target,))
    assert owned_sha(store, target) is None
    assert target.is_symlink() and elsewhere.read_bytes() == b"new"


def test_a_folder_at_the_path_is_left_alone(
    roots: Roots, store: Store, clock: FakeClock, registry: StoreRegistry
) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    target.mkdir(parents=True)
    registry.begin({target: sha(b"new")})

    settled = settle_pending_writes(store, at=clock.now())

    assert settled == Settled(left=(target,))
    assert target.is_dir()


def test_a_path_out_of_sight_keeps_its_row_pending_until_it_is_back(
    roots: Roots, tmp_path: Path, store: Store, clock: FakeClock, registry: StoreRegistry
) -> None:
    job = roots.ng_jobs / JOB
    job.mkdir()
    target = job / f"{JOB}_summary.pdf"
    other = roots.control / "shadow" / JOB / f"{JOB}_summary.pdf"
    target.write_bytes(b"new")
    other.parent.mkdir(parents=True)
    other.write_bytes(b"new")
    registry.begin({other: sha(b"new"), target: sha(b"new")})
    job.rename(tmp_path / "out of sight")

    first = settle_pending_writes(store, at=clock.now())

    assert first == Settled(adopted=(other,), waiting=(target,))
    assert owned_sha(store, other) == sha(b"new")  # no need to wait for the rest
    assert owned_sha(store, target) is None
    assert len(list_pending_writes(store)) == 1
    assert list_dev(store) == []

    (tmp_path / "out of sight").rename(job)
    second = settle_pending_writes(store, at=clock.now())

    assert second == Settled(adopted=(other, target))
    assert owned_sha(store, target) == sha(b"new")
    assert list_pending_writes(store) == []
    assert len(list_dev(store)) == 1


def test_a_drive_that_is_not_there_keeps_its_row_pending(
    store: Store, clock: FakeClock, registry: StoreRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = Path("Q:/NG Jobs") / JOB / f"{JOB}_summary.pdf"
    real_lstat = os.lstat

    def no_drive(path, **kwargs):
        if str(path).startswith("Q:"):
            raise OSError(21, "The device is not ready")
        return real_lstat(path, **kwargs)

    monkeypatch.setattr(os, "lstat", no_drive)
    registry.begin({target: sha(b"new")})

    settled = settle_pending_writes(store, at=clock.now())

    assert settled == Settled(waiting=(target,))
    assert len(list_pending_writes(store)) == 1


def test_a_file_held_open_by_a_sync_client_is_looked_at_again_later(
    roots: Roots, store: Store, clock: FakeClock, registry: StoreRegistry
) -> None:
    (roots.ng_jobs / JOB).mkdir()
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    target.write_bytes(b"new")
    registry.begin({target: sha(b"new")})
    held = _winapi.CreateFile(
        str(target), _winapi.GENERIC_READ, 0, _winapi.NULL, _winapi.OPEN_EXISTING, 0, _winapi.NULL
    )
    try:
        first = settle_pending_writes(store, at=clock.now())
    finally:
        _winapi.CloseHandle(held)

    assert first == Settled(waiting=(target,))
    assert settle_pending_writes(store, at=clock.now()) == Settled(adopted=(target,))


def test_a_settle_during_a_commit_leaves_that_commits_row_alone(
    roots: Roots,
    store: Store,
    clock: FakeClock,
    registry: StoreRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    seen: list[Settled] = []

    def settle_now(src: Path, dst: Path) -> None:
        seen.append(settle_pending_writes(store, at=clock.now()))

    watch_renames(monkeypatch, after=settle_now)

    result = commit_write(
        [FileWrite(target, b"x", None)], roots=roots, interlock=clear, registry=registry
    )

    assert result.ok, result.reason
    assert seen == [Settled(waiting=(target,))]
    assert list_dev(store) == []
    assert journal(store) == {"pending": 0, "done": 1, "aborted": 0}


def test_settling_again_changes_nothing(
    roots: Roots, store: Store, clock: FakeClock, registry: StoreRegistry
) -> None:
    (roots.ng_jobs / JOB).mkdir()
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    target.write_bytes(b"new")
    registry.begin({target: sha(b"new")})
    settle_pending_writes(store, at=clock.now())
    target.write_bytes(b"changed since")

    assert settle_pending_writes(store, at=clock.now()) == Settled()
    assert owned_sha(store, target) == sha(b"new")
    assert len(list_dev(store)) == 1


def test_a_settle_reads_files_so_it_is_refused_inside_a_transaction(
    store: Store, clock: FakeClock
) -> None:
    with store.transaction() as tx:
        with pytest.raises(StoreError):
            settle_pending_writes(tx, at=clock.now())


def test_aborting_a_closed_row_changes_nothing(
    roots: Roots, store: Store, registry: StoreRegistry
) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    row = registry.begin({target: sha(b"x")})
    registry.record(row, {target: sha(b"x")})

    registry.abort(row)

    assert journal(store) == {"pending": 0, "done": 1, "aborted": 0}


def test_a_journal_row_must_name_full_paths_and_sha256_hashes(
    registry: StoreRegistry, roots: Roots
) -> None:
    with pytest.raises(ValueError):
        registry.begin({})
    with pytest.raises(ValueError):
        registry.begin({Path("relative.pdf"): sha(b"x")})
    with pytest.raises(ValueError):
        registry.begin({roots.ng_jobs / "x.pdf": "not a hash"})


# ----- a restored file the rollback could not put back


def test_a_file_a_rollback_could_not_restore_stays_pending_for_the_settle(
    roots: Roots,
    store: Store,
    clock: FakeClock,
    registry: StoreRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items, log = job_files(roots)
    assert commit_write(
        [FileWrite(items, V1, None)], roots=roots, interlock=clear, registry=registry
    ).ok

    def fail_the_log(src: Path, dst: Path) -> None:
        if dst.name == log.name:
            raise PermissionError(13, "The file is in use by another program")

    def the_restore_fails(path: Path, content: bytes) -> None:
        raise PermissionError(13, "The file is in use by another program")

    watch_renames(monkeypatch, before=fail_the_log)
    monkeypatch.setattr(writes, "_replace_with", the_restore_fails)

    result = commit_write(
        [FileWrite(items, V2, sha(V1)), FileWrite(log, LOG, None)],
        roots=roots, interlock=clear, registry=registry,
    )

    assert not result.ok
    assert "could not be restored" in result.reason
    assert result.written == (items,)
    assert str(result.backups[0]) in result.reason
    assert result.backups[0].read_bytes() == V1
    assert items.read_bytes() == V2
    assert journal(store)["pending"] == 1
    monkeypatch.undo()

    assert settle_pending_writes(store, at=clock.now()) == Settled(adopted=(items,), left=(log,))
    assert owned_sha(store, items) == sha(V2)


def test_with_the_json_record_a_failed_restore_says_where_the_old_content_is(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, other = roots.control / "status.json", roots.control / "other.json"
    write_new(roots, status, b"s1")

    def fail_other(src: Path, dst: Path) -> None:
        if dst.name == other.name:
            raise PermissionError(13, "The file is in use by another program")

    def the_restore_fails(path: Path, content: bytes) -> None:
        raise PermissionError(13, "The file is in use by another program")

    watch_renames(monkeypatch, before=fail_other)
    monkeypatch.setattr(writes, "_replace_with", the_restore_fails)

    result = commit_write(
        [FileWrite(status, b"s2", sha(b"s1")), FileWrite(other, b"o", None)],
        roots=roots, interlock=clear,
    )

    assert not result.ok
    assert "could not be restored" in result.reason
    assert str(result.backups[0]) in result.reason
    assert result.written == (status,)
    assert status.read_bytes() == b"s2"
    assert result.backups[0].read_bytes() == b"s1"


# ----- W18: the JSON record and a stop as it is written


JSON_RECORD = "owned_files.json"


def test_a_stop_just_after_the_record_is_replaced_keeps_the_commit(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, other = roots.control / "status.json", roots.control / "other.json"
    write_new(roots, status, b"s1")

    def stop_at_the_record(src: Path, dst: Path) -> None:
        if dst.name == JSON_RECORD:
            raise KeyboardInterrupt

    watch_renames(monkeypatch, after=stop_at_the_record)

    with pytest.raises(KeyboardInterrupt):
        commit_write(
            [FileWrite(status, b"s2", sha(b"s1")), FileWrite(other, b"o", None)],
            roots=roots, interlock=clear,
        )
    monkeypatch.undo()

    assert status.read_bytes() == b"s2" and other.read_bytes() == b"o"
    assert leftovers(roots.control) == [] and leftovers(roots.app_data) == []
    later = commit_write([FileWrite(status, b"s3", sha(b"s2"))], roots=roots, interlock=clear)
    assert later.ok, later.reason  # the record and the disk agree


def test_a_stop_just_before_the_record_is_replaced_rolls_back(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, other = roots.control / "status.json", roots.control / "other.json"
    write_new(roots, status, b"s1")

    def stop_at_the_record(src: Path, dst: Path) -> None:
        if dst.name == JSON_RECORD:
            raise KeyboardInterrupt

    watch_renames(monkeypatch, before=stop_at_the_record)

    with pytest.raises(KeyboardInterrupt):
        commit_write(
            [FileWrite(status, b"s2", sha(b"s1")), FileWrite(other, b"o", None)],
            roots=roots, interlock=clear,
        )
    monkeypatch.undo()

    assert status.read_bytes() == b"s1" and not other.exists()
    later = commit_write([FileWrite(status, b"s3", sha(b"s1"))], roots=roots, interlock=clear)
    assert later.ok, later.reason


def test_a_failure_just_after_the_record_is_replaced_is_a_complete_commit(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    status = roots.control / "status.json"
    write_new(roots, status, b"s1")

    def fail_after_the_record(src: Path, dst: Path) -> None:
        if dst.name == JSON_RECORD:
            raise RuntimeError("something after the replace")

    watch_renames(monkeypatch, after=fail_after_the_record)

    result = commit_write([FileWrite(status, b"s2", sha(b"s1"))], roots=roots, interlock=clear)

    assert result.ok, result.reason
    assert status.read_bytes() == b"s2"


def test_a_record_that_cannot_be_written_rolls_the_commit_back(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, other = roots.control / "status.json", roots.control / "other.json"
    write_new(roots, status, b"s1")

    def fail_at_the_record(src: Path, dst: Path) -> None:
        if dst.name == JSON_RECORD:
            raise PermissionError(13, "The file is in use by another program")

    watch_renames(monkeypatch, before=fail_at_the_record)

    result = commit_write(
        [FileWrite(status, b"s2", sha(b"s1")), FileWrite(other, b"o", None)],
        roots=roots, interlock=clear,
    )

    assert not result.ok
    assert "could not record" in result.reason and "in use" in result.reason
    assert "rolled back" in result.reason
    assert status.read_bytes() == b"s1" and not other.exists()
    assert result.written == ()


# ----- W17: a stop just after a temp file or a folder is made


def test_a_stop_as_a_new_temp_file_is_identified_removes_it_then_stops(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_fstat = os.fstat
    stops = [KeyboardInterrupt]

    def stop_once(fd: int):
        st = real_fstat(fd)
        if stops:
            raise stops.pop()
        return st

    monkeypatch.setattr(os, "fstat", stop_once)

    with pytest.raises(KeyboardInterrupt):
        commit_write(
            [FileWrite(roots.control / "heartbeat.json", b"beat", None)],
            roots=roots, interlock=clear,
        )

    assert leftovers(roots.control) == []


def test_a_stop_just_after_a_new_folder_is_made_removes_it_then_stops(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_mkdir = os.mkdir

    def stop_after(path, *args, **kwargs) -> None:
        real_mkdir(path, *args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "mkdir", stop_after)

    with pytest.raises(KeyboardInterrupt):
        commit_write(
            [FileWrite(roots.ng_jobs / JOB / f"{JOB}_summary.pdf", b"x", None)],
            roots=roots, interlock=clear,
        )

    assert not (roots.ng_jobs / JOB).exists()


def test_a_stop_before_mkdir_makes_nothing_and_notes_nothing(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    def stop_before(path, *args, **kwargs) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "mkdir", stop_before)

    with pytest.raises(KeyboardInterrupt):
        commit_write(
            [FileWrite(roots.ng_jobs / JOB / f"{JOB}_summary.pdf", b"x", None)],
            roots=roots, interlock=clear,
        )

    assert not (roots.ng_jobs / JOB).exists()


def test_a_folder_that_cannot_be_made_is_a_plain_refusal(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(path, *args, **kwargs) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(os, "mkdir", denied)

    result = commit_write(
        [FileWrite(roots.ng_jobs / JOB / f"{JOB}_summary.pdf", b"x", None)],
        roots=roots, interlock=clear,
    )

    assert not result.ok
    assert "could not be prepared" in result.reason and "denied" in result.reason


# ----- W19, W26: a temp file changed before its rename


def only_temp(folder: Path) -> Path:
    (temp,) = leftovers(folder)
    return temp


def test_a_temp_file_rewritten_during_the_last_safety_check_stops_every_rename(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, other = roots.control / "status.json", roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    write_new(roots, status, b"s1")
    renamed: list[Path] = []
    calls = 0

    def interlock(paths: list[Path]) -> str | None:
        nonlocal calls
        calls += 1
        if calls == 2:  # in place: the same file, so its identity is unchanged
            only_temp(roots.ng_jobs).write_bytes(b"THEIRS")
        return None

    watch_renames(monkeypatch, before=lambda src, dst: renamed.append(dst))

    result = commit_write(
        [FileWrite(status, b"s2", sha(b"s1")), FileWrite(other, b"%PDF", None)],
        roots=roots, interlock=interlock,
    )

    assert not result.ok
    assert "temp file" in result.reason and "nothing was written" in result.reason
    assert renamed == []
    assert status.read_bytes() == b"s1" and not other.exists()
    assert result.written == ()
    assert leftovers(roots.ng_jobs) == [] and leftovers(roots.control) == []


def test_a_temp_file_replaced_by_another_file_is_not_renamed_in_and_not_removed(
    roots: Roots,
) -> None:
    status = roots.control / "status.json"
    calls = 0

    def interlock(paths: list[Path]) -> str | None:
        nonlocal calls
        calls += 1
        if calls == 2:  # the same bytes in another file: its identity gives it away
            temp = only_temp(roots.control)
            copy = roots.control / "a copy"
            copy.write_bytes(b"s2")
            os.replace(copy, temp)
        return None

    result = commit_write([FileWrite(status, b"s2", None)], roots=roots, interlock=interlock)

    assert not result.ok
    assert "temp file" in result.reason
    assert not status.exists()
    assert only_temp(roots.control).read_bytes() == b"s2"  # not its own, so never removed


def test_a_temp_file_that_vanishes_stops_the_commit(roots: Roots) -> None:
    status = roots.control / "status.json"
    calls = 0

    def interlock(paths: list[Path]) -> str | None:
        nonlocal calls
        calls += 1
        if calls == 2:
            only_temp(roots.control).unlink()
        return None

    result = commit_write([FileWrite(status, b"s2", None)], roots=roots, interlock=interlock)

    assert not result.ok
    assert "temp file" in result.reason
    assert not status.exists()


def test_a_file_edited_after_its_rename_is_reported_as_left_in_place(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = roots.ng_jobs / JOB / f"{JOB}_NGRunner_log.txt"

    def edit_in_place(src: Path, dst: Path) -> None:
        if dst.name == log.name:
            dst.write_bytes(b"edited")

    watch_renames(monkeypatch, after=edit_in_place)

    result = commit_write([FileWrite(log, b"log line", None)], roots=roots, interlock=clear)

    assert not result.ok
    assert "left in place" in result.reason
    assert log.read_bytes() == b"edited"
    assert result.written == ()


def test_a_file_replaced_after_its_rename_is_reported_as_left_as_found(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = roots.ng_jobs / JOB / f"{JOB}_NGRunner_log.txt"

    def replace_it(src: Path, dst: Path) -> None:
        if dst.name == log.name:
            copy = log.with_name("copy")
            copy.write_bytes(b"someone else's")
            real["replace"](copy, log)

    real = watch_renames(monkeypatch, after=replace_it)

    result = commit_write([FileWrite(log, b"log line", None)], roots=roots, interlock=clear)

    assert not result.ok
    assert "left as found" in result.reason and "left in place" not in result.reason
    assert log.read_bytes() == b"someone else's"


# ----- W20, W21: a swapped folder and a taken temp name


def test_a_folder_swapped_as_the_temp_file_is_created_gets_no_content(
    roots: Roots, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = roots.ng_jobs / JOB
    job.mkdir()
    target = job / f"{JOB}_summary.pdf"
    real_create = writes._create_new
    secret = b"a client's PDF"

    def swap_then_create(path: Path):
        move_behind_junction(job, library)  # just after the last check, just before CreateFile
        return real_create(path)

    monkeypatch.setattr(writes, "_create_new", swap_then_create)

    result = commit_write([FileWrite(target, secret, None)], roots=roots, interlock=clear)

    assert not result.ok
    assert "replaced by a link or junction" in result.reason
    for name in files_in(library):
        assert secret not in (library / name).read_bytes()
    assert leftovers(library) == []


@pytest.mark.parametrize(
    "leave", [leave_a_folder, leave_a_junction], ids=["a folder", "a junction"]
)
def test_a_folder_at_the_temp_name_just_means_another_name(
    roots: Roots, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, leave
) -> None:
    target = roots.control / "heartbeat.json"
    taken = roots.control / "heartbeat.json.deadbeef.ngr-tmp"
    still_there = leave(taken, tmp_path / "victim")
    known_temp_names(monkeypatch, first="deadbeef")

    result = commit_write([FileWrite(target, b"beat", None)], roots=roots, interlock=clear)

    assert result.ok, result.reason
    assert target.read_bytes() == b"beat"
    assert still_there()


def test_no_free_temp_name_is_a_plain_refusal(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = roots.control / "heartbeat.json"
    (roots.control / "heartbeat.json.deadbeef.ngr-tmp").write_bytes(b"leftover")
    monkeypatch.setattr(secrets, "token_hex", lambda nbytes=None: "deadbeef")

    result = commit_write([FileWrite(target, b"beat", None)], roots=roots, interlock=clear)

    assert not result.ok
    assert "no free temp name" in result.reason
    assert (roots.control / "heartbeat.json.deadbeef.ngr-tmp").read_bytes() == b"leftover"


def test_permission_denied_with_nothing_at_the_temp_name_is_a_plain_refusal(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(path: Path):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(writes, "_create_new", denied)

    result = commit_write(
        [FileWrite(roots.control / "heartbeat.json", b"beat", None)], roots=roots, interlock=clear
    )

    assert not result.ok
    assert "could not be prepared" in result.reason and "denied" in result.reason


# ----- W23, W24, W25: a long name, a root through a root, a record nested too deep


def test_a_file_name_too_long_for_its_temp_name_is_refused_in_plain_words(roots: Roots) -> None:
    name = "x" * 235 + ".pdf"  # 239 characters: valid on NTFS, but its temp name would not be

    result = commit_write(
        [FileWrite(roots.control / name, b"x", None)], roots=roots, interlock=clear
    )

    assert not result.ok
    assert "239 characters" in result.reason and "up to 238" in result.reason
    assert tree(roots.control) == set()


def test_a_file_name_of_238_characters_is_not_refused_for_its_length(roots: Roots) -> None:
    name = "x" * 234 + ".pdf"

    result = commit_write(
        [FileWrite(roots.control / name, b"x", None)], roots=roots, interlock=clear
    )

    assert "characters" not in (result.reason or "")


def test_a_root_set_through_a_link_inside_another_root_is_a_settings_problem(
    roots: Roots, tmp_path: Path
) -> None:
    real_place = tmp_path / "real control"
    real_place.mkdir()
    (roots.ng_jobs / "_NG Runner").mkdir()
    through = roots.ng_jobs / "_NG Runner" / "ctl"
    junction(real_place, through)
    spelled = replace(roots, control=through)

    result = commit_write(
        [FileWrite(through / "heartbeat.json", b"beat", None)], roots=spelled, interlock=clear
    )

    assert not result.ok
    assert "setting for the control folder" in result.reason and "NG Jobs" in result.reason
    assert "folder settings" in result.reason
    assert files_in(real_place) == []


def test_a_root_set_through_a_link_elsewhere_still_works(roots: Roots, tmp_path: Path) -> None:
    real_place = tmp_path / "real control"
    real_place.mkdir()
    (tmp_path / "links").mkdir()
    through = tmp_path / "links" / "ctl"
    junction(real_place, through)
    spelled = replace(roots, control=through)

    result = commit_write(
        [FileWrite(through / "heartbeat.json", b"beat", None)], roots=spelled, interlock=clear
    )

    assert result.ok, result.reason
    assert (real_place / "heartbeat.json").read_bytes() == b"beat"


def test_a_record_nested_too_deep_is_reported_as_damaged(roots: Roots) -> None:
    record = roots.app_data / "owned_files.json"
    record.write_text("[" * 200_000 + "]" * 200_000, encoding="utf-8")
    asked: list[list[Path]] = []

    result = commit_write(
        [FileWrite(roots.control / "heartbeat.json", b"beat", None)],
        roots=roots, interlock=lambda paths: asked.append(paths),
    )

    assert not result.ok
    assert "damaged" in result.reason
    assert asked == []


# ----- W26: the branches no test reached


def test_a_target_that_becomes_a_link_after_planning_is_refused(
    roots: Roots, library: Path
) -> None:
    status = roots.control / "status.json"
    write_new(roots, status, b"s1")
    calls = 0

    def interlock(paths: list[Path]) -> str | None:
        nonlocal calls
        calls += 1
        if calls == 2:
            status.unlink()
            junction(library, status)
        return None

    result = commit_write(
        [FileWrite(status, b"s2", sha(b"s1"))], roots=roots, interlock=interlock
    )

    assert not result.ok
    assert "link or junction" in result.reason
    assert files_in(library) == ["parameters.dat"]
    assert leftovers(roots.control) == []


def test_a_target_that_cannot_be_read_is_not_overwritten(roots: Roots) -> None:
    status = roots.control / "status.json"
    write_new(roots, status, b"s1")
    held = _winapi.CreateFile(
        str(status), _winapi.GENERIC_READ, 0, _winapi.NULL, _winapi.OPEN_EXISTING, 0, _winapi.NULL
    )
    try:
        result = commit_write(
            [FileWrite(status, b"s2", sha(b"s1"))], roots=roots, interlock=clear
        )
    finally:
        _winapi.CloseHandle(held)

    assert not result.ok
    assert "could not be read" in result.reason
    assert status.read_bytes() == b"s1"


def test_a_pinned_folder_that_is_moved_away_stops_the_commit(
    roots: Roots, tmp_path: Path
) -> None:
    job = roots.ng_jobs / JOB
    job.mkdir()
    calls = 0

    def interlock(paths: list[Path]) -> str | None:
        nonlocal calls
        calls += 1
        if calls == 2:
            job.rename(tmp_path / "moved away")
        return None

    result = commit_write(
        [FileWrite(job / f"{JOB}_summary.pdf", b"x", None)], roots=roots, interlock=interlock
    )

    assert not result.ok
    assert "moved or removed" in result.reason
    assert not job.exists()


def test_a_file_removed_after_its_rename_is_named_in_the_reason(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = roots.control / "first.json", roots.control / "second.json"

    def remove_then_fail(src: Path, dst: Path) -> None:
        if dst.name == second.name:
            first.unlink()
            raise PermissionError(13, "The file is in use by another program")

    watch_renames(monkeypatch, before=remove_then_fail)

    result = commit_write(
        [FileWrite(first, b"1", None), FileWrite(second, b"2", None)],
        roots=roots, interlock=clear,
    )

    assert not result.ok
    assert "first.json was moved or removed" in result.reason
    assert result.written == ()


def test_an_unexpected_failure_while_staging_is_a_plain_refusal(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args) -> None:
        raise RuntimeError("a bug")

    monkeypatch.setattr(writes, "_back_up", boom)
    target = roots.control / "heartbeat.json"

    result = commit_write([FileWrite(target, b"beat", None)], roots=roots, interlock=clear)

    assert not result.ok
    assert "Unexpected problem (RuntimeError)" in result.reason
    assert not target.exists() and leftovers(roots.control) == []


def test_an_unexpected_failure_after_the_renames_rolls_back(
    roots: Roots, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args) -> None:
        raise RuntimeError("a bug")

    monkeypatch.setattr(writes, "_read_back_all", boom)
    target = roots.control / "heartbeat.json"

    result = commit_write([FileWrite(target, b"beat", None)], roots=roots, interlock=clear)

    assert not result.ok
    assert "Unexpected problem (RuntimeError)" in result.reason and "rolled back" in result.reason
    assert not target.exists()


# ----- the clock


def test_a_backup_folder_is_named_from_the_clock_it_is_given(
    roots: Roots, clock: FakeClock
) -> None:
    status = roots.control / "status.json"
    write_new(roots, status, b"s1")

    result = commit_write(
        [FileWrite(status, b"s2", sha(b"s1"))], roots=roots, interlock=clear, clock=clock
    )

    assert result.ok, result.reason
    stamp = result.backups[0].relative_to(roots.app_data / "backup").parts[0]
    assert stamp == "20261030T133000000000Z"


def test_a_registry_brings_its_clock_for_the_backup_folder(
    roots: Roots, registry: StoreRegistry
) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    assert commit_write(
        [FileWrite(target, b"v1", None)], roots=roots, interlock=clear, registry=registry
    ).ok

    result = commit_write(
        [FileWrite(target, b"v2", sha(b"v1"))], roots=roots, interlock=clear, registry=registry
    )

    assert result.backups[0].relative_to(roots.app_data / "backup").parts[0].startswith("20261030")


def test_two_commits_in_one_instant_keep_their_own_backup_folders(
    roots: Roots, clock: FakeClock
) -> None:
    status = roots.control / "status.json"
    write_new(roots, status, b"s1")

    first = commit_write(
        [FileWrite(status, b"s2", sha(b"s1"))], roots=roots, interlock=clear, clock=clock
    )
    second = commit_write(
        [FileWrite(status, b"s3", sha(b"s2"))], roots=roots, interlock=clear, clock=clock
    )

    assert first.backups[0].read_bytes() == b"s1" and second.backups[0].read_bytes() == b"s2"
    assert second.backups[0].relative_to(roots.app_data / "backup").parts[0].endswith("-2")


def test_writes_never_reads_the_time_itself() -> None:
    assert "datetime.now" not in Path(writes.__file__).read_text(encoding="utf-8")


# ----- commit_recorded: what Ctx.commit, the messenger and the supervisor call


def test_commit_recorded_writes_records_and_logs_the_commit(
    roots: Roots, store: Store, clock: FakeClock
) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"

    result = commit_recorded(
        [FileWrite(target, b"v1", None)], store=store, clock=clock, roots=roots, interlock=clear
    )

    assert result.ok, result.reason
    assert owned_sha(store, target) == sha(b"v1")
    (row,) = list_write_records(store)
    assert row.ok and row.paths == (target,) and row.after == (sha(b"v1"),) and row.at == NOW


def test_commit_recorded_logs_a_refusal_too(
    roots: Roots, store: Store, clock: FakeClock
) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"

    result = commit_recorded(
        [FileWrite(target, b"v1", None)],
        store=store, clock=clock, roots=roots, interlock=lambda paths: "Mozaik is running",
    )

    assert not result.ok
    (row,) = list_write_records(store)
    assert not row.ok and "Mozaik is running" in row.reason


def test_commit_recorded_returns_the_refusal_even_when_the_row_cant_be_saved(
    roots: Roots, store: Store, clock: FakeClock
) -> None:
    result = commit_recorded(
        [FileWrite(Path("relative.pdf"), b"x", None)],
        store=store, clock=clock, roots=roots, interlock=clear,
    )

    assert not result.ok
    assert "full path" in result.reason
    assert list_write_records(store) == []


# ----- pruning (ruling 24)


JOB_ID = "1234"


def render_at(
    store: Store,
    roots: Roots,
    day: int,
    fingerprint: str | None,
    *,
    governing: bool = False,
    cache: Path | None = None,
) -> tuple[int, Path]:
    """A fetch of the job's summary, that many days after 1 October, with its cached file."""
    ensure_job(store, JOB_ID, JOB, now=NOW)
    folder = roots.app_data / "renders"
    folder.mkdir(exist_ok=True)
    path = cache or folder / f"{day}.pdf"
    path.write_bytes(b"%PDF")
    record = add_render(
        store, studio_id=JOB_ID, fetched_at=datetime(2026, 10, 1, tzinfo=timezone.utc)
        + timedelta(days=day), check_ok=True, fingerprint=fingerprint, cache_path=path,
    )
    if governing:
        set_governing(store, record.id)
    return record.id, path


def render_ids(store: Store) -> set[int]:
    return {r.id for r in list_renders(store, JOB_ID)}


def test_pruning_keeps_the_governing_render_and_the_latest_that_differs(
    roots: Roots, store: Store
) -> None:
    gov, gov_file = render_at(store, roots, 1, "a", governing=True)
    same, same_file = render_at(store, roots, 2, "a")
    old_diff, old_diff_file = render_at(store, roots, 3, "b")
    new_diff, new_diff_file = render_at(store, roots, 4, "c")
    newest, newest_file = render_at(store, roots, 5, "a")

    pruned = prune_renders(store, before=NOW)

    assert pruned == PrunedRenders(removed=3)
    assert render_ids(store) == {gov, new_diff}
    assert gov_file.exists() and new_diff_file.exists()
    assert not same_file.exists() and not old_diff_file.exists() and not newest_file.exists()


def test_pruning_with_no_governing_render_keeps_the_newest_and_the_latest_that_differs(
    roots: Roots, store: Store
) -> None:
    old, old_file = render_at(store, roots, 1, "a")
    diff, _ = render_at(store, roots, 2, "b")
    no_print, no_print_file = render_at(store, roots, 3, None)
    newest, _ = render_at(store, roots, 4, "a")

    assert prune_renders(store, before=NOW).removed == 2
    assert render_ids(store) == {diff, newest}
    assert not old_file.exists() and not no_print_file.exists()


def test_pruning_leaves_recent_renders_whatever_they_hold(roots: Roots, store: Store) -> None:
    gov, _ = render_at(store, roots, 1, "a", governing=True)
    recent, recent_file = render_at(store, roots, 20, "a")

    pruned = prune_renders(store, before=datetime(2026, 10, 15, tzinfo=timezone.utc))

    assert pruned == PrunedRenders()
    assert render_ids(store) == {gov, recent}
    assert recent_file.exists()


def test_pruning_never_removes_a_file_outside_the_data_folder(
    roots: Roots, tmp_path: Path, store: Store
) -> None:
    render_at(store, roots, 1, "a", governing=True)
    outside = tmp_path / "Jim's copy.pdf"
    stray, _ = render_at(store, roots, 2, "a", cache=outside)
    render_at(store, roots, 3, "a")

    pruned = prune_renders(store, before=NOW)

    assert outside.read_bytes() == b"%PDF"
    assert outside in pruned.left
    assert stray not in render_ids(store)


def test_pruning_keeps_the_row_of_a_file_in_use_and_tries_again_next_night(
    roots: Roots, store: Store
) -> None:
    render_at(store, roots, 1, "a", governing=True)
    same, same_file = render_at(store, roots, 2, "a")
    held = _winapi.CreateFile(
        str(same_file), _winapi.GENERIC_READ, 0, _winapi.NULL, _winapi.OPEN_EXISTING,
        0, _winapi.NULL,
    )
    try:
        pruned = prune_renders(store, before=NOW)
    finally:
        _winapi.CloseHandle(held)

    assert pruned == PrunedRenders(removed=0, left=(same_file,))
    assert same in render_ids(store)
    assert prune_renders(store, before=NOW) == PrunedRenders(removed=1)
    assert not same_file.exists()


def test_pruning_keeps_a_file_another_kept_render_still_uses(
    roots: Roots, store: Store
) -> None:
    gov, gov_file = render_at(store, roots, 1, "a", governing=True)
    same, _ = render_at(store, roots, 2, "a", cache=gov_file)

    assert prune_renders(store, before=NOW) == PrunedRenders(removed=1)
    assert render_ids(store) == {gov}
    assert gov_file.exists()


def test_pruning_a_render_with_no_cached_file_needs_no_file(
    roots: Roots, store: Store
) -> None:
    render_at(store, roots, 1, "a", governing=True)
    record = add_render(
        store, studio_id=JOB_ID, fetched_at=datetime(2026, 10, 3, tzinfo=timezone.utc),
        check_ok=False,
    )

    assert prune_renders(store, before=NOW).removed == 1
    assert record.id not in render_ids(store)


def test_pruning_old_journal_rows_keeps_every_pending_one(
    roots: Roots, store: Store, clock: FakeClock, registry: StoreRegistry
) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    closed, aborted = registry.begin({target: sha(b"x")}), registry.begin({target: sha(b"y")})
    registry.record(closed, {target: sha(b"x")})
    registry.abort(aborted)
    still_pending = registry.begin({target: sha(b"z")})

    prune_write_records(store, before=NOW + timedelta(days=30))

    assert [r.id for r in list_pending_writes(store)] == [still_pending]
    assert journal(store) == {"pending": 1, "done": 0, "aborted": 0}


def test_pruning_old_commit_rows_keeps_those_after_the_cutoff(store: Store, roots: Roots) -> None:
    write = FileWrite(roots.ng_jobs / JOB / f"{JOB}_summary.pdf", b"x", None)
    ok = CommitResult(ok=True)
    record_commit(store, [write], ok, at=NOW - timedelta(days=100))
    recent = record_commit(store, [write], ok, at=NOW - timedelta(days=1))

    assert prune_write_records(store, before=NOW - timedelta(days=30)) == 1
    assert [r.id for r in list_write_records(store)] == [recent.id]


def test_pruning_never_touches_the_owned_table(
    roots: Roots, store: Store, clock: FakeClock, registry: StoreRegistry
) -> None:
    target = roots.ng_jobs / JOB / f"{JOB}_summary.pdf"
    assert commit_write(
        [FileWrite(target, b"x", None)], roots=roots, interlock=clear, registry=registry
    ).ok

    prune_write_records(store, before=NOW + timedelta(days=1000))

    assert owned_sha(store, target) == sha(b"x")


def test_pruning_renders_is_refused_inside_a_transaction(store: Store) -> None:
    with store.transaction() as tx:
        with pytest.raises(StoreError):
            prune_renders(tx, before=NOW)
