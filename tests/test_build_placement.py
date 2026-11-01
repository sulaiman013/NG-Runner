"""
P5a's placement rules (ngrunner/build/placement.py): Jim's build choices that a drawing doesn't show,
as code. Each rule has its lines here, each citing the line of Jim's docs it ports, and every case his
rules don't settle reads "could not verify", never a guess (AGENTS.md rule 8).

  heights and mount  RULES 290-292, 318, 325-326: the IC height snapped to 32k+20, the mount from the
                     notch token, never from the height
  top line           RULES 329-339: one T per room, the top of an FM panel beside the WM run, else the
                     run's own top, and within 1 in of 2132 is 2132
  stacked gap        an FM upper drawn above the floor stands on the closet counter, built 3/4 in
                     (RULES 73, 218, 221; Jim's own build of a real stacked tower)
  run start          where a run starts when the drawing leaves a gap at the wall's start: no rule of
                     Jim's settles it under the 3 in Wall Filler (RULES 264, BUILD 276)

Every wall and room here is made up.
"""
from __future__ import annotations

import pytest

from ngrunner.build import placement as pl
from ngrunner.build.placement import Drawn, DrawnWall

IN = 25.4


def wm(x_in: float, bottom_in: float, top_in: float, height_in: float) -> Drawn:
    return Drawn(x_in * IN, bottom_in * IN, top_in * IN, pl.snap_height_mm(height_in), "WM")


def fm(x_in: float, bottom_in: float, top_in: float, height_in: float) -> Drawn:
    return Drawn(x_in * IN, bottom_in * IN, top_in * IN, pl.snap_height_mm(height_in), "FM")


# ----- heights and mount (RULES 290-292, 318, 325-326)


@pytest.mark.parametrize("height_in, mm", [(83.9, 2132.0), (47.4, 1204.0), (23.5, 596.0), (71.3, 1812.0),
                                           (56.2, 1428.0), (34.8, 884.0), (20.9, 532.0), (60.0, 1524.0)])
def test_an_ic_height_snaps_to_the_32k_plus_20_grid(height_in: float, mm: float) -> None:
    assert pl.snap_height_mm(height_in) == mm


def test_a_height_more_than_1_3_mm_off_the_grid_could_not_verify() -> None:
    assert pl.snap_height_mm(50.0) is None  # 1270 mm: 2 mm from 1268


@pytest.mark.parametrize("description, mount", [
    ("VT 14x47.4x3/4 FD FN WH", "WM"), ("VT RAD 14x47.4x3/4 LH RN WH", "WM"), ("VT 14x23.5x3/4 RH LN WH", "WM"),
    ("VT 14x83.9x3/4 FD WH", "FM"), ("VT 14x56.2x3/4 ND WH", "FM"), ("VT 14x34.8x3/4 NN WH", "FM"),
])
def test_the_mount_is_the_notch_token_never_the_height(description: str, mount: str) -> None:
    assert pl.mount(description) == mount


# ----- the top line (RULES 329-339)


def test_an_fm_panel_reaching_up_beside_the_wm_run_sets_the_top_line() -> None:
    found = pl.top_line([2260.0], [900.0], [2290.0])
    assert (found.status, found.value_mm) == ("placed", 2260.0)
    assert "RULES 334" in found.why


def test_the_highest_fm_panel_beside_the_run_sets_it() -> None:
    assert pl.top_line([2132.0, 2260.0, 884.0], [928.0], [2132.0]).value_mm == 2260.0


def test_an_fm_panel_below_the_lowest_wm_bottom_is_not_beside_the_run() -> None:
    found = pl.top_line([884.0], [1492.0], [2410.0])
    assert found.status == "could not verify" and found.value_mm is None


def test_with_no_fm_panel_beside_it_a_run_drawn_within_1_in_of_2132_hangs_on_2132() -> None:
    found = pl.top_line([], [940.0, 1546.0], [2140.0, 2152.0])
    assert (found.status, found.value_mm) == ("placed", 2132.0)
    assert "RULES 338" in found.why


def test_with_no_fm_panel_beside_it_a_run_drawn_elsewhere_could_not_verify() -> None:
    found = pl.top_line([], [940.0], [2457.0])
    assert found.status == "could not verify" and found.value_mm is None
    assert "96.7" in found.why and "RULES 337" in found.why


def test_two_drawn_tops_in_one_room_with_no_fm_panel_could_not_verify() -> None:
    assert pl.top_line([], [940.0, 300.0], [2132.0, 1960.0]).status == "could not verify"


def test_an_fm_top_within_1_in_of_2132_is_2132() -> None:
    assert pl.top_line([2150.0], [900.0], [2150.0]).value_mm == 2132.0


def test_a_room_with_no_wm_panel_needs_no_top_line() -> None:
    assert pl.top_line([2132.0], [], []).status == "not needed"


@pytest.mark.parametrize("height_mm, elev_mm", [(1812.0, 320.0), (1204.0, 928.0), (852.0, 1280.0), (756.0, 1376.0),
                                                (692.0, 1440.0), (596.0, 1536.0)])
def test_a_wm_panel_hangs_from_the_top_line(height_mm: float, elev_mm: float) -> None:
    assert pl.hung_elev_mm(2132.0, height_mm) == elev_mm  # RULES 341-342's table


# ----- the stacked gap: an FM upper on the counter


def test_an_fm_upper_stands_on_the_counter_three_quarters_above_the_base() -> None:
    found = pl.counter_elev([884.0, 884.0], has_counter=True)
    assert found.status == "placed" and found.value_mm == pytest.approx(903.304)


def test_with_no_counter_on_the_wall_an_fm_upper_could_not_verify() -> None:
    found = pl.counter_elev([884.0], has_counter=False)
    assert found.status == "could not verify" and "WS" in found.why


