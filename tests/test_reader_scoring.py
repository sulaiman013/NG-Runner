"""
Scoring the reader (design 3.6 and the P5 exit criteria): accept and error rates per wall category,
the share of jobs fully built, cost per wall, and silent errors counted apart. A silent error is a
wrong value the model gave without flagging doubt; one resets the P5 count. Synthetic only.
"""
from __future__ import annotations

import copy

import pytest

from tools.reader_eval import pairing, prep, scoring, truth
from tools.reader_eval import synthetic as s

VT60 = prep.PartLine(1, "VT 14x60x3/4 FD FN WH")
RAD60 = prep.PartLine(1, "VT RAD 14x60x3/4 RH LN WH")
RIGHT = {"verticals": [
    {"x_in": 0.0, "bottom_in": 36.0, "top_in": 96.0, "height": "60", "radius": "no", "unsure": []},
    {"x_in": 35.0, "bottom_in": 36.0, "top_in": 96.0, "height": "60", "radius": "yes", "unsure": []}],
    "count_unsure": False, "unreadable": []}


def _pair(case: str = "T", page: int = 4) -> pairing.Pair:
    crop = prep.WallCrop(page, 0, prep.name_key_hash(s.DESIGN), "2", 59.75, 108.0, b"", (1, 1), (1, 1),
                         (0, 0, 0, 0), "0" * 64, (VT60, RAD60))
    return pairing.Pair(case, crop, "Room1", truth.read_room(s.room_des()).walls[1])


def _read(answer: dict | None = None, *, status: str = "answered", cost: float = 0.02) -> scoring.Reading:
    return scoring.Reading(status, RIGHT if answer is None and status == "answered" else answer, cost)


def _with(change: dict, index: int = 1) -> dict:
    answer = copy.deepcopy(RIGHT)
    answer["verticals"][index].update(change)
    return answer


def _statuses(score: scoring.WallScore) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for o in score.outcomes:
        out.setdefault(o.kind, []).append(o.status)
    return out


# ----- the accept gate: code checks only, no truth


def test_a_sure_answer_that_fits_the_parts_list_is_accepted() -> None:
    assert scoring.gate(_read(), _pair().crop) is None


@pytest.mark.parametrize(("answer", "status", "why"), [
    (None, "refused", "refused"),
    (None, "truncated", "truncated"),
    ({"verticals": "nope"}, "answered", "schema_failure"),
    (_with({"unsure": ["x_in"]}), "answered", "unsure"),
    (_with({"x_in": None}), "answered", "unsure"),
    (_with({"height": "unclear"}), "answered", "unsure"),
    (_with({"radius": "unclear"}), "answered", "unsure"),
    (_with({"height": "47.4"}), "answered", "height_not_allowed"),
    (_with({"radius": "no"}), "answered", "radius_count_differs"),
    ({**RIGHT, "count_unsure": True}, "answered", "count_unsure"),
    ({**RIGHT, "unreadable": ["verticals hidden"]}, "answered", "unreadable"),
    ({**RIGHT, "verticals": RIGHT["verticals"][:1]}, "answered", "count_differs_from_parts_list"),
    ({**RIGHT, "verticals": RIGHT["verticals"][::-1]}, "answered", "not_left_to_right"),
    (_with({"top_in": 130.0}), "answered", "outside_the_wall"),
])
def test_anything_unsure_or_off_the_parts_list_declines_the_wall(answer: dict | None, status: str,
                                                                 why: str) -> None:
    assert scoring.gate(_read(answer, status=status), _pair().crop) == why


# ----- values against the as-cut truth


def test_a_right_answer_scores_every_value_right() -> None:
    score = scoring.score_wall(_pair(), _read())
    assert score.accepted and score.wrong == 0 and score.silent == 0
    assert _statuses(score) == {"order": ["right"], "x": ["right"] * 2, "bottom": ["right"] * 2,
                                "top": ["right"] * 2, "height": ["right"] * 2, "radius": ["right"] * 2}


def test_a_wrong_value_given_without_doubt_is_a_silent_error() -> None:
    score = scoring.score_wall(_pair(), _read(_with({"x_in": 30.0})))
    assert _statuses(score)["x"] == ["right", "wrong_silent"]
    assert score.silent == 1 and score.accepted  # the gate can't see it: a wrong placement


