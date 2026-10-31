r"""
Make the tools' Python for development: a venv outside the repo holding exactly the packages Jim's
toolkit imports, pinned with their hashes in tools/tools-venv.txt (rulings R1 and R7).

Why a venv of its own: his scripts import openpyxl, pdfplumber, numpy, Pillow, scipy and PyMuPDF, which
NG Runner never imports and its build doesn't carry. Kept apart, they can't change NG Runner, and NG
Runner's packages can't change what his scripts see. NG Runner runs his scripts in it with -I, so no
PYTHON* variable and no user site reach them (toolkit.py).

    python tools\make_tools_venv.py [--into <folder>]

It makes %LOCALAPPDATA%\NGRunnerDev\toolsvenv unless told otherwise, with the Python that runs this,
which must be CPython 3.14, 64-bit, like the build. It refuses a folder that is there. Then point NG
Runner at it: set NGR_TOOLS_PYTHON to <folder>\Scripts\python.exe. Exit code: 0 made, 1 failed,
2 wrong command line or Python.
"""
from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import venv
from pathlib import Path

PINNED = Path(__file__).resolve().parent / "tools-venv.txt"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make the tools' Python for Jim's scripts.")
    parser.add_argument("--into", help="the new venv's folder (default %%LOCALAPPDATA%%\\NGRunnerDev\\toolsvenv)")
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 14) or platform.architecture()[0] != "64bit":
        print("Run this with CPython 3.14, 64-bit: the pinned wheels are for that Python.")
        return 2
    base = os.environ.get("LOCALAPPDATA", "")
    into = Path(args.into) if args.into else Path(base) / "NGRunnerDev" / "toolsvenv"
    if not into.is_absolute():
        print("The venv's folder must be a full path.")
        return 2
    if into.exists():
        print(f"{into} is there already; nothing was changed.")
        return 2
    venv.EnvBuilder(with_pip=True).create(into)
    python = into / "Scripts" / "python.exe"
    done = subprocess.run([str(python), "-I", "-m", "pip", "install", "--disable-pip-version-check",
                           "--require-hashes", "--only-binary", ":all:", "--no-deps", "-r", str(PINNED)])
    if done.returncode != 0:
        print(f"The packages were not installed; the venv at {into} is incomplete.")
        return 1
    print(f"Made {into}. Set NGR_TOOLS_PYTHON={python}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
