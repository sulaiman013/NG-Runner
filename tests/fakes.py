"""
The day simulation's world (docs/p1a/integration-spec.md, section 8): everything around NG Runner,
played by fakes on one fake clock, so a day runs in seconds and no test reaches a network, Google,
Studio, Render, G: or this PC's settings. tests/test_e2e_day.py tells the stories.

Why a module of its own: every scenario needs the same people around NG Runner, and what differs
is what they do. Barbara edits a sheet, the Apps Script seals her rows into the control folder, the
office PC pushes a job list to Render, Studio renders a summary afresh for each fetch, and the
scripts on Google send the mail. Each is played here as plainly as it can be:
- Studio and Render are real HTTP servers on 127.0.0.1 (helpers_studio.py), so NG Runner's own
  clients run for real. Settings hold https:// names, as settings.json must, and LocalRoutes, the
  one session NG Runner is given, carries those names to the fakes and refuses every other host.
- FakeAppsScript seals files with the test key, as google/ does with its Script Property.
- RealScripts runs the real google/Outbox.gs and Watchdog.gs under node instead, wherever a
  scenario turns on what they decide: a test playing the Apps Script hid a real mismatch once.

Names are made up, addresses are example.com, and the signing key is google/test/harness.js's own,
so what the real scripts seal and what Python seals open on both sides.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from helpers_pdf import summary_pdf
from helpers_studio import FakeStudio, Reply, running_studio
from ngrunner.backup import new_keypair
from ngrunner.clock import FakeClock, to_iso
from ngrunner.config import AppPaths, Settings, roots, save_settings
from ngrunner.intake.fetch import LAYOUT_PATHS, candidate_slugs
from ngrunner.secrets import MemorySecrets
from ngrunner.signing import key_from_text, new_id, open_envelope, seal
from ngrunner.tray import COLOURS

KEY_HEX = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"  # harness.js's
KEY = key_from_text(KEY_HEX)
EXPORT_KEY = "test-export-key"
STUDIO_URL = "https://studio.example.com"
RENDER_URL = "https://render.example.com"
PUSHOVER_HOST = "api.pushover.net"
PUSHOVER_KEYS = {"pushover_token": "a1" * 15, "pushover_user": "b2" * 15}
START = datetime(2026, 10, 27, 17, 0, tzinfo=timezone.utc)  # Tue 10/27 10:00 AM Pacific
TEST_MUTEX = "Local\\NGRunner.Test.DaySimulation"           # never the app's own mutex
BACKUP_PUBLIC_KEY = new_keypair()[1]                        # no world has a "no backup key" item
GOOGLE = Path(__file__).resolve().parent.parent / "google"
_FIVE_MINUTES = timedelta(minutes=5)
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
# A closet's PULLED list as Studio prints it, whose takeoff is complete: 8 parts of 3/4 white (2
# sheets, 26 feet of tape), and shelf pins, which are no board.
CLOSET_PARTS = (("VT-WH", 2, "VT 14x84x3/4 FD WH"), ("SH-WH", 6, "SH 14x24x3/4 FIX WH"),
                ("400000155", 8, "Shelf Pin"))


# ----- Studio: a report for each Studio ID, rendered afresh for every fetch


@dataclass
class StudioJob:
    """What Studio holds for one project, and its switches.

    name: the job's name, which Studio's address is made from and page 1 prints.
    client: the client's name, for the same address.
    designs: page 1's (name, category) pairs; change them and the IC summary has changed.
    printed_name: what page 1 prints when it is not name (another job's report, or a rename).
    missing: 404 at its address, as for a wrong slug.
    early: how many of the next fetches get a render Studio has not finished.
    truncated: the file ends part-way.
    delay: seconds before the reply starts (Studio rendering).
    renders: how many reports it has handed out.
    proposal: the design names on its project page's Proposal tab, when not page 1's.
    page_status: the status of its project page, when it isn't there to read.
    page_changed: its project page no longer has the tabs NG Runner reads.
    real_slugs: the address Studio really has it at, when no derived one finds it.
    parts: the PULLED list's rows (item, quantity, description), printed as Studio prints them so
        the shared converter reads them; None prints the made-up list it can't read.
    """

    studio_id: str
    name: str
    client: str = ""
    designs: tuple[tuple[str, str], ...] = ()
    printed_name: str | None = None
    missing: bool = False
    early: int = 0
    truncated: bool = False
    delay: float = 0.0
    renders: int = 0
    proposal: tuple[str, ...] | None = None
    page_status: int = 200
    page_changed: bool = False
    real_slugs: tuple[str, str] | None = None
    parts: tuple[tuple[str, int, str], ...] | None = CLOSET_PARTS

    def __post_init__(self) -> None:
        if not self.designs:
            surname = self.name.split("_")[0]
            self.designs = ((f"{surname} - Primary Closet", "His and Her Master"),)

    @property
    def slugs(self) -> tuple[str, str]:
        return self.real_slugs or candidate_slugs(self.name, self.client or None)[0]


class _LiveReplies(dict):
    """FakeStudio's replies, worked out per request (it looks each one up with get()), since Studio
    renders afresh: two fetches of one job hold the same content in different bytes."""

    def __init__(self, studio: Studio) -> None:
        super().__init__()
        self._studio = studio

    def get(self, path: str, default: Any = None) -> Any:
        return self._studio.reply(path) or default


_REPORT = re.compile(r"/(?P<kind>install_summary(?:_landscape)?)/(?P<client>[^/]+)/"
                     r"(?P<project>[^/]+)/(?P<id>[0-9]+)/")
_LAYOUTS = {path: layout for layout, path in LAYOUT_PATHS.items()}
_PROJECT_PAGE = re.compile(r"/project/details/(?P<id>[0-9]+)/")


def _project_page_html(job: StudioJob) -> str:
    """A project page in the shape measured on real ones (intake/studio_designs.py): a wrapper per
    design on each tab, one div.design-details inside it, and the report's link."""
    def cards(wrapper: str, names: Iterable[str]) -> str:
        return "".join(
            f'<form id="{wrapper}_{n}" class="design-details"><div class="design-details">'
            f"<h3>{name}</h3></div></form>" for n, name in enumerate(names, start=1))

    client, project = job.slugs
    listed = tuple(name for name, _ in job.designs) if job.proposal is None else job.proposal
    return (f'<html><body><a href="/install_summary_landscape/{client}/{project}/{job.studio_id}/">'
            f'Install report</a><div id="project-proposal">'
            f"{cards('design_proposal_details', listed)}</div>"
            f'<div id="project-design">{cards("design_details", listed)}</div></body></html>')


class Studio:
    """The fake Studio. asked lists every path anyone asked for; served, each report handed out as
    (studio ID, layout). down makes every address answer 503."""

    def __init__(self, fake: FakeStudio) -> None:
        self.fake = fake
        self.jobs: dict[str, StudioJob] = {}
        self.down = False
        self.served: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self._count = 0
        fake.replies = _LiveReplies(self)

    @property
    def base_url(self) -> str:
        return self.fake.base_url

    @property
    def asked(self) -> list[str]:
        return self.fake.asked

    def add(self, studio_id: str, name: str, *, client: str = "", **switches: Any) -> StudioJob:
        job = StudioJob(studio_id, name, client, **switches)
        self.jobs[studio_id] = job
        return job

    def fetches(self, studio_id: str, layout: str | None = None) -> int:
        return sum(1 for sid, how in self.served
                   if sid == studio_id and layout in (None, how))

    def report(self, studio_id: str, *, layout: str = "landscape", note: int = 0,
               finished: int | None = None) -> bytes:
        """The job's report as Studio would render it now; note varies its bytes alone."""
        job = self.jobs[studio_id]
        created = (datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(seconds=note))
        return summary_pdf(project_name=job.printed_name or job.name, layout=layout,
                           designs=job.designs, finished=finished,
                           created=created.strftime("D:%Y%m%d%H%M%S-07'00'"), parts=job.parts)

    def reply(self, path: str) -> Reply | None:
        if self.down:
            return Reply(status=503, body=b"<html>Service Unavailable</html>")
        page = _PROJECT_PAGE.fullmatch(path)
        if page is not None:
            return self._project_page(page["id"])
        found = _REPORT.fullmatch(path)
        job = self.jobs.get(found["id"]) if found else None
        if job is None or job.missing or (found["client"], found["project"]) != job.slugs:
            return None
        layout = _LAYOUTS[found["kind"]]
        with self._lock:
            self._count += 1
            job.renders += 1
            early = job.early > 0
            job.early -= 1 if early else 0
            self.served.append((job.studio_id, layout))
            note = self._count
        finished = max(len(job.designs) - 1, 0) if early else None
        pdf = self.report(job.studio_id, layout=layout, note=note, finished=finished)
        return Reply(body=pdf[:-64] if job.truncated else pdf, delay=job.delay)

    def _project_page(self, studio_id: str) -> Reply | None:
        job = self.jobs.get(studio_id)
        if job is None:
            return None
        if job.page_status != 200:
            return Reply(status=job.page_status, body=b"<html>Not here</html>")
        html = "<html><body>A new design</body></html>" if job.page_changed else (
            _project_page_html(job))
        return Reply(body=html.encode("utf-8"))


# ----- Render: the job list, as the office PC last pushed it


@dataclass(frozen=True)
class Opp:
    """One Opportunity as Render's export carries it. designer is the owner's whole name; client
    defaults to the name's first word."""

    studio_id: str
    name: str
    stage: str = "Ready to Order"
    designer: str = "Pat Example"
    client: str | None = None

    def exported(self) -> dict[str, Any]:
        return {"studio_id": self.studio_id, "name": self.name, "stage": self.stage,
                "designer": self.designer, "client": self.client or self.name.split("_")[0],
                "created": "2026-10-14", "modified": "2026-10-14"}


class FakeRender:
    """The hosted Assembly Reports: GET /api/jobs/export and POST /api/sync, on a real server.

    opportunities: Salesforce's list now; change it freely. push() is the office PC's publisher:
    what Render serves is what was pushed last, read at that moment, and a Sync request gets a push
    a minute later (on_clock), unless answers_sync is off. sync_requests holds when each came, and
    export_status makes the export answer that status instead."""

    def __init__(self, clock: FakeClock, key: str = EXPORT_KEY) -> None:
        self.clock = clock
        self.key = key
        self.base_url = ""
        self.opportunities: list[Opp] = []
        self.sync_requests: list[datetime] = []
        self.exports = 0
        self.answers_sync = True
        self.export_status = 200
        self._listed: tuple[Opp, ...] = ()
        self._synced_at: datetime | None = None
        self._push_at: datetime | None = None
        self._lock = threading.Lock()

    @property
    def synced_at(self) -> datetime | None:
        return self._synced_at

    def push(self) -> None:
        """The office PC pushes the list as it is now; Render's time is floored to the second."""
        with self._lock:
            self._listed = tuple(self.opportunities)
            self._synced_at = self.clock.now().replace(microsecond=0)
            self._push_at = None

    def on_clock(self) -> None:
        if self._push_at is not None and self.clock.now() >= self._push_at:
            self.push()

    def sync_asked(self) -> None:
        with self._lock:
            now = self.clock.now()
            self.sync_requests.append(now)
            if self.answers_sync and self._push_at is None:
                self._push_at = now + timedelta(minutes=1)

    def export_body(self) -> dict[str, Any]:
        with self._lock:
            self.exports += 1
            synced = self._synced_at
            return {"synced_at": None if synced is None else synced.isoformat(),
                    "job_count": len(self._listed), "jobs": [o.exported() for o in self._listed]}


class _RenderHandler(BaseHTTPRequestHandler):
    def _render(self) -> FakeRender:
        return self.server.render  # type: ignore[attr-defined]

    def _reply(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
        render = self._render()
        if self.path != "/api/jobs/export":
            self._reply(404, {"error": "not found"})
        elif render.export_status != 200:
            self._reply(render.export_status, {"error": "busy"})
        elif self.headers.get("X-Export-Key") != render.key:
            self._reply(403, {"error": "forbidden"})
        else:
            self._reply(200, render.export_body())

    def do_POST(self) -> None:  # noqa: N802 - the name http.server calls
        if self.path != "/api/sync":
            self._reply(404, {"error": "not found"})
            return
        self._render().sync_asked()
        self._reply(200, {"ok": True})

    def date_time_string(self, timestamp: float | None = None) -> str:
        """The Date header reads the fake clock: RenderClient compares it with its own clock, and
        real time is days away from a simulated day."""
        return format_datetime(self._render().clock.now(), usegmt=True)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - http.server's name
        pass


@contextmanager
def running_render(clock: FakeClock, key: str = EXPORT_KEY) -> Iterator[FakeRender]:
    """A FakeRender listening on a free port of 127.0.0.1 (its base_url) until the block ends."""
    render = FakeRender(clock, key)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RenderHandler)
    server.render = render  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                              daemon=True)
    thread.start()
    render.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield render
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


