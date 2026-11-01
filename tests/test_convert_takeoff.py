"""
The takeoff (ngrunner/convert/takeoff.py): board parts summed by colour and built thickness, with
RULES' sheets, the 4x8 basis and tape, and the frozen jobs and real pairs it is scored on
(docs/p2/build-spec.md, sections 3.2, 5.3 and 6.3).

Synthetic parts use made-up codes. Real files are read only under the fixtures mark, by index,
and no test prints a real name.
"""
from __future__ import annotations

import functools
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from ngrunner.convert import convert, rules, takeoff_branch
from ngrunner.convert.parts import BoardPart, Unplaced, board_part
from ngrunner.convert.summary_lines import SummaryLine, pulled_lines, read_pages
from ngrunner.convert.takeoff import Bucket, Takeoff, takeoff

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(os.environ.get("NGR_FIXTURES", ROOT / ".local-fixtures"))
TOOLKIT = Path(os.environ.get("NGR_TOOLKIT", FIXTURES / "toolkit" / "20261031"))
FROZEN = TOOLKIT / "NG_Automation_reference" / "reference" / "regression"


def bp(kind: str, qty: int, a: float, b: float, built: float, colour: str = "WH", *,
       wall_mount: bool = False, printed: float | None = None) -> BoardPart:
    return BoardPart(kind, qty, a, b, built if printed is None else printed, built, (colour,), colour,
                     wall_mount, "pulled")


def lost(reason: str = "a SH printed 0.6in thick") -> Unplaced:
    return Unplaced(SummaryLine("CTO", "SH-WH", 2, "SH 14x24x0.6 FIX WH", 8, 0), reason)


def closet(parts: list[BoardPart | Unplaced], **kw: object) -> Takeoff:
    return takeoff(parts, sheet_size=rules.job_sheet_size(
        [p for p in parts if isinstance(p, BoardPart)]), branch="closet", **kw)  # type: ignore[arg-type]


# ----- buckets


@pytest.mark.parametrize("colour, thk, sheet, sheets", [
    ("WH", 0.75, (49.0, 97.0), 3),      # RULES 122: Egger Classic White
    ("WH", 1.0, (49.0, 97.0), 3),
    ("WH", 0.25, (61.0, 109.0), 2),     # RULES 133: 1-4 Egger
    ("CC", 0.75, (61.0, 109.0), 2),     # RULES 123: Egger Black U999, as RULES says today (question 12 open)
    ("CC", 1.0, (49.0, 97.0), 3),
    ("AP", 0.75, (49.0, 97.0), 3),      # RULES 124: Tafisa
    ("AP", 0.25, (49.0, 97.0), 3),      # RULES 134: 1-4 Tafisa in that colour
    ("IV", 0.75, (49.0, 97.0), 3),      # RULES 125: Arauco
])
def test_each_rules_table_line_gives_its_sheet(colour: str, thk: float, sheet: tuple,
                                               sheets: int) -> None:
    kind = "BP" if thk == 0.25 else "SH"
    t = closet([bp(kind, 10, 24, 48, thk, colour)])     # 80 sqft: 88 with waste
    (bucket,) = t.buckets
    assert (bucket.colour, bucket.thk_in, bucket.sheet_in) == (colour, thk, sheet)
    assert bucket.sheets_with_waste == sheets


def test_a_white_part_built_one_inch_makes_the_whole_jobs_white_amwh() -> None:
    """Question 10: one white part built 1in or thicker makes the whole job's white AMWH (RULES 214,
    and the RULES of 2026-10-06; Jim's yes awaited), so its 1/4 white backs go on White 1/4 Vinyl at
    49x97 (RULES 135), not 1-4 Egger at 61x109, wherever they are on the job."""
    parts = [bp("SH", 2, 22, 46, 1.0), bp("BP", 3, 24, 48, 0.25), bp("VT", 4, 14, 84, 0.75)]
    t = closet(parts)
    assert [(b.thk_in, b.sheet_in, b.sheets_with_waste) for b in t.buckets] == [
        (0.25, (49.0, 97.0), 1), (0.75, (49.0, 97.0), 2), (1.0, (49.0, 97.0), 1)]
    assert t.could_not_verify == () and t.complete
    assert rules.thick_white(parts) and rules.job_brand("WH", amwh=True) == "AMWH"
    assert rules.job_brand("AP", amwh=True) == "Tafisa"              # only white is pushed to AMWH


