"""Settings and paths: shared folders are never guessed, and a bad setting is refused by name."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ngrunner import backup
from ngrunner.config import (
    AppPaths, SettingsError, app_paths, load_settings, roots, save_settings, settings_from,
    settings_to_json,
)
from ngrunner.messages import lint, templates

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
    assert s.sold_stages == ("Ready to Order", "Ready to Install")
    assert s.render_url == "https://icla-assembly-report-app.onrender.com"


def test_with_no_push_channel_in_p1a_the_phone_setting_defaults_to_none():
    assert settings_from(GOOD).phone == "none"  # docs/decisions.md: the board, tray and digest


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
    ({"sold_stages": []}, "sold_stages"),
])
def test_each_bad_setting_is_refused_by_name(change, words):
    with pytest.raises(SettingsError, match=words):
        settings_from(GOOD | change)


def test_a_new_install_starts_in_shadow_mode():
    """Shadow files nothing into NG Jobs and sends nothing to Barbara or Jim (integration spec
    7), so a settings file that doesn't say is the safe case, never live."""
    assert settings_from(GOOD).mode == "shadow"


def test_live_mode_is_only_ever_what_the_file_says_and_survives_a_save(tmp_path):
    paths = AppPaths(tmp_path / "app")
    save_settings(paths, settings_from(GOOD | {"mode": "live"}))

    assert load_settings(paths).mode == "live"
    assert json.loads(paths.settings.read_text(encoding="utf-8"))["mode"] == "live"


@pytest.mark.parametrize("mode", ["Live", "LIVE", " live", "", "dry run", None, True, ["live"]])
def test_any_other_mode_is_refused_rather_than_guessed(mode):
    with pytest.raises(SettingsError, match="mode must be one of: shadow, live"):
        settings_from(GOOD | {"mode": mode})


def test_a_mode_can_not_get_in_around_the_settings_check():
    """The switch on the board saves replace(settings, mode=...), which skips settings_from."""
    with pytest.raises(SettingsError, match="mode"):
        replace(settings_from(GOOD), mode="Live")


def test_with_no_backup_key_the_settings_still_load_with_it_blank():
    """Blank is not a refusal: the nightly backup becomes a board item, never a crash."""
    assert settings_from(GOOD).backup_public_key == ""
    assert settings_from(GOOD | {"backup_public_key": "  "}).backup_public_key == ""


def test_the_public_key_keygen_prints_is_kept_and_is_the_one_the_backup_uses(tmp_path):
    _, public = backup.new_keypair()
    paths = AppPaths(tmp_path / "app")

    save_settings(paths, settings_from(GOOD | {"backup_public_key": f" {public}\n"}))

    kept = load_settings(paths).backup_public_key
    assert kept == public and backup.key_id(kept) == backup.key_id(public)


@pytest.mark.parametrize("key", [
    "ngrbak-public-1-" + "ab" * 31,                # too short
    "ngrbak-public-1-" + "AB" * 32,                # not as keygen prints it
    "ngrbak-public-2-" + "ab" * 32,                # a form this version can't use
    "not a key",
    None,
    1234,
], ids=["short", "capitals", "other form", "words", "null", "number"])
def test_anything_else_is_refused_by_name_without_repeating_it(key):
    with pytest.raises(SettingsError, match="backup_public_key must be") as refused:
        settings_from(GOOD | {"backup_public_key": key})

    assert str(key) not in str(refused.value)


def test_the_private_key_never_gets_into_the_settings_or_into_the_refusal():
    """The support bundle shows the settings, and the private key belongs off Jim's PC."""
    private, _ = backup.new_keypair()

    with pytest.raises(SettingsError, match="backup_public_key must be") as refused:
        settings_from(GOOD | {"backup_public_key": private})

    assert private not in str(refused.value)


def test_settings_round_trip_through_the_data_folder(tmp_path):
    paths = AppPaths(tmp_path / "app")
    s = settings_from(GOOD | {"phone": "slack"})
    save_settings(paths, s)
    assert load_settings(paths) == s
    assert json.loads(paths.settings.read_text(encoding="utf-8"))["ng_jobs"] == GOOD["ng_jobs"]


def test_settings_hold_no_email_addresses_as_no_address_is_ever_written_into_a_file(tmp_path):
    """Roles are resolved to addresses by the Apps Script's own settings (build-spec section 3),
    so settings have no place for one."""
    paths = AppPaths(tmp_path / "app")
    save_settings(paths, settings_from(GOOD))

    assert "emails" not in json.loads(paths.settings.read_text(encoding="utf-8"))
    assert not hasattr(settings_from(GOOD), "emails")
    with pytest.raises(SettingsError, match="unknown setting 'emails'"):
        settings_from(GOOD | {"emails": {"barbara": "barbara@example.com"}})


# ----- a settings file from a newer version: launcher.py can fall back to an older one


NEWER = GOOD | {"night_build": {"from": "01:30"}, "drawing_reader": "on"}


def _file_holding(tmp_path: Path, raw: dict) -> AppPaths:
    paths = AppPaths(tmp_path / "app")
    paths.app_data.mkdir()
    paths.settings.write_text(json.dumps(raw), encoding="utf-8")
    return paths


