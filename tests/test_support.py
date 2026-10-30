"""What Sulaiman needs to help from afar: the support bundle, and nothing he must not receive;
and the runbook, which must name every P1a alarm as it reads."""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ngrunner import __main__ as cli
from ngrunner import launcher, single, support
from ngrunner.clock import FakeClock, to_iso
from ngrunner.config import AppPaths, Settings, save_settings
from ngrunner.messages import templates

NOW = datetime(2026, 10, 29, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    return AppPaths(tmp_path / "NGRunner")


def set_up(paths: AppPaths, tmp_path: Path) -> None:
    save_settings(paths, Settings(ng_jobs=tmp_path / "NG Jobs", control=tmp_path / "control",
                                  mozaik_shared=tmp_path / "Mozaik Shared"))
    with sqlite3.connect(paths.db) as con:
        con.execute("CREATE TABLE jobs(studio_id TEXT PRIMARY KEY, name TEXT NOT NULL)")
        con.executemany("INSERT INTO jobs VALUES (?, ?)", [("1001", "Testerson_101426"), ("1002", "Dummitt_110226")])
        con.execute("CREATE TABLE cards(id TEXT PRIMARY KEY)")
        con.execute("PRAGMA user_version = 3")
    con.close()
    paths.logs.mkdir(parents=True)
    (paths.logs / "launcher.log").write_text("2026-10-29T19:00:00+00:00 started 0.0.1\n", encoding="utf-8")
    (paths.logs / "app-output.log").write_bytes(b"Testerson_101426 filed\n")
    launcher.record_self_test(paths, "0.0.1", [], clock=FakeClock(NOW))


def unpack(data: bytes) -> tuple[dict, dict[str, bytes]]:
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        files = {name: bundle.read(name) for name in bundle.namelist()}
    return json.loads(files.pop("bundle.json")), files


def test_the_bundle_holds_versions_settings_schema_counts_and_recent_logs(paths, tmp_path):
    set_up(paths, tmp_path)
    facts, files = unpack(support.make_bundle(paths, clock=FakeClock(NOW)))
    assert facts["made_at"] == to_iso(NOW) and facts["app_version"] == launcher.app_version()
    assert facts["packages"]["cryptography"] == "50.0.2" and facts["packages"]["pypdf"] == "6.21.4"
    assert facts["settings"]["ng_jobs"] == str(tmp_path / "NG Jobs")
    assert facts["settings"]["mode"] == "shadow" and "emails" not in facts["settings"]
    assert facts["settings"]["set_aside"] == []
    assert facts["database"] == {"schema_version": 3, "tables": {"cards": 0, "jobs": 2}, "problems": []}
    assert facts["launcher"]["versions"]["0.0.1"]["self_test"]["ok"] is True
    assert facts["data_folder"]["db_bytes"] == paths.db.stat().st_size
    assert sorted(files) == ["logs/app-output.log", "logs/launcher.log"]
    assert files["logs/app-output.log"] == b"Testerson_101426 filed\n"


def test_the_bundle_never_holds_an_address_or_the_boards_token(paths, tmp_path):
    set_up(paths, tmp_path)
    settings = json.loads(paths.settings.read_text(encoding="utf-8"))
    settings["emails"] = {"barbara": "barbara@example.com"}  # nothing writes one: a hand edit
    paths.settings.write_text(json.dumps(settings), encoding="utf-8")
    token = "Secret-Board-Token-0123456789abcdefghijk"
    single.publish_board(paths, f"http://127.0.0.1:50123/?t={token}")
    data = support.make_bundle(paths, clock=FakeClock(NOW))
    facts, files = unpack(data)
    assert facts["settings"]["set_aside"] == ["emails"]                 # by name, never its value
    everything = json.dumps(facts).encode("utf-8") + b"".join(files.values())
    for secret in (b"@example.com", token.encode("utf-8")):
        assert secret not in everything and secret not in data


def test_a_missing_or_damaged_database_and_settings_are_reported_never_raised(paths):
    paths.app_data.mkdir(parents=True)
    paths.db.write_bytes(b"SQLite format 3\x00" + bytes(500))
    paths.launcher_state.write_text("{ damaged", encoding="utf-8")
    facts, files = unpack(support.make_bundle(paths, clock=FakeClock(NOW)))
    assert facts["settings"] == {"problem": "NG Runner has no settings yet; run its setup first"}
    assert facts["database"]["problems"] and facts["database"]["schema_version"] is None
    assert facts["launcher"] == {"problem": "NG Runner's start-up record is damaged"}
    assert files == {}


def test_only_the_end_of_a_long_log_is_kept(paths):
    paths.logs.mkdir(parents=True)
    (paths.logs / "app-output.log").write_bytes(b"START\n" + b"x" * 50_000 + b"\nEND\n")
    _, files = unpack(support.make_bundle(paths, clock=FakeClock(NOW), max_log_bytes=1_000))
    kept = files["logs/app-output.log"]
    assert kept.endswith(b"\nEND\n") and b"START" not in kept and len(kept) <= 1_100
    assert kept.startswith(b"[the first 49,011 bytes are left out]\n")


def test_only_the_newest_logs_are_kept(paths):
    paths.logs.mkdir(parents=True)
    for n in range(12):
        log = paths.logs / f"day-{n:02d}.log"
        log.write_text(f"day {n}\n", encoding="utf-8")
        stamp = NOW.timestamp() - (12 - n) * 3600
        os.utime(log, (stamp, stamp))
    _, files = unpack(support.make_bundle(paths, clock=FakeClock(NOW), max_logs=3))
    assert sorted(files) == ["logs/day-09.log", "logs/day-10.log", "logs/day-11.log"]


def test_bundles_are_named_by_their_utc_time():
    assert support.bundle_name(NOW) == "support-20261029-200000.zip"


def test_the_command_line_writes_the_bundle_into_the_data_folder(paths, tmp_path):
    set_up(paths, tmp_path)
    out = io.StringIO()
    environ = {"NGR_APP_DATA": str(paths.app_data)}
    assert cli.main(["support-bundle"], out=out, environ=environ, clock=FakeClock(NOW)) == 0
    assert cli.main(["support-bundle"], out=out, environ=environ, clock=FakeClock(NOW)) == 0
    written = sorted((paths.app_data / "support").iterdir())
    assert [p.name for p in written] == ["support-20261029-200000-2.zip", "support-20261029-200000.zip"]
    assert out.getvalue().splitlines()[0] == f"Support bundle written: {written[1]}"
    assert unpack(written[1].read_bytes())[0]["made_at"] == to_iso(NOW)


# ----- the runbook (spec 6.7): every P1a alarm with its one-line fix

ROOT = Path(__file__).resolve().parent.parent
RUNBOOK = ROOT / "docs" / "p1a" / "runbook.md"


def _alarm_rows() -> list[str]:
    """The rows of the runbook's alarm table: one per alarm, its fix in the last cell."""
    text = RUNBOOK.read_text(encoding="utf-8")
    table = text[text.index("## Alarms, and their one-line fixes"):text.index("## Update and rollback")]
    rows = [line for line in table.splitlines() if line.startswith("| ")]
    return rows[1:]                      # the first is the table's heading


def _watchdog_alerts() -> list[str]:
    source = (ROOT / "google" / "Watchdog.gs").read_text(encoding="utf-8")
    return re.findall(r"alert_\('([^']+)'", source) + re.findall(r"subject: '([^']+)'", source)


@pytest.mark.parametrize("words", [
    *templates.ALARMS.values(),
    templates.stuck("<Job>"),
    templates.cant_send(since=NOW, now=NOW, waiting=0).title,
    templates.QUESTIONS["fetch_failed"],
    "failed to start twice",
    "No version of NG Runner could start",
    "start-up record is damaged",
    "NG Runner's database is damaged",
    "last used by a newer version of NG Runner",
    "NG Runner has no settings yet",
    "is not reachable right now",
    "refused NG Runner's export key",
    'daily "ran" line',
])
def test_the_runbook_names_every_p1a_alarm_as_it_reads(words):
    assert any(words in row for row in _alarm_rows()), words


def test_the_runbook_names_every_alert_the_watchdog_sends():
    alerts = _watchdog_alerts()
    assert {"NG Runner is not checking in", "No list reached Jim", "No list today"} <= set(alerts)
    for alert in alerts:
        assert any(alert in row for row in _alarm_rows()), alert


def test_every_alarm_row_says_who_sees_it_and_gives_a_one_line_fix():
    rows = _alarm_rows()
    assert len(rows) >= 20
    for row in rows:
        cells = [cell.strip() for cell in row.strip("|").split(" | ")]
        assert len(cells) == 3 and all(cells), row


@pytest.mark.parametrize("heading", ["## Start, stop, pause", "## Falling back to chat", "## Update and rollback",
                                     "## Restore onto a new PC", "## Key rotation", "## Deleting fixtures"])
def test_the_runbook_has_every_section_the_spec_asks_for(heading):
    assert heading in RUNBOOK.read_text(encoding="utf-8")


def test_the_runbook_calls_the_hosted_app_what_the_glossary_calls_it():
    # In this repo a render is one download of an IC summary (GLOSSARY.md); the hosted app is the
    # online Assembly Reports, and "Render" names only the service whose settings hold its key.
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "the job list from Render" not in text and "the online Assembly Reports" in text
