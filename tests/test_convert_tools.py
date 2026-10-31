"""
The converter's two dev tools: tools/collect_p2_fixtures.py (copies the real pairs by index) and
tools/score_takeoff.py (scores a takeoff against the chat's JSON on named fields), on made-up
folders and files (docs/p2/build-spec.md, sections 4 and 5.4).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from ngrunner.convert import rules
from ngrunner.convert.parts import BoardPart
from ngrunner.convert.takeoff import takeoff

ROOT = Path(__file__).resolve().parent.parent


def _tool(name: str):  # noqa: ANN202 - a module loaded from tools/
    spec = importlib.util.spec_from_file_location(f"ngr_tools_{name}", ROOT / "tools" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


collect = _tool("collect_p2_fixtures")
scorer = _tool("score_takeoff")


# ----- collecting


def _job(root: Path, name: str, *, summary: bool = True, conversion: bool = True,
         book: bool = True, mtime: float = 1.0e9) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    if summary:
        (folder / f"{name}_summary.pdf").write_bytes(b"%PDF-1.4 made up")
    if conversion:
        path = folder / f"{name}_IC_Conversion.json"
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (mtime, mtime))
    if book:
        (folder / f"{name}_IC_Conversion.xlsx").write_bytes(b"PK made up")


def test_pairs_are_copied_by_index_newest_json_first_with_only_their_place(tmp_path: Path) -> None:
    ng = tmp_path / "NG Jobs"
    _job(ng, "Testerson_101426", mtime=1.0e9)
    _job(ng, "Fakename_090126", book=False, mtime=3.0e9)
    _job(ng, "Nosummary_080126", summary=False)
    _job(ng / "Old", "Oldtest_070126", mtime=2.0e9)
    dest = tmp_path / "pairs"
    found = collect.collect(ng, dest)
    assert [p.place for p in found] == ["top", "old", "top"]
    assert json.loads((dest / "index.json").read_text(encoding="utf-8")) == [
        {"place": "top"}, {"place": "old"}, {"place": "top"}]
    assert sorted(f.name for f in (dest / "0").iterdir()) == ["conversion.json", "summary.pdf"]
    assert sorted(f.name for f in (dest / "2").iterdir()) == [
        "conversion.json", "conversion.xlsx", "summary.pdf"]
    assert "Testerson" not in (dest / "index.json").read_text(encoding="utf-8")


def test_collecting_refuses_to_run_over_an_earlier_copy(tmp_path: Path) -> None:
    ng = tmp_path / "NG Jobs"
    _job(ng, "Testerson_101426")
    (tmp_path / "pairs").mkdir()
    with pytest.raises(collect.CollectError):
        collect.collect(ng, tmp_path / "pairs")


def test_a_folder_with_two_conversions_is_not_a_pair(tmp_path: Path) -> None:
    _job(tmp_path, "Testerson_101426")
    (tmp_path / "Testerson_101426" / "Testerson_101426b_IC_Conversion.json").write_text(
        "{}", encoding="utf-8")
    assert collect.pair_in(tmp_path / "Testerson_101426", "top") is None


# ----- scoring


def _ours() -> object:
    parts = [BoardPart("SH", 10, 24, 48, 0.75, 0.75, ("WH",), "WH", False, "pulled"),
             BoardPart("BP", 2, 24, 48, 0.375, 0.25, ("WH",), "WH", False, "pulled")]
    return takeoff(parts, sheet_size=rules.sheet_size, branch="closet", estimate_total=7999.05)


LIST_SHAPE = {
    "total_board_parts": 12,
    "buckets": [{"material": "Egger Classic White", "thk_in": 0.75, "parts": 10, "sqft": 80.0,
                 "sheets_with_waste": 3, "calc_sheets_raw": 2.5, "edgeband_lf": 40.0},
                {"material": "White 1/4 Vinyl", "thk_in": 0.25, "parts": 2, "sqft": 16.0,
                 "sheets_with_waste": 1, "calc_sheets_raw": 0.5, "edgeband_lf": 0.0}],
    "edgeband": {"rows": [{"material": "Egger Classic White 1mm", "linear_feet": 40.0}]},
    "estimate": {"total": "$7,999.05"},
    "designs": [{"name": "Testerson - Primary Closet"}],
    "client": "Testerson Family",
}


def test_the_list_shape_scores_every_named_field() -> None:
    s = scorer.score(_ours(), LIST_SHAPE, designs=1)
    assert (s.parts.ok, s.area.ok, s.tape.ok, s.estimate.ok, s.designs.ok) == (
        True, True, True, True, True)
    assert {k: m.ok for k, m in s.sqft_by_thk.items()} == {"0.25": True, "0.75": True}
    assert {k: m.ok for k, m in s.buckets.items()} == {"WH 0.25": True, "WH 0.75": True}


def test_a_field_the_file_lacks_is_not_scored_never_a_miss() -> None:
    s = scorer.score(_ours(), {"client": "Testerson Family"})
    assert (s.parts.ok, s.area.ok, s.tape.ok, s.estimate.ok, s.designs.ok) == (
        None, None, None, None, None)


def test_the_older_dict_shape_reads_by_thickness() -> None:
    data = {"buckets": {"0.75": {"sqft": 80.0, "sheets": 3}, "0.25": {"sqft": 16.0, "sheets": 1}},
            "edgeband_lf": 40.04, "estimate_total": 7999.05}
    s = scorer.score(_ours(), data)
    assert (s.area.ok, s.tape.ok, s.estimate.ok) == (True, True, True)


def test_a_miss_is_scored_as_a_miss_with_both_numbers() -> None:
    data = dict(LIST_SHAPE, total_board_parts=13)
    s = scorer.score(_ours(), data)
    assert (s.parts.ours, s.parts.theirs, s.parts.ok) == (12, 13.0, False)


def test_the_score_holds_no_client_field() -> None:
    assert "Testerson" not in repr(scorer.score(_ours(), LIST_SHAPE, designs=1))
