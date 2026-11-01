"""
Pairing a crop with its .des wall (docs/p2/build-spec.md 5.7). A wall that can't be paired surely is
left out of the score and counted with its reason; nothing is guessed. Synthetic only.
"""
from __future__ import annotations

from tools.reader_eval import pairing, prep, truth
from tools.reader_eval import synthetic as s

VT60 = prep.PartLine(1, "VT 14x60x3/4 FD FN WH")
RAD60 = prep.PartLine(1, "VT RAD 14x60x3/4 RH LN WH")
SHELF = prep.PartLine(2, "SH 14x34.3x3/4 ADJ WH")


def _crop(wall: str = "2", width: float | None = 59.75, parts: tuple[prep.PartLine, ...] = (VT60, RAD60, SHELF),
          design: str = s.DESIGN, page: int = 4) -> prep.WallCrop:
    return prep.WallCrop(page, 0, prep.name_key_hash(design), wall, width, 108.0, b"png", (10, 10), (10, 10),
                         (0, 0, 0, 0), f"{page:064d}", parts)


def _rooms(**kwargs: object) -> dict[str, truth.RoomTruth]:
    return {"Room1": truth.read_room(s.room_des(**kwargs))}


def test_a_crop_pairs_with_the_wall_of_its_length_in_the_room_of_its_design() -> None:
    result = pairing.pair("T", [_crop()], _rooms())
    assert [(p.room, p.wall.position) for p in result.pairs] == [("Room1", 2)]
    assert result.left_out == ()


def test_the_wall_number_need_not_match_the_ic_number() -> None:
    result = pairing.pair("T", [_crop(wall="3")], _rooms())
    assert [p.wall.position for p in result.pairs] == [2]


def test_no_room_of_that_design_leaves_it_out() -> None:
    result = pairing.pair("T", [_crop(design="Somebody - Else")], _rooms())
    assert result.pairs == () and [x.code for x in result.left_out] == ["no_room"]


def test_two_rooms_of_that_design_leave_it_out() -> None:
    rooms = {"Room1": truth.read_room(s.room_des()), "Room2": truth.read_room(s.room_des())}
    result = pairing.pair("T", [_crop()], rooms)
    assert [x.code for x in result.left_out] == ["several_rooms"]


def test_a_wall_with_no_size_on_the_summary_is_left_out() -> None:
    assert [x.code for x in pairing.pair("T", [_crop(width=None)], _rooms()).left_out] == ["no_wall_size"]


def test_a_wall_whose_length_changed_is_left_out() -> None:
    assert [x.code for x in pairing.pair("T", [_crop(width=59.375)], _rooms()).left_out] == [
        "no_wall_of_that_length"]


def test_two_walls_of_that_length_with_different_panels_are_left_out() -> None:
    walls = ((1, 1517.65, 2743.2), (2, 1517.65, 2743.2))
    panels = (s.PanelSpec("FM 84", 0, 0, 2132, 1), s.PanelSpec("FM 84", 0, 0, 2132, 2),
              s.PanelSpec("FM 84", 900, 0, 2132, 2))
    crop = _crop(parts=(prep.PartLine(2, "VT 14x83.9x3/4 FD WH"),))
    result = pairing.pair("T", [crop], _rooms(walls=walls, panels=panels, others=()))
    assert [x.code for x in result.left_out] == ["several_walls_of_that_length"]


def test_two_walls_of_that_length_with_the_same_panels_pair_either_way() -> None:
    walls = ((1, 1517.65, 2743.2), (2, 1517.65, 2743.2))
    panels = (s.PanelSpec("FM 84", 0, 0, 2132, 1), s.PanelSpec("FM 84", 0, 0, 2132, 2))
    crop = _crop(parts=(prep.PartLine(1, "VT 14x83.9x3/4 FD WH"),))
    result = pairing.pair("T", [crop], _rooms(walls=walls, panels=panels, others=()))
    assert len(result.pairs) == 1 and result.left_out == ()


def test_a_vertical_count_that_differs_from_the_parts_list_is_left_out() -> None:
    crop = _crop(parts=(VT60, RAD60, prep.PartLine(1, "VT 14x60x3/4 FD FN WH")))
    assert [x.code for x in pairing.pair("T", [crop], _rooms()).left_out] == ["vertical_count_differs"]


