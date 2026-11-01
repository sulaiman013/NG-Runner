"""
The checks at work (P3: "sections done", Check now, the audit of a chat-built job, settles and the close
prompt): ngrunner/checks/jobchecks.py, with the real runner, readers, store and a made-up toolkit whose
crosscheck reads the rooms it is handed (tests/helpers_checks.py). Runs happen inline here; in the app
they run off the tick's thread, one at a time.

The rules held here:
- A check runs on staged copies in NG Runner's data folder, never in place and never on a cloud or
  network drive.
- A run is bound to the very bytes of the rooms it ran on, and the IC Conversion's: a later save makes it
  stale, and a stale run never counts.
- A settle is bound to its finding and to the rooms' design identities: the same design saved again
  keeps it, and any real change asks again.
- No adopted toolkit: it says so plainly, and nothing runs.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers_checks import adopt, conversion, fake_toolkit, mozaik_job, room, runs_of
from ngrunner.checks import jobchecks as jc
from ngrunner.clock import FakeClock, to_iso
from ngrunner.config import AppPaths, Settings
from ngrunner.guards.kept import NEST_FILES
from ngrunner.intake.pipeline import InlineFetches
from ngrunner.messages import templates
from ngrunner.messages.lint import CLEAN, lint
from ngrunner.store import (
    ensure_job, get_check_run, list_check_runs, list_dev, list_events, list_prompts, list_settles,
    open_store, set_state, update_job,
)

Z = timezone.utc
NOW = datetime(2026, 10, 31, 4, 42, tzinfo=Z)          # 9:42 PM Pacific
SID, JOB = "4242", "Testerson_101426"


class Told:
    """The messenger and the toaster as the checks see them: what they would have said."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []
        self.toasts: list[object] = []

    def send(self, message: object, *, to: str, channel: str = "email", ref: str | None = None) -> str:
        self.sent.append((to, message))
        return "0" * 32

    def toast(self, message: object) -> None:
        self.toasts.append(message)


@pytest.fixture
def world(tmp_path: Path) -> SimpleNamespace:
    paths = AppPaths(tmp_path / "data")
    paths.app_data.mkdir(parents=True)
    settings = Settings(ng_jobs=tmp_path / "NG Jobs", mozaik_shared=tmp_path / "Mozaik Shared",
                        control=tmp_path / "control")
    for folder in (settings.ng_jobs, settings.mozaik_shared / "Jobs", settings.control):
        folder.mkdir(parents=True)
    store = open_store(paths.db)
    ensure_job(store, SID, JOB, now=NOW)
    update_job(store, SID, folder=JOB, branch="closet", now=NOW)
    told = Told()
    ctx = SimpleNamespace(store=store, settings=settings, paths=paths, clock=FakeClock(NOW), mode="live",
                          messenger=told)
    log = tmp_path / "runs.jsonl"
    return SimpleNamespace(ctx=ctx, told=told, runner=InlineFetches(), log=log, tmp=tmp_path,
                           source=fake_toolkit(tmp_path / "jims", log=log))


def adopted(world: SimpleNamespace) -> None:
    adopt(world.ctx.store, world.ctx.paths.toolkit, world.source, at=NOW)


def built(world: SimpleNamespace, *, fault: bool = False) -> Path:
    conversion(world.ctx.settings.ng_jobs, JOB)
    return mozaik_job(world.ctx.settings.mozaik_shared, JOB, room1=room(fault=fault))


def tick(world: SimpleNamespace, minutes: float = 1) -> None:
    world.ctx.clock.advance(minutes * 60)
    jc.tick(world.ctx, world.runner, toast=world.told.toast)


def latest(world: SimpleNamespace):
    return list_check_runs(world.ctx.store, SID)[-1]


def checked(world: SimpleNamespace, *, trigger: str = "check_now"):
    reply = jc.ask(world.ctx, SID, trigger=trigger)
    tick(world)
    return reply, latest(world)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ----- no toolkit: said plainly, nothing runs