def test_a_wrong_value_flagged_unsure_is_not_silent() -> None:
    score = scoring.score_wall(_pair(), _read(_with({"x_in": 30.0, "unsure": ["x_in"]})))
    assert _statuses(score)["x"] == ["right", "wrong_flagged"] and score.silent == 0 and not score.accepted


def test_a_value_left_null_is_not_given_never_an_error() -> None:
    score = scoring.score_wall(_pair(), _read(_with({"top_in": None, "unsure": ["top_in"]})))
    assert _statuses(score)["top"] == ["right", "not_given"] and score.wrong == 0


def test_doubt_on_a_verticals_existence_flags_all_its_values() -> None:
    score = scoring.score_wall(_pair(), _read(_with({"x_in": 20.0, "bottom_in": 0.0, "unsure": ["exists"]})))
    assert score.silent == 0 and score.wrong == 2


def test_a_missing_vertical_is_a_silent_order_error_unless_the_count_was_doubted() -> None:
    missing = {**RIGHT, "verticals": RIGHT["verticals"][:1]}
    assert _statuses(scoring.score_wall(_pair(), _read(missing)))["order"] == ["wrong_silent"]
    doubted = {**missing, "count_unsure": True}
    assert _statuses(scoring.score_wall(_pair(), _read(doubted)))["order"] == ["wrong_flagged"]


def test_an_invented_vertical_is_silent_wrong_order_and_silent_wrong_values() -> None:
    extra = copy.deepcopy(RIGHT)
    extra["verticals"].append({"x_in": 50.0, "bottom_in": 36.0, "top_in": 96.0, "height": "60",
                               "radius": "no", "unsure": []})
    score = scoring.score_wall(_pair(), _read(extra))
    assert _statuses(score)["order"] == ["wrong_silent"]
    assert score.silent == 1 + 3  # the order, and the invented vertical's x, bottom and top


def test_tolerances_decide_and_the_deviation_is_kept() -> None:
    near = scoring.score_wall(_pair(), _read(_with({"x_in": 35.9, "top_in": 97.4})))
    assert near.wrong == 0
    assert max(o.deviation or 0 for o in near.outcomes if o.kind == "x") == pytest.approx(0.9, abs=0.01)
    far = scoring.score_wall(_pair(), _read(_with({"x_in": 35.9})), tol_x=0.5)
    assert far.silent == 1


def test_a_refused_wall_gives_no_values_and_no_errors() -> None:
    score = scoring.score_wall(_pair(), _read(status="refused"))
    assert not score.accepted and score.decline == "refused" and score.wrong == 0


# ----- the report


def test_rates_are_per_category_and_silent_errors_are_counted_apart() -> None:
    scores = [scoring.score_wall(_pair("A", 4), _read()),
              scoring.score_wall(_pair("B", 4), _read(_with({"x_in": 30.0}))),
              scoring.score_wall(_pair("C", 4), _read(_with({"unsure": ["x_in"]})))]
    report = scoring.summarize(scores, required={"A": {"A/4"}, "B": {"B/4"}, "C": {"C/4"}})
    radius = report.categories["radius"]
    assert (radius.walls, radius.accepted, radius.accepted_wrong, radius.silent_errors) == (3, 2, 1, 1)
    assert radius.accept_rate == pytest.approx(2 / 3) and radius.error_rate == pytest.approx(1 / 2)
    assert report.silent_errors == 1 and report.wrong_placements == 1
    assert (report.jobs_fully_built, report.jobs) == (1, 3)
    assert report.cost_per_wall == pytest.approx(0.02)


def test_a_job_with_a_wall_left_out_is_never_fully_built() -> None:
    report = scoring.summarize([scoring.score_wall(_pair("A", 4), _read())], required={"A": {"A/4", "A/6"}})
    assert report.jobs_fully_built == 0


def test_the_report_text_names_cases_and_counts_only() -> None:
    report = scoring.summarize([scoring.score_wall(_pair("A", 4), _read())], required={"A": {"A/4"}})
    text = report.text()
    assert "Silent errors: 0" in text and s.CLIENT not in text
