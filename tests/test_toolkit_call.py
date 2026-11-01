"""
A room function of Jim's, run from the toolkit copy (toolkit.run_room_call): what BUILD has his chat do with
wm_eb.enforce_room() on a staged room, which has no command line of its own. The room is read and the
result written in binary (RULES section 6), in the same Job Object and on the same terms as run_tool.

The toolkit here is made up: a script with room functions, and none of Jim's code. tests/test_lock_banding.py
runs his own wm_eb through it.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ngrunner import __main__ as cli
from ngrunner import toolkit
from ngrunner.clock import FakeClock
from ngrunner.toolkit import Snapshot, ToolkitError, run_room_call, take_snapshot

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)

ROOMS = '''
import sibling

def upper(text):
    return text.replace("hide", "HIDE"), ["one change"]

def plain(text):
    return text + sibling.TAIL

def broken(text):
    raise ValueError("no")

def not_text(text):
    return 7
'''
ROOM = b'15\r\n<?xml version="1.0" encoding="utf-8"?>\r\n<Room hide="1" />\r\n'


@pytest.fixture
def snapshot(tmp_path: Path) -> Snapshot:
    scripts = tmp_path / "_NG_Automation" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "rooms.py").write_text(ROOMS, encoding="utf-8")
    (scripts / "sibling.py").write_text('TAIL = "\\r\\n"\n', encoding="utf-8")
    return take_snapshot(scripts.parent, tmp_path / "toolkit" / "copy", clock=FakeClock(NOW))


@pytest.fixture
def room(tmp_path: Path) -> Path:
    path = tmp_path / "staged" / "Room1.des"
    path.parent.mkdir()
    path.write_bytes(ROOM)
    return path


@pytest.mark.parametrize("python", [None, Path(sys.executable)], ids=["run-call", "a Python of its own"])
def test_the_function_gets_the_rooms_text_and_its_first_result_is_written_as_bytes(snapshot, room, tmp_path,
                                                                                   python):
    out = tmp_path / "staged" / "out.des"

    said = run_room_call(snapshot, "scripts/rooms.py", "upper", room, out, python=python)

    assert said.exit_code == 0 and not said.timed_out and said.stderr == ""
    assert out.read_bytes() == ROOM.replace(b"hide", b"HIDE")          # CRLF kept, as bytes
    assert room.read_bytes() == ROOM


def test_a_function_that_returns_text_alone_is_written_too_and_finds_what_lies_beside_it(snapshot, room,
                                                                                          tmp_path):
    out = tmp_path / "staged" / "out.des"

    assert run_room_call(snapshot, "scripts/rooms.py", "plain", room, out).exit_code == 0
    assert out.read_bytes() == ROOM + b"\r\n"


@pytest.mark.parametrize("python", [None, Path(sys.executable)], ids=["run-call", "a Python of its own"])
@pytest.mark.parametrize("function", ["broken", "not_text", "missing"])
def test_a_function_that_breaks_or_gives_no_text_is_a_failed_run_with_nothing_written(snapshot, room, tmp_path,
                                                                                    function, python):
    out = tmp_path / "staged" / "out.des"

    said = run_room_call(snapshot, "scripts/rooms.py", function, room, out, python=python)

    assert said.exit_code != 0 and said.stderr.strip()
    assert not out.exists()


@pytest.mark.parametrize("python", [None, Path(sys.executable)], ids=["run-call", "a Python of its own"])
def test_an_out_file_already_there_is_never_written_over(snapshot, room, tmp_path, python):
    out = tmp_path / "staged" / "out.des"
    out.write_bytes(b"there")

    said = run_room_call(snapshot, "scripts/rooms.py", "upper", room, out, python=python)

    assert said.exit_code != 0 and out.read_bytes() == b"there"


def test_only_a_script_in_the_copy_is_called(snapshot, room, tmp_path):
    (snapshot.path.parent / "outside.py").write_text("def upper(t):\n    return t\n", encoding="utf-8")

    with pytest.raises(ToolkitError):
        run_room_call(snapshot, "../outside.py", "upper", room, tmp_path / "out.des")


@pytest.mark.parametrize("function", ["", "upper.__class__", "a b", "_private"])
def test_only_a_plain_public_name_is_called(snapshot, room, tmp_path, function):
    with pytest.raises(ToolkitError):
        run_room_call(snapshot, "scripts/rooms.py", function, room, tmp_path / "out.des")


def test_a_call_writes_nothing_into_the_copy_so_its_hash_holds(snapshot, room, tmp_path):
    run_room_call(snapshot, "scripts/rooms.py", "upper", room, tmp_path / "staged" / "out.des")

    assert toolkit.hash_of_tree(snapshot.path) == snapshot.sha256


# ----- which command runs it


def test_in_the_tools_python_it_runs_isolated_with_a_start_of_its_own():
    command = toolkit.call_command(r"C:\x\rooms.py", "upper", r"C:\s\Room1.des", r"C:\s\out.des",
                                   python=Path(r"C:\tools\python.exe"))

    assert command[:6] == [r"C:\tools\python.exe", "-I", "-B", "-X", "utf8", "-c"]
    assert command[7:] == [r"C:\x\rooms.py", "upper", r"C:\s\Room1.des", r"C:\s\out.des"]


def test_from_the_repo_and_in_a_build_it_is_the_run_call_command(monkeypatch):
    assert toolkit.call_command(r"C:\x\rooms.py", "upper", "a", "b")[-6:] == [
        "ngrunner", "run-call", r"C:\x\rooms.py", "upper", "a", "b"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\made-up\NGRunner.exe")
    assert toolkit.call_command(r"C:\x\rooms.py", "upper", "a", "b") == [
        r"C:\made-up\NGRunner.exe", "run-call", r"C:\x\rooms.py", "upper", "a", "b"]


# ----- the run-call command, in this process


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    monkeypatch.setattr(sys, "path", list(sys.path))
    for name in ("rooms", "sibling"):
        monkeypatch.delitem(sys.modules, name, raising=False)


def test_the_command_calls_the_function_and_writes_its_text(isolated, snapshot, room, tmp_path):
    out = tmp_path / "staged" / "out.des"

    assert cli.main(["run-call", str(snapshot.path / "scripts" / "rooms.py"), "upper", str(room), str(out)],
                    environ={}) == 0
    assert out.read_bytes() == ROOM.replace(b"hide", b"HIDE")


def test_the_command_says_what_broke_on_stderr_and_writes_nothing(isolated, snapshot, room, tmp_path, capsys):
    out = tmp_path / "staged" / "out.des"

    assert cli.main(["run-call", str(snapshot.path / "scripts" / "rooms.py"), "broken", str(room), str(out)],
                    environ={}) == 1
    assert "ValueError" in capsys.readouterr().err and not out.exists()


def test_the_command_with_too_little_is_a_usage_error():
    assert cli.main(["run-call", "rooms.py", "upper"], environ={}) == 2
