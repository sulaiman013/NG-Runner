"""
The takeoff on intake (P3's "takeoff on intake"; docs/p2/build-spec.md, sections 5.1 to 5.4): every
IC summary NG Runner files gets its takeoff, made by the shared converter from the filed bytes, off
the tick's thread, and kept in the store. A converter that fails costs only the takeoff, never the
filing. Garage and mixed jobs stay in chat (ruling R4). It runs in tests/test_pipeline.py's world:
the real store, commit_write and summary check, with a fake Studio whose summaries print a PULLED
list as Studio's do (helpers_pdf). Job names, addresses and emails are made up.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from helpers_pdf import summary_pdf
from test_pipeline import (
    CLOSET_PARTS, GARAGE, MIXED, TESTERSON, World, in_folder, pdf_of,
)
from ngrunner import jobmodel
from ngrunner.convert import convert, rules, takeoff_branch
from ngrunner.convert.summary_lines import SummaryUnreadable
from ngrunner.intake import pipeline
from ngrunner.intake.pipeline import BackgroundFetches
from ngrunner.intake.summary_check import check_summary
from ngrunner.messages.lint import check_line
from ngrunner.store import get_state, list_events, list_outbox, list_takeoffs

FOLDER = "Testerson_101426"
UNPLACED_PART = ("CK", 2, "CK DBack 3.6x22.2x0.6 NN WH")      # 0.6 thick: the rules don't place it
MORE_SHELVES = (("VT-WH", 2, "VT 14x84x3/4 FD WH"), ("SH-WH", 10, "SH 14x24x3/4 FIX WH"),
                ("400000155", 8, "Shelf Pin"))
# A client's details, where a summary could put them: a row's own text can quote anything.
ADDRESS, EMAIL, PHONE = "12 Fakeview Lane", "tester@example.com", "(555) 010-0000"
LEAKY_PARTS = (("VT-WH", 2, f"VT 14x84x3/4 FD WH {ADDRESS} {EMAIL} {PHONE}"),
               ("SH-WH", 6, f"SH 14x24x3/4 FIX WH {ADDRESS}"),
               ("CK", 2, f"CK DBack 3.6x22.2x0.6 NN WH {ADDRESS} {EMAIL}"),
               ("SC-WH", 1, f"SC 3x96x3/4 {ADDRESS} Testville CA"))
NEVER = ("Fakeview", "example.com", "555", "010-0000", "Testville")


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


@pytest.fixture
def shadow(tmp_path: Path) -> World:
    return World(tmp_path, mode="shadow")


def filed(world: World, row: str = "Testerson", **studio: Any) -> World:
    world.installed(TESTERSON, **studio)
    world.add_row(row)
    world.run_until(lambda: world.row(row).state == "filed", 45)
    return world


def takeoffs(world: World) -> list[Any]:
    return list_takeoffs(world.store, "4242")


def sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ----- the made-up summary the fakes serve, through the converter


@pytest.mark.parametrize("layout", ["landscape", "portrait"])
def test_the_made_up_summary_with_a_pulled_list_reads_for_the_check_and_the_converter(layout):
    data = summary_pdf(layout=layout, parts=(*CLOSET_PARTS, UNPLACED_PART))

    check = check_summary(data)
    t = convert(data, branch=takeoff_branch(check))

    assert check.ok and takeoff_branch(check) == "closet"
    assert (t.board_parts, len(t.unplaced), t.complete) == (8, 1, False)
    assert [(b.colour, b.thk_in, b.parts, b.sheets_with_waste, b.tape_lf) for b in t.buckets] == [
        ("WH", 0.75, 8, 2, 26.0)]


def test_the_made_up_summary_without_a_pulled_list_is_one_the_converter_cant_read():
    """What the made-up summary held before it could print a list as Studio does: the summary
    check passes it and the converter refuses it, as it would a real render in a shape it hasn't
    met. The pipeline then files and says the takeoff failed (below)."""
    data = summary_pdf()

    assert check_summary(data).ok
    with pytest.raises(SummaryUnreadable):
        convert(data, branch="closet")


# ----- a closet: its takeoff, from the bytes it filed


def test_a_filed_closet_gets_its_takeoff_from_the_bytes_it_filed(world):
    filed(world)

    record, = takeoffs(world)
    summary = world.folder(FOLDER) / f"{FOLDER}_summary.pdf"
    assert record.render_sha256 == sha_of(summary) == pipeline.filed_facts(
        world.store, "4242")["sha256"]
    assert (record.state, record.branch, record.complete, record.could_not_verify) == (
        "made", "closet", True, 0)
    assert record.rules_sha256 == rules.fingerprint()
    bucket, = record.result["buckets"]
    assert (bucket["colour"], bucket["thk_in"], bucket["parts"], bucket["sheets_with_waste"],
            bucket["tape_lf"]) == ("WH", 0.75, 8, 2, 26.0)
    assert (record.result["board_parts"], record.result["unplaced"]) == (8, [])
    assert world.events("takeoff")[-1].text.startswith("Made its takeoff from the IC summary: 8")
    assert world.ops(pipeline.TAKEOFF) == [] and world.dev() == []


def test_a_takeoff_is_made_once_whatever_comes_after(world):
    filed(world)
    world.run(30)
    world.night()
    world.night()

    assert len(takeoffs(world)) == 1
    assert len(world.events("takeoff")) == 1


def test_the_converter_runs_off_the_ticks_thread_and_the_next_tick_takes_its_result(
        world, monkeypatch):
    gate, ran_on = threading.Event(), []
    real = pipeline.convert

    def slow(data: bytes, *, branch: str) -> Any:
        ran_on.append(threading.current_thread())
        assert gate.wait(10)
        return real(data, branch=branch)

    monkeypatch.setattr(pipeline, "convert", slow)
    world.ctx.converts = BackgroundFetches(1, name="ngrunner-takeoff")
    filed(world)                                       # the tick that filed came straight back

    assert takeoffs(world) == [] and ran_on
    assert ran_on[0] is not threading.current_thread()
    assert ran_on[0].name.startswith("ngrunner-takeoff-")
    gate.set()
    ran_on[0].join(10)
    world.run(1)
    assert [r.state for r in takeoffs(world)] == ["made"]


# ----- what can't be made never stops filing


def test_a_converter_that_crashes_never_stops_filing(world, monkeypatch):
    def crash(data: bytes, *, branch: str) -> Any:
        raise RuntimeError("a converter bug, with C:\\a\\path in its words")

    monkeypatch.setattr(pipeline, "convert", crash)
    filed(world)
    world.run(2)

    assert {f"{FOLDER}/{FOLDER}_summary.pdf", f"{FOLDER}/{FOLDER}_NGRunner.json"} <= set(
        world.tree())
    assert world.sent("barbara", f"Filed as {FOLDER}")
    record, = takeoffs(world)
    assert (record.state, record.result, record.complete) == ("failed", None, False)
    item, = world.dev("takeoff_failed")
    assert item.text == (f"{FOLDER}: the takeoff could not be made (the converter stopped with"
                         " an error, which is in NG Runner's log).")
    assert check_line(item.text) == []     # it reaches Sulaiman in its words, not as a bare code
    assert item.studio_id is None          # Sulaiman's alone: the night never tells Jim it is stuck
    assert world.dev("pipeline_step") == []


def test_a_summary_the_converter_cant_read_is_still_filed_and_its_takeoff_failed(world):
    filed(world, parts=None)

    assert world.row("Testerson").state == "filed"
    assert [r.state for r in takeoffs(world)] == ["failed"]
    assert [d.code for d in world.dev()] == ["takeoff_failed"]
    assert "(its parts list could not be read)" in world.dev()[0].text
    assert "chat's takeoff is used" in world.events("takeoff")[-1].text


def test_a_takeoff_that_crashes_while_it_is_taken_stops_no_filing_and_is_tried_again(
        world, monkeypatch):
    real, broken = pipeline.add_takeoff, [True]

    def flaky(*args: Any, **kwargs: Any) -> Any:
        if broken[0]:
            raise RuntimeError("the store broke")
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline, "add_takeoff", flaky)
    filed(world)
    assert takeoffs(world) == [] and world.dev("pipeline_step")
    broken[0] = False
    world.run(10)
    assert [r.state for r in takeoffs(world)] == ["made"]


# ----- garage and mixed jobs stay in chat (ruling R4)


@pytest.mark.parametrize("designs, branch", [(GARAGE, "garage"), (MIXED, "mixed")])
def test_a_garage_or_mixed_job_stays_in_chat_with_no_takeoff(world, designs, branch):
    filed(world, "Testerson_101426", designs=designs)

    record, = takeoffs(world)
    assert (record.state, record.branch, record.result, record.complete) == (
        "in_chat", branch, None, False)
    assert "stays in chat" in world.events("takeoff")[-1].text
    assert world.dev() == []


def test_a_closet_jim_answered_for_an_unclear_summary_still_stays_in_chat(world):
    world.installed(TESTERSON, designs=(("Testerson - Thing", ""),))
    world.add_row(FOLDER)
    world.run_until(lambda: world.cards("closet_or_garage"), 45)
    world.answer(world.cards("closet_or_garage")[0], "Closet")
    world.run_until(lambda: world.row(FOLDER).state == "filed", 30)

    record, = takeoffs(world)
    assert (record.state, record.branch) == ("in_chat", "unclear")


# ----- what the takeoff says


def test_lines_the_rules_dont_place_make_it_incomplete_and_are_counted(world):
    filed(world, parts=(*CLOSET_PARTS, UNPLACED_PART))

    record, = takeoffs(world)
    assert (record.state, record.complete, record.could_not_verify) == ("made", False, 1)
    assert record.result["unplaced"] == [{"page": 4, "qty": 2}]       # where, how many: no text
    assert "could not verify 1 line" in world.events("takeoff")[-1].text


def test_a_changed_summary_gets_a_new_takeoff(world):
    filed(world)
    world.studio.jobs["4242"].parts = MORE_SHELVES
    world.night()
    card, = world.cards("ic_changed")
    world.answer(card, "File the new one")
    world.run(2)

    first, second = takeoffs(world)
    summary = world.folder(FOLDER) / f"{FOLDER}_summary.pdf"
    assert second.render_sha256 == sha_of(summary) != first.render_sha256
    assert (first.result["board_parts"], second.result["board_parts"]) == (8, 12)
    assert world.notes(FOLDER)["takeoff"]["board_parts"] == 12


def test_a_summary_already_in_its_folder_filed_with_no_write_gets_a_takeoff_of_that_file(world):
    world.installed(TESTERSON)
    path = in_folder(world, data=pdf_of(note=5))
    world.add_row("Testerson_101426")
    world.run_until(lambda: world.row("Testerson_101426").state == "filed", 45)

    record, = takeoffs(world)
    assert record.render_sha256 == sha_of(path) and record.state == "made"


def test_an_observed_job_gets_no_takeoff(world):
    world.folder(FOLDER).mkdir()
    world.installed(TESTERSON)
    world.add_row(FOLDER)
    world.run_until(lambda: len(world.renders("4242")) >= 2, 45)
    world.run(2)
    in_folder(world)
    world.run(70, step=5)

    assert world.row(FOLDER).state == "filed" and world.job("4242").mode == "observed"
    assert takeoffs(world) == [] and world.ops(pipeline.TAKEOFF, state=None) == []


# ----- the notes file: schema 3 brought the takeoff for Jim's chat (schema 4 adds the check)


def test_the_notes_carry_the_takeoff_for_jims_chat(world):
    filed(world)

    notes = world.notes(FOLDER)
    takeoff = notes["takeoff"]
    assert notes["schema"] == 4
    assert (takeoff["state"], takeoff["complete"], takeoff["could_not_verify"]) == (
        "made", True, 0)
    assert takeoff["basis"] == "takeoff estimate; the nest decides"
    assert takeoff["summary_sha256"] == notes["summary"]["sha256"]
    assert takeoff["rules"] == rules.fingerprint()
    assert takeoff["buckets"][0]["sheets_with_waste"] == 2
    assert takeoff["board_parts"] == 8 and takeoff["tape_lf"] == 26.0


def test_the_notes_of_a_garage_say_its_takeoff_stays_in_chat(world):
    filed(world, "Testerson_101426", designs=GARAGE)

    takeoff = world.notes(f"{FOLDER}_Gar")["takeoff"]
    assert (takeoff["state"], takeoff["branch"], takeoff["complete"]) == (
        "in_chat", "garage", False)
    assert "buckets" not in takeoff


def test_the_notes_are_rewritten_once_for_the_takeoff_and_not_again(world):
    filed(world)
    before = world.tree()
    world.run(30)

    assert world.tree() == before


def test_shadow_files_aside_and_makes_the_takeoff_too(shadow):
    filed(shadow)

    record, = takeoffs(shadow)
    aside = shadow.control / "shadow"
    assert record.render_sha256 == sha_of(aside / FOLDER / f"{FOLDER}_summary.pdf")
    assert shadow.notes(FOLDER, aside)["takeoff"]["state"] == "made"
    assert shadow.tree() == {}                              # nothing in NG Jobs


def test_going_live_makes_the_takeoff_of_the_summary_it_files_for_real(shadow):
    filed(shadow)
    shadow.ctx.mode = "live"
    pipeline.go_live(shadow.store, shadow.clock.now())
    shadow.run_until(lambda: pipeline.filed_facts(shadow.store, "4242") is not None
                     and pipeline.filed_facts(shadow.store, "4242")["where"] == "ng_jobs", 45)
    shadow.run(1)

    aside, live = takeoffs(shadow)
    assert live.render_sha256 == sha_of(shadow.folder(FOLDER) / f"{FOLDER}_summary.pdf")
    assert live.render_sha256 != aside.render_sha256
    assert shadow.notes(FOLDER)["takeoff"]["state"] == "made"


# ----- no client detail, anywhere the takeoff goes


def test_no_client_detail_reaches_the_store_the_notes_the_events_or_the_board(world):
    filed(world, parts=LEAKY_PARTS)
    world.run(2)

    record, = takeoffs(world)
    assert record.state == "made" and not record.complete
    seen = " ".join([
        repr(record), json.dumps(dataclasses.asdict(record), default=str),
        (world.folder(FOLDER) / f"{FOLDER}_NGRunner.json").read_text(encoding="utf-8"),
        (world.folder(FOLDER) / f"{FOLDER}_NGRunner_log.txt").read_text(encoding="utf-8"),
        *(event.text for event in list_events(world.store)),
        *(" ".join((m.subject, *m.lines)) for m in list_outbox(world.store)),
        repr(jobmodel.board_view(world.store, world.settings, world.clock,
                                 health=_health(), paused=False, shadow=False)),
        repr(jobmodel.digest_view(world.store, world.settings, world.clock)),
    ])
    assert [word for word in NEVER if word in seen] == []


def _health() -> Any:
    from ngrunner.board.views import HealthView

    return HealthView(paths=(), last_export=None, list_synced_at=None, heartbeat=None, toasts=None)


def test_the_takeoff_kept_in_the_store_holds_codes_and_numbers_only(world):
    filed(world, parts=(*CLOSET_PARTS, UNPLACED_PART))

    record, = takeoffs(world)
    allowed = {"buckets", "board_parts", "tape_lf", "estimate_total", "unplaced",
               "gate_pulled_equals_designs"}
    assert set(record.result) == allowed
    assert all(set(u) == {"page", "qty"} for u in record.result["unplaced"])
    assert get_state(world.store, "job.filed.4242")
