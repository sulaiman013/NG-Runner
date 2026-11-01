r"""
The supervisor: NG Runner as a running app (integration spec section 6).

Why it exists: every other module does one thing when called. Something has to start them in the
right order, call them on time, and carry on when one of them fails, because nobody is watching
Jim's PC. The rules that shaped it:

- It must come up with nothing. install.ps1 starts NG Runner before its settings or secrets exist,
  and two failed starts retire the version (launcher.py). So a missing setting or secret, or a
  database it can't read, is a plain item on the board and a red tray, never an exception out of
  start() and never a crash loop.
- Nothing before record_up waits on a shared folder: a dead Google Drive must not read as a failed
  start. The first read of G: is the scheduler's.
- The schedule runs on the clock it is given. run_due(now) does what is due at now: the scheduler
  thread calls it every second, and a test calls it with a FakeClock's time, so a day takes seconds.
- Once-a-day work is keyed in the store, not in this process's memory (the digest by Pacific date,
  the night's work by its night key, step by step), so a restart neither repeats it nor loses it. A
  window the PC was off for is caught up at the next run_due, and named.
- One task failing never stops the others: it is logged and opens a DEV item.

The guards run beside the pipeline's tick and never inside it: a guard's failure is its own, and
their reads of G: are on a worker, whose result is taken up within a second of coming back. So do P3's
checks (checks/jobchecks.py): Jim's crosscheck runs on a worker of its own, one at a time, and what it
found is taken up within a second; Check now, a settle and a close prompt's answer are the board's.
P4's lock work (lock/work.py) runs the same way: its reads of Drive and Jim's scripts on a worker of
its own, and every write it makes, into NG Runner's own copies only until P4 goes live, on the
scheduler's thread. Every pause, from the tray, the board or a signed file, releases every lease.

Quit means quit until the next logon (launcher.record_quit), and pause means filing stops while
heartbeats, the digest, the guards and messages go on: the interlock decides that, so the pipeline keeps ticking
while paused and carries on where it stopped. A pause can come from the tray, the board or a signed
file in the control folder; a resume only from the PC.

The board's address carries its token, so it is never logged or printed: app-output.log and the
logs go into the support bundle. The modules built alongside this one are imported in _seams(), so a
test can hand in fakes for the pipeline, the messenger and the job model.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import threading
import webbrowser
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ngrunner import backup, config, digest, heartbeat, launcher, single, support, toasts, toolkit
from ngrunner.board import BoardServer, create_board, new_token
from ngrunner.board.views import (
    BoardView,
    EventView,
    HealthView,
    ListItem,
    PathCheck,
    TrayState,
    tray_tooltip,
)
from ngrunner.cards import Card, Command, Rejection, card_from_record, parse_command
from ngrunner.clock import (
    Clock,
    SystemClock,
    at_pacific,
    fmt_clock,
    fmt_date,
    pacific_day,
    parse_wall,
    utc,
)
from ngrunner.config import AppPaths, Settings, SettingsError
from ngrunner.guards.kept import folder_of_card, mozaik_said
from ngrunner.intake.studio_designs import StudioSite
from ngrunner.interlock import PAUSED, is_paused
from ngrunner.joblist import RenderClient
from ngrunner.messages import Message, templates
from ngrunner.messages.lint import redact
from ngrunner.secrets import CredentialSecrets, Secrets, SecretsError
from ngrunner.signing import EnvelopeError, key_from_text, open_envelope
from ngrunner.store import (
    LATEST,
    Store,
    StoreError,
    accept_envelope,
    answer_card,
    close_dev,
    get_card,
    get_fab_row,
    get_job,
    get_list_state,
    get_outbox,
    get_state,
    get_state_time,
    last_accepted_seq,
    list_cards,
    list_dev,
    list_events,
    list_fab_rows,
    list_jobs,
    list_outbox,
    list_pending,
    list_snapshots,
    list_write_records,
    log_event,
    mark_dev_told_jim,
    next_seq,
    open_dev,
    open_store,
    prune_renders,
    prune_seen_ids,
    prune_write_records,
    record_reminder,
    seen_id,
    set_state,
    set_state_time,
    settle_pending_writes,
)
from ngrunner.toasts import ToastMessage
from ngrunner.tray import Tray, TrayActions
from ngrunner.writes import FileWrite

_log = logging.getLogger(__name__)

# ----- the schedule

MINUTE = timedelta(minutes=1)
HEARTBEAT_EVERY = timedelta(minutes=5)
SETTLE_EVERY = timedelta(minutes=5)
NIGHT_FROM = time(1, 30)
LATE_AFTER = timedelta(minutes=10)    # a window missed by this much is named when it catches up
NIGHT_RETRY = timedelta(minutes=30)   # a night step that failed (Drive away) tries again this soon
HALF_SYNCED = timedelta(minutes=10)   # build-spec 3: a file still unreadable after this goes to DEV
TOLD_JIM_AFTER = timedelta(hours=48)  # a DEV item about a job this old is Jim's to know about
KEEP_RENDERS = timedelta(days=7)      # store.prune_renders: a week-old pair is no longer in use
KEEP_WRITES = timedelta(days=90)      # the writes rows; owned is never pruned
TOOLKIT_OVERRUN = timedelta(hours=1)  # a copy and its regression take seconds; past this it is stuck
_LOOP_WAIT_S = 1.0
_STOP_WAIT_S = 30.0

# ----- what it keeps in the state table

LAST_DIGEST_DAY = "last_digest_day"   # the Pacific date the digest last went
LAST_DIGEST_AT = "last_digest_at"
NIGHT_KEY = "night_key"               # the night whose work is done
NIGHT_STEPS = "night_steps"           # JSON {"key": ..., "done": [...]}: how far tonight has got
LAST_HEARTBEAT = "last_heartbeat"
STUDIO_SIGN_IN = "studio.last_sign_in"
LEFTOVER_TEMPS = "leftover_temps"     # JSON list of the *.ngr-tmp paths the night's scan found
TOAST_SETTING = "toast_setting"       # "on", "off" or "unknown"
TOASTED_CARDS = "cards_toasted"       # JSON list of the open cards already shown
MORNING_TOAST_DAY = "morning_toast_day"
LAST_EXPORT = "fab_exported_at"       # the pipeline's: exported_at of the last accepted export

# ----- plain words

TEMP_SUFFIX = ".ngr-tmp"              # writes.py's temp files (AGENTS.md: Before filing, item 7)
SETUP = "Run NGRunner.exe setup, then Quit NG Runner from the tray and start it again."
NO_SIGNING_KEY = ("NG Runner has no signing key, so it can't read the Fab Schedule or send"
                  f" messages. {SETUP}")
BAD_SIGNING_KEY = ("NG Runner's signing key can't be used, so it can't read the Fab Schedule or"
                   f" send messages. {SETUP}")
NO_EXPORT_KEY = f"NG Runner has no job list key, so it can't match new rows. {SETUP}"
BAD_EXPORT_KEY = f"NG Runner's job list key can't be used, so it can't match new rows. {SETUP}"
NO_BACKUP_KEY = "The nightly backup is off: NG Runner has no backup key. Tell Sulaiman."
STUDIO_LOGIN = ("studio_username", "studio_password")  # the secrets Ctx.studio is built from
NO_STUDIO_LOGIN = f"Designs are not cross-checked: NG Runner has no Studio login. {SETUP}"
BAD_STUDIO_LOGIN = ("Designs are not cross-checked: NG Runner can't read its Studio login. Tell"
                    " Sulaiman.")
NO_STORE = ("NG Runner can't read its database, so it isn't working. Tell Sulaiman; the runbook"
            " says how to restore it.")
CANT_DO_THAT = "That can't be done right now: NG Runner can't read its database. Tell Sulaiman."
NOT_SET_UP = "NG Runner has no settings yet, so that can't be done. Run NGRunner.exe setup first."
NO_PARTS = "NG Runner could not start its work, so nothing is being filed. Tell Sulaiman."
NOT_OPEN = "That question is no longer open, so nothing changed."
NOT_A_CHOICE = "That isn't one of the question's answers, so nothing changed."
CHECKING = "Checking the Fab Schedule now."
NO_TRACKER_FOLDER = ("The lock pre-flight is off: NG Runner has no tracker folder. Tell Sulaiman.")
_THE_GUARDS_SAY = ("DO NOT CUT", "NOT CHECKED")  # a red that keeps its own words over a setup line
_BOARD_TOKEN = re.compile(r"([?&]t=)[A-Za-z0-9_-]+")


def _seams() -> SimpleNamespace:
    """The modules built alongside this one, imported here so a test can hand in fakes. A build
    that lacks one fails its self-test first (selftest.P1A_MODULES), and then fails to start."""
    from ngrunner import jobmodel, messenger
    from ngrunner.guards import watch
    from ngrunner.intake import pipeline

    return SimpleNamespace(pipeline=pipeline, messenger=messenger, jobmodel=jobmodel, guards=watch)


def _mozaik_words(looked: bool, running: bool | None) -> str:
    """What the observer last found, for Health. Nothing gates on it in P1b (build spec 5.12)."""
    if not looked:
        return ""
    return "Could not tell" if running is None else ("Running" if running else "Not running")


def night_key(now: datetime) -> date:
    """The night a moment belongs to: the Pacific date whose 1:30 AM has most recently passed. A
    date, so the hour the autumn change repeats is still one night (at_pacific takes the first)."""
    day = pacific_day(now)
    return day if utc(now) >= at_pacific(day, NIGHT_FROM) else day - timedelta(days=1)


def _try_toolkit(source: Path, folder: Path, adopted: list[str], clock: Clock) -> toolkit.Adoption:
    """On the toolkit's worker: whatever goes wrong comes back as a fact, never as a dead thread.
    adopted: the shas adopted, newest first; the newest is what the copy is compared with, and the
    two newest are kept, as a pass of the guards may still be running on the older."""
    try:
        return toolkit.try_adoption(source, folder, adopted=adopted[0] if adopted else None,
                                    keep=adopted[:2], clock=clock)
    except Exception as exc:  # noqa: BLE001 - on a worker, the failure must reach the DEV lane
        _log.exception("trying a copy of Jim's toolkit failed")
        return toolkit.Adoption(toolkit.NOT_COPIED, f"Trying a copy of Jim's toolkit failed ({type(exc).__name__}).")


def scan_leftover_temps(folders: Iterable[Path], *, limit: int = 200) -> list[str]:
    """Every *.ngr-tmp file under folders, read-only: commit_write never deletes one it did not
    make in the same commit, so a crash leaves them for Sulaiman to see on the board (AGENTS.md:
    Before filing goes live, item 7). A folder it can't read is skipped; os.walk follows no link."""
    found: list[str] = []
    for folder in folders:
        for here, _folders, files in os.walk(folder):
            found += [os.path.join(here, name) for name in files if name.endswith(TEMP_SUFFIX)]
            if len(found) >= limit:
                return found[:limit]
    return found


