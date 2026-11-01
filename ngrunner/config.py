r"""
Where NG Runner keeps its own things, and the settings Sulaiman sets at install.

Every path NG Runner uses comes from here and is absolute (AGENTS.md rule 7), so no module
guesses a folder. Its own data folder is %LOCALAPPDATA%\NGRunner (NGR_APP_DATA overrides it, for
tests only). The shared folders it writes through commit_write (NG Jobs and the control folder;
Mozaik Shared from later phases) are settings with no defaults, checked when loaded: a wrong
guess there would file a summary where Jim doesn't look. Settings live in the data folder as
settings.json; secrets never do (Windows Credential Manager holds those).
"""
from __future__ import annotations

import json
import ntpath
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ngrunner.clock import parse_wall

if TYPE_CHECKING:
    from ngrunner.writes import Roots

APP_NAME = "NGRunner"
PHONES = ("pushover", "slack", "none")
LAYOUTS = ("landscape", "portrait")
MODES = ("shadow", "live")
ROLES = ("jim", "barbara", "will", "george", "sulaiman", "backup_contact")
_URL = re.compile(r"https://[a-z0-9.-]+(?::\d{1,5})?/?")
# The public key as backup.new_keypair() prints it. Checked here by its form, as backup.py imports
# this module and the launcher's build leaves cryptography out; tests/test_config.py ties the two.
_BACKUP_KEY = re.compile(r"ngrbak-public-1-[0-9a-f]{64}")


class SettingsError(ValueError):
    """A setting is missing or wrong; the message says which, in plain words."""


# ----- NG Runner's own data folder


@dataclass(frozen=True)
class AppPaths:
    """NG Runner's own data folder and what is in it. Code may write here directly (rule 1)."""

    app_data: Path

    @property
    def db(self) -> Path:
        return self.app_data / "ngrunner.db"

    @property
    def settings(self) -> Path:
        return self.app_data / "settings.json"

    @property
    def logs(self) -> Path:
        return self.app_data / "logs"

    @property
    def renders(self) -> Path:
        """Fetched renders, kept on this PC only: client documents, never synced, never in git."""
        return self.app_data / "renders"

    @property
    def backup(self) -> Path:
        """commit_write's copies of the files it replaced."""
        return self.app_data / "backup"

    @property
    def staging(self) -> Path:
        return self.app_data / "staging"

    @property
    def toolkit(self) -> Path:
        """Copies of Jim's toolkit (toolkit.take_snapshot). A check runs from one, never in place."""
        return self.app_data / "toolkit"

    @property
    def launcher_state(self) -> Path:
        return self.app_data / "launcher.json"

    @property
    def p4_shadow(self) -> Path:
        """Where P4's writes go until P4 goes live: a lease, a tracker batch, a room's fix and a job
        sheet, each into a copy on this PC's own disk (shadow_roots). Never synced, never in git."""
        return self.app_data / "P4 shadow"


def app_paths(environ: Mapping[str, str] = os.environ) -> AppPaths:
    """%LOCALAPPDATA%\\NGRunner, or NGR_APP_DATA when set (tests). Either must be a full path."""
    override = environ.get("NGR_APP_DATA", "").strip()
    if override:
        return AppPaths(_full_path(override, "NGR_APP_DATA"))
    base = environ.get("LOCALAPPDATA", "").strip()
    if not base:
        raise SettingsError("LOCALAPPDATA is not set, so NG Runner has no data folder")
    return AppPaths(_full_path(base, "LOCALAPPDATA") / APP_NAME)


# ----- settings


