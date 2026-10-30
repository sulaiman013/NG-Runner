"""
Tests for the filing pipeline (ngrunner.intake.pipeline), against the real store, the real
commit_write and the real matcher, summary check and agreement, with fakes for everyone around
them: Studio, the online Assembly Reports, the messenger and the Apps Script, played in process on
a fake clock. The test writes the signed Fab export itself. Files live under tmp_path, names are
made up, and nothing here touches the network. tests/test_e2e_day.py runs the whole app.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from helpers_pdf import summary_pdf
from ngrunner import jobmodel
from ngrunner.cards import card_from_record
from ngrunner.config import Settings
from ngrunner.intake import pipeline
from ngrunner.intake.fetch import Agreement, candidate_slugs
from ngrunner.intake.pipeline import BackgroundFetches, Ctx, InlineFetches, add_job, tick
from ngrunner.intake.studio_designs import ProjectPage, StudioSite, StudioUnavailable, normalise
from ngrunner.intake.summary_check import check_summary, content_fingerprint
from ngrunner.interlock import PAUSED
from ngrunner.joblist import JobList, JobListError
from ngrunner.match import Opportunity, slugify
from ngrunner.messages import templates
from ngrunner.messages.lint import check_line, lint
from ngrunner.secrets import MemorySecrets
from ngrunner.signing import key_from_text, new_id, seal
from ngrunner.store import (
    add_outbox,
    answer_card,
    get_job,
    get_opportunity,
    get_render,
    get_state,
    list_aliases,
    list_cards,
    list_dev,
    list_events,
    list_fab_rows,
    list_outbox,
    list_pending,
    list_renders,
    list_write_records,
    mark_dev_told_jim,
    next_seq,
    open_dev,
    open_store,
    owned_sha,
    set_state,
    settle_pending_writes,
)
from ngrunner.store import db as store_db
from ngrunner.store.write_journal import StoreRegistry
from ngrunner.writes import Roots

KEY_HEX = "ab" * 32
KEY = key_from_text(KEY_HEX)
START = datetime(2026, 10, 27, 17, 0, tzinfo=UTC)  # Tue 10/27 10:00 AM Pacific
PE = "Pat Example"
SOLD = "Ready to Order"


def opp(studio_id: str, name: str) -> Opportunity:
    return Opportunity(name=name, studio_id=studio_id, owner=PE, stage=SOLD)


TESTERSON = opp("4242", "Testerson_101426")
TESTERSON_2 = opp("4243", "Testerson_101526")
FAKELEY = opp("4343", "Fakeley_101426")
CLOSET = (("Testerson - Primary Closet", "His and Her Master"),)
TWO = (("Testerson - Primary Closet", "His and Her Master"), ("Testerson - Office", "Office"))
GARAGE = (("Testerson - Garage", "Garage"),)
MIXED = (("Testerson - Primary Closet", "His and Her Master"), ("Testerson - Garage", "Garage"))
BLANK = (("Testerson - Thing", ""),)
FUTURE_INSTALL = ("Monday", " 11 /30")


def minutes(n: float) -> timedelta:
    return timedelta(minutes=n)


# ----- the fakes around the pipeline


class _Reply:
    def __init__(self, status: int = 200, body: bytes = b"", headers: dict | None = None) -> None:
        self.status_code, self.body = status, body
        self.headers = {"Content-Length": str(len(body)), **(headers or {})}

    def iter_content(self, size: int) -> Any:
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]

    def close(self) -> None:
        pass


class _StudioJob:
    def __init__(self, name: str, designs: Any = CLOSET, client: str = "") -> None:
        self.name, self.designs, self.client = name, designs, client
        self.printed: str | None = None
        self.early = 0
        self.reply: _Reply | None = None
        self.bytes: bytes | None = None
        self.layout_sent: str | None = None
        self.served = 0
        self.slugs: tuple[str, str] | None = None   # where Studio really has it, when not derived


class FakeStudio:
    """Studio, in process: report addresses for each Studio ID, rendered afresh for every fetch."""

    def __init__(self) -> None:
        self.jobs: dict[str, _StudioJob] = {}
        self.asked: list[str] = []
        self.raises: Exception | None = None
        self.gate: threading.Event | None = None
        self.last: dict[str, bytes] = {}            # by Studio ID: the last report handed out

    def add(self, opportunity: Opportunity, **options: Any) -> _StudioJob:
        job = _StudioJob(opportunity.name, **options)
        self.jobs[opportunity.studio_id] = job
        return job

    def fetches(self, studio_id: str, layout: str | None = None) -> int:
        words = {"landscape": "install_summary_landscape", "portrait": "install_summary/"}
        word = words.get(layout, "")
        return sum(f"/{studio_id}/" in url and word in url for url in self.asked)

    def get(self, url: str, **_: Any) -> _Reply:
        self.asked.append(url)
        if self.gate is not None:
            self.gate.wait(10)
        if self.raises is not None:
            raise self.raises
        found = re.search(r"/(install_summary(?:_landscape)?)/([^/]+)/([^/]+)/(\d+)/$", url)
        job = self.jobs.get(found[4]) if found else None
        reply = self._reply_for(found, job)
        if job is not None and reply.status_code == 200:
            self.last[found[4]] = reply.body
        return reply

    def _reply_for(self, found: Any, job: _StudioJob | None) -> _Reply:
        slugs = None if job is None else (job.slugs or candidate_slugs(job.name, job.client or None)[0])
        if job is None or (found[2], found[3]) != slugs:
            return _Reply(404, b"<html>Not Found</html>")
        if job.reply is not None:
            return job.reply
        if job.bytes is not None:
            return _Reply(body=job.bytes)
        job.served += 1
        layout = job.layout_sent or ("landscape" if found[1].endswith("landscape") else "portrait")
        finished = 1 if job.early > 0 else None
        job.early = max(0, job.early - 1)
        return _Reply(body=summary_pdf(
            project_name=job.printed or job.name, layout=layout, designs=job.designs,
            finished=finished, created=f"D:20261014{job.served:06d}-07'00'"))


class FakeSite:
    """Studio's project pages: StudioSite's one method. Unless a test says otherwise, the Proposal
    tab lists what the report handed out last lists, as it did on 59 of 59 real sold jobs. It
    never signs in, and reaches nothing.

    proposals  by Studio ID: the design names the Proposal tab lists instead.
    links      by Studio ID: the report address on the page.
    down       raised by every read.
    """

    def __init__(self, studio: FakeStudio) -> None:
        self.studio = studio
        self.proposals: dict[str, tuple[str, ...]] = {}
        self.links: dict[str, str] = {}
        self.down: Exception | None = None
        self.reads: list[threading.Thread] = []

    def project_page(self, studio_id: str) -> ProjectPage:
        self.reads.append(threading.current_thread())
        if self.down is not None:
            raise self.down
        listed = self.proposals.get(studio_id)
        if listed is None:
            last = self.studio.last.get(studio_id)
            listed = () if last is None else tuple(check_summary(last).designs)
        cards = tuple(normalise(name) for name in listed)
        return ProjectPage(cards, cards, self.links.get(studio_id))


class FakeRender:
    """The online Assembly Reports: a list read at synced_at, and Sync requests, which by default
    the office PC answers at once."""

    def __init__(self, clock: Any) -> None:
        self.clock = clock
        self.opportunities: list[Opportunity] = []
        self.clients: dict[str, str] = {}
        self.synced_at: datetime | None = clock.now()
        self.exports = 0
        self.pushes = 0
        self.answers_push = True
        self.error: JobListError | None = None

    def export(self) -> JobList:
        self.exports += 1
        if self.error is not None:
            raise self.error
        return JobList(self.synced_at, self.clock.now(), tuple(self.opportunities),
                       dict(self.clients), None)

    def request_push(self) -> bool:
        self.pushes += 1
        if self.answers_push:
            self.synced_at = self.clock.now()
        return True


class FakeMessenger:
    """The messenger's contract: a row in the outbox from the moment send commits, under the ref it
    was given. Everything sent must already pass the lint, as templates are meant to."""

    def __init__(self, store: Any, clock: Any) -> None:
        self.store, self.clock = store, clock
        self.broken = False

    def send(self, message: Any, *, to: str, channel: str = "email", ref: str | None = None) -> str:
        if self.broken:
            raise RuntimeError("the outbox can't be written")
        assert lint(message) == [], message
        now, outbox_id = self.clock.now(), new_id()
        with self.store.transaction() as tx:
            seq = next_seq(tx, "outbox", start=int(now.timestamp()))
            add_outbox(tx, id=outbox_id, seq=seq, channel=channel, recipient=to,
                       subject=message.title, lines=message.lines, urgent=message.urgent,
                       created_at=now, ref=ref)
        return outbox_id


_NO_ROW = SimpleNamespace(state=None, studio_id=None, reason=None, id=None)


class World:
    def __init__(self, tmp: Path, *, mode: str = "live") -> None:
        for name in ("NG Jobs", "NG Jobs/Old", "Mozaik Shared", "control/fab", "data"):
            (tmp / name).mkdir(parents=True, exist_ok=True)
        self.ng_jobs, self.old = tmp / "NG Jobs", tmp / "NG Jobs" / "Old"
        self.control = tmp / "control"
        self.clock = _clock(START)
        self.store = open_store(tmp / "data" / "ngrunner.db")
        self.settings = Settings(ng_jobs=self.ng_jobs, mozaik_shared=tmp / "Mozaik Shared",
                                 control=self.control, mode=mode)
        self.paths = _paths(tmp / "data")
        self.roots = Roots(self.ng_jobs, tmp / "Mozaik Shared", self.control, tmp / "data")
        self.studio = FakeStudio()
        self.site = FakeSite(self.studio)
        self.render = FakeRender(self.clock)
        self.messenger = FakeMessenger(self.store, self.clock)
        self.sheet: list[dict] = []
        self.seq = 100
        self.minute = 0
        self.ctx = Ctx(store=self.store, settings=self.settings,
                       secrets=MemorySecrets({"signing_key": KEY_HEX}), clock=self.clock,
                       roots=self.roots, paths=self.paths, studio_http=self.studio,
                       render_client=self.render, messenger=self.messenger, mode=mode,
                       fetches=InlineFetches(), studio=self.site)

    # ----- the Apps Script and Barbara

    def export(self, rows: list[dict] | None = None) -> None:
        self.seq += 1
        listed = self.sheet if rows is None else rows
        payload = {"sheet": "Sheet1", "exported_at": self.clock.now().isoformat(), "rows": [
            {"sheet_row": i + 2, "job": r["job"], "designer": r["designer"],
             "install": list(r["install"]), "fab_date": ""} for i, r in enumerate(listed)]}
        data = seal("fab_export", payload, key=KEY, seq=self.seq, issued_at=self.clock.now())
        (self.control / "fab" / "export.json").write_bytes(data)

    def add_row(self, job: str, designer: str = "PE", install: tuple = FUTURE_INSTALL,
                at: int | None = None) -> None:
        row = {"job": job, "designer": designer, "install": install}
        self.sheet.insert(len(self.sheet) if at is None else at, row)

    def correct(self, was: str, **fields: Any) -> None:
        next(r for r in self.sheet if r["job"] == was).update(fields)

    def installed(self, *listed: Opportunity, rows: int = 12, **options: Any) -> World:
        """Installed, with rows already on the sheet, and a list read before any new row."""
        self.list(*listed, **options)
        for n in range(rows):
            self.add_row(f"Sample{chr(97 + n)}")
        self.export()
        self.tick()
        return self

    def list(self, *listed: Opportunity, **options: Any) -> None:
        self.render.opportunities = [*self.render.opportunities, *listed]
        for opportunity in listed:
            self.studio.add(opportunity, **options)

    # ----- time

    def tick(self) -> None:
        tick(self.ctx)

    def run(self, n: int = 1, *, step: int | None = None) -> None:
        """n minutes, ticking every 5 (or every minute, for less than 5), with an export at every
        5th minute, as the Apps Script writes them."""
        step = step or (5 if n >= 5 else 1)
        for _ in range(0, n, step):
            self.clock.advance(minutes(step))
            self.minute += step
            if self.minute % 5 < step:
                self.export()
            self.tick()

    def run_until(self, done: Callable[[], Any], within: int, *, step: int = 5) -> None:
        for _ in range(0, within, step):
            if done():
                return
            self.run(step, step=step)
        assert done(), f"not done within {within} minutes: {self.state()}"

    def night(self) -> None:
        """Straight to the night's re-fetch, and to its second fetch if it asks for one."""
        self.clock.set(self.ops(pipeline.REFETCH)[0].due_at)
        self.export()
        self.tick()
        for op in self.ops(pipeline.REFETCH_2):
            self.clock.set(op.due_at)
            self.export()
            self.tick()
        self.clock.advance(minutes(1))
        self.tick()

    def state(self) -> str:
        return (f"rows={[(r.text, r.state, r.reason) for r in self.rows()]}"
                f" cards={[(c.id, c.kind, c.state) for c in self.cards(state=None)]}"
                f" ops={[(p.kind, p.due_at, p.note) for p in list_pending(self.store)]}"
                f" dev={[(d.code, d.text) for d in self.dev()]}")

    # ----- reading

    def rows(self) -> list[Any]:
        return list_fab_rows(self.store)

    def row(self, text: str) -> Any:
        return next((r for r in self.rows() if r.text == text), _NO_ROW)

    def job(self, studio_id: str) -> Any:
        return get_job(self.store, studio_id)

    def cards(self, kind: str | None = None, *, state: str | None = "open") -> list[Any]:
        return [c for c in list_cards(self.store, state=state) if kind in (None, c.kind)]

    def sent(self, to: str | None = None, subject: str = "") -> list[Any]:
        return [m for m in list_outbox(self.store)
                if to in (None, m.recipient) and subject in m.subject]

    def dev(self, code: str | None = None) -> list[Any]:
        return [d for d in list_dev(self.store) if code in (None, d.code)]

    def ops(self, kind: str, state: str | None = "open") -> list[Any]:
        return list_pending(self.store, kind=kind, state=state)

    def events(self, kind: str) -> list[Any]:
        return [e for e in list_events(self.store) if e.kind == kind]

    def renders(self, studio_id: str) -> list[Any]:
        return list_renders(self.store, studio_id)

    def folder(self, name: str) -> Path:
        return self.ng_jobs / name

    def tree(self, root: Path | None = None) -> dict[str, str]:
        root = root or self.ng_jobs
        return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob("*") if p.is_file()}

    def notes(self, folder: str, root: Path | None = None) -> dict:
        path = (root or self.ng_jobs) / folder / f"{folder}_NGRunner.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def answer(self, card: Any, choice: str) -> None:
        assert answer_card(self.store, card.id, answer=choice, answer_words=choice,
                           answered_by="jim", answered_at=self.clock.now(), channel="board")

    def filed(self, row: str, opportunity: Opportunity = TESTERSON) -> None:
        """A row typed, waited for, matched, fetched twice and filed."""
        self.add_row(row)
        self.run_until(lambda: any(r.text == row and r.state == "filed" for r in self.rows()), 45)


def _clock(when: datetime) -> Any:
    from ngrunner.clock import FakeClock

    return FakeClock(when)


def _paths(folder: Path) -> Any:
    from ngrunner.config import AppPaths

    return AppPaths(folder)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def three_files(folder: str) -> set[str]:
    return {f"{folder}/{folder}_summary.pdf", f"{folder}/{folder}_NGRunner.json",
            f"{folder}/{folder}_NGRunner_log.txt"}


def pdf_of(name: str = "Testerson_101426", *, note: int = 99, designs: Any = CLOSET,
           layout: str = "landscape") -> bytes:
    return summary_pdf(project_name=name, designs=designs, layout=layout,
                       created=f"D:20261001{note:06d}-07'00'")


# ----- step 1: the first export


def test_the_first_export_sees_every_row_and_starts_nothing(world):
    world.installed(TESTERSON)
    assert {r.state for r in world.rows()} == {"seen_at_install"}
    assert len(world.rows()) == 12
    world.run(15)
    assert {r.state for r in world.rows()} == {"seen_at_install"}
    assert world.sent() == [] and world.cards(state=None) == [] and world.studio.asked == []
    assert world.render.pushes == 0
    assert world.tree() == {}


def test_a_first_export_with_no_rows_is_not_the_baseline(world):
    world.list(TESTERSON)
    world.export(rows=[])
    world.tick()
    assert world.rows() == [] and world.dev("fab_held")
    world.add_row("Samplea")
    world.run(5)
    assert world.row("Samplea").state == "seen_at_install"       # not new
    assert world.dev("fab_held") == []


