r"""
The harness for Jim's toolkit: a hash-pinned copy of it, and running a script of that copy (P1b build
spec 5.13, design 6).

Why a copy: the checks must run from what passed its own regression, never from the folder Jim is
changing, so a verdict can say which scripts stood behind it. Nothing here adopts a copy: that needs
Jim's regression and baseline (group 3). So no snapshot is taken on a schedule yet.

Why a Job Object: these are Jim's scripts, written for a chat's container, and a first Windows run is
itself a test (risk E4). One that hangs must cost a timeout and not a stuck worker, and what it started
must go with it. The process starts suspended, goes into the job and only then runs a line, so even a
first line that starts a child can't slip out. Closing the job's last handle kills whatever is left,
so a crash of NG Runner takes them too.

The build has no Python, so NGRunner.exe runs a script by itself, with `run-tool` and runpy, in the UTF-8
mode the build carries (ngrunner/__main__.py). From the repo it is sys.executable -X utf8. Any module a
script imports must be in the build. Read from the toolkit copy of 2026-10-31: sheet_size_check,
br_cam_check and units need only sys, re, os and glob, and des_cutlist re, sys, os and collections, all
in the build. openpyxl, numpy and others that builders import are not, and one that isn't there fails
that run with an ImportError, which is could_not_verify.

What a tool's output means is in guards/tool_checks.py, read from its source and not seen running, and
READERS names the two that source pins exactly. tool_verdict fails closed around them: no reader, a run
that never finished, a reader that breaks or a code it doesn't know are all could_not_verify. Nothing
calls any of this yet: watch.nest_checks is None until a copy can be adopted (group 3), and with no
design match NEST_CHECKED stays out of reach.

Checks run on staged copies (stage_files) in a folder of their own, and never in place. Only ever read:
the toolkit's own folder and what is staged. Everything written is in NG Runner's data folder.
"""
from __future__ import annotations

import glob
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psutil
import win32job

from ngrunner.clock import Clock
from ngrunner.guards.facts import (
    BR_CAM_NEST, COULD_NOT_VERIFY, FAILED, PASSED, SHEET_SIZE, CheckedFiles, ToolRun, ToolVerdict,
)
from ngrunner.guards.reads import Unreadable, read_shared
from ngrunner.guards.tool_checks import br_cam_nest, sheet_size
from ngrunner.store import Db, list_snapshots

if TYPE_CHECKING:
    from ngrunner.guards.nestfiles import ToCheck

_CREATE_SUSPENDED = 0x00000004     # the subprocess module has no name for it
_LEFT_BEHIND_DIRS = ("__pycache__",)
_LEFT_BEHIND_ENDINGS = (".pyc",)
_LEFT_BEHIND_NAMES = ("desktop.ini",)
_LEFT_BEHIND_PREFIXES = ("~$",)
_AFTER_A_KILL_S = 10               # what a killed job's pipes get to close, so a stuck read can't hang
_KNOWN_CODES = (PASSED, FAILED, COULD_NOT_VERIFY)

ToolReader = Callable[[ToolRun], tuple[str, str]]       # a run: (code, its fixed line)

# Only what the source pins exactly, and it says so (guards/tool_checks.py). Each stays could_not_verify
# on anything it hasn't seen there. No other tool has a reader until its output is pinned the same way.
READERS: Mapping[str, ToolReader] = {SHEET_SIZE: sheet_size, BR_CAM_NEST: br_cam_nest}


class ToolkitError(Exception):
    """What stopped a copy or a run before it could say anything, in plain words."""


@dataclass(frozen=True)
class Snapshot:
    """A whole copy of a toolkit. sha256: over its files' names and hashes. files: how many."""

    path: Path
    sha256: str
    taken_at: datetime
    files: int


# ----- the copy


