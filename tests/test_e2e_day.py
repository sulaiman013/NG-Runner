"""
Acceptance: the day simulation (docs/p1a/integration-spec.md, section 8). Each scenario runs NG
Runner itself among the fakes of tests/fakes.py, on a fake clock: real HTTP to fake servers on
127.0.0.1, real files under tmp_path, the real store and the real chokepoint. A scenario says what
the people around NG Runner do, and what must be true afterwards.

Where a scenario turns on what the Apps Script decides (the morning's digest, the shadow run, a new
database), the real google/Outbox.gs and Watchdog.gs run under node.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import socket
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import monotonic
from typing import Any

import pytest

from fakes import (
    CLOSET_PARTS,
    EXPORT_KEY,
    GOOGLE,
    KEY,
    KEY_HEX,
    PUSHOVER_KEYS,
    RENDER_URL,
    START,
    STUDIO_URL,
    TEST_MUTEX,
    Opp,
    RealScripts,
    World,
    colour_of,
    filed_paths,
    opps,
    running_world,
    seen_rows,
    tree,
    wait_for_fetches,
    with_studio_site,
)
from conftest import FIXTURES
from helpers_opt import NOT_NESTED, nest_text, sheet, utf8, utf16
from helpers_tap import BOTH, CLEAN, DOUBLED, ON_61, program
from test_guards_headsup import takeoff_json
from test_guards_labels import label_page, pdf
from test_guards_tracker import write_snapshot
from ngrunner import fab, heartbeat, jobmodel, single, toasts
from ngrunner.clock import PACIFIC, at_pacific, from_iso, to_iso
from ngrunner.config import load_settings
from ngrunner.intake import pipeline as intake_pipeline
from ngrunner.intake.fetch import FetchTarget, Render, agree, fetch_render, report_urls
from ngrunner.intake.summary_check import check_summary, content_fingerprint
from ngrunner.joblist import JobListError, RenderClient
from ngrunner.match import slugify
from ngrunner.messages import Message, pushover, templates
from ngrunner.messages.lint import lint
from ngrunner.messages.outbox import outbox_file, read_receipt
from ngrunner.signing import open_envelope
from ngrunner.guards import headsup, watch
from ngrunner.guards.design import room_identity
from ngrunner.guards.facts import (
    BR_CAM_NEST, COULD_NOT_VERIFY, DESIGN_MATCHES, FAILED, PASSED, SHEET_SIZE, DesignMatch, ToolVerdict,
)
from ngrunner.guards.kept import NEST_FILES, ask_for_redo
from ngrunner.guards.reads import CloudOnly
from ngrunner.guards import scan as guards_scan_module
from ngrunner.guards.scan import Plan
from ngrunner.guards.scan import scan as guard_scan
from ngrunner.store import (
    LATEST, MIGRATIONS, ProgramRecord, Store, add_card, add_snapshot, answer_card, ensure_job,
    get_heads_up, get_list_state, get_nest, get_outbox, get_program, get_state, list_clearances,
    list_events, list_nests, list_owner_log, list_programs, list_takeoffs, migrate, set_state,
)
from ngrunner.tray import COLOURS, draw_icon

PE, LS = "Pat Example", "Lee Sample"
TESTERSON = Opp("4242", "Testerson_101426", designer=PE)
TESTERSON_2 = Opp("4243", "Testerson_101526", designer=PE)
FAKELEY = Opp("4343", "Fakeley_101426", designer=PE)
FAKELEY_2 = Opp("4344", "Fakeley_101526", designer=PE)
MOCKFORD = Opp("4444", "Mockford_101426", designer=PE)
MOCKFORD_2 = Opp("4445", "Mockford_101526", designer=PE)
DUMMITT = Opp("4545", "Dummitt_101426", designer=PE)
DESIGNS_2 = (("Testerson - Primary Closet", "His and Her Master"),
             ("Testerson - Office", "Office"))
GARAGE_ONLY = (("Testerson - Garage", "Garage"),)
MIXED = (("Fakeley - Primary Closet", "His and Her Master"), ("Fakeley - Garage", "Garage"))
BLANK_CATEGORY = (("Mockford - Thing", ""),)
JIM_MAIL, DEV_MAIL = "jim@example.com", "dev@example.com"
GUARD_JOB, CLASSIC_FOLDER = "Testerson_101426", "3-4 Egger Classic White"


def minutes(n: float) -> timedelta:
    return timedelta(minutes=n)


def hours(n: float) -> timedelta:
    return timedelta(hours=n)


def pacific_at(month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return at_pacific(date(2026, month, day), time(hour, minute))


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """A connection to anything but this PC fails the moment it is tried, whichever code tries."""
    real_connect, real_lookup = socket.socket.connect, socket.getaddrinfo

    def connect(self: socket.socket, address: object) -> object:
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise OSError(f"no network in tests: {address[0]}")
        return real_connect(self, address)

    def lookup(host: object, *args: object, **kwargs: object) -> object:
        if host not in (None, "", "0.0.0.0", "127.0.0.1", "::1", "localhost"):
            raise OSError(f"no network in tests: {host}")
        return real_lookup(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    # What Windows' notification setting is on the PC running the tests is no fact of the day.
    monkeypatch.setattr(toasts, "toasts_enabled", lambda **_: True)


@pytest.fixture
def make_world(tmp_path) -> Iterator[Callable[..., World]]:
    """Worlds for one test, each stopped at its end."""
    opened = []

    def make(**options: object) -> World:
        running = running_world(tmp_path, **options)
        opened.append(running)
        return running.__enter__()

    yield make
    for running in reversed(opened):
        running.__exit__(None, None, None)


def installed(world: World, *listed: Opp, supervised: bool = True) -> World:
    """NG Runner just installed: its first export has been read. The listed Opportunities are on
    a job list the office PC pushed before any row below appeared."""
    opps(world, *listed)
    world.render.push()
    if not world.script.sheet:
        seen_rows(world)
    if supervised:
        world.start()
    world.step()
    return world


def is_filed(world: World, folder: str) -> Callable[[], bool]:
    return lambda: (world.ng_jobs / folder / f"{folder}_summary.pdf").exists()


def row_is(world: World, text: str, *states: str) -> Callable[[], bool]:
    return lambda: any(r.text == text and r.state in states for r in world.rows())


def folder_files(world: World, folder: str) -> set[str]:
    return {path for path in tree(world.ng_jobs) if path.startswith(f"{folder}/")}


def record_of(world: World, folder: str) -> dict:
    return json.loads((world.ng_jobs / folder / f"{folder}_NGRunner.json").read_text(
        encoding="utf-8"))


def assert_one_commit(world: World, folder: str) -> None:
    assert folder_files(world, folder) == filed_paths(folder)


# ----- the world itself: what the scenarios stand on


def test_the_fake_studio_renders_afresh_so_two_fetches_agree_in_different_bytes(make_world):
    world = make_world()
    opps(world, TESTERSON)
    target = FetchTarget("4242", "Testerson_101426", "Testerson", "landscape")
    first = fetch_render(target, http=world.http, base_url=STUDIO_URL)
    second = fetch_render(target, http=world.http, base_url=STUDIO_URL)
    assert first.ok and first.check.ok and second.ok and first.url.startswith(STUDIO_URL)
    assert first.data != second.data
    assert content_fingerprint(first.data) == content_fingerprint(second.data)
    outcome = agree(Render(first.data, START, "landscape"),
                    Render(second.data, START + minutes(10), "landscape"))
    assert outcome.code == "agree"
    assert world.studio.fetches("4242", "landscape") == 2


def test_the_fake_studios_switches(make_world):
    world = make_world()
    opps(world, TESTERSON)
    job = world.studio.jobs["4242"]
    target = FetchTarget("4242", "Testerson_101426", "Testerson", "landscape")

    def fetch(**options: object):
        return fetch_render(target, http=world.http, base_url=STUDIO_URL, **options)

    job.missing = True
    assert fetch().code == "not_found"
    job.missing = False
    job.truncated = True
    assert fetch().code == "truncated"
    job.truncated = False
    job.early = 1
    early, complete = fetch(), fetch()
    assert early.ok and not early.check.ok
    assert complete.ok and complete.check.ok
    job.delay = 0.3
    assert fetch(timeout_s=0.05).code == "timeout"
    job.delay = 0.0
    world.studio.down = True
    assert fetch().code == "http_error"
    world.studio.down = False
    job.printed_name = "Fakeley_101426"
    assert fetch().code == "wrong_job"
    job.printed_name = None
    before = content_fingerprint(fetch().data)
    job.designs = DESIGNS_2                                  # the IC summary has changed
    changed = fetch()
    assert changed.check.ok and content_fingerprint(changed.data) != before


def test_the_fake_render_serves_what_was_pushed_and_answers_a_sync_a_minute_later(make_world):
    world = make_world()
    client = RenderClient(RENDER_URL, EXPORT_KEY, http=world.http, clock=world.clock)
    assert client.export().synced_at is None             # no list yet
    world.render.opportunities.append(TESTERSON)
    assert client.export().opportunities == ()           # Salesforce has it; Render doesn't yet
    world.render.push()
    got = client.export()
    assert got.synced_at == world.clock.now()
    assert [o.name for o in got.opportunities] == ["Testerson_101426"]
    assert got.clients["4242"] == "Testerson"

    world.clock.advance(minutes(30))
    world.render.opportunities.append(FAKELEY)
    assert client.request_push() is True
    assert world.render.sync_requests == [world.clock.now()]
    world.render.on_clock()
    assert client.export().synced_at == got.synced_at    # the office PC takes about a minute
    world.clock.advance(minutes(1))
    world.render.on_clock()
    assert client.export().synced_at == world.clock.now()
    assert len(client.export().opportunities) == 2

    direct = RenderClient(world.render.base_url, EXPORT_KEY, http=world.http, clock=world.clock,
                          max_clock_skew=None)
    assert len(direct.export().opportunities) == 2
    wrong = RenderClient(RENDER_URL, "another-key", http=world.http, clock=world.clock)
    with pytest.raises(JobListError) as refused:
        wrong.export()
    assert refused.value.code == "refused"
    world.render.export_status = 503
    with pytest.raises(JobListError) as busy:
        client.export()
    assert busy.value.transient


def test_the_session_reaches_only_the_fakes(make_world):
    world = make_world()
    with pytest.raises(Exception, match="no network in tests"):
        world.http.get("https://example.com/")
    assert pushover.send(*PUSHOVER_KEYS.values(), "Title", "Text", urgent=True, http=world.http)
    assert world.http.pushover[0]["title"] == "Title"


def test_the_fake_apps_script_seals_what_ng_runner_reads(make_world):
    world = make_world()
    script = world.script
    script.add_row("Testerson", "PE", ("Friday", " 11 /6"))
    script.add_row("Fakeley_101426", "LS")
    first = script.export()
    export = fab.read_export((world.control / "fab" / "export.json").read_bytes(), key=KEY)
    assert export.seq == first == int(START.timestamp())
    assert export.exported_at == START
    assert [r.job for r in export.rows] == ["Testerson", "Fakeley_101426"]
    assert export.rows[0] == fab.ExportRow(2, "Testerson", "PE", ("Friday", " 11 /6"), "")

    world.clock.advance(minutes(5))
    script.correct_row("Testerson", to="Testerson_101426")
    script.remove_row("Fakeley_101426")
    assert script.export() == first + 1
    again = fab.read_export((world.control / "fab" / "export.json").read_bytes(), key=KEY)
    edits = fab.read_edits((world.control / "fab" / "edits.json").read_bytes(), key=KEY)
    assert [(e.column, e.old, e.new) for e in edits.edits] == [
        ("job", "Testerson", "Testerson_101426")]
    changes = fab.diff(export.rows, again.rows, edits.edits)
    assert sorted(c.kind for c in changes) == ["edited", "gone"]

    assert open_envelope((world.control / "pause" / f"{script.pause()}.json").read_bytes(),
                         key=KEY, kind="pause").payload == {
        "reason": "Sulaiman is checking something"}


def test_the_fake_apps_script_sends_each_message_once_with_a_receipt(make_world):
    world = make_world()
    made = outbox_file(templates.filed("Testerson_101426", row="Testerson"), to="barbara",
                       channel="email", key=KEY, seq=7, now=world.clock.now())
    target = world.control.joinpath(*made.path_parts)
    target.parent.mkdir(parents=True)
    target.write_bytes(made.data)

    world.script.receipts_stopped = True
    assert world.script.deliver() == []
    assert not (world.control / "receipts").exists()
    world.script.receipts_stopped = False
    sent = world.script.deliver()
    assert [(s.to, s.subject) for s in sent] == [("barbara", "Filed as Testerson_101426")]
    assert world.script.deliver() == []
    receipt = read_receipt((world.control / "receipts" / f"{made.id}.json").read_bytes(), key=KEY)
    assert receipt.ok and receipt.outbox_id == made.id


def test_the_real_scripts_run_over_the_control_folder_and_their_receipts_read_here(make_world,
                                                                                   tmp_path):
    world = make_world(at=pacific_at(10, 30, 6, 30))
    now = world.clock.now()
    made = outbox_file(templates.quiet_digest(date(2026, 10, 30)), to="jim", channel="email",
                       key=KEY, seq=int(now.timestamp()), now=now)
    world.control.joinpath(*made.path_parts).parent.mkdir(parents=True)
    world.control.joinpath(*made.path_parts).write_bytes(made.data)
    beat = heartbeat.payload(app_version="0.1.0", paused=False, open_risks=(), last_fab_seq=None,
                             list_synced_at=None, pending=0, dev_open=0)
    parts, data = heartbeat.heartbeat_file(beat, key=KEY, seq=int(now.timestamp()), now=now)
    world.control.joinpath(*parts).parent.mkdir(parents=True)
    world.control.joinpath(*parts).write_bytes(data)

    real = RealScripts(world.control, tmp_path / "node")
    real.run((now, "processOutbox"))
    assert real.digest_day == "20261030"
    assert real.mail_to(JIM_MAIL) == ["NG Runner · Fri 10/30"]
    real.run((now + minutes(30), "runWatchdog"))             # what it knew carries over
    assert real.mail_to(JIM_MAIL) == ["NG Runner · Fri 10/30"]
    assert real.beat["seq"] == int(now.timestamp())
    assert real.results[-1]["value"]["stale"] is False
    receipt = read_receipt((world.control / "receipts" / f"{made.id}.json").read_bytes(), key=KEY)
    assert receipt.ok


def test_the_tray_stand_in_reads_every_colour_it_is_shown():
    for colour in COLOURS:
        assert colour_of(draw_icon(colour, 3)) == colour


def test_the_key_is_the_harness_key_so_both_sides_open_each_others_files():
    harness = (GOOGLE / "test" / "harness.js").read_text(encoding="utf-8")
    assert f"const KEY_HEX = '{KEY_HEX}'" in harness


# ----- scenario 1: install


def test_scenario_01_install_sees_what_is_there_and_touches_nothing(make_world):
    world = make_world()
    opps(world, TESTERSON, FAKELEY)
    same = world.studio.report("4343", note=99_000)
    world.make_folder("Testerson_101426")
    world.make_folder("Fakeley_101426", summary=same)
    before = tree(world.ng_jobs)
    world.script.add_row("Mockford", "PE")
    world.script.add_row("Dummitt_101426", "LS")
    seen_rows(world)
    installed(world)

    assert all(r.state == "seen_at_install" for r in world.rows())
    assert len(world.rows()) == len(world.script.sheet)
    assert world.outbox() == [] and world.cards(state=None) == []
    assert world.studio.asked == []
    assert tree(world.ng_jobs) == before

    world.run(minutes=15)                                    # seen rows stay seen
    assert all(r.state == "seen_at_install" for r in world.rows())
    assert world.outbox() == [] and world.studio.asked == []

    # An exact-name row for a job whose folder was there at install is matched, and watched only.
    world.script.add_row("Testerson_101426", "PE")
    world.script.add_row("Fakeley_101426", "PE")
    world.run_until(lambda: row_is(world, "Fakeley_101426", "filed")()
                    and world.job("4242") is not None and len(world.renders("4242")) >= 2,
                    within=minutes(45))
    world.run(minutes=2)
    assert world.job("4242").mode == "observed"
    assert tree(world.ng_jobs) == before                     # no writes in either folder
    row = world.board_row("Testerson_101426")
    assert row.overlay == "OBSERVED"
    assert "folder" in row.next_step.lower() or "install" in row.next_step.lower()
    # A same-fingerprint summary still marks an observed job filed, with no write.
    assert world.job("4343").mode == "observed"
    assert world.board_row("Fakeley_101426").overlay == "OBSERVED"


# ----- scenario 2: a bare surname row, start to finish


def test_scenario_02_a_bare_surname_row_is_filed_told_and_listed(make_world, tmp_path):
    world = make_world()
    installed(world, TESTERSON)
    pushes_at_install = world.render.synced_at
    added_at = world.clock.now()
    world.script.add_row("Testerson", "PE")

    # It waits one export: a half-typed row never asks.
    world.run_until(lambda: any(r.text == "Testerson" for r in world.rows()), within=minutes(6))
    assert world.row("Testerson").state == "new"
    assert world.render.sync_requests == []
    assert world.studio.asked == []

    # The next export still shows it, and the stored list is older than the row: ask for a push.
    world.run_until(lambda: world.render.sync_requests, within=minutes(6))
    assert world.row("Testerson").state == "waiting_list"
    assert len(world.render.sync_requests) == 1
    assert world.render.synced_at == pushes_at_install

    # The fresh list matches it, and two fetches 10 minutes apart agree.
    filed_at = world.run_until(is_filed(world, "Testerson_101426"), within=minutes(40))
    assert filed_at - added_at <= minutes(45)
    assert len(world.render.sync_requests) == 1              # once per 5 minutes at most, and done
    assert world.row("Testerson").state == "filed"
    assert world.row("Testerson").studio_id == "4242"
    job = world.job("4242")
    assert (job.name, job.branch, job.folder, job.mode) == ("Testerson_101426", "closet",
                                                            "Testerson_101426", "auto")
    assert job.name_frozen
    first, second = world.renders("4242")
    assert second.fetched_at - first.fetched_at >= minutes(10)
    assert first.sha256 != second.sha256 and first.fingerprint == second.fingerprint

    # Three new files, the record of them right, and nothing else.
    assert_one_commit(world, "Testerson_101426")
    summary = (world.ng_jobs / "Testerson_101426" / "Testerson_101426_summary.pdf").read_bytes()
    assert check_summary(summary).project_name == "Testerson_101426"
    record = record_of(world, "Testerson_101426")
    assert record["schema"] == 3 and record["job"] == "Testerson_101426"
    assert record["branch"] == "closet" and record["items"] == []
    assert record["summary"]["file"] == "Testerson_101426_summary.pdf"
    assert record["summary"]["sha256"] == tree(world.ng_jobs)[
        "Testerson_101426/Testerson_101426_summary.pdf"]
    assert record["summary"]["layout"] == "landscape"
    assert len(record["summary"]["fetched"]) == 2
    log = (world.ng_jobs / "Testerson_101426" / "Testerson_101426_NGRunner_log.txt").read_text(
        encoding="utf-8")
    assert "Filed the IC summary" in log

    # "Filed as" goes to Barbara, once, and quotes her row.
    world.run(minutes=2)
    told = world.outbox(to="barbara", subject="Filed as Testerson_101426")
    assert len(told) == 1 and told[0].state == "sent"
    assert any('Fab row "Testerson"' in line for line in told[0].lines)
    assert [s.subject for s in world.script.sent if s.to == "barbara"] == [told[0].subject]
    assert world.board_row("Testerson_101426").next_step.startswith("Filed")
    assert {t.key: t.state for t in world.board_row("Testerson_101426").ticks}["filed"] == "done"

    # A heartbeat every 5 minutes, all the while.
    beats = world.heartbeats()
    gaps = [b.issued_at - a.issued_at for a, b in zip(beats, beats[1:])]
    assert len(beats) >= (world.clock.now() - added_at) // minutes(5)
    assert all(minutes(5) <= gap <= minutes(6) for gap in gaps)
    assert [b.seq for b in beats] == sorted({b.seq for b in beats})

    # The next 6:30 AM digest lists the job, and the real scripts count it as the morning's list.
    world.run_to(pacific_at(10, 28, 6, 25), step=3600)
    world.run_to(pacific_at(10, 28, 6, 32))
    digests = [m for m in world.outbox(to="jim") if m.ref == "digest:2026-10-28"]
    assert len(digests) == 1
    assert any("Testerson_101426" in line for line in digests[0].lines)
    assert digests[0].subject == "NG Runner · Wed 10/28"
    real = RealScripts(world.control, tmp_path / "node")
    real.run((world.clock.now(), "processOutbox"), (pacific_at(10, 28, 7, 5), "runWatchdog"))
    assert real.digest_day == "20261028"
    assert real.mail_to(JIM_MAIL) == ["NG Runner · Wed 10/28"]
    assert "No list reached Jim" not in real.mail_to(DEV_MAIL)


# ----- scenario 3: an unclear row


@pytest.mark.parametrize("settled_by", ["Barbara", "Jim"])
def test_scenario_03_an_unclear_row_asks_once_and_is_settled_by_either(make_world, settled_by):
    world = make_world()
    installed(world, TESTERSON, TESTERSON_2)
    world.script.add_row("Testerson", "PE")

    world.run_until(lambda: world.cards("which_job"), within=minutes(30))
    world.run(minutes=15)                                    # and no second card or email
    cards = world.cards("which_job")
    emails = world.outbox(to="barbara", subject="Which job")
    assert len(cards) == 1 and len(emails) == 1
    card = cards[0]
    row = world.row("Testerson")
    assert row.state == "asked" and card.fab_row_id == row.id
    assert set(card.choice_ids) == {"4242", "4243", None}
    assert card.choices[-1] == templates.NONE_OF_THESE
    assert world.studio.asked == [] and tree(world.ng_jobs) == {}
    assert any(m.kind == "which_job" for m in world.toasts.shown)
    assert world.board_row("Testerson").studio_id is None    # a row with no job yet shows

    if settled_by == "Barbara":
        world.script.correct_row("Testerson", to="Testerson_101526")
    else:
        world.actions.answer(card.id, card.choices[card.choice_ids.index("4243")])
    world.run_until(is_filed(world, "Testerson_101526"), within=minutes(45))

    assert_one_commit(world, "Testerson_101526")
    assert not (world.ng_jobs / "Testerson_101426").exists()
    settled = world.cards("which_job", state=None)
    assert len(settled) == 1
    assert settled[0].state == ("withdrawn" if settled_by == "Barbara" else "answered")
    typed = "Testerson_101526" if settled_by == "Barbara" else "Testerson"
    assert world.row(typed).studio_id == "4243" and world.row(typed).state == "filed"
    assert len(world.outbox(to="barbara", subject="Which job")) == 1
    world.run(minutes=2)
    assert len(world.outbox(to="barbara", subject="Filed as Testerson_101526")) == 1


# ----- scenario 4: an early render


def test_scenario_04_an_early_render_waits_for_a_complete_pair(make_world):
    world = make_world()
    installed(world, TESTERSON)
    job = world.studio.jobs["4242"]
    job.designs, job.early = DESIGNS_2, 1
    world.script.add_row("Testerson", "PE")

    world.run_until(lambda: world.renders("4242"), within=minutes(30))
    assert not world.renders("4242")[0].check_ok
    first_at = world.renders("4242")[0].fetched_at
    world.run(minutes=25)                                    # an early render retries every 30 min
    assert len(world.renders("4242")) == 1
    assert tree(world.ng_jobs) == {}

    filed_at = world.run_until(is_filed(world, "Testerson_101426"), within=minutes(40))
    assert filed_at - first_at >= minutes(40)                # +30 for fetch 1, +10 for fetch 2
    renders = world.renders("4242")
    assert [r.check_ok for r in renders] == [False, True, True]
    assert renders[2].fetched_at - renders[1].fetched_at >= minutes(10)
    summary = (world.ng_jobs / "Testerson_101426" / "Testerson_101426_summary.pdf").read_bytes()
    assert check_summary(summary).ok
    assert world.cards(state=None) == []


# ----- a slow Studio: a tick never waits on a fetch (integration spec section 1)


def test_a_slow_fetch_never_holds_the_tick_up_and_lands_by_itself(make_world):
    world = make_world()
    installed(world, TESTERSON)
    world.studio.jobs["4242"].delay = 3.0                    # far longer than one turn
    world.script.add_row("Testerson", "PE")

    for _ in range(20):                                      # step(), without its wait
        world.clock.advance(60)
        world.render.on_clock()
        world.script.on_clock()
        world.sup.run_due(world.clock.now())
        if world.studio.served:
            break
    assert world.studio.served, world.where_things_stand()
    assert world.renders("4242") == []                       # the turn ended with Studio rendering
    wait_for_fetches()
    world.studio.jobs["4242"].delay = 0.0
    world.step()                                             # the next tick takes its result
    assert len(world.renders("4242")) == 1                   # what the simulation's wait rests on
    world.run_until(is_filed(world, "Testerson_101426"), within=minutes(15))


def test_two_fetches_that_disagree_file_nothing_until_two_agree(make_world):
    world = make_world()
    installed(world, TESTERSON)
    world.script.add_row("Testerson", "PE")
    world.run_until(lambda: world.renders("4242"), within=minutes(30))
    world.studio.jobs["4242"].designs = DESIGNS_2            # Studio changes it between the two
    world.run_until(lambda: len(world.renders("4242")) == 2, within=minutes(15))
    world.run(minutes=2)
    assert tree(world.ng_jobs) == {}
    world.run_until(is_filed(world, "Testerson_101426"), within=minutes(15))
    first, second, third = world.renders("4242")
    assert first.fingerprint != second.fingerprint == third.fingerprint
    assert third.fetched_at - second.fetched_at >= minutes(10)


# ----- scenario 5: the branch


def test_scenario_05_garage_mixed_and_a_design_that_cant_be_classified(make_world):
    world = make_world()
    installed(world, TESTERSON, FAKELEY, MOCKFORD)
    world.studio.jobs["4242"].designs = GARAGE_ONLY
    world.studio.jobs["4343"].designs = MIXED
    world.studio.jobs["4444"].designs = BLANK_CATEGORY
    for name in ("Testerson_101426", "Fakeley_101426", "Mockford_101426"):
        world.script.add_row(name, "PE")

    world.run_until(lambda: is_filed(world, "Testerson_101426_Gar")()
                    and is_filed(world, "Fakeley_101426")()
                    and world.cards("closet_or_garage"), within=minutes(45))
    assert_one_commit(world, "Testerson_101426_Gar")         # garage only: <Name>_Gar
    assert world.job("4242").branch == "garage"
    assert not (world.ng_jobs / "Testerson_101426").exists()
    assert_one_commit(world, "Fakeley_101426")               # mixed: one job, no _Gar, no card
    assert world.job("4343").branch == "closet"
    card, = world.cards("closet_or_garage")                  # only that job waits
    assert card.studio_id == "4444"
    assert card.choices == ("Closet", "Garage")
    assert not any(path.startswith("Mockford") for path in tree(world.ng_jobs))
    assert world.job("4444").branch is None

    world.actions.answer(card.id, "Garage")
    world.run_until(is_filed(world, "Mockford_101426_Gar"), within=minutes(5))
    assert_one_commit(world, "Mockford_101426_Gar")
    assert world.job("4444").branch == "garage"
    assert world.cards("closet_or_garage") == []
    assert record_of(world, "Mockford_101426_Gar")["branch"] == "garage"


# ----- scenario 6: a folder that already holds a summary


def test_scenario_06_a_summary_already_there_is_compared_and_never_overwritten(make_world):
    world = make_world()
    installed(world, TESTERSON, FAKELEY, MOCKFORD, DUMMITT)
    # Folders made after install, by hand, so these jobs aren't OBSERVED.
    studio = world.studio
    world.make_folder("Testerson_101426", summary=studio.report("4242", note=99_000))
    studio.jobs["4343"].designs = (("Fakeley - Office", "Office"),)
    world.make_folder("Fakeley_101426", summary=studio.report("4343", note=99_001))
    studio.jobs["4343"].designs = (("Fakeley - Primary Closet", "His and Her Master"),)
    world.make_folder("Mockford_101426", old=True, summary=studio.report("4444", note=99_002))
    studio.jobs["4545"].designs = (("Dummitt - Pantry", "Pantry"),)
    world.make_folder("Dummitt_101426", old=True, summary=studio.report("4545", note=99_003))
    studio.jobs["4545"].designs = (("Dummitt - Primary Closet", "His and Her Master"),)
    before = tree(world.ng_jobs)
    for name in ("Testerson_101426", "Fakeley_101426", "Mockford_101426", "Dummitt_101426"):
        world.script.add_row(name, "PE")

    world.run_until(lambda: row_is(world, "Testerson_101426", "filed")()
                    and row_is(world, "Mockford_101426", "filed")()
                    and len(world.cards("ic_changed")) == 2, within=minutes(60))
    world.run(minutes=15)

    assert tree(world.ng_jobs) == before                     # no write anywhere, top or Old
    assert {c.studio_id for c in world.cards("ic_changed")} == {"4343", "4545"}
    assert len(world.cards("ic_changed")) == 2
    assert not (world.ng_jobs / "Mockford_101426").exists()  # never a second folder at the top
    assert not (world.ng_jobs / "Dummitt_101426").exists()
    assert world.job("4242").mode == "auto"


# ----- scenario 7: paused


def test_scenario_07_paused_stops_filing_and_nothing_else(make_world):
    world = make_world(at=pacific_at(10, 28, 6, 5))
    installed(world, TESTERSON, FAKELEY, FAKELEY_2)
    world.script.add_row("Testerson", "PE")
    world.run_until(lambda: world.renders("4242"), within=minutes(30))

    world.script.pause()
    world.step()                                             # pause files are read every minute
    assert world.tray.colour == "grey"
    assert world.tray_state(paused=True).colour == "grey"
    beats_before = len(world.heartbeats())
    world.script.add_row("Fakeley", "PE")                    # a new row still asks

    # Nothing is filed; heartbeats, the digest and messages go on.
    world.run_to(pacific_at(10, 28, 6, 50))
    assert tree(world.ng_jobs) == {} and tree(world.control / "shadow") == {}
    assert world.row("Testerson").state == "matched"
    assert len(world.heartbeats()) >= beats_before + 3
    assert world.heartbeats()[-1].payload["paused"] is True
    assert [m.ref for m in world.outbox(to="jim")].count("digest:2026-10-28") == 1
    email = world.outbox(to="barbara", subject="Which job")
    assert len(email) == 1 and email[0].state == "sent"
    assert world.board(paused=True).paused is True
    assert world.board_row("Testerson_101426", paused=True).overlay == "PAUSED"
    assert world.outbox(to="barbara", subject="Filed as") == []

    # Resumed: it carries on from where it stopped.
    world.actions.resume()
    world.run_until(is_filed(world, "Testerson_101426"), within=minutes(15))
    world.run(minutes=2)
    assert_one_commit(world, "Testerson_101426")
    assert world.tray.colour != "grey"
    assert len(world.outbox(to="barbara", subject="Filed as")) == 1
    assert world.row("Testerson").state == "filed"


# ----- scenario 8: Studio down


def test_scenario_08_studio_down_for_24_hours_raises_the_card_once(make_world):
    world = make_world()
    installed(world, TESTERSON)
    world.studio.down = True
    world.script.add_row("Testerson", "PE")
    world.run_until(lambda: world.studio.asked, within=minutes(30))
    tried_at = world.clock.now()
    addresses = len(report_urls(FetchTarget("4242", "Testerson_101426", "Testerson", "landscape")))

    world.run_to(tried_at + hours(23.5), step=1800)
    assert world.cards("fetch_failed") == []
    assert tree(world.ng_jobs) == {}
    # Back off: 5, 15, 30 and 60 minutes, then hourly, so fewer attempts than these turns.
    assert len(world.studio.asked) // addresses <= 40

    world.run_until(lambda: world.cards("fetch_failed"), within=hours(1.5), step=600)
    card, = world.cards("fetch_failed")
    assert card.studio_id == "4242" and "Testerson_101426" in card.text
    assert world.clock.now() - tried_at >= hours(24)
    assert card.default_choice == "Keep trying"
    world.run(minutes=240, step=3600)
    assert len(world.cards("fetch_failed", state=None)) == 1
    assert tree(world.ng_jobs) == {}


# ----- scenario 9: receipts stop


@pytest.mark.parametrize("with_keys", [True, False], ids=["with Pushover keys", "without them"])
def test_scenario_09_no_receipts_turn_the_tray_red_and_urgent_ones_find_a_way(
        make_world, monkeypatch, with_keys):
    pushed: list[tuple[str, bool]] = []

    def send(token, user, title, text, *, urgent, http, **options) -> bool:
        pushed.append((title, urgent))
        return True

    monkeypatch.setattr(pushover, "send", send)
    world = make_world(secrets=PUSHOVER_KEYS if with_keys else None)
    installed(world)
    assert world.tray.colour == "green"
    world.script.receipts_stopped = True
    since = world.clock.now()
    message_id = world.messenger.send(templates.cant_send(since=since, now=since, waiting=1),
                                      to="sulaiman")

    world.run(minutes=4)
    assert not world.messenger.overdue()
    assert world.tray.colour != "red" and pushed == []

    world.run(minutes=3)                                     # past 5 minutes with no receipt
    assert world.messenger.overdue()
    assert world.tray_state(unwell=True).colour == "red"
    assert world.tray.colour == "red"

    world.run(minutes=12)                                    # let any of its own messages come due
    if with_keys:
        assert (templates.cant_send(since=since, now=since, waiting=1).title, True) in pushed
        assert get_outbox(world.store, message_id).fallback_at is not None
        assert not [d for d in world.dev() if d.urgent]
    else:
        assert pushed == []
        assert get_outbox(world.store, message_id).fallback_at is None
        assert [d for d in world.dev() if d.urgent]
    assert world.tray.colour == "red"
    settled = len(pushed)
    world.run(minutes=10)
    assert len(pushed) == settled                            # once each, never every minute

    world.script.receipts_stopped = False
    world.run(minutes=2)
    assert get_outbox(world.store, message_id).state == "sent"
    assert not world.messenger.overdue()
    assert not [d for d in world.dev() if d.urgent]
    assert world.tray.colour != "red"


# ----- scenario 10: the nightly re-fetch


def test_scenario_10_changed_content_confirmed_twice_raises_the_card_and_refiles_nothing(
        make_world):
    world = make_world()
    installed(world, TESTERSON)
    world.script.add_row("Testerson", "PE")
    world.run_until(is_filed(world, "Testerson_101426"), within=minutes(45))
    world.run(minutes=2)                     # its takeoff, made and in its notes
    filed = tree(world.ng_jobs)
    summary_sha = filed["Testerson_101426/Testerson_101426_summary.pdf"]
    fetches_at_filing = world.studio.fetches("4242")

    # The first night: the same content in new bytes, so no card.
    world.run_to(pacific_at(10, 28, 1, 15), step=3600)
    world.run_to(pacific_at(10, 28, 2, 15), step=300)
    assert world.studio.fetches("4242") > fetches_at_filing
    assert world.cards(state=None) == []
    assert tree(world.ng_jobs) == filed

    # The next night the IC summary has changed, and two agreeing fetches confirm it.
    world.studio.jobs["4242"].designs = DESIGNS_2
    world.run_to(pacific_at(10, 29, 1, 15), step=3600)
    world.run_until(lambda: world.cards("ic_changed"), within=hours(24), step=300)
    card, = world.cards("ic_changed")
    assert card.studio_id == "4242"
    changed = [r for r in world.renders("4242") if r.fingerprint != world.renders("4242")[0]
               .fingerprint]
    assert len(changed) >= 2
    assert changed[1].fetched_at - changed[0].fetched_at >= minutes(10)

    # Nothing is re-filed by itself. The notes Jim's chat reads list the open card.
    now = tree(world.ng_jobs)
    assert now["Testerson_101426/Testerson_101426_summary.pdf"] == summary_sha
    assert set(now) == set(filed)
    items = json.dumps(record_of(world, "Testerson_101426")["items"])
    assert card.id in items or "IC changed" in items or "ic_changed" in items
    assert len(world.outbox(to="barbara", subject="Filed as")) == 1

    # And one card, however many nights it stands.
    world.run(minutes=24 * 60, step=3600)
    assert len(world.cards("ic_changed", state=None)) == 1
    assert (tree(world.ng_jobs)["Testerson_101426/Testerson_101426_summary.pdf"]
            == summary_sha)


# ----- scenario 11: the night of the clock change


def test_scenario_11_the_night_the_clocks_go_back_the_night_work_runs_once(make_world):
    world = make_world(at=pacific_at(10, 31, 18, 0))
    installed(world, TESTERSON)
    world.script.add_row("Testerson", "PE")
    world.run_until(is_filed(world, "Testerson_101426"), within=minutes(45))
    fetches_before = world.studio.fetches("4242")

    # 1:30 AM comes twice on Sunday 11/1: 08:30 and 09:30 UTC. Run well past both.
    world.run_to(pacific_at(11, 1, 0, 30), step=3600)
    world.run_to(pacific_at(11, 1, 4, 0), step=600)

    def ran_lines() -> list[object]:
        return world.outbox(to="sulaiman", subject="NG Runner ran")

    assert sorted(tree(world.control / "backups")) == ["20261101.ngrbak"]
    assert len(ran_lines()) == 1
    assert world.studio.fetches("4242") == fetches_before + 1
    assert len(tree(world.control / "logs")) == 1

    # The digest comes once, at the wall clock's 6:30 AM.
    world.run_to(pacific_at(11, 1, 6, 15), step=3600)
    world.run_to(pacific_at(11, 1, 7, 0), step=300)
    digests = [m for m in world.outbox(to="jim") if m.ref == "digest:2026-11-01"]
    assert len(digests) == 1
    assert digests[0].created_at >= pacific_at(11, 1, 6, 30)

    # The night after is a night of its own.
    world.run_to(pacific_at(11, 2, 1, 15), step=3600)
    world.run_to(pacific_at(11, 2, 2, 0), step=300)
    assert sorted(tree(world.control / "backups")) == ["20261101.ngrbak", "20261102.ngrbak"]
    assert len(ran_lines()) == 2


# ----- scenario 12: a restart


def test_scenario_12_a_restart_mid_pipeline_finishes_without_duplicates(make_world):
    world = make_world()
    guard = single.acquire(TEST_MUTEX)
    assert guard is not None
    installed(world, TESTERSON, FAKELEY, FAKELEY_2)
    world.script.add_row("Testerson", "PE")
    world.script.add_row("Fakeley", "PE")
    world.run_until(lambda: world.renders("4242") and world.cards("which_job"),
                    within=minutes(30))

    # Stopped between the two fetches, with a card out; a new process takes over.
    world.stop()
    guard.release()
    guard = single.acquire(TEST_MUTEX)
    assert guard is not None
    world.start()
    world.run_until(is_filed(world, "Testerson_101426"), within=minutes(30))
    world.run(minutes=3)

    # And again once it is filed.
    filed = tree(world.ng_jobs)
    world.stop()
    guard.release()
    guard = single.acquire(TEST_MUTEX)
    assert guard is not None
    world.start()
    world.run(minutes=30)
    guard.release()

    assert tree(world.ng_jobs) == filed
    assert_one_commit(world, "Testerson_101426")
    assert len(world.renders("4242")) == 2
    assert len(world.outbox(to="barbara", subject="Filed as Testerson_101426")) == 1
    assert len(world.outbox(to="barbara", subject="Which job")) == 1
    assert len(world.cards("which_job", state=None)) == 1
    assert world.row("Fakeley").state == "asked"
    assert world.row("Testerson").state == "filed"
    assert len({m.seq for m in world.outbox()}) == len(world.outbox())


# ----- scenario 13: the wrong-job property


@dataclass(frozen=True)
class _Case:
    target: Opp
    row: str
    designer: str
    how: str


_OWNERS = ((PE, "PE"), (LS, "LS"), ("Quinn Fake", "QF"), ("Ari Mock", "AM"))
_SOLD = ("Ready to Order", "Ready to Install")
_FIRST = ("Test", "Fake", "Mock", "Dumm", "Sampl", "Plac", "Stub", "Faux", "Sham", "Pret")
_LAST = ("erson", "eley", "ford", "itt", "eton", "holm", "by", "well", "stead", "ham")
_KINDS = ("exact", "exact_unsold", "bare", "other_designers_sale", "two_sold", "newer_unsold",
          "room", "typed_date", "five_digit", "first_name", "compound_rival", "remake",
          "wrong_report")


def _sale_date(rng: random.Random, *, after: int = 0) -> tuple[int, int]:
    return 10, rng.randint(1 + after, 14 + after)


def _name(surname: str, day: tuple[int, int]) -> str:
    return f"{surname}_{day[0]:02d}{day[1]:02d}26"


def _generated_day(seed: int, count: int = 26) -> tuple[list[Opp], list[_Case], dict[str, str]]:
    """Rows and Opportunities in every shape the matcher must sort. Each row is typed faithfully
    for its target (its designer's initials, words from its name), so the only way a summary lands
    elsewhere is NG Runner's own mistake. Returns what the list holds, the rows, and the names
    Studio prints instead of the job's, for the reports that are another job's."""
    rng = random.Random(seed)
    surnames = [first + last for first in _FIRST for last in _LAST]
    rng.shuffle(surnames)
    listed: list[Opp] = []
    cases: list[_Case] = []
    wrong: dict[str, str] = {}
    next_id = iter(range(10_000 + seed * 1000, 99_999))

    def add(name: str, owner: str, stage: str | None = None) -> Opp:
        opp = Opp(str(next(next_id)), name, stage or rng.choice(_SOLD), owner)
        listed.append(opp)
        return opp

    for how in [_KINDS[i % len(_KINDS)] for i in range(count)]:
        surname = surnames.pop()
        (owner, initials), (other, _) = rng.sample(_OWNERS, 2)
        day = _sale_date(rng)
        name = _name(surname, day)
        typed = surname
        target = add(name, owner, "Propose" if how == "exact_unsold" else None)
        if how in ("exact", "exact_unsold"):
            typed = name
        elif how == "other_designers_sale":
            add(_name(surname, _sale_date(rng, after=14)), other)
        elif how == "two_sold":
            add(_name(surname, _sale_date(rng, after=14)), owner)
        elif how == "newer_unsold":
            add(_name(surname, _sale_date(rng, after=14)), owner, "Propose")
        elif how == "room":
            typed = f"{surname} closet"
        elif how == "typed_date":
            typed = f"{surname} {day[0]}/{day[1]}/26"
        elif how == "five_digit":
            typed = f"{surname}_{day[0]:02d}{day[1]:02d}2"
        elif how == "first_name":
            typed = f"{surname} Jane"
        elif how == "compound_rival":
            add(f"{surname}{surnames.pop()}_{day[0]:02d}{day[1]:02d}26", owner)
        elif how == "remake":
            typed = f"{surname} remake"
        elif how == "wrong_report":
            wrong[target.studio_id] = _name(surnames.pop(), day)
        cases.append(_Case(target, typed, initials, how))
    return listed, cases, wrong


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_scenario_13_every_filed_summary_belongs_to_the_row_it_was_generated_for(make_world, seed):
    listed, cases, wrong = _generated_day(seed)
    world = make_world()
    opps(world, *listed)
    for studio_id, printed in wrong.items():
        world.studio.jobs[studio_id].printed_name = printed
    installed(world, supervised=False)
    for case in cases:
        world.script.add_row(case.row, case.designer)

    def settled() -> bool:
        states = {r.text: r for r in world.rows()}
        held = {c.studio_id for c in world.cards() if c.studio_id is not None}
        return all(case.row in states
                   and (states[case.row].state in ("filed", "asked", "none")
                        or states[case.row].studio_id in held)
                   for case in cases)

    world.run_until(settled, within=hours(3))
    world.run(minutes=15)

    filed = []
    for case in cases:
        row = world.row(case.row)
        if row.state == "filed":
            assert row.studio_id == case.target.studio_id, f"{case} filed to {row.studio_id}"
            filed.append(case)
        else:                                                # every other row asks
            asks = world.cards("which_job", state="open")
            held = world.cards(state="open")
            assert (any(c.fab_row_id == row.id for c in asks)
                    or any(row.studio_id is not None and c.studio_id == row.studio_id
                           for c in held)), f"{case}: {row}"

    # Every summary in NG Jobs is its own job's, and is explained by a row.
    folders = {path.split("/")[0] for path in tree(world.ng_jobs) if path.endswith("_summary.pdf")}
    assert folders == {case.target.name for case in filed}
    for case in filed:
        data = (world.ng_jobs / case.target.name / f"{case.target.name}_summary.pdf").read_bytes()
        assert slugify(check_summary(data).project_name) == slugify(case.target.name)
    # The property was really tried: the plainest rows file, and the ones that can't be sure ask.
    shapes_filed = {case.how for case in filed}
    assert {"exact", "bare"} <= shapes_filed
    assert not shapes_filed & {"two_sold", "first_name", "wrong_report", "newer_unsold",
                               "compound_rival", "remake", "five_digit"}


# ----- scenario 14: shadow mode


def test_scenario_14_shadow_mode_files_aside_tells_only_sulaiman_and_switches_live(make_world):
    world = make_world(mode="shadow")
    installed(world, TESTERSON, FAKELEY, MOCKFORD, MOCKFORD_2)
    world.script.add_row("Testerson", "PE")
    world.script.add_row("Mockford", "PE")                   # an unclear row: a card, no toast
    world.run_until(lambda: (world.control / "shadow" / "Testerson_101426"
                             / "Testerson_101426_summary.pdf").exists()
                    and world.cards("which_job"), within=minutes(45))
    world.run(minutes=2)

    # The files land in control\shadow\<Name>\, and NG Jobs is untouched.
    assert set(tree(world.control / "shadow")) == filed_paths("Testerson_101426")
    assert tree(world.ng_jobs) == {}
    assert world.row("Testerson").state == "filed"           # a shadow decision is stored as live

    # "Filed as" goes to Sulaiman only, marked SHADOW; so does what Barbara would have had.
    assert world.outbox(to="barbara") == [] and world.outbox(to="jim") == []
    assert len(world.outbox(to="sulaiman", subject="SHADOW Filed as Testerson_101426")) == 1
    assert len(world.outbox(to="sulaiman", subject="SHADOW Which job")) == 1
    assert {s.to for s in world.script.sent} == {"sulaiman"}

    # The board and the tray say SHADOW, and no toast shows.
    board = world.board()
    assert board.shadow is True and board.tray.tooltip.startswith("SHADOW")
    assert world.tray.tooltip.startswith("SHADOW")
    assert world.toasts.shown == []

    # Switching to live is logged, and files the next row for real.
    world.actions.set_mode("live")
    assert load_settings(world.paths).mode == "live"
    world.settings = replace(world.settings, mode="live")
    assert any("live" in e.text.lower() for e in world.events())
    world.script.add_row("Fakeley", "PE")
    world.run_until(is_filed(world, "Fakeley_101426"), within=minutes(45))
    world.run(minutes=2)
    assert_one_commit(world, "Fakeley_101426")
    assert not (world.control / "shadow" / "Fakeley_101426").exists()
    assert len(world.outbox(to="barbara", subject="Filed as Fakeley_101426")) == 1
    assert world.outbox(to="sulaiman", subject="SHADOW Filed as Fakeley") == []
    assert not world.tray.tooltip.startswith("SHADOW")
    assert not world.board().shadow


# ----- scenarios 15, 16 and 20: the real scripts


def _quiet_until_the_morning(world: World) -> None:
    """Installed before the night's work, with nothing to say; the fake script sends what the
    night leaves, and then the real one takes over for the morning."""
    installed(world)
    world.run_to(pacific_at(10, 30, 6, 25), step=1800)
    world.script.receipts_stopped = True


def test_scenario_15_a_quiet_night_counts_as_the_mornings_digest(make_world, tmp_path):
    world = make_world(at=pacific_at(10, 30, 1, 0))
    _quiet_until_the_morning(world)
    world.run_to(pacific_at(10, 30, 6, 31))
    digest, = [m for m in world.outbox(to="jim") if m.ref == "digest:2026-10-30"]
    assert digest.subject == "NG Runner · Fri 10/30"
    assert len(digest.lines) == 1 and digest.lines[0].startswith("Nothing new")

    real = RealScripts(world.control, tmp_path / "node")
    real.run((world.clock.now(), "processOutbox"))
    assert real.digest_day == "20261030"
    assert real.mail_to(JIM_MAIL) == ["NG Runner · Fri 10/30"]
    world.step()
    assert get_outbox(world.store, digest.id).state == "sent"  # the real receipt reads here

    world.run_to(pacific_at(10, 30, 7, 5), step=300)
    real.run((pacific_at(10, 30, 7, 5), "runWatchdog"))
    assert real.mail_to(JIM_MAIL) == ["NG Runner · Fri 10/30"]  # no "No list today"
    assert "No list reached Jim" not in real.mail_to(DEV_MAIL)
    assert real.push == []
    assert real.results[-1]["value"]["stale"] is False


def test_scenario_16_a_shadow_morning_counts_and_jim_gets_nothing(make_world, tmp_path):
    world = make_world(mode="shadow", at=pacific_at(10, 30, 1, 0))
    _quiet_until_the_morning(world)
    world.run_to(pacific_at(10, 30, 6, 31))
    digest, = [m for m in world.outbox(to="sulaiman") if m.ref == "digest:2026-10-30"]
    assert digest.subject == "SHADOW NG Runner · Fri 10/30"
    assert world.outbox(to="jim") == []

    real = RealScripts(world.control, tmp_path / "node", props={"SHADOW": "1"})
    real.run((world.clock.now(), "processOutbox"))
    assert real.digest_day == "20261030"
    world.run_to(pacific_at(10, 30, 7, 5), step=300)
    real.run((pacific_at(10, 30, 7, 5), "runWatchdog"))
    assert real.mail_to(JIM_MAIL) == []
    subjects = real.mail_to(DEV_MAIL)
    assert "SHADOW NG Runner · Fri 10/30" in subjects
    assert "No list reached Jim" not in subjects and "SHADOW No list today" not in subjects
    assert real.push == []


def test_scenario_20_a_new_database_on_a_used_control_folder_is_heard(make_world, tmp_path):
    first = make_world(at=pacific_at(10, 27, 10, 0))
    installed(first)
    first.script.receipts_stopped = True
    first.messenger.send(templates.filed("Testerson_101426", row="Testerson"), to="barbara")
    first.run(minutes=10)
    real = RealScripts(first.control, tmp_path / "node")
    real.run((first.clock.now(), ["processOutbox", "runWatchdog"]))
    assert real.mail_to("barbara@example.com") == ["Filed as Testerson_101426"]
    old_seq = max(m.seq for m in first.outbox())
    old_beat = first.heartbeats()[-1].seq
    first.stop()

    # A new database, a day later, on the same control folder: every stream starts over, at the
    # current Unix second, so it is above whatever the old one reached.
    later = pacific_at(10, 28, 10, 0)
    second = make_world(at=later, data="data-after-a-new-pc")
    installed(second)
    second.script.receipts_stopped = True
    second.messenger.send(templates.filed("Fakeley_101426", row="Fakeley"), to="barbara")
    second.run(minutes=10)
    new_mail = second.outbox(subject="Filed as Fakeley_101426")[0]
    assert new_mail.seq >= int(later.timestamp()) > old_seq
    new_beats = [b for b in second.heartbeats() if b.issued_at >= later]
    assert new_beats and new_beats[0].seq >= int(later.timestamp()) > old_beat

    # The real scripts accept them: the jump is a gap, given up after 2 minutes, as for any gap.
    now = second.clock.now()
    real.run((now, "processOutbox"), (now + minutes(3), ["processOutbox", "runWatchdog"]))
    assert real.mail_to("barbara@example.com")[-1] == "Filed as Fakeley_101426"
    assert real.beat["seq"] == second.heartbeats()[-1].seq
    assert real.results[-1]["value"]["stale"] is False
    second.step()
    assert get_outbox(second.store, new_mail.id).state == "sent"


# ----- scenario 17: rows typed with a date or a five-digit typo


def test_scenario_17_rows_typed_oddly_pass_the_lint_and_are_quoted_as_typed(make_world):
    world = make_world()
    installed(world, TESTERSON, FAKELEY)
    typed = ("Testerson 10/13/26", "Fakeley_10142")
    for row in typed:
        world.script.add_row(row, "PE")
    world.run_until(lambda: len(world.cards("which_job")) == 2, within=minutes(30))
    world.run(minutes=2)

    def message_of(m) -> Message:
        return Message("outbox", m.subject, m.lines, m.urgent)

    for row in typed:
        emails = [m for m in world.outbox(to="barbara") if f'"{row}"' in m.subject]
        assert len(emails) == 1, world.where_things_stand()
        assert lint(message_of(emails[0])) == []
        assert f'Fab row "{row}"' in emails[0].lines[0]      # as typed, not its fixed template

    # The digest that brings each card back at one day quotes it the same way.
    world.run_to(pacific_at(10, 29, 6, 15), step=3600)
    world.run_to(pacific_at(10, 29, 6, 40), step=300)
    digest, = [m for m in world.outbox(to="jim") if m.ref == "digest:2026-10-29"]
    assert lint(message_of(digest)) == []
    text = "\n".join(digest.lines)
    for row in typed:
        assert f'Fab row "{row}"' in text, text
    assert "did not pass" not in text
    assert not [d for d in world.dev() if "message check" in d.text or "lint" in d.code]


# ----- scenario 18: an empty export


def test_scenario_18_an_empty_export_is_held_and_rows_seen_at_install_stay_seen(make_world):
    world = make_world()
    installed(world)
    seen = {r.id for r in world.rows()}
    world.script.auto_export = False

    world.clock.advance(minutes(5))
    world.script.export(rows=[])                             # a sheet that came back empty
    world.step()
    assert {r.state for r in world.rows()} == {"seen_at_install"}
    assert [d for d in world.dev() if d.code == "fab_held"]  # held, and logged to DEV

    world.clock.advance(minutes(5))
    world.script.export()                                    # the whole sheet again
    world.step()
    world.script.auto_export = True
    world.run(minutes=20)
    assert {r.id for r in world.rows()} == seen              # nothing looks new
    assert {r.state for r in world.rows()} == {"seen_at_install"}
    assert world.cards(state=None) == []
    assert world.outbox(to="barbara") == [] and world.outbox(to="jim") == []
    assert world.studio.asked == [] and world.render.sync_requests == []
    assert tree(world.ng_jobs) == {}


# ----- scenario 19: a late receipt


def test_scenario_19_a_late_receipt_with_a_lower_seq_still_counts(make_world):
    world = make_world()
    installed(world)
    world.script.receipts_stopped = True
    colour_before = world.tray.colour
    first = world.messenger.send(templates.filed("Testerson_101426", row="Testerson"),
                                 to="barbara")
    second = world.messenger.send(templates.filed("Fakeley_101426", row="Fakeley"), to="barbara")
    high = int(world.clock.now().timestamp()) + 100

    world.script.write_receipt(second, seq=high)             # the later one arrives first
    world.step()
    assert get_outbox(world.store, second).state == "sent"
    assert get_outbox(world.store, first).state == "written"

    world.script.write_receipt(first, seq=high - 1)          # Drive syncs in any order
    world.step()
    assert get_outbox(world.store, first).state == "sent"

    world.run(minutes=7)
    assert not world.messenger.overdue()
    assert world.tray.colour == colour_before
    assert not [d for d in world.dev() if "receipt" in f"{d.code} {d.text}".lower()]


# ----- scenario 21: a hand-filed summary in the other layout


def test_scenario_21_a_hand_filed_portrait_is_compared_with_a_fetched_portrait_pair(make_world):
    world = make_world()
    installed(world, TESTERSON)
    world.make_folder("Testerson_101426",
                      summary=world.studio.report("4242", layout="portrait", note=99_000))
    before = tree(world.ng_jobs)
    world.script.add_row("Testerson_101426", "PE")

    world.run_until(row_is(world, "Testerson_101426", "filed"), within=minutes(60))
    assert tree(world.ng_jobs) == before                     # no write
    assert world.cards("ic_changed", state=None) == []       # the same content, so no card
    assert world.studio.fetches("4242", "portrait") >= 2
    assert world.renders("4242")[-1].layout == "portrait"    # the job keeps portrait

    landscape = world.studio.fetches("4242", "landscape")
    world.run_to(pacific_at(10, 28, 1, 15), step=3600)
    world.run_to(pacific_at(10, 28, 2, 15), step=300)        # the night's re-fetch is portrait too
    assert world.studio.fetches("4242", "landscape") == landscape
    assert world.renders("4242")[-1].layout == "portrait"
    assert world.cards("ic_changed", state=None) == []


# ----- scenario 22: answering after a restart and a new list


def test_scenario_22_an_answer_links_only_through_the_id_its_choice_holds(make_world):
    world = make_world()
    installed(world, TESTERSON, TESTERSON_2, FAKELEY, FAKELEY_2, MOCKFORD, MOCKFORD_2, DUMMITT)
    for row in ("Testerson", "Fakeley", "Mockford"):
        world.script.add_row(row, "PE")
    world.run_until(lambda: len(world.cards("which_job")) == 3, within=minutes(30))
    world.run(minutes=2)

    def card_of(row: str):
        rec = world.row(row)
        found, = [c for c in world.cards("which_job") if c.fab_row_id == rec.id]
        return found

    testerson, fakeley, mockford = card_of("Testerson"), card_of("Fakeley"), card_of("Mockford")

    # A restart, and a new job list: 4243's rival is joined by a newer sale, 4344 is gone.
    world.render.opportunities.remove(FAKELEY_2)
    world.render.opportunities.append(Opp("4246", "Testerson_101626", designer=PE))
    world.studio.add("4246", "Testerson_101626", client="Testerson")
    world.restart()
    pushed_at = world.clock.now()
    world.render.push()
    world.script.add_row("Dummitt_101426", "PE")             # makes it read the new list
    world.run_until(lambda: (state := get_list_state(world.store)) is not None
                    and state.synced_at >= pushed_at.replace(microsecond=0)
                    and world.job("4545") is not None, within=minutes(15))

    # The choice's ID decides, whatever a new match would give.
    echo = world.actions.answer(testerson.id, testerson.choices[testerson.choice_ids.index("4243")])
    assert echo
    world.run_until(is_filed(world, "Testerson_101526"), within=minutes(30))
    assert world.row("Testerson").studio_id == "4243"
    assert world.job("4246") is None

    # An ID no longer in the list links nothing, the board says so, and the row stays asked.
    said = world.actions.answer(fakeley.id, fakeley.choices[fakeley.choice_ids.index("4344")])
    world.run(minutes=3)
    row = world.row("Fakeley")
    assert row.state == "asked" and row.studio_id is None
    assert world.job("4344") is None
    assert world.studio.fetches("4344") == 0
    words = f"{said} {world.board_row('Fakeley').next_step}".lower()
    assert any(phrase in words for phrase in ("no longer", "gone", "not in the", "isn't in",
                                              "left the")), words

    # "None of these" sets the row to none, logged, and files nothing.
    world.actions.answer(mockford.id, templates.NONE_OF_THESE)
    world.run(minutes=3)
    assert world.row("Mockford").state == "none"
    assert world.row("Mockford").studio_id is None
    assert world.studio.fetches("4444") == world.studio.fetches("4445") == 0


def test_the_generated_day_holds_every_shape_and_types_each_row_for_its_target():
    listed, cases, wrong = _generated_day(1, count=len(_KINDS))
    assert {case.how for case in cases} == set(_KINDS)
    owners = dict((name, initials) for name, initials in _OWNERS)
    assert all(owners[case.target.designer] == case.designer for case in cases)
    assert len({o.studio_id for o in listed}) == len(listed)
    assert len({o.name for o in listed}) == len(listed)
    assert len(wrong) == 1
    assert _generated_day(1, count=len(_KINDS))[1] == cases  # the same seed, the same day


# ----- the Studio design check: the real StudioSite and parser, over HTTP to the fake Studio


def log_of(world: World, folder: str) -> str:
    return (world.ng_jobs / folder / f"{folder}_NGRunner_log.txt").read_text(encoding="utf-8")


def answer(world: World, card: object, choice: str) -> None:
    assert answer_card(world.store, card.id, answer=choice, answer_words=choice, answered_by="jim",
                       answered_at=world.clock.now(), channel="board")


def test_scenario_23_a_report_that_matches_files_with_its_counts_for_a_person_to_check(make_world):
    world = with_studio_site(make_world())
    installed(world, TESTERSON, supervised=False)
    world.studio.jobs["4242"].designs = DESIGNS_2
    world.script.add_row("Testerson", "PE")
    world.run_until(is_filed(world, "Testerson_101426"), within=minutes(45))
    world.run(minutes=2)

    pages = world.renders("4242")[-1].page_count
    record = record_of(world, "Testerson_101426")
    assert record["designs"] == 2 and record["designs_checked"] is True
    assert record["summary"]["pages"] == pages
    assert f"2 designs, {pages} pages" in log_of(world, "Testerson_101426")
    assert "not cross-checked" not in log_of(world, "Testerson_101426")
    told, = world.outbox(to="barbara", subject="Filed as Testerson_101426")
    assert told.subject == f"Filed as Testerson_101426: 2 designs, {pages} pages"
    assert told.lines[0].endswith(f"is filed as Testerson_101426: 2 designs, {pages} pages.")
    assert [s.subject for s in world.script.sent if s.to == "barbara"] == [told.subject]
    assert lint(Message("filed", told.subject, told.lines)) == []
    assert world.dev() == [] and world.sign_ins == []


def test_scenario_24_a_report_that_differs_from_the_proposal_never_files_until_jim_says(make_world):
    world = with_studio_site(make_world())
    installed(world, TESTERSON, FAKELEY, supervised=False)
    world.studio.jobs["4242"].proposal = ("Testerson - Primary Closet", "Testerson - Office")
    world.script.add_row("Testerson", "PE")
    world.script.add_row("Fakeley", "PE")

    # Only that job waits.
    world.run_until(is_filed(world, "Fakeley_101426"), within=minutes(45))
    card, = world.cards("design_mismatch")
    assert card.text == ("Testerson_101426: the report lists 1 design and Studio's proposal lists"
                         " 2.")
    assert card.choices == ("Fetch it again", "File it as it is")
    assert folder_files(world, "Testerson_101426") == set()

    # It looks again every 30 minutes, and one card stands however long nobody answers.
    served = world.studio.fetches("4242")
    world.run(minutes=120, step=300)
    assert world.studio.fetches("4242") >= served + 3
    assert len(world.cards("design_mismatch", state=None)) == 1
    assert folder_files(world, "Testerson_101426") == set()
    assert world.outbox(to="barbara", subject="Filed as Testerson") == []
    assert world.row("Testerson").state == "matched"

    # "Fetch it again" still differs: a new card, and nothing filed.
    answer(world, card, "Fetch it again")
    world.run_until(lambda: world.cards("design_mismatch"), within=minutes(5))
    again, = world.cards("design_mismatch")
    assert again.id != card.id
    assert folder_files(world, "Testerson_101426") == set()

    # "File it as it is" files it, once two fetches agree, and says Jim chose to.
    answer(world, again, "File it as it is")
    world.run_until(is_filed(world, "Testerson_101426"), within=minutes(30))
    world.run(minutes=2)
    assert_one_commit(world, "Testerson_101426")
    record = record_of(world, "Testerson_101426")
    assert record["designs"] == 1 and record["designs_checked"] is False
    assert "Jim chose to file it as it is" in log_of(world, "Testerson_101426")
    event, = world.events(kind="designs_differ_filed")
    assert event.lane == "ACT" and "Jim chose to file it as it is" in event.text
    assert world.cards("design_mismatch") == []
    assert len(world.outbox(to="barbara", subject="Filed as Testerson_101426")) == 1
    assert world.dev() == []


def test_scenario_25_once_the_proposal_is_put_right_the_card_goes_and_the_job_carries_on(make_world):
    world = with_studio_site(make_world())
    installed(world, TESTERSON, supervised=False)
    world.studio.jobs["4242"].proposal = ("Testerson - Primary Closet", "Testerson - Office")
    world.script.add_row("Testerson", "PE")
    world.run_until(lambda: world.cards("design_mismatch"), within=minutes(45))
    world.studio.jobs["4242"].proposal = None
    world.run_until(is_filed(world, "Testerson_101426"), within=minutes(60))
    assert world.cards("design_mismatch") == []
    assert len(world.cards("design_mismatch", state="withdrawn")) == 1
    assert record_of(world, "Testerson_101426")["designs_checked"] is True


@pytest.mark.parametrize("how, dev_code", [("page 503", "studio_unavailable"),
                                           ("no login", "studio_unavailable"),
                                           ("page changed", "studio_page_changed")])
def test_scenario_26_studio_unreachable_still_files_and_says_so(make_world, how, dev_code):
    world = with_studio_site(make_world(), signed_in=how != "no login")
    installed(world, TESTERSON, FAKELEY, supervised=False)
    for studio_id in ("4242", "4343"):
        world.studio.jobs[studio_id].page_status = 503 if how == "page 503" else 200
        world.studio.jobs[studio_id].page_changed = how == "page changed"
    world.script.add_row("Testerson", "PE")
    world.script.add_row("Fakeley", "PE")
    world.run_until(is_filed(world, "Fakeley_101426"), within=minutes(45))
    world.run_until(is_filed(world, "Testerson_101426"), within=minutes(10))
    world.run(minutes=2)

    for folder in ("Testerson_101426", "Fakeley_101426"):
        assert_one_commit(world, folder)
        record = record_of(world, folder)
        assert record["designs"] == 1 and record["designs_checked"] is False
        assert "designs not cross-checked" in log_of(world, folder)
    assert world.cards("design_mismatch", state=None) == []
    assert [d.code for d in world.dev()] == [dev_code]       # one a day, for both jobs
    told = world.outbox(to="barbara", subject="Filed as")
    assert len(told) == 2 and all("1 design," in m.subject for m in told)
    assert world.sign_ins == []


def test_scenario_27_a_job_the_derived_addresses_miss_is_fetched_through_studios_own_link(make_world):
    world = with_studio_site(make_world())
    installed(world, TESTERSON, supervised=False)
    world.studio.jobs["4242"].real_slugs = ("testerson_family_trust", "testerson_jobsite")
    world.script.add_row("Testerson", "PE")
    world.run_until(is_filed(world, "Testerson_101426"), within=minutes(45))
    first, second = world.renders("4242")
    assert "/testerson_family_trust/testerson_jobsite/4242/" in first.url
    assert second.url == first.url
    assert record_of(world, "Testerson_101426")["designs_checked"] is True
    assert world.cards(state=None) == []


@pytest.mark.parametrize("reachable", [True, False], ids=["checked", "not cross-checked"])
def test_scenario_28_the_board_and_the_digest_show_the_counts_and_if_they_were_checked(make_world,
                                                                                       reachable):
    from ngrunner import jobmodel

    world = with_studio_site(make_world())
    installed(world, TESTERSON)
    world.studio.jobs["4242"].page_status = 200 if reachable else 503
    world.script.add_row("Testerson", "PE")
    world.run_until(is_filed(world, "Testerson_101426"), within=minutes(45))
    world.run(minutes=2)

    pages = world.renders("4242")[-1].page_count
    step = world.board_row("Testerson_101426").next_step
    assert step.startswith("Filed") and f"1 design, {pages} pages" in step
    assert ("designs not cross-checked" in step) is not reachable
    line, = [line for line in jobmodel.digest_view(world.store, world.settings, world.clock).jobs
             if line.job == "Testerson_101426"]
    assert f"1 design, {pages} pages" in line.text
    assert ("designs not cross-checked" in line.text) is not reachable
    assert record_of(world, "Testerson_101426")["designs_checked"] is reachable


# ----- scenario 29: going live with jobs the trial run filed aside


def test_scenario_29_going_live_files_what_the_trial_run_filed_aside(make_world):
    world = make_world(mode="shadow")
    installed(world, TESTERSON)
    world.script.add_row("Testerson", "PE")
    world.run_until(row_is(world, "Testerson", "filed"), within=minutes(45))
    world.run(minutes=2)
    assert tree(world.ng_jobs) == {}
    assert world.outbox(to="barbara") == []

    # The switch brings it back at the next pass, and the board says what is true until it lands.
    world.actions.set_mode("live")
    world.settings = replace(world.settings, mode="live")
    world.step()
    row = world.board_row("Testerson_101426")
    assert row.next_step.startswith("Trial run only, not in NG Jobs")
    assert {t.key: t.state for t in row.ticks}["filed"] != "done"

    world.run_until(is_filed(world, "Testerson_101426"), within=minutes(45))
    world.run(minutes=2)
    assert_one_commit(world, "Testerson_101426")
    assert set(tree(world.control / "shadow")) == filed_paths("Testerson_101426")
    assert len(world.outbox(to="barbara", subject="Filed as Testerson_101426")) == 1
    row = world.board_row("Testerson_101426")
    assert row.next_step.startswith("Filed") and "Trial" not in row.next_step
    assert {t.key: t.state for t in row.ticks}["filed"] == "done"


# ----- the nest guard (P1b build spec 6, step 4): real files on a fake clock, the real reader


def post(world: World, folder: str, material: str, name: str, data: bytes) -> None:
    """A program lands in NG Jobs, as a post from Mozaik does."""
    place = world.ng_jobs / folder / material
    place.mkdir(parents=True, exist_ok=True)
    (place / name).write_bytes(data)


def nest_of(world: World, folder: str = GUARD_JOB, material: str = CLASSIC_FOLDER) -> Any:
    return get_nest(world.store, folder, material)


def nest_is(world: World, state: str, folder: str = GUARD_JOB,
            material: str = CLASSIC_FOLDER) -> Callable[[], bool]:
    return lambda: (nest := nest_of(world, folder, material)) is not None and nest.state == state


def guards_dev(world: World) -> set[str]:
    """The DEV items a scenario here is about: a world has no Studio login, and may have no list."""
    return {d.code for d in world.dev()} - {"setup", "job_list", "job_list_stale", "studio_unavailable"}


def to_george(world: World, subject: str = "") -> list[Any]:
    return world.outbox(to="george", subject=subject or None)


def sheet_cards(world: World) -> list[Any]:
    return world.cards("sheet_size")


def test_scenario_30_a_first_scan_alarms_nothing_whatever_is_already_there(make_world):
    world = make_world()
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS02.TAP", ON_61)
    post(world, "Fakeley_101426", CLASSIC_FOLDER, "FAKS01.TAP", BOTH)
    installed(world)
    world.run(minutes=5)

    assert world.tray.colour != "red" and world.tray_state().colour != "red"
    assert to_george(world) == [] and sheet_cards(world) == [] and guards_dev(world) == set()
    assert {p.state for p in list_programs(world.store)} == {"seen_at_install"}
    nest = nest_of(world)
    assert nest.state == "not_checked" and nest.posted_at is None
    assert nest.reasons == (templates.POSTED_BEFORE,)
    assert not [line for line in jobmodel.digest_view(world.store, world.settings,
                                                      world.clock).owner
                if "Not checked" in line or "DO NOT CUT" in line]
    assert world.board().jobs == ()                 # an old folder with no job doesn't crowd it


def test_scenario_31_an_upgrade_from_before_the_guards_alarms_nothing_and_keeps_what_was_there(
        make_world):
    """The live shadow run's database goes through migration 4 and then this very first scan."""
    world = make_world()
    assert migrate(Store(world.paths.db), MIGRATIONS[:3]) == 3
    old = Store(world.paths.db)
    ensure_job(old, "4242", "Testerson_101426", now=START, designer="PE")
    set_state(old, "installed_at", "kept")
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    post(world, "Fakeley_101426", CLASSIC_FOLDER, "FAKS01.TAP", ON_61)
    installed(world)
    world.run(minutes=5)

    assert Store(world.paths.db).schema_version() == LATEST
    assert list((world.paths.backup).rglob("ngrunner-v3.db"))            # copied before the upgrade
    assert get_state(world.store, "installed_at") == "kept" and world.job("4242") is not None
    assert world.tray.colour != "red" and to_george(world) == [] and sheet_cards(world) == []
    assert guards_dev(world) == set()
    assert nest_of(world).state == "not_checked" and nest_of(world).posted_at is None
    assert nest_of(world).studio_id == "4242"       # its job is known, so its row will show it


def test_scenario_32_a_doubled_program_is_red_in_two_minutes_with_one_email_to_george(make_world):
    world = make_world()
    installed(world)
    world.run(minutes=1)

    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))

    assert nest_of(world).reasons == ("8 cam holes are doubled in program TESS01",)
    assert world.tray.colour == "red" and "DO NOT CUT" in world.tray.tooltip
    told = to_george(world, "DO NOT CUT")
    assert len(told) == 1 and told[0].urgent
    assert told[0].lines == ("1. Testerson_101426: DO NOT CUT 3/4 Egger Classic White (trial). 8 cam"
                             " holes are doubled in program TESS01.",)
    assert [toast.kind for toast in world.toasts.shown] == ["do_not_cut"]

    # On the board, on Your list and on the digest's first line, with a free row for its folder.
    row, = world.board().jobs
    assert row.name == GUARD_JOB and row.studio_id is None
    assert {t.key: t.state for t in row.ticks}["nest"] == "blocked"
    assert [(m.material, m.state) for m in row.materials] == [(CLASSIC_FOLDER, "do_not_cut")]
    assert row.materials[0].note == "8 cam holes are doubled in program TESS01"
    assert any("DO NOT CUT" in item.text for item in world.board().your_list)
    view = jobmodel.digest_view(world.store, world.settings, world.clock)
    assert view.owner[0] == ("DO NOT CUT Testerson_101426 3/4 Egger Classic White (trial): 8 cam"
                             " holes are doubled in program TESS01")

    # Nothing new, nothing more.
    world.run(minutes=30)
    assert len(to_george(world, "DO NOT CUT")) == 1 and len(world.toasts.shown) == 1
    assert sheet_cards(world) == []                 # doubled holes have no card: nothing clears them


