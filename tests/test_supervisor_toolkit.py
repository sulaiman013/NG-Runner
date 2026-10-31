"""
The night's toolkit step (design 6, Updates): each night NG Runner copies Jim's _NG_Automation, and a
copy that changed is adopted only once his regression is clean on it. It runs on a worker of its own,
never on the thread that ticks, and what it found is written on the schedule's thread. A copy that
doesn't pass is never used: the last adopted one goes on, and Sulaiman gets one DEV item, closed by the
next adoption.

The supervisor here is tests/test_supervisor.py's world on a fake clock; the regression is faked, as
tests/test_toolkit_real.py runs the real one.
"""
from __future__ import annotations

import threading
from pathlib import Path

from ngrunner import toolkit
from ngrunner.intake.pipeline import BackgroundFetches, InlineFetches
from ngrunner.store import list_events, list_snapshots
from ngrunner.toolkit import BASELINE, Regression, Snapshot
from test_supervisor import at, make_world  # noqa: F401 - the world fixture, shared

assert make_world


class Regress:
    def __init__(self, clean: bool = True) -> None:
        self.clean = clean
        self.threads: list[str] = []

    def __call__(self, snapshot: Snapshot, **kwargs) -> Regression:
        self.threads.append(threading.current_thread().name)
        return Regression(snapshot.sha256, self.clean, "clean" if self.clean else "Its regression moved 1 thing.",
                          0 if self.clean else 1)


def jims_toolkit(world) -> Path:
    tree = world.tmp / "Mozaik" / "_NG_Automation"
    (tree / "scripts").mkdir(parents=True)
    (tree / "scripts" / "regression.py").write_text("print('made up')\n", encoding="utf-8")
    (tree / "reference" / "regression").mkdir(parents=True)
    (tree / BASELINE).write_text("{}", encoding="utf-8")
    return tree


def toolkit_items(world) -> list[str]:
    return [code for code in world.dev_codes() if code.startswith("toolkit")]


def test_the_night_copies_the_toolkit_and_adopts_it_when_its_regression_is_clean(make_world, monkeypatch):
    regress = Regress()
    monkeypatch.setattr(toolkit, "run_regression", regress)
    world = make_world(at(10, 30, 1, 25))
    tree = jims_toolkit(world)
    sup = world.start(toolkit_runs=InlineFetches())

    world.run(sup, minutes=10)

    records = list_snapshots(world.store)
    assert [(r.sha256, r.adopted) for r in records] == [(toolkit.hash_of_tree(tree), True)]
    assert toolkit.adopted_snapshot(world.store, world.paths.toolkit).sha256 == records[0].sha256
    assert any(e.kind == "toolkit_adopted" for e in list_events(world.store))
    assert toolkit_items(world) == []
    world.run(sup, minutes=24 * 60, step=10)          # the next night: unchanged, so no second run
    assert len(regress.threads) == 1 and len(list_snapshots(world.store)) == 1


def test_it_runs_on_a_worker_and_never_on_the_thread_that_ticks(make_world, monkeypatch):
    regress = Regress()
    monkeypatch.setattr(toolkit, "run_regression", regress)
    world = make_world(at(10, 30, 1, 25))
    jims_toolkit(world)
    sup = world.start(toolkit_runs=BackgroundFetches(1, name="ngrunner-toolkit"))

    world.run(sup, minutes=6)
    for _ in range(200):
        if regress.threads:
            break
        threading.Event().wait(0.05)
    for _ in range(200):                               # what it found is taken up on the schedule
        world.run(sup, minutes=1)
        if list_snapshots(world.store):
            break
        threading.Event().wait(0.05)

    assert regress.threads and regress.threads[0].startswith("ngrunner-toolkit")
    assert threading.current_thread().name not in regress.threads
    assert [r.adopted for r in list_snapshots(world.store)] == [True]


def test_a_changed_toolkit_that_doesnt_pass_is_not_used_and_sulaiman_hears_once(make_world, monkeypatch):
    regress = Regress()
    monkeypatch.setattr(toolkit, "run_regression", regress)
    world = make_world(at(10, 30, 1, 25))
    tree = jims_toolkit(world)
    sup = world.start(toolkit_runs=InlineFetches())
    world.run(sup, minutes=10)
    first = toolkit.adopted_snapshot(world.store, world.paths.toolkit).sha256
    (tree / "scripts" / "takeoff.py").write_text("WASTE = 1.2\n", encoding="utf-8")
    regress.clean = False

    world.run(sup, minutes=24 * 60, step=10)          # a night that refuses it
    world.run(sup, minutes=24 * 60, step=10)          # and again: the same item stands

    assert toolkit.adopted_snapshot(world.store, world.paths.toolkit).sha256 == first
    assert [r.adopted for r in list_snapshots(world.store)] == [True, False, False]
    assert toolkit_items(world) == ["toolkit_refused"]
    regress.clean = True
    world.run(sup, minutes=26 * 60, step=10)          # Jim approved and accepted: it passes (the clocks
                                                      # went back on Nov 1, so a day is 25 hours here)
    assert toolkit.adopted_snapshot(world.store, world.paths.toolkit).sha256 == toolkit.hash_of_tree(tree)
    assert toolkit_items(world) == []


def test_with_no_toolkit_in_mozaik_shared_the_night_tries_nothing(make_world, monkeypatch):
    regress = Regress()
    monkeypatch.setattr(toolkit, "run_regression", regress)
    world = make_world(at(10, 30, 1, 25))
    sup = world.start(toolkit_runs=InlineFetches())

    world.run(sup, minutes=10)

    assert regress.threads == [] and list_snapshots(world.store) == [] and toolkit_items(world) == []


def test_a_toolkit_that_cant_be_copied_opens_one_item(make_world, monkeypatch):
    world = make_world(at(10, 30, 1, 25))
    jims_toolkit(world)

    def cant(*args, **kwargs):
        return toolkit.Adoption("failed", "Jim's toolkit could not be copied: a file is in use.")

    monkeypatch.setattr(toolkit, "try_adoption", cant)
    sup = world.start(toolkit_runs=InlineFetches())

    world.run(sup, minutes=10)

    assert toolkit_items(world) == ["toolkit_failed"] and list_snapshots(world.store) == []
