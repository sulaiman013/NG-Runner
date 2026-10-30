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
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ngrunner.clock import parse_wall

if TYPE_CHECKING:
    from ngrunner.writes import Roots

APP_NAME = "NGRunner"
PHONES = ("pushover", "slack", "none")
LAYOUTS = ("landscape", "portrait")
ROLES = ("jim", "barbara", "will", "george", "sulaiman", "backup_contact")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
_URL = re.compile(r"https://[a-z0-9.-]+(?::\d{1,5})?/?")


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
    def launcher_state(self) -> Path:
        return self.app_data / "launcher.json"


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
    phone: str = "pushover"              # decision 2: pushover, slack or none
    emails: tuple[tuple[str, str], ...] = ()     # (role, address); roles in ROLES
    sold_stages: tuple[str, ...] = ("Ready to Order", "Ready to Install")  # questionnaire 3a

    def email(self, role: str) -> str | None:
        return dict(self.emails).get(role)


_PATHS = ("ng_jobs", "mozaik_shared", "control")
_WALL_TIMES = ("digest_at", "quiet_from", "quiet_until", "expected_on_from", "expected_on_until")


def load_settings(paths: AppPaths) -> Settings:
    """Read and check settings.json. Anything missing, unknown or malformed is refused."""
    try:
        raw = json.loads(paths.settings.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SettingsError("NG Runner has no settings yet; run its setup first") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SettingsError("NG Runner's settings file can't be read") from exc
    if not isinstance(raw, dict):
        raise SettingsError("NG Runner's settings file is not a set of named settings")
    return settings_from(raw)


def settings_from(raw: Mapping[str, Any]) -> Settings:
    """Settings from plain values (the JSON form), checked field by field."""
    known = {f.name for f in fields(Settings)}
    unknown = sorted(set(raw) - known)
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
    return Settings(**values)


def _choices(raw: Mapping[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if "report_layout" in raw:
        values["report_layout"] = _one_of(raw["report_layout"], LAYOUTS, "report_layout")
    if "phone" in raw:
        values["phone"] = _one_of(raw["phone"], PHONES, "phone")
    if "pc_sleeps_at_night" in raw:
        if not isinstance(raw["pc_sleeps_at_night"], bool):
            raise SettingsError("pc_sleeps_at_night must be true or false")
        values["pc_sleeps_at_night"] = raw["pc_sleeps_at_night"]
    if "emails" in raw:
        values["emails"] = _emails(raw["emails"])
    if "sold_stages" in raw:
        stages = raw["sold_stages"]
        if not (isinstance(stages, list) and stages
                and all(isinstance(s, str) and s.strip() for s in stages)):
            raise SettingsError("sold_stages must be a list of Salesforce stage names")
        values["sold_stages"] = tuple(s.strip() for s in stages)
    return values


def settings_to_json(settings: Settings) -> dict[str, Any]:
    """The JSON form of settings (what save_settings writes)."""
    out = asdict(settings)
    for name in _PATHS:
        out[name] = str(out[name])
    out["emails"] = dict(settings.emails)
    out["sold_stages"] = list(settings.sold_stages)
    return out


def save_settings(paths: AppPaths, settings: Settings) -> None:
    """Write settings.json in NG Runner's own data folder: temp file, then replace."""
    paths.app_data.mkdir(parents=True, exist_ok=True)
    temp = paths.settings.with_name(paths.settings.name + ".tmp")
    temp.write_text(json.dumps(settings_to_json(settings), indent=1, sort_keys=True),
                    encoding="utf-8")
    os.replace(temp, paths.settings)


def roots(settings: Settings, paths: AppPaths) -> Roots:
    """The roots commit_write knows about, from these settings."""
    from ngrunner.writes import Roots

    return Roots(settings.ng_jobs, settings.mozaik_shared, settings.control, paths.app_data)


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


def _emails(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict):
        raise SettingsError("emails must map a role to an address")
    out = []
    for role, address in sorted(value.items()):
        if role not in ROLES:
            raise SettingsError(f"emails: {role!r} is not a known role")
        if not isinstance(address, str) or not _EMAIL.fullmatch(address.strip()):
            raise SettingsError(f"emails: the address for {role} is not an email address")
        out.append((role, address.strip()))
    return tuple(out)
