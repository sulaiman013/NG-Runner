"""
Telling a reading error from an offset Jim's build makes on purpose (P5 conditions): the as-cut .des
follows his rules where the drawing does not, so a wrong value is split by rule:
  run start    every vertical's x off by one amount, and the cut run tight to a wall end
  top line     every hung vertical off by one amount, the cut ones sharing one top (RULES: one T)
  stacked gap  the shifted verticals are exactly the uppers of stacked towers
  reading error  anything else: a vertical misplaced on its own, a wrong count, height or radius
Synthetic only.
"""
from __future__ import annotations

import copy

from tools.reader_eval import diagnose, pairing, prep, scoring, truth
from tools.reader_eval import synthetic as s

VT60 = prep.PartLine(1, "VT 14x60x3/4 FD FN WH")
RAD60 = prep.PartLine(1, "VT RAD 14x60x3/4 RH LN WH")
RIGHT = {"verticals": [
    {"x_in": 0.0, "bottom_in": 36.0, "top_in": 96.0, "height": "60", "radius": "no", "unsure": []},
    {"x_in": 35.0, "bottom_in": 36.0, "top_in": 96.0, "height": "60", "radius": "yes", "unsure": []}],
    "count_unsure": False, "unreadable": []}


def _pair(panels: tuple = (), parts: tuple = (VT60, RAD60)) -> pairing.Pair:
    room = truth.read_room(s.room_des(panels=panels, others=())) if panels else truth.read_room(s.room_des())
    crop = prep.WallCrop(4, 0, prep.name_key_hash(s.DESIGN), "2", 59.75, 108.0, b"", (1, 1), (1, 1),
                         (0, 0, 0, 0), "0" * 64, parts)
    return pairing.Pair("T", crop, "Room1", room.walls[1])


def _read(answer: dict) -> scoring.Reading:
    return scoring.Reading("answered", answer, 0.04)


def _shift(answer: dict, field: str, by: float, which: tuple[int, ...] | None = None) -> dict:
    out = copy.deepcopy(answer)
    for i, v in enumerate(out["verticals"]):
        if which is None or i in which:
            v[field] = round(v[field] + by, 3)
    return out


def test_a_right_wall_is_right() -> None:
    d = diagnose.diagnose(_pair(), _read(RIGHT))
    assert d.kinds == ("right",) and d.silent_offset == d.silent_reading == 0


def test_a_whole_run_shifted_off_a_wall_tight_run_is_the_run_start() -> None:
    d = diagnose.diagnose(_pair(), _read(_shift(RIGHT, "x_in", 1.5)))
    assert d.kinds == ("run start",) and (d.silent_offset, d.silent_reading) == (2, 0)


def test_a_whole_run_shifted_when_the_cut_run_is_not_at_a_wall_end_is_a_reading_error() -> None:
    panels = (s.PanelSpec("WM Panel 48 Thru", 100, 914.4, 1524, 2),
              s.PanelSpec("WM Panel 48 Right Radius", 988.95, 914.4, 1524, 2))
    answer = copy.deepcopy(RIGHT)
    answer["verticals"][0]["x_in"], answer["verticals"][1]["x_in"] = 100 / 25.4 + 1.5, 988.95 / 25.4 + 1.5
    d = diagnose.diagnose(_pair(panels), _read(answer))
    assert d.kinds == ("reading error",) and d.silent_reading == 2


def test_every_hung_vertical_off_by_one_amount_is_the_top_line() -> None:
    answer = _shift(_shift(RIGHT, "bottom_in", -3.3), "top_in", -3.3)
    d = diagnose.diagnose(_pair(), _read(answer))
    assert d.kinds == ("top line",) and (d.silent_offset, d.silent_reading) == (4, 0)


