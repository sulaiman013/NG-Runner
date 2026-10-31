"""
The toolkit's harness (P1b build spec 5.13): a hash-pinned copy of a toolkit, and running a script of it
in UTF-8 mode inside a Job Object that kills it, and whatever it started, on a timeout.

The toolkit here is made up: a folder with two small scripts, as the build spec's section 6 says. Jim's
own has been read and not run. What reads a real tool's output is tests/test_guards_tool_checks.py's; here
is the seam around it, which fails closed: anything not recognised is could_not_verify.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, sleep
from types import SimpleNamespace
from typing import Any

import pytest

from ngrunner import __main__ as cli
from ngrunner import toolkit
from ngrunner.clock import FakeClock
from ngrunner.config import AppPaths
from ngrunner.guards.facts import BR_CAM_NEST, COULD_NOT_VERIFY, FAILED, PASSED, SHEET_SIZE, CheckedFiles
from ngrunner.guards.reads import CloudOnly
from ngrunner.messages.lint import check_line
from ngrunner.toolkit import (
    READERS, Snapshot, ToolkitError, ToolRun, run_tool, stage_files, take_snapshot, tool_verdict,
)

NOW = datetime(2026, 10, 30, 22, 0, tzinfo=timezone.utc)
CLOCK = FakeClock(NOW)

SAY = """
import sys
sys.stdout.write("said " + "|".join(sys.argv[1:]) + "\\n")
sys.stdout.write("utf8 mode " + str(sys.flags.utf8_mode) + "\\n")
sys.stdout.write("caf\\u00e9\\n")
sys.stderr.write("to stderr\\n")
sys.exit(int(sys.argv[1]) if sys.argv[1:] and sys.argv[1].isdigit() else 0)
"""

HANG = """
import os, subprocess, sys, time
sys.stdout.write("started\\n"); sys.stdout.flush()
beat = sys.argv[1] if len(sys.argv) > 1 else None
mode = sys.argv[2] if len(sys.argv) > 2 else ""
if beat:                # a child of its own, that keeps touching a file for as long as it lives
    code = ("import sys, time\\n"
            "while True:\\n"
            "    open(sys.argv[1], 'a', encoding='utf-8').write('x')\\n"
            "    time.sleep(0.05)\\n")
    out = None if mode == "inherit" else subprocess.DEVNULL
    subprocess.Popen([sys.executable, "-c", code, beat], stdin=subprocess.DEVNULL, stdout=out, stderr=out)
if mode == "leave":     # it ends, once its child is surely running
    while not os.path.exists(beat):
        time.sleep(0.05)
    sys.exit(0)
time.sleep(600)
"""


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A made-up toolkit: two small scripts, and a sibling one of them imports."""
    folder = tmp_path / "_NG_Automation"
    scripts = folder / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "say.py").write_text(SAY, encoding="utf-8")
    (scripts / "hang.py").write_text(HANG, encoding="utf-8")
    (scripts / "with_sibling.py").write_text("import sibling\nprint(sibling.WORD)\n", encoding="utf-8")
    (scripts / "sibling.py").write_text('WORD = "from the sibling"\n', encoding="utf-8")
    (folder / "reference").mkdir()
    (folder / "reference" / "baseline.json").write_bytes(b"{}")
    return folder


@pytest.fixture
def snapshot(source: Path, tmp_path: Path) -> Snapshot:
    return take_snapshot(source, tmp_path / "toolkit" / "copy", clock=CLOCK)