# ----- logging: one rotating file; never a secret, the board's address or a message body


class _Masked(logging.Formatter):
    """A last guard for the rule above: anything carrying the board's token loses it."""

    def format(self, record: logging.LogRecord) -> str:
        return _BOARD_TOKEN.sub(r"\1[hidden]", super().format(record))


def _start_logging(paths: AppPaths) -> logging.Handler | None:
    """One rotating file in AppPaths.logs for the ngrunner.* and waitress loggers; None when it
    can't be made, as NG Runner runs on without a log."""
    try:
        paths.logs.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            paths.logs / "ngrunner.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(_Masked("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger("ngrunner").setLevel(logging.INFO)
    for name in ("ngrunner", "waitress"):
        logging.getLogger(name).addHandler(handler)
    return handler


def _stop_logging(handler: logging.Handler | None) -> None:
    if handler is not None:
        for name in ("ngrunner", "waitress"):
            logging.getLogger(name).removeHandler(handler)
        handler.close()


# ----- the supervisor


@dataclass
class _Task:
    """One line of the schedule: what to do, and how often. A clock set back makes it due."""

    name: str
    every: timedelta
    action: Callable[[datetime], None]
    last: datetime | None = None

    def due(self, now: datetime) -> bool:
        return self.last is None or now < self.last or now - self.last >= self.every