def test_a_newer_versions_settings_file_loads_with_what_this_version_knows(tmp_path):
    settings = load_settings(_file_holding(tmp_path, NEWER | {"mode": "live"}))

    assert settings.ng_jobs == Path(GOOD["ng_jobs"]) and settings.mode == "live"


def test_what_was_set_aside_makes_one_note_naming_it_but_never_saying_its_value(tmp_path):
    note = load_settings(_file_holding(tmp_path, NEWER)).set_aside_note()

    assert "'drawing_reader', 'night_build'" in note  # sorted, so a repeat is the same DEV item
    assert "01:30" not in note


def test_with_nothing_set_aside_there_is_no_note(tmp_path):
    assert load_settings(_file_holding(tmp_path, GOOD)).set_aside_note() == ""
    assert settings_from(GOOD).set_aside_note() == ""


def test_the_note_stays_on_one_line_whatever_a_name_holds(tmp_path):
    note = load_settings(_file_holding(tmp_path, GOOD | {"odd\nname": 1})).set_aside_note()

    assert "\n" not in note and "odd" in note


def test_the_note_reaches_sulaiman_as_written_in_a_dev_message(tmp_path):
    note = load_settings(_file_holding(tmp_path, NEWER)).set_aside_note()
    now = datetime(2026, 10, 30, 22, 0, tzinfo=timezone.utc)

    message = templates.dev([templates.DevLine(now, note)], day=now.date())

    assert lint.lint(message) == [] and "'night_build'" in message.lines[0]


def test_what_was_set_aside_goes_back_into_the_file_on_the_next_save(tmp_path):
    """Without it, a switch of mode on the older version would cost the newer one its setting."""
    paths = _file_holding(tmp_path, NEWER)
    before = load_settings(paths)

    save_settings(paths, replace(before, mode="live"))

    saved = json.loads(paths.settings.read_text(encoding="utf-8"))
    assert saved["night_build"] == {"from": "01:30"} and saved["drawing_reader"] == "on"
    assert saved["mode"] == "live" and "set_aside" not in saved
    assert load_settings(paths).set_aside_note() == before.set_aside_note() != ""


def test_what_was_set_aside_is_not_in_the_json_form_other_code_shows(tmp_path):
    shown = settings_to_json(load_settings(_file_holding(tmp_path, NEWER)))

    assert "night_build" not in shown and "set_aside" not in shown


@pytest.mark.parametrize("change, words", [
    ({"mode": "Live"}, "mode must be one of"),
    ({"ng_jobs": ""}, "ng_jobs folder is not set"),
], ids=["a bad mode", "no NG Jobs"])
def test_a_setting_this_version_does_know_is_still_checked_in_a_newer_file(tmp_path, change, words):
    with pytest.raises(SettingsError, match=words):
        load_settings(_file_holding(tmp_path, NEWER | change))


def test_a_file_can_not_fill_in_what_was_set_aside_by_its_field_name(tmp_path):
    """set_aside is where load_settings keeps things, and no setting of its own."""
    with pytest.raises(SettingsError, match="unknown setting 'set_aside'"):
        settings_from(GOOD | {"set_aside": [["night_build", "off"]]})

    settings = load_settings(_file_holding(tmp_path, GOOD | {"set_aside": "odd"}))

    assert settings.set_aside == (("set_aside", "odd"),)


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


# ----- P1b's settings


def _p1b_raw(**more):
    return {"ng_jobs": r"C:\NG Jobs", "mozaik_shared": r"C:\Mozaik Shared", "control": r"C:\control",
            **more}


def test_p1b_settings_default_so_a_settings_file_from_before_them_still_loads():
    settings = settings_from(_p1b_raw())
    assert settings.tracker_folder is None and settings.order_flag_total == 25000
    assert settings.order_flags_on is False


def test_the_tracker_folder_is_a_full_path_when_given():
    settings = settings_from(_p1b_raw(tracker_folder=r"C:\tracker"))
    assert str(settings.tracker_folder) == r"C:\tracker"
    with pytest.raises(SettingsError, match="tracker_folder"):
        settings_from(_p1b_raw(tracker_folder="relative"))
    assert settings_from(_p1b_raw(tracker_folder=None)).tracker_folder is None
    assert settings_from(_p1b_raw(tracker_folder="")).tracker_folder is None


@pytest.mark.parametrize("bad", ["25000", 0, -1, True, None, float("inf")])
def test_the_order_flag_total_is_a_positive_number(bad):
    with pytest.raises(SettingsError, match="order_flag_total"):
        settings_from(_p1b_raw(order_flag_total=bad))
    assert settings_from(_p1b_raw(order_flag_total=30000.5)).order_flag_total == 30000.5


@pytest.mark.parametrize("bad", ["yes", 1, None])
def test_order_flags_on_is_true_or_false(bad):
    with pytest.raises(SettingsError, match="order_flags_on"):
        settings_from(_p1b_raw(order_flags_on=bad))
    assert settings_from(_p1b_raw(order_flags_on=True)).order_flags_on is True


def test_p1b_settings_survive_a_save_and_a_load(tmp_path):
    paths = AppPaths(tmp_path)
    saved = settings_from(_p1b_raw(tracker_folder=r"C:\tracker", order_flag_total=30000,
                                   order_flags_on=True))
    save_settings(paths, saved)
    assert load_settings(paths) == saved
    save_settings(paths, settings_from(_p1b_raw()))
    assert load_settings(paths).tracker_folder is None