@dataclass(frozen=True)
class Settings:
    """What Sulaiman sets at install. Paths have no defaults; the rest follow the design."""

    ng_jobs: Path
    mozaik_shared: Path
    control: Path
    render_url: str = "https://icla-assembly-report-app.onrender.com"
    studio_url: str = "https://studio.inspiredclosets.com"
    report_layout: str = "landscape"     # questionnaire item 4
    digest_at: str = "06:30"
    quiet_from: str = "22:00"            # phone quiet hours, except DO NOT CUT (design 4)
    quiet_until: str = "06:00"
    expected_on_from: str = "06:00"      # hours the PC is expected on, for the watchdog
    expected_on_until: str = "23:00"
    pc_sleeps_at_night: bool = False     # the declared "PC sleeps at night" mode
    phone: str = "none"                  # P1a has no push channel (docs/decisions.md)
    sold_stages: tuple[str, ...] = ("Ready to Order", "Ready to Install")  # questionnaire 3a
    mode: str = "shadow"                 # live only by a deliberate switch (integration spec 7)
    backup_public_key: str = ""          # blank: the nightly backup is a board item, not a crash
    # P1b. No default for the folder: with none, the lock pre-flight says it could not verify.
    tracker_folder: Path | None = None   # where Jim's tracker drops its snapshots; only ever read
    order_flag_total: float = 25000      # decision 12c: a heads-up names a total at or above this
    order_flags_on: bool = False         # nothing reaches Barbara or Will until Sulaiman turns it on
    # What load_settings set aside, as (name, value), sorted. No setting of this version: nothing
    # reads, compares or prints it, and save_settings puts it back for the version that knows it.
    set_aside: tuple[tuple[str, Any], ...] = field(default=(), compare=False, repr=False)

    def __post_init__(self) -> None:
        _one_of(self.mode, MODES, "mode")  # here, so replace(settings, mode=...) can't skip it

    def set_aside_note(self) -> str:
        """One line for the supervisor to pass to DEV, naming what load_settings set aside and
        never its values; "" when it set nothing aside."""
        if not self.set_aside:
            return ""
        names = ", ".join(repr(name) for name, _ in self.set_aside)
        return f"The settings file has settings this version does not know, kept unused: {names}"


_PATHS = ("ng_jobs", "mozaik_shared", "control")
_WALL_TIMES = ("digest_at", "quiet_from", "quiet_until", "expected_on_from", "expected_on_until")
_SETTINGS = frozenset(f.name for f in fields(Settings)) - {"set_aside"}


