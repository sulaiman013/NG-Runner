"""
A job's P4 panel on the board (lock/view.py, through the real job model over a real store), and the page and
its buttons through Flask's test client.

What it holds: every status is an icon, a label and a colour; each lock button shows exactly what is missing;
"lock without check" is a confirmation of its own, offered only where the check is all that is missing;
[Re-send only the missing row] shows only where the after-drop step said it may be offered; a batch in doubt
is on Your list and the digest's For you line; every line passes Jim's format. Job names are made up.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_board import BASE, PORT, TOKEN, FakeActions, FakeViews, _post
from ngrunner import jobmodel
from ngrunner.board import create_board, set_port
from ngrunner.board.views import (
    BatchView, BoardView, FixView, HealthView, JobRow, JobSheetView, LeaseView, LockButtonView, LockPanelView,
    TrayState,
)
from ngrunner.clock import FakeClock
from ngrunner.config import Settings
from ngrunner.lock import kept
from ngrunner.messages import templates
from ngrunner.messages.lint import check_line
from ngrunner.store import ensure_job, open_store, update_job
from ngrunner.store.locks import add_batch, update_batch
from ngrunner.tracker_inbox import HEADER

Z = timezone.utc
NOW = datetime(2026, 10, 31, 4, 42, tzinfo=Z)          # Fri 10/30 9:42 PM Pacific
SID, JOB = "4242", "Testerson_101426"
NO_CHECK = "No check of this job's design has run, so it can't be locked yet"
NO_NEST = "No nest of this job has been read"


def panel() -> LockPanelView:
    return LockPanelView(
        buttons=(LockButtonView(SID, "lock_design", None, "Lock design", "waivable", (NO_CHECK,)),
                 LockButtonView(SID, "finish_lock", "3-4 Egger Classic White", "Finish lock 3/4 Egger Classic White",
                                "ready"),
                 LockButtonView(SID, "lock", None, "Lock", "blocked", (NO_CHECK, NO_NEST))),
        lease=LeaseView("active", templates.lease_words("active", at=NOW, until=NOW + timedelta(hours=2), now=NOW)),
        batches=(BatchView("20261031T044200Z-abc123", "Lock design", "doubt",
                           "The tracker doesn't show the batch as written: 1 row missing", can_resend=True),
                 BatchView("20261031T050000Z-def456", "Finish lock", "confirmed",
                           "Confirmed by two snapshots 10 minutes apart")),
        fixes=(FixView(7, "applied", "Room 1: the hang notch is cut from the wrong face on 1 panel (R1C3)",
                       can_undo=True),),
        sheet=JobSheetView("mismatch", templates.job_sheet_words("mismatch"),
                           ('3/4" Egger Classic White: the job sheet says 8 sheets, the nest has 9',)),
        note=templates.P4_SHADOW_NOTE)


def view_with(p4: LockPanelView) -> BoardView:
    health = HealthView(paths=(), last_export=None, list_synced_at=None, heartbeat=None, toasts=None)
    row = JobRow(studio_id=SID, name=JOB, install=None, ticks=(), next_step="Filed", p4=p4)
    return BoardView(jobs=(row,), your_list=(), cards=(), health=health, tray=TrayState("green", 0, "ok"),
                     paused=False, shadow=False)


class LockActions(FakeActions):
    def lock_click(self, studio_id: str, click: str, material: str, without_check: bool) -> str:
        return self._record("lock_click", studio_id, click, material, without_check)

    def resend_row(self, batch_id: str) -> str:
        return self._record("resend_row", batch_id)

    def undo_fix(self, fix_id: str) -> str:
        return self._record("undo_fix", fix_id)


def browser_for(view: BoardView, actions: LockActions):
    app = create_board(FakeViews(view), actions, token=TOKEN, clock=FakeClock(NOW))
    set_port(app, PORT)
    client = app.test_client()
    client.get(f"/?t={TOKEN}", base_url=BASE)
    return client


def test_the_page_shows_the_panel_with_icon_label_and_colour_and_exactly_what_each_lock_misses() -> None:
    page = html.unescape(browser_for(view_with(panel()), LockActions()).get("/", base_url=BASE).get_data(as_text=True))

    assert templates.P4_SHADOW_NOTE in page
    assert 'tag tag--ok' in page and "Ready" in page and "#i-lock" in page
    assert 'tag tag--warn' in page and "Needs its check" in page
    assert 'tag tag--bad' in page and "Not yet" in page and "In doubt" in page
    assert page.count(NO_CHECK) == 2 and NO_NEST in page
    assert "Finish lock 3/4 Egger Classic White" in page and 'action="/lock/click"' in page
    assert page.count('name="without_check" value="yes"') == 1          # only where the check is all it misses
    assert templates.LOCK_WITHOUT_CHECK in page
    assert page.count(templates.RESEND) == 1 and 'action="/lock/resend"' in page
    assert 'name="batch" value="20261031T044200Z-abc123"' in page
    assert "Undo" in page and 'action="/lock/undo"' in page and 'name="fix" value="7"' in page
    assert "Lease held" in page and "Confirmed" in page and "Job sheet differs" in page
    assert "the job sheet says 8 sheets, the nest has 9" in page


def test_no_resend_and_no_undo_where_they_cant_be_offered() -> None:
    plain = LockPanelView(batches=(BatchView("b1", "Lock design", "doubt", "1 row not as written"),),
                          fixes=(FixView(3, "undone", "A fix"),), note=templates.P4_SHADOW_NOTE)
    page = browser_for(view_with(plain), LockActions()).get("/", base_url=BASE).get_data(as_text=True)
    assert html.unescape(page).count(templates.RESEND) == 0 and 'action="/lock/undo"' not in page


def test_the_lock_buttons_post_to_the_actions_and_nothing_else_is_a_lock() -> None:
    actions = LockActions()
    client = browser_for(view_with(panel()), actions)

    _post(client, "/lock/click", {"studio_id": SID, "click": "lock_design", "material": ""})
    _post(client, "/lock/click", {"studio_id": SID, "click": "lock_design", "material": "", "without_check": "yes"})
    _post(client, "/lock/click", {"studio_id": SID, "click": "unlock", "material": ""})
    _post(client, "/lock/resend", {"batch": "b1"})
    _post(client, "/lock/undo", {"fix": "7"})

    assert actions.calls == [("lock_click", SID, "lock_design", "", False), ("lock_click", SID, "lock_design", "", True),
                             ("resend_row", "b1"), ("undo_fix", "7")]


# ----- through the real job model


@pytest.fixture
def store(tmp_path: Path):
    made = open_store(tmp_path / "ngrunner.db")
    ensure_job(made, SID, JOB, now=NOW - timedelta(hours=2))
    update_job(made, SID, folder=JOB, branch="closet", now=NOW - timedelta(hours=2))
    return made


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(ng_jobs=tmp_path / "NG Jobs", mozaik_shared=tmp_path / "Mozaik Shared",
                    control=tmp_path / "control")


def doubt(store) -> str:
    rows = [list(HEADER), ["EDGEBAND", JOB, "", 'Egger Classic White 1mm — 15/16" / 1"', "26.0", "", "Egger Classic White"]]
    batch = add_batch(store, id="20261031T044200Z-abc123", folder=JOB, kind="lock_design", verb="append", material=None,
                      rows=rows, file="tracker_inbox_20261031T044200Z-abc123.csv", dropped_at=NOW - timedelta(hours=1))
    update_batch(store, batch.id, state="doubt", words="The tracker doesn't show the batch as written: 1 row missing")
    kept.keep_after(store, batch.id, {"state": "doubt", "can_resend": True, "words": "The tracker doesn't show the batch"
                                      " as written: 1 row missing", "missing": ["x"], "differs": [], "taken": None,
                                      "archived": True, "told": "doubt"})
    return batch.id


def test_a_batch_in_doubt_is_on_the_row_your_list_and_the_digests_for_you_line(store, settings) -> None:
    batch_id = doubt(store)
    health = HealthView(paths=(), last_export=None, list_synced_at=None, heartbeat=None, toasts=None)

    view = jobmodel.board_view(store, settings, FakeClock(NOW), health=health, paused=False, shadow=False)
    row = next(job for job in view.jobs if job.name == JOB)

    batch, = row.p4.batches
    assert (batch.id, batch.state, batch.can_resend) == (batch_id, "doubt", True)
    assert any(f"{JOB}: the Lock design batch is in doubt" in item.text for item in view.your_list)
    assert view.tray.colour == "amber"
    owner = jobmodel.digest_view(store, settings, FakeClock(NOW)).owner
    assert any("batch is in doubt" in line for line in owner)


def test_every_lock_line_on_the_board_passes_jims_format(store, settings) -> None:
    doubt(store)
    health = HealthView(paths=(), last_export=None, list_synced_at=None, heartbeat=None, toasts=None)
    view = jobmodel.board_view(store, settings, FakeClock(NOW), health=health, paused=False, shadow=False)
    p4 = next(job for job in view.jobs if job.name == JOB).p4

    strings = [p4.note, *(w for b in p4.buttons for w in (b.label, *b.missing)), *(b.words for b in p4.batches),
               *(item.text for item in view.your_list)]
    assert all(check_line(text) == [] for text in strings), [t for t in strings if check_line(t)]


# ----- a shadow batch closed against the chat's own rows, and P4's "5 locks match the chat" on Health


def test_a_shadow_batchs_closing_shows_as_icon_label_and_colour() -> None:
    from ngrunner.board import display

    closed = LockPanelView(batches=(
        BatchView("b1", "Lock design", "matched", "Matches the chat's own rows on the tracker"),
        BatchView("b2", "Lock", "differing", "Differs from the chat's own rows: Egger Classic White: Calc Sheets"
                                             " 8.5 on the chat's row, 8.2 on NG Runner's"),
        BatchView("b3", "Finish lock", "not_compared", "Not compared: the chat's own rows for this job didn't come"
                                                       " within 7 days")), note=templates.P4_SHADOW_NOTE)
    page = html.unescape(browser_for(view_with(closed), LockActions()).get("/", base_url=BASE).get_data(as_text=True))

    for state in ("matched", "differing", "not_compared"):
        style = display.BATCHES[state]
        assert style.tone and style.icon and style.label in page, state
    assert (display.BATCHES["matched"].tone, display.BATCHES["differing"].tone,
            display.BATCHES["not_compared"].tone) == ("ok", "warn", "muted")
    assert "Calc Sheets 8.5 on the chat's row" in page and page.count(templates.RESEND) == 0


def test_health_shows_how_many_of_five_shadow_locks_match_the_chat(store, settings) -> None:
    from ngrunner.lock import shadow

    for n, (folder, state) in enumerate(((JOB, "matched"), ("Sampleton_101526", "differing"))):
        made = add_batch(store, id=f"b{n}", folder=folder, kind="lock_design", verb="append", material=None,
                         rows=[list(HEADER), ["JOBSUMMARY", folder, "", "🔲 In progress", ""]],
                         file=f"tracker_inbox_b{n}.csv", dropped_at=NOW, source="ng_runner")
        update_batch(store, made.id, state=state, closed_at=NOW)

    said = jobmodel.shadow_locks(store)

    assert said == "1 of 5 jobs' shadow locks match the chat; 1 differs" == shadow.tally_words(shadow.Tally(1, 1))
    health = HealthView(paths=(), last_export=None, list_synced_at=None, heartbeat=None, toasts=None,
                        shadow_locks=said)
    page = html.unescape(browser_for(BoardView(jobs=(), your_list=(), cards=(), health=health,
                                               tray=TrayState("green", 0, "ok"), paused=False, shadow=False),
                                     LockActions()).get("/health", base_url=BASE).get_data(as_text=True))
    assert "Shadow locks against the chat" in page and said in page and check_line(said) == []


# ----- the heartbeat's open risks: fixes approved and not yet verified


def test_the_heartbeats_open_risks_list_each_fix_approved_and_not_yet_verified(store, settings) -> None:
    """Approved and not written, written and not judged by the next save, or judged could not verify. A fix
    that held, one Jim changed, one Mozaik undid (a card of its own) and one undone are no open risk. P4 is
    in shadow, so the lines say so, and Jim's digest doesn't carry them."""
    from ngrunner.store.locks import add_fix, mark_fix_undone, record_survival

    plan = {"room": "Room2.des", "words": ["Room 2: the hang notch is cut from the wrong face on 1 panel (R2C3)"]}
    kept.add_pending(store, kind="apply", at=NOW, folder=JOB, plan=plan)
    kept.add_pending(store, kind="undo", at=NOW, folder=JOB, fix=99)

    def fix(room: str):
        return add_fix(store, folder=JOB, room=room, fixer="notch_face", fingerprint="f" * 64, base_sha256="a" * 64,
                       result_sha256="b" * 64, patch=[{"offset": 1, "old": "00", "new": "01"}],
                       elements=[{"cab": "3", "id": "2"}], programs={}, backups=[], applied_at=NOW)

    fix("Room1.des")
    for room, code in (("Room3.des", "held"), ("Room4.des", "could_not_verify"), ("Room6.des", "you_changed"),
                       ("Room7.des", "mozaik_undid")):
        record_survival(store, fix(room).id, code=code, words="said", at=NOW)
    mark_fix_undone(store, fix("Room5.des").id, at=NOW)

    risks = jobmodel.open_risks(store, settings, FakeClock(NOW))

    assert [line for line in risks if "fix" in line] == [
        f"{JOB}: a fix approved for Room 2 isn't written yet, so it isn't verified (shadow); see the board",
        f"{JOB}: the fix written on Room 1 isn't verified until the room is saved again (shadow); see the board",
        f"{JOB}: the fix on Room 4 could not be verified after the save (shadow); see the board"]
    assert all(check_line(line) == [] for line in risks)
    assert not [line for line in jobmodel.digest_view(store, settings, FakeClock(NOW)).owner if "fix" in line]
