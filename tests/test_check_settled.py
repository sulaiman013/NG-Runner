"""
One settled list for all jobs (design 3.3, "Settled entry", and section 6: "Settle defaults to this job. 'All
jobs' must name a condition. It takes effect at once, is written to the shared settled list the chat
reads"): checks/settled.py and jobchecks.settle with scope all_jobs.

The rules held here:
- A settle for all jobs needs the condition it holds under, in Jim's words, and only an item that may be
  settled at all can be.
- It stands for every finding of its kind (the checker and what it found) on any job, through new runs and
  design changes, where a settle of this job alone asks again.
- It is written to NG Runner's own settled list, `NG Jobs\\_NG Runner\\`, and in shadow (P4's build, before
  go-live) only to NG Runner's own copy of that folder, never the real NG Jobs.
- Each job's notes carry the settles that stand for its items with their scope, so the chat reading a job's
  notes alongside the list sees one answer.
Job names are made up.
"""
from __future__ import annotations

import json

from helpers_checks import room
from ngrunner.checks import jobchecks as jc
from ngrunner.checks import settled
from ngrunner.config import shadow_roots
from ngrunner.messages import templates
from ngrunner.store import ensure_job, list_settles, update_job
from test_check_runs import JOB, NOW, SID, adopted, built, checked, fixes, tick, world  # noqa: F401

OTHER_SID, OTHER = "4343", "Sampleton_101526"
CONDITION = "When the notch sits on a finished end the chat builds by hand"


def settle_all(world, *, sid=SID, words=CONDITION, n=None):
    view = jc.job_checks(world.ctx.store, NOW)[sid]
    item = fixes(view)[0] if n is None else next(s for s in view.items if s.item.n == n)
    return jc.settle(world.ctx, sid, view.run.id, item.item.n, how="not_a_problem", words=words,
                     channel="board", scope="all_jobs")


def other_job(world, *, fault=True):
    from helpers_checks import conversion, mozaik_job

    ensure_job(world.ctx.store, OTHER_SID, OTHER, now=NOW)
    update_job(world.ctx.store, OTHER_SID, folder=OTHER, branch="closet", now=NOW)
    conversion(world.ctx.settings.ng_jobs, OTHER)
    mozaik_job(world.ctx.settings.mozaik_shared, OTHER, room1=room(fault=fault, width=380))
    jc.ask(world.ctx, OTHER_SID, trigger="check_now")
    tick(world)


def test_a_settle_for_all_jobs_needs_its_condition_in_jims_words(world) -> None:
    adopted(world)
    built(world, fault=True)
    checked(world)

    for words in ("", "   "):
        assert settle_all(world, words=words) == templates.SETTLE_NEEDS_CONDITION
    assert list_settles(world.ctx.store) == []

    assert settle_all(world) == templates.settled_echo(JOB, n=1, how="not_a_problem", all_jobs=True)
    settle, = list_settles(world.ctx.store)
    assert (settle.scope, settle.checker, settle.what, settle.words) == ("all_jobs", "notch_check", "notch_face",
                                                                         CONDITION)


def test_it_stands_for_the_same_kind_of_finding_on_every_job_and_through_a_real_change(world) -> None:
    adopted(world)
    job = built(world, fault=True)
    checked(world)
    settle_all(world)

    other_job(world)
    (job / "Room1.des").write_bytes(room(fault=True, width=400))      # a real change: a job's own settle asks again
    tick(world)
    checked(world)

    for sid in (SID, OTHER_SID):
        view = jc.job_checks(world.ctx.store, NOW)[sid]
        assert fixes(view) == [] and view.settled == 1 and view.to_fix == 0, sid
        assert view.items[0].settle.scope == "all_jobs"


def test_it_is_written_to_ng_runners_own_settled_list_and_never_the_real_ng_jobs(world) -> None:
    adopted(world)
    built(world, fault=True)
    checked(world)

    settle_all(world)

    copy = shadow_roots(world.ctx.paths).ng_jobs / settled.FOLDER / settled.FILE
    listed = json.loads(copy.read_text(encoding="utf-8"))
    assert listed["schema"] == 1
    entry, = listed["settled"]
    assert entry == {"scope": "all jobs", "checker": "notch_check", "what": "notch_face",
                     "words": "The notch is on the wrong face", "condition": CONDITION,
                     "how": "not_a_problem", "said": templates.SETTLE_LABELS["not_a_problem"], "at": entry["at"],
                     "active": True, "absorbed_by_rules": False}
    assert not (world.ctx.settings.ng_jobs / settled.FOLDER).exists()
    assert not list(world.ctx.settings.ng_jobs.rglob(settled.FILE))


def test_the_list_is_written_again_when_it_is_missing_or_not_what_the_store_says(world) -> None:
    adopted(world)
    built(world, fault=True)
    checked(world)
    settle_all(world)
    copy = shadow_roots(world.ctx.paths).ng_jobs / settled.FOLDER / settled.FILE
    written = copy.read_bytes()

    copy.unlink()
    assert settled.keep(world.ctx) is None and copy.read_bytes() == written
    assert settled.keep(world.ctx) is None and copy.read_bytes() == written          # unchanged: nothing written


def test_each_jobs_notes_carry_the_settles_that_stand_for_its_items_with_their_scope(world) -> None:
    adopted(world)
    built(world, fault=True)
    checked(world)
    settle_all(world)

    block, said = jc.notes_block(JOB, jc.job_checks(world.ctx.store, NOW)[SID])

    entry, = said
    assert (entry["scope"], entry["condition"], entry["how"]) == ("all jobs", CONDITION, "not_a_problem")
    assert entry["list"] == f"NG Jobs\\{settled.FOLDER}\\{settled.FILE}"
    assert block["items"][0]["settled"] is True


def test_a_settle_of_this_job_alone_still_asks_again_on_another_job(world) -> None:
    adopted(world)
    built(world, fault=True)
    checked(world)
    view = jc.job_checks(world.ctx.store, NOW)[SID]
    jc.settle(world.ctx, SID, view.run.id, fixes(view)[0].item.n, how="not_a_problem",
              words=templates.SETTLE_LABELS["not_a_problem"], channel="board")

    other_job(world)

    assert len(fixes(jc.job_checks(world.ctx.store, NOW)[OTHER_SID])) == 1
    assert not (shadow_roots(world.ctx.paths).ng_jobs / settled.FOLDER / settled.FILE).exists()
