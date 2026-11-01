"""
P5a's plan reader (ngrunner/build/plan.py): a design's plan view read as its wall chain, the order of the
walls and each one's angle, from pixels and the WALL/AREA table together, and "could not verify" with the
reason wherever the two don't settle it. Every plan here is drawn by tests/helpers_plan.py, as IC draws
its own, and every room is made up.
"""
from __future__ import annotations

import pytest
from helpers_plan import as_pdf, draw_plan, rows
from PIL import Image, ImageDraw, ImageFont

from ngrunner.build import plan as pr
from ngrunner.build.room import Plan, TableRow
from ngrunner.build.walls import NO_PLAN, build_walls

U = [("1", 24.0, 90.0), ("2", 80.0, 0.0), ("3", 30.0, 270.0)]
REACH_IN = [("1", 4.5, 180.0), ("2", 24.0, 90.0), ("3", 80.0, 0.0), ("4", 24.0, 270.0), ("5", 6.0, 180.0)]


def read(chain, **kw) -> pr.PlanReading:
    return pr.read_raster(draw_plan(chain, **kw), rows(chain))


# ----- the shapes


def test_one_wall_across_the_page_is_ang_0() -> None:
    reading = read([("1", 120.0, 0.0)])
    assert reading.plan == Plan(("1",), (0.0,)) and reading.why == ""


def test_one_wall_up_the_page_could_not_verify() -> None:
    reading = read([("1", 120.0, 90.0)])
    assert reading.plan is None and "up the page" in reading.why


def test_a_straight_run_of_two_walls_turns_neither_way_so_could_not_verify() -> None:
    reading = read([("1", 60.0, 0.0), ("2", 40.0, 0.0)])
    assert reading.plan is None and "neither way clockwise" in reading.why


def test_an_l_reads_its_order_angles_and_free_ends() -> None:
    reading = read([("1", 70.0, 90.0), ("2", 50.0, 0.0)])
    assert reading.plan == Plan(("1", "2"), (90.0, 0.0))
    assert reading.free_ends == ("1", "2") and reading.joints == (("1", "2"),)


def test_a_u_reads_clockwise() -> None:
    reading = read(U)
    assert reading.plan == Plan(("1", "2", "3"), (90.0, 0.0, 270.0))


def test_a_chain_traced_from_its_other_end_is_turned_clockwise() -> None:
    """A U open at the top: the trace meets its left wall first, and runs it counter-clockwise."""
    reading = read([("1", 30.0, 270.0), ("2", 80.0, 180.0), ("3", 24.0, 90.0)], origin=(1300.0, 300.0))
    assert reading.plan == Plan(("1", "2", "3"), (270.0, 180.0, 90.0))
    assert reading.free_ends == ("1", "3")


def test_a_reach_in_with_equal_returns_is_told_apart_by_its_labels() -> None:
    reading = read(REACH_IN)
    assert reading.plan == Plan(("1", "2", "3", "4", "5"), (180.0, 90.0, 0.0, 270.0, 180.0))
    assert {"2", "4"} <= set(reading.labels)


def test_a_chain_not_in_the_tables_order_reads_as_the_plan_draws_it() -> None:
    """Frozen job Z's second room runs 5, 1, 2, 3, 4: its door-side stubs are numbered last."""
    chain = [("5", 11.125, 180.0), ("1", 24.0, 90.0), ("2", 91.75, 0.0), ("3", 24.0, 270.0), ("4", 9.875, 180.0)]
    reading = read(chain)
    assert reading.plan == Plan(("5", "1", "2", "3", "4"), (180.0, 90.0, 0.0, 270.0, 180.0))
    assert reading.free_ends == ("5", "4")


def test_an_inward_turn_is_measured_to_the_far_side_of_its_corner_square() -> None:
    step = [("1", 60.0, 90.0), ("2", 22.5, 0.0), ("3", 16.125, 90.0), ("4", 47.625, 0.0), ("5", 23.875, 270.0)]
    reading = read(step, origin=(300.0, 1000.0))
    assert reading.plan == Plan(("1", "2", "3", "4", "5"), (90.0, 0.0, 90.0, 0.0, 270.0))


