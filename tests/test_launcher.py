"""The launcher: start the newest version whose self-test passed; fall back after two failed starts.

Starts are faked at the process boundary: a fake process plays one start of the app (comes up,
stops with an exit code, or hangs) and reports "up" through record_up, exactly as the real app
does. One test starts a real process. The mutex, browser and message box are injected, so no
test touches a real NG Runner.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ngrunner import launcher
from ngrunner.clock import FakeClock
from ngrunner.config import AppPaths

ROOT = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 10, 29, 20, 0, tzinfo=timezone.utc)


# ----- fakes at the process boundary


class FakeProcess:
    """One start of the app: 'up' (reports up, keeps running), 'exit:<code>', or 'hang'."""

    def __init__(self, paths: AppPaths, attempt: str | None, behaviour: str, clock: FakeClock):
        self.paths, self.attempt, self.behaviour, self.clock = paths, attempt, behaviour, clock
        self.pid = 4242
        self.killed = False

    def poll(self) -> int | None:
        if self.behaviour == "up" and self.attempt:
            launcher.record_up(self.paths, self.attempt, clock=self.clock)
        if self.behaviour.startswith("exit:"):
            return int(self.behaviour.split(":")[1])
        return None

    def kill(self) -> None:
        self.killed = True


class FakeStarts:
    """Plays each version's starts in order, and remembers what was started with which args."""

    def __init__(self, paths: AppPaths, clock: FakeClock, plans: dict[str, list[str]]):
        self.paths, self.clock, self.plans = paths, clock, plans
        self.calls: list[tuple[str, list[str]]] = []
        self.processes: list[FakeProcess] = []

    def __call__(self, installed: launcher.Installed, args: list[str], log: Path) -> FakeProcess:
        self.calls.append((installed.version, list(args)))
        behaviour = self.plans[installed.version].pop(0)
        if behaviour == "cannot-start":
            raise PermissionError(5, "Access is denied")
        attempt = args[args.index("--attempt") + 1] if "--attempt" in args else None
        process = FakeProcess(self.paths, attempt, behaviour, self.clock)
        self.processes.append(process)
        return process

    def versions(self) -> list[str]:
        return [version for version, _ in self.calls]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    return AppPaths(tmp_path / "data" / "NGRunner")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "Programs" / "NGRunner"


def install(root: Path, *versions: str) -> None:
    for version in versions:
        folder = root / f"app-{version}"
        folder.mkdir(parents=True)
        (folder / launcher.APP_EXE).write_bytes(b"MZ")


def passed(paths: AppPaths, clock: FakeClock, *versions: str) -> None:
    for version in versions:
        launcher.record_self_test(paths, version, [], clock=clock)


def run(root: Path, paths: AppPaths, clock: FakeClock, starts: FakeStarts, *, mode: str = "logon",
        running: bool = False, told: list[str] | None = None,
        opened: list[AppPaths] | None = None) -> launcher.Launched:
    return launcher.launch(
        root, paths, mode=mode, start=starts, running=lambda: running,
        open_board=lambda p: bool((opened if opened is not None else []).append(p)) or True,
        tell=(told.append if told is not None else None),
        clock=clock, sleep=lambda s: None, timeout_s=5, poll_s=1,
    )


# ----- which version starts


def test_it_starts_the_newest_version_whose_self_test_passed(root, paths, clock):
    install(root, "0.0.1", "0.0.2", "0.0.3", "0.0.4")
    passed(paths, clock, "0.0.1", "0.0.2")
    launcher.record_self_test(paths, "0.0.3", ["ngrunner.store could not be imported"], clock=clock)
    starts = FakeStarts(paths, clock, {"0.0.2": ["up"]})            # 0.0.4: never self-tested
    result = run(root, paths, clock, starts)
    assert (result.outcome, result.version) == ("started", "0.0.2")
    assert starts.versions() == ["0.0.2"]
    assert launcher.pending_notes(paths) == []


def test_versions_are_ordered_as_numbers_and_unfinished_copies_are_ignored(root):
    install(root, "0.0.9", "0.0.10", "1.2")
    (root / "app-0.0.11.partial-3fa2").mkdir(parents=True)        # an install still copying
    (root / "app-0.0.12").mkdir()                                  # no NGRunner.exe in it
    (root / "app-latest").mkdir()
    (root / "launcher").mkdir()
    assert [i.version for i in launcher.installed_versions(root)] == ["1.2", "0.0.10", "0.0.9"]
    assert launcher.installed_versions(root / "missing") == []


def test_nothing_starts_where_nothing_passed_its_self_test(root, paths, clock):
    install(root, "0.0.1")
    starts = FakeStarts(paths, clock, {})
    told: list[str] = []
    result = run(root, paths, clock, starts, mode="open", told=told)
    assert result.outcome == "nothing_to_start" and starts.calls == []
    assert told == ["NG Runner could not start, so nothing is being filed."
                    " Work in chat as before, and tell Sulaiman."]


