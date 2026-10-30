"""
The supervisor on a fake clock: a simulated day, restarts, pause and resume, what it does with
nothing set up, the board's actions and the tray.

The pipeline and the messenger are fakes here, so these tests say only what the supervisor does.
The store, the job model, the board, the tray and commit_write are real, under tmp_path. Every
scenario of the real parts together is in tests/test_e2e_day.py.
"""
from __future__ import annotations

import io
import json
import logging
import os
import threading
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from ngrunner import backup, jobmodel, launcher, single, supervisor, toasts
from ngrunner.clock import PACIFIC, FakeClock, pacific_day
from ngrunner.config import AppPaths, Settings, load_settings, save_settings
from ngrunner.intake.studio_designs import StudioSite, StudioUnavailable
from ngrunner.joblist import RenderClient
from ngrunner.messages import Message, templates
from ngrunner.messages.lint import check_line, lint
from ngrunner.secrets import MemorySecrets, SecretsError, SecretsUnavailable
from ngrunner.signing import key_from_text, new_id, new_key, open_envelope, seal
from ngrunner.store import (
    get_state_time,
    MIGRATIONS,
    Store,
    add_card,
    add_outbox,
    ensure_job,
    get_card,
    get_state,
    list_dev,
    list_events,
    list_outbox,
    log_event,
    migrate,
    next_seq,
    open_dev,
    set_state,
)
from ngrunner.supervisor import Supervisor, night_key, run, scan_leftover_temps
from ngrunner.toasts import IMMEDIATE_KINDS, Choice, ToastMessage
from ngrunner.writes import commit_write

KEY_HEX = new_key()
KEY = key_from_text(KEY_HEX)
EXPORT = "an-export-key-for-the-tests"
JOB = "Testerson_101426"


def at(month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=PACIFIC)


# ----- the fakes


@dataclass
class FakeCtx:
    """pipeline.Ctx's fields, and the commit wrapper with an interlock that always says yes."""

    store: Any
    settings: Any
    secrets: Any
    clock: Any
    roots: Any
    paths: Any
    studio_http: Any
    render_client: Any
    messenger: Any
    mode: str
    stop_requested: Any = None
    studio: Any = None

    def commit(self, writes: list) -> Any:
        return commit_write(writes, roots=self.roots, interlock=lambda paths: None)


class FakePipeline:
    Ctx = FakeCtx

    def __init__(self) -> None:
        self.ticks: list[FakeCtx] = []
        self.added: list[dict[str, Any]] = []
        self.fail: Exception | None = None
        self.stopped = 0
        self.went_live = 0

    def tick(self, ctx: FakeCtx) -> None:
        self.ticks.append(ctx)
        if self.fail is not None:
            raise self.fail

    def add_job(self, ctx: FakeCtx, *, name: str | None = None,
                studio_id: str | None = None) -> str:
        self.added.append({"name": name, "studio_id": studio_id})
        return f"Added {name or 'it'}."

    def stop_work(self, ctx: FakeCtx) -> None:
        self.stopped += 1

    def go_live(self, db: Any, now: Any) -> None:
        self.went_live += 1


class FakeMessenger:
    """The seam's shape: its rows are real outbox rows, so the job model reads them as it would."""

    def __init__(self, store: Any, settings: Any, secrets: Any, clock: Any, roots: Any, paths: Any,
                 *, http: Any = None, toaster: Any = None) -> None:
        self.store, self.settings, self.clock, self.toaster = store, settings, clock, toaster
        self.receipt_checks = 0
        self.late = False

    def send(self, message: Any, *, to: str, channel: str = "email",
             ref: str | None = None) -> str:
        now = self.clock.now()
        if ref is None and message.kind in ("digest", "quiet_digest"):
            ref = f"digest:{pacific_day(now).isoformat()}"
        return add_outbox(
            self.store, id=new_id(), channel=channel, recipient=to, subject=message.title,
            seq=next_seq(self.store, "outbox", start=int(now.timestamp())), created_at=now,
            lines=message.lines, urgent=message.urgent, ref=ref or message.kind).id

    def check_receipts(self) -> None:
        self.receipt_checks += 1

    def overdue(self) -> bool:
        return self.late

    def toast_card(self, card: Any) -> Any:
        if card.kind in IMMEDIATE_KINDS and self.settings.mode != "shadow":
            return self.toaster.notify(ToastMessage(card.kind, card.text, (), tuple(
                Choice(label, f"{card.id} {label}") for label in card.choices)))
        return None

    def toast(self, message: Any) -> Any:
        if self.settings.mode != "shadow":
            return self.toaster.notify(ToastMessage(message.kind, message.title, message.lines))
        return None


class FakeTray:
    """What Tray takes as its backend: no icon ever shows."""

    def __init__(self) -> None:
        self.tooltip = ""
        self.menu: tuple[Any, ...] = ()
        self.image: Any = None
        self._stopped = threading.Event()

    def __call__(self, image: Any, tooltip: str, menu: tuple[Any, ...]) -> FakeTray:
        self.show(image, tooltip, menu)
        return self

    def run(self) -> None:
        self._stopped.wait()

    def stop(self) -> None:
        self._stopped.set()

    def show(self, image: Any, tooltip: str, menu: tuple[Any, ...]) -> None:
        self.image, self.tooltip, self.menu = image, tooltip, menu

    def item(self, label: str) -> Any:
        return next(entry for entry in self.menu if entry.label == label).action


class FakeToaster:
    def __init__(self) -> None:
        self.shown: list[ToastMessage] = []
        self.answer = "shown"

    def notify(self, message: ToastMessage) -> str:
        if self.answer == "shown":
            self.shown.append(message)
        return self.answer