def test_with_no_adopted_toolkit_a_check_says_so_and_runs_nothing(world) -> None:
    built(world)

    reply, run = checked(world)

    assert reply == f"{templates.NO_TOOLKIT}."
    assert (run.state, run.words) == ("not_run", templates.NO_TOOLKIT)
    assert runs_of(world.log) == []
    view = jc.job_checks(world.ctx.store, NOW)[SID]
    assert view.state == "not_run" and templates.NO_TOOLKIT in view.line


# ----- a check: staged, bound to what it ran on, told


def test_a_check_runs_on_staged_copies_and_is_bound_to_the_bytes_it_ran_on(world) -> None:
    adopted(world)
    job = built(world, fault=True)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in job.iterdir()}

    reply, run = checked(world)

    assert reply == templates.check_asked(JOB)
    handed, = runs_of(world.log)
    assert all(world.ctx.paths.staging in Path(p).parents for p in handed)      # never the job in place
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in job.iterdir()} == before
    assert not any((world.ctx.paths.staging / "checks").iterdir())               # its staging goes
    assert (run.state, run.code, run.kind, run.trigger) == ("done", "failed", "check", "check_now")
    assert run.rooms == {name: sha((job / name).read_bytes()) for name in ("Room0.des", "Room1.des")}
    assert run.conversion_sha256 == sha((world.ctx.settings.ng_jobs / JOB / f"{JOB}_IC_Conversion.json")
                                        .read_bytes())
    assert run.snapshot_sha256 and set(run.identities) == set(run.rooms) and run.trial
    assert run.stats["Room1.des"][0] == (job / "Room1.des").stat().st_size


def test_what_a_check_found_is_an_item_with_its_chat_line_on_the_job(world) -> None:
    adopted(world)
    built(world, fault=True)

    checked(world)

    view = jc.job_checks(world.ctx.store, NOW)[SID]
    assert view.state == "failed" and view.to_fix == 1 and view.counts
    item, = [shown for shown in view.open if shown.item.kind == "fix"]
    assert templates.check_item_words(item.item) == "R1C3: the notch is on the wrong face"
    assert templates.check_chat_line(JOB, item.item) == (
        f"{JOB}: put the notch on its line-bore face on R1C3, then run crosscheck")


def test_a_check_jim_asked_for_is_told_at_once_as_a_toast_in_his_format(world) -> None:
    adopted(world)
    built(world, fault=True)

    checked(world)
    tick(world)

    toast, = world.told.toasts
    assert toast.kind == "check_result" and toast.title.startswith(f"{JOB} checked")
    assert toast.title.endswith("(trial)")
    assert toast.lines[0].startswith("1. R1C3: the notch is on the wrong face. In chat: ")
    assert world.told.sent == []
    assert get_check_run(world.ctx.store, latest(world).id).told_at is not None


def test_in_shadow_mode_a_check_is_told_only_to_sulaiman_and_no_toast_shows(world) -> None:
    world.ctx.mode = "shadow"
    adopted(world)
    built(world, fault=True)

    checked(world)

    to, message = world.told.sent[0]
    assert to == "jim" and message.kind == "check_result"       # the messenger redirects it, marked
    assert world.told.toasts == []


def test_a_clean_check_reads_exactly_the_clean_line(world, monkeypatch) -> None:
    monkeypatch.setattr("ngrunner.checks.findings.Q1_SETTLED", True)
    adopted(world)
    built(world)

    checked(world)

    toast, = world.told.toasts
    assert toast.lines == (CLEAN,)
    assert jc.job_checks(world.ctx.store, NOW)[SID].state == "passed"


def test_until_q1_is_settled_a_run_with_nothing_wrong_still_reads_could_not_verify(world) -> None:
    adopted(world)
    built(world)

    checked(world)

    view = jc.job_checks(world.ctx.store, NOW)[SID]
    assert view.state == "could_not_verify" and view.to_fix == 0 and view.unverified == 1
    assert world.told.toasts[0].lines != (CLEAN,)


