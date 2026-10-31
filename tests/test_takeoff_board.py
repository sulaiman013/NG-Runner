"""
NG Runner's takeoff on the board and in the digest: on a filed job's line, its sheets by colour and
thickness and its tape, labelled "takeoff estimate; the nest decides"; a "could not verify N lines"
note when lines couldn't be placed; and every state as icon, label and colour, as the board's other
states are. The page is rendered through Flask's test client with tests/test_board.py's fakes; the
job model is read from tests/test_pipeline.py's world. Made-up names only.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from markupsafe import escape

from test_board import (  # noqa: F401 - actions, app, browser and views are its fixtures
    BASE, _health, _ticks, _view, actions, app, browser, views,
)
from test_pipeline import CLOSET_PARTS, GARAGE, TESTERSON, World
from ngrunner import jobmodel
from ngrunner.board.views import JobRow, TakeoffView
from ngrunner.guards.kept import TAKEOFFS
from ngrunner.messages import templates
from ngrunner.store import set_state
from ngrunner.messages.lint import check_line

LINES = ("2 sheets 3/4 Egger Classic White", "26 feet of tape for 3/4 Egger Classic White")
ESTIMATE = "takeoff estimate; the nest decides"
UNPLACED_PART = ("CK", 2, "CK DBack 3.6x22.2x0.6 NN WH")


def _with_takeoff(takeoff: TakeoffView, notes: tuple[str, ...] = ()) -> Any:
    return _view(jobs=(JobRow("101", "Testerson_101426", None,
                              _ticks("done", "done", "done", "done", "done"), "Filed",
                              notes=notes, takeoff=takeoff),))


# ----- the page


@pytest.mark.parametrize("state, tone, icon, label", [
    ("made", "info", "file", "NG Runner's takeoff"),
    ("chat", "info", "file", "Chat's takeoff"),
    ("unverified", "warn", "alert", "Could not verify"),
    ("in_chat", "muted", "minus", "Takeoff in chat"),
    ("failed", "muted", "minus", "No takeoff"),
])
def test_each_takeoff_state_has_its_icon_label_and_colour(browser, views, state, tone, icon,
                                                          label):
    views.view = _with_takeoff(TakeoffView(state))
    page = browser.get("/", base_url=BASE).get_data(as_text=True)
    shown = re.escape(str(escape(label)))                     # as the page escapes it: &#39;
    found = re.search(rf'<div class="takeoff">\s*<span class="tag tag--{tone}">'
                      rf'<svg[^>]*><use href="#i-{icon}"></use></svg><span>{shown}</span>', page)
    assert found, page


def test_a_complete_takeoff_shows_its_sheets_and_tape_labelled_as_an_estimate(browser, views):
    views.view = _with_takeoff(TakeoffView("made", LINES, ESTIMATE))
    page = browser.get("/", base_url=BASE).get_data(as_text=True)
    assert all(line in page for line in LINES)
    assert ESTIMATE in page


def test_a_takeoff_that_could_not_verify_shows_its_note_and_no_list(browser, views):
    views.view = _with_takeoff(TakeoffView("unverified"), notes=("could not verify 3 lines",))
    page = browser.get("/", base_url=BASE).get_data(as_text=True)
    assert "could not verify 3 lines" in page and "takeoff-lines" not in page


def test_a_job_with_no_takeoff_shows_none(browser, views):
    views.view = _view()
    assert 'class="takeoff"' not in browser.get("/", base_url=BASE).get_data(as_text=True)


def test_a_takeoff_in_a_state_the_board_doesnt_have_is_refused_where_it_is_made():
    with pytest.raises(ValueError):
        TakeoffView("partial")


# ----- the job model


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def filed(world: World, row: str = "Testerson", **studio: Any) -> World:
    world.installed(TESTERSON, **studio)
    world.add_row(row)
    world.run_until(lambda: world.row(row).state == "filed", 45)
    return world


def row_of(world: World, name: str = "Testerson_101426") -> JobRow:
    view = jobmodel.board_view(world.store, world.settings, world.clock, health=_health(),
                               paused=False, shadow=False)
    return next(row for row in view.jobs if row.name == name)


def test_a_filed_jobs_line_carries_its_complete_takeoff(world):
    filed(world)

    row = row_of(world)
    assert row.takeoff == TakeoffView("made", LINES, ESTIMATE)
    assert not any("could not verify" in note for note in row.notes)
    assert all(not check_line(line) for line in row.takeoff.lines)


def test_lines_that_couldnt_be_placed_are_a_could_not_verify_note_and_no_list(world):
    filed(world, parts=(*CLOSET_PARTS, UNPLACED_PART))

    row = row_of(world)
    assert row.takeoff == TakeoffView("unverified")
    assert "could not verify 1 line" in row.notes


def test_a_garage_says_its_takeoff_stays_in_chat(world):
    filed(world, "Testerson_101426", designs=GARAGE)

    row = row_of(world)
    assert row.takeoff is not None and row.takeoff.state == "in_chat"
    assert row.takeoff.lines == (
        "Its takeoff stays in chat: a garage job, until BUILD's garage rule is ported.",)


def test_a_takeoff_that_failed_says_the_chats_is_used(world):
    filed(world, parts=None)

    row = row_of(world)
    assert row.takeoff is not None and row.takeoff.state == "failed"
    assert "chat's takeoff is used" in row.takeoff.lines[0]


def test_a_job_still_on_its_way_has_no_takeoff_on_its_line(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "matched", 30)

    assert row_of(world).takeoff is None


def _digest(world: World) -> str:
    view = jobmodel.digest_view(world.store, world.settings, world.clock)
    return "\n".join(f"{line.job}: {line.text}" for line in view.jobs)


def test_the_digest_line_of_a_filed_job_says_its_takeoff(world):
    filed(world)

    assert ("takeoff: 2 sheets 3/4 Egger Classic White and 26 feet of tape (takeoff estimate;"
            " the nest decides)") in _digest(world)


def test_the_digest_line_says_how_many_lines_could_not_be_verified(world):
    filed(world, parts=(*CLOSET_PARTS, UNPLACED_PART))

    text = _digest(world)
    assert "takeoff: could not verify 1 line" in text and "sheets" not in text


def test_a_digest_line_too_long_with_its_takeoff_keeps_the_filing_and_drops_the_takeoff(
        world, monkeypatch):
    from ngrunner.messages import templates

    filed(world)
    monkeypatch.setattr(templates, "takeoff_digest",
                        lambda *a, **k: "takeoff: see C:\\a\\path")      # the lint stops a path
    text = _digest(world)
    assert "Testerson_101426: filed" in text and "path" not in text


# ----- the chat's list as the heads-up of a takeoff that isn't complete (ruling, 2026-10-31)


def test_when_the_chats_list_is_the_heads_up_the_line_and_the_digest_say_so(world):
    import json

    filed(world, parts=(*CLOSET_PARTS, UNPLACED_PART))
    set_state(world.store, TAKEOFFS, json.dumps(
        {"Testerson_101426": {"read": [1, "x"], "bad": False, "source": "chat"}}))

    row = row_of(world)
    assert row.takeoff == TakeoffView("chat", (templates.CHATS_TAKEOFF,))
    assert "could not verify 1 line" in row.notes
    assert ("takeoff: could not verify 1 line, so the order heads-up is the chat's takeoff"
            in _digest(world))
