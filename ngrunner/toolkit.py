r"""
The harness for Jim's toolkit: a hash-pinned copy of it, and running a script of that copy (P1b build
spec 5.13, design 6).

Why a copy: the checks must run from what passed its own regression, never from the folder Jim is
changing, so a verdict can say which scripts stood behind it. A copy is adopted the way his RULES
section 8 adopts a change (try_adoption): copied whole, his regression.py run on that very copy, and
adopted only when it prints his clean line and the copy's hash still holds. A changed toolkit is copied
again and must pass again; one that doesn't is never used, and the last adopted copy goes on. The
supervisor tries one each night, on a worker of its own.

Why a Job Object: these are Jim's scripts, written for a chat's container, and a first Windows run is
itself a test (risk E4). One that hangs must cost a timeout and not a stuck worker, and what it started
must go with it. The process starts suspended, goes into the job and only then runs a line, so even a
first line that starts a child can't slip out. Closing the job's last handle kills whatever is left,
so a crash of NG Runner takes them too.

Which Python (rulings R1 and R7). A tools' Python of its own when there is one: NGR_TOOLS_PYTHON, or
toolspython\\python.exe beside a build's NGRunner.exe. It runs isolated (-I: no PYTHON* variables, no
user site), writing no bytecode (-B), in UTF-8 mode, and a two-line start puts the script's folder
first on its path, as `python script.py` does and -I doesn't. Jim's scripts import openpyxl,
pdfplumber, numpy, Pillow, scipy and PyMuPDF (fitz) between them, which NG Runner's build doesn't
carry: that Python has them, pinned in tools/tools-venv.txt. With none, NGRunner.exe runs a script by
itself, with `run-tool` and runpy, in the UTF-8 mode the build carries (ngrunner/__main__.py), and
from the repo it is sys.executable -X utf8. That serves every script the checks and regression need,
as they import only the standard library and each other; one that imports a package the build lacks
fails its run with an ImportError, which is no result. A script a script starts (crosscheck runs each
checker as sys.executable <checker>.py) gets the same Python, and in a build NGRunner.exe takes a
first argument ending .py as run-tool (R7). Either way it inherits no PYTHON* variable but the three
set here, so it runs in UTF-8 mode, writes no bytecode and reads no user site.

Where (R1). A copy, a run's folder and anything staged must be on a local disk: a fixed drive with
NTFS or ReFS, never a network path, and never a cloud drive, which is how Google Drive for desktop's
G: shows itself (FAT32). A copy that changed while a script ran gives no result, as a run must be of
what was hashed.

What a tool's output means is in guards/tool_checks.py, read from its source and not seen running, and
READERS names the two that source pins exactly. tool_verdict fails closed around them: no reader, a run
that never finished, a reader that breaks or a code it doesn't know are all could_not_verify.
watch.nest_checks runs both from the adopted copy once there is one; NEST_CHECKED also needs the design
match, which is built in guards/. crosscheck and its checkers are read in ngrunner/checks/.

A fixer of Jim's with no command line of its own (wm_eb.enforce_room, which BUILD has his chat call on a
staged room) is called the same way by run_room_call: one function of a script of the copy, given the
room's text and its text written back, in binary, under the same Job Object, Python and hash checks.

Checks run on staged copies (stage_files) in a folder of their own, and never in place. Only ever read:
the toolkit's own folder and what is staged. Everything written is in NG Runner's data folder.
"""
from __future__ import annotations

import glob
import hashlib
import os
import re
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
import pywintypes
import win32api
import win32file
import win32job

from ngrunner.clock import Clock
from ngrunner.guards.facts import (
    BR_CAM_NEST, COULD_NOT_VERIFY, FAILED, PASSED, SHEET_SIZE, CheckedFiles, ToolRun, ToolVerdict,
)
from ngrunner.guards.reads import Unreadable, read_shared
from ngrunner.guards.tool_checks import br_cam_nest, sheet_size
from ngrunner.store import Db, SnapshotRecord, add_snapshot, list_snapshots

if TYPE_CHECKING:
    from ngrunner.guards.nestfiles import ToCheck

