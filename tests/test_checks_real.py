"""
The check runner on Jim's real toolkit and the ten real jobs, Cases A to J (P2 build spec 2, ruling R1).

crosscheck runs from a copy of his toolkit, in the tools' Python, isolated, in a Job Object, on a staged
copy of each job and its IC Conversion (read from NG Jobs on G:, never written). What it printed is read
by ngrunner/checks/readers.py, and the verdicts are pinned here as measured on 2026-10-31. Marked
fixtures: they skip when the toolkit, the tools' Python or NG Jobs is absent. Real jobs are named by
case letter only, and no output of his is shown: only checker names and codes.

What the table shows, and why no case passes whole on this PC: wm_variant_check reads its library from
a Linux path (could_not_verify on every case), and parm_check has no template job to compare with
(could_not_verify on every case). Both are for Jim's scripts or the staging on his PC, not for a reader.
"""
from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import FIXTURES
from helpers_toolkit import CASES, conversion, job_folder, stage_tree, tools_python
from ngrunner.checks import LEFT_OUT, Planned, library_found, planned, read_crosscheck, run_crosscheck
from ngrunner.checks.readers import NO_LIBRARY, NO_REFERENCE, NOT_FLAGGED, STOPPED, UNKNOWN_LINE
from ngrunner.clock import FakeClock
from ngrunner.guards.facts import COULD_NOT_VERIFY, FAILED, PASSED, ToolRun
from ngrunner.toolkit import take_snapshot

pytestmark = pytest.mark.fixtures

NOW = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)
CNV = COULD_NOT_VERIFY
EVERY_CASE = {"wm_variant_check": CNV, "parm_check": CNV}
# Every checker that isn't a pass, by case, as measured on 2026-10-31. The rest pass.
MEASURED = {
    "A": {**EVERY_CASE, "br_cam_check": FAILED, "notch_check": FAILED, "eb_tops": CNV, "panel_xcheck": CNV},
    "B": {**EVERY_CASE, "br_cam_check": FAILED, "panel_xcheck": FAILED},
    "C": EVERY_CASE,
    "D": {**EVERY_CASE, "br_cam_check": FAILED, "op_schema_check": CNV},
    "E": EVERY_CASE,
    "F": {**EVERY_CASE, "op_schema_check": CNV},
    "G": EVERY_CASE,
    "H": {**EVERY_CASE, "drill_depth_check": FAILED, "eb_tops": CNV, "panel_xcheck": CNV},
    "I": {**EVERY_CASE, "drill_depth_check": FAILED, "panel_xcheck": FAILED},
    "J": {**EVERY_CASE, "section_align": FAILED, "br_cam_check": FAILED, "notch_check": FAILED, "eb_tops": CNV,
          "top_line": CNV, "panel_xcheck": CNV},
}
FOURTEEN = ("section_align", "section_span", "drill_depth_check", "outset_check", "part_geom_check", "br_cam_check",
            "op_schema_check", "wm_variant_check", "notch_check", "eb_tops", "top_line", "parm_check",
            "panel_xcheck", "ic_code_check")


@pytest.fixture(scope="module")
def copy(tmp_path_factory):
    tools_python()
    tree = stage_tree(tmp_path_factory.mktemp("jims"))
    return take_snapshot(tree, tmp_path_factory.mktemp("data") / "toolkit" / "copy", clock=FakeClock(NOW))


@pytest.fixture(scope="module")
def results(copy, tmp_path_factory):
    work = tmp_path_factory.mktemp("staging")
    return {case: run_crosscheck(copy, job_folder(case), conversion(case), work, python=tools_python())
            for case in CASES}


# ----- what it runs, and what it leaves out


def test_crosscheck_runs_14_checkers_and_leaves_out_three_that_index_names(copy) -> None:
    plan = planned(copy)
    index = (copy.path / "INDEX.md").read_text(encoding="utf-8")
    rows = set(re.findall(r"^\| `(\w+)\.py` \|", index, re.M))
    source = (copy.path / "scripts" / "crosscheck.py").read_text(encoding="utf-8")
    words = {node.value for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Constant)}

    assert tuple(p.name for p in plan) == FOURTEEN
    assert [p.name for p in plan if p.advisory] == ["ic_code_check"]
    assert {"hinge_check", "sheet_size_check"} <= rows - set(FOURTEEN)
    assert "--nest" in index and "--nest" not in words                 # br_cam_check runs, but not on the nest
    assert LEFT_OUT == ("hinge_check", "sheet_size_check", "br_cam_check --nest")


def test_wm_variant_checks_library_is_a_linux_path_this_pc_doesnt_have(copy) -> None:
    assert not library_found(copy)


# ----- the ten real jobs


