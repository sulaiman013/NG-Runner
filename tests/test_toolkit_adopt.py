"""
Adopting a copy of Jim's toolkit the way his own RULES section 8 does: copy it, run his regression on
that copy, and adopt it only when the regression is clean on that very copy (design 6, Updates). A
changed toolkit is copied again and must pass again; one that doesn't pass is never used, the last
adopted copy goes on, and Sulaiman hears of it. The night's step runs on a worker, never on the tick's
thread, and the store is written on the schedule's thread.

The toolkits here are made up and the regression is faked; tests/test_toolkit_real.py runs his real
regression on his real toolkit.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from ngrunner import toolkit
from ngrunner.clock import FakeClock
from ngrunner.guards.facts import ToolRun
from ngrunner.store import add_snapshot, list_snapshots, open_store
from ngrunner.toolkit import (
    BASELINE, REGRESSION_CLEAN, REGRESSION_SCRIPT, Adoption, Regression, Snapshot, hash_of_tree,
    read_regression, record_adoption, run_regression, take_snapshot, try_adoption,
)

NOW = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)
SHA = "e" * 64


def ran(out: str, code: int | None = 0, err: str = "", timed_out: bool = False) -> ToolRun:
    return ToolRun(code, out, err, timed_out)


# ----- what regression says


def test_regression_is_clean_only_on_its_one_clean_line_and_exit_0() -> None:
    said = read_regression(SHA, ran(REGRESSION_CLEAN + "\n"))

    assert said == Regression(SHA, True, said.words) and said.words
    assert REGRESSION_CLEAN == "Regression clean \u2014 nothing moved."


@pytest.mark.parametrize("run", [
    ran(REGRESSION_CLEAN + "\n", code=1),
    ran(REGRESSION_CLEAN + "\nand one more line\n"),
    ran("Regression clean - nothing moved.\n"),                 # a hyphen, not his dash
    ran(REGRESSION_CLEAN + "\n", err="Traceback (most recent call last):\n"),
    ran(REGRESSION_CLEAN + "\n", code=None, timed_out=True),
    ran(""),
    ran("baseline written: C:\\x\\reference\\regression\\baseline.json\n"),
    ran("1. a: takeoff changed\n", code=0),
    ran("1. a: takeoff changed\n3. b: skipped a number\n", code=1),
], ids=["exit 1", "more", "hyphen", "stderr", "timed out", "silent", "baseline written", "list exit 0",
        "list gap"])
def test_anything_else_regression_says_is_not_clean(run: ToolRun) -> None:
    said = read_regression(SHA, run)

    assert not said.clean and said.words
    assert said.sha256 == SHA


def test_a_numbered_list_is_what_moved() -> None:
    said = read_regression(SHA, ran("1. Job: new rule failure \u2014 x\n2. Job: takeoff changed \u2014 y\n", code=1))

    assert (said.clean, said.moved) == (False, 2)
    assert "2" in said.words and "Job" not in said.words       # his items name jobs: never repeated


def test_a_written_baseline_is_never_a_pass_since_nothing_was_compared() -> None:
    said = read_regression(SHA, ran("baseline written: C:\\x\\baseline.json\n"))

    assert not said.clean and "baseline" in said.words


@pytest.fixture
def made_up(tmp_path: Path):
    """A made-up toolkit with a regression.py that says what REGRESSION_SAYS holds, and the args."""
    source = tmp_path / "_NG_Automation"
    (source / "scripts").mkdir(parents=True)
    (source / "scripts" / "regression.py").write_text(
        "import os, sys\n"
        "open(os.environ['NGR_TEST_ARGS'], 'w', encoding='utf-8').write(repr(sys.argv[1:]))\n"
        "sys.stdout.write(open(os.environ['NGR_TEST_SAYS'], encoding='utf-8').read())\n"
        "sys.exit(int(os.environ.get('NGR_TEST_EXIT', '0')))\n", encoding="utf-8")
    (source / "reference" / "regression").mkdir(parents=True)
    (source / BASELINE).write_text("{}", encoding="utf-8")
    (source / "RULES.md").write_text("# rules\n", encoding="utf-8")
    return source


def test_regression_runs_on_the_copy_with_no_arguments_so_never_accept(made_up: Path, tmp_path: Path,
                                                                         monkeypatch) -> None:
    says, args = tmp_path / "says.txt", tmp_path / "args.txt"
    says.write_text(REGRESSION_CLEAN + "\n", encoding="utf-8")
    monkeypatch.setenv("NGR_TEST_SAYS", str(says))
    monkeypatch.setenv("NGR_TEST_ARGS", str(args))
    copy = take_snapshot(made_up, tmp_path / "data" / "toolkit" / "x", clock=FakeClock(NOW))

    said = run_regression(copy)

    assert said.clean and said.sha256 == copy.sha256
    assert args.read_text(encoding="utf-8") == "[]"


def test_regression_never_runs_on_a_copy_with_no_baseline(made_up: Path, tmp_path: Path) -> None:
    (made_up / BASELINE).unlink()
    copy = take_snapshot(made_up, tmp_path / "data" / "toolkit" / "x", clock=FakeClock(NOW))
    calls = []

    said = run_regression(copy, run=lambda *a, **k: calls.append(a) or ran(REGRESSION_CLEAN))

    assert not said.clean and calls == []


def test_a_regression_that_cant_be_run_is_not_clean(made_up: Path, tmp_path: Path) -> None:
    copy = take_snapshot(made_up, tmp_path / "data" / "toolkit" / "x", clock=FakeClock(NOW))

    def broken(*args, **kwargs):
        raise toolkit.ToolkitError("The check could not be started: no.")

    said = run_regression(copy, run=broken)

    assert not said.clean and "could not be started" in said.words


# ----- trying a copy: copied, regression on that copy, adopted only when it is clean


class Regress:
    """A faked regression: clean or not, and what it was asked about."""

    def __init__(self, clean: bool = True, *, sha: str | None = None, then=None) -> None:
        self.clean, self.sha, self.then, self.asked = clean, sha, then, []

    def __call__(self, snapshot: Snapshot) -> Regression:
        self.asked.append(snapshot)
        assert hash_of_tree(snapshot.path) == snapshot.sha256      # it runs on the very copy
        if self.then:
            self.then(snapshot)
        return Regression(self.sha or snapshot.sha256, self.clean, "clean" if self.clean else "moved", 0 if self.clean else 1)


def attempt(made_up: Path, folder: Path, regress: Regress, *, adopted: str | None = None,
            keep: tuple[str, ...] = ()) -> Adoption:
    return try_adoption(made_up, folder, adopted=adopted, keep=keep, clock=FakeClock(NOW), regress=regress)


def test_a_new_toolkit_is_copied_and_adopted_when_its_regression_is_clean(made_up: Path, tmp_path: Path) -> None:
    folder = tmp_path / "data" / "toolkit"
    regress = Regress()

    tried = attempt(made_up, folder, regress)

    assert tried.outcome == "adopted" and tried.taken_at == NOW
    assert tried.sha256 == hash_of_tree(made_up) == regress.asked[0].sha256
    assert regress.asked[0].path == folder / tried.sha256
    assert hash_of_tree(folder / tried.sha256) == tried.sha256
    assert sorted(p.name for p in folder.iterdir()) == [tried.sha256]


def test_a_regression_that_isnt_clean_adopts_nothing_and_keeps_no_copy(made_up: Path, tmp_path: Path) -> None:
    folder = tmp_path / "data" / "toolkit"

    tried = attempt(made_up, folder, Regress(clean=False))

    assert tried.outcome == "refused" and tried.words
    assert list(folder.iterdir()) == []


def test_a_clean_regression_of_another_copy_adopts_nothing(made_up: Path, tmp_path: Path) -> None:
    tried = attempt(made_up, tmp_path / "data" / "toolkit", Regress(sha="f" * 64))

    assert tried.outcome == "refused"


def test_a_copy_changed_while_its_regression_ran_is_not_adopted(made_up: Path, tmp_path: Path) -> None:
    def scribble(snapshot: Snapshot) -> None:
        (snapshot.path / "scripts" / "takeoff.py").write_text("WASTE = 2\n", encoding="utf-8")

    tried = attempt(made_up, tmp_path / "data" / "toolkit", Regress(then=scribble))

    assert tried.outcome == "refused"


def test_an_unchanged_toolkit_is_not_run_again(made_up: Path, tmp_path: Path) -> None:
    folder = tmp_path / "data" / "toolkit"
    first = attempt(made_up, folder, Regress())
    again = Regress()

    tried = attempt(made_up, folder, again, adopted=first.sha256)

    assert tried.outcome == "unchanged" and again.asked == []
    assert sorted(p.name for p in folder.iterdir()) == [first.sha256]


def test_a_changed_toolkit_is_copied_again_and_must_pass_again(made_up: Path, tmp_path: Path) -> None:
    folder = tmp_path / "data" / "toolkit"
    first = attempt(made_up, folder, Regress())
    (made_up / "scripts" / "takeoff.py").write_text("WASTE = 1.1\n", encoding="utf-8")

    refused = attempt(made_up, folder, Regress(clean=False), adopted=first.sha256)
    passed = attempt(made_up, folder, Regress(), adopted=first.sha256)

    assert refused.outcome == "refused" and passed.outcome == "adopted"
    assert refused.sha256 == passed.sha256 != first.sha256
    assert hash_of_tree(folder / first.sha256) == first.sha256        # the last adopted copy stayed


def test_an_adopted_copy_that_was_damaged_is_put_back_from_the_same_bytes(made_up: Path, tmp_path: Path) -> None:
    folder = tmp_path / "data" / "toolkit"
    first = attempt(made_up, folder, Regress())
    (folder / first.sha256 / "RULES.md").write_text("changed by hand\n", encoding="utf-8")
    again = Regress()

    tried = attempt(made_up, folder, again, adopted=first.sha256)

    assert tried.outcome == "unchanged" and again.asked == []
    assert hash_of_tree(folder / first.sha256) == first.sha256


def test_a_toolkit_that_isnt_there_is_a_failure_in_plain_words(tmp_path: Path) -> None:
    tried = try_adoption(tmp_path / "gone", tmp_path / "data" / "toolkit", adopted=None, clock=FakeClock(NOW),
                         regress=Regress())

    assert tried.outcome == "failed" and "toolkit" in tried.words and tried.sha256 is None


def test_old_copies_go_and_only_what_it_made_goes(made_up: Path, tmp_path: Path) -> None:
    folder = tmp_path / "data" / "toolkit"
    folder.mkdir(parents=True)
    for name in ("a" * 64, "b" * 64, "c" * 64, "taking-20261030T090000000000Z-1"):
        (folder / name).mkdir()
        (folder / name / "x.py").write_text("x\n", encoding="utf-8")
    (folder / "Jim's notes").mkdir()                    # not a name NG Runner gives
    (folder / "run-abc").mkdir()                        # a run under way keeps its folder

    attempt(made_up, folder, Regress(), adopted="b" * 64, keep=("a" * 64, "b" * 64))

    left = {p.name for p in folder.iterdir()}
    assert {"a" * 64, "b" * 64, "Jim's notes", "run-abc"} <= left
    assert "c" * 64 not in left and "taking-20261030T090000000000Z-1" not in left


# ----- recording it, on the schedule's thread


def test_an_adoption_is_recorded_and_is_the_copy_the_checks_then_use(made_up: Path, tmp_path: Path) -> None:
    store = open_store(tmp_path / "ngrunner.db")
    folder = tmp_path / "data" / "toolkit"
    tried = attempt(made_up, folder, Regress())

    record = record_adoption(store, tried)

    assert (record.adopted, record.sha256, record.taken_at) == (True, tried.sha256, NOW)
    copy = toolkit.adopted_snapshot(store, folder)
    assert copy.sha256 == tried.sha256 and copy.path == folder / tried.sha256


def test_a_refusal_is_recorded_and_keeps_the_last_adopted_copy_in_use(made_up: Path, tmp_path: Path) -> None:
    store = open_store(tmp_path / "ngrunner.db")
    folder = tmp_path / "data" / "toolkit"
    first = attempt(made_up, folder, Regress())
    record_adoption(store, first)
    (made_up / "RULES.md").write_text("# rules, changed\n", encoding="utf-8")

    record = record_adoption(store, attempt(made_up, folder, Regress(clean=False), adopted=first.sha256))

    assert record.adopted is False and record.result
    assert toolkit.adopted_snapshot(store, folder).sha256 == first.sha256


@pytest.mark.parametrize("outcome", ["unchanged", "failed"])
def test_nothing_is_recorded_for_a_copy_that_wasnt_tried(outcome: str, tmp_path: Path) -> None:
    store = open_store(tmp_path / "ngrunner.db")

    assert record_adoption(store, Adoption(outcome, "words")) is None
    assert list_snapshots(store) == []


def test_a_record_says_adopted_only_for_an_adoption(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ngrunner.db")

    with pytest.raises(ValueError):
        record_adoption(store, Adoption("adopted", "clean", sha256=None, taken_at=NOW))
