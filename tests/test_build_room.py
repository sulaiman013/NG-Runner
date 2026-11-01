"""
P5a's room model (ngrunner/build/schedule.py, room.py): each wall's panel schedule from a reading of its
drawing, its own IC parts lines and Jim's rules, every value with its source, and "could not verify" with
the reason wherever his rules or the inputs don't settle it (AGENTS.md rule 8). One test per rule and per
could-not-verify case, each citing the line of Jim's docs it holds the code to. Every room is made up.
"""
from __future__ import annotations

import pytest

from ngrunner.build import schedule as sc
from ngrunner.build.room import ICLine, PanelSpec, Plan, ReadVertical, ReadWall, RoomModel, TableRow

IN = 25.4
SIDES = (TableRow("1", 24.0, 98.0), TableRow("3", 24.0, 98.0))


def line(qty: int | None, desc: str) -> ICLine:
    return ICLine(qty, desc)


def v(x: float, bottom: float, top: float, height: str, radius: str = "no", unsure: tuple[str, ...] = ()) -> ReadVertical:
    return ReadVertical(x, bottom, top, height, radius, unsure)


def room(*walls: ReadWall, length: float = 60.75, plan: Plan | None = None, name: str = "Testerson - Primary") -> RoomModel:
    return sc.build_room(name, (SIDES[0], TableRow("2", length, 98.0), SIDES[1]), walls, plan)


def panels(model: RoomModel, wall: str = "2") -> tuple[PanelSpec, ...]:
    return next(s for s in model.schedules if s.wall == wall).panels


# A wall of the made-up Testerson job: two floor panels and a hung one between them, each bay a section.
PLAIN = ReadWall("2", (line(2, "VT 14x83.9x3/4 FD WH"), line(1, "VT 14x47.4x3/4 FD FN WH"),
                       line(2, "SH 14x29.6x3/4 FIX WH"), line(2, "SH 14x28.9x3/4 FIX WH")),
                 (v(0.0, 0.0, 83.94, "83.9"), v(30.375, 36.5, 83.94, "47.4"), v(60.0, 0.0, 83.94, "83.9")))


# ----- one room, end to end


def test_a_plain_wall_builds_every_value_with_its_source() -> None:
    model = room(PLAIN)
    fm, wm, _ = panels(model)
    assert (fm.x_mm.value, fm.elev_mm.value, fm.height_mm.value, fm.depth_mm.value) == (0.0, 0.0, 2132.0, 356.0)
    assert (fm.mount.value, fm.side.status, fm.product.value) == ("FM", "not needed", "FM 84")
    assert (wm.x_mm.value, wm.elev_mm.value, wm.height_mm.value, wm.top_mm) == (771.525, 928.0, 1204.0, 2132.0)
    assert (wm.mount.value, wm.side.value, wm.radius.value, wm.rea.value, wm.product.value) == (
        "WM", "Thru", False, False, "WM Panel 48 Thru")
    assert (wm.x_mm.source, wm.height_mm.source, wm.mount.source, wm.elev_mm.source, wm.radius.source) == (
        "reading", "IC line", "IC line", "rule", "reading")
    assert model.top_line.value_mm == 2132.0 and model.name == "Testerson - Primary"


def test_without_a_plan_only_the_chain_could_not_verify_on_a_plain_wall() -> None:
    model = room(PLAIN)
    what = {(where.split()[0], name) for where, name, _ in model.unverified()}
    assert what == {("wall", "position"), ("wall", "angle"), ("wall", "pos_mm"), ("room", "joints")}
    assert all(f.status != "could not verify" for p in panels(model) for _, f in p.facts())


def test_with_a_plan_a_plain_wall_has_nothing_left_to_verify() -> None:
    model = room(PLAIN, plan=Plan(("1", "2", "3"), (90.0, 0.0, 270.0)))
    assert model.unverified() == [] and model.joints.value == ((1, 2), (2, 3))


# ----- the vertical's own IC lines


def test_the_height_is_its_ic_lines_snapped_to_32k_plus_20() -> None:
    """RULES 291: the IC description for that piece, snapped."""
    assert [p.height_mm.value for p in panels(room(PLAIN))] == [2132.0, 1204.0, 2132.0]