def test_the_same_file_read_again_changes_nothing_and_logs_nothing(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.export()
    world.tick()
    assert world.row("Testerson").state == "new"
    for _ in range(3):
        world.clock.advance(minutes(1))
        world.tick()                                             # no new export: the same file
    assert world.row("Testerson").state == "new"                 # so it has waited for nothing
    assert world.ops(pipeline.MATCH_ROW) == []
    assert world.dev("fab_replay") == []


def test_an_older_export_is_a_replay_logged_to_dev_and_ignored(world):
    world.installed(TESTERSON)
    world.seq = 50
    world.add_row("Testerson")
    world.export()
    world.tick()
    assert world.dev("fab_replay")
    assert not any(r.text == "Testerson" for r in world.rows())
    world.seq = 500
    world.export()
    world.tick()
    assert world.dev("fab_replay") == []
    assert world.row("Testerson").state == "new"


def test_a_half_synced_file_goes_to_dev_only_after_10_minutes(world):
    world.installed(TESTERSON)
    path = world.control / "fab" / "export.json"
    whole = path.read_bytes()
    path.write_bytes(whole[:20])
    world.clock.advance(minutes(1))
    world.tick()
    world.clock.advance(minutes(9))
    world.tick()
    assert world.dev("fab_unreadable") == []
    world.clock.advance(minutes(1))
    world.tick()
    assert world.dev("fab_unreadable")
    path.write_bytes(whole)
    world.tick()
    assert world.dev("fab_unreadable") == []


def test_the_old_export_alarm_goes_by_exported_at_and_clears(world):
    world.list(TESTERSON)
    world.tick()
    assert world.dev("fab_export_stale")                         # none at all
    world.add_row("Samplea")
    world.export()
    world.tick()
    assert world.dev("fab_export_stale") == []
    world.clock.advance(minutes(29))
    world.tick()
    assert world.dev("fab_export_stale") == []
    world.clock.advance(minutes(2))
    world.tick()                                                 # the file, read again, is old
    assert world.dev("fab_export_stale")
    world.export()
    world.tick()
    assert world.dev("fab_export_stale") == []


def test_no_signing_key_is_a_dev_item_and_no_crash(world):
    world.ctx.secrets = MemorySecrets()
    world.installed(TESTERSON)
    assert world.rows() == [] and world.dev("no_signing_key")


# ----- step 1: a sheet that suddenly changes a lot


def test_an_export_that_changes_a_lot_is_held_until_the_next_says_the_same(world):
    world.installed(TESTERSON, rows=20)
    held = world.sheet[:10]
    world.export(rows=held)
    world.tick()
    assert all(r.state == "seen_at_install" for r in world.rows())
    assert world.dev("fab_held")
    world.export(rows=held)                                      # the same again: it was real
    world.tick()
    assert sum(r.state == "gone" for r in world.rows()) == 10
    assert world.dev("fab_held") == []


def test_a_cut_export_followed_by_the_whole_sheet_changes_nothing(world):
    world.installed(TESTERSON, rows=20)
    ids = {r.id for r in world.rows()}
    world.export(rows=world.sheet[:10])
    world.tick()
    world.export()
    world.run(10)
    assert {r.id for r in world.rows()} == ids
    assert {r.state for r in world.rows()} == {"seen_at_install"}
    assert world.dev("fab_held") == []


def test_an_empty_export_is_held_however_small_the_sheet(world):
    world.installed(TESTERSON, rows=3)
    world.export(rows=[])
    world.tick()
    assert all(r.state == "seen_at_install" for r in world.rows())
    assert world.dev("fab_held")
    world.export()
    world.run(10)
    assert {r.state for r in world.rows()} == {"seen_at_install"}
    assert world.cards(state=None) == [] and world.sent() == [] and world.studio.asked == []


def test_a_held_new_row_acts_only_after_the_hold(world):
    world.installed(TESTERSON, rows=20)
    for n in range(6):
        world.add_row(f"Dummitt{chr(97 + n)}", at=0)
    world.export()
    world.tick()
    assert not any(r.text.startswith("Dummitt") for r in world.rows())
    world.export()
    world.tick()
    assert sum(r.text.startswith("Dummitt") for r in world.rows()) == 6


def test_a_few_rows_gone_and_new_are_not_held(world):
    world.installed(TESTERSON, rows=20)
    del world.sheet[:3]
    world.export()
    world.tick()
    assert world.dev("fab_held") == []
    assert sum(r.state == "gone" for r in world.rows()) == 3


# ----- step 1: new, moved, edited and gone rows


def test_a_new_row_acts_only_once_a_later_export_still_shows_it(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.export()
    world.tick()
    assert world.row("Testerson").state == "new" and world.render.pushes == 0
    first_seen = world.row("Testerson").first_seen
    assert first_seen == world.clock.now()                       # now, not exported_at
    world.clock.advance(minutes(5))
    world.export()
    world.tick()
    assert world.row("Testerson").state != "new"


def test_a_half_typed_row_never_asks(world):
    world.installed(TESTERSON)
    world.add_row("Testers")
    world.export()
    world.tick()
    world.clock.advance(minutes(5))
    world.correct("Testers", job="Testerson")
    world.export()
    world.tick()                                                 # it changed, so it waits again
    assert world.cards(state=None) == [] and world.sent() == []
    assert world.row("Testerson").state == "new"


def test_a_row_that_moves_keeps_its_record_and_its_sheet_row_stays_current(world):
    world.installed(TESTERSON, rows=5)
    row_id = world.row("Samplec").id
    world.add_row("Dummitt", at=0)
    world.export()
    world.tick()
    assert world.row("Samplec").id == row_id and world.row("Samplec").sheet_row == 5
    world.export()
    world.tick()
    assert world.row("Samplec").sheet_row == 5


def test_an_edit_of_a_row_seen_at_install_stays_seen_and_is_logged(world):
    world.installed(TESTERSON, rows=5)
    world.correct("Samplea", job="Testerson_101426")
    world.export()
    world.run(10)
    assert world.row("Testerson_101426").state == "seen_at_install"
    assert world.events("row_edited")
    assert world.job("4242") is None and world.studio.asked == []


def test_a_gone_row_withdraws_its_card_and_starts_nothing(world):
    world.installed(TESTERSON, TESTERSON_2)
    world.add_row("Testerson")
    world.run_until(lambda: world.cards("which_job"), 15)
    world.sheet.pop()
    world.export()
    world.tick()
    assert world.row("Testerson").state == "gone"
    assert [c.state for c in world.cards("which_job", state=None)] == ["withdrawn"]
    assert world.events("row_gone")


def test_a_row_coming_back_returns_to_the_state_it_had(world):
    world.installed(TESTERSON, rows=8)
    row_id = world.row("Samplea").id
    gone = world.sheet.pop(0)
    world.export()
    world.tick()
    assert world.row("Samplea").state == "gone"
    world.sheet.insert(0, gone)
    world.clock.advance(minutes(5))
    world.export()
    world.tick()
    assert world.row("Samplea").state == "seen_at_install" and world.row("Samplea").id == row_id
    assert len(world.rows()) == 8


def test_a_row_coming_back_after_a_day_is_a_new_row(world):
    world.installed(TESTERSON, rows=8)
    gone = world.sheet.pop(0)
    world.export()
    world.tick()
    world.clock.advance(timedelta(hours=25))
    world.sheet.insert(0, gone)
    world.export()
    world.tick()
    assert [r.state for r in world.rows() if r.text == "Samplea"] == ["gone", "new"]


def test_a_row_that_was_asking_comes_back_and_barbara_gets_no_second_email(world):
    world.installed(TESTERSON, TESTERSON_2)
    world.add_row("Testerson")
    world.run_until(lambda: world.cards("which_job"), 15)
    gone = world.sheet.pop()
    world.export()
    world.tick()
    world.sheet.append(gone)
    world.clock.advance(minutes(5))
    world.export()
    world.tick()
    assert world.row("Testerson").state == "asked"
    assert len(world.cards("which_job")) == 1
    assert len(world.sent("barbara", "Which job")) == 1


def test_only_the_edits_since_the_previous_export_are_passed_on(world, monkeypatch):
    world.installed(TESTERSON)
    before = world.clock.now() - minutes(1)
    after = world.clock.now() + minutes(1)
    edit = {"sheet_row": 2, "column": "job"}
    edits = {"edits": [{"at": before.isoformat(), "old": "Sampley", "new": "Samplez"} | edit,
                       {"at": after.isoformat(), "old": "Samplea", "new": "Samplex"} | edit]}
    (world.control / "fab" / "edits.json").write_bytes(
        seal("fab_edits", edits, key=KEY, seq=1, issued_at=after))
    seen = []
    real = pipeline.diff

    def spy(previous, rows, given=(), **options):
        seen.append(list(given))
        return real(previous, rows, given, **options)

    monkeypatch.setattr(pipeline, "diff", spy)
    world.clock.advance(minutes(5))
    world.export()
    world.tick()
    assert [e.new for e in seen[0]] == ["Samplex"]


def test_an_unreadable_edit_log_is_only_a_lost_hint(world):
    world.installed(TESTERSON)
    (world.control / "fab" / "edits.json").write_bytes(b"not an envelope")
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert world.dev("pipeline_step") == []


# ----- step 2: the job list


def test_a_stale_list_asks_for_a_push_once_per_5_minutes_and_polls_each_tick(world):
    world.installed(TESTERSON)
    world.render.answers_push = False
    world.clock.advance(minutes(1))
    world.add_row("Testerson")
    world.export()
    world.tick()
    world.clock.advance(minutes(5))
    world.export()
    exports = world.render.exports
    world.tick()
    assert world.row("Testerson").state == "waiting_list"
    assert world.render.pushes == 1
    world.clock.advance(minutes(1))
    world.tick()
    assert world.render.exports == exports + 2                   # polled each tick
    assert world.render.pushes == 1
    world.clock.advance(minutes(5))
    world.tick()
    assert world.render.pushes == 2


def test_the_row_matches_once_a_fresh_list_comes(world):
    world.installed(TESTERSON)
    world.render.answers_push = False
    world.clock.advance(minutes(1))
    world.add_row("Testerson")
    world.run(10)
    assert world.row("Testerson").state == "waiting_list"
    world.render.synced_at = world.clock.now()
    world.run(1)
    assert world.row("Testerson").state == "matched"
    assert world.row("Testerson").studio_id == "4242"


def test_freshness_is_synced_at_never_fetched_at(world):
    # A list fetched this minute but read from Salesforce before the row is stale, or a stale
    # list would auto-match with no error.
    world.installed(TESTERSON)
    world.render.answers_push = False
    world.clock.advance(minutes(1))
    world.add_row("Testerson")
    world.run(20)
    assert world.row("Testerson").state == "waiting_list"
    assert world.job("4242") is None


def test_a_corrected_row_needs_a_list_read_after_the_correction(world):
    world.installed(TESTERSON, TESTERSON_2)
    world.add_row("Testerson")
    world.run_until(lambda: world.cards("which_job"), 15)
    world.render.answers_push = False
    synced = world.render.synced_at
    world.clock.advance(minutes(1))
    world.correct("Testerson", job="Testerson_101526")
    world.run(10)
    assert world.row("Testerson_101526").text_changed_at > synced
    assert world.row("Testerson_101526").state == "waiting_list"
    assert world.job("4243") is None
    world.render.synced_at = world.clock.now()
    world.run(1)
    assert world.row("Testerson_101526").studio_id == "4243"


@pytest.mark.parametrize("change", ["empty", "no time", "older"])
def test_a_stored_list_is_never_replaced_by_an_empty_timeless_or_older_one(world, change):
    world.installed(TESTERSON)
    stored = world.render.synced_at
    if change == "empty":
        world.render.opportunities = []
        world.render.synced_at = world.clock.now()
    elif change == "no time":
        world.render.synced_at = None
    else:
        world.render.synced_at = stored - timedelta(hours=3)
        world.render.opportunities = []
    world.clock.advance(minutes(1))
    world.add_row("Dummitt")
    world.run(10)
    assert get_opportunity(world.store, "4242") is not None
    assert world.row("Dummitt").state == "waiting_list"


def test_transient_list_errors_retry_quietly_and_others_go_to_dev_with_their_words(world):
    world.installed(TESTERSON)
    world.clock.advance(minutes(1))
    world.add_row("Testerson")
    world.render.error = JobListError("timeout", "It did not answer.")
    world.run(10)
    assert world.dev("job_list") == []
    world.render.error = JobListError("refused", "It refused the key.")
    world.run(1)
    assert [d.text for d in world.dev("job_list")] == ["It refused the key."]
    world.render.error = None
    world.run(2)
    assert world.dev("job_list") == []
    assert world.row("Testerson").studio_id == "4242"


def test_no_export_key_is_a_board_item_and_rows_wait(world):
    world.installed(TESTERSON)
    world.ctx.render_client = None
    world.clock.advance(minutes(1))
    world.add_row("Testerson")
    world.run(10)
    assert world.dev("no_export_key")
    assert world.row("Testerson").state == "waiting_list"


def test_the_list_alarm_stands_while_the_list_is_old_and_older_than_the_oldest_waiting_row(world):
    world.installed(TESTERSON)
    world.render.answers_push = False
    world.clock.advance(minutes(10))
    world.add_row("Testerson")
    world.run(40, step=5)
    assert world.dev("job_list_stale") == []
    world.run(30, step=5)
    assert [d.text for d in world.dev("job_list_stale")] == [templates.ALARMS["job_list_stale"]]
    world.render.synced_at = world.clock.now()
    world.run(1)
    assert world.dev("job_list_stale") == []


def test_the_clients_reach_the_store_and_the_report_address(world):
    world.installed(FAKELEY, client="Fran Fakeley")
    world.render.clients = {"4343": "Fran Fakeley"}
    world.clock.advance(minutes(61))
    world.tick()
    assert get_opportunity(world.store, "4343").client == "Fran Fakeley"


# ----- step 3: matching


def test_a_bare_surname_row_matches_and_queues_the_first_fetch(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "matched", 15)
    job, row = world.job("4242"), world.row("Testerson")
    assert (job.name, job.mode, job.name_frozen) == ("Testerson_101426", "auto", False)
    assert row.studio_id == "4242"


def test_an_unclear_row_gets_a_card_and_one_email_and_no_second_of_either(world):
    world.installed(TESTERSON, TESTERSON_2)
    world.add_row("Testerson")
    world.run_until(lambda: world.cards("which_job"), 15)
    world.run(30)
    card, = world.cards("which_job")
    assert world.row("Testerson").state == "asked"
    assert card.fab_row_id == world.row("Testerson").id
    assert set(card.choice_ids) == {"4242", "4243", None}
    assert card.choices[-1] == templates.NONE_OF_THESE
    email, = world.sent("barbara", "Which job")
    assert 'Fab row "Testerson"' in email.lines[0]
    assert any("Testerson_101426" in line for line in email.lines)
    assert world.studio.asked == []


@pytest.mark.parametrize("typed", ["Dummitt", "x" * 201], ids=["no such job", "a row too long"])
def test_a_row_with_no_match_gets_the_same_card_and_email_with_no_candidates(world, typed):
    world.installed(TESTERSON)
    world.add_row(typed)
    world.run_until(lambda: world.cards("which_job"), 15)
    card, = world.cards("which_job")
    assert card.choices == (templates.NONE_OF_THESE,) and card.choice_ids == (None,)
    assert world.row(typed).state == "asked"
    assert len(world.sent("barbara", "Which job")) == 1


@pytest.mark.parametrize("old", [False, True], ids=["at the top", "in Old"])
def test_a_folder_in_ng_jobs_keeps_a_surname_row_from_auto_matching(world, old):
    world.installed(TESTERSON)
    (world.old if old else world.ng_jobs).joinpath("Testerson_101426").mkdir()
    world.add_row("Testerson")
    world.run_until(lambda: world.cards("which_job"), 15)
    assert world.job("4242") is None
    assert world.row("Testerson").state == "asked"


def test_with_ng_jobs_out_of_reach_rows_wait(world, tmp_path):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    moved = tmp_path / "away"
    world.ng_jobs.rename(moved)
    world.run(10)
    assert world.row("Testerson").state == "new"
    assert world.job("4242") is None and world.cards(state=None) == []
    assert world.dev("ng_jobs_unreachable")
    moved.rename(world.ng_jobs)
    world.run(1)
    assert world.dev("ng_jobs_unreachable") == []
    assert world.row("Testerson").state == "matched"


def test_the_first_export_waits_for_ng_jobs_to_be_readable(world, tmp_path):
    world.list(TESTERSON)
    world.add_row("Samplea")
    world.export()
    moved = tmp_path / "away"
    world.ng_jobs.rename(moved)
    world.tick()
    assert world.rows() == []
    moved.rename(world.ng_jobs)
    world.tick()
    assert world.row("Samplea").state == "seen_at_install"


# ----- Jim's answers


def asked(world: World, *listed: Opportunity) -> Any:
    world.installed(*listed)
    world.add_row("Testerson")
    world.run_until(lambda: world.cards("which_job"), 15)
    return world.cards("which_job")[0]


def test_jims_answer_links_the_job_its_choice_id_names_and_files_it(world):
    card = asked(world, TESTERSON, TESTERSON_2)
    world.answer(card, card.choices[card.choice_ids.index("4243")])
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert world.row("Testerson").studio_id == "4243"
    assert three_files("Testerson_101526") == set(world.tree())
    assert world.job("4242") is None


def test_an_answer_whose_job_left_the_list_links_nothing_and_says_so(world):
    card = asked(world, TESTERSON, TESTERSON_2)
    world.render.opportunities = [TESTERSON]
    world.render.synced_at = world.clock.now()
    world.clock.advance(timedelta(hours=2))
    world.tick()                                                 # a new list, read quietly
    world.answer(card, card.choices[card.choice_ids.index("4243")])
    world.run(3)
    row = world.row("Testerson")
    assert row.state == "asked" and row.studio_id is None and "left the job list" in row.reason
    assert world.job("4243") is None and world.studio.asked == []


def test_none_of_these_sets_the_row_to_none_logged_with_no_refiling(world):
    card = asked(world, TESTERSON, TESTERSON_2)
    world.answer(card, templates.NONE_OF_THESE)
    world.run(5)
    assert world.row("Testerson").state == "none"
    assert world.studio.asked == [] and world.events("row_none")


def test_an_answer_is_acted_on_once(world):
    card = asked(world, TESTERSON, TESTERSON_2)
    world.answer(card, card.choices[0])
    world.run(3)
    answered = len(world.events("card_answered"))
    world.run(3)
    assert len(world.events("card_answered")) == answered == 1


def test_barbaras_correction_withdraws_the_card_and_files_the_job_she_named(world):
    card = asked(world, TESTERSON, TESTERSON_2)
    world.correct("Testerson", job="Testerson_101526")
    world.run_until(lambda: world.row("Testerson_101526").state == "filed", 45)
    assert [c.state for c in world.cards("which_job", state=None)] == ["withdrawn"]
    assert card.id == world.cards("which_job", state=None)[0].id
    assert world.row("Testerson_101526").studio_id == "4243"
    assert len(world.sent("barbara", "Which job")) == 1
    assert len(world.sent("barbara", "Filed as Testerson_101526")) == 1


# ----- step 4: the first fetch


def filing(world: World, *listed: Opportunity) -> None:
    """A surname row for the first job listed, up to its first fetch."""
    world.installed(*listed)
    world.add_row(listed[0].name.split("_")[0])
    world.run_until(lambda: world.studio.asked, 15)


@pytest.mark.parametrize("reply", [
    _Reply(404, b"no"), _Reply(503, b"down"), _Reply(200, b"<html>not a pdf</html>"),
    _Reply(200, b"%PDF-1.4 cut off"),
], ids=["not_found", "http_error", "not_pdf", "truncated"])
def test_a_refused_fetch_backs_off_5_15_30_60_then_hourly(world, reply):
    world.list(TESTERSON)
    world.studio.jobs["4242"].reply = reply
    world.add_row("Samplea")
    world.export()
    world.tick()
    world.add_row("Testerson")
    world.run_until(lambda: world.studio.asked, 15)
    at = world.renders("4242")[-1].fetched_at
    gaps = []
    for _ in range(5):
        op, = world.ops(pipeline.FETCH_1)
        gaps.append((op.due_at - at) / minutes(1))
        at = op.due_at
        world.clock.set(op.due_at)
        world.tick()
    assert gaps == [5, 15, 30, 60, 60]
    assert world.ops(pipeline.FETCH_1)[0].note == world.renders("4242")[-1].missing[0]


@pytest.mark.parametrize("error, reason", [(requests.Timeout(), "longer than"),
                                           (requests.ConnectionError(), "reach Studio")],
                         ids=["timeout", "network"])
def test_studio_slow_or_out_of_reach_backs_off_with_its_reason(world, error, reason):
    world.installed(TESTERSON)
    world.studio.raises = error
    world.add_row("Testerson")
    world.run_until(lambda: world.studio.asked, 15)
    op, = world.ops(pipeline.FETCH_1)
    assert reason in op.note and op.attempts == 1


def test_an_early_render_retries_every_30_minutes_then_the_complete_pair_files(world):
    world.installed(TESTERSON, designs=TWO)
    world.studio.jobs["4242"].early = 1
    world.add_row("Testerson")
    world.run_until(lambda: world.studio.asked, 15)
    first = world.renders("4242")[0]
    assert not first.check_ok
    world.run(25)
    assert len(world.renders("4242")) == 1 and world.tree() == {}
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert [r.check_ok for r in world.renders("4242")] == [False, True, True]
    assert world.renders("4242")[2].fetched_at - first.fetched_at >= minutes(40)


def test_too_big_is_a_dev_item_and_is_not_retried(world):
    world.installed(TESTERSON)
    world.studio.jobs["4242"].reply = _Reply(200, b"", {"Content-Length": str(10 ** 9)})
    world.add_row("Testerson")
    world.run_until(lambda: world.studio.asked, 15)
    assert world.dev("too_big")[0].studio_id == "4242"
    assert world.ops(pipeline.FETCH_1) == []
    asked_so_far = len(world.studio.asked)
    world.run(30)
    assert len(world.studio.asked) == asked_so_far


def test_no_project_name_retries_and_then_raises_the_24_hour_card(world):
    world.installed(TESTERSON)
    world.studio.jobs["4242"].reply = _Reply(body=summary_pdf(project_name=None))
    world.add_row("Testerson")
    world.run_until(lambda: world.studio.asked, 15)
    assert world.ops(pipeline.FETCH_1)[0].attempts == 1 and world.cards(state=None) == []
    world.run(23 * 60, step=60)
    assert world.cards("fetch_failed") == []
    world.run(120, step=30)
    card, = world.cards("fetch_failed")
    assert "PROJECT NAME" in card.text and "Testerson_101426" in card.text
    assert card.studio_id == "4242"


def test_studio_down_for_a_day_raises_one_card_and_keeps_trying(world):
    world.installed(TESTERSON)
    world.studio.raises = requests.ConnectionError()
    world.add_row("Testerson")
    world.run_until(lambda: world.studio.asked, 15)
    world.run(23 * 60, step=60)
    assert world.cards("fetch_failed") == []
    world.run(120, step=30)
    assert len(world.cards("fetch_failed", state=None)) == 1
    world.run(240, step=60)
    assert len(world.cards("fetch_failed", state=None)) == 1
    tried = len(world.studio.asked)
    world.studio.raises = None
    world.run_until(lambda: world.row("Testerson").state == "filed", 150)
    assert len(world.studio.asked) > tried
    assert world.cards("fetch_failed") == []                     # the question no longer stands
    assert world.cards("fetch_failed", state="withdrawn")


def test_keep_trying_gives_another_24_hours_and_stop_trying_stops(world, tmp_path):
    world.installed(TESTERSON)
    world.studio.raises = requests.ConnectionError()
    world.add_row("Testerson")
    world.run_until(lambda: world.cards("fetch_failed"), 28 * 60, step=60)
    world.answer(world.cards("fetch_failed")[0], "Keep trying")
    world.run(1)
    op, = world.ops(pipeline.FETCH_1)
    assert op.deadline_at == world.clock.now() + timedelta(hours=24)
    world.run(60, step=30)
    assert world.cards("fetch_failed") == []
    world.run_until(lambda: world.cards("fetch_failed"), 25 * 60, step=60)
    world.answer(world.cards("fetch_failed")[0], "Stop trying")
    world.run(1)
    tried = len(world.studio.asked)
    world.run(120, step=30)
    assert len(world.studio.asked) == tried and world.ops(pipeline.FETCH_1) == []
    assert world.ops(pipeline.FETCH_1, state="failed")


def test_another_jobs_report_is_a_dev_item_and_the_card_at_once(world):
    world.installed(TESTERSON)
    world.studio.jobs["4242"].printed = "Fakeley_101426"
    world.add_row("Testerson")
    world.run_until(lambda: world.studio.asked, 15)
    card, = world.cards("fetch_failed")
    assert "Fakeley_101426" in card.text and card.studio_id == "4242"
    assert world.dev("wrong_job")[0].studio_id == "4242"
    assert world.tree() == {}
    world.run(30)
    assert len(world.cards("fetch_failed", state=None)) == 1 and len(world.dev("wrong_job")) == 1
    assert world.job("4242").name == "Testerson_101426" and world.job("4242").aliases == ()


def test_a_renamed_job_before_filing_is_the_same_job_and_files_under_its_new_name(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "matched", 15)
    world.render.opportunities = [opp("4242", "Testerson_101526")]
    world.render.synced_at = world.clock.now()
    world.studio.jobs["4242"].printed = "TESTERSON 101526"       # printed its own way: by slug
    world.clock.advance(timedelta(hours=2))
    world.tick()                                                 # the list comes, then a fetch
    world.run_until(lambda: world.row("Testerson").state == "filed", 60)
    job = world.job("4242")
    assert job.name == "Testerson_101526" and job.folder == "Testerson_101526"
    assert job.name_frozen and "Testerson_101426" in job.aliases
    assert world.events("renamed") and world.cards(state=None) == []
    assert world.dev("wrong_job") == []
    assert set(world.tree()) == three_files("Testerson_101526")


def test_the_address_that_worked_is_asked_first_next_time(world):
    world.installed(TESTERSON, client="Pat Testerson")
    studio = world.studio.jobs["4242"]
    studio.client = "Pat Testerson"
    world.add_row("Testerson")
    world.run_until(lambda: world.renders("4242"), 15)
    url = world.renders("4242")[0].url
    world.studio.asked.clear()
    world.run_until(lambda: len(world.renders("4242")) == 2, 15)
    assert world.studio.asked[0] == url
    assert get_render(world.store, world.renders("4242")[1].id).url == url


def test_a_jobs_first_fetch_fixes_its_layout_whatever_the_setting_says_later(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.renders("4242"), 15)
    from dataclasses import replace

    world.ctx.settings = replace(world.settings, report_layout="portrait")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert {r.layout for r in world.renders("4242")} == {"landscape"}
    assert world.studio.fetches("4242", "portrait") == 0


def test_the_card_comes_however_the_ticks_fall_on_the_retries(world):
    # With a tick only as each retry falls due, there is never a tick when the retry isn't due.
    world.installed(TESTERSON)
    world.studio.raises = requests.ConnectionError()
    world.add_row("Testerson")
    world.run_until(lambda: world.studio.asked, 15)
    for _ in range(40):
        world.clock.set(world.ops(pipeline.FETCH_1)[0].due_at)
        world.tick()
        if world.cards("fetch_failed"):
            break
    assert world.cards("fetch_failed")
    assert world.clock.now() >= world.ops(pipeline.FETCH_1)[0].deadline_at


def test_a_new_job_starts_in_the_setting_s_layout(world):
    from dataclasses import replace

    world.ctx.settings = replace(world.settings, report_layout="portrait")
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert {r.layout for r in world.renders("4242")} == {"portrait"}
    assert world.notes("Testerson_101426")["summary"]["layout"] == "portrait"


# ----- running fetches off the tick's thread


def background(world: World) -> None:
    world.ctx.fetches = BackgroundFetches()


def to_the_match(world: World, *rows: str) -> None:
    """New rows that have waited their export, on a fresh list: the next tick matches them."""
    world.clock.advance(minutes(1))
    for row in rows:
        world.add_row(row)
    world.export()
    world.tick()
    world.clock.advance(minutes(5))
    world.export()
    world.tick()                                                 # a stale list asks for a push
    world.clock.advance(minutes(1))


def wait_until(done: Callable[[], Any]) -> None:
    deadline = time.monotonic() + 10
    while not done():
        assert time.monotonic() < deadline, "the fetch never finished"
        time.sleep(0.01)


def test_a_tick_never_waits_on_a_fetch_and_the_next_one_takes_its_result(world):
    world.installed(TESTERSON)
    background(world)
    world.studio.gate = threading.Event()
    to_the_match(world, "Testerson")
    started = time.monotonic()
    world.tick()                                                 # Studio is stuck in this fetch
    assert time.monotonic() - started < 5
    wait_until(lambda: world.studio.asked)
    assert world.renders("4242") == []
    world.studio.gate.set()
    wait_until(lambda: not world.ctx.fetches._running)
    world.tick()
    assert len(world.renders("4242")) == 1
    assert world.ops(pipeline.FETCH_2)


def test_a_couple_of_fetches_run_at_a_time(world):
    jobs = [opp(str(5000 + n), f"Dummitt{chr(97 + n)}_101426") for n in range(4)]
    world.installed(*jobs)
    background(world)
    world.studio.gate = threading.Event()
    to_the_match(world, *(job.name for job in jobs))
    world.tick()
    wait_until(lambda: len(world.studio.asked) == 2)
    world.tick()
    time.sleep(0.05)
    assert len(world.studio.asked) == 2
    world.studio.gate.set()
    wait_until(lambda: not world.ctx.fetches._running)
    world.ctx.fetches.stop()


def _disk_full(*args: Any, **kwargs: Any) -> None:
    raise OSError("the disk is full")


@pytest.mark.parametrize("off_the_thread", [True, False], ids=["background", "inline"])
def test_a_fetch_that_raises_is_a_refused_fetch_that_backs_off_and_is_then_fine(
        world, monkeypatch, off_the_thread):
    world.installed(TESTERSON)
    if off_the_thread:
        background(world)
    to_the_match(world, "Testerson")
    monkeypatch.setattr(pipeline, "_cache", _disk_full)
    world.tick()
    if off_the_thread:
        wait_until(lambda: not world.ctx.fetches._running)
        world.tick()                                             # takes it
    op, = world.ops(pipeline.FETCH_1)
    assert op.attempts == 1 and op.due_at == world.clock.now() + minutes(5)
    assert op.note == "NG Runner could not finish fetching the IC summary for Testerson_101426"
    render, = world.renders("4242")
    assert not render.check_ok and render.missing == (op.note,) and render.cache_path is None
    assert world.dev("pipeline_step") == []                      # the job's, not the pipeline's
    monkeypatch.undo()
    world.clock.advance(minutes(5))
    world.tick()
    if off_the_thread:
        wait_until(lambda: not world.ctx.fetches._running)
        world.tick()
    assert world.ops(pipeline.FETCH_1) == [] and world.ops(pipeline.FETCH_2)


def test_a_fetch_that_keeps_raising_raises_the_24_hour_card_with_its_reason(world, monkeypatch):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    monkeypatch.setattr(pipeline, "_cache", _disk_full)
    world.run_until(lambda: world.cards("fetch_failed"), 26 * 60, step=30)
    card, = world.cards("fetch_failed")
    assert "could not finish fetching the IC summary" in card.text
    assert world.tree() == {}
    assert lint(templates.card_message(card_from_record(card, job="Testerson_101426"))) == []


def test_a_project_page_read_that_breaks_oddly_is_a_refused_fetch_too(world):
    world.installed(TESTERSON)
    world.site.down = RuntimeError("something nobody planned for")
    world.add_row("Testerson")
    world.run_until(lambda: world.renders("4242"), 30)
    op, = world.ops(pipeline.FETCH_1)
    assert op.attempts == 1 and "could not finish fetching" in op.note
    assert world.ops(pipeline.FETCH_2) == [] and world.tree() == {}


def test_work_that_dies_anyway_is_dropped_and_its_row_stays_due():
    fetches = BackgroundFetches()

    def dies() -> None:
        raise OSError("not even a refused fetch came of it")

    fetches.start(7, dies)
    wait_until(lambda: not fetches._running)
    assert fetches.busy() == {7}
    assert fetches.finished() == [] and fetches.busy() == set()


def test_a_stop_request_ends_a_tick_between_steps_and_what_is_due_stays_due(world):
    world.installed(TESTERSON)
    to_the_match(world, "Testerson")
    calls = []
    world.ctx.stop_requested = lambda: calls.append(1) or len(calls) > 3
    world.tick()
    assert world.studio.asked == [] and world.job("4242") is None
    world.ctx.stop_requested = lambda: False
    world.tick()
    assert world.job("4242") is not None and world.studio.asked


def test_stop_work_starts_no_new_fetch(world):
    world.installed(TESTERSON)
    pipeline.stop_work(world.ctx)
    world.add_row("Testerson")
    world.run(10)
    assert world.studio.asked == []


# ----- step 5: two fetches must agree


def to_second_fetch(world: World, *listed: Opportunity, **options: Any) -> None:
    listed = listed or (TESTERSON,)
    world.installed(*listed, **options)
    world.add_row(listed[0].name.split("_")[0])
    world.run_until(lambda: world.ops(pipeline.FETCH_2), 15)


def second_fetch_due(world: World) -> datetime:
    return world.ops(pipeline.FETCH_2)[0].due_at


def second_due(world: World) -> Any:
    op, = world.ops(pipeline.FETCH_2)
    world.clock.set(op.due_at)
    return op


def test_two_fetches_ten_minutes_apart_that_agree_are_filed(world):
    to_second_fetch(world)
    first = world.renders("4242")[0]
    assert world.ops(pipeline.FETCH_2)[0].due_at == first.fetched_at + minutes(10)
    world.run_until(lambda: world.row("Testerson").state == "filed", 15)
    first, second = world.renders("4242")
    assert second.fetched_at - first.fetched_at >= minutes(10)
    assert first.sha256 != second.sha256 and first.fingerprint == second.fingerprint
    assert second.governing and not first.governing


def test_a_second_fetch_that_differs_becomes_the_new_first_with_the_same_deadline(world):
    to_second_fetch(world)
    deadline = world.ops(pipeline.FETCH_2)[0].deadline_at
    assert deadline is not None
    world.studio.jobs["4242"].designs = TWO
    op = second_due(world)
    world.tick()
    assert world.tree() == {}
    again, = world.ops(pipeline.FETCH_2)
    assert again.id == op.id and again.deadline_at == deadline
    second = world.renders("4242")[1]
    assert again.due_at == second.fetched_at + minutes(10)
    assert any("different text" in e.text for e in world.events("fetches_differ"))
    world.run_until(lambda: world.row("Testerson").state == "filed", 15)
    assert len(world.renders("4242")) == 3
    assert world.renders("4242")[2].governing


def test_one_file_served_twice_is_one_dev_item_and_a_fetch_again(world):
    to_second_fetch(world)
    studio = world.studio.jobs["4242"]
    studio.bytes = world.renders("4242")[0].cache_path.read_bytes()
    second_due(world)
    world.tick()
    assert world.tree() == {}
    assert len(world.dev("same_bytes")) == 1 and world.dev("same_bytes")[0].studio_id == "4242"
    assert second_fetch_due(world) == world.renders("4242")[1].fetched_at + minutes(10)
    second_due(world)
    world.tick()
    assert len(world.dev("same_bytes")) == 1                     # one standing problem, one item
    studio.bytes = None
    second_due(world)
    world.tick()
    world.run_until(lambda: world.row("Testerson").state == "filed", 15)
    assert world.dev("same_bytes") == []


def test_a_first_fetch_that_no_longer_passes_is_dropped_and_the_second_counts_as_first(world):
    to_second_fetch(world, designs=TWO)
    first = world.renders("4242")[0]
    first.cache_path.write_bytes(summary_pdf(designs=TWO, finished=1))
    second_due(world)
    world.tick()
    assert world.tree() == {}
    assert second_fetch_due(world) == world.renders("4242")[1].fetched_at + minutes(10)
    world.run_until(lambda: world.row("Testerson").state == "filed", 15)
    assert world.renders("4242")[2].governing


def test_a_second_fetch_that_is_early_keeps_the_first_and_tries_every_30_minutes(world):
    to_second_fetch(world, designs=TWO)
    world.studio.jobs["4242"].early = 1
    second_due(world)
    world.tick()
    second = world.renders("4242")[1]
    assert not second.check_ok
    assert world.ops(pipeline.FETCH_2)[0].due_at == second.fetched_at + minutes(30)
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert [r.check_ok for r in world.renders("4242")] == [True, False, True]


def test_too_soon_fetches_again_at_the_first_fetchs_time_plus_10_minutes(world, monkeypatch):
    to_second_fetch(world)
    first = world.renders("4242")[0]
    monkeypatch.setattr(pipeline, "agree",
                        lambda *a, **k: Agreement(False, "too_soon", "Too soon."))
    world.clock.advance(minutes(20))
    world.tick()
    assert world.ops(pipeline.FETCH_2)[0].due_at == first.fetched_at + minutes(10)
    assert world.tree() == {}


def test_layouts_that_differ_are_a_dev_item_and_no_loop(world):
    to_second_fetch(world)
    world.studio.jobs["4242"].layout_sent = "portrait"
    second_due(world)
    world.tick()
    assert world.dev("layouts_differ")[0].studio_id == "4242"
    assert world.ops(pipeline.FETCH_2) == []
    asked = len(world.studio.asked)
    world.run(60)
    assert len(world.studio.asked) == asked and world.tree() == {}


def test_a_first_fetch_whose_cached_file_is_gone_counts_as_no_first_fetch(world):
    to_second_fetch(world)
    world.renders("4242")[0].cache_path.unlink()
    second_due(world)
    world.tick()
    assert world.tree() == {} and world.dev("pipeline_step") == []
    assert second_fetch_due(world) == world.renders("4242")[1].fetched_at + minutes(10)
    world.run_until(lambda: world.row("Testerson").state == "filed", 15)


def test_a_fetch_failing_at_the_second_step_keeps_the_first(world):
    to_second_fetch(world)
    world.studio.raises = requests.ConnectionError()
    second_due(world)
    world.tick()
    op, = world.ops(pipeline.FETCH_2)
    assert op.attempts == 1 and "reach Studio" in op.note
    world.studio.raises = None
    world.run_until(lambda: world.row("Testerson").state == "filed", 15)
    assert world.renders("4242")[0].check_ok


def test_a_reason_quoting_a_design_goes_to_the_log_and_never_to_a_card(world):
    # An agreement's reason can quote design names: the event log has it, a card never does.
    to_second_fetch(world, designs=TWO)
    world.studio.jobs["4242"].early = 1
    second_due(world)
    world.tick()
    assert not any("Primary Closet" in c.text for c in world.cards(state=None))
    assert not any("Primary Closet" in line for m in world.sent() for line in m.lines)


# ----- step 6: the branch


@pytest.mark.parametrize("designs, folder, branch", [
    (GARAGE, "Testerson_101426_Gar", "garage"),
    (MIXED, "Testerson_101426", "closet"),
    (CLOSET, "Testerson_101426", "closet"),
], ids=["garage only", "mixed: one job, no _Gar, no card", "closets"])
def test_the_folder_follows_the_designs(world, designs, folder, branch):
    world.installed(TESTERSON, designs=designs)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert set(world.tree()) == three_files(folder)
    assert world.job("4242").branch == branch and world.job("4242").folder == folder
    assert world.notes(folder)["branch"] == branch
    assert world.cards(state=None) == []


def test_an_all_garage_job_is_garage_whatever_closet_word_the_row_carries(world):
    world.installed(TESTERSON, designs=GARAGE)
    world.add_row("Testerson closet")
    world.run_until(lambda: world.row("Testerson closet").state == "filed", 45)
    assert set(world.tree()) == three_files("Testerson_101426_Gar")
    assert world.cards(state=None) == []


@pytest.mark.parametrize("row, designs", [("Testerson garage", CLOSET),
                                          ("Testerson_101426_Gar", CLOSET),
                                          ("Testerson_101426", BLANK)],
                         ids=["the row says garage", "the row says _Gar", "a blank category"])
def test_a_branch_that_cant_be_told_raises_the_card_and_only_that_job_waits(world, row, designs):
    world.installed(TESTERSON, FAKELEY, designs=designs)
    world.studio.jobs["4343"].designs = CLOSET
    world.add_row(row)
    world.add_row("Fakeley")
    world.run_until(lambda: world.cards("closet_or_garage")
                    and world.row("Fakeley").state == "filed", 45)
    card, = world.cards("closet_or_garage")
    assert card.studio_id == "4242" and card.choices == ("Closet", "Garage")
    assert "Testerson -" not in card.text                        # no design name reaches it
    assert world.job("4242").branch is None
    assert set(world.tree()) == three_files("Fakeley_101426")
    world.run(10)
    assert len(world.cards("closet_or_garage", state=None)) == 1
    world.answer(card, "Garage")
    world.run(2)
    assert world.job("4242").branch == "garage"
    assert three_files("Testerson_101426_Gar") <= set(world.tree())


def test_the_opportunity_name_is_asked_about_too(world):
    named = opp("4242", "Testerson_101426_Gar")
    world.installed(named, designs=CLOSET)
    world.add_row("Testerson_101426_Gar")
    world.run_until(lambda: world.cards("closet_or_garage"), 45)
    assert world.tree() == {}


# ----- step 6: what is already in the folder


def in_folder(world: World, folder: str = "Testerson_101426", *, old: bool = False,
              name: str | None = None, data: bytes | None = None) -> Path:
    place = (world.old if old else world.ng_jobs) / folder
    place.mkdir(parents=True, exist_ok=True)
    path = place / (name or f"{folder}_summary.pdf")
    path.write_bytes(data if data is not None else pdf_of())
    return path


@pytest.mark.parametrize("old", [False, True], ids=["at the top", "in Old"])
@pytest.mark.parametrize("name", [None, "testerson_testerson_101426_summary.pdf",
                                  "Testerson_101426 (2026-10-01).pdf", "Testerson summary.pdf"],
                         ids=["its own name", "Studio's name", "a dated copy", "hand-named"])
def test_the_same_summary_there_marks_the_job_filed_with_no_write(world, old, name):
    world.installed(TESTERSON)
    in_folder(world, old=old, name=name)
    before = world.tree()
    world.add_row("Testerson_101426")
    world.run_until(lambda: world.row("Testerson_101426").state == "filed", 45)
    assert world.tree() == before
    assert world.cards(state=None) == []
    assert world.job("4242").folder == "Testerson_101426" and world.job("4242").name_frozen
    assert not (old and world.folder("Testerson_101426").exists())
    world.run(2)
    assert len(world.sent("barbara", "Filed as")) == 1
    assert world.events("filed")


def test_a_garage_jobs_folder_is_looked_in_under_its_garage_name(world):
    world.installed(TESTERSON, designs=GARAGE)
    in_folder(world, "Testerson_101426_Gar", data=pdf_of(designs=GARAGE))
    before = world.tree()
    world.add_row("Testerson_101426")
    world.run_until(lambda: world.row("Testerson_101426").state == "filed", 45)
    assert world.tree() == before and world.job("4242").folder == "Testerson_101426_Gar"
    assert world.job("4242").branch == "garage" and world.cards(state=None) == []


def test_a_file_at_the_summarys_own_name_is_never_written_over_whatever_it_holds(world):
    world.installed(TESTERSON)
    in_folder(world, data=summary_pdf(project_name="Fakeley_101426"))
    before = world.tree()
    world.add_row("Testerson_101426")
    world.run_until(lambda: world.cards("ic_changed"), 45)
    assert world.tree() == before


def test_a_summary_that_cant_be_read_blocks_the_job_and_writes_nothing(world, monkeypatch):
    world.installed(TESTERSON)
    in_folder(world)
    before = world.tree()
    real = pipeline.content_fingerprint
    seen = []

    def unreadable(source: Any) -> str:
        if isinstance(source, Path):
            seen.append(source)
            raise ValueError("a damaged file")
        return real(source)

    monkeypatch.setattr(pipeline, "content_fingerprint", unreadable)
    world.add_row("Testerson_101426")
    world.run_until(lambda: seen, 45)
    assert "can't be read" in pipeline.blocked_reason(world.store, "4242")
    assert world.tree() == before and world.row("Testerson_101426").state == "matched"


def test_a_pdf_that_is_not_a_summary_is_not_taken_for_one(world):
    world.installed(TESTERSON)
    in_folder(world, name="Assembly print.pdf", data=summary_pdf(project_name=None))
    world.add_row("Testerson_101426")
    world.run_until(lambda: world.row("Testerson_101426").state == "filed", 45)
    assert three_files("Testerson_101426") < set(world.tree())


@pytest.mark.parametrize("old", [False, True], ids=["at the top", "in Old"])
def test_a_different_summary_there_raises_the_card_and_is_never_overwritten(world, old):
    world.installed(TESTERSON)
    in_folder(world, old=old, data=pdf_of(designs=TWO))
    before = world.tree()
    world.add_row("Testerson_101426")
    world.run_until(lambda: world.cards("ic_changed"), 45)
    world.run(30)
    card, = world.cards("ic_changed")
    assert card.studio_id == "4242" and world.tree() == before
    assert world.row("Testerson_101426").state == "matched"
    assert not (old and world.folder("Testerson_101426").exists())
    assert card.text.count("page") == 2 and "Primary" not in card.text


def test_the_card_says_what_differs_from_the_summary_already_there(world):
    world.installed(TESTERSON)
    in_folder(world, data=pdf_of(designs=TWO))
    world.add_row("Testerson_101426")
    world.run_until(lambda: world.cards("ic_changed"), 45)
    card, = world.cards("ic_changed")
    assert re.search(r"\d+ lines? of text", card.text)
    assert "catalog" not in card.text


def test_what_changed_never_holds_up_a_card():
    assert pipeline._what_changed(None, b"anything") == ""
    assert pipeline._what_changed(b"not a pdf", b"not a pdf either") == ""
    assert pipeline._what_changed(Path("no such file.pdf"), b"not a pdf") == ""


def test_keep_the_filed_one_marks_it_filed_with_no_write_and_asks_no_more(world):
    world.installed(TESTERSON)
    in_folder(world, data=pdf_of(designs=TWO))
    before = world.tree()
    world.add_row("Testerson_101426")
    world.run_until(lambda: world.cards("ic_changed"), 45)
    world.answer(world.cards("ic_changed")[0], "Keep the filed one")
    world.run(2)
    assert world.row("Testerson_101426").state == "filed" and world.tree() == before
    assert len(world.sent("barbara", "Filed as")) == 1
    world.night()
    world.night()
    assert world.cards("ic_changed", state=None)[0].state == "answered"
    assert len(world.cards("ic_changed", state=None)) == 1


def test_file_the_new_one_over_a_hand_filed_summary_is_refused_and_says_why(world):
    world.installed(TESTERSON)
    in_folder(world, data=pdf_of(designs=TWO))
    before = world.tree()
    world.add_row("Testerson_101426")
    world.run_until(lambda: world.cards("ic_changed"), 45)
    world.answer(world.cards("ic_changed")[0], "File the new one")
    world.run(3)
    assert world.tree() == before                                # the chokepoint's own rule
    op, = world.ops(pipeline.FILE_NEW)
    assert op.note and pipeline.blocked_reason(world.store, "4242") == op.note
    assert world.row("Testerson_101426").state == "matched"


def test_a_summary_in_the_other_layout_is_compared_with_a_fetched_pair_and_the_job_keeps_it(world):
    world.installed(TESTERSON)
    in_folder(world, data=pdf_of(layout="portrait"))
    before = world.tree()
    world.add_row("Testerson_101426")
    world.run_until(lambda: world.row("Testerson_101426").state == "filed", 60)
    assert world.tree() == before and world.cards(state=None) == []
    assert world.studio.fetches("4242", "portrait") == 2
    assert world.renders("4242")[-1].layout == "portrait"
    assert world.renders("4242")[-1].governing
    landscape = world.studio.fetches("4242", "landscape")
    world.clock.advance(timedelta(days=1))
    world.tick()
    assert world.studio.fetches("4242", "landscape") == landscape  # it keeps portrait
    assert world.studio.fetches("4242", "portrait") == 3


def test_a_summary_in_the_other_layout_with_other_content_raises_the_card(world):
    world.installed(TESTERSON)
    in_folder(world, data=pdf_of(layout="portrait", designs=TWO))
    world.add_row("Testerson_101426")
    world.run_until(lambda: world.cards("ic_changed"), 60)
    assert world.studio.fetches("4242", "portrait") == 2
    assert world.renders("4242")[-1].layout == "portrait"


def test_an_observed_jobs_folder_gets_no_writes_and_the_row_says_why(world):
    world.folder("Testerson_101426").mkdir()
    world.installed(TESTERSON)
    world.add_row("Testerson_101426")
    world.run_until(lambda: len(world.renders("4242")) >= 2, 45)
    world.run(5)
    assert world.job("4242").mode == "observed"
    assert world.tree() == {}
    assert world.ops(pipeline.FILE)[0].note is None
    assert "installed" in world.row("Testerson_101426").reason


def test_an_observed_job_is_found_by_its_garage_folder_too(world):
    world.folder("Testerson_101426_Gar").mkdir()
    world.installed(TESTERSON, designs=GARAGE)
    world.add_row("Testerson_101426")
    world.run_until(lambda: len(world.renders("4242")) >= 2, 45)
    world.run(5)
    assert world.job("4242").mode == "observed" and world.tree() == {}


def test_a_same_summary_arriving_in_an_observed_folder_still_marks_it_filed(world):
    world.folder("Testerson_101426").mkdir()
    world.installed(TESTERSON)
    world.add_row("Testerson_101426")
    world.run_until(lambda: len(world.renders("4242")) >= 2, 45)
    world.run(2)
    in_folder(world)
    before = world.tree()
    world.run(70, step=5)
    assert world.row("Testerson_101426").state == "filed" and world.tree() == before


def test_a_folder_only_in_old_gets_no_write_and_no_second_folder_at_the_top(world):
    world.installed(TESTERSON)
    (world.old / "Testerson_101426").mkdir()
    world.add_row("Testerson_101426")
    world.run_until(lambda: len(world.renders("4242")) >= 2, 45)
    world.run(5)
    assert world.tree() == {}
    assert not world.folder("Testerson_101426").exists()
    assert "Old" in pipeline.blocked_reason(world.store, "4242")


# ----- step 6: a commit, and a refusal


def test_filing_writes_three_new_files_and_notes_what_it_filed(world):
    world.installed(TESTERSON, designs=TWO)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    folder = "Testerson_101426"
    assert set(world.tree()) == three_files(folder)
    first, second = world.renders("4242")
    assert world.folder(folder).joinpath(f"{folder}_summary.pdf").read_bytes() == (
        second.cache_path.read_bytes())
    record = world.notes(folder)
    assert list(record) == ["schema", "job", "branch", "summary", "designs", "designs_checked",
                            "filed_at", "items", "note"]
    assert record["schema"] == 1 and record["job"] == folder and record["branch"] == "closet"
    assert record["designs"] == 2 and record["designs_checked"] is True
    assert record["summary"] == {
        "file": f"{folder}_summary.pdf", "sha256": second.sha256, "layout": "landscape",
        "pages": second.page_count,
        "fetched": [first.fetched_at.isoformat(), second.fetched_at.isoformat()]}
    assert record["items"] == [] and "Chat sessions read this file" in record["note"]
    log = world.folder(folder).joinpath(f"{folder}_NGRunner_log.txt").read_text(encoding="utf-8")
    assert re.fullmatch(rf"10/27 \d+:\d\d AM  Filed the IC summary \(landscape, 2 designs,"
                        rf" {second.page_count} pages\), checked on two fetches 10 minutes apart\.\n",
                        log)
    job = world.job("4242")
    assert job.name_frozen and job.folder == folder
    assert world.row("Testerson").state == "filed"
    assert world.ops(pipeline.REFETCH)
    event, = world.events("filed")
    assert event.studio_id == "4242" and event.lane == "LOG"
    assert "Filed the IC summary" in event.text
    written = [r for r in list_write_records(world.store) if r.ok]
    assert len(written) == 1 and len(written[0].paths) == 3


def test_barbara_is_told_once_and_her_row_is_quoted(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    world.run(5)
    email, = world.sent("barbara")
    pages = world.renders("4242")[-1].page_count
    assert email.subject == f"Filed as Testerson_101426: 1 design, {pages} pages"
    assert 'Fab row "Testerson"' in email.lines[0] and email.ref.startswith("filed:")
    assert email.lines[0].endswith(f"is filed as Testerson_101426: 1 design, {pages} pages.")


def test_a_garage_filing_says_so_to_barbara(world):
    world.installed(TESTERSON, designs=GARAGE)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    email, = world.sent("barbara")
    assert "(garage)" in email.lines[0] and email.subject.startswith("Filed as Testerson_101426:")


def test_a_refusal_leaves_the_job_blocked_with_the_reason_and_is_retried(world):
    undated = opp("4242", "Testerson_Job")
    world.installed(undated)
    world.add_row("Testerson_Job")
    world.run_until(lambda: world.ops(pipeline.FILE), 45)
    world.run(1)
    op, = world.ops(pipeline.FILE)
    assert "does not look like a job folder" in op.note
    assert world.row("Testerson_Job").state == "matched"
    assert not world.folder("Testerson_Job").exists()
    assert len(world.events("blocked")) == 1
    world.run(20)
    assert world.ops(pipeline.FILE)[0].attempts > op.attempts    # tried again, logged once
    assert len(world.events("blocked")) == 1


def test_paused_nothing_is_filed_and_resumed_it_carries_on(world):
    to_second_fetch(world)
    set_state(world.store, PAUSED, "yes")
    world.run(30)
    assert world.tree() == {}
    assert world.ops(pipeline.FILE)[0].note is None              # a pause is not a refusal
    assert world.row("Testerson").state == "matched"
    assert world.sent("barbara", "Filed as") == []
    set_state(world.store, PAUSED, "no")
    world.run(2)
    assert set(world.tree()) == three_files("Testerson_101426")
    assert world.row("Testerson").state == "filed"


def test_while_paused_rows_are_still_matched_and_asked_about(world):
    world.installed(TESTERSON, TESTERSON_2)
    set_state(world.store, PAUSED, "yes")
    world.add_row("Testerson")
    world.run(15)
    assert world.cards("which_job") and len(world.sent("barbara", "Which job")) == 1


def test_with_ng_jobs_unreachable_at_filing_the_job_is_blocked_and_retried(world, tmp_path):
    to_second_fetch(world)
    set_state(world.store, PAUSED, "yes")
    second_due(world)
    world.tick()                                                 # agreed, and waiting to file
    moved = tmp_path / "away"
    world.ng_jobs.rename(moved)
    set_state(world.store, PAUSED, "no")
    world.run(3)
    assert world.ops(pipeline.FILE)[0].note == pipeline._NOT_REACHABLE
    moved.rename(world.ng_jobs)
    world.run(6)
    assert set(world.tree()) == three_files("Testerson_101426")


def test_a_cached_file_gone_before_filing_fetches_a_pair_again(world):
    to_second_fetch(world)
    set_state(world.store, PAUSED, "yes")
    second_due(world)
    world.tick()
    world.run(1)
    for render in world.renders("4242"):
        render.cache_path.unlink(missing_ok=True)
    set_state(world.store, PAUSED, "no")
    world.run(1)
    assert world.tree() == {} and world.ops(pipeline.FETCH_1)
    world.run_until(lambda: world.row("Testerson").state == "filed", 30)


# ----- shadow mode


@pytest.fixture
def shadow(tmp_path: Path) -> World:
    return World(tmp_path, mode="shadow")


def test_in_shadow_the_files_land_in_the_control_folder_and_ng_jobs_is_untouched(shadow):
    shadow.installed(TESTERSON)
    shadow.add_row("Testerson")
    shadow.run_until(lambda: shadow.row("Testerson").state == "filed", 45)
    aside = shadow.control / "shadow"
    assert set(shadow.tree(aside)) == three_files("Testerson_101426")
    assert shadow.tree() == {} and not shadow.folder("Testerson_101426").exists()
    assert shadow.job("4242").folder == "Testerson_101426"
    assert pipeline.filed_facts(shadow.store, "4242")["where"] == "shadow"
    assert shadow.notes("Testerson_101426", aside)["job"] == "Testerson_101426"


def test_in_shadow_a_summary_in_ng_jobs_is_still_read_and_nothing_written(shadow):
    shadow.installed(TESTERSON)
    in_folder(shadow)
    shadow.add_row("Testerson_101426")
    shadow.run_until(lambda: shadow.row("Testerson_101426").state == "filed", 45)
    assert shadow.tree(shadow.control / "shadow") == {}


def test_switching_to_live_files_the_next_row_for_real_and_leaves_the_last_as_it_was(shadow):
    shadow.installed(TESTERSON, FAKELEY)
    shadow.add_row("Testerson")
    shadow.run_until(lambda: shadow.row("Testerson").state == "filed", 45)
    shadow.ctx.mode = "live"
    shadow.add_row("Fakeley")
    shadow.run_until(lambda: shadow.row("Fakeley").state == "filed", 45)
    assert set(shadow.tree()) == three_files("Fakeley_101426")
    assert set(shadow.tree(shadow.control / "shadow")) == three_files("Testerson_101426")
    assert not (shadow.control / "shadow" / "Fakeley_101426").exists()


def test_a_pruned_shadow_folder_is_not_not_filed_and_blocks_nothing(shadow):
    import shutil

    shadow.installed(TESTERSON)
    shadow.add_row("Testerson")
    shadow.run_until(lambda: shadow.row("Testerson").state == "filed", 45)
    shutil.rmtree(shadow.control / "shadow" / "Testerson_101426")
    # A nightly change opens a card, so the notes would be rewritten if they were there.
    shadow.studio.jobs["4242"].designs = TWO
    shadow.night()
    assert shadow.cards("ic_changed")
    shadow.run(10)
    assert shadow.row("Testerson").state == "filed"
    assert shadow.ops(pipeline.FILE) == [] and pipeline.blocked_reason(shadow.store, "4242") is None
    assert shadow.dev("notes_refused") == [] and shadow.events("notes_gone")
    assert shadow.tree(shadow.control / "shadow") == {}


def test_a_pause_stops_shadow_filing_too(shadow):
    to_second_fetch(shadow)
    set_state(shadow.store, PAUSED, "yes")
    shadow.run(30)
    assert shadow.tree(shadow.control) == {f"fab/{n}": h for n, h in [
        ("export.json", shadow.tree(shadow.control)["fab/export.json"])]}


# ----- section 4: keeping the notes current


def changed_at_night(world: World) -> Any:
    """A filed job whose IC summary changes, confirmed by two fetches the next night."""
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    world.studio.jobs["4242"].designs = TWO
    world.night()
    return world.cards("ic_changed")[0]


def test_a_card_opening_and_closing_rewrites_the_notes_and_they_stay_owned(world):
    card = changed_at_night(world)
    folder = "Testerson_101426"
    item, = world.notes(folder)["items"]
    assert item["card"] == card.id and item["kind"] == "ic_changed"
    assert item["question"] == templates.QUESTIONS["ic_changed"]
    path = world.folder(folder) / f"{folder}_NGRunner.json"
    assert owned_sha(world.store, Path(pipeline._resolved(world.ng_jobs)) / folder / path.name) == (
        hashlib.sha256(path.read_bytes()).hexdigest())
    world.answer(card, "Keep the filed one")
    world.run(2)
    assert world.notes(folder)["items"] == []
    assert world.dev("notes_refused") == []


def test_notes_edited_by_hand_are_refused_logged_and_block_nothing(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    folder = "Testerson_101426"
    path = world.folder(folder) / f"{folder}_NGRunner.json"
    path.write_text("{}", encoding="utf-8")
    world.studio.jobs["4242"].designs = TWO
    world.night()
    assert world.cards("ic_changed")
    assert path.read_text(encoding="utf-8") == "{}"
    assert world.dev("notes_refused")[0].studio_id == "4242"
    assert world.ops(pipeline.FILE) == []
    refused = sum(not r.ok for r in list_write_records(world.store))
    world.run(20)
    assert sum(not r.ok for r in list_write_records(world.store)) == refused  # not every minute


def test_a_summary_replaced_by_hand_is_logged_never_overwritten_and_the_notes_describe_it(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    folder = "Testerson_101426"
    summary = world.folder(folder) / f"{folder}_summary.pdf"
    mine = pdf_of(designs=TWO, layout="portrait")
    summary.write_bytes(mine)
    world.run(12)
    assert summary.read_bytes() == mine
    record = world.notes(folder)
    assert record["summary"]["sha256"] == hashlib.sha256(mine).hexdigest()
    assert record["summary"]["replaced_by_hand"] is True
    assert record["summary"]["layout"] == "portrait"
    assert record["summary"]["pages"] == check_summary(mine).page_count
    assert pipeline.filed_facts(world.store, "4242")["replaced_by_hand"] is True
    assert len(world.events("summary_replaced")) == 1
    log = (world.folder(folder) / f"{folder}_NGRunner_log.txt").read_text(encoding="utf-8")
    assert "replaced by hand" in log.splitlines()[-1] and len(log.splitlines()) == 2
    world.run(30, step=5)
    assert len(world.events("summary_replaced")) == 1
    assert world.row("Testerson").state == "filed"


def test_a_summary_touched_but_not_changed_is_left_alone(world):
    import os

    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    folder = "Testerson_101426"
    summary = world.folder(folder) / f"{folder}_summary.pdf"
    os.utime(summary, (1, 1))
    before = world.tree()
    world.run(12)
    assert world.tree() == before and world.events("summary_replaced") == []


def test_notes_out_of_sight_are_rewritten_once_they_are_back_and_not_called_gone(world, tmp_path):
    card = changed_at_night(world)
    moved = tmp_path / "away"
    world.ng_jobs.rename(moved)
    world.answer(card, "Keep the filed one")
    world.run(3)
    assert world.events("notes_gone") == [] and world.dev("pipeline_step") == []
    moved.rename(world.ng_jobs)
    world.run(1)
    assert world.notes("Testerson_101426")["items"] == []


def test_a_folder_moved_to_old_after_filing_is_no_problem(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    world.folder("Testerson_101426").rename(world.old / "Testerson_101426")
    world.run(15)
    assert world.row("Testerson").state == "filed"
    assert world.events("summary_replaced") == [] and world.dev() == []


# ----- from filing on: the nightly re-fetch


def test_the_night_re_fetch_of_the_same_content_changes_nothing(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    before, fetches = world.tree(), world.studio.fetches("4242")
    op, = world.ops(pipeline.REFETCH)
    assert op.due_at.isoformat() == "2026-10-28T08:30:00+00:00"  # 1:30 AM Pacific
    assert op.deadline_at is None
    world.clock.set(op.due_at)
    world.tick()
    assert world.studio.fetches("4242") == fetches + 1
    assert world.tree() == before and world.cards(state=None) == []
    assert world.ops(pipeline.REFETCH_2) == []
    assert world.ops(pipeline.REFETCH)[0].due_at.isoformat() == "2026-10-29T08:30:00+00:00"


def test_changed_content_needs_two_agreeing_fetches_and_then_raises_one_card(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    before = world.tree()
    world.studio.jobs["4242"].designs = TWO
    op, = world.ops(pipeline.REFETCH)
    world.clock.set(op.due_at)
    world.tick()
    assert world.cards("ic_changed") == []                       # one fetch proves nothing
    confirm, = world.ops(pipeline.REFETCH_2)
    assert confirm.due_at == world.renders("4242")[-1].fetched_at + minutes(10)
    world.clock.set(confirm.due_at)
    world.tick()
    card, = world.cards("ic_changed")
    assert card.studio_id == "4242" and card.choices == ("Keep the filed one", "File the new one")
    assert re.search(r"\d+ lines? of text", card.text)                  # says what differs
    assert "Testerson_101426" in world.events("card_opened")[-1].text or card.text
    world.run(1)
    assert but_the_notes(world.tree()) == but_the_notes(before)  # nothing re-filed by itself
    world.night()
    world.night()
    assert len(world.cards("ic_changed", state=None)) == 1


def test_a_change_that_goes_away_before_it_is_confirmed_raises_nothing(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    world.studio.jobs["4242"].designs = TWO
    world.clock.set(world.ops(pipeline.REFETCH)[0].due_at)
    world.tick()
    world.studio.jobs["4242"].designs = CLOSET
    world.run(60, step=10)
    assert world.cards("ic_changed", state=None) == []


def test_keep_the_filed_one_is_not_asked_again_for_that_content_but_is_for_new_content(world):
    card = changed_at_night(world)
    world.answer(card, "Keep the filed one")
    world.run(1)
    world.night()
    world.night()
    assert len(world.cards("ic_changed", state=None)) == 1
    world.studio.jobs["4242"].designs = MIXED
    world.night()
    assert len(world.cards("ic_changed", state=None)) == 2


def test_file_the_new_one_replaces_its_own_summary_and_keeps_the_notes_true(world):
    card = changed_at_night(world)
    folder = "Testerson_101426"
    world.answer(card, "File the new one")
    world.run(2)
    new = world.renders("4242")[-1]
    summary = world.folder(folder) / f"{folder}_summary.pdf"
    assert content_fingerprint(summary.read_bytes()) == new.fingerprint
    record = world.notes(folder)
    assert record["summary"]["sha256"] == hashlib.sha256(summary.read_bytes()).hexdigest()
    assert record["items"] == []
    assert any(r.governing and r.fingerprint == new.fingerprint for r in world.renders("4242"))
    assert world.ops(pipeline.FILE_NEW) == []
    log = (world.folder(folder) / f"{folder}_NGRunner_log.txt").read_text(encoding="utf-8")
    assert "as Jim chose" in log.splitlines()[-1]
    assert len(world.sent("barbara", "Filed as")) == 1
    world.night()
    world.night()
    assert len(world.cards("ic_changed", state=None)) == 1       # it now governs: no second card


def test_a_fingerprint_read_by_another_pypdf_is_read_again_and_raises_no_false_card(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    governing = world.renders("4242")[-1]
    import sqlite3

    with sqlite3.connect(world.store.path) as conn:
        conn.execute("UPDATE renders SET fingerprint = 'old', pypdf = '0.0' WHERE id = ?",
                     (governing.id,))
    world.clock.set(world.ops(pipeline.REFETCH)[0].due_at)
    world.tick()
    assert world.ops(pipeline.REFETCH_2) == [] and world.cards(state=None) == []
    assert get_render(world.store, governing.id).fingerprint == governing.fingerprint


def test_a_fingerprint_that_cant_be_read_again_is_could_not_verify_and_raises_nothing(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    governing = world.renders("4242")[-1]
    import sqlite3

    with sqlite3.connect(world.store.path) as conn:
        conn.execute("UPDATE renders SET fingerprint = 'old', pypdf = '0.0' WHERE id = ?",
                     (governing.id,))
    governing.cache_path.unlink()
    world.studio.jobs["4242"].designs = TWO
    world.clock.set(world.ops(pipeline.REFETCH)[0].due_at)
    world.tick()
    assert world.ops(pipeline.REFETCH_2) == [] and world.cards(state=None) == []


def test_the_re_fetch_ends_after_30_days(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    filed_at = world.clock.now()
    for _ in range(40):
        ops = world.ops(pipeline.REFETCH)
        if not ops:
            break
        world.clock.set(ops[0].due_at)
        world.tick()
    assert world.ops(pipeline.REFETCH) == []
    assert world.clock.now() - filed_at >= timedelta(days=30)
    assert world.clock.now() - filed_at < timedelta(days=32)


def test_a_failed_night_tries_again_the_next_night_with_no_card(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    world.studio.raises = requests.ConnectionError()
    op, = world.ops(pipeline.REFETCH)
    world.clock.set(op.due_at)
    world.tick()
    assert world.cards(state=None) == [] and not any(d.studio_id for d in world.dev())
    assert world.ops(pipeline.REFETCH)[0].due_at == op.due_at + timedelta(days=1)


def test_a_rename_after_filing_is_an_alias_and_the_folder_is_never_renamed(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    before = world.tree()
    world.render.opportunities = [opp("4242", "Testerson_102026")]
    world.render.synced_at = world.clock.now()
    world.studio.jobs["4242"].printed = "Testerson_102026"
    op, = world.ops(pipeline.REFETCH)
    world.clock.set(op.due_at)
    world.tick()                                                 # a new list, and a wrong_job
    assert [a.name for a in list_aliases(world.store, "4242")] == ["Testerson_102026"]
    assert world.job("4242").name == "Testerson_101426"          # the frozen name stands
    assert world.events("renamed") and world.dev("wrong_job") == []
    assert world.ops(pipeline.REFETCH)[0].due_at <= world.clock.now()  # at once, under the new name
    world.studio.asked.clear()
    world.run(1)
    assert world.studio.asked and world.dev("wrong_job") == []
    # The name is on page 1, so this is changed content like any other: one card, Jim's to settle.
    world.clock.set(world.ops(pipeline.REFETCH_2)[0].due_at)
    world.tick()
    assert len(world.cards("ic_changed")) == 1
    world.run(1)
    summary = "Testerson_101426/Testerson_101426_summary.pdf"
    assert world.tree()[summary] == before[summary] and set(world.tree()) == set(before)
    assert world.job("4242").folder == "Testerson_101426"


def test_a_wrong_job_at_night_with_no_rename_is_a_dev_item(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    world.studio.jobs["4242"].printed = "Fakeley_101426"
    world.clock.set(world.ops(pipeline.REFETCH)[0].due_at)
    world.tick()
    assert world.dev("wrong_job")[0].studio_id == "4242"
    assert world.job("4242").aliases == ()


# ----- a filed row that is edited


def but_the_notes(tree: dict[str, str]) -> dict[str, str]:
    return {path: sha for path, sha in tree.items() if "_NGRunner.json" not in path}


def filed_row(world: World, text: str = "Testerson", install: tuple = FUTURE_INSTALL) -> None:
    world.installed(TESTERSON, TESTERSON_2 if text != "Testerson" else FAKELEY)
    world.add_row(text, install=install)
    world.run_until(lambda: world.row(text).state == "filed", 45)


def test_a_filed_row_rematched_to_its_own_job_raises_nothing(world):
    filed_row(world)
    world.correct("Testerson", job="Testerson closet")
    world.run(10)
    assert world.row("Testerson closet").state == "filed"
    assert world.cards(state=None) == []
    assert world.ops(pipeline.LIST_REMATCH) == []
    assert world.row("Testerson closet").studio_id == "4242"


def test_a_filed_row_that_reads_as_another_job_asks_and_files_nothing_new(world):
    filed_row(world)
    before = world.tree()
    world.correct("Testerson", job="Fakeley")
    world.run(10)
    card, = world.cards("row_changed")
    assert card.studio_id == "4242" and card.choices[0] == "Keep Testerson_101426"
    assert "4343" in card.choice_ids and card.choices[-1] == templates.NONE_OF_THESE
    assert card.choice_ids[0] is None and card.choice_ids[-1] is None
    assert but_the_notes(world.tree()) == but_the_notes(before) and world.job("4343") is None
    assert world.notes("Testerson_101426")["items"][0]["card"] == card.id
    assert 'Fab row "Testerson" now reads "Fakeley"' in card.text


def test_a_row_change_waits_for_a_list_read_after_it(world):
    filed_row(world)
    world.render.answers_push = False
    world.clock.advance(minutes(1))
    world.correct("Testerson", job="Fakeley")
    world.run(10)
    assert world.cards(state=None) == [] and world.ops(pipeline.LIST_REMATCH)
    assert world.ops(pipeline.LIST_REMATCH)[0].note == "Testerson"
    world.render.synced_at = world.clock.now()
    world.run(1)
    assert world.cards("row_changed")


def test_keep_the_job_leaves_the_row_filed_to_it(world):
    filed_row(world)
    world.correct("Testerson", job="Fakeley")
    world.run(10)
    card, = world.cards("row_changed")
    world.answer(card, card.choices[0])
    world.run(3)
    row = world.row("Fakeley")
    assert row.state == "filed" and row.studio_id == "4242" and world.job("4343") is None


def test_answering_with_another_job_files_that_one(world):
    filed_row(world)
    world.correct("Testerson", job="Fakeley")
    world.run(10)
    card, = world.cards("row_changed")
    world.answer(card, card.choices[card.choice_ids.index("4343")])
    world.run_until(lambda: world.row("Fakeley").studio_id == "4343"
                    and world.row("Fakeley").state == "filed", 45)
    assert three_files("Fakeley_101426") <= set(world.tree())
    assert three_files("Testerson_101426") <= set(world.tree())


def test_none_of_these_on_a_changed_row_sets_it_to_none(world):
    filed_row(world)
    world.correct("Testerson", job="Fakeley")
    world.run(10)
    card, = world.cards("row_changed")
    world.answer(card, templates.NONE_OF_THESE)
    world.run(3)
    assert world.row("Fakeley").state == "none" and world.row("Fakeley").studio_id is None
    assert world.job("4242").folder == "Testerson_101426"


def test_a_matched_rows_new_text_holds_its_filing_until_settled(world):
    to_second_fetch(world)
    world.render.answers_push = False
    world.clock.advance(minutes(1))
    world.correct("Testerson", job="Fakeley")
    world.export()
    second_due(world)
    world.tick()
    world.run(5)
    assert world.tree() == {}
    assert world.ops(pipeline.LIST_REMATCH) and world.ops(pipeline.FILE)
    world.render.opportunities = [TESTERSON, FAKELEY]
    world.render.synced_at = world.clock.now()
    world.run(1)
    card, = world.cards("row_changed")
    world.run(6)
    assert world.tree() == {}
    world.answer(card, card.choices[0])                          # keep the job: it files
    world.run(2)
    assert set(world.tree()) == three_files("Testerson_101426")


def past_install() -> tuple[str, str]:
    return ("", "10/20/26")


def test_only_the_install_date_changing_after_it_passed_asks_if_it_is_a_remake(world):
    filed_row(world, install=past_install())
    before = world.tree()
    world.correct("Testerson", install=("", "11/30/26"))
    world.run(6)
    card, = world.cards("remake")
    assert world.cards("row_changed") == []
    assert card.choices == templates.REMAKE_CHOICES
    assert card.text.startswith("Is this a remake of Testerson_101426?")
    assert card.studio_id == "4242" and card.fab_row_id == world.row("Testerson").id
    assert world.job("4242").install is not None
    assert but_the_notes(world.tree()) == but_the_notes(before)
    world.run(10)
    assert len(world.cards("remake", state=None)) == 1


def test_the_same_job_absorbs_the_edit_and_a_remake_is_left_for_chat(world):
    filed_row(world, install=past_install())
    world.correct("Testerson", install=("", "11/30/26"))
    world.run(6)
    world.answer(world.cards("remake")[0], templates.REMAKE_CHOICES[1])
    world.run(2)
    assert world.job("4242").install == ("", "11/30/26")
    assert world.events("remake") == []


def test_a_remake_answer_is_logged_for_jim_and_files_nothing(world):
    filed_row(world, install=past_install())
    before = world.tree()
    world.correct("Testerson", designer="LS")
    world.run(6)
    assert world.cards("remake")
    world.answer(world.cards("remake")[0], templates.REMAKE_CHOICES[0])
    world.run(2)
    assert world.events("remake")[0].lane == "ACT"
    assert "chat" in world.events("remake")[0].text
    assert but_the_notes(world.tree()) == but_the_notes(before)


def test_an_install_date_still_to_come_is_absorbed_as_an_edit(world):
    filed_row(world)
    world.correct("Testerson", install=("", "12/15/26"))
    world.run(6)
    assert world.cards(state=None) == []
    assert world.job("4242").install == ("", "12/15/26")
    assert world.row("Testerson").state == "filed"


def test_an_install_date_that_cant_be_read_is_no_reason_to_ask(world):
    filed_row(world, install=("TBD", ""))
    world.correct("Testerson", install=("", "12/15/26"))
    world.run(6)
    assert world.cards(state=None) == []


def test_a_second_row_for_a_filed_job_is_told_and_files_nothing(world):
    filed_row(world, "Testerson_101426")
    before = world.tree()
    world.add_row("Testerson_101426", designer="LS")
    world.run(15)
    rows = [r for r in world.rows() if r.text == "Testerson_101426"]
    assert [r.state for r in rows] == ["filed", "filed"]
    assert world.tree() == before
    assert len(world.sent("barbara", "Filed as")) == 2


# ----- Add job


def test_add_job_by_exact_name_files_it_with_no_email_to_barbara(world):
    world.installed(TESTERSON)
    said = add_job(world.ctx, name="Testerson_101426")
    assert "Testerson_101426" in said and "4242" not in said
    world.run_until(lambda: world.job("4242").folder is not None, 45)
    assert set(world.tree()) == three_files("Testerson_101426")
    world.run(3)
    assert world.sent() == []
    assert world.job("4242").name_frozen


def test_add_job_by_studio_id_found_in_the_list(world):
    world.installed(TESTERSON)
    add_job(world.ctx, studio_id="4242")
    assert world.ops(pipeline.FETCH_1)
    world.run_until(lambda: world.job("4242").folder is not None, 45)


def test_add_job_with_digits_for_a_name_means_a_studio_id(world):
    world.installed(TESTERSON)
    add_job(world.ctx, name="4242")
    assert world.job("4242") is not None


@pytest.mark.parametrize("kwargs, words", [
    ({"name": "Testerson"}, "named exactly"),
    ({"name": "Dummitt_101426"}, "named exactly"),
    ({"studio_id": "9999"}, "not in the job list"),
], ids=["a bare surname is no exact name", "no such name", "no such Studio ID"])
def test_add_job_refuses_what_is_not_exactly_a_job_and_says_so(world, kwargs, words):
    world.installed(TESTERSON)
    said = add_job(world.ctx, **kwargs)
    assert words in said
    assert world.job("4242") is None and world.ops(pipeline.FETCH_1) == []
    assert world.events("add_job")[0].lane == "ACT" and "9999" not in said


def test_add_job_before_any_list_says_so(world):
    assert "not been read" in add_job(world.ctx, name="Testerson_101426")


def test_add_job_of_a_filed_job_says_so_and_starts_nothing(world):
    filed_row(world)
    assert "already filed" in add_job(world.ctx, name="Testerson_101426")
    assert world.ops(pipeline.FETCH_1) == []


def test_add_job_for_a_folder_found_at_install_is_observed(world):
    world.folder("Testerson_101426").mkdir()
    world.installed(TESTERSON)
    add_job(world.ctx, name="Testerson_101426")
    assert world.job("4242").mode == "observed"


def test_add_job_twice_starts_one_fetch(world):
    world.installed(TESTERSON)
    add_job(world.ctx, name="Testerson_101426")
    add_job(world.ctx, name="Testerson_101426")
    assert len(world.ops(pipeline.FETCH_1)) == 1


# ----- one bad row, and what leaves the PC


@pytest.mark.parametrize("words", [pipeline._CHOICE_GONE, pipeline._OBSERVED_REASON,
                                   pipeline._OLD_REASON, pipeline._NOT_REACHABLE],
                         ids=["choice", "observed", "old", "reach"])
def test_what_the_board_shows_of_a_row_or_a_refusal_passes_the_lint(words):
    assert check_line(" ".join(words.split()).rstrip(".")) in ([], None, "")


def test_one_row_that_fails_never_stops_the_others(world, monkeypatch):
    world.installed(TESTERSON, FAKELEY)
    world.add_row("Dummitt")
    world.add_row("Testerson")
    real = pipeline.match_fab_row

    def failing(row, *args, **kwargs):
        if row.text == "Dummitt":
            raise ValueError("a row that can't be read")
        return real(row, *args, **kwargs)

    monkeypatch.setattr(pipeline, "match_fab_row", failing)
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    item, = world.dev("pipeline_step")
    assert item.text == "match rows: ValueError"
    assert world.row("Dummitt").state == "new"


def test_a_send_that_fails_is_tried_again_and_goes_once(world):
    world.installed(TESTERSON)
    world.messenger.broken = True
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert world.sent() == [] and world.ops(pipeline.TELL_FILED)
    world.messenger.broken = False
    world.run(3)
    assert len(world.sent("barbara", "Filed as")) == 1
    assert world.ops(pipeline.TELL_FILED) == []
    done, = world.ops(pipeline.TELL_FILED, state="done")
    assert get_state(world.store, f"tell.{done.id}") is None


def test_a_message_held_through_quiet_hours_counts_as_sent(world):
    # The messenger holds it with no row yet, so a restart finds its ref there, not in the outbox.
    world.installed(TESTERSON)
    message = templates.filed("Testerson_101426", row="Testerson")
    with world.store.transaction() as tx:
        pipeline._queue_tell(tx, pipeline.TELL_FILED, message, to="barbara", ref="filed:1",
                             now=world.clock.now())
    world.messenger.held = lambda: [{"ref": "filed:1"}]
    world.tick()
    assert world.sent() == [] and world.ops(pipeline.TELL_FILED) == []


def test_every_card_the_pipeline_raises_passes_the_lint(world):
    world.installed(TESTERSON, TESTERSON_2, FAKELEY, designs=CLOSET)
    world.studio.jobs["4343"].printed = "Dummitt_101426"
    world.add_row("Testerson")
    world.add_row("Dummitt 10/14/26 x")
    world.add_row("Fakeley")
    world.run_until(lambda: len(world.cards("which_job")) == 2 and world.cards("fetch_failed"), 30)
    for record in world.cards():
        rows = {r.id: r.text for r in world.rows()}
        job = world.job(record.studio_id).name if record.studio_id else None
        card = card_from_record(record, job=job, row=rows.get(record.fab_row_id))
        assert lint(templates.card_message(card)) == [], card


def test_the_remake_card_passes_the_lint_for_every_form_of_row():
    from datetime import datetime as dt

    from ngrunner.cards import new_card

    for row in ("Testerson", "Testerson 10/14/26", "Testerson_10142", "Testerson_101426_Gar"):
        spec = templates.remake_card("Testerson_101426", row=row)
        card = new_card(spec, created_at=dt(2026, 10, 30, tzinfo=UTC), allocate=lambda prefix: 1)
        assert lint(templates.card_message(card)) == [], row


# ----- the Studio design check (AGENTS.md: Before filing goes live, item 8)


ONE_NAME = ("Testerson - Primary Closet",)
TWO_NAMES = ("Testerson - Primary Closet", "Testerson - Office")
OTHER_NAME = ("Testerson - Pantry",)


def held_on_designs(world: World, proposal: tuple[str, ...] = TWO_NAMES, **options: Any) -> Any:
    """A job whose fetch 1 passed, and whose report and Proposal tab list different designs."""
    world.installed(TESTERSON, **options)
    world.site.proposals["4242"] = proposal
    world.add_row("Testerson")
    world.run_until(lambda: world.cards("design_mismatch"), 45)
    return world.cards("design_mismatch")[0]


def no_files(world: World) -> None:
    assert world.tree() == {} and world.ops(pipeline.FILE) == []
    assert not world.ops(pipeline.FETCH_2)


def test_a_report_that_matches_the_proposal_files_and_says_it_was_checked(world):
    world.installed(TESTERSON, designs=TWO)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    record = world.notes("Testerson_101426")
    assert record["designs"] == 2 and record["designs_checked"] is True
    assert world.cards(state=None) == [] and world.dev() == []
    assert len(world.site.reads) == 1                            # after fetch 1, and only then
    facts = pipeline.filed_facts(world.store, "4242")
    assert facts["designs"] == 2 and facts["designs_checked"] is True
    assert pipeline.filed_note(world.store, "4242") == (
        f"2 designs, {world.renders('4242')[-1].page_count} pages")


@pytest.mark.parametrize("proposal, words", [
    (TWO_NAMES, "the report lists 1 design and Studio's proposal lists 2."),
    ((), "the report lists 1 design and Studio's proposal lists 0."),
    (OTHER_NAME, "the report lists 1 design and Studio's proposal lists 1, with different names."),
], ids=["report_missing_designs", "proposal_missing_designs", "same_count_other_names"])
def test_a_report_that_differs_never_files_and_one_card_says_so_in_counts(world, proposal, words):
    card = held_on_designs(world, proposal)
    assert card.kind == "design_mismatch" and card.studio_id == "4242"
    assert card.text == f"Testerson_101426: {words}"
    assert card.choices == templates.DESIGN_MISMATCH_CHOICES == ("Fetch it again", "File it as it is")
    assert card.default_choice is None
    made = card_from_record(card, job="Testerson_101426")
    assert lint(templates.card_message(made)) == []
    world.run(180, step=5)
    no_files(world)
    assert len(world.cards("design_mismatch", state=None)) == 1  # one card, however many looks
    assert world.row("Testerson").state == "matched"


def test_a_design_name_reaches_no_card_message_or_file(world):
    world.installed(TESTERSON, designs=TWO)
    world.add_row("Testerson")
    world.site.down = StudioUnavailable("network", "Studio could not be reached.")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    held_row = opp("4343", "Fakeley_101426")
    world.list(held_row, designs=(("Fakeley - Mudroom", "Hall/Storage"),))
    world.site.down = None
    world.site.proposals["4343"] = ("Fakeley - Mudroom", "Fakeley - Pantry")
    world.add_row("Fakeley")
    world.run_until(lambda: world.cards("design_mismatch"), 45)
    world.run(5)
    said = [c.text for c in world.cards(state=None)] + [
        line for m in world.sent() for line in (m.subject, *m.lines)]
    folder = world.folder("Testerson_101426")
    said += [(folder / f"Testerson_101426{end}").read_text(encoding="utf-8")
             for end in ("_NGRunner.json", "_NGRunner_log.txt")]
    words = " ".join(said).casefold()
    assert said and not any(name in words for name in ("primary closet", "office", "mudroom", "pantry"))


def test_only_the_job_that_differs_waits(world):
    world.installed(TESTERSON, FAKELEY)
    world.site.proposals["4242"] = TWO_NAMES
    world.add_row("Testerson")
    world.add_row("Fakeley")
    world.run_until(lambda: world.row("Fakeley").state == "filed", 45)
    assert set(world.tree()) == three_files("Fakeley_101426")
    assert world.row("Testerson").state == "matched" and world.cards("design_mismatch")


def test_it_looks_again_every_30_minutes_and_carries_on_once_they_agree(world):
    card = held_on_designs(world)
    op, = world.ops(pipeline.FETCH_1)
    assert op.deadline_at is None and op.due_at == card.created_at + minutes(30)
    fetched = world.studio.fetches("4242")
    world.clock.set(op.due_at - minutes(1))
    world.tick()
    assert world.studio.fetches("4242") == fetched               # not before it is due
    world.clock.set(op.due_at)
    world.tick()
    assert world.studio.fetches("4242") == fetched + 1
    assert world.ops(pipeline.FETCH_1)[0].due_at == world.clock.now() + minutes(30)
    assert len(world.cards("design_mismatch", state=None)) == 1

    del world.site.proposals["4242"]                             # the proposal is put right
    world.run_until(lambda: world.row("Testerson").state == "filed", 60)
    assert world.cards("design_mismatch") == []
    assert world.cards("design_mismatch", state="withdrawn")
    assert world.events("designs_agree")
    assert world.notes("Testerson_101426")["designs_checked"] is True
    assert world.dev() == []


def test_a_day_of_a_card_nobody_answers_raises_no_fetch_failed_card(world):
    held_on_designs(world)
    world.run(26 * 60, step=30)
    assert world.cards("fetch_failed", state=None) == []
    assert len(world.cards("design_mismatch", state=None)) == 1
    no_files(world)


def test_fetch_it_again_queues_a_fresh_fetch_at_once_and_opens_a_new_card_if_it_still_differs(world):
    card = held_on_designs(world)
    fetched = world.studio.fetches("4242")
    world.answer(card, "Fetch it again")
    world.tick()
    assert world.studio.fetches("4242") == fetched + 1
    open_now, = world.cards("design_mismatch")
    assert open_now.id != card.id
    no_files(world)
    assert world.events("designs_differ_filed") == []
    world.answer(open_now, "Fetch it again")
    del world.site.proposals["4242"]
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert world.cards("design_mismatch") == []
    assert world.notes("Testerson_101426")["designs_checked"] is True


def test_file_it_as_it_is_still_needs_two_fetches_and_says_jim_chose(world):
    card = held_on_designs(world)
    world.answer(card, "File it as it is")
    world.tick()
    assert world.ops(pipeline.FETCH_1) == []                     # no more looking
    assert world.tree() == {}                                    # two fetches must still agree
    event, = world.events("designs_differ_filed")
    assert event.lane == "ACT" and event.studio_id == "4242"
    assert event.text == ("Jim chose to file it as it is. Testerson_101426: the report lists 1"
                          " design and Studio's proposal lists 2.")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    first, second = world.renders("4242")[-2:]
    assert first.fingerprint == second.fingerprint
    record = world.notes("Testerson_101426")
    assert record["designs"] == 1 and record["designs_checked"] is False
    log = world.folder("Testerson_101426").joinpath("Testerson_101426_NGRunner_log.txt")
    assert "Jim chose to file it as it is" in log.read_text(encoding="utf-8")
    assert pipeline.filed_note(world.store, "4242").endswith("filed as it is, as Jim chose")
    assert world.cards("design_mismatch") == []


def test_file_it_as_it_is_covers_that_content_and_no_other(world):
    card = held_on_designs(world)
    world.answer(card, "File it as it is")
    world.tick()
    world.studio.jobs["4242"].designs = TWO                      # Studio changes the report
    world.site.proposals["4242"] = ONE_NAME + ("Testerson - Pantry",)
    world.run_until(lambda: world.cards("design_mismatch"), 30)
    no_files(world)
    assert world.ops(pipeline.FETCH_1)


def test_a_second_fetch_with_other_content_is_compared_before_it_can_file(world):
    world.installed(TESTERSON)
    world.site.proposals["4242"] = ONE_NAME
    world.add_row("Testerson")
    world.run_until(lambda: world.ops(pipeline.FETCH_2), 30)
    reads = len(world.site.reads)
    world.studio.jobs["4242"].designs = TWO                      # a design more than the proposal
    world.run_until(lambda: world.cards("design_mismatch"), 30)
    assert len(world.site.reads) == reads + 1
    no_files(world)
    assert world.ops(pipeline.FETCH_1)


def test_a_second_fetch_of_the_same_content_is_not_compared_again(world):
    to_second_fetch(world)
    reads = len(world.site.reads)
    world.run_until(lambda: world.row("Testerson").state == "filed", 30)
    assert len(world.site.reads) == reads


@pytest.mark.parametrize("code", ["network", "sign_in_failed", "no_login"])
def test_studio_out_of_reach_still_files_and_says_so(world, code):
    world.installed(TESTERSON)
    world.site.down = StudioUnavailable(code, "Studio could not be reached.")
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    record = world.notes("Testerson_101426")
    assert record["designs"] == 1 and record["designs_checked"] is False
    log = world.folder("Testerson_101426").joinpath("Testerson_101426_NGRunner_log.txt")
    assert "designs not cross-checked" in log.read_text(encoding="utf-8")
    item, = world.dev("studio_unavailable")
    assert item.text.startswith("Studio could not be reached.") and item.studio_id is None
    assert world.cards(state=None) == []
    assert pipeline.filed_note(world.store, "4242").endswith("designs not cross-checked")
    assert world.events("filed")[0].text.endswith("designs not cross-checked.")


def test_no_studio_at_all_files_marked_and_is_no_crash(world):
    world.ctx.studio = None
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert world.notes("Testerson_101426")["designs_checked"] is False
    item, = world.dev("studio_unavailable")
    assert "login" in item.text
    assert world.dev("pipeline_step") == []
    email, = world.sent("barbara")
    assert "1 design" in email.subject                           # the counts still go out


def test_a_page_that_changed_files_with_its_own_dev_code(world):
    world.installed(TESTERSON)
    world.site.down = StudioUnavailable("page_changed", "Studio's project page changed shape.")
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert world.notes("Testerson_101426")["designs_checked"] is False
    assert world.dev("studio_unavailable") == []
    assert len(world.dev("studio_page_changed")) == 1


def test_one_dev_item_a_day_however_many_jobs_and_a_read_on_a_later_day_closes_it(world):
    world.installed(TESTERSON, FAKELEY)
    world.site.down = StudioUnavailable("network", "Studio could not be reached.")
    world.add_row("Testerson")
    world.add_row("Fakeley")
    world.run_until(lambda: world.row("Fakeley").state == "filed", 45)
    first, = world.dev("studio_unavailable")

    world.site.down = None                                       # a read today leaves it standing
    mockford = opp("4444", "Mockford_101426")
    world.list(mockford)
    world.add_row("Mockford")
    world.run_until(lambda: world.row("Mockford").state == "filed", 45)
    assert [item.id for item in world.dev("studio_unavailable")] == [first.id]

    world.clock.advance(timedelta(days=1))                       # the next day: one more, not two
    world.site.down = StudioUnavailable("network", "Studio could not be reached.")
    dummitt = opp("4545", "Dummitt_101426")
    world.list(dummitt)
    world.add_row("Dummitt")
    world.run_until(lambda: world.row("Dummitt").state == "filed", 45)
    second, = world.dev("studio_unavailable")
    assert second.id != first.id

    world.clock.advance(timedelta(days=1))                       # a day later, it reads again
    world.site.down = None
    last = opp("4646", "Testova_101426")
    world.list(last)
    world.add_row("Testova")
    world.run_until(lambda: world.row("Testova").state == "filed", 45)
    assert world.dev("studio_unavailable") == []


def test_studio_going_out_of_reach_never_reads_as_the_two_agreeing(world):
    card = held_on_designs(world)
    world.site.down = StudioUnavailable("network", "Studio could not be reached.")
    world.run(120, step=5)
    no_files(world)
    assert world.cards("design_mismatch")[0].id == card.id
    assert len(world.dev("studio_unavailable")) == 1


def test_fetch_it_again_with_studio_out_of_reach_still_holds_what_it_saw(world):
    card = held_on_designs(world)
    world.site.down = StudioUnavailable("network", "Studio could not be reached.")
    world.answer(card, "Fetch it again")
    world.run(30, step=5)
    no_files(world)
    assert world.cards("design_mismatch")


def test_the_check_runs_where_fetches_run_never_on_the_ticks_thread(world):
    world.installed(TESTERSON)
    background(world)
    to_the_match(world, "Testerson")
    world.tick()
    wait_until(lambda: world.site.reads and not world.ctx.fetches._running)
    assert world.site.reads[0] is not threading.current_thread()


def test_a_sign_in_that_takes_a_minute_does_not_stretch_the_two_fetches_apart(world):
    world.installed(TESTERSON)
    clock, real = world.clock, world.site.project_page
    asked_at: list[datetime] = []

    def slow(studio_id: str) -> ProjectPage:
        asked_at.append(clock.now())
        page = real(studio_id)
        clock.advance(minutes(1))
        return page

    world.site.project_page = slow
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    first, second = world.renders("4242")
    assert first.fetched_at == asked_at[0]                       # when the report came
    assert second.fetched_at - first.fetched_at >= minutes(10)


def test_a_job_released_from_its_row_withdraws_its_design_card(world):
    card = held_on_designs(world)
    assert world.cards("design_mismatch")[0].id == card.id
    row_id = world.row("Testerson").id
    with world.store.transaction() as tx:
        pipeline.update_fab_row(tx, row_id, now=world.clock.now(), studio_id=None, state="none")
        pipeline._release_job(tx, "4242", world.clock.now())
    assert world.cards("design_mismatch") == []
    assert world.ops(pipeline.FETCH_1) == []


# ----- Studio's own link to a report the derived addresses miss


REAL_LINK = "https://studio.inspiredclosets.com/install_summary_landscape/fakeley_llc/x_1/4242/"


def link_only(world: World) -> None:
    """A job whose report is only where Studio's project page says."""
    world.installed(TESTERSON)
    world.studio.jobs["4242"].slugs = ("fakeley_llc", "x_1")
    world.site.links["4242"] = REAL_LINK
    world.site.proposals["4242"] = ONE_NAME


def test_when_every_derived_address_404s_studios_own_link_fetches_it_and_is_kept(world):
    link_only(world)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    first, second = world.renders("4242")
    assert first.url.endswith("/fakeley_llc/x_1/4242/") and second.url == first.url
    assert world.notes("Testerson_101426")["designs_checked"] is True
    assert len(world.site.reads) == 1                            # its page was read once, and reused


def test_a_link_that_404s_too_is_tried_once_per_attempt_and_backs_off(world):
    link_only(world)
    world.site.links["4242"] = REAL_LINK.replace("x_1", "gone")
    world.add_row("Testerson")
    world.run_until(lambda: world.ops(pipeline.FETCH_1) and world.ops(pipeline.FETCH_1)[0].attempts, 30)
    op, = world.ops(pipeline.FETCH_1)
    assert sum("/fakeley_llc/gone/" in url for url in world.studio.asked) == 1
    assert op.due_at == world.clock.now() + minutes(5)
    assert world.tree() == {} and world.cards(state=None) == []


def test_a_link_already_asked_is_not_asked_again(world):
    world.installed(TESTERSON)
    client, project = candidate_slugs("Testerson_101426", None)[0]
    world.studio.jobs["4242"].slugs = ("nowhere", "nothing")
    world.site.links["4242"] = (
        f"https://studio.inspiredclosets.com/install_summary_landscape/{client}/{project}/4242/")
    world.add_row("Testerson")
    world.run_until(lambda: world.ops(pipeline.FETCH_1) and world.ops(pipeline.FETCH_1)[0].attempts, 30)
    derived = [url for url in world.studio.asked if f"/{client}/{project}/4242/" in url]
    assert len(derived) == 1


def test_no_link_and_no_page_leaves_a_plain_not_found_with_the_reason_in_dev(world):
    world.installed(TESTERSON)
    world.studio.jobs["4242"].slugs = ("nowhere", "nothing")
    world.site.down = StudioUnavailable("network", "Studio could not be reached.")
    world.add_row("Testerson")
    world.run_until(lambda: world.ops(pipeline.FETCH_1) and world.ops(pipeline.FETCH_1)[0].attempts, 30)
    assert len(world.dev("studio_unavailable")) == 1
    assert world.tree() == {}


# ----- counts a person can check


def test_a_second_rows_filed_as_carries_the_counts_too(world):
    world.installed(TESTERSON, designs=TWO)
    world.filed("Testerson")
    world.add_row("Testerson_101426")
    world.run_until(lambda: world.row("Testerson_101426").state == "filed", 30)
    world.run(5)
    pages = world.renders("4242")[-1].page_count
    emails = world.sent("barbara")
    assert len(emails) == 2
    assert all(e.subject == f"Filed as Testerson_101426: 2 designs, {pages} pages" for e in emails)


def test_a_summary_already_there_gives_its_own_counts_with_the_check_it_had(world):
    world.installed(TESTERSON)
    in_folder(world, data=pdf_of())
    world.add_row("Testerson_101426")
    world.run_until(lambda: world.row("Testerson_101426").state == "filed", 45)
    facts = pipeline.filed_facts(world.store, "4242")
    assert not facts["wrote"] and facts["designs"] == 1 and facts["designs_checked"] is True
    assert world.sent("barbara")[0].subject.startswith("Filed as Testerson_101426: 1 design,")


def test_keeping_a_summary_in_the_folder_counts_that_file_and_it_was_not_cross_checked(world):
    world.installed(TESTERSON)
    in_folder(world, data=pdf_of(designs=TWO))
    world.add_row("Testerson_101426")
    world.run_until(lambda: world.cards("ic_changed"), 45)
    world.answer(world.cards("ic_changed")[0], "Keep the filed one")
    world.run(2)
    facts = pipeline.filed_facts(world.store, "4242")
    assert facts["designs"] == 2 and facts["designs_checked"] is False


def test_a_summary_replaced_by_hand_gets_its_own_counts_and_is_not_cross_checked(world):
    world.installed(TESTERSON)
    world.filed("Testerson")
    folder = "Testerson_101426"
    (world.folder(folder) / f"{folder}_summary.pdf").write_bytes(pdf_of(designs=TWO))
    world.run(15)
    record = world.notes(folder)
    assert record["summary"]["replaced_by_hand"] is True
    assert record["designs"] == 2 and record["designs_checked"] is False


def test_file_the_new_one_counts_the_new_render_with_the_check_it_had_that_night(world):
    card = changed_at_night(world)
    world.answer(card, "File the new one")
    world.run(2)
    record = world.notes("Testerson_101426")
    assert record["designs"] == 2 and record["designs_checked"] is True
    log = world.folder("Testerson_101426").joinpath("Testerson_101426_NGRunner_log.txt")
    last = log.read_text(encoding="utf-8").splitlines()[-1]
    assert "as Jim chose" in last and "2 designs" in last
    assert "designs not cross-checked" not in last


def test_a_count_that_cant_be_read_is_never_guessed():
    words = pipeline._filed_words({"layout": "landscape", "pages": 0, "designs": None})
    assert "could not be read" in words and "0 " not in words
    assert check_line(words) == []


# ----- File the new one must not end in nothing


def lose_caches(world: World, *renders: Any) -> None:
    for render in renders:
        render.cache_path.unlink()


def test_file_the_new_one_falls_back_to_the_other_fetch_of_the_same_content(world):
    card = changed_at_night(world)
    first, second = world.renders("4242")[-2:]
    assert first.fingerprint == second.fingerprint
    lose_caches(world, second)
    world.answer(card, "File the new one")
    world.run(2)
    summary = world.folder("Testerson_101426") / "Testerson_101426_summary.pdf"
    assert summary.read_bytes() == first.cache_path.read_bytes()
    assert get_render(world.store, first.id).governing
    assert world.ops(pipeline.FILE_NEW) == [] and world.ops(pipeline.FILE_NEW, state="failed") == []
    assert world.cards("ic_changed") == []


def test_with_no_copy_left_the_card_stays_open_and_a_fresh_pair_takes_the_old_ones_place(world):
    card = changed_at_night(world)
    first, second = world.renders("4242")[-2:]
    before = world.tree()
    lose_caches(world, first, second)
    world.answer(card, "File the new one")
    world.tick()
    assert world.ops(pipeline.FILE_NEW, state="failed") == []
    again, = world.cards("ic_changed")                           # the same question stands
    assert again.id != card.id and again.text == card.text
    assert world.events("fetch_again")[-1].lane == "ACT"
    assert but_the_notes(world.tree()) == but_the_notes(before)
    world.run_until(lambda: not world.ops(pipeline.REFETCH_2), 40)
    fresh = world.renders("4242")[-1]
    assert fresh.id > second.id and fresh.cache_path.exists()
    assert [c.id for c in world.cards("ic_changed")] == [again.id]   # no third card

    world.answer(again, "File the new one")
    world.run(2)
    folder = "Testerson_101426"
    summary = world.folder(folder) / f"{folder}_summary.pdf"
    assert content_fingerprint(summary.read_bytes()) == fresh.fingerprint
    assert world.notes(folder)["designs"] == 2
    assert world.cards("ic_changed") == [] and world.ops(pipeline.FILE_NEW) == []


def test_with_no_copy_left_and_the_change_gone_the_card_is_withdrawn(world):
    card = changed_at_night(world)
    lose_caches(world, *world.renders("4242")[-2:])
    world.answer(card, "File the new one")
    world.tick()
    world.studio.jobs["4242"].designs = CLOSET                   # Studio goes back
    world.run_until(lambda: not world.ops(pipeline.REFETCH_2), 40)
    assert world.cards("ic_changed") == []
    assert get_state(world.store, pipeline._ic_key("4242")) is None


def test_answering_again_before_the_fresh_pair_is_in_is_still_no_quiet_end(world):
    card = changed_at_night(world)
    lose_caches(world, *world.renders("4242")[-2:])
    world.answer(card, "File the new one")
    world.tick()
    world.studio.jobs["4242"].reply = _Reply(503, b"<html>Busy</html>")   # nothing fresh comes
    world.answer(world.cards("ic_changed")[0], "File the new one")
    world.tick()
    world.tick()
    assert len(world.cards("ic_changed")) == 1
    assert world.ops(pipeline.FILE_NEW, state="failed") == []


def test_keep_the_filed_one_after_a_lost_render_keeps_it_and_asks_no_more(world):
    card = changed_at_night(world)
    before = world.tree()
    lose_caches(world, *world.renders("4242")[-2:])
    world.answer(card, "File the new one")
    world.tick()
    world.answer(world.cards("ic_changed")[0], "Keep the filed one")
    world.run_until(lambda: not world.ops(pipeline.REFETCH_2), 40)
    assert world.cards("ic_changed") == []
    assert but_the_notes(world.tree()) == but_the_notes(before)
    assert get_state(world.store, pipeline._ic_key("4242")) is None


def first_time_question(world: World) -> Any:
    """Not filed yet, and the folder holds a summary with other content."""
    world.installed(TESTERSON)
    in_folder(world, data=pdf_of(designs=TWO))
    world.add_row("Testerson_101426")
    world.run_until(lambda: world.cards("ic_changed"), 45)
    return world.cards("ic_changed")[0]


def test_a_first_time_question_with_no_copy_left_fetches_a_pair_and_keeps_the_card_open(world):
    card = first_time_question(world)
    lose_caches(world, *world.renders("4242")[-2:])
    world.answer(card, "File the new one")
    world.tick()
    assert world.ops(pipeline.FILE_NEW, state="failed") == []
    again, = world.cards("ic_changed")
    assert again.id != card.id
    assert world.ops(pipeline.FETCH_1) or world.ops(pipeline.FETCH_2)
    world.run_until(lambda: not world.ops(pipeline.FETCH_2) and not world.ops(pipeline.FETCH_1), 40)
    world.run(5)
    assert [c.id for c in world.cards("ic_changed")] == [again.id]
    question = json.loads(get_state(world.store, pipeline._ic_key("4242")))
    assert "lost" not in question
    assert get_render(world.store, question["render"]).cache_path.exists()


def test_a_first_time_question_kept_while_a_pair_is_coming_stops_the_fetching(world):
    card = first_time_question(world)
    lose_caches(world, *world.renders("4242")[-2:])
    world.answer(card, "File the new one")
    world.tick()
    world.answer(world.cards("ic_changed")[0], "Keep the filed one")
    world.run(30)
    assert world.ops(pipeline.FETCH_1) == [] and world.ops(pipeline.FETCH_2) == []
    assert world.cards("ic_changed") == []
    assert world.row("Testerson_101426").state == "filed"


def test_file_new_with_nothing_asked_ends_with_a_message_never_silently(world):
    world.installed(TESTERSON)
    world.filed("Testerson")
    pipeline.add_pending(world.store, pipeline.FILE_NEW, due_at=world.clock.now(), studio_id="4242")
    world.tick()
    op, = world.ops(pipeline.FILE_NEW, state="failed")
    assert op.note
    assert world.dev("file_new_lost")[0].studio_id == "4242"


# ----- Ctx.commit is commit_recorded (finding 8)


def test_the_pipeline_commits_only_through_commit_recorded(world, monkeypatch):
    calls: list[dict] = []
    real = pipeline.commit_recorded

    def spy(writes: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return real(writes, **kwargs)

    monkeypatch.setattr(pipeline, "commit_recorded", spy)
    world.installed(TESTERSON)
    world.filed("Testerson")
    call, = calls
    assert call["store"] is world.store and call["clock"] is world.clock
    assert call["roots"] is world.roots and callable(call["interlock"])
    assert not hasattr(pipeline, "commit_write")
    written = [r for r in list_write_records(world.store) if r.ok]
    assert len(written) == 1


def test_a_commits_row_that_cant_be_saved_never_changes_what_the_commit_did(world, monkeypatch):
    import ngrunner.writes as writes_module
    from ngrunner.store import StoreError

    def refused(*args: Any, **kwargs: Any) -> None:
        raise StoreError("the store is busy")

    monkeypatch.setattr(writes_module, "record_commit", refused)
    world.installed(TESTERSON)
    world.filed("Testerson")
    assert set(world.tree()) == three_files("Testerson_101426")
    assert world.dev("pipeline_step") == []


# ----- with the real messenger: the seam itself


def real_messenger(world: World) -> Any:
    from ngrunner.messenger import Messenger

    world.ctx.messenger = Messenger(world.store, world.settings, world.ctx.secrets, world.clock,
                                    world.roots, world.paths)
    return world.ctx.messenger


def test_the_real_messenger_writes_the_email_under_the_ref_the_pipeline_gave(world):
    real_messenger(world)
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    email, = world.sent("barbara", "Filed as Testerson_101426")
    assert email.ref == f"filed:{world.row('Testerson').id}:4242" and email.state == "written"
    assert (world.control / "outbox" / f"{email.id}.json").is_file()
    world.run(5)
    assert len(world.sent("barbara", "Filed as")) == 1


def test_in_shadow_the_real_messenger_sends_barbaras_email_to_sulaiman_marked(shadow):
    real_messenger(shadow)
    shadow.installed(TESTERSON)
    shadow.add_row("Testerson")
    shadow.run_until(lambda: shadow.row("Testerson").state == "filed", 45)
    assert shadow.sent("barbara") == []
    assert len(shadow.sent("sulaiman", "SHADOW Filed as Testerson_101426")) == 1


def test_a_message_the_real_messenger_holds_overnight_goes_once_in_the_morning(world):
    messenger = real_messenger(world)
    world.clock.set(datetime(2026, 10, 28, 5, 0, tzinfo=UTC))    # 10 PM in Los Angeles
    world.render.synced_at = world.clock.now()
    world.installed(TESTERSON, TESTERSON_2)
    world.add_row("Testerson")
    world.run_until(lambda: world.cards("which_job"), 15)
    world.run(5)
    assert world.sent() == [] and len(messenger.held()) == 1
    assert world.ops(pipeline.TELL_WHICH_JOB) == []
    ref = messenger.held()[0]["ref"]
    message = templates.filed("Testerson_101426", row="x")
    with world.store.transaction() as tx:                        # as if a crash cut the mark short
        pipeline._queue_tell(tx, pipeline.TELL_WHICH_JOB, message, to="barbara", ref=ref,
                             now=world.clock.now())
    world.tick()
    assert len(messenger.held()) == 1
    world.clock.set(datetime(2026, 10, 28, 14, 0, tzinfo=UTC))   # 7 AM
    messenger.check_receipts()
    assert len(world.sent("barbara", "Which job")) == 1 and messenger.held() == []


# ----- a crash or a restart at any point


class _Crash(BaseException):
    """The process dying: nothing catches it."""


class _CrashingConnection:
    """A connection that stops the process just after its nth commit of a change, once armed."""

    def __init__(self, conn: Any, state: dict) -> None:
        self._conn, self._state = conn, state
        self._wrote = False

    def execute(self, sql: str, *args: Any) -> Any:
        head = sql.lstrip()[:6].upper()
        if head in ("INSERT", "UPDATE", "DELETE"):
            self._wrote = True
        result = self._conn.execute(sql, *args)
        if head == "COMMIT" and self._wrote:
            self._wrote = False
            if self._state["armed"]:
                self._state["commits"] += 1
                if self._state["commits"] == self._state["at"]:
                    raise _Crash()
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def _scenario(world: World, state: dict) -> None:
    """A job and an unclear row, from a row appearing to the job filed, told and asked about."""
    world.installed(TESTERSON, opp("5000", "Dummitt_101426"), opp("5001", "Dummitt_101526"), rows=3)
    state["armed"] = True
    world.add_row("Testerson")
    world.add_row("Dummitt")
    world.run_until(lambda: _settled(world), 60)


def _settled(world: World) -> bool:
    return (world.row("Testerson").state == "filed" and world.row("Dummitt").state == "asked"
            and not world.ops(pipeline.TELL_FILED) and not world.ops(pipeline.TELL_WHICH_JOB))


def _assert_no_duplicates(world: World) -> None:
    assert set(world.tree()) == three_files("Testerson_101426")
    assert len(world.sent("barbara", "Filed as Testerson_101426")) == 1
    assert len(world.sent("barbara", "Which job")) == 1
    assert [c.state for c in world.cards(state=None)] == ["open"]
    assert len(world.events("filed")) == 1
    assert len(world.ops(pipeline.REFETCH)) == 1
    assert world.row("Testerson").studio_id == "4242"
    assert len(world.rows()) == 5
    assert world.dev("pipeline_step") == []


def _run_stopped_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at: int) -> tuple[World, dict]:
    state = {"commits": 0, "at": at, "armed": False}
    real = store_db._connect
    monkeypatch.setattr(store_db, "_connect", lambda path: _CrashingConnection(real(path), state))
    world = World(tmp_path)
    try:
        _scenario(world, state)
    except _Crash:
        state["crashed"] = True
    finally:
        monkeypatch.setattr(store_db, "_connect", real)
    return world, state


def _restart_and_finish(world: World) -> None:
    """As a new process would: the writes settled, a new context on the same store, and on to the
    end, then some ticks more."""
    settle_pending_writes(world.store, at=world.clock.now())
    world.ctx = Ctx(store=world.store, settings=world.settings, secrets=world.ctx.secrets,
                    clock=world.clock, roots=world.roots, paths=world.paths,
                    studio_http=world.studio, render_client=world.render, messenger=world.messenger,
                    mode="live", fetches=InlineFetches())
    world.run_until(lambda: _settled(world), 60)
    world.run(5)


def test_the_scenario_without_a_crash_leaves_no_duplicates(tmp_path, monkeypatch):
    world, state = _run_stopped_at(tmp_path, monkeypatch, -1)
    assert "crashed" not in state and state["commits"] > 20
    world.run(5)
    _assert_no_duplicates(world)


def test_a_crash_after_any_change_resumes_without_a_duplicate_file_message_or_card(tmp_path,
                                                                                   monkeypatch):
    # Every commit a pass makes, one crash each: a stop between any two steps of any tick.
    _, clean = _run_stopped_at(tmp_path / "clean", monkeypatch, -1)
    for at in range(1, clean["commits"] + 1):
        world, state = _run_stopped_at(tmp_path / f"crash{at}", monkeypatch, at)
        assert state.get("crashed"), f"no crash at commit {at} of {clean['commits']}"
        _restart_and_finish(world)
        _assert_no_duplicates(world)


def test_a_stop_between_the_renames_and_the_record_is_finished_at_restart(world, monkeypatch):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    real = StoreRegistry.record

    def stopped(self, journal_id, written):
        raise _Crash()

    monkeypatch.setattr(StoreRegistry, "record", stopped)
    with pytest.raises(_Crash):
        world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    monkeypatch.setattr(StoreRegistry, "record", real)
    assert set(world.tree()) == three_files("Testerson_101426")   # it stopped with them in place
    assert world.row("Testerson").state == "matched"
    settled = settle_pending_writes(world.store, at=world.clock.now())
    assert len(settled.adopted) == 3
    before = world.tree()
    world.run(2)
    assert world.row("Testerson").state == "filed" and world.tree() == before
    assert len(world.sent("barbara", "Filed as")) == 1
    assert len(world.events("filed")) == 1


@pytest.mark.parametrize("left", [("_summary.pdf",), ("_summary.pdf", "_NGRunner.json"),
                                  ("_NGRunner.json",), ("_NGRunner_log.txt",)],
                         ids=["the summary", "the summary and the notes", "only the notes",
                              "only the log"])
def test_after_part_of_a_commit_was_adopted_only_the_missing_files_are_written(world, left):
    to_second_fetch(world)
    set_state(world.store, PAUSED, "yes")
    second_due(world)
    world.tick()
    folder = "Testerson_101426"
    second = world.renders("4242")[-1]
    data = second.cache_path.read_bytes()
    place = world.folder(folder)
    place.mkdir()
    for suffix in left:
        content = data if suffix == "_summary.pdf" else b"written before the crash"
        (place / f"{folder}{suffix}").write_bytes(content)
    from ngrunner.store import record_owned

    record_owned(world.store, {
        pipeline._resolved(world.ng_jobs) / folder / f"{folder}{suffix}":
        hashlib.sha256((place / f"{folder}{suffix}").read_bytes()).hexdigest() for suffix in left},
        at=world.clock.now())
    set_state(world.store, PAUSED, "no")
    world.run(1)
    assert set(world.tree()) == three_files(folder)
    for suffix in left:
        if suffix != "_summary.pdf":
            assert (place / f"{folder}{suffix}").read_bytes() == b"written before the crash"
    assert world.row("Testerson").state == "filed"
    assert sum(r.ok for r in list_write_records(world.store)) == 1


def test_after_a_crash_the_notes_describe_the_summary_that_is_there_not_a_later_pair(world):
    # A crash left its own summary; then a new pair was fetched, in other bytes, with one content.
    to_second_fetch(world)
    set_state(world.store, PAUSED, "yes")
    second_due(world)
    world.tick()
    folder = "Testerson_101426"
    earlier = pdf_of()
    place = world.folder(folder)
    place.mkdir()
    (place / f"{folder}_summary.pdf").write_bytes(earlier)
    from ngrunner.store import record_owned

    sha = hashlib.sha256(earlier).hexdigest()
    own = pipeline._resolved(world.ng_jobs) / folder / f"{folder}_summary.pdf"
    record_owned(world.store, {own: sha}, at=world.clock.now())
    set_state(world.store, PAUSED, "no")
    world.run(1)
    assert world.notes(folder)["summary"]["sha256"] == sha
    world.run(15)
    assert world.events("summary_replaced") == []
    assert (place / f"{folder}_summary.pdf").read_bytes() == earlier


# ----- the wrong-job property, on generated rows


OWNERS = ((PE, "PE"), ("Lee Sample", "LS"), ("Quinn Fake", "QF"))
FIRST = ("Test", "Fake", "Mock", "Dumm", "Sampl", "Plac", "Stub", "Faux", "Sham", "Pret")
LAST = ("erson", "eley", "ford", "itt", "eton", "holm", "by", "well", "stead", "ham")
KINDS = ("exact", "exact_unsold", "bare", "other_designer", "two_sold", "newer_unsold", "room",
         "typed_date", "five_digit", "first_name", "compound", "remake", "wrong_report")


def _generated(seed: int, count: int) -> tuple[list[Opportunity], list[tuple], dict[str, str]]:
    """Rows and Opportunities in every shape the matcher must sort, each row typed for its target,
    so the only way a summary lands elsewhere is the pipeline's own mistake."""
    rng = random.Random(seed)
    names = [a + b for a in FIRST for b in LAST]
    rng.shuffle(names)
    listed: list[Opportunity] = []
    cases: list[tuple] = []
    printed: dict[str, str] = {}
    ids = iter(range(10_000 + seed * 1000, 99_999))

    def add(name: str, owner: str, stage: str = SOLD) -> Opportunity:
        opportunity = Opportunity(name, str(next(ids)), owner, stage)
        listed.append(opportunity)
        return opportunity

    for how in [KINDS[i % len(KINDS)] for i in range(count)]:
        surname = names.pop()
        (owner, initials), (other, _) = rng.sample(OWNERS, 2)
        day = rng.randint(1, 14)
        name = f"{surname}_10{day:02d}26"
        later = f"{surname}_10{day + 14:02d}26"
        target = add(name, owner, "Propose" if how == "exact_unsold" else SOLD)
        typed = surname
        if how in ("exact", "exact_unsold"):
            typed = name
        elif how == "other_designer":
            add(later, other)
        elif how == "two_sold":
            add(later, owner)
        elif how == "newer_unsold":
            add(later, owner, "Propose")
        elif how == "room":
            typed = f"{surname} closet"
        elif how == "typed_date":
            typed = f"{surname} 10/{day}/26"
        elif how == "five_digit":
            typed = f"{surname}_10{day:02d}2"
        elif how == "first_name":
            typed = f"{surname} Jane"
        elif how == "compound":
            add(f"{surname}{names.pop()}_10{day:02d}26", owner)
        elif how == "remake":
            typed = f"{surname} remake"
        elif how == "wrong_report":
            printed[target.studio_id] = f"{names.pop()}_10{day:02d}26"
        cases.append((target, typed, initials, how))
    return listed, cases, printed


@pytest.mark.parametrize("seed", range(1, 4))
def test_every_filed_summary_belongs_to_the_row_it_was_generated_for_and_every_other_row_asks(
        tmp_path, seed):
    listed, cases, printed = _generated(seed, 13 + seed)
    world = World(tmp_path)
    world.render.opportunities = listed
    for opportunity in listed:
        world.studio.add(opportunity)
        world.studio.jobs[opportunity.studio_id].printed = printed.get(opportunity.studio_id)
    for n in range(40):
        world.add_row(f"Samplerow{chr(97 + n % 26)}{chr(97 + n // 26)}")
    world.export()
    world.tick()
    for _target, typed, initials, _how in cases:
        world.add_row(typed, initials)
    world.run(50)
    world.run(3 * 60, step=15)

    filed = []
    for target, typed, _initials, how in cases:
        row = world.row(typed)
        if row.state == "filed":
            assert row.studio_id == target.studio_id, f"{how}: {typed} filed to {row.studio_id}"
            filed.append((target, how))
        else:
            asks = [c for c in world.cards() if c.fab_row_id == row.id or (
                row.studio_id is not None and c.studio_id == row.studio_id)]
            assert asks, f"{how}: {typed} is {row.state} and nothing asks"
    folders = {path.split("/")[0] for path in world.tree() if path.endswith("_summary.pdf")}
    assert folders == {target.name for target, _how in filed}
    for target, _how in filed:
        data = (world.folder(target.name) / f"{target.name}_summary.pdf").read_bytes()
        assert slugify(check_summary(data).project_name) == slugify(target.name)
    assert filed and len(filed) < len(cases)
    assert not {how for _target, how in filed} & {"wrong_report", "two_sold", "first_name",
                                                  "newer_unsold", "compound", "remake",
                                                  "five_digit"}


# ----- going live: what the trial run filed aside


def _filed_in_shadow(shadow: World, row: str = "Testerson") -> None:
    shadow.installed(TESTERSON)
    shadow.add_row(row)
    shadow.run_until(lambda: shadow.row(row).state == "filed", 45)
    assert shadow.tree() == {}


def _go_live(shadow: World) -> None:
    """What supervisor.set_mode does."""
    shadow.ctx.mode = "live"
    pipeline.go_live(shadow.store, shadow.clock.now())


def _row_left_the_sheet(shadow: World, row: str = "Testerson") -> None:
    shadow.sheet[:] = [r for r in shadow.sheet if r["job"] != row]
    shadow.run(10)
    assert shadow.row(row).state == "gone"


def test_going_live_files_what_the_trial_run_filed_aside_and_tells_barbara(shadow):
    _filed_in_shadow(shadow)
    told = shadow.sent("barbara", "Filed as Testerson_101426")
    _go_live(shadow)
    shadow.tick()
    assert pipeline.filed_facts(shadow.store, "4242") is None
    assert shadow.row("Testerson").state == "matched"
    assert [e.lane for e in shadow.events(pipeline.TRIAL_ONLY_EVENT)] == ["ACT"]
    shadow.run_until(lambda: shadow.row("Testerson").state == "filed", 45)
    shadow.run(2)
    assert set(shadow.tree()) == three_files("Testerson_101426")
    assert pipeline.filed_facts(shadow.store, "4242")["where"] == "ng_jobs"
    emails = shadow.sent("barbara", "Filed as Testerson_101426")
    assert len(emails) == len(told) + 1 and len({m.ref for m in emails}) == len(emails)
    shadow.run(60)
    assert len(shadow.events(pipeline.TRIAL_ONLY_EVENT)) == 1
    assert len(shadow.sent("barbara", "Filed as Testerson_101426")) == len(emails)


def test_going_live_compares_with_a_hand_made_folder_and_never_overwrites_it(shadow):
    _filed_in_shadow(shadow)
    path = in_folder(shadow, data=pdf_of(designs=TWO))
    before = path.read_bytes()
    _go_live(shadow)
    shadow.run_until(lambda: shadow.cards("ic_changed"), 45)
    assert path.read_bytes() == before
    assert set(shadow.tree()) == {"Testerson_101426/Testerson_101426_summary.pdf"}


def test_going_live_with_the_same_summary_by_hand_files_with_no_write(shadow):
    _filed_in_shadow(shadow)
    path = in_folder(shadow)
    before = path.read_bytes()
    _go_live(shadow)
    shadow.tick()
    shadow.run_until(lambda: shadow.row("Testerson").state == "filed", 45)
    assert path.read_bytes() == before
    facts = pipeline.filed_facts(shadow.store, "4242")
    assert facts["where"] == "ng_jobs" and facts["wrote"] is False


def test_a_job_whose_row_left_the_sheet_stays_aside_and_says_so_until_it_is_added(shadow):
    _filed_in_shadow(shadow)
    _row_left_the_sheet(shadow)
    fetches = shadow.studio.fetches("4242")
    _go_live(shadow)
    shadow.run(60)
    assert shadow.tree() == {} and shadow.studio.fetches("4242") == fetches
    assert pipeline.filed_facts(shadow.store, "4242")["where"] == "shadow"
    assert len(shadow.events(pipeline.TRIAL_ONLY_EVENT)) == 1
    assert shadow.ops(pipeline.REFETCH) == []
    assert "fetching" in pipeline.add_job(shadow.ctx, studio_id="4242")
    shadow.run_until(lambda: shadow.folder("Testerson_101426").exists(), 45)
    assert set(shadow.tree()) == three_files("Testerson_101426")


def test_a_new_row_for_a_job_left_aside_files_it_for_real(shadow):
    _filed_in_shadow(shadow)
    _row_left_the_sheet(shadow)
    _go_live(shadow)
    shadow.run(5)
    shadow.add_row("Testerson_101426")
    shadow.run_until(lambda: shadow.row("Testerson_101426").state == "filed", 45)
    assert set(shadow.tree()) == three_files("Testerson_101426")


def test_a_card_open_at_going_live_goes_and_the_job_is_filed_afresh(shadow):
    _filed_in_shadow(shadow)
    shadow.studio.jobs["4242"].designs = TWO
    shadow.night()
    assert shadow.cards("ic_changed")
    _go_live(shadow)
    shadow.tick()
    assert shadow.cards("ic_changed") == []
    shadow.run_until(lambda: shadow.row("Testerson").state == "filed", 45)
    assert set(shadow.tree()) == three_files("Testerson_101426")


def test_notes_of_a_job_left_aside_are_not_kept_up_once_live(shadow):
    from ngrunner.store import add_card

    _filed_in_shadow(shadow)
    _row_left_the_sheet(shadow)
    aside = shadow.control / "shadow"
    before = shadow.tree(aside)
    _go_live(shadow)
    shadow.run(5)
    add_card(shadow.store, prefix="TES", kind="closet_or_garage", text="A question.",
             choices=("Closet", "Garage"), created_at=shadow.clock.now(), studio_id="4242")
    shadow.run(5)
    assert shadow.tree(aside) == before


# ----- NG Jobs out of reach: a row must not wait in silence


def test_a_row_through_a_night_with_ng_jobs_away_reaches_jim(world, tmp_path):
    from ngrunner import jobmodel
    from ngrunner.board.views import HealthView

    def tray() -> Any:
        return jobmodel.tray_state(world.store, world.settings, world.clock, unwell=False,
                                   paused=False, shadow=False)

    def board() -> Any:
        health = HealthView(paths=(), last_export=None, list_synced_at=None, heartbeat=None,
                            toasts=None)
        return jobmodel.board_view(world.store, world.settings, world.clock, health=health,
                                   paused=False, shadow=False)

    world.installed(TESTERSON)
    moved = tmp_path / "away"
    world.ng_jobs.rename(moved)
    world.add_row("Testerson")
    world.run(30)
    alarm = templates.ALARMS["ng_jobs_unreachable"]
    assert world.dev("ng_jobs_unreachable")
    assert tray().colour == "green"
    row = next(r for r in board().jobs if r.name == "Testerson")
    assert "NG Jobs can't be read" in row.next_step
    world.run(14 * 60, step=30)
    assert world.row("Testerson").state == "new"
    assert tray().colour == "amber" and tray().count == 1
    view = board()
    assert alarm in view.health.alarms and alarm in view.your_list[0].text
    assert alarm in jobmodel.digest_view(world.store, world.settings, world.clock).owner
    moved.rename(world.ng_jobs)
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert world.dev("ng_jobs_unreachable") == [] and tray().colour == "green"


def test_the_ng_jobs_alarm_clears_even_when_the_waiting_row_has_gone(world, tmp_path):
    world.installed(TESTERSON)
    moved = tmp_path / "away"
    world.ng_jobs.rename(moved)
    world.add_row("Testerson")
    world.run(30)
    assert world.dev("ng_jobs_unreachable")
    world.sheet[:] = [r for r in world.sheet if r["job"] != "Testerson"]
    world.run(10)
    assert world.ops(pipeline.MATCH_ROW) == [] and world.dev("ng_jobs_unreachable")
    moved.rename(world.ng_jobs)
    world.run(5)
    assert world.dev("ng_jobs_unreachable") == []


# ----- DEV items that have a way to close


def test_a_failed_step_keeps_its_dev_item_until_its_next_clean_pass(world, monkeypatch):
    failing = {"now": True}

    def _flaky(ctx: Any) -> None:
        if failing["now"]:
            raise OSError("the step broke")

    def _fine(ctx: Any) -> None:
        pass

    monkeypatch.setattr(pipeline, "_STEPS", (_flaky, _fine))
    world.tick()
    world.tick()
    assert [d.text.split(":")[0] for d in world.dev("pipeline_step")] == ["flaky"]
    failing["now"] = False
    world.tick()
    assert world.dev("pipeline_step") == []


def test_the_ten_minute_look_keeps_its_item_between_looks(world):
    from ngrunner.store import open_dev, set_state_time

    now = world.clock.now()
    open_dev(world.store, at=now, code="pipeline_step", text="watch filed: OSError: it broke")
    set_state_time(world.store, pipeline._WATCHED, now)
    world.tick()
    assert world.dev("pipeline_step")
    world.clock.advance(minutes(11))
    world.tick()
    assert world.dev("pipeline_step") == []


def test_filing_closes_the_jobs_dev_items_and_no_others(world):
    from ngrunner.store import open_dev

    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.job("4242") is not None, 30)
    now = world.clock.now()
    open_dev(world.store, at=now, code="layouts_differ", text="They differ.", studio_id="4242")
    open_dev(world.store, at=now, code="backup_failed", text="Not about a job.")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert world.dev("layouts_differ") == [] and world.dev("backup_failed")


def test_a_night_that_reads_as_filed_closes_a_layouts_item(world):
    from ngrunner.store import open_dev

    world.installed(TESTERSON)
    world.filed("Testerson")
    open_dev(world.store, at=world.clock.now(), code="layouts_differ", text="They differ.",
             studio_id="4242")
    world.night()
    assert world.dev("layouts_differ") == []


# ----- the 24-hour limit reaches every fetch, whichever path queued it


def test_a_second_fetch_that_never_comes_still_raises_the_card(world):
    to_second_fetch(world)
    assert world.ops(pipeline.FETCH_2)[0].deadline_at is not None
    world.studio.raises = requests.ConnectionError()
    world.run_until(lambda: world.cards("fetch_failed"), 26 * 60, step=60)


def test_a_pair_fetched_again_after_the_cache_went_still_ends_in_the_card(world):
    to_second_fetch(world)
    set_state(world.store, PAUSED, "yes")
    second_due(world)
    world.tick()
    world.run(1)
    for render in world.renders("4242"):
        render.cache_path.unlink(missing_ok=True)
    set_state(world.store, PAUSED, "no")
    world.run(1)
    assert world.ops(pipeline.FETCH_1) and world.tree() == {}
    world.studio.raises = requests.ConnectionError()
    world.run_until(lambda: world.cards("fetch_failed"), 26 * 60, step=60)


def test_a_job_that_keeps_a_folders_layout_still_ends_in_the_card(world):
    world.installed(TESTERSON)
    in_folder(world, data=pdf_of(layout="portrait"))
    world.add_row("Testerson_101426")
    world.run_until(lambda: world.events("layout_kept"), 60)
    world.studio.raises = requests.ConnectionError()
    world.run_until(lambda: world.cards("fetch_failed"), 26 * 60, step=60)


def test_a_fresh_pair_after_a_lost_render_still_ends_in_the_card_if_studio_stays_down(world):
    card = first_time_question(world)
    lose_caches(world, *world.renders("4242")[-2:])
    world.studio.raises = requests.ConnectionError()
    world.answer(card, "File the new one")
    world.run_until(lambda: world.cards("fetch_failed"), 26 * 60, step=60)


@pytest.mark.parametrize("choice", ["Fetch it again", "File it as it is"])
def test_an_answer_to_the_design_card_still_ends_in_the_card_if_studio_stays_down(world, choice):
    card = held_on_designs(world)
    world.studio.raises = requests.ConnectionError()
    world.answer(card, choice)
    world.run_until(lambda: world.cards("fetch_failed"), 26 * 60, step=60)


def test_the_card_comes_at_the_deadline_while_the_next_retry_is_still_ahead(world):
    world.installed(TESTERSON)
    world.studio.raises = requests.ConnectionError()
    world.add_row("Testerson")
    world.run_until(lambda: world.studio.asked, 15)
    for _ in range(60):
        op, = world.ops(pipeline.FETCH_1)
        if op.due_at > op.deadline_at:
            break
        world.clock.set(op.due_at)
        world.tick()
    assert op.due_at > op.deadline_at and world.cards("fetch_failed") == []
    tried = len(world.studio.asked)
    world.clock.set(op.deadline_at)
    world.tick()
    assert world.cards("fetch_failed") and len(world.studio.asked) == tried


# ----- answers that cross with what changed meanwhile


def test_a_row_changed_answer_whose_job_left_the_list_links_nothing_and_says_so(world):
    filed_row(world)
    world.correct("Testerson", job="Fakeley")
    world.run(10)
    card, = world.cards("row_changed")
    world.render.opportunities = [TESTERSON]
    world.render.synced_at = world.clock.now()
    world.clock.advance(timedelta(hours=2))
    world.tick()                                                 # a new list, read quietly
    world.answer(card, card.choices[card.choice_ids.index("4343")])
    world.run(3)
    row = world.row("Fakeley")
    assert row.studio_id == "4242" and "left the job list" in row.reason
    assert world.job("4343") is None and world.studio.fetches("4343") == 0


def test_an_answer_crossing_with_a_correction_does_not_link_the_row_to_the_answer(world):
    card = asked(world, TESTERSON, TESTERSON_2)
    world.answer(card, card.choices[card.choice_ids.index("4243")])
    world.correct("Testerson", job="Testerson_101426")
    world.run(5, step=5)
    world.run_until(lambda: world.row("Testerson_101426").state == "filed", 45)
    assert world.row("Testerson_101426").studio_id == "4242"
    assert world.job("4243") is None and three_files("Testerson_101426") == set(world.tree())


# ----- the matcher's rules, end to end through a row, a card and no fetch


def _asks(world: World, row: str) -> None:
    world.add_row(row)
    world.run_until(lambda: world.cards("which_job"), 15)
    assert world.row(row).state == "asked" and world.studio.asked == []


def test_a_row_naming_two_opportunities_exactly_asks(world):
    world.installed(opp("4242", "Testerson_101426"), opp("4243", "Testerson_101426"))
    _asks(world, "Testerson_101426")
    assert set(world.cards("which_job")[0].choice_ids) == {"4242", "4243", None}


def test_a_newer_sale_of_the_designer_still_at_propose_asks(world):
    world.installed(TESTERSON, Opportunity(name="Testerson_101526", studio_id="4243", owner=PE,
                                           stage="Propose"))
    _asks(world, "Testerson")


def test_an_exact_name_with_an_unsold_twin_asks(world):
    world.installed(TESTERSON, Opportunity(name="Testerson_101426", studio_id="4243", owner=PE,
                                           stage="Propose"))
    _asks(world, "Testerson_101426")


def test_a_sold_rival_with_the_surname_in_its_name_asks(world):
    world.installed(TESTERSON, opp("4243", "TestersonFakeley_100126"))
    _asks(world, "Testerson")


def test_a_remake_row_asks_and_never_files_as_its_parent(world):
    world.installed(TESTERSON)
    _asks(world, "Testerson remake")


# ----- a pause, for the pipeline's own guard


def test_while_paused_the_notes_wait_and_catch_up_after(world):
    filed_row(world)
    before = world.tree()
    set_state(world.store, PAUSED, "yes")
    world.correct("Testerson", job="Fakeley")
    world.run(10)
    card, = world.cards("row_changed")
    assert world.tree() == before and world.dev("notes_refused") == []
    set_state(world.store, PAUSED, "no")
    world.run(2)
    assert world.notes("Testerson_101426")["items"][0]["card"] == card.id


def test_file_the_new_one_waits_out_a_pause(world):
    card = changed_at_night(world)
    before = world.tree()
    set_state(world.store, PAUSED, "yes")
    world.answer(card, "File the new one")
    world.run(10)
    assert world.tree() == before
    assert world.ops(pipeline.FILE_NEW) and world.ops(pipeline.FILE_NEW)[0].note is None
    set_state(world.store, PAUSED, "no")
    world.run(2)
    assert world.ops(pipeline.FILE_NEW) == []
    assert world.tree() != before


def test_an_incomplete_render_at_night_raises_nothing(world):
    world.installed(TESTERSON, designs=TWO)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    world.studio.jobs["4242"].early = 5
    world.clock.set(world.ops(pipeline.REFETCH)[0].due_at)
    world.tick()
    assert world.ops(pipeline.REFETCH_2) == []
    world.night()
    assert world.cards("ic_changed", state=None) == []


# ----- a sign-in or a fetch that never comes back must not stop all filing


def tray(world: World) -> str:
    return jobmodel.tray_state(world.store, world.settings, world.clock, unwell=False,
                               paused=False, shadow=False).colour


LOGIN = {"studio_username": "sam@example.com", "studio_password": "not-a-real-one"}


def test_a_sign_in_that_never_returns_is_given_up_on_and_the_job_still_files(world):
    gate = threading.Event()

    def never_returns(username: str, password: str) -> dict[str, str]:
        gate.wait(30)
        return {}

    world.ctx.studio = StudioSite(MemorySecrets(LOGIN), world.clock, sign_in=never_returns,
                                  sign_in_limit=timedelta(seconds=0.05))
    try:
        world.installed(TESTERSON)
        world.add_row("Testerson")
        world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    finally:
        gate.set()
    assert world.notes("Testerson_101426")["designs_checked"] is False
    assert world.dev("studio_unavailable")


def test_a_read_waiting_behind_a_sign_in_under_way_gives_up_too(world):
    site = StudioSite(MemorySecrets(LOGIN), world.clock, sign_in_limit=timedelta(seconds=0.02))
    with site._lock, pytest.raises(StudioUnavailable) as caught:
        site.project_page("4242")
    assert caught.value.code == "sign_in_failed"


def test_a_fetch_that_hangs_gives_up_its_place_and_the_tray_says_so(world):
    world.installed(TESTERSON)
    world.ctx.fetches = BackgroundFetches(overrun=timedelta(seconds=0.01))
    world.studio.gate = threading.Event()
    to_the_match(world, "Testerson")
    world.tick()
    wait_until(lambda: world.studio.asked)
    time.sleep(0.03)
    world.tick()
    assert world.ctx.fetches.free() == 2
    item, = world.dev("fetch_stuck")
    assert item.studio_id == "4242" and tray(world) == "amber"
    op, = world.ops(pipeline.FETCH_1)
    assert op.attempts == 1 and op.note
    facts = jobmodel._read(world.store, world.clock.now())
    words = jobmodel._your_list(facts, paused=False)[0][1].text
    assert "Testerson_101426" in words and check_line(words) == []
    world.studio.gate.set()                                      # what it brings back is dropped
    wait_until(lambda: not [t for t in threading.enumerate()
                            if t.name.startswith("ngrunner-fetch-")])
    assert world.ctx.fetches.finished() == [] and world.renders("4242") == []
    world.studio.gate = None
    world.ctx.fetches = InlineFetches()
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert world.dev("fetch_stuck") == [] and tray(world) == "green"


class _NoPlaces(InlineFetches):
    """Every place taken, by something that won't let go."""

    def free(self) -> int:
        return 0


def test_a_job_that_never_gets_a_place_still_raises_the_card(world):
    world.installed(TESTERSON)
    world.ctx.fetches = _NoPlaces()
    world.add_row("Testerson")
    world.run_until(lambda: world.ops(pipeline.FETCH_1), 15)
    world.run(23 * 60, step=60)
    assert world.cards("fetch_failed") == []
    world.run_until(lambda: world.cards("fetch_failed"), 3 * 60, step=15)
    assert world.ops(pipeline.FETCH_1)[0].attempts == 0 and world.studio.asked == []
    assert "not been able to start" in world.cards("fetch_failed")[0].text


def test_a_pc_that_slept_past_the_limit_still_gets_its_chance_first(world):
    world.installed(TESTERSON)
    world.ctx.fetches = _NoPlaces()
    world.add_row("Testerson")
    world.run_until(lambda: world.ops(pipeline.FETCH_1), 15)
    world.ctx.fetches = InlineFetches()
    world.clock.advance(timedelta(days=3))
    world.tick()
    assert world.cards("fetch_failed") == [] and world.ops(pipeline.FETCH_2)


# ----- shadow mode keeps out of NG Jobs, and live files into it


def test_in_shadow_file_the_new_one_over_a_summary_in_ng_jobs_files_aside(shadow):
    shadow.installed(TESTERSON)
    in_folder(shadow, name="testerson_testerson_101426_summary.pdf", data=pdf_of(designs=TWO))
    before = shadow.tree()
    shadow.add_row("Testerson_101426")
    shadow.run_until(lambda: shadow.cards("ic_changed"), 45)
    shadow.answer(shadow.cards("ic_changed")[0], "File the new one")
    shadow.run(3)
    assert shadow.tree() == before
    assert set(shadow.tree(shadow.control / "shadow")) == three_files("Testerson_101426")
    assert pipeline.filed_facts(shadow.store, "4242")["where"] == "shadow"
    assert shadow.row("Testerson_101426").state == "filed"


def _back_to_shadow(world: World) -> None:
    world.ctx = replace(world.ctx, mode="shadow")


def test_switching_back_file_the_new_one_writes_nothing_in_ng_jobs(world):
    card = changed_at_night(world)
    before = world.tree()
    _back_to_shadow(world)
    world.answer(card, "File the new one")
    world.run(2)
    assert world.tree() == before
    assert set(world.tree(world.control / "shadow")) == three_files("Testerson_101426")
    assert pipeline.filed_facts(world.store, "4242")["where"] == "shadow"


def test_switching_back_notes_are_not_rewritten_in_ng_jobs_and_catch_up_when_live(world):
    filed_row(world)
    before = world.tree()
    _back_to_shadow(world)
    world.correct("Testerson", job="Fakeley")
    world.run(10)
    card, = world.cards("row_changed")
    assert world.tree() == before and world.dev("notes_refused") == []
    world.ctx.mode = "live"
    world.run(2)
    assert world.notes("Testerson_101426")["items"][0]["card"] == card.id


def test_filed_aside_then_live_file_the_new_one_lands_in_ng_jobs(shadow):
    shadow.installed(TESTERSON)
    shadow.add_row("Testerson")
    shadow.run_until(lambda: shadow.row("Testerson").state == "filed", 45)
    shadow.ctx.mode = "live"
    shadow.studio.jobs["4242"].designs = TWO
    shadow.night()
    card, = shadow.cards("ic_changed")
    aside = shadow.tree(shadow.control / "shadow")
    shadow.answer(card, "File the new one")
    shadow.run(2)
    assert set(shadow.tree()) == three_files("Testerson_101426")
    assert shadow.tree(shadow.control / "shadow") == aside
    assert pipeline.filed_facts(shadow.store, "4242")["where"] == "ng_jobs"
    summary = shadow.folder("Testerson_101426") / "Testerson_101426_summary.pdf"
    assert content_fingerprint(summary.read_bytes()) == shadow.renders("4242")[-1].fingerprint
    assert shadow.ops(pipeline.FILE_NEW) == []
    assert shadow.notes("Testerson_101426")["designs"] == 2


def test_asked_in_shadow_answered_live_lands_in_ng_jobs(shadow):
    shadow.installed(TESTERSON)
    shadow.add_row("Testerson")
    shadow.run_until(lambda: shadow.row("Testerson").state == "filed", 45)
    shadow.studio.jobs["4242"].designs = TWO
    shadow.night()
    card, = shadow.cards("ic_changed")
    shadow.ctx.mode = "live"
    shadow.answer(card, "File the new one")
    shadow.run(2)
    assert set(shadow.tree()) == three_files("Testerson_101426")


def test_filed_aside_then_live_never_overwrites_what_ng_jobs_holds(shadow):
    shadow.installed(TESTERSON)
    shadow.add_row("Testerson")
    shadow.run_until(lambda: shadow.row("Testerson").state == "filed", 45)
    shadow.ctx.mode = "live"
    in_folder(shadow, data=pdf_of(designs=MIXED))                # Jim's, by hand
    before = shadow.tree()
    shadow.studio.jobs["4242"].designs = TWO
    shadow.night()
    card, = shadow.cards("ic_changed")
    shadow.answer(card, "File the new one")
    shadow.run(3)
    assert shadow.tree() == before
    op, = shadow.ops(pipeline.FILE_NEW)
    assert op.note and pipeline.blocked_reason(shadow.store, "4242") == op.note


def test_filed_aside_then_live_with_the_new_content_already_in_ng_jobs_asks_nothing(shadow):
    shadow.installed(TESTERSON)
    shadow.add_row("Testerson")
    shadow.run_until(lambda: shadow.row("Testerson").state == "filed", 45)
    shadow.ctx.mode = "live"
    in_folder(shadow, data=pdf_of(designs=TWO))
    before = shadow.tree()
    shadow.studio.jobs["4242"].designs = TWO
    shadow.night()
    assert shadow.cards("ic_changed", state=None) == [] and shadow.tree() == before
    facts = pipeline.filed_facts(shadow.store, "4242")
    assert facts["where"] == "ng_jobs" and facts["wrote"] is False
    assert shadow.renders("4242")[-1].governing


# ----- the night's change is checked against the proposal, and the card says what it found


def test_a_night_change_reads_the_proposal_and_the_card_gives_the_counts(world):
    world.installed(TESTERSON, designs=TWO)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert world.notes("Testerson_101426")["designs_checked"] is True
    world.studio.jobs["4242"].designs = CLOSET                   # the report loses a room
    world.site.proposals["4242"] = TWO_NAMES
    reads = len(world.site.reads)
    world.night()
    assert len(world.site.reads) > reads
    card, = world.cards("ic_changed")
    assert "the report lists 1 design and Studio's proposal lists 2" in card.text
    assert check_line(card.text) == []
    world.answer(card, "File the new one")
    world.run(2)
    record = world.notes("Testerson_101426")
    assert record["designs"] == 1 and record["designs_checked"] is False
    log = world.folder("Testerson_101426") / "Testerson_101426_NGRunner_log.txt"
    assert "Jim chose to file it as it is" in log.read_text(encoding="utf-8").splitlines()[-1]
    assert pipeline.filed_note(world.store, "4242").endswith("filed as it is, as Jim chose")


def test_a_night_change_the_proposal_agrees_with_is_filed_as_checked(world):
    card = changed_at_night(world)
    assert "the report lists 2 designs and Studio's proposal lists 2" in card.text
    world.answer(card, "File the new one")
    world.run(2)
    assert world.notes("Testerson_101426")["designs_checked"] is True


def test_a_night_change_with_studio_out_of_reach_says_not_cross_checked(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    world.studio.jobs["4242"].designs = TWO
    world.site.down = StudioUnavailable("network", "Studio could not be reached.")
    world.night()
    card, = world.cards("ic_changed")
    assert "its designs could not be cross-checked" in card.text and check_line(card.text) == []
    assert world.dev("studio_unavailable")
    world.answer(card, "File the new one")
    world.run(2)
    assert world.notes("Testerson_101426")["designs_checked"] is False
    log = world.folder("Testerson_101426") / "Testerson_101426_NGRunner_log.txt"
    assert "designs not cross-checked" in log.read_text(encoding="utf-8").splitlines()[-1]


# ----- a design card never goes out of date


THREE_NAMES = (*TWO_NAMES, "Testerson - Pantry")


def test_when_the_report_changes_under_a_design_card_a_new_one_tells_the_current_counts(world):
    card = held_on_designs(world, THREE_NAMES, designs=TWO)
    assert "the report lists 2 designs and Studio's proposal lists 3" in card.text
    world.studio.jobs["4242"].designs = CLOSET
    world.run_until(lambda: world.cards("design_mismatch")[0].id != card.id, 45)
    new, = world.cards("design_mismatch")
    assert "the report lists 1 design and Studio's proposal lists 3" in new.text
    assert [c.state for c in world.cards("design_mismatch", state=None) if c.id == card.id] == [
        "withdrawn"]
    world.answer(new, "File it as it is")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert world.notes("Testerson_101426")["designs"] == 1
    assert "the report lists 1 design and Studio's proposal lists 3" in world.events(
        "designs_differ_filed")[0].text


def test_when_the_content_changes_and_the_counts_do_not_a_new_card_still_asks(world):
    card = held_on_designs(world, THREE_NAMES, designs=TWO)
    world.studio.jobs["4242"].designs = (("Testerson - Primary Closet", "His and Her Master"),
                                         ("Testerson - Pantry", "Pantry"))
    world.run_until(lambda: world.cards("design_mismatch")[0].id != card.id, 45)
    assert len(world.cards("design_mismatch")) == 1


def test_when_only_the_proposal_changes_a_new_card_tells_the_current_counts(world):
    card = held_on_designs(world)
    world.site.proposals["4242"] = THREE_NAMES
    world.run_until(lambda: world.cards("design_mismatch")[0].id != card.id, 45)
    assert "Studio's proposal lists 3" in world.cards("design_mismatch")[0].text


def test_an_answer_crossing_with_a_change_is_not_taken_for_the_new_content(world):
    card = held_on_designs(world, THREE_NAMES, designs=TWO)
    world.ctx.fetches = BackgroundFetches()
    world.studio.jobs["4242"].designs = CLOSET
    world.clock.set(world.ops(pipeline.FETCH_1)[0].due_at)
    world.tick()
    wait_until(lambda: not world.ctx.fetches._running)
    world.answer(card, "File it as it is")                       # both reach the same tick
    world.tick()
    assert world.events("designs_differ_filed") == [] and world.tree() == {}
    new, = world.cards("design_mismatch")
    assert new.id != card.id and "the report lists 1 design" in new.text
    assert world.ops(pipeline.FETCH_2) == [] and world.ops(pipeline.FETCH_1)


def test_file_it_as_it_is_on_content_other_than_its_card_told_asks_again(world):
    card = held_on_designs(world)
    said = json.loads(get_state(world.store, "job.designs.4242"))
    said["told"]["fingerprint"] = "not what the first fetch holds"
    set_state(world.store, "job.designs.4242", json.dumps(said))
    world.answer(card, "File it as it is")
    world.tick()
    assert world.events("designs_differ_filed") == [] and world.ops(pipeline.FETCH_2) == []
    new, = world.cards("design_mismatch")
    assert new.id != card.id and world.ops(pipeline.FETCH_1)


# ----- two sales with one name


def test_a_second_sale_with_the_same_name_never_touches_the_first_ones_folder(world):
    world.installed(opp("4242", "Testerson_101426"), opp("4299", "Testerson_101426"))
    add_job(world.ctx, studio_id="4242")
    world.run_until(lambda: pipeline.filed_facts(world.store, "4242"), 45)
    before = world.tree()
    add_job(world.ctx, studio_id="4299")
    world.run_until(lambda: pipeline.blocked_reason(world.store, "4299"), 45)
    world.run(30)
    assert world.tree() == before and world.cards("ic_changed") == []
    reason = pipeline.blocked_reason(world.store, "4299")
    assert "Another sale with this name" in reason and check_line(reason) == []
    assert [d.studio_id for d in world.dev("same_folder")] == ["4299"]
    world.run(2 * 60, step=15)
    assert tray(world) == "amber"


# ----- DEV items about a job's fetching end with the filing


def _export_just_read(world: World) -> None:
    """Days pass in one step here, and an old export has its own amber."""
    from ngrunner.store import set_state_time

    set_state_time(world.store, pipeline.FAB_EXPORTED_AT, world.clock.now())


def test_a_dev_item_about_a_job_that_later_files_closes_and_leaves_no_amber(world):
    world.installed(TESTERSON)
    world.studio.jobs["4242"].reply = _Reply(200, b"", {"Content-Length": str(10 ** 9)})
    world.add_row("Testerson")
    world.run_until(lambda: world.dev("too_big"), 15)
    world.studio.jobs["4242"].reply = None
    add_job(world.ctx, studio_id="4242")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert world.dev("too_big") == []
    world.clock.advance(timedelta(days=30))
    _export_just_read(world)
    assert tray(world) == "green"


@pytest.mark.parametrize("code", ["too_big", "layouts_differ", "same_bytes", "wrong_job"])
def test_a_fetch_item_opened_after_filing_never_reads_as_a_stuck_job(world, code):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    open_dev(world.store, at=world.clock.now(), code=code, text="Something about a fetch.",
             studio_id="4242")
    mark_dev_told_jim(world.store, world.dev(code)[0].id, at=world.clock.now())
    world.clock.advance(timedelta(days=3))
    _export_just_read(world)
    assert tray(world) == "green"
    assert not any("waited on NG Runner" in line for line in jobmodel.digest_view(
        world.store, world.settings, world.clock).owner)


def test_a_clean_night_closes_a_wrong_job_item_from_an_earlier_night(world):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    world.studio.jobs["4242"].printed = "Fakeley_101426"
    world.clock.set(world.ops(pipeline.REFETCH)[0].due_at)
    world.tick()
    assert world.dev("wrong_job")
    world.studio.jobs["4242"].printed = None
    world.clock.set(world.ops(pipeline.REFETCH)[0].due_at)
    world.tick()
    assert world.dev("wrong_job") == []


# ----- Barbara's last word names the job the row ended on


def test_a_row_relinked_to_another_job_tells_barbara_that_job_too(world):
    filed_row(world)
    world.correct("Testerson", job="Fakeley")
    world.run(10)
    card, = world.cards("row_changed")
    world.answer(card, card.choices[card.choice_ids.index("4343")])
    world.run_until(lambda: world.row("Fakeley").studio_id == "4343"
                    and world.row("Fakeley").state == "filed", 45)
    world.run(2)
    assert len(world.sent("barbara", "Filed as Fakeley_101426")) == 1
    assert len(world.sent("barbara", "Filed as Testerson_101426")) == 1


# ----- a DEV item of a step holds no raw text, and ends when the step passes


def test_a_step_that_fails_stores_its_name_and_error_class_and_closes_when_it_passes(
        world, monkeypatch):
    world.installed(TESTERSON)
    world.add_row("Testerson")
    real = pipeline.match_fab_row

    def failing(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError(13, "Access is denied", r"G:\Shared\NG Jobs\Testerson_101426")

    monkeypatch.setattr(pipeline, "match_fab_row", failing)
    world.run_until(lambda: world.dev("pipeline_step"), 15)
    item, = world.dev("pipeline_step")
    assert item.text == "match rows: PermissionError" and "Testerson" not in item.text
    world.run(10)
    assert len(world.dev("pipeline_step")) == 1                  # it stands while it fails
    monkeypatch.setattr(pipeline, "match_fab_row", real)
    world.run(5)
    assert world.dev("pipeline_step") == []


def test_one_steps_failure_is_not_closed_by_another_item_passing(world, monkeypatch):
    world.installed(TESTERSON)
    world.add_row("Dummitt")
    world.add_row("Testerson")
    real = pipeline.match_fab_row

    def failing(row: Any, *args: Any, **kwargs: Any) -> Any:
        if row.text == "Dummitt":
            raise ValueError("a row that can't be read")
        return real(row, *args, **kwargs)

    monkeypatch.setattr(pipeline, "match_fab_row", failing)
    world.run_until(lambda: world.row("Testerson").state == "filed", 45)
    assert world.dev("pipeline_step")                            # one row passing closes nothing


# ----- a stop after File the new one's commit


def test_a_stop_after_file_the_new_ones_commit_writes_nothing_twice(world, monkeypatch):
    card = changed_at_night(world)
    world.answer(card, "File the new one")
    real, failed = pipeline.set_governing, []

    def stops_once(*args: Any, **kwargs: Any) -> Any:
        if not failed:
            failed.append(True)
            raise RuntimeError("stopped here")
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline, "set_governing", stops_once)
    backups = world.roots.app_data / "backup"
    world.run(1)
    assert failed and world.ops(pipeline.FILE_NEW)
    made = {path for path in backups.rglob("*") if path.is_file()}
    world.run(1)
    assert world.ops(pipeline.FILE_NEW) == []
    assert {path for path in backups.rglob("*") if path.is_file()} == made
    log = world.folder("Testerson_101426") / "Testerson_101426_NGRunner_log.txt"
    assert log.read_text(encoding="utf-8").count("as Jim chose") == 1
    assert world.events("refiled")