class Supervisor:
    """NG Runner while it runs. start() brings it up; run_due(now) does what is due; stop() ends
    it. It is also the board's BoardViews and BoardActions (board/views.py).

    start() starts no thread of its own, so a test drives run_due by a fake clock;
    start_scheduler() is the thread that does it in real life (run() below). settings,
    render_client and studio are for tests: everywhere else settings come from settings.json, the
    client from the setting and the key, and the Studio site from the saved login."""

    def __init__(
        self,
        paths: AppPaths,
        *,
        clock: Clock | None = None,
        secrets: Secrets | None = None,
        http: Any = None,
        tray_backend: Any = None,
        toaster: Any = None,
        attempt: str | None = None,
        settings: Settings | None = None,
        render_client: Any = None,
        studio: Any = None,
        confirm: Callable[[str], bool] | None = None,
        browser: Callable[[str], object] = webbrowser.open,
        opener: Callable[[str], None] | None = None,
        toolkit_runs: Any = None,
        check_runs: Any = None,
        lock_work: Any = None,
    ) -> None:
        self._paths = paths
        self._toolkit_runs = toolkit_runs      # where the night's toolkit copy is tried (a FetchRunner)
        self._check_runs = check_runs          # where P3's checks run (a FetchRunner), one at a time
        self._lock_work = lock_work            # P4 at work (lock.work.LockWork), with a worker of its own
        self._clock = clock or SystemClock()
        self._secrets = secrets if secrets is not None else CredentialSecrets()
        self._http = http
        self._tray_backend = tray_backend
        self._toaster = toaster or toasts.Toaster(clock=self._clock, on_choice=self.toast_choice)
        self._attempt = attempt
        self._settings = settings
        self._given_settings = settings is not None
        self._given_client = render_client
        self._given_studio = studio
        self._studio: Any = studio
        self._confirm = confirm
        self._browser = browser
        self._opener = opener
        self._seams = _seams()
        self._store: Store | None = None
        self._store_problem: str | None = None
        self._settings_problem: str | None = None
        self._ctx: Any = None
        self._messenger: Any = None
        self._server: BoardServer | None = None
        self._tray: Tray | None = None
        self._board_url: str | None = None
        self._handler: logging.Handler | None = None
        self._thread: threading.Thread | None = None
        self._signing: bytes | None = None
        self._export_text: str | None = None
        self._export_problem: str | None = None
        self._blockers: tuple[str, ...] = ()
        self._notes: tuple[str, ...] = ()
        self._last_setup: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        self._path_checks: tuple[PathCheck, ...] = ()
        self._toast_setting: bool | None = None
        self._last_tray: tuple[TrayState, bool] | None = None
        self._night_retry_at: datetime | None = None
        self._pause_read: set[str] = set()
        self._pause_bad_since: dict[str, datetime] = {}
        self._run_lock = threading.RLock()
        self._stop = threading.Event()
        self._stopped = False
        self._stop_requested = threading.Event()
        self._asked: set[str] = set()
        self._asked_lock = threading.Lock()
        self._mode_wanted: Settings | None = None
        self._tasks = (
            _Task("database check", MINUTE, self._check_store),
            _Task("setup check", MINUTE, self._check_setup),
            _Task("pause files", MINUTE, self._read_pause_files),
            _Task("guards", MINUTE, self._guards),
            _Task("checks", MINUTE, self._checks),
            _Task("lock work", MINUTE, self._lock),
            _Task("pipeline tick", MINUTE, self._tick),
            _Task("receipts check", MINUTE, self._check_receipts),
            _Task("heartbeat", HEARTBEAT_EVERY, self._heartbeat),
            _Task("write settling", SETTLE_EVERY, self._settle),
            _Task("night work", MINUTE, self._night),
            _Task("toolkit copy", MINUTE, self._take_toolkit),
            _Task("digest", MINUTE, self._digest),
            _Task("toasts", MINUTE, self._show_toasts),
            _Task("health check", MINUTE, self._refresh_health),
            _Task("starts", MINUTE, self._retry_starts),
        )
        self._night_steps: tuple[tuple[str, Callable[[datetime], bool]], ...] = (
            ("backup", self._night_backup),
            ("log", self._night_log),
            ("toast_setting", self._night_toast_setting),
            ("prune_backups", self._night_prune_backups),
            ("prune_renders", self._night_prune_renders),
            ("prune_rows", self._night_prune_rows),
            ("scan_temps", self._night_scan_temps),
            ("ran", self._night_ran),
            ("dev_lines", self._night_dev_lines),
            ("tell_jim", self._night_tell_jim),
            ("toolkit", self._night_toolkit),
        )

    # ----- starting and stopping

    def start(self) -> None:
        """Bring NG Runner up, in the order of integration spec section 6. Whatever it lacks
        becomes an item on the board; nothing here waits on a shared folder before record_up."""
        now = self._clock.now()
        self._handler = _start_logging(self._paths)
        self._load_settings()
        self._open_store(now)
        self._guarded("start-up", lambda _now: self._build_parts(), now)
        self._guarded("setup check", self._refresh_setup, now)
        self._guarded("start-up", self._mark_first_start, now)
        self._start_board(now)
        self._start_tray(now)
        self._announce_up()
        self._guarded("launcher notes", self._take_launcher_notes, now)
        self._guarded("write settling", self._settle, now)

    def start_scheduler(self) -> None:
        """The thread that calls run_due every second until stop(). Missed windows catch up at
        its first call, once everything above is done."""
        self._thread = threading.Thread(target=self._loop, name="ngrunner-scheduler", daemon=True)
        self._thread.start()

    def request_stop(self) -> None:
        """Ask for a clean stop: run() sees it and calls stop() on its own thread."""
        self._stop_requested.set()

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested.is_set()

    def stop(self) -> None:
        """Stop the scheduler between steps, then the board, then the tray, then withdraw the
        board's address. Nothing is left half-written: commit_write is all-or-nothing, and the
        store is transactional. Safe to call twice."""
        if self._stopped:
            return
        self._stopped = True
        self._stop.set()
        if self._thread is not None:
            self._thread.join(_STOP_WAIT_S)
        if self._ctx is not None:
            self._quietly("the pipeline's stop", lambda: self._seams.pipeline.stop_work(self._ctx))
        if self._toolkit_runs is not None:      # a try under way dies with the process, and its job
            self._quietly("the toolkit copy's stop", self._toolkit_runs.stop)
        if self._check_runs is not None:        # a check under way dies with the process; it is asked again
            self._quietly("the checks' stop", self._check_runs.stop)
        if self._lock_work is not None:         # a read under way dies with the process; its click waits
            self._quietly("the lock work's stop", self._lock_work.runner.stop)
        if self._server is not None:
            self._quietly("the board's stop", self._server.stop)
        if self._tray is not None:
            self._quietly("the tray's stop", self._tray.stop)
        self._quietly("withdrawing the board", lambda: single.withdraw_board(self._paths))
        _stop_logging(self._handler)
        self._handler = None

    def _loop(self) -> None:
        while not self._stop.wait(_LOOP_WAIT_S):
            try:
                self.run_due(self._clock.now())
            except Exception:  # noqa: BLE001 - nothing may end the scheduler; logged
                _log.exception("a pass of the schedule failed")

    def _load_settings(self) -> None:
        if self._given_settings:
            return
        try:
            self._settings = config.load_settings(self._paths)
        except SettingsError as exc:
            self._settings, self._settings_problem = None, f"{exc}."

    def _open_store(self, now: datetime) -> None:
        """The database, copied first when this version is about to upgrade it: open_store
        migrates at once, and an older version then refuses the file. One it can't read shows red,
        and is tried again every minute."""
        try:
            self._paths.app_data.mkdir(parents=True, exist_ok=True)
            self._copy_before_upgrade(now)
            self._store, self._store_problem = open_store(self._paths.db), None
        except (StoreError, OSError) as exc:
            _log.error("the database can't be used: %s", exc)
            self._store, self._store_problem = None, NO_STORE

    def _copy_before_upgrade(self, now: datetime) -> None:
        """Into a folder named as commit_write names its copies: backup.prune keeps it 60 days."""
        store = Store(self._paths.db)
        version = store.schema_version()
        if 0 < version < LATEST:
            folder = self._paths.backup / f"{utc(now):%Y%m%dT%H%M%S%f}Z"
            folder.mkdir(parents=True, exist_ok=True)
            store.backup_to(folder / f"ngrunner-v{version}.db")

    def _build_parts(self) -> None:
        """The messenger and the pipeline's context, once there are settings and a database."""
        self._ctx = self._messenger = self._export_text = None
        if self._store is None or self._settings is None:
            return
        roots = config.roots(self._settings, self._paths)
        self._messenger = self._seams.messenger.Messenger(
            self._store, self._settings, self._secrets, self._clock, roots, self._paths,
            http=self._http, toaster=self._toaster)
        self._ctx = self._seams.pipeline.Ctx(
            store=self._store, settings=self._settings, secrets=self._secrets, clock=self._clock,
            roots=roots, paths=self._paths, studio_http=self._http,
            render_client=self._given_client, messenger=self._messenger,
            mode=self._settings.mode, stop_requested=self._stop.is_set,
            studio=None)  # _read_studio_login fills it in, at once and every minute

    def _mark_first_start(self, now: datetime) -> None:
        """Nothing was missed before NG Runner existed, so a new install owes no digest and no
        night's work: its windows start from now."""
        if self._store is None or self._settings is None:
            return
        day = pacific_day(now)
        if get_state(self._store, LAST_DIGEST_DAY) is None:
            due = at_pacific(day, parse_wall(self._settings.digest_at))
            done = day if utc(now) >= due else day - timedelta(days=1)
            set_state(self._store, LAST_DIGEST_DAY, done.isoformat())
        if get_state(self._store, NIGHT_KEY) is None:
            set_state(self._store, NIGHT_KEY, night_key(now).isoformat())

    def _start_board(self, now: datetime) -> None:
        """One that can't start tells Sulaiman, the heartbeat and the digest (an alarm while its
        DEV item stands), since with no board Jim can't answer a card. The schedule tries again."""
        try:
            app = create_board(self, self, token=new_token(), roots=self._open_roots(),
                               clock=self._clock, opener=self._opener)
            self._server = BoardServer(app)
            self._board_url = self._server.start()
        except Exception:  # noqa: BLE001 - logged and told; NG Runner still files without its board
            _log.exception("the board could not start")
            self._server = None
            self._dev(now, "board_failed", templates.ALARMS["board_failed"])

    def _open_roots(self) -> tuple[Path, ...]:
        """What the board's Open may open. control\\shadow in either mode: a switch on the board
        can't rebuild the app that is serving the click."""
        if self._settings is None:
            return ()
        return (self._settings.ng_jobs, self._paths.renders, self._settings.control / "shadow")

    def _start_tray(self, now: datetime) -> None:
        actions = TrayActions(
            open_board=self.open_board, pause=self.pause, resume=self.resume,
            check_now=self.check_now, send_support_bundle=self.ask_support_bundle,
            quit=self.quit)
        try:
            self._tray = Tray(actions, self._tray_state(now), paused=self._is_paused(),
                              confirm=self._confirm, backend=self._tray_backend)
            self._tray.start()
        except Exception:  # noqa: BLE001 - logged and told; NG Runner still files without its icon
            _log.exception("the tray could not start")
            self._tray = None
            self._dev(now, "tray_failed", templates.ALARMS["tray_failed"])

    def _retry_starts(self, now: datetime) -> None:
        """What could not start at start-up. No board and no tray leave Jim no sign at all, while
        heartbeats go on, so a watchdog has nothing to report."""
        if self._server is None:
            self._start_board(now)
            if self._server is not None:
                self._quietly("publishing the board",
                              lambda: single.publish_board(self._paths, self._board_url))
        if self._tray is None:
            self._start_tray(now)
            self._last_tray = None
        running = {"board_failed": self._server, "tray_failed": self._tray}
        for item in list_dev(self._store) if self._store is not None else ():
            if running.get(item.code) is not None:
                close_dev(self._store, item.id)

    def _announce_up(self) -> None:
        """The board's address where a second start finds it, then the launcher's "this worked"."""
        if self._board_url is not None:
            self._quietly("publishing the board",
                          lambda: single.publish_board(self._paths, self._board_url))
        if self._attempt is not None:
            launcher.record_up(self._paths, self._attempt, clock=self._clock)

    def _take_launcher_notes(self, now: datetime) -> None:
        if self._store is None:
            return
        notes = launcher.pending_notes(self._paths)
        for note in notes:
            open_dev(self._store, at=note.at, code="launcher", text=note.text)
        if notes:
            launcher.ack_notes(self._paths, [note.id for note in notes])
        aside = self._settings.set_aside_note() if self._settings is not None else ""
        if aside:
            self._dev(now, "settings_set_aside", aside)

    # ----- the schedule

    def run_due(self, now: datetime) -> None:
        """Do every task that is due at now, each on its own, in the schedule's order: the pause
        files before the tick, the night's work before the digest."""
        now = utc(now)
        with self._run_lock:
            ran = self._do_what_was_asked(now)
            for task in self._tasks:
                if self._stop.is_set():
                    return
                if task.due(now):
                    task.last = now
                    ran = True
                    self._guarded(task.name, task.action, now)
            if ran:  # not every second: the job model reads the store for it
                self._guarded("tray update", self._update_tray, now)

    def _do_what_was_asked(self, now: datetime) -> bool:
        """What the tray and the board asked for since the last pass: their threads must return at
        once."""
        self._take_mode()
        with self._asked_lock:
            asked, self._asked = self._asked, set()
        if "check" in asked:
            next(task for task in self._tasks if task.name == "pipeline tick").last = None
        if "checks" in asked:
            next(task for task in self._tasks if task.name == "checks").last = None
        if "lock" in asked:
            next(task for task in self._tasks if task.name == "lock work").last = None
        self._take_checks(now)
        self._take_lock(now)
        if "support" in asked:
            self._guarded("support bundle", self._send_support_bundle, now)
        return self._take_guards(now) or bool(asked)

    def _take_guards(self, now: datetime) -> bool:
        """What the guards' read found, within a second of its coming back. True when there was
        something, so the tray looks. Looked at every second, so not _guarded, which reads the DEV
        lane each time; the guards' next clean pass closes what this opens."""
        if self._ctx is None or self._store_problem:
            return False
        try:
            return bool(self._seams.guards.take_finished(self._ctx))
        except StoreError as exc:
            _log.error("the guards could not use the database: %s", exc)
            self._store_problem = NO_STORE
        except Exception as exc:  # noqa: BLE001 - a guard's failure must never stop the schedule
            _log.exception("the guards failed")
            self._dev(now, "task_failed", f"The guards failed ({type(exc).__name__}).")
        return False

    def _guarded(self, name: str, action: Callable[[datetime], object], now: datetime) -> None:
        try:
            action(now)
            self._task_passed(name)
        except StoreError as exc:
            _log.error("the %s could not use the database: %s", name, exc)
            self._store_problem = NO_STORE
        except Exception as exc:  # noqa: BLE001 - one task's failure must never stop the others
            _log.exception("the %s failed", name)
            self._dev(now, "task_failed", f"The {name} failed ({type(exc).__name__}).")

    def _close_dev(self, code: str, text: str = "") -> None:
        """What stopped being wrong: the DEV lane holds what is wrong now, and the nightly line
        doesn't repeat what has cleared."""
        for item in list_dev(self._store):
            if item.code == code and item.text.startswith(text):
                close_dev(self._store, item.id)

    def _task_passed(self, name: str) -> None:
        """A failed task's DEV item goes at its next clean pass, so the DEV lane holds what is wrong
        now and the nightly line doesn't repeat what has cleared."""
        if self._store is not None and not self._store_problem:
            for item in list_dev(self._store):
                if item.code == "task_failed" and item.text.startswith(f"The {name} failed"):
                    close_dev(self._store, item.id)

    @staticmethod
    def _quietly(what: str, action: Callable[[], object]) -> None:
        try:
            action()
        except Exception:  # noqa: BLE001 - shutting down or starting up: logged, and on it goes
            _log.exception("%s failed", what)

    def _ready(self) -> bool:
        """Whether NG Runner can read and send signed files at all."""
        return self._ctx is not None and self._signing is not None and not self._store_problem

    def _dev(self, now: datetime, code: str, text: str, *, studio_id: str | None = None,
             urgent: bool = False) -> None:
        if self._store is None:
            _log.warning("no database for a DEV item (%s)", code)
            return
        try:
            open_dev(self._store, at=now, code=code, text=text, studio_id=studio_id, urgent=urgent)
        except (StoreError, ValueError):
            _log.exception("a DEV item could not be opened (%s)", code)

    def _check_store(self, now: datetime) -> None:
        if self._store is None:
            self._open_store(now)
            if self._store is not None:
                self._build_parts()
                self._mark_first_start(now)
                self._take_launcher_notes(now)
            return
        try:
            self._store.schema_version()
            self._store_problem = None
        except StoreError as exc:
            _log.error("the database can't be read: %s", exc)
            self._store_problem = NO_STORE

    def _check_setup(self, now: datetime) -> None:
        self._refresh_setup(now)

    def _refresh_setup(self, now: datetime) -> None:
        """What NG Runner lacks: blockers keep it from its work (a red tray), notes don't. Read
        again every minute, so a key put in place starts working with no restart."""
        blockers: list[str] = [self._store_problem] if self._store_problem else []
        notes: list[str] = []
        if self._settings is None:
            blockers.append(self._settings_problem or NOT_SET_UP)
        else:
            if self._store is not None and self._ctx is None:
                blockers.append(NO_PARTS)  # its log says why
            blockers += self._read_keys()
            if not self._settings.backup_public_key:
                notes.append(NO_BACKUP_KEY)
            notes += self._read_studio_login()
        found = (tuple(blockers), tuple(notes))
        if found != self._last_setup:
            self._last_setup = found
            self._blockers, self._notes = found
            self._note_setup_in_dev(now)

    def _note_setup_in_dev(self, now: datetime) -> None:
        """One DEV item per thing missing, closed when it is put right, so the nightly lines to
        Sulaiman say only what still stands."""
        if self._store is None or self._store_problem:
            return
        standing = {*self._blockers, *self._notes}
        for item in list_dev(self._store):
            if item.code == "setup" and item.text not in standing:
                close_dev(self._store, item.id)
        for text in standing:
            self._dev(now, "setup", text)

    def _read_keys(self) -> list[str]:
        try:
            signing, export = self._secrets.get("signing_key"), self._secrets.get("export_key")
        except SecretsError as exc:
            self._signing = None
            return [f"{exc}. Tell Sulaiman."]
        problems = []
        self._signing = None
        if not signing:
            problems.append(NO_SIGNING_KEY)
        else:
            try:
                self._signing = key_from_text(signing)
            except EnvelopeError:
                problems.append(BAD_SIGNING_KEY)
        if self._given_client is None:
            self._refresh_render_client(export)
            if not export:
                problems.append(NO_EXPORT_KEY)
            elif self._export_problem:
                problems.append(self._export_problem)
        return problems

    def _refresh_render_client(self, export: str | None) -> None:
        """A new client only when the key changed: a requests.Session is worth keeping."""
        if export == self._export_text or self._ctx is None:
            return
        self._export_text, self._export_problem = export, None
        client = None
        if export:
            try:
                # A clock handed in is a test's, and a fake server's Date header is real time.
                skew = {} if isinstance(self._clock, SystemClock) else {"max_clock_skew": None}
                client = RenderClient(self._settings.render_url, export, http=self._http,
                                      clock=self._clock, **skew)
            except ValueError:
                self._export_problem = BAD_EXPORT_KEY
        self._ctx = replace(self._ctx, render_client=client)

    def _read_studio_login(self) -> list[str]:
        """Ctx.studio: the Studio site while its login is saved, otherwise None, which files each
        report marked "designs not cross-checked". So it is a board item and never a blocker, and a
        login put in place starts working with no restart. One StudioSite serves the whole run, as
        it remembers when it last signed in, so a wrong password can't lock the account."""
        problem = None
        if self._given_studio is None:
            try:
                if not all(self._secrets.get(name) for name in STUDIO_LOGIN):
                    problem = NO_STUDIO_LOGIN
            except SecretsError:
                problem = BAD_STUDIO_LOGIN
        if self._studio is None and problem is None:
            self._studio = StudioSite(self._secrets, self._clock, http=self._http,
                                      recall=self._last_studio_sign_in,
                                      remember=self._note_studio_sign_in)
        studio = None if problem else self._studio
        if self._ctx is not None and self._ctx.studio is not studio:
            self._ctx = replace(self._ctx, studio=studio)
        return [problem] if problem else []

    def _last_studio_sign_in(self) -> datetime | None:
        """Across a restart, so a crash loop can't retry a wrong password. An unreadable store
        counts as never: this process still remembers its own."""
        try:
            return get_state_time(self._store, STUDIO_SIGN_IN) if self._store is not None else None
        except StoreError:
            return None

    def _note_studio_sign_in(self, when: datetime) -> None:
        self._quietly("noting a Studio sign-in", lambda: set_state_time(self._store, STUDIO_SIGN_IN, when))

    # ----- the minute's work

    def _tick(self, now: datetime) -> None:
        if self._ready():
            self._seams.pipeline.tick(self._ctx)

    def _guards(self, now: datetime) -> None:
        """Beside the pipeline and never inside it: a guard's failure is its own, and a guard only
        reads, so it runs through a pause and with no signing key. Telling people waits for one."""
        if self._ctx is not None and not self._store_problem:
            self._seams.guards.tick(self._ctx)

    def _checks(self, now: datetime) -> None:
        """P3's checks: what the worker found, closes and newly built jobs, and the next run. Like the
        guards, they only read, so they run through a pause and with no signing key."""
        if self._ctx is not None and not self._store_problem:
            from ngrunner.checks import jobchecks

            jobchecks.tick(self._ctx, self._check_work(), toast=self._toast)

    def _check_work(self) -> Any:
        if self._check_runs is None:
            from ngrunner.checks.jobchecks import CHECK_OVERRUN
            from ngrunner.intake.pipeline import BackgroundFetches

            self._check_runs = BackgroundFetches(1, overrun=CHECK_OVERRUN, name="ngrunner-checks")
        return self._check_runs

    def _take_checks(self, now: datetime) -> bool:
        """What a check found, within a second of its coming back, on this thread."""
        if self._ctx is None or self._store_problem or self._check_runs is None:
            return False
        try:
            from ngrunner.checks import jobchecks

            return jobchecks.take_finished(self._ctx, self._check_runs, toast=self._toast)
        except StoreError as exc:
            _log.error("the checks could not use the database: %s", exc)
            self._store_problem = NO_STORE
        except Exception as exc:  # noqa: BLE001 - a check's failure must never stop the schedule
            _log.exception("the checks failed")
            self._dev(now, "task_failed", f"The checks failed ({type(exc).__name__}).")
        return False

    def _lock(self, now: datetime) -> None:
        """P4 at work: Jim's lock clicks, the batch after them, fixes, Undo and leases. Its writes go only
        to NG Runner's own copies until P4 goes live, and the P4 interlock holds each one."""
        if self._ctx is not None and not self._store_problem:
            self._lock_works().tick(self._ctx)

    def _lock_works(self) -> Any:
        if self._lock_work is None:
            from ngrunner.intake.pipeline import BackgroundFetches
            from ngrunner.lock import work

            self._lock_work = work.LockWork(BackgroundFetches(1, overrun=work.OVERRUN, name=work.WORKER))
        return self._lock_work

    def _take_lock(self, now: datetime) -> bool:
        """What the lock work's worker found, within a second of its coming back, on this thread."""
        if self._ctx is None or self._store_problem or self._lock_work is None:
            return False
        try:
            return self._lock_work.take_finished(self._ctx)
        except StoreError as exc:
            _log.error("the lock work could not use the database: %s", exc)
            self._store_problem = NO_STORE
        except Exception as exc:  # noqa: BLE001 - P4's failure must never stop the schedule
            _log.exception("the lock work failed")
            self._dev(now, "task_failed", f"The lock work failed ({type(exc).__name__}).")
        return False

    def _release_leases(self, now: datetime) -> None:
        """Pause hands every job back to Jim's chat: each lease NG Runner holds is released. One that
        can't be says why on the board and to Sulaiman, and the rest are released all the same."""
        if self._ctx is None or self._store is None or self._store_problem:
            return
        from ngrunner.lock import work

        try:
            results = work.release_leases(self._store, self._paths, self._ctx.roots,
                                          self._settings.tracker_folder, self._clock)
        except Exception:  # noqa: BLE001 - a pause must pause, whatever the leases do; logged
            _log.exception("releasing the leases failed")
            self._dev(now, "lease_not_released", "The leases could not be released at a pause; they lapse"
                                                 " by themselves within 2 and a half hours.")
            return
        for result in results:
            if not result.ok:
                self._dev(now, "lease_not_released", f"{result.folder}: its lease was not released at a pause:"
                                                     f" {result.reason or 'no reason given'}")

    def _check_receipts(self, now: datetime) -> None:
        if self._ready():
            self._messenger.check_receipts()

    def _settle(self, now: datetime) -> None:
        """A path unreachable at start-up keeps its row pending, so this comes round again."""
        if self._store is not None and not self._store_problem:
            settle_pending_writes(self._store, at=now)

    def _heartbeat(self, now: datetime) -> None:
        if not self._ready():
            return
        store = self._store
        state = get_list_state(store)
        beat = heartbeat.payload(
            app_version=launcher.app_version(), paused=self._is_paused(),
            open_risks=self._open_risks(),
            last_fab_seq=last_accepted_seq(store, "fab_export") or None,
            list_synced_at=None if state is None else state.synced_at,
            pending=len(list_pending(store)), dev_open=len(list_dev(store)))
        seq = next_seq(store, heartbeat.KIND, start=int(now.timestamp()))
        parts, data = heartbeat.heartbeat_file(beat, key=self._signing, seq=seq, now=now)
        result = self._ctx.commit([FileWrite(self._settings.control.joinpath(*parts), data, None)])
        if result.ok:
            set_state_time(store, LAST_HEARTBEAT, now)
            self._close_dev("heartbeat_failed")
        else:
            _log.warning("a heartbeat was not written: %s", result.reason)
            self._dev(now, "heartbeat_failed", "Heartbeats are not reaching the control folder.")

    def _open_risks(self) -> tuple[str, ...]:
        """What the digest would put first: the watchdog repeats these if no digest goes."""
        try:
            return tuple(self._seams.jobmodel.digest_view(
                self._store, self._settings, self._clock).owner)
        except Exception:  # noqa: BLE001 - a heartbeat says it is alive, whatever else is wrong
            _log.exception("the open risks could not be read for a heartbeat")
            return ()

    def _read_pause_files(self, now: datetime) -> None:
        """Signed files Sulaiman drops in control\\pause: each one is acted on once, by its seq and
        id, so one left in the folder does not pause NG Runner again after a resume."""
        if not self._ready():
            return
        folder = self._settings.control / "pause"
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return  # none yet, or Drive is away: the next minute looks again
        for name in names:
            if name.endswith(".json") and name not in self._pause_read:
                self._read_pause_file(folder / name, now)

    def _read_pause_file(self, path: Path, now: datetime) -> None:
        try:
            envelope = open_envelope(path.read_bytes(), key=self._signing, kind="pause")
        except OSError:
            return
        except EnvelopeError as exc:
            first = self._pause_bad_since.setdefault(path.name, now)
            if now - first >= HALF_SYNCED:  # half-synced until then
                self._dev(now, "pause_file_bad", f"A pause file can't be read: {exc}.")
            return
        self._pause_read.add(path.name)
        self._pause_bad_since.pop(path.name, None)
        if accept_envelope(self._store, "pause", seq=envelope.seq, id=envelope.id):
            reason = " ".join(str(envelope.payload.get("reason", "")).split())[:200]
            self._set_paused(True, now, f"Paused from the control folder. {reason}".strip())
        elif seen_id(self._store, "pause", envelope.id):  # new to it, but its number is too low
            self._dev(now, "pause_file_old", "A pause file with an old number was ignored.")

    def _show_toasts(self, now: datetime) -> None:
        if self._messenger is None or self._store_problem:
            return
        self._toast_new_cards()
        self._toast_morning_list(now)

    def _toast_new_cards(self) -> None:
        """Each open card once: the messenger shows the kinds that hold a job and leaves the rest
        for the digest. Shadow mode shows none, but keeps count, so a switch to live doesn't bring
        them all at once."""
        cards = list_cards(self._store, state="open")
        shown = set(json.loads(get_state(self._store, TOASTED_CARDS, "[]") or "[]"))
        for record in cards:
            card = None if record.id in shown else self._card_or_none(record)
            if card is not None:
                self._messenger.toast_card(card)
        set_state(self._store, TOASTED_CARDS, json.dumps(sorted(card.id for card in cards)))

    def _toast_morning_list(self, now: datetime) -> None:
        """With the digest, or at the first start after it. Held back by the rate rule, it tries
        again next minute."""
        day = pacific_day(now).isoformat()
        store = self._store
        if (self._is_shadow() or get_state(store, LAST_DIGEST_DAY) != day
                or get_state(store, MORNING_TOAST_DAY) == day):
            return
        sent = [row for row in list_outbox(store) if row.ref == f"digest:{day}"]
        if sent and self._messenger.toast(
                Message("morning_list", sent[-1].subject, sent[-1].lines[:3])) == "shown":
            set_state(store, MORNING_TOAST_DAY, day)

    def _toast(self, message: ToastMessage) -> None:
        """A short answer to something Jim just did. None in shadow mode, like every toast."""
        if not self._is_shadow():
            self._toaster.notify(message)

    def _refresh_health(self, now: datetime) -> None:
        """Read here, on the scheduler's thread, so a page never waits on a shared folder."""
        self._toast_setting = toasts.toasts_enabled()
        if self._settings is not None:
            self._path_checks = tuple(
                _path_check(label, path) for label, path in (
                    ("NG Jobs", self._settings.ng_jobs),
                    ("Control folder", self._settings.control),
                    ("Mozaik Shared", self._settings.mozaik_shared),
                    ("Tracker snapshots", self._settings.tracker_folder)) if path is not None)

    # ----- the digest

    def _digest(self, now: datetime) -> None:
        """Once per Pacific date, from settings.digest_at. Caught up late, it says so. A crash
        after it was queued finds it in the outbox, so it never goes twice."""
        if not self._ready():
            return
        store, day = self._store, pacific_day(now)
        due = at_pacific(day, parse_wall(self._settings.digest_at))
        if now < due or get_state(store, LAST_DIGEST_DAY) == day.isoformat():
            return
        if not self._sent(f"digest:{day.isoformat()}"):
            view = self._seams.jobmodel.digest_view(store, self._settings, self._clock)
            if now - due >= LATE_AFTER:
                missed = templates.missed_digest(due=due, sent=now)
                view = replace(view, owner=(*view.owner, missed))
            message = digest.compose(view)
            queued = get_outbox(store, self._messenger.send(message, to="jim"))
            # Replaced by vetted(), it brought no card back, so each stays due for the next digest.
            if queued is not None and queued.lines == message.lines:
                for card in digest.reminders(view):
                    record_reminder(store, card.id, at=view.now)
        set_state(store, LAST_DIGEST_DAY, day.isoformat())
        set_state_time(store, LAST_DIGEST_AT, now)

    def _sent(self, ref: str) -> bool:
        """Whether a message with this ref has a row: a crash after a send, before its step was
        noted, must not send it twice. Jim's and Sulaiman's email never waits out quiet hours, so
        it has its row from the moment it is sent."""
        return any(row.ref == ref for row in list_outbox(self._store))

    # ----- the night's work

    def _night(self, now: datetime) -> None:
        """Once per night key, from 1:30 AM. Each step is noted as it finishes, so a restart picks
        up where this stopped. A step that failed (Drive away) tries again every 30 minutes."""
        if not self._ready() or (self._night_retry_at and now < self._night_retry_at):
            return
        store, key = self._store, night_key(now).isoformat()
        if get_state(store, NIGHT_KEY) == key:
            return
        done = self._night_done(key)
        if not done:
            self._name_late_night(now)
        failed = False
        for name, step in self._night_steps:
            if self._stop.is_set():
                return
            if name not in done:
                if self._night_step(name, step, now):
                    done.add(name)
                    set_state(store, NIGHT_STEPS, json.dumps({"key": key, "done": sorted(done)}))
                else:
                    failed = True
        if failed:
            self._night_retry_at = now + NIGHT_RETRY
            return
        self._night_retry_at = None
        set_state(store, NIGHT_KEY, key)

    def _night_done(self, key: str) -> set[str]:
        record = json.loads(get_state(self._store, NIGHT_STEPS, "{}") or "{}")
        return set(record.get("done", ())) if record.get("key") == key else set()

    def _night_step(self, name: str, step: Callable[[datetime], bool], now: datetime) -> bool:
        try:
            done = step(now)
            if done:
                self._close_dev("night_step_failed", f"The night's {name} step failed")
            return done
        except StoreError:
            raise
        except Exception as exc:  # noqa: BLE001 - one step's failure must not cost the rest
            _log.exception("the night's %s step failed", name)
            self._dev(now, "night_step_failed", f"The night's {name} step failed"
                                                f" ({type(exc).__name__}).")
            return False

    def _name_late_night(self, now: datetime) -> None:
        """Only a first try can be late: a retry means Drive was away, not that the PC was off."""
        due = at_pacific(night_key(now), NIGHT_FROM)
        if now - due >= LATE_AFTER:
            log_event(self._store, at=now, kind="night_late",
                      text=f"Missed the {fmt_clock(due)} night work: the PC was off. Ran at"
                           f" {fmt_clock(now)}.")

    def _night_backup(self, now: datetime) -> bool:
        """Committed to control\\backups once per night; blank key: the board says so already."""
        key = self._settings.backup_public_key
        if not key:
            return True
        try:
            made = backup.nightly_backup(self._paths, key, clock=self._clock)
        except backup.BackupError as exc:
            self._dev(now, "backup_failed", str(exc))
            return False
        target = self._settings.control / made.folder / made.name
        if target.exists():  # the crash came after the commit, before the step was noted
            return True
        result = self._ctx.commit([FileWrite(target, made.data, None)])
        if not result.ok:
            self._dev(now, "backup_failed", f"Tonight's backup was not saved: {result.reason}")
        else:
            self._close_dev("backup_failed")
        return result.ok

    def _night_log(self, now: datetime) -> bool:
        """Yesterday's log in control\\logs: job names and what happened, never an event's own
        text, which can quote a row as typed, address and all."""
        day = pacific_day(now) - timedelta(days=1)
        target = self._settings.control / "logs" / f"{day:%Y%m%d}.txt"
        if target.exists():
            return True
        result = self._ctx.commit([FileWrite(target, self._yesterdays_log(day), None)])
        if not result.ok:
            self._dev(now, "log_failed", f"Yesterday's log was not saved: {result.reason}")
        else:
            self._close_dev("log_failed")
        return result.ok

    def _yesterdays_log(self, day: date) -> bytes:
        names = {job.studio_id: job.name for job in list_jobs(self._store)}
        lines = [f"{fmt_date(event.at)} {fmt_clock(event.at)}  "
                 f"{names.get(event.studio_id, 'NG Runner')}: {event.kind.replace('_', ' ')}"
                 for event in list_events(self._store, since=_midnight(day),
                                          until=_midnight(day + timedelta(days=1)))
                 if event.lane != "DEV"]
        return ("\n".join(lines or [f"{fmt_date(day)}  Nothing happened."]) + "\n").encode("utf-8")

    def _night_toast_setting(self, now: datetime) -> bool:
        self._toast_setting = toasts.toasts_enabled()
        words = {True: "on", False: "off", None: "unknown"}[self._toast_setting]
        set_state(self._store, TOAST_SETTING, words)
        if self._toast_setting is False:
            self._dev(now, "toasts_off", templates.ALARMS["toasts_off"])
        else:  # the job model reads an open item with this code as the alarm standing
            for item in list_dev(self._store):
                if item.code == "toasts_off":
                    close_dev(self._store, item.id)
        return True

    def _night_prune_backups(self, now: datetime) -> bool:
        for reason in backup.prune(self._paths, clock=self._clock).left:
            self._dev(now, "backup_prune", reason)
        return True

    def _night_prune_renders(self, now: datetime) -> bool:
        left = prune_renders(self._store, before=now - KEEP_RENDERS).left
        if left:
            self._dev(now, "render_prune", f"{len(left)} old cached renders could not be removed.")
        return True

    def _night_prune_rows(self, now: datetime) -> bool:
        prune_seen_ids(self._store)
        prune_write_records(self._store, before=now - KEEP_WRITES)
        return True

    def _night_scan_temps(self, now: datetime) -> bool:
        """Read-only. The board's Health page lists what it finds, for Sulaiman."""
        found = scan_leftover_temps((self._settings.ng_jobs, self._settings.control / "shadow"))
        set_state(self._store, LEFTOVER_TEMPS, json.dumps(found))
        if found:
            self._dev(now, "leftover_temps", "Leftover temp files are in the job folders; the"
                                             " board's Health page lists them.")
        return True

    def _night_ran(self, now: datetime) -> bool:
        """The daily "ran" line: its absence is itself the alarm (design 2). Named by the night,
        as a retry after midnight is still that night, and so still the same line."""
        day = night_key(now) - timedelta(days=1)
        ref = f"ran:{day.isoformat()}"
        if not self._sent(ref):
            self._messenger.send(self._ran_line(day), to="sulaiman", ref=ref)
        return True

    def _ran_line(self, day: date) -> Any:
        store = self._store
        start, end = _midnight(day), _midnight(day + timedelta(days=1))
        cards = list_cards(store, state=None)
        return templates.ran(
            day, version=launcher.app_version(),
            new_rows=sum(start <= row.first_seen < end and row.state != "seen_at_install"
                         for row in list_fab_rows(store)),
            filed=sum(event.kind == "filed"
                      for event in list_events(store, since=start, until=end)),
            cards_opened=sum(start <= card.created_at < end for card in cards),
            cards_answered=sum(card.answered_at is not None and start <= card.answered_at < end
                               for card in cards),
            writes_refused=sum(not record.ok and record.at < end
                               for record in list_write_records(store, since=start)),
            dev_open=len(list_dev(store)))

    def _night_dev_lines(self, now: datetime) -> bool:
        """The open DEV items for Sulaiman; none means no message, as "ran" gives the count."""
        items, day = list_dev(self._store), night_key(now)
        ref = f"dev:{day.isoformat()}"
        if items and not self._sent(ref):
            names = {job.studio_id: job.name for job in list_jobs(self._store)}
            lines = [templates.DevLine(item.at, item.text, names.get(item.studio_id), item.code)
                     for item in items]
            self._messenger.send(templates.dev(lines, day=day), to="sulaiman", ref=ref)
        return True

    def _night_tell_jim(self, now: datetime) -> bool:
        """A DEV item about a job, over 48 hours old: marked told, which puts it on the next
        digest's For you line, once, with the manual fallback (jobmodel). One about no job leaves
        nothing to work in chat, so it stays with Sulaiman."""
        for item in list_dev(self._store):
            if (item.told_jim_at is None and item.studio_id is not None
                    and now - item.at >= TOLD_JIM_AFTER
                    and not self._seams.jobmodel.settled_by_filing(self._store, item)):
                job = get_job(self._store, item.studio_id)
                if job is not None:
                    log_event(self._store, at=now, kind="dev_told_jim", lane="ACT",
                              text=templates.stuck(job.name), studio_id=item.studio_id)
                    mark_dev_told_jim(self._store, item.id, at=now)
        return True

    # ----- the night's toolkit copy (design 6, Updates; RULES section 8)

    def _night_toolkit(self, now: datetime) -> bool:
        """Tonight's copy of Jim's _NG_Automation, tried on a worker of its own: adopted only once his
        regression is clean on that copy (toolkit.try_adoption), and recorded here by _take_toolkit.
        Done once started, so a regression that takes minutes holds up no other step. With no toolkit
        in Mozaik Shared there is nothing to copy: Health shows the folder."""
        source = self._settings.mozaik_shared / toolkit.TOOLKIT_FOLDER
        runs = self._toolkit_work()
        if runs.busy() or runs.free() <= 0 or not source.is_dir():
            return True
        adopted = list(dict.fromkeys(record.sha256 for record in reversed(list_snapshots(self._store))
                                     if record.adopted))      # newest first, each once
        folder, clock = self._paths.toolkit, self._clock
        runs.start(0, lambda: _try_toolkit(source, folder, adopted[:2], clock))
        self._take_toolkit(now)          # a runner that works at once has it already
        return True

    def _toolkit_work(self) -> Any:
        if self._toolkit_runs is None:
            from ngrunner.intake.pipeline import BackgroundFetches

            self._toolkit_runs = BackgroundFetches(1, overrun=TOOLKIT_OVERRUN, name="ngrunner-toolkit")
        return self._toolkit_runs

    def _take_toolkit(self, now: datetime) -> None:
        """What the night's try came to, on this thread: the store is written here only."""
        if self._toolkit_runs is None or self._store is None or self._store_problem:
            return
        for _key in self._toolkit_runs.overran():
            self._dev(now, "toolkit_stuck", "Tonight's copy of Jim's toolkit ran for over an hour and"
                                            " was given up. The checks go on with the copy adopted before.")
        for tried in self._toolkit_runs.finished():
            self._record_toolkit(tried, now)

    def _record_toolkit(self, tried: toolkit.Adoption, now: datetime) -> None:
        toolkit.record_adoption(self._store, tried)
        if tried.outcome in (toolkit.ADOPTED, toolkit.UNCHANGED):
            for code in ("toolkit_refused", "toolkit_failed", "toolkit_stuck"):
                self._close_dev(code)
        if tried.outcome == toolkit.ADOPTED:
            log_event(self._store, at=now, kind="toolkit_adopted",
                      text=f"Adopted a new copy of Jim's toolkit. {tried.words}")
        elif tried.outcome == toolkit.REFUSED:
            self._one_dev(now, "toolkit_refused", f"Jim's toolkit changed, and NG Runner did not adopt"
                                                  f" the copy. {tried.words} {self._checks_go_on()}")
        elif tried.outcome == toolkit.NOT_COPIED:
            self._one_dev(now, "toolkit_failed", f"{tried.words} {self._checks_go_on()}")

    def _checks_go_on(self) -> str:
        adopted = [record for record in list_snapshots(self._store) if record.adopted]
        if not adopted:
            return "No copy is adopted yet, so no nest can be checked."
        return f"The checks go on with the copy adopted {fmt_date(adopted[-1].taken_at)}."

    def _one_dev(self, now: datetime, code: str, text: str) -> None:
        """One open item of this code: the same words leave it standing, new words replace it."""
        for item in list_dev(self._store):
            if item.code == code and item.text != text:
                close_dev(self._store, item.id)
        self._dev(now, code, text)

    # ----- the support bundle

    def ask_support_bundle(self) -> None:
        """The tray's Send support bundle: its thread must return at once, so the next pass of the
        schedule does it."""
        with self._asked_lock:
            self._asked.add("support")

    def _send_support_bundle(self, now: datetime) -> None:
        data = support.make_bundle(self._paths, clock=self._clock)
        name = support.bundle_name(now)
        if self._ctx is None:  # not set up: there is no control folder, and this is when it helps
            folder = self._paths.app_data / "support"
            folder.mkdir(parents=True, exist_ok=True)
            with open(folder / name, "xb") as out:
                out.write(data)
            self._toast(ToastMessage("check_result", "Support bundle saved on this PC"))
            return
        target = self._settings.control / "support" / name
        result = self._ctx.commit([FileWrite(target, data, None)])
        if result.ok:
            log_event(self._store, at=now, kind="support_bundle", text="Support bundle sent.")
        else:
            self._dev(now, "support_bundle", f"A support bundle was not sent: {result.reason}")
        self._toast(ToastMessage(
            "check_result", "Support bundle sent" if result.ok else "Support bundle not sent"))

    # ----- the tray

    def open_board(self) -> None:
        """The address goes only to the browser, never to a log."""
        if self._board_url is not None:
            self._browser(self._board_url)

    def quit(self) -> None:
        """Quit means quit, until the next logon. The tray has already asked."""
        launcher.record_quit(self._paths, clock=self._clock)
        self.request_stop()

    def _update_tray(self, now: datetime) -> None:
        if self._tray is None:
            return
        state, paused = self._tray_state(now), self._is_paused()
        if self._last_tray != (state, paused):
            self._last_tray = (state, paused)
            self._tray.update(state, paused=paused)

    def _tray_state(self, now: datetime) -> TrayState:
        """Red with the reason when it can't do its work; otherwise the job model's. An unreadable
        store is decided here, as no view can be read then."""
        shadow, paused = self._is_shadow(), self._is_paused()
        problem = next(iter(self._blockers), None)
        if self._store is None or self._settings is None or self._store_problem:
            text = self._store_problem or problem or NOT_SET_UP
            return TrayState("red", 0, tray_tooltip(text, shadow=shadow))
        try:
            state = self._seams.jobmodel.tray_state(
                self._store, self._settings, self._clock, unwell=bool(problem) or self._overdue(),
                paused=paused, shadow=shadow)
        except StoreError:
            self._store_problem = NO_STORE
            return TrayState("red", 0, tray_tooltip(NO_STORE, shadow=shadow))
        if problem and state.colour == "red" and not any(
                words in state.tooltip for words in _THE_GUARDS_SAY):
            return replace(state, tooltip=tray_tooltip(problem, shadow=shadow))
        return state

    def _overdue(self) -> bool:
        return self._messenger is not None and bool(self._messenger.overdue())

    # ----- the board: what it shows

    def board(self) -> BoardView:
        """The current view. What NG Runner lacks and can't work without comes first on Your list,
        in plain words; with no settings or no database, that is all there is to show. A note (no
        backup key, no Studio login) is for Sulaiman and stops nothing, so it shows on Health: on
        Your list it would say something waits while the tray says nothing does."""
        now, paused, shadow = self._clock.now(), self._is_paused(), self._is_shadow()
        health = self._health()
        items = tuple(ListItem(text) for text in self._blockers)
        if self._store is not None and self._settings is not None and not self._store_problem:
            try:
                view = self._seams.jobmodel.board_view(
                    self._store, self._settings, self._clock, health=health, paused=paused,
                    shadow=shadow)
                return replace(view, your_list=(*items, *view.your_list),
                               tray=self._tray_state(now))
            except StoreError:
                self._store_problem = NO_STORE
                items = (ListItem(NO_STORE), *items)
        return BoardView(jobs=(), your_list=items, cards=(), health=health,
                         tray=self._tray_state(now), paused=paused, shadow=shadow)

    def history(self) -> list[EventView]:
        if self._store is None or self._store_problem:
            return []
        return self._seams.jobmodel.history(self._store)

    def _health(self) -> HealthView:
        """The Health page, from what the scheduler last read, so no page waits on a shared
        folder. The job model adds the alarms the store holds."""
        store = self._store
        last_export = list_synced_at = beat = None
        mozaik = ""
        leftovers: tuple[Path, ...] = ()
        if store is not None and not self._store_problem:
            try:
                last_export = get_state_time(store, LAST_EXPORT)
                state = get_list_state(store)
                list_synced_at = None if state is None else state.synced_at
                beat = get_state_time(store, LAST_HEARTBEAT)
                leftovers = tuple(
                    Path(p) for p in json.loads(get_state(store, LEFTOVER_TEMPS, "[]") or "[]"))
                mozaik = _mozaik_words(*mozaik_said(store))
            except StoreError:
                self._store_problem = NO_STORE
        off = (NO_TRACKER_FOLDER,) if (self._settings is not None
                                       and self._settings.tracker_folder is None) else ()
        return HealthView(paths=self._path_checks, last_export=last_export,
                          list_synced_at=list_synced_at, heartbeat=beat,
                          toasts=self._toast_setting, alarms=(*self._blockers, *self._notes, *off),
                          leftover_temps=leftovers, app_version=launcher.app_version(),
                          mozaik=mozaik)

    def _is_paused(self) -> bool:
        if self._store is None:
            return False
        try:
            return is_paused(self._store)
        except StoreError:
            return False

    def _is_shadow(self) -> bool:
        settings = self._mode_wanted or self._settings
        return settings is None or settings.mode == "shadow"

    # ----- the board: what it does

    def answer(self, card_id: str, choice: str) -> str:
        """A card's button: choice is its label, exactly as shown."""
        return self._answer(card_id, choice, words=choice, channel="board")

    def command(self, text: str) -> str:
        """The command box, read by cards.parse_command and nothing else."""
        return self._run_command(text, channel="command")

    def toast_choice(self, argument: str) -> None:
        """A click on a toast: its button's "<card ID> <label>", a close prompt's
        "check-prompt <id> <n>", or the toast itself."""
        if argument == toasts.OPEN_BOARD:
            self.open_board()
            return
        from ngrunner.checks import jobchecks

        prompt = jobchecks.prompt_choice(argument)
        if prompt is not None:
            reply = self._answer_prompt(prompt[0], prompt[1], words=argument.split()[-1], channel="toast")
            if reply:
                self._toast(ToastMessage("echo", reply))
            return
        reply = self._run_command(argument, channel="toast")
        if reply:
            self._toast(ToastMessage("echo", reply))

    def pause(self) -> str:
        reply = self._set_paused(True, self._clock.now(), "Paused at the PC.")
        return reply or templates.echo(Command("pause", "pause"))

    def resume(self) -> str:
        reply = self._set_paused(False, self._clock.now(), "Resumed at the PC.")
        return reply or templates.echo(Command("resume", "resume"))

    def check_now(self) -> str:
        """The next pass of the schedule, within a second, runs the tick."""
        with self._asked_lock:
            self._asked.add("check")
        return CHECKING

    def add_job(self, text: str) -> str:
        """By name or Studio ID, through the same parser as the command box, so digits mean a
        Studio ID."""
        return self._run_command(f"add job {text}", channel="board")

    def set_mode(self, mode: str) -> str:
        """The deliberate switch between shadow and live, on Health, logged as an event. It answers
        at once: a pass of the schedule can be waiting on Render, and goes on as it began. So it
        takes effect now if no pass is running, or else as the next one starts, and that pass ticks.
        Going live hands back what the trial run filed aside (pipeline.go_live)."""
        if self._settings is None:
            return NOT_SET_UP
        if self._store is None or self._store_problem or self._ctx is None:
            return CANT_DO_THAT
        now = self._clock.now()
        current = self._mode_wanted or self._settings
        old = current.mode
        if mode == old:
            return f"NG Runner is already in {mode} mode, so nothing changed."
        settings = replace(current, mode=mode)
        config.save_settings(self._paths, settings)
        log_event(self._store, at=now, kind="mode_changed", lane="ACT",
                  text=f"Switched from {old} to {mode}.")
        if mode == "live":
            self._seams.pipeline.go_live(self._store, now)
        with self._asked_lock:
            self._mode_wanted = settings
            self._asked.add("check")
        if self._run_lock.acquire(blocking=False):
            try:
                self._take_mode()
            finally:
                self._run_lock.release()
        self._update_tray(now)
        if mode == "live":
            return "Switched to live: NG Runner now files into NG Jobs."
        return "Switched to shadow: NG Runner now files only into the control folder."

    def _take_mode(self) -> None:
        """Between passes: the switch set_mode was asked for. Never during one, as a pass rebuilds
        the context from what it read."""
        with self._asked_lock:
            settings, self._mode_wanted = self._mode_wanted, None
        if settings is None:
            return
        self._settings = settings
        if self._messenger is not None:
            self._messenger.settings = settings  # it reads them at each send
        if self._ctx is not None:
            self._ctx = replace(self._ctx, settings=settings, mode=settings.mode)

    def check_job(self, studio_id: str) -> str:
        """The board's Check now on one job (P3)."""
        if self._store is None or self._store_problem or self._ctx is None:
            return CANT_DO_THAT
        from ngrunner.checks import jobchecks

        reply = jobchecks.ask(self._ctx, studio_id.strip(), trigger="check_now")
        with self._asked_lock:
            self._asked.add("checks")
        return reply

    def settle_item(self, studio_id: str, run_id: str, n: str, how: str) -> str:
        """A check item's settle button: "Not a problem for this job" or "I already told chat"."""
        if self._store is None or self._store_problem or self._ctx is None:
            return CANT_DO_THAT
        from ngrunner.checks import jobchecks
        from ngrunner.messages.templates import SETTLE_GONE, SETTLE_LABELS

        if how not in SETTLE_LABELS or not run_id.strip().isdigit() or not n.strip().isdigit():
            return SETTLE_GONE
        return jobchecks.settle(self._ctx, studio_id.strip(), int(run_id), int(n), how=how,
                                words=SETTLE_LABELS[how], channel="board")

    def answer_prompt(self, prompt_id: str, choice: str) -> str:
        """A close prompt's button on the board."""
        if not prompt_id.strip().isdigit():
            return NOT_OPEN
        return self._answer_prompt(int(prompt_id), choice, words=choice, channel="board")

    def _answer_prompt(self, prompt_id: int, choice: str | int, *, words: str, channel: str) -> str:
        if self._store is None or self._store_problem or self._ctx is None:
            return CANT_DO_THAT
        from ngrunner.checks import jobchecks

        reply = jobchecks.answer_prompt(self._ctx, prompt_id, choice, words=words, channel=channel)
        with self._asked_lock:
            self._asked.add("checks")
        return reply

    def lock_click(self, studio_id: str, click: str, material: str, without_check: bool) -> str:
        """The board's Lock design, Finish lock and Lock (P4): refused at once with what is missing,
        or asked, and acted on at the next pass of the schedule."""
        if self._store is None or self._store_problem or self._ctx is None:
            return CANT_DO_THAT
        from ngrunner.lock import work

        reply = work.ask_lock(self._ctx, studio_id.strip(), click.strip(), material.strip() or None,
                              bool(without_check))
        with self._asked_lock:
            self._asked.add("lock")
        return reply

    def resend_row(self, batch_id: str) -> str:
        """[Re-send only the missing row], where the board offers it."""
        if self._store is None or self._store_problem or self._ctx is None:
            return CANT_DO_THAT
        from ngrunner.lock import work

        reply = work.ask_resend(self._ctx, batch_id.strip())
        with self._asked_lock:
            self._asked.add("lock")
        return reply

    def undo_fix(self, fix_id: str) -> str:
        """Undo of a fix NG Runner wrote: it waits on the interlock like the fix did."""
        if self._store is None or self._store_problem or self._ctx is None:
            return CANT_DO_THAT
        from ngrunner.lock import work
        from ngrunner.messages.templates import LOCK_GONE

        if not fix_id.strip().isdigit():
            return LOCK_GONE
        reply = work.ask_undo(self._ctx, int(fix_id))
        with self._asked_lock:
            self._asked.add("lock")
        return reply

    def _run_command(self, text: str, *, channel: str) -> str:
        if self._store is None or self._store_problem:
            return CANT_DO_THAT
        parsed = parse_command(text, self._open_cards())
        if isinstance(parsed, Rejection):
            return parsed.text
        if parsed.kind == "pause":
            return self.pause()
        if parsed.kind == "resume":
            return self.resume()
        if parsed.kind == "answer":
            return self._answer(parsed.card_id, parsed.choice, words=parsed.text, channel=channel)
        if parsed.kind == "check":
            return self._check_named(parsed.name or "")
        return self._add_job(parsed)

    def _check_named(self, name: str) -> str:
        """The command box's "check <job>": Check now on the one job that name names, whole."""
        if self._ctx is None:
            return NOT_SET_UP
        from ngrunner.checks import jobchecks

        named = jobchecks.job_named(self._store, name)
        if not named:
            return jobchecks.NO_SUCH_JOB
        return self.check_job(named)

    def _open_cards(self) -> list[Card]:
        found = (self._card_or_none(record) for record in list_cards(self._store, state="open"))
        return [card for card in found if card is not None]

    def _card_of(self, record: Any) -> Card:
        """Named by its job, its row, or for a sheet size card with neither, its folder."""
        job = None if record.studio_id is None else get_job(self._store, record.studio_id)
        row = None if record.fab_row_id is None else get_fab_row(self._store, record.fab_row_id)
        name = job.name if job is not None else (
            folder_of_card(self._store, record.id) if row is None else None)
        return card_from_record(record, job=name, row=None if row is None else row.text)

    def _card_or_none(self, record: Any) -> Card | None:
        """One card that can't be shown costs only itself: the command box reads every open card,
        and it must go on working for the rest."""
        try:
            return self._card_of(record)
        except ValueError:
            self._dev(self._clock.now(), "card_unshowable",
                      f"Card {record.id} is about nothing NG Runner can name, so it can't be shown.")
            return None

    def _answer(self, card_id: str, choice: str, *, words: str, channel: str) -> str:
        """Jim's answer is recorded as he gave it, and the pipeline's next tick acts on it, only
        through the card's choice_ids."""
        if self._store is None or self._store_problem:
            return CANT_DO_THAT
        record = get_card(self._store, card_id)
        if record is None or record.state != "open":
            return NOT_OPEN
        if choice not in record.choices:
            return NOT_A_CHOICE
        if not answer_card(self._store, card_id, answer=choice, answer_words=words,
                           answered_by="jim", answered_at=self._clock.now(), channel=channel):
            return NOT_OPEN
        with self._asked_lock:
            self._asked.add("check")
        return templates.echo(Command("answer", words, card_id=card_id, choice=choice))

    def _add_job(self, parsed: Command) -> str:
        """Enters the pipeline at step 3 or 4, in its own transaction; its next tick does the rest.
        The reply is the pipeline's plain line: it knows whether the job was found."""
        if self._ctx is None:
            return NOT_SET_UP
        return self._seams.pipeline.add_job(self._ctx, name=parsed.name,
                                            studio_id=parsed.studio_id)

    def _set_paused(self, paused: bool, now: datetime, text: str) -> str | None:
        """Sets the flag the interlock reads, logs it, and turns the tray grey at once. Returns why
        it could not, or None."""
        if self._store is None or self._store_problem:
            return CANT_DO_THAT
        if self._is_paused() != paused:
            set_state(self._store, PAUSED, "yes" if paused else "no")
            log_event(self._store, at=now, kind="paused" if paused else "resumed", text=text,
                      lane="ACT")
        if paused:
            self._release_leases(now)
        self._update_tray(now)
        return None