@dataclass
class World:
    """One install under tmp_path on one fake clock. Its keys and settings are all there unless a
    test says otherwise."""

    tmp: Path
    clock: FakeClock
    secrets: MemorySecrets
    mode: str = "live"
    backup_key: str = ""
    pipeline: FakePipeline = field(default_factory=FakePipeline)
    tray: FakeTray = field(default_factory=FakeTray)
    toaster: FakeToaster = field(default_factory=FakeToaster)
    opened: list[str] = field(default_factory=list)
    started: list[Supervisor] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.paths = AppPaths(self.tmp / "data")
        self.control, self.ng_jobs = self.tmp / "control", self.tmp / "NG Jobs"
        for folder in (self.paths.app_data, self.control, self.ng_jobs, self.tmp / "Mozaik"):
            folder.mkdir(parents=True, exist_ok=True)

    def save_settings(self) -> None:
        save_settings(self.paths, Settings(
            ng_jobs=self.ng_jobs, mozaik_shared=self.tmp / "Mozaik", control=self.control,
            mode=self.mode, backup_public_key=self.backup_key))

    def start(self, **options: Any) -> Supervisor:
        sup = Supervisor(self.paths, clock=self.clock, secrets=self.secrets, confirm=lambda q: True,
                         tray_backend=self.tray, toaster=self.toaster, browser=self.opened.append,
                         **options)
        self.started.append(sup)
        sup.start()
        return sup

    def restart(self, sup: Supervisor) -> Supervisor:
        sup.stop()
        self.pipeline.ticks.clear()
        return self.start()

    def run(self, sup: Supervisor, *, minutes: int, step: int = 1) -> None:
        for _ in range(minutes // step):
            self.clock.advance(timedelta(minutes=step))
            sup.run_due(self.clock.now())

    @property
    def store(self) -> Store:
        return Store(self.paths.db)

    def heartbeats(self) -> list[Path]:
        return sorted((self.control / "heartbeat").rglob("*.json"))

    def sent(self, *, to: str | None = None, ref: str | None = None) -> list[Any]:
        return [row for row in list_outbox(self.store)
                if (to is None or row.recipient == to) and (ref is None or row.ref == ref)]

    def dev_codes(self) -> list[str]:
        return [item.code for item in list_dev(self.store)]


@pytest.fixture
def make_world(tmp_path, monkeypatch):
    """A world maker: settings saved, both keys there, no real toast setting read."""
    worlds: list[World] = []

    def make(start: datetime, *, mode: str = "live", backup_key: str = "", settings: bool = True,
             secrets: dict[str, str] | None = None) -> World:
        keys = {"signing_key": KEY_HEX, "export_key": EXPORT} if secrets is None else secrets
        world = World(tmp_path / str(len(worlds)), FakeClock(start), MemorySecrets(keys), mode,
                      backup_key)
        monkeypatch.setattr(supervisor, "_seams", lambda: SimpleNamespace(
            pipeline=world.pipeline, messenger=SimpleNamespace(Messenger=FakeMessenger),
            jobmodel=jobmodel))
        if settings:
            world.save_settings()
        worlds.append(world)
        return world

    monkeypatch.setattr(toasts, "toasts_enabled", lambda **kw: True)
    yield make
    for world in worlds:
        for sup in world.started:
            sup.stop()


def browse(sup: Supervisor) -> tuple[requests.Session, str]:
    """A browser on the board, cookie swapped in: the session and the board's base address."""
    session = requests.Session()
    url = sup._board_url
    assert session.get(url, timeout=10).status_code == 200
    return session, url.split("/?")[0]


def post(session: requests.Session, base: str, route: str, sup: Supervisor, **form: str) -> Any:
    token = sup._board_url.split("t=")[1]
    return session.post(base + route, data={"token": token, **form},
                        headers={"Origin": base}, timeout=10)


def colour(world: World, name: str) -> bool:
    """Whether the tray's icon is in that colour now: its centre's edge pixel holds the fill."""
    from ngrunner.tray import COLOURS

    return world.tray.image.getpixel((world.tray.image.width // 2, 8))[:3] == COLOURS[name].fill


# ----- a simulated day


def test_a_day_runs_every_task_on_time_and_once(make_world):
    private, public = backup.new_keypair()
    world = make_world(at(10, 30, 12), backup_key=public)
    leftover = world.ng_jobs / JOB / f"{JOB}_summary.pdf.abc123.ngr-tmp"
    leftover.parent.mkdir()
    leftover.write_bytes(b"left by a crash")
    old_copy = world.paths.backup / "20260101T000000000000Z"
    old_copy.mkdir(parents=True)
    (old_copy / "was.pdf").write_bytes(b"old")
    os.utime(old_copy / "was.pdf", (1767225600, 1767225600))  # January 2026
    os.utime(old_copy, (1767225600, 1767225600))
    new_copy = world.paths.backup / "20261029T000000000000Z"
    new_copy.mkdir()
    (new_copy / "was.pdf").write_bytes(b"new")

    sup = world.start()
    log_event(world.store, at=world.clock.now(), kind="filed", text="Filed", lane="ACT",
              studio_id=ensure_job(world.store, "4242", JOB, now=world.clock.now()).studio_id)
    world.run(sup, minutes=24 * 60, step=5)

    # the 5-minute lines of the schedule: a tick each pass (every minute in real life)
    assert len(world.pipeline.ticks) == 288
    assert sup._messenger.receipt_checks == 288
    beats = world.heartbeats()
    assert len(beats) == 288
    opened = [open_envelope(path.read_bytes(), key=KEY, kind="heartbeat") for path in beats]
    assert [e.seq for e in opened] == sorted(e.seq for e in opened)
    assert opened[-1].payload["paused"] is False

    # the digest: once, at 6:30 AM, named by its date, and not called missed
    digests = world.sent(ref="digest:2026-10-31")
    assert len(digests) == 1 and digests[0].recipient == "jim"
    assert digests[0].created_at == at(10, 31, 6, 30)
    assert "Missed" not in " ".join(digests[0].lines)
    assert world.sent(ref="digest:2026-10-30") == []

    # the night: once, from 1:30 AM
    assert (world.control / "backups" / "20261031.ngrbak").exists()
    assert backup.restore((world.control / "backups" / "20261031.ngrbak").read_bytes(),
                          private).startswith(b"SQLite")
    log_text = (world.control / "logs" / "20261030.txt").read_text(encoding="utf-8")
    assert f"{JOB}: filed" in log_text and "Filed" not in log_text
    assert len(world.sent(to="sulaiman", ref="ran:2026-10-30")) == 1
    assert world.sent(to="sulaiman", ref="ran:2026-10-30")[0].created_at == at(10, 31, 1, 30)
    assert get_state(world.store, "night_key") == "2026-10-31"

    # the night's upkeep: old copies pruned, recent ones kept, leftovers listed and left in place
    assert not old_copy.exists() and new_copy.exists()
    assert leftover.exists()
    assert sup.board().health.leftover_temps == (leftover,)
    assert "leftover_temps" in world.dev_codes()


def test_minute_by_minute_the_cadences_hold(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    world.run(sup, minutes=30)
    assert len(world.pipeline.ticks) == 30
    assert len(world.heartbeats()) == 6
    assert sup._messenger.receipt_checks == 30


def test_a_new_install_owes_no_digest_or_night_for_the_day_it_came(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    world.run(sup, minutes=60)
    assert world.sent() == []


def test_started_before_6_30_the_first_digest_is_that_morning(make_world):
    world = make_world(at(10, 30, 5))
    sup = world.start()
    world.run(sup, minutes=100, step=5)
    assert len(world.sent(ref="digest:2026-10-30")) == 1
    assert world.sent(to="sulaiman") == []  # the night's work began before this install


def test_the_board_serves_every_page_from_the_real_job_model(make_world):
    world = make_world(at(10, 30, 12), backup_key=backup.new_keypair()[1])
    sup = world.start()
    world.run(sup, minutes=2)
    session, base = browse(sup)
    for page in ("/", "/list", "/cards", "/history", "/health"):
        assert session.get(base + page, timeout=10).status_code == 200


# ----- restarts and missed windows


def test_a_restart_repeats_nothing_and_catches_up_what_it_missed_by_name(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    world.run(sup, minutes=20 * 60, step=15)           # to 8 AM the next day
    assert len(world.sent(ref="digest:2026-10-31")) == 1

    sup = world.restart(sup)                           # nothing to do again
    world.run(sup, minutes=30, step=5)
    assert len(world.sent(ref="digest:2026-10-31")) == 1
    assert len(world.sent(ref="ran:2026-10-30")) == 1
    assert world.pipeline.ticks                        # and the minute's work goes on

    sup.stop()                                         # off across the night and the digest
    world.clock.set(at(11, 1, 8, 12))
    sup = world.start()
    assert world.sent(ref="digest:2026-11-01") == []   # nothing is sent before the first pass
    sup.run_due(world.clock.now())
    digest = world.sent(ref="digest:2026-11-01")
    assert len(digest) == 1
    assert "Missed the 6:30 AM digest: the PC was off. Sent at 8:12 AM" in " ".join(digest[0].lines)
    assert any(e.kind == "night_late" and "Missed the 1:30 AM night work" in e.text
               for e in list_events(world.store))
    assert len(world.sent(ref="ran:2026-10-31")) == 1

    sup = world.restart(sup)
    world.run(sup, minutes=10)
    assert len(world.sent(ref="digest:2026-11-01")) == 1


def test_a_digest_queued_before_a_crash_is_not_sent_twice(make_world):
    world = make_world(at(10, 30, 6, 29))
    sup = world.start()
    FakeMessenger(world.store, None, None, world.clock, None, None).send(
        SimpleNamespace(kind="digest", title="Queued", lines=("Queued.",), urgent=False), to="jim")
    world.run(sup, minutes=2)
    assert len(world.sent(ref="digest:2026-10-30")) == 1
    assert get_state(world.store, "last_digest_day") == "2026-10-30"


def test_a_night_that_ran_on_time_and_retried_is_not_called_missed(make_world, monkeypatch):
    world = make_world(at(10, 30, 1, 25), backup_key=backup.new_keypair()[1])
    sup = world.start()
    real, calls = backup.nightly_backup, []

    def away_once(*args: Any, **kw: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise backup.BackupError("Drive is away.")
        return real(*args, **kw)

    monkeypatch.setattr(backup, "nightly_backup", away_once)
    world.run(sup, minutes=40)
    assert len(calls) == 2 and (world.control / "backups" / "20261030.ngrbak").exists()
    assert get_state(world.store, "night_key") == "2026-10-30"
    assert not [e for e in list_events(world.store) if e.kind == "night_late"]


def test_a_night_step_that_fails_tries_again_alone(make_world, monkeypatch):
    world = make_world(at(10, 30, 1, 25))
    sup = world.start()
    real_prune, calls = backup.prune, []

    def flaky(*args: Any, **kw: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("Drive is away")
        return real_prune(*args, **kw)

    monkeypatch.setattr(backup, "prune", flaky)
    world.run(sup, minutes=6)                          # past 1:30: one failure
    assert len(calls) == 1 and "night_step_failed" in world.dev_codes()
    assert get_state(world.store, "night_key") == "2026-10-29"
    assert len(world.sent(ref="ran:2026-10-29")) == 1  # the other steps went on
    world.run(sup, minutes=20)
    assert len(calls) == 1                             # not every minute
    world.run(sup, minutes=11)
    assert len(calls) == 2
    assert "night_step_failed" not in world.dev_codes()  # it worked, so nothing is wrong now
    assert get_state(world.store, "night_key") == "2026-10-30"
    assert len(world.sent(ref="ran:2026-10-29")) == 1  # the done steps did not run again


def test_a_restart_mid_night_runs_only_the_steps_left(make_world):
    world = make_world(at(10, 30, 1, 25))
    sup = world.start()
    set_state(world.store, "night_steps", json.dumps({"key": "2026-10-30", "done": ["ran"]}))
    world.run(sup, minutes=6)
    assert world.sent(ref="ran:2026-10-29") == []      # noted as done before the "crash"
    assert get_state(world.store, "night_key") == "2026-10-30"


def test_the_night_the_clocks_change_runs_once(make_world):
    world = make_world(at(10, 31, 23))                 # 2 AM on 11/1 goes back to 1 AM
    sup = world.start()
    world.run(sup, minutes=7 * 60, step=10)
    assert world.clock.now() > at(11, 1, 4)             # past both 1:30 AMs
    assert len(world.sent(ref="ran:2026-10-31")) == 1
    assert get_state(world.store, "night_key") == "2026-11-01"


def test_the_night_key_is_the_date_whose_1_30_has_passed():
    assert night_key(at(10, 30, 1, 29)).isoformat() == "2026-10-29"
    assert night_key(at(10, 30, 1, 30)).isoformat() == "2026-10-30"
    assert night_key(at(10, 30, 23)).isoformat() == "2026-10-30"
    first = datetime(2026, 11, 1, 1, 30, tzinfo=PACIFIC, fold=0)
    second = datetime(2026, 11, 1, 1, 30, tzinfo=PACIFIC, fold=1)
    assert night_key(first) == night_key(second) == pacific_day(first)


def test_a_clock_set_back_does_not_stop_the_schedule(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    world.run(sup, minutes=3)
    world.clock.set(at(10, 30, 11))
    world.run(sup, minutes=2)
    assert len(world.pipeline.ticks) >= 5


# ----- pause and resume


def test_pause_stops_only_what_the_interlock_stops(make_world):
    world = make_world(at(10, 30, 6))
    sup = world.start()
    assert "Paused" in sup.pause()
    assert get_state(world.store, "paused") == "yes"
    assert colour(world, "grey")
    assert any(entry.label == "Resume" for entry in world.tray.menu)

    world.run(sup, minutes=35)                         # across the 6:30 digest
    assert len(world.pipeline.ticks) == 35             # the interlock refuses filing, not the tick
    assert open_envelope(world.heartbeats()[-1].read_bytes(), key=KEY).payload["paused"] is True
    assert len(world.sent(ref="digest:2026-10-30")) == 1
    assert sup.board().paused is True

    assert "Resumed" in sup.resume()
    assert get_state(world.store, "paused") == "no"
    assert not colour(world, "grey")
    assert [e.kind for e in list_events(world.store)] == ["paused", "resumed"]


def test_the_tray_and_the_command_box_pause_and_resume_too(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    world.tray.item("Pause")()
    assert sup.board().paused is True
    world.tray.item("Resume")()
    assert sup.board().paused is False
    assert "Paused" in sup.command("pause")
    assert sup.board().paused is True
    assert "Resumed" in sup.command("RESUME")
    assert sup.board().paused is False


def test_a_pause_survives_a_restart(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    sup.pause()
    sup = world.restart(sup)
    assert sup.board().paused is True
    assert colour(world, "grey")


def pause_file(world: World, *, seq: int, data: bytes | None = None, key: bytes = KEY,
               reason: str = "Testing") -> Path:
    folder = world.control / "pause"
    folder.mkdir(exist_ok=True)
    id = new_id()
    target = folder / f"{id}.json"
    target.write_bytes(data if data is not None else seal(
        "pause", {"reason": reason}, key=key, seq=seq, issued_at=world.clock.now(), id=id))
    return target


def test_a_signed_pause_file_pauses_once_and_never_again_after_a_resume(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    sup.run_due(world.clock.now())
    pause_file(world, seq=1000, reason="Jim asked")
    world.run(sup, minutes=1)
    assert sup.board().paused is True
    assert colour(world, "grey")
    assert any("Jim asked" in e.text for e in list_events(world.store))

    sup.resume()
    world.run(sup, minutes=2)
    assert sup.board().paused is False                  # the file is still in the folder

    sup = world.restart(sup)
    world.run(sup, minutes=2)
    assert sup.board().paused is False                  # nor after a restart
    assert "pause_file_old" not in world.dev_codes()


def test_a_pause_file_that_cant_be_read_waits_10_minutes_then_goes_to_dev(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    pause_file(world, seq=1, data=b'{"body": "half a fi')
    world.run(sup, minutes=9)
    assert "pause_file_bad" not in world.dev_codes()
    world.run(sup, minutes=2)
    assert "pause_file_bad" in world.dev_codes()
    assert sup.board().paused is False


def test_a_pause_file_signed_with_another_key_or_an_old_number_pauses_nothing(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    pause_file(world, seq=5000, key=key_from_text(new_key()))
    pause_file(world, seq=5000)
    world.run(sup, minutes=1)
    assert sup.board().paused is True
    sup.resume()
    pause_file(world, seq=4000)                         # lower, and new to it
    world.run(sup, minutes=1)
    assert sup.board().paused is False
    assert "pause_file_old" in world.dev_codes()


def test_the_pause_folder_going_away_is_no_problem(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    world.run(sup, minutes=1)                           # no pause folder at all
    assert not [code for code in world.dev_codes() if code.startswith("pause")]
    assert sup.board().paused is False


# ----- with something missing: a board item, never a crash loop


def test_with_no_settings_it_still_comes_up_and_says_so(make_world, monkeypatch):
    world = make_world(at(10, 30, 12), settings=False)
    ups: list[str] = []
    monkeypatch.setattr(launcher, "record_up",
                        lambda paths, attempt, *, clock: ups.append(attempt) or True)
    sup = world.start(attempt="a" * 32)
    assert ups == ["a" * 32]                            # or two starts would retire the version
    world.run(sup, minutes=3)
    assert world.pipeline.ticks == [] and world.heartbeats() == []

    view = sup.board()
    assert view.your_list[0].text == "NG Runner has no settings yet; run its setup first."
    assert view.shadow is True and view.tray.colour == "red"
    assert view.tray.tooltip.startswith("SHADOW")
    assert colour(world, "red")
    assert sup.set_mode("live") == supervisor.NOT_SET_UP
    assert sup.add_job("Testerson") == supervisor.NOT_SET_UP

    session, base = browse(sup)                         # and the real page shows it
    assert "no settings yet" in session.get(base + "/list", timeout=10).text
    assert session.get(base + "/health", timeout=10).status_code == 200


def test_a_settings_file_that_cant_be_read_is_a_board_item_too(make_world):
    world = make_world(at(10, 30, 12), settings=False)
    world.paths.settings.write_text("{not json", encoding="utf-8")
    sup = world.start()
    assert "settings file" in sup.board().your_list[0].text
    world.run(sup, minutes=2)
    assert world.pipeline.ticks == []


def test_a_missing_signing_key_is_a_board_item_and_comes_good_with_no_restart(make_world):
    world = make_world(at(10, 30, 12), secrets={"export_key": EXPORT})
    sup = world.start()
    world.run(sup, minutes=3)
    assert world.pipeline.ticks == [] and world.heartbeats() == []
    assert supervisor.NO_SIGNING_KEY in [item.text for item in sup.board().your_list]
    assert supervisor.NO_SIGNING_KEY in sup.board().health.alarms
    assert sup.board().tray.colour == "red"
    assert sup.board().cards == ()

    world.secrets.put("signing_key", KEY_HEX)
    world.run(sup, minutes=5)
    assert len(world.pipeline.ticks) == 5 and world.heartbeats()
    assert supervisor.NO_SIGNING_KEY not in [item.text for item in sup.board().your_list]


def test_a_signing_key_that_isnt_one_is_a_board_item(make_world):
    world = make_world(at(10, 30, 12), secrets={"signing_key": "not a key", "export_key": EXPORT})
    sup = world.start()
    world.run(sup, minutes=2)
    assert supervisor.BAD_SIGNING_KEY in [item.text for item in sup.board().your_list]
    assert world.pipeline.ticks == []


def test_a_missing_export_key_stops_only_the_job_list(make_world):
    world = make_world(at(10, 30, 12), secrets={"signing_key": KEY_HEX})
    sup = world.start()
    world.run(sup, minutes=5)
    assert len(world.pipeline.ticks) == 5
    assert world.pipeline.ticks[-1].render_client is None
    assert world.heartbeats()
    assert supervisor.NO_EXPORT_KEY in [item.text for item in sup.board().your_list]
    assert supervisor.NO_EXPORT_KEY in [item.text for item in list_dev(world.store)]
    assert sup.board().tray.colour == "red"

    world.secrets.put("export_key", EXPORT)
    world.run(sup, minutes=1)
    assert isinstance(world.pipeline.ticks[-1].render_client, RenderClient)
    assert supervisor.NO_EXPORT_KEY not in [item.text for item in sup.board().your_list]
    assert supervisor.NO_EXPORT_KEY not in [item.text for item in list_dev(world.store)]
    assert sup.board().tray.colour != "red"


def test_an_export_key_that_cant_be_used_is_a_board_item(make_world):
    world = make_world(at(10, 30, 12), secrets={"signing_key": KEY_HEX, "export_key": "two words"})
    sup = world.start()
    world.run(sup, minutes=2)
    assert supervisor.BAD_EXPORT_KEY in [item.text for item in sup.board().your_list]
    assert world.pipeline.ticks[-1].render_client is None


def test_no_credential_manager_is_a_board_item(make_world):
    world = make_world(at(10, 30, 12))

    class NoCredentials(MemorySecrets):
        def get(self, name: str) -> str | None:
            raise SecretsUnavailable("Windows Credential Manager can't be used here")

    world.secrets = NoCredentials()
    sup = world.start()
    world.run(sup, minutes=2)
    assert "Credential Manager" in sup.board().your_list[0].text
    assert world.pipeline.ticks == []
    assert sup.board().tray.colour == "red"


def test_a_blank_backup_key_is_a_board_item_and_not_a_failed_night(make_world):
    world = make_world(at(10, 30, 1, 25))
    sup = world.start()
    world.run(sup, minutes=6)
    assert supervisor.NO_BACKUP_KEY in sup.board().health.alarms
    assert sup.board().tray.colour != "red"
    assert not (world.control / "backups").exists()
    assert get_state(world.store, "night_key") == "2026-10-30"


def test_a_backup_that_fails_goes_to_dev_and_tries_again(make_world, monkeypatch):
    world = make_world(at(10, 30, 1, 25), backup_key=backup.new_keypair()[1])
    sup = world.start()

    def broken(*args: Any, **kw: Any) -> Any:
        raise backup.BackupError("There is no database to back up yet.")

    monkeypatch.setattr(backup, "nightly_backup", broken)
    world.run(sup, minutes=6)
    assert "backup_failed" in world.dev_codes()
    assert get_state(world.store, "night_key") != "2026-10-30"
    monkeypatch.undo()
    world.run(sup, minutes=31)
    assert "backup_failed" not in world.dev_codes()    # it worked, so nothing is wrong now


def test_a_log_that_could_not_be_saved_closes_its_item_once_it_is_saved(make_world, monkeypatch):
    world = make_world(at(10, 30, 1, 25))
    sup = world.start()
    real = FakeCtx.commit

    def no_logs(self: FakeCtx, writes: list) -> Any:
        if any(write.path.parent.name == "logs" for write in writes):
            return SimpleNamespace(ok=False, reason="Drive is away.")
        return real(self, writes)

    monkeypatch.setattr(FakeCtx, "commit", no_logs)
    world.run(sup, minutes=6)
    assert "log_failed" in world.dev_codes()
    monkeypatch.undo()
    world.run(sup, minutes=31)
    assert "log_failed" not in world.dev_codes()


def test_what_pruning_left_goes_to_dev(make_world, monkeypatch):
    world = make_world(at(10, 30, 1, 25))
    sup = world.start()
    monkeypatch.setattr(backup, "prune", lambda paths, *, clock: backup.Pruned((), ("A link.",)))
    world.run(sup, minutes=6)
    assert "A link." in [item.text for item in list_dev(world.store)]


def test_a_database_it_cant_read_shows_red_and_is_tried_again(make_world):
    world = make_world(at(10, 30, 12))
    world.paths.db.write_bytes(b"this is not a database, and it is long enough to be read" * 200)
    sup = world.start()
    world.run(sup, minutes=2)
    assert world.pipeline.ticks == []
    view = sup.board()
    assert view.tray.colour == "red" and supervisor.NO_STORE in [i.text for i in view.your_list]
    assert colour(world, "red")
    assert sup.pause() == supervisor.CANT_DO_THAT
    session, base = browse(sup)
    assert session.get(base + "/list", timeout=10).status_code == 200

    world.paths.db.unlink()                             # restored
    world.run(sup, minutes=2)
    assert world.pipeline.ticks
    assert supervisor.NO_STORE not in [i.text for i in sup.board().your_list]


def test_a_database_about_to_be_upgraded_is_copied_first(make_world):
    world = make_world(at(10, 30, 12))
    old = Store(world.paths.db)
    migrate(old, MIGRATIONS[:1])
    assert old.schema_version() == 1
    sup = world.start()
    assert sup._store.schema_version() == len(MIGRATIONS)
    copies = list(world.paths.backup.rglob("ngrunner-v1.db"))
    assert len(copies) == 1
    assert Store(copies[0]).schema_version() == 1


def test_a_new_database_has_nothing_to_copy(make_world):
    world = make_world(at(10, 30, 12))
    world.start()
    assert not world.paths.backup.exists()


def test_one_task_failing_stops_nothing_else(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    world.pipeline.fail = ValueError("something odd")
    world.run(sup, minutes=6)
    assert len(world.pipeline.ticks) == 6 and world.heartbeats()
    assert world.dev_codes().count("task_failed") == 1  # one standing problem, one item


def test_a_failed_heartbeat_is_one_dev_item(make_world, monkeypatch):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    monkeypatch.setattr(FakeCtx, "commit", lambda self, writes: SimpleNamespace(
        ok=False, reason="Drive is away."))
    world.run(sup, minutes=11)
    assert world.dev_codes().count("heartbeat_failed") == 1


def test_a_heartbeat_that_gets_through_again_closes_its_item(make_world, monkeypatch):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    with monkeypatch.context() as away:
        away.setattr(FakeCtx, "commit", lambda self, writes: SimpleNamespace(
            ok=False, reason="Drive is away."))
        world.run(sup, minutes=11)
    assert "heartbeat_failed" in world.dev_codes()
    world.run(sup, minutes=11)
    assert "heartbeat_failed" not in world.dev_codes()


def test_the_last_studio_sign_in_survives_in_the_store(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    assert sup._last_studio_sign_in() is None
    sup._note_studio_sign_in(world.clock.now())
    assert sup._last_studio_sign_in() == world.clock.now()
    assert get_state_time(world.store, "studio.last_sign_in") == world.clock.now()


# ----- the board's actions


def card(world: World, *, kind: str = "closet_or_garage") -> Any:
    ensure_job(world.store, "4242", JOB, now=world.clock.now())
    return add_card(world.store, prefix="TES", kind=kind, text="Is it a closet or a garage?",
                    choices=("Closet", "Garage"), created_at=world.clock.now(), studio_id="4242")


def test_a_cards_button_records_the_answer_as_jims(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    record = card(world)
    assert sup.answer(record.id, "Garage") == f"Saved: {record.id}, Garage."
    saved = get_card(world.store, record.id)
    assert (saved.state, saved.answer, saved.answer_words, saved.answered_by, saved.channel) == (
        "answered", "Garage", "Garage", "jim", "board")
    assert sup.answer(record.id, "Closet") == supervisor.NOT_OPEN
    assert sup.answer("NOPE-1", "Closet") == supervisor.NOT_OPEN
    other = card(world)
    assert sup.answer(other.id, "Shed") == supervisor.NOT_A_CHOICE
    assert get_card(world.store, other.id).state == "open"


def test_the_command_box_goes_through_the_parser(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    record = card(world)
    assert sup.command("what now?").startswith("I didn't understand. Try:")
    assert sup.command(f"{record.id.lower()} 2") == f"Saved: {record.id}, Garage."
    saved = get_card(world.store, record.id)
    assert (saved.answer, saved.answer_words, saved.channel) == (
        "Garage", f"{record.id.lower()} 2", "command")


def test_add_job_goes_to_the_pipeline_with_digits_as_a_studio_id(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    assert sup.add_job(JOB) == f"Added {JOB}."
    assert sup.add_job("4242") == "Added it."
    assert sup.command(f"add job {JOB}") == f"Added {JOB}."
    assert world.pipeline.added == [{"name": JOB, "studio_id": None},
                                    {"name": None, "studio_id": "4242"},
                                    {"name": JOB, "studio_id": None}]
    assert sup.add_job("   ").startswith("I didn't understand.")


def test_check_now_runs_the_tick_at_the_next_pass(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    sup.run_due(world.clock.now())
    sup.run_due(world.clock.now())
    assert len(world.pipeline.ticks) == 1
    assert sup.check_now() == supervisor.CHECKING
    sup.run_due(world.clock.now())
    assert len(world.pipeline.ticks) == 2


def test_the_switch_to_live_is_saved_logged_and_takes_effect(make_world):
    world = make_world(at(10, 30, 12), mode="shadow")
    sup = world.start()
    world.run(sup, minutes=1)
    assert sup.board().shadow is True and world.tray.tooltip.startswith("SHADOW")
    assert world.pipeline.ticks[-1].mode == "shadow"

    assert "live" in sup.set_mode("live")
    assert world.pipeline.went_live == 1               # hands back what the trial run filed aside
    assert load_settings(world.paths).mode == "live"
    assert sup._messenger.settings.mode == "live"
    assert sup.board().shadow is False and not world.tray.tooltip.startswith("SHADOW")
    world.run(sup, minutes=1)
    assert world.pipeline.ticks[-1].mode == "live"
    assert world.pipeline.ticks[-1].settings.mode == "live"
    event = next(e for e in list_events(world.store) if e.kind == "mode_changed")
    assert event.text == "Switched from shadow to live." and event.lane == "ACT"

    assert "already" in sup.set_mode("live")
    assert len([e for e in list_events(world.store) if e.kind == "mode_changed"]) == 1
    assert "shadow" in sup.set_mode("shadow")
    assert load_settings(world.paths).mode == "shadow"
    assert world.pipeline.went_live == 1               # going back to shadow hands nothing back


def test_the_real_board_takes_clicks_through_every_guard(make_world):
    world = make_world(at(10, 30, 12), mode="shadow")
    sup = world.start()
    session, base = browse(sup)
    assert post(session, base, "/pause", sup).status_code == 200
    assert sup.board().paused is True
    assert post(session, base, "/resume", sup).status_code == 200
    assert sup.board().paused is False
    record = card(world)
    post(session, base, "/cards/answer", sup, card=record.id, choice="Closet")
    assert get_card(world.store, record.id).answer == "Closet"
    assert post(session, base, "/mode", sup, mode="live").status_code == 200
    assert load_settings(world.paths).mode == "live"
    assert session.post(base + "/pause", data={"token": "wrong"}, headers={"Origin": base},
                        timeout=10).status_code == 403
    assert sup.board().paused is False


def test_open_is_limited_to_the_job_folders_renders_and_shadow(make_world):
    world = make_world(at(10, 30, 12))
    opened: list[str] = []
    sup = world.start(opener=opened.append)
    inside = world.ng_jobs / JOB
    inside.mkdir()
    session, base = browse(sup)
    post(session, base, "/open", sup, path=str(inside))
    assert opened == [str(inside)]
    post(session, base, "/open", sup, path=str(world.tmp))
    assert opened == [str(inside)]


# ----- toasts


def test_a_card_that_holds_a_job_pops_up_once_with_its_buttons(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    record = card(world)
    world.run(sup, minutes=3)
    assert [t.kind for t in world.toaster.shown] == ["closet_or_garage"]
    assert world.toaster.shown[0].choices[1] == Choice("Garage", f"{record.id} Garage")
    sup = world.restart(sup)
    world.run(sup, minutes=3)
    assert len(world.toaster.shown) == 1


def test_a_fetch_failed_card_waits_for_the_digest(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    card(world, kind="fetch_failed")
    world.run(sup, minutes=2)
    assert world.toaster.shown == []


def test_shadow_mode_shows_no_toast_and_a_switch_to_live_brings_no_flood(make_world):
    world = make_world(at(10, 30, 6, 25), mode="shadow")
    sup = world.start()
    card(world)
    world.run(sup, minutes=10)                          # a card, and the digest
    assert world.toaster.shown == []
    sup.toast_choice("not a command")
    assert world.toaster.shown == []
    sup.set_mode("live")
    world.clock.set(at(10, 30, 7))
    card(world)
    world.run(sup, minutes=2)
    assert len([t for t in world.toaster.shown if t.kind == "closet_or_garage"]) == 1


def test_the_morning_list_toast_shows_with_the_digest_once(make_world):
    world = make_world(at(10, 30, 6, 25))
    sup = world.start()
    world.run(sup, minutes=10)
    assert [t.kind for t in world.toaster.shown] == ["morning_list"]
    assert world.toaster.shown[0].title == world.sent(ref="digest:2026-10-30")[0].subject
    sup = world.restart(sup)
    world.run(sup, minutes=3)
    assert len(world.toaster.shown) == 1


def test_the_morning_list_held_back_tries_again_and_a_late_start_shows_it(make_world):
    world = make_world(at(10, 30, 5))
    sup = world.start()
    sup.stop()
    world.clock.set(at(10, 30, 9))
    world.toaster.answer = "held"
    sup = world.start()
    world.run(sup, minutes=2)
    assert world.toaster.shown == [] and get_state(world.store, "morning_toast_day") is None
    world.toaster.answer = "shown"
    world.run(sup, minutes=1)
    assert [t.kind for t in world.toaster.shown] == ["morning_list"]


def test_a_click_on_a_toast_answers_or_opens_the_board(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    record = card(world)
    sup.toast_choice(toasts.OPEN_BOARD)
    assert world.opened == [sup._board_url]
    sup.toast_choice(f"{record.id} Garage")
    saved = get_card(world.store, record.id)
    assert (saved.answer, saved.channel) == ("Garage", "toast")
    assert world.toaster.shown[-1].kind == "echo"
    assert world.toaster.shown[-1].title == f"Saved: {record.id}, Garage."


# ----- the tray


def test_the_trays_colour_follows_the_receipts(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    sup.run_due(world.clock.now())
    assert colour(world, "green")
    sup._messenger.late = True
    world.run(sup, minutes=1)
    assert colour(world, "red")
    sup._messenger.late = False
    world.run(sup, minutes=1)
    assert colour(world, "green")


def test_quit_records_it_and_asks_for_a_stop(make_world, monkeypatch):
    world = make_world(at(10, 30, 12))
    quits: list[Any] = []
    monkeypatch.setattr(launcher, "record_quit", lambda paths, *, clock: quits.append(paths))
    sup = world.start()
    assert sup.stop_requested is False
    world.tray.item("Quit")()
    assert quits == [world.paths] and sup.stop_requested is True


def test_the_support_bundle_goes_to_the_control_folder_on_the_next_pass(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    world.tray.item("Send support bundle")()
    assert not (world.control / "support").exists()
    sup.run_due(world.clock.now())
    bundles = list((world.control / "support").glob("support-*.zip"))
    assert len(bundles) == 1
    token = sup._board_url.split("t=")[1]
    with zipfile.ZipFile(io.BytesIO(bundles[0].read_bytes())) as bundle:
        names = bundle.namelist()
        assert "bundle.json" in names
        assert all(token.encode() not in bundle.read(name) for name in names)


def test_with_no_settings_the_support_bundle_stays_on_this_pc(make_world):
    world = make_world(at(10, 30, 12), settings=False)
    sup = world.start()
    sup.ask_support_bundle()
    sup.run_due(world.clock.now())
    assert len(list((world.paths.app_data / "support").glob("support-*.zip"))) == 1


def test_open_board_goes_to_the_browser(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    world.tray.item("Open board")()
    assert world.opened == [sup._board_url]


# ----- start-up order, logging and the board's address


def test_start_up_runs_in_the_specs_order_and_settles_the_writes(make_world, monkeypatch):
    world = make_world(at(10, 30, 12))
    order: list[str] = []
    real_publish = single.publish_board
    monkeypatch.setattr(supervisor, "open_store", (lambda real: lambda path: (
        order.append("store"), real(path))[1])(supervisor.open_store))
    monkeypatch.setattr(single, "publish_board", lambda paths, url: (
        order.append("publish"), real_publish(paths, url))[1])
    monkeypatch.setattr(launcher, "record_up", lambda paths, attempt, *, clock: order.append("up"))
    monkeypatch.setattr(launcher, "pending_notes", lambda paths: order.append("notes") or [])
    monkeypatch.setattr(supervisor, "settle_pending_writes",
                        lambda store, *, at: order.append("settle"))
    world.start(attempt="a" * 32)
    assert order == ["store", "publish", "up", "notes", "settle"]


def test_nothing_before_record_up_touches_the_shared_folders(make_world, monkeypatch):
    world = make_world(at(10, 30, 12))
    seen: list[str] = []
    real_stat, real_listdir = os.stat, os.listdir
    shared = (str(world.control), str(world.ng_jobs))

    def note(path: Any) -> None:
        if str(path).startswith(shared):
            seen.append(str(path))

    def stat_spy(path: Any, *args: Any, **kw: Any) -> Any:
        note(path)
        return real_stat(path, *args, **kw)

    def listdir_spy(path: Any = ".") -> Any:
        note(path)
        return real_listdir(path)

    monkeypatch.setattr(os, "stat", stat_spy)
    monkeypatch.setattr(os, "listdir", listdir_spy)
    monkeypatch.setattr(launcher, "record_up", lambda *a, **k: seen.append("UP"))
    world.start(attempt="a" * 32)
    assert seen.index("UP") == 0


def test_launcher_notes_become_dev_items_then_are_acknowledged(make_world, monkeypatch):
    world = make_world(at(10, 30, 12))
    acked: list[list[str]] = []
    note = launcher.DevNote("n1", world.clock.now(), "Version 0.1 was retired.")
    monkeypatch.setattr(launcher, "pending_notes", lambda paths: [note])
    monkeypatch.setattr(launcher, "ack_notes", lambda paths, ids: acked.append(list(ids)))
    world.start()
    assert [i.text for i in list_dev(world.store) if i.code == "launcher"] == [note.text]
    assert acked == [["n1"]]


def test_settings_this_version_does_not_know_go_to_dev_by_name(make_world):
    world = make_world(at(10, 30, 12))
    raw = json.loads(world.paths.settings.read_text(encoding="utf-8"))
    world.paths.settings.write_text(json.dumps({**raw, "from_the_future": "secret value"}),
                                    encoding="utf-8")
    world.start()
    text = next(i.text for i in list_dev(world.store) if i.code == "settings_set_aside")
    assert "from_the_future" in text and "secret value" not in text


def test_the_log_is_one_rotating_file_and_never_holds_the_boards_address(make_world, capsys):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    world.run(sup, minutes=2)
    token = sup._board_url.split("t=")[1]
    logging.getLogger("ngrunner.test").warning("an odd thing happened")
    logging.getLogger("waitress.test").warning("a waitress thing happened")
    logging.getLogger("ngrunner.test").warning("opened %s", sup._board_url)
    sup.stop()
    text = (world.paths.logs / "ngrunner.log").read_text(encoding="utf-8")
    assert "an odd thing happened" in text and "a waitress thing happened" in text
    assert token not in text
    out = capsys.readouterr()
    assert token not in out.out and token not in out.err
    assert not logging.getLogger("ngrunner").handlers  # stop() lets go of the file


def test_stopping_stops_the_pipeline_and_the_board_and_can_be_done_twice(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    world.run(sup, minutes=1)
    ctx = world.pipeline.ticks[-1]
    assert ctx.stop_requested() is False
    board_file = world.paths.app_data / single.BOARD_FILE
    assert sup._board_url in board_file.read_text(encoding="utf-8")
    sup.stop()
    assert ctx.stop_requested() is True and world.pipeline.stopped == 1
    assert not board_file.exists()
    sup.stop()
    assert world.pipeline.stopped == 1


def test_the_scan_for_leftovers_only_reads(tmp_path):
    keep = tmp_path / "Job" / "sub"
    keep.mkdir(parents=True)
    (keep / "a.pdf.123.ngr-tmp").write_bytes(b"x")
    (keep / "a.pdf").write_bytes(b"y")
    assert scan_leftover_temps([tmp_path, tmp_path / "gone"]) == [
        str(keep / "a.pdf.123.ngr-tmp")]
    assert sorted(p.name for p in keep.iterdir()) == ["a.pdf", "a.pdf.123.ngr-tmp"]
    for n in range(5):
        (keep / f"{n}.ngr-tmp").write_bytes(b"x")
    assert len(scan_leftover_temps([tmp_path], limit=3)) == 3


# ----- run: the command line's way in


def test_a_second_start_opens_the_running_board_and_exits_0(make_world, monkeypatch, capsys):
    world = make_world(at(10, 30, 12))
    name = f"Local\\NGRunner.Test.{uuid.uuid4().hex}"
    held, done = threading.Event(), threading.Event()

    def holder() -> None:
        guard = single.acquire(name)
        held.set()
        done.wait(10)
        guard.release()

    thread = threading.Thread(target=holder)
    thread.start()
    held.wait(5)
    opened: list[Any] = []
    monkeypatch.setattr(single, "open_running_board", lambda paths: opened.append(paths) or True)
    try:
        assert run(world.paths, mutex=name) == 0
        assert opened == []
        assert run(world.paths, open_board=True, mutex=name) == 0
        assert opened == [world.paths]
    finally:
        done.set()
        thread.join()
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_run_comes_up_works_and_stops_cleanly_when_asked(make_world, monkeypatch, capsys):
    world = make_world(at(10, 30, 12))
    tag = uuid.uuid4().hex
    mutex, event = f"Local\\NGRunner.Test.{tag}", f"Local\\NGRunner.TestStop.{tag}"
    ups: list[str] = []
    monkeypatch.setattr(launcher, "record_up",
                        lambda paths, attempt, *, clock: ups.append(attempt) or True)
    result: list[int] = []
    thread = threading.Thread(target=lambda: result.append(run(
        world.paths, attempt="b" * 32, open_board=True, mutex=mutex, stop_event=event,
        clock=world.clock, secrets=world.secrets, tray_backend=world.tray, toaster=world.toaster,
        browser=world.opened.append)))
    thread.start()
    board_file = world.paths.app_data / single.BOARD_FILE
    deadline = threading.Event()
    for _ in range(200):
        if world.pipeline.ticks:
            break
        deadline.wait(0.05)
    assert world.pipeline.ticks                         # the scheduler thread is running
    assert board_file.exists() and ups == ["b" * 32]
    assert len(world.opened) == 1
    assert single.request_stop(event) is True
    thread.join(20)
    assert result == [0] and not thread.is_alive()
    assert not board_file.exists()
    out = capsys.readouterr()
    assert world.opened[0] not in out.out and world.opened[0] not in out.err
    guard = single.acquire(mutex, wait_s=0)             # the lock was let go
    assert guard is not None
    guard.release()


def test_tray_quit_ends_run(make_world, monkeypatch):
    world = make_world(at(10, 30, 12))
    tag = uuid.uuid4().hex
    monkeypatch.setattr(launcher, "record_quit", lambda paths, *, clock: True)
    result: list[int] = []
    thread = threading.Thread(target=lambda: result.append(run(
        world.paths, mutex=f"Local\\NGRunner.Test.{tag}",
        stop_event=f"Local\\NGRunner.TestStop.{tag}", clock=world.clock, secrets=world.secrets,
        tray_backend=world.tray, toaster=world.toaster, confirm=lambda q: True)))
    thread.start()
    deadline = threading.Event()
    for _ in range(200):
        if world.tray.menu:
            break
        deadline.wait(0.05)
    world.tray.item("Quit")()
    thread.join(20)
    assert result == [0]


# ----- the real seams, as far as they have landed


def test_the_real_messenger_fits_the_supervisor(make_world, monkeypatch):
    messenger = pytest.importorskip("ngrunner.messenger")
    world = make_world(at(10, 30, 6, 25))
    monkeypatch.setattr(supervisor, "_seams", lambda: SimpleNamespace(
        pipeline=world.pipeline, messenger=messenger, jobmodel=jobmodel))
    sup = world.start()
    assert isinstance(sup._messenger, messenger.Messenger)
    world.run(sup, minutes=10)
    assert [row.ref for row in world.sent(to="jim")] == ["digest:2026-10-30"]
    assert len(list((world.control / "outbox").glob("*.json"))) == 1


def test_a_dev_item_over_48_hours_is_told_once_through_the_next_digest(make_world):
    world = make_world(at(10, 28, 1, 25))
    sup = world.start()
    ensure_job(world.store, "4242", JOB, now=world.clock.now())
    open_dev(world.store, at=world.clock.now() - timedelta(hours=49), code="odd_thing",
             text="An odd thing.", studio_id="4242")
    open_dev(world.store, at=world.clock.now() - timedelta(hours=49), code="about_no_job",
             text="Another odd thing.")
    world.run(sup, minutes=6)                           # 1:30 AM: the night's work marks it
    told = {item.code: item.told_jim_at for item in list_dev(world.store)}
    assert told["odd_thing"] == at(10, 28, 1, 30).astimezone(told["odd_thing"].tzinfo)
    assert told["about_no_job"] is None                 # that one stays with Sulaiman
    world.run(sup, minutes=5 * 60, step=30)
    lines = " ".join(world.sent(ref="digest:2026-10-28")[0].lines)
    assert f"{JOB} has waited on NG Runner for over 2 days" in lines
    world.run(sup, minutes=24 * 60, step=60)
    assert f"{JOB} has waited" not in " ".join(world.sent(ref="digest:2026-10-29")[0].lines)


def test_a_card_due_back_is_marked_when_the_digest_brings_it_back(make_world):
    world = make_world(at(10, 28, 12))
    sup = world.start()
    record = card(world)
    world.clock.set(at(10, 29, 12, 30))
    sup.run_due(world.clock.now())                      # one day old: its digest brings it back
    reminded = (at(10, 29, 12, 30).astimezone(world.clock.now().tzinfo),)
    assert get_card(world.store, record.id).reminded_at == reminded
    world.clock.set(at(10, 30, 6, 30))
    sup.run_due(world.clock.now())                      # and once per step: not again on 10/30
    assert get_card(world.store, record.id).reminded_at == reminded


def real_messenger(world: World, monkeypatch: Any) -> None:
    from ngrunner import messenger

    monkeypatch.setattr(supervisor, "_seams", lambda: SimpleNamespace(
        pipeline=world.pipeline, messenger=messenger, jobmodel=jobmodel))


def test_a_digest_the_lint_replaced_marks_no_card_so_the_next_one_brings_it_back(
        make_world, monkeypatch):
    world = make_world(at(10, 28, 12))
    real_messenger(world, monkeypatch)
    sup = world.start()
    record = card(world)
    kept = supervisor.digest.compose
    bad = Message("digest", "Friday's list", ("1. It is in C:\\Users\\Testerson\\Fab.pdf",))
    assert lint(bad)
    monkeypatch.setattr(supervisor.digest, "compose", lambda view: bad)
    world.clock.set(at(10, 29, 12, 30))
    sup.run_due(world.clock.now())
    sent, = world.sent(ref="digest:2026-10-29")
    assert sent.lines != bad.lines                      # the fixed template went instead
    assert "message_replaced" in world.dev_codes()
    assert get_card(world.store, record.id).reminded_at == ()
    monkeypatch.setattr(supervisor.digest, "compose", kept)
    world.clock.set(at(10, 30, 6, 30))
    sup.run_due(world.clock.now())
    assert get_card(world.store, record.id).reminded_at != ()


def test_a_digest_redirected_in_shadow_mode_still_marks_its_cards(make_world, monkeypatch):
    world = make_world(at(10, 28, 12), mode="shadow")
    real_messenger(world, monkeypatch)
    sup = world.start()
    record = card(world)
    world.clock.set(at(10, 29, 12, 30))
    sup.run_due(world.clock.now())
    sent, = world.sent(ref="digest:2026-10-29")
    assert sent.recipient == "sulaiman" and sent.subject.startswith("SHADOW")
    assert get_card(world.store, record.id).reminded_at != ()


@pytest.mark.parametrize("restart_at", [at(10, 30, 1, 45), at(10, 31, 0, 10)],
                         ids=["the same night", "after midnight"])
def test_a_crash_after_the_nights_lines_went_sends_neither_again(make_world, restart_at):
    """The crash came after the sends, before their steps were noted: the night runs them again,
    and finds their refs in the outbox."""
    world = make_world(at(10, 30, 1, 25))
    sup = world.start()
    open_dev(world.store, at=world.clock.now(), code="a_problem", text="A problem.")
    world.run(sup, minutes=6)
    assert get_state(world.store, "night_key") == "2026-10-30"
    sent = [(row.id, row.ref) for row in world.sent(to="sulaiman")]
    assert {ref for _, ref in sent} == {"ran:2026-10-29", "dev:2026-10-30"}
    set_state(world.store, "night_key", "2026-10-29")
    set_state(world.store, "night_steps", json.dumps({"key": "2026-10-30", "done": ["backup"]}))
    world.clock.set(restart_at)
    sup.run_due(world.clock.now())
    assert get_state(world.store, "night_key") == "2026-10-30"
    assert [(row.id, row.ref) for row in world.sent(to="sulaiman")] == sent


def test_the_real_messenger_keeps_the_refs_the_night_looks_for(make_world, monkeypatch):
    world = make_world(at(10, 30, 1, 25))
    real_messenger(world, monkeypatch)
    sup = world.start()
    open_dev(world.store, at=world.clock.now(), code="a_problem", text="A problem.")
    world.run(sup, minutes=6)
    sent = sorted(row.ref for row in world.sent(to="sulaiman"))
    assert sent == ["dev:2026-10-30", "ran:2026-10-29"]
    set_state(world.store, "night_key", "2026-10-29")
    set_state(world.store, "night_steps", "{}")
    world.run(sup, minutes=1)
    assert sorted(row.ref for row in world.sent(to="sulaiman")) == sent


def test_the_next_nights_lines_still_go(make_world):
    world = make_world(at(10, 30, 1, 25))
    sup = world.start()
    open_dev(world.store, at=world.clock.now(), code="a_problem", text="A problem.")
    world.run(sup, minutes=6)
    world.clock.set(at(10, 31, 1, 30))
    sup.run_due(world.clock.now())
    assert {row.ref for row in world.sent(to="sulaiman")} == {
        "ran:2026-10-29", "dev:2026-10-30", "ran:2026-10-30", "dev:2026-10-31"}


@pytest.mark.parametrize("flag, paused", [("yes", True), ("no", False), ("anything else", True)])
def test_the_tray_and_the_interlock_read_the_paused_flag_alike(make_world, flag, paused):
    from ngrunner.interlock import is_paused

    world = make_world(at(10, 30, 12))
    sup = world.start()
    set_state(world.store, "paused", flag)
    assert sup._is_paused() is is_paused(world.store) is paused
    assert sup.board().paused is paused
    assert (sup.board().tray.colour == "grey") is paused


# ----- Ctx.studio, from the saved login


STUDIO_LOGIN = {"studio_username": "testerson@example.com", "studio_password": "not-a-real-one"}


class StudioDown:
    """Stands in for Studio's address: it says who asked, and that it is down. No network."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, **options: Any) -> Any:
        self.asked.append((url, options["cookies"]))
        return SimpleNamespace(status_code=503, headers={}, url=url, text="")


def test_with_no_studio_login_it_is_a_board_item_and_nothing_else_stops(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    assert sup._ctx.studio is None
    assert supervisor.NO_STUDIO_LOGIN in sup.board().health.alarms
    assert sup.board().tray.colour == "green"
    assert "setup" in world.dev_codes() and "task_failed" not in world.dev_codes()
    world.run(sup, minutes=2)
    assert len(world.pipeline.ticks) == 2 and world.pipeline.ticks[-1].studio is None


def test_the_words_for_a_missing_login_pass_the_message_check():
    for words in (supervisor.NO_STUDIO_LOGIN, supervisor.BAD_STUDIO_LOGIN):
        assert not check_line(words), words
        dev = templates.dev((templates.DevLine(at(10, 30), words, None, "setup"),),
                            day=at(10, 30).date())
        assert not lint(dev), words


@pytest.mark.parametrize("half", ["studio_username", "studio_password"])
def test_half_a_login_is_no_login(make_world, half):
    world = make_world(at(10, 30, 12), secrets={"signing_key": KEY_HEX, "export_key": EXPORT,
                                                half: STUDIO_LOGIN[half]})
    sup = world.start()
    assert sup._ctx.studio is None
    assert supervisor.NO_STUDIO_LOGIN in sup.board().health.alarms


def test_a_saved_login_builds_one_studio_site_with_no_restart(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    for name, value in STUDIO_LOGIN.items():
        world.secrets.put(name, value)
    world.run(sup, minutes=1)
    site = sup._ctx.studio
    assert isinstance(site, StudioSite)
    assert world.pipeline.ticks[-1].studio is site
    assert supervisor.NO_STUDIO_LOGIN not in sup.board().health.alarms
    assert not [i for i in list_dev(world.store)
                if i.code == "setup" and i.text == supervisor.NO_STUDIO_LOGIN]
    world.run(sup, minutes=3)
    assert sup._ctx.studio is site                      # it remembers its last sign-in


def test_a_login_taken_away_is_none_again_and_coming_back_keeps_the_same_site(make_world):
    world = make_world(at(10, 30, 12), secrets={"signing_key": KEY_HEX, "export_key": EXPORT,
                                                **STUDIO_LOGIN})
    sup = world.start()
    site = sup._ctx.studio
    assert isinstance(site, StudioSite)
    world.secrets.remove("studio_password")
    world.run(sup, minutes=1)
    assert sup._ctx.studio is None
    assert supervisor.NO_STUDIO_LOGIN in sup.board().health.alarms
    world.secrets.put("studio_password", STUDIO_LOGIN["studio_password"])
    world.run(sup, minutes=1)
    assert sup._ctx.studio is site                      # a wrong password can't lock the account


def test_the_studio_site_reads_with_the_saved_secrets_and_no_browser(make_world):
    session = {"sessionid": "a" * 32, "csrftoken": "b" * 64}
    world = make_world(at(10, 30, 12), secrets={
        "signing_key": KEY_HEX, "export_key": EXPORT, **STUDIO_LOGIN,
        "studio_session": json.dumps(session)})
    down = StudioDown()
    sup = world.start(http=down)
    with pytest.raises(StudioUnavailable) as caught:
        sup._ctx.studio.project_page("4242")            # with its session: no sign-in is needed
    assert caught.value.code == "network"
    assert [cookies for _, cookies in down.asked] == [session]
    assert down.asked[0][0].endswith("/project/details/4242/")


def test_the_studio_site_lasts_through_a_switch_of_mode_and_a_new_export_key(make_world):
    world = make_world(at(10, 30, 12), secrets={"signing_key": KEY_HEX, "export_key": EXPORT,
                                                **STUDIO_LOGIN})
    sup = world.start()
    site = sup._ctx.studio
    sup.set_mode("shadow")
    world.secrets.put("export_key", "another-export-key-for-the-tests")
    world.run(sup, minutes=2)
    assert sup._ctx.studio is site and sup._ctx.mode == "shadow"
    assert sup._ctx.render_client is not None


def test_the_studio_site_keeps_its_last_sign_in_where_a_restart_can_read_it(make_world):
    world = make_world(at(10, 30, 12), secrets={"signing_key": KEY_HEX, "export_key": EXPORT,
                                                **STUDIO_LOGIN})
    sup = world.start()
    site = sup._ctx.studio
    assert site._recall() is None
    site._remember(world.clock.now())
    assert get_state_time(world.store, "studio.last_sign_in") == world.clock.now()
    assert site._recall() == world.clock.now()


def test_a_studio_site_handed_in_is_used_with_no_login_and_no_board_item(make_world):
    world = make_world(at(10, 30, 12))
    site = object()
    sup = world.start(studio=site)
    assert sup._ctx.studio is site
    assert supervisor.NO_STUDIO_LOGIN not in sup.board().health.alarms


class NoLoginReads(MemorySecrets):
    """Credential Manager failing for the login alone, its error naming the secret."""

    def get(self, name: str) -> str | None:
        if name.startswith("studio_"):
            raise SecretsError(f"Windows Credential Manager could not read the {name} secret")
        return super().get(name)


def test_a_login_that_cant_be_read_is_a_board_item_and_not_a_crash(make_world):
    world = make_world(at(10, 30, 12))
    world.secrets = NoLoginReads({"signing_key": KEY_HEX, "export_key": EXPORT})
    sup = world.start()
    assert sup._ctx.studio is None
    assert supervisor.BAD_STUDIO_LOGIN in sup.board().health.alarms
    assert sup.board().tray.colour == "green"
    world.run(sup, minutes=2)
    assert len(world.pipeline.ticks) == 2 and "task_failed" not in world.dev_codes()


def test_every_new_module_is_in_the_self_test():
    from ngrunner.selftest import P1A_MODULES

    assert {"ngrunner.intake.pipeline", "ngrunner.intake.studio_designs", "ngrunner.interlock",
            "ngrunner.jobmodel", "ngrunner.supervisor", "ngrunner.messenger", "ngrunner.canary",
            "pythoncom", "win32com", "win32com.shell", "win32com.propsys",
            "win32cred"} <= set(P1A_MODULES)


def test_the_real_pipeline_messenger_and_job_model_fit_the_supervisor(make_world, monkeypatch):
    """No export key, so no client and no network: an empty control folder, ticking."""
    from ngrunner import messenger
    from ngrunner.intake import pipeline

    world = make_world(at(10, 30, 12), secrets={"signing_key": KEY_HEX})
    monkeypatch.setattr(supervisor, "_seams", lambda: SimpleNamespace(
        pipeline=pipeline, messenger=messenger, jobmodel=jobmodel))
    sup = world.start()
    world.run(sup, minutes=6)
    assert "task_failed" not in world.dev_codes()
    assert world.heartbeats()
    assert supervisor.NO_EXPORT_KEY in [item.text for item in sup.board().your_list]
    session, base = browse(sup)
    assert session.get(base + "/health", timeout=10).status_code == 200
    assert "pipeline" not in [item.code for item in list_dev(world.store)]


def test_the_supervisor_and_the_pipeline_name_the_same_state(make_world):
    from ngrunner.intake import pipeline
    from ngrunner.interlock import PAUSED

    assert supervisor.LAST_EXPORT == pipeline.FAB_EXPORTED_AT
    assert supervisor.PAUSED == PAUSED
    assert pipeline.STREAM == "fab_export"


def test_a_part_that_cant_start_is_a_board_item_and_not_a_green_tray(make_world, monkeypatch):
    world = make_world(at(10, 30, 12))

    def broken(*args: Any, **kw: Any) -> None:
        raise RuntimeError("a broken part")

    monkeypatch.setattr(FakeMessenger, "__init__", broken)
    sup = world.start()
    assert supervisor.NO_PARTS in [item.text for item in sup.board().your_list]
    assert sup.board().tray.colour == "red"
    world.run(sup, minutes=2)
    assert world.pipeline.ticks == []


# ----- a board or a tray that can't start


def _beats(world: World) -> list[Any]:
    return [open_envelope(path.read_bytes(), key=KEY, kind="heartbeat").payload
            for path in world.heartbeats()]


def test_a_board_that_cant_start_is_told_and_the_schedule_tries_again(make_world, monkeypatch):
    world = make_world(at(10, 30, 12))
    real, broken = supervisor.create_board, {"now": True}

    def create(*args: Any, **options: Any) -> Any:
        if broken["now"]:
            raise OSError("no port")
        return real(*args, **options)

    monkeypatch.setattr(supervisor, "create_board", create)
    sup = world.start()
    assert sup._board_url is None and "board_failed" in world.dev_codes()
    world.run(sup, minutes=6)
    assert templates.ALARMS["board_failed"] in _beats(world)[-1]["open_risks"]
    assert templates.ALARMS["board_failed"] in world.tray.tooltip
    broken["now"] = False
    world.run(sup, minutes=1)
    assert sup._board_url is not None and "board_failed" not in world.dev_codes()
    board_file = world.paths.app_data / single.BOARD_FILE
    assert sup._board_url in board_file.read_text(encoding="utf-8")


def test_a_tray_that_cant_start_is_told_and_the_schedule_tries_again(make_world, monkeypatch):
    world = make_world(at(10, 30, 12))
    real, broken = supervisor.Tray, {"now": True}

    def make(*args: Any, **options: Any) -> Any:
        if broken["now"]:
            raise OSError("no tray here")
        return real(*args, **options)

    monkeypatch.setattr(supervisor, "Tray", make)
    sup = world.start()
    assert sup._tray is None and "tray_failed" in world.dev_codes()
    world.run(sup, minutes=6)
    assert templates.ALARMS["tray_failed"] in _beats(world)[-1]["open_risks"]
    broken["now"] = False
    world.run(sup, minutes=1)
    assert sup._tray is not None and "tray_failed" not in world.dev_codes()


# ----- the switch of mode


def test_the_mode_switch_never_waits_on_a_pass_and_takes_effect_when_it_ends(make_world):
    world = make_world(at(10, 30, 12), mode="shadow")
    sup = world.start()
    held, release = threading.Event(), threading.Event()

    def a_long_pass() -> None:
        with sup._run_lock:
            held.set()
            release.wait(20)

    long_pass = threading.Thread(target=a_long_pass, daemon=True)
    long_pass.start()
    assert held.wait(5)
    answers: list[str] = []
    click = threading.Thread(target=lambda: answers.append(sup.set_mode("live")), daemon=True)
    click.start()
    click.join(5)
    try:
        assert answers and "live" in answers[0]
        assert load_settings(world.paths).mode == "live"
        assert sup.board().shadow is False
        assert sup._ctx.mode == "shadow"              # what the pass under way goes on with
        assert "already" in sup.set_mode("live")
    finally:
        release.set()
        long_pass.join(5)
    sup.run_due(world.clock.now())
    assert sup._ctx.mode == "live" and sup._messenger.settings.mode == "live"
    assert world.pipeline.ticks[-1].mode == "live"
    assert len([e for e in list_events(world.store) if e.kind == "mode_changed"]) == 1


def test_a_switch_and_a_switch_back_during_one_pass_end_where_they_were_told(make_world):
    world = make_world(at(10, 30, 12), mode="shadow")
    sup = world.start()
    sup._run_lock.acquire()
    done = threading.Event()

    def clicks() -> None:
        assert "live" in sup.set_mode("live")
        assert "shadow" in sup.set_mode("shadow")
        done.set()

    click = threading.Thread(target=clicks, daemon=True)
    click.start()
    try:
        assert done.wait(5)
    finally:
        sup._run_lock.release()
    click.join(5)
    sup.run_due(world.clock.now())
    assert sup._ctx.mode == "shadow" and load_settings(world.paths).mode == "shadow"


def test_the_switch_ticks_at_the_next_pass(make_world):
    world = make_world(at(10, 30, 12), mode="shadow")
    sup = world.start()
    world.run(sup, minutes=1)
    ticks = len(world.pipeline.ticks)
    sup.set_mode("live")
    sup.run_due(world.clock.now())
    assert len(world.pipeline.ticks) == ticks + 1 and world.pipeline.ticks[-1].mode == "live"
    assert world.pipeline.went_live == 1
    sup.set_mode("shadow")
    assert world.pipeline.went_live == 1


# ----- what closes, and what a note is


def test_a_note_is_on_health_and_not_on_your_list_so_the_board_agrees_with_itself(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    view = sup.board()
    assert view.your_list == () and view.tray.colour == "green"
    assert "nothing is waiting" in view.tray.tooltip
    assert {supervisor.NO_BACKUP_KEY, supervisor.NO_STUDIO_LOGIN} <= set(view.health.alarms)


def test_a_failed_task_item_closes_on_that_tasks_next_clean_pass(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    world.pipeline.fail = RuntimeError("it broke")
    world.run(sup, minutes=2)
    assert world.dev_codes().count("task_failed") == 1
    world.pipeline.fail = None
    world.run(sup, minutes=1)
    assert "task_failed" not in world.dev_codes()


def test_a_clean_pass_of_one_task_leaves_anothers_item_open(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    open_dev(world.store, at=world.clock.now(), code="task_failed",
             text="The night work failed (OSError).")
    sup._guarded("pipeline tick", lambda now: None, world.clock.now())
    assert "task_failed" in world.dev_codes()


def test_the_night_does_not_tell_jim_a_filed_job_waits(make_world):
    world = make_world(at(10, 30, 12))
    sup = world.start()
    ensure_job(world.store, "4242", JOB, now=world.clock.now())
    open_dev(world.store, at=world.clock.now() - timedelta(hours=49), code="layouts_differ",
             text="They differ.", studio_id="4242")
    set_state(world.store, "job.filed.4242", json.dumps({"where": "ng_jobs"}))
    sup._night_tell_jim(world.clock.now())
    assert list_dev(world.store)[0].told_jim_at is None
