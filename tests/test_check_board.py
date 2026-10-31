"""
A job's check on the board, Your list, the tray and the digest (P3), through the real job model over a
real store, and the board's page and buttons through Flask's test client. The checks' results are made
by the real findings from synthetic checker output (test_checks.CLEAN, made-up names); nothing runs here.

What it holds: status is always an icon, a label and a colour; a stale check never counts anywhere; a
settled item leaves Your list; an unanswered close prompt is on Your list, turns the tray amber and is in
the digest; every line passes Jim's format; no client detail is on the page.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_board import BASE, PORT, TOKEN, FakeActions, FakeViews, _post
from test_checks import CLEAN, read
from ngrunner import jobmodel
from ngrunner.board import create_board, set_port
from ngrunner.board.views import BoardView, HealthView
from ngrunner.checks import findings as f
from ngrunner.checks import jobchecks as jc
from ngrunner.clock import FakeClock
from ngrunner.config import Settings
from ngrunner.digest import compose
from ngrunner.messages import templates
from ngrunner.messages.lint import check_line, lint
from ngrunner.store import (
    add_prompt, add_settle, ask_check, ensure_job, finish_check, log_event, mark_stale, open_store,
    start_check, update_job,
)

Z = timezone.utc
NOW = datetime(2026, 10, 31, 4, 42, tzinfo=Z)          # Fri 10/30 9:42 PM Pacific
SID, JOB = "4242", "Testerson_101426"
NOTCH = ("Room1.des\n   !! C3   Testerson Special        notch on face True, line bores on False - NOTCH ON THE"
         " WRONG FACE")
ROOMS = {"Room0.des": "a" * 64, "Room1.des": "b" * 64}
IDENTITIES = {"Room0.des": "c" * 64, "Room1.des": "d" * 64}


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


def run(store, *, fault: bool = True, at: datetime = NOW, kind: str = "check", trial: bool = True):
    sections = {**CLEAN, "notch_check": NOTCH} if fault else CLEAN
    found = f.findings_of(read(sections, flagged=("notch_check",) if fault else ()), kind=kind)
    asked = ask_check(store, studio_id=SID, folder=JOB, kind=kind, trigger="check_now" if kind == "check" else "audit",
                      at=at - timedelta(minutes=1))
    start_check(store, asked.id, at=at - timedelta(minutes=1))
    finish_check(store, asked.id, at=at, code=found.code, words="x", snapshot_sha256="1" * 64,
                 conversion_sha256="2" * 64, rooms=ROOMS, identities=IDENTITIES, stats={}, result=f.to_json(found),
                 features=(), trial=trial)
    log_event(store, at=at, kind="checked", lane="ACT", studio_id=SID, text="Checked.")
    return asked.id


def board(store, settings, now=NOW):
    health = HealthView(paths=(), last_export=None, list_synced_at=None, heartbeat=None, toasts=None)
    return jobmodel.board_view(store, settings, FakeClock(now), health=health, paused=False, shadow=False)


def row_of(view: BoardView):
    return next(job for job in view.jobs if job.name == JOB)


# ----- the job's row


def test_a_failed_check_shows_on_its_row_with_each_item_its_chat_line_and_a_settle(store, settings) -> None:
    run_id = run(store)

    check = row_of(board(store, settings)).check

    assert check.state == "failed" and check.studio_id == SID and check.run_id == run_id
    assert check.line == "Checked 9:42 PM (trial): 1 to fix, 1 could not verify"
    fix, cnv = check.items
    assert (fix.n, fix.kind, fix.can_settle) == (1, "fix", True)
    assert fix.text == "R1C3: the notch is on the wrong face"
    assert fix.chat == f"{JOB}: put the notch on its line-bore face on R1C3, then run crosscheck"
    assert (cnv.kind, cnv.can_settle, cnv.chat) == ("could_not_verify", False, None)


def test_every_job_has_check_now_and_one_never_checked_says_so(store, settings) -> None:
    check = row_of(board(store, settings)).check

    assert check.state == "none" and check.studio_id == SID and check.items == ()


def test_a_stale_check_never_counts_on_the_row_your_list_or_the_tray(store, settings) -> None:
    run_id = run(store)
    mark_stale(store, run_id, at=NOW + timedelta(minutes=5))

    view = board(store, settings, NOW + timedelta(minutes=6))

    assert row_of(view).check.state == "stale"
    assert "check it again" in row_of(view).check.line
    assert all(not item.can_settle for item in row_of(view).check.items)
    assert view.your_list == () and view.tray.colour == "green"


def test_something_to_fix_is_on_your_list_and_turns_the_tray_amber(store, settings) -> None:
    run(store)

    view = board(store, settings)

    item, = view.your_list
    assert item.text == f"{JOB}: 1 to fix from its check at 9:42 PM (trial); see the board."
    assert item.job == JOB and view.tray.colour == "amber"


def test_a_settled_item_leaves_your_list_and_shows_settled_on_the_row(store, settings) -> None:
    run_id = run(store)
    fix = next(i for i in f.from_json(jc.get_check_run(store, run_id).result).items if i.kind == "fix")
    add_settle(store, studio_id=SID, run_id=run_id, fingerprint=fix.fingerprint, identities=IDENTITIES,
               conversion_sha256="2" * 64, how="told_chat", words="I already told chat", channel="board", at=NOW)

    view = board(store, settings)

    assert view.your_list == ()
    shown = row_of(view).check.items[0]
    assert shown.settled == "Settled 9:42 PM: I already told chat" and not shown.can_settle


def test_an_unanswered_close_prompt_is_on_your_list_turns_the_tray_amber_and_answers_on_the_row(
        store, settings) -> None:
    prompt = add_prompt(store, studio_id=SID, folder=JOB, closed_at=NOW - timedelta(minutes=2), first=True)

    view = board(store, settings)

    item, = view.your_list
    assert item.text == f"{JOB}: closed 9:40 PM, not checked. Sections done?"
    assert view.tray.colour == "amber"
    asked = row_of(view).check.prompt
    assert asked.id == prompt.id and asked.choices == templates.CHECK_CLOSE_FIRST


def test_an_audit_reads_as_the_build_checked(store, settings) -> None:
    run(store, kind="audit", fault=False)

    assert row_of(board(store, settings)).check.line.startswith("Build checked 9:42 PM (trial)")


# ----- the digest


def test_the_digest_says_what_a_check_found_and_names_a_waiting_close(store, settings) -> None:
    run(store)
    add_prompt(store, studio_id=SID, folder=JOB, closed_at=NOW + timedelta(minutes=10), first=False)

    digest = jobmodel.digest_view(store, settings, FakeClock(NOW + timedelta(hours=9)))
    message = compose(digest)

    text = "\n".join(message.lines)
    assert f"{JOB}: closed" in text and "Sections done?" in text
    line, = [line for line in message.lines if line.startswith("1. ")]
    assert "checked 9:42 PM (trial): 1 to fix and 1 could not verify" in line
    assert lint(message) == []


def test_a_stale_check_says_nothing_in_the_digest(store, settings) -> None:
    run_id = run(store)
    mark_stale(store, run_id, at=NOW + timedelta(minutes=5))

    message = compose(jobmodel.digest_view(store, settings, FakeClock(NOW + timedelta(hours=9))))

    assert "to fix" not in "\n".join(message.lines)


# ----- the page and its buttons


@pytest.fixture
def actions() -> "CheckActions":
    return CheckActions()


class CheckActions(FakeActions):
    def check_job(self, studio_id: str) -> str:
        return self._record("check_job", studio_id)

    def settle_item(self, studio_id: str, run_id: str, n: str, how: str) -> str:
        return self._record("settle_item", studio_id, run_id, n, how)

    def answer_prompt(self, prompt_id: str, choice: str) -> str:
        return self._record("answer_prompt", prompt_id, choice)


def browser_for(view: BoardView, actions: CheckActions):
    app = create_board(FakeViews(view), actions, token=TOKEN, clock=FakeClock(NOW))
    set_port(app, PORT)
    client = app.test_client()
    client.get(f"/?t={TOKEN}", base_url=BASE)
    return client


def test_the_page_shows_the_check_with_icon_label_and_colour_and_every_button(store, settings, actions) -> None:
    run_id = run(store)
    add_prompt(store, studio_id=SID, folder=JOB, closed_at=NOW, first=False)

    page = html.unescape(browser_for(board(store, settings), actions).get("/", base_url=BASE).get_data(as_text=True))

    assert 'tag tag--bad' in page and "To fix" in page and "#i-x" in page
    assert "Check now" in page and 'action="/checks/check"' in page
    assert "R1C3: the notch is on the wrong face" in page
    assert f'In chat: "{JOB}: put the notch on its line-bore face on R1C3, then run crosscheck"' in page
    assert templates.SETTLE_LABELS["not_a_problem"] in page and templates.SETTLE_LABELS["told_chat"] in page
    assert f'name="run_id" value="{run_id}"' in page
    assert "Yes, check it" in page and 'action="/checks/prompt"' in page
    assert "Testerson Special" not in page                   # what a checker printed never shows


def test_the_buttons_post_to_the_actions(store, settings, actions) -> None:
    client = browser_for(board(store, settings), actions)

    _post(client, "/checks/check", {"studio_id": SID})
    _post(client, "/checks/settle", {"studio_id": SID, "run_id": "3", "n": "1", "how": "told_chat"})
    _post(client, "/checks/prompt", {"prompt_id": "7", "choice": "Yes, check it"})
    _post(client, "/checks/settle", {"studio_id": SID, "run_id": "3", "n": "1", "how": "forever"})

    assert actions.calls == [("check_job", SID), ("settle_item", SID, "3", "1", "told_chat"),
                             ("answer_prompt", "7", "Yes, check it")]


def test_every_check_line_on_the_board_passes_jims_format(store, settings) -> None:
    run(store)
    add_prompt(store, studio_id=SID, folder=JOB, closed_at=NOW, first=True)

    view = board(store, settings)
    check = row_of(view).check

    strings = [check.line, *(i.text for i in check.items), *(i.chat for i in check.items if i.chat),
               *(item.text for item in view.your_list)]
    assert all(check_line(text) == [] for text in strings), [t for t in strings if check_line(t)]