def test_scenario_32b_two_materials_in_one_pass_are_two_messages_never_one_in_the_others_place(
        make_world):
    world = make_world()
    installed(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    post(world, GUARD_JOB, "1-4 Egger Classic White", "TESS02.TAP", DOUBLED)
    post(world, "Fakeley_101426", CLASSIC_FOLDER, "FAKS01.TAP", DOUBLED)
    world.run(minutes=2)

    told = to_george(world, "DO NOT CUT")
    assert sorted(m.subject for m in told) == [
        "DO NOT CUT · Fakeley_101426 · 3/4 Egger Classic White",
        "DO NOT CUT · Testerson_101426 · 1/4 Egger Classic White",
        "DO NOT CUT · Testerson_101426 · 3/4 Egger Classic White"]


def test_scenario_33_a_half_written_file_waits_then_acts_once_whole(make_world):
    world = make_world()
    installed(world)
    world.run(minutes=1)

    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED[:len(DOUBLED) // 2])
    world.step()
    world.step()
    assert world.tray.colour != "red" and to_george(world) == []
    assert nest_of(world) is None or nest_of(world).state != "do_not_cut"

    # Still growing: it never settles, so it isn't even read.
    grown = bytearray(DOUBLED[:len(DOUBLED) // 2])
    for extra in range(4):
        grown += b"G0 Z1.5100\r\n"
        post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", bytes(grown))
        world.step()
    assert to_george(world) == []

    # It stops, not whole: it is re-read, and after 10 minutes Sulaiman hears.
    world.run(minutes=5)
    assert to_george(world) == [] and guards_dev(world) == set()
    world.run(minutes=8)
    assert to_george(world) == []
    assert guards_dev(world) == {"program_unfinished"}
    assert world.tray.colour != "red"
    assert "could not read" in nest_of(world).reasons[0].casefold()

    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))
    assert len(to_george(world, "DO NOT CUT")) == 1
    assert guards_dev(world) == set()


def test_scenario_33b_a_program_acts_only_once_two_polls_agree_on_it(make_world):
    world = make_world()
    installed(world)
    world.run(minutes=1)

    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    world.step()                                      # the first poll sees it, and reads nothing
    assert get_program(world.store, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP").state == "new"
    assert nest_of(world) is None

    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", program(doubled=3))    # whole, and changed
    world.step()                                      # no two polls agree yet
    assert nest_of(world) is None and to_george(world) == []

    world.step()                                      # now they do
    assert nest_of(world).reasons == ("3 cam holes are doubled in program TESS01",)


def test_scenario_34_a_repost_with_the_fault_gone_returns_to_not_checked_and_george_is_told(
        make_world):
    world = make_world()
    installed(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))
    world.step()

    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    world.run_until(nest_is(world, "not_checked"), within=minutes(2))

    assert "0 doubled holes" in nest_of(world).reasons[0]
    assert "the nest itself is not checked" in nest_of(world).reasons[0]
    assert nest_of(world).posted_at is not None
    told = to_george(world, "No longer DO NOT CUT")
    assert len(told) == 1 and "NOT CHECKED" in told[0].lines[0]
    assert world.tray.colour != "red"
    assert not [item for item in world.board().your_list if "DO NOT CUT" in item.text]


def test_scenario_35_jims_clearance_closes_the_card_and_a_repost_asks_again(make_world):
    world = make_world()
    installed(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS07.TAP", ON_61)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))

    assert nest_of(world).reasons == ("A program is on 61x109 and this colour is 49x97",)
    card, = sheet_cards(world)
    assert card.choices == templates.SHEET_SIZE_CHOICES and card.studio_id is None
    assert "Program TESS07 of 3/4 Egger Classic White is on 61x109" in card.text
    assert any(c.id == card.id for c in world.board().cards)     # no job, no row, and still shown

    # A click, through the same way in as the board's button.
    assert world.actions.answer(card.id, templates.SHEET_SIZE_CHOICES[0]).startswith("Saved")
    world.run_until(nest_is(world, "not_checked"), within=minutes(2))

    assert sheet_cards(world) == [] and world.tray.colour != "red"
    sha = hashlib.sha256(ON_61).hexdigest()
    assert [(c.program_sha256, c.what, c.card_id) for c in list_clearances(world.store)] == [
        (sha, "sheet_size", card.id)]
    told = to_george(world, "Cleared by Jim")
    assert len(told) == 1 and "clears the sheet size only" in told[0].lines[0]
    assert to_george(world, "No longer DO NOT CUT") == []        # said already, by Cleared by Jim
    assert "1 cleared past 49x97" in nest_of(world).reasons[0]

    # The same bytes posted again change nothing, as the clearance names them; others ask again.
    world.run(minutes=5)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS07.TAP", ON_61)
    os.utime(world.ng_jobs / GUARD_JOB / CLASSIC_FOLDER / "TESS07.TAP", (1_800_000_000, 1_800_000_000))
    world.run(minutes=3)
    assert sheet_cards(world) == [] and nest_of(world).state == "not_checked"
    assert len(to_george(world, "DO NOT CUT")) == 1
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS07.TAP", program(reach=(60.75, 108.75), mark=1))
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))
    again, = sheet_cards(world)
    assert again.id != card.id and world.tray.colour == "red"
    assert len(to_george(world, "DO NOT CUT")) == 2


def test_scenario_36_seven_programs_past_their_size_are_one_card_with_a_clearance_each(make_world):
    world = make_world()
    installed(world)
    world.run(minutes=1)
    datas = {f"TESS0{n}.TAP": program(reach=(60.75, 108.75), mark=n) for n in range(1, 8)}
    for name, data in datas.items():
        post(world, GUARD_JOB, CLASSIC_FOLDER, name, data)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))

    card, = sheet_cards(world)
    assert "7 programs of 3/4 Egger Classic White, programs TESS01-07, are on 61x109" in card.text

    # An eighth while it is open: one card still, saying what stands now.
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS08.TAP", program(reach=(60.75, 108.75), mark=8))
    world.run(minutes=2)
    card, = sheet_cards(world)
    assert "8 programs" in card.text and "TESS01-08" in card.text
    datas["TESS08.TAP"] = program(reach=(60.75, 108.75), mark=8)

    answer(world, card, templates.SHEET_SIZE_CHOICES[0])
    world.run_until(nest_is(world, "not_checked"), within=minutes(2))
    assert {c.program_sha256 for c in list_clearances(world.store)} == {
        hashlib.sha256(data).hexdigest() for data in datas.values()}
    assert len(list_clearances(world.store)) == 8 and sheet_cards(world) == []

    # A ninth names only itself.
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS09.TAP", program(reach=(60.75, 108.75), mark=9))
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))
    card, = sheet_cards(world)
    assert "Program TESS09 of" in card.text


