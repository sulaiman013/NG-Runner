"""
P5a's walls (ngrunner/build/walls.py): a room's walls from its design's WALL/AREA table, as Jim's
gen_rooms.py makes them. The table settles each wall's length and height; the plan view settles the
chain's order and each wall's angle (BUILD 199-221), and without a plan (plan.py reads it) those could not
verify. Every room here is made up.
"""
from __future__ import annotations

import pytest

from ngrunner.build.room import Plan, TableRow
from ngrunner.build.walls import NO_PLAN, build_walls, chain, inches, net_turn, table_rows

ROOM = (TableRow("1", 24.0, 98.0), TableRow("2", 60.75, 98.0), TableRow("3", 24.0, 98.0))


# ----- the table


@pytest.mark.parametrize("text, value", [('124 3/8"', 124.375), ('98"', 98.0), ('4 5/8"', 4.625), ("60.5", 60.5)])
def test_a_size_reads_as_the_table_prints_it(text: str, value: float) -> None:
    assert inches(text) == value


@pytest.mark.parametrize("text", ["", "abc", '12 3/0"', "12 x 3"])
def test_anything_else_is_no_size(text: str) -> None:
    with pytest.raises(ValueError):
        inches(text)


def test_the_rows_come_from_the_summary_checks_own_reading() -> None:
    facts = [{"name": "1", "width": '24"', "height": '98"'}, {"name": "2", "width": '60 3/4"', "height": '98"'}]
    assert table_rows(facts) == (TableRow("1", 24.0, 98.0), TableRow("2", 60.75, 98.0))


def test_lengths_and_heights_are_the_tables_inches_to_4_places() -> None:
    walls, _, height, _ = build_walls((TableRow("1", 124.375, 107.75), TableRow("2", 63.375, 107.75)))
    assert [(w.length_mm.value, w.height_mm.value) for w in walls] == [(3159.125, 2736.85), (1609.725, 2736.85)]
    assert {w.length_mm.source for w in walls} == {"table"} and {w.length_mm.status for w in walls} == {"set"}
    assert height.value == 2736.85


def test_h_walls_is_the_tallest_wall() -> None:
    _, _, height, _ = build_walls((TableRow("1", 24.0, 98.0), TableRow("2", 60.0, 98.5)))
    assert height.value == round(98.5 * 25.4, 4) and "gen_rooms.py 111-113" in height.why


def test_each_walls_idtag_is_its_number() -> None:
    walls, _, _, _ = build_walls(ROOM)
    assert [w.id_tag.value for w in walls] == [1, 2, 3]


def test_a_wall_not_named_by_a_number_has_no_idtag() -> None:
    walls, _, _, _ = build_walls((TableRow("1", 24.0, 98.0), TableRow("A", 60.0, 98.0)))
    assert walls[1].id_tag.status == "could not verify" and "not named by a number" in walls[1].id_tag.why


def test_a_table_naming_a_wall_twice_could_not_verify() -> None:
    walls, joints, _, findings = build_walls((TableRow("1", 24.0, 98.0), TableRow("1", 60.0, 98.0)),
                                             Plan(("1", "1"), (90.0, 0.0)))
    assert [f.status for f in findings] == ["could not verify"] and "more than once" in findings[0].why
    assert all(w.angle.status == "could not verify" for w in walls) and joints.status == "could not verify"


# ----- the plan


def test_without_a_plan_the_chain_could_not_verify() -> None:
    walls, joints, _, findings = build_walls(ROOM)
    assert [w.name for w in walls] == ["1", "2", "3"]
    for w in walls:
        for fact in (w.position, w.angle, w.pos_mm):
            assert (fact.status, fact.why) == ("could not verify", NO_PLAN)
    assert joints.status == "could not verify" and findings == ()


def test_a_plan_chains_the_walls_head_to_tail_from_0_0() -> None:
    walls, joints, _, findings = build_walls(ROOM, Plan(("1", "2", "3"), (90.0, 0.0, 270.0)))
    assert findings == ()
    assert [w.pos_mm.value for w in walls] == [(0.0, 0.0), (0.0, 609.6), (1543.05, 609.6)]
    assert [w.angle.value for w in walls] == [90.0, 0.0, 270.0] and [w.position.value for w in walls] == [1, 2, 3]
    assert joints.value == ((1, 2), (2, 3))


def test_the_chain_runs_in_the_plans_order_and_its_joints_name_idtags() -> None:
    """A reach-in whose chain starts at its last-numbered return, as one of Jim's frozen rooms does."""
    room = ROOM + (TableRow("4", 4.75, 98.0),)
    walls, joints, _, _ = build_walls(room, Plan(("4", "1", "2", "3"), (180.0, 90.0, 0.0, 270.0)))
    assert [w.name for w in walls] == ["4", "1", "2", "3"] and [w.position.value for w in walls] == [1, 2, 3, 4]
    assert walls[1].pos_mm.value == (-120.65, 0.0)
    assert joints.value == ((4, 1), (1, 2), (2, 3))


def test_a_plan_turning_anticlockwise_could_not_verify() -> None:
    walls, _, _, findings = build_walls(ROOM, Plan(("1", "2", "3"), (270.0, 0.0, 90.0)))
    assert net_turn((270.0, 0.0, 90.0)) == 180.0
    assert "not clockwise" in findings[0].why and all(w.angle.status == "could not verify" for w in walls)


def test_a_plan_leaving_out_a_wall_could_not_verify() -> None:
    _, joints, _, findings = build_walls(ROOM, Plan(("1", "2"), (90.0, 0.0)))
    assert "author every wall in the table" in findings[0].why and joints.status == "could not verify"


def test_a_plan_with_too_few_angles_could_not_verify() -> None:
    _, _, _, findings = build_walls(ROOM, Plan(("1", "2", "3"), (90.0, 0.0)))
    assert "number of angles" in findings[0].why


def test_a_chain_never_gives_minus_zero() -> None:
    assert chain([100.0, 100.0], [180.0, 90.0]) == [(0.0, 0.0), (-100.0, 0.0)]
    assert str(chain([100.0, 100.0], [270.0, 180.0])[1][0]) == "0.0"
