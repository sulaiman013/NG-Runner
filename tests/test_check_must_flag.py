"""
NG Runner's own "must flag" examples for the eight checkers Jim's regression doesn't cover (answered from
his own files, question 27, docs/decisions.md): section_span, part_geom_check, eb_tops, top_line,
parm_check and panel_xcheck, which crosscheck runs, and his two nest checks, sheet_size_check and
br_cam_check --nest.

Why. His regression.py runs section_align, drill_depth_check, wm_variant_check, br_cam_check,
outset_check, notch_side, op_schema_check and ic_code_check on his three frozen jobs, and fails when one
stops flagging what it flagged. Nothing does that for these eight, so a change that silenced one, in
NG Runner's readers or in what his script prints, would let a bad job through unseen. Each example here
is a fault in the shape the checker's source prints, with made-up names, and must read as failed with
an item to fix: never passed, never could not verify. The tests marked fixtures tie each fault line to
his source (read, never copied), so a change to what he prints fails here too.
"""
from __future__ import annotations

import ast

import pytest

from helpers_toolkit import real_toolkit
from test_checks import CLEAN, ROOM1, read
from test_guards_tool_checks import FIX as SIZE_FIX
from test_guards_tool_checks import ALL_THERE, OPT, TWIN, TWINS_FIX, counts, run, size_line
from ngrunner.checks import findings as f
from ngrunner.guards.facts import FAILED, PASSED
from ngrunner.guards.tool_checks import br_cam_nest, sheet_size
from ngrunner.messages import Message, templates
from ngrunner.messages.lint import lint

# Jim's regression's checkers (regression.py, checkers()), and the eight it leaves to NG Runner.
HIS_REGRESSION = frozenset({"section_align", "drill_depth_check", "wm_variant_check", "br_cam_check",
                            "outset_check", "notch_side", "op_schema_check", "ic_code_check"})
NG_RUNNERS = ("section_span", "part_geom_check", "eb_tops", "top_line", "parm_check", "panel_xcheck",
              "sheet_size_check", "br_cam_check --nest")

# (checker, what it printed, whether crosscheck flags it, the item's kind of fault, its tags)
MUST_FLAG = [
    ("section_span", f"{ROOM1}\n   !! C3   48 DH                     starts at 12.000, 6.350mm GAP after C2",
     True, "span", ("R1C3",)),
    ("section_span", f"{ROOM1}\n   !! C4   48 S Hang 1S              ends at 890.000, 10.000mm short of C5"
                     " (bay 0.000..900.000)", True, "span", ("R1C4",)),
    ("part_geom_check", f"{ROOM1}\n   !! C2   FM 84                    product 356x2133.6 (DxH) but its part is"
                        " 356x2120 (WxL) - stale part geometry, touch the product in Mozaik or fix the cache",
     True, "part_size", ("R1C2",)),
    # eb_tops and top_line exit 0 and crosscheck's pattern misses them: NG Runner flags them (question 26).
    ("eb_tops", "   Room1.des    COUNTERTOP 24.000in x 96.000in  four edges, slot 1\n\nwould change: 1 part(s)/slot(s)",
     False, "banding", ("R1",)),
    ("eb_tops", "   Room1.des    banding SLOT 6 -> Made Up Colour_L999 (tex 3763); slots 1-5 unchanged\n\n"
                "would change: 1 part(s)/slot(s)", False, "banding", ("R1",)),
    ("top_line", "   Room1.des    WM Panel 48 Thru Radius    H=1216    Elev   1184.0 ->  1222.4  (top 2400 -> 2438)\n\n"
                 "would change: 1 WM panels", False, "top_line", ("R1",)),
    ("parm_check", "  OK Closets-JobParms.dat                         identical\n"
                   "  !! NG Closets-JobParms.dat                      1 parameter(s) differ\n"
                   "        JointFast2 CNC Joint Fasteners #2 this job = 14     reference = 1       (want 1 = None)\n\n"
                   "RESULT: 1 parameter file(s) drifted - fix before building", True, "parameters", ()),
    ("panel_xcheck", "IC board parts 18   Mozaik cutlist 17\n\n-- SH: IC 10  Mozaik 9\n"
                     "   MISSING (294.6, 356.0) x1   SH 14x11.6x3/4 FIX WH", True, "missing_part", ()),
    ("panel_xcheck", "IC board parts 18   Mozaik cutlist 19\n\n-- DW: IC 1  Mozaik 2\n   EXTRA   (158.5, 447.8) x1",
     True, "extra_part", ()),
]


def _ids() -> list[str]:
    return [f"{checker} {what}" for checker, _, _, what, _ in MUST_FLAG]


# ----- through crosscheck