def test_scenario_37_both_faults_say_both_and_clearing_the_size_leaves_the_doubled_holes(make_world):
    world = make_world()
    installed(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", BOTH)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))

    assert nest_of(world).reasons == ("2 cam holes are doubled in program TESS01",
                                      "A program is on 61x109 and this colour is 49x97")
    george, = to_george(world, "DO NOT CUT")
    assert len(george.lines) == 2
    card, = sheet_cards(world)

    answer(world, card, templates.SHEET_SIZE_CHOICES[0])
    world.run(minutes=3)
    assert nest_of(world).state == "do_not_cut"
    assert nest_of(world).reasons == ("2 cam holes are doubled in program TESS01",)
    assert sheet_cards(world) == [] and world.tray.colour == "red"
    assert len(to_george(world, "Cleared by Jim")) == 1
    assert len(to_george(world, "DO NOT CUT")) == 1          # nothing new is wrong
    assert to_george(world, "No longer DO NOT CUT") == []


def test_scenario_38_re_nest_it_stays_do_not_cut_and_doesnt_ask_again_until_a_repost(make_world):
    world = make_world()
    installed(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS07.TAP", ON_61)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))
    card, = sheet_cards(world)

    answer(world, card, templates.SHEET_SIZE_CHOICES[1])
    world.run(minutes=5)

    assert nest_of(world).state == "do_not_cut" and world.tray.colour == "red"
    assert sheet_cards(world) == [] and list_clearances(world.store) == []
    assert to_george(world, "Cleared by Jim") == []
    assert guards_dev(world) == set()                # acted on, and nothing refused along the way

    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS07.TAP", CLEAN)
    world.run_until(nest_is(world, "not_checked"), within=minutes(2))
    assert len(to_george(world, "No longer DO NOT CUT")) == 1


def test_scenario_39_a_pause_stops_no_guard_since_a_guard_only_reads(make_world):
    world = make_world()
    installed(world)
    world.actions.pause()
    world.run(minutes=1)

    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))

    assert len(to_george(world, "DO NOT CUT")) == 1
    assert world.tray.colour == "red"                # grey must not hide a DO NOT CUT
    assert tree(world.ng_jobs).keys() == {f"{GUARD_JOB}/{CLASSIC_FOLDER}/TESS01.TAP"}


