"""Settings and paths: shared folders are never guessed, and a bad setting is refused by name."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ngrunner.config import (
    AppPaths, SettingsError, app_paths, load_settings, roots, save_settings, settings_from,
)

GOOD = {"ng_jobs": r"G:\Shared\NG Jobs", "mozaik_shared": r"C:\Users\x\OneDrive - Co\Mozaik Shared",
        "control": r"G:\My Drive\NG Runner control"}


def test_the_data_folder_is_localappdata_unless_a_full_override_is_given():
    assert app_paths({"LOCALAPPDATA": r"C:\Users\x\AppData\Local"}).app_data == Path(
        r"C:\Users\x\AppData\Local\NGRunner")
    assert app_paths({"NGR_APP_DATA": r"D:\t\app", "LOCALAPPDATA": r"C:\L"}).app_data == Path(r"D:\t\app")
    for env in ({"NGR_APP_DATA": r"relative\app"}, {"LOCALAPPDATA": ""}, {},
                {"NGR_APP_DATA": "\\\\.\\C:\\x"}):
        with pytest.raises(SettingsError):
            app_paths(env)


def test_good_settings_load_with_the_design_defaults():
    s = settings_from(GOOD)
    assert s.ng_jobs == Path(GOOD["ng_jobs"]) and s.report_layout == "landscape"
    assert (s.digest_at, s.quiet_from, s.quiet_until) == ("06:30", "22:00", "06:00")
    assert s.sold_stages == ("Ready to Order", "Ready to Install") and s.phone == "pushover"
    assert s.render_url == "https://icla-assembly-report-app.onrender.com"


@pytest.mark.parametrize("change, words", [
    ({"ng_jobs": ""}, "ng_jobs folder is not set"),
    ({"control": r"relative\control"}, "full path"),
    ({"surprise": 1}, "unknown setting"),
    ({"render_url": "http://example.com"}, "https://"),
    ({"studio_url": "https://example.com/path"}, "no path"),
    ({"report_layout": "Landscape"}, "report_layout"),
    ({"phone": "sms"}, "phone"),
    ({"digest_at": "6.30"}, "digest_at"),
    ({"pc_sleeps_at_night": "yes"}, "true or false"),
    ({"emails": {"barbara": "not an address"}}, "not an email"),
    ({"emails": {"stranger": "someone@example.com"}}, "not a known role"),
    ({"sold_stages": []}, "sold_stages"),
])
def test_each_bad_setting_is_refused_by_name(change, words):
    with pytest.raises(SettingsError, match=words):
        settings_from(GOOD | change)


def test_settings_round_trip_through_the_data_folder(tmp_path):
    paths = AppPaths(tmp_path / "app")
    s = settings_from(GOOD | {"emails": {"barbara": "barbara@example.com"}, "phone": "slack"})
    save_settings(paths, s)
    assert load_settings(paths) == s and s.email("barbara") == "barbara@example.com"
    assert json.loads(paths.settings.read_text(encoding="utf-8"))["ng_jobs"] == GOOD["ng_jobs"]


def test_missing_or_broken_settings_say_so(tmp_path):
    paths = AppPaths(tmp_path)
    with pytest.raises(SettingsError, match="no settings yet"):
        load_settings(paths)
    paths.settings.write_text("{not json", encoding="utf-8")
    with pytest.raises(SettingsError, match="can't be read"):
        load_settings(paths)


def test_roots_come_from_the_settings_and_the_data_folder(tmp_path):
    r = roots(settings_from(GOOD), AppPaths(tmp_path))
    assert (r.ng_jobs, r.control, r.app_data) == (Path(GOOD["ng_jobs"]), Path(GOOD["control"]), tmp_path)