@pytest.mark.parametrize("case", CASES)
def test_each_real_case_reads_as_measured(results, case: str) -> None:
    result = results[case]

    assert result.ran == FOURTEEN and result.left_out == LEFT_OUT
    got = {v.checker: v.code for v in result.verdicts if v.code != PASSED and not v.advisory}
    assert got == MEASURED[case]
    assert not [v for v in result.verdicts if v.why == UNKNOWN_LINE]    # every line printed is one it knows
    assert result.code == (FAILED if FAILED in got.values() else CNV)


def test_why_each_could_not_verify(results) -> None:
    whys = {(v.checker, v.why) for result in results.values() for v in result.verdicts
            if v.code == CNV and not v.advisory}

    assert {why for checker, why in whys if checker == "wm_variant_check"} == {NO_LIBRARY}
    assert {why for checker, why in whys if checker == "parm_check"} == {NO_REFERENCE}
    assert {why for checker, why in whys if checker in ("eb_tops", "top_line")} == {NOT_FLAGGED}
    assert {why for checker, why in whys if checker == "panel_xcheck"} == {STOPPED}    # it crashed on 3


def test_what_it_ran_on_is_named_by_hash(results) -> None:
    for case, result in results.items():
        assert result.rooms and all(re.fullmatch(r"[0-9a-f]{64}", sha) for _, sha in result.rooms), case
        assert re.fullmatch(r"[0-9a-f]{64}", result.conversion_sha256) and result.snapshot_sha256


def test_no_verdict_repeats_a_jobs_own_words(results) -> None:
    key = json.loads((FIXTURES / "p1b" / "case_key.json").read_text(encoding="utf-8"))
    for case, result in results.items():
        surname = re.split(r"[_ ]", key[case])[0]
        shown = result.words + repr(result) + " ".join(v.why for v in result.verdicts)
        assert surname not in shown, case


# ----- the same as Jim's own crosscheck files, on Windows


def _sections(text: str) -> dict[str, str]:
    found, name = {}, None
    for line in text.splitlines()[4:]:
        header = re.fullmatch(r"===== (\S+).*", line)
        if header:
            name = header[1]
            found[name] = []
        elif name:
            found[name].append(line)
    return {k: "\n".join(v).strip() for k, v in found.items()}


def _paths_out(text: str, job: str) -> str:
    return re.sub(r"(?m)^(\s*)\S.*?[\\/]" + re.escape(job) + r"[\\/]", r"\1<job>/", text.replace("\\", "/"))


def test_on_windows_every_checker_prints_what_it_printed_for_jim_but_parm_check(copy, tmp_path: Path) -> None:
    index = FIXTURES / "p1b" / "checks" / "index.json"
    key_file = FIXTURES / "p1b" / "case_key.json"
    if not index.is_file() or not key_file.is_file():
        pytest.skip("Jim's crosscheck files aren't on this PC")
    case_of = {name: case for case, name in json.loads(key_file.read_text(encoding="utf-8")).items()}
    compared = []
    for entry in json.loads(index.read_text(encoding="utf-8")):
        case = case_of.get(entry["job"])
        if entry["role"] != "crosscheck" or case == "D":       # D's rooms changed after his run
            continue
        his = _sections((FIXTURES / "p1b" / "checks" / entry["file"]).read_text(encoding="utf-8"))
        ours_dir = tmp_path / case
        ran = run_crosscheck(copy, job_folder(case), conversion(case), ours_dir, python=tools_python(),
                             run=_keeping(ours_dir / "kept.txt"))
        ours = _sections((ours_dir / "kept.txt").read_text(encoding="utf-8"))
        for checker in FOURTEEN:
            if checker != "parm_check":
                assert _paths_out(ours[checker], entry["job"]) == _paths_out(his[checker], entry["job"]), (case, checker)
        compared.append(case)
        assert ran.ran == FOURTEEN

    assert sorted(compared) == ["C", "E", "F", "G"]


def _keeping(into: Path):
    """run_tool, keeping what crosscheck wrote before its staging folder goes."""
    from ngrunner.toolkit import run_tool

    def run(snapshot, script, args, **kwargs) -> ToolRun:
        said = run_tool(snapshot, script, args, **kwargs)
        written = next(Path(args[1]).glob("*_crosscheck.txt"), None)
        if written is not None:
            into.write_text(written.read_text(encoding="utf-8"), encoding="utf-8")
        return said

    return run