def test_scenario_40_shadow_mode_tells_only_sulaiman_marked_and_shows_no_toast(make_world):
    world = make_world(mode="shadow")
    installed(world)
    world.run(minutes=1)

    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", BOTH)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))
    world.step()

    assert to_george(world) == [] and world.outbox(to="jim") == []
    assert len(world.outbox(to="sulaiman", subject="SHADOW DO NOT CUT")) == 1
    assert {s.to for s in world.script.sent} == {"sulaiman"}
    assert world.toasts.shown == []
    assert world.board().shadow and world.tray.tooltip.startswith("SHADOW")
    assert world.tray.colour == "red"

    card, = sheet_cards(world)
    answer(world, card, templates.SHEET_SIZE_CHOICES[0])
    world.run(minutes=3)
    assert to_george(world) == []
    assert len(world.outbox(to="sulaiman", subject="SHADOW Cleared by Jim")) == 1


def test_scenario_41_a_card_for_a_folder_with_no_job_works_through_the_command_box(make_world):
    world = make_world()
    installed(world, TESTERSON)
    world.run(minutes=1)
    post(world, "Fakeley_101426", CLASSIC_FOLDER, "FAKS01.TAP", ON_61)
    world.run_until(nest_is(world, "do_not_cut", folder="Fakeley_101426"), within=minutes(2))
    card, = sheet_cards(world)
    assert card.studio_id is None and card.fab_row_id is None

    # A card the store allows about nothing at all can't be shown, and must cost only itself.
    add_card(world.store, prefix="NON", kind="sheet_size", text="About nothing.",
             choices=templates.SHEET_SIZE_CHOICES, created_at=START)
    assert world.actions.command("pause").startswith("Paused")
    world.actions.resume()
    reply = world.actions.command(f"{card.id} 1")
    assert reply.startswith("Saved"), reply
    world.run_until(nest_is(world, "not_checked", folder="Fakeley_101426"), within=minutes(2))
    assert card.id not in {c.id for c in world.board().cards}
    assert guards_dev(world) == {"card_unshowable"}     # said once, to Sulaiman


