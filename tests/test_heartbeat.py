"""The heartbeat: every 5 minutes, proof that NG Runner is alive and what is at risk, for the
Apps Script's watchdog (design section 4, "Silence and failure")."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from ngrunner.heartbeat import heartbeat_file, payload
from ngrunner.signing import key_from_text, open_envelope

KEY = key_from_text("00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff")
NOW = datetime(2026, 10, 31, 5, 0, tzinfo=timezone.utc)      # Fri 10/30 10:00 PM Pacific
SYNCED = datetime(2026, 10, 31, 4, 49, tzinfo=timezone.utc)


def beat(**change):
    args = {"app_version": "0.1.0", "paused": False,
            "open_risks": ("Testerson_101426: second fetch due at 10:10 PM",),
            "last_fab_seq": 42, "list_synced_at": SYNCED, "pending": 2, "dev_open": 1} | change
    return payload(**args)


def test_the_payload_has_exactly_the_fields_the_watchdog_reads():
    assert beat() == {
        "app_version": "0.1.0", "paused": False,
        "open_risks": ["Testerson_101426: second fetch due at 10:10 PM"],
        "last_fab_seq": 42, "list_synced_at": "2026-10-31T04:49:00+00:00", "pending": 2,
        "dev_open": 1,
    }
    json.dumps(beat())                                  # plain JSON all through


def test_what_is_not_known_yet_is_null():
    got = beat(last_fab_seq=None, list_synced_at=None, open_risks=())
    assert (got["last_fab_seq"], got["list_synced_at"], got["open_risks"]) == (None, None, [])


def test_a_risk_whose_words_fail_the_lint_is_counted_not_shown():
    # The watchdog may show the risks to Jim ("Not checked: ..."), so each must read cleanly.
    got = beat(open_risks=("Testerson_101426: second fetch due at 10:10 PM",
                           "Fakeley_102826: file at G:\\NG Jobs\\x is held",
                           "Mockford_101426: render 3f3f3f3f3f3f3f3f is held"))
    assert got["open_risks"] == ["Testerson_101426: second fetch due at 10:10 PM",
                                 "2 more open risks are on the board."]


@pytest.mark.parametrize("change", [
    {"app_version": ""}, {"app_version": 1}, {"paused": "no"}, {"pending": -1},
    {"pending": True}, {"dev_open": 1.5}, {"last_fab_seq": True}, {"last_fab_seq": -3},
    {"list_synced_at": datetime(2026, 10, 31, 4, 49)}, {"open_risks": "one risk"},
    {"open_risks": (3,)},
])
def test_a_wrong_value_is_refused_not_sent(change):
    with pytest.raises((TypeError, ValueError)):
        beat(**change)


def test_a_heartbeat_file_is_signed_and_filed_by_its_pacific_day_and_number():
    parts, data = heartbeat_file(beat(), key=KEY, seq=7, now=NOW)
    assert parts == ("heartbeat", "20261030", "7.json")       # 10/30 in Pacific, not UTC's 10/31
    envelope = open_envelope(data, key=KEY, kind="heartbeat")
    assert (envelope.seq, envelope.issued_at, envelope.payload) == (7, NOW, beat())


def test_each_heartbeat_file_has_its_own_id():
    first = open_envelope(heartbeat_file(beat(), key=KEY, seq=7, now=NOW)[1], key=KEY)
    second = open_envelope(heartbeat_file(beat(), key=KEY, seq=8, now=NOW)[1], key=KEY)
    assert first.id != second.id


def test_a_heartbeat_file_is_one_commit_write_allows_into_the_control_folder(tmp_path):
    # Wave 2 writes it; this proves the path and bytes fit the chokepoint's P1a rules.
    from ngrunner.writes import FileWrite, Roots, commit_write

    roots = Roots(tmp_path / "NG Jobs", tmp_path / "Mozaik Shared", tmp_path / "control",
                  tmp_path / "appdata")
    for folder in (roots.ng_jobs, roots.mozaik_shared, roots.control, roots.app_data):
        folder.mkdir()
    parts, data = heartbeat_file(beat(), key=KEY, seq=1, now=NOW)
    result = commit_write([FileWrite(roots.control.joinpath(*parts), data, None)], roots=roots,
                          interlock=lambda paths: None)
    assert result.ok, result.reason


@pytest.mark.parametrize("seq", [0, True, "7", 7.0])
def test_a_heartbeat_number_that_is_not_a_whole_number_from_1_is_refused(seq):
    with pytest.raises(ValueError):
        heartbeat_file(beat(), key=KEY, seq=seq, now=NOW)


def test_open_risks_may_come_from_any_iterable():
    assert beat(open_risks=iter(["Testerson_101426: filing in flight"]))["open_risks"] == [
        "Testerson_101426: filing in flight"]
