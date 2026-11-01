"""
P4 at work (lock/work.py): the rules its wiring adds to the parts it calls, on a real store, NG Runner's own
shadow folders and fakes for the messenger and Mozaik's process list. Reads run inline here; in the app they
run on a worker of their own. Job names and materials are made up.

The rules held here:
- The P4 interlock it builds stands on the folders NG Runner is configured with and the settings' tracker
  folder, whatever roots a commit is handed: a batch pointed at the real tracker folder is refused.
- A click refused by check-before-lock is logged, asks for nothing, and drops nothing; "lock without check"
  goes ahead only where the check is all that is missing, and is logged as such.
- A fix is applied only by Jim's approval of its card, and exactly the plan that card asked.
- A batch is judged again only on a new snapshot, or a change in the folder.
- A batch the tracker hasn't taken in 30 minutes is one DEV item, once.
- A lease is renewed while a fix waits on its job, and released once none does.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ngrunner.clock import FakeClock
from ngrunner.config import AppPaths, Settings, roots, shadow_roots
from ngrunner.intake.pipeline import InlineFetches
from ngrunner.lock import kept, work
from ngrunner.lock.leases import LEASE_TERM, take_lease
from ngrunner.messages import templates
from ngrunner.store import add_card, answer_card, ensure_job, list_dev, list_events, open_store, update_job
from ngrunner.store.locks import add_batch, get_lease, list_batches
from ngrunner.tracker_inbox import HEADER
from ngrunner.writes import FileWrite, commit_recorded

T0 = datetime(2026, 10, 31, 13, 0, tzinfo=UTC)
SID, JOB = "4242", "Testerson_101426"
ROWS = [list(HEADER), ["JOBSUMMARY", JOB, "", "🔲 In progress", ""]]


@dataclass
class Sent:
    messages: list[tuple[Any, str]] = field(default_factory=list)

    def send(self, message: Any, *, to: str, **_: Any) -> str:
        self.messages.append((message, to))
        return "id"


@pytest.fixture
def ctx(tmp_path: Path):
    paths = AppPaths(tmp_path / "data")
    paths.app_data.mkdir(parents=True)
    settings = Settings(ng_jobs=tmp_path / "NG Jobs", mozaik_shared=tmp_path / "Mozaik Shared",
                        control=tmp_path / "control", tracker_folder=tmp_path / "NG Conversion program Claude")
    for folder in (settings.ng_jobs, settings.mozaik_shared / "Jobs", settings.control, settings.tracker_folder):
        folder.mkdir(parents=True)
    store = open_store(paths.db)
    ensure_job(store, SID, JOB, now=T0)
    update_job(store, SID, folder=JOB, branch="closet", now=T0)
    return _Ctx(store, settings, FakeClock(T0), roots(settings, paths), paths, Sent())


@dataclass
class _Ctx:
    store: Any
    settings: Settings
    clock: FakeClock
    roots: Any
    paths: AppPaths
    messenger: Sent
    mode: str = "live"


def not_running() -> bool:
    return False


# ----- the interlock stands on the real folders


def test_a_batch_pointed_at_the_settings_tracker_folder_is_refused_whatever_roots_it_is_handed(ctx) -> None:
    shadow = shadow_roots(ctx.paths)
    pointed = replace(shadow, tracker=ctx.settings.tracker_folder.parent / "tracker test")
    pointed.tracker.mkdir()
    real_place = replace(shadow, tracker=ctx.settings.tracker_folder)
    check = work.interlock_for(ctx, running=not_running)

    assert check([real_place.tracker / "tracker_inbox_1.csv"]) is not None
    assert check([pointed.tracker / "tracker_inbox_1.csv"]) is None
    refused = commit_recorded([FileWrite(ctx.settings.tracker_folder / "tracker_inbox_1.csv", b"x", None)],
                              store=ctx.store, clock=ctx.clock, roots=real_place, interlock=check, phase="P4 shadow")
    assert not refused.ok and list(ctx.settings.tracker_folder.iterdir()) == []


# ----- a click: refused at once with what is missing, or asked


def test_a_refused_click_is_logged_asks_nothing_and_drops_nothing(ctx) -> None:
    reply = work.ask_lock(ctx, SID, "lock", None, False)

    assert reply.startswith(f"{JOB}: Lock wasn't done. Missing: ")
    assert "No check of this job's design has run" in reply and "No nest of this job has been read" in reply
    assert [e.text for e in list_events(ctx.store) if e.kind == "lock_refused"] == [reply]
    assert kept.requests(ctx.store) == [] and list_batches(ctx.store) == []


def test_lock_without_check_never_waives_a_nest(ctx) -> None:
    reply = work.ask_lock(ctx, SID, "lock", None, True)
    assert "No nest of this job has been read" in reply and "No check" not in reply
    assert kept.requests(ctx.store) == []


def test_lock_without_check_is_asked_and_logged_as_such(ctx) -> None:
    reply = work.ask_lock(ctx, SID, "lock_design", None, True)

    assert reply == templates.lock_asked(JOB, label="Lock design", without_check=True)
    request, = kept.requests(ctx.store)
    assert (request["click"], request["without_check"], request["folder"]) == ("lock_design", True, JOB)
    assert [e.kind for e in list_events(ctx.store) if e.kind.startswith("lock")] == ["lock_without_check"]
    assert work.ask_lock(ctx, SID, "lock_design", None, True) == templates.LOCK_BUSY


def test_anything_but_one_of_jims_three_clicks_is_no_lock(ctx) -> None:
    assert work.ask_lock(ctx, SID, "unlock", None, False) == templates.NOT_A_LOCK
    assert work.ask_lock(ctx, SID, "finish_lock", None, False) == templates.NOT_A_LOCK
    assert work.ask_lock(ctx, "9999", "lock", None, False) == templates.NOT_A_LOCK
    assert work.ask_lock(ctx, SID, "finish_lock", "3-4 Nothing Here", False) == templates.LOCK_GONE


# ----- a fix: only Jim's approval of its card applies it, and exactly its plan


PLAN = {"fixer": "notch_face", "folder": JOB, "room": "Room1.des", "base": "a" * 64, "result": "b" * 64,
        "patch": [{"offset": 1, "old": "00", "new": "01"}], "elements": [
            {"cab": "3", "id": "2", "attribute": "Hide", "before": "False", "after": "True"}],
        "tags": ["R1C3"], "hand_flipped": [], "renest": [], "snapshot": "c" * 64, "fingerprint": "d" * 64,
        "words": ["Room 1: the hang notch is cut from the wrong face on 1 panel (R1C3)"]}


def fix_card(ctx, answer: str | None):
    spec = templates.fix_card(JOB, words=PLAN["words"], renest=())
    card = add_card(ctx.store, prefix="TES", kind="fix", text=spec.text, choices=spec.choices, created_at=T0,
                    studio_id=SID)
    kept.keep_plan(ctx.store, card.id, kept.plan_from(PLAN), studio_id=SID)
    if answer is not None:
        answer_card(ctx.store, card.id, answer=answer, answer_words=answer, answered_by="jim", answered_at=T0,
                    channel="board")
    return card


def test_a_fix_card_left_open_or_answered_leave_it_applies_nothing(ctx) -> None:
    lock = work.LockWork(InlineFetches(), running=not_running, holder="PC")
    fix_card(ctx, None)
    fix_card(ctx, templates.FIX_CHOICES[1])

    lock.tick(ctx)

    assert kept.pending(ctx.store) == []
    assert [e.kind for e in list_events(ctx.store) if e.kind.startswith("fix_")] == ["fix_left"]


def test_an_approved_fix_waits_with_exactly_the_plan_its_card_asked(ctx) -> None:
    lock = work.LockWork(InlineFetches(), running=not_running, holder="PC")
    card = fix_card(ctx, templates.FIX_CHOICES[0])

    lock._act_on_cards(ctx, T0)

    waiting, = kept.pending(ctx.store)
    assert waiting["kind"] == "apply" and waiting["card"] == card.id
    assert kept.plan_from(waiting["plan"]) == kept.plan_from(PLAN)
    lock._act_on_cards(ctx, T0)                     # acted on once
    assert len(kept.pending(ctx.store)) == 1


# ----- a batch: judged again only on something new, and 30 minutes is a DEV item


def test_a_batch_is_judged_again_only_on_a_new_snapshot(ctx, monkeypatch) -> None:
    from ngrunner.lock.drop import AfterRead

    batch = add_batch(ctx.store, id="20261031T130000Z-abc123", folder=JOB, kind="lock_design", verb="append",
                      material=None, rows=ROWS, file="tracker_inbox_20261031T130000Z-abc123.csv", dropped_at=T0)
    judged = []
    real = work.judge_after
    monkeypatch.setattr(work, "judge_after", lambda b, seen, **kw: judged.append(seen) or real(b, seen, **kw))
    lock = work.LockWork(InlineFetches(), running=not_running, holder="PC")

    lock._took_after(ctx, [(batch, AfterRead())])
    lock._took_after(ctx, [(batch, AfterRead())])

    assert len(judged) == 1


def test_a_batch_the_tracker_hasnt_taken_in_30_minutes_is_one_dev_item(ctx) -> None:
    add_batch(ctx.store, id="20261031T130000Z-abc123", folder=JOB, kind="lock_design", verb="append", material=None,
              rows=ROWS, file="tracker_inbox_20261031T130000Z-abc123.csv", dropped_at=T0)
    lock = work.LockWork(InlineFetches(), running=not_running, holder="PC")

    lock._batch_limits(ctx, T0 + timedelta(minutes=29))
    assert [d for d in list_dev(ctx.store) if d.code == work.BATCH_SLOW] == []
    lock._batch_limits(ctx, T0 + timedelta(minutes=31))
    lock._batch_limits(ctx, T0 + timedelta(minutes=45))
    slow, = [d for d in list_dev(ctx.store) if d.code == work.BATCH_SLOW]
    assert "still waits for the tracker" in slow.text


# ----- leases: kept while a fix waits, released when none does


def take(ctx):
    return take_lease(JOB, store=ctx.store, roots=shadow_roots(ctx.paths), clock=ctx.clock, holder="PC",
                      interlock=work.interlock_for(ctx, running=not_running))


def test_a_lease_is_renewed_while_a_fix_waits_and_released_once_none_does(ctx) -> None:
    assert take(ctx).ok
    kept.add_pending(ctx.store, kind="apply", at=T0, folder=JOB, plan=PLAN)
    lock = work.LockWork(InlineFetches(), running=not_running, holder="PC")
    ctx.clock.advance(timedelta(hours=2, minutes=1))

    lock._keep_leases(ctx, ctx.clock.now())
    renewed = get_lease(ctx.store, JOB)
    assert renewed.state == "active" and renewed.expires_at == ctx.clock.now().replace(microsecond=0) + LEASE_TERM

    kept.drop_pending(ctx.store, kept.pending(ctx.store)[0]["id"])
    lock._keep_leases(ctx, ctx.clock.now())
    assert get_lease(ctx.store, JOB).state == "released"


def test_release_leases_goes_through_a_pause(ctx) -> None:
    from ngrunner.interlock import PAUSED
    from ngrunner.store import set_state

    assert take(ctx).ok
    set_state(ctx.store, PAUSED, "yes")

    released, = work.release_leases(ctx.store, ctx.paths, ctx.roots, ctx.settings.tracker_folder, ctx.clock,
                                    holder="PC")

    assert released.ok and get_lease(ctx.store, JOB).state == "released"
    assert json.dumps(released.lease.state) == '"released"'


# ----- survival, and the one redo


def written_fix(ctx, *, redo_of: int | None = None):
    from ngrunner.store.locks import add_fix

    fix = add_fix(ctx.store, folder=JOB, room="Room1.des", fixer="notch_face", fingerprint=PLAN["fingerprint"],
                  base_sha256=PLAN["base"], result_sha256=PLAN["result"], patch=PLAN["patch"],
                  elements=PLAN["elements"], programs={}, backups=[], applied_at=T0)
    fix_card(ctx, None)
    if redo_of is not None:
        kept.note_redo(ctx.store, fix.id, redo_of)
    return fix


def test_jims_own_change_after_a_fix_is_a_line_in_the_log_and_tells_nobody(ctx) -> None:
    from ngrunner.lock.fixes import Survival

    fix = written_fix(ctx)
    work.LockWork(InlineFetches(), running=not_running)._took_survival(
        ctx, (fix.id, Survival("you_changed", "You changed it in Mozaik: C3 holds other values now")))

    changed, = [e for e in list_events(ctx.store) if e.kind == "you_changed"]
    assert changed.lane == "LOG" and changed.text == f"{JOB}: You changed it in Mozaik: C3 holds other values now"
    assert ctx.messenger.messages == []
    assert [c for c in list_cards_of(ctx) if c.kind == "fix_undone"] == []


def list_cards_of(ctx):
    from ngrunner.store import list_cards

    return list_cards(ctx.store, state=None)


def test_mozaik_undoing_a_fix_is_urgent_with_one_redo_and_never_a_redo_of_a_redo(ctx) -> None:
    from ngrunner.lock.fixes import Survival

    undid = Survival("mozaik_undid", "Mozaik undid the fix on C3: the save put the old values back", urgent=True,
                     redo=True)
    first = written_fix(ctx)
    lock = work.LockWork(InlineFetches(), running=not_running)
    lock._took_survival(ctx, (first.id, undid))

    (message, to), = ctx.messenger.messages
    assert to == "sulaiman" and message.urgent and message.title.startswith(f"SHADOW {JOB}: after the save")
    redo, = [c for c in list_cards_of(ctx) if c.kind == "fix_undone"]
    assert redo.choices == templates.REDO_CHOICES

    again = written_fix(ctx, redo_of=first.id)
    lock._took_survival(ctx, (again.id, undid))
    assert len([c for c in list_cards_of(ctx) if c.kind == "fix_undone"]) == 1


# ----- the job sheet at lock


def test_a_job_sheet_that_differs_from_the_nests_is_kept_as_items_and_never_filed(ctx) -> None:
    from ngrunner.lock.job_sheet import MISMATCH, JobSheetRun, SheetItem

    made = JobSheetRun(MISMATCH, (SheetItem("3/4", "Egger Classic White", 8, 9, '3/4" Egger Classic White: the job'
                                            " sheet says 8 sheets, the nest has 9"),), b"docx", "differs")
    work.LockWork(InlineFetches(), running=not_running)._took_sheet(ctx, (JOB, made, False))

    said = kept.sheet_of(ctx.store, JOB)
    assert said["code"] == MISMATCH and said["items"] == [made.items[0].words] and not said["written"]
    assert not list(shadow_roots(ctx.paths).ng_jobs.rglob("* CNC Job Sheet.docx"))


def test_a_job_sheet_that_matches_is_filed_only_in_ng_runners_own_copy_and_only_where_there_is_none(ctx) -> None:
    from ngrunner.lock.job_sheet import MATCHES, JobSheetRun

    made = JobSheetRun(MATCHES, (), b"a job sheet", "matches")
    lock = work.LockWork(InlineFetches(), running=not_running)

    lock._took_sheet(ctx, (JOB, made, True))           # NG Jobs has one already: never another
    assert not kept.sheet_of(ctx.store, JOB)["written"]
    lock._took_sheet(ctx, (JOB, made, False))

    copy = shadow_roots(ctx.paths).ng_jobs / JOB / f"{JOB} CNC Job Sheet.docx"
    assert copy.read_bytes() == b"a job sheet" and kept.sheet_of(ctx.store, JOB)["written"]
    assert not list(ctx.settings.ng_jobs.rglob("* CNC Job Sheet.docx"))


# ----- re-send: only where the after-drop step offered it


def test_a_re_send_is_refused_unless_the_after_drop_step_offered_it(ctx) -> None:
    from ngrunner.store.locks import update_batch

    batch = add_batch(ctx.store, id="20261031T130000Z-abc123", folder=JOB, kind="lock_design", verb="append",
                      material=None, rows=ROWS, file="tracker_inbox_20261031T130000Z-abc123.csv", dropped_at=T0)
    update_batch(ctx.store, batch.id, state="doubt", words="The tracker doesn't show the batch as written")
    kept.keep_after(ctx.store, batch.id, {"state": "doubt", "can_resend": False, "words": "1 row not as written"})

    assert work.ask_resend(ctx, batch.id) == templates.LOCK_GONE and kept.requests(ctx.store) == []

    kept.keep_after(ctx.store, batch.id, {"state": "doubt", "can_resend": True, "words": "1 row missing"})
    assert work.ask_resend(ctx, batch.id) == templates.resend_asked(JOB)
    assert [r["kind"] for r in kept.requests(ctx.store)] == ["resend"]


# ----- staging a fix's room: planned again when asked, and never over a fix not judged yet


def test_a_fix_whose_room_was_saved_since_its_plan_is_planned_again(ctx) -> None:
    item = kept.add_pending(ctx.store, kind="apply", at=T0, folder=JOB, plan=PLAN)
    kept.update_pending(ctx.store, item["id"], staged=True, replan=True)

    job = work.LockWork(InlineFetches(), running=not_running)._stage_work(ctx, T0)

    assert job is not None and job()[0] == "plan"


def test_a_room_is_not_staged_over_an_earlier_fix_still_waiting_for_jims_next_save(ctx) -> None:
    written_fix(ctx)
    kept.add_pending(ctx.store, kind="apply", at=T0, folder=JOB, plan={**PLAN, "fingerprint": "e" * 64})
    lock = work.LockWork(InlineFetches(), running=not_running)

    assert lock._stage_work(ctx, T0) is None
