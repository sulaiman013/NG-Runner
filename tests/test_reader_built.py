"""
P5a's room model compared with the as-cut room (tools/reader_eval/built.py): the walls by Jim's regression
compare (his regression.py 188-220: length and height within 0.5 mm, angle within 0.01 degree, by file
order), each panel found on its wall by X, then every value right, wrong or could not verify. A frozen room
has its own panels stand in for the reading and its own chain for the plan. Synthetic only: one made-up
summary and room; the real ones are in test_build_room_real.py.
"""
from __future__ import annotations

import pytest

from ngrunner.build.room import ICLine, ReadVertical, ReadWall, TableRow
from ngrunner.build.schedule import build_room
from tools.reader_eval import built, pairing, payload, prep, scoring, truth
from tools.reader_eval import synthetic as s

CLOCKWISE = (b'PosX="0" PosY="0" Ang="0" WallNumber="2"', b'PosX="1076.325" PosY="0" Ang="270" WallNumber="2"')


@pytest.fixture(scope="module")
def world() -> tuple[bytes, tuple[prep.WallCrop, ...], truth.RoomTruth, tuple[pairing.Pair, ...]]:
    summary = s.summary_pdf()
    crops = prep.prepare(summary).crops
    room = truth.read_room(s.room_des().replace(*CLOCKWISE))
    return summary, crops, room, pairing.pair("X", crops, {"Room1": room}).pairs


def test_the_truth_keeps_each_walls_angle_and_start_and_each_panels_depth() -> None:
    room = truth.read_room(s.room_des().replace(*CLOCKWISE))
    assert [(w.ang, w.pos_mm) for w in room.walls] == [(0.0, (0.0, 0.0)), (270.0, (1076.325, 0.0))]
    assert {p.depth_mm for p in room.walls[1].panels} == {356.0}


def test_the_tables_are_read_by_ng_runners_own_summary_check(world) -> None:
    summary, crops, _, _ = world
    assert built.tables(summary) == {crops[0].design_key: (TableRow("1", 42.375, 108.0), TableRow("2", 59.75, 108.0))}


def test_the_rooms_own_chain_stands_in_for_the_plan(world) -> None:
    _, _, room, _ = world
    plan = built.plan_of(room)
    assert (plan.order, plan.angles) == (("1", "2"), (0.0, 270.0))


def test_a_frozen_room_compares_its_walls_and_every_panels_values(world) -> None:
    summary, crops, room, pairs = world
    (result,) = built.build_frozen({"X": pairs}, {"X": crops}, {"X": summary}, {"X": {"Room1": room}}, jobs=("X",))
    assert result.walls == [] and result.wall_values["angle right"] == 2 and result.wall_values["start right"] == 2
    (hung,) = [w for w in result.panels if w.panels]
    assert [p.status for p in hung.panels] == ["could not verify", "could not verify"]
    for p in hung.panels:  # drawn 96 in high with no FM panel beside them: the top line is open
        assert {k for k, v in p.values.items() if v == "could not verify"} == {"elev", "top"}
        assert {k for k, v in p.values.items() if v == "right"} == {"x", "height", "depth", "mount", "side", "radius",
                                                                    "rea", "product"}
        assert "top line" in p.why["elev"]


def test_a_value_the_model_settled_wrongly_is_wrong_never_could_not_verify(world) -> None:
    _, _, room, pairs = world
    wall = pairs[1]
    model = build_room("X room 1", (TableRow("2", 59.75, 108.0),), [ReadWall("2", (
        ICLine(1, "VT 14x60x3/4 FD FN WH"), ICLine(1, "VT 14x60x3/4 FD FN WH"), ICLine(2, "SH 14x34.3x3/4 ADJ WH")),
        (ReadVertical(0.0, 36.0, 96.0, "60", "no"), ReadVertical(35.0, 36.0, 96.0, "60", "no")))])
    found = built._read_wall_result(model, wall, "X/4")
    second = found.panels[1]
    assert second.values["radius"] == "wrong" and second.status == "wrong"


def test_panels_pair_left_to_right_and_a_missed_one_is_missing(world) -> None:
    _, _, _, pairs = world
    model = build_room("X room 1", (TableRow("2", 59.75, 108.0),), [ReadWall("2", (ICLine(1, "VT 14x60x3/4 FD FN WH"),),
                                                                             (ReadVertical(0.0, 36.0, 96.0, "60", "no"),))])
    found = built._read_wall_result(model, pairs[1], "X/4")
    assert [p.status for p in found.panels][-1] == "missing"


def test_a_reading_with_no_answer_leaves_its_wall_unread() -> None:
    crop = prep.WallCrop(4, 0, "k", "2", 59.75, 108.0, b"", (1, 1), (1, 1), (0, 0, 0, 0), "0" * 64,
                         (prep.PartLine(2, "VT 14x83.9x3/4 FD WH"),))
    wall = built.read_wall(crop, None)
    assert wall.verticals is None and wall.lines == (ICLine(2, "VT 14x83.9x3/4 FD WH"),)


def test_a_readings_doubts_reach_the_model() -> None:
    crop = prep.WallCrop(4, 0, "k", "2", 59.75, 108.0, b"", (1, 1), (1, 1), (0, 0, 0, 0), "0" * 64, ())
    answer = {"verticals": [{"x_in": 1.0, "bottom_in": 0.0, "top_in": 83.9, "height": "83.9", "radius": "no",
                             "unsure": ["x_in"]}], "count_unsure": True, "unreadable": ["other"]}
    wall = built.read_wall(crop, answer)
    assert wall.count_unsure and wall.unreadable == ("other",) and wall.verticals is not None
    assert wall.verticals[0].unsure == ("x_in",)


@pytest.mark.parametrize("why, kind", [
    ("the run is drawn 1.00 in from the wall's start, under a 3 in Wall Filler: ...", "run start"),
    ("no FM panel beside the WM run sets the top line, and the run is drawn at 96.00 in", "top line"),
    ("by geometry Left 1, by its IC lines' drill codes Thru 1: RULES 372 ...", "geometry and IC drill codes disagree"),
    ("a floor panel over 84 in: Jim's docs name only FM 84", "FM 84 or FM 90")])
def test_each_reason_is_counted_by_its_kind(why: str, kind: str) -> None:
    assert built.reason(why) == kind


def test_the_reports_name_case_tags_only(world) -> None:
    summary, crops, room, pairs = world
    frozen = built.build_frozen({"X": pairs}, {"X": crops}, {"X": summary}, {"X": {"Room1": room}}, jobs=("X",))
    answer = payload.answer_of(pairs[1])
    read = built.build_read([(pairs[1], scoring.Reading("answered", answer, 0.0))], {"X": crops}, {"X": summary},
                            {"X": {"Room1": room}})
    text = built.report_frozen(frozen) + built.report_read(read, "DEV")
    assert "X/4" in text and s.CLIENT not in text and s.DESIGN not in text and s.PROJECT not in text

