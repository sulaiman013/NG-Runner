"""
NG Runner's own top line (ngrunner/checks/topline.py) on the real rooms: Jobs A to J, the 40 P5 jobs and
Jim's 3 frozen jobs, read by bytes, and tied to Jim's top_line.py by ast, never by import. Marked fixtures:
skips when the material is absent. Jobs are named by case letter, P-tag or frozen letter only; nothing a
room holds is shown.

Measured on 2026-11-01 (docs/decisions.md, "P3: the replay trial and the audit"): of 112 product rooms, 42
hang nothing, 50 are on their one top line, 15 have a WM panel off it, and 5 have a top as built off the
height grid, which RULES leaves to Jim. tests/test_trial_real.py holds that Jim's own top_line agrees on
every room both read.
"""
from __future__ import annotations

import ast
import collections
import re

import pytest

from conftest import FIXTURES
from helpers_toolkit import CASES, real_toolkit
from ngrunner.build import placement
from ngrunner.checks import topline
from ngrunner.guards.facts import COULD_NOT_VERIFY, FAILED

pytestmark = pytest.mark.fixtures

FAILED_ROOMS = {"J": {2}, "P02": {1, 2, 3}, "P03": {1}, "P07": {2, 3}, "P12": {1, 2}, "P16": {1, 3}, "P18": {2, 3},
                "P21": {1}, "P24": {2}}
LEFT_TO_JIM = {"F": {1}, "P05": {1}, "P16": {2}, "P22": {1}, "Y": {1}}      # a top as built off the grid


def real_rooms() -> dict[str, dict[str, bytes]]:
    toolkit = real_toolkit()
    jobs = {case: next((toolkit / "jobs" / case).iterdir()) for case in CASES if (toolkit / "jobs" / case).is_dir()}
    p5 = FIXTURES / "reader" / "p5jobs"
    jobs |= {tag.name: next(tag.iterdir()) for tag in sorted(p5.glob("P[0-9][0-9]")) if tag.is_dir()}
    regression = toolkit / "NG_Automation_reference" / "reference" / "regression"
    frozen = sorted(p / "rooms" for p in regression.iterdir() if (p / "rooms").is_dir())
    jobs |= dict(zip("XYZ", frozen))
    if len(jobs) != 53:
        pytest.skip("the 53 real jobs aren't all on this PC")
    return {tag: {p.name: p.read_bytes() for p in folder.glob("Room*.des")} for tag, folder in jobs.items()}


def test_which_products_are_panels_is_jims_top_line_py_word_for_word() -> None:
    source = (real_toolkit() / "NG_Automation_scripts" / "scripts" / "top_line.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assigned = {node.targets[0].id: node.value.value for node in tree.body
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)}
    panels = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "panels")
    matched = [call.args[0].value for call in ast.walk(panels) if isinstance(call, ast.Call)
               and getattr(call.func, "attr", "") == "match" and isinstance(call.args[0], ast.Constant)]
    widths = [node.comparators[0].value for node in ast.walk(panels) if isinstance(node, ast.Compare)
              and isinstance(node.comparators[0], ast.Constant)]

    assert matched == [topline.WM_PANEL.pattern, topline.FM_PANEL.pattern]
    assert widths == [topline.PANEL_MAX_WIDTH_MM]
    assert re.search(r'ProdName="\(\(\?:(.+?)\)', assigned["WMRX"])[1] == topline.WM_PANEL.pattern.strip("()")
    assert (assigned["T"], assigned["NEAR"]) == (placement.NORM_TOP_MM, placement.NEAR_NORM_MM)


def test_on_the_real_rooms_as_measured() -> None:
    found = {tag: topline.rooms_top(rooms) for tag, rooms in real_rooms().items()}
    codes = collections.Counter(top.code for tops in found.values() for top in tops)

    assert codes == {"passed": 50, topline.NONE: 42, FAILED: 15, COULD_NOT_VERIFY: 5}
    assert {tag: {t.room for t in tops if t.code == FAILED} for tag, tops in found.items()
            if any(t.code == FAILED for t in tops)} == FAILED_ROOMS
    assert {tag: {t.room for t in tops if t.code == COULD_NOT_VERIFY} for tag, tops in found.items()
            if any(t.code == COULD_NOT_VERIFY for t in tops)} == LEFT_TO_JIM
    # Every room off its top line has a panel off T: no real room hangs on two tops, or off the Elev grid.
    assert {f for tops in found.values() for t in tops for f in t.found} == {topline.OFF_TOP, topline.AS_BUILT}
