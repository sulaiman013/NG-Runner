"""
Each of Jim's fixers wired into P4's worker (lock/work.py) as the notch is: planned for the finding it fixes
while a check that counts holds it open, made into a fix card in its own words, and undone and judged after
a save by the fixer that wrote it, found by the name the fix keeps. All in shadow, on a real store and NG
Runner's own folders, with fakes where Jim's scripts would run. Names are made up.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from ngrunner.checks.findings import FIX, MOZAIK, Item
from ngrunner.checks.jobchecks import JobCheck, ShownItem
from ngrunner.intake.pipeline import InlineFetches
from ngrunner.lock import kept, work
from ngrunner.lock.banding import COUNTERTOP_BANDING, WM_BANDING
from ngrunner.lock.fixes import Element, FixPlan
from ngrunner.lock.notch import NOTCH_FACE
from ngrunner.lock.patch import Patch
from ngrunner.lock.topline import TOP_LINE
from ngrunner.store import list_cards, list_dev
from ngrunner.store.locks import add_fix, record_survival
from ngrunner.toolkit import Snapshot
from test_lock_work import JOB, SID, T0, ctx, not_running  # noqa: F401 - ctx is a fixture

OPEN = (("notch_check", "notch_face", ("R1C3",)), ("top_line", "top_line", ("R2",)),
        ("notch_check", "wm_banding", ("R1", "R3")), ("eb_tops", "banding", ("R3",)))


def check(*items: tuple[str, str, tuple[str, ...]], kind: str = FIX, run_id: int = 7) -> JobCheck:
    shown = tuple(ShownItem(Item(n, kind, checker, what, tags)) for n, (checker, what, tags) in enumerate(items, 1))
    return JobCheck(SimpleNamespace(id=run_id), "failed", "", items=shown, open=shown, counts=True)


def test_each_fixer_is_given_the_rooms_its_finding_is_open_in() -> None:
    found = [(fixer.name, room) for fixer, room in work._fixer_rooms(check(*OPEN))]

    assert found == [("notch_face", "Room1.des"), ("top_line", "Room2.des"), ("wm_banding", "Room1.des"),
                     ("wm_banding", "Room3.des"), ("countertop_banding", "Room3.des")]
    assert work._fixer_rooms(check(*OPEN, kind=MOZAIK)) == []
    assert work._fixer_rooms(check(("br_cam_check", "bridge_cams", ("R1C4",)), ("outset_check", "outset", ("R1",)))) == []


def test_the_notch_is_planned_once_per_run_and_room_as_before_and_each_other_fixer_on_its_own() -> None:
    assert work._planned_key(7, "Room1.des", NOTCH_FACE) == "7|Room1.des"
    assert work._planned_key(7, "Room1.des", TOP_LINE) == "7|Room1.des|top_line"
    assert len({work._planned_key(7, "Room1.des", f) for f in (NOTCH_FACE, TOP_LINE, WM_BANDING,
                                                              COUNTERTOP_BANDING)}) == 4


@pytest.fixture
def adopted(ctx, monkeypatch, tmp_path):  # noqa: F811 - ctx is test_lock_work's fixture
    snapshot = Snapshot(tmp_path / "toolkit", "0" * 64, T0, 1)
    monkeypatch.setattr(work, "adopted_snapshot", lambda store, folder: snapshot)
    (ctx.settings.mozaik_shared / "Jobs" / JOB).mkdir(parents=True)
    return snapshot


def test_the_worker_plans_each_open_finding_with_its_own_fixer_one_at_a_time(ctx, adopted, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(work, "job_checks", lambda store, now: {SID: check(*OPEN)})
    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(work, "plan_fix", lambda fixer, snapshot, room, **kw: asked.append((fixer.name, room.name)))
    lock = work.LockWork(InlineFetches(), running=not_running, holder="PC")

    while (job := lock._plan_work(ctx, T0)) is not None:
        kind, found = job()
        assert kind == "plan" and "refused" not in found and found["studio_id"] == SID

    assert asked == [("notch_face", "Room1.des"), ("top_line", "Room2.des"), ("wm_banding", "Room1.des"),
                     ("wm_banding", "Room3.des"), ("countertop_banding", "Room3.des")]
    assert kept.names_in(ctx.store, kept.PLANNED) == {"7|Room1.des", "7|Room2.des|top_line", "7|Room1.des|wm_banding",
                                                      "7|Room3.des|wm_banding", "7|Room3.des|countertop_banding"}


def plan_of(fixer: Any, words: str) -> FixPlan:
    return FixPlan(fixer.name, JOB, "Room2.des", Patch("a" * 64, "b" * 64, ()),
                   (Element("4", "", "Elev", "1127.1", "928"),), ("R2C4",), (), ("3-4 Egger Classic White",),
                   "c" * 64, "d" * 64, (words,))


def test_a_top_line_plan_becomes_a_fix_card_in_its_own_words(ctx) -> None:  # noqa: F811
    words = "Room 2: 1 wall-hung panel (R2C4) doesn't hang on the room's top line; the fix moves it up or down onto it"
    lock = work.LockWork(InlineFetches(), running=not_running, holder="PC")

    lock._took_plan(ctx, {"studio_id": SID, "folder": JOB, "room": "Room2.des", "plan": plan_of(TOP_LINE, words)})

    card, = [c for c in list_cards(ctx.store, state=None) if c.kind == "fix"]
    assert card.text.startswith(words + ".") and card.choices[0] == "Fix and re-nest 3/4 Egger Classic White"
    assert kept.plan_of(ctx.store, card.id)["fixer"] == "top_line"


@dataclass
class Called:
    fixers: list[str]

    def __call__(self, record: Any, fixer: Any, *a: Any, **k: Any) -> Any:
        self.fixers.append(fixer.name)
        return SimpleNamespace(ok=True, reason=None, code="waiting", words="")


def written(ctx, fixer: str):  # noqa: F811
    return add_fix(ctx.store, folder=JOB, room="Room2.des", fixer=fixer, fingerprint="d" * 64, base_sha256="a" * 64,
                   result_sha256="b" * 64, patch=[{"offset": 1, "old": "00", "new": "01"}],
                   elements=[{"cab": "4", "id": "", "attribute": "Elev", "before": "1127.1", "after": "928"}],
                   programs={}, backups=[], applied_at=T0)


def test_undo_is_made_by_the_fixer_that_wrote_the_fix(ctx, monkeypatch) -> None:  # noqa: F811
    called = Called([])
    monkeypatch.setattr(work, "undo_fix", called)
    lock = work.LockWork(InlineFetches(), running=not_running, holder="PC")
    monkeypatch.setattr(lock, "_done", lambda *a, **k: None)
    monkeypatch.setattr(work, "interlock_for", lambda c, **k: (lambda paths: None))
    monkeypatch.setattr(work, "take_lease", lambda *a, **k: SimpleNamespace(ok=True))

    for name in ("top_line", "wm_banding", "countertop_banding", "notch_face"):
        fix = written(ctx, name)
        lock._try(ctx, kept.add_pending(ctx.store, kind="undo", at=T0, folder=JOB, fix=fix.id), T0)

    assert called.fixers == ["top_line", "wm_banding", "countertop_banding", "notch_face"]


def test_undo_of_a_fix_by_a_fixer_ng_runner_no_longer_has_is_given_up_saying_so(ctx, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(work, "interlock_for", lambda c, **k: (lambda paths: None))
    monkeypatch.setattr(work, "take_lease", lambda *a, **k: SimpleNamespace(ok=True))
    fix = written(ctx, "swap_rea")
    lock = work.LockWork(InlineFetches(), running=not_running, holder="PC")

    lock._try(ctx, kept.add_pending(ctx.store, kind="undo", at=T0, folder=JOB, fix=fix.id), T0)

    assert kept.pending(ctx.store) == []
    item, = [i for i in list_dev(ctx.store) if i.code == work.FIX_GAVE_UP]
    assert "no fixer by the name" in item.text


def test_survival_is_judged_by_the_fixer_that_wrote_the_fix(ctx, adopted, monkeypatch) -> None:  # noqa: F811
    called = Called([])
    monkeypatch.setattr(work, "survival", called)
    fix = written(ctx, "wm_banding")
    copy = work.shadow(ctx).mozaik_shared / "Jobs" / JOB / "Room2.des"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"saved by Jim since")
    lock = work.LockWork(InlineFetches(), running=not_running, holder="PC")

    job = lock._survival_work(ctx, T0)
    assert job is not None and job()[1][0] == fix.id
    assert called.fixers == ["wm_banding"]

    record_survival(ctx.store, fix.id, code="held", words="The fix on C4 held after the save", at=T0)
    written(ctx, "swap_rea")                         # no fixer of that name: never judged, never a crash
    assert lock._survival_work(ctx, T0) is None


def test_a_plan_made_again_after_a_save_is_made_by_its_own_fixer(ctx, adopted, monkeypatch) -> None:  # noqa: F811
    asked: list[str] = []
    monkeypatch.setattr(work, "plan_fix", lambda fixer, *a, **k: asked.append(fixer.name))
    item = kept.add_pending(ctx.store, kind="apply", at=T0, folder=JOB,
                            plan=kept.plan_json(plan_of(COUNTERTOP_BANDING, "Room 2: a countertop")))
    kept.update_pending(ctx.store, item["id"], staged=True, replan=True)

    kind, found = work.LockWork(InlineFetches(), running=not_running)._stage_work(ctx, T0)()

    assert kind == "plan" and found["pending"] == item["id"] and asked == ["countertop_banding"]


def test_a_plan_by_a_fixer_ng_runner_no_longer_has_is_refused_saying_so(ctx, adopted) -> None:  # noqa: F811
    found = work._plan(None, adopted, ctx.settings.mozaik_shared / "Jobs" / JOB, JOB, "Room1.des",
                       ctx.paths.staging / "fixes", {"pending": 1})
    assert found["refused"] == work.NO_FIXER