def take_snapshot(source: Path, dest: Path, *, clock: Clock,
                  read: Callable[[Path], bytes] = read_shared) -> Snapshot:
    """A whole copy of source at dest, which must be new, under its name only once complete, with
    what the copy holds hashed: the hash is of the bytes copied, whatever happens to source next."""
    if dest.exists():
        raise ToolkitError(f"There is already a toolkit copy at {dest.name}.")
    named = _toolkit_files(source)
    part = dest.with_name(f"{dest.name}.part")
    try:
        part.mkdir(parents=True)
        lines = [_copied(path, relative, part, read) for relative, path in named]
        os.rename(part, dest)
    except (OSError, Unreadable, ToolkitError) as exc:
        shutil.rmtree(part, ignore_errors=True)        # made by this call, a moment ago
        raise ToolkitError(f"The toolkit could not be copied: {_words(exc)}") from exc
    return Snapshot(path=dest, sha256=_hash_of(lines), taken_at=clock.now(), files=len(lines))


def hash_of_tree(root: Path) -> str:
    """The same hash, of a copy as it is now."""
    return _hash_of([(relative, hashlib.sha256(path.read_bytes()).hexdigest())
                     for relative, path in _files_in(root)])


def stage_files(files: Iterable[Path], into: Path,
                *, read: Callable[[Path], bytes] = read_shared) -> list[Path]:
    """A local copy of each file in into, for a check to run on: never the file in place. Read with
    every share flag, as Mozaik may be saving it (build spec 5.10)."""
    staged = []
    try:
        into.mkdir(parents=True, exist_ok=True)
        for path in files:
            target = into / path.name
            with open(target, "xb") as out:
                out.write(read(path))
            staged.append(target)
    except (OSError, Unreadable) as exc:
        raise ToolkitError(f"A file could not be staged for a check: {_words(exc)}") from exc
    return staged


def _toolkit_files(source: Path) -> list[tuple[str, Path]]:
    if not source.is_dir():
        raise ToolkitError("There is no toolkit folder to copy.")
    try:
        named = _files_in(source)
    except OSError as exc:
        raise ToolkitError(f"The toolkit folder could not be listed: {_words(exc)}") from exc
    if not named:
        raise ToolkitError("The toolkit folder is empty.")
    return named


def _files_in(root: Path) -> list[tuple[str, Path]]:
    """(name relative to root with forward slashes, path), sorted, leaving out what Python or a sync
    leaves behind: it changes without the toolkit changing. A link can't be vouched for."""
    found = []
    for folder, folders, files in os.walk(root, onerror=_raise):
        folders[:] = [name for name in folders if name not in _LEFT_BEHIND_DIRS]
        for name in (*folders, *files):
            if (Path(folder) / name).is_symlink():
                raise ToolkitError(f"{name} in the toolkit folder is a link.")
        for name in files:
            if not _left_behind(name):
                path = Path(folder) / name
                found.append((path.relative_to(root).as_posix(), path))
    return sorted(found)


def _raise(error: OSError) -> None:
    raise error


def _left_behind(name: str) -> bool:
    return (name.endswith(_LEFT_BEHIND_ENDINGS) or name.casefold() in _LEFT_BEHIND_NAMES
            or name.startswith(_LEFT_BEHIND_PREFIXES))


def _copied(path: Path, relative: str, into: Path, read: Callable[[Path], bytes]) -> tuple[str, str]:
    data = read(path)
    target = into / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "xb") as out:
        out.write(data)
    return relative, hashlib.sha256(data).hexdigest()


def _hash_of(lines: Iterable[tuple[str, str]]) -> str:
    return hashlib.sha256("\n".join(f"{name}\t{sha}" for name, sha in sorted(lines)).encode()).hexdigest()


# ----- running a script


def run_tool(snapshot: Snapshot, script: str, args: Sequence[str], *, timeout_s: int = 120) -> ToolRun:
    """Run one script of the copy, in a folder of its own, and what it gave back. Raises ToolkitError
    when it can't be started in its job at all: nothing ran, which is no result."""
    path = _script_in(snapshot, script)
    if hash_of_tree(snapshot.path) != snapshot.sha256:
        raise ToolkitError("The toolkit copy has changed since it was taken, so nothing was run.")
    command = tool_command(str(path), _expanded(args))
    work = Path(tempfile.mkdtemp(prefix="run-", dir=snapshot.path.parent))
    try:
        return _run_in_a_job(command, work, timeout_s)
    finally:
        shutil.rmtree(work, ignore_errors=True)        # made by this call


