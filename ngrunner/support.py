r"""
The support bundle: one zip file with what Sulaiman needs to help from afar (spec 6.7).

Why: Sulaiman supports NG Runner remotely and part-time, so when something goes wrong he first
needs the state of Jim's install without a remote session: the versions, the settings, the
database's schema version and row counts, the launcher's record and the recent logs. The
tray's "Send support bundle" makes one, and so does NGRunner.exe support-bundle.

What it never holds: secrets (they live in Windows Credential Manager, which it never reads),
email addresses (the settings show only which roles have one), the board's per-launch token
(board.json is never read), and any row of the database (counts only). Logs hold job names
but never addresses (design section 6, "Logs"), and only the end of each recent log is kept,
so a bundle stays small. Nothing it reads can make it fail: a missing or damaged part is
described in the bundle instead.
"""
from __future__ import annotations

import io
import json
import os
import platform
import stat
import sys
import zipfile
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from ngrunner.backup import BackupError, describe, snapshot
from ngrunner.clock import Clock, to_iso, utc
from ngrunner.config import AppPaths, SettingsError, load_settings, settings_to_json

# The pinned libraries (pyproject.toml), by distribution name.
PACKAGES = ("pypdf", "flask", "waitress", "requests", "pystray", "pillow", "psutil", "pywin32",
            "windows-toasts", "cryptography", "tzdata")
MAX_LOG_BYTES = 200_000
MAX_LOGS = 8
_UNKNOWN = "could not verify"


def bundle_name(now: datetime) -> str:
    return f"support-{utc(now):%Y%m%d-%H%M%S}.zip"


def make_bundle(paths: AppPaths, *, clock: Clock, max_log_bytes: int = MAX_LOG_BYTES,
                max_logs: int = MAX_LOGS) -> bytes:
    """The bundle as zip bytes: bundle.json, and logs/<name> for the newest logs."""
    now = clock.now()
    facts = {
        "made_at": to_iso(now),
        "app_version": _app_version(),
        "python": sys.version,
        "windows": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "utf8_mode": bool(sys.flags.utf8_mode),
        "packages": _packages(),
        "settings": _settings(paths),
        "database": _database(paths),
        "launcher": _launcher(paths),
        "data_folder": _data_folder(paths),
    }
    stamp = utc(now).timetuple()[:6]  # a zip time has no zone: this one is UTC
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        text = json.dumps(facts, indent=1, sort_keys=True)
        _add(bundle, "bundle.json", text.encode("utf-8"), stamp)
        for log in _recent_logs(paths.logs, max_logs):
            _add(bundle, f"logs/{log.name}", _tail(log, max_log_bytes), stamp)
    return buffer.getvalue()


def _add(bundle: zipfile.ZipFile, name: str, data: bytes, stamp: tuple[int, ...]) -> None:
    info = zipfile.ZipInfo(name, date_time=stamp)
    info.compress_type = zipfile.ZIP_DEFLATED
    bundle.writestr(info, data)


# ----- what goes in


def _app_version() -> str:
    from ngrunner.launcher import app_version

    try:
        return app_version()
    except (OSError, ValueError, KeyError):
        return _UNKNOWN


def _packages() -> dict[str, str]:
    found = {}
    for name in PACKAGES:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            found[name] = _UNKNOWN
    return found


def _settings(paths: AppPaths) -> dict[str, Any]:
    try:
        settings = load_settings(paths)
    except SettingsError as exc:
        return {"problem": str(exc)}
    shown = settings_to_json(settings)
    shown["emails"] = sorted(dict(settings.emails))  # which roles have one, never the address
    return shown


def _database(paths: AppPaths) -> dict[str, Any]:
    if not paths.db.exists():
        return {"schema_version": None, "tables": {},
                "problems": ["NG Runner has no database yet."]}
    try:
        facts = describe(snapshot(paths.db))
    except BackupError as exc:
        return {"schema_version": None, "tables": {}, "problems": [str(exc)]}
    return {"schema_version": facts.schema_version, "tables": facts.tables,
            "problems": list(facts.problems)}


def _launcher(paths: AppPaths) -> dict[str, Any]:
    from ngrunner.launcher import LauncherStateError, state

    try:
        return state(paths)
    except LauncherStateError as exc:
        return {"problem": str(exc)}


def _data_folder(paths: AppPaths) -> dict[str, Any]:
    return {
        "db_bytes": _size(paths.db),
        "wal_bytes": _size(paths.db.with_name(paths.db.name + "-wal")),
        "backup_copies": _count(paths.backup),
        "renders": _count(paths.renders),
        "support_bundles": _count(paths.app_data / "support"),
    }


def _size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _count(folder: Path) -> int:
    try:
        return sum(1 for _ in os.scandir(folder))
    except OSError:
        return 0


def _recent_logs(folder: Path, limit: int) -> list[Path]:
    """The newest plain files directly in the logs folder (never through a link)."""
    try:
        entries = [e for e in os.scandir(folder) if e.is_file(follow_symlinks=False)]
    except OSError:
        return []
    found = []
    for entry in entries:
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if not st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            found.append((st.st_mtime, Path(entry.path)))
    return [path for _, path in sorted(found, reverse=True)[:limit]]


def _tail(path: Path, limit: int) -> bytes:
    """The end of a log, at most limit bytes, with a first line saying how much is left out."""
    try:
        with open(path, "rb") as log:
            size = os.fstat(log.fileno()).st_size
            if size <= limit:
                return log.read()
            log.seek(size - limit)
            kept = log.read(limit)
    except OSError as exc:
        return f"[this log could not be read: {exc.strerror or exc}]\n".encode("utf-8")
    return f"[the first {size - limit:,} bytes are left out]\n".encode("utf-8") + kept