def test_jims_own_files_read_as_his_verdict_says_with_parm_check_as_he_ran_it(copy) -> None:
    index = FIXTURES / "p1b" / "checks" / "index.json"
    if not index.is_file():
        pytest.skip("Jim's crosscheck files aren't on this PC")
    plan = tuple(Planned(name, name == "ic_code_check") for name in FOURTEEN)
    key = json.loads((FIXTURES / "p1b" / "case_key.json").read_text(encoding="utf-8"))
    case_of = {name: case for case, name in key.items()}
    read, unknown = {}, set()
    for entry in json.loads(index.read_text(encoding="utf-8")):
        if entry["role"] != "crosscheck":
            continue
        text = (FIXTURES / "p1b" / "checks" / entry["file"]).read_text(encoding="utf-8")
        text = text.split("\n===== RECHECK")[0]              # what the chat added after crosscheck wrote it
        lines = text.splitlines()
        rooms = tuple(re.fullmatch(r"  rooms   (.*)", lines[1])[1].split(", "))
        flagged = "FLAGGED" in lines[3]
        run = ToolRun(1 if flagged else 0, lines[3].strip() + "\nwritten: x\n", "", False)
        result = read_crosscheck(text, run, plan=plan, rooms=rooms, library_found=True)
        read[entry["file"]] = {v.checker: v.code for v in result.verdicts if not v.advisory}
        unknown |= {(entry["job"], v.checker) for v in result.verdicts if v.why == UNKNOWN_LINE}

    # The one line the readers don't know: Case D's file is from 10-18, when panel_xcheck still printed
    # the rail cover as a SAW LIST line. The copy of 10-31 leaves rail covers out, so it can't be read.
    assert {(case_of[job], checker) for job, checker in unknown} == {("D", "panel_xcheck")}
    # His template was there for four of them. Case D's run of 10-18 had none: parm_check compared
    # nothing and printed RESULT: CLEAN, which his crosscheck took as clean.
    by_case = {case_of[entry["job"]]: read[entry["file"]]["parm_check"]
               for entry in json.loads(index.read_text(encoding="utf-8")) if entry["role"] == "crosscheck"}
    assert by_case == {"C": PASSED, "D": CNV, "E": FAILED, "F": PASSED, "G": PASSED}


def test_in_ng_runners_own_python_the_checks_read_the_same(copy, results, tmp_path: Path, monkeypatch) -> None:
    # What a build without a tools' Python does: NG Runner's own Python runs crosscheck by run-tool,
    # and each checker it starts. The checks import only the standard library, so it must agree.
    monkeypatch.delenv("NGR_TOOLS_PYTHON", raising=False)

    own = run_crosscheck(copy, job_folder("F"), conversion("F"), tmp_path, python=None)

    assert [(v.checker, v.code) for v in own.verdicts] == [(v.checker, v.code) for v in results["F"].verdicts]


# ----- P3: what each real case's check comes to for Jim, as items naming parts by tag


ITEMS_TO_FIX = {   # (kind of fault, the parts it names), as measured on 2026-10-31
    "A": [("doubled_holes", ("R1C8", "R1C10")), ("notch_face", ("R1C9", "R1C11"))],
    "B": [("doubled_holes", ("R2C4", "R2C8", "R2C10", "R2C12")), ("extra_part", ())],
    "C": [], "E": [], "F": [], "G": [],
    "D": [("doubled_holes", ("R1C5",))],
    "H": [("bore_depth", ("R1C3", "R1C5", "R1C7", "R1C8", "R2C3"))],
    "I": [("bore_depth", ("R1C5",)), ("extra_part", ()), ("missing_part", ())],
    "J": [("bridge_cams", ("R1C25",)), ("doubled_holes", ("R2C2", "R2C4", "R2C6", "R2C14", "R2C16")),
          ("notch_face", ("R2C1", "R2C3", "R2C5", "R2C13", "R2C15")),
          ("top_off", ("R1C2", "R1C4", "R1C7", "R1C11", "R1C13", "R1C21", "R1C23", "R2C10")), ("wm_banding", ("R2",))],
}


@pytest.mark.parametrize("case", CASES)
def test_each_real_cases_check_is_items_by_tag_in_jims_format_and_names_no_client(results, case: str) -> None:
    from ngrunner.checks import findings
    from ngrunner.messages import Message, templates
    from ngrunner.messages.lint import lint

    found = findings.findings_of(results[case])
    key = json.loads((FIXTURES / "p1b" / "case_key.json").read_text(encoding="utf-8"))
    job = key[case]

    assert sorted((i.what, i.tags) for i in found.items if i.kind in ("fix", "mozaik")) == ITEMS_TO_FIX[case]
    assert not [i for i in found.items if i.what == "other"]               # every fault line has its kind
    assert found.code == (FAILED if ITEMS_TO_FIX[case] else CNV)            # Q1 open: nothing passes whole
    lines = tuple(f"{i.n}. {templates.check_item_line(job, i)}" for i in found.items)
    assert lint(Message("check_result", f"{job} checked 9:42 PM (trial)", lines)) == []
    surname = re.split(r"[_ ]", job)[0]
    stored = json.dumps(findings.to_json(found))
    assert surname not in stored and surname not in "\n".join(lines).replace(job, "")
