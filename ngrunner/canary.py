r"""
The canary: does this drive behave the way commit_write relies on? (integration spec section 7;
AGENTS.md: Before filing goes live, item 3.)

Why: every commit knows its files by identity (volume serial and file ID), creates them with a
no-follow exclusive create, and renames them into place. A sync client can break any of that, and
then every commit refuses, or worse. So before anything is filed on a drive this proves it there,
one plain line per check, in a new test folder inside the folder it is given, and runs commit_write
itself end to end, with that test folder as its control root. Exit code 0 only when every check
passes.

It removes only what it made. Everything it creates goes on a list with its identity, and at the end
each one is removed only while it is still that very file or folder; this is a dev tool, not the
chokepoint, so it has its own small list. Anything else found there stays where it is, is named,
and fails the last check. It never runs inside NG Jobs or Mozaik Shared, and since it runs before
settings exist, it refuses any folder named like them too.
"""
from __future__ import annotations

import _winapi
import hashlib
import msvcrt
import os
import re
import secrets
import stat
import tempfile
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TextIO

from ngrunner import writes
from ngrunner.clock import SystemClock
from ngrunner.writes import FileWrite, Roots, commit_write

REFUSED = 2                  # nothing ran: the folder is wrong (the command line's exit code)
FAILED = 1
WAITS = (5.0, 25.0, 30.0)    # the IDs are read again 5, 30 and 60 seconds on, while Drive syncs
JOB = "Testerson_101426"     # synthetic names only
OTHER_JOB = "Fakeley_101426"
# Any spelling of the two names, as words of their own: "Testing Jobs" is not "NG Jobs".
_NAMED_LIKE = re.compile(r"(?<![a-z0-9])(?:ng[^a-z0-9]*jobs|mozaik[^a-z0-9]*shared)")
_GENERIC_WRITE = _winapi.GENERIC_WRITE   # writes.py's no-follow exclusive create, flag for flag
_CREATE_NEW = 1
_OPEN_REPARSE_POINT = 0x00200000
_ATTRIBUTE_NORMAL = 0x80
_Identity = tuple[int, int]


def run_canary(folder: Path, *, out: TextIO, refuse: Iterable[Path] = (),
               scratch: Path | None = None, sleep: Callable[[float], None] = time.sleep) -> int:
    """Run every check in a new test folder inside folder, and remove what it made. out gets one
    plain line per check. refuse: more folders it must not run inside (the settings' NG Jobs and
    Mozaik Shared, when there are settings). scratch: where its local folder goes (the system's
    temp folder by default; commit_write needs a data folder apart from the tested one). 0 only
    when every check passes; 1 when one failed; 2 when the folder was refused and nothing ran."""
    folder = Path(folder)
    report = _Report(out)
    reason = _refusal(folder, refuse)
    if reason:
        report.say(reason)
        return REFUSED
    made = _Made()
    stamp = SystemClock().now().strftime("%Y%m%dT%H%M%SZ")
    run = folder / f"ngrunner-canary-{stamp}-{secrets.token_hex(4)}"
    try:
        os.mkdir(run)
        made.note(run)
        local = Path(tempfile.mkdtemp(prefix="ngr-canary-", dir=scratch))
    except OSError as exc:
        report.say(f"The test folder could not be made ({_why(exc)}), so nothing was checked.")
        made.remove_all()
        return FAILED
    report.say(f"Testing in {run}")
    try:
        _guarded(report, "The drive's file checks", lambda: _file_checks(run, made, report, sleep))
        _guarded(report, "commit_write's checks", lambda: _commit_checks(run, local, made, report))
    finally:
        _clean(made, local, run, report)
    return report.finish()


# ----- what it refuses


def _refusal(folder: Path, refuse: Iterable[Path]) -> str | None:
    if not folder.is_absolute():
        return f"The canary needs a full path with a drive, and {folder} is not one."
    if not folder.is_dir():
        return f"There is no folder at {folder}."
    real = Path(os.path.realpath(folder))
    named_like = any(_NAMED_LIKE.search(part.casefold())
                     for spelled in (folder, real) for part in spelled.parts)
    if named_like or any(_inside(real, Path(root)) for root in refuse):
        return (f"{folder} is NG Jobs or Mozaik Shared, inside one of them, or named like them. The"
                " canary never runs there, so nothing was written. Give it a test folder of your"
                " own.")
    return None


def _inside(path: Path, root: Path) -> bool:
    return any(_same(place, root) for place in (path, *path.parents))