def _path_check(label: str, path: Path) -> PathCheck:
    try:
        os.stat(path)
    except FileNotFoundError:
        return PathCheck(label, path, False, "Not found")
    except OSError:
        return PathCheck(label, path, None, "Could not be checked")
    return PathCheck(label, path, True)


def _midnight(day: date) -> datetime:
    return at_pacific(day, time(0, 0))


# ----- the command line's run


def run(
    paths: AppPaths,
    *,
    attempt: str | None = None,
    open_board: bool = False,
    mutex: str = single.SUPERVISOR,
    stop_event: str = single.STOP_EVENT,
    **options: Any,
) -> int:
    """NG Runner itself, until it is asked to stop (tray Quit, or the install scripts' stop event).
    The returned 0 also means another copy holds the lock: the launcher reads that as already
    running. Nothing here prints: app-output.log goes into the support bundle, and the board's
    address carries its token. The guard stays on this thread, which lives as long as the process.
    mutex and stop_event are for tests; options go to Supervisor."""
    guard = single.acquire(mutex)
    if guard is None:
        if open_board:
            single.open_running_board(paths)
        return 0
    try:
        with single.StopSignal(stop_event) as stop:
            supervisor = Supervisor(paths, attempt=attempt, **options)
            try:
                supervisor.start()
                if open_board:
                    supervisor.open_board()
                supervisor.start_scheduler()
                while not (supervisor.stop_requested or stop.wait(_LOOP_WAIT_S / 2)):
                    pass
            finally:
                supervisor.stop()
    finally:
        guard.release()
    return 0