def test_every_line_a_check_tells_passes_jims_format(world) -> None:
    adopted(world)
    built(world, fault=True)

    checked(world)

    toast, = world.told.toasts
    from ngrunner.messages import Message
    assert lint(Message("check_result", toast.title, toast.lines)) == []


def test_asking_twice_asks_once(world) -> None:
    adopted(world)
    built(world)

    first = jc.ask(world.ctx, SID, trigger="check_now")
    again = jc.ask(world.ctx, SID, trigger="sections_done")

    assert first == templates.check_asked(JOB) and again == templates.check_already_asked(JOB)
    assert len(list_check_runs(world.ctx.store, SID)) == 1


def test_a_job_nng_runner_doesnt_know_is_refused_plainly(world) -> None:
    assert jc.ask(world.ctx, "999", trigger="check_now") == templates.NOT_A_JOB


# ----- what it needs that isn't there


def test_no_ic_conversion_yet_is_not_run_saying_so(world) -> None:
    adopted(world)
    mozaik_job(world.ctx.settings.mozaik_shared, JOB)

    _, run = checked(world)

    assert run.state == "not_run" and "IC Conversion" in run.words
    assert runs_of(world.log) == []


def test_no_job_folder_in_mozaik_shared_is_not_run_saying_so(world) -> None:
    adopted(world)
    conversion(world.ctx.settings.ng_jobs, JOB)

    _, run = checked(world)

    assert run.state == "not_run" and "Mozaik Shared" in run.words


def test_a_job_open_in_mozaik_waits_and_checks_once_it_closes(world) -> None:
    adopted(world)
    job = built(world)
    (job / "MozaikJob.LCK").write_bytes(b"JDELA\r\n")

    _, run = checked(world)
    assert run.state == "asked" and runs_of(world.log) == []
    assert jc.job_checks(world.ctx.store, NOW)[SID].state == "waiting"

    (job / "MozaikJob.LCK").unlink()
    tick(world)
    assert latest(world).state == "done" and len(runs_of(world.log)) == 1


def test_a_work_folder_not_on_a_local_disk_runs_nothing(world, monkeypatch) -> None:
    from ngrunner import toolkit
    import win32file

    adopted(world)
    built(world)
    monkeypatch.setattr(toolkit, "_volume", lambda root: (win32file.DRIVE_FIXED, "FAT32"))

    _, run = checked(world)

    assert run.state == "not_run" and "local disk" in run.words
    assert runs_of(world.log) == []


def test_a_check_that_stops_with_an_error_is_not_run_and_goes_to_sulaiman(world, monkeypatch) -> None:
    adopted(world)
    built(world)

    def broken(*args, **kwargs):
        raise RuntimeError("a checker blew up")

    monkeypatch.setattr(jc, "run_crosscheck", broken)
    _, run = checked(world)

    assert run.state == "not_run" and "error" in run.words
    assert {item.code for item in list_dev(world.ctx.store)} == {jc.CHECK_FAILED}


def test_a_check_that_runs_past_its_time_is_given_up_and_goes_to_sulaiman(world) -> None:
    adopted(world)
    built(world)

    class Hung(InlineFetches):
        def start(self, key, work):
            self.key = key

        def overran(self):
            return [self.key] if hasattr(self, "key") else []

    world.runner = Hung()
    _, run = checked(world)
    tick(world)

    assert latest(world).state == "not_run"
    assert {item.code for item in list_dev(world.ctx.store)} == {jc.CHECK_STUCK}


# ----- a later save: stale, and a stale run never counts


def test_a_save_after_a_check_makes_it_stale_and_it_never_counts_again(world) -> None:
    adopted(world)
    job = built(world, fault=True)
    _, run = checked(world)
    rooms_then = dict(run.rooms)
    assert jc.counts(run, rooms=rooms_then, conversion_sha256=run.conversion_sha256)

    (job / "Room1.des").write_bytes(room(fault=True, note="saved again"))
    tick(world)

    stale = get_check_run(world.ctx.store, run.id)
    assert stale.stale_at is not None
    assert not jc.counts(stale, rooms=rooms_then, conversion_sha256=run.conversion_sha256)
    view = jc.job_checks(world.ctx.store, NOW)[SID]
    assert view.state == "stale" and not view.counts and view.open == () and view.to_fix == 0
    assert "check it again" in view.line