_CREATE_SUSPENDED = 0x00000004     # the subprocess module has no name for it
_LEFT_BEHIND_DIRS = ("__pycache__",)
_LEFT_BEHIND_ENDINGS = (".pyc",)
_LEFT_BEHIND_NAMES = ("desktop.ini",)
_LEFT_BEHIND_PREFIXES = ("~$",)
_AFTER_A_KILL_S = 10               # what a killed job's pipes get to close, so a stuck read can't hang
_KNOWN_CODES = (PASSED, FAILED, COULD_NOT_VERIFY)

TOOLS_PYTHON = "NGR_TOOLS_PYTHON"                     # the tools' Python, a full path (development)
BUNDLED_TOOLS_PYTHON = Path("toolspython") / "python.exe"     # beside a build's NGRunner.exe
_LOCAL_FILE_SYSTEMS = ("NTFS", "REFS")
# What `python script.py` does that -I leaves out: the script's folder first on the path, and its
# name as argv[0]. Its exit code and output are the script's own.
_START = ("import os, runpy, sys; s = sys.argv[1]; sys.argv = sys.argv[1:]; "
          "sys.path.insert(0, os.path.dirname(os.path.abspath(s))); runpy.run_path(s, run_name='__main__')")
_CHILD_VARIABLES = {"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
# run_room_call's start in the tools' Python (call_room_function is the same in this process): the script's
# folder first on the path, its function given the room's text, the text it gives back written new.
_CALL = ("import os, sys; s, f, a, b = sys.argv[1:5]; sys.argv = [s]; "
         "sys.path.insert(0, os.path.dirname(os.path.abspath(s))); "
         "m = __import__(os.path.splitext(os.path.basename(s))[0]); "
         "r = getattr(m, f)(open(a, 'rb').read().decode('utf-8')); r = r[0] if isinstance(r, tuple) else r; "
         "r if isinstance(r, str) else sys.exit('the function gave no room text'); "
         "open(b, 'xb').write(r.encode('utf-8'))")
_PUBLIC_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

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
    refuse_unless_local(dest, "A toolkit copy")
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
    refuse_unless_local(into, "A check's staged copy")
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


def run_tool(snapshot: Snapshot, script: str, args: Sequence[str], *, timeout_s: int = 120,
             python: Path | None = None) -> ToolRun:
    """Run one script of the copy, in a folder of its own, and what it gave back: in python, or the
    tools' Python, or NG Runner's own (tools_python). Raises ToolkitError when it can't be started in
    its job at all, or the copy changed while it ran: either way, no result."""
    path = _script_in(snapshot, script)
    return _run(snapshot, lambda py: tool_command(str(path), _expanded(args), python=py), timeout_s, python)


def run_room_call(snapshot: Snapshot, script: str, function: str, room: Path, out: Path, *,
                  timeout_s: int = 120, python: Path | None = None) -> ToolRun:
    """One room function of a script of the copy, as BUILD has Jim's chat run wm_eb.enforce_room() on a
    staged Room#.des, where the script has no command line for it: function(the room's text) gives the
    new text, or a tuple whose first item is it. The room is read and out written in binary (RULES
    section 6), and out must not be there yet. The same Job Object, Python and checks as run_tool; a
    function that breaks or gives no text is a run that failed, with nothing written."""
    if not _PUBLIC_NAME.fullmatch(function):
        raise ToolkitError("Only a plain function of a script is called, so nothing was run.")
    path = _script_in(snapshot, script)
    return _run(snapshot, lambda py: call_command(str(path), function, str(room), str(out), python=py),
                timeout_s, python)


def _run(snapshot: Snapshot, command_for: Callable[[Path | None], list[str]], timeout_s: int,
         python: Path | None) -> ToolRun:
    refuse_unless_local(snapshot.path, "The toolkit copy")
    if hash_of_tree(snapshot.path) != snapshot.sha256:
        raise ToolkitError("The toolkit copy has changed since it was taken, so nothing was run.")
    python = python if python is not None else tools_python()
    if python is not None and not python.is_file():
        raise ToolkitError("The Python that runs Jim's scripts isn't there, so nothing was run.")
    command = command_for(python)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=snapshot.path.parent))
    try:
        said = _run_in_a_job(command, work, timeout_s, isolated=python is not None)
    finally:
        shutil.rmtree(work, ignore_errors=True)        # made by this call
    if hash_of_tree(snapshot.path) != snapshot.sha256:
        raise ToolkitError("The toolkit copy changed while a script of it ran, so what it said can't be used.")
    return said


