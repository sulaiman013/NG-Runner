r"""
One NG Runner at a time, and a second start that hands over to the first.

Why: NG Runner is started from three places (the logon task, the 10-minute "ensure running"
task and a Run-key fallback, design section 6), and two copies would both act on the same new
Fab row. A named mutex, Local\NGRunner.Supervisor, decides: the process that owns it is NG
Runner, and any other start exits quietly once it has opened the running board, so a click on
the Start Menu item still shows Jim his board (spec 6.7).

What counts is owning the mutex, not its existing. A probe (the launcher asking "is it
running?") holds a handle for a moment but takes ownership only of a mutex nobody owns, and
gives it back at once; a real start waits a little (acquire's wait_s) for exactly that
moment, so a probe never turns a real start away. Windows ends the ownership when its owner
exits or crashes ("abandoned"), and the next start takes it over, so a crash never locks NG
Runner out. Ownership belongs to a thread: the supervisor acquires its guard on the main
thread, which lives as long as the process, and releases it there.

The board's address (with its per-launch token) is handed over in board.json in NG Runner's
own data folder. Only an address on 127.0.0.1 is ever written or opened, and only while the
process that wrote it is still the one running, so a stale or edited file opens nothing.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import secrets
import time
import webbrowser
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from types import TracebackType

from ngrunner.config import AppPaths

SUPERVISOR = "Local\\NGRunner.Supervisor"
STOP_EVENT = "Local\\NGRunner.Stop"  # install.ps1 and uninstall.ps1 set it (installer\common.ps1)
BOARD_FILE = "board.json"
_BOARD_URL = re.compile(r"http://127\.0\.0\.1:([0-9]{1,5})/[^\s]*")

# ----- Windows mutexes (kernel32 through ctypes, so the small launcher build needs no pywin32)

_SYNCHRONIZE = 0x00100000
_MUTEX_MODIFY_STATE = 0x0001
_EVENT_MODIFY_STATE = 0x0002
_WAIT_OBJECT_0, _WAIT_ABANDONED, _WAIT_TIMEOUT = 0x0, 0x80, 0x102
_ERROR_FILE_NOT_FOUND, _ERROR_ACCESS_DENIED, _ERROR_ALREADY_EXISTS = 2, 5, 183

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # its own instance: argtypes stay here
_k32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
_k32.CreateMutexW.restype = wintypes.HANDLE
_k32.OpenMutexW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
_k32.OpenMutexW.restype = wintypes.HANDLE
_k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
_k32.WaitForSingleObject.restype = wintypes.DWORD
_k32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
_k32.ReleaseMutex.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = (wintypes.HANDLE,)
_k32.CloseHandle.restype = wintypes.BOOL
_k32.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
_k32.CreateEventW.restype = wintypes.HANDLE
_k32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
_k32.OpenEventW.restype = wintypes.HANDLE
_k32.SetEvent.argtypes = (wintypes.HANDLE,)
_k32.SetEvent.restype = wintypes.BOOL
_k32.ResetEvent.argtypes = (wintypes.HANDLE,)
_k32.ResetEvent.restype = wintypes.BOOL


class InstanceGuard:
    """Ownership of a named mutex, held until release() (or the process ends). Release it on
    the thread that acquired it: Windows ties a mutex's ownership to that thread."""

    def __init__(self, name: str, handle: int) -> None:
        self.name = name
        self._handle: int | None = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle:
            _k32.ReleaseMutex(handle)
            _k32.CloseHandle(handle)

    def __enter__(self) -> InstanceGuard:
        return self

    def __exit__(self, kind: type[BaseException] | None, value: BaseException | None,
                 tb: TracebackType | None) -> None:
        self.release()


def acquire(name: str = SUPERVISOR, *, wait_s: float = 2.0) -> InstanceGuard | None:
    """Own the named mutex, or None when another process owns it. It waits up to wait_s for
    an owner to let go: long enough to outlast a probe, never long enough to share a start."""
    handle = _k32.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error(), "NG Runner could not create its start lock")
    result = _k32.WaitForSingleObject(handle, max(0, int(wait_s * 1000)))
    if result in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
        return InstanceGuard(name, handle)
    error = ctypes.get_last_error()
    _k32.CloseHandle(handle)
    if result == _WAIT_TIMEOUT:
        return None
    raise ctypes.WinError(error, "NG Runner could not check its start lock")