class LocalRoutes(requests.Session):
    """The one session NG Runner is given: it carries the names in Settings to the fakes on this PC
    and refuses every other host, so nothing can reach a network by mistake. A post to Pushover is
    recorded in pushover and answered yes."""

    def __init__(self, routes: Mapping[str, str]) -> None:
        super().__init__()
        self.trust_env = False
        self._routes = dict(routes)
        self.pushover: list[dict[str, Any]] = []

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> requests.Response:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if host == PUSHOVER_HOST:
            self.pushover.append(dict(kwargs.get("data") or {}))
            reply = requests.Response()
            reply.status_code = 200
            reply._content = b'{"status": 1}'
            reply.encoding = "utf-8"
            return reply
        if host in self._routes:
            url = self._routes[host] + parts.path + (f"?{parts.query}" if parts.query else "")
        elif host not in _LOOPBACK:
            raise requests.ConnectionError(f"no network in tests: {host} is not one of the fakes")
        return super().request(method, url, *args, **kwargs)


# ----- the Apps Script: Barbara's sheet in, signed files out

_INSTALL = ("Wednesday", " 11 /4")  # as Sheet1 shows it: Wed 11/4/26, a week after START


@dataclass
class SheetRow:
    """One Sheet1 row as Barbara left it. Rows have no IDs: a row's number is its place."""

    job: str
    designer: str = "PE"
    install: tuple[str, str] = _INSTALL
    fab_date: str = ""