def test_a_height_off_the_grid_could_not_verify() -> None:
    """RULES 318, gate 4: every height on 32k+20 within 1.3 mm."""
    wall = ReadWall("2", (line(1, "VT 14x50x3/4 FD WH"),), (v(0.0, 0.0, 50.0, "50"),))
    (p,) = panels(room(wall))
    assert p.height_mm.status == "could not verify" and "32k+20" in p.height_mm.why
    assert p.elev_mm.status == "could not verify"


def test_the_mount_is_the_notch_token_on_its_own_line() -> None:
    """RULES 290, 324-326: FN, LN or RN hangs it, NN, ND or none stands it on the floor."""
    wall = ReadWall("2", (line(1, "VT 14x47.4x3/4 RH LN WH"), line(1, "VT 14x83.9x3/4 NN WH"),
                          line(1, "SH 14x29.6x3/4 FIX WH")),
                    (v(0.0, 36.5, 83.94, "47.4"), v(30.375, 0.0, 83.94, "83.9")))
    assert [p.mount.value for p in panels(room(wall))] == ["WM", "FM"]


def test_lines_of_one_height_that_disagree_on_the_notch_could_not_verify() -> None:
    wall = ReadWall("2", (line(1, "VT 14x47.4x3/4 FD FN WH"), line(1, "VT 14x47.4x3/4 FD WH")),
                    (v(0.0, 36.5, 83.94, "47.4"), v(30.0, 36.5, 83.94, "47.4")))
    for p in panels(room(wall)):
        assert p.mount.status == "could not verify" and "disagree on a hanging notch" in p.mount.why
        assert p.product.status == "could not verify" and p.elev_mm.status == "could not verify"


def test_a_floor_line_drawn_hanging_with_nothing_under_it_could_not_verify() -> None:
    """The line says floor (RULES 290, 326), the drawing says hung: neither is guessed."""
    wall = ReadWall("2", (line(2, "VT 11.9x60x3/4 FD WH"), line(1, "SH 11.9x23.9x3/4 FIX WH")),
                    (v(0.0, 36.75, 96.75, "60"), v(24.625, 36.75, 96.75, "60")))
    for p in panels(room(wall)):
        assert p.mount.status == "could not verify" and "no base under it" in p.mount.why
        assert p.product.status == "could not verify" and p.side.status == "could not verify"


def test_the_depth_is_its_line_snapped_to_the_grid() -> None:
    """SCHEMA 165-166: nearest of the grid within 1.3 mm."""
    wall = ReadWall("2", (line(1, "VT 11.9x83.9x3/4 FD WH"),), (v(0.0, 0.0, 83.94, "83.9"),))
    (p,) = panels(room(wall))
    assert (p.depth_mm.value, p.depth_mm.status) == (302.0, "set")


def test_a_depth_off_the_grid_is_kept_ic_exact_and_flagged_review() -> None:
    """SCHEMA 166, BUILD 286: beyond 1.3 mm, keep IC-exact and flag Review."""
    wall = ReadWall("2", (line(1, "VT 17.8x83.9x3/4 FD WH"),), (v(0.0, 0.0, 83.94, "83.9"),))
    (p,) = panels(room(wall))
    assert (p.depth_mm.value, p.depth_mm.status) == (452.12, "review") and "Review" in p.depth_mm.why


def test_lines_of_one_height_and_two_depths_could_not_verify() -> None:
    """RULES 304-306: the DEPTH row says which, and the reading doesn't give it."""
    wall = ReadWall("2", (line(1, "VT 14x83.9x3/4 FD WH"), line(1, "VT 11.9x83.9x3/4 FD WH")),
                    (v(0.0, 0.0, 83.94, "83.9"), v(30.0, 0.0, 83.94, "83.9")))
    for p in panels(room(wall)):
        assert p.depth_mm.status == "could not verify" and "DEPTH row" in p.depth_mm.why


def test_rea_comes_from_its_line() -> None:
    """RULES 411-413."""
    wall = ReadWall("2", (line(1, "VT REA 14x83.9x3/4 FD WH"),), (v(0.0, 0.0, 83.94, "83.9"),))
    (p,) = panels(room(wall))
    assert (p.rea.value, p.rea.source) == (True, "IC line")