def _same(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(a) == os.path.normcase(b)  # a root out of reach right now


# ----- the checks


class _Report:
    def __init__(self, out: TextIO) -> None:
        self._out = out
        self.passed = self.failed = 0

    def say(self, line: str) -> None:
        self._out.write(line + "\n")

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.passed += bool(ok)
        self.failed += not ok
        self.say(f"{'PASS' if ok else 'FAIL'}  {name}{f': {detail}' if detail and not ok else ''}")
        return bool(ok)

    def finish(self) -> int:
        total = self.passed + self.failed
        if self.failed:
            self.say(f"{self.failed} of {total} checks failed. NG Runner must not file on this"
                     " drive until they pass.")
            return FAILED
        self.say(f"All {total} checks passed.")
        return 0


def _guarded(report: _Report, name: str, step: Callable[[], None]) -> None:
    """A step that fails half-way is a failed check; the next step and the clean-up still run."""
    try:
        step()
    except Exception as exc:  # noqa: BLE001 - any failure of a check is that check's result
        report.check(f"{name} finished", False, _why(exc))


def _file_checks(run: Path, made: _Made, report: _Report, sleep: Callable[[float], None]) -> None:
    """The file-system facts writes.py relies on, one by one."""
    folder = run / "files"
    os.mkdir(folder)
    made.note(folder)
    folder_id = _identity(folder)
    report.check("The test folder has a file ID", all(folder_id))
    temp = folder / f"a.txt.{secrets.token_hex(4)}.ngr-tmp"
    with _create_new(temp) as file:
        made.note(temp)
        report.check("The no-follow create makes a new file", True)
        opened = os.fstat(file.fileno())
        seen = os.lstat(temp)
        same = (opened.st_dev, opened.st_ino) == (seen.st_dev, seen.st_ino)
        report.check("fstat and lstat give the same identity while the file is open",
                     same and all((opened.st_dev, opened.st_ino)))
        report.check("The new file is a plain file, not a link", _is_plain(seen))
        file.write(b"canary " * 200)
        file.flush()
        try:
            os.fsync(file.fileno())
            report.check("fsync works", True)
        except OSError as exc:
            report.check("fsync works", False, _why(exc))
    temp_id = _identity(temp)
    report.check("The identity holds once the file is closed",
                 temp_id == (opened.st_dev, opened.st_ino))
    report.check("The no-follow create refuses a name already there", _create_refused(temp))
    final = folder / "a.txt"
    os.rename(temp, final)
    made.moved(temp, final)
    report.check("A rename keeps the identity", _identity(final) == temp_id)
    final_id = _replace_checks(folder, final, made, report)
    for wait, after in zip(WAITS, (5, 30, 60)):
        sleep(wait)
        report.check(f"File and folder IDs are the same {after} seconds later",
                     _identity(final) == final_id and _identity(folder) == folder_id)
    _junction_note(run, folder, made, report)


def _replace_checks(folder: Path, final: Path, made: _Made, report: _Report) -> _Identity:
    second = folder / f"a.txt.{secrets.token_hex(4)}.ngr-tmp"
    with _create_new(second) as file:
        made.note(second)
        file.write(b"second version")
    second_id = _identity(second)
    try:
        os.rename(second, final)
        made.moved(second, final)
        report.check("A rename refuses a target already there", False, "it replaced the file")
        return _identity(final)
    except FileExistsError:
        report.check("A rename refuses a target already there", True)
    os.replace(second, final)
    made.moved(second, final)
    report.check("A replace puts the new file in, with its identity",
                 _identity(final) == second_id)
    report.check("Read-back gives the bytes written", final.read_bytes() == b"second version")
    return second_id


def _create_new(path: Path):
    handle = _winapi.CreateFile(str(path), _GENERIC_WRITE, 0, 0, _CREATE_NEW,
                                _OPEN_REPARSE_POINT | _ATTRIBUTE_NORMAL, 0)
    return open(msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY), "wb")


def _create_refused(path: Path) -> bool:
    try:
        _create_new(path).close()
    except FileExistsError:
        return True
    return False


def _junction_note(run: Path, target: Path, made: _Made, report: _Report) -> None:
    """Information only: commit_write refuses links wherever they appear. Drive refuses to make
    them at all."""
    junction = run / "link"
    try:
        _winapi.CreateJunction(str(target), str(junction))
    except OSError:
        if junction.is_dir():      # CreateJunction makes the folder first, and on Drive it stays
            made.note(junction)
        report.say("NOTE  A junction can't be made here, so none can appear in a job folder.")
        return
    made.note(junction)
    report.say("NOTE  A junction can be made here; commit_write refuses to follow one.")


def _commit_checks(run: Path, local: Path, made: _Made, report: _Report) -> None:
    """commit_write end to end, with the test folder as its control root. The other roots stay
    local and out of the way: NG Jobs and Mozaik Shared are not touched, and need not exist."""
    (local / "app_data").mkdir()
    roots = Roots(local / "NG Jobs", local / "Mozaik Shared", run, local / "app_data")
    job = run / "shadow" / JOB
    summary, record = job / f"{JOB}_summary.pdf", job / f"{JOB}_NGRunner.json"
    pdf = b"%PDF-1.7 canary\n%%EOF\n"
    result = commit_write([FileWrite(summary, pdf, None), FileWrite(record, b'{"v": 1}', None)],
                          roots=roots, interlock=_clear)
    made.note_commit(run, result.written)
    report.check("commit_write files two new files",
                 result.ok and summary.read_bytes() == pdf, result.reason or "")
    first = hashlib.sha256(b'{"v": 1}').hexdigest()
    result = commit_write([FileWrite(record, b'{"v": 2}', first)], roots=roots, interlock=_clear)
    made.note_commit(run, result.written)
    report.check("commit_write overwrites its own file, with a backup",
                 result.ok and record.read_bytes() == b'{"v": 2}' and len(result.backups) == 1,
                 result.reason or "")
    leftovers = [p.name for p in job.iterdir() if p.name.endswith(".ngr-tmp")]
    report.check("No temp file is left behind", not leftovers, ", ".join(leftovers))
    _rollback_check(run, roots, job, made, report)


