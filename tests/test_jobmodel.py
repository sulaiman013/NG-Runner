"""The job model over a store filled with each situation: ticks, overlays, next steps, Your list,
the tray and the digest (integration spec section 5). Synthetic names only."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from ngrunner import jobmodel
from ngrunner.board.views import BoardView, HealthView, TrayState
from ngrunner.clock import FakeClock
from ngrunner.digest import compose, reminders
from ngrunner.messages import templates
from ngrunner.messages.lint import check_line, lint
from ngrunner.store import (
    add_card, add_fab_row, add_outbox, add_pending, add_render, answer_card, close_dev, ensure_job,
    finish_pending, list_pending, log_event, mark_dev_told_jim, mark_outbox_written, open_dev,
    open_store, record_receipt, record_reminder, replace_opportunities, retry_pending,
    set_governing, set_state, set_state_time, update_fab_row, update_job, withdraw_card,
)

NOW = datetime(2026, 10, 30, 22, 5, tzinfo=timezone.utc)   # Fri 10/30 3:05 PM Pacific
MORNING = datetime(2026, 10, 30, 13, 30, tzinfo=timezone.utc)   # Fri 10/30 6:30 AM Pacific
HOUR = timedelta(hours=1)
HEALTH = HealthView(paths=(), last_export=None, list_synced_at=None, heartbeat=None, toasts=None)
JOB, OTHER = "Testerson_101426", "Fakeley_102726"


@pytest.fixture
def store(tmp_path):
    return open_store(tmp_path / "ngrunner.db")


@pytest.fixture
def clock():
    return FakeClock(NOW)


def a_job(store, name=JOB, sid="101", *, mode="auto", at=NOW - HOUR, install=None):
    ensure_job(store, sid, name, now=at, mode=mode, install=install)
    return sid


def a_row(store, text="Testerson", *, state="new", sid=None, install=None, reason=None,
          at=NOW - HOUR):
    row = add_fab_row(store, first_seen=at, text=text, designer="PE", install=install)
    return update_fab_row(store, row.id, now=at, state=state, studio_id=sid, reason=reason)


def a_card(store, kind="closet_or_garage", *, sid=None, row=None, at=NOW - HOUR, prefix="TES",
           choices=("One", "Two")):
    return add_card(store, prefix=prefix, kind=kind, text="Waiting on Jim.", choices=choices,
                    created_at=at, studio_id=sid, fab_row_id=row)


def a_digest(store, at, *, state=None):
    made = add_outbox(store, id=f"{at.day:032x}", seq=at.day, channel="email", recipient="jim",
                      subject="NG Runner · a day", lines=["Nothing new."], urgent=False,
                      created_at=at, ref=f"digest:{at.date()}")
    if state == "failed":
        record_receipt(store, made.id, at=at, ok=False)
    return made


def view_of(store, clock, *, paused=False, shadow=False):
    return jobmodel.board_view(store, None, clock, health=HEALTH, paused=paused, shadow=shadow)


def row_named(view, name=JOB):
    return next(row for row in view.jobs if row.name == name)


def ticks(row):
    return {tick.key: tick.state for tick in row.ticks}


def tray(store, clock, *, unwell=False, paused=False, shadow=False) -> TrayState:
    return jobmodel.tray_state(store, None, clock, unwell=unwell, paused=paused, shadow=shadow)


# ----- rows and ticks


def test_a_new_job_has_the_five_ticks_in_order_with_plain_labels(store, clock):
    a_job(store)
    row = row_named(view_of(store, clock))
    assert [(t.key, t.label) for t in row.ticks] == [
        ("row", "On the Fab Schedule"), ("job", "Matched to Salesforce"),
        ("summary", "Fetched and checked twice"), ("filed", "Filed"),
        ("branch", "Closet or garage"),
    ]
    assert ticks(row) == {"row": "todo", "job": "done", "summary": "todo", "filed": "todo",
                          "branch": "todo"}
    assert row.studio_id == "101"


def test_a_row_with_no_job_shows_as_typed_and_waits_on_its_card(store, clock):
    row = a_row(store, 'Testerson "the big one"', state="asked")
    a_card(store, "which_job", row=row.id, choices=(JOB, "None of these"))
    shown = row_named(view_of(store, clock), row.text)
    assert shown.studio_id is None
    assert shown.next_step == "Card TES-1: which job is this row?"
    assert ticks(shown)["row"] == "done" and ticks(shown)["job"] == "blocked"
    assert shown.overlay is None


@pytest.mark.parametrize("state", ["seen_at_install", "gone"])
def test_rows_nothing_will_happen_to_stay_off_the_board(store, clock, state):
    a_row(store, "Mockford", state=state)
    assert view_of(store, clock).jobs == ()


def test_a_job_on_the_schedule_ticks_its_row_and_keeps_it_when_the_row_goes_to_archive(store,
                                                                                       clock):
    sid = a_job(store)
    row = a_row(store, state="matched", sid=sid)
    assert ticks(row_named(view_of(store, clock)))["row"] == "done"
    update_fab_row(store, row.id, now=NOW, state="gone")
    assert ticks(row_named(view_of(store, clock)))["row"] == "done"


def test_a_filed_job_says_when_and_ticks_all_with_its_branch(store, clock):
    sid = a_job(store)
    a_row(store, state="filed", sid=sid)
    update_job(store, sid, now=NOW, branch="garage")
    log_event(store, at=NOW, kind="filed", text=f"Filed {JOB}.", lane="ACT", studio_id=sid)
    row = row_named(view_of(store, clock))
    assert row.next_step == "Filed 10/30 3:05 PM"
    assert row.overlay is None
    assert set(ticks(row).values()) == {"done"}
    assert [t.label for t in row.ticks if t.key == "branch"] == ["Garage"]


def test_a_refiled_job_says_its_latest_filing(store, clock):
    sid = a_job(store)
    log_event(store, at=NOW - 2 * HOUR, kind="filed", text="Filed.", lane="ACT", studio_id=sid)
    log_event(store, at=NOW, kind="filed", text="Filed again.", lane="ACT", studio_id=sid)
    assert row_named(view_of(store, clock)).next_step == "Filed 10/30 3:05 PM"


def test_a_filed_event_in_the_dev_lane_says_nothing_about_filing(store, clock):
    sid = a_job(store)
    log_event(store, at=NOW, kind="filed", text="Not really.", lane="DEV", studio_id=sid)
    assert ticks(row_named(view_of(store, clock)))["filed"] == "todo"


def test_a_job_added_by_hand_counts_as_filed_by_its_event_alone(store, clock):
    sid = a_job(store)
    log_event(store, at=NOW, kind="filed", text=f"Filed {JOB}.", lane="ACT", studio_id=sid)
    row = row_named(view_of(store, clock))
    assert ticks(row)["filed"] == "done" and ticks(row)["row"] == "todo"


def test_a_filed_row_with_no_event_still_counts(store, clock):
    a_row(store, state="filed", sid=a_job(store), at=NOW - HOUR)
    assert row_named(view_of(store, clock)).next_step == "Filed 10/30 2:05 PM"


def test_the_summary_tick_needs_two_fetches_that_agree_and_are_really_two(store, clock):
    sid = a_job(store)

    def render(sha, fingerprint="same", check_ok=True, at=NOW):
        return add_render(store, studio_id=sid, fetched_at=at, check_ok=check_ok,
                          sha256=sha * 64, fingerprint=fingerprint)

    def state():
        return ticks(row_named(view_of(store, clock)))["summary"]

    render("a")
    assert state() == "todo"
    render("a")                     # one render served twice
    assert state() == "todo"
    render("b", fingerprint="other")
    assert state() == "todo"
    render("c", fingerprint="other", check_ok=False)
    assert state() == "todo"
    render("d", fingerprint="other")
    render("e", fingerprint="other")
    assert state() == "done"


def test_a_governing_render_ticks_the_summary(store, clock):
    sid = a_job(store)
    set_governing(store, add_render(store, studio_id=sid, fetched_at=NOW, check_ok=True).id)
    assert ticks(row_named(view_of(store, clock)))["summary"] == "done"


def test_a_fetch_card_blocks_the_summary_tick_and_a_branch_card_the_branch_tick(store, clock):
    a_card(store, "fetch_failed", sid=a_job(store))
    a_card(store, "closet_or_garage", sid=a_job(store, OTHER, "102"), prefix="FAK")
    view = view_of(store, clock)
    assert ticks(row_named(view))["summary"] == "blocked"
    assert ticks(row_named(view, OTHER))["branch"] == "blocked"


def test_moving_jobs_come_first_soonest_install_first_and_filed_jobs_last(store, clock):
    a_job(store, "Dummitt_111226", "1", install=("", "11/12/26"))
    a_job(store, "Mockford_110226", "2", install=("", "11/2/26"))
    done = a_job(store, "Fakeley_102726", "3", install=("", "10/31/26"))
    log_event(store, at=NOW, kind="filed", text="Filed.", lane="ACT", studio_id=done)
    a_job(store, "Testerson_101426", "4")
    assert [row.name for row in view_of(store, clock).jobs] == [
        "Mockford_110226", "Dummitt_111226", "Testerson_101426", "Fakeley_102726",
    ]


# ----- the install date


def test_the_install_date_is_a_date_when_the_cells_say_one(store, clock):
    a_row(store, state="matched", sid=a_job(store), install=("Wednesday", " 11 /4"))
    assert row_named(view_of(store, clock)).install == date(2026, 11, 4)


def test_the_install_date_says_could_not_verify_when_the_cells_dont_say_one(store, clock):
    a_row(store, state="matched", sid=a_job(store), install=("Friday", "week of 11/2"))
    assert row_named(view_of(store, clock)).install == "could not verify"


@pytest.mark.parametrize("cells", [None, ("", ""), ("  ", "")])
def test_the_install_date_is_none_only_with_no_cells(store, clock, cells):
    a_row(store, state="matched", sid=a_job(store), install=cells)
    assert row_named(view_of(store, clock)).install is None


def test_a_date_with_no_year_stays_near_when_the_row_appeared(store, clock):
    a_row(store, state="matched", sid=a_job(store), install=("", "7/1"), at=NOW)
    assert row_named(view_of(store, clock)).install == date(2027, 7, 1) - timedelta(days=365)
    clock.advance(timedelta(days=200))     # the nearest 7/1 to this day is next year's
    assert row_named(view_of(store, clock)).install == date(2026, 7, 1)


def test_a_job_with_no_row_uses_its_own_install_cells(store, clock):
    a_job(store, install=("", "11/9/26"))
    assert row_named(view_of(store, clock)).install == date(2026, 11, 9)


# ----- WAITING


def test_waiting_for_a_list(store, clock):
    a_row(store, state="waiting_list", sid=a_job(store))
    row = row_named(view_of(store, clock))
    assert (row.overlay, row.next_step) == ("WAITING", "Waiting for a fresh job list")


def test_a_row_waiting_for_a_list_with_no_job_yet(store, clock):
    a_row(store, "Mockford", state="waiting_list")
    row = row_named(view_of(store, clock), "Mockford")
    assert (row.overlay, row.next_step) == ("WAITING", "Waiting for a fresh job list")


def test_a_new_row_waits_one_export(store, clock):
    a_row(store, "Mockford", state="new")
    row = row_named(view_of(store, clock), "Mockford")
    assert row.overlay == "WAITING" and "next Fab export" in row.next_step


def test_a_pending_list_wait(store, clock):
    add_pending(store, "list_wait", due_at=NOW, studio_id=a_job(store))
    assert row_named(view_of(store, clock)).next_step == "Waiting for a fresh job list"


def test_the_second_fetch_says_when(store, clock):
    add_pending(store, "fetch_2", due_at=NOW + timedelta(minutes=10), studio_id=a_job(store))
    row = row_named(view_of(store, clock))
    assert (row.overlay, row.next_step) == ("WAITING", "Second fetch at 3:15 PM")


def test_a_fetch_tomorrow_names_the_day_and_one_due_now_says_now(store, clock):
    op = add_pending(store, "fetch_1", due_at=NOW + timedelta(days=1), studio_id=a_job(store))
    assert row_named(view_of(store, clock)).next_step == "First fetch Sat 10/31 at 3:05 PM"
    retry_pending(store, op.id, due_at=NOW)
    assert row_named(view_of(store, clock)).next_step == "Fetching again now"


def test_a_filed_job_with_a_nightly_refetch_is_not_waiting(store, clock):
    sid = a_job(store)
    a_row(store, state="filed", sid=sid)
    add_pending(store, "refetch", due_at=NOW + HOUR, studio_id=sid)
    row = row_named(view_of(store, clock))
    assert row.overlay is None and row.next_step.startswith("Filed")


# ----- a card is the next step


def test_a_card_is_the_next_step_and_waits_on_jim_not_on_ng_runner(store, clock):
    sid = a_job(store)
    a_row(store, state="matched", sid=sid)
    a_card(store, sid=sid, prefix="FAK")
    row = row_named(view_of(store, clock))
    assert (row.overlay, row.next_step) == (None, "Card FAK-1: closet or garage?")


def test_the_fetch_card_keeps_waiting_because_it_keeps_trying(store, clock):
    sid = a_job(store)
    add_pending(store, "fetch_1", due_at=NOW + HOUR, studio_id=sid)
    a_card(store, "fetch_failed", sid=sid)
    row = row_named(view_of(store, clock))
    assert row.overlay == "WAITING"
    assert row.next_step == "Card TES-1: could not get a complete IC summary"


def test_an_ic_changed_card_on_a_filed_job_shows_and_keeps_its_filed_tick(store, clock):
    sid = a_job(store)
    a_row(store, state="filed", sid=sid)
    a_card(store, "ic_changed", sid=sid)
    row = row_named(view_of(store, clock))
    assert row.next_step == "Card TES-1: IC changed, which render governs?"
    assert ticks(row)["filed"] == "done" and row.overlay is None


def test_the_remake_card_asks_about_a_remake(store, clock):
    sid = a_job(store)
    a_row(store, state="filed", sid=sid)
    a_card(store, "remake", sid=sid, choices=templates.REMAKE_CHOICES)
    row = row_named(view_of(store, clock))
    assert row.next_step == "Card TES-1: is this a remake?"
    assert view_of(store, clock).your_list[0].text == f"{JOB}: is this a remake? Card TES-1."


def test_the_design_card_says_the_two_lists_differ_and_holds_the_summary_tick(store, clock):
    a_card(store, "design_mismatch", sid=a_job(store), choices=templates.DESIGN_MISMATCH_CHOICES)
    row = row_named(view_of(store, clock))
    assert row.next_step == "Card TES-1: the report and Studio's proposal differ"
    assert ticks(row)["summary"] == "blocked"
    item = view_of(store, clock).your_list[0]
    assert item.text == f"{JOB}: the report and Studio's proposal differ. Card TES-1."
    assert not check_line(item.text)


def test_a_row_answered_with_a_job_that_left_the_list_says_so(store, clock):
    a_row(store, state="asked", reason="That job has left the job list, so pick another")
    row = row_named(view_of(store, clock), "Testerson")
    assert row.next_step == "That job has left the job list, so pick another"


def test_a_row_with_no_job_found_and_a_bare_code_for_its_reason_gets_fixed_words(store, clock):
    a_row(store, state="none", reason="row_too_long")
    row = row_named(view_of(store, clock), "Testerson")
    assert row.next_step == jobmodel.NO_JOB_STEP
    assert ticks(row)["job"] == "blocked"


# ----- BLOCKED, with its one-line fix


def refused(store, note, *, sid=None):
    sid = sid or a_job(store)
    add_pending(store, "file", due_at=NOW, studio_id=sid, note=note)
    return sid


def test_a_refused_filing_is_blocked_with_a_reason_and_a_one_line_fix(store, clock):
    refused(store, "NG Jobs is not reachable right now")
    row = row_named(view_of(store, clock))
    assert row.overlay == "BLOCKED"
    assert row.next_step == ("Blocked: NG Jobs is not reachable right now. Fix: check that this PC"
                             " can reach the NG Jobs folder")
    assert ticks(row)["filed"] == "blocked"


@pytest.mark.parametrize("note, words", [
    (r"Testerson_101426_summary.pdf was not written: G:\NG Jobs is not reachable right now.",
     "its folder can't be reached right now"),
    (r"C:\x\Testerson_101426_summary.pdf has changed since it was read, so it was not overwritten",
     "its folder changed during the filing"),
    (r"Not written: C:\x\y refused by something else", "NG Runner's safety check refused"),
])
def test_a_refusal_naming_a_file_or_path_is_put_in_fixed_words(store, clock, note, words):
    refused(store, note)
    step = row_named(view_of(store, clock)).next_step
    assert step.startswith(f"Blocked: {words}") and "Fix: " in step
    assert not check_line(step)


def test_a_filing_refused_as_unreachable_in_the_pipelines_words_gets_the_fix(store, clock):
    refused(store, "NG Jobs can't be reached right now, so nothing was written")
    row = row_named(view_of(store, clock))
    assert row.overlay == "BLOCKED"
    assert row.next_step == ("Blocked: NG Jobs can't be reached right now, so nothing was written."
                             " Fix: check that this PC can reach the NG Jobs folder")


def test_a_folder_in_old_has_nothing_to_fix_so_it_is_observed_and_says_why(store, clock):
    note = "Its folder is in the Old folder of NG Jobs, so NG Runner wrote nothing"
    refused(store, note)
    row = row_named(view_of(store, clock))
    assert (row.overlay, row.next_step) == ("OBSERVED", note)
    assert ticks(row)["filed"] == "todo"
    assert row_named(view_of(store, clock, paused=True)).overlay == "OBSERVED"


def test_the_words_and_kinds_this_reads_are_the_pipelines(store, clock):
    from ngrunner.intake import pipeline
    assert jobmodel.OLD_FOLDER_WORDS in pipeline._OLD_REASON.casefold()
    assert jobmodel.EXPORTED_AT_KEY == pipeline.FAB_EXPORTED_AT
    for words in (pipeline._OLD_REASON, pipeline._OBSERVED_REASON, pipeline._NOT_REACHABLE,
                  pipeline._CHOICE_GONE):
        assert not check_line(words), words
    sid = a_job(store)
    add_pending(store, pipeline.FETCH_2, due_at=NOW + HOUR, studio_id=sid)
    assert row_named(view_of(store, clock)).next_step == "Second fetch at 4:05 PM"
    other = a_job(store, OTHER, "102")
    log_event(store, at=NOW, kind="filed", text="Filed.", studio_id=other)
    for kind in (pipeline.REFETCH, pipeline.REFETCH_2, pipeline.LIST_REMATCH):
        add_pending(store, kind, due_at=NOW + HOUR, studio_id=other)
    assert row_named(view_of(store, clock), OTHER).overlay is None
    third = a_job(store, "Mockford_101426", "103")
    add_pending(store, pipeline.FILE_NEW, due_at=NOW + HOUR, note="Refused", studio_id=third)
    assert row_named(view_of(store, clock), "Mockford_101426").overlay == "BLOCKED"


def test_a_filing_not_refused_is_only_waiting(store, clock):
    add_pending(store, "file", due_at=NOW + HOUR, studio_id=a_job(store))
    row = row_named(view_of(store, clock))
    assert (row.overlay, row.next_step) == ("WAITING", "Filing at 4:05 PM")


def test_a_refused_refiling_shows_on_a_filed_job(store, clock):
    sid = a_job(store)
    a_row(store, state="filed", sid=sid)
    refused(store, "NG Jobs is not reachable right now", sid=sid)
    assert row_named(view_of(store, clock)).overlay == "BLOCKED"


# ----- a filed job's line: the counts a person who knows the job can check


def filed_job(store, facts, *, at=NOW - HOUR):
    from ngrunner.intake import pipeline

    sid = a_job(store)
    log_event(store, at=at, kind="filed", text="Filed.", studio_id=sid)
    set_state(store, pipeline._filed_key(sid), json.dumps(facts))
    return sid


@pytest.mark.parametrize("facts, words", [
    ({"designs": 3, "pages": 19, "designs_checked": True}, ", 3 designs, 19 pages"),
    ({"designs": 1, "pages": 1, "designs_checked": True}, ", 1 design, 1 page"),
    ({"designs": 3, "pages": 19, "designs_checked": False},
     ", 3 designs, 19 pages, designs not cross-checked"),
    ({"designs": 3, "pages": 19, "designs_checked": False, "designs_chosen": True},
     ", 3 designs, 19 pages, filed as it is, as Jim chose"),
    ({"designs": None, "pages": 19, "designs_checked": False}, ", designs not cross-checked"),
])
def test_a_filed_jobs_line_says_its_counts_and_if_they_were_not_cross_checked(
        store, clock, facts, words):
    filed_job(store, facts)
    step = row_named(view_of(store, clock)).next_step
    assert step == f"Filed 10/30 2:05 PM{words}"
    assert not check_line(step)
    day = jobmodel.digest_view(store, None, clock)
    assert [(line.job, line.text) for line in day.jobs] == [(JOB, f"filed 10/30 2:05 PM{words}")]
    assert not lint(compose(day))


def test_a_filed_job_with_no_facts_says_only_when(store, clock):
    sid = a_job(store)
    log_event(store, at=NOW - HOUR, kind="filed", text="Filed.", studio_id=sid)
    assert row_named(view_of(store, clock)).next_step == "Filed 10/30 2:05 PM"


def test_the_filed_line_and_the_pipeline_agree_on_the_words(store, clock):
    from ngrunner.intake import pipeline

    sid = filed_job(store, {"designs": 2, "pages": 7, "designs_checked": False})
    assert pipeline.filed_note(store, sid) == "2 designs, 7 pages, designs not cross-checked"
    assert jobmodel.filed_note_of is pipeline.filed_note_of


# ----- BLOCKED on Your list, once it has lasted over an hour


REACH = "NG Jobs is not reachable right now"
MINUTE = timedelta(minutes=1)


def blocked_since(store, since, note=REACH, *, sid=None):
    """A refused filing as the pipeline leaves one: the note, and an event from when it began."""
    sid = refused(store, note, sid=sid)
    log_event(store, at=since, kind="blocked", studio_id=sid, text=note)
    return sid


@pytest.mark.parametrize("lasted", [MINUTE, HOUR - MINUTE, HOUR])
def test_a_job_blocked_for_up_to_an_hour_stays_off_your_list(store, clock, lasted):
    blocked_since(store, NOW - lasted)
    view = view_of(store, clock)
    assert row_named(view).overlay == "BLOCKED"
    assert view.your_list == ()
    assert tray(store, clock).colour == "green"


def test_a_job_blocked_for_over_an_hour_joins_your_list_and_the_tray_goes_amber(store, clock):
    blocked_since(store, NOW - HOUR - MINUTE)
    item, = view_of(store, clock).your_list
    assert item.text == (f"{JOB}: Blocked: {REACH}. Fix: check that this PC can reach the NG Jobs"
                         " folder.")
    assert (item.job, item.age_days, item.card_id) == (JOB, 0, None)
    assert not check_line(item.text)
    assert (tray(store, clock).colour, tray(store, clock).count) == ("amber", 1)
    digest = jobmodel.digest_view(store, None, clock)
    assert (digest.open_items, digest.oldest_item) == (1, NOW - HOUR - MINUTE)


def test_a_refusal_in_words_not_fit_to_show_joins_your_list_in_fixed_words(store, clock):
    blocked_since(store, NOW - 2 * HOUR, r"Not written: C:\x\y refused by something else")
    item, = view_of(store, clock).your_list
    assert "NG Runner's safety check refused to file it" in item.text
    assert "C:" not in item.text and not check_line(item.text)


def test_blocked_jobs_take_their_place_on_your_list_by_how_long_they_have_waited(store, clock):
    a_card(store, sid=a_job(store), at=NOW - 2 * HOUR)
    blocked_since(store, NOW - 3 * HOUR, sid=a_job(store, OTHER, "102"))
    assert [item.job for item in view_of(store, clock).your_list] == [OTHER, JOB]
    assert tray(store, clock).count == 2


def test_a_pause_keeps_a_blocked_job_off_your_list_since_the_tray_is_grey(store, clock):
    blocked_since(store, NOW - 3 * HOUR)
    assert view_of(store, clock, paused=True).your_list == ()
    assert row_named(view_of(store, clock, paused=True)).overlay == "PAUSED"
    assert (tray(store, clock, paused=True).colour, tray(store, clock, paused=True).count) == (
        "grey", 0)
    set_state(store, "paused", "yes")
    assert jobmodel.digest_view(store, None, clock).open_items == 0
    set_state(store, "paused", "no")
    assert jobmodel.digest_view(store, None, clock).open_items == 1


def test_a_refusal_only_the_pause_caused_never_counts(store, clock):
    blocked_since(store, NOW - 5 * HOUR, "Not written: NG Runner is paused")
    assert row_named(view_of(store, clock)).overlay == "BLOCKED"
    assert view_of(store, clock).your_list == ()
    assert tray(store, clock).colour == "green"


def test_the_hour_starts_from_the_refusal_after_the_pause(store, clock):
    sid = a_job(store)
    log_event(store, at=NOW - 5 * HOUR, kind="blocked", studio_id=sid, text="NG Runner is paused")
    blocked_since(store, NOW - 10 * MINUTE, sid=sid)
    assert view_of(store, clock).your_list == ()
    clock.advance(HOUR)
    assert [item.job for item in view_of(store, clock).your_list] == [JOB]


def test_a_reason_that_changes_does_not_restart_the_hour(store, clock):
    sid = a_job(store)
    log_event(store, at=NOW - 2 * HOUR, kind="blocked", studio_id=sid, text=REACH)
    blocked_since(store, NOW - MINUTE, "Its folder has changed", sid=sid)
    item, = view_of(store, clock).your_list
    assert "Its folder has changed" in item.text
    assert jobmodel.digest_view(store, None, clock).oldest_item == NOW - 2 * HOUR


def test_a_filing_ends_the_refusals_before_it(store, clock):
    sid = a_job(store)
    log_event(store, at=NOW - 72 * HOUR, kind="blocked", studio_id=sid, text=REACH)
    log_event(store, at=NOW - 48 * HOUR, kind="filed", studio_id=sid, text="Filed.")
    blocked_since(store, NOW - 10 * MINUTE, sid=sid)
    assert row_named(view_of(store, clock)).overlay == "BLOCKED"
    assert view_of(store, clock).your_list == ()


def test_an_old_folder_and_an_observed_job_never_count(store, clock):
    blocked_since(store, NOW - 5 * HOUR,
                  "Its folder is in the Old folder of NG Jobs, so NG Runner wrote nothing")
    blocked_since(store, NOW - 5 * HOUR, sid=a_job(store, OTHER, "102", mode="observed"))
    assert view_of(store, clock).your_list == ()
    assert tray(store, clock).colour == "green"


def test_a_refusal_with_no_event_to_date_it_counts_at_once(store, clock):
    refused(store, REACH)
    item, = view_of(store, clock).your_list
    assert (item.job, item.age_days) == (JOB, 0)
    assert tray(store, clock).colour == "amber"


def test_the_pipelines_own_refusal_starts_the_hour_once(store, clock):
    from types import SimpleNamespace

    from ngrunner.intake import pipeline

    ctx = SimpleNamespace(store=store)
    op = add_pending(store, pipeline.FILE, due_at=NOW, studio_id=a_job(store))
    pipeline._refused(ctx, op, pipeline._NOT_REACHABLE, NOW)
    assert view_of(store, clock).your_list == ()
    clock.advance(HOUR + MINUTE)
    op, = list_pending(store, kind=pipeline.FILE)
    pipeline._refused(ctx, op, pipeline._NOT_REACHABLE, clock.now())     # tried again: no new event
    assert [item.job for item in view_of(store, clock).your_list] == [JOB]
    assert tray(store, clock).colour == "amber"


def test_a_job_no_longer_refused_leaves_your_list(store, clock):
    blocked_since(store, NOW - 2 * HOUR)
    op, = list_pending(store, kind="file")
    retry_pending(store, op.id, due_at=NOW, note=None)
    assert view_of(store, clock).your_list == ()


# ----- NOT_RUN, with a reason and a retry


def test_a_step_that_ended_badly_is_not_run_with_a_reason_and_a_retry(store, clock):
    sid = a_job(store)
    op = add_pending(store, "fetch_1", due_at=NOW, studio_id=sid)
    finish_pending(store, op.id, "failed", note="Studio sent a file too big to read")
    row = row_named(view_of(store, clock))
    assert row.overlay == "NOT_RUN"
    assert row.next_step == (f"Not run: Studio sent a file too big to read. To try again, type add"
                             f" job {JOB}")


def test_a_dev_item_with_nothing_left_to_do_is_not_run_and_keeps_its_text_to_itself(store, clock):
    open_dev(store, at=NOW, code="layouts_differ", text="Internal words", studio_id=a_job(store))
    row = row_named(view_of(store, clock))
    assert row.overlay == "NOT_RUN" and "Internal words" not in row.next_step


def test_a_dev_item_on_a_row_waiting_for_its_list_is_only_waiting(store, clock):
    sid = a_job(store)
    a_row(store, state="waiting_list", sid=sid)
    open_dev(store, at=NOW, code="a_problem", text="A problem", studio_id=sid)
    assert row_named(view_of(store, clock)).overlay == "WAITING"


def test_a_dev_item_with_work_still_open_is_only_waiting(store, clock):
    sid = a_job(store)
    open_dev(store, at=NOW, code="same_bytes", text="One render served twice", studio_id=sid)
    add_pending(store, "fetch_2", due_at=NOW + HOUR, studio_id=sid)
    assert row_named(view_of(store, clock)).overlay == "WAITING"


# ----- OBSERVED


def test_an_observed_job_says_why_it_gets_no_writes(store, clock):
    a_row(store, state="matched", sid=a_job(store, mode="observed"))
    row = row_named(view_of(store, clock))
    assert (row.overlay, row.next_step) == ("OBSERVED", jobmodel.OBSERVED_STEP)
    assert ticks(row)["filed"] == "todo"


def test_an_observed_job_shows_its_rows_own_reason(store, clock):
    a_row(store, state="matched", sid=a_job(store, mode="observed"),
          reason="Its folder is in NG Jobs Old, so NG Runner only watches it")
    row = row_named(view_of(store, clock))
    assert row.next_step == "Its folder is in NG Jobs Old, so NG Runner only watches it"


def test_an_observed_job_marked_filed_says_filed_and_stays_observed(store, clock):
    a_row(store, state="filed", sid=a_job(store, mode="observed"))
    row = row_named(view_of(store, clock))
    assert row.overlay == "OBSERVED" and row.next_step.startswith("Filed")


# ----- PAUSED


def test_paused_covers_what_is_not_filed_and_only_that(store, clock):
    waiting = a_job(store)
    add_pending(store, "fetch_2", due_at=NOW + HOUR, studio_id=waiting)
    filed = a_job(store, OTHER, "102")
    a_row(store, OTHER, state="filed", sid=filed)
    a_job(store, "Dummitt_101426", "103", mode="observed")
    a_row(store, "Mockford", state="waiting_list")
    view = view_of(store, clock, paused=True)
    assert view.paused is True
    assert row_named(view).overlay == "PAUSED"
    assert row_named(view).next_step == jobmodel.PAUSED_STEP
    assert row_named(view, "Mockford").overlay == "PAUSED"
    assert row_named(view, "Dummitt_101426").overlay == "OBSERVED"
    assert row_named(view, OTHER).overlay is None
    assert row_named(view, OTHER).next_step.startswith("Filed")


def test_a_refusal_left_from_a_pause_that_has_ended_says_it_clears_by_itself(store, clock):
    refused(store, "Not written: NG Runner is paused")
    row = row_named(view_of(store, clock))
    assert row.overlay == "BLOCKED" and "tries again at its next check" in row.next_step


def test_paused_keeps_a_card_as_the_next_step_since_jim_can_still_answer(store, clock):
    a_card(store, sid=a_job(store))
    row = row_named(view_of(store, clock, paused=True))
    assert (row.overlay, row.next_step) == ("PAUSED", "Card TES-1: closet or garage?")


def test_paused_outranks_a_refusal_that_may_be_only_the_pause(store, clock):
    refused(store, "Not written: NG Runner is paused")
    row = row_named(view_of(store, clock, paused=True))
    assert (row.overlay, row.next_step) == ("PAUSED", jobmodel.PAUSED_STEP)


def test_resuming_changes_only_what_the_pause_changed(store, clock):
    add_pending(store, "fetch_2", due_at=NOW + HOUR, studio_id=a_job(store))
    assert row_named(view_of(store, clock, paused=True)).overlay == "PAUSED"
    row = row_named(view_of(store, clock, paused=False))
    assert (row.overlay, row.next_step) == ("WAITING", "Second fetch at 4:05 PM")


# ----- SHADOW


def test_shadow_shows_on_the_board_and_starts_the_tooltip(store, clock):
    view = view_of(store, clock, shadow=True)
    assert view.shadow is True and view.tray.tooltip.startswith("SHADOW: ")
    assert tray(store, clock, shadow=True).tooltip.startswith("SHADOW: ")


@pytest.mark.parametrize("set_up", ["paused", "red", "amber", "green"])
def test_every_tray_state_says_shadow_exactly_in_shadow_mode(store, clock, set_up):
    if set_up == "red":
        open_dev(store, at=NOW, code="no_way", text="Stuck", urgent=True)
    if set_up == "amber":
        a_card(store, sid=a_job(store))
    paused = set_up == "paused"
    assert tray(store, clock, paused=paused, shadow=True).tooltip.startswith("SHADOW")
    assert not tray(store, clock, paused=paused, shadow=False).tooltip.startswith("SHADOW")
    assert isinstance(view_of(store, clock, paused=paused, shadow=True), BoardView)
    assert isinstance(view_of(store, clock, paused=paused, shadow=False), BoardView)


# ----- Your list


def test_your_list_holds_open_cards_oldest_first_with_their_age_and_link(store, clock):
    sid = a_job(store)
    a_card(store, sid=sid, at=NOW - timedelta(days=2))
    row = a_row(store, "Mockford", state="asked")
    a_card(store, "which_job", row=row.id, prefix="MOC", choices=(OTHER, "None of these"))
    items = view_of(store, clock).your_list
    assert [(i.text, i.job, i.age_days, i.card_id) for i in items] == [
        (f"{JOB}: closet or garage? Card TES-1.", JOB, 2, "TES-1"),
        ('Fab row "Mockford": which job is this row? Card MOC-1.', None, 0, "MOC-1"),
    ]


def test_answered_and_withdrawn_cards_leave_your_list(store, clock):
    sid = a_job(store)
    answered, withdrawn = a_card(store, sid=sid), a_card(store, sid=sid)
    answer_card(store, answered.id, answer="One", answer_words="One", answered_by="jim",
                answered_at=NOW, channel="board")
    withdraw_card(store, withdrawn.id)
    assert view_of(store, clock).your_list == ()


def test_a_48_hour_dev_item_about_a_job_joins_your_list_with_the_manual_fallback(store, clock):
    sid = a_job(store)
    open_dev(store, at=NOW - timedelta(hours=49), code="too_big", text="A big file", studio_id=sid)
    item, = view_of(store, clock).your_list
    assert item.job == JOB and item.age_days == 2 and item.card_id is None
    assert item.text == f"{JOB} has waited on NG Runner for over 2 days; work it in chat as before."
    assert "A big file" not in item.text


def test_a_dev_item_under_48_hours_stays_with_sulaiman(store, clock):
    sid = a_job(store)
    open_dev(store, at=NOW - timedelta(hours=48), code="too_big", text="A big file", studio_id=sid)
    assert view_of(store, clock).your_list == ()


def test_a_dev_item_about_no_job_stays_with_sulaiman_however_old(store, clock):
    open_dev(store, at=NOW - timedelta(days=9), code="backup_failed", text="No backup")
    assert view_of(store, clock).your_list == ()
    assert tray(store, clock).colour == "green"


def test_a_closed_dev_item_leaves_your_list(store, clock):
    sid = a_job(store)
    item = open_dev(store, at=NOW - timedelta(days=3), code="too_big", text="Big", studio_id=sid)
    close_dev(store, item.id)
    assert view_of(store, clock).your_list == ()


def test_two_old_dev_items_on_one_job_are_one_line_at_the_older_age(store, clock):
    sid = a_job(store)
    open_dev(store, at=NOW - timedelta(days=5), code="too_big", text="Big", studio_id=sid)
    open_dev(store, at=NOW - timedelta(days=3), code="layouts_differ", text="Odd", studio_id=sid)
    item, = view_of(store, clock).your_list
    assert item.age_days == 5


def test_cards_show_what_they_are_about_as_a_person_reads_it(store, clock):
    sid = a_job(store)
    a_card(store, sid=sid, choices=("Closet", "Garage"))
    row = a_row(store, "Mockford", state="asked")
    a_card(store, "which_job", row=row.id, prefix="MOC", at=NOW - timedelta(days=1),
           choices=(OTHER, "None of these"))
    cards = view_of(store, clock).cards
    assert [(c.id, c.job, c.choices, c.age_days) for c in cards] == [
        ("MOC-1", 'Fab row "Mockford"', (OTHER, "None of these"), 1),
        ("TES-1", JOB, ("Closet", "Garage"), 0),
    ]


def test_a_card_about_nothing_shows_nowhere_and_breaks_nothing(store, clock):
    add_card(store, prefix="TES", kind="which_job", text="Odd.", choices=("One",), created_at=NOW)
    view = view_of(store, clock)
    assert view.cards == () and view.your_list == ()


# ----- the tray


def test_the_tray_is_green_when_all_is_well(store, clock):
    state = tray(store, clock)
    assert (state.colour, state.count) == ("green", 0) and state.tooltip


def test_the_tray_is_amber_with_the_count_of_your_list(store, clock):
    sid = a_job(store)
    a_card(store, sid=sid)
    assert (tray(store, clock).colour, tray(store, clock).count) == ("amber", 1)
    assert "1 item is on Your list" in tray(store, clock).tooltip
    open_dev(store, at=NOW - timedelta(hours=49), code="too_big", text="Big",
             studio_id=a_job(store, OTHER, "102"))
    assert (tray(store, clock).colour, tray(store, clock).count) == ("amber", 2)
    assert "2 items are on Your list" in tray(store, clock).tooltip


def test_the_tray_is_red_when_the_supervisor_says_unwell(store, clock):
    assert tray(store, clock, unwell=True).colour == "red"


def test_the_tray_is_red_for_an_urgent_dev_item_until_it_closes(store, clock):
    item = open_dev(store, at=NOW, code="cant_send", text="No way out", urgent=True)
    state = tray(store, clock)
    assert state.colour == "red" and "No way out" not in state.tooltip
    close_dev(store, item.id)
    assert tray(store, clock).colour == "green"


def test_the_tray_is_not_red_for_a_dev_item_that_isnt_urgent(store, clock):
    open_dev(store, at=NOW, code="a_problem", text="A problem")
    assert tray(store, clock).colour == "green"


def sent_out(store, at, *, ref=None):
    made = add_outbox(store, id="a" * 32, seq=1, channel="email", recipient="jim", subject="Hello",
                      lines=["One."], urgent=False, created_at=at, ref=ref)
    mark_outbox_written(store, made.id, at=at)
    return made


def test_the_tray_turns_red_after_5_minutes_with_no_receipt_and_back_on_one(store, clock):
    made = sent_out(store, NOW)
    clock.advance(timedelta(minutes=4, seconds=59))
    assert tray(store, clock).colour == "green"
    clock.advance(1)
    assert tray(store, clock).colour == "red"
    assert view_of(store, clock).tray.colour == "red"     # the board's and the icon's agree
    assert templates.ALARMS["no_receipts"] in tray(store, clock).tooltip
    record_receipt(store, made.id, at=clock.now(), ok=True)
    assert tray(store, clock).colour == "green"


def test_red_outranks_amber_and_keeps_the_count(store, clock):
    a_card(store, sid=a_job(store))
    state = tray(store, clock, unwell=True)
    assert (state.colour, state.count) == ("red", 1)


def test_grey_outranks_all_when_paused(store, clock):
    a_card(store, sid=a_job(store))
    open_dev(store, at=NOW, code="cant_send", text="No way out", urgent=True)
    state = tray(store, clock, unwell=True, paused=True)
    assert (state.colour, state.count) == ("grey", 1)
    assert "paused" in state.tooltip


# ----- alarms


def test_alarms_come_from_the_store_in_order_and_join_what_the_supervisor_saw(store, clock):
    open_dev(store, at=NOW, code="toasts_off", text="Toasts are off")
    open_dev(store, at=NOW, code="not_an_alarm", text="Something else")
    health = HealthView(paths=(), last_export=None, list_synced_at=None, heartbeat=None,
                        toasts=False, alarms=("The export key is missing.",))
    view = jobmodel.board_view(store, None, clock, health=health, paused=True, shadow=False)
    assert view.health.alarms == (templates.ALARMS["toasts_off"], templates.ALARMS["paused"],
                                  "The export key is missing.")


def test_the_exports_own_time_decides_its_alarm_whatever_the_dev_lane_says(store, clock):
    words = templates.ALARMS["fab_export_stale"]
    open_dev(store, at=NOW, code="fab_export_stale", text="Old export")
    assert jobmodel.alarms(store, clock) == (words,)              # no time yet: the DEV item says
    set_state_time(store, "fab_exported_at", NOW - timedelta(minutes=30))
    assert jobmodel.alarms(store, clock) == ()
    clock.advance(1)
    assert jobmodel.alarms(store, clock) == (words,)


def test_the_toast_setting_decides_its_alarm(store, clock):
    words = templates.ALARMS["toasts_off"]
    for setting, expected in (("unknown", ()), ("on", ()), ("off", (words,))):
        set_state(store, "toast_setting", setting)
        assert jobmodel.alarms(store, clock) == expected


def test_the_job_list_alarm_needs_a_row_waiting_and_an_old_list(store, clock):
    def stale():
        return templates.ALARMS["job_list_stale"] in jobmodel.alarms(store, clock)

    replace_opportunities(store, [], synced_at=NOW - 2 * HOUR, fetched_at=NOW - 2 * HOUR)
    assert not stale()                                         # no row is waiting
    row = a_row(store, "Mockford", state="waiting_list", at=NOW - HOUR)
    assert stale()
    replace_opportunities(store, [], synced_at=NOW - HOUR / 2, fetched_at=NOW)
    assert not stale()                                         # fresher than the row
    update_fab_row(store, row.id, now=NOW, text="Mockford Dummitt")
    assert not stale()                                         # older than the row, but not by 1 h
    clock.advance(HOUR)
    assert stale()


def test_the_job_list_alarm_with_no_list_at_all(store, clock):
    a_row(store, "Mockford", state="waiting_list", at=NOW - HOUR)
    assert not jobmodel.alarms(store, clock)
    clock.advance(1)
    assert jobmodel.alarms(store, clock) == (templates.ALARMS["job_list_stale"],)


def test_alarms_read_paused_from_the_store_unless_told(store, clock):
    set_state(store, "paused", "no")
    assert jobmodel.alarms(store, clock) == ()
    set_state(store, "paused", "yes")
    assert jobmodel.alarms(store, clock) == (templates.ALARMS["paused"],)
    assert jobmodel.alarms(store, clock, paused=False) == ()


# ----- the digest


@pytest.fixture
def morning():
    return FakeClock(MORNING)


def digest_of(store, clock):
    message = compose(jobmodel.digest_view(store, None, clock))
    assert not lint(message), message.text
    return message


def test_a_quiet_night_gets_one_line(store, morning):
    message = digest_of(store, morning)
    assert message.kind == "quiet_digest"
    assert message.lines == ("Nothing new since the last list, and nothing is waiting on you.",)


def test_a_quiet_night_with_filed_jobs_and_refetches_stays_quiet(store, morning):
    sid = a_job(store, at=MORNING - timedelta(days=3))
    a_row(store, state="filed", sid=sid, at=MORNING - timedelta(days=3))
    log_event(store, at=MORNING - timedelta(days=3), kind="filed", text="Filed.", lane="ACT",
              studio_id=sid)
    a_digest(store, MORNING - timedelta(days=1))
    log_event(store, at=MORNING - HOUR, kind="refetch", text="Read again, the same.", studio_id=sid)
    add_pending(store, "refetch", due_at=MORNING + HOUR, studio_id=sid)
    assert digest_of(store, morning).kind == "quiet_digest"


def test_a_quiet_night_with_an_old_card_says_your_list_in_its_one_line(store, morning):
    card = a_card(store, sid=a_job(store), at=MORNING - timedelta(days=2))
    record_reminder(store, card.id, at=MORNING - timedelta(days=1))
    a_digest(store, MORNING - timedelta(days=1))
    message = digest_of(store, morning)
    assert message.kind == "quiet_digest"
    assert message.lines == (
        "Nothing new since the last list. Your list: 1 open, oldest 2 days.",)


def test_a_job_filed_since_the_last_digest_gets_one_line(store, morning):
    a_digest(store, MORNING - timedelta(days=1))
    sid = a_job(store)
    at = MORNING - 2 * HOUR
    a_row(store, state="filed", sid=sid)
    log_event(store, at=at, kind="filed", text="Filed.", lane="ACT", studio_id=sid)
    log_event(store, at=at, kind="told", text="Told.", lane="ACT", studio_id=sid)
    view = jobmodel.digest_view(store, None, morning)
    assert [(line.job, line.text) for line in view.jobs] == [(JOB, "filed 10/30 4:30 AM")]
    assert digest_of(store, morning).lines == (f"1. {JOB}: filed 10/30 4:30 AM.",
                                               "Your list: nothing open.")


def test_a_blocked_job_is_a_touch_in_the_log_lane_too(store, morning):
    a_digest(store, MORNING - timedelta(days=1))
    sid = refused(store, "NG Jobs can't be reached right now, so nothing was written")
    log_event(store, at=MORNING - HOUR, kind="blocked", text="Refused.", studio_id=sid)
    message = digest_of(store, morning)
    assert message.lines[0].startswith(f"1. {JOB}: blocked: NG Jobs can't be reached")


def test_filing_counts_as_a_touch_in_the_log_lane_too(store, morning):
    sid = a_job(store)
    log_event(store, at=MORNING - HOUR, kind="filed", text="Filed.", studio_id=sid)
    assert [line.job for line in jobmodel.digest_view(store, None, morning).jobs] == [JOB]


def test_only_events_since_the_last_digest_count(store, morning):
    sid = a_job(store)
    log_event(store, at=MORNING - timedelta(days=2), kind="filed", text="Filed.", lane="ACT",
              studio_id=sid)
    a_digest(store, MORNING - timedelta(days=1))
    assert jobmodel.digest_view(store, None, morning).jobs == ()


def test_a_digest_that_failed_to_send_doesnt_count_as_the_last_one(store, morning):
    sid = a_job(store)
    log_event(store, at=MORNING - timedelta(hours=30), kind="filed", text="Filed.", lane="ACT",
              studio_id=sid)
    a_digest(store, MORNING - timedelta(days=1), state="failed")
    assert [line.job for line in jobmodel.digest_view(store, None, morning).jobs] == [JOB]


@pytest.mark.parametrize("lane, kind", [("DEV", "filed"), ("DEV", "too_big"), ("LOG", "refetch")])
def test_what_jim_isnt_told_doesnt_touch_a_job(store, morning, lane, kind):
    log_event(store, at=MORNING - HOUR, kind=kind, text="Quiet.", lane=lane,
              studio_id=a_job(store))
    assert jobmodel.digest_view(store, None, morning).jobs == ()


def test_a_card_opened_since_the_last_digest_touches_its_job(store, morning):
    a_digest(store, MORNING - timedelta(days=1))
    a_card(store, sid=a_job(store, OTHER, "102"), prefix="FAK", at=MORNING - HOUR)
    assert digest_of(store, morning).lines == (
        f"1. {OTHER}: closet or garage? Card FAK-1.", "Your list: 1 open, oldest under a day.")


def test_a_which_job_card_opened_since_the_last_digest_gets_a_line_of_its_own(store, morning):
    a_digest(store, MORNING - timedelta(days=1))
    row = a_row(store, "Mockford", state="asked", at=MORNING - HOUR)
    a_card(store, "which_job", row=row.id, prefix="MOC", at=MORNING - HOUR,
           choices=(OTHER, "None of these"))
    assert digest_of(store, morning).lines == (
        '1. Fab row "Mockford": which job is this row? Card MOC-1.',
        "Your list: 1 open, oldest under a day.")


def test_cards_come_back_at_1_3_and_7_days_with_their_age(store, morning):
    a_digest(store, MORNING - timedelta(days=1))
    row = a_row(store, "Mockford", state="asked", at=MORNING - timedelta(days=3))
    a_card(store, "which_job", row=row.id, prefix="MOC", at=MORNING - timedelta(days=3),
           choices=(OTHER, "None of these"))
    back_yesterday = a_card(store, sid=a_job(store), at=MORNING - timedelta(days=2))
    record_reminder(store, back_yesterday.id, at=MORNING - timedelta(days=1))
    message = digest_of(store, morning)
    assert message.lines == (
        '1. Card MOC-1, open 3 days: which job is Fab row "Mockford"?',
        "Your list: 2 open, oldest 3 days.",
    )
    view = jobmodel.digest_view(store, None, morning)
    assert [card.id for card in reminders(view)] == ["MOC-1"]


def test_a_row_card_due_back_is_not_also_a_new_line(store, morning):
    row = a_row(store, "Mockford", state="asked", at=MORNING - timedelta(days=1))
    a_card(store, "which_job", row=row.id, prefix="MOC", at=MORNING - timedelta(days=1),
           choices=(OTHER, "None of these"))
    assert digest_of(store, morning).lines[0] == (
        '1. Card MOC-1, open 1 day: which job is Fab row "Mockford"?')


def test_owner_items_first_jobs_installing_within_5_days_with_nothing_filed(store, morning):
    a_job(store, "Mockford_110226", "1", install=("", "11/2/26"))
    a_job(store, "Dummitt_110426", "2", install=("", "11/4/26"))         # 5 days away
    a_job(store, "Testerson_110526", "3", install=("", "11/5/26"))       # 6 days away
    a_job(store, "Fakeley_102926", "4", install=("", "10/29/26"))        # passed
    done = a_job(store, "Fakeley_110226", "5", install=("", "11/2/26"))
    a_row(store, state="filed", sid=done, install=("", "11/2/26"))
    a_job(store, "Testerson_110226", "6", install=("Friday", "week of 11/2"))     # can't be read
    a_row(store, "Mockford", state="asked", install=("", "10/30/26"))
    a_row(store, "Dummitt", state="seen_at_install", install=("", "10/30/26"))
    a_row(store, "Fakeley", state="none", install=("", "10/30/26"))      # Jim said none of these
    owner = jobmodel.digest_view(store, None, morning).owner
    assert owner == (
        'Fab row "Mockford" installs Fri 10/30 and has no IC summary filed yet',
        "Mockford_110226 installs Mon 11/2 and has no IC summary filed yet",
        "Dummitt_110426 installs Wed 11/4 and has no IC summary filed yet",
    )
    assert digest_of(store, morning).lines[0].startswith('For you: Fab row "Mockford" installs')


def test_the_digest_lists_alarms_after_installs(store, morning):
    a_job(store, "Mockford_110226", "1", install=("", "11/2/26"))
    open_dev(store, at=MORNING, code="fab_export_stale", text="Old export")
    set_state(store, "paused", "yes")
    owner = jobmodel.digest_view(store, None, morning).owner
    assert owner[0].startswith("Mockford_110226 installs")
    assert owner[1:] == (templates.ALARMS["fab_export_stale"], templates.ALARMS["paused"])
    digest_of(store, morning)


def test_a_48_hour_dev_item_is_told_once_with_the_manual_fallback(store, morning):
    """The night's work marks it at 1:30 AM; the next digest tells Jim; later ones don't."""
    sid = a_job(store)
    item = open_dev(store, at=MORNING - timedelta(hours=49), code="too_big", text="Big",
                    studio_id=sid)
    a_digest(store, MORNING - timedelta(days=1))
    mark_dev_told_jim(store, item.id, at=MORNING - 5 * HOUR)
    log_event(store, at=MORNING - 5 * HOUR, kind="dev_told_jim", text="Told.", lane="ACT",
              studio_id=sid)
    view = jobmodel.digest_view(store, None, morning)
    assert view.owner == (f"{JOB} has waited on NG Runner for over 2 days; work it in chat as"
                          " before",)
    assert view.jobs == ()                     # For you says it already
    assert (view.open_items, view.oldest_item) == (1, item.at)
    assert "work it in chat as before" in digest_of(store, morning).text
    a_digest(store, MORNING)
    morning.advance(timedelta(days=1))
    later = jobmodel.digest_view(store, None, morning)
    assert later.owner == ()                   # told once
    assert later.open_items == 1               # still on Your list


def test_a_dev_item_not_marked_yet_waits_for_the_next_digest_on_your_list(store, morning):
    open_dev(store, at=MORNING - timedelta(hours=49), code="too_big", text="Big",
             studio_id=a_job(store))
    view = jobmodel.digest_view(store, None, morning)
    assert view.owner == () and view.open_items == 1


def test_a_dev_item_about_no_job_never_reaches_jim(store, morning):
    item = open_dev(store, at=MORNING - timedelta(days=4), code="too_big", text="Big")
    mark_dev_told_jim(store, item.id, at=MORNING - HOUR)
    assert jobmodel.digest_view(store, None, morning).owner == ()
    assert digest_of(store, morning).kind == "quiet_digest"


def test_a_digest_over_every_situation_passes_the_lint(store, morning):
    paused_at = MORNING - HOUR
    a_digest(store, MORNING - timedelta(days=1))
    names = ("Testerson_101426", "Fakeley_102726", "Mockford_110226", "Dummitt_103026",
             "Testerson_110226", "Fakeley_110326", "Mockford_110426")
    ids = [a_job(store, name, str(n), install=("", "11/3/26")) for n, name in enumerate(names, 1)]
    for sid in ids:
        log_event(store, at=paused_at, kind="moved", text="Moved.", lane="ACT", studio_id=sid)
    a_row(store, names[0], state="filed", sid=ids[0])
    add_pending(store, "fetch_2", due_at=MORNING + HOUR, studio_id=ids[1])
    add_pending(store, "file", due_at=MORNING, studio_id=ids[2],
                note=r"G:\NG Jobs\Mockford_110226 is not reachable right now")
    op = add_pending(store, "fetch_1", due_at=MORNING, studio_id=ids[3])
    finish_pending(store, op.id, "failed", note="Studio sent a file too big to read")
    update_job(store, ids[4], now=MORNING, mode="observed")
    a_card(store, "ic_changed", sid=ids[5], prefix="FAK")
    a_row(store, names[6], state="waiting_list", sid=ids[6])
    message = digest_of(store, morning)
    assert message.kind == "digest"
    assert any("Second fetch" in line or "second fetch" in line for line in message.lines)
    assert not any("G:" in line for line in message.lines)


def test_one_odd_job_name_costs_its_own_line_and_not_the_digest(store, morning):
    sid = a_job(store, "Testerson", "1")
    other = a_job(store, OTHER, "2")
    for job in (sid, other):
        log_event(store, at=MORNING - HOUR, kind="filed", text="Filed.", lane="ACT", studio_id=job)
    message = digest_of(store, morning)
    assert message.kind == "digest"
    assert message.lines[0].startswith("1. NG Runner: a job changed")
    assert message.lines[1] == f"2. {OTHER}: filed 10/30 5:30 AM."


def test_a_reason_that_fails_the_lint_never_reaches_the_digest(store, morning):
    sid = a_job(store)
    op = add_pending(store, "fetch_1", due_at=MORNING, studio_id=sid)
    finish_pending(store, op.id, "failed", note="See https://example.com/a for it")
    log_event(store, at=MORNING - HOUR, kind="stopped", text="Stopped.", lane="ACT", studio_id=sid)
    assert "example.com" not in digest_of(store, morning).text


def test_the_digest_view_says_your_list_too(store, morning):
    a_card(store, sid=a_job(store), at=MORNING - timedelta(days=4))
    view = jobmodel.digest_view(store, None, morning)
    assert view.open_items == 1 and view.oldest_item == MORNING - timedelta(days=4)
    assert view.cards[0].job == JOB


# ----- every line on the board passes the lint


def test_every_next_step_passes_the_lint(store, clock):
    situations = [
        ("list_wait", None), ("fetch_1", None), ("fetch_2", None), ("file", None),
        ("file", "NG Jobs is not reachable right now"), ("other_thing", None),
    ]
    for n, (kind, note) in enumerate(situations, 1):
        add_pending(store, kind, due_at=NOW + HOUR, note=note,
                    studio_id=a_job(store, f"Testerson_1014{n:02d}", str(n)))
    a_row(store, "Mockford", state="new")
    a_row(store, "Dummitt", state="waiting_list")
    a_row(store, "Fakeley", state="none")
    a_row(store, "Testerson", state="asked")
    a_job(store, "Dummitt_101426", "20", mode="observed")
    a_job(store, "Fakeley_101426", "21")
    for paused in (False, True):
        for row in view_of(store, clock, paused=paused).jobs:
            assert not check_line(row.next_step), row.next_step


def test_the_install_words_and_tick_labels_pass_the_lint(store, clock):
    a_job(store)
    assert not check_line(jobmodel.COULD_NOT_VERIFY)
    for tick in row_named(view_of(store, clock)).ticks:
        assert not check_line(tick.label), tick.label


def test_every_tooltip_and_your_list_line_passes_the_lint(store, clock):
    a_card(store, sid=a_job(store))
    row = a_row(store, "Mockford", state="asked")
    a_card(store, "which_job", row=row.id, prefix="MOC", choices=(OTHER, "None of these"))
    open_dev(store, at=NOW - timedelta(days=3), code="too_big", text="Big",
             studio_id=a_job(store, OTHER, "102"))
    for item in view_of(store, clock).your_list:
        assert not check_line(item.text), item.text
    assert not check_line(tray(store, clock).tooltip)                       # amber
    assert not check_line(tray(store, clock, paused=True).tooltip)          # grey
    assert not check_line(tray(store, clock, unwell=True).tooltip)          # red, unwell
    open_dev(store, at=NOW, code="no_way", text="No way out", urgent=True)
    assert not check_line(tray(store, clock).tooltip)                       # red, urgent
    sent_out(store, NOW - HOUR)
    assert not check_line(tray(store, clock).tooltip)                       # red, no receipts


# ----- history


def test_history_is_newest_first_with_the_jobs_name_and_no_dev_lane(store):
    sid = a_job(store)
    log_event(store, at=NOW - HOUR, kind="fetched", text="Fetched.", studio_id=sid)
    log_event(store, at=NOW, kind="filed", text="Filed.", lane="ACT", studio_id=sid)
    log_event(store, at=NOW, kind="note", text="A note about no job.")
    log_event(store, at=NOW, kind="dev", text="For Sulaiman.", lane="DEV", studio_id=sid)
    assert [(e.job, e.text, e.lane) for e in jobmodel.history(store)] == [
        (None, "A note about no job.", "LOG"),
        (JOB, "Filed.", "ACT"),
        (JOB, "Fetched.", "LOG"),
    ]


def test_history_keeps_to_its_limit(store):
    for n in range(5):
        log_event(store, at=NOW + timedelta(minutes=n), kind="note", text=f"Note {n}.")
    assert [e.text for e in jobmodel.history(store, limit=2)] == ["Note 4.", "Note 3."]
    assert jobmodel.history(store, limit=0) == []
    assert len(jobmodel.history(store)) == 5


# ----- a store with nothing in it


def test_an_empty_store_gives_an_empty_board_and_settings_arent_needed(store, clock):
    view = view_of(store, clock)
    assert view.jobs == () and view.your_list == () and view.cards == ()
    assert view.health.alarms == () and view.tray.colour == "green"
    assert jobmodel.history(store) == []


# ----- the second round: what the board and the tray say


def test_a_design_mismatch_blocks_the_summary_tick_even_once_two_fetches_agree(store, clock):
    sid = a_job(store)
    add_render(store, studio_id=sid, fetched_at=NOW, check_ok=True, sha256="a" * 64,
               fingerprint="same")
    add_render(store, studio_id=sid, fetched_at=NOW, check_ok=True, sha256="b" * 64,
               fingerprint="same")
    a_card(store, "design_mismatch", sid=sid)
    assert ticks(row_named(view_of(store, clock)))["summary"] == "blocked"


def test_a_job_filed_again_shows_the_new_time(store, clock):
    sid = a_job(store)
    log_event(store, at=NOW - 3 * HOUR, kind="filed", text="Filed.", studio_id=sid)
    log_event(store, at=NOW - HOUR, kind="refiled", text="Filed the new one.", lane="ACT",
              studio_id=sid)
    assert row_named(view_of(store, clock)).next_step == "Filed 10/30 2:05 PM"


@pytest.mark.parametrize("code", ["fab_export_stale", "job_list_stale"])
def test_a_standing_alarm_turns_the_tray_amber_in_its_own_words(store, clock, code):
    if code == "fab_export_stale":
        set_state_time(store, "fab_exported_at", NOW - 3 * HOUR)
    else:
        a_row(store, state="waiting_list", at=NOW - 3 * HOUR)
    state = tray(store, clock)
    assert state.colour == "amber" and state.count == 0
    assert templates.ALARMS[code] in state.tooltip and "nothing is waiting" not in state.tooltip


def test_a_standing_alarm_goes_beside_the_count_and_a_pause_still_wins(store, clock):
    set_state_time(store, "fab_exported_at", NOW - 3 * HOUR)
    a_card(store, sid=a_job(store))
    state = tray(store, clock)
    assert state.colour == "amber" and state.count == 1
    assert "1 item" in state.tooltip and templates.ALARMS["fab_export_stale"] in state.tooltip
    assert tray(store, clock, paused=True).colour == "grey"


def test_ng_jobs_out_of_reach_counts_on_your_list_after_an_hour(store, clock):
    alarm = templates.ALARMS["ng_jobs_unreachable"]
    item = open_dev(store, at=NOW - HOUR / 2, code="ng_jobs_unreachable", text="Can't be read.")
    row = a_row(store, "Testerson")
    assert alarm in jobmodel.alarms(store, clock)
    assert tray(store, clock).colour == "green" and view_of(store, clock).your_list == ()
    assert "NG Jobs can't be read" in row_named(view_of(store, clock), row.text).next_step
    clock.advance(HOUR)
    state, view = tray(store, clock), view_of(store, clock)
    assert state.colour == "amber" and state.count == 1
    assert view.your_list[0].text == f"{alarm}."
    close_dev(store, item.id)
    assert tray(store, clock).colour == "green"
    assert "NG Jobs" not in row_named(view_of(store, clock), row.text).next_step


def _filed_then_trial_only(store, install=None):
    sid = a_job(store, install=install)
    log_event(store, at=NOW - 3 * HOUR, kind="filed", text="Filed aside.", studio_id=sid)
    log_event(store, at=NOW - HOUR, kind="trial_only", text="Trial run only.", lane="ACT",
              studio_id=sid)
    return sid


def test_a_job_filed_only_in_the_trial_run_says_so_until_it_is_filed_for_real(store, clock):
    sid = _filed_then_trial_only(store)
    add_pending(store, "fetch_1", due_at=NOW, studio_id=sid)
    row = row_named(view_of(store, clock))
    assert row.next_step == "Trial run only, not in NG Jobs. First fetch now"
    assert ticks(row)["filed"] == "todo" and ticks(row)["summary"] == "todo"
    line = jobmodel.digest_view(store, None, clock).jobs[0]
    assert line.job == JOB and line.text.startswith("trial run only, not in NG Jobs")
    finish_pending(store, list_pending(store)[0].id, "done")
    log_event(store, at=NOW, kind="filed", text="Filed.", studio_id=sid)
    row = row_named(view_of(store, clock))
    assert row.next_step.startswith("Filed 10/30") and ticks(row)["filed"] == "done"


def test_a_job_left_aside_says_how_to_file_it(store, clock):
    _filed_then_trial_only(store)
    row = row_named(view_of(store, clock))
    assert row.overlay == "NOT_RUN" and row.next_step.startswith("Trial run only, not in NG Jobs.")
    assert f"add job {JOB}" in row.next_step


def test_a_job_filed_only_in_the_trial_run_still_warns_of_its_install(store, clock):
    _filed_then_trial_only(store, install=("", "11/2/26"))
    owner = jobmodel.digest_view(store, None, clock).owner
    assert templates.install_soon(JOB, date(2026, 11, 2)) in owner


def test_a_dev_item_on_a_filed_job_does_not_say_it_waits(store, clock):
    sid = a_job(store)
    open_dev(store, at=NOW - 49 * HOUR, code="layouts_differ", text="They differ.", studio_id=sid)
    log_event(store, at=NOW - 60 * HOUR, kind="filed", text="Filed.", studio_id=sid)
    assert view_of(store, clock).your_list == () and tray(store, clock).colour == "green"


def test_a_job_filed_long_ago_leaves_the_board_unless_something_is_open(store, clock):
    gone = a_job(store, "Dummitt_081426", "1", at=NOW - 60 * 24 * HOUR)
    log_event(store, at=NOW - 45 * 24 * HOUR, kind="filed", text="Filed.", studio_id=gone)
    asking = a_job(store, "Mockford_081426", "2", at=NOW - 60 * 24 * HOUR)
    log_event(store, at=NOW - 45 * 24 * HOUR, kind="filed", text="Filed.", studio_id=asking)
    a_card(store, "ic_changed", sid=asking, prefix="MOC")
    recent = a_job(store, "Fakeley_101426", "3")
    log_event(store, at=NOW - 2 * 24 * HOUR, kind="filed", text="Filed.", studio_id=recent)
    moving = a_job(store, "Testerson_101426", "4", at=NOW - 60 * 24 * HOUR)
    a_row(store, "Testerson", state="matched", sid=moving, at=NOW - 60 * 24 * HOUR)
    names = {row.name for row in view_of(store, clock).jobs}
    assert names == {"Mockford_081426", "Fakeley_101426", "Testerson_101426"}


def test_the_board_reads_filed_facts_in_one_query_however_many_jobs(store, clock, monkeypatch):
    from ngrunner.store import db

    def fill(start, count):
        for n in range(start, start + count):
            sid = a_job(store, f"Testerson_{n}", str(n))
            set_state(store, f"job.filed.{sid}", json.dumps({"designs": 3, "pages": 19}))
            log_event(store, at=NOW, kind="filed", text="Filed.", studio_id=sid)

    def connections():
        made = []
        real = db._connect
        monkeypatch.setattr(db, "_connect", lambda path: made.append(path) or real(path))
        view = view_of(store, clock)
        monkeypatch.setattr(db, "_connect", real)
        return len(made), view

    fill(1, 3)
    few, _ = connections()
    fill(100, 30)
    many, view = connections()
    assert few == many
    assert "3 designs, 19 pages" in view.jobs[0].next_step