def test_lines_that_disagree_on_rea_could_not_verify() -> None:
    wall = ReadWall("2", (line(1, "VT REA 14x83.9x3/4 FD WH"), line(1, "VT 14x83.9x3/4 FD WH")),
                    (v(0.0, 0.0, 83.94, "83.9"), v(30.0, 0.0, 83.94, "83.9")))
    for p in panels(room(wall)):
        assert p.rea.status == "could not verify" and p.product.status == "could not verify"


def test_a_height_the_wall_has_no_line_of_could_not_verify() -> None:
    wall = ReadWall("2", (line(1, "VT 14x83.9x3/4 FD WH"),), (v(0.0, 0.0, 47.4, "47.4"),))
    (p,) = panels(room(wall))
    assert {p.height_mm.status, p.mount.status, p.depth_mm.status} == {"could not verify"}


def test_a_height_the_reading_did_not_name_could_not_verify() -> None:
    wall = ReadWall("2", (line(1, "VT 14x83.9x3/4 FD WH"),), (v(0.0, 0.0, 83.94, "unclear"),))
    (p,) = panels(room(wall))
    assert p.height_mm.status == "could not verify" and "named no VT height" in p.height_mm.why


# ----- the wall's gates (RULES 313-318)


def test_a_count_that_differs_from_the_lines_could_not_verify_any_line() -> None:
    """Gate 1: verticals placed == the wall's VT quantities."""
    wall = ReadWall("2", PLAIN.lines, PLAIN.verticals[:2])
    model = room(wall)
    schedule = model.schedules[0]
    assert any(f.rule == "IC lines" and "gate 1" in f.why for f in schedule.findings)
    assert all(p.height_mm.status == "could not verify" for p in schedule.panels)


def test_heights_that_are_not_the_lines_could_not_verify_any_line() -> None:
    """Gate 4, which gate 6 needs: the heights read are the lines' heights as a set."""
    wall = ReadWall("2", PLAIN.lines, (v(0.0, 0.0, 83.94, "83.9"), v(30.375, 36.5, 83.94, "83.9"),
                                       v(60.0, 0.0, 83.94, "83.9")))
    schedule = room(wall).schedules[0]
    assert any("gate 4" in f.why for f in schedule.findings)
    assert all(p.mount.status == "could not verify" for p in schedule.panels)


def test_a_reading_unsure_of_the_count_could_not_verify_any_line() -> None:
    wall = ReadWall("2", PLAIN.lines, PLAIN.verticals, count_unsure=True)
    assert all(p.height_mm.status == "could not verify" for p in panels(room(wall)))


def test_an_unreadable_quantity_could_not_verify_any_line() -> None:
    wall = ReadWall("2", (line(None, "VT 14x83.9x3/4 FD WH"),), (v(0.0, 0.0, 83.94, "83.9"),))
    (p,) = panels(room(wall))
    assert p.height_mm.status == "could not verify" and "unreadable" in p.height_mm.why


# ----- the radius (RULES 294, gate 3)


RADIUS = ReadWall("2", (line(2, "VT 14x83.9x3/4 FD WH"), line(1, "VT RAD 14x47.4x3/4 FD FN WH"),
                        line(2, "SH 14x29.6x3/4 FIX WH"), line(2, "SH 14x28.9x3/4 FIX WH")),
                  (v(0.0, 0.0, 83.94, "83.9"), v(30.375, 36.5, 83.94, "47.4", "yes"), v(60.0, 0.0, 83.94, "83.9")))


def test_the_reading_says_which_vertical_is_the_radius_and_the_vt_rad_line_that_one_is() -> None:
    _, wm, _ = panels(room(RADIUS))
    assert (wm.radius.value, wm.product.value) == (True, "WM Panel 48 Thru Radius")


def test_a_radius_count_that_differs_from_the_vt_rad_lines_could_not_verify() -> None:
    wall = ReadWall("2", RADIUS.lines, (RADIUS.verticals[0], v(30.375, 36.5, 83.94, "47.4", "no"),
                                        RADIUS.verticals[2]))
    for p in panels(room(wall)):
        assert p.radius.status == "could not verify" and "gate 3" in p.radius.why
        assert p.product.status == "could not verify"


