"""
The reader evaluation on the real material (docs/p2/build-spec.md 5.7), read-only: the 13 collected
summaries (.local-fixtures\\reader\\cases, from `run collect`), the 39 real rooms and Jim's frozen
regression jobs in his toolkit. Cases are named by letter; nothing here prints a job's name. Counts
measured on 2026-10-31 are pinned as floors or ceilings, so a regression fails, never fitted to.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from tools.reader_eval import cases, pairing, payload, prep, run, truth

pytestmark = pytest.mark.fixtures

KNOWN_REFUSALS = {"form_on_page", "no_drawing", "several_drawings", "text_over_drawing",
                  "annotation_over_drawing", "unreadable_drawing", "too_large"}
KNOWN_LEFT_OUT = {"no_room", "several_rooms", "no_wall_size", "no_wall_of_that_length",
                  "several_walls_of_that_length", "no_parts_list", "rotated_vertical", "count_unreadable",
                  "vertical_count_differs", "radius_count_differs", "heights_differ", "wall_paired_twice"}


@pytest.fixture(scope="module")
def world() -> run.World:
    if not (cases.reader_dir() / "cases" / "index.json").is_file():
        pytest.skip("no collected summaries: run python -m tools.reader_eval.run collect")
    try:
        return run.load()
    except cases.CaseError as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def regression() -> Path:
    path = cases.toolkit() / "NG_Automation_reference" / "reference" / "regression"
    if not (path / "baseline.json").is_file():
        pytest.skip("Jim's toolkit is not in .local-fixtures (NGR_TOOLKIT)")
    return path


# ----- prep and containment


def test_every_elevation_page_gives_a_contained_crop_or_a_refusal(world: run.World) -> None:
    crops = refused = pages = 0
    for result in world.preps.values():
        assert result.elevation_pages == len(result.crops) + len(result.refused)
        assert {r.code for r in result.refused} <= KNOWN_REFUSALS
        assert result.orphan_wall_tables == 0
        crops, refused, pages = crops + len(result.crops), refused + len(result.refused), pages + result.elevation_pages
    assert pages >= 41 and crops >= 41  # 2026-10-31: 41 elevation pages, 41 crops, none refused


def test_every_real_crop_is_native_pixels_trimmed_on_the_right_only(world: run.World) -> None:
    for result in world.preps.values():
        for crop in result.crops:
            w, h = crop.pixels
            assert max(w, h) <= prep.MAX_SIDE and prep.visual_tokens(w, h) <= prep.MAX_VISUAL_TOKENS
            assert crop.trim[0] == crop.trim[1] == crop.trim[3] == 0
            assert (w + crop.trim[2], h) == crop.native


def test_no_real_parts_line_carries_a_job_name_header_footer_or_notes(world: run.World) -> None:
    names = cases.job_names()
    words = {w.casefold() for job in names.values() for w in (job, job.split("_")[0]) if len(w) >= 3}
    for letter, result in world.preps.items():
        for crop in result.crops:
            for line in crop.parts:
                text = line.as_text()
                assert not any(w in text.casefold() for w in words), (letter, crop.page)
                assert not re.search(r"Design:|Project:|Notes:|Page \d+ of|inspiredclosetsla|\(\d{3}\) \d{3}-", text), (
                    letter, crop.page)


def test_the_page_one_and_footer_deny_list_is_built_for_every_summary(world: run.World) -> None:
    names = cases.job_names()
    for letter, result in world.preps.items():
        assert len(result.deny) >= 20
        if letter in names:
            assert result.deny.hits(names[letter].split("_")[0]), letter


# ----- truth


def test_the_truth_reader_equals_jims_baseline_schedule(regression: Path) -> None:
    baseline = json.loads((regression / "baseline.json").read_text(encoding="utf-8"))
    checked = 0
    for job, recorded in baseline.items():
        for room, schedule in recorded["schedule"].items():
            mine = truth.read_room((regression / job / "rooms" / f"{room}.des").read_bytes())
            got = sorted((p.prod, p.wall_ref, round(p.x_mm, 2), round(p.elev_mm, 2), round(p.height_mm, 2))
                         for w in mine.walls for p in w.panels)
            want = sorted((p["prod"], p["wall"], p["x"], p["elev"], p["h"]) for p in schedule["panels"])
            assert got == want, (job[:1], room)
            checked += len(want)
    assert checked == 37  # every panel the baseline records, in its 9 frozen rooms


def test_the_panel_rule_is_read_from_jims_regression_py(regression: Path) -> None:
    source = (cases.toolkit() / "NG_Automation_scripts" / "scripts" / "regression.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    panel = next(node.value.args[0].value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "PANEL" for t in node.targets))
    assert panel == truth.PANEL.pattern
    widths = [c.comparators[0].value for c in ast.walk(tree) if isinstance(c, ast.Compare)
              and isinstance(c.ops[0], ast.LtE) and "Width" in ast.unparse(c.left)]
    assert widths == [truth.PANEL_MAX_WIDTH_MM]


def test_a_products_wall_is_its_position_on_every_real_room(world: run.World) -> None:
    attrs = re.compile(rb'(\w+)="([^"]*)"')
    checked = 0
    for letter in cases.TEST_CASES + cases.WORKED_CASES:
        for data in cases.rooms(letter).values():
            lengths = [float(dict(attrs.findall(m.group(0)))[b"Len"]) for m in re.finditer(rb"<Wall [^>]*>", data)]
            for m in re.finditer(rb"<Product [^>]*>", data):
                a = dict(attrs.findall(m.group(0)))
                position = int(a[b"Wall"].split(b"_")[0])
                if position:
                    x, w = float(a[b"X"]), float(a[b"Width"])
                    assert -2 <= x and x + w <= lengths[position - 1] + 2, letter
                    checked += 1
    assert checked >= 289


# ----- pairing and payloads


def test_pairing_counts_hold(world: run.World) -> None:
    pairs = sum(len(r.pairs) for r in world.pairings.values())
    tests = sum(len(world.pairings[c].pairs) for c in cases.TEST_CASES)
    left = [x.code for r in world.pairings.values() for x in r.left_out]
    assert pairs >= 34 and tests >= 26 and len(left) <= 7  # 2026-10-31
    assert set(left) <= KNOWN_LEFT_OUT


def test_every_real_test_wall_builds_a_contained_request(world: run.World) -> None:
    worked = run.worked_walls(world)
    words = {w.casefold() for job in cases.job_names().values() for w in (job, job.split("_")[0]) if len(w) >= 3}
    built = 0
    for wall in run.test_walls(world):
        req = run.request_for(world, wall, worked)  # raises ContainmentError if anything is off
        payload.assert_contained(req)
        sent = "\n".join([req.target.text, *(e.turn.text for e in req.examples), *(e.answer for e in req.examples)])
        assert not any(w in sent.casefold() for w in words), wall.case
        built += 1
    assert built >= 26


def test_worked_walls_are_held_out_of_the_test_set(world: run.World) -> None:
    worked = run.worked_walls(world)
    assert 3 <= len(worked) <= 5 and {w.case for w in worked} <= set(cases.WORKED_CASES)
    test_hashes = {p.crop.sha256 for p in run.test_walls(world)}
    assert not test_hashes & {w.crop.sha256 for w in worked}


def test_a_paired_wall_agrees_with_its_parts_list(world: run.World) -> None:
    for p in (p for r in world.pairings.values() for p in r.pairs):
        assert pairing._as_drawn(p.crop, p.wall) is None