@pytest.mark.parametrize("change", ["a room's bytes", "a room added", "a room gone", "the IC Conversion"])
def test_counts_needs_exactly_the_bytes_it_ran_on(world, change) -> None:
    adopted(world)
    built(world)
    _, run = checked(world)
    rooms, conv = dict(run.rooms), run.conversion_sha256
    if change == "a room's bytes":
        rooms["Room1.des"] = "f" * 64
    elif change == "a room added":
        rooms["Room2.des"] = "f" * 64
    elif change == "a room gone":
        del rooms["Room1.des"]
    else:
        conv = "f" * 64

    assert jc.counts(run, rooms=dict(run.rooms), conversion_sha256=run.conversion_sha256)
    assert not jc.counts(run, rooms=rooms, conversion_sha256=conv)


def test_the_ic_conversion_changing_makes_a_check_stale_too(world) -> None:
    adopted(world)
    built(world)
    _, run = checked(world)

    conversion(world.ctx.settings.ng_jobs, JOB, '{"schema_version": "2.0", "changed": true}')
    tick(world)

    assert get_check_run(world.ctx.store, run.id).stale_at is not None


def test_nothing_changed_keeps_a_check_current(world) -> None:
    adopted(world)
    built(world)
    _, run = checked(world)

    tick(world, 30)

    assert get_check_run(world.ctx.store, run.id).stale_at is None
    assert jc.job_checks(world.ctx.store, NOW)[SID].counts


def test_a_job_changed_while_it_was_checked_is_stale_from_the_start(world, monkeypatch) -> None:
    adopted(world)
    job = built(world)
    real = jc.run_crosscheck

    def saving_meanwhile(*args, **kwargs):
        said = real(*args, **kwargs)
        (job / "Room1.des").write_bytes(room(width=400))
        return said

    monkeypatch.setattr(jc, "run_crosscheck", saving_meanwhile)
    _, run = checked(world)

    assert run.state == "done" and run.stale_at is not None


# ----- settles: bound to the finding and the design; a change asks again


def fixes(view) -> list:
    """The items still to fix: could-not-verify ones stay open too, but are no finding to settle."""
    return [shown for shown in view.open if shown.item.kind == "fix"]


def settle_first(world, how="not_a_problem"):
    view = jc.job_checks(world.ctx.store, NOW)[SID]
    item = fixes(view)[0]
    return jc.settle(world.ctx, SID, view.run.id, item.item.n, how=how,
                     words=templates.SETTLE_LABELS[how], channel="board")


def test_a_settled_item_leaves_your_list_and_is_kept_with_jims_words(world) -> None:
    adopted(world)
    built(world, fault=True)
    checked(world)

    reply = settle_first(world, "told_chat")

    assert reply == templates.settled_echo(JOB, n=1, how="told_chat")
    settle, = list_settles(world.ctx.store, SID)
    assert (settle.how, settle.words, settle.channel) == ("told_chat", "I already told chat", "board")
    view = jc.job_checks(world.ctx.store, NOW)[SID]
    assert fixes(view) == [] and view.settled == 1 and view.to_fix == 0
    assert [e.kind for e in list_events(world.ctx.store) if e.kind == "settled"] == ["settled"]


def test_the_same_design_saved_again_keeps_the_settle_through_a_new_check(world) -> None:
    adopted(world)
    job = built(world, fault=True)
    checked(world)
    settle_first(world)

    (job / "Room1.des").write_bytes(room(fault=True, note="Mozaik saved it again"))     # same design
    tick(world)
    _, again = checked(world)

    assert again.id != list_check_runs(world.ctx.store, SID)[0].id
    view = jc.job_checks(world.ctx.store, NOW)[SID]
    assert view.run.id == again.id and fixes(view) == [] and view.settled == 1