def test_a_stub_as_short_as_the_walls_are_thick_turns_where_it_meets_its_neighbour_at_a_corner() -> None:
    """A 3 1/4 in stub on a plan whose walls are 3 in thick is square: its corner touch says it turns."""
    chain = [("1", 3.25, 180.0), ("2", 24.0, 90.0), ("3", 80.0, 0.0), ("4", 30.0, 270.0)]
    reading = read(chain)
    assert reading.plan == Plan(("1", "2", "3", "4"), (180.0, 90.0, 0.0, 270.0))


def test_a_square_stub_running_on_from_its_neighbour_could_not_verify() -> None:
    """End to end it could run on, or own an inward corner: the touch alone doesn't say which."""
    reading = read([("1", 60.0, 90.0), ("2", 40.0, 0.0), ("3", 3.25, 0.0)])
    assert reading.plan is None and "square stub" in reading.why


def test_a_closed_room_opens_before_the_tables_first_wall() -> None:
    """Frozen job X is a closed rectangle, and Jim's room starts at its wall 1."""
    box = [("1", 124.375, 90.0), ("2", 63.375, 0.0), ("3", 124.375, 270.0), ("4", 63.375, 180.0)]
    reading = read(box, scale=6.0, origin=(700.0, 1100.0), size=(1800, 1600))
    assert reading.plan == Plan(("1", "2", "3", "4"), (90.0, 0.0, 270.0, 180.0))
    assert reading.free_ends == () and reading.joints == (("1", "2"), ("2", "3"), ("3", "4"))


def test_walls_filled_white_inside_their_outline_read_too() -> None:
    reading = read(U, fill=(255, 255, 255))
    assert reading.plan == Plan(("1", "2", "3"), (90.0, 0.0, 270.0))


def test_cabinets_and_other_fills_are_not_walls() -> None:
    reading = read(U, cabinets=[(560, 230, 900, 420), (900, 230, 1100, 420)])
    assert reading.plan == Plan(("1", "2", "3"), (90.0, 0.0, 270.0))


# ----- what could not verify


def test_equal_walls_with_a_label_missing_could_not_verify() -> None:
    reading = read(REACH_IN, labels={"4": None})
    assert reading.plan is None and "wall 4 could not be read" in reading.why


def test_equal_walls_whose_labels_sit_between_them_could_not_verify() -> None:
    middle = (500.0 + 40 * 8.0, 1000.0 - 12 * 8.0)  # both labels in the room's middle
    reading = read(REACH_IN, labels={"2": middle, "4": (middle[0] + 1, middle[1] + 200)})
    assert reading.plan is None and "too near" in reading.why


def test_a_label_read_twice_could_not_verify() -> None:
    img = draw_plan(REACH_IN)
    ImageDraw.Draw(img).text((1500, 150), "4", font=ImageFont.load_default(size=90), fill=(0, 0, 0), anchor="mm")
    reading = pr.read_raster(img, rows(REACH_IN))
    assert reading.plan is None and "reads twice" in reading.why


def test_lengths_that_are_not_the_tables_could_not_verify() -> None:
    table = (TableRow("1", 24.0, 98.0), TableRow("2", 60.0, 98.0), TableRow("3", 30.0, 98.0))
    reading = pr.read_raster(draw_plan(U), table)
    assert reading.plan is None and "don't match the WALL/AREA table" in reading.why


def test_a_table_with_another_count_of_walls_could_not_verify() -> None:
    reading = pr.read_raster(draw_plan(U), rows(U) + (TableRow("4", 10.0, 98.0),))
    assert reading.plan is None and "3 wall strips" in reading.why and "4 walls" in reading.why


def test_a_turn_that_is_not_a_right_angle_could_not_verify() -> None:
    img = draw_plan(U)
    draw = ImageDraw.Draw(img)
    draw.polygon([(1300, 300), (1340, 300), (1560, 600), (1520, 600)], fill=(183, 181, 179), outline=(0, 0, 0))
    table = rows(U) + (TableRow("4", 45.0, 98.0),)
    reading = pr.read_raster(img, table)
    assert reading.plan is None and "isn't a right angle" in reading.why and "BUILD 213-218" in reading.why


