"""
NG Runner's own check of a built room's top line (ngrunner/checks/topline.py), with P5's placement rule
read as it is: one T per room and every WM panel on it (RULES 330-339), and gate 5's Elev grid (RULES
319). And its items, as an audit and a check carry them (findings.py), each with Jim's words and a chat line.

The rooms are synthetic, in the shape Mozaik writes (version line 15, CRLF, then the XML), with made-up
product names where a name is no panel's. tests/test_checks_topline_real.py runs it on the real rooms.
"""
from __future__ import annotations

import pytest

from test_checks import CLEAN, read
from ngrunner.checks import findings as f
from ngrunner.checks import topline as t
from ngrunner.guards.facts import COULD_NOT_VERIFY, FAILED, PASSED
from ngrunner.messages import Message, templates
from ngrunner.messages.lint import lint

JOB = "Testerson_101426"


def panel(cab: int, name: str, elev: float, height: float, width: float = 19.0) -> str:
    return (f'<Product CabNo="{cab}" Count="1" ProdName="{name}" Width="{width}" Height="{height}"'
            f' Elev="{elev}" X="{cab * 100}" />')


def room(*products: str) -> bytes:
    return ('15\r\n<?xml version="1.0" encoding="utf-8"?>\r\n<Room Quan="1"><Products>'
            + "".join(products) + "</Products></Room>").encode("utf-8")


FM_2132 = panel(1, "FM 84", 0, 2132)              # 2132 = 32 x 66 + 20, a closet height on the grid
WM_ON = panel(3, "WM Panel 48 Thru", 320, 1812)    # 320 + 1812 = 2132
WM_LOW = panel(5, "WM Panel 48 Thru", 288, 1812)   # its top 2100: off the room's one top line


def test_wm_panels_on_the_top_line_an_fm_panel_beside_them_sets_pass() -> None:
    top = t.room_top(1, room(FM_2132, WM_ON))

    assert (top.code, top.t_mm, top.found) == (PASSED, 2132.0, ())


def test_a_wm_panel_off_that_top_line_is_named() -> None:
    top = t.room_top(1, room(FM_2132, WM_ON, WM_LOW))

    assert (top.code, top.off_top, top.found) == (FAILED, (5,), (t.OFF_TOP,))


def test_the_fm_panel_beside_the_run_sets_t_however_far_from_2132() -> None:
    # RULES 334-336: the WM tops align with that room's FM tops, however far from 2132.
    fm = panel(1, "FM 84", 0, 1940)
    top = t.room_top(1, room(fm, panel(3, "WM Panel 48 Thru", 1940 - 1012, 1012)))

    assert (top.code, top.t_mm) == (PASSED, 1940.0)


def test_with_no_fm_panel_beside_a_run_within_an_inch_of_2132_hangs_on_2132() -> None:
    # RULES 338: a T within 1 in of 2132 that is not 2132 is a mistake.
    top = t.room_top(1, room(panel(3, "WM Panel 48 Thru", 340, 1812)))       # its top 2152

    assert (top.code, top.t_mm, top.off_top) == (FAILED, 2132.0, (3,))


def test_with_no_fm_panel_beside_it_one_top_as_built_is_the_rooms_t() -> None:
    # RULES 337: no FM panel beside the run, T is the run's own top as built. 1940 is on 32k+20.
    top = t.room_top(1, room(panel(3, "WM Panel 48 Thru", 928, 1012), panel(5, "WM 48 REA Left", 608, 1332)))

    assert (top.code, top.t_mm) == (PASSED, 1940.0)


def test_with_no_fm_panel_beside_it_two_tops_as_built_are_the_defect_rules_names() -> None:
    # RULES 330-331: two WM tops in one room is a defect, and nothing beside them says which is right.
    top = t.room_top(1, room(panel(3, "WM Panel 48 Thru", 928, 1012), panel(5, "WM Panel 48 Thru", 960, 1012)))

    assert (top.code, top.found, top.tops) == (FAILED, (t.TWO_TOPS,), (1940.0, 1972.0))


def test_a_top_as_built_off_the_height_grid_is_left_to_jim() -> None:
    # RULES 337 takes it as built; gate 5 (RULES 319) wants Elev on 32k: P5a placement left it for Jim.
    top = t.room_top(1, room(panel(3, "WM Panel 48 Thru", 914.4, 1524)))       # cut at 96 in exactly

    assert (top.code, top.found) == (COULD_NOT_VERIFY, (t.AS_BUILT,))


def test_a_wm_panel_on_the_top_line_with_its_elev_off_the_grid_fails_gate_5() -> None:
    # 2132 - 1333 = 799: on T, but a height off 32k+20 puts Elev off 32k.
    top = t.room_top(1, room(FM_2132, panel(3, "WM Panel 48 Thru", 799, 1333)))

    assert (top.code, top.off_grid, top.found) == (FAILED, (3,), (t.ELEV_GRID,))