@pytest.mark.parametrize("checker, text, flagged, what, tags", MUST_FLAG, ids=_ids())
def test_each_fault_reads_failed_never_passed_or_could_not_verify(checker, text, flagged, what, tags) -> None:
    result = read({**CLEAN, checker: text}, flagged=(checker,) if flagged else ())

    verdict, = [v for v in result.verdicts if v.checker == checker]
    assert verdict.code == FAILED and result.code == FAILED


@pytest.mark.parametrize("checker, text, flagged, what, tags", MUST_FLAG, ids=_ids())
def test_each_fault_is_an_item_to_fix_naming_its_parts(checker, text, flagged, what, tags) -> None:
    # Q1 open: a parameter that isn't the top's is still to fix, so the answer is the same either way.
    for q1_settled in (True, False):
        result = read({**CLEAN, checker: text}, flagged=(checker,) if flagged else ())
        found = f.findings_of(result, q1_settled=q1_settled)

        item, = [i for i in found.items if i.checker == checker]
        assert (item.kind, item.what, item.tags) == (f.FIX, what, tags)
        assert found.code == FAILED
        said = f"1. {templates.check_item_line('Testerson_101426', item)}"
        assert templates.check_chat_line("Testerson_101426", item) and lint(Message("check_result", "t", (said,))) == []


def test_the_same_checkers_clean_output_still_passes() -> None:
    """The other half: each of these, as it prints a clean run, isn't flagged by NG Runner either."""
    result = read()
    codes = {v.checker: v.code for v in result.verdicts}
    assert {checker: codes[checker] for checker, *_ in MUST_FLAG} == {checker: PASSED for checker, *_ in MUST_FLAG}


# ----- his two nest checks


def test_a_nest_on_the_wrong_sheet_reads_failed() -> None:
    code, words = sheet_size(run(size_line("!!", on=(61, 109), want=(49, 97)), SIZE_FIX, *counts(3), ALL_THERE,
                                 code=1))
    assert code == FAILED and words


def test_doubled_holes_in_a_nest_read_failed() -> None:
    code, words = br_cam_nest(run(OPT, TWIN.format(n=2), TWINS_FIX, code=1))
    assert code == FAILED and words


# ----- tied to his source (fixtures): what these examples hold is what his scripts print

# Pieces of each fault line's format, as they are in his source.
PRINTED = {
    "section_span.py": ('"   !! C%-3s %-24s starts at %.3f, %s%.3fmm %s %s"',
                        '"   !! C%-3s %-24s ends at %.3f, %.3fmm %s C%s (bay %.3f..%.3f)"'),
    "part_geom_check.py": ('"   !! C%-3s %-24s product %gx%g (DxH) but its part is %gx%g (WxL)"',),
    "eb_tops.py": ("COUNTERTOP {float(L)/25.4:.3f}in x {float(D)/25.4:.3f}in", "four edges, slot {slot}",
                   "banding SLOT 6 -> {name} (tex {second}); slots 1-5 unchanged",
                   "{'would change' if chk else 'changed'}: {n} part(s)/slot(s)"),
    "top_line.py": ("H={H:<7.0f} Elev {E:7.1f} -> {want:7.1f}  (top {H+E:.0f} -> {top:.0f})",
                    "{'would change' if chk else 'changed'}: {n} WM panels"),
    "parm_check.py": ("'  !! %-44s %d parameter(s) differ'",
                      "'        %-9s %-20s this job = %-6s reference = %-6s  (want %s)'",
                      "'%d parameter file(s) drifted - fix before building'"),
    "panel_xcheck.py": ('f"   MISSING {size} x{left}', 'f"   EXTRA   {msize} x{n}"'),
}


def _source(script: str) -> str:
    path = real_toolkit() / "NG_Automation_scripts" / "scripts" / script
    if not path.is_file():
        pytest.skip("no local toolkit: set NGR_TOOLKIT (AGENTS.md: Fixtures)")
    return path.read_text(encoding="utf-8")


@pytest.mark.fixtures
@pytest.mark.parametrize("script", sorted(PRINTED))
def test_each_fault_line_here_is_one_his_script_prints(script: str) -> None:
    source = _source(script)
    gone = [n for n, piece in enumerate(PRINTED[script]) if piece not in source]
    assert not gone, f"{script} no longer prints what the must-flag examples hold: piece(s) {gone}"


@pytest.mark.fixtures
def test_his_regression_still_leaves_these_eight_to_ng_runner() -> None:
    tree = ast.parse(_source("regression.py"))
    checkers = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "checkers")
    imported = {alias.name for node in ast.walk(checkers) if isinstance(node, ast.Import) for alias in node.names}
    assert imported == HIS_REGRESSION
    assert not {name.split()[0] for name in NG_RUNNERS if " " not in name} & imported
