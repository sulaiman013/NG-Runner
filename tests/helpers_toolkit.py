"""
Jim's toolkit laid out as his own tree, from the local fixtures, and the tools' Python (rulings R1, R7).

His zip arrived as four parts (docs, scripts, reference, templates) beside ten job folders. His tree is
_NG_Automation\\ with the docs at its root and scripts\\, reference\\ and templates\\ beside them, which is
where regression.py and crosscheck.py look. This lays the parts out that way in a folder of the test's
own: never in .local-fixtures, never on G:, and never in git, as the code is his and the repo is public.
Real jobs are named by case letter only; the key is outside the repo.

Every test that uses this is marked fixtures and skips when the toolkit or the tools' Python is absent.
The tools' Python is NGR_TOOLS_PYTHON, or the development venv tools/make_tools_venv.py makes.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from conftest import FIXTURES

DEV_TOOLS_PYTHON = Path(os.environ.get("LOCALAPPDATA", "C:\\")) / "NGRunnerDev" / "toolsvenv" / "Scripts" / "python.exe"
CASES = "ABCDEFGHIJ"
_PARTS = {"NG_Automation_scripts": "scripts", "NG_Automation_reference": "reference",
          "NG_Automation_templates": "templates"}


def real_toolkit() -> Path:
    """The newest copy of Jim's zip in the fixtures."""
    found = sorted(p for p in (FIXTURES / "toolkit").glob("2*") if (p / "NG_Automation_scripts").is_dir())
    if not found:
        pytest.skip("Jim's toolkit isn't on this PC (.local-fixtures/toolkit)")
    return found[-1]


def tools_python() -> Path:
    """The Python Jim's scripts run in here, outside the repo, with his packages."""
    given = os.environ.get("NGR_TOOLS_PYTHON", "").strip()
    path = Path(given) if given else DEV_TOOLS_PYTHON
    if not path.is_file():
        pytest.skip("the tools' Python isn't on this PC (python tools/make_tools_venv.py makes it)")
    return path


def stage_tree(into: Path) -> Path:
    """_NG_Automation as Jim has it, copied into `into`, which must be new."""
    zipped = real_toolkit()
    tree = into / "_NG_Automation"
    tree.mkdir(parents=True)
    for part, folder in _PARTS.items():
        shutil.copytree(zipped / part / folder, tree / folder)
    for doc in sorted((zipped / "NG_Automation_docs").iterdir()):
        if doc.is_file():
            shutil.copyfile(doc, tree / doc.name)
    return tree


def job_folder(case: str) -> Path:
    """A real Mozaik job folder, by case letter: read only, and only ever staged."""
    found = real_toolkit() / "jobs" / case
    if not found.is_dir():
        pytest.skip(f"Case {case}'s job folder isn't on this PC")
    return next(found.iterdir())


def real_jobs() -> dict[str, Path]:
    """Every real Mozaik job folder here, read only, by case letter (A to J), P-tag (the P5 jobs) or a1 to a5 (the
    jobs of Jim's answers of 2026-10-31, in name order; their template job left out): the jobs colour.KnownGood
    looks in, standing for Mozaik Shared\\Jobs. Its keys name no client."""
    out: dict[str, Path] = {}
    toolkit = real_toolkit()
    for case in CASES:
        found = sorted((toolkit / "jobs" / case).glob("*/"))
        if found:
            out[case] = found[0]
    for tag in sorted((FIXTURES / "reader" / "p5jobs").glob("P[0-9][0-9]")):
        found = sorted(p for p in tag.iterdir() if p.is_dir())
        if found:
            out[tag.name] = found[0]
    answers = sorted(p for p in FIXTURES.glob("answers*/*") if (p / "mozaik").is_dir() and not p.name.startswith("Lux "))
    out.update({f"a{i}": p / "mozaik" for i, p in enumerate(answers, start=1)})
    return out


def ng_jobs() -> Path | None:
    given = os.environ.get("NGR_NG_JOBS")
    if given:
        return Path(given)
    for base in (Path(r"G:\.shortcut-targets-by-id"), Path(r"G:\My Drive"), Path(r"G:\Shared drives")):
        if base.is_dir():
            for pattern in ("*/*/Sales Folder/CNC operations/NG Jobs", "*/Sales Folder/CNC operations/NG Jobs"):
                for found in base.glob(pattern):
                    return found
    return None


def ng_job(case: str) -> Path:
    """A case's NG Jobs folder on G:, only ever read."""
    root, key = ng_jobs(), FIXTURES / "p1b" / "case_key.json"
    if root is None or not key.is_file():
        pytest.skip("NG Jobs or the case key isn't on this PC")
    name = json.loads(key.read_text(encoding="utf-8"))[case]
    for place in (root / name, root / "Old" / name):
        if place.is_dir():
            return place
    pytest.skip(f"Case {case} has no NG Jobs folder")
    raise AssertionError    # pytest.skip raises


def conversion(case: str) -> Path:
    """The case's IC Conversion in NG Jobs, the file crosscheck reads by default."""
    found = sorted(ng_job(case).glob("*_IC_Conversion.json"))
    if len(found) != 1:
        pytest.skip(f"Case {case} has no single IC Conversion")
    return found[0]