def tool_command(script: str, args: Sequence[str]) -> list[str]:
    """What runs a script: the build's own exe, which has no Python beside it, or this Python."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "run-tool", script, *args]
    return [sys.executable, "-X", "utf8", "-m", "ngrunner", "run-tool", script, *args]


def _script_in(snapshot: Snapshot, script: str) -> Path:
    root = snapshot.path.resolve()
    path = (root / script).resolve()
    if root not in path.parents or not path.is_file():
        raise ToolkitError(f"{Path(script).name} isn't a script in the toolkit copy.")
    return path


def _expanded(args: Sequence[str]) -> list[str]:
    """Windows doesn't expand *.opt, and a tool handed one literally could read nothing and exit 0,
    so one that matches nothing never reaches a tool. Only the last part may be a pattern: a folder's
    name is taken as it is."""
    out: list[str] = []
    for arg in args:
        if "*" not in arg and "?" not in arg:
            out.append(arg)
            continue
        path = Path(arg)
        if not path.is_absolute():
            raise ToolkitError("A wildcard for a check must be a full path.")
        matches = sorted(glob.glob(str(Path(glob.escape(str(path.parent))) / path.name)))
        if not matches:
            raise ToolkitError(f"{path.name} matches no file, so the check was not run.")
        out += matches
    return out


def _run_in_a_job(command: list[str], work: Path, timeout_s: int) -> ToolRun:
    job = _new_job()
    try:
        process = _start_in(job, command, work)
        try:
            out, err = process.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            win32job.TerminateJobObject(job, 1)
            out, err = _what_it_had_said(process)
            return ToolRun(None, _text(out), _text(err), timed_out=True)
        return ToolRun(process.returncode, _text(out), _text(err), timed_out=False)
    finally:
        job.Close()                 # kills whatever the script left running


def _new_job() -> Any:
    job = win32job.CreateJobObject(None, "")
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    return job


def _start_in(job: Any, command: list[str], work: Path) -> subprocess.Popen[bytes]:
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=work, env=_environment(),
            creationflags=_CREATE_SUSPENDED | subprocess.CREATE_NO_WINDOW)
    except OSError as exc:
        raise ToolkitError(f"The check could not be started: {_words(exc)}") from exc
    try:
        _assign(job, process)
        _resume(process)
    except Exception as exc:  # noqa: BLE001 - pywin32 and psutil raise their own; it never ran
        process.kill()
        process.communicate()
        raise ToolkitError("The check could not be put in its job, so it was not run.") from exc
    return process


def _assign(job: Any, process: subprocess.Popen[bytes]) -> None:
    win32job.AssignProcessToJobObject(job, process._handle)   # type: ignore[attr-defined]


def _resume(process: subprocess.Popen[bytes]) -> None:
    psutil.Process(process.pid).resume()


def _environment() -> dict[str, str]:
    """No .pyc written into the copy (it would change its hash), and, from the repo, where ngrunner
    is, as the run starts in another folder. UTF-8 mode comes from -X utf8, or the build's own option."""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if not getattr(sys, "frozen", False):
        repo = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (repo, env.get("PYTHONPATH"))))
    return env


def _what_it_had_said(process: subprocess.Popen[bytes]) -> tuple[bytes, bytes]:
    try:
        return process.communicate(timeout=_AFTER_A_KILL_S)
    except subprocess.TimeoutExpired:
        process.kill()
        return b"", b""


def _text(data: bytes | None) -> str:
    return (data or b"").decode("utf-8", errors="replace")


# ----- the seam: what a run means


