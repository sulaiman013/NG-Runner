"""
P5a's room writer (ngrunner/build/writer.py, desxml.py): Room#.des from the room model, seeded from the
template job's Room1, every value Jim's rules settle written as BUILD 140-296 and SCHEMA 7-37 say, and a
room that needs any value that couldn't be verified not written, with the values that block it (AGENTS.md
rule 8). The template, the products and the room are made up (helpers_build.py).
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

import helpers_build as hb
from ngrunner.build import writer as wr
from ngrunner.build.desxml import XmlError, attrs, crlf, elements, num, one, set_attr, start_tags
from ngrunner.build.room import Plan, unverified


@pytest.fixture
def lib(tmp_path: Path):
    return hb.library(tmp_path)


def written(model=None, *, lib, number: int = 1, settings: wr.RoomSettings = hb.SETTINGS) -> wr.WrittenRoom:
    out = wr.write_room(model or hb.model(), number, hb.template(), lib, settings)
    assert isinstance(out, wr.WrittenRoom), getattr(out, "blocked_by", out)
    return out


def text(room: wr.WrittenRoom) -> str:
    return room.content.decode("utf-8")


# ----- the balanced scanner (BUILD 146-147)


def test_the_scanner_finds_whole_elements_past_a_quoted_greater_than_and_nested_namesakes() -> None:
    doc = '<a><Products x="1 > 0"><Product n="1"><Product n="2" /></Product><Product n="3" /></Products></a>'
    found = elements(doc, "Product")
    assert [attrs(e.head(doc))["n"] for e in found] == ["1", "3"]
    assert found[0].of(doc) == '<Product n="1"><Product n="2" /></Product>'
    assert one(doc, "Products").head(doc) == '<Products x="1 > 0">'
    assert elements(doc, "ProductOptions") == []  # a name is matched whole, never as a prefix


def test_the_scanner_refuses_an_element_never_closed_and_set_attr_one_never_there() -> None:
    with pytest.raises(XmlError):
        elements("<a><Wall>", "Wall")
    with pytest.raises(XmlError):
        set_attr('<Wall Len="1" />', "Height", "2")
    assert set_attr('<Wall Len="1" WallLen="5" />', "Len", "2") == '<Wall Len="2" WallLen="5" />'
    assert start_tags('<a><Wall X="1" /><Walls /></a>', "Wall") == ['<Wall X="1" />']


@pytest.mark.parametrize("value, shown", [(2132.0, "2132"), (771.525, "771.525"), (0.1 + 0.2, "0.3"),
                                          (-0.0001, "0"), (1012.1000001, "1012.1"), (-117.475, "-117.475")])
def test_numbers_are_written_to_three_places_trailing_zeros_stripped_never_g(value: float, shown: str) -> None:
    assert num(value) == shown  # BUILD 290-291


def test_every_line_ends_crlf() -> None:
    assert crlf("a\nb\r\nc\n") == "a\r\nb\r\nc\r\n"


# ----- the template


def test_the_template_gives_its_one_wall_its_fm84_its_library_and_a_reference_rooms_joint() -> None:
    t = hb.template()
    assert t.version == "15" and t.source_lib == hb.LIB and t.categories == ("3", "1")
    assert t.wall.startswith("<Wall IDTag") and t.wall.endswith("</Wall>")
    assert t.joint == hb.JOINT and t.joint_attrs[:2] == ("Wall1", "Wall2") and len(t.joint_attrs) == 8
    assert 'ProdName="FM 84 "' in t.fm84 and "CathedralHeight" in t.wall_attrs


@pytest.mark.parametrize("room, words", [
    (hb.template_room(version="<?xml"), "version line"),
    (hb.template_room(walls=2), "2 walls"),
    (hb.template_room(fm=False), "no single FM 84"),
    (hb.template_room().replace(b'SourceLib="Testerson Library" Width="400"', b'SourceLib="Other" Width="400"'),
     "SourceLib"),
    (b"\xff\xfe", "UTF-8"),
])
def test_a_template_room_the_writer_cant_seed_from_is_refused_in_words(room: bytes, words: str) -> None:
    with pytest.raises(wr.TemplateError, match=words):
        wr.load_template(room, [hb.reference_room()])


# ----- a room written


def test_the_room_tag_has_its_uniqueid_name_and_the_highest_idtag(lib) -> None:
    room = text(written(hb.model(name='Testerson "Primary" & Hall'), lib=lib, number=2))
    head = attrs(one(room, "Room").head(room))
    assert head["UniqueID"] == "50002"  # BUILD 149
    assert head["Name"] == "Testerson &quot;Primary&quot; &amp; Hall"
    assert head["IdTagCount"] == "6"  # 3 walls, then 3 products: SCHEMA 32-37
    assert attrs(one(room, "RoomParms").head(room))["H_Walls"] == "2489.2"  # the tallest wall


def test_the_file_is_crlf_with_the_templates_version_line_and_reads_as_xml(lib) -> None:
    import xml.etree.ElementTree as ET

    content = written(lib=lib).content
    assert content.startswith(b"15\r\n<?xml") and content.count(b"\n") == content.count(b"\r\n")
    ET.fromstring(content.partition(b"\r\n")[2])


def test_walls_are_clones_of_the_templates_wall_with_only_their_numbers_changed(lib) -> None:
    room = text(written(lib=lib))
    tags = start_tags(room, "Wall")
    template = hb.template()
    assert [tuple(attrs(t)) for t in tags] == [template.wall_attrs] * 3  # SCHEMA 9-30
    first = attrs(tags[1])
    assert (first["IDTag"], first["Len"], first["Height"], first["PosX"], first["PosY"], first["Ang"],
            first["WallNumber"]) == ("2", "1543.05", "2489.2", "0", "609.6", "0", "2")
    fixed = attrs(template.wall[:template.wall.index(">") + 1])
    assert all(first[k] == fixed[k] for k in fixed if k not in ("IDTag", "Len", "Height", "PosX", "PosY", "Ang",
                                                                "WallNumber"))
    assert "<LabelDimensionOverrideMap />" in one(room, "Walls").of(room)


def test_joints_are_clones_of_a_reference_rooms_joint_naming_idtags(lib) -> None:
    room = text(written(lib=lib))
    joints = [attrs(t) for t in start_tags(room, "WallJoint")]
    assert [(j["Wall1"], j["Wall2"]) for j in joints] == [("1", "2"), ("2", "3")]
    assert all(tuple(j) == hb.template().joint_attrs and j["Wall1Corner"] == "1" for j in joints)


def test_a_chain_out_of_table_order_puts_each_panel_on_its_walls_place_in_the_file(lib) -> None:
    """Frozen Z's rooms whose chain isn't the table's put their panels on the file position, 3_1 for wall 2."""
    plan = Plan(("3", "1", "2"), (0.0, 270.0, 180.0))
    room = text(written(hb.model(plan=plan), lib=lib))
    assert [attrs(t)["WallNumber"] for t in start_tags(room, "Wall")] == ["3", "1", "2"]
    assert {attrs(t)["Wall"] for t in start_tags(room, "Product")} == {"3_1"}
    assert [(attrs(t)["Wall1"], attrs(t)["Wall2"]) for t in start_tags(room, "WallJoint")] == [("3", "1"), ("1", "2")]


