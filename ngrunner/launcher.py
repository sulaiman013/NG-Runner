r"""
The launcher: which installed version of NG Runner to start, and falling back when one won't.

Why: an update must never leave Jim without NG Runner because of a bad build (design 3.1, and
section 6 "Updates"). Versions sit side by side in %LOCALAPPDATA%\Programs\NGRunner\app-<ver>\,
and the launcher (a small build of its own in launcher\ beside them, rarely changed) is what
the Start Menu item, the two scheduled tasks and the Run-key fallback start. It starts the
newest version whose post-install self-test passed, then waits for that version to report
that it is up. A start that stops first, or is not up in time, has failed. Two failed starts
in a row retire the version; the previous good one is started, and a note is left for the
version that then runs to send to Sulaiman's DEV lane. Nothing is ever installed from here.

Its record (AppPaths.launcher_state, launcher.json) is shared by three kinds of process: the
launcher, the self-test (NGRunner.exe --self-test --record) and the running app (up, Quit,
notes taken). Every read and change happens under a named mutex for that data folder, and a
change lands with a replace, so no reader sees half a file. A damaged record starts nothing,
since no self-test can be confirmed from it; the next self-test with --record sets the
damaged file aside and writes a fresh record.

Modes: "open" (the Start Menu item: if NG Runner runs already, its board opens instead),
"logon" (the logon task and the Run key) and "ensure" (the 10-minute task, which leaves NG
Runner off after Jim chose Quit, until he signs in again or starts it from the Start Menu).
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import re
import secrets
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from ngrunner import single
from ngrunner.clock import Clock, SystemClock, from_iso, to_iso
from ngrunner.config import AppPaths, SettingsError, app_paths

APP_EXE = "NGRunner.exe"
APP_OUTPUT = "app-output.log"          # the app's own console output, in AppPaths.logs
LAUNCHER_LOG = "launcher.log"
VERSION_FILE = "ngrunner-version.txt"  # written into every build by ngrunner.spec
MAX_FAILED_STARTS = 2
MODES = ("open", "logon", "ensure")

_STATE_VERSION = 1
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}")
_FOLDER = re.compile(r"app-([0-9]+(?:\.[0-9]+){0,3})")
_KEEP_ATTEMPTS = 20
_KEEP_NOTES = 20
_KEEP_PROBLEMS = 20
_LOG_LIMIT = 1_000_000                 # bytes; one older copy (.1) is kept
_STATE_WAIT_S = 10.0
_CANNOT_START = ("NG Runner could not start, so nothing is being filed."
                 " Work in chat as before, and tell Sulaiman.")
_BUSY = "NG Runner is starting already. Try again in a minute."


class LauncherStateError(Exception):
    """The launcher's record can't be used; the message says why, in plain words."""


@dataclass(frozen=True)
class Installed:
    """One installed version: its app-<version> folder."""

    version: str
    folder: Path

    @property
    def exe(self) -> Path:
        return self.folder / APP_EXE


@dataclass(frozen=True)
class DevNote:
    """A note the launcher left for Sulaiman's DEV lane; the running app sends it."""

    id: str
    at: datetime
    text: str


@dataclass(frozen=True)
class Launched:
    """What a launch did. outcome: started, already_running, stopped_by_user,
    nothing_to_start or busy. reason: plain words, for the log."""

    outcome: str
    version: str | None = None
    reason: str = ""
    pid: int | None = None