# ----- failed starts and falling back


def test_two_failed_starts_fall_back_to_the_previous_good_version_and_leave_a_note(root, paths, clock):
    install(root, "0.0.1", "0.0.2")
    passed(paths, clock, "0.0.1", "0.0.2")
    starts = FakeStarts(paths, clock, {"0.0.2": ["exit:1", "exit:1"], "0.0.1": ["up"]})
    result = run(root, paths, clock, starts)
    assert (result.outcome, result.version) == ("started", "0.0.1")
    assert starts.versions() == ["0.0.2", "0.0.2", "0.0.1"]
    [note] = launcher.pending_notes(paths)
    assert note.text == ("NG Runner 0.0.2 failed to start twice (it stopped with exit code 1"
                         " before it was up), so 0.0.1 was started instead.")
    assert note.at == T0


def test_one_failed_start_is_tried_again_and_a_good_start_clears_the_count(root, paths, clock):
    install(root, "0.0.1", "0.0.2")
    passed(paths, clock, "0.0.1", "0.0.2")
    starts = FakeStarts(paths, clock, {"0.0.2": ["exit:3", "up", "exit:1", "up"]})
    assert run(root, paths, clock, starts).version == "0.0.2"
    assert run(root, paths, clock, starts).version == "0.0.2"      # one failure again: no fallback
    assert starts.versions() == ["0.0.2"] * 4
    assert launcher.pending_notes(paths) == []


def test_a_start_that_never_comes_up_is_stopped_and_counts_as_failed(root, paths, clock):
    install(root, "0.0.1", "0.0.2")
    passed(paths, clock, "0.0.1", "0.0.2")
    starts = FakeStarts(paths, clock, {"0.0.2": ["hang", "hang"], "0.0.1": ["up"]})
    result = run(root, paths, clock, starts)
    assert result.version == "0.0.1"
    assert [p.killed for p in starts.processes] == [True, True, False]
    [note] = launcher.pending_notes(paths)
    assert "was not up after 5 seconds" in note.text


def test_a_version_that_cannot_even_be_started_counts_as_failed(root, paths, clock):
    install(root, "0.0.1", "0.0.2")
    passed(paths, clock, "0.0.1", "0.0.2")
    starts = FakeStarts(paths, clock, {"0.0.2": ["cannot-start", "cannot-start"], "0.0.1": ["up"]})
    assert run(root, paths, clock, starts).version == "0.0.1"
    [note] = launcher.pending_notes(paths)
    assert "(it could not be started: Access is denied)" in note.text


def test_a_version_that_failed_twice_stays_out_until_its_self_test_passes_again(root, paths, clock):
    install(root, "0.0.1", "0.0.2")
    passed(paths, clock, "0.0.1", "0.0.2")
    starts = FakeStarts(paths, clock, {"0.0.2": ["exit:1", "exit:1", "up"], "0.0.1": ["up", "up"]})
    run(root, paths, clock, starts)
    assert run(root, paths, clock, starts).version == "0.0.1"      # 0.0.2 not tried again
    passed(paths, clock, "0.0.2")                                  # Sulaiman re-ran its self-test
    assert run(root, paths, clock, starts).version == "0.0.2"
    assert starts.versions() == ["0.0.2", "0.0.2", "0.0.1", "0.0.1", "0.0.2"]


def test_when_every_version_fails_jim_is_told_and_the_note_waits_for_a_working_one(root, paths, clock):
    install(root, "0.0.1", "0.0.2")
    passed(paths, clock, "0.0.1", "0.0.2")
    starts = FakeStarts(paths, clock, {"0.0.2": ["exit:1", "exit:1"], "0.0.1": ["exit:2", "exit:2"]})
    told: list[str] = []
    result = run(root, paths, clock, starts, mode="open", told=told)
    assert result.outcome == "nothing_to_start" and len(told) == 1
    [note] = launcher.pending_notes(paths)
    assert note.text == ("No version of NG Runner could start: 0.0.2 (it stopped with exit code 1"
                         " before it was up); 0.0.1 (it stopped with exit code 2 before it was up).")


def test_the_scheduled_tasks_never_show_jim_a_message(root, paths, clock):
    install(root, "0.0.1")
    starts = FakeStarts(paths, clock, {})
    told: list[str] = []
    for mode in ("logon", "ensure"):
        assert run(root, paths, clock, starts, mode=mode, told=told).outcome == "nothing_to_start"
    assert told == []


# ----- already running, and Jim's Quit


def test_the_ensure_task_leaves_a_running_runner_alone(root, paths, clock):
    install(root, "0.0.1")
    passed(paths, clock, "0.0.1")
    starts = FakeStarts(paths, clock, {})
    opened: list[AppPaths] = []
    result = run(root, paths, clock, starts, mode="ensure", running=True, opened=opened)
    assert result.outcome == "already_running" and starts.calls == [] and opened == []