def files_of(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


# ----- the snapshot: copy, then sha256 of sorted (relpath, sha)


def test_a_snapshot_is_a_whole_copy_with_the_hash_the_spec_gives(source: Path, snapshot: Snapshot) -> None:
    assert files_of(snapshot.path) == files_of(source)
    lines = sorted((path, hashlib.sha256(data).hexdigest()) for path, data in files_of(source).items())

    assert snapshot.sha256 == hashlib.sha256(
        "\n".join(f"{path}\t{sha}" for path, sha in lines).encode()).hexdigest()
    assert snapshot.taken_at == NOW
    assert snapshot.files == len(lines)


def test_the_same_files_elsewhere_have_the_same_hash_and_any_change_another(
        source: Path, tmp_path: Path, snapshot: Snapshot) -> None:
    again = take_snapshot(source, tmp_path / "toolkit" / "again", clock=CLOCK)
    assert again.sha256 == snapshot.sha256

    (source / "scripts" / "say.py").write_bytes(b"# changed\n")
    changed = take_snapshot(source, tmp_path / "toolkit" / "changed", clock=CLOCK)
    assert changed.sha256 != snapshot.sha256

    (source / "scripts" / "say.py").write_bytes(SAY.encode())
    (source / "reference" / "baseline.json").rename(source / "reference" / "other.json")
    renamed = take_snapshot(source, tmp_path / "toolkit" / "renamed", clock=CLOCK)
    assert renamed.sha256 != snapshot.sha256


def test_the_toolkit_folder_is_only_ever_read(source: Path, tmp_path: Path) -> None:
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.rglob("*") if p.is_file()}

    made = take_snapshot(source, tmp_path / "toolkit" / "copy", clock=CLOCK)
    run_tool(made, "scripts/with_sibling.py", [])

    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.rglob("*") if p.is_file()}
    assert after == before


def test_what_python_or_a_sync_leaves_behind_is_no_part_of_a_toolkit(source: Path, tmp_path: Path,
                                                                     snapshot: Snapshot) -> None:
    (source / "scripts" / "__pycache__").mkdir()
    (source / "scripts" / "__pycache__" / "say.cpython-314.pyc").write_bytes(b"compiled")
    (source / "desktop.ini").write_bytes(b"[.ShellClassInfo]")
    (source / "~$note.docx").write_bytes(b"open in Word")

    made = take_snapshot(source, tmp_path / "toolkit" / "tidied", clock=CLOCK)

    assert made.sha256 == snapshot.sha256
    assert not (made.path / "scripts" / "__pycache__").exists()


@pytest.mark.parametrize("make", [
    lambda folder: None,
    lambda folder: folder.mkdir(),
    lambda folder: folder.write_bytes(b"a file"),
], ids=["not there", "empty", "a file"])
def test_a_toolkit_with_nothing_to_copy_is_refused_in_plain_words(tmp_path: Path, make) -> None:
    folder = tmp_path / "_NG_Automation"
    make(folder)

    with pytest.raises(ToolkitError) as caught:
        take_snapshot(folder, tmp_path / "toolkit" / "copy", clock=CLOCK)

    assert "Traceback" not in str(caught.value) and str(caught.value)
    assert not (tmp_path / "toolkit" / "copy").exists()


def test_a_snapshot_never_takes_the_place_of_one_that_is_there(source: Path, snapshot: Snapshot) -> None:
    before = files_of(snapshot.path)
    (source / "scripts" / "say.py").write_bytes(b"# changed\n")

    with pytest.raises(ToolkitError):
        take_snapshot(source, snapshot.path, clock=CLOCK)

    assert files_of(snapshot.path) == before


def test_a_file_that_cant_be_read_leaves_no_snapshot_at_all(source: Path, tmp_path: Path) -> None:
    dest = tmp_path / "toolkit" / "copy"

    def read(path: Path) -> bytes:
        if path.name == "say.py":
            raise CloudOnly("say.py is only in the cloud.", 362)
        return path.read_bytes()

    with pytest.raises(ToolkitError, match="cloud"):
        take_snapshot(source, dest, clock=CLOCK, read=read)

    assert not dest.exists()
    assert list((tmp_path / "toolkit").iterdir()) == []


