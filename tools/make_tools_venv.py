r"""
Make the tools' Python for development: a venv outside the repo holding exactly the packages Jim's
toolkit imports, pinned with their hashes in tools/tools-venv.txt (rulings R1 and R7), and the programs
his scripts start by name, pinned with their SHA-256 in tools/tools-bin.txt.

Why a venv of its own: his scripts import openpyxl, pdfplumber, numpy, Pillow, scipy and PyMuPDF, which
NG Runner never imports and its build doesn't carry. Kept apart, they can't change NG Runner, and NG
Runner's packages can't change what his scripts see. NG Runner runs his scripts in it with -I, so no
PYTHON* variable and no user site reach them (toolkit.py).

Why the programs: elev_panels.py, gate 6's drawing check, renders a page with poppler's pdftoppm by name
(its line 23). The pinned poppler build is fetched from its tagged release, refused unless its SHA-256 is
the pinned one, and unpacked under %LOCALAPPDATA%\NGRunnerDev\tools\<name>-<version>\, never onto the
system PATH: NG Runner puts its bin folder first on PATH only for the subprocess that runs Jim's script
(gates.measure_wall), found through NG Runner's pdftoppm setting. So Jim's PC gets the identical binary.

    python tools\make_tools_venv.py [--into <folder>] [--programs-into <folder>]
    python tools\make_tools_venv.py --programs-only [--programs-into <folder>]

It makes %LOCALAPPDATA%\NGRunnerDev\toolsvenv unless told otherwise, with the Python that runs this,
which must be CPython 3.14, 64-bit, like the build. It refuses a folder that is there, for the venv and for
each program. Then point NG Runner at them: set NGR_TOOLS_PYTHON to <folder>\Scripts\python.exe, and its
pdftoppm setting to the pdftoppm.exe it names. Exit code: 0 made, 1 failed or refused, 2 wrong command
line or Python.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import venv
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

PINNED = Path(__file__).resolve().parent / "tools-venv.txt"
PROGRAMS = Path(__file__).resolve().parent / "tools-bin.txt"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_RELEASE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/releases/download/v?(?P<tag>[^/]+)/[^/]+\.zip")


class Refused(Exception):
    """A pinned program can't be put in place. The message is plain words."""


@dataclass(frozen=True)
class Program:
    """One line of tools-bin.txt: a program's zip from a tagged release, its SHA-256, and its bin folder in it."""
    name: str
    version: str
    url: str
    sha256: str
    bin: str

    @property
    def folder(self) -> str:
        return f"{self.name}-{self.version}"

    def bin_in(self, root: Path) -> Path:
        """Its bin folder once unpacked under root."""
        return root / self.folder / Path(*PurePosixPath(self.bin).parts)