def test_a_radius_the_reading_is_unsure_of_could_not_verify() -> None:
    wall = ReadWall("2", RADIUS.lines, (RADIUS.verticals[0], v(30.375, 36.5, 83.94, "47.4", "unclear"),
                                        RADIUS.verticals[2]))
    assert all(p.radius.status == "could not verify" for p in panels(room(wall)))


def test_a_vt_rad_line_on_a_floor_panel_has_no_product() -> None:
    wall = ReadWall("2", (line(1, "VT RAD 14x83.9x3/4 FD WH"),), (v(0.0, 0.0, 83.94, "83.9", "yes"),))
    (p,) = panels(room(wall))
    assert p.product.status == "could not verify" and "radius product" in p.product.why


# ----- x and the run start (placement.run_start)


def test_x_is_the_drawn_left_face_to_3_places() -> None:
    """BUILD 283-284."""
    _, wm, _ = panels(room(PLAIN))
    assert wm.x_mm.value == round(30.375 * IN, 3) and "BUILD 283-284" in wm.x_mm.why


def test_x_could_not_verify_where_the_run_start_does() -> None:
    """A run drawn 1 in from the wall's start: tight, centred or as drawn, no rule of Jim's says."""
    wall = ReadWall("2", PLAIN.lines, (v(1.0, 0.0, 83.94, "83.9"), v(31.375, 36.5, 83.94, "47.4"),
                                       v(61.0, 0.0, 83.94, "83.9")))
    schedule = room(wall, length=62.75).schedules[0]
    assert all(p.x_mm.status == "could not verify" and "Wall Filler" in p.x_mm.why for p in schedule.panels)
    assert schedule.findings[-1].rule == "run start"


def test_x_the_reading_is_unsure_of_could_not_verify() -> None:
    wall = ReadWall("2", PLAIN.lines, (PLAIN.verticals[0], v(30.375, 36.5, 83.94, "47.4", unsure=("x_in",)),
                                       PLAIN.verticals[2]))
    assert panels(room(wall))[1].x_mm.status == "could not verify"


def test_a_vertical_the_reading_doubts_could_not_verify_at_all() -> None:
    wall = ReadWall("2", PLAIN.lines, (PLAIN.verticals[0], v(30.375, 36.5, 83.94, "47.4", unsure=("exists",)),
                                       PLAIN.verticals[2]))
    doubted = panels(room(wall))[1]
    assert all(f.status == "could not verify" and f.why == sc.EXISTS for _, f in doubted.facts())


def test_a_reading_with_no_position_could_not_place_its_wall() -> None:
    wall = ReadWall("2", PLAIN.lines, (PLAIN.verticals[0], v(30.375, None, 83.94, "47.4"), PLAIN.verticals[2]))
    schedule = room(wall).schedules[0]
    assert any(f.rule == "positions" for f in schedule.findings)
    assert all(p.elev_mm.status == "could not verify" for p in schedule.panels)


# ----- Elev (placement: RULES 292, 329-339, the counter)


def test_a_floor_panel_stands_at_elev_0() -> None:
    wall = ReadWall("2", (line(1, "VT 14x83.9x3/4 FD WH"),), (v(0.0, 0.4, 83.94, "83.9"),))
    (p,) = panels(room(wall))
    assert (p.elev_mm.value, p.elev_mm.source) == (0.0, "rule") and "RULES 292" in p.elev_mm.why


def test_a_hung_panel_hangs_on_the_rooms_top_line() -> None:
    """RULES 330: Elev = T - height; the FM panel beside the run gives T."""
    _, wm, _ = panels(room(PLAIN))
    assert wm.elev_mm.value == 2132.0 - 1204.0 and "top line" in wm.elev_mm.why


def test_a_hung_panel_with_no_top_line_could_not_verify() -> None:
    wall = ReadWall("2", (line(1, "VT 14x47.4x3/4 FD FN WH"),), (v(0.0, 48.6, 96.0, "47.4"),))
    (p,) = panels(room(wall))
    assert p.elev_mm.status == "could not verify" and "top line" in p.elev_mm.why