def test_two_separate_runs_could_not_verify() -> None:
    img = draw_plan(U, origin=(300.0, 1000.0))
    second = draw_plan([("4", 40.0, 0.0)], origin=(1300.0, 600.0), labels={"4": None})
    img.paste(second.crop((1250, 520, 1700, 640)), (1250, 520))
    reading = pr.read_raster(img, rows(U) + (TableRow("4", 40.0, 98.0),))
    assert reading.plan is None and "separate runs" in reading.why and "BUILD 219-221" in reading.why


def test_no_table_could_not_verify() -> None:
    assert pr.read_raster(draw_plan(U), ()).why == "the WALL/AREA table has no walls"


def test_a_table_naming_a_wall_twice_could_not_verify() -> None:
    table = (TableRow("1", 24.0, 98.0), TableRow("1", 80.0, 98.0), TableRow("3", 30.0, 98.0))
    assert "twice" in pr.read_raster(draw_plan(U), table).why


# ----- the digits


@pytest.mark.parametrize("digit", list("0123456789"))
@pytest.mark.parametrize("size", [50, 90, 140])
def test_each_digit_reads_back(digit: str, size: int) -> None:
    img = Image.new("L", (size * 2, size * 2), 0)
    ImageDraw.Draw(img).text((size, size), digit, font=ImageFont.load_default(size=size), fill=255, anchor="mm")
    box = img.getbbox()
    assert box is not None
    assert pr.read_digit(img.crop(box).point(lambda v: 255 if v >= 128 else 0)) == digit


def test_a_blob_that_is_no_digit_reads_as_none() -> None:
    img = Image.new("L", (60, 90), 0)
    ImageDraw.Draw(img).rectangle((0, 0, 59, 89), fill=255)
    assert pr.read_digit(img) is None


def test_two_digits_side_by_side_are_one_number() -> None:
    img = Image.new("RGB", (600, 300), "white")
    ImageDraw.Draw(img).text((300, 150), "12", font=ImageFont.load_default(size=100), fill=(0, 0, 0), anchor="mm")
    assert [lab.text for lab in pr.labels(img)] == ["12"]


# ----- the PDF


def test_the_plan_is_the_pages_one_large_image() -> None:
    pdf = as_pdf(draw_plan(U))
    raster = pr.plan_raster(pdf, 1)
    assert raster is not None and raster.size == (1800, 1400)
    assert pr.read_plan(pdf, 1, rows(U)).plan == Plan(("1", "2", "3"), (90.0, 0.0, 270.0))


def test_a_page_with_no_image_could_not_verify() -> None:
    pdf = as_pdf(draw_plan(U), pages=2)
    assert pr.plan_raster(pdf, 2) is None and pr.plan_raster(pdf, 3) is None
    assert pr.read_plan(pdf, 2, rows(U)).why == "the WALL/AREA page holds no plan"


# ----- into the room model (walls.py)


def test_a_read_plan_chains_the_walls() -> None:
    walls, joints, _, findings = build_walls(rows(U), read(U))
    assert findings == () and [w.angle.value for w in walls] == [90.0, 0.0, 270.0]
    assert [w.pos_mm.value for w in walls] == [(0.0, 0.0), (0.0, 609.6), (2032.0, 609.6)]
    assert joints.value == ((1, 2), (2, 3)) and {w.angle.source for w in walls} == {"plan"}


def test_an_unread_plan_leaves_the_chain_could_not_verify_with_the_readers_reason() -> None:
    reading = read(REACH_IN, labels={"4": None})
    walls, joints, _, _ = build_walls(rows(REACH_IN), reading)
    assert {(w.angle.status, w.angle.why) for w in walls} == {("could not verify", reading.why)}
    assert joints.status == "could not verify" and joints.why == reading.why


def test_no_plan_at_all_still_says_so() -> None:
    walls, _, _, _ = build_walls(rows(U))
    assert {w.angle.why for w in walls} == {NO_PLAN}
