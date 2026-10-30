"""The canary (integration spec section 7), on tmp_path folders only: every check passes on a
plain folder, a failure shows, it refuses NG Jobs and Mozaik Shared, and it removes only what it
made, checked by identity."""
from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from ngrunner import canary
from ngrunner.canary import run_canary


@pytest.fixture
def place(tmp_path: Path) -> Path:
    folder = tmp_path / "Sulaiman Testing"
    folder.mkdir()
    return folder


def _run(place: Path, tmp_path: Path, **kwargs) -> tuple[int, str]:
    out = io.StringIO()
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    kwargs.setdefault("sleep", lambda s: None)
    code = run_canary(place, out=out, scratch=scratch, **kwargs)
    return code, out.getvalue()


def test_every_check_passes_on_a_plain_folder_and_all_it_made_is_gone(
        place: Path, tmp_path: Path) -> None:
    (place / "not the canary's.txt").write_text("stay", encoding="utf-8")
    code, text = _run(place, tmp_path)
    assert code == 0, text
    assert "FAIL" not in text and "All " in text
    assert [p.name for p in place.iterdir()] == ["not the canary's.txt"]
    assert (place / "not the canary's.txt").read_text(encoding="utf-8") == "stay"
    assert list((tmp_path / "scratch").iterdir()) == []


@pytest.mark.parametrize("line", [
    "The no-follow create makes a new file",
    "The no-follow create refuses a name already there",
    "fstat and lstat give the same identity while the file is open",
    "A rename keeps the identity",
    "fsync works",
    "A rename refuses a target already there",
    "A replace puts the new file in, with its identity",
    "Read-back gives the bytes written",
    "File and folder IDs are the same 60 seconds later",
    "commit_write files two new files",
    "commit_write overwrites its own file, with a backup",
    "No temp file is left behind",
    "A forced failure rolls the whole commit back",
    "Everything the canary made was removed again",
])
def test_each_check_says_so_in_one_plain_line(place: Path, tmp_path: Path, line: str) -> None:
    _, text = _run(place, tmp_path)
    assert f"PASS  {line}\n" in text


def test_the_ids_are_read_again_while_the_drive_syncs(place: Path, tmp_path: Path) -> None:
    waits: list[float] = []
    _run(place, tmp_path, sleep=waits.append)
    assert sum(waits) == 60


def test_os_rename_is_put_back_after_the_forced_failure(place: Path, tmp_path: Path) -> None:
    before = os.rename
    _run(place, tmp_path)
    assert os.rename is before


# ----- a failure shows, and the exit code says so