def _rollback_check(run: Path, roots: Roots, job: Path, made: _Made, report: _Report) -> None:
    """commit_write has no seam for a rollback, so the failure comes from wrapping os.rename in
    this process, as tests/test_writes.py does: the second rename of a commit fails."""
    log = job / f"{JOB}_NGRunner_log.txt"
    other = run / "shadow" / OTHER_JOB / f"{OTHER_JOB}_NGRunner.json"
    real_rename, calls = os.rename, [0]

    def fail_the_second(src, dst, **kwargs) -> None:
        calls[0] += 1
        if calls[0] == 2:
            raise OSError(5, "failure made by the canary")
        real_rename(src, dst, **kwargs)

    writes.os.rename = fail_the_second
    try:
        result = commit_write([FileWrite(log, b"one", None), FileWrite(other, b"two", None)],
                              roots=roots, interlock=_clear)
    finally:
        writes.os.rename = real_rename
    made.note_commit(run, result.written)
    clean = not any(p.name.endswith(".ngr-tmp") for p in job.iterdir())
    report.check("A forced failure rolls the whole commit back",
                 not result.ok and not log.exists() and not other.exists() and clean
                 and not other.parent.exists(), result.reason or "the commit went through")


def _clear(paths: list[Path]) -> None:
    """The canary's interlock: its targets are its own new test folder, with no Mozaik in sight."""
    return None


# ----- removing what it made


DRIVES_NOTE = "desktop.ini"


class _Made:
    """Everything the canary made, with its identity, in the order made."""

    def __init__(self) -> None:
        self._items: dict[Path, _Identity] = {}

    def note(self, path: Path) -> None:
        self._items[path] = _identity(path)

    def moved(self, old: Path, new: Path) -> None:
        self._items.pop(old, None)
        self.note(new)

    def note_commit(self, run: Path, written: Iterable[Path]) -> None:
        """What a commit reports as written, with the folders it made on the way, outermost
        first. Its paths are spelled from the resolved root, so the root is found by identity."""
        for path in written:
            chain = [path]
            while not _same(chain[-1].parent, run) and chain[-1].parent != chain[-1]:
                chain.append(chain[-1].parent)
            for place in reversed(chain):
                self.note(place)

    def remove_all(self) -> tuple[int, list[str]]:
        """Remove each item while it is still that very file or folder, newest first. Returns how
        many went, and what stayed and why. What was never on the list is never touched, so it
        also keeps its folder in place."""
        removed, kept = 0, []
        for path, identity in reversed(list(self._items.items())):
            try:
                st = os.lstat(path)
                if (st.st_dev, st.st_ino) != identity:
                    kept.append(f"{path.name} (it is not what the canary made)")
                    continue
                if stat.S_ISDIR(st.st_mode):
                    _remove_drives_note(path)
                    os.rmdir(path)
                else:
                    os.unlink(path)
                removed += 1
            except FileNotFoundError:
                continue
            except OSError as exc:
                kept.append(f"{path.name} ({_why(exc)})")
        return removed, kept


def _remove_drives_note(folder: Path) -> None:
    """Drive for desktop adds a hidden desktop.ini to a folder a little while after it is made
    (measured 2026-10-30), and os.rmdir then refuses. It is Drive's own note on a folder the canary
    made, so it goes with the folder. Anything else in there still stops the removal."""
    note = folder / DRIVES_NOTE
    try:
        if stat.S_ISREG(os.lstat(note).st_mode):
            os.unlink(note)
    except OSError:
        pass  # none there, or it can't go: then os.rmdir says what stayed


def _clean(made: _Made, local: Path, run: Path, report: _Report) -> None:
    from ngrunner.backup import remove_own_tree  # here: only this step needs the backup module

    removed, kept = made.remove_all()
    left = remove_own_tree(local, inside=local.parent)
    if left:
        kept.append(left)
    report.check("Everything the canary made was removed again", not kept and not run.exists(),
                 "left in place: " + "; ".join(kept) if kept else f"{run.name} is not empty")


# ----- small pieces


def _identity(path: Path) -> _Identity:
    st = os.lstat(path)
    return (st.st_dev, st.st_ino)


def _is_plain(st: os.stat_result) -> bool:
    attributes = getattr(st, "st_file_attributes", 0)
    return stat.S_ISREG(st.st_mode) and not attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT


def _why(exc: BaseException) -> str:
    return getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