def test_a_wall_not_read_counts_its_floor_lines_for_the_top_line() -> None:
    """A wall with no reading stands its FM lines on the floor (RULES 292)."""
    hung = ReadWall("2", (line(1, "VT 14x47.4x3/4 FD FN WH"),), (v(0.0, 48.6, 96.0, "47.4"),))
    other = ReadWall("3", (line(2, "VT 14x89x3/4 FD WH"),))
    model = room(hung, other)
    assert model.top_line.value_mm == pytest.approx(2260.0)
    assert panels(model)[0].elev_mm.value == pytest.approx(2260.0 - 1204.0)
    unread = next(s for s in model.schedules if s.wall == "3")
    assert not unread.read and unread.findings[0].status == "could not verify"


STACK = ReadWall("2", (line(2, "VT 14x34.8x3/4 FD WH"), line(2, "VT 14x47.4x3/4 FD WH"),
                       line(1, "WS WH 101 20.1x31x1-1/8 WH"), line(2, "SH 14x29.9x3/4 FIX WH")),
                 (v(0.0, 0.0, 34.8, "34.8"), v(0.0, 35.55, 82.95, "47.4"), v(30.625, 0.0, 34.8, "34.8"),
                  v(30.625, 35.55, 82.95, "47.4")))


def test_an_fm_upper_stands_on_the_counter_over_its_base() -> None:
    """RULES 73, 218, 221: a closet WS is built 3/4 in."""
    ps = panels(room(STACK))
    assert [p.elev_mm.value for p in ps] == [0.0, 884.0 + 19.304, 0.0, 884.0 + 19.304]
    assert [p.stacked.value for p in ps] == ["lower", "upper", "lower", "upper"]


def test_an_fm_upper_with_no_counter_on_the_wall_could_not_verify() -> None:
    wall = ReadWall("2", tuple(x for x in STACK.lines if not x.desc.startswith("WS")), STACK.verticals)
    ps = panels(room(wall))
    assert ps[1].elev_mm.status == "could not verify" and "no WS" in ps[1].elev_mm.why


def test_a_vertical_alone_at_its_x_is_not_stacked() -> None:
    assert {p.stacked.value for p in panels(room(PLAIN))} == {"no"}


# ----- the notch side (RULES 351-372)


def side_wall(*verticals: ReadVertical, lines: tuple[ICLine, ...]) -> ReadWall:
    return ReadWall("2", lines, verticals)


def test_a_bay_that_is_an_ic_shelf_width_is_a_section_and_covers_both_faces() -> None:
    _, wm, _ = panels(room(PLAIN))
    assert wm.side.value == "Thru" and "drill codes agree" in wm.side.why


def test_a_face_open_to_the_room_makes_the_panel_left_or_right() -> None:
    """Open left: Left; open right: Right (RULES 364-368)."""
    wall = side_wall(v(20.0, 36.5, 83.94, "47.4"), v(40.375, 36.5, 83.94, "47.4"),
                     lines=(line(1, "VT 14x47.4x3/4 LH RN WH"), line(1, "VT 14x47.4x3/4 RH LN WH"),
                            line(2, "SH 14x19.6x3/4 FIX WH")))
    assert [p.side.value for p in panels(room(wall, length=70.0))] == ["Left", "Right"]
    assert [p.product.value for p in panels(room(wall, length=70.0))] == ["WM Panel 48 Left", "WM Panel 48 Right"]


def test_a_panel_within_6_and_a_half_inches_of_the_wall_is_covered_by_it() -> None:
    """RULES 361-362, 370: a panel tight to a return wall is Thru."""
    wall = side_wall(v(6.0, 36.5, 83.94, "47.4"), v(26.375, 36.5, 83.94, "47.4"),
                     lines=(line(1, "VT 14x47.4x3/4 FD FN WH"), line(1, "VT 14x47.4x3/4 RH LN WH"),
                            line(2, "SH 14x19.6x3/4 FIX WH")))
    assert [p.side.value for p in panels(room(wall, length=70.0))] == ["Thru", "Right"]