def is_running(name: str = SUPERVISOR) -> bool:
    """Whether another process owns the named mutex. It never creates the mutex, and it owns
    one nobody owned only for the moment it takes to give it back."""
    handle = _k32.OpenMutexW(_SYNCHRONIZE | _MUTEX_MODIFY_STATE, False, name)
    if not handle:
        error = ctypes.get_last_error()
        if error == _ERROR_FILE_NOT_FOUND:
            return False
        if error == _ERROR_ACCESS_DENIED:
            return True  # it exists but can't be opened here: never start a second one beside it
        raise ctypes.WinError(error, "NG Runner could not check whether it is running")
    try:
        result = _k32.WaitForSingleObject(handle, 0)
        if result == _WAIT_TIMEOUT:
            return True
        if result in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
            _k32.ReleaseMutex(handle)
            return False
        raise ctypes.WinError(ctypes.get_last_error(),
                              "NG Runner could not check whether it is running")
    finally:
        _k32.CloseHandle(handle)


# ----- being asked to stop (by install.ps1 and uninstall.ps1, before an update or removal)


class StopSignal:
    """The named event a running supervisor watches: once it is set, NG Runner finishes what
    it is doing and quits. Make it only after acquire() succeeded: any request left over is
    then from a runner that has already gone, so it is cleared rather than obeyed."""

    def __init__(self, name: str = STOP_EVENT) -> None:
        handle = _k32.CreateEventW(None, True, False, name)  # manual reset: stays set once set
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error(),
                                  "NG Runner could not listen for a stop request")
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            _k32.ResetEvent(handle)
        self._handle: int | None = handle

    def wait(self, timeout_s: float) -> bool:
        """Wait up to timeout_s for a stop request; True once one has come."""
        if not self._handle:
            return False
        waited = _k32.WaitForSingleObject(self._handle, max(0, int(timeout_s * 1000)))
        return waited == _WAIT_OBJECT_0

    def is_set(self) -> bool:
        return self.wait(0)

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle:
            _k32.CloseHandle(handle)

    def __enter__(self) -> StopSignal:
        return self

    def __exit__(self, kind: type[BaseException] | None, value: BaseException | None,
                 tb: TracebackType | None) -> None:
        self.close()


def request_stop(name: str = STOP_EVENT) -> bool:
    """Ask the running NG Runner to stop, as the install scripts do; False when none listens."""
    handle = _k32.OpenEventW(_EVENT_MODIFY_STATE, False, name)
    if not handle:
        return False
    try:
        return bool(_k32.SetEvent(handle))
    finally:
        _k32.CloseHandle(handle)


# ----- handing the board over to a second start


def publish_board(paths: AppPaths, url: str) -> None:
    """For the running supervisor, once its board is up: leave its address where a second
    start finds it. Only an address on this PC (http://127.0.0.1:<port>/...) is accepted."""
    if not _is_board_url(url):
        raise ValueError("the board's address must be http://127.0.0.1:<port>/ on this PC")
    record = {"url": url, "pid": os.getpid(), "started": _started(os.getpid())}
    paths.app_data.mkdir(parents=True, exist_ok=True)
    target = paths.app_data / BOARD_FILE
    temp = target.with_name(f"{BOARD_FILE}.{secrets.token_hex(4)}.tmp")
    temp.write_text(json.dumps(record), encoding="utf-8")
    os.replace(temp, target)


def withdraw_board(paths: AppPaths) -> None:
    """For the supervisor as it quits: remove board.json, while it is still this process's."""
    target = paths.app_data / BOARD_FILE
    record = _read_board(target)
    if record is not None and record.get("pid") == os.getpid():
        try:
            target.unlink()
        except FileNotFoundError:
            pass


def open_running_board(
    paths: AppPaths,
    *,
    opener: Callable[[str], object] = webbrowser.open,
    wait_s: float = 10.0,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> bool:
    """For a second start: open the running NG Runner's board in the browser. Waits up to
    wait_s for a runner that is still starting to publish it; False when there is none."""
    deadline = monotonic() + wait_s
    while True:
        url = _live_board_url(paths)
        if url is not None:
            return opener(url) is not False
        if monotonic() >= deadline:
            return False
        sleep(0.5)


def _live_board_url(paths: AppPaths) -> str | None:
    """The published address, only while the process that published it still runs."""
    record = _read_board(paths.app_data / BOARD_FILE)
    if record is None:
        return None
    url, pid, started = record.get("url"), record.get("pid"), record.get("started")
    if not (isinstance(url, str) and _is_board_url(url)):
        return None
    if isinstance(pid, bool) or not isinstance(pid, int) or not isinstance(started, int | float):
        return None
    now = _started(pid)
    return url if now is not None and abs(now - started) < 0.5 else None


def _read_board(path: Path) -> dict[str, object] | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _is_board_url(url: str) -> bool:
    match = _BOARD_URL.fullmatch(url)
    return bool(match) and 0 < int(match[1]) < 65536


def _started(pid: int) -> float | None:
    """When process pid started (seconds since 1970), or None if it is not running: a pid of
    an ended process can be reused, its start time can't."""
    import psutil  # here, not at the top: the launcher's small build imports this module

    try:
        return psutil.Process(pid).create_time()
    except (psutil.Error, OSError, ValueError):
        return None
