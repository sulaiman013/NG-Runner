# -*- mode: python ; coding: utf-8 -*-
r"""
PyInstaller 6.22 build of NG Runner: one release folder with two folder builds and the installer.

Why this shape (design section 6, "Stack"; spec 6.7): folder builds, because a one-file build
unpacks itself into %TEMP% at every start, which is slow and is what antivirus distrusts; no
UPX, whose packed exes antivirus flags; UTF-8 mode, so no text is ever read or written in the
PC's code page. The launcher is a build of its own, kept small and rarely changed, so a bad
app build can never break the thing that starts (and falls back from) app builds.

Build from the repo root, never into the repo (build\ and dist\ stay out of git):
    .venv\Scripts\python.exe -m PyInstaller ngrunner.spec --noconfirm --distpath <out>\dist --workpath <out>\build

What lands in <out>\dist, the release folder Sulaiman carries to Jim's PC:
    app\            NGRunner.exe (console; the launcher starts it with no window) + _internal\
    launcher\       NGRunnerLauncher.exe (no console; the Start Menu item, both tasks and the
                    Run key start it) + its own _internal\
    installer\      install.ps1, uninstall.ps1, common.ps1, tasks\*.xml
    manifest.json   every file's sha256; install.ps1 refuses any difference. The build prints
                    the manifest's own sha256, for install.ps1 -ManifestSha256.
Use a new, empty <out> for each build.
"""
import importlib.util
import platform
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import PyInstaller
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

REPO = Path(SPECPATH)
sys.path.insert(0, str(REPO))  # the package is found from wherever PyInstaller is started

from ngrunner.selftest import P1A_MODULES  # noqa: E402 - needs the line above


VERSION = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
UTF8_MODE = [("X utf8", None, "OPTION")]
# The pinned libraries (pyproject.toml), by distribution name: their metadata goes in, so the
# support bundle can say which versions a build carries.
DISTRIBUTIONS = ("pypdf", "flask", "waitress", "requests", "pystray", "pillow", "psutil", "pywin32",
                 "windows-toasts", "cryptography", "tzdata")
# Never needed at run time; kept out of both builds.
LEFT_OUT = ["tkinter", "_tkinter", "pytest", "_pytest", "PyInstaller", "setuptools", "pip"]


def _release_manifest():
    spec = importlib.util.spec_from_file_location("ngr_release_manifest", REPO / "installer" / "manifest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Both builds carry the version: ngrunner.launcher.app_version() reads it from _internal\.
version_file = Path(workpath) / "ngrunner-version.txt"
version_file.parent.mkdir(parents=True, exist_ok=True)
version_file.write_text(VERSION, encoding="utf-8")

# ----- the app: NGRunner.exe

app = Analysis(
    [str(REPO / "installer" / "entry_app.py")],
    pathex=[str(REPO)],
    # The self-test imports every P1a module and library by name (so PyInstaller can't see
    # those imports itself), and pystray picks its backend by name.
    # Studio's sign-in imports playwright only when it runs, and playwright starts its own driver
    # from a data folder, so both have to be named here.
    hiddenimports=collect_submodules("ngrunner") + ["pystray._win32"]
                  + collect_submodules("playwright")
                  + [name for name in P1A_MODULES if not name.startswith("ngrunner")],
    datas=[(str(version_file), ".")] + collect_data_files("ngrunner")
          + collect_data_files("playwright", include_py_files=False)
          + [entry for name in DISTRIBUTIONS for entry in copy_metadata(name)],
    excludes=LEFT_OUT,
    noarchive=False,
)
app_exe = EXE(
    PYZ(app.pure),
    app.scripts,
    UTF8_MODE,
    exclude_binaries=True,
    name="NGRunner",
    console=True,
    upx=False,
)
COLLECT(app_exe, app.binaries, app.datas, upx=False, name="app")

# ----- the launcher: NGRunnerLauncher.exe, small and with no console window

launcher = Analysis(
    [str(REPO / "installer" / "entry_launcher.py")],
    pathex=[str(REPO)],
    datas=[(str(version_file), ".")],
    excludes=LEFT_OUT + ["cryptography", "flask", "waitress", "werkzeug", "jinja2", "requests", "PIL",
                         "pypdf", "pystray", "windows_toasts", "winrt", "win32api", "win32com", "pywintypes"],
    noarchive=False,
)
launcher_exe = EXE(
    PYZ(launcher.pure),
    launcher.scripts,
    UTF8_MODE,
    exclude_binaries=True,
    name="NGRunnerLauncher",
    console=False,
    upx=False,
)
COLLECT(launcher_exe, launcher.binaries, launcher.datas, upx=False, name="launcher")

# ----- the installer files and the manifest

release_manifest = _release_manifest()
release = Path(DISTPATH)
release_manifest.copy_installer(REPO / "installer", release)
written = release_manifest.write(
    release, VERSION, built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    python=platform.python_version(), pyinstaller=PyInstaller.__version__)
print(f"NG Runner {VERSION} release in {release}; manifest.json sha256 {release_manifest.sha256(written)}")