def tool_command(script: str, args: Sequence[str], *, python: Path | None = None) -> list[str]:
    """What runs a script: the tools' Python, isolated; else the build's own exe, which has no Python
    beside it; else this Python."""
    if python is not None:
        return [str(python), "-I", "-B", "-X", "utf8", "-c", _START, script, *args]
    if getattr(sys, "frozen", False):
        return [sys.executable, "run-tool", script, *args]
    return [sys.executable, "-X", "utf8", "-m", "ngrunner", "run-tool", script, *args]


def call_command(script: str, function: str, room: str, out: str, *, python: Path | None = None) -> list[str]:
    """What runs a room function (run_room_call): as tool_command, with a start of its own in the tools'
    Python and the run-call command in the build's exe or from the repo."""
    args = [script, function, room, out]
    if python is not None:
        return [str(python), "-I", "-B", "-X", "utf8", "-c", _CALL, *args]
    if getattr(sys, "frozen", False):
        return [sys.executable, "run-call", *args]
    return [sys.executable, "-X", "utf8", "-m", "ngrunner", "run-call", *args]


def call_room_function(script: str, function: str, room: str, out: str) -> None:
    """run-call in this process: what _CALL does in the tools' Python, for the build, which has none."""
    folder = os.path.dirname(os.path.abspath(script))
    sys.argv = [script]
    sys.path.insert(0, folder)
    module = __import__(os.path.splitext(os.path.basename(script))[0])
    with open(room, "rb") as source:
        said = getattr(module, function)(source.read().decode("utf-8"))
    text = said[0] if isinstance(said, tuple) else said
    if not isinstance(text, str):
        raise SystemExit("the function gave no room text")
    with open(out, "xb") as written:
        written.write(text.encode("utf-8"))


def tools_python(environ: Mapping[str, str] = os.environ) -> Path | None:
    """The Python Jim's scripts run in, when there is one of their own: NGR_TOOLS_PYTHON, a full path
    to one that is there, or the one a build carries beside NGRunner.exe. None: NG Runner's own."""
    given = environ.get(TOOLS_PYTHON, "").strip()
    if given:
        path = Path(given)
        if not path.is_absolute() or not path.is_file():
            raise ToolkitError(f"{TOOLS_PYTHON} names no Python that is there, so nothing was run.")
        return path
    if getattr(sys, "frozen", False):
        bundled = Path(sys.executable).parent / BUNDLED_TOOLS_PYTHON
        if bundled.is_file():
            return bundled
    return None


def on_local_disk(path: Path) -> bool:
    """Whether path is on this PC's own disk: a full path on a fixed drive formatted NTFS or ReFS. A
    network path isn't, and nor is Google Drive for desktop's drive, which shows itself as a fixed
    FAT32 one."""
    text = str(path)
    if text.startswith(("\\\\", "//")) or not path.is_absolute():
        return False
    drive = os.path.splitdrive(text)[0]
    if len(drive) != 2:
        return False
    try:
        kind, file_system = _volume(drive + "\\")
    except pywintypes.error:
        return False
    return kind == win32file.DRIVE_FIXED and file_system.upper() in _LOCAL_FILE_SYSTEMS


def refuse_unless_local(path: Path, what: str) -> None:
    if not on_local_disk(path):
        raise ToolkitError(f"{what} must be on a local disk of this PC, never a network or cloud drive.")


def _volume(root: str) -> tuple[int, str]:
    """(the drive's type, its file system's name)."""
    return win32file.GetDriveType(root), win32api.GetVolumeInformation(root)[4]


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


def _run_in_a_job(command: list[str], work: Path, timeout_s: int, *, isolated: bool = False) -> ToolRun:
    job = _new_job()
    try:
        process = _start_in(job, command, work, isolated=isolated)
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


def _start_in(job: Any, command: list[str], work: Path, *, isolated: bool = False) -> subprocess.Popen[bytes]:
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=work, env=_environment(isolated),
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