class Process(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def kill(self) -> None: ...


Starter = Callable[[Installed, list[str], Path], Process]


# ----- versions


def app_version() -> str:
    """This build's version: written into a build by ngrunner.spec, else pyproject.toml's."""
    if getattr(sys, "frozen", False):
        text = (Path(getattr(sys, "_MEIPASS")) / VERSION_FILE).read_text(encoding="utf-8")
    else:
        import tomllib

        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        text = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    version = text.strip()
    if not _VERSION.fullmatch(version):
        raise ValueError(f"{version!r} is not a version like 0.1.2")
    return version


def installed_versions(root: Path) -> list[Installed]:
    """Every complete app-<version> folder under root (holding NGRunner.exe), newest first.
    A folder still being copied (app-<ver>.partial-...) or a link is not a version."""
    try:
        entries = list(os.scandir(root))
    except OSError:
        return []
    found = []
    for entry in entries:
        match = _FOLDER.fullmatch(entry.name)
        if (match and entry.is_dir(follow_symlinks=False) and not entry.is_junction()
                and (Path(entry.path) / APP_EXE).is_file()):
            found.append(Installed(match[1], Path(entry.path)))
    return sorted(found, key=lambda i: _order(i.version), reverse=True)


def _order(version: str) -> tuple[int, ...]:
    parts = [int(p) for p in version.split(".")]
    return tuple(parts + [0] * (4 - len(parts)))


# ----- what the self-test and the running app record


def record_self_test(paths: AppPaths, version: str, problems: Iterable[str], *,
                     clock: Clock) -> None:
    """Record a version's self-test (NGRunner.exe --self-test --record). A pass also brings
    back a version retired after failed starts. A damaged record is set aside, not reused."""
    if not _VERSION.fullmatch(version):
        raise ValueError(f"{version!r} is not a version like 0.1.2")
    found = [str(p) for p in problems]

    def change(state: dict[str, Any]) -> bool:
        entry = _entry(state, version)
        entry["self_test"] = {"ok": not found, "at": to_iso(clock.now()),
                              "problems": found[:_KEEP_PROBLEMS]}
        if not found:
            entry["failed_starts"], entry["retired"] = 0, False
        return True

    _update(paths, change, clock=clock, reset_damaged=True)


def record_up(paths: AppPaths, attempt: str, *, clock: Clock) -> bool:
    """For the app, once it is fully up: this start (its --attempt) worked. False when the
    attempt is not one the launcher made, or the record can't be read."""

    def change(state: dict[str, Any]) -> bool:
        started = state["attempts"].get(attempt)
        if not isinstance(started, dict) or not isinstance(started.get("version"), str):
            return False
        started["up"] = to_iso(clock.now())
        entry = _entry(state, started["version"])
        entry["failed_starts"], entry["last_up"] = 0, started["up"]
        return True

    return _try_update(paths, change, clock)


def record_quit(paths: AppPaths, *, clock: Clock) -> bool:
    """For the app, when Jim chooses Quit: the ensure task then leaves NG Runner off until he
    signs in again or starts it from the Start Menu. False when it could not be recorded."""

    def change(state: dict[str, Any]) -> bool:
        state["stopped_by_user"] = to_iso(clock.now())
        return True

    return _try_update(paths, change, clock)


def pending_notes(paths: AppPaths) -> list[DevNote]:
    """Notes for Sulaiman not yet taken by the running app (empty if the record is damaged)."""
    try:
        state = _read(paths)
    except LauncherStateError:
        return []
    notes = []
    for note in state["notes"]:
        try:
            notes.append(DevNote(str(note["id"]), from_iso(str(note["at"])), str(note["text"])))
        except (KeyError, TypeError, ValueError):
            continue  # one damaged note never hides the others
    return notes


def state(paths: AppPaths) -> dict[str, Any]:
    """The whole record, as stored (for the support bundle). LauncherStateError if damaged."""
    return _read(paths)


def ack_notes(paths: AppPaths, ids: Iterable[str]) -> bool:
    """For the app, once its own store holds the notes: forget them here."""
    taken = set(ids)

    def change(state: dict[str, Any]) -> bool:
        kept = [n for n in state["notes"] if not (isinstance(n, dict) and n.get("id") in taken)]
        changed = len(kept) != len(state["notes"])
        state["notes"] = kept
        return changed

    return _try_update(paths, change, SystemClock())


# ----- launching


def launch(
    root: Path,
    paths: AppPaths,
    *,
    mode: str = "open",
    start: Starter | None = None,
    running: Callable[[], bool] = single.is_running,
    open_board: Callable[[AppPaths], bool] = single.open_running_board,
    tell: Callable[[str], None] | None = None,
    clock: Clock | None = None,
    sleep: Callable[[float], None] = time.sleep,
    timeout_s: float = 120.0,
    poll_s: float = 1.0,
) -> Launched:
    """Start NG Runner from the versions under root (see the module docstring). tell shows Jim
    a message, and is used only in "open" mode, when he clicked the Start Menu item."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    clock = clock or SystemClock()
    run = _Run(root, paths, mode, start or start_process, running, clock, sleep, timeout_s, poll_s)
    try:
        with _locked(paths, "Launcher", wait_s=30.0 if mode == "open" else 5.0) as got:
            if not got:
                result = Launched("busy", reason="another launcher is starting NG Runner")
            else:
                result = run.launch(open_board)
    except OSError as exc:  # a full disk, say. This build has no console: an error raised
        # from here would be a box on Jim's PC, and would hold up the 10-minute task.
        reason = f"NG Runner could not use its start-up record ({_why(exc)})"
        result = Launched("nothing_to_start", reason=reason)
    if tell is not None and mode == "open":
        if result.outcome == "busy":
            tell(_BUSY)
        elif result.outcome == "nothing_to_start":
            tell(_CANNOT_START)
    _log(paths, clock, _describe(result))
    return result


@dataclass
class _Run:
    root: Path
    paths: AppPaths
    mode: str
    start: Starter
    running: Callable[[], bool]
    clock: Clock
    sleep: Callable[[float], None]
    timeout_s: float
    poll_s: float

    def launch(self, open_board: Callable[[AppPaths], bool]) -> Launched:
        if self.running():
            if self.mode == "open":
                open_board(self.paths)
            return Launched("already_running", reason="NG Runner is already running")
        try:
            return self._start_one()
        except LauncherStateError as exc:
            return Launched("nothing_to_start", reason=f"{exc}, so no version was started")

    def _start_one(self) -> Launched:
        state = _read(self.paths)
        if state["stopped_by_user"] and self.mode == "ensure":
            return Launched("stopped_by_user", reason="Jim chose Quit, so it stays off for now")
        if state["stopped_by_user"]:
            _update(self.paths, _clear_stop, clock=self.clock)
        retired: list[tuple[str, str]] = []  # versions retired by this launch, and why
        while True:
            installed = installed_versions(self.root)
            target = _first_startable(installed, _read(self.paths))
            if target is None:
                return self._nothing(installed, retired)
            outcome, why, process = self._try(target)
            if outcome == "up":
                if retired:
                    _add_note(self.paths, self.clock, _fallback_note(retired, target.version))
                return Launched("started", target.version, pid=getattr(process, "pid", None))
            if outcome == "already_running":
                if retired:
                    _add_note(self.paths, self.clock, _fallback_note(retired, None))
                return Launched("already_running", target.version,
                                reason="another start of NG Runner came up first")
            _log(self.paths, self.clock, f"{target.version} failed to start: {why}")
            if _record_failure(self.paths, target.version, why, self.clock):
                retired.append((target.version, why))

    def _try(self, target: Installed) -> tuple[str, str, Process | None]:
        attempt = secrets.token_hex(16)
        _update(self.paths, lambda s: _add_attempt(s, attempt, target.version, self.clock),
                clock=self.clock)
        args = ["run", "--attempt", attempt] + (["--open-board"] if self.mode == "open" else [])
        try:
            process = self.start(target, args, self.paths.logs / APP_OUTPUT)
        except OSError as exc:
            return "failed", f"it could not be started: {_why(exc)}", None
        for _ in range(max(1, math.ceil(self.timeout_s / self.poll_s))):
            if _is_up(self.paths, attempt):
                return "up", "", process
            code = process.poll()
            if code is not None:
                if _is_up(self.paths, attempt):
                    return "up", "", process
                if code == 0 and self.running():
                    return "already_running", "", process
                return "failed", f"it stopped with exit code {code} before it was up", process
            self.sleep(self.poll_s)
        if _is_up(self.paths, attempt):
            return "up", "", process
        process.kill()
        why = f"it was not up after {self.timeout_s:g} seconds, so it was stopped"
        return "failed", why, process

    def _nothing(self, installed: list[Installed], retired: list[tuple[str, str]]) -> Launched:
        if retired:
            listed = "; ".join(f"{version} ({why})" for version, why in retired)
            _add_note(self.paths, self.clock, f"No version of NG Runner could start: {listed}.")
        if not installed:
            reason = "no version of NG Runner is installed"
        elif retired:
            reason = "every version that passed its self-test failed to start twice"
        else:
            reason = "no installed version has passed its self-test and started"
        return Launched("nothing_to_start", reason=reason)


def start_process(
    installed: Installed, args: list[str], log: Path, *, command: list[str] | None = None
) -> subprocess.Popen[bytes]:
    """Start a version hidden (no console window), its output appended to log. It leaves the
    scheduled task's job when Windows allows it, so ending the task never ends NG Runner.
    command replaces the exe (tests only)."""
    argv = [*(command or [str(installed.exe)]), *args]
    log.parent.mkdir(parents=True, exist_ok=True)
    _rotate(log)
    with open(log, "ab") as output:  # binary: the app's output is passed through as it comes
        options: dict[str, Any] = dict(cwd=installed.folder, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, close_fds=True)
        try:
            return subprocess.Popen(argv, creationflags=subprocess.CREATE_NO_WINDOW
                                    | subprocess.CREATE_BREAKAWAY_FROM_JOB, **options)
        except PermissionError:  # the task's job does not allow breakaway: start inside it
            return subprocess.Popen(argv, creationflags=subprocess.CREATE_NO_WINDOW, **options)


def main(argv: list[str] | None = None) -> int:
    """The launcher build's entry: NGRunnerLauncher.exe [--logon | --ensure]. Exit codes:
    0 NG Runner runs (or was left off on purpose), 1 nothing could start, 2 not set up."""
    parser = argparse.ArgumentParser(prog="NGRunnerLauncher")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--logon", dest="mode", action="store_const", const="logon",
                       help="started by the logon task or the Run key")
    modes.add_argument("--ensure", dest="mode", action="store_const", const="ensure",
                       help="started by the 10-minute task")
    parser.add_argument("--root", type=Path, help=argparse.SUPPRESS)  # tests: the install root
    args = parser.parse_args(argv)
    mode = args.mode or "open"
    root = args.root or _install_root()
    try:
        paths = app_paths()
    except SettingsError:
        return 2
    if root is None:
        _log(paths, SystemClock(), "not started: the launcher is not inside an installed build")
        return 2
    result = launch(root, paths, mode=mode, tell=_message_box)
    return 1 if result.outcome == "nothing_to_start" else 0


def _install_root() -> Path | None:
    """<root>\\launcher\\NGRunnerLauncher.exe in a build; None when not running from one."""
    return Path(sys.executable).resolve().parent.parent if getattr(sys, "frozen", False) else None


def _message_box(text: str) -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MessageBoxW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                   ctypes.c_uint)
    user32.MessageBoxW(None, text, "NG Runner", 0x30 | 0x10000)  # warning icon, to the front


# ----- the record (launcher.json)


def _empty() -> dict[str, Any]:
    return {"v": _STATE_VERSION, "versions": {}, "attempts": {}, "notes": [],
            "stopped_by_user": None}


def _read(paths: AppPaths) -> dict[str, Any]:
    with _locked(paths, "LauncherState", wait_s=_STATE_WAIT_S) as got:
        if not got:
            raise LauncherStateError("NG Runner's start-up record is busy")
        return _load(paths)


def _load(paths: AppPaths) -> dict[str, Any]:
    try:
        text = paths.launcher_state.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty()
    except (OSError, UnicodeDecodeError) as exc:
        raise LauncherStateError(
            f"NG Runner's start-up record can't be read ({_why(exc)})") from exc
    try:
        state = json.loads(text)
    except ValueError as exc:
        raise LauncherStateError("NG Runner's start-up record is damaged") from exc
    if not (isinstance(state, dict) and state.get("v") == _STATE_VERSION
            and isinstance(state.get("versions"), dict) and isinstance(state.get("attempts"), dict)
            and isinstance(state.get("notes"), list)
            and isinstance(state.get("stopped_by_user"), str | None)):
        raise LauncherStateError("NG Runner's start-up record is damaged or from a newer version")
    return state


def _update(paths: AppPaths, change: Callable[[dict[str, Any]], bool], *, clock: Clock,
            reset_damaged: bool = False) -> bool:
    """Apply change to the record under its lock; saved only when change says it changed."""
    with _locked(paths, "LauncherState", wait_s=_STATE_WAIT_S) as got:
        if not got:
            raise LauncherStateError("NG Runner's start-up record is busy")
        try:
            state = _load(paths)
        except LauncherStateError:
            if not reset_damaged:
                raise
            _set_aside(paths, clock)
            state = _empty()
        changed = change(state)
        if changed:
            _save(paths, state)
        return changed


def _try_update(paths: AppPaths, change: Callable[[dict[str, Any]], bool], clock: Clock) -> bool:
    try:
        return _update(paths, change, clock=clock)
    except (LauncherStateError, OSError):
        return False


def _save(paths: AppPaths, state: dict[str, Any]) -> None:
    paths.app_data.mkdir(parents=True, exist_ok=True)
    target = paths.launcher_state
    temp = target.with_name(f"{target.name}.{secrets.token_hex(4)}.tmp")
    temp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    for tries_left in range(20, 0, -1):
        try:
            os.replace(temp, target)
            return
        except PermissionError:  # a virus scanner can hold a new file for a moment
            if tries_left == 1:
                temp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def _set_aside(paths: AppPaths, clock: Clock) -> None:
    """Keep a damaged record for Sulaiman under a new name; never over an earlier one."""
    stamp = clock.now().strftime("%Y%m%dT%H%M%SZ")
    for n in range(1, 100):
        aside = paths.launcher_state.with_name(
            f"{paths.launcher_state.name}.damaged-{stamp}" + ("" if n == 1 else f"-{n}"))
        try:
            os.rename(paths.launcher_state, aside)
            return
        except FileExistsError:
            continue
        except FileNotFoundError:
            return


@contextmanager
def _locked(paths: AppPaths, what: str, *, wait_s: float) -> Iterator[bool]:
    """A named mutex for this data folder (so two data folders never wait for each other)."""
    folder = os.path.normcase(os.path.abspath(paths.app_data))
    key = hashlib.sha256(folder.encode("utf-8")).hexdigest()[:16]
    guard = single.acquire(f"Local\\NGRunner.{what}.{key}", wait_s=wait_s)
    try:
        yield guard is not None
    finally:
        if guard is not None:
            guard.release()


def _entry(state: dict[str, Any], version: str) -> dict[str, Any]:
    entry = state["versions"].get(version)
    if not isinstance(entry, dict):
        entry = state["versions"][version] = {}
    entry.setdefault("failed_starts", 0)
    entry.setdefault("retired", False)
    return entry


def _first_startable(installed: list[Installed], state: dict[str, Any]) -> Installed | None:
    for candidate in installed:
        entry = state["versions"].get(candidate.version)
        if not isinstance(entry, dict):
            continue
        self_test = entry.get("self_test")
        failed = entry.get("failed_starts", 0)
        if (isinstance(self_test, dict) and self_test.get("ok") is True and not entry.get("retired")
                and isinstance(failed, int) and failed < MAX_FAILED_STARTS):
            return candidate
    return None


def _add_attempt(state: dict[str, Any], attempt: str, version: str, clock: Clock) -> bool:
    attempts = state["attempts"]
    attempts[attempt] = {"version": version, "started": to_iso(clock.now()), "up": None}
    for old in list(attempts)[:-_KEEP_ATTEMPTS]:
        del attempts[old]
    return True


def _is_up(paths: AppPaths, attempt: str) -> bool:
    try:
        started = _read(paths)["attempts"].get(attempt)
    except LauncherStateError:
        return False
    return isinstance(started, dict) and bool(started.get("up"))


def _record_failure(paths: AppPaths, version: str, why: str, clock: Clock) -> bool:
    """Count a failed start; True when it retired the version."""
    retired = []

    def change(state: dict[str, Any]) -> bool:
        entry = _entry(state, version)
        entry["failed_starts"] = int(entry.get("failed_starts") or 0) + 1
        entry["last_failure"] = {"at": to_iso(clock.now()), "why": why}
        if entry["failed_starts"] >= MAX_FAILED_STARTS:
            entry["retired"] = True
        retired.append(entry["retired"])
        return True

    _update(paths, change, clock=clock)
    return retired[0]


def _clear_stop(state: dict[str, Any]) -> bool:
    state["stopped_by_user"] = None
    return True


def _add_note(paths: AppPaths, clock: Clock, text: str) -> None:
    """A note for DEV. One that can't be saved (a full disk) never undoes a start that worked."""

    def change(state: dict[str, Any]) -> bool:
        state["notes"] = [*state["notes"], {"id": secrets.token_hex(16), "at": to_iso(clock.now()),
                                            "code": "launcher", "text": text}][-_KEEP_NOTES:]
        return True

    _try_update(paths, change, clock)


def _fallback_note(retired: list[tuple[str, str]], started: str | None) -> str:
    """started: the version started instead, or None when another start came up first."""
    failed = "; ".join(f"{version} failed to start twice ({why})" for version, why in retired)
    if started is None:
        return f"NG Runner {failed}, and another start of NG Runner came up meanwhile."
    return f"NG Runner {failed}, so {started} was started instead."


# ----- the launcher's own log


def _describe(result: Launched) -> str:
    if result.outcome == "started":
        return f"started {result.version} (process {result.pid})"
    if result.outcome == "nothing_to_start":
        return f"nothing to start: {result.reason}"
    return f"{result.outcome.replace('_', ' ')}: {result.reason}"


def _log(paths: AppPaths, clock: Clock, text: str) -> None:
    """One line in logs\\launcher.log. A log that can't be written never stops a start."""
    try:
        paths.logs.mkdir(parents=True, exist_ok=True)
        path = paths.logs / LAUNCHER_LOG
        _rotate(path)
        with open(path, "a", encoding="utf-8") as log:
            log.write(f"{to_iso(clock.now())} {text}\n")
    except OSError:
        pass


def _rotate(path: Path) -> None:
    try:
        if path.stat().st_size > _LOG_LIMIT:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass  # no log yet, or it is in use: append to it as it is


def _why(exc: BaseException) -> str:
    return getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
