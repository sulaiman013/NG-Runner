"""
The reader evaluation's truth: the as-cut panels per wall, read from a Room#.des by bytes
(docs/p2/build-spec.md 5.7). A vertical is what Jim's regression.py calls a panel (its PANEL
prefixes, at most 25.4 mm wide); a product's Wall="<n>_1" names the n-th <Wall> in file order,
which is not always the wall's WallNumber (measured on the 39 real rooms, 2026-10-31).
Synthetic rooms only; the real ones are in test_reader_real.py.
"""
from __future__ import annotations

import pytest

from tools.reader_eval import prep, truth
from tools.reader_eval import synthetic as s


def test_a_room_gives_its_walls_in_file_order_with_their_sizes() -> None:
    room = truth.read_room(s.room_des())
    assert room.key == prep.name_key_hash(s.DESIGN)
    assert [(w.position, w.number) for w in room.walls] == [(1, "1"), (2, "2")]
    assert room.walls[1].length_in == pytest.approx(59.75)
    assert room.walls[1].height_in == pytest.approx(108)


def test_panels_come_left_to_right_in_inches_with_top_and_bottom() -> None:
    wall = truth.read_room(s.room_des()).walls[1]
    assert [(p.x_in, p.bottom_in, p.top_in) for p in wall.panels] == [
        pytest.approx((0, 36, 96), abs=0.01), pytest.approx((35, 36, 96), abs=0.01)]
    assert [p.radius for p in wall.panels] == [False, True]
    assert truth.read_room(s.room_des()).walls[0].panels == ()


def test_a_products_wall_is_its_position_in_the_file_not_its_number() -> None:
    walls = ((5, 282.575, 2489.2), (1, 609.6, 2489.2), (2, 2330.45, 2489.2), (3, 609.6, 2489.2))
    room = truth.read_room(s.room_des(walls=walls, panels=(s.PanelSpec("FM 84", 1362, 0, 2132, 3),),
                                      others=()))
    carrying = [w for w in room.walls if w.panels]
    assert [(w.position, w.number) for w in carrying] == [(3, "2")]
    assert carrying[0].length_in == pytest.approx(91.75)


def test_only_jims_panel_products_are_verticals() -> None:
    panels = (s.PanelSpec("FM 84", 0, 0, 2132, 2), s.PanelSpec("WM 48 REA Thru", 300, 928, 1204, 2),
              s.PanelSpec("Wall Mount 72 (Thru)", 600, 0, 1828, 2), s.PanelSpec("Lux 3in Cleat", 700, 0, 76, 2),
              s.PanelSpec("Upper Panel", 800, 900, 600, 2), s.PanelSpec("FM 84", 900, 0, 2132, 2, width_mm=30))
    wall = truth.read_room(s.room_des(panels=panels, others=())).walls[1]
    assert [p.prod for p in wall.panels] == ["FM 84", "WM 48 REA Thru", "Wall Mount 72 (Thru)"]


def test_the_panel_rule_is_jims_regression_rule() -> None:
    assert truth.PANEL.pattern == r"^(FM ?84|FM 90|WM Panel 48|WM 48 REA|Wall Mount)"
    assert truth.PANEL_MAX_WIDTH_MM == 25.4


def test_a_stacked_tower_reads_lower_then_upper_at_the_same_x() -> None:
    panels = (s.PanelSpec("WM Panel 48 Thru", 500, 1400, 700, 2), s.PanelSpec("FM 84", 500, 0, 1300, 2),
              s.PanelSpec("FM 84", 0, 0, 2132, 2))
    wall = truth.read_room(s.room_des(panels=panels, others=())).walls[1]
    assert [(round(p.x_in, 2), round(p.bottom_in, 2)) for p in wall.panels] == [
        (0.0, 0.0), (19.69, 0.0), (19.69, 55.12)]
    assert "stacked tower" in wall.categories


@pytest.mark.parametrize(("panels", "others", "expected"), [
    ((), (), {"no verticals"}),
    ((s.PanelSpec("FM 84", 300, 0, 2132, 2),), (), {"plain"}),
    ((s.PanelSpec("FM 84", 0, 0, 2132, 2),), (), {"plain", "flush end"}),
    ((s.PanelSpec("FM 84", 1498.65, 0, 2132, 2),), (), {"plain", "flush end"}),
    ((s.PanelSpec("WM Panel 48 Thru Radius", 300, 928, 1204, 2),), (), {"radius"}),
    ((s.PanelSpec("WM 48 REA Thru", 300, 928, 1204, 2),), (), {"REA"}),
    ((s.PanelSpec("FM 84", 300, 0, 2132, 2),), (("84 BR Left", 320, 400, 2),), {"bridge"}),
    ((s.PanelSpec("FM 84", 300, 0, 2132, 2),), (("Hutch 36", 320, 400, 2),), {"hutch"}),
])
def test_wall_categories_follow_the_design_list(panels: tuple, others: tuple, expected: set[str]) -> None:
    wall = truth.read_room(s.room_des(panels=panels, others=others)).walls[1]
    assert set(wall.categories) == expected


def test_a_rotated_vertical_is_kept_and_marked() -> None:
    wall = truth.read_room(s.room_des(panels=(s.PanelSpec("FM 84", 300, 0, 2132, 2, rot=90),),
                                      others=())).walls[1]
    assert wall.panels[0].rot == 90 and wall.has_rotated


def test_a_room_with_no_walls_has_none() -> None:
    room = truth.read_room(s.room_des(walls=(), panels=(), others=()))
    assert room.walls == ()


@pytest.mark.parametrize("data", [
    b"<?xml version='1.0'?><Room Name='x'></Room>",  # no version line
    b"15\r\n<?xml version='1.0'?><Nothing/>",  # no Room
    b"15\r\n<?xml?><Room Name=\"x\"><Walls><Wall IDTag=\"1\" Len=\"abc\" Height=\"1\" WallNumber=\"1\"/></Walls></Room>",
])
def test_anything_else_is_refused_not_guessed(data: bytes) -> None:
    with pytest.raises(truth.TruthError):
        truth.read_room(data)


def test_a_product_naming_a_wall_the_room_lacks_is_refused() -> None:
    with pytest.raises(truth.TruthError):
        truth.read_room(s.room_des(panels=(s.PanelSpec("FM 84", 0, 0, 2132, 9),), others=()))


def test_the_schedule_matches_jims_baseline_shape() -> None:
    wall = truth.read_room(s.room_des()).walls[1]
    assert wall.panels[1].schedule == {"prod": "WM Panel 48 Right Radius", "wall": "2_1", "x": 888.95,
                                       "elev": 914.4, "h": 1524.0}
