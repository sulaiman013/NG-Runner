"""
The delivery against Jim's real toolkit (local only, marked fixtures; skips when the toolkit, the pairs or
a Python with pypdf and openpyxl is absent).

RULES section 8's gate, for real: his regression.py, run on a staged copy of his toolkit with
ng_convert.py added, must be clean, through the very adoption NG Runner runs each night. Then the
script runs from that adopted copy, isolated, as NG Runner's runner runs his scripts: on his three
frozen summaries, where his own takeoff.py's reading of its parts is set against baseline.json, and on
all 52 real pairs, with his real parse_pulled.py, classify.py and takeoff.py checking it every time.

Nothing names a job: frozen jobs by position, pairs by index. Measured 2026-10-31; every count is a
measured fact, never a target.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import FIXTURES
from helpers_toolkit import real_toolkit, tools_python
from ngrunner.clock import FakeClock
from ngrunner.toolkit import Snapshot, hash_of_tree, run_regression, run_tool
from tools.delivery import front, package
from tools.workbook import xlsx
from tools.workbook.conversion import ConversionRefused, conversion

pytestmark = pytest.mark.fixtures
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
PAIRS = FIXTURES / "p2" / "pairs"


@pytest.fixture(scope="module")
def python() -> Path:
    found = tools_python()
    if subprocess.run([str(found), "-I", "-c", "import pypdf, openpyxl, pdfplumber"], capture_output=True,
                      timeout=60).returncode != 0:
        pytest.skip("the tools' Python has no pypdf yet (tools/tools-venv.txt needs it)")
    return found


@pytest.fixture(scope="module")
def offered(python: Path, tmp_path_factory):
    kit = real_toolkit()
    before = hash_of_tree(kit)
    work = tmp_path_factory.mktemp("work")
    copies: list[Snapshot] = []

    def frozen(snapshot: Snapshot, into: Path) -> list[dict]:
        copies.append(snapshot)
        return package.frozen_jobs(snapshot, into, python=python)

    result = package.offer(toolkit=kit, out=tmp_path_factory.mktemp("delivery"), work=work, clock=FakeClock(NOW),
                           regress=lambda s: run_regression(s, python=python), made="2026-10-31", frozen=frozen)
    assert hash_of_tree(kit) == before                                # his toolkit: read, never written
    return result, (copies[0] if copies else None)


def test_his_regression_is_clean_on_his_toolkit_with_ng_convert_added(offered) -> None:
    result, copy = offered
    assert result.offered, result.words
    assert result.manifest["regression"]["words"] == "Its regression is clean: nothing moved on Jim's frozen jobs."
    assert (copy.path / "scripts" / package.SCRIPT).read_text(encoding="utf-8") == package.build_script(made="2026-10-31")


def test_on_his_frozen_summaries_his_takeoff_py_reads_its_parts_as_baseline_json_on_2_of_3(offered) -> None:
    frozen = offered[0].manifest["frozen"]
    assert [(f["job"], f["exit"], f["board_parts_equal_baseline"]) for f in frozen] == [
        ("frozen job 1", front.WRITTEN, True), ("frozen job 2", front.NOT_COMPLETE, True),
        ("frozen job 3", front.WRITTEN, True)]
    assert [f["takeoff_equals_baseline"] for f in frozen] == [True, False, True]
    # Frozen job 2 (the build spec's Y): its area and tape differ by the two rows tests/test_convert_takeoff.py
    # names, and one 0.7in door is left as could not verify.
    assert frozen[1]["differs"] == ["buckets", "edgeband_lf"]


@pytest.fixture(scope="module")
def runs(offered, python: Path, tmp_path_factory) -> dict[int, tuple]:
    _, copy = offered
    index = json.loads((PAIRS / "index.json").read_text(encoding="utf-8"))
    out = {}
    for i in range(len(index)):
        target = tmp_path_factory.mktemp(f"pair{i}")
        said = run_tool(copy, "scripts/ng_convert.py", [str(PAIRS / str(i) / "summary.pdf"), "--out", str(target)],
                        python=python, timeout_s=600)
        out[i] = (said, target)
    return out


def test_from_the_adopted_copy_it_writes_every_real_closet_and_refuses_every_garage(runs) -> None:
    codes = {i: said.exit_code for i, (said, _) in runs.items()}
    assert len(codes) == 52
    # 36 and 10 since 2026-10-31, when two pairs became complete: a second-colour worksurface built in its
    # own colour, and white 1-1/8in shelves built 1in AMWH (answered from Jim's own files, questions 2, 9, 10).
    assert sum(c == front.WRITTEN for c in codes.values()) == 36
    assert sum(c == front.NOT_COMPLETE for c in codes.values()) == 10
    refused = {i: runs[i][0].stdout for i, c in codes.items() if c == front.REFUSED}
    assert len(refused) == 6 and all("garage job" in said for said in refused.values()), refused
    assert all(said.stderr == "" for said, _ in runs.values())


def test_what_it_wrote_is_the_conversion_and_its_workbook_checks_clean(runs) -> None:
    for i, (said, target) in runs.items():
        if said.exit_code not in (front.WRITTEN, front.NOT_COMPLETE):
            continue
        conv_path = next(target.glob("*_IC_Conversion.json"))
        conv = json.loads(conv_path.read_text(encoding="utf-8"))
        try:
            mine = conversion((PAIRS / str(i) / "summary.pdf").read_bytes(), summary_name="summary.pdf")
        except ConversionRefused:
            pytest.fail(f"pair {i}: written there, refused here")
        skip = {"ng_convert", "pulled_classified", "source"}
        assert {k: v for k, v in conv.items() if k not in skip} == {k: v for k, v in mine.items() if k not in skip}, i
        assert conv["ng_convert"]["checked"] == front.CHECKED and conv["ng_convert"]["takeoff_py"], i
        assert xlsx.check(conv_path.with_suffix(".xlsx"), conv) == [], i
        assert all(r["bucket"] for r in conv["pulled_classified"]), i         # his classify bucketed the rest