def _environment(isolated: bool = False) -> dict[str, str]:
    """No .pyc written into the copy, UTF-8 mode and no user site, for the script and for any Python
    it starts, which reads these where -I would not. In the tools' Python no other PYTHON* variable
    reaches either; from the repo NG Runner's own needs PYTHONPATH to find ngrunner, as the run starts
    in another folder."""
    env = {**os.environ}
    if isolated:
        env = {name: value for name, value in env.items() if not name.upper().startswith("PYTHON")}
    env.update(_CHILD_VARIABLES)
    if not isolated and not getattr(sys, "frozen", False):
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


# ----- adopting a copy: his regression, clean on that very copy (RULES section 8, design 6)

TOOLKIT_FOLDER = "_NG_Automation"                 # in Mozaik Shared, beside Jobs (INDEX: Toolkit paths)
REGRESSION_SCRIPT = "scripts/regression.py"
BASELINE = "reference/regression/baseline.json"
REGRESSION_CLEAN = "Regression clean — nothing moved."      # what regression.py prints, exactly
REGRESSION_TIMEOUT_S = 900                        # 10 s on this PC; a slow PC and a cold Drive get room
ADOPTED, UNCHANGED, REFUSED, NOT_COPIED = "adopted", "unchanged", "refused", "failed"
_MOVED = re.compile(r"(\d+)\. \S.*")
_SHA_NAME = re.compile(r"[0-9a-f]{64}")
_TAKING = "taking-"


@dataclass(frozen=True)
class Regression:
    """What Jim's regression said of one copy. clean: exactly his clean line, exit 0, nothing else.
    words: NG Runner's own, for a person; his items name his frozen jobs, so they are never repeated.
    moved: how many items moved, when it listed them."""

    sha256: str
    clean: bool
    words: str
    moved: int = 0


@dataclass(frozen=True)
class Adoption:
    """What one try came to: adopted, refused (copied, and its regression wasn't clean on it),
    unchanged (the adopted copy's very bytes) or failed (no copy to try). sha256 and taken_at are of
    the copy tried."""

    outcome: str
    words: str
    sha256: str | None = None
    taken_at: datetime | None = None


def run_regression(snapshot: Snapshot, *, python: Path | None = None,
                   timeout_s: int = REGRESSION_TIMEOUT_S, run: Callable[..., ToolRun] | None = None) -> Regression:
    """regression.py on the copy, as RULES section 8 runs it: from its scripts folder, beside its
    reference folder, with no argument, so never --accept. A copy with no baseline is never run, as
    regression.py would write one and say nothing moved."""
    if not (snapshot.path / BASELINE).is_file():
        return Regression(snapshot.sha256, False, "It has no regression baseline, so nothing could be compared.")
    try:
        said = (run or run_tool)(snapshot, REGRESSION_SCRIPT, [], timeout_s=timeout_s, python=python)
    except ToolkitError as exc:
        return Regression(snapshot.sha256, False, f"Its regression could not be run. {exc}")
    return read_regression(snapshot.sha256, said, timeout_s=timeout_s)


def read_regression(sha256: str, run: ToolRun, *, timeout_s: int = REGRESSION_TIMEOUT_S) -> Regression:
    """Clean only on exactly his clean line, exit 0 and nothing on stderr; moved only on his numbered
    list, numbered from 1 with no gap, and exit 1. Anything else is not clean, in NG Runner's words."""
    if run.timed_out or run.exit_code is None:
        return Regression(sha256, False, f"Its regression didn't finish in {timeout_s // 60} minutes.")
    if run.stderr.strip():
        return Regression(sha256, False, "Its regression stopped with an error.")
    lines = run.stdout.splitlines()
    if run.exit_code == 0 and lines == [REGRESSION_CLEAN]:
        return Regression(sha256, True, "Its regression is clean: nothing moved on Jim's frozen jobs.")
    numbers = [int(found[1]) if (found := _MOVED.fullmatch(line)) else None for line in lines]
    if run.exit_code == 1 and lines and numbers == list(range(1, len(lines) + 1)):
        return Regression(sha256, False, f"Its regression moved {len(lines)} thing{'s' * (len(lines) != 1)}"
                                         " on Jim's frozen jobs, so it waits for Jim's approval.", len(lines))
    if any(line.startswith("baseline written") for line in lines):
        return Regression(sha256, False, "Its regression wrote a new baseline, so nothing was compared.")
    return Regression(sha256, False, "Its regression printed something NG Runner doesn't know.")