def test_only_the_stacked_uppers_off_by_one_amount_is_the_stacked_gap() -> None:
    panels = (s.PanelSpec("FM 84", 0, 0, 2132, 2), s.PanelSpec("FM 84", 500, 0, 1300, 2),
              s.PanelSpec("WM Panel 48 Thru", 500, 1400, 700, 2))
    parts = (prep.PartLine(1, "VT 14x83.9x3/4 FD WH"), prep.PartLine(1, "VT 14x51.2x3/4 FD WH"),
             prep.PartLine(1, "VT 14x27.6x3/4 FD FN WH"))
    answer = {"verticals": [
        {"x_in": 0.0, "bottom_in": 0.0, "top_in": 83.94, "height": "83.9", "radius": "no", "unsure": []},
        {"x_in": 19.69, "bottom_in": 0.0, "top_in": 51.18, "height": "51.2", "radius": "no", "unsure": []},
        {"x_in": 19.69, "bottom_in": 55.52, "top_in": 83.08, "height": "27.6", "radius": "no", "unsure": []}],
        "count_unsure": False, "unreadable": []}
    d = diagnose.diagnose(_pair(panels, parts), _read(answer))
    assert d.kinds == ("stacked gap",) and (d.silent_offset, d.silent_reading) == (2, 0)


def test_one_vertical_misplaced_on_its_own_is_a_reading_error() -> None:
    answer = copy.deepcopy(RIGHT)
    answer["verticals"][1]["x_in"] = 30.0
    d = diagnose.diagnose(_pair(), _read(answer))
    assert d.kinds == ("reading error",) and d.silent_reading == 1


def test_a_wrong_count_height_or_radius_is_a_reading_error() -> None:
    missing = {**RIGHT, "verticals": RIGHT["verticals"][:1]}
    assert diagnose.diagnose(_pair(), _read(missing)).kinds == ("reading error",)
    radius = copy.deepcopy(RIGHT)
    radius["verticals"][1]["radius"] = "no"
    d = diagnose.diagnose(_pair(), _read(radius))
    assert d.kinds == ("reading error",) and d.silent_reading == 1


def test_an_offset_and_a_reading_error_on_one_wall_are_both_named() -> None:
    answer = _shift(_shift(RIGHT, "bottom_in", -3.3), "top_in", -3.3)
    answer["verticals"][1]["radius"] = "no"
    d = diagnose.diagnose(_pair(), _read(answer))
    assert d.kinds == ("reading error", "top line") and (d.silent_offset, d.silent_reading) == (4, 1)


def test_a_flagged_wrong_value_is_counted_apart_from_a_silent_one() -> None:
    answer = _shift(RIGHT, "x_in", 1.5)
    answer["verticals"][0]["unsure"] = ["x_in"]
    d = diagnose.diagnose(_pair(), _read(answer))
    assert (d.flagged_offset, d.silent_offset) == (1, 1)


def test_the_split_adds_up_to_the_scores_silent_errors() -> None:
    for answer in (RIGHT, _shift(RIGHT, "x_in", 1.5), _shift(_shift(RIGHT, "bottom_in", -3.3), "top_in", -3.3)):
        d = diagnose.diagnose(_pair(), _read(answer))
        assert d.silent_offset + d.silent_reading == scoring.score_wall(_pair(), _read(answer)).silent


def test_no_answer_is_no_diagnosis() -> None:
    d = diagnose.diagnose(_pair(), scoring.Reading("refused", None, 0.0))
    assert d.kinds == ("no answer",)


def test_on_a_stacked_tower_wall_uniformly_shifted_hung_verticals_are_the_stacked_gap() -> None:
    panels = (s.PanelSpec("FM 84", 76.2, 0, 884, 2), s.PanelSpec("FM 84", 348.6, 903, 1428, 2),
              s.PanelSpec("FM 84", 1000, 0, 884, 2), s.PanelSpec("FM 84", 1000, 903, 1428, 2))
    parts = (prep.PartLine(2, "VT 14x34.8x3/4 FD WH"), prep.PartLine(2, "VT 14x56.2x3/4 FD WH"))
    up = 903 / 25.4 + 0.4
    answer = {"verticals": [
        {"x_in": 3.0, "bottom_in": 0.0, "top_in": 34.8, "height": "34.8", "radius": "no", "unsure": []},
        {"x_in": 13.72, "bottom_in": up, "top_in": up + 56.22, "height": "56.2", "radius": "no", "unsure": []},
        {"x_in": 39.37, "bottom_in": 0.0, "top_in": 34.8, "height": "34.8", "radius": "no", "unsure": []},
        {"x_in": 39.37, "bottom_in": up, "top_in": up + 56.22, "height": "56.2", "radius": "no", "unsure": []}],
        "count_unsure": False, "unreadable": []}
    d = diagnose.diagnose(_pair(panels, parts), _read(answer))
    assert d.kinds == ("stacked gap",) and d.silent_reading == 0