def test_without_a_thick_white_part_white_backs_are_1_4_egger_at_61x109() -> None:
    """Question 11: Egger Classic White backs go on 1-4 Egger, 5x9 (RULES 133)."""
    parts = [bp("BP", 3, 24, 48, 0.25), bp("VT", 4, 14, 84, 0.75)]
    t = closet(parts)
    assert [(b.thk_in, b.sheet_in) for b in t.buckets] == [(0.25, (61.0, 109.0)), (0.75, (49.0, 97.0))]
    assert not rules.thick_white(parts) and rules.job_brand("WH", amwh=False) == "Egger Classic White"


def test_a_colour_the_rules_dont_size_has_no_sheet_never_49x97() -> None:
    t = closet([bp("SH", 4, 14, 24, 0.75, "NV")])
    (bucket,) = t.buckets
    assert (bucket.sheet_in, bucket.sheets_with_waste) == (None, None)
    assert bucket.sqft == pytest.approx(9.33)
    assert t.could_not_verify == ("No sheet size for 3/4 NV: could not verify its sheets.",)
    assert not t.complete


def test_the_ceiling_is_taken_once_per_bucket_project_wide() -> None:
    t = closet([bp("SH", 10, 24, 48, 0.75), bp("VT", 10, 24, 48, 0.75)])
    (bucket,) = t.buckets
    assert (bucket.parts, bucket.sqft, bucket.sheets_with_waste) == (20, 160.0, 6)


def test_buckets_split_by_colour_and_built_thickness() -> None:
    t = closet([bp("SH", 1, 12, 24, 0.75, "AP"), bp("SH", 1, 12, 24, 0.75, "LG"),
                bp("BP", 1, 12, 24, 0.25, "AP")])
    assert [(b.colour, b.thk_in) for b in t.buckets] == [("AP", 0.25), ("AP", 0.75), ("LG", 0.75)]


def test_both_sheet_numbers_are_carried() -> None:
    t = closet([bp("SH", 10, 24, 48, 0.75)])
    (bucket,) = t.buckets
    assert (bucket.sheets_with_waste, bucket.calc_sheets_32) == (3, 2.5)


def test_tape_sums_per_bucket_and_in_all() -> None:
    t = closet([bp("SH", 2, 14, 24, 0.75), bp("VT", 1, 14, 48, 0.75, wall_mount=True),
                bp("BP", 1, 24, 48, 0.25), bp("DR", 1, 12, 24, 0.75, "AP")])
    by = {(b.colour, b.thk_in): b.tape_lf for b in t.buckets}
    assert by == {("WH", 0.75): round(4 + 76 / 12, 1), ("WH", 0.25): 0.0, ("AP", 0.75): 6.0}
    assert (t.board_parts, t.tape_lf) == (5, round(4 + 76 / 12 + 6, 1))


def test_area_is_rounded_as_takeoff_py_rounds_it() -> None:
    (bucket,) = closet([bp("SH", 3, 14, 23.9, 0.75)]).buckets
    assert bucket.sqft == round(3 * 14 * 23.9 / 144, 2)


# ----- what can't be verified


def test_unplaced_rows_stay_out_of_the_counts_and_make_it_incomplete() -> None:
    t = closet([bp("SH", 2, 14, 24, 0.75), lost()])
    assert (t.board_parts, len(t.unplaced), t.complete) == (2, 1, False)
    assert t.could_not_verify == ("Page 9: 2 of item SH-WH, a SH printed 0.6in thick",)


def test_a_complete_takeoff_has_nothing_to_verify() -> None:
    t = closet([bp("SH", 2, 14, 24, 0.75)])
    assert (t.complete, t.could_not_verify) == (True, ())


def test_the_takeoff_keeps_no_row_text() -> None:
    t = closet([lost()])
    assert t.unplaced[0].line.desc == "" and "SH 14x24x0.6" not in repr(t)


def test_the_estimate_and_the_gate_are_carried() -> None:
    t = closet([bp("SH", 1, 14, 24, 0.75)], estimate_total=7999.05,
               gate_pulled_equals_designs=True)
    assert (t.estimate_total, t.gate_pulled_equals_designs, t.branch) == (7999.05, True, "closet")


def test_a_part_left_without_a_colour_is_refused() -> None:
    cover = BoardPart("TR Rail Cover", 1, 3.5, 96, 0.0, 0.75, (), "", False, "pulled_hardware")
    with pytest.raises(ValueError):
        closet([cover])


# ----- garage and mixed jobs stay in chat (ruling R4)