def test_a_gap_that_is_no_shelf_width_is_no_section() -> None:
    """BUILD 274-277: a bridge or an opening is not a section (RULES 370)."""
    wall = side_wall(v(20.0, 36.5, 83.94, "47.4"), v(36.0, 36.5, 83.94, "47.4"), v(56.375, 36.5, 83.94, "47.4"),
                     lines=(line(1, "VT 14x47.4x3/4 LH RN WH"), line(1, "VT 14x47.4x3/4 FD FN WH"),
                            line(1, "VT 14x47.4x3/4 RH LN WH"), line(2, "SH 14x19.6x3/4 FIX WH")))
    sides = [p.side for p in panels(room(wall, length=90.0))]
    assert [s.value for s in sides] == [None, "Left", "Right"]  # the 15 1/4 in gap is no 19.6 shelf
    assert "decide by hand" in sides[0].why


def test_a_section_past_another_panel_covers_no_face() -> None:
    """A hung radius panel against a stacked tower: the drawers past the tower's base are not its section,
    so its right face is open (Right), and the tower's upper beside it is open on its left (Left)."""
    wall = side_wall(v(10.0, 36.5, 83.94, "47.4"), v(30.375, 36.5, 83.94, "47.4", "yes"),
                     v(31.125, 0.0, 42.36, "42.4"), v(31.125, 60.47, 83.94, "23.5"),
                     v(61.75, 0.0, 42.36, "42.4"), v(61.75, 60.47, 83.94, "23.5"),
                     lines=(line(1, "VT 14x47.4x3/4 LH RN WH"), line(1, "VT RAD 14x47.4x3/4 RH LN WH"),
                            line(2, "VT 19.1x42.4x3/4 FD WH"), line(1, "VT 14x23.5x3/4 LH RN WH"),
                            line(1, "VT 14x23.5x3/4 FD FN WH"), line(1, "SH 14x19.6x3/4 FIX WH"),
                            line(2, "SH 19.1x29.9x3/4 FIX WH"), line(1, "SH 14x29.9x3/4 FIX WH")))
    full = ReadWall("1", (line(1, "VT 14x83.9x3/4 FD WH"),))  # not read: its 83.9 sets the top line
    ps = panels(room(wall, full, length=64.5))
    assert [p.side.value for p in ps] == ["Left", "Right", None, "Left", None, "Thru"]
    assert [p.product.value for p in ps] == ["WM Panel 48 Left", "WM Panel 48 Right Radius", "FM 84",
                                             "WM Panel 48 Left", "FM 84", "WM Panel 48 Thru"]
    assert [p.elev_mm.value for p in ps] == [928.0, 928.0, 0.0, 1536.0, 0.0, 1536.0]


def test_both_faces_open_could_not_verify() -> None:
    """swap_rea.py 54: no section on either side, decide by hand."""
    wall = side_wall(v(30.0, 36.5, 83.94, "47.4"), lines=(line(1, "VT 14x47.4x3/4 FD FN WH"),))
    (p,) = panels(room(wall, length=70.0))
    assert p.side.status == "could not verify" and "decide by hand" in p.side.why


def test_geometry_the_drill_codes_disagree_with_could_not_verify() -> None:
    """RULES 372 says the plan wins, RULES 62 ranks IC codes above geometry inference: Jim's call."""
    wall = side_wall(v(20.0, 36.5, 83.94, "47.4"), v(40.375, 36.5, 83.94, "47.4"),
                     lines=(line(2, "VT 14x47.4x3/4 FD FN WH"), line(2, "SH 14x19.6x3/4 FIX WH")))
    for p in panels(room(wall, length=70.0)):
        assert p.side.status == "could not verify" and "RULES 62" in p.side.why


def test_a_side_that_depends_on_where_the_run_starts_could_not_verify() -> None:
    """Tight, centred or as drawn (placement.run_start): a run drawn 2 in from the start, ending 5 1/2 in
    from the far wall, has that wall within 6.5 in as drawn or centred, and 7 1/2 in away tight."""
    wall = side_wall(v(2.0, 36.5, 83.94, "47.4"), v(22.375, 36.5, 83.94, "47.4"),
                     lines=(line(1, "VT 14x47.4x3/4 FD FN WH"), line(1, "VT 14x47.4x3/4 RH LN WH"),
                            line(2, "SH 14x19.6x3/4 FIX WH")))
    left, right = panels(room(wall, length=28.625))
    assert left.side.value == "Thru"
    assert right.side.status == "could not verify" and "where the run starts" in right.side.why