def test_scenario_42_a_guard_that_fails_stops_no_filing(make_world, monkeypatch):
    world = make_world()
    installed(world, TESTERSON)

    def broken(plan: Any) -> Any:
        raise RuntimeError("a guard fell over")

    monkeypatch.setattr(watch, "scan", broken)
    world.script.add_row("Testerson", "PE")
    world.run_until(is_filed(world, "Testerson_101426"), within=minutes(45))

    assert guards_dev(world) == {"guard_failed"}
    assert "a guard fell over" not in " ".join(d.text for d in world.dev())
    monkeypatch.undo()
    world.step()
    assert guards_dev(world) == set()                # the next clean pass closes it


def test_scenario_42b_one_material_that_fails_costs_only_itself(make_world, monkeypatch):
    world = make_world()
    installed(world)
    world.run(minutes=1)
    real = watch._one_material

    def broken_for_one(ctx: Any, plan: Any, found: Any, place: Any, *rest: Any) -> None:
        if place[0] == "Fakeley_101426":
            raise RuntimeError("one bad material")
        real(ctx, plan, found, place, *rest)

    monkeypatch.setattr(watch, "_one_material", broken_for_one)
    post(world, "Fakeley_101426", CLASSIC_FOLDER, "FAKS01.TAP", DOUBLED)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    world.run(minutes=3)

    assert nest_of(world).state == "do_not_cut"            # the other one is still judged
    assert guards_dev(world) == {"guard_failed"}
    item = next(d for d in world.dev() if d.code == "guard_failed")
    world.run(minutes=3)
    assert next(d for d in world.dev() if d.code == "guard_failed").id == item.id    # one, standing
    monkeypatch.undo()
    world.run(minutes=3)
    assert guards_dev(world) == set()
    assert nest_of(world, "Fakeley_101426").state == "do_not_cut"


def test_scenario_42c_a_tracker_that_fails_for_any_reason_costs_no_verdict(make_world, monkeypatch):
    world = make_world(tracker=True)
    installed(world, TESTERSON)
    world.run(minutes=1)

    def broken(folder: Any) -> Any:
        raise RuntimeError("something nobody thought of")

    monkeypatch.setattr(guards_scan_module, "read_tracker", broken)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))

    assert len(to_george(world, "DO NOT CUT")) == 1
    world.step()                                      # with a real post, the pre-flight is due
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS02.TAP", program(doubled=2))
    world.run(minutes=2)
    assert "10 cam holes are doubled" in " ".join(nest_of(world).reasons)     # still judging
    assert "guard_failed" in guards_dev(world)
    assert "nobody thought" not in " ".join(d.text for d in world.dev())
    monkeypatch.undo()
    world.run(minutes=2)
    assert "guard_failed" not in guards_dev(world)


def test_scenario_42d_a_guard_tick_that_fails_outright_stops_no_filing(make_world, monkeypatch):
    world = make_world()
    installed(world, TESTERSON)

    def broken(ctx: Any) -> Any:
        raise RuntimeError("a guard fell over before it read anything")

    monkeypatch.setattr(watch, "_plan", broken)
    world.script.add_row("Testerson", "PE")
    world.run_until(is_filed(world, "Testerson_101426"), within=minutes(45))

    assert any(d.code == "task_failed" and d.text.startswith("The guards failed")
               for d in world.dev())
    assert world.heartbeats()                        # and the rest of the schedule goes on
    monkeypatch.undo()
    world.run(minutes=2)
    assert not any(d.code == "task_failed" for d in world.dev())


def test_scenario_43_a_guard_read_that_hangs_holds_up_no_filing(make_world, monkeypatch):
    world = make_world()
    installed(world, TESTERSON)
    release = threading.Event()
    real_scan = watch.scan

    def stuck(plan: Any) -> Any:
        release.wait(60)
        return real_scan(plan)

    monkeypatch.setattr(watch, "scan", stuck)
    world.script.add_row("Testerson", "PE")
    try:
        for _ in range(60):                          # step(), waiting only for the fetches
            world.clock.advance(60)
            world.render.on_clock()
            world.script.on_clock()
            began = monotonic()
            world.sup.run_due(world.clock.now())
            assert monotonic() - began < 20, "a pass of the schedule waited on the guards' read"
            wait_for_fetches(guards=False)
            world.script.deliver()
            if is_filed(world, "Testerson_101426")():
                break
        assert is_filed(world, "Testerson_101426")(), world.where_things_stand()
        assert any(t.name.startswith("ngrunner-guards-") for t in threading.enumerate())
    finally:
        release.set()
    wait_for_fetches()


def test_scenario_44_a_read_stops_between_files_on_a_stop_request_and_concludes_nothing(tmp_path):
    root = tmp_path / "NG Jobs"
    for n in range(3):
        (root / f"Testerson_10142{n}" / CLASSIC_FOLDER).mkdir(parents=True)
    asked = {"count": 0}

    def stop() -> bool:
        asked["count"] += 1
        return asked["count"] > 1

    found = guard_scan(Plan(ng_jobs=root, stop=stop))
    assert found.stopped and not found.is_whole()
    assert asked["count"] == 2