@pytest.mark.parametrize("branch", ["garage", "mixed", "unclear"])
def test_a_job_that_is_not_a_closet_takes_no_board_part(branch: str) -> None:
    with pytest.raises(ValueError):
        takeoff([bp("SH", 1, 14, 24, 0.75)], sheet_size=rules.sheet_size, branch=branch)
    t = takeoff([lost("a garage job")], sheet_size=rules.sheet_size, branch=branch)
    assert (t.branch, t.buckets, t.board_parts, t.complete) == (branch, (), 0, False)


def test_a_mixed_summary_is_mixed_and_one_garage_design_is_garage() -> None:
    assert takeoff_branch([("Testerson - Primary", "Closet")]) == "closet"
    assert takeoff_branch([("Testerson - Garage", "Garage")]) == "garage"
    assert takeoff_branch([("Testerson - Primary", "Closet"),
                           ("Testerson - Garage", "Garage")]) == "mixed"
    assert takeoff_branch([("Testerson - Primary", "")]) == "unclear"
    assert takeoff_branch([]) == "unclear"


# ----- the frozen jobs (local only): the regression


def _frozen() -> list[tuple[str, dict]]:
    base = FROZEN / "baseline.json"
    if not base.is_file():
        pytest.skip("no local toolkit: set NGR_TOOLKIT (docs/p2/build-spec.md, section 4)")
    return list(json.loads(base.read_text(encoding="utf-8")).items())


def _frozen_takeoff(job: str) -> Takeoff:
    return convert((FROZEN / job / "summary.pdf").read_bytes(), branch="closet")


def _as_baseline(t: Takeoff) -> dict:
    """takeoff.py's shape: buckets by built thickness, sheets on RULES' sheet."""
    buckets: dict[str, dict] = {}
    for b in t.buckets:
        key = f"{b.thk_in:g}"
        if key in buckets:
            raise AssertionError("two colours at one thickness: not takeoff.py's shape")
        buckets[key] = {"parts": b.parts, "sqft": b.sqft, "sheets": b.sheets_with_waste}
    return {"board_parts": t.board_parts, "buckets": buckets, "edgeband_lf": t.tape_lf}


@pytest.mark.fixtures
def test_frozen_jobs_x_and_z_equal_the_baseline_takeoff() -> None:
    jobs = _frozen()
    for k in (0, 2):
        job, recorded = jobs[k]
        t = _frozen_takeoff(job)
        want = recorded["takeoff"]
        assert t.complete, f"frozen job {k}"
        assert _as_baseline(t) == {key: want[key] for key in ("board_parts", "buckets",
                                                              "edgeband_lf")}, f"frozen job {k}"


@pytest.mark.fixtures
def test_frozen_job_y_differs_only_by_its_two_named_rows() -> None:
    """Y's chat typed a Door Euro printed 0.7in thick as a 3/4 DR (its own note: see Q1), and
    left out the rail cover printed as hardware. The converter places the cover (RULES 224) and
    can't place the door: so its parts are 36 = 35 + 1, its area the baseline's less the door
    plus the cover, and its tape the same."""
    job, recorded = _frozen()[1]
    t = _frozen_takeoff(job)
    want = recorded["takeoff"]
    door_sqft, door_tape = 23.8 * 15.6 / 144, 2 * (23.8 + 15.6) / 12
    cover_sqft, cover_tape = 3.5 * 96 / 144, 2 * 96 / 12
    assert [u.reason for u in t.unplaced] == [
        "a DR printed 0.7in thick, which the rules don't name for it"]
    assert t.board_parts == want["board_parts"] == 36
    (bucket,) = t.buckets
    assert bucket.sqft == pytest.approx(want["buckets"]["0.75"]["sqft"] - door_sqft + cover_sqft,
                                        abs=0.01)
    assert t.tape_lf == pytest.approx(want["edgeband_lf"] - door_tape + cover_tape, abs=0.1)


# ----- the real pairs (local only, by index): scored, never fitted to
#
# Indexes are the build spec's pair codes (0 is PAA): tools/collect_p2_fixtures.py keeps its order.
# Measured 2026-10-31 on the 52 pairs. A count here is a floor or a named cause, never a target:
# when one moves, find why before changing it.

PAN, PAO, PAV, PBA = 13, 14, 21, 26


@functools.cache
def _scored() -> tuple[dict, ...]:
    pairs = FIXTURES / "p2" / "pairs" / "index.json"
    if not pairs.is_file():
        pytest.skip("no local pairs: run tools/collect_p2_fixtures.py (AGENTS.md: Fixtures)")
    scorer = _scorer()
    index = json.loads(pairs.read_text(encoding="utf-8"))
    return tuple(dict(scorer.score_pair(i), place=entry["place"]) for i, entry in enumerate(index))