def test_the_start_menu_opens_the_running_board_instead_of_a_second_runner(root, paths, clock):
    install(root, "0.0.1")
    passed(paths, clock, "0.0.1")
    starts = FakeStarts(paths, clock, {})
    opened: list[AppPaths] = []
    result = run(root, paths, clock, starts, mode="open", running=True, opened=opened)
    assert result.outcome == "already_running" and starts.calls == [] and opened == [paths]


def test_a_start_that_finds_another_runner_up_is_not_a_failure(root, paths, clock):
    install(root, "0.0.1")
    passed(paths, clock, "0.0.1")
    starts = FakeStarts(paths, clock, {"0.0.1": ["exit:0"]})
    probes = iter([False, True])        # not running when checked; running once the start exits
    result = launcher.launch(root, paths, mode="logon", start=starts, running=lambda: next(probes),
                             clock=clock, sleep=lambda s: None, timeout_s=5, poll_s=1)
    assert result.outcome == "already_running"
    assert launcher.pending_notes(paths) == []


def test_a_version_retired_just_before_another_start_came_up_is_still_reported(root, paths, clock):
    install(root, "0.0.1", "0.0.2")
    passed(paths, clock, "0.0.1", "0.0.2")
    starts = FakeStarts(paths, clock, {"0.0.2": ["exit:1", "exit:1"], "0.0.1": ["exit:0"]})
    probes = iter([False, True])          # not running at first; running when 0.0.1 exits 0
    result = launcher.launch(root, paths, mode="logon", start=starts, running=lambda: next(probes),
                             clock=clock, sleep=lambda s: None, timeout_s=5, poll_s=1)
    assert result.outcome == "already_running"
    [note] = launcher.pending_notes(paths)
    assert note.text == ("NG Runner 0.0.2 failed to start twice (it stopped with exit code 1"
                         " before it was up), and another start of NG Runner came up meanwhile.")


def test_after_jim_quits_only_signing_in_or_the_start_menu_starts_it_again(root, paths, clock):
    install(root, "0.0.1")
    passed(paths, clock, "0.0.1")
    launcher.record_quit(paths, clock=clock)
    starts = FakeStarts(paths, clock, {"0.0.1": ["up", "up"]})
    assert run(root, paths, clock, starts, mode="ensure").outcome == "stopped_by_user"
    assert run(root, paths, clock, starts, mode="logon").outcome == "started"
    assert run(root, paths, clock, starts, mode="ensure", running=False).outcome == "started"
    assert starts.versions() == ["0.0.1", "0.0.1"]


def test_the_app_is_told_its_attempt_and_whether_to_open_the_board(root, paths, clock):
    install(root, "0.0.1")
    passed(paths, clock, "0.0.1")
    starts = FakeStarts(paths, clock, {"0.0.1": ["up", "up"]})
    run(root, paths, clock, starts, mode="open")
    run(root, paths, clock, starts, mode="ensure")
    (_, opening), (_, background) = starts.calls
    assert opening[:2] == ["run", "--attempt"] and opening[3:] == ["--open-board"]
    assert background[:2] == ["run", "--attempt"] and len(background) == 3


# ----- notes and the state file


def test_notes_wait_until_the_running_version_has_taken_them(root, paths, clock):
    install(root, "0.0.1", "0.0.2")
    passed(paths, clock, "0.0.1", "0.0.2")
    run(root, paths, clock, FakeStarts(paths, clock, {"0.0.2": ["exit:1", "exit:1"], "0.0.1": ["up"]}))
    notes = launcher.pending_notes(paths)
    assert len(notes) == 1 and launcher.pending_notes(paths) == notes
    launcher.ack_notes(paths, [n.id for n in notes])
    assert launcher.pending_notes(paths) == []


def test_up_from_an_unknown_attempt_changes_nothing(paths, clock):
    assert launcher.record_up(paths, "f" * 32, clock=clock) is False