def test_an_unreadable_vertical_quantity_is_left_out() -> None:
    crop = _crop(parts=(prep.PartLine(None, "VT 14x60x3/4 FD FN WH"), RAD60))
    assert [x.code for x in pairing.pair("T", [crop], _rooms()).left_out] == ["count_unreadable"]


def test_a_radius_count_that_differs_is_left_out() -> None:
    crop = _crop(parts=(VT60, prep.PartLine(1, "VT 14x60x3/4 FD FN WH")))
    assert [x.code for x in pairing.pair("T", [crop], _rooms()).left_out] == ["radius_count_differs"]


def test_heights_that_differ_from_the_parts_list_are_left_out() -> None:
    crop = _crop(parts=(prep.PartLine(1, "VT 14x47.4x3/4 FD FN WH"), prep.PartLine(1, "VT RAD 14x60x3/4 RH WH")))
    assert [x.code for x in pairing.pair("T", [crop], _rooms()).left_out] == ["heights_differ"]


def test_an_ic_height_pairs_with_its_snapped_mozaik_height() -> None:
    panels = (s.PanelSpec("FM 84", 0, 0, 2132, 2), s.PanelSpec("WM Panel 48 Thru", 800, 928, 1204, 2))
    crop = _crop(parts=(prep.PartLine(1, "VT 14x83.9x3/4 FD WH"), prep.PartLine(1, "VT 14x47.4x3/4 FD FN WH")))
    assert len(pairing.pair("T", [crop], _rooms(panels=panels, others=())).pairs) == 1


def test_a_wall_with_no_parts_list_is_left_out() -> None:
    assert [x.code for x in pairing.pair("T", [_crop(parts=())], _rooms()).left_out] == ["no_parts_list"]


def test_a_wall_with_a_rotated_vertical_is_left_out() -> None:
    panels = (s.PanelSpec("WM Panel 48 Thru", 0, 914.4, 1524, 2),
              s.PanelSpec("WM Panel 48 Right Radius", 888.95, 914.4, 1524, 2, rot=90))
    assert [x.code for x in pairing.pair("T", [_crop()], _rooms(panels=panels)).left_out] == ["rotated_vertical"]


def test_two_crops_on_one_wall_are_both_left_out() -> None:
    result = pairing.pair("T", [_crop(page=4), _crop(page=6)], _rooms())
    assert result.pairs == () and [x.code for x in result.left_out] == ["wall_paired_twice"] * 2


def test_a_wall_with_no_verticals_pairs_and_its_truth_is_empty() -> None:
    crop = _crop(wall="1", width=42.375, parts=(prep.PartLine(1, "DW Hook 4.9x12.2x3/4 FL WH"),))
    result = pairing.pair("T", [crop], _rooms())
    assert [p.wall.panels for p in result.pairs] == [()]


def test_walls_with_verticals_that_nothing_paired_are_counted() -> None:
    result = pairing.pair("T", [_crop(wall="1", width=42.375, parts=(prep.PartLine(1, "DW Hook"),))], _rooms())
    assert [(w.room, w.position) for w in result.unpaired_walls] == [("Room1", 2)]


def test_a_left_out_wall_names_no_client() -> None:
    result = pairing.pair("T", [_crop(design="Somebody - Else")], _rooms())
    text = repr(result)
    assert "Somebody" not in text and s.CLIENT not in text


# ----- two walls of one length: the whole WALL/AREA table decides, when exactly one mapping fits

_U = ((1, 1076.325, 2743.2), (2, 1517.65, 2743.2), (3, 2025.65, 2743.2), (4, 1517.65, 2743.2), (5, 76.2, 2743.2))
_U_TABLE = (("1", 42.375), ("2", 59.75), ("3", 79.75), ("4", 59.75), ("5", 3.0))
_U_PANELS = (s.PanelSpec("FM 84", 0, 0, 2132, 2), s.PanelSpec("FM 84", 0, 0, 2132, 4),
             s.PanelSpec("FM 84", 700, 0, 2132, 4))


def _u_crop(wall: str, table: tuple[tuple[str, float], ...], vts: int) -> prep.WallCrop:
    crop = _crop(wall=wall, parts=(prep.PartLine(vts, "VT 14x83.9x3/4 FD WH"),))
    return prep.WallCrop(*[getattr(crop, f) for f in ("page", "design", "design_key", "wall", "width_in",
                                                      "height_in", "png", "pixels", "native", "trim",
                                                      "sha256", "parts")], table=table)