def tool_verdict(tool: str, run: ToolRun, checked: CheckedFiles,
                 *, readers: Mapping[str, ToolReader] = READERS) -> ToolVerdict:
    """What one run says, failing closed: no reader, a run that never finished, a reader that breaks
    or a code it doesn't know are all could_not_verify, in NG Runner's own words."""
    reader = readers.get(tool)
    if reader is None or run.timed_out or run.exit_code is None:
        return _could_not_verify(tool, checked)
    try:
        code, line = reader(run)
    except Exception:  # noqa: BLE001 - whatever a reader does, it is no pass
        return _could_not_verify(tool, checked)
    if code not in _KNOWN_CODES or code == COULD_NOT_VERIFY or not isinstance(line, str):
        return _could_not_verify(tool, checked)
    return ToolVerdict(tool=tool, code=code, line=line, checked=checked)


# ----- Jim's two nest checks, from the adopted copy

SHEET_SIZE_SCRIPT = "scripts/sheet_size_check.py"
BR_CAM_SCRIPT = "scripts/br_cam_check.py"


def adopted_snapshot(db: Db, toolkit_folder: Path) -> Snapshot | None:
    """The newest copy adopted, at <toolkit folder>/<its sha256>; None with none, or with its copy
    gone. Nothing adopts one yet: that needs Jim's regression to run on it (group 3)."""
    adopted = [record for record in list_snapshots(db) if record.adopted]
    if not adopted:
        return None
    newest = adopted[-1]
    path = toolkit_folder / newest.sha256
    if not path.is_dir():
        return None
    return Snapshot(path=path, sha256=newest.sha256, taken_at=newest.taken_at,
                    files=len(_files_in(path)))


def nest_checks_from(snapshot: Snapshot, work: Path) -> Callable[[ToCheck], tuple[tuple[ToolVerdict, ...], None]]:
    """Both checks on one material, each on staged copies in a folder of its own under work, never on
    the files in place. A staged file not the very bytes the pass is to name runs nothing: could not
    verify. No design match comes of it, so no Nest checked either, until des_cutlist has a reader."""

    def run(job: ToCheck) -> tuple[tuple[ToolVerdict, ...], None]:
        work.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="check-", dir=work))
        try:
            nest, programs = _staged(job, stage)
            if nest is None:
                return (_could_not_verify(SHEET_SIZE, job.files),
                        _could_not_verify(BR_CAM_NEST, job.files)), None
            size = run_tool(snapshot, SHEET_SIZE_SCRIPT, [str(nest), "--taps", str(programs)])
            cams = run_tool(snapshot, BR_CAM_SCRIPT, ["--nest", str(nest)])
            return (tool_verdict(SHEET_SIZE, size, job.files),
                    tool_verdict(BR_CAM_NEST, cams, job.files)), None
        finally:
            shutil.rmtree(stage, ignore_errors=True)        # made by this call

    return run


def _staged(job: ToCheck, stage: Path) -> tuple[Path | None, Path]:
    """The nest as <material>.opt, and its programs in <stage>/taps/<material>/, which is where
    sheet_size_check --taps looks for them. None for the nest when a copy isn't what was read."""
    programs = stage / "taps"
    nest, = stage_files([job.opt_path], stage / "nest")
    copies = stage_files([job.program_dir / name for name, _ in job.files.manifest],
                         programs / job.material)
    wanted = {name: sha for name, sha in job.files.manifest}
    if (hashlib.sha256(nest.read_bytes()).hexdigest() != job.files.opt_sha256
            or any(hashlib.sha256(copy.read_bytes()).hexdigest() != wanted[copy.name] for copy in copies)):
        return None, programs
    return nest, programs


def _could_not_verify(tool: str, checked: CheckedFiles) -> ToolVerdict:
    return ToolVerdict(tool=tool, code=COULD_NOT_VERIFY, checked=checked,
                       line="NG Runner could not read what one of the nest checks said")


def _words(exc: BaseException) -> str:
    if isinstance(exc, OSError):
        return exc.strerror or "Windows would not allow it."
    return str(exc)
