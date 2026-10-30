"""The support bundle: what Sulaiman needs to help from afar, and nothing he must not receive."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ngrunner import __main__ as cli
from ngrunner import launcher, single, support
from ngrunner.clock import FakeClock, to_iso
from ngrunner.config import AppPaths, Settings, save_settings

NOW = datetime(2026, 10, 29, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    return AppPaths(tmp_path / "NGRunner")


def set_up(paths: AppPaths, tmp_path: Path) -> None:
    save_settings(paths, Settings(
        ng_jobs=tmp_path / "NG Jobs", mozaik_shared=tmp_path / "Mozaik Shared", control=tmp_path / "control",
        emails=(("barbara", "barbara@example.com"), ("jim", "jim@example.com"))))
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
    assert facts["settings"]["emails"] == ["barbara", "jim"]            # which roles have one
    assert facts["database"] == {"schema_version": 3, "tables": {"cards": 0, "jobs": 2}, "problems": []}
    assert facts["launcher"]["versions"]["0.0.1"]["self_test"]["ok"] is True
    assert facts["data_folder"]["db_bytes"] == paths.db.stat().st_size
    assert sorted(files) == ["logs/app-output.log", "logs/launcher.log"]
    assert files["logs/app-output.log"] == b"Testerson_101426 filed\n"


def test_the_bundle_never_holds_an_address_or_the_boards_token(paths, tmp_path):
    set_up(paths, tmp_path)
    token = "Secret-Board-Token-0123456789abcdefghijk"
    single.publish_board(paths, f"http://127.0.0.1:50123/?t={token}")
    data = support.make_bundle(paths, clock=FakeClock(NOW))
    facts, files = unpack(data)
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
