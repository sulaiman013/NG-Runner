"""
Acceptance: the day simulation (docs/p1a/integration-spec.md, section 8). Each scenario runs NG
Runner itself among the fakes of tests/fakes.py, on a fake clock: real HTTP to fake servers on
127.0.0.1, real files under tmp_path, the real store and the real chokepoint. A scenario says what
the people around NG Runner do, and what must be true afterwards.

Where a scenario turns on what the Apps Script decides (the morning's digest, the shadow run, a new
database), the real google/Outbox.gs and Watchdog.gs run under node.
"""
from __future__ import annotations

import json
import random
import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta

import pytest

from fakes import (
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
from ngrunner import fab, heartbeat, single, toasts
from ngrunner.clock import at_pacific
from ngrunner.config import load_settings
from ngrunner.intake.fetch import FetchTarget, Render, agree, fetch_render, report_urls
from ngrunner.intake.summary_check import check_summary, content_fingerprint
from ngrunner.joblist import JobListError, RenderClient
from ngrunner.match import slugify
from ngrunner.messages import Message, pushover, templates
from ngrunner.messages.lint import lint
from ngrunner.messages.outbox import outbox_file, read_receipt
from ngrunner.signing import open_envelope
from ngrunner.store import answer_card, get_list_state, get_outbox
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
    assert record["schema"] == 1 and record["job"] == "Testerson_101426"
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