@dataclass(frozen=True)
class Sent:
    """One message the fake Apps Script sent."""

    id: str
    to: str
    channel: str
    subject: str
    lines: tuple[str, ...]
    urgent: bool
    ref: str


class FakeAppsScript:
    """google/ played by the test: seals Fab exports, edits and receipts with the test key and
    writes them into the control folder. Its streams start at the current Unix second, as the real
    ones do. on_clock() is its triggers: an export every 5 minutes, the outbox every minute."""

    def __init__(self, control: Path, clock: FakeClock, *, key: bytes = KEY) -> None:
        self.control = control
        self.clock = clock
        self.key = key
        self.sheet: list[SheetRow] = []
        self.edits: list[dict[str, Any]] = []
        self.sent: list[Sent] = []
        self.receipts_stopped = False
        self.auto_export = True
        self.exports = 0
        self._last_export: datetime | None = None
        self._done: set[str] = set()
        self._seq: dict[str, int] = {}

    # Barbara

    def add_row(self, job: str, designer: str = "PE", install: tuple[str, str] = _INSTALL, *,
                fab_date: str = "") -> SheetRow:
        row = SheetRow(job, designer, install, fab_date)
        self.sheet.append(row)
        return row

    def row(self, job: str) -> SheetRow:
        return next(r for r in self.sheet if r.job == job)

    def correct_row(self, job: str, *, to: str | None = None, designer: str | None = None,
                    install: tuple[str, str] | None = None) -> None:
        """Barbara corrects a cell or two in place; the edit log sees each."""
        row = self.row(job)
        number = self.sheet.index(row) + 2
        if to is not None:
            self._edit(number, "job", row.job, to)
            row.job = to
        if designer is not None:
            self._edit(number, "designer", row.designer, designer)
            row.designer = designer
        if install is not None:
            self._edit(number, "install", row.install[1], install[1])
            row.install = install
        self.write_edits()

    def remove_row(self, job: str) -> None:
        """Finished rows move to Archive, so the rows below move up."""
        self.sheet.remove(self.row(job))

    def _edit(self, sheet_row: int, column: str, old: str | None, new: str | None) -> None:
        self.edits.append({"at": to_iso(self.clock.now()), "sheet_row": sheet_row,
                           "column": column, "old": old, "new": new})

    # the files

    def _next_seq(self, kind: str) -> int:
        start = int(self.clock.now().timestamp())
        self._seq[kind] = self._seq.get(kind, start - 1) + 1
        return self._seq[kind]

    def _write(self, parts: Sequence[str], data: bytes) -> Path:
        """Replaces the file in one step, as Drive for desktop shows a finished upload."""
        target = self.control.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".uploading")
        temp.write_bytes(data)
        os.replace(temp, target)
        return target

    def export(self, rows: Sequence[SheetRow] | None = None) -> int:
        """Writes fab/export.json with the sheet's rows (or these); returns its seq."""
        now = self.clock.now()
        listed = self.sheet if rows is None else rows
        payload = {"sheet": "Sheet1", "exported_at": to_iso(now), "rows": [
            {"sheet_row": number, "job": r.job, "designer": r.designer, "install": list(r.install),
             "fab_date": r.fab_date}
            for number, r in enumerate(listed, start=2)
        ]}
        seq = self._next_seq("fab_export")
        self._write(("fab", "export.json"),
                    seal("fab_export", payload, key=self.key, seq=seq, issued_at=now))
        self.exports += 1
        self._last_export = now
        return seq

    def write_edits(self) -> int:
        now = self.clock.now()
        seq = self._next_seq("fab_edits")
        self._write(("fab", "edits.json"), seal("fab_edits", {"edits": self.edits[-500:]},
                                                key=self.key, seq=seq, issued_at=now))
        return seq

    def write_receipt(self, outbox_id: str, *, ok: bool = True, error: str | None = None,
                      channel: str = "email", seq: int | None = None) -> Path:
        """receipts/<outbox id>.json; seq lets a scenario make one arrive out of order."""
        now = self.clock.now()
        payload = {"outbox_id": outbox_id, "sent_at": to_iso(now), "channel": channel, "ok": ok,
                   "error": error}
        number = self._next_seq("receipt") if seq is None else seq
        return self._write(("receipts", f"{outbox_id}.json"),
                           seal("receipt", payload, key=self.key, seq=number, issued_at=now))

    def pause(self, reason: str = "Sulaiman is checking something") -> str:
        """A signed pause file, as tools/sign_pause.py makes one; returns its id."""
        pause_id = new_id()
        self._write(("pause", f"{pause_id}.json"),
                    seal("pause", {"reason": reason}, key=self.key,
                         seq=self._next_seq("pause"), issued_at=self.clock.now(), id=pause_id))
        return pause_id

    def waiting_in_outbox(self) -> list[Any]:
        """The outbox files not sent yet, opened, in sequence order."""
        folder = self.control / "outbox"
        if not folder.is_dir():
            return []
        opened = [open_envelope(p.read_bytes(), key=self.key, kind="outbox")
                  for p in folder.glob("*.json") if p.stem not in self._done]
        return sorted(opened, key=lambda envelope: envelope.seq)

    def deliver(self) -> list[Sent]:
        """Sends what waits in outbox/, each once, and writes each receipt; nothing while
        receipts_stopped (the script is down, so its messages simply wait)."""
        if self.receipts_stopped:
            return []
        sent = []
        for envelope in self.waiting_in_outbox():
            self._done.add(envelope.id)
            body = envelope.payload
            sent.append(Sent(envelope.id, body["to"], body["channel"], body["subject"],
                             tuple(body["lines"]), body["urgent"], body["ref"]))
            self.write_receipt(envelope.id, channel=body["channel"])
        self.sent.extend(sent)
        return sent

    def on_clock(self) -> None:
        now = self.clock.now()
        if self.auto_export and (self._last_export is None
                                 or now - self._last_export >= _FIVE_MINUTES):
            self.export()
        self.deliver()


