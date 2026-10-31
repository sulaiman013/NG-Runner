"""
The delivered scripts/ng_convert.py, run as Jim's chat runs it: a Python with pypdf and openpyxl, the
script in a toolkit's scripts folder beside his takeoff.py, parse_pulled.py and classify.py (here the
made-up stand-ins of tests/helpers_delivery.py), on a made-up summary.

They need a Python with pypdf and openpyxl: the tools' Python (NGR_TOOLS_PYTHON, or the development
venv) once tools/tools-venv.txt carries pypdf. Marked fixtures, and skipped when it has neither.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from helpers_delivery import install, rows_of, toolkit
from helpers_pdf import summary_pdf
from helpers_toolkit import tools_python
from ngrunner.clock import FakeClock
from ngrunner.toolkit import hash_of_tree, run_tool, take_snapshot
from test_workbook_conversion import PARTS
from tools.delivery import front, package
from tools.workbook import xlsx

pytestmark = pytest.mark.fixtures
JOB = "Testerson_101426"
COMPLETE = [("SH-WH", 4, "SH 14x24x3/4 FIX WH"), ("VT-WH", 2, "VT 14x83.9x3/4 FD WH"), ("400000155", 8, "Shelf Pin")]
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def python() -> Path:
    found = tools_python()
    done = subprocess.run([str(found), "-I", "-c", "import pypdf, openpyxl"], capture_output=True, timeout=60)
    if done.returncode != 0:
        pytest.skip("the tools' Python has no pypdf yet (tools/tools-venv.txt needs it)")
    return found


@pytest.fixture(scope="module")
def script_text() -> str:
    return package.build_script(made="2026-10-31")


def job_folder(tmp_path: Path, parts=PARTS, **kw) -> Path:
    path = tmp_path / "NG Jobs" / JOB / f"{JOB}_summary.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(summary_pdf(parts=parts, **kw))
    return path


def run(python: Path, script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(python), "-I", "-B", "-X", "utf8", str(script), *args], capture_output=True, text=True,
                          encoding="utf-8", timeout=300)


def written(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def test_a_complete_closet_writes_both_files_checked_and_exits_0(python, script_text, tmp_path) -> None:
    summary = job_folder(tmp_path, COMPLETE)
    scripts = toolkit(tmp_path / "kit", summary.read_bytes())
    script = install(script_text, scripts)

    done = run(python, script, str(summary))

    assert done.returncode == front.WRITTEN, done.stdout + done.stderr
    assert written(summary.parent) == [f"{JOB}_IC_Conversion.json", f"{JOB}_IC_Conversion.xlsx", summary.name]
    conv = json.loads((summary.parent / f"{JOB}_IC_Conversion.json").read_text(encoding="utf-8"))
    mine = front.convert_summary(summary, job=None, scripts=scripts)
    assert {k: v for k, v in conv.items() if k != "ng_convert"} == {k: v for k, v in mine.items() if k != "ng_convert"}
    assert conv["ng_convert"]["version"] == package.version() and conv["ng_convert"]["checked"] == front.CHECKED
    assert xlsx.check(summary.parent / f"{JOB}_IC_Conversion.xlsx", conv) == []
    assert done.stdout.splitlines()[1].startswith("Takeoff complete: 6 board parts. Sheets: 1 - 3/4 Egger Classic White.")


def test_lines_it_could_not_verify_are_listed_never_filled_and_exit_3(python, script_text, tmp_path) -> None:
    summary = job_folder(tmp_path)
    script = install(script_text, toolkit(tmp_path / "kit", summary.read_bytes()))

    done = run(python, script, str(summary))

    assert done.returncode == front.NOT_COMPLETE, done.stdout + done.stderr
    lines = done.stdout.splitlines()
    assert lines[1] == "Takeoff not complete: 1 line could not verify, and it is never filled in:"
    assert lines[2].startswith("1. Page 4: 2 of item CK-WH (CK DBack 3.6x22.2x0.6 NN WH)")
    conv = json.loads((summary.parent / f"{JOB}_IC_Conversion.json").read_text(encoding="utf-8"))
    assert "CK" not in {p["type"] for p in conv["parts"]} and len(conv["unplaced"]) == 1
    book = xlsx.read(summary.parent / f"{JOB}_IC_Conversion.xlsx")
    assert "CK" not in {book.sheet("Parts (mm)").value(r, 2) for r in range(2, book.sheet("Parts (mm)").max_row + 1)}


@pytest.mark.parametrize("case", ["second reading", "toolkit", "garage", "summary of another job"])
def test_a_refusal_writes_nothing_and_says_why(python, script_text, tmp_path, case) -> None:
    designs = (("Testerson - Garage", "Garage"),) if case == "garage" else (("Testerson - Primary Closet", "Closet"),)
    summary = job_folder(tmp_path, designs=designs)
    data = summary.read_bytes()
    scripts = toolkit(tmp_path / "kit", data, rows=rows_of(data, drop=0) if case == "second reading" else None)
    script = install(script_text, scripts)
    args = {"second reading": [], "toolkit": ["--out", str(scripts.parent / "out")], "garage": [],
            "summary of another job": ["--job", "Otherson_101426"]}[case]
    kit_before = hash_of_tree(scripts.parent)

    done = run(python, script, str(summary), *args)

    assert done.returncode == front.REFUSED, done.stdout + done.stderr
    words = {"second reading": "differ on 1 board line", "toolkit": "never writes inside the toolkit",
             "garage": "garage job", "summary of another job": "PROJECT NAME"}[case]
    assert words in done.stdout and done.stderr == ""
    assert written(summary.parent) == [summary.name]
    assert hash_of_tree(scripts.parent) == kit_before


def test_files_there_are_written_over_only_with_replace(python, script_text, tmp_path) -> None:
    summary = job_folder(tmp_path, COMPLETE)
    script = install(script_text, toolkit(tmp_path / "kit", summary.read_bytes()))
    there = summary.parent / f"{JOB}_IC_Conversion.json"
    there.write_text("the chat's own", encoding="utf-8")

    refused = run(python, script, str(summary))
    replaced = run(python, script, str(summary), "--replace")

    assert refused.returncode == front.REFUSED and "is there already" in refused.stdout
    assert replaced.returncode == front.WRITTEN and json.loads(there.read_text(encoding="utf-8"))["job"] == JOB
    assert not list(summary.parent.glob("*.part"))


def test_it_runs_from_a_copy_as_ng_runners_runner_runs_his_scripts(python, script_text, tmp_path) -> None:
    """Isolated (-I), in a Job Object, from a hash-pinned copy that must not change (toolkit.run_tool)."""
    summary = job_folder(tmp_path, COMPLETE)
    scripts = toolkit(tmp_path / "kit", summary.read_bytes())
    install(script_text, scripts)
    copy = take_snapshot(scripts.parent, tmp_path / "data" / "toolkit" / "copy", clock=FakeClock(NOW))
    out = tmp_path / "staging"

    said = run_tool(copy, "scripts/ng_convert.py", [str(summary), "--out", str(out)], python=python, timeout_s=300)

    assert said.exit_code == front.WRITTEN, said.stdout + said.stderr
    assert written(out) == [f"{JOB}_IC_Conversion.json", f"{JOB}_IC_Conversion.xlsx"]
    assert hash_of_tree(copy.path) == copy.sha256


def test_in_a_python_without_openpyxl_it_says_so(script_text, tmp_path) -> None:
    summary = job_folder(tmp_path, COMPLETE)
    script = install(script_text, toolkit(tmp_path / "kit", summary.read_bytes()))
    done = run(Path(sys.executable), script, str(summary))           # NG Runner's own venv: pypdf, no openpyxl
    assert done.returncode == front.REFUSED and done.stdout.strip() == (
        "openpyxl isn't installed in this Python, so nothing was converted.")