def test_an_fm_upper_with_no_base_under_it_or_bases_of_two_heights_could_not_verify() -> None:
    assert pl.counter_elev([], has_counter=True).status == "could not verify"
    assert pl.counter_elev([884.0, 916.0], has_counter=True).status == "could not verify"


# ----- the run start


@pytest.mark.parametrize("first_x_in", [0.0, 0.06, 0.125])
def test_a_run_drawn_tight_to_the_walls_start_starts_as_drawn(first_x_in: float) -> None:
    assert pl.run_start(first_x_in * IN).status == "as drawn"


@pytest.mark.parametrize("first_x_in", [3.0, 2.9, 24.0, 48.625])
def test_a_run_behind_a_wall_filler_or_an_opening_starts_as_drawn(first_x_in: float) -> None:
    assert pl.run_start(first_x_in * IN).status == "as drawn"


@pytest.mark.parametrize("first_x_in", [0.5, 0.75, 1.0, 1.5, 2.75])
def test_a_run_drawn_a_little_off_the_walls_start_could_not_verify(first_x_in: float) -> None:
    found = pl.run_start(first_x_in * IN)
    assert found.status == "could not verify" and found.value_mm is None
    assert "tight" in found.why and "centred" in found.why


# ----- a room placed: the rules together on drawn walls


def test_a_room_hangs_every_wm_panel_on_its_fm_panels_top_and_leaves_the_floor_panels_as_drawn() -> None:
    walls = [DrawnWall((fm(0, 0, 83.9, 83.9), fm(24.6, 0, 83.9, 83.9), wm(48.0, 36.6, 84.0, 47.4)), False),
             DrawnWall((wm(0, 60.2, 83.7, 23.5), wm(30.0, 60.2, 83.7, 23.5)), False)]
    room = pl.place_room(walls)
    assert room.top_line.value_mm == 2132.0
    hung = room.walls[0].verticals[2]
    assert (hung.bottom_mm, hung.top_mm, hung.by) == (928.0, 2132.0, "top line")
    assert [(v.bottom_mm, v.top_mm) for v in room.walls[1].verticals] == [(1536.0, 2132.0)] * 2
    floor = room.walls[0].verticals[0]
    assert (floor.bottom_mm, floor.top_mm, floor.by) == (0.0, 83.9 * IN, "drawing")


def test_fm_panels_on_walls_not_drawn_still_set_the_top_line() -> None:
    room = pl.place_room([DrawnWall((wm(0, 36.0, 95.9, 60.0),), False)], other_fm_tops_mm=[2260.0])
    assert room.top_line.value_mm == 2260.0
    assert room.walls[0].verticals[0].bottom_mm == 2260.0 - 1524.0


def test_when_the_top_line_could_not_verify_the_wm_panels_stay_as_drawn_and_the_wall_says_why() -> None:
    room = pl.place_room([DrawnWall((wm(0, 36.75, 96.75, 60.0), wm(30.0, 36.75, 96.75, 60.0)), False)])
    assert room.top_line.status == "could not verify"
    assert [v.by for v in room.walls[0].verticals] == ["drawing", "drawing"]
    assert any(f.rule == "top line" for f in room.walls[0].could_not_verify)


def test_a_stacked_tower_on_a_counter_puts_its_fm_uppers_on_the_counter() -> None:
    wall = DrawnWall((fm(3.0, 0, 34.8, 34.8), fm(13.75, 35.93, 92.18, 56.2), fm(39.38, 0, 34.8, 34.8),
                      fm(39.38, 35.93, 92.18, 56.2)), True)
    placed = pl.place_room([wall]).walls[0].verticals
    assert [v.by for v in placed] == ["drawing", "stacked gap", "drawing", "stacked gap"]
    assert placed[1].bottom_mm == pytest.approx(903.304) and placed[1].top_mm == pytest.approx(903.304 + 1428.0)


def test_a_wm_upper_of_a_stacked_tower_hangs_on_the_top_line_not_the_counter() -> None:
    wall = DrawnWall((fm(24.5, 0, 83.9, 83.9), fm(61.6, 0, 42.4, 42.4), wm(61.6, 60.0, 83.5, 23.5)), True)
    placed = pl.place_room([wall]).walls[0].verticals
    assert (placed[2].bottom_mm, placed[2].by) == (1536.0, "top line")


def test_a_vertical_whose_mount_or_height_is_unknown_stays_as_drawn_and_could_not_verify() -> None:
    odd = Drawn(10 * IN, 30 * IN, 80 * IN, None, "WM")
    unknown = Drawn(40 * IN, 30 * IN, 80 * IN, 1204.0, None)
    room = pl.place_room([DrawnWall((fm(0, 0, 83.9, 83.9), odd, unknown), False)])
    assert [v.by for v in room.walls[0].verticals[1:]] == ["drawing", "drawing"]
    assert {f.rule for f in room.walls[0].could_not_verify} >= {"height", "mount"}


def test_the_run_start_never_moves_a_vertical_it_only_says_whether_the_start_is_settled() -> None:
    wall = DrawnWall((fm(0.75, 0, 83.9, 83.9), fm(21.75, 0, 83.9, 83.9)), False)
    placed = pl.place_room([wall]).walls[0]
    assert [v.x_mm for v in placed.verticals] == [0.75 * IN, 21.75 * IN]
    assert [f.rule for f in placed.could_not_verify] == ["run start"]


def test_a_wall_with_no_verticals_has_nothing_to_place() -> None:
    room = pl.place_room([DrawnWall((), False)])
    assert room.walls[0].verticals == () and room.walls[0].findings == ()
    assert room.top_line.status == "not needed"