# ----- the real scripts, under node


def find_node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    default = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "node.exe"
    return str(default) if default.is_file() else None


# Loads the real .gs files beside the harness's fakes, puts the control folder's files in, then
# runs each step's functions at its time.
_DRIVER = r"""
const fs = require('node:fs');
const { load } = require(process.argv[2]);
const job = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const h = load({ now: job.steps[0].at, props: job.props });
for (const f of job.files) h.control.put(f.path, f.text);
const results = [];
for (const step of job.steps) {
  h.clock.set(step.at);
  for (const name of step.calls) results.push({ name, at: step.at, value: h.gs[name]() ?? null });
}
const named = h.control.names('receipts');
console.log(JSON.stringify({
  results,
  digest_day: h.gs.state_('digest.day'),
  beat: h.gs.state_('watchdog.beat'),
  mail: h.mail.sent.map((m) => ({ to: m.to, subject: m.subject, body: m.body })),
  push: h.fetch.requests.map((r) => r.params.payload),
  props: h.props.getProperties(),
  receipts: named.map((name) => ({ name, text: h.control.text('receipts/' + name) })),
  logs: h.logs,
}));
"""


class RealScripts:
    """The real google/Outbox.gs and Watchdog.gs over a control folder on disk. Each run() loads
    the folder's outbox, heartbeats and receipts into the harness's fake Drive, runs the functions,
    and writes the receipts back, so NG Runner reads what the real script signed. What the scripts
    keep in Script Properties carries from run to run. mail and push gather what they sent.

    props: Script Properties beyond the harness's defaults, such as {"SHADOW": "1"}.
    Jim is jim@example.com, Barbara barbara@example.com, Sulaiman dev@example.com (harness.js)."""

    def __init__(self, control: Path, work: Path, *,
                 props: Mapping[str, str] | None = None) -> None:
        node = find_node()
        if node is None:
            import pytest

            pytest.skip("node is not installed, so the Apps Script can't run here")
        self.node = node
        self.control = control
        self.work = work
        self.props: dict[str, Any] = dict(props or {})
        self.mail: list[dict[str, str]] = []
        self.push: list[dict[str, Any]] = []
        self.digest_day: str | None = None
        self.beat: dict[str, Any] | None = None
        self.results: list[dict[str, Any]] = []
        self.logs: list[list[str]] = []
        work.mkdir(parents=True, exist_ok=True)

    def _files(self) -> list[dict[str, str]]:
        found = []
        for folder in ("outbox", "receipts", "heartbeat"):
            for path in sorted((self.control / folder).rglob("*.json")):
                found.append({"path": path.relative_to(self.control).as_posix(),
                              "text": path.read_text(encoding="utf-8")})
        return found

    def run(self, *steps: tuple[datetime, str | Sequence[str]]) -> dict[str, Any]:
        """Runs (time, function or functions) steps in order; returns this run's output."""
        job = {"props": self.props, "files": self._files(), "steps": [
            {"at": to_iso(at), "calls": [calls] if isinstance(calls, str) else list(calls)}
            for at, calls in steps
        ]}
        job_file = self.work / "job.json"
        job_file.write_text(json.dumps(job), encoding="utf-8")
        script = self.work / "driver.js"
        script.write_text(_DRIVER, encoding="utf-8")
        harness = GOOGLE / "test" / "harness.js"
        done = subprocess.run([self.node, str(script), str(harness), str(job_file)],
                              capture_output=True, encoding="utf-8", errors="replace",
                              timeout=120, check=False)
        if done.returncode != 0:
            raise AssertionError(f"the real scripts failed: {done.stderr[-4000:]}")
        got = json.loads(done.stdout)
        self.props = got["props"]
        self.mail += got["mail"]
        self.push += got["push"]
        self.digest_day = got["digest_day"]
        self.beat = got["beat"]
        self.results += got["results"]
        self.logs += got["logs"]
        for receipt in got["receipts"]:
            target = self.control / "receipts" / receipt["name"]
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(receipt["text"], encoding="utf-8")
        return got

    def mail_to(self, address: str) -> list[str]:
        return [m["subject"] for m in self.mail if m["to"] == address]


