r"""
The release manifest: every file of a release folder with its sha256, written at build time.

Why: Sulaiman carries each release to Jim's PC over a remote session, and a copy can arrive
damaged or incomplete. install.ps1 refuses a release whose files don't match manifest.json
exactly (a changed, missing or extra file), and Sulaiman can pin the manifest itself by its
sha256 (install.ps1 -ManifestSha256), which ngrunner.spec prints after each build (design
section 6, "Updates": the update script hash-checks its local copy).

A release folder holds app\ (NGRunner.exe and its _internal\), launcher\ (NGRunnerLauncher.exe
and its own _internal\), installer\ (the scripts and task XML below) and manifest.json. This
module is used by ngrunner.spec and by the tests; install.ps1 checks the same format in
PowerShell, since Jim's PC has no Python.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path, PurePosixPath

FORMAT = 1
APP_NAME = "NGRunner"
MANIFEST = "manifest.json"
FOLDERS = ("app", "launcher", "installer")
# The installer files a release carries, below installer\ in the repo and in the release.
INSTALLER_FILES = ("install.ps1", "uninstall.ps1", "common.ps1", "tasks/NGRunner-logon.xml",
                   "tasks/NGRunner-ensure.xml")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}")  # as ngrunner.launcher reads app-<version>


def copy_installer(source: Path, release: Path) -> None:
    """Copy the installer files from the repo's installer\\ into release\\installer\\."""
    for name in INSTALLER_FILES:
        target = release / "installer" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)


def write(release: Path, version: str, *, built_at: str, python: str, pyinstaller: str) -> Path:
    """Write release\\manifest.json for what is in release now; returns its path."""
    if not _VERSION.fullmatch(version):
        raise ValueError(f"{version!r} is not a version like 0.1.2 (the launcher reads only those)")
    files = _hashes(release)
    missing = [f for f in FOLDERS if not any(name.startswith(f + "/") for name in files)]
    if missing:
        raise ValueError(f"the release has no {missing[0]} folder")
    manifest = {"app": APP_NAME, "format": FORMAT, "version": version, "built_at": built_at,
                "python": python, "pyinstaller": pyinstaller, "files": files}
    path = release / MANIFEST
    path.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
    return path


def verify(release: Path) -> list[str]:
    """Problems with the release folder against its manifest; empty when it matches exactly."""
    try:
        manifest = json.loads((release / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"manifest.json can't be read ({exc})"]
    listed = manifest.get("files") if isinstance(manifest, dict) else None
    if (not isinstance(listed, dict) or manifest.get("app") != APP_NAME
            or manifest.get("format") != FORMAT):
        return ["manifest.json is not an NG Runner release manifest"]
    found = _hashes(release)
    problems = [f"{name} is missing" for name in sorted(set(listed) - set(found))]
    problems += [f"{name} is not in the manifest" for name in sorted(set(found) - set(listed))]
    problems += [f"{name} has changed" for name in sorted(set(listed) & set(found))
                 if found[name] != str(listed[name]).lower()]
    return problems


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hashes(release: Path) -> dict[str, str]:
    """Every file below the release's three folders, by its path below release (with /)."""
    files = {}
    for folder in FOLDERS:
        for path in sorted((release / folder).rglob("*")):
            if path.is_file():
                files[str(PurePosixPath(path.relative_to(release).as_posix()))] = sha256(path)
    return files
