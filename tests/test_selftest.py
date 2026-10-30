"""The post-install self-test: imports, a migration on a copy of the database, the 20 forbidden
writes, and the message templates; the installer and the launcher trust its exit code.

The store and the message templates are other slices' work, so they are faked here; the
forbidden writes run against the real commit_write.
"""
from __future__ import annotations

import io
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ngrunner import __main__ as cli
from ngrunner import launcher, selftest
from ngrunner.config import AppPaths
from ngrunner.writes import CommitResult

NOW = datetime(2026, 10, 29, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    return AppPaths(tmp_path / "NGRunner")


def good_migrate(db: Path) -> None:
    """A stand-in for the store's migrations: ordered, idempotent, setting user_version."""
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE IF NOT EXISTS jobs(studio_id TEXT PRIMARY KEY, name TEXT NOT NULL)")
        con.execute("PRAGMA user_version = 2")
    con.close()


@dataclass(frozen=True)
class Message:
    kind: str
    title: str
    lines: tuple[str, ...]
    urgent: bool = False


def clean_samples() -> list[Message]:
    return [Message("filed", "Filed as Testerson_101426", ("1. Testerson_101426: filed.",))]


def no_problems(message: Message) -> list[str]:
    return []


def checks(**overrides):
    """Self-test options with fakes for the neighbours: overrides replace any of them."""
    options = dict(modules=("ngrunner.clock", "ngrunner.writes"), migrate=good_migrate,
                   samples=clean_samples, lint=no_problems)
    return {**options, **overrides}


# ----- the whole self-test


def test_a_sound_install_passes_with_no_problems(paths):
    assert selftest.run_self_test(paths, **checks()) == []


def test_the_self_test_writes_only_inside_its_data_folder_and_cleans_up(paths, tmp_path):
    (tmp_path / "next to it.txt").write_bytes(b"untouched")
    selftest.run_self_test(paths, **checks())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["NGRunner", "next to it.txt"]
    assert list(paths.staging.iterdir()) == []


def test_every_problem_found_is_listed_in_plain_words(paths):
    def bad_migrate(db: Path) -> None:
        raise ValueError("the database is newer than this version of NG Runner")

    problems = selftest.run_self_test(paths, **checks(
        modules=("ngrunner.clock", "ngrunner.no_such_slice"), migrate=bad_migrate,
        lint=lambda m: ["it opens with Hi"]))
    assert problems == [
        "ngrunner.no_such_slice could not be imported"
        " (ModuleNotFoundError: No module named 'ngrunner.no_such_slice').",
        "The database migration failed on a copy: the database is newer than this version of NG Runner.",
        "Message template filed: it opens with Hi.",
    ]


# ----- imports


def test_a_module_that_breaks_while_importing_is_named(tmp_path, monkeypatch):
    (tmp_path / "ngr_broken_for_test.py").write_text("raise RuntimeError('half written')\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert selftest.import_problems(["ngr_broken_for_test", "json"]) == [
        "ngr_broken_for_test could not be imported (RuntimeError: half written)."]


def test_the_p1a_module_list_covers_every_slice():
    listed = set(selftest.P1A_MODULES)
    for name in ("ngrunner.writes", "ngrunner.match", "ngrunner.intake.summary_check", "ngrunner.store",
                 "ngrunner.secrets", "ngrunner.intake.fetch", "ngrunner.intake.branch", "ngrunner.joblist",
                 "ngrunner.fab", "ngrunner.messages.lint", "ngrunner.messages.templates", "ngrunner.cards",
                 "ngrunner.digest", "ngrunner.heartbeat", "ngrunner.board", "ngrunner.tray",
                 "ngrunner.toasts", "ngrunner.launcher", "ngrunner.single", "ngrunner.backup",
                 "ngrunner.support", "ngrunner.selftest"):
        assert name in listed
    assert {"flask", "waitress", "pystray", "windows_toasts", "cryptography", "pypdf"} <= listed


# ----- the migration, on a copy only


def _live_database(paths: AppPaths) -> bytes:
    paths.app_data.mkdir(parents=True)
    with sqlite3.connect(paths.db) as con:
        con.execute("CREATE TABLE jobs(studio_id TEXT PRIMARY KEY, name TEXT NOT NULL)")
        con.execute("INSERT INTO jobs VALUES ('1001', 'Testerson_101426')")
        con.execute("PRAGMA user_version = 1")
    con.close()
    return paths.db.read_bytes()


def test_the_migration_runs_on_a_copy_and_never_on_the_live_database(paths):
    before = _live_database(paths)
    seen: list[Path] = []

    def migrate(db: Path) -> None:
        seen.append(db)
        good_migrate(db)

    assert selftest.run_self_test(paths, **checks(migrate=migrate)) == []
    assert paths.db.read_bytes() == before
    assert len(seen) == 2 and paths.db not in seen and seen[0] == seen[1]


def test_a_fresh_install_migrates_a_new_empty_database(paths):
    seen: list[Path] = []

    def migrate(db: Path) -> None:
        seen.append(db)
        good_migrate(db)

    assert selftest.run_self_test(paths, **checks(migrate=migrate)) == []
    assert seen and not paths.db.exists()


def test_by_default_the_stores_own_migrations_run_on_the_copy(paths):
    # The store slice's open_store() creates or upgrades a database; nothing is faked here.
    _live_database(paths)
    before = paths.db.read_bytes()
    problems = selftest.run_self_test(paths, modules=(), samples=clean_samples, lint=no_problems)
    assert problems == [] and paths.db.read_bytes() == before


def test_a_database_newer_than_the_code_is_reported(paths):
    paths.app_data.mkdir(parents=True)
    with sqlite3.connect(paths.db) as con:
        con.execute("PRAGMA user_version = 999")
    con.close()
    problems = selftest.run_self_test(paths, modules=(), samples=clean_samples, lint=no_problems)
    assert len(problems) == 1 and problems[0].startswith("The database migration failed on a copy:")
    assert "newer version of NG Runner" in problems[0]


def test_a_migration_that_is_not_idempotent_is_caught(paths):
    def twice_is_too_many(db: Path) -> None:
        with sqlite3.connect(db) as con:
            con.execute("CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY)")
            con.execute("INSERT INTO runs DEFAULT VALUES")
            con.execute("PRAGMA user_version = 2")
        con.close()

    assert selftest.run_self_test(paths, **checks(migrate=twice_is_too_many)) == [
        "The database migration is not idempotent: running it again changed the copy."]


def test_a_migration_that_sets_no_schema_version_is_caught(paths):
    def unversioned(db: Path) -> None:
        with sqlite3.connect(db) as con:
            con.execute("CREATE TABLE IF NOT EXISTS jobs(studio_id TEXT PRIMARY KEY)")
        con.close()

    assert selftest.run_self_test(paths, **checks(migrate=unversioned)) == [
        "The database migration left no schema version (PRAGMA user_version is 0)."]


# ----- the forbidden writes


def test_the_table_holds_the_twenty_scripted_forbidden_writes():
    assert len(selftest.FORBIDDEN_WRITES) == 20
    assert len({w.label for w in selftest.FORBIDDEN_WRITES}) == 20


def test_every_forbidden_write_is_refused_by_the_real_chokepoint(tmp_path):
    assert selftest.forbidden_write_problems(tmp_path / "sandbox") == []


def test_a_chokepoint_that_lets_forbidden_writes_through_is_caught(tmp_path):
    def lets_everything_through(writes, **kwargs) -> CommitResult:
        for w in writes:
            w.path.parent.mkdir(parents=True, exist_ok=True)
            w.path.write_bytes(w.content)
        return CommitResult(ok=True, written=tuple(w.path for w in writes))

    problems = selftest.forbidden_write_problems(tmp_path / "sandbox", commit=lets_everything_through)
    assert len(problems) == 20
    assert problems[0] == "Forbidden write 1 (a Room file in a Mozaik job) was not refused."


def test_a_refusal_that_still_leaves_a_file_behind_is_caught(tmp_path):
    def refuses_but_writes(writes, **kwargs) -> CommitResult:
        allowed = any(name in str(writes[0].path) for name in ("Dummitt_110226", "heartbeat"))
        for w in writes:
            if allowed or "_NG Runner" in w.path.parts:
                w.path.parent.mkdir(parents=True, exist_ok=True)
                w.path.write_bytes(w.content)
        return CommitResult(ok=allowed, reason=None if allowed else "refused")

    problems = selftest.forbidden_write_problems(tmp_path / "sandbox", commit=refuses_but_writes)
    assert problems == ["Forbidden write 8 (the Installer App's parts catalog) was refused,"
                        " but files changed: NG Jobs\\_NG Runner\\parts_catalog.csv."]


def test_a_sandbox_that_refuses_everything_proves_nothing_and_says_so(tmp_path):
    def refuses_everything(writes, **kwargs) -> CommitResult:
        return CommitResult(ok=False, reason="NG Jobs is not reachable right now")

    problems = selftest.forbidden_write_problems(tmp_path / "sandbox", commit=refuses_everything)
    assert problems[0].startswith("The self-test's own allowed write to NG Jobs was refused")
    assert "proves nothing" in problems[0]


# ----- templates


def test_templates_that_fail_the_lint_are_named_by_kind():
    def lint(message: Message) -> list[str]:
        return ["a time is shown as 21:42"] if message.kind == "digest" else []

    samples = [Message("digest", "NG Runner · Fri 10/30", ("1. Testerson_101426: filed 21:42.",)),
               Message("filed", "Filed as Fakeley_102026", ("1. Fakeley_102026: filed.",))]
    assert selftest.template_problems(lambda: samples, lint) == [
        "Message template digest: a time is shown as 21:42."]


def test_templates_that_can_not_be_rendered_are_reported_not_raised():
    def broken() -> Iterable[Message]:
        raise KeyError("job")

    assert selftest.template_problems(broken, no_problems) == [
        "The message templates could not be rendered (KeyError: 'job')."]
    assert selftest.template_problems(lambda: [], no_problems) == [
        "No message templates were found to check."]


# ----- the command line: the installer and launcher trust this exit code


def run_cli(argv: list[str], paths: AppPaths, problems: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    code = cli.main(argv, out=out, environ={"NGR_APP_DATA": str(paths.app_data)},
                    self_test=lambda p: list(problems), clock=_Fixed())
    return code, out.getvalue()


class _Fixed:
    def now(self) -> datetime:
        return NOW


def test_the_command_line_self_test_exits_0_only_when_clean(paths):
    code, text = run_cli(["--self-test"], paths, [])
    assert code == 0 and text == f"NG Runner {launcher.app_version()} self-test passed.\n"
    code, text = run_cli(["--self-test"], paths, ["ngrunner.store could not be imported (x)."])
    assert code == 1
    assert text == (f"NG Runner {launcher.app_version()} self-test found 1 problem:\n"
                    "1. ngrunner.store could not be imported (x).\n")


def test_recording_the_self_test_is_what_lets_the_launcher_start_a_version(paths, tmp_path):
    root = tmp_path / "Programs"
    folder = root / f"app-{launcher.app_version()}"
    folder.mkdir(parents=True)
    (folder / launcher.APP_EXE).write_bytes(b"MZ")
    assert run_cli(["--self-test"], paths, [])[0] == 0
    assert launcher.launch(root, paths, mode="logon", running=lambda: False,
                           start=_never_started).outcome == "nothing_to_start"
    assert run_cli(["--self-test", "--record"], paths, ["x."])[0] == 1
    assert launcher.launch(root, paths, mode="logon", running=lambda: False,
                           start=_never_started).outcome == "nothing_to_start"
    assert run_cli(["--self-test", "--record"], paths, [])[0] == 0
    with pytest.raises(_Started):
        launcher.launch(root, paths, mode="logon", running=lambda: False, start=_never_started)


class _Started(Exception):
    pass


def _never_started(installed, args, log):
    raise _Started(installed.version)


def test_record_only_goes_with_the_self_test(paths):
    out = io.StringIO()
    assert cli.main(["--record"], out=out, environ={"NGR_APP_DATA": str(paths.app_data)}) == 2


def test_the_version_command_prints_the_build_version(paths):
    out = io.StringIO()
    assert cli.main(["--version"], out=out, environ={"NGR_APP_DATA": str(paths.app_data)}) == 0
    assert out.getvalue() == f"NG Runner {launcher.app_version()}\n"


@pytest.mark.parametrize("argv", [["run"], ["run", "--attempt", "a" * 32, "--open-board"],
                                  ["install-shortcut", "--target", "C:\\x.exe"], ["release-all"], []])
def test_commands_wave_2_fills_say_so_and_exit_3(paths, argv):
    out = io.StringIO()
    assert cli.main(argv, out=out, environ={"NGR_APP_DATA": str(paths.app_data)}) == 3
    assert "not built yet" in out.getvalue()