def test_a_real_change_after_a_settle_asks_again(world) -> None:
    adopted(world)
    job = built(world, fault=True)
    checked(world)
    settle_first(world)

    (job / "Room1.des").write_bytes(room(fault=True, width=400))                        # a real change
    tick(world)
    checked(world)

    view = jc.job_checks(world.ctx.store, NOW)[SID]
    assert len(fixes(view)) == 1 and view.settled == 0 and view.to_fix == 1


def test_an_ic_conversion_changed_after_a_settle_asks_again(world) -> None:
    adopted(world)
    built(world, fault=True)
    checked(world)
    settle_first(world)

    conversion(world.ctx.settings.ng_jobs, JOB, '{"schema_version": "2.0", "rev": 2}')
    tick(world)
    checked(world)

    assert len(fixes(jc.job_checks(world.ctx.store, NOW)[SID])) == 1


def test_a_stale_check_can_not_be_settled(world) -> None:
    adopted(world)
    job = built(world, fault=True)
    checked(world)
    run = latest(world)
    (job / "Room1.des").write_bytes(room(fault=True, width=400))
    tick(world)

    reply = jc.settle(world.ctx, SID, run.id, 1, how="not_a_problem", words="Not a problem", channel="board")

    assert reply == templates.SETTLE_STALE and list_settles(world.ctx.store) == []


def test_an_item_that_can_never_be_settled_is_refused(world) -> None:
    adopted(world)
    built(world)
    checked(world)                      # Q1 open: the parameters could not be verified

    view = jc.job_checks(world.ctx.store, NOW)[SID]
    cnv = next(item for item in view.items if item.item.kind == "could_not_verify")
    reply = jc.settle(world.ctx, SID, view.run.id, cnv.item.n, how="not_a_problem", words="x", channel="board")

    assert reply == templates.SETTLE_NEVER and list_settles(world.ctx.store) == []


# ----- trial labels: until the checks and each feature have been exercised


def test_checks_read_trial_until_fifteen_have_run_and_a_new_feature_stays_on_trial(world, monkeypatch) -> None:
    adopted(world)
    job = built(world)
    for n in range(jc.TRIAL_CHECKS):
        _, run = checked(world)
        assert run.trial, n
        (job / "Room1.des").write_bytes(room(width=300 + n))
        tick(world)

    _, plain = checked(world)
    assert not plain.trial and plain.features == ()

    (job / "Room1.des").write_bytes(room(products='<Product CabNo="9" Count="1" ProdName="84 BR Left" />'))
    tick(world)
    _, bridge = checked(world)
    assert bridge.trial and bridge.features == ("bridge",)


# ----- the close prompt (group 2: built on what the guards keep of the job's lock files)


def holding(world, held: bool) -> None:
    """What the guards keep of the job's lock files: MozaikJob.LCK held, or gone. ASSUMPTION: the lock is
    there while the job is open in Mozaik and goes when it closes (no real .LCK has been read)."""
    entry = [["MozaikJob.LCK", "JDELA", to_iso(world.ctx.clock.now())]] if held else []
    set_state(world.ctx.store, NEST_FILES, json.dumps({JOB: {"opts": {}, "rooms": {}, "locks": {},
                                                               "held": entry}}))


def test_closing_a_job_asks_whether_sections_are_done_first_with_three_answers(world) -> None:
    built(world)
    holding(world, True)
    tick(world)
    assert list_prompts(world.ctx.store) == []

    holding(world, False)
    tick(world)

    prompt, = list_prompts(world.ctx.store, SID)
    assert prompt.first
    toast, = world.told.toasts
    assert toast.kind == "close_prompt" and toast.sticky
    assert [c.label for c in toast.choices] == list(templates.CHECK_CLOSE_FIRST)
    assert toast.choices[0].argument == f"{jc.PROMPT_ARG} {prompt.id} 1"