def test_a_plain_floor_panel_has_no_notch() -> None:
    """RULES 397-398: LH or RH on a floor panel is Mozaik's holes."""
    fm = panels(room(PLAIN))[0]
    assert fm.side.status == "not needed" and fm.product.value == "FM 84"


def test_an_rea_floor_panel_takes_its_side_from_the_geometry() -> None:
    """RULES 415-417: FM 84 REA Thru, Left or Right."""
    wall = side_wall(v(0.0, 0.0, 83.94, "83.9"), v(18.625, 0.0, 83.94, "83.9"),
                     lines=(line(2, "VT REA 14x83.9x3/4 FD WH"), line(1, "SH 14x17.9x3/4 FIX WH")))
    assert [p.product.value for p in panels(room(wall, length=19.375))] == ["FM 84 REA Thru"] * 2


def test_an_rea_wall_panel_is_a_wm_48_rea() -> None:
    """RULES 415-416: WM 48 REA <side>, and Radius after it for a VT RAD."""
    wall = side_wall(v(0.0, 36.5, 83.94, "47.4", "yes"), v(18.625, 36.5, 83.94, "47.4"), v(40.0, 0.0, 83.94, "83.9"),
                     lines=(line(1, "VT RAD REA 14x47.4x3/4 FD FN WH"), line(1, "VT REA 14x47.4x3/4 FD FN WH"),
                            line(1, "VT 14x83.9x3/4 FD WH"), line(1, "SH 14x17.9x3/4 FIX WH"),
                            line(1, "SH 14x20.6x3/4 FIX WH")))
    ps = panels(room(wall, length=40.75))
    assert [p.product.value for p in ps] == ["WM 48 REA Thru Radius", "WM 48 REA Thru", "FM 84"]


def test_a_floor_panel_over_84_in_could_not_verify_its_product() -> None:
    """Jim's docs name FM 84 only (BUILD 243); his builds use FM 84 and FM 90 for the same heights."""
    wall = ReadWall("2", (line(1, "VT 14x89x3/4 FD WH"),), (v(0.0, 0.0, 89.0, "89"),))
    (p,) = panels(room(wall))
    assert p.height_mm.value == 2260.0 and p.product.status == "could not verify" and "FM 90" in p.product.why


def test_a_wall_not_in_the_table_could_not_verify_a_side() -> None:
    wall = ReadWall("9", PLAIN.lines, PLAIN.verticals)
    _, wm, _ = panels(room(wall), "9")
    assert wm.side.status == "could not verify" and "WALL/AREA table" in wm.side.why


# ----- the parts lines, read


def test_the_drill_code_is_lh_rh_or_fd() -> None:
    assert [sc.drill(d) for d in ("VT 14x47.4x3/4 LH RN WH", "VT 14x47.4x3/4 RH LN WH", "VT 14x83.9x3/4 WH",
                                  "VT 14x47.4x3/4 FD FN WH")] == ["LH", "RH", "FD", "FD"]


def test_a_shelf_line_gives_its_width() -> None:
    assert sc.shelf_widths_mm((line(1, "SH 14x23.9x3/4 ADJ WH"), line(1, "VT 14x83.9x3/4 FD WH"))) == [23.9 * IN]


@pytest.mark.parametrize("mount, rea, radius, side, name", [
    ("FM", False, False, None, "FM 84"), ("FM", True, False, "Left", "FM 84 REA Left"),
    ("WM", False, False, "Right", "WM Panel 48 Right"), ("WM", False, True, "Left", "WM Panel 48 Left Radius"),
    ("WM", True, True, "Thru", "WM 48 REA Thru Radius"), ("WM", True, False, None, None),
    ("FM", False, True, None, None)])
def test_the_product_is_the_library_name(mount: str, rea: bool, radius: bool, side: str | None, name: str | None) -> None:
    """RULES 377-398, 411-417; BUILD 243."""
    assert sc.product(mount, rea, radius, side) == name  # type: ignore[arg-type]
