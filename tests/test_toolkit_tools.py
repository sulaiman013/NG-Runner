"""
Which Python runs Jim's scripts, and where (rulings R1 and R7, docs/p2/build-spec.md section 9).

R1: his scripts run on a staged copy, in a Python of their own with his packages, isolated (-I), inside a
Job Object, never in place and never on G:. R7: NGRunner.exe takes a first argument ending .py as
run-tool, as crosscheck.py starts each checker as sys.executable <checker>.py, and in the build that is
NGRunner.exe. The made-up scripts here are synthetic; the tests marked fixtures run in the real tools'
Python and skip when it is absent.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep

import pytest
import win32file

from helpers_toolkit import tools_python as real_tools_python
from ngrunner import __main__ as cli
from ngrunner import toolkit
from ngrunner.clock import FakeClock
from ngrunner.toolkit import Snapshot, ToolkitError, run_tool, take_snapshot

NOW = datetime(2026, 10, 31, 22, 0, tzinfo=timezone.utc)
CLOCK = FakeClock(NOW)

ENV = """
import importlib.util, os, sys
print("isolated", sys.flags.isolated)
print("utf8", sys.flags.utf8_mode)
print("no bytecode", sys.dont_write_bytecode)
print("venv", sys.prefix != sys.base_prefix)
print("argv", "|".join(sys.argv[1:]))
print("beside", os.path.dirname(os.path.abspath(sys.argv[0])) == sys.path[0])
print("user site", sys.flags.no_user_site)
print("python vars", sorted(k for k in os.environ if k.upper().startswith("PYTHON")))
for name in ("openpyxl", "pdfplumber", "numpy", "PIL", "scipy", "fitz"):
    print(name, importlib.util.find_spec(name) is not None)
"""

PARENT = """
import subprocess, sys, os
here = os.path.dirname(os.path.abspath(__file__))
p = subprocess.run([sys.executable, os.path.join(here, "child.py"), "caf\\u00e9"], capture_output=True,
                   text=True, cwd=here)
sys.stdout.write(p.stdout)
sys.exit(p.returncode)
"""

CHILD = """
import os, sys, sibling
print("child", sys.argv[1], sys.flags.utf8_mode, sys.dont_write_bytecode, sibling.WORD)
print("child python vars", sorted(k for k in os.environ if k.upper().startswith("PYTHON")))
sys.exit(4)
"""

HANG = """
import os, subprocess, sys, time
beat = sys.argv[1]
code = ("import sys, time\\n"
        "while True:\\n"
        "    open(sys.argv[1], 'a', encoding='utf-8').write('x')\\n"
        "    time.sleep(0.05)\\n")