def load_settings(paths: AppPaths) -> Settings:
    """Read and check settings.json. Anything missing or malformed is refused. A setting this
    version doesn't know is set aside instead, with Settings.set_aside_note() saying so: a
    fallback to an older version (launcher.py) must never be refused by the file a newer one
    left."""
    try:
        raw = json.loads(paths.settings.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SettingsError("NG Runner has no settings yet; run its setup first") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SettingsError("NG Runner's settings file can't be read") from exc
    if not isinstance(raw, dict):
        raise SettingsError("NG Runner's settings file is not a set of named settings")
    aside = sorted((name, value) for name, value in raw.items() if name not in _SETTINGS)
    known = {name: value for name, value in raw.items() if name in _SETTINGS}
    return replace(settings_from(known), set_aside=tuple(aside))


def settings_from(raw: Mapping[str, Any]) -> Settings:
    """Settings from plain values (the JSON form), checked field by field."""
    unknown = sorted(set(raw) - _SETTINGS)
    if unknown:
        raise SettingsError(f"unknown setting {unknown[0]!r}")
    missing = [name for name in _PATHS if not raw.get(name)]
    if missing:
        raise SettingsError(f"the {missing[0]} folder is not set")
    values: dict[str, Any] = {name: _full_path(str(raw[name]), name) for name in _PATHS}
    for name in ("render_url", "studio_url"):
        if name in raw:
            values[name] = _url(raw[name], name)
    for name in _WALL_TIMES:
        if name in raw:
            values[name] = _wall(raw[name], name)
    values.update(_choices(raw))
    values.update(_guard_settings(raw))
    return Settings(**values)


def _guard_settings(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Each is optional, as a settings file from before P1b must still load: two failed starts
    retire a version."""
    values: dict[str, Any] = {}
    if raw.get("tracker_folder"):
        values["tracker_folder"] = _full_path(str(raw["tracker_folder"]), "tracker_folder")
    if "order_flag_total" in raw:
        total = raw["order_flag_total"]
        if (isinstance(total, bool) or not isinstance(total, int | float)
                or not 0 < total < float("inf")):
            raise SettingsError("order_flag_total must be a dollar amount above 0")
        values["order_flag_total"] = total
    if "order_flags_on" in raw:
        if not isinstance(raw["order_flags_on"], bool):
            raise SettingsError("order_flags_on must be true or false")
        values["order_flags_on"] = raw["order_flags_on"]
    return values


def _choices(raw: Mapping[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if "report_layout" in raw:
        values["report_layout"] = _one_of(raw["report_layout"], LAYOUTS, "report_layout")
    if "phone" in raw:
        values["phone"] = _one_of(raw["phone"], PHONES, "phone")
    if "mode" in raw:
        values["mode"] = raw["mode"]  # Settings itself checks this one
    if "pc_sleeps_at_night" in raw:
        if not isinstance(raw["pc_sleeps_at_night"], bool):
            raise SettingsError("pc_sleeps_at_night must be true or false")
        values["pc_sleeps_at_night"] = raw["pc_sleeps_at_night"]
    if "backup_public_key" in raw:
        values["backup_public_key"] = _backup_key(raw["backup_public_key"])
    if "sold_stages" in raw:
        stages = raw["sold_stages"]
        if not (isinstance(stages, list) and stages
                and all(isinstance(s, str) and s.strip() for s in stages)):
            raise SettingsError("sold_stages must be a list of Salesforce stage names")
        values["sold_stages"] = tuple(s.strip() for s in stages)
    return values


def settings_to_json(settings: Settings) -> dict[str, Any]:
    """The JSON form of settings, without what load_settings set aside, so showing this can't
    repeat those values."""
    out = {name: getattr(settings, name) for name in _SETTINGS}
    for name in _PATHS:
        out[name] = str(out[name])
    out["sold_stages"] = list(settings.sold_stages)
    out["tracker_folder"] = None if settings.tracker_folder is None else str(settings.tracker_folder)
    return out


def save_settings(paths: AppPaths, settings: Settings) -> None:
    """Write settings.json in NG Runner's own data folder: temp file, then replace. What
    load_settings set aside goes back in, so a fallback to an older version costs nothing."""
    paths.app_data.mkdir(parents=True, exist_ok=True)
    temp = paths.settings.with_name(paths.settings.name + ".tmp")
    values = dict(settings.set_aside) | settings_to_json(settings)
    temp.write_text(json.dumps(values, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(temp, paths.settings)


def roots(settings: Settings, paths: AppPaths) -> Roots:
    """The roots commit_write knows about, from these settings. The tracker's folder is never one
    of them before P4 goes live: settings.tracker_folder is only read (the pre-flight, the after-drop
    step), so a real batch has nowhere to go, and P4's rules refuse it by name besides."""
    from ngrunner.writes import Roots

    return Roots(settings.ng_jobs, settings.mozaik_shared, settings.control, paths.app_data)


SHADOW_PLACES = ("NG Jobs copy", "Mozaik copy", "control copy", "data", "tracker inbox copy")


def shadow_roots(paths: AppPaths) -> Roots:
    """The roots P4's writes go to until P4 goes live: folders of their own in AppPaths.p4_shadow,
    on this PC's own disk, made here when missing. None is named like the real ones (writes.py's P4
    rules refuse a folder named Mozaik Shared or NG Conversion program Claude), and the P4 interlock
    refuses anything below the real folders whatever roots it is handed. A shadow room is NG
    Runner's own copy of a real one (lock.work), so its own data folder may hold it."""
    from ngrunner.writes import Roots

    places = [paths.p4_shadow / name for name in SHADOW_PLACES]
    for place in places:
        place.mkdir(parents=True, exist_ok=True)
    ng_jobs, mozaik, control, data, tracker = places
    return Roots(ng_jobs, mozaik, control, data, tracker=tracker)


# ----- checks


def _full_path(text: str, name: str) -> Path:
    drive, root, _ = ntpath.splitroot(text.strip())
    if not (drive and root) or text.strip().startswith("\\\\.\\"):
        raise SettingsError(f"{name} must be a full path with a drive ({text})")
    return Path(text.strip())


def _url(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _URL.fullmatch(value.strip().lower()):
        raise SettingsError(f"{name} must be an https:// address with no path")
    return value.strip().rstrip("/")


def _wall(value: Any, name: str) -> str:
    try:
        parse_wall(str(value))
    except ValueError as exc:
        raise SettingsError(f"{name} must be a time like 06:30") from exc
    return str(value)


def _one_of(value: Any, allowed: tuple[str, ...], name: str) -> str:
    if value not in allowed:
        raise SettingsError(f"{name} must be one of: {', '.join(allowed)}")
    return value


def _backup_key(value: Any) -> str:
    """The public key, or blank. A refusal never repeats the value: it may be the private key."""
    text = value.strip() if isinstance(value, str) else None
    if text is None or (text and not _BACKUP_KEY.fullmatch(text)):
        raise SettingsError("backup_public_key must be the public key keygen prints, or blank")
    return text
