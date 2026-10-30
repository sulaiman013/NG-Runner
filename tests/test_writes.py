"""
Tests for the write chokepoint, commit_write() (design section 3.2), through its public
interface only. Every root is a fake folder under tmp_path; real G: and OneDrive are never
touched. Job names are synthetic.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

import ngrunner.writes as writes
from ngrunner.writes import FileWrite, Roots, commit_write

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
    assert "left as found" in result.reason
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
