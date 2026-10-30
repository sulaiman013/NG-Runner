"""One NG Runner at a time: the supervisor mutex, and a second start handing over to the first.

The mutex tests use a real second process, because ownership of a Windows mutex belongs to a
thread and a second start is always another process. Every test uses its own random mutex
name, so nothing here can collide with a real NG Runner or with another test run.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from ngrunner import single
from ngrunner.config import AppPaths

ROOT = Path(__file__).resolve().parent.parent

# A second process: takes the mutex named on its command line, says whether it got it, then
# holds it until its stdin closes (or it is killed).
_HOLDER = r"""
import sys
from ngrunner import single
guard = single.acquire(sys.argv[1], wait_s=0)
print("held" if guard else "busy", flush=True)
sys.stdin.readline()
"""


def _name() -> str:
    return f"Local\\NGRunner.test-{secrets.token_hex(8)}"


def _child_env() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(ROOT)}


@contextmanager
def _second_process(name: str) -> Iterator[tuple[subprocess.Popen[str], str]]:
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, name], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, encoding="utf-8", env=_child_env(),
    )
    try:
        assert proc.stdout is not None
        yield proc, proc.stdout.readline().strip()
    finally:
        proc.kill()
        proc.wait(timeout=30)


# ----- the supervisor mutex


def test_the_first_start_owns_the_mutex_and_a_second_process_is_turned_away():
    name = _name()
    guard = single.acquire(name)
    assert guard is not None
    try:
        with _second_process(name) as (_, said):
            assert said == "busy"
    finally:
        guard.release()


def test_while_another_process_runs_this_one_is_told_so():
    name = _name()
    with _second_process(name) as (_, said):
        assert said == "held"
        assert single.acquire(name, wait_s=0) is None
        assert single.is_running(name)


def test_asking_whether_it_runs_never_takes_the_place_of_a_real_start():
    name = _name()
    assert not single.is_running(name)
    assert not single.is_running(name)
    with _second_process(name) as (_, said):
        assert said == "held"          # the probes left nothing behind that a start trips over


def test_a_crashed_runner_does_not_lock_the_next_start_out():
    name = _name()
    with _second_process(name) as (proc, said):
        assert said == "held"
        proc.kill()
        proc.wait(timeout=30)
        guard = single.acquire(name, wait_s=1)
        assert guard is not None
        guard.release()


def test_quitting_lets_the_next_start_in():
    name = _name()
    with single.acquire(name) as guard:
        assert guard is not None
    assert not single.is_running(name)
    with _second_process(name) as (_, said):
        assert said == "held"


# ----- the board handover


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    return AppPaths(tmp_path / "NGRunner")


def test_a_second_start_opens_the_running_board(paths: AppPaths):
    url = "http://127.0.0.1:50123/?t=" + secrets.token_urlsafe(32)
    single.publish_board(paths, url)
    opened: list[str] = []
    assert single.open_running_board(paths, opener=opened.append, wait_s=0)
    assert opened == [url]


def test_a_board_left_by_a_runner_that_has_ended_is_not_opened(paths: AppPaths):
    code = (
        "import sys; from pathlib import Path; from ngrunner import single;"
        " from ngrunner.config import AppPaths;"
        " single.publish_board(AppPaths(Path(sys.argv[1])), 'http://127.0.0.1:50124/?t=x')"
    )
    subprocess.run([sys.executable, "-c", code, str(paths.app_data)], check=True,
                   env=_child_env(), timeout=60)
    opened: list[str] = []
    assert not single.open_running_board(paths, opener=opened.append, wait_s=0)
    assert opened == []


def test_no_board_published_means_nothing_is_opened(paths: AppPaths):
    opened: list[str] = []
    assert not single.open_running_board(paths, opener=opened.append, wait_s=0)
    assert opened == []


@pytest.mark.parametrize("url", [
    "https://example.com/?t=x",
    "http://127.0.0.1.example.com:50123/",
    "file:///C:/Windows/System32/cmd.exe",
    "http://localhost:50123/",
    "http://127.0.0.1:50123/\nhttp://example.com/",
])
def test_only_a_board_on_this_pc_is_ever_published_or_opened(paths: AppPaths, url: str):
    with pytest.raises(ValueError):
        single.publish_board(paths, url)
    single.publish_board(paths, "http://127.0.0.1:50125/?t=x")
    board = paths.app_data / single.BOARD_FILE
    tampered = json.loads(board.read_text(encoding="utf-8"))
    tampered["url"] = url
    board.write_text(json.dumps(tampered), encoding="utf-8")
    opened: list[str] = []
    assert not single.open_running_board(paths, opener=opened.append, wait_s=0)
    assert opened == []


def test_a_runner_withdraws_only_its_own_board(paths: AppPaths):
    single.publish_board(paths, "http://127.0.0.1:50126/?t=x")
    single.withdraw_board(paths)
    assert not (paths.app_data / single.BOARD_FILE).exists()
    single.withdraw_board(paths)                     # nothing there: still fine


# ----- asking a running NG Runner to stop (install.ps1 and uninstall.ps1 do this)


def _event_name() -> str:
    return f"Local\\NGRunner.test-stop-{secrets.token_hex(8)}"


def test_a_stop_request_from_another_process_reaches_the_running_runner():
    name = _event_name()
    with single.StopSignal(name) as signal:
        assert not signal.is_set()
        asked = subprocess.run(
            [sys.executable, "-c", "import sys; from ngrunner import single; print(single.request_stop(sys.argv[1]))", name],
            capture_output=True, text=True, encoding="utf-8", env=_child_env(), timeout=60)
        assert asked.stdout.strip() == "True"
        assert signal.wait(5) and signal.is_set()


def test_the_installers_stop_request_reaches_it_too():
    name = _event_name()
    with single.StopSignal(name) as signal:
        done = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             f"$e = [Threading.EventWaitHandle]::OpenExisting('{name}'); [void]$e.Set(); $e.Dispose()"],
            capture_output=True, text=True, encoding="utf-8", timeout=300)
        assert done.returncode == 0, done.stderr
        assert signal.wait(5)


def test_with_no_runner_listening_a_stop_request_says_so():
    assert single.request_stop(_event_name()) is False


def test_a_new_runner_does_not_inherit_an_old_stop_request():
    name = _event_name()
    with single.StopSignal(name) as old:
        assert single.request_stop(name)
        assert old.is_set()
        with single.StopSignal(name) as new:          # started once the old one has quit
            assert not new.is_set()