def try_adoption(source: Path, folder: Path, *, adopted: str | None, keep: Iterable[str] = (),
                 clock: Clock, regress: Callable[[Snapshot], Regression] | None = None) -> Adoption:
    """Copy source into folder and, when the copy isn't the adopted one's bytes, run regression on it:
    adopted only when it is clean on that very copy, whose hash is checked again after it. Writes
    only in folder: the copy, under its sha256 once tried, and nothing kept of a refused one. Old
    copies go first, but adopted and keep, and only names NG Runner gives. Off the tick's thread; the
    store is the caller's to write (record_adoption)."""
    keep = {*keep, *([adopted] if adopted else [])}
    _tidy(folder, keep)
    taking = folder / f"{_TAKING}{clock.now():%Y%m%dT%H%M%S%f}-{os.getpid()}"
    try:
        copy = take_snapshot(source, taking, clock=clock)
    except ToolkitError as exc:
        return Adoption(NOT_COPIED, f"Jim's toolkit could not be copied. {exc}")
    try:
        target = _put_in_place(copy, folder)
    except OSError as exc:
        shutil.rmtree(taking, ignore_errors=True)       # made by this call
        return Adoption(NOT_COPIED, f"Jim's toolkit could not be copied. {_words(exc)}")
    tried = Snapshot(path=target, sha256=copy.sha256, taken_at=copy.taken_at, files=copy.files)
    if copy.sha256 == adopted:
        return Adoption(UNCHANGED, "Jim's toolkit is the copy already adopted.", copy.sha256, copy.taken_at)
    said = (regress or run_regression)(tried)
    clean = said.clean and said.sha256 == tried.sha256 and _unchanged(tried)
    if not clean:
        if tried.sha256 not in keep:
            shutil.rmtree(target, ignore_errors=True)   # tried by this call, and never to be used
        words = said.words if not said.clean else "Its copy changed while its regression ran."
        return Adoption(REFUSED, words, tried.sha256, tried.taken_at)
    return Adoption(ADOPTED, said.words, tried.sha256, tried.taken_at)


def record_adoption(db: Db, tried: Adoption) -> SnapshotRecord | None:
    """A copy tried, on record: adopted only for an adoption. Nothing for one not tried."""
    if tried.outcome not in (ADOPTED, REFUSED):
        return None
    if tried.sha256 is None or tried.taken_at is None:
        raise ValueError("a copy tried has its sha256 and when it was taken")
    return add_snapshot(db, taken_at=tried.taken_at, sha256=tried.sha256, adopted=tried.outcome == ADOPTED,
                        result=tried.words)


def _put_in_place(copy: Snapshot, folder: Path) -> Path:
    """The copy at folder/<its sha256>. One there already is of the same bytes or it is replaced, as
    only NG Runner names folders so."""
    target = folder / copy.sha256
    if target.is_dir() and hash_of_tree(target) == copy.sha256:
        shutil.rmtree(copy.path, ignore_errors=True)    # made by this call: the same bytes are there
        return target
    if target.exists():
        shutil.rmtree(target)
    os.rename(copy.path, target)
    return target


def _unchanged(copy: Snapshot) -> bool:
    try:
        return hash_of_tree(copy.path) == copy.sha256
    except (OSError, ToolkitError):
        return False


def _tidy(folder: Path, keep: set[str]) -> None:
    """Every copy NG Runner made in folder but those kept, and any try a stop cut short. Never a
    name it doesn't give, and never a run's own folder, which goes when its run ends."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return
    for name in names:
        ours = (_SHA_NAME.fullmatch(name) and name not in keep) or name.startswith(_TAKING)
        if ours and (folder / name).is_dir() and not (folder / name).is_symlink():
            shutil.rmtree(folder / name, ignore_errors=True)


# ----- Jim's two nest checks, from the adopted copy

SHEET_SIZE_SCRIPT = "scripts/sheet_size_check.py"
BR_CAM_SCRIPT = "scripts/br_cam_check.py"


def adopted_snapshot(db: Db, toolkit_folder: Path) -> Snapshot | None:
    """The newest copy adopted, at <toolkit folder>/<its sha256>; None with none, or with its copy
    gone. Only try_adoption makes one, once Jim's regression is clean on it, and run_tool checks its
    hash again before every run."""
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