def test_a_room_with_no_wm_panel_and_a_product_wider_than_a_panel_have_nothing_to_hang() -> None:
    wide = panel(7, "WM Panel 48 Thru", 0, 900, width=600)                   # no panel: over 25.4 wide
    assert t.room_top(1, room(FM_2132, wide)).code == t.NONE
    assert t.room_top(1, room(panel(2, "Testerson Special", 0, 900))).code == t.NONE


def test_room0_and_a_room_that_isnt_a_design_are_left_out() -> None:
    tops = t.rooms_top({"Room0.des": room(WM_LOW), "Room1.des": room(FM_2132, WM_ON), "Room2.des": b"not a room"})

    assert [top.room for top in tops] == [1]


def test_a_panel_whose_numbers_dont_read_is_unreadable_never_guessed() -> None:
    with pytest.raises(t.RoomUnreadable):
        t.room_top(1, room('<Product CabNo="3" ProdName="WM Panel 48 Thru" Width="19" Height="tall" Elev="0" />'))


# ----- as items: an audit's and a check's, with Jim's words and a chat line


def found(rooms: dict[str, bytes], *, sections=CLEAN, flagged=(), kind: str = "audit") -> f.Findings:
    return f.findings_of(read(sections, flagged=flagged), kind=kind, room_files=rooms, q1_settled=True)


def test_an_audit_names_the_panel_off_the_top_line_with_a_chat_line() -> None:
    result = found({"Room0.des": room(), "Room1.des": room(FM_2132, WM_ON, WM_LOW)})

    item, = [i for i in result.items if i.checker == f.ROOM_TOP]
    assert (item.kind, item.what, item.tags, item.settles) == ("fix", "off_top", ("R1C5",), True)
    assert templates.check_item_words(item) == "R1C5: the WM panel isn't on the room's one top line"
    assert templates.check_chat_line(JOB, item) == f"{JOB}: hang R1C5 on the room's one top line, then run crosscheck"
    assert result.code == FAILED and f.ROOM_TOP in {v.checker for v in result.verdicts}


def test_where_jims_top_line_names_the_room_ng_runners_own_item_for_it_is_left_out() -> None:
    his = "   Room1.des    WM Panel 48 Thru           H=1812    Elev   288.0 ->   320.0  (top 2100 -> 2132)\n\n" \
          "would change: 1 WM panels"
    rooms = {"Room1.des": room(FM_2132, WM_ON, WM_LOW, panel(9, "WM Panel 48 Thru", 799, 1333))}

    result = found(rooms, sections={**CLEAN, "top_line": his})

    assert sorted((i.checker, i.what, i.tags) for i in result.items if i.kind == "fix") == [
        (f.ROOM_TOP, "elev_grid", ("R1C9",)), ("top_line", "top_line", ("R1",))]


def test_two_tops_and_a_top_left_to_jim_read_in_his_format() -> None:
    rooms = {"Room1.des": room(panel(3, "WM Panel 48 Thru", 928, 1012), panel(5, "WM Panel 48 Thru", 960, 1012)),
             "Room2.des": room(panel(3, "WM Panel 48 Thru", 914.4, 1524)),
             "Room3.des": room(FM_2132, panel(3, "WM Panel 48 Thru", 799, 1333))}

    result = found(rooms, kind="check")

    two, = [i for i in result.items if i.what == "two_tops"]
    left, = [i for i in result.items if i.what == "top_as_built"]
    grid, = [i for i in result.items if i.what == "elev_grid"]
    assert (two.tags, left.tags, left.kind, left.settles) == (("R1",), ("R2",), "could_not_verify", False)
    assert grid.tags == ("R3C3",) and templates.check_chat_line(JOB, grid) == (
        f"{JOB}: put R3C3 on a height and an elevation on the grids, then run crosscheck")
    lines = tuple(f"{i.n}. {templates.check_item_line(JOB, i)}" for i in result.items)
    assert lint(Message("check_result", f"{JOB} build checked 9:42 PM (trial)", lines)) == []
    assert templates.check_item_words(left) == ("Could not verify R2: the room's top line, as built, is off the"
                                                " height grid, which Jim's rules leave to him")


def test_the_same_finding_has_the_same_fingerprint_and_another_top_another() -> None:
    once = found({"Room1.des": room(FM_2132, WM_ON, WM_LOW)}).items
    again = found({"Room1.des": room(FM_2132, WM_ON, WM_LOW)}).items
    other = found({"Room1.des": room(FM_2132, WM_ON, panel(5, "WM Panel 48 Thru", 256, 1812))}).items

    assert once[0].fingerprint == again[0].fingerprint != other[0].fingerprint
