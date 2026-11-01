"""
P5a's gates on a written room (ngrunner/build/gates.py): the bytes read back and held to the model, the
wall's IC lines and Jim's rules (RULES 313-450, BUILD 199-296, SCHEMA 7-37). A room written right passes
every gate; each test then breaks one thing in the written bytes, or in the measured drawing, and the one
gate that guards it catches it. Everything is made up (helpers_build.py).
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path

import pytest

import helpers_build as hb
from ngrunner.build import gates as gt
from ngrunner.build import products as pr
from ngrunner.build import writer as wr
from ngrunner.build.room import ReadWall
from ngrunner.guards.facts import ToolRun

IN = 25.4
# The made-up wall 2 drawn as Jim's elev_panels.py prints it: the wall's box at both ends, each panel's lines.
DRAWN = (gt.DrawnVertical(0.13, 98.0, 0.0), gt.DrawnVertical(0.75, 82.5, 2.0),
         gt.DrawnVertical(30.4, 82.0, 37.5), gt.DrawnVertical(60.05, 82.5, 2.0), gt.DrawnVertical(60.62, 98.0, 0.0))
MEASURED = {"2": gt.Measured(DRAWN)}


@pytest.fixture
def lib(tmp_path: Path) -> pr.Library:
    return hb.library(tmp_path)


def run(lib: pr.Library, change: Callable[[str], str] | None = None, *, model=None,
        walls: tuple[ReadWall, ...] = hb.WALLS, measured=MEASURED) -> gt.RoomGates:
    model = model or hb.model()
    room = wr.write_room(model, 1, hb.template(), lib, hb.SETTINGS)
    assert isinstance(room, wr.WrittenRoom)
    if change is not None:
        room = dataclasses.replace(room, content=change(room.content.decode("utf-8")).encode("utf-8"))
    return gt.gate_room(room, model, walls, hb.template(), lib, measured)


def only(gates: gt.RoomGates) -> dict[str, str]:
    return {g.name: g.status for g in gates.not_passed()}


def test_a_room_written_right_passes_every_gate(lib) -> None:
    gates = run(lib)
    assert gates.passed and [g.name for g in gates.gates] == list(gt.REQUIRED), gt.report(gates)


def test_a_room_whose_drawing_wasnt_measured_could_not_verify_gate_6(lib) -> None:
    gates = run(lib, measured=None)
    assert only(gates) == {"gate 6": "could not verify"} and not gates.passed
    assert gates.gate("gate 6").items == ("wall 2: its drawing was not measured",)


def test_a_room_written_with_rea_panels_passes_every_gate(lib) -> None:
    wall = ReadWall("2", (hb.line(2, "VT 14x83.9x3/4 FD REA WH"), hb.line(1, "VT 14x47.4x3/4 FD FN REA WH"),
                          *hb.PLAIN.lines[2:]), hb.PLAIN.verticals)
    gates = run(lib, model=hb.model(hb.SIDES[0], wall, hb.SIDES[1]), walls=(hb.SIDES[0], wall, hb.SIDES[1]))
    assert gates.passed, gt.report(gates)


# ----- the file, the walls and the panels, audited back


@pytest.mark.parametrize("change, gate", [
    (lambda t: t.replace("\r\n  <Walls>", "\n  <Walls>"), "file"),
    (lambda t: "16" + t[2:], "file"),
    (lambda t: t.replace("</Products>", "</Product>"), "file"),
    (lambda t: t.replace(' CathedralHeight="0"', "", 1), "attributes"),
    (lambda t: t.replace('MiterBack="False" />', 'MiterBack="False" Extra="1" />', 1), "attributes"),
    (lambda t: t.replace('Len="1543.05"', 'Len="1543.1"'), "walls"),
    (lambda t: t.replace('IdTagCount="6"', 'IdTagCount="3"'), "walls"),
    (lambda t: t.replace('Ang="270"', 'Ang="90"'), "walls"),
    (lambda t: t.replace('X="771.525"', 'X="772.525"', 1), "panels"),
    (lambda t: t.replace('Wall="2_1"', 'Wall="1_1"', 1), "panels"),
])
def test_a_written_value_that_isnt_the_models_fails_its_gate(lib, change, gate: str) -> None:
    assert gate in only(run(lib, change))


def test_a_model_holding_a_could_not_verify_fails_settled(lib) -> None:
    from ngrunner.build.room import unverified

    model = hb.model()
    room = wr.write_room(model, 1, hb.template(), lib, hb.SETTINGS)
    assert isinstance(room, wr.WrittenRoom)
    broken = dataclasses.replace(model, joints=unverified("made up"))
    gates = gt.gate_room(room, broken, hb.WALLS, hb.template(), lib, MEASURED)
    assert only(gates)["settled"] == "failed"


# ----- RULES 313-319


def test_gate_1_a_wall_with_more_verticals_listed_than_placed(lib) -> None:
    wall = dataclasses.replace(hb.PLAIN, lines=(hb.line(3, "VT 14x83.9x3/4 FD WH"), *hb.PLAIN.lines[1:]))
    gates = run(lib, walls=(hb.SIDES[0], wall, hb.SIDES[1]))
    assert only(gates) == {"gate 1": "failed", "gate 2": "failed"}


def test_gate_2_a_panel_built_wm_whose_line_has_no_notch(lib) -> None:
    wall = dataclasses.replace(hb.PLAIN, lines=(hb.PLAIN.lines[0], hb.line(1, "VT 14x47.4x3/4 FD WH"),
                                                *hb.PLAIN.lines[2:]))
    assert only(run(lib, walls=(hb.SIDES[0], wall, hb.SIDES[1]))) == {"gate 2": "failed", "drill codes": "failed"}


def test_gate_3_a_vt_rad_line_and_no_radius_product(lib) -> None:
    wall = dataclasses.replace(hb.PLAIN, lines=(hb.PLAIN.lines[0], hb.line(1, "VT RAD 14x47.4x3/4 FD FN WH"),
                                                *hb.PLAIN.lines[2:]))
    assert only(run(lib, walls=(hb.SIDES[0], wall, hb.SIDES[1]))) == {"gate 3": "failed"}


def test_gates_1_to_3_could_not_verify_on_an_unreadable_quantity(lib) -> None:
    wall = dataclasses.replace(hb.PLAIN, lines=(hb.line(None, "VT 14x83.9x3/4 FD WH"), *hb.PLAIN.lines[1:]))
    found = only(run(lib, walls=(hb.SIDES[0], wall, hb.SIDES[1])))
    assert found == {"gate 1": "could not verify", "gate 2": "could not verify", "gate 3": "could not verify"}


def test_gate_4_a_height_off_the_32k_20_grid(lib) -> None:
    gates = run(lib, lambda t: t.replace('Height="1204"', 'Height="1210"').replace('L="1204"', 'L="1210"'))
    assert {"gate 4", "gate 5", "panels"} <= set(only(gates))


def test_gate_5_a_wm_panel_off_the_rooms_top_line(lib) -> None:
    gates = run(lib, lambda t: t.replace('Elev="928"', 'Elev="896"'))
    assert only(gates) == {"panels": "failed", "gate 5": "failed"}


def test_gate_5_reads_t_as_top_line_py_does() -> None:
    assert gt.room_top([(0, 2132)], [(928, 1204)]) == 2132  # an FM panel beside the run (RULES 334)
    assert gt.room_top([(0, 1076)], [(1536, 596)]) == 2132  # none beside: the run's own top (RULES 337)
    assert gt.room_top([], [(1204, 1204), (1300, 1204)]) == 2408  # the commonest top, far from 2132
    assert gt.room_top([(0, 2120)], [(916, 1204)]) == 2132  # within 1 in of 2132 is 2132 (RULES 338)
    assert gt.room_top([(0, 2132)], []) is None


# ----- gate 6: the drawing as Jim's elev_panels.py measures it (RULES 320-322)


def test_gate_6_reads_elev_panels_table_and_fails_closed_on_anything_else() -> None:
    table = ("   x (in)      top   bottom  drawn len\n     0.13     98.0      0.0       98.0\n"
             "    30.40     82.0     37.5       44.5\n")
    assert gt.read_measure(table).verticals == (gt.DrawnVertical(0.13, 98.0, 0.0), gt.DrawnVertical(30.4, 82.0, 37.5))
    assert gt.read_measure("no wall box found on that page").verticals is None
    assert gt.read_measure(table + "warning: something\n").verticals is None
    assert gt.read_measure(table.replace("44.5", "40.0")).verticals is None


@pytest.mark.parametrize("drawn, status", [
    (DRAWN[:2] + DRAWN[3:], "failed"),  # the hung panel isn't drawn
    (DRAWN + (gt.DrawnVertical(45.0, 82.0, 2.0),), "failed"),  # a vertical drawn with no panel
    ((DRAWN[0], DRAWN[1], gt.DrawnVertical(30.4, 82.0, 47.5), *DRAWN[3:]), "failed"),  # drawn 34.5: another height
    ((DRAWN[0], gt.DrawnVertical(0.75, 67.65, 2.0), *DRAWN[2:]), "could not verify"),  # 65.65: 47.4 or 83.9
    (DRAWN[1:4], "passed"),  # no box lines printed
])
def test_gate_6_holds_each_placed_panel_to_the_line_drawn_at_its_x(lib, drawn, status: str) -> None:
    lines = (*hb.PLAIN.lines, hb.line(0, "VT 14x35x3/4 FD WH"), hb.line(0, "VT 14x127.9x3/4 FD WH"))
    wall = dataclasses.replace(hb.PLAIN, lines=lines)
    gates = run(lib, measured={"2": gt.Measured(drawn)}, walls=(hb.SIDES[0], wall, hb.SIDES[1]))
    assert gates.gate("gate 6").status == status, gates.gate("gate 6").items


def test_gate_6_could_not_verify_when_jims_script_cant_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from ngrunner import toolkit

    said = ToolRun(1, "", "FileNotFoundError: [WinError 2] ... 'pdftoppm'", False)
    monkeypatch.setattr(toolkit, "run_tool", lambda *a, **k: said)
    got = gt.measure_wall(object(), Path("C:/x.pdf"), 3, 80.0, 98.5)
    assert got.verticals is None and "pdftoppm" in got.why
    stopped = ToolRun(1, "", "SystemExit: no wall box found on that page", False)
    monkeypatch.setattr(toolkit, "run_tool", lambda *a, **k: stopped)
    assert gt.measure_wall(object(), Path("C:/x.pdf"), 3, 80.0, 98.5).why.endswith("no wall box found on that page")
    table = "   x (in)      top   bottom  drawn len\n     1.00     82.0      2.0       80.0\n"
    monkeypatch.setattr(toolkit, "run_tool", lambda *a, **k: ToolRun(0, table, "", False))
    assert gt.measure_wall(object(), Path("C:/x.pdf"), 3, 80.0, 98.5).verticals == (gt.DrawnVertical(1.0, 82.0, 2.0),)


# ----- names, notches, banding, REA, bores, parts, libraries


@pytest.mark.parametrize("change, gate", [
    (lambda t: t.replace('ProdName="WM Panel 48 Thru"', 'ProdName="WM Panel 48 Thru Rad"'), "legal names"),
    (lambda t: t.replace('ProdName="WM Panel 48 Thru"', 'ProdName="WM Panel 48 Left"'), "drill codes"),
    (lambda t: t.replace('Hide="True"', 'Hide="False"', 1), "notch"),
    (lambda t: t.replace('Depth_Eq="FEnd.th" IsUserOp="True" FlipSideOp="False"',
                         'Depth_Eq="FEnd.th-HangNotchSkin" IsUserOp="True" FlipSideOp="False"'), "notch"),
    (lambda t: t.replace('X="1012.1"', 'X="1012"', 1), "groove"),
    (lambda t: t.replace('EBand="1" EBandLock="True" SideName="Front"', 'EBand="1" EBandLock="False" SideName="Front"', 1),
     "WM banding"),
    (lambda t: t.replace('<PartOpsXml Version="2">', '<PartOpsXml Version="2"><OperationLineBore Quan="1" />', 1),
     "line bores"),
    (lambda t: t.replace('W="356" L="2132"', 'W="356" L="2131"', 1), "part"),
    (lambda t: t.replace(f'SourceLib="{hb.LIB}"', 'SourceLib="1 PAC Closet Library V2"', 1), "SourceLib"),
    (lambda t: t.replace("<Tops />", '<Tops MatORId="5" />'), "SourceLib"),
])
def test_each_of_jims_product_rules_has_a_gate_that_catches_it(lib, change, gate: str) -> None:
    assert gate in only(run(lib, change))


def rea_room(lib: pr.Library, change: Callable[[str], str]) -> gt.RoomGates:
    wall = ReadWall("2", (hb.line(2, "VT 14x83.9x3/4 FD REA WH"), hb.line(1, "VT 14x47.4x3/4 FD FN REA WH"),
                          *hb.PLAIN.lines[2:]), hb.PLAIN.verticals)
    return run(lib, change, model=hb.model(hb.SIDES[0], wall, hb.SIDES[1]), walls=(hb.SIDES[0], wall, hb.SIDES[1]))


def test_an_reas_drilling_changed_by_a_byte_fails(lib) -> None:
    assert only(rea_room(lib, lambda t: t.replace('Diameter="8"', 'Diameter="9"', 1))) == {"REA": "failed"}


def test_an_reas_slant_left_at_its_template_size_fails(lib) -> None:
    gates = rea_room(lib, lambda t: t.replace('Y="301.62"', 'Y="300"', 1))
    assert only(gates) == {"REA": "failed"}


def test_an_reas_notch_on_the_other_face_than_its_bores_fails(lib) -> None:
    """Its notch's face is the one thing of an REA's drilling Jim's rules let NG Runner change (RULES 404-409,
    423-426), so a notch on the other face fails the notch gate, as a change to its drilling the REA gate."""
    gates = rea_room(lib, lambda t: t.replace('Depth_Eq="FEnd.th-5" IsUserOp="True" FlipSideOp="True"',
                                              'Depth_Eq="FEnd.th-5" IsUserOp="True" FlipSideOp="False"', 1))
    assert only(gates) == {"notch": "failed", "REA": "failed"}


def test_an_reas_notch_off_height_less_191_9_is_reported_not_failed_or_corrected(lib) -> None:
    t = lib["WM 48 REA Thru"]
    off = pr.ProductTemplate(t.name, t.text.replace('X="1012.1"', 'X="1000"'), t.height, t.depth, t.width)
    lib2 = pr.Library({**{n: lib[n] for n in lib}, t.name: off})
    gates = rea_room(lib2, lambda t: t)
    assert gates.passed and any("REA notch cached X 1000" in r for r in gates.review)  # RULES 426


def test_a_superseded_rea_name_is_never_legal(lib) -> None:
    gates = rea_room(lib, lambda t: t.replace('ProdName="FM 84 REA Thru"', 'ProdName="FM 84 REA"', 1))
    assert "superseded" in " ".join(gates.gate("legal names").items)


def test_a_gate_that_didnt_run_leaves_the_room_not_passed(lib) -> None:
    gates = run(lib)
    fewer = dataclasses.replace(gates, gates=tuple(g for g in gates.gates if g.name != "WM banding"))
    assert not fewer.passed and fewer.missing() == ["WM banding"]
    assert gt.report(fewer) == ["Room1.des WM banding: the gate didn't run"]


def test_an_unreadable_room_fails_and_says_why(lib) -> None:
    gates = run(lib, lambda t: t.replace("<Products>", "<Products><Product>", 1))
    assert not gates.passed and {"file", "read back"} <= {g.name for g in gates.not_passed()}