def test_a_later_close_asks_with_two_answers_at_most_once_in_three_hours(world) -> None:
    built(world)
    for _ in range(2):
        holding(world, True)
        tick(world)
        holding(world, False)
        tick(world)
    assert len(list_prompts(world.ctx.store, SID)) == 1

    world.ctx.clock.advance(3 * 3600)
    holding(world, True)
    tick(world)
    holding(world, False)
    tick(world)

    first, later = list_prompts(world.ctx.store, SID)
    assert not later.first and [c.label for c in world.told.toasts[-1].choices] == list(templates.CHECK_CLOSE)


def test_yes_check_it_asks_a_check_and_ignoring_it_pins_it_until_answered(world) -> None:
    adopted(world)
    built(world)
    holding(world, True)
    tick(world)
    holding(world, False)
    tick(world)
    prompt, = list_prompts(world.ctx.store, SID)

    view = jc.job_checks(world.ctx.store, world.ctx.clock.now())[SID]
    assert view.prompt is not None and view.prompt.id == prompt.id     # ignored: pinned, never checked

    reply = jc.answer_prompt(world.ctx, prompt.id, templates.CHECK_CLOSE_FIRST[0], words="1", channel="toast")
    tick(world)

    assert reply == templates.check_asked(JOB)
    assert latest(world).trigger == "sections_done" and latest(world).state == "done"
    assert jc.job_checks(world.ctx.store, world.ctx.clock.now())[SID].prompt is None


def test_not_yet_runs_nothing_and_an_error_goes_to_sulaiman(world) -> None:
    built(world)
    holding(world, True)
    tick(world)
    holding(world, False)
    tick(world)
    prompt, = list_prompts(world.ctx.store, SID)

    reply = jc.answer_prompt(world.ctx, prompt.id, templates.CHECK_CLOSE_FIRST[2], words="3", channel="board")

    assert list_check_runs(world.ctx.store, SID) == []
    assert {item.code for item in list_dev(world.ctx.store)} == {jc.MOZAIK_ERROR}
    assert reply == templates.mozaik_error_echo(JOB)
    assert jc.answer_prompt(world.ctx, prompt.id, templates.CHECK_CLOSE_FIRST[0], words="1",
                            channel="board") == templates.PROMPT_ANSWERED


def kept_rooms(world, *, held: bool, since: datetime) -> None:
    """What the guards keep of the job: its lock files, and when its rooms last changed as listings saw."""
    entry = [["MozaikJob.LCK", "JDELA", to_iso(world.ctx.clock.now())]] if held else []
    rooms = {"seen": "x", "since": to_iso(since), "read": "x"}
    set_state(world.ctx.store, NEST_FILES, json.dumps({JOB: {"opts": {}, "rooms": rooms, "locks": {},
                                                               "held": entry}}))


def opened_and_closed(world, *, rooms_since: datetime) -> None:
    kept_rooms(world, held=True, since=rooms_since)
    tick(world)
    kept_rooms(world, held=False, since=rooms_since)
    tick(world)


def test_a_later_close_with_nothing_saved_since_its_check_asks_nothing(world) -> None:
    adopted(world)
    built(world)
    opened_and_closed(world, rooms_since=NOW)                   # the first close asks, always
    first, = list_prompts(world.ctx.store, SID)
    jc.answer_prompt(world.ctx, first.id, templates.CHECK_CLOSE_FIRST[0], words="1", channel="toast")
    tick(world)
    assert latest(world).state == "done"

    world.ctx.clock.advance(3 * 3600)
    opened_and_closed(world, rooms_since=NOW)                   # opened, looked at, closed: nothing saved

    assert len(list_prompts(world.ctx.store, SID)) == 1 and len(world.told.toasts) == 2   # the prompt, the result