def read_programs(text: str) -> list[Program]:
    """tools-bin.txt's programs. A line that isn't name, version, a tagged release's zip, sha256= and bin= is
    refused, as is a release that isn't the version's own tag ("latest" among them)."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 5 or not parts[3].startswith("sha256=") or not parts[4].startswith("bin="):
            raise Refused("tools-bin.txt has a line that isn't name, version, url, sha256= and bin=")
        name, version, url, sha, bin_dir = parts[0], parts[1], parts[2], parts[3][7:].lower(), parts[4][4:]
        found = _RELEASE.fullmatch(url)
        if found is None or found["tag"] != version:
            raise Refused(f"{name} must come from its own tagged release's zip on GitHub, never from latest")
        if not _SHA256.fullmatch(sha):
            raise Refused(f"{name}'s pinned SHA-256 isn't 64 hex digits")
        if PurePosixPath(bin_dir).is_absolute() or ".." in PurePosixPath(bin_dir).parts:
            raise Refused(f"{name}'s bin folder must be inside its zip")
        out.append(Program(name, version, url, sha, bin_dir))
    return out


def download(url: str, to: Path) -> None:
    """The file at url, over HTTPS, into to."""
    with urllib.request.urlopen(url, timeout=600) as response, to.open("wb") as out:  # noqa: S310 - pinned https
        shutil.copyfileobj(response, out)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(program: Program, root: Path, *, get: Callable[[str, Path], None] = download) -> Path:
    """The program's zip fetched into a folder of its own, its SHA-256 held to the pin, and only then unpacked
    under root\\<name>-<version>\\: its bin folder. Refused, with nothing unpacked, when the folder is there,
    the hash differs, the zip names a path outside its folder or it holds no such bin folder."""
    target = root / program.folder
    if target.exists():
        raise Refused(f"{target} is there already; nothing was changed.")
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="fetch-", dir=root))
    try:
        zipped = work / PurePosixPath(urlparse(program.url).path).name
        get(program.url, zipped)
        got = sha256_of(zipped)
        if got != program.sha256:
            raise Refused(f"The {program.name} {program.version} download's SHA-256 is {got}, and tools-bin.txt "
                          f"pins {program.sha256}, so it was not unpacked.")
        unpack(zipped, target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not program.bin_in(root).is_dir():
        shutil.rmtree(target, ignore_errors=True)
        raise Refused(f"The {program.name} zip holds no {program.bin} folder, so it was not kept.")
    return program.bin_in(root)


def unpack(zipped: Path, into: Path) -> None:
    """Every member of the zip into a new folder, after checking none names a path outside it."""
    with zipfile.ZipFile(zipped) as z:
        for name in z.namelist():
            path = PurePosixPath(name.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or ":" in name:
                raise Refused("The zip names a path outside its own folder, so it was not unpacked.")
        into.mkdir(parents=True)
        z.extractall(into)


def make_venv(into: Path) -> int:
    venv.EnvBuilder(with_pip=True).create(into)
    python = into / "Scripts" / "python.exe"
    done = subprocess.run([str(python), "-I", "-m", "pip", "install", "--disable-pip-version-check",
                           "--require-hashes", "--only-binary", ":all:", "--no-deps", "-r", str(PINNED)])
    if done.returncode != 0:
        print(f"The packages were not installed; the venv at {into} is incomplete.")
        return 1
    print(f"Made {into}. Set NGR_TOOLS_PYTHON={python}")
    return 0


def make_programs(root: Path, *, get: Callable[[str, Path], None] = download) -> int:
    try:
        for program in read_programs(PROGRAMS.read_text(encoding="utf-8")):
            found = fetch(program, root, get=get)
            print(f"Unpacked {program.name} {program.version} under {root / program.folder}. Set NG Runner's "
                  f"pdftoppm setting to {found / 'pdftoppm.exe'}")
    except Refused as exc:
        print(exc)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make the tools' Python and programs for Jim's scripts.")
    parser.add_argument("--into", help="the new venv's folder (default %%LOCALAPPDATA%%\\NGRunnerDev\\toolsvenv)")
    parser.add_argument("--programs-into", help="where the pinned programs are unpacked "
                                                "(default %%LOCALAPPDATA%%\\NGRunnerDev\\tools)")
    parser.add_argument("--programs-only", action="store_true", help="fetch only the programs of tools-bin.txt")
    args = parser.parse_args(argv)
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "NGRunnerDev"
    programs = Path(args.programs_into) if args.programs_into else base / "tools"
    if not programs.is_absolute():
        print("The programs' folder must be a full path.")
        return 2
    if not args.programs_only:
        if sys.version_info[:2] != (3, 14) or platform.architecture()[0] != "64bit":
            print("Run this with CPython 3.14, 64-bit: the pinned wheels are for that Python.")
            return 2
        into = Path(args.into) if args.into else base / "toolsvenv"
        if not into.is_absolute():
            print("The venv's folder must be a full path.")
            return 2
        if into.exists():
            print(f"{into} is there already; nothing was changed.")
            return 2
        made = make_venv(into)
        if made != 0:
            return made
    return make_programs(programs)


if __name__ == "__main__":
    raise SystemExit(main())