def test_the_colour_goes_in_as_given_and_the_templates_own_blank_set_goes(lib) -> None:
    room = text(written(lib=lib))
    head = attrs(one(room, "RoomSet").head(room))
    assert {head[a] for a in wr.DRAWER_ATTRS} == {hb.FRONT}
    assert hb.MATERIALS in room and 'RootTemplateId="88"' not in room
    assert head["AutofillLib"] == hb.LIB and head["Note"] == "a > b"  # the rest of the tag as it was


def test_every_panel_is_placed_on_its_wall_left_to_right_with_its_own_identity(lib) -> None:
    room = text(written(lib=lib))
    prods = [attrs(t) for t in start_tags(room, "Product")]
    assert [p["ProdName"].strip() for p in prods] == ["FM 84", "WM Panel 48 Thru", "FM 84"]
    assert [(p["X"], p["Elev"], p["Height"], p["Depth"]) for p in prods] == [
        ("0", "0", "2132", "356"), ("771.525", "928", "1204", "356"), ("1524", "0", "2132", "356")]
    assert [(p["IDTag"], p["UniqueID"], p["CabNo"], p["Wall"]) for p in prods] == [
        ("4", "50001001", "1", "2_1"), ("5", "50001002", "2", "2_1"), ("6", "50001003", "3", "2_1")]
    assert {p["SourceLib"] for p in prods} == {hb.LIB}  # BUILD 289: the template room's


def test_nothing_but_the_walls_joints_products_room_tag_parms_and_roomset_changes(lib) -> None:
    template = hb.template().text
    room = text(written(lib=lib))
    for name in ("Appls", "Tops", "RoomNotes"):
        assert one(room, name).of(room) == one(template, name).of(template)


