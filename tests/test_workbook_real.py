"""
The workbook on the 52 real pairs, scored against the chat's own workbooks by named cells, and its snap
rule held to Jim's SCHEMA (local only, marked fixtures; skips when the material is absent).

Nothing names a pair: a failure says its index. Every count is a measured fact or a floor (measured
2026-10-31), never a target: when one moves, find why before changing it. Each difference left on a
complete closet has its cause written beside it.
"""
from __future__ import annotations

import functools
import json
import re

import pytest

from conftest import FIXTURES
from tools.workbook import conversion as conversion_module
from tools.workbook import score
from tools.workbook.conversion import ConversionRefused, conversion
from tools.workbook.sheets import layout
from tools.workbook.xlsx import check_book

pytestmark = pytest.mark.fixtures
PAIRS = FIXTURES / "p2" / "pairs"
TAKEOFF_FIELDS = ("board_parts", "sqft", "tape", "part_pieces", "built_mm", "buckets", "bucket_sqm",
                  "bucket_calc_raw", "edgeband", "estimate", "walls", "pulled_board")
# Every difference on a complete closet scored in his format, and why (2026-10-31).
KNOWN = {
    (18, "bucket_sheets"): "the chat built IC Charcoal as 'Charcoal (AMLM)' on 49x97; RULES 123 puts it on"
                           " Egger Black U999 at 61x109, so 3 sheets here and 4 there",
    (18, "schedule"): "the same sheet count, in the paste block",
    (6, "pulled_pieces"): "the chat's PULLED classified lists only its 10 board lines",
    (12, "pulled_pieces"): "the chat merged eight steel-rail lines (3+2+1+2+1+1+2+2 = 14) into one row of 13",
    (9, "material_names"): "NG Runner's name for IC CC is the brand; the chat wrote the library texture",
    (17, "material_names"): "Tafisa colours: NG Runner names the brand and IC code, the chat the Tafisa colour",
    (18, "material_names"): "the chat's Charcoal (AMLM), above",
    (20, "material_names"): "Tafisa colours, as for pair 17",
}


@functools.cache
def _rows() -> tuple[dict, ...]:
    if not (PAIRS / "index.json").is_file():
        pytest.skip("no local pairs: run tools/collect_p2_fixtures.py (AGENTS.md: Fixtures)")
    index = json.loads((PAIRS / "index.json").read_text(encoding="utf-8"))
    return tuple(score.score_pair(i) for i in range(len(index)))


def _complete_in_his_format() -> list[dict]:
    return [r for r in _rows() if r["made"] and r["complete"] and r.get("format") == "his"]


def test_14_complete_closets_are_in_his_format_and_every_takeoff_cell_equals_the_chats() -> None:
    scored = _complete_in_his_format()
    assert [r["index"] for r in scored] == [1, 2, 3, 5, 6, 8, 9, 12, 17, 18, 19, 20, 24, 25]
    for name in TAKEOFF_FIELDS:
        assert [r["index"] for r in scored if r["fields"][name]["ok"] is False] == [], name


def test_every_other_difference_on_a_complete_closet_has_its_named_cause() -> None:
    misses = {(r["index"], name) for r in _complete_in_his_format() for name, f in r["fields"].items()
              if f.get("ok") is False and name != "part_rows"}
    assert misses == set(KNOWN)


def test_on_older_formats_the_board_parts_differ_only_by_the_rail_cover_rule() -> None:
    """Before Jim's rail-cover rule (2026-09-01) a rail cover printed as hardware wasn't counted. Pair
    15 joined these on 2026-10-31, complete once its 1-1/8in white shelves were built 1in AMWH (question 9):
    81 board parts, as the chat counted."""
    older = {r["index"]: r["fields"]["board_parts"] for r in _rows()
             if r["made"] and r["complete"] and r.get("format") == "other" and r.get("fields")}
    assert len(older) == 14 and sum(f["ok"] for f in older.values()) == 11
    assert older[15]["ok"]
    for i in (34, 39, 47):
        conv = conversion((PAIRS / str(i) / "summary.pdf").read_bytes(), summary_name="summary.pdf")
        rails = sum(p["qty"] for p in conv["parts"] if p["type"] == "TR Rail Cover")
        assert older[i]["ours"] - older[i]["theirs"] == rails, i


def test_an_incomplete_closet_differs_from_the_chat_by_its_could_not_verify_lines_and_no_other_piece() -> None:
    for r in _rows():
        if not (r["made"] and not r["complete"] and r.get("format") == "his"):
            continue
        conv = conversion((PAIRS / str(r["index"]) / "summary.pdf").read_bytes(), summary_name="summary.pdf")
        ours_only = r["fields"]["part_pieces"]["ours_only"]
        theirs_only = sum(row[-1] for row in r["fields"]["part_pieces"]["theirs_only"])
        # What the chat counted and we didn't is at most what we left as could not verify, plus what
        # we counted at another size (pair 26 only, below).
        assert theirs_only <= sum(u["qty"] for u in conv["unplaced"]) + sum(row[-1] for row in ours_only), r["index"]
        if r["index"] != 26:
            assert ours_only == [], r["index"]


def test_pair_26s_whole_number_drawer_faces_are_counted_as_printed_a_converter_finding() -> None:
    """Open, for ngrunner/convert/parts.py: 'DW 12x24x3/4' sits in the same numbered series as the
    nominal 'DW 08x24' and 'DW 05x24', which it leaves as could not verify, but only a leading zero
    reads as nominal, so 12x24 and 12x30 are counted as printed. The chat built them 11.2x24.5 and
    11.2x30.5. This pins the finding until the converter settles it."""
    row = next(r for r in _rows() if r["index"] == 26)
    assert row["fields"]["part_pieces"]["ours_only"] == [["DW", 12.0, 24.0, 1], ["DW", 12.0, 30.0, 1]]


def test_every_closet_made_lays_out_a_workbook_that_checks_clean() -> None:
    made = 0
    for i, _ in enumerate(_rows()):
        try:
            conv = conversion((PAIRS / str(i) / "summary.pdf").read_bytes(), summary_name="summary.pdf")
        except ConversionRefused:
            continue
        assert check_book(layout(conv), conv) == [], i
        made += 1
    assert made == 46


def test_garage_and_mixed_summaries_are_refused_whole() -> None:
    refused = [r["index"] for r in _rows() if not r["made"]]
    assert len(refused) == 6 and {r["why"] for r in _rows() if not r["made"]} == {"garage"}


# ----- the snap rule, held to SCHEMA's words


def _schema() -> str:
    from helpers_toolkit import real_toolkit
    return (real_toolkit() / "NG_Automation_docs" / "SCHEMA.md").read_text(encoding="utf-8")


def test_the_snap_depths_grid_and_tolerance_are_schemas() -> None:
    text = _schema()
    section = text[text.index("## Snap tolerances (Takeoff)"):][:900]
    depths = re.search(r"Depths → nearest of \{([^}]*)\}", section)
    assert depths and tuple(float(d) for d in depths[1].split(",")) == conversion_module.SNAP_DEPTHS_MM
    assert "Heights → nearest `32k + 20`" in section
    assert (conversion_module.SNAP_GRID_MM, conversion_module.SNAP_GRID_OFFSET_MM) == (32.0, 20.0)
    assert "Tolerance 1.3mm" in section and conversion_module.SNAP_TOLERANCE_MM == 1.3
    assert "Snap ONLY: VT depth, VT height, SH depth." in section