def _scorer():  # noqa: ANN202 - tools/score_takeoff.py, loaded by path
    spec = importlib.util.spec_from_file_location("ngr_tools_score_takeoff",
                                                  ROOT / "tools" / "score_takeoff.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _closets(place: str, measure: str) -> list[dict]:
    return [r for r in _scored() if r["place"] == place and r["branch"] == "closet"
            and r["score"][measure]["ok"] is not None]


@pytest.mark.fixtures
def test_only_closets_are_converted_and_garage_and_mixed_jobs_stay_in_chat() -> None:
    branches = [r["branch"] for r in _scored()]
    assert {b: branches.count(b) for b in set(branches)} == {"closet": 46, "garage": 4, "mixed": 2}
    assert all(r["score"]["parts"]["ours"] == 0 for r in _scored() if r["branch"] != "closet")


@pytest.mark.fixtures
def test_every_top_level_closet_equals_the_chat_or_differs_by_exactly_its_unplaced_rows() -> None:
    rows = _closets("top", "parts")
    explained = [r["index"] for r in rows if r["score"]["parts"]["ours"] in (
        r["score"]["parts"]["theirs"], r["score"]["parts"]["theirs"] - r["unplaced_qty"])]
    assert (len(rows), len(explained)) == (23, 23)
    assert sum(r["score"]["parts"]["ok"] for r in rows) >= 17


@pytest.mark.fixtures
def test_top_level_area_and_tape_miss_only_where_a_row_is_unplaced() -> None:
    for measure, floor, held in (("area", 17, 23), ("tape", 17, 22)):
        rows = _closets("top", measure)
        assert len(rows) == held
        assert sum(r["score"][measure]["ok"] for r in rows) >= floor
        assert [r["index"] for r in rows if not r["score"][measure]["ok"] and not r["unplaced_qty"]
                ] == [], measure


@pytest.mark.fixtures
def test_old_misses_are_rail_covers_their_conversions_left_out_but_two_named_files() -> None:
    """Conversions older than the rail-cover rule (2026-09-01) left out a rail cover printed as
    hardware. The two files left: one whose own total disagrees with its parts list, and one
    that covers a single room of its summary (its old one-room shape)."""
    rows = _closets("old", "parts")
    unexplained = []
    for r in rows:
        data = (FIXTURES / "p2" / "pairs" / str(r["index"]) / "summary.pdf").read_bytes()
        covers = sum(p.qty for line in pulled_lines(read_pages(data))
                     if isinstance(p := board_part(line), BoardPart) and p.source == "pulled_hardware")
        ours, theirs = r["score"]["parts"]["ours"], r["score"]["parts"]["theirs"]
        if theirs not in (ours, ours - covers + r["unplaced_qty"]):
            unexplained.append(r["index"])
    assert len(rows) == 22
    assert sum(r["score"]["parts"]["ok"] for r in rows) >= 12
    assert len(unexplained) == 2, unexplained


@pytest.mark.fixtures
def test_the_spec_s_named_pairs_give_exactly_their_unplaced_rows() -> None:
    by = {r["index"]: r for r in _scored()}
    assert by[PAN]["branch"] == "mixed"
    assert _reasons(by[PAO]) == {"0.6in thick": 4}          # its 2 glass shelves are bought in (question 8)
    assert _reasons(by[PAV]) == {"0.6in thick": 1}
    assert _reasons(by[PBA]) == {"the Shelf form": 10, "a nominal size": 8}


def _reasons(row: dict) -> dict[str, int]:
    """Unplaced quantities by reason, read again from the summary (the score keeps reasons only)."""
    data = (FIXTURES / "p2" / "pairs" / str(row["index"]) / "summary.pdf").read_bytes()
    t = convert(data, branch="closet")
    out: dict[str, int] = {}
    for u in t.unplaced:
        key = next(k for k in ("0.6in thick", "a kind the rules don't name", "the Shelf form",
                               "a nominal size")
                   if k in u.reason)
        out[key] = out.get(key, 0) + u.line.qty
    return out


@pytest.mark.fixtures
def test_the_design_lists_gate_and_the_estimates() -> None:
    rows = _scored()
    assert sum(r["gate"] for r in rows) == 44
    held = [r for r in rows if r["score"]["estimate"]["ok"] is not None]
    assert (len(held), sum(r["score"]["estimate"]["ok"] for r in held)) == (46, 46)