def test_a_check_that_fails_gives_exit_1_and_everything_is_still_removed(
        place: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_fsync(fd: int) -> None:
        raise OSError(5, "fsync is not supported here")

    monkeypatch.setattr(canary.os, "fsync", no_fsync)
    code, text = _run(place, tmp_path)
    assert code == 1
    assert "FAIL  fsync works: fsync is not supported here" in text
    assert "must not file on this drive" in text
    assert list(place.iterdir()) == []


def test_an_identity_that_changes_while_it_is_open_fails(
        place: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = os.fstat

    def other_identity(fd: int):
        st = real(fd)
        fields = list(st)
        fields[1] = st.st_ino + 1  # st_ino
        return os.stat_result(fields)

    monkeypatch.setattr(canary.os, "fstat", other_identity)
    code, text = _run(place, tmp_path)
    assert code == 1
    assert "FAIL  fstat and lstat give the same identity while the file is open" in text


def test_a_check_that_blows_up_still_runs_the_next_and_cleans_up(
        place: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_replace(src, dst) -> None:
        raise OSError(5, "replace is not supported here")

    monkeypatch.setattr(canary.os, "replace", no_replace)
    code, text = _run(place, tmp_path)
    assert code == 1
    assert "FAIL  The drive's file checks finished: replace is not supported here" in text
    assert "commit_write files two new files" in text  # the next step still ran
    assert list(place.iterdir()) == []


def test_a_commit_that_is_not_rolled_back_fails_the_check(
        place: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_commit = canary.commit_write
    done = []

    def no_rollback(items, **kwargs):
        done.append(1)
        if len(done) == 3:  # the forced failure: pretend that it went through
            return canary.writes.CommitResult(ok=True)
        return real_commit(items, **kwargs)

    monkeypatch.setattr(canary, "commit_write", no_rollback)
    code, text = _run(place, tmp_path)
    assert code == 1 and "FAIL  A forced failure rolls the whole commit back" in text


# ----- what it refuses


@pytest.mark.parametrize("name", [
    "NG Jobs", "ng jobs", "NG_Jobs", "NG-Jobs", "NGJobs", "NG Jobs (1)", "Mozaik Shared",
    "MOZAIK SHARED", "Mozaik_Shared", "Copy of Mozaik Shared",
])
@pytest.mark.parametrize("deeper", [False, True])
def test_it_refuses_a_folder_named_like_or_inside_ng_jobs_or_mozaik_shared(
        tmp_path: Path, name: str, deeper: bool) -> None:
    folder = tmp_path / name
    (folder / "Testerson_101426").mkdir(parents=True)
    target = folder / "Testerson_101426" if deeper else folder
    before = sorted(p.name for p in target.iterdir())
    code, text = _run(target, tmp_path)
    assert code == 2
    assert "never runs there" in text
    assert sorted(p.name for p in target.iterdir()) == before
    assert "PASS" not in text


def test_a_word_that_only_ends_like_ng_is_not_ng_jobs(tmp_path: Path) -> None:
    folder = tmp_path / "Testing Jobs"
    folder.mkdir()
    assert _run(folder, tmp_path)[0] == 0


def test_it_refuses_the_folders_the_settings_name_by_identity_whatever_they_are_called(
        place: Path, tmp_path: Path) -> None:
    jobs = tmp_path / "Filed work"
    (jobs / "Testerson_101426").mkdir(parents=True)
    code, text = _run(jobs / "Testerson_101426", tmp_path, refuse=[jobs])
    assert code == 2 and "never runs there" in text
    code, _ = _run(place, tmp_path, refuse=[jobs])
    assert code == 0


def test_it_refuses_a_junction_into_ng_jobs(tmp_path: Path) -> None:
    import _winapi

    jobs = tmp_path / "NG Jobs"
    jobs.mkdir()
    link = tmp_path / "harmless"
    _winapi.CreateJunction(str(jobs), str(link))
    try:
        code, text = _run(link, tmp_path)
    finally:
        os.rmdir(link)
    assert code == 2 and "never runs there" in text
    assert list(jobs.iterdir()) == []


def test_it_refuses_a_relative_path_and_a_missing_folder(tmp_path: Path) -> None:
    assert _run(Path("relative"), tmp_path)[0] == 2
    code, text = _run(tmp_path / "nowhere", tmp_path)
    assert code == 2 and "There is no folder" in text


# ----- it removes only what it made


def _during_the_run(place: Path, act):
    """A sleep that does something to the test folder on its first call, mid-run."""
    calls = []

    def sleep(seconds: float) -> None:
        calls.append(seconds)
        if len(calls) == 1:
            act(next(place.glob("ngrunner-canary-*")))

    return sleep


def test_a_file_someone_else_drops_in_stays_and_fails_the_last_check(
        place: Path, tmp_path: Path) -> None:
    def drop(run: Path) -> None:
        (run / "files" / "desktop.ini").write_bytes(b"not the canary's")

    code, text = _run(place, tmp_path, sleep=_during_the_run(place, drop))
    assert code == 1
    assert "FAIL  Everything the canary made was removed again" in text
    run = next(place.glob("ngrunner-canary-*"))
    assert (run / "files" / "desktop.ini").read_bytes() == b"not the canary's"
    assert [p.name for p in (run / "files").iterdir()] == ["desktop.ini"]


def test_a_file_swapped_under_its_name_is_not_the_one_it_made_and_stays(
        place: Path, tmp_path: Path) -> None:
    def swap(run: Path) -> None:
        final = run / "files" / "a.txt"
        final.unlink()
        final.write_bytes(b"not the canary's")

    code, text = _run(place, tmp_path, sleep=_during_the_run(place, swap))
    assert code == 1
    assert "a.txt (it is not what the canary made)" in text
    run = next(place.glob("ngrunner-canary-*"))
    assert (run / "files" / "a.txt").read_bytes() == b"not the canary's"


def test_its_own_junction_goes_as_a_link_and_is_only_a_note(place: Path, tmp_path: Path) -> None:
    code, text = _run(place, tmp_path)
    assert code == 0, text
    assert "NOTE  A junction can be made here" in text


def test_with_no_scratch_it_uses_the_system_temp_folder_and_removes_it(
        place: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(canary.tempfile, "tempdir", str(temp))
    out = io.StringIO()
    assert run_canary(place, out=out, sleep=lambda s: None) == 0, out.getvalue()
    assert list(temp.iterdir()) == []
