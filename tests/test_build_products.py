"""
P5a's products (ngrunner/build/products.py): each panel a clone of Jim's library product the model names,
resized by value, its hang notch, line bores and banding by his rules, and an REA's drilling never touched
(RULES 377-450, BUILD 250-291). The products are made up in his products' shape (helpers_build.py).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import helpers_build as hb
from ngrunner.build import products as pr
from ngrunner.build.desxml import attrs, elements, one, start_tags


def lib(tmp_path: Path) -> pr.Library:
    return hb.library(tmp_path)


def clone(t: pr.ProductTemplate, height: float = 596, depth: float = 302, **kw) -> str:
    args = dict(x=1565.175, elev=1536, wall="3_1", id_tag=16, unique_id=50001012, cab_no=12, source_lib=hb.LIB)
    args.update(kw)
    return pr.clone(t, height=height, depth=depth, **args)


def points(text: str, where: str = "PartShapeXml") -> list[dict[str, str]]:
    shape = elements(text, where)[0].of(text)
    return [attrs(t) for t in start_tags(shape, "ShapePoint")]


# ----- which product


def test_the_library_holds_every_legal_name_and_none_superseded(tmp_path: Path) -> None:
    found = lib(tmp_path)
    assert set(found.names()) == set(pr.LEGAL) and not found.problems
    assert not set(pr.SUPERSEDED) & set(found.names())  # RULES 416-417
    assert pr.template_file("WM Panel 48 Left Radius") == "WM_Panel_48_Left_Radius.xml"  # wm_panel.py 5-7
    assert pr.template_file("FM 84 REA Thru") == "FM_84_REA_Thru.xml"  # rea_panel.py 26


def test_fm84_is_the_template_rooms_own(tmp_path: Path) -> None:
    fm = lib(tmp_path)["FM 84"]
    assert (fm.height, fm.depth) == (2132.0, 356.0) and f'SourceLib="{hb.LIB}"' in fm.text


def test_a_file_that_holds_another_product_or_none_is_left_out_with_why(tmp_path: Path) -> None:
    folder = hb.library_files(tmp_path / "t")
    (folder / "WM_Panel_48_Left.xml").write_text(hb.wm_square("Right"), encoding="utf-8")
    (folder / "WM_48_REA_Thru.xml").unlink()
    found = pr.load_library(folder, hb.template().fm84)
    assert "WM Panel 48 Left" not in found and "WM 48 REA Thru" not in found
    assert "holds 'WM Panel 48 Right'" in found.problems["WM Panel 48 Left"]
    assert found.problems["WM 48 REA Thru"] == "Jim's templates have no WM_48_REA_Thru.xml"


# ----- resizing by value (RULES 419-421, rea_panel.py 95-107)


def test_a_resize_moves_every_height_and_depth_even_a_resaved_mix(tmp_path: Path) -> None:
    out = clone(lib(tmp_path)["WM Panel 48 Thru"])
    head = attrs(one(out, "Product").head(out))
    part = attrs(start_tags(out, "CabProdPart")[0])
    assert (head["Height"], head["Depth"], part["L"], part["W"], part["Y"]) == ("596", "302", "596", "302", "302")
    assert [(p["X"], p["Y"]) for p in points(out)] == [("0", "0"), ("596", "0"), ("596", "302"), ("0", "302")]
    assert "1204.001" not in out and "355.9988" not in out


def test_the_template_rooms_fm84_2132_012_is_its_2132(tmp_path: Path) -> None:
    out = clone(lib(tmp_path)["FM 84"], 1076, 484, elev=0)
    assert [p["X"] for p in points(out)][:2] == ["1076", "1076"] and "2132" not in out


def test_width_is_19_on_the_product_and_its_top_outline(tmp_path: Path) -> None:
    out = clone(lib(tmp_path)["WM Panel 48 Left"])
    assert attrs(one(out, "Product").head(out))["Width"] == "19"
    assert {p["X"] for p in points(out, "TopShapeXml")} == {"0", "19"}  # BUILD 284


def test_an_reas_slant_moves_with_its_height_and_depth(tmp_path: Path) -> None:
    out = clone(lib(tmp_path)["WM 48 REA Left"], 1076, 406.5)
    xy = [(float(p["X"]), float(p["Y"])) for p in points(out)]
    assert (1076.0, 406.5 - 54.38) in xy and (1076 - 228.6, 406.5) in xy  # RULES 419-421


# ----- the notch, the bores, the banding


def test_every_hang_notchs_cached_x_is_height_less_191_9(tmp_path: Path) -> None:
    out = clone(lib(tmp_path)["WM Panel 48 Left Radius"])
    assert {attrs(t)["X"] for t in start_tags(out, "OperationGroove")} == {"404.1"}  # RULES 400-403


def test_line_bores_are_stripped_whole_except_on_an_rea(tmp_path: Path) -> None:
    found = lib(tmp_path)
    thru = clone(found["WM Panel 48 Thru"])
    assert "OperationLineBore" not in thru and "OpIdTagReference" not in thru  # BUILD 288, balanced
    assert "OperationLineBore" not in clone(found["FM 84"], 2132, 356, elev=0)
    assert thru.count("<OperationGroove") == 2


def test_an_reas_drilling_is_its_templates_byte_for_byte(tmp_path: Path) -> None:
    t = lib(tmp_path)["WM 48 REA Right"]
    out = clone(t, 1076, 406.5)
    assert pr.operations(out) == t.operations() and len(t.operations()) == 4  # RULES 423-427
    assert attrs(start_tags(out, "OperationGroove")[0])["X"] == "1012.1"  # never corrected (RULES 426)


def test_wm_banding_tapes_and_locks_every_edge_reaching_the_front_half_and_bares_the_back(tmp_path: Path) -> None:
    t = lib(tmp_path)["WM Panel 48 Thru"]
    taped_back = t.text.replace('ID="0" X="0" Y="0" PtType="0" EBand="0"', 'ID="0" X="0" Y="0" PtType="0" EBand="2"')
    t = pr.ProductTemplate(t.name, taped_back, t.height, t.depth, t.width)
    out = clone(t)
    got = [(p["EBand"], p["EBandLock"]) for p in points(out)]
    # back (0,0)->(H,0) bare; top, front and bottom taped and locked (RULES 431-436)
    assert got == [("0", "False"), ("1", "True"), ("1", "True"), ("1", "True")]


def test_a_slot_jim_picked_is_kept(tmp_path: Path) -> None:
    t = lib(tmp_path)["WM Panel 48 Thru"]
    t = pr.ProductTemplate(t.name, t.text.replace('SideName="Front"', 'SideName="Front"').replace(
        'EBand="1" EBandLock="False" SideName="Front"', 'EBand="4" EBandLock="False" SideName="Front"'),
        t.height, t.depth, t.width)
    assert ("4", "True") in [(p["EBand"], p["EBandLock"]) for p in points(clone(t))]


def test_an_fm_panel_keeps_its_templates_banding(tmp_path: Path) -> None:
    out = clone(lib(tmp_path)["FM 84"], 2132, 356, elev=0)
    assert [(p["EBand"], p["EBandLock"]) for p in points(out)][:2] == [("0", "False"), ("1", "False")]  # RULES 448


def test_a_clone_takes_its_place_and_identity_and_the_rooms_library(tmp_path: Path) -> None:
    out = clone(lib(tmp_path)["WM Panel 48 Left"])
    head = attrs(one(out, "Product").head(out))
    assert (head["UniqueID"], head["IDTag"], head["CabNo"], head["X"], head["Elev"], head["Wall"],
            head["SourceLib"]) == ("50001012", "16", "12", "1565.175", "1536", "3_1", hb.LIB)
    assert head["ProdName"] == "WM Panel 48 Left" and out.count("SourceLib=") == 1


@pytest.mark.parametrize("name, wm, rea", [("WM Panel 48 Thru", True, False), ("WM 48 REA Left Radius", True, True),
                                           ("FM 84 REA Right", False, True), ("FM 84", False, False)])
def test_the_families(name: str, wm: bool, rea: bool) -> None:
    assert (pr.is_wm(name), pr.is_rea(name)) == (wm, rea)