def test_a_later_close_after_a_save_asks_sections_done_with_two_answers(world) -> None:
    adopted(world)
    job = built(world)
    _, run = checked(world)
    world.ctx.clock.advance(3600)

    (job / "Room1.des").write_bytes(room(width=400))            # Jim saves a change while the job is open
    saved = world.ctx.clock.now()
    opened_and_closed(world, rooms_since=saved)

    prompt, = list_prompts(world.ctx.store, SID)
    assert not prompt.first and get_check_run(world.ctx.store, run.id).stale_at is not None
    toast = world.told.toasts[-1]
    assert toast.kind == "close_prompt" and [c.label for c in toast.choices] == list(templates.CHECK_CLOSE)
    assert toast.lines == ("Sections done?",) and toast.title.startswith(f"{JOB} closed")


def test_a_change_the_listing_saw_after_the_check_began_asks_even_before_the_run_goes_stale(world) -> None:
    from ngrunner.store import CheckRunRecord

    done = CheckRunRecord(1, SID, JOB, "check", "check_now", "done", NOW, NOW, NOW, "passed", "", "", "", {}, {},
                          {}, None, (), False, None, None)
    after = {"seen": "y", "since": to_iso(NOW + jc.timedelta(minutes=1)), "read": None}
    before = {"seen": "x", "since": to_iso(NOW - jc.timedelta(minutes=1)), "read": "x"}

    assert jc.unchecked_change([done], after) and not jc.unchecked_change([done], before)
    assert jc.unchecked_change([], before) and jc.unchecked_change([done], None)       # can't tell: ask


def test_a_close_while_a_check_waits_for_it_asks_nothing_and_the_check_runs(world) -> None:
    adopted(world)
    job = built(world)
    (job / "MozaikJob.LCK").write_bytes(b"JDELA\r\n")
    kept_rooms(world, held=True, since=NOW)
    jc.ask(world.ctx, SID)
    tick(world)
    assert latest(world).words == jc.WAITING

    (job / "MozaikJob.LCK").unlink()
    kept_rooms(world, held=False, since=NOW)
    tick(world)
    tick(world)

    assert list_prompts(world.ctx.store, SID) == [] and latest(world).state == "done"


def test_in_shadow_mode_no_close_prompt_toast_shows(world) -> None:
    world.ctx.mode = "shadow"
    built(world)
    holding(world, True)
    tick(world)
    holding(world, False)
    tick(world)

    assert len(list_prompts(world.ctx.store, SID)) == 1 and world.told.toasts == []


# ----- the audit of a chat-built job: once, when its rooms appear, before sections go in


def listed(world, *, rooms: bool) -> None:
    said = {JOB: {"opts": {}, "locks": {}, "held": [],
                  "rooms": {"seen": "x", "since": to_iso(world.ctx.clock.now()), "read": "x"}}} if rooms else {}
    set_state(world.ctx.store, NEST_FILES, json.dumps(said))


def test_a_job_built_after_install_is_audited_once_and_one_there_at_install_never(world) -> None:
    adopted(world)
    built(world)
    listed(world, rooms=False)
    tick(world)                                        # the first look: nothing built yet
    listed(world, rooms=True)
    tick(world)
    tick(world, 10)

    audit, = list_check_runs(world.ctx.store, SID)
    assert (audit.kind, audit.trigger, audit.state) == ("audit", "audit", "done")
    assert world.told.toasts == []                     # nobody asked: the board and the digest say it


def test_a_job_there_at_the_first_look_is_never_audited(world) -> None:
    adopted(world)
    built(world)
    listed(world, rooms=True)
    tick(world)
    tick(world, 10)

    assert list_check_runs(world.ctx.store, SID) == []


def test_a_check_cut_short_by_a_restart_is_asked_again_and_runs(world) -> None:
    from ngrunner.store import ask_check, start_check

    adopted(world)
    built(world)
    run = ask_check(world.ctx.store, studio_id=SID, folder=JOB, kind="check", trigger="check_now", at=NOW)
    start_check(world.ctx.store, run.id, at=NOW)            # running when NG Runner stopped: its worker died

    tick(world)

    assert get_check_run(world.ctx.store, run.id).state == "done" and len(runs_of(world.log)) == 1