def test_a_damaged_start_up_record_starts_nothing_until_a_self_test_rewrites_it(root, paths, clock):
    install(root, "0.0.1")
    passed(paths, clock, "0.0.1")
    paths.launcher_state.write_text("{ not json", encoding="utf-8")
    starts = FakeStarts(paths, clock, {"0.0.1": ["up"]})
    told: list[str] = []
    result = run(root, paths, clock, starts, mode="open", told=told)
    assert result.outcome == "nothing_to_start" and starts.calls == []
    assert "start-up record" in result.reason and len(told) == 1
    passed(paths, clock, "0.0.1")
    assert run(root, paths, clock, starts).outcome == "started"
    kept = list(paths.app_data.glob("launcher.json.damaged-*"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == "{ not json"


def test_a_record_that_can_not_be_written_is_a_plain_outcome_never_an_error_box(root, paths, clock,
                                                                                 monkeypatch):
    # The launcher build has no console: an exception would be a traceback box on Jim's PC, and
    # the 10-minute task (IgnoreNew) would not run again until someone closed it.
    install(root, "0.0.1")
    passed(paths, clock, "0.0.1")

    def disk_full(*args, **kwargs):
        raise OSError(28, "There is not enough space on the disk")

    monkeypatch.setattr(launcher, "_save", disk_full)
    starts = FakeStarts(paths, clock, {})
    told: list[str] = []
    result = run(root, paths, clock, starts, mode="open", told=told)
    assert result.outcome == "nothing_to_start" and starts.calls == []
    assert "There is not enough space on the disk" in result.reason and len(told) == 1
    assert "There is not enough space on the disk" in (
        paths.logs / "launcher.log").read_text(encoding="utf-8")


def test_a_start_lock_that_windows_refuses_is_a_plain_outcome_too(root, paths, clock, monkeypatch):
    install(root, "0.0.1")

    def refused(*args, **kwargs):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(launcher.single, "acquire", refused)
    result = run(root, paths, clock, FakeStarts(paths, clock, {}), mode="ensure")
    assert result.outcome == "nothing_to_start" and "Access is denied" in result.reason


def test_a_note_that_can_not_be_saved_never_undoes_a_start_that_worked(root, paths, clock, monkeypatch):
    install(root, "0.0.1", "0.0.2")
    passed(paths, clock, "0.0.1", "0.0.2")
    real = launcher._save

    def full_once_a_note_is_due(where, state):
        if state["notes"]:
            raise OSError(28, "There is not enough space on the disk")
        real(where, state)

    monkeypatch.setattr(launcher, "_save", full_once_a_note_is_due)
    starts = FakeStarts(paths, clock, {"0.0.2": ["exit:1", "exit:1"], "0.0.1": ["up"]})
    result = run(root, paths, clock, starts)
    assert (result.outcome, result.version) == ("started", "0.0.1")
    assert launcher.pending_notes(paths) == []


def test_the_launcher_writes_a_log_line_for_every_start(root, paths, clock):
    install(root, "0.0.1")
    passed(paths, clock, "0.0.1")
    run(root, paths, clock, FakeStarts(paths, clock, {"0.0.1": ["up"]}))
    log = (paths.logs / "launcher.log").read_text(encoding="utf-8")
    assert "started 0.0.1" in log and "2026-10-29T20:00:00+00:00" in log


def test_the_state_file_is_plain_json_with_a_version(paths, clock):
    passed(paths, clock, "0.0.1")
    state = json.loads(paths.launcher_state.read_text(encoding="utf-8"))
    assert state["v"] == 1 and state["versions"]["0.0.1"]["self_test"]["ok"] is True


# ----- a real process


FAKE_APP = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, {root!r})
    from ngrunner import launcher
    from ngrunner.clock import SystemClock
    from ngrunner.config import app_paths
    args = sys.argv[1:]
    print("the fake app is starting with", " ".join(args[:1]), file=sys.stderr, flush=True)
    launcher.record_up(app_paths(), args[args.index("--attempt") + 1], clock=SystemClock())
    time.sleep(120)
""")


def test_a_real_start_runs_hidden_and_keeps_the_apps_output(root, paths, clock, tmp_path, monkeypatch):
    monkeypatch.setenv("NGR_APP_DATA", str(paths.app_data))
    script = tmp_path / "fake_app.py"
    script.write_text(FAKE_APP.format(root=str(ROOT)), encoding="utf-8")
    install(root, "0.0.1")
    passed(paths, clock, "0.0.1")

    def start(installed: launcher.Installed, args: list[str], log: Path):
        return launcher.start_process(installed, args, log, command=[sys.executable, str(script)])

    result = launcher.launch(root, paths, mode="logon", start=start, running=lambda: False,
                             clock=clock, timeout_s=90, poll_s=0.2)
    try:
        assert (result.outcome, result.version) == ("started", "0.0.1") and result.pid
        output = (paths.logs / launcher.APP_OUTPUT).read_text(encoding="utf-8", errors="replace")
        assert "the fake app is starting with run" in output
    finally:
        if result.pid:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(result.pid)], capture_output=True)


# ----- versions


def test_the_app_version_comes_from_pyproject():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{launcher.app_version()}"' in text


def test_the_launcher_command_line_returns_1_when_nothing_can_start(root, paths, monkeypatch):
    monkeypatch.setenv("NGR_APP_DATA", str(paths.app_data))
    root.mkdir(parents=True)
    assert launcher.main(["--root", str(root), "--ensure"]) == 1
    assert "nothing to start" in (paths.logs / "launcher.log").read_text(encoding="utf-8")