# ----- stand-ins for Windows: the tray icon and the toasts


def colour_of(image: Any) -> str:
    """Which tray colour draw_icon drew: its ring-side fill, the same for any count or mark."""
    size = image.size[0]
    r, g, b = image.getpixel((size // 2, size // 6))[:3]

    def distance(name: str) -> int:
        fill = COLOURS[name].fill
        return (fill[0] - r) ** 2 + (fill[1] - g) ** 2 + (fill[2] - b) ** 2

    return min(COLOURS, key=distance)


class RecordingTray:
    """Stands in for pystray: the factory Tray takes (backend=) and the icon it makes. It keeps
    every tooltip and colour shown, and no icon ever appears."""

    def __init__(self) -> None:
        self.tooltips: list[str] = []
        self.colours: list[str] = []
        self.menu: tuple[Any, ...] = ()
        self._stopped = threading.Event()

    def __call__(self, image: Any, tooltip: str, menu: tuple[Any, ...]) -> RecordingTray:
        self._stopped = threading.Event()
        self.show(image, tooltip, menu)
        return self

    def run(self) -> None:
        self._stopped.wait()

    def stop(self) -> None:
        self._stopped.set()

    def show(self, image: Any, tooltip: str, menu: tuple[Any, ...]) -> None:
        self.tooltips.append(tooltip)
        self.colours.append(colour_of(image))
        self.menu = menu

    @property
    def colour(self) -> str | None:
        return self.colours[-1] if self.colours else None

    @property
    def tooltip(self) -> str | None:
        return self.tooltips[-1] if self.tooltips else None


class RecordingToasts:
    """Stands in for the toaster and for its Windows backend: it keeps what would have shown."""

    def __init__(self) -> None:
        self.shown: list[Any] = []

    def notify(self, message: Any) -> str:
        self.shown.append(message)
        return "shown"

    def show(self, message: Any, on_choice: Callable[[str], None] | None = None) -> None:
        self.shown.append(message)


# ----- waiting for fetches


def wait_for_fetches(timeout: float = 90.0, *, guards: bool = True) -> None:
    """A tick never waits for a fetch, so the simulation does, before it moves the clock: a fetch
    stamped after the clock moved would read as later than it was. BackgroundFetches.start returns
    with its thread running, so every fetch a tick started is listed here. tests/test_e2e_day.py's
    slow-fetch test fails if these threads ever stop being named so. The takeoff of a summary just
    filed is waited for too, and so is a P3 check and P4's lock work. guards: the guards' read of NG
    Jobs too."""
    names = ("ngrunner-fetch-", "ngrunner-takeoff-", "ngrunner-checks-", "ngrunner-lock-")
    names += ("ngrunner-guards-",) if guards else ()
    for thread in threading.enumerate():
        if thread.name.startswith(names):
            thread.join(timeout)
            if thread.is_alive():
                raise AssertionError(f"{thread.name} did not finish within {timeout} s")


# ----- the world


@dataclass
class _Parts:
    """What running_world starts: the servers live as long as the world."""

    studio: Studio
    render: FakeRender
    http: LocalRoutes


class World:
    """One install of NG Runner among fakes, on one fake clock.

    Files: tmp/control, tmp/NG Jobs (with Old inside), tmp/Mozaik Shared and tmp/<data>. Settings
    are built directly and saved, so the supervisor's load_settings reads the same ones.

    Everything NG Runner does happens in step(): the clock moves, Render and the Apps Script do what
    is due, then the supervisor runs what is due. start() brings it up; with none, step() runs
    runs the pipeline's tick and the receipts check alone, through ctx.
    """

    def __init__(self, tmp: Path, parts: _Parts, clock: FakeClock, *, mode: str = "live",
                 data: str = "data", secrets: Mapping[str, str] | None = None,
                 tracker: bool = False, order_flags_on: bool = False) -> None:
        self.tmp = tmp
        self.clock = clock
        self.studio = parts.studio
        self.render = parts.render
        self.http = parts.http
        self.control = tmp / "control"
        self.ng_jobs = tmp / "NG Jobs"
        self.old = self.ng_jobs / "Old"
        self.mozaik = tmp / "Mozaik Shared"
        self.paths = AppPaths(tmp / data)
        for folder in (self.control, self.old, self.mozaik, self.paths.app_data):
            folder.mkdir(parents=True, exist_ok=True)
        self.tracker = tmp / "tracker snapshots"
        self.settings = Settings(ng_jobs=self.ng_jobs, mozaik_shared=self.mozaik,
                                 control=self.control, render_url=RENDER_URL,
                                 studio_url=STUDIO_URL, mode=mode,
                                 backup_public_key=BACKUP_PUBLIC_KEY,
                                 tracker_folder=self.tracker if tracker else None,
                                 order_flags_on=order_flags_on)
        save_settings(self.paths, self.settings)
        self.secrets = MemorySecrets({"signing_key": KEY_HEX, "export_key": EXPORT_KEY,
                                      **(secrets or {})})
        self.script = FakeAppsScript(self.control, clock)
        self.tray = RecordingTray()
        self.toasts = RecordingToasts()
        self.sup: Any = None
        self.site: Any = None           # what Ctx.studio is, in the pipeline on its own
        self.site_http: Any = None
        self.sign_ins: list[str] = []
        self._store: Any = None
        self._ctx: Any = None
        self._messenger: Any = None

    # ----- starting and stopping NG Runner

    def start(self) -> Any:
        """A supervisor on this world's data folder; a second call is a restart's new one."""
        from ngrunner.supervisor import Supervisor

        self.sup = Supervisor(self.paths, clock=self.clock, secrets=self.secrets, http=self.http,
                              tray_backend=self.tray, toaster=self.toasts, studio=self.site)
        self.sup.start()
        return self.sup

    def stop(self) -> None:
        if self.sup is not None:
            sup, self.sup = self.sup, None
            sup.stop()

    def restart(self) -> Any:
        """The supervisor stops, and a new one starts on the same store, as a new process would."""
        self.stop()
        self._ctx = self._messenger = None
        return self.start()

    # ----- the pipeline on its own, for what needs no supervisor

    @property
    def store(self) -> Any:
        if self._store is None:
            from ngrunner.store import open_store

            self._store = open_store(self.paths.db)
        return self._store

    @property
    def roots(self) -> Any:
        return roots(self.settings, self.paths)

    @property
    def actions(self) -> Any:
        """What the board's buttons and the command box call: the supervisor is its BoardActions."""
        return self.sup

    @property
    def messenger(self) -> Any:
        """A messenger of the seam's own shape on this store, for a scenario to send through."""
        if self._messenger is None:
            from ngrunner.messenger import Messenger

            self._messenger = Messenger(self.store, self.settings, self.secrets, self.clock,
                                        self.roots, self.paths)
        return self._messenger

    @property
    def ctx(self) -> Any:
        """The pipeline's context, built directly: a RenderClient with no check of Render's clock
        (a real server's Date is real time, and this day is simulated)."""
        if self._ctx is None:
            from ngrunner.intake.pipeline import Ctx
            from ngrunner.joblist import RenderClient

            client = RenderClient(self.render.base_url, EXPORT_KEY, http=self.http,
                                  clock=self.clock, max_clock_skew=None)
            self._ctx = Ctx(store=self.store, settings=self.settings, secrets=self.secrets,
                            clock=self.clock, roots=self.roots, paths=self.paths,
                            studio_http=self.http, render_client=client,
                            messenger=self.messenger, mode=self.settings.mode, studio=self.site)
        return self._ctx

    # ----- time

    def _run_due(self) -> None:
        if self.sup is not None:
            self.sup.run_due(self.clock.now())
        else:
            from ngrunner.intake import pipeline

            pipeline.tick(self.ctx)
            self.messenger.check_receipts()

    def step(self, seconds: float = 60.0) -> None:
        """One turn of the world: the clock moves, Render and the Apps Script do what is due, NG
        Runner runs what is due, and what it started is waited for, so the next turn is later."""
        self.clock.advance(seconds)
        self.render.on_clock()
        self.script.on_clock()
        self._run_due()
        wait_for_fetches()
        if self.sup is not None:       # the scheduler looks every second: the guards' pass is back
            self.sup.run_due(self.clock.now())
        self.script.deliver()

    def run(self, *, minutes: float, step: float = 60.0) -> None:
        end = self.clock.now() + timedelta(minutes=minutes)
        while self.clock.now() < end:
            self.step(step)

    def run_to(self, when: datetime, *, step: float = 60.0) -> None:
        """Runs until the clock reads when, landing on it exactly."""
        while self.clock.now() < when:
            self.step(min(step, (when - self.clock.now()).total_seconds()))

    def run_until(self, done: Callable[[], bool], *, within: timedelta,
                  step: float = 60.0) -> datetime:
        """Runs until done() holds; returns that time. Fails, saying where things stand, when it
        has not by within from now."""
        limit = self.clock.now() + within
        while not done():
            if self.clock.now() >= limit:
                raise AssertionError(f"not done within {within}:\n{self.where_things_stand()}")
            self.step(step)
        return self.clock.now()

    # ----- what is known

    def rows(self) -> list[Any]:
        from ngrunner.store import list_fab_rows

        return list_fab_rows(self.store)

    def row(self, text: str) -> Any:
        found = [r for r in self.rows() if r.text == text]
        assert found, f"no Fab row reads {text!r}:\n{self.where_things_stand()}"
        return found[-1]

    def cards(self, kind: str | None = None, *, state: str | None = "open") -> list[Any]:
        from ngrunner.store import list_cards

        return [c for c in list_cards(self.store, state=state) if kind in (None, c.kind)]

    def outbox(self, *, to: str | None = None, subject: str | None = None) -> list[Any]:
        """Messages in the store, only those to this role whose subject starts with this."""
        from ngrunner.store import list_outbox

        return [m for m in list_outbox(self.store) if to in (None, m.recipient)
                and (subject is None or m.subject.startswith(subject))]

    def job(self, studio_id: str) -> Any:
        from ngrunner.store import get_job

        return get_job(self.store, studio_id)

    def renders(self, studio_id: str) -> list[Any]:
        from ngrunner.store import list_renders

        return list_renders(self.store, studio_id)

    def dev(self) -> list[Any]:
        from ngrunner.store import list_dev

        return list_dev(self.store)

    def events(self, *, kind: str | None = None) -> list[Any]:
        from ngrunner.store import list_events

        return [e for e in list_events(self.store) if kind in (None, e.kind)]

    def heartbeats(self) -> list[Any]:
        """Every heartbeat in the control folder, opened, oldest first."""
        return sorted((open_envelope(p.read_bytes(), key=KEY, kind="heartbeat")
                       for p in (self.control / "heartbeat").rglob("*.json")),
                      key=lambda envelope: envelope.seq)

    def board(self, *, paused: bool = False) -> Any:
        """The job model's board now, the way the supervisor asks for it."""
        from ngrunner import jobmodel
        from ngrunner.board.views import HealthView

        health = HealthView(paths=(), last_export=None, list_synced_at=None, heartbeat=None,
                            toasts=None)
        return jobmodel.board_view(self.store, self.settings, self.clock, health=health,
                                   paused=paused, shadow=self.settings.mode == "shadow")

    def board_row(self, name: str, *, paused: bool = False) -> Any:
        rows = [r for r in self.board(paused=paused).jobs if r.name == name]
        assert rows, f"no board row is named {name!r}:\n{self.where_things_stand()}"
        return rows[0]

    def tray_state(self, *, unwell: bool = False, paused: bool = False) -> Any:
        from ngrunner import jobmodel

        return jobmodel.tray_state(self.store, self.settings, self.clock, unwell=unwell,
                                   paused=paused, shadow=self.settings.mode == "shadow")

    def where_things_stand(self) -> str:
        """What a failing scenario needs to show: every row, card, message and DEV item."""
        lines = [f"clock: {to_iso(self.clock.now())}"]
        try:
            lines += [f"row {r.text!r}: {r.state} ({r.reason}) job {r.studio_id}"
                      for r in self.rows()]
            lines += [f"card {c.id} {c.kind}: {c.state}" for c in self.cards(state=None)]
            lines += [f"outbox {m.recipient}: {m.subject} [{m.state}]" for m in self.outbox()]
            lines += [f"dev {d.code}: {d.text}" for d in self.dev()]
        except Exception as exc:  # noqa: BLE001 - a report on a failure must not fail itself
            lines.append(f"(the store could not be read: {exc})")
        lines.append(f"studio asked {len(self.studio.asked)} addresses, served"
                     f" {len(self.studio.served)}; sync requests: {len(self.render.sync_requests)}")
        lines += [f"file {path}" for path in sorted(tree(self.ng_jobs))]
        return "\n".join(lines)

    # ----- folders by hand

    def make_folder(self, name: str, *, old: bool = False,
                    summary: bytes | None = None) -> Path:
        """An NG Jobs folder someone made, with a summary in it when one is given."""
        folder = (self.old if old else self.ng_jobs) / name
        folder.mkdir(parents=True, exist_ok=True)
        if summary is not None:
            (folder / f"{name}_summary.pdf").write_bytes(summary)
        return folder


def tree(root: Path) -> dict[str, str]:
    """Every file below root, by relative path, with its sha256: compare two to see what changed."""
    if not root.is_dir():
        return {}
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def filed_paths(folder: str) -> set[str]:
    """The three files a commit leaves in a job folder, as tree() names them."""
    return {f"{folder}/{folder}_summary.pdf", f"{folder}/{folder}_NGRunner.json",
            f"{folder}/{folder}_NGRunner_log.txt"}


@contextmanager
def running_world(tmp: Path, *, mode: str = "live", at: datetime = START, data: str = "data",
                  secrets: Mapping[str, str] | None = None, **settings: Any) -> Iterator[World]:
    """A world with its fakes running, stopped with NG Runner at the end of the block."""
    clock = FakeClock(at)
    with running_studio() as fake, running_render(clock) as render:
        studio = Studio(fake)
        http = LocalRoutes({urlsplit(STUDIO_URL).hostname: studio.base_url,
                            urlsplit(RENDER_URL).hostname: render.base_url})
        world = World(tmp, _Parts(studio, render, http), clock, mode=mode, data=data,
                      secrets=secrets, **settings)
        try:
            yield world
        finally:
            world.stop()
            http.close()
            if world.site_http is not None:
                world.site_http.close()


def with_studio_site(world: World, *, signed_in: bool = True) -> World:
    """The real StudioSite and its real parser for this world's pipeline, over real HTTP to the
    fake Studio. StudioSite asks for Studio's own address, so it gets a session of its own whose one
    route is the fake. It never signs in: signed_in stores a session it can use, and without one it
    finds no login. A sign-in it did try would be noted in sign_ins, and fail."""
    from ngrunner.intake.fetch import STUDIO_URL as STUDIOS_OWN_ADDRESS
    from ngrunner.intake.studio_designs import StudioSite, StudioUnavailable

    def no_sign_in(username: str, password: str) -> dict[str, str]:
        world.sign_ins.append(username)
        raise StudioUnavailable("sign_in_failed", "A test never signs in.")

    if signed_in:
        world.secrets.put("studio_session", json.dumps({"sessionid": "a-test-session",
                                                        "csrftoken": "a-test-token"}))
    world.site_http = LocalRoutes({urlsplit(STUDIOS_OWN_ADDRESS).hostname: world.studio.base_url})
    world.site = StudioSite(world.secrets, world.clock, http=world.site_http, sign_in=no_sign_in)
    return world


def opps(world: World, *listed: Opp) -> None:
    """Puts these Opportunities on Salesforce's list and their reports on Studio."""
    for opp in listed:
        world.render.opportunities.append(opp)
        if opp.studio_id not in world.studio.jobs:
            world.studio.add(opp.studio_id, opp.name, client=opp.client or opp.name.split("_")[0])


def seen_rows(world: World, rows: Iterable[tuple[str, str]] | None = None) -> None:
    """The rows already on the sheet at install: enough that a few new rows are a small change."""
    names = rows if rows is not None else [
        (f"{stem}{ending}", "PE") for stem in ("Sample", "Place") for ending in
        ("ton", "holm", "by", "well", "stead", "ham")]
    for job, designer in names:
        world.script.add_row(job, designer, ("Monday", " 11 /2"))