def test_scenario_44b_a_file_not_the_size_its_listing_said_has_not_settled(tmp_path, monkeypatch):
    place = tmp_path / "NG Jobs" / GUARD_JOB / CLASSIC_FOLDER
    place.mkdir(parents=True)
    (place / "TESS01.TAP").write_bytes(DOUBLED)
    first = guard_scan(Plan(ng_jobs=tmp_path / "NG Jobs", first_scan=True))
    listed = first.listed((GUARD_JOB, CLASSIC_FOLDER))["TESS01.TAP"]
    row = ProgramRecord(GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", None, listed.size, listed.mtime, None,
                        None, None, None, START, "new")
    monkeypatch.setattr(guards_scan_module, "read_shared", lambda path: DOUBLED[:100])

    found = guard_scan(Plan(ng_jobs=tmp_path / "NG Jobs",
                            known={(GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP"): row}))
    assert found.taps == {} and found.unread == {(GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP")}


def test_scenario_45_a_posted_not_checked_changes_no_colour_until_a_toolkit_is_adopted(make_world):
    world = make_world()
    installed(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    world.run_until(nest_is(world, "not_checked"), within=minutes(2))

    assert nest_of(world).posted_at is not None
    assert world.tray_state().colour == "green"
    row, = world.board().jobs                         # still on the board
    assert row.materials[0].state == "not_checked"
    view = jobmodel.digest_view(world.store, world.settings, world.clock)
    risk = next(line for line in view.owner if line.startswith("Not checked"))   # and in the digest
    assert GUARD_JOB in risk and "3/4 Egger Classic White" in risk
    world.run(minutes=6)
    assert any(line.startswith("Not checked") for line in
               world.heartbeats()[-1].payload["open_risks"])    # for the watchdog, as it stands

    add_snapshot(world.store, taken_at=world.clock.now(), sha256="a" * 64, adopted=False)
    assert world.tray_state().colour == "green"       # taken isn't adopted
    add_snapshot(world.store, taken_at=world.clock.now(), sha256="b" * 64, adopted=True)
    assert world.tray_state().colour == "red"
    assert "NOT CHECKED" in world.tray_state().tooltip


def test_scenario_46_verdicts_lose_trial_after_five_real_nests_but_a_new_feature_stays_on_trial(
        make_world):
    world = make_world()
    installed(world)
    world.run(minutes=1)
    for n in range(5):
        post(world, f"Sampleton_10142{n}", CLASSIC_FOLDER, f"SAMS0{n}.TAP", CLEAN)
    world.run(minutes=2)
    assert all(nest.trial for nest in list_nests(world.store))

    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))
    assert nest_of(world).trial is False
    assert "(trial)" not in to_george(world, "DO NOT CUT")[0].lines[0]

    post(world, "Fakeley_101426_Gar", CLASSIC_FOLDER, "FAKS01.TAP", DOUBLED)
    world.run_until(nest_is(world, "do_not_cut", folder="Fakeley_101426_Gar"), within=minutes(2))
    assert nest_of(world, "Fakeley_101426_Gar").trial is True
    post(world, "Mockford_101426_Gar", CLASSIC_FOLDER, "MOCS01.TAP", DOUBLED)
    world.run_until(nest_is(world, "do_not_cut", folder="Mockford_101426_Gar"), within=minutes(2))
    assert nest_of(world, "Mockford_101426_Gar").trial is False


def test_scenario_47_a_program_gone_then_back_unchanged_is_no_new_post(make_world):
    world = make_world()
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    installed(world)
    world.run(minutes=1)
    held = tmp_copy = world.tmp / "aside.TAP"
    place = world.ng_jobs / GUARD_JOB / CLASSIC_FOLDER / "TESS01.TAP"
    os.replace(place, held)
    world.run(minutes=4)
    assert nest_of(world).state == "not_posted"
    os.replace(tmp_copy, place)
    world.run(minutes=4)
    assert nest_of(world).state == "not_checked" and nest_of(world).posted_at is None
    assert to_george(world) == [] and world.tray.colour != "red"


def test_scenario_47b_an_old_program_beside_a_new_one_alarms_nothing_and_a_new_fault_names_only_it(
        make_world):
    world = make_world()
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    installed(world)
    world.run(minutes=1)

    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS02.TAP", CLEAN)
    world.run_until(lambda: nest_of(world).posted_at is not None, within=minutes(2))
    assert nest_of(world).state == "not_checked" and to_george(world) == []
    old = get_program(world.store, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP")
    assert old.state == "seen_at_install" and old.sha256 == hashlib.sha256(DOUBLED).hexdigest()

    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS03.TAP", program(doubled=2))
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))
    assert nest_of(world).reasons == ("2 cam holes are doubled in program TESS03",)


def test_scenario_48_a_folder_moved_to_old_stops_being_watched_and_clears_its_red(make_world):
    world = make_world()
    installed(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", ON_61)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))
    assert len(sheet_cards(world)) == 1

    os.replace(world.ng_jobs / GUARD_JOB, world.old / GUARD_JOB)
    world.run(minutes=4)

    assert nest_of(world).state == "not_posted" and world.tray.colour != "red"
    assert sheet_cards(world) == [] and world.board().jobs == ()


def test_scenario_49_a_listing_that_loses_most_folders_at_once_is_not_believed(make_world, monkeypatch):
    world = make_world()
    for n in range(12):
        post(world, f"Sampleton_10142{n:02d}"[:17], CLASSIC_FOLDER, "SAMS01.TAP", CLEAN)
    installed(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))

    # The drive starts up and shows an empty folder for a while.
    aside = world.tmp / "aside"
    os.replace(world.ng_jobs, aside)
    world.ng_jobs.mkdir()
    world.run(minutes=20)
    assert nest_of(world).state == "do_not_cut" and world.tray.colour == "red"
    assert "ng_jobs_looks_empty" in {d.code for d in world.dev()}

    shutil.rmtree(world.ng_jobs)
    os.replace(aside, world.ng_jobs)
    world.run(minutes=3)
    assert nest_of(world).state == "do_not_cut"
    assert len(to_george(world, "DO NOT CUT")) == 1      # nothing was forgotten, so nothing is new
    assert "ng_jobs_looks_empty" not in {d.code for d in world.dev()}


def test_scenario_49b_many_folders_really_moved_to_old_are_believed_after_two_hours(make_world):
    world = make_world()
    for n in range(12):
        post(world, f"Sampleton_1014{n:02d}", CLASSIC_FOLDER, "SAMS01.TAP", CLEAN)
    installed(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))

    os.replace(world.ng_jobs / GUARD_JOB, world.old / GUARD_JOB)
    for n in range(12):
        os.replace(world.ng_jobs / f"Sampleton_1014{n:02d}", world.old / f"Sampleton_1014{n:02d}")
    world.run(minutes=100)
    assert nest_of(world).state == "do_not_cut"       # not yet
    world.run(minutes=25)

    assert nest_of(world).state == "not_posted" and world.tray.colour != "red"
    assert "ng_jobs_looks_empty" not in {d.code for d in world.dev()}


def test_scenario_50_a_first_scan_waits_for_a_listing_of_everything(make_world, monkeypatch):
    world = make_world()
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    post(world, "Fakeley_101426", CLASSIC_FOLDER, "FAKS01.TAP", DOUBLED)
    real = guards_scan_module._list_folder

    def one_folder_fails(path: Any, folder: str, found: Any) -> None:
        if folder == GUARD_JOB:
            raise OSError("the drive said no")
        real(path, folder, found)

    monkeypatch.setattr(guards_scan_module, "_list_folder", one_folder_fails)
    installed(world)
    world.run(minutes=3)
    assert get_state(world.store, watch.FIRST_SCAN) is None and list_programs(world.store) == []
    world.run(minutes=9)
    assert "first_scan_waiting" in {d.code for d in world.dev()}

    monkeypatch.undo()
    world.run(minutes=3)
    assert get_state(world.store, watch.FIRST_SCAN) is not None
    assert {p.state for p in list_programs(world.store)} == {"seen_at_install"}
    assert to_george(world) == [] and world.tray.colour != "red" and guards_dev(world) == set()


def test_scenario_51_ng_jobs_out_of_reach_uses_the_alarm_it_has_and_alarms_nothing_else(make_world):
    world = make_world()
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    installed(world)
    world.run(minutes=1)
    aside = world.tmp / "aside"
    os.replace(world.ng_jobs, aside)
    world.run(minutes=3)
    assert "ng_jobs_unreachable" in {d.code for d in world.dev()}
    assert to_george(world) == [] and nest_of(world).state == "not_checked"
    os.replace(aside, world.ng_jobs)
    world.run(minutes=3)
    assert "ng_jobs_unreachable" not in {d.code for d in world.dev()}
    assert to_george(world) == [] and nest_of(world).posted_at is None


# ----- the order heads-up


def post_takeoff(world: World, folder: str, data: dict[str, Any] | bytes) -> None:
    place = world.ng_jobs / folder
    place.mkdir(parents=True, exist_ok=True)
    body = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
    (place / f"{folder}_IC_Conversion.json").write_bytes(body)


def heads_ups(world: World, to: str | None = None) -> list[Any]:
    return [m for m in world.outbox(to=to) if "order heads-up" in m.subject]


def test_scenario_52_a_shadow_run_sends_each_takeoff_once_to_sulaiman_and_a_change_says_revised(
        make_world):
    world = make_world(mode="shadow")
    installed(world)
    world.run(minutes=1)
    post_takeoff(world, GUARD_JOB, takeoff_json())
    world.run(minutes=2)

    told = heads_ups(world, "sulaiman")
    assert len(told) == 1 and told[0].subject.startswith("SHADOW ")
    assert "revised" not in told[0].subject
    assert "Testerson Lane" not in " ".join(told[0].lines)   # the address stays in the file
    assert world.outbox(to="barbara") == [] and world.outbox(to="will") == []

    # The same bytes synced again send nothing, and aren't taken for a new heads-up.
    went = get_heads_up(world.store, GUARD_JOB).sent_at
    post_takeoff(world, GUARD_JOB, takeoff_json())
    world.run(minutes=3)
    assert len(heads_ups(world, "sulaiman")) == 1
    assert get_heads_up(world.store, GUARD_JOB).sent_at == went

    post_takeoff(world, GUARD_JOB, takeoff_json(estimate={"total": 26000.0}))
    world.run(minutes=2)
    told = heads_ups(world, "sulaiman")
    assert len(told) == 2 and "revised" in told[1].subject
    assert any("$26,000.00" in line for line in told[1].lines)


def test_scenario_53_nothing_reaches_barbara_or_will_until_it_is_turned_on(make_world):
    world = make_world()
    installed(world)
    world.run(minutes=1)
    post_takeoff(world, GUARD_JOB, takeoff_json())
    world.run(minutes=3)
    assert heads_ups(world) == []
    assert get_heads_up(world.store, GUARD_JOB) is not None    # noted, so turning it on sends no pile


def test_scenario_54_turned_on_each_takeoff_goes_to_barbara_and_will_once(make_world):
    world = make_world(order_flags_on=True)
    post_takeoff(world, "Fakeley_101426", takeoff_json())        # there at install: never sent
    installed(world)
    world.run(minutes=1)
    post_takeoff(world, GUARD_JOB, takeoff_json())
    world.run(minutes=3)
    assert len(heads_ups(world, "barbara")) == 1 and len(heads_ups(world, "will")) == 1
    assert all(GUARD_JOB in m.subject for m in heads_ups(world))
    assert to_george(world) == [] and world.outbox(to="jim") == []


def test_scenario_55_a_takeoff_that_wont_read_is_one_dev_item_a_board_note_and_no_list(make_world):
    world = make_world(mode="shadow")
    installed(world)
    world.run(minutes=1)
    post_takeoff(world, GUARD_JOB, takeoff_json(buckets={"0.75": {"sheets": 3}}))
    world.run(minutes=3)

    assert heads_ups(world) == []
    assert guards_dev(world) == {"takeoff_unreadable"}
    row, = world.board().jobs
    assert row.notes == (headsup.UNREADABLE_NOTE,)
    world.run(minutes=5)
    assert [d.code for d in world.dev()].count("takeoff_unreadable") == 1    # not one a minute

    post_takeoff(world, GUARD_JOB, takeoff_json())
    world.run(minutes=3)
    assert len(heads_ups(world, "sulaiman")) == 1 and guards_dev(world) == set()
    assert world.board().jobs == ()


# ----- <folder>_NGRunner.json (schema 2 brought the nests and the pre-flight; 3 the takeoff)


def test_scenario_56_a_filed_jobs_notes_carry_its_nests_and_pre_flight(make_world):
    world = make_world(tracker=True)
    installed(world, TESTERSON)
    world.script.add_row("Testerson", "PE")
    world.run_until(is_filed(world, "Testerson_101426"), within=minutes(45))
    write_snapshot(world.tracker, taken=to_iso(world.clock.now()).replace("+00:00", "Z"))
    post(world, "Testerson_101426", CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))
    world.run(minutes=16)

    record = record_of(world, "Testerson_101426")
    assert record["schema"] == 3
    nest, = record["nests"]
    assert (nest["material"], nest["state"], nest["basis"], nest["programs"]) == (
        CLASSIC_FOLDER, "do_not_cut", "programs", ["TESS01.TAP"])
    assert from_iso(nest["at"])
    assert record["preflight"]["at"]
    assert {"code": "no_check", "text": record["preflight"]["items"][-1]["text"]} in (
        record["preflight"]["items"]) or record["preflight"]["items"]

    # On the board too, and the row names the job, not a free folder.
    row = world.board_row("Testerson_101426")
    assert [m.state for m in row.materials] == ["do_not_cut"]
    assert row.lock and all(isinstance(line, str) for line in row.lock)

    # A time alone is no reason to write: a fresh pre-flight, or a verdict made afresh.
    before = tree(world.ng_jobs)["Testerson_101426/Testerson_101426_NGRunner.json"]
    ask_for_redo(world.store, "Testerson_101426", CLASSIC_FOLDER)
    write_snapshot(world.tracker, taken=to_iso(world.clock.now()).replace("+00:00", "Z"))
    world.run(minutes=16)
    assert tree(world.ng_jobs)["Testerson_101426/Testerson_101426_NGRunner.json"] == before

    # What changes reaches the file.
    post(world, "Testerson_101426", CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    world.run_until(nest_is(world, "not_checked"), within=minutes(2))
    world.step()
    assert record_of(world, "Testerson_101426")["nests"][0]["state"] == "not_checked"


def test_scenario_57_an_observed_job_gets_no_writes_and_its_guards_show_on_the_board(make_world):
    world = make_world()
    world.make_folder("Testerson_101426")
    post(world, "Testerson_101426", CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    installed(world, TESTERSON)
    world.script.add_row("Testerson_101426", "PE")
    world.run_until(lambda: world.job("4242") is not None and world.job("4242").mode == "observed",
                    within=minutes(30))
    before = tree(world.ng_jobs)
    post(world, "Testerson_101426", CLASSIC_FOLDER, "TESS02.TAP", DOUBLED)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))
    world.run(minutes=3)

    after = tree(world.ng_jobs)
    assert set(after) - set(before) == {f"Testerson_101426/{CLASSIC_FOLDER}/TESS02.TAP"}
    assert [m.state for m in world.board_row("Testerson_101426").materials] == ["do_not_cut"]
    assert len(to_george(world, "DO NOT CUT")) == 1


def test_scenario_57b_an_observed_job_marked_filed_still_gets_no_notes_for_its_guards(make_world):
    world = make_world()
    opps(world, FAKELEY)
    world.make_folder("Fakeley_101426", summary=world.studio.report("4343", note=99_000))
    installed(world)
    world.script.add_row("Fakeley_101426", "PE")
    world.run_until(row_is(world, "Fakeley_101426", "filed"), within=minutes(45))
    assert world.job("4343").mode == "observed"
    before = tree(world.ng_jobs)

    post(world, "Fakeley_101426", CLASSIC_FOLDER, "FAKS01.TAP", DOUBLED)
    world.run_until(nest_is(world, "do_not_cut", folder="Fakeley_101426"), within=minutes(2))
    world.run(minutes=3)

    assert set(tree(world.ng_jobs)) - set(before) == {
        f"Fakeley_101426/{CLASSIC_FOLDER}/FAKS01.TAP"}
    assert world.events(kind="notes_gone") == []     # it never even tried
    assert [m.state for m in world.board_row("Fakeley_101426").materials] == ["do_not_cut"]


def test_scenario_58_a_tracker_it_cant_read_says_so_and_stops_nothing(make_world):
    world = make_world(tracker=True)
    installed(world, TESTERSON)
    world.script.add_row("Testerson", "PE")
    world.run_until(is_filed(world, "Testerson_101426"), within=minutes(45))
    world.run(minutes=16)

    assert "could not" in " ".join(world.board_row("Testerson_101426").lock).casefold()
    record = record_of(world, "Testerson_101426")
    assert [item["code"] for item in record["preflight"]["items"]] == ["tracker_unreadable"]
    world.run(minutes=61)
    assert "tracker_unreadable" in {d.code for d in world.dev()}
    write_snapshot(world.tracker, taken=to_iso(world.clock.now()).replace("+00:00", "Z"))
    world.run(minutes=16)
    assert "tracker_unreadable" not in {d.code for d in world.dev()}


def test_scenario_59_labels_that_dont_fit_read_could_not_verify_and_alarm_nobody(make_world):
    world = make_world(tracker=True)
    installed(world)
    world.run(minutes=1)
    write_snapshot(world.tracker, taken=to_iso(world.clock.now()).replace("+00:00", "Z"))
    (world.tracker / "parts_catalog.csv").write_text(
        "Job,Room,RoomName,Part,Product,W,H,D,Wall,Pieces,JobModified\n"
        f"{GUARD_JOB},1,Room,R1C3,Tower,635,96,523,1_1,FEnd (L)~484.2~2132~1,2026-10-20\n",
        encoding="utf-8")
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    world.run_until(nest_is(world, "not_checked"), within=minutes(2))
    label = pdf(label_page(part="R1C9"))                 # a part the catalog doesn't have
    (world.ng_jobs / GUARD_JOB / f"{GUARD_JOB} Labels.pdf").write_bytes(label)
    world.run(minutes=20)

    row, = world.board().jobs
    assert any("parts catalog" in line for line in row.lock)
    assert any("could not verify" in reason.casefold() and "parts catalog" in reason
               for reason in nest_of(world).reasons)
    assert nest_of(world).state == "not_checked" and to_george(world) == []
    assert world.tray.colour != "red"


# ----- the same paths on real programs, named by case and never printed (build spec 11)


def _real_programs(**match: str) -> list[tuple[dict[str, Any], bytes]]:
    index = FIXTURES / "p1b" / "taps" / "index.json"
    if not index.exists():
        pytest.skip("no real programs on this PC")
    entries = json.loads(index.read_text(encoding="utf-8"))
    return [(entry, (index.parent / entry["file"]).read_bytes()) for entry in entries
            if all(entry[field] == value for field, value in match.items())]


def _post_real(world: World, material: str, programs: list[tuple[dict[str, Any], bytes]]) -> None:
    """Real bytes under made-up names: Mozaik's capitals, and the merging tool's lower case."""
    for n, (entry, data) in enumerate(programs, start=1):
        merged = entry["name"].endswith(".tap")
        name = f"Tes{n}-{n + 1}.tap" if merged else f"TESS{n:02d}.TAP"
        post(world, GUARD_JOB, material, name, data)


# ----- the nests (P1b build spec 5.3, 5.11, 5.12): made-up .opt files, from Jim's lines (helpers_opt)
#
# No real .opt, .UNS or .LCK has been read, so these show the flow is ready for them: it reads what is
# documented, fails closed on anything else, and with no toolkit never says Nest checked. A stand-in for
# Jim's checks (Checks) is the only way any test here reaches a pass.


def room_file(*, width: int = 356, note: str = "") -> bytes:
    """A room as Mozaik writes one (version line 15, then the XML). note: an annotation, which a
    re-save of the same design may change (guards/design.py, room_identity)."""
    return (b'15\r\n<?xml version="1.0" encoding="utf-8"?>\r\n<Room Quan="1" RoomNosDirty="False"><Products>'
            b'<Product CabNo="1" Count="1"><CabProdPart Name="Top" Quan="1" W="%d" L="870" UsageType="0"'
            b' Type="Top" /></Product></Products><Annotations>%s</Annotations></Room>'
            % (width, note.encode("utf-8")))


ROOM = room_file()
SAVED_NEST = utf16(nest_text(sheets=(sheet(),), runs=("TESS01.TAP",)))
NOT_SAVED = NOT_NESTED
OPEN_MINUTES = 4                        # two polls and the two minutes a nest must stay unchanged


def ready(world: World) -> World:
    """NG Runner installed on a PC with Mozaik Shared\\Jobs there, as Jim's has."""
    (world.mozaik / "Jobs").mkdir(exist_ok=True)
    return installed(world)


def job_folder(world: World, folder: str = GUARD_JOB) -> Path:
    return world.mozaik / "Jobs" / folder


def save_nest(world: World, data: bytes = SAVED_NEST, folder: str = GUARD_JOB,
              material: str = CLASSIC_FOLDER) -> Path:
    """What saving in the Optimizer leaves: Jobs/<Job>/<Material>.opt, with its job's rooms beside it
    (BUILD 546, build spec section 7)."""
    place = job_folder(world, folder)
    place.mkdir(parents=True, exist_ok=True)
    if not (place / "Room1.des").exists():
        (place / "Room1.des").write_bytes(ROOM)
    path = place / f"{material}.opt"
    path.write_bytes(data)
    return path


class Checks:
    """A stand-in for Jim's two checks and the design match, which can't run yet (group 3). It says
    what it is told to about the very files it is handed, as they will. Nothing like this is wired in
    outside a test: watch.nest_checks is None."""

    def __init__(self, code: str = PASSED, line: str = "") -> None:
        self.code, self.line = code, line
        self.ran_on: list[Any] = []

    def __call__(self, job: Any) -> tuple[tuple[ToolVerdict, ...], DesignMatch]:
        self.ran_on.append(job.files)
        tools = tuple(ToolVerdict(name, self.code, self.line, checked=job.files)
                      for name in (SHEET_SIZE, BR_CAM_NEST))
        return tools, DesignMatch(DESIGN_MATCHES, checked=job.files)


@pytest.fixture
def checks(monkeypatch) -> Checks:
    stand_in = Checks()
    monkeypatch.setattr(watch, "nest_checks", lambda ctx: stand_in)
    return stand_in


def passed(world: World, checks: Checks) -> None:
    """A material posted and nested, with every check passing."""
    ready(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    save_nest(world)
    world.run_until(nest_is(world, "nest_checked"), within=minutes(OPEN_MINUTES))


def nests_read(world: World, monkeypatch) -> list[str]:
    """Every .opt or .des read from here on."""
    reads: list[str] = []
    real = guards_scan_module.read_shared

    def counted(path: Path) -> bytes:
        if path.suffix.casefold() in (".opt", ".des"):
            reads.append(path.name)
        return real(path)

    monkeypatch.setattr(guards_scan_module, "read_shared", counted)
    return reads


def test_scenario_60_nests_there_at_install_raise_nothing_and_are_not_read(make_world, monkeypatch):
    world = make_world()
    reads = nests_read(world, monkeypatch)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    save_nest(world, NOT_SAVED)                                    # Case H, Case I: parts and no sheets
    save_nest(world, b"not a nest", material="1-4 Egger Classic White")
    save_nest(world, SAVED_NEST, folder="Fakeley_102026")          # a job with no programs yet
    (job_folder(world) / "MozaikJob.UNS").write_bytes(b"JDELA\r\n")

    ready(world)
    world.run(minutes=10)

    assert nest_of(world).state == "not_checked" and nest_of(world).posted_at is None
    assert nest_of(world, material="1-4 Egger Classic White") is None
    assert reads == []
    assert to_george(world) == [] and guards_dev(world) == set()
    assert world.board().your_list == () and world.tray.colour != "red"


def test_scenario_60b_an_upgrade_from_before_nests_were_read_alarms_nothing(make_world):
    # The upgrade: NG Jobs' first scan is long done, and now Mozaik Shared is read for the first time.
    world = make_world()
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    installed(world)
    world.run(minutes=2)
    assert get_state(world.store, watch.FIRST_SCAN) is not None
    assert get_state(world.store, NEST_FILES) is None              # there is no Jobs folder yet

    save_nest(world, NOT_SAVED)
    save_nest(world, b"not a nest", material="1-4 Egger Classic White")
    world.run(minutes=10)

    assert get_state(world.store, NEST_FILES) is not None
    assert nest_of(world).state == "not_checked" and nest_of(world).posted_at is None
    assert world.board().your_list == () and guards_dev(world) == set()
    assert to_george(world) == []


def test_scenario_60c_an_old_nest_that_changes_is_a_new_one(make_world):
    world = make_world()
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    save_nest(world, SAVED_NEST)
    ready(world)
    world.run(minutes=2)

    save_nest(world, NOT_SAVED)                                    # the Optimizer opened, not run
    world.run_until(nest_is(world, "save_nest"), within=minutes(OPEN_MINUTES))


def test_scenario_61_parts_and_no_sheets_asks_to_save_the_nest_and_alarms_nobody(make_world):
    # BUILD 776-787: a .opt with parts and no sheets means not nested. Build spec 5.3, row 6.
    world = make_world()
    ready(world)
    world.run(minutes=2)

    save_nest(world, NOT_SAVED)
    world.run_until(nest_is(world, "save_nest"), within=minutes(OPEN_MINUTES))

    assert nest_of(world).reasons == (templates.SAVE_THE_NEST,)
    assert nest_of(world).basis == "nest"
    assert to_george(world) == [] and world.tray.colour != "red"
    assert [t.kind for t in world.toasts.shown] == []
    row, = world.board().jobs
    assert row.name == GUARD_JOB
    assert [(m.material, m.state) for m in row.materials] == [(CLASSIC_FOLDER, "save_nest")]
    assert row.materials[0].note == templates.SAVE_THE_NEST
    item, = [i for i in world.board().your_list if "ave the nest" in i.text]
    assert GUARD_JOB in item.text and "3/4 Egger Classic White" in item.text
    assert world.tray.colour == "amber"

    # Saved after all: the request goes, and it reads no better than NOT CHECKED with no toolkit.
    save_nest(world, SAVED_NEST)
    world.run_until(nest_is(world, "not_checked"), within=minutes(OPEN_MINUTES))
    assert not any("ave the nest" in i.text for i in world.board().your_list)


def test_scenario_61b_parts_and_no_sheets_beside_posted_programs_asks_too(make_world):
    # BUILD 696: the programs were posted, and the nest was never saved to disk.
    world = make_world()
    ready(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    world.run_until(nest_is(world, "not_checked"), within=minutes(2))

    save_nest(world, NOT_SAVED)
    world.run_until(nest_is(world, "save_nest"), within=minutes(OPEN_MINUTES))

    assert to_george(world) == []


def test_scenario_61c_a_fault_in_the_programs_stands_over_a_nest_not_saved(make_world):
    world = make_world()
    ready(world)
    world.run(minutes=1)
    save_nest(world, NOT_SAVED)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))
    world.run(minutes=OPEN_MINUTES)

    assert nest_of(world).state == "do_not_cut"
    assert len(to_george(world, "DO NOT CUT")) == 1


def test_scenario_62_a_nest_is_read_only_once_unchanged_for_two_minutes_and_twice(make_world, monkeypatch):
    # Design 5, Reads: unchanged for 120 s, hashes the same twice, and parses.
    world = make_world()
    ready(world)
    world.run(minutes=1)
    reads = nests_read(world, monkeypatch)
    began = world.clock.now()

    save_nest(world)
    for n in range(5):                  # it keeps changing: never read, however long
        world.run(minutes=1)
        save_nest(world, utf16(nest_text(sheets=(sheet(),), runs=("TESS01.TAP",), holes=n + 3)))
    assert [r for r in reads if r.endswith(".opt")] == []

    began = world.clock.now()
    world.run_until(lambda: any(r.endswith(".opt") for r in reads), within=minutes(5))
    assert world.clock.now() - began >= timedelta(seconds=120)
    assert [r for r in reads if r.endswith(".opt")] == [f"{CLASSIC_FOLDER}.opt"] * 2


def test_scenario_62b_a_verdict_while_the_nest_is_settling_says_so_and_no_pass(make_world):
    world = make_world()
    ready(world)
    world.run(minutes=1)
    save_nest(world)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    world.run_until(nest_is(world, "not_checked"), within=minutes(2))
    assert templates.NEST_SETTLING in nest_of(world).reasons

    world.run(minutes=OPEN_MINUTES)
    assert templates.NEST_SETTLING not in nest_of(world).reasons
    assert nest_of(world).state == "not_checked"
    assert nest_of(world).opt_sha256 == hashlib.sha256(SAVED_NEST).hexdigest()


@pytest.mark.parametrize("data", [SAVED_NEST, utf8(nest_text(sheets=(sheet(),), runs=("TESS01.TAP",)))],
                         ids=["utf-16", "utf-8"])
def test_scenario_63_with_no_toolkit_a_good_nest_and_clean_programs_never_read_nest_checked(make_world, data):
    # The point of the guard. Every file is right, and only Jim's own checks can say so.
    world = make_world()
    ready(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    save_nest(world, data)

    world.run(minutes=OPEN_MINUTES * 3)

    assert watch.nest_checks(world.ctx) is None
    assert nest_of(world).state == "not_checked"
    assert nest_of(world).reasons == (templates.programs_read_note(
        at=nest_of(world).updated_at, sheet="49x97"),)
    assert nest_of(world).checked_at is None
    assert to_george(world) == [] and guards_dev(world) == set()
    assert {t.key: t.state for t in world.board().jobs[0].ticks}["nest"] == "todo"


def test_scenario_64_a_program_the_nest_names_and_isnt_there_is_a_finding_and_an_extra_one_is_a_log_line(
        make_world):
    # RULES 722-726, build spec 5.11. 65 of 551 real programs are hand-named, so an extra says little.
    world = make_world()
    ready(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESfixRM.TAP", program(mark=3))
    save_nest(world, utf16(nest_text(sheets=(sheet(),), runs=("TESS01.TAP", "TESS02.TAP"))))

    world.run(minutes=OPEN_MINUTES)

    nest = nest_of(world)
    assert nest.state == "not_checked"
    assert "The nest names program TESS02, and it is not in the folder" in nest.reasons
    assert not any("TESfixRM" in reason for reason in nest.reasons)
    assert to_george(world) == [] and world.tray.colour != "red"      # no doubt is ever a DO NOT CUT
    logged = [e for e in list_events(world.store) if e.kind == "program_not_in_nest"]
    assert len(logged) == 1 and "TESfixRM" in logged[0].text and logged[0].lane == "LOG"

    # The post lands, and the finding goes.
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS02.TAP", program(mark=4))
    world.run(minutes=3)
    assert not any("not in the folder" in reason for reason in nest_of(world).reasons)


@pytest.mark.parametrize("data", [
    b"not a nest", SAVED_NEST[:200], nest_text(sheets=(sheet(),)).encode("utf-16-le"),
    utf8(nest_text(sheets=(sheet(quan="many"),))), utf8("<OptimizeJob/>"),
], ids=["not a nest", "cut off", "utf-16 with no bom", "a sheet not as documented", "nothing in it"])
def test_scenario_65_a_nest_that_wont_read_is_not_checked_says_so_and_tells_sulaiman_once(make_world, data):
    world = make_world()
    ready(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    world.run(minutes=2)

    save_nest(world, data)
    world.run(minutes=OPEN_MINUTES * 2)

    assert nest_of(world).state == "not_checked"
    assert templates.NEST_UNREADABLE in nest_of(world).reasons
    assert to_george(world) == [] and world.tray.colour != "red"
    item, = [d for d in world.dev() if d.code == "nest_unreadable"]
    assert GUARD_JOB in item.text and "Traceback" not in item.text

    save_nest(world, SAVED_NEST)
    world.run(minutes=OPEN_MINUTES)
    assert templates.NEST_UNREADABLE not in nest_of(world).reasons
    assert "nest_unreadable" not in guards_dev(world)


def test_scenario_65b_a_nest_only_in_the_cloud_tries_again_and_tells_sulaiman_after_ten_minutes(
        make_world, monkeypatch):
    world = make_world()
    ready(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    world.run(minutes=2)
    real = guards_scan_module.read_shared
    away = {"yes": True}

    def cloud(path: Path) -> bytes:
        if path.suffix == ".opt" and away["yes"]:
            raise CloudOnly(f"{path.name} is only in the cloud.", 362)
        return real(path)

    monkeypatch.setattr(guards_scan_module, "read_shared", cloud)
    save_nest(world)
    world.run(minutes=OPEN_MINUTES)
    assert templates.NEST_UNREADABLE in nest_of(world).reasons and guards_dev(world) == set()

    world.run(minutes=10)
    assert guards_dev(world) == {"nest_unreadable"}

    away["yes"] = False
    world.run(minutes=2)
    assert nest_of(world).opt_sha256 == hashlib.sha256(SAVED_NEST).hexdigest()
    assert guards_dev(world) == set()


def test_scenario_65c_mozaik_shared_out_of_reach_costs_nothing_and_concludes_nothing(make_world):
    world = make_world()
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    save_nest(world)
    ready(world)
    world.run(minutes=2)
    aside = world.tmp / "aside"
    os.replace(world.mozaik / "Jobs", aside)
    world.run(minutes=OPEN_MINUTES)

    assert guards_dev(world) == set() and nest_of(world).state == "not_checked"

    os.replace(aside, world.mozaik / "Jobs")
    world.run(minutes=OPEN_MINUTES)
    assert guards_dev(world) == set()


def test_scenario_65d_a_listing_that_empties_for_a_while_raises_nothing_when_it_comes_back(make_world):
    # A cloud drive starting up shows an empty folder (docs/decisions.md). What comes back unchanged is
    # what it was: if not, every old nest with no sheets would ask to be saved, all at once.
    world = make_world()
    save_nest(world, NOT_SAVED)
    save_nest(world, b"not a nest", material="1-4 Egger Classic White")
    (job_folder(world) / "MozaikJob.LCK").write_bytes(b"JDELA\r\n")
    ready(world)
    world.run(minutes=2)
    jobs = world.mozaik / "Jobs"
    aside = world.tmp / "aside"
    os.replace(jobs, aside)
    jobs.mkdir()

    world.run(minutes=10)
    assert world.board().your_list == () and guards_dev(world) == set()

    jobs.rmdir()
    os.replace(aside, jobs)
    world.run(minutes=10)

    assert nest_of(world) is None and nest_of(world, material="1-4 Egger Classic White") is None
    assert world.board().your_list == () and guards_dev(world) == set()


def test_scenario_65e_a_lock_gone_with_its_folder_is_no_longer_shown(make_world):
    world = make_world()
    ready(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    lock = job_folder(world) / "MozaikJob.LCK"
    lock.parent.mkdir(parents=True)
    lock.write_bytes(b"JDELA\r\n")
    world.run(minutes=3)
    assert any("Open in Mozaik" in note for note in world.board().jobs[0].notes)

    shutil.rmtree(job_folder(world))
    world.run(minutes=3)

    assert not any("Open in Mozaik" in note for note in world.board().jobs[0].notes)


def test_scenario_66_every_check_passing_is_nest_checked_bound_to_its_files(make_world, checks):
    world = make_world()
    passed(world, checks)

    nest = nest_of(world)
    assert nest.state == "nest_checked" and nest.basis == "nest"
    assert nest.opt_sha256 == hashlib.sha256(SAVED_NEST).hexdigest()
    assert nest.manifest == {"TESS01.TAP": hashlib.sha256(CLEAN).hexdigest()}
    assert nest.room_hashes == {"Room1.des": room_identity("Room1.des", ROOM)}
    assert nest.checked_at is not None
    assert nest.reasons[0].startswith("Nest checked") and "TESS01" in nest.reasons[0]
    assert {t.key: t.state for t in world.board().jobs[0].ticks}["nest"] == "done"
    told = to_george(world, "Nest checked")
    assert len(told) == 1 and not told[0].urgent
    assert len(checks.ran_on) == 1

    world.run(minutes=30)                           # nothing new: no run of the checks, nothing more
    assert len(checks.ran_on) == 1 and len(to_george(world, "Nest checked")) == 1
    assert nest_of(world).state == "nest_checked"


def _not_checked_at_once(world: World) -> None:
    """A pass never stands while what it rests on is moving: within one poll."""
    world.run(minutes=1)
    assert nest_of(world).state == "not_checked"
    assert nest_of(world).reasons == (templates.NEST_CHANGED,)


@pytest.mark.parametrize("change", [
    pytest.param(lambda w: save_nest(w, utf16(nest_text(sheets=(sheet(quan=2),), runs=("TESS01.TAP",)))),
                 id="the nest"),
    pytest.param(lambda w: post(w, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", program(mark=9)),
                 id="a program"),
    pytest.param(lambda w: (job_folder(w) / "Room1.des").write_bytes(room_file(width=357)), id="a room"),
    pytest.param(lambda w: (job_folder(w) / "Room2.des").write_bytes(ROOM), id="a room added"),
    pytest.param(lambda w: post(w, GUARD_JOB, CLASSIC_FOLDER, "TESS02.TAP", program(mark=8)),
                 id="a program added"),
])
def test_scenario_67_any_change_to_what_a_pass_rests_on_puts_it_back_to_not_checked(make_world, checks,
                                                                                    change):
    # Design 5: a pass is stored as .opt hash + TAP manifest + room hashes.
    world = make_world()
    passed(world, checks)
    checks.code = COULD_NOT_VERIFY                  # what a real check of the new files can't yet say

    change(world)
    _not_checked_at_once(world)
    world.run(minutes=OPEN_MINUTES)

    assert nest_of(world).state == "not_checked" and nest_of(world).checked_at is None
    assert len(checks.ran_on) == 2
    told = to_george(world, "NOT CHECKED")
    assert len(told) == 1 and "changed since it was checked" in told[0].lines[0]
    assert len(to_george(world, "Nest checked")) == 1
    assert {t.key: t.state for t in world.board().jobs[0].ticks}["nest"] == "todo"


def test_scenario_67c_a_nest_gone_puts_it_back_and_nothing_alarms(make_world, checks):
    world = make_world()
    passed(world, checks)
    (job_folder(world) / f"{CLASSIC_FOLDER}.opt").unlink()

    _not_checked_at_once(world)
    world.run(minutes=OPEN_MINUTES)

    assert nest_of(world).state == "not_checked" and nest_of(world).checked_at is None
    assert to_george(world, "DO NOT CUT") == [] and len(to_george(world, "NOT CHECKED")) == 1


def test_scenario_67g_a_nest_gone_and_back_unchanged_is_checked_again_and_passes(make_world, checks):
    world = make_world()
    passed(world, checks)
    path = job_folder(world) / f"{CLASSIC_FOLDER}.opt"
    aside = world.tmp / "aside.opt"
    os.replace(path, aside)
    world.run(minutes=OPEN_MINUTES)
    assert nest_of(world).state == "not_checked" and nest_of(world).checked_at is None

    os.replace(aside, path)
    world.run_until(nest_is(world, "nest_checked"), within=minutes(OPEN_MINUTES))
    assert len(checks.ran_on) == 2


def test_scenario_67h_a_nest_that_cant_be_read_for_now_concludes_nothing_for_ten_minutes(
        make_world, checks, monkeypatch):
    # Nothing is concluded from what can't be seen. It reads NOT CHECKED, as it must, and George hears
    # only once it has stayed so for as long as a program may take to arrive.
    world = make_world()
    passed(world, checks)
    told = len(world.outbox(to="george"))
    real = guards_scan_module.read_shared
    away = {"yes": True}

    def cloud(path: Path) -> bytes:
        if path.suffix == ".opt" and away["yes"]:
            raise CloudOnly(f"{path.name} is only in the cloud.", 362)
        return real(path)

    monkeypatch.setattr(guards_scan_module, "read_shared", cloud)
    path = job_folder(world) / f"{CLASSIC_FOLDER}.opt"
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

    world.run(minutes=OPEN_MINUTES)
    assert nest_of(world).state == "not_checked" and nest_of(world).checked_at is not None
    assert len(world.outbox(to="george")) == told

    away["yes"] = False
    world.run_until(nest_is(world, "nest_checked"), within=minutes(2))
    assert len(world.outbox(to="george")) == told and len(checks.ran_on) == 1


def test_scenario_67i_a_nest_that_stays_unreadable_loses_its_pass_and_george_hears(
        make_world, checks, monkeypatch):
    world = make_world()
    passed(world, checks)
    real = guards_scan_module.read_shared

    def cloud(path: Path) -> bytes:
        if path.suffix == ".opt":
            raise CloudOnly(f"{path.name} is only in the cloud.", 362)
        return real(path)

    monkeypatch.setattr(guards_scan_module, "read_shared", cloud)
    path = job_folder(world) / f"{CLASSIC_FOLDER}.opt"
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

    world.run(minutes=20)

    assert nest_of(world).state == "not_checked" and nest_of(world).checked_at is None
    assert len(to_george(world, "NOT CHECKED")) == 1
    assert guards_dev(world) == {"nest_unreadable"}


def test_scenario_67d_the_same_bytes_saved_again_stand_as_they_were_and_tell_nobody(make_world, checks):
    # How We Work 94: Mozaik re-saves the .opt on close, so the time alone says nothing. By content.
    world = make_world()
    passed(world, checks)
    told, at = len(world.outbox(to="george")), nest_of(world).checked_at
    path = job_folder(world) / f"{CLASSIC_FOLDER}.opt"
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

    _not_checked_at_once(world)
    world.run_until(nest_is(world, "nest_checked"), within=minutes(OPEN_MINUTES))

    assert nest_of(world).checked_at == at
    assert len(checks.ran_on) == 1              # no new run: the hashes are what the pass names
    assert len(world.outbox(to="george")) == told


def test_scenario_67j_a_room_saved_again_with_the_same_design_keeps_its_pass_and_tells_nobody(
        make_world, checks):
    # Measured on the 30 real .des/.bak pairs: 20 differ in bytes from a re-save, and not one in its
    # cutlist. A pass rests on the design (design.room_identity), so Mozaik saving on close drops nothing.
    world = make_world()
    passed(world, checks)
    told, at = len(world.outbox(to="george")), nest_of(world).checked_at
    resaved = room_file(note='<AnnotationSet X="2" />')
    assert resaved != ROOM
    (job_folder(world) / "Room1.des").write_bytes(resaved)

    _not_checked_at_once(world)                 # moving, so no pass stands for a moment
    world.run_until(nest_is(world, "nest_checked"), within=minutes(OPEN_MINUTES))

    assert nest_of(world).checked_at == at
    assert len(checks.ran_on) == 1
    assert len(world.outbox(to="george")) == told


def test_scenario_67e_a_pass_with_a_new_nest_that_passes_again_is_told_as_new(make_world, checks):
    world = make_world()
    passed(world, checks)
    first = nest_of(world).checked_at

    save_nest(world, utf16(nest_text(sheets=(sheet(quan=2),), runs=("TESS01.TAP",))))
    _not_checked_at_once(world)
    world.run_until(nest_is(world, "nest_checked"), within=minutes(OPEN_MINUTES))

    assert nest_of(world).checked_at > first
    assert len(to_george(world, "Nest checked")) == 2


def test_scenario_67f_a_pass_outlasts_a_restart_and_still_falls_to_a_change_after_it(make_world, checks):
    world = make_world()
    passed(world, checks)
    world.restart()
    world.run(minutes=5)
    assert nest_of(world).state == "nest_checked" and len(checks.ran_on) == 1

    save_nest(world, NOT_SAVED)
    world.run(minutes=1)
    assert nest_of(world).state == "not_checked"
    world.run_until(nest_is(world, "save_nest"), within=minutes(OPEN_MINUTES))


def test_scenario_68_a_failed_check_is_do_not_cut_in_its_own_fixed_line(make_world, checks):
    # Build spec 5.3, row 5. Never from a doubt: only from a check that says it failed.
    world = make_world()
    checks.code, checks.line = FAILED, "A made-up fixed line of a failed check"
    ready(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    save_nest(world)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(OPEN_MINUTES))

    assert nest_of(world).basis == "nest"
    told = to_george(world, "DO NOT CUT")
    assert len(told) == 1 and told[0].urgent and "made-up fixed line" in told[0].lines[0]


def test_scenario_68d_a_fault_gone_by_a_pass_says_checked_and_never_that_it_reads_not_checked(
        make_world, checks):
    world = make_world()
    checks.code, checks.line = FAILED, "A made-up fixed line of a failed check"
    ready(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    save_nest(world)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(OPEN_MINUTES))

    checks.code, checks.line = PASSED, ""
    save_nest(world, utf16(nest_text(sheets=(sheet(quan=2),), runs=("TESS01.TAP",))))
    world.run_until(nest_is(world, "nest_checked"), within=minutes(OPEN_MINUTES))

    assert nest_of(world).george_told_at is None
    assert len(to_george(world, "Nest checked")) == 1
    assert to_george(world, "No longer DO NOT CUT") == []


def test_scenario_68e_our_check_finding_what_jims_passes_is_do_not_cut_and_tells_sulaiman(make_world, checks):
    # Case B, measured: Jim's sheet size check reads the header's size only, so a hand-added sheet
    # passes it, and only the programs show it. Either's fault is DO NOT CUT; the disagreement is DEV's.
    world = make_world()
    ready(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", ON_61)
    save_nest(world)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(OPEN_MINUTES))
    world.run(minutes=OPEN_MINUTES)

    item, = [d for d in world.dev() if d.code == "checks_disagree"]
    assert item.text == (f"{GUARD_JOB} 3/4 Egger Classic White: the sheet size checks disagree."
                         " NG Runner's check finds a fault and Jim's doesn't.")
    assert nest_of(world).state == "do_not_cut"


def test_scenario_68f_jims_check_finding_what_ours_passes_is_do_not_cut_and_tells_sulaiman(make_world, checks):
    # Case E and H, measured: a header on the wrong sheet whose parts fit, which the programs can't show.
    world = make_world()
    checks.code, checks.line = FAILED, "A made-up fixed line of a failed check"
    ready(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    save_nest(world)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(OPEN_MINUTES))

    said = sorted(d.text for d in world.dev() if d.code == "checks_disagree")
    assert said == [f"{GUARD_JOB} 3/4 Egger Classic White: the doubled holes checks disagree. Jim's check"
                    " finds a fault and NG Runner's doesn't.",
                    f"{GUARD_JOB} 3/4 Egger Classic White: the sheet size checks disagree. Jim's check"
                    " finds a fault and NG Runner's doesn't."]


def test_scenario_68g_checks_that_agree_or_couldnt_say_tell_sulaiman_nothing(make_world, checks):
    world = make_world()
    passed(world, checks)
    assert "checks_disagree" not in guards_dev(world)

    checks.code = COULD_NOT_VERIFY
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", ON_61)
    world.run(minutes=OPEN_MINUTES + 2)
    assert "checks_disagree" not in guards_dev(world)


def test_scenario_68b_a_check_that_breaks_is_no_pass_and_goes_to_sulaiman(make_world, monkeypatch):
    def broken(_job: Any) -> Any:
        raise RuntimeError("a check fell over")

    monkeypatch.setattr(watch, "nest_checks", lambda ctx: broken)
    world = make_world()
    ready(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    save_nest(world)
    world.run(minutes=OPEN_MINUTES)

    assert nest_of(world).state == "not_checked"
    world.run(minutes=5)                            # it stands until a check runs clean
    assert guards_dev(world) == {"nest_check_failed"}
    assert "a check fell over" not in " ".join(d.text for d in world.dev())

    monkeypatch.setattr(watch, "nest_checks", lambda ctx: Checks(COULD_NOT_VERIFY))
    save_nest(world, utf16(nest_text(sheets=(sheet(quan=2),), runs=("TESS01.TAP",))))
    world.run(minutes=OPEN_MINUTES)
    assert guards_dev(world) == set() and nest_of(world).state == "not_checked"


def test_scenario_68c_in_shadow_mode_a_pass_and_its_loss_go_only_to_sulaiman_marked(make_world, checks):
    world = make_world(mode="shadow")
    passed(world, checks)
    checks.code = COULD_NOT_VERIFY
    save_nest(world, utf16(nest_text(sheets=(sheet(quan=2),), runs=("TESS01.TAP",))))
    world.run(minutes=OPEN_MINUTES + 1)

    assert to_george(world) == []
    subjects = [m.subject for m in world.outbox(to="sulaiman")]
    assert any(s.startswith("SHADOW Nest checked") for s in subjects)
    assert any(s.startswith("SHADOW NOT CHECKED") for s in subjects)


def test_scenario_69_the_owner_log_keeps_what_locks_name_and_another_owner_goes_to_dev(make_world):
    # FAILURES 12, 49: UNS on a closed job, swapped for LCK on open. Decision 4.
    world = make_world()
    save_nest(world)
    (job_folder(world) / "MozaikJob.UNS").write_bytes(b"JDELA\r\n")
    ready(world)
    world.run(minutes=3)
    log = list_owner_log(world.store)
    assert [(r.folder, r.file, r.owner) for r in log] == [(GUARD_JOB, "MozaikJob.UNS", "JDELA")]
    assert guards_dev(world) == set()

    world.run(minutes=5)                            # nothing changed: nothing more
    assert len(list_owner_log(world.store)) == 1

    # Mozaik opens it: UNS gives way to LCK.
    (job_folder(world) / "MozaikJob.UNS").unlink()
    (job_folder(world) / "MozaikJob.LCK").write_bytes(b"JDELA\r\n")
    world.run(minutes=2)
    assert [r.file for r in list_owner_log(world.store)] == ["MozaikJob.UNS", "MozaikJob.LCK"]
    assert guards_dev(world) == set()

    # Somebody else: at once, and once.
    (job_folder(world) / "OptimizeJob.LCK").write_bytes(b"GEORGE\r\n")
    world.run(minutes=2)
    item, = [d for d in world.dev() if d.code == "lock_owner_other"]
    assert "GEORGE" in item.text and "JDELA" in item.text
    world.run(minutes=5)
    assert len([d for d in world.dev() if d.code == "lock_owner_other"]) == 1

    # Every lock is exactly where it was left.
    assert (job_folder(world) / "MozaikJob.LCK").read_bytes() == b"JDELA\r\n"
    assert (job_folder(world) / "OptimizeJob.LCK").read_bytes() == b"GEORGE\r\n"


def test_scenario_69b_the_board_shows_a_locks_owner_and_age_and_when_it_goes(make_world):
    world = make_world()
    ready(world)
    world.run(minutes=1)
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    lock = job_folder(world) / "MozaikJob.LCK"
    lock.parent.mkdir(parents=True)
    lock.write_bytes(b"JDELA\r\n")
    when = (world.clock.now() - timedelta(days=2)).timestamp()
    os.utime(lock, (when, when))
    world.run(minutes=3)

    row, = world.board().jobs
    assert any("Open in Mozaik by JDELA" in note and "2 days" in note for note in row.notes)

    world.run(minutes=60 * 24)
    row, = world.board().jobs
    assert any("3 days" in note for note in row.notes)     # its age goes on with no new read

    lock.unlink()
    world.run(minutes=2)
    assert not any("Mozaik" in note for note in world.board().jobs[0].notes)


def test_scenario_69c_nothing_gates_on_a_lock_or_on_mozaik_running(make_world, monkeypatch):
    # Build spec 5.12: nothing gates on it in P1b. A DO NOT CUT must get out while a job is open.
    monkeypatch.setattr(guards_scan_module, "mozaik_running", lambda: True)
    world = make_world()
    save_nest(world)
    (job_folder(world) / "MozaikJob.LCK").write_bytes(b"JDELA\r\n")
    ready(world)
    world.run(minutes=2)

    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", DOUBLED)
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))

    assert len(to_george(world, "DO NOT CUT")) == 1
    assert (job_folder(world) / "MozaikJob.LCK").exists()


def test_scenario_69d_health_says_what_the_observer_found(make_world, monkeypatch):
    answers = {"now": True}
    monkeypatch.setattr(guards_scan_module, "mozaik_running", lambda: answers["now"])
    world = make_world()
    ready(world)
    world.run(minutes=2)
    assert world.sup._health().mozaik == "Running"

    for said, words in ((False, "Not running"), (None, "Could not tell")):
        answers["now"] = said
        world.run(minutes=2)
        assert world.sup._health().mozaik == words


def test_scenario_69e_a_lock_that_cant_be_read_logs_nothing_and_breaks_nothing(make_world, monkeypatch):
    world = make_world()
    real = guards_scan_module.read_shared

    def cloud(path: Path) -> bytes:
        if path.suffix.casefold() in (".uns", ".lck"):
            raise CloudOnly(f"{path.name} is only in the cloud.", 362)
        return real(path)

    monkeypatch.setattr(guards_scan_module, "read_shared", cloud)
    save_nest(world)
    (job_folder(world) / "MozaikJob.LCK").write_bytes(b"JDELA\r\n")
    post(world, GUARD_JOB, CLASSIC_FOLDER, "TESS01.TAP", CLEAN)
    ready(world)
    world.run(minutes=3)

    assert list_owner_log(world.store) == [] and guards_dev(world) == set()


@pytest.mark.fixtures
def test_real_case_d_raises_do_not_cut_for_its_doubled_holes(make_world):
    world = make_world()
    installed(world)
    world.run(minutes=1)
    _post_real(world, CLASSIC_FOLDER, _real_programs(case="D", row="doubled"))
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))

    assert len(nest_of(world).reasons) == 1 and "8 cam holes are doubled" in nest_of(world).reasons[0]
    assert sheet_cards(world) == [] and len(to_george(world, "DO NOT CUT")) == 1
    assert world.tray.colour == "red"


@pytest.mark.fixtures
def test_real_case_bs_hand_added_sheet_raises_its_card_and_a_click_clears_only_the_size(make_world):
    world = make_world()
    installed(world)
    world.run(minutes=1)
    _post_real(world, CLASSIC_FOLDER, _real_programs(case="B", row="hand_added",
                                                     material=CLASSIC_FOLDER))
    world.run_until(nest_is(world, "do_not_cut"), within=minutes(2))

    assert any("is on 61x109 and this colour is 49x97" in r for r in nest_of(world).reasons)
    card, = sheet_cards(world)
    answer(world, card, templates.SHEET_SIZE_CHOICES[0])
    world.run(minutes=3)

    assert sheet_cards(world) == [] and len(list_clearances(world.store)) == 1
    assert not any("this colour is" in r for r in nest_of(world).reasons)
    assert nest_of(world).state == "not_checked"     # this folder holds no doubled holes
    assert "1 cleared past 49x97" in nest_of(world).reasons[0]
    assert len(to_george(world, "Cleared by Jim")) == 1


@pytest.mark.fixtures
def test_real_case_cs_quarter_on_61x109_is_nothing_past_its_size(make_world):
    world = make_world()
    installed(world)
    world.run(minutes=1)
    _post_real(world, "1-4 Egger Classic White", _real_programs(case="C", role="quarter"))
    world.run_until(nest_is(world, "not_checked", material="1-4 Egger Classic White"),
                    within=minutes(2))

    assert "nothing past 61x109" in nest_of(world, material="1-4 Egger Classic White").reasons[0]
    assert to_george(world) == [] and sheet_cards(world) == []


@pytest.mark.fixtures
def test_real_merged_program_of_several_sheets_says_its_holes_were_not_compared(make_world):
    world = make_world()
    installed(world)
    world.run(minutes=1)
    _post_real(world, CLASSIC_FOLDER, _real_programs(row="families", role="merged"))
    world.run_until(nest_is(world, "not_checked"), within=minutes(2))

    assert "outside 1 merged program" in nest_of(world).reasons[0]
    assert to_george(world) == []


# ----- the takeoff on intake (P3): NG Runner's takeoff of what it files, and the heads-up from it


GARAGE_FOLDER = f"{GUARD_JOB}_Gar"
UNPLACED_PARTS = (*CLOSET_PARTS, ("CK", 2, "CK DBack 3.6x22.2x0.6 NN WH"))
MORE_SHELVES = (("VT-WH", 2, "VT 14x84x3/4 FD WH"), ("SH-WH", 10, "SH 14x24x3/4 FIX WH"),
                ("400000155", 8, "Shelf Pin"))


def filed_aside(world: World, folder: str = GUARD_JOB) -> Path:
    return world.control / "shadow" / folder


def at_or_after_six(when: datetime) -> bool:
    """Sent in the day: quiet hours end at 6 AM Pacific."""
    return 6 <= when.astimezone(PACIFIC).hour < 22


def takeoffs_of(world: World, studio_id: str = "4242") -> list[Any]:
    return list_takeoffs(world.store, studio_id)


def text_of(message: Any) -> str:
    return "\n".join((message.subject, *message.lines))


def test_scenario_70_a_closet_filed_aside_gets_its_takeoff_and_sulaiman_one_heads_up_from_it(
        make_world):
    world = make_world(mode="shadow")
    installed(world, TESTERSON)
    world.script.add_row("Testerson", "PE")
    aside = filed_aside(world)
    world.run_until(lambda: (aside / f"{GUARD_JOB}_summary.pdf").exists(), within=minutes(45))
    world.run(minutes=3)

    record, = takeoffs_of(world)
    assert (record.state, record.complete, record.could_not_verify) == ("made", True, 0)
    told = heads_ups(world, "sulaiman")
    assert len(told) == 1 and told[0].subject == (
        f"SHADOW {GUARD_JOB}: order heads-up (takeoff estimate; the nest decides)")
    assert f"1. {GUARD_JOB}: 2 sheets 3/4 Egger Classic White (takeoff estimate; the nest" \
           " decides)." in told[0].lines
    assert world.outbox(to="barbara") == [] and world.outbox(to="will") == []
    notes = json.loads((aside / f"{GUARD_JOB}_NGRunner.json").read_text(encoding="utf-8"))
    assert notes["schema"] == 3 and notes["takeoff"]["state"] == "made"
    assert notes["takeoff"]["summary_sha256"] == notes["summary"]["sha256"]
    row = world.board_row(GUARD_JOB)
    assert row.takeoff.state == "made" and row.takeoff.label == headsup.ESTIMATE
    assert "2 sheets 3/4 Egger Classic White" in row.takeoff.lines

    # The chat's JSON, arriving later in NG Jobs, sends nothing: NG Runner's takeoff is the source.
    post_takeoff(world, GUARD_JOB, takeoff_json(estimate={"total": 30000.0}))
    world.run(minutes=3)
    assert len(heads_ups(world, "sulaiman")) == 1

    # Nor do a restart and the night's re-fetch of the same content.
    world.restart()
    world.run_to(pacific_at(10, 28, 2, 30), step=600)
    assert len(heads_ups(world, "sulaiman")) == 1 and len(takeoffs_of(world)) == 1
    assert guards_dev(world) == set()


def test_scenario_70b_live_and_turned_on_barbara_and_will_get_it_and_jims_digest_says_it(
        make_world):
    world = make_world(order_flags_on=True)
    installed(world, TESTERSON)
    world.script.add_row("Testerson", "PE")
    world.run_until(is_filed(world, GUARD_JOB), within=minutes(45))
    world.run(minutes=3)

    assert [len(heads_ups(world, who)) for who in ("barbara", "will")] == [1, 1]
    assert all("NG Runner's own takeoff of the IC summary" in text_of(m) for m in heads_ups(world))
    assert to_george(world) == [] and heads_ups(world, "jim") == []
    assert record_of(world, GUARD_JOB)["takeoff"]["board_parts"] == 8

    world.run_to(pacific_at(10, 28, 6, 25), step=3600)
    world.run_to(pacific_at(10, 28, 6, 32))
    digest, = [m for m in world.outbox(to="jim") if m.ref == "digest:2026-10-28"]
    line, = [line for line in digest.lines if GUARD_JOB in line]
    assert ("takeoff: 2 sheets 3/4 Egger Classic White and 26 feet of tape (takeoff estimate;"
            " the nest decides)") in line
    assert lint(Message("digest", digest.subject, digest.lines)) == []


def test_scenario_71_lines_the_rules_dont_place_send_could_not_verify_then_the_chats_list(
        make_world):
    world = make_world(mode="shadow")
    installed(world, TESTERSON)
    world.studio.jobs["4242"].parts = UNPLACED_PARTS
    world.script.add_row("Testerson", "PE")
    world.run_until(lambda: (filed_aside(world) / f"{GUARD_JOB}_summary.pdf").exists(),
                    within=minutes(45))
    world.run(minutes=3)

    record, = takeoffs_of(world)
    assert (record.state, record.complete, record.could_not_verify) == ("made", False, 1)
    told, = heads_ups(world, "sulaiman")
    assert "could not verify the takeoff" in told.subject
    assert "could not verify 1 line" in text_of(told)
    assert "sheet" not in text_of(told) and "CK" not in text_of(told)
    row = world.board_row(GUARD_JOB)
    assert row.takeoff.state == "unverified" and row.takeoff.lines == ()
    assert "could not verify 1 line" in row.notes

    # The chat's JSON, once it arrives and reads, is the fallback: its whole list, revised, and
    # saying it is the chat's (Sulaiman's ruling, 2026-10-31). Never a mix of the two.
    post_takeoff(world, GUARD_JOB, takeoff_json())
    world.run(minutes=3)
    first, revised = heads_ups(world, "sulaiman")
    assert "revised" in revised.subject and "could not verify" not in revised.subject
    assert "From the chat's takeoff, since NG Runner's own takeoff could not verify 1 line." in (
        revised.lines[0])
    assert "12 sheets" in text_of(revised) and "Testerson Lane" not in text_of(revised)
    row = world.board_row(GUARD_JOB)
    assert row.takeoff.state == "chat" and "could not verify 1 line" in row.notes
    world.run(minutes=5)
    assert len(heads_ups(world, "sulaiman")) == 2


def test_scenario_72_a_garage_stays_in_chat_and_the_chats_json_is_its_heads_up(make_world):
    world = make_world(mode="shadow")
    installed(world, TESTERSON)
    world.studio.jobs["4242"].designs = GARAGE_ONLY
    world.script.add_row(GUARD_JOB, "PE")
    world.run_until(lambda: (filed_aside(world, GARAGE_FOLDER)
                             / f"{GARAGE_FOLDER}_summary.pdf").exists(), within=minutes(45))
    world.run(minutes=3)

    record, = takeoffs_of(world)
    assert (record.state, record.branch, record.result) == ("in_chat", "garage", None)
    assert heads_ups(world) == []
    row = world.board_row(GUARD_JOB)
    assert row.takeoff.state == "in_chat" and "stays in chat" in row.takeoff.lines[0]
    notes = json.loads((filed_aside(world, GARAGE_FOLDER) / f"{GARAGE_FOLDER}_NGRunner.json")
                       .read_text(encoding="utf-8"))
    assert notes["takeoff"]["state"] == "in_chat" and "buckets" not in notes["takeoff"]

    post_takeoff(world, GARAGE_FOLDER, takeoff_json())
    world.run(minutes=3)
    told, = heads_ups(world, "sulaiman")
    assert told.subject.startswith(f"SHADOW {GARAGE_FOLDER}: order heads-up")
    assert "NG Runner's own" not in text_of(told) and "Testerson Lane" not in text_of(told)


def test_scenario_73_a_changed_summary_jim_files_gets_a_new_takeoff_and_a_revised_heads_up(
        make_world):
    world = make_world(order_flags_on=True)
    installed(world, TESTERSON)
    world.script.add_row("Testerson", "PE")
    world.run_until(is_filed(world, GUARD_JOB), within=minutes(45))
    world.run(minutes=3)
    assert len(heads_ups(world, "barbara")) == 1

    world.studio.jobs["4242"].parts = MORE_SHELVES
    world.run_to(pacific_at(10, 28, 1, 15), step=3600)
    world.run_until(lambda: world.cards("ic_changed"), within=hours(24), step=300)
    card, = world.cards("ic_changed")
    world.actions.answer(card.id, "File the new one")
    world.run_until(lambda: len(takeoffs_of(world)) == 2, within=minutes(10))
    # Made in the night, it waits out quiet hours, as Barbara's and Will's email does (P1b).
    world.run_until(lambda: len(heads_ups(world, "will")) == 2, within=hours(12), step=600)
    assert all(at_or_after_six(m.created_at) for m in heads_ups(world))

    first, second = takeoffs_of(world)
    assert (first.result["board_parts"], second.result["board_parts"]) == (8, 12)
    assert second.render_sha256 == tree(world.ng_jobs)[f"{GUARD_JOB}/{GUARD_JOB}_summary.pdf"]
    for who in ("barbara", "will"):
        told = heads_ups(world, who)
        assert len(told) == 2 and "revised" in told[1].subject and "revised" not in told[0].subject
        assert "2 sheets 3/4 Egger Classic White" in text_of(told[1])
    assert record_of(world, GUARD_JOB)["takeoff"]["board_parts"] == 12


def test_scenario_74_a_converter_that_crashes_stops_no_filing_and_the_chats_takeoff_is_used(
        make_world, monkeypatch):
    world = make_world(order_flags_on=True)
    installed(world, TESTERSON)

    def crash(data: bytes, *, branch: str) -> Any:
        raise RuntimeError("the converter fell over at C:\\some\\path")

    monkeypatch.setattr(intake_pipeline, "convert", crash)
    world.script.add_row("Testerson", "PE")
    world.run_until(is_filed(world, GUARD_JOB), within=minutes(45))
    world.run(minutes=3)

    assert_one_commit(world, GUARD_JOB)
    assert len(world.outbox(to="barbara", subject=f"Filed as {GUARD_JOB}")) == 1
    record, = takeoffs_of(world)
    assert record.state == "failed"
    assert guards_dev(world) == {"takeoff_failed"}
    assert "path" not in " ".join(d.text for d in world.dev())
    assert heads_ups(world) == []
    assert world.board_row(GUARD_JOB).takeoff.state == "failed"

    post_takeoff(world, GUARD_JOB, takeoff_json())
    world.run(minutes=3)
    assert [len(heads_ups(world, who)) for who in ("barbara", "will")] == [1, 1]
    assert all("NG Runner's own" not in text_of(m) for m in heads_ups(world))

    # Two days on, it is still Sulaiman's alone: the night never tells Jim the job is stuck.
    world.run(minutes=50 * 60, step=3600)
    assert not any(e.kind == "dev_told_jim" for e in world.events())