subprocess.Popen([sys.executable, "-c", code, beat], stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
while not os.path.exists(beat):
    time.sleep(0.05)
sys.stdout.write("started\\n"); sys.stdout.flush()
time.sleep(600)
"""

SCRIBBLE = """
import os
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "left.txt"), "w", encoding="utf-8").write("x")
print("wrote beside myself")
"""


@pytest.fixture
def snapshot(tmp_path: Path) -> Snapshot:
    source = tmp_path / "_NG_Automation"
    scripts = source / "scripts"
    scripts.mkdir(parents=True)
    for name, body in (("env.py", ENV), ("parent.py", PARENT), ("child.py", CHILD), ("hang.py", HANG),
                       ("scribble.py", SCRIBBLE)):
        (scripts / name).write_text(body, encoding="utf-8")
    (scripts / "sibling.py").write_text('WORD = "from the sibling"\n', encoding="utf-8")
    return take_snapshot(source, tmp_path / "data" / "toolkit" / "copy", clock=CLOCK)


def said(run) -> dict[str, str]:
    return dict(line.split(" ", 1) for line in run.stdout.splitlines() if " " in line)


# ----- which Python


def test_with_a_tools_python_a_script_runs_isolated_in_utf8_mode_writing_no_bytecode() -> None:
    command = toolkit.tool_command(r"C:\made-up\say.py", ["--taps", "x"], python=Path(r"C:\tools\python.exe"))

    assert command[:6] == [r"C:\tools\python.exe", "-I", "-B", "-X", "utf8", "-c"]
    assert command[-3:] == [r"C:\made-up\say.py", "--taps", "x"]


def test_the_tools_python_is_its_variable_and_must_be_a_full_path_that_is_there(tmp_path: Path) -> None:
    exe = tmp_path / "python.exe"
    exe.write_bytes(b"")

    assert toolkit.tools_python({"NGR_TOOLS_PYTHON": str(exe)}) == exe
    assert toolkit.tools_python({}) is None
    for wrong in ("python.exe", str(tmp_path / "gone.exe")):
        with pytest.raises(ToolkitError):
            toolkit.tools_python({"NGR_TOOLS_PYTHON": wrong})


def test_a_build_runs_them_in_the_python_it_carries_and_else_in_itself(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "NGRunner.exe"))

    assert toolkit.tools_python({}) is None
    assert toolkit.tool_command(r"C:\made-up\say.py", ["x"])[:2] == [str(tmp_path / "NGRunner.exe"), "run-tool"]
    bundled = tmp_path / "toolspython" / "python.exe"
    bundled.parent.mkdir()
    bundled.write_bytes(b"")
    assert toolkit.tools_python({}) == bundled


def test_a_tools_python_that_isnt_there_runs_nothing(snapshot: Snapshot, tmp_path: Path) -> None:
    with pytest.raises(ToolkitError):
        run_tool(snapshot, "scripts/env.py", [], python=tmp_path / "gone" / "python.exe")


# ----- ruling R7: NGRunner.exe <script>.py is run-tool


@pytest.fixture
def script(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    return tmp_path / "made_up_checker.py"


def test_r7_a_first_argument_ending_py_is_run_tool(script: Path, capsys) -> None:
    script.write_text("import sys; print('|'.join(sys.argv)); sys.exit(3)", encoding="utf-8")

    assert cli.main([str(script), "a", "--b", "--version"], environ={}) == 3
    assert capsys.readouterr().out.strip() == f"{script}|a|--b|--version"


def test_r7_takes_py_in_any_case_and_nothing_else(script: Path, capsys) -> None:
    upper = script.with_name("MADE_UP.PY")
    upper.write_text("print('ran')", encoding="utf-8")

    assert cli.main([str(upper)], environ={}) == 0
    assert capsys.readouterr().out.strip() == "ran"
    assert cli.main([str(script.with_suffix(".pyc"))], environ={}) == 2     # a usage error, run nothing


def test_run_tool_writes_no_bytecode_beside_the_script(script: Path, capsys) -> None:
    (script.parent / "sibling_mod.py").write_text("WORD = 'w'\n", encoding="utf-8")
    script.write_text("import sibling_mod; print(sibling_mod.WORD)", encoding="utf-8")

    assert cli.main([str(script)], environ={}) == 0
    assert not (script.parent / "__pycache__").exists()


# ----- only on a local disk, and a copy that changes in a run says nothing


def test_a_snapshot_on_a_drive_that_isnt_a_local_disk_runs_nothing(snapshot: Snapshot, monkeypatch) -> None:
    drive = os.path.splitdrive(str(snapshot.path))[0].upper()
    real = toolkit._volume
    monkeypatch.setattr(toolkit, "_volume", lambda root: (win32file.DRIVE_FIXED, "FAT32")
                        if root.upper().startswith(drive) else real(root))      # as Google Drive's G: shows

    with pytest.raises(ToolkitError, match="local disk"):
        run_tool(snapshot, "scripts/env.py", [])


def test_a_snapshot_is_only_ever_taken_onto_a_local_disk(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "_NG_Automation"
    source.mkdir()
    (source / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(toolkit, "_volume", lambda root: (win32file.DRIVE_REMOTE, "NTFS"))

    with pytest.raises(ToolkitError, match="local disk"):
        take_snapshot(source, tmp_path / "copy", clock=CLOCK)
    assert not (tmp_path / "copy").exists()


@pytest.mark.parametrize("path", [r"\\server\share\toolkit", r"\\?\UNC\server\share\toolkit", "toolkit"])
def test_a_network_or_relative_path_is_not_a_local_disk(path: str) -> None:
    assert not toolkit.on_local_disk(Path(path))


def test_this_pcs_own_data_folder_is_a_local_disk(tmp_path: Path) -> None:
    assert toolkit.on_local_disk(tmp_path)


@pytest.mark.fixtures
def test_google_drive_for_desktop_is_not_a_local_disk() -> None:
    if not Path("G:\\").is_dir():
        pytest.skip("no G: on this PC")
    assert not toolkit.on_local_disk(Path("G:\\"))


def test_a_copy_that_changes_during_a_run_gives_no_result(snapshot: Snapshot) -> None:
    with pytest.raises(ToolkitError, match="changed"):
        run_tool(snapshot, "scripts/scribble.py", [])


# ----- in the real tools' Python (fixtures)


@pytest.mark.fixtures
def test_in_the_tools_python_a_script_runs_isolated_with_his_packages(snapshot: Snapshot, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", r"C:\somewhere\else")
    monkeypatch.setenv("PYTHONSTARTUP", r"C:\somewhere\startup.py")

    run = run_tool(snapshot, "scripts/env.py", ["--taps", "x y"], python=real_tools_python())

    assert run.exit_code == 0 and not run.timed_out and run.stderr == ""
    found = said(run)
    assert (found["isolated"], found["utf8"], found["no"], found["venv"]) == ("1", "1", "bytecode True", "True")
    assert found["argv"] == "--taps|x y" and found["beside"] == "True"
    assert all(found[name] == "True" for name in ("openpyxl", "pdfplumber", "numpy", "PIL", "scipy", "fitz"))


@pytest.mark.fixtures
def test_a_script_a_script_starts_runs_in_the_same_python_in_utf8_mode_with_no_python_variables(
        snapshot: Snapshot, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", r"C:\somewhere\else")

    run = run_tool(snapshot, "scripts/parent.py", [], python=real_tools_python())

    assert run.exit_code == 4                       # the child's, as crosscheck reads it
    lines = run.stdout.splitlines()
    assert lines[0] == "child caf\u00e9 1 True from the sibling"
    assert lines[1] == "child python vars ['PYTHONDONTWRITEBYTECODE', 'PYTHONNOUSERSITE', 'PYTHONUTF8']"
    assert not (snapshot.path / "scripts" / "__pycache__").exists()


@pytest.mark.fixtures
def test_a_timeout_kills_the_tools_python_and_what_it_started(snapshot: Snapshot, tmp_path: Path) -> None:
    # As tests/test_toolkit.py's _said_before_the_kill: on a busy PC a Python start (here a venv's
    # launcher, its interpreter and a grandchild) can take longer than the timeout, and then the kill
    # comes before the first line. Tried again while nothing was said; every run must time out in time.
    # Left unkilled, a run takes its timeout and toolkit._AFTER_A_KILL_S more, and says nothing.
    beat = tmp_path / "beat.txt"
    for _ in range(3):
        began = monotonic()
        run = run_tool(snapshot, "scripts/hang.py", [str(beat)], python=real_tools_python(), timeout_s=8)
        assert run.timed_out is True and run.exit_code is None
        assert monotonic() - began < 8 + toolkit._AFTER_A_KILL_S - 1
        if run.stdout.strip():
            break

    assert run.stdout.strip() == "started"          # what it said before it was killed is kept
    sizes = []
    for _ in range(3):
        sleep(0.5)
        sizes.append(beat.stat().st_size if beat.exists() else 0)
    assert sizes[0] == sizes[-1]                    # the grandchild is gone too


@pytest.mark.fixtures
def test_the_tools_python_holds_exactly_the_pinned_packages() -> None:
    pinned = Path(__file__).resolve().parent.parent / "tools" / "tools-venv.txt"
    wanted = {line.split("==")[0].strip().casefold(): line.split("==")[1].split()[0].strip()
              for line in pinned.read_text(encoding="utf-8").splitlines()
              if "==" in line and not line.lstrip().startswith("#")}
    frozen = subprocess.run([str(real_tools_python()), "-I", "-m", "pip", "freeze", "--all"],
                            capture_output=True, text=True, encoding="utf-8", check=True).stdout
    have = {line.split("==")[0].casefold(): line.split("==")[1] for line in frozen.splitlines() if "==" in line}
    have.pop("pip", None)

    assert have == wanted