# ----- what blocks a room (AGENTS.md rule 8)


def test_without_a_plan_the_room_is_not_written_and_the_chain_is_what_blocks_it(lib) -> None:
    out = wr.write_room(hb.model(plan=None), 1, hb.template(), lib, hb.SETTINGS)
    assert isinstance(out, wr.BlockedRoom) and out.file_name == "Room1.des"
    assert {what for _, what, _ in out.blocked_by} == {"position", "angle", "pos_mm", "joints"}
    assert all("plan" in why for _, _, why in out.blocked_by)


def test_a_room_whose_colour_isnt_known_is_not_written(lib) -> None:
    out = wr.write_room(hb.model(), 1, hb.template(), lib, wr.RoomSettings())
    assert isinstance(out, wr.BlockedRoom)
    assert [(what, why) for _, what, why in out.blocked_by] == [("materials", wr.NO_MATERIALS),
                                                                ("drawer front", wr.NO_DRAWER_FRONT)]


@pytest.mark.parametrize("materials, words", [
    (hb.MATERIALS.replace('"94"', '"88"'), "cabinet id 88"),
    (hb.MATERIALS.replace('RootTemplateId="12"', 'RootTemplateId="12" MatORId="3"'), "MatORId"),
    (hb.MATERIALS.split("\r\n    <BaseMatTempSel CategoryId=\"1\">")[0], "categories 3"),
    ("<Walls />", "not a run"),
    (hb.MATERIALS + "<Extra />", "not a run"),
])
def test_materials_that_arent_a_known_good_set_block_the_room(lib, materials: str, words: str) -> None:
    out = wr.write_room(hb.model(), 1, hb.template(), lib, wr.RoomSettings(materials, hb.FRONT))
    assert isinstance(out, wr.BlockedRoom)
    assert any(what == "materials" and words in why for _, what, why in out.blocked_by)


def test_a_product_jims_templates_dont_hold_blocks_the_room(tmp_path: Path) -> None:
    folder = hb.library_files(tmp_path / "templates")
    (folder / "WM_Panel_48_Thru.xml").unlink()
    from ngrunner.build.products import load_library

    lib = load_library(folder, hb.template().fm84)
    out = wr.write_room(hb.model(), 1, hb.template(), lib, hb.SETTINGS)
    assert isinstance(out, wr.BlockedRoom)
    assert out.blocked_by == (("wall 2 panel 2", "product", "Jim's templates have no WM_Panel_48_Thru.xml"),)


def test_a_could_not_verify_anywhere_in_the_model_blocks_the_whole_room(lib) -> None:
    model = hb.model()
    schedule = model.schedules[1]
    panel = dataclasses.replace(schedule.panels[1], x_mm=unverified("the reading isn't sure where it stands"))
    broken = dataclasses.replace(model, schedules=(model.schedules[0], dataclasses.replace(
        schedule, panels=(schedule.panels[0], panel, schedule.panels[2])), model.schedules[2]))
    out = wr.write_room(broken, 1, hb.template(), lib, hb.SETTINGS)
    assert isinstance(out, wr.BlockedRoom)
    assert out.blocked_by == (("wall 2 panel 2", "x_mm", "the reading isn't sure where it stands"),)


def test_room0_is_never_a_designs(lib) -> None:
    out = wr.write_room(hb.model(), 0, hb.template(), lib, hb.SETTINGS)
    assert isinstance(out, wr.BlockedRoom) and out.blocked_by[0][1] == "number"


def test_a_depth_kept_ic_exact_is_written_and_listed_for_review(lib) -> None:
    wall = dataclasses.replace(hb.PLAIN, lines=(hb.line(2, "VT 15x83.9x3/4 FD WH"),
                                                hb.line(1, "VT 15x47.4x3/4 FD FN WH"), *hb.PLAIN.lines[2:]))
    room = written(hb.model(hb.SIDES[0], wall, hb.SIDES[1]), lib=lib)
    depths = {attrs(t)["Depth"] for t in start_tags(text(room), "Product")}
    assert depths == {"381"}  # SCHEMA 166: off the grid, kept IC-exact
    assert len(room.review) == 3 and all("flagged Review" in r for r in room.review)


def test_the_written_room_holds_only_crlf_even_from_lf_templates(lib) -> None:
    content = written(lib=lib).content
    assert re.search(rb"(?<!\r)\n", content) is None