def test_one_table_mapping_settles_two_walls_of_one_length() -> None:
    rooms = _rooms(walls=_U, panels=_U_PANELS, others=())
    assert [p.wall.position for p in pairing.pair("T", [_u_crop("2", _U_TABLE, 1)], rooms).pairs] == [2]
    assert [p.wall.position for p in pairing.pair("T", [_u_crop("4", _U_TABLE, 2)], rooms).pairs] == [4]


def test_a_table_numbered_from_another_start_maps_by_its_offset() -> None:
    rooms = _rooms(walls=_U, panels=_U_PANELS, others=())
    shifted = tuple((str(int(n) + 1), w) for n, w in _U_TABLE)
    assert [p.wall.position for p in pairing.pair("T", [_u_crop("5", shifted, 2)], rooms).pairs] == [4]


def test_a_mirror_image_room_stays_left_out() -> None:
    walls = ((1, 482.6, 2743.2), (2, 1517.65, 2743.2), (3, 2025.65, 2743.2), (4, 1517.65, 2743.2), (5, 482.6, 2743.2))
    table = (("1", 19.0), ("2", 59.75), ("3", 79.75), ("4", 59.75), ("5", 19.0))
    result = pairing.pair("T", [_u_crop("2", table, 1)], _rooms(walls=walls, panels=_U_PANELS, others=()))
    assert [x.code for x in result.left_out] == ["several_walls_of_that_length"]


def test_a_table_that_fits_no_mapping_stays_left_out() -> None:
    table = (("1", 42.375), ("2", 59.75), ("3", 80.5), ("4", 59.75), ("5", 3.0))
    result = pairing.pair("T", [_u_crop("2", table, 1)], _rooms(walls=_U, panels=_U_PANELS, others=()))
    assert [x.code for x in result.left_out] == ["several_walls_of_that_length"]


# ----- rooms Mozaik names differently from the design (the P5 jobs, 2026-10-31)


def _named(design: str, others: tuple[str, ...] = ()) -> prep.WallCrop:
    crop = _crop(design=design)
    keys = tuple(prep.name_key(d) for d in (design, *others))
    return prep.WallCrop(*[getattr(crop, f) for f in ("page", "design", "design_key", "wall", "width_in",
                                                      "height_in", "png", "pixels", "native", "trim",
                                                      "sha256", "parts")],
                         design_name_key=prep.name_key(design), summary_designs=keys)


def _room(name: str) -> dict[str, truth.RoomTruth]:
    return {"Room1": truth.read_room(s.room_des(name=name))}


def test_an_exactly_named_room_pairs_by_name() -> None:
    result = pairing.pair("T", [_named(s.DESIGN)], _room(s.DESIGN))
    assert [p.room_rule for p in result.pairs] == ["name"]


def test_a_room_named_by_part_of_one_design_pairs_by_that_part() -> None:
    crop = _named("Testerson - Primary Closet warm sand", ("Testerson - Daughter Closet",))
    result = pairing.pair("T", [crop], _room("Primary"))
    assert [p.room_rule for p in result.pairs] == ["part of the name"]


def test_a_room_name_that_is_part_of_two_designs_is_left_out() -> None:
    crop = _named("Testerson - Primary Closet", ("Testerson - Primary Closet - option 2",))
    result = pairing.pair("T", [crop], _room("Primary Closet"))
    assert [x.code for x in result.left_out] == ["no_room"]


def test_the_only_design_and_the_only_room_pair_by_elimination() -> None:
    result = pairing.pair("T", [_named("Testerson - Shelves for Kitchen")], _room("Room 1"))
    assert [p.room_rule for p in result.pairs] == ["only design, only room"]


def test_two_designs_and_generically_named_rooms_are_left_out() -> None:
    crop = _named("Testerson - Shelves for Kitchen", ("Testerson - Pantry",))
    assert [x.code for x in pairing.pair("T", [crop], _room("Room 1")).left_out] == ["no_room"]


def test_room_names_are_read_with_their_xml_escapes_undone() -> None:
    room = truth.read_room(s.room_des(name="Testerson - Baby Closet - 26&quot;deep"))
    assert room.key == prep.name_key_hash('Testerson - Baby Closet - 26"deep')
    assert room.name_key == prep.name_key('Testerson - Baby Closet - 26"deep')
    assert "Testerson" not in repr(room)