def test_a_copy_that_stops_half_way_is_never_there_under_its_name(source: Path, tmp_path: Path,
                                                                  monkeypatch) -> None:
    dest = tmp_path / "toolkit" / "copy"

    def fail(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(toolkit.os, "rename", fail)

    with pytest.raises(ToolkitError):
        take_snapshot(source, dest, clock=CLOCK)

    assert not dest.exists()
    assert list((tmp_path / "toolkit").iterdir()) == []


def test_while_it_copies_the_copy_isnt_there_under_its_name(source: Path, tmp_path: Path) -> None:
    # A crash part way must leave nothing that can be taken for a whole copy.
    dest = tmp_path / "toolkit" / "copy"
    seen = []

    def read(path: Path) -> bytes:
        seen.append(dest.exists())
        return path.read_bytes()

    take_snapshot(source, dest, clock=CLOCK, read=read)

    assert seen and not any(seen)
    assert dest.is_dir()


def test_the_data_folder_says_where_snapshots_live(tmp_path: Path) -> None:
    assert AppPaths(tmp_path).toolkit == tmp_path / "toolkit"


# ----- staged copies: a check never runs in place


def test_a_file_is_staged_through_the_shared_reader_and_the_original_is_left(tmp_path: Path) -> None:
    original = tmp_path / "Mozaik Shared" / "3-4 Classic White.opt"
    original.parent.mkdir()
    original.write_bytes(b"a nest")
    reads = []

    def read(path: Path) -> bytes:
        reads.append(path)
        return path.read_bytes()

    staged = stage_files([original], tmp_path / "staged", read=read)

    assert staged == [tmp_path / "staged" / "3-4 Classic White.opt"]
    assert staged[0].read_bytes() == b"a nest"
    assert reads == [original]


def test_a_file_that_cant_be_staged_says_so_in_plain_words(tmp_path: Path) -> None:
    with pytest.raises(ToolkitError):
        stage_files([tmp_path / "none here.opt"], tmp_path / "staged")


# ----- running a script: what comes back


def test_a_script_runs_and_gives_back_its_exit_code_and_both_outputs(snapshot: Snapshot) -> None:
    run = run_tool(snapshot, "scripts/say.py", ["7", "two words", "--taps"])

    assert isinstance(run, ToolRun)
    assert run.exit_code == 7 and run.timed_out is False
    assert run.stdout.splitlines()[0] == "said 7|two words|--taps"
    assert run.stderr.strip() == "to stderr"


def test_it_runs_in_utf8_mode_whatever_the_pcs_code_page(snapshot: Snapshot) -> None:
    # Build spec 5.13. Jim's scripts ran on Linux in the chat's container until now (risk E4).
    run = run_tool(snapshot, "scripts/say.py", [])

    assert "utf8 mode 1" in run.stdout
    assert "caf\u00e9" in run.stdout


def test_a_script_finds_what_lies_beside_it_as_python_itself_would(snapshot: Snapshot) -> None:
    assert run_tool(snapshot, "scripts/with_sibling.py", []).stdout.strip() == "from the sibling"


def test_a_script_that_breaks_is_a_failed_run_never_a_crash_here(snapshot: Snapshot, tmp_path: Path) -> None:
    (snapshot.path / "scripts" / "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    fresh = Snapshot(snapshot.path, toolkit.hash_of_tree(snapshot.path), NOW, snapshot.files + 1)

    run = run_tool(fresh, "scripts/broken.py", [])

    assert run.exit_code == 1 and run.timed_out is False
    assert "boom" in run.stderr


def test_a_run_writes_nothing_into_the_snapshot_so_its_hash_holds(snapshot: Snapshot) -> None:
    run_tool(snapshot, "scripts/with_sibling.py", [])

    assert toolkit.hash_of_tree(snapshot.path) == snapshot.sha256
    assert not (snapshot.path / "scripts" / "__pycache__").exists()


def test_a_run_starts_in_a_folder_of_its_own_that_goes_when_it_ends(snapshot: Snapshot) -> None:
    (snapshot.path / "where.py").write_text("import os\nprint(os.getcwd())\n", encoding="utf-8")
    fresh = Snapshot(snapshot.path, toolkit.hash_of_tree(snapshot.path), NOW, snapshot.files + 1)
    before = set(snapshot.path.parent.iterdir())

    run = run_tool(fresh, "where.py", [])

    where = Path(run.stdout.strip())
    assert where != snapshot.path and snapshot.path not in where.parents
    assert not where.exists()
    assert set(snapshot.path.parent.iterdir()) == before


# ----- wildcards: Windows doesn't expand them


def test_a_wildcard_is_expanded_first_in_order(snapshot: Snapshot, tmp_path: Path) -> None:
    staged = tmp_path / "staged here"
    staged.mkdir()
    for name in ("B.opt", "A.opt", "C.txt"):
        (staged / name).write_bytes(b"")

    run = run_tool(snapshot, "scripts/say.py", [str(staged / "*.opt"), "--taps"])

    assert run.stdout.splitlines()[0] == f"said {staged / 'A.opt'}|{staged / 'B.opt'}|--taps"


def test_a_wildcard_in_a_folder_with_brackets_is_still_only_the_wildcard(snapshot: Snapshot,
                                                                         tmp_path: Path) -> None:
    staged = tmp_path / "job [2]"
    staged.mkdir()
    (staged / "A.opt").write_bytes(b"")

    run = run_tool(snapshot, "scripts/say.py", [str(staged / "*.opt")])

    assert run.stdout.splitlines()[0] == f"said {staged / 'A.opt'}"


def test_a_wildcard_matching_nothing_never_reaches_a_tool(snapshot: Snapshot, tmp_path: Path) -> None:
    # A tool handed a literal *.opt could read nothing and exit 0, which must not look like a pass.
    with pytest.raises(ToolkitError):
        run_tool(snapshot, "scripts/say.py", [str(tmp_path / "*.opt")])


def test_a_relative_wildcard_is_refused_since_every_path_here_is_absolute(snapshot: Snapshot) -> None:
    with pytest.raises(ToolkitError):
        run_tool(snapshot, "scripts/say.py", ["*.opt"])


# ----- what is refused before anything runs


@pytest.mark.parametrize("script", ["scripts/none here.py", "../outside.py", "C:/Windows/System32/x.py"],
                         ids=["not there", "above it", "elsewhere"])
def test_only_a_script_in_the_snapshot_runs(snapshot: Snapshot, script: str) -> None:
    (snapshot.path.parent / "outside.py").write_text("print('ran')\n", encoding="utf-8")

    with pytest.raises(ToolkitError):
        run_tool(snapshot, script, [])


def test_a_snapshot_changed_since_it_was_taken_runs_nothing(snapshot: Snapshot, tmp_path: Path) -> None:
    mark = tmp_path / "it ran"
    (snapshot.path / "scripts" / "say.py").write_text(
        f"open(r'{mark}', 'w', encoding='utf-8').close()\n", encoding="utf-8")

    with pytest.raises(ToolkitError):
        run_tool(snapshot, "scripts/say.py", [])

    assert not mark.exists()


# ----- the Job Object: a timeout kills all of it


def _stops_growing(path: Path) -> bool:
    for _ in range(40):
        if path.exists():
            break
        sleep(0.05)
    size = path.stat().st_size if path.exists() else -1
    sleep(0.5)
    return (path.stat().st_size if path.exists() else -1) == size


def _said_before_the_kill(snapshot: Snapshot, args: list[str], *, within_s: float) -> Any:
    """A run that timed out, retried while nothing was said yet: a Python start now and then takes
    longer than the timeout on a busy PC (measured 2026-10-31 with three builds running), and then a
    kill comes before the script's first line. Every run must still time out within its time."""
    for _ in range(3):
        began = monotonic()
        run = run_tool(snapshot, "scripts/hang.py", args, timeout_s=8)
        assert run.timed_out is True
        assert run.exit_code is None            # never to be read as a clean exit
        assert monotonic() - began < within_s
        if run.stdout.strip():
            break
    return run


def test_a_timeout_kills_the_script_and_keeps_what_it_said(snapshot: Snapshot) -> None:
    run = _said_before_the_kill(snapshot, [], within_s=30)

    assert run.stdout.strip() == "started"


def test_a_timeout_kills_what_the_script_started_too(snapshot: Snapshot, tmp_path: Path) -> None:
    # A Python start now and then takes 3 s on this PC, and here two come in a row, so a child that
    # hadn't yet started shows nothing and is tried again.
    beat = tmp_path / "the child is alive"

    for _ in range(3):
        run = run_tool(snapshot, "scripts/hang.py", [str(beat)], timeout_s=8)
        assert run.timed_out is True
        if beat.exists():
            break
    assert beat.exists(), "the child never ran, so this shows nothing"
    assert _stops_growing(beat)


def test_a_child_left_running_by_a_script_that_ended_is_killed_with_the_job(snapshot: Snapshot,
                                                                            tmp_path: Path) -> None:
    beat = tmp_path / "the child is alive"

    run = run_tool(snapshot, "scripts/hang.py", [str(beat), "leave"], timeout_s=30)

    assert run.exit_code == 0 and run.timed_out is False
    assert beat.exists(), "the child never ran, so this shows nothing"
    assert _stops_growing(beat)


def test_a_timeout_ends_at_once_with_what_was_said_even_with_a_child_holding_its_output(
        snapshot: Snapshot, tmp_path: Path) -> None:
    # Killing the script alone would leave its child holding the pipes, so reading them would hang.
    run = _said_before_the_kill(snapshot, [str(tmp_path / "beat"), "inherit"],
                                within_s=8 + toolkit._AFTER_A_KILL_S)

    assert run.stdout.strip() == "started"


def test_a_script_is_in_its_job_before_any_line_of_it_runs(snapshot: Snapshot, monkeypatch) -> None:
    # Started suspended, put in the job, then let go: so even a first line that starts a child can't
    # slip out. What this can show from here: the order of the three calls.
    calls = []
    real_assign, real_resume = toolkit._assign, toolkit._resume
    monkeypatch.setattr(toolkit, "_assign", lambda *a: (calls.append("assign"), real_assign(*a))[1])
    monkeypatch.setattr(toolkit, "_resume", lambda *a: (calls.append("resume"), real_resume(*a))[1])

    assert run_tool(snapshot, "scripts/say.py", []).exit_code == 0
    assert calls == ["assign", "resume"]


def test_a_process_that_cant_be_put_in_its_job_never_runs(snapshot: Snapshot, monkeypatch,
                                                          tmp_path: Path) -> None:
    mark = tmp_path / "it ran"
    (snapshot.path / "mark.py").write_text(
        f"open(r'{mark}', 'w', encoding='utf-8').close()\n", encoding="utf-8")
    fresh = Snapshot(snapshot.path, toolkit.hash_of_tree(snapshot.path), NOW, snapshot.files + 1)

    def refuse(*_: object) -> None:
        raise OSError("access denied")

    monkeypatch.setattr(toolkit, "_assign", refuse)

    with pytest.raises(ToolkitError):
        run_tool(fresh, "mark.py", [])

    sleep(0.5)
    assert not mark.exists()


# ----- which command runs it


def test_from_the_repo_it_uses_this_python_in_utf8_mode() -> None:
    command = toolkit.tool_command(r"C:\made-up\say.py", ["--taps"])

    assert command[:3] == [sys.executable, "-X", "utf8"]
    assert command[-3:] == ["run-tool", r"C:\made-up\say.py", "--taps"]


def test_in_a_build_it_calls_the_exe_itself_with_no_python_of_its_own(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\made-up\NGRunner.exe")

    assert toolkit.tool_command(r"C:\made-up\say.py", ["--taps", "x"]) == [
        r"C:\made-up\NGRunner.exe", "run-tool", r"C:\made-up\say.py", "--taps", "x"]


# ----- the run-tool command, in this process


@pytest.fixture
def script(tmp_path: Path, monkeypatch) -> Path:
    """Running a script changes sys.argv and sys.path, as Python's own start would, so both go back."""
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    monkeypatch.setattr(sys, "path", list(sys.path))
    return tmp_path / "made_up_tool.py"


@pytest.mark.parametrize("body, code", [
    ("pass", 0), ("import sys; sys.exit()", 0), ("import sys; sys.exit(0)", 0),
    ("import sys; sys.exit(3)", 3), ("import sys; sys.exit('a plain reason')", 1),
    ("raise ValueError('no')", 1), ("if __name__ == '__main__':\n    raise SystemExit(4)", 4),
], ids=["ends", "exit", "exit 0", "exit 3", "exit words", "raises", "as __main__"])
def test_the_command_gives_the_exit_code_python_would(script: Path, body: str, code: int, capsys) -> None:
    script.write_text(body, encoding="utf-8")

    assert cli.main(["run-tool", str(script)], environ={}) == code


def test_the_command_passes_arguments_that_look_like_its_own_flags(script: Path, capsys) -> None:
    script.write_text("import sys; print('|'.join(sys.argv[1:]))", encoding="utf-8")

    assert cli.main(["run-tool", str(script), "--taps", "--version", "--self-test", "x y"],
                    environ={}) == 0
    assert capsys.readouterr().out.strip() == "--taps|--version|--self-test|x y"


def test_the_command_names_the_script_as_argv_0_and_needs_no_data_folder(script: Path, capsys) -> None:
    script.write_text("import sys; print(sys.argv[0])", encoding="utf-8")

    assert cli.main(["run-tool", str(script)], environ={}) == 0     # no NGR_APP_DATA, no LOCALAPPDATA
    assert capsys.readouterr().out.strip() == str(script)


def test_the_command_says_what_went_wrong_on_stderr_and_nothing_of_its_own_on_stdout(script: Path,
                                                                                    capsys) -> None:
    script.write_text("import sys; print('out'); sys.exit('a plain reason')", encoding="utf-8")

    cli.main(["run-tool", str(script)], environ={})

    captured = capsys.readouterr()
    assert captured.out.strip() == "out"
    assert "a plain reason" in captured.err


def test_the_command_with_no_script_is_a_usage_error() -> None:
    assert cli.main(["run-tool"], environ={}) == 2


# ----- the seam: what reads a tool's output is left out, and fails closed


CHECKED = CheckedFiles("a" * 64, (("TESS01.TAP", "b" * 64),), (("Room1.des", "c" * 64),))
RAN = ToolRun(exit_code=0, stdout="ALL CLEAN", stderr="", timed_out=False)


@pytest.mark.parametrize("tool", [SHEET_SIZE, BR_CAM_NEST, "anything else"])
def test_with_no_reader_even_a_clean_exit_is_could_not_verify(tool: str) -> None:
    verdict = tool_verdict(tool, RAN, CHECKED, readers={})

    assert verdict.code == COULD_NOT_VERIFY
    assert verdict.tool == tool and verdict.checked == CHECKED


@pytest.mark.parametrize("tool", [SHEET_SIZE, BR_CAM_NEST, "anything else"])
def test_words_no_reader_has_seen_are_could_not_verify_even_with_a_clean_exit(tool: str) -> None:
    assert tool_verdict(tool, RAN, CHECKED).code == COULD_NOT_VERIFY


def test_no_tool_has_a_reader_but_the_two_the_source_pins() -> None:
    assert set(READERS) == {SHEET_SIZE, BR_CAM_NEST}


@pytest.mark.parametrize("reader, code", [
    (lambda run: (PASSED, ""), PASSED),
    (lambda run: (FAILED, "A fixed line."), FAILED),
    (lambda run: (COULD_NOT_VERIFY, ""), COULD_NOT_VERIFY),
    (lambda run: ("passed ", ""), COULD_NOT_VERIFY),
    (lambda run: ("OK", ""), COULD_NOT_VERIFY),
    (lambda run: (None, ""), COULD_NOT_VERIFY),
    (lambda run: "passed", COULD_NOT_VERIFY),
    (lambda run: None, COULD_NOT_VERIFY),
    (lambda run: 1 / 0, COULD_NOT_VERIFY),
], ids=["passed", "failed", "unsure", "spaced", "another word", "none", "a bare word", "nothing",
        "a reader that breaks"])
def test_only_a_code_it_knows_comes_through_and_anything_else_is_could_not_verify(reader, code) -> None:
    assert tool_verdict(SHEET_SIZE, RAN, CHECKED, readers={SHEET_SIZE: reader}).code == code


@pytest.mark.parametrize("run", [
    ToolRun(exit_code=None, stdout="ALL CLEAN", stderr="", timed_out=True),
    ToolRun(exit_code=0, stdout="ALL CLEAN", stderr="", timed_out=True),
    ToolRun(exit_code=None, stdout="ALL CLEAN", stderr="", timed_out=False),
], ids=["timed out", "timed out with an exit code", "no exit code"])
def test_a_run_that_never_finished_cant_pass_whatever_a_reader_would_say(run: ToolRun) -> None:
    verdict = tool_verdict(SHEET_SIZE, run, CHECKED, readers={SHEET_SIZE: lambda _: (PASSED, "")})

    assert verdict.code == COULD_NOT_VERIFY


def test_a_failure_says_its_fixed_line_or_nothing_of_a_tools_own_output() -> None:
    verdict = tool_verdict(SHEET_SIZE, RAN, CHECKED, readers={SHEET_SIZE: lambda _: (FAILED, "A line.")})

    assert verdict.line == "A line."


@pytest.mark.parametrize("tool", [SHEET_SIZE, BR_CAM_NEST, "anything else"])
def test_could_not_verify_is_said_in_words_that_pass_the_lint(tool: str) -> None:
    verdict = tool_verdict(tool, RAN, CHECKED)

    assert verdict.line and check_line(verdict.line) == []
    assert "ALL CLEAN" not in verdict.line


# ----- Jim's two checks from the adopted copy, on staged copies (made-up scripts, real-format output)

FAKE_CHECK = """
import os, sys
log = os.environ.get("NGR_TEST_ARGV_LOG")
if log:
    with open(log, "a", encoding="utf-8") as out:
        out.write(repr(sys.argv[1:]) + chr(10))
here = os.path.dirname(os.path.abspath(__file__))
name = os.path.splitext(os.path.basename(__file__))[0]
with open(os.path.join(here, name + ".txt"), encoding="utf-8") as said:
    sys.stdout.write(said.read())
with open(os.path.join(here, name + ".exit"), encoding="utf-8") as code:
    sys.exit(int(code.read()))
"""
SIZE_OK = ('   ok 3-4 Egger Classic White                      nested 49" x 97"   expected 49" x 97"\n'
           "      1 physical sheet(s) on 1 pattern(s), 0 flip pass(es), 1 g-code run(s)\n"
           "      SHEET COUNT FOR THE TRACKER AND THE JOB SHEET = 1\n"
           "      ok every program the nest names is on disk, nothing stale: TESS01.TAP\n")
CAMS_DOUBLED = ("3-4 Egger Classic White.opt\n"
                "   !! R1C8    Fixed Shelf      TWO 20mm HOLES ON TOP OF EACH OTHER at X=9.525/9.5 Y=260"
                " - 0.025mm apart (x2 on the sheet)\n"
                "        -> the NEST doubles the housing even though the .des is clean: two joint"
                " fastener templates match these sections. Fix the templates and RE-OPTIMIZE;"
                " do not cut this nest.\n")


@pytest.fixture
def adopted(tmp_path: Path, monkeypatch):
    """A made-up toolkit holding the two checks, copied and adopted, and one material to check."""
    from helpers_opt import MOZAIK_NESTED
    from helpers_tap import CLEAN
    from ngrunner.guards.nestfiles import ToCheck
    from ngrunner.store import add_snapshot, open_store

    source = tmp_path / "_NG_Automation" / "scripts"
    source.mkdir(parents=True)
    for name, said, code in (("sheet_size_check", SIZE_OK, 0), ("br_cam_check", CAMS_DOUBLED, 1)):
        (source / f"{name}.py").write_text(FAKE_CHECK, encoding="utf-8")
        (source / f"{name}.txt").write_text(said, encoding="utf-8")
        (source / f"{name}.exit").write_text(str(code), encoding="utf-8")
    folder = tmp_path / "data" / "toolkit"
    made = take_snapshot(source.parent, folder / "taking", clock=CLOCK)
    os.rename(made.path, folder / made.sha256)
    store = open_store(tmp_path / "ngrunner.db")
    add_snapshot(store, taken_at=NOW, sha256=made.sha256, adopted=True)

    nest = tmp_path / "Mozaik Shared" / "Jobs" / "Testerson_101426" / "3-4 Egger Classic White.opt"
    nest.parent.mkdir(parents=True)
    nest.write_bytes(MOZAIK_NESTED)
    programs = tmp_path / "NG Jobs" / "Testerson_101426" / "3-4 Egger Classic White"
    programs.mkdir(parents=True)
    (programs / "TESS01.TAP").write_bytes(CLEAN)
    files = CheckedFiles(hashlib.sha256(MOZAIK_NESTED).hexdigest(),
                         (("TESS01.TAP", hashlib.sha256(CLEAN).hexdigest()),), (("Room1.des", "c" * 64),))
    job = ToCheck("Testerson_101426", "3-4 Egger Classic White", nest, nest.parent, programs, files)
    log = tmp_path / "argv.log"
    monkeypatch.setenv("NGR_TEST_ARGV_LOG", str(log))
    return SimpleNamespace(store=store, folder=folder, job=job, log=log, work=tmp_path / "data" / "staging",
                           nest=nest, programs=programs)


def test_no_copy_adopted_is_no_checks_which_is_today(tmp_path: Path) -> None:
    from ngrunner.store import add_snapshot, open_store

    store = open_store(tmp_path / "ngrunner.db")
    assert toolkit.adopted_snapshot(store, tmp_path / "toolkit") is None
    add_snapshot(store, taken_at=NOW, sha256="d" * 64, adopted=False)
    (tmp_path / "toolkit" / ("d" * 64)).mkdir(parents=True)     # taken, there, and not adopted
    assert toolkit.adopted_snapshot(store, tmp_path / "toolkit") is None


def test_an_adopted_copy_that_is_gone_is_no_checks(adopted) -> None:
    shutil.rmtree(adopted.folder)

    assert toolkit.adopted_snapshot(adopted.store, adopted.folder) is None


def test_the_two_checks_run_from_the_adopted_copy_on_staged_copies_and_are_read(adopted) -> None:
    snapshot = toolkit.adopted_snapshot(adopted.store, adopted.folder)
    before = (adopted.nest.read_bytes(), adopted.nest.stat().st_mtime_ns)

    tools, design = toolkit.nest_checks_from(snapshot, adopted.work)(adopted.job)

    assert [(t.tool, t.code, t.line) for t in tools] == [
        (SHEET_SIZE, PASSED, ""), (BR_CAM_NEST, FAILED, "2 holes are doubled in the nest")]
    assert all(t.checked == adopted.job.files for t in tools)
    assert design is None                           # no reader of des_cutlist: no Nest checked
    given = [eval(line) for line in adopted.log.read_text(encoding="utf-8").splitlines()]
    nest, taps = Path(given[0][0]), Path(given[0][2])
    assert given[0][1] == "--taps" and given[1] == ["--nest", str(nest)]
    assert nest.name == "3-4 Egger Classic White.opt" and adopted.work in nest.parents
    assert (taps / "3-4 Egger Classic White" / "TESS01.TAP").name == "TESS01.TAP"
    assert adopted.work in taps.parents
    assert not nest.exists()                        # the staged copies go when it is done
    assert (adopted.nest.read_bytes(), adopted.nest.stat().st_mtime_ns) == before


def test_a_nest_changed_since_it_was_read_runs_no_check_and_is_could_not_verify(adopted) -> None:
    adopted.nest.write_bytes(adopted.nest.read_bytes() + b" ")
    snapshot = toolkit.adopted_snapshot(adopted.store, adopted.folder)

    tools, _ = toolkit.nest_checks_from(snapshot, adopted.work)(adopted.job)

    assert {t.code for t in tools} == {COULD_NOT_VERIFY}
    assert not adopted.log.exists()


def test_a_program_changed_since_it_was_read_runs_no_check_either(adopted) -> None:
    (adopted.programs / "TESS01.TAP").write_bytes(b"something else")
    snapshot = toolkit.adopted_snapshot(adopted.store, adopted.folder)

    tools, _ = toolkit.nest_checks_from(snapshot, adopted.work)(adopted.job)

    assert {t.code for t in tools} == {COULD_NOT_VERIFY}
    assert not adopted.log.exists()


def test_the_guards_seam_uses_the_adopted_copy_and_none_without_one(adopted) -> None:
    from ngrunner.guards import watch

    ctx = SimpleNamespace(store=adopted.store, paths=SimpleNamespace(toolkit=adopted.folder,
                                                                      staging=adopted.work))
    assert callable(watch.nest_checks(ctx))
    shutil.rmtree(adopted.folder)
    assert watch.nest_checks(ctx) is None
