"""
The reader re-scored with P5a's placement rules (tools/reader_eval/placed.py): a reading goes through
the rules room by room, a vertical a rule places takes the rule's values, every other value stays the
reader's, and the before and after are scored by the same scorer. Synthetic only: one made-up room.
"""
from __future__ import annotations

from tools.reader_eval import pairing, placed, prep, scoring, truth
from tools.reader_eval import synthetic as s

MM = 25.4
WM47 = prep.PartLine(1, "VT 14x47.4x3/4 FD FN WH")
RAD47 = prep.PartLine(1, "VT RAD 14x47.4x3/4 RH LN WH")
FM84 = prep.PartLine(2, "VT 14x83.9x3/4 FD WH")
HUNG = (s.PanelSpec("WM Panel 48 Thru", 0, 928, 1204, 2), s.PanelSpec("WM Panel 48 Right Radius", 888.95, 928, 1204, 2))


def _crop(page: int, wall: str, width: float, parts: tuple[prep.PartLine, ...]) -> prep.WallCrop:
    return prep.WallCrop(page, 0, prep.name_key_hash(s.DESIGN), wall, width, 108.0, b"", (1, 1), (1, 1),
                         (0, 0, 0, 0), "0" * 64, parts)


def _pair(parts: tuple[prep.PartLine, ...] = (WM47, RAD47)) -> pairing.Pair:
    room = truth.read_room(s.room_des(panels=HUNG, others=()))
    return pairing.Pair("T", _crop(4, "2", 59.75, parts), "Room1", room.walls[1])


def _reading(bottom: float, top: float) -> scoring.Reading:
    answer = {"verticals": [
        {"x_in": 0.0, "bottom_in": bottom, "top_in": top, "height": "47.4", "radius": "no", "unsure": []},
        {"x_in": 35.0, "bottom_in": bottom, "top_in": top, "height": "47.4", "radius": "yes", "unsure": []}],
        "count_unsure": False, "unreadable": []}
    return scoring.Reading("answered", answer, 0.03)


OTHER_WALL = _crop(3, "1", 42.375, (FM84,))  # the room's other wall: two FM panels of 83.9


def test_a_top_line_offset_is_placed_from_the_fm_panels_on_another_wall_and_then_scores_right() -> None:
    pair = _pair()
    done = placed.place_readings([(pair, _reading(33.2, 80.6))], {"T": [OTHER_WALL, pair.crop]})[0]
    assert done.top_line is not None and done.top_line.value_mm == 2132.0
    assert [v["bottom_in"] for v in done.after.answer["verticals"]] == [928 / MM] * 2
    assert [v["top_in"] for v in done.after.answer["verticals"]] == [2132 / MM] * 2
    assert scoring.score_wall(pair, done.before).silent == 4 and scoring.score_wall(pair, done.after).wrong == 0


def test_only_the_rules_values_change_every_other_value_is_the_readers() -> None:
    pair = _pair()
    done = placed.place_readings([(pair, _reading(33.2, 80.6))], {"T": [OTHER_WALL, pair.crop]})[0]
    for before, after in zip(done.before.answer["verticals"], done.after.answer["verticals"]):
        assert {k: v for k, v in before.items() if k not in ("bottom_in", "top_in")} == \
            {k: v for k, v in after.items() if k not in ("bottom_in", "top_in")}


def test_a_wall_the_rules_cannot_place_keeps_the_readers_values_and_says_why() -> None:
    pair = _pair()
    done = placed.place_readings([(pair, _reading(33.2, 80.6 + 12))], {"T": [pair.crop]})[0]
    assert done.after.answer == done.before.answer
    assert "top line" in {f.rule for f in done.could_not_verify}


def test_a_reading_with_no_answer_passes_through_unplaced() -> None:
    pair = _pair()
    refused = scoring.Reading("refused", None, 0.0)
    done = placed.place_readings([(pair, refused)], {"T": [pair.crop]})[0]
    assert done.wall is None and done.after is refused


def test_a_vertical_takes_its_height_and_mount_from_its_own_walls_lines() -> None:
    crop = _crop(4, "2", 59.75, (WM47, prep.PartLine(1, "VT 14x47.4x3/4 FD WH"), FM84,
                                 prep.PartLine(1, "WS WH 101 20.1x63.1x1-1/8 WH")))
    wall = placed.drawn_wall(crop, {"verticals": [
        {"x_in": 0.0, "bottom_in": 36.5, "top_in": 83.9, "height": "47.4", "radius": "no", "unsure": []},
        {"x_in": 20.0, "bottom_in": 0.0, "top_in": 83.9, "height": "83.9", "radius": "no", "unsure": []}]})
    assert [(v.height_mm, v.mount) for v in wall.verticals] == [(1204.0, None), (2132.0, "FM")]
    assert wall.has_counter


def test_the_fm_tops_of_a_wall_not_read_are_its_fm_pieces_standing_on_the_floor() -> None:
    assert placed.floor_fm_tops(_crop(3, "1", 42.375, (FM84, WM47))) == [2132.0, 2132.0]


def test_the_tally_counts_build_choice_errors_fixed_and_none_newly_wrong() -> None:
    pair = _pair()
    done = placed.place_readings([(pair, _reading(33.2, 80.6))], {"T": [OTHER_WALL, pair.crop]})
    pooled, cats, changed, unverified = placed.tally(done)
    assert (pooled.build_choice_before, pooled.build_choice_after, pooled.fixed_values) == (4, 0, 4)
    assert pooled.newly_wrong_walls == [] and pooled.newly_wrong_values == 0
    assert changed == {"top line": 1} and not unverified
    assert set(cats) == set(pair.wall.categories)


def test_a_silent_error_left_on_a_cut_panel_off_the_32_mm_elev_grid_is_counted_apart() -> None:
    off = tuple(s.PanelSpec(p.prod, p.x_mm, 916, 1204, 2) for p in HUNG)  # cut on 32k+20: top 2120
    pair = pairing.Pair("T", _pair().crop, "Room1", truth.read_room(s.room_des(panels=off, others=())).walls[1])
    pooled = placed.tally(placed.place_readings([(pair, _reading(36.53, 83.94))], {"T": [OTHER_WALL, pair.crop]}))[0]
    assert pooled.silent_on_passed_after == 4 and pooled.of_which_cut_off_grid == 4  # each 0.47 in off


def test_the_report_names_case_tags_only() -> None:
    pair = _pair()
    text = placed.report(placed.place_readings([(pair, _reading(33.2, 80.6))], {"T": [OTHER_WALL, pair.crop]}))
    assert "T/4" in text and s.CLIENT not in text and s.DESIGN not in text


def test_the_frozen_check_gives_back_a_frozen_rooms_top_line_from_its_own_panels() -> None:
    pair = _pair()
    rooms = placed.frozen_check({"X": [pair]}, {"X": [OTHER_WALL, pair.crop]}, jobs=("X",))
    assert len(rooms) == 1 and rooms[0].top_line.value_mm == 2132.0
    assert rooms[0].hung == (True, True) and rooms[0].hung_unverified == 0
    assert rooms[0].run_starts == ("as drawn",)
