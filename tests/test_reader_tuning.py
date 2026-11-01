"""
Tuning GPT-6 Luna on the DEV walls only (Sulaiman, 2026-10-31), one change at a time:
more worked walls with a note in Jim's terms, Jim's rules in the instructions, enlarged tiles of the
dimension strip and the verticals (design 3.6), three reads that must agree, and code checks on its
answer. Each change may turn a wrong value into "unsure", never the other way. The TEST walls (the
P5 jobs) are never a worked wall. Synthetic only.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json

import pytest
from PIL import Image

from tools.reader_eval import pairing, payload, prep, scoring, truth, tuning
from tools.reader_eval import synthetic as s

VT60 = prep.PartLine(1, "VT 14x60x3/4 FD FN WH")
RAD60 = prep.PartLine(1, "VT RAD 14x60x3/4 RH LN WH")
RIGHT = {"verticals": [
    {"x_in": 0.0, "bottom_in": 36.0, "top_in": 96.0, "height": "60", "radius": "no", "unsure": []},
    {"x_in": 35.0, "bottom_in": 36.0, "top_in": 96.0, "height": "60", "radius": "yes", "unsure": []}],
    "count_unsure": False, "unreadable": []}


def _pair(case: str, page: int = 4, size: tuple[int, int] = (300, 400)) -> tuple[pairing.Pair, prep.SummaryPrep]:
    walls = (s.DEFAULT_WALLS[0], s.WallSpec("2", '59 3/4"', image_size=size))
    result = prep.prepare(s.summary_pdf(walls=walls))
    pair = next(p for p in pairing.pair(case, result.crops, {"Room1": truth.read_room(s.room_des())}).pairs
                if p.wall.panels)
    return pair, result


# ----- worked walls: more of them, never from the target's job, never from TEST


def test_worked_walls_come_from_the_pool_never_the_targets_job_or_a_test_job() -> None:
    pool = [_pair(c, size=(300 + 9 * k, 400))[0] for k, c in enumerate(["X", "Y", "A", "B", "C"])]
    chosen = tuning.choose_worked(pool, target_case="A", n=3)
    assert len(chosen) == 3 and "A" not in {p.case for p in chosen}
    with pytest.raises(ValueError, match="TEST"):
        tuning.choose_worked([*pool, _pair("P07")[0]], target_case="A", n=3)


def test_worked_walls_cover_the_failing_categories_first() -> None:
    plain, _ = _pair("X")
    stacked = pairing.Pair("Y", plain.crop, "Room1", truth.read_room(s.room_des(panels=(
        s.PanelSpec("FM 84", 500, 0, 1300, 2), s.PanelSpec("WM Panel 48 Thru", 500, 1400, 700, 2)),
        others=())).walls[1])
    chosen = tuning.choose_worked([plain, stacked], target_case="A", n=1)
    assert chosen[0].case == "Y"  # the stacked tower outranks a plain wall


def test_a_worked_walls_note_is_one_line_in_jims_terms_with_no_name() -> None:
    pair, _ = _pair("X")
    note = tuning.note_for(pair)
    assert "\n" not in note and "VT RAD" in note and "35" in note
    assert s.CLIENT not in note and s.DESIGN not in note


# ----- three reads that must agree


def _three(*answers: dict) -> dict:
    return tuning.agree(list(answers), tol_x=0.125, tol_y=0.25)


def test_three_agreeing_reads_give_their_values_and_no_doubt() -> None:
    out = _three(RIGHT, RIGHT, RIGHT)
    assert out["verticals"][1]["x_in"] == 35.0 and all(not v["unsure"] for v in out["verticals"])


def test_a_value_the_reads_disagree_on_becomes_unsure() -> None:
    off = copy.deepcopy(RIGHT)
    off["verticals"][1]["x_in"] = 35.75
    out = _three(RIGHT, off, RIGHT)
    assert out["verticals"][1]["unsure"] == ["x_in"] and out["verticals"][0]["unsure"] == []


def test_reads_with_different_counts_doubt_the_count() -> None:
    short = {**RIGHT, "verticals": RIGHT["verticals"][:1]}
    assert _three(RIGHT, short, RIGHT)["count_unsure"] is True


def test_a_disagreeing_height_or_radius_becomes_unsure() -> None:
    other = copy.deepcopy(RIGHT)
    other["verticals"][1]["radius"] = "no"
    assert "radius" in _three(RIGHT, RIGHT, other)["verticals"][1]["unsure"]


def test_a_read_that_failed_doubts_the_whole_wall() -> None:
    out = tuning.agree([RIGHT, None, RIGHT], tol_x=0.125, tol_y=0.25)
    assert out["count_unsure"] is True


# ----- code checks on an answer: a failed check is unsure, never accepted


def _crop() -> prep.WallCrop:
    return prep.WallCrop(4, 0, "k", "2", 59.75, 108.0, b"", (1, 1), (1, 1), (0, 0, 0, 0), "0" * 64, (VT60, RAD60))


def test_a_clean_answer_passes_the_checks_unchanged() -> None:
    assert tuning.check({**RIGHT, "bays": [34.25]}, _crop()) == {**RIGHT, "bays": [34.25]}


def test_spacing_that_disagrees_with_the_strip_makes_the_x_unsure() -> None:
    out = tuning.check({**RIGHT, "bays": [33.0]}, _crop())
    assert "x_in" in out["verticals"][1]["unsure"]


def test_overlapping_verticals_make_both_x_unsure() -> None:
    answer = copy.deepcopy(RIGHT)
    answer["verticals"][1]["x_in"] = 0.3
    out = tuning.check(answer, _crop())
    assert all("x_in" in v["unsure"] for v in out["verticals"])


def test_a_height_the_parts_list_does_not_allow_is_unsure() -> None:
    answer = copy.deepcopy(RIGHT)
    answer["verticals"][0]["height"] = "47.4"
    assert "height" in tuning.check(answer, _crop())["verticals"][0]["unsure"]


def test_a_count_off_the_parts_list_doubts_the_count() -> None:
    assert tuning.check({**RIGHT, "verticals": RIGHT["verticals"][:1]}, _crop())["count_unsure"] is True


def test_a_check_only_ever_adds_doubt() -> None:
    doubted = copy.deepcopy(RIGHT)
    doubted["verticals"][0]["unsure"] = ["top_in"]
    doubted["verticals"][1]["unsure"] = ["top_in"]  # and the spacing check flags this vertical's x
    doubted["count_unsure"] = True
    for answer in (RIGHT, doubted, {**RIGHT, "bays": [33.0]}, {**doubted, "bays": [33.0]}):
        out = tuning.check(answer, _crop())
        assert out["count_unsure"] >= answer["count_unsure"]
        for before, after in zip(answer["verticals"], out["verticals"]):
            assert set(before["unsure"]) <= set(after["unsure"])
            assert {k: before[k] for k in payload.FIELDS} == {k: after[k] for k in payload.FIELDS}


def test_bays_are_dropped_before_scoring() -> None:
    reading = scoring.Reading("answered", tuning.strip_bays({**RIGHT, "bays": [34.25]}), 0.0)
    assert scoring.gate(reading, _crop()) is None


# ----- tiles: the dimension strip and each vertical, enlarged, each contained


def test_tiles_are_the_strip_and_one_per_vertical_each_prepared_and_within_budget() -> None:
    pair, _ = _pair("T", size=(600, 800))
    tiles = prep.tiles(pair.crop)
    assert tiles[0].kind == "strip" and [t.kind for t in tiles[1:]] == ["vertical", "vertical"]
    for t in tiles:
        img = Image.open(io.BytesIO(t.png))
        assert img.size == t.pixels and t.sha256 == hashlib.sha256(t.png).hexdigest()
        assert prep.was_prepared(t.sha256)
        assert max(img.size) <= prep.MAX_SIDE and prep.visual_tokens(*img.size) <= prep.MAX_VISUAL_TOKENS


def test_a_tile_is_a_part_of_the_crop_enlarged_never_new_pixels() -> None:
    pair, _ = _pair("T", size=(600, 800))
    strip = prep.tiles(pair.crop)[0]
    assert strip.box[2] <= 600 and strip.box[3] <= 800 and strip.pixels[0] >= strip.box[2] - strip.box[0]


def test_a_tiled_request_sends_the_tiles_and_still_passes_containment() -> None:
    target, result = _pair("T", size=(600, 800))
    worked, other = _pair("W", size=(320, 420))
    req = payload.build("T/4", target, [worked], result.deny, {"W": other.deny}, tiles=True)
    assert len(req.target.tiles) == 3
    payload.assert_contained(req)


def test_a_tile_that_prep_did_not_make_is_never_sent() -> None:
    target, result = _pair("T", size=(600, 800))
    req = payload.build("T/4", target, [], result.deny, tiles=True)
    png = s.png_bytes(s.elevation_image(90, 90, ruler=10))
    forged = payload.Image(png, hashlib.sha256(png).hexdigest(), (90, 90))
    bad = payload.WallRequest(req.wall_id, req.instructions, req.examples,
                              payload.Turn(req.target.image, req.target.text, (forged,)), req.schema, req.deny)
    with pytest.raises(payload.ContainmentError, match="not prepared"):
        payload.assert_contained(bad)


# ----- the reviewed texts: every variant's instructions and schema, and nothing else


def test_every_variants_instructions_and_schema_are_reviewed_constants() -> None:
    for rules in (False, True):
        for tiles in (False, True):
            for bays in (False, True):
                text = payload.instructions(rules=rules, tiles=tiles, bays=bays)
                assert text in payload.REVIEWED_INSTRUCTIONS
                for word in ("Jim", "Inspired", "George", "Barbara"):
                    assert word not in text
    assert payload.instructions() == payload.INSTRUCTIONS
    assert json.dumps(payload.SCHEMA_BAYS, sort_keys=True) in payload.REVIEWED_SCHEMAS


def test_the_rules_say_what_not_to_infer() -> None:
    text = payload.instructions(rules=True)
    assert "top line" in text and "where the run starts" in text and "unsure" in text


def test_a_worked_walls_note_rides_in_its_text_and_is_deny_scanned() -> None:
    target, result = _pair("T")
    worked, other = _pair("W", size=(320, 420))
    req = payload.build("T/4", target, [worked], result.deny, {"W": other.deny},
                        notes={("W", worked.crop.page): "Read left to right."})
    assert req.examples[0].turn.text.endswith("How it was read: Read left to right.")
    with pytest.raises(payload.ContainmentError):
        payload.build("T/4", target, [worked], result.deny, {"W": other.deny},
                      notes={("W", worked.crop.page): f"As {s.CLIENT} asked."})


# ----- the run: DEV and TEST apart, three reads cached apart, one TEST run


from pathlib import Path  # noqa: E402

from tools.reader_eval import budget, cases, providers, run  # noqa: E402


def _world(monkeypatch: pytest.MonkeyPatch) -> run.World:
    monkeypatch.setattr(cases, "test_cases", lambda *a, **k: ("A", "P01"))
    preps, pairings = {}, {}
    for k, letter in enumerate(("A", "P01", "X", "Y", "Z")):
        walls = (s.DEFAULT_WALLS[0], s.WallSpec("2", '59 3/4"', image_size=(300 + 7 * k, 400)))
        preps[letter] = prep.prepare(s.summary_pdf(walls=walls))
        pairings[letter] = pairing.pair(letter, preps[letter].crops, {"Room1": truth.read_room(s.room_des())})
    return run.World(preps, pairings)


def test_dev_holds_the_first_jobs_and_the_frozen_ones_and_test_only_the_p5_jobs(monkeypatch) -> None:
    world = _world(monkeypatch)
    assert {p.case for p in tuning.dev_walls(world)} == {"A", "X", "Y", "Z"}
    assert {p.case for p in tuning.test_walls(world)} == {"P01"}


def test_the_pool_never_holds_a_test_wall(monkeypatch) -> None:
    world = _world(monkeypatch)
    wide = tuning.pool_for(world, tuning.Variant("v", pool="frozen+dev"), {"A/4", "P01/4"})
    assert {p.case for p in wide} == {"X", "Y", "Z", "A"}
    assert {p.case for p in tuning.pool_for(world, tuning.Variant("v"), {"A/4"})} == {"X", "Y", "Z"}


class Cycling:
    """A fake OpenRouter reply that gives a different x for the radius on every other call."""

    def __init__(self) -> None:
        self.sent = 0

    def __call__(self, url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
        self.sent += 1
        answer = copy.deepcopy(RIGHT)
        answer["verticals"][1]["x_in"] = 35.0 if self.sent % 2 else 36.0
        reply = {"model": "openai/gpt-6-luna", "provider": "Azure",
                 "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer), "refusal": None}}],
                 "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "cost": 0.001}}
        return 200, json.dumps(reply).encode("utf-8")


def test_three_reads_are_cached_apart_and_their_disagreement_is_doubt(monkeypatch, tmp_path: Path) -> None:
    world = _world(monkeypatch)
    transport = Cycling()
    variant = tuning.Variant("sc", reads=3)
    walls = [p for p in tuning.dev_walls(world) if p.case == "A" and p.wall.panels]
    pool = tuning.pool_for(world, variant, set())
    results, counts = tuning.read_variant(world, variant, walls, pool, providers.OpenRouterLuna(transport=transport),
                                          "sk-test", budget.CostGuard(1.0), answers=tmp_path, call=True,
                                          say=lambda _: None)
    assert transport.sent == 3 and counts["calls"] == 3 and len(list(tmp_path.rglob("*.json"))) == 3
    (_, reading), = results
    assert "x_in" in reading.answer["verticals"][1]["unsure"]
    assert reading.cost_usd == pytest.approx(0.003)
    tuning.read_variant(world, variant, walls, pool, providers.OpenRouterLuna(transport=transport), "sk-test",
                        budget.CostGuard(1.0), answers=tmp_path, call=True, say=lambda _: None)
    assert transport.sent == 3  # cached: nothing is sent twice


def test_the_test_set_is_read_once_with_one_variant(tmp_path: Path) -> None:
    marker = tmp_path / "tuning_final.json"
    v = tuning.Variant("best", worked=10, notes=True)
    tuning.final_guard(marker, v)
    tuning.final_guard(marker, v)  # the same run may resume: its answers are cached
    with pytest.raises(ValueError, match="once"):
        tuning.final_guard(marker, tuning.Variant("other", worked=12))


def test_the_test_set_needs_final(monkeypatch) -> None:
    with pytest.raises(SystemExit):
        tuning.main(["--name", "x", "--set", "test"])


def test_tiles_can_go_on_the_target_wall_only() -> None:
    target, result = _pair("T", size=(600, 800))
    worked, other = _pair("W", size=(620, 820))
    req = payload.build("T/4", target, [worked], result.deny, {"W": other.deny}, tiles=True, worked_tiles=False)
    assert len(req.target.tiles) == 3 and req.examples[0].turn.tiles == ()
    payload.assert_contained(req)


def test_two_walls_with_a_byte_identical_request_are_sent_once(monkeypatch, tmp_path: Path) -> None:
    world = _world(monkeypatch)
    transport = Cycling()
    variant = tuning.Variant("dup", reads=3)
    wall = next(p for p in tuning.dev_walls(world) if p.case == "A" and p.wall.panels)
    pool = tuning.pool_for(world, variant, set())
    results, counts = tuning.read_variant(world, variant, [wall, wall], pool,
                                          providers.OpenRouterLuna(transport=transport), "sk-test",
                                          budget.CostGuard(1.0), answers=tmp_path, call=True, say=lambda _: None)
    assert transport.sent == 3 and counts["calls"] == 3  # not 6: the same request is never sent twice
    assert len(results) == 2 and results[0][1].answer == results[1][1].answer
