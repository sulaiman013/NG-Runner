"""
The nest-to-design match (guards/design.py), on made-up rooms, nests and cutlists. Real cases are in
tests/test_real_designs.py, marked fixtures.

What it must never do: pass a nest that isn't the saved design. Only the code "matches" is part of a pass,
anything it can't read is could_not_verify, and every difference it can see in size, count, place,
operations or material, or a room the nest wasn't made from, is "Not verified", naming the parts by tag.
"""
from __future__ import annotations

import hashlib

import pytest

from helpers_design import (
    GROOVE, cutlist_text, nest, nest_part, op, part, printed, product, room,
)
from ngrunner.guards import design
from ngrunner.guards.bak import DesignUnreadable
from ngrunner.guards.design import (
    COUNTER_TOP, CutlistUnreadable, match, read_cutlist, read_design, read_nest, room_identity,
)
from ngrunner.guards.facts import (
    COULD_NOT_VERIFY, DESIGN_MATCHES, NOT_VERIFIED, CheckedFiles,
)
from ngrunner.guards.opt import OptUnreadable
from ngrunner.messages.lint import check_line

CHECKED = CheckedFiles("a" * 64, (("TESS01.TAP", "b" * 64),), (("Room1.des", "c" * 64),))
MATERIAL = "3-4 Made-up White"
SHELF_OPS = (op(), op(X="860.425"))
PANEL_OPS = (op("OperationGroove"), op("OperationGroove", ID="3", Hide="False", FlipSideOp="False"))


# ----- one small job, as Mozaik keeps it: two rooms, one nest


def rooms(*room1_products: str, room0: bytes | None = None) -> dict[str, bytes]:
    room1 = room(*(room1_products or (
        product(3, part("Top", 356, 869.95, ops=SHELF_OPS), part("Fixed Shelf", 356, 869.95, ops=SHELF_OPS)),
        product(2, part("WM Panel", 356, 1524, ops=PANEL_OPS, kind="FEnd")))))
    return {"Room0.des": room0 if room0 is not None else room(product(0, part("Base", 101.6, 2438.4, mat="118",
                                                                         tex="3505"))),
            "Room1.des": room1}


def nest_parts(**over) -> list[str]:
    parts = {
        "base": nest_part("R0N1", "Base", 101.6, 2438.4),
        "top": nest_part("R1C3", "Top", 356, 869.95, ops=SHELF_OPS),
        "shelf": nest_part("R1C3", "Fixed Shelf", 356, 869.95, ops=SHELF_OPS),
        "panel": nest_part("R1C2", "WM Panel", 356, 1524, ops=PANEL_OPS),
    }
    parts.update(over)
    return [p for p in parts.values() if p]


CUT = [(1, "Made-up White", "Base", 101.6, 2438.4), (1, "Made-up White", "Fixed Shelf", 356.0, 870.0),
       (1, "Made-up White", "Top", 356.0, 870.0), (1, "Made-up White", "WM Panel", 356.0, 1524.0)]


def judge(nest_bytes: bytes | None = None, *, cut=None, design_rooms=None, others=(),
          saved=None) -> design.DesignMatch:
    design_rooms = design_rooms or rooms()
    nests = [read_nest(MATERIAL, nest_bytes if nest_bytes is not None else nest(*nest_parts()))]
    nests += [read_nest(name, data) for name, data in others]
    cutlist = read_cutlist(printed(cutlist_text(CUT if cut is None else cut)))
    numbers = saved if saved is not None else design.room_numbers(design_rooms)
    return match(MATERIAL, nests, cutlist, read_design(design_rooms), numbers, checked=CHECKED)


# ----- des_cutlist's output, as it prints it


def test_the_cutlist_is_read_line_by_line_with_its_total() -> None:
    text = cutlist_text([(1, "Made-up White", "Top", 356.0, 870.0), (4, "Made-up White", "WM Panel", 356, 1524),
                         (2, "_Lux Top Made-up 3/4&quot;", COUNTER_TOP, 431.8, 939.8)])

    cut = read_cutlist(printed(text))

    assert cut.total == 7
    assert cut.rows[1] == design.CutRow(4, "Made-up White", "WM Panel", 356.0, 1524.0)
    assert cut.rows[2].colour == "_Lux Top Made-up 3/4&quot;"          # as printed: it unescapes nothing


@pytest.mark.parametrize("text, exit_code, stderr, timed_out", [
    pytest.param("1 | White | Top | 356.0 | 870.0\nTOTAL 1\n", 1, "", False, id="exit 1"),
    pytest.param("1 | White | Top | 356.0 | 870.0\nTOTAL 1\n", None, "", False, id="never finished"),
    pytest.param("1 | White | Top | 356.0 | 870.0\nTOTAL 1\n", 0, "", True, id="timed out"),
    pytest.param("1 | White | Top | 356.0 | 870.0\nTOTAL 1\n", 0, "Traceback ...", False, id="said something"),
    pytest.param("1 | White | Top | 356.0 | 870.0\n", 0, "", False, id="no total"),
    pytest.param("1 | White | Top | 356.0 | 870.0\nTOTAL 2\n", 0, "", False, id="a total that doesn't add up"),
    pytest.param("1 | White | Top | 356.0\nTOTAL 1\n", 0, "", False, id="four fields"),
    pytest.param("1 | White | Top | Bar | 356.0 | 870.0\nTOTAL 1\n", 0, "", False, id="six fields"),
    pytest.param("1 | White | Top | 356 | 870.0\nTOTAL 1\n", 0, "", False, id="a size not as rounded"),
    pytest.param("1 | White | Top | nan | 870.0\nTOTAL 1\n", 0, "", False, id="nan"),
    pytest.param("1 | White | Top | 1e-05 | 870.0\nTOTAL 1\n", 0, "", False, id="a float written short"),
    pytest.param("0 | White | Top | 356.0 | 870.0\nTOTAL 0\n", 0, "", False, id="none of a part"),
    pytest.param("x | White | Top | 356.0 | 870.0\nTOTAL 1\n", 0, "", False, id="a count not a number"),
    pytest.param("1 | White |  | 356.0 | 870.0\nTOTAL 1\n", 0, "", False, id="no name"),
    pytest.param("TOTAL 0\n1 | White | Top | 356.0 | 870.0\n", 0, "", False, id="a line after the total"),
    pytest.param("1 | White | Top | 356.0 | 870.0\nTOTAL 1\nTOTAL 1\n", 0, "", False, id="two totals"),
    pytest.param("", 0, "", False, id="nothing"),
    pytest.param("1 | White | Top | 356.0 | 870.0\n\nTOTAL 1\n", 0, "", False, id="a blank line"),
])
def test_anything_the_tool_does_not_print_fails_closed(text, exit_code, stderr, timed_out) -> None:
    with pytest.raises(CutlistUnreadable):
        read_cutlist(printed(text, exit_code=exit_code, stderr=stderr, timed_out=timed_out))


def test_an_empty_design_reads_as_a_cutlist_of_nothing() -> None:
    assert read_cutlist(printed("TOTAL 0\n")).rows == ()


# ----- the stand-in: the saved design read by bytes, for what des_cutlist doesn't print


def test_the_stand_in_reads_each_piece_with_its_tag_place_and_operations() -> None:
    data = room(product(3, part("Top", 356, 869.95, ops=SHELF_OPS, quan=2),
                        part("Hinge", 10, 10, kind="Metal"), part("Drawer Box", 100, 400, usage=2),
                        part("Gone", 356, 600, quan=0), part("F.Shelf", 356, 600, report=" Fixed Shelf "),
                        count=2),
                quan=2)

    pieces = read_design({"Room4.des": data})

    assert len(pieces) == 2 * 2 * 2 + 2 * 2                           # room x count x quan
    top = pieces[0]
    assert (top.tag, top.room, top.cab, top.name, top.w_mm, top.l_mm) == ("R4C3", 4, "3", "Top", 356.0, 869.95)
    assert [o.kind for o in top.ops] == ["OperationHole", "OperationHole"]
    assert not any(name.endswith("_Eq") for o in top.ops for name, _ in o.values)
    assert {p.name for p in pieces} == {"Top", "Fixed Shelf"}           # ReportName first, stripped


def test_room_0_pieces_have_no_cabinet_since_a_nest_counts_them_instead() -> None:
    # Measured: a nest's Room 0 tags are R0N1, R0N2... by count, and one real Room 0 product has CabNo 0.
    piece, = read_design({"Room0.des": room(product(0, part("Base", 101.6, 2438.4, mat="118", tex="3505")))})

    assert (piece.tag, piece.room, piece.cab) == ("Room 0", 0, None)
    assert (piece.material, piece.texture) == ("118", "3505")


@pytest.mark.parametrize("data", [
    pytest.param(b"<Room/>", id="no version line"),
    pytest.param(b"14\r\n<Room Quan=\"1\"><Products/></Room>", id="a version not seen"),
    pytest.param(b"15\r\n<Room Quan=\"1\"><Products>", id="cut off"),
    pytest.param(b"15\r\n<Job><Products/></Job>", id="not a room"),
    pytest.param(room(product(1, part("Top", "wide", 870))), id="a size not a number"),
    pytest.param(room(product(1, part("Top", 356, 870)), quan="two"), id="a room count not a number"),
])
def test_the_stand_in_refuses_a_room_not_as_mozaik_writes_one(data) -> None:
    with pytest.raises(DesignUnreadable):
        read_design({"Room1.des": data})


def test_the_stand_in_refuses_a_file_not_named_as_a_room() -> None:
    with pytest.raises(DesignUnreadable):
        read_design({"Kitchen.des": room()})


# ----- a room's identity: what a pass rests on, the same through a re-save of the same design


def identity(data: bytes, name: str = "Room1.des") -> str:
    return room_identity(name, data)


ROOM_1 = room(product(3, part("Top", 356, 869.95, ops=SHELF_OPS)), product(5, part("Top", 356, 600)))


@pytest.mark.parametrize("resaved", [
    pytest.param(room(product(3, part("Top", 356, 869.95, ops=SHELF_OPS)), product(5, part("Top", 356, 600)),
                      annotation='<AnnotationSet X="1" />'), id="annotations"),
    pytest.param(room(product(3, part("Top", 356, 869.95, ops=SHELF_OPS)), product(5, part("Top", 356, 600)),
                      dirty="True", tag_count=9), id="the room's numbering flags"),
    pytest.param(room(product(3, part("Top", "356.0", 869.95, ops=SHELF_OPS)), product(5, part("Top", 356, 600))),
                 id="a number written another way"),
])
def test_a_room_saved_again_with_the_same_design_keeps_its_identity(resaved) -> None:
    assert identity(resaved) == identity(ROOM_1)
    assert hashlib.sha256(resaved).hexdigest() != hashlib.sha256(ROOM_1).hexdigest()


def test_room_0_renumbered_on_save_keeps_its_identity() -> None:
    # Measured on 8 of 10 real jobs: Mozaik renumbers Room 0's products on save.
    before = room(product(1, part("Base", 101.6, 2438.4), uid=1), product(2, part("Base", 76.2, 2036), uid=2))
    after = room(product(2, part("Base", 101.6, 2438.4), uid=1), product(3, part("Base", 76.2, 2036), uid=2))

    assert identity(after, "Room0.des") == identity(before, "Room0.des")
    assert identity(after, "Room1.des") != identity(before, "Room1.des")


@pytest.mark.parametrize("changed", [
    pytest.param(room(product(3, part("Top", 356, 870.95, ops=SHELF_OPS)), product(5, part("Top", 356, 600))),
                 id="a size"),
    pytest.param(room(product(3, part("Top", 356, 869.95, ops=(op(), op(X="860.425", Hide="True")))),
                      product(5, part("Top", 356, 600))), id="an operation"),
    pytest.param(room(product(4, part("Top", 356, 869.95, ops=SHELF_OPS)), product(5, part("Top", 356, 600))),
                 id="a cabinet's number"),
    pytest.param(room(product(3, part("Top", 356, 869.95, ops=SHELF_OPS)), product(5, part("Top", 356, 600)),
                      quan=2), id="the room's count"),
    pytest.param(room(product(3, part("Top", 356, 869.95, ops=SHELF_OPS)), product(5, part("Top", 356, 600,
                                                                                         tex="3784"))),
                 id="a texture set by hand"),
    pytest.param(room(product(3, part("Top", 356, 869.95, ops=SHELF_OPS))), id="a product removed"),
])
def test_a_change_to_the_design_changes_its_identity(changed) -> None:
    assert identity(changed) != identity(ROOM_1)


def test_a_room_that_does_not_read_has_no_identity() -> None:
    with pytest.raises(DesignUnreadable):
        identity(b"<Room/>")


# ----- the nest's parts


def test_a_nest_gives_its_own_parts_and_not_a_flip_pass_copy() -> None:
    data = nest(*nest_parts(), sheets=2, flip_copy=nest_part("R1C3", "Top", 356, 869.95, ops=SHELF_OPS))

    read = read_nest(MATERIAL, data)

    assert read.physical_sheets == 2 and read.rooms == (0, 1) and read.material_id == "118"
    assert sorted(p.tag for p in read.pieces) == ["R0N1", "R1C2", "R1C3", "R1C3"]
    top = next(p for p in read.pieces if p.name == "Top")                 # the leading space is gone
    assert (top.room, top.cab, top.texture) == (1, "3", "3505")
    base = next(p for p in read.pieces if p.name == "Base")
    assert (base.room, base.cab) == (0, None)


def test_a_part_in_a_nest_counts_as_many_pieces_as_its_quan() -> None:
    read = read_nest(MATERIAL, nest(nest_part("R0N3", "Base", 101.6, 2438.4, quan=6)))

    assert len(read.pieces) == 6


@pytest.mark.parametrize("data", [
    pytest.param(nest(nest_part("Shelf 3", "Top")), id="a tag not as Mozaik writes one"),
    pytest.param(nest(*nest_parts(), rooms=()), id="no rooms named"),
    pytest.param(nest(nest_part("R1C3", "Top", "wide", 870)), id="a size not a number"),
    pytest.param(nest(nest_part("R1C3", "Top", 356, 870, quan=0)), id="none of a part"),
    pytest.param(nest(), id="no parts"),
    pytest.param(b"8\r\n<OptimizeMaterial>", id="cut off"),
])
def test_a_nest_not_as_measured_is_refused(data) -> None:
    with pytest.raises(OptUnreadable):
        read_nest(MATERIAL, data)


# ----- the match: only "matches" passes


def test_a_nest_that_is_the_saved_design_matches() -> None:
    found = judge()

    assert found.code == DESIGN_MATCHES
    assert found.checked == CHECKED and found.parts == () and found.note == ""


def test_a_size_within_what_des_cutlist_rounds_away_matches() -> None:
    # 869.95 prints as 870.0; a nest at 869.99 is the same part to des_cutlist's 0.1 mm.
    over = nest_parts(top=nest_part("R1C3", "Top", 356, 869.99, ops=SHELF_OPS))

    assert judge(nest(*over)).code == DESIGN_MATCHES


@pytest.mark.parametrize("what, parts, tags", [
    ("a size", dict(top=nest_part("R1C3", "Top", 356, 870.7, ops=SHELF_OPS)), ("R1C3",)),
    ("a part more", dict(extra=nest_part("R1C3", "Top", 356, 869.95, ops=SHELF_OPS)), ("R1C3",)),
    ("a part less", dict(shelf=""), ("R1C3",)),
    ("a part's name", dict(shelf=nest_part("R1C3", "Adjustable Shelf", 356, 869.95, ops=SHELF_OPS)),
     ("R1C3",)),
    ("an operation gone", dict(top=nest_part("R1C3", "Top", 356, 869.95, ops=SHELF_OPS[:1])), ("R1C3",)),
    ("a groove's Hide swapped", dict(panel=nest_part("R1C2", "WM Panel", 356, 1524, ops=(
        op("OperationGroove", Hide="False"), op("OperationGroove", ID="3", Hide="True", FlipSideOp="False")))),
     ("R1C2",)),
    ("a hole moved 0.7 mm", dict(top=nest_part("R1C3", "Top", 356, 869.95, ops=(op(X="10.225"), op(X="860.425")))),
     ("R1C3",)),
    ("a part at another cabinet", dict(top=nest_part("R1C5", "Top", 356, 869.95, ops=SHELF_OPS)),
     ("R1C3", "R1C5")),
    ("a material set by hand", dict(base=nest_part("R0N1", "Base", 101.6, 2438.4, texture="3784")),
     ("R0N1",)),
])
def test_each_difference_is_not_verified_naming_the_parts_by_tag(what, parts, tags) -> None:
    found = judge(nest(*nest_parts(**parts)))

    assert found.code == NOT_VERIFIED, what
    assert found.parts == tags, what
    assert found.checked == CHECKED
    assert found.note.startswith("Not verified: the nest is not the saved design")
    assert all(tag in found.note for tag in tags)


def test_a_material_set_by_hand_must_be_the_nests_material() -> None:
    other = nest(*nest_parts(), material_id="101")

    found = judge(other)

    assert found.code == NOT_VERIFIED and found.parts == ("R0N1",)


def test_what_the_design_says_nothing_about_is_not_compared() -> None:
    # No material is set by hand on the Top, so its texture in the nest is the material's own; and a
    # nest may hold operations the design doesn't, as Mozaik adds joint fastener holes at optimize.
    over = nest_parts(top=nest_part("R1C3", "Top", 356, 869.95, ops=(*SHELF_OPS, op(X="500")), texture="1506"))

    assert judge(nest(*over)).code == DESIGN_MATCHES


def test_an_operation_within_005_mm_is_the_same_operation() -> None:
    over = nest_parts(panel=nest_part("R1C2", "WM Panel", 356, 1524, ops=(
        op("OperationGroove", Ang="49.99962"), PANEL_OPS[1])))

    assert judge(nest(*over)).code == DESIGN_MATCHES


def test_a_room_the_nest_was_not_made_from_is_not_verified() -> None:
    saved = rooms()
    saved["Room4.des"] = room(product(1, part("Top", 356, 600)))
    cut = [*CUT, (1, "Made-up White", "Top", 356.0, 600.0)]

    found = judge(cut=cut, design_rooms=saved)

    assert found.code == NOT_VERIFIED
    assert "Room 4 is in the saved design and not in the nest" in found.note
    assert "R4C1" in found.parts


def test_a_room_in_the_nest_and_not_saved_is_not_verified() -> None:
    found = judge(nest(*nest_parts(), rooms=(0, 1, 2)))

    assert found.code == NOT_VERIFIED and "Room 2 is in the nest and not in the saved design" in found.note


def test_a_part_split_between_two_materials_matches_both() -> None:
    saved = rooms(product(3, part("Top", 356, 869.95, ops=SHELF_OPS), part("Fixed Shelf", 356, 869.95,
                                                                           ops=SHELF_OPS)),
                  product(2, part("WM Panel", 356, 1524, ops=PANEL_OPS, kind="FEnd")),
                  product(7, part("Top", 356, 869.95, ops=SHELF_OPS)))
    cut = [*CUT[:2], (2, "Made-up White", "Top", 356.0, 870.0), CUT[3]]
    sibling = nest(nest_part("R1C7", "Top", 356, 869.95, ops=SHELF_OPS), material_id="101")

    found = judge(cut=cut, design_rooms=saved, others=[("3-4 Other Colour", sibling)])

    assert found.code == DESIGN_MATCHES


def test_a_part_of_the_design_in_no_nest_is_not_verified_for_every_material() -> None:
    saved = rooms(product(3, part("Top", 356, 869.95, ops=SHELF_OPS), part("Fixed Shelf", 356, 869.95,
                                                                           ops=SHELF_OPS)),
                  product(2, part("WM Panel", 356, 1524, ops=PANEL_OPS, kind="FEnd")),
                  product(9, part("Filler", 152.4, 2743.2)))
    cut = [*CUT, (1, "Made-up White", "Filler", 152.4, 2743.2)]

    found = judge(cut=cut, design_rooms=saved)

    assert found.code == NOT_VERIFIED and found.parts == ("R1C9",)


def test_a_nest_with_no_sheets_takes_only_what_nested_ones_left() -> None:
    # Case I's form: a material whose parts were all moved to another, left with parts and no sheets.
    stale = nest(*nest_parts(), sheets=0, material_id="101")

    assert judge(others=[("3-4 Other Colour", stale)]).code == DESIGN_MATCHES


def test_counter_tops_are_matched_on_size_from_the_cutlist_alone() -> None:
    # des_cutlist reads them from <Tops>, which hold no parts and no operations in the design.
    cut = [*CUT, (1, "Made-up Marble", COUNTER_TOP, 503.2, 1366.5)]
    top = nest_part("R1N0", COUNTER_TOP, 503.2, 1366.5)

    assert judge(nest(*nest_parts(), top), cut=cut).code == DESIGN_MATCHES
    wrong = judge(nest(*nest_parts(), nest_part("R1N0", COUNTER_TOP, 503.2, 2721)), cut=cut)
    assert wrong.code == NOT_VERIFIED and wrong.parts == ("R1N0",)


def test_when_the_two_readings_of_the_design_disagree_nothing_is_concluded() -> None:
    cut = [*CUT[:3], (2, "Made-up White", "WM Panel", 356.0, 1524.0)]

    found = judge(cut=cut)

    assert found.code == COULD_NOT_VERIFY and found.checked == CHECKED


def test_a_nest_with_no_sheets_is_never_judged_a_match() -> None:
    found = judge(nest(*nest_parts(), sheets=0))

    assert found.code == COULD_NOT_VERIFY


def test_the_judged_nest_must_be_among_those_read() -> None:
    cutlist = read_cutlist(printed(cutlist_text(CUT)))
    found = match("3-4 Not Read", [read_nest(MATERIAL, nest(*nest_parts()))], cutlist, read_design(rooms()),
                  (0, 1), checked=CHECKED)

    assert found.code == COULD_NOT_VERIFY


def test_a_long_list_of_parts_is_cut_short_in_words_and_kept_whole_in_parts() -> None:
    many = [product(n, part("Top", 356, 600 + n)) for n in range(1, 13)]
    saved = rooms(*many)
    cut = [(1, "Made-up White", "Top", 356.0, float(600 + n)) for n in range(1, 13)]
    cut.append(CUT[0])
    shifted = [nest_part("R0N1", "Base", 101.6, 2438.4)] + [nest_part(f"R1C{n}", "Top", 356, 700 + n)
                                                           for n in range(1, 13)]

    found = judge(nest(*shifted), cut=cut, design_rooms=saved)

    assert found.code == NOT_VERIFIED and len(found.parts) == 12
    assert "and 4 more" in found.note


@pytest.mark.parametrize("over", [
    {}, dict(shelf=""), dict(top=nest_part("R1C5", "Top", 356, 869.95, ops=SHELF_OPS)),
])
def test_every_note_passes_the_lint(over) -> None:
    found = judge(nest(*nest_parts(**over)))
    saved = rooms()
    saved["Room4.des"] = room(product(1, part("Top", 356, 600)))
    rooms_off = judge(cut=[*CUT, (1, "Made-up White", "Top", 356.0, 600.0)], design_rooms=saved)

    for note in (found.note, rooms_off.note, design.COULD_NOT_COMPARE):
        assert note == "" or check_line(note) == [], note


def test_match_only_ever_gives_one_of_its_three_codes() -> None:
    codes = {judge(nest(*nest_parts(**over))).code for over in (
        {}, dict(shelf=""), dict(top=nest_part("R1C3", "Top", 356, 870.7)))}

    assert codes <= {DESIGN_MATCHES, NOT_VERIFIED, COULD_NOT_VERIFY} and DESIGN_MATCHES in codes


# ----- running it: a made-up des_cutlist from a made-up toolkit copy, through the real run_tool

FAKE_CUTLIST = """
import os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "des_cutlist.txt"), encoding="utf-8") as said:
    sys.stdout.write(said.read())
with open(os.path.join(here, "des_cutlist.exit"), encoding="utf-8") as code:
    sys.exit(int(code.read()))
"""


class Job:
    """One made-up job: its folder in Mozaik Shared with rooms and a nest, a toolkit copy whose
    des_cutlist prints what it is told, and what the guards would hand the check."""

    def __init__(self, tmp_path, *, said: str = cutlist_text(CUT), exit_code: int = 0,
                 design: dict[str, bytes] | None = None) -> None:
        from ngrunner.clock import FakeClock
        from ngrunner.toolkit import take_snapshot
        from datetime import datetime, timezone

        source = tmp_path / "_NG_Automation" / "scripts"
        source.mkdir(parents=True)
        (source / "des_cutlist.py").write_text(FAKE_CUTLIST, encoding="utf-8")
        (source / "des_cutlist.txt").write_text(said, encoding="utf-8")
        (source / "des_cutlist.exit").write_text(str(exit_code), encoding="utf-8")
        self.snapshot = take_snapshot(source.parent, tmp_path / "data" / "toolkit" / "copy",
                                      clock=FakeClock(datetime(2026, 10, 31, tzinfo=timezone.utc)))
        self.folder = tmp_path / "Mozaik Shared" / "Jobs" / "Testerson_101426"
        self.folder.mkdir(parents=True)
        for name, data in (design or rooms()).items():
            (self.folder / name).write_bytes(data)
        self.opt = self.folder / f"{MATERIAL}.opt"
        self.opt.write_bytes(nest(*nest_parts()))
        self.work = tmp_path / "data" / "staging"

    def to_check(self, *, rooms_read=None):
        from ngrunner.guards.nestfiles import ToCheck

        read = rooms_read or tuple((p.name, room_identity(p.name, p.read_bytes()))
                                   for p in sorted(self.folder.glob("Room*.des")))
        files = CheckedFiles(hashlib.sha256(self.opt.read_bytes()).hexdigest(), (("TESS01.TAP", "b" * 64),), read)
        return ToCheck("Testerson_101426", MATERIAL, self.opt, self.folder, self.folder.parent, files)

    def run(self, **kw):
        return design.design_checks_from(self.snapshot, self.work)(self.to_check(**kw))


def test_the_match_runs_des_cutlist_from_the_copy_on_staged_rooms_and_matches(tmp_path) -> None:
    job = Job(tmp_path)

    found = job.run()

    assert found.code == DESIGN_MATCHES and found.checked == job.to_check().files
    assert list((tmp_path / "data" / "staging").iterdir()) == []          # its own folder, gone


def test_des_cutlist_is_handed_a_folder_of_staged_rooms_and_nothing_else(tmp_path, monkeypatch) -> None:
    job = Job(tmp_path)
    seen: list[list[str]] = []
    real = design.run_tool

    def spy(snapshot, script, args, **kw):
        from pathlib import Path
        seen.append([script, *args, *sorted(p.name for p in Path(args[0]).iterdir())])
        return real(snapshot, script, args, **kw)

    monkeypatch.setattr(design, "run_tool", spy)
    job.run()

    script, folder, *names = seen[0]
    assert script == "scripts/des_cutlist.py" and names == ["Room0.des", "Room1.des"]
    assert str(job.folder) not in folder and str(tmp_path / "data" / "staging") in folder


@pytest.mark.parametrize("make, rooms_read", [
    pytest.param(dict(exit_code=1), None, id="the tool fails"),
    pytest.param(dict(said="1 | White | Top | 356.0\nTOTAL 1\n"), None, id="a line the tool doesn't print"),
    pytest.param({}, (("Room0.des", "0" * 64), ("Room1.des", "1" * 64)), id="rooms not as they were read"),
    pytest.param({}, (("Room1.des", "1" * 64),), id="fewer rooms than are there"),
])
def test_anything_short_of_a_clean_comparison_is_could_not_verify(tmp_path, make, rooms_read) -> None:
    job = Job(tmp_path, **make)

    found = job.run(rooms_read=rooms_read)

    assert found.code == COULD_NOT_VERIFY and found.note == design.COULD_NOT_COMPARE


def test_a_nest_of_the_job_that_wont_read_is_could_not_verify(tmp_path) -> None:
    job = Job(tmp_path)
    (job.folder / "1-4 Made-up White.opt").write_bytes(b"8\r\n<OptimizeMaterial>")

    assert job.run().code == COULD_NOT_VERIFY


def test_a_nest_changed_since_it_was_read_is_could_not_verify(tmp_path) -> None:
    job = Job(tmp_path)
    files = job.to_check()
    job.opt.write_bytes(nest(*nest_parts(shelf="")))

    assert design.design_checks_from(job.snapshot, job.work)(files).code == COULD_NOT_VERIFY


SPLIT = rooms(product(3, part("Top", 356, 869.95, ops=SHELF_OPS), part("Fixed Shelf", 356, 869.95, ops=SHELF_OPS)),
              product(2, part("WM Panel", 356, 1524, ops=PANEL_OPS, kind="FEnd")),
              product(7, part("Top", 356, 869.95, ops=SHELF_OPS)))
SPLIT_CUT = [*CUT[:2], (2, "Made-up White", "Top", 356.0, 870.0), CUT[3]]


def test_every_nest_of_the_job_is_read_since_a_size_may_be_split_between_them(tmp_path) -> None:
    alone = Job(tmp_path / "alone", said=cutlist_text(SPLIT_CUT), design=SPLIT)
    split = Job(tmp_path / "split", said=cutlist_text(SPLIT_CUT), design=SPLIT)
    (split.folder / "3-4 Other Colour.opt").write_bytes(
        nest(nest_part("R1C7", "Top", 356, 869.95, ops=SHELF_OPS), material_id="101"))

    assert (alone.run().code, alone.run().parts) == (NOT_VERIFIED, ("R1C7",))
    assert split.run().code == DESIGN_MATCHES


def test_a_wrong_part_in_another_nest_never_takes_the_judged_nests_place(tmp_path) -> None:
    # Whichever nest is read first, a piece that fits takes its place before one that doesn't.
    job = Job(tmp_path)
    (job.folder / "1-4 Comes First.opt").write_bytes(nest(nest_part("R1C3", "Top", 356, 869.95)))

    assert job.run().code == DESIGN_MATCHES


# ----- wired in: watch.nest_checks runs it beside Jim's two checks, from the same adopted copy


def test_the_guards_run_the_design_match_beside_jims_two_checks(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace

    from ngrunner.guards import watch
    from ngrunner.guards.facts import BR_CAM_NEST, PASSED, SHEET_SIZE, ToolVerdict

    copy = object()
    tools = (ToolVerdict(SHEET_SIZE, PASSED, checked=CHECKED), ToolVerdict(BR_CAM_NEST, PASSED, checked=CHECKED))
    found = design.DesignMatch(NOT_VERIFIED, checked=CHECKED, parts=("R1C3",), note="Not verified: x")
    given = []
    monkeypatch.setattr(watch, "adopted_snapshot", lambda store, folder: copy)
    monkeypatch.setattr(watch, "nest_checks_from", lambda snap, work: (lambda job: (tools, None)))
    monkeypatch.setattr(watch, "design_checks_from",
                        lambda snap, work: given.append(snap) or (lambda job: found))
    ctx = SimpleNamespace(store=None, paths=SimpleNamespace(toolkit=tmp_path, staging=tmp_path))

    assert watch.nest_checks(ctx)(object()) == (tools, found)
    assert given == [copy]


def test_with_no_adopted_copy_the_guards_run_no_check_and_no_design_match(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace

    from ngrunner.guards import watch

    monkeypatch.setattr(watch, "adopted_snapshot", lambda store, folder: None)
    ctx = SimpleNamespace(store=None, paths=SimpleNamespace(toolkit=tmp_path, staging=tmp_path))

    assert watch.nest_checks(ctx) is None


def test_george_and_jims_digest_say_not_verified_in_words_the_lint_allows() -> None:
    from ngrunner.messages import templates
    from ngrunner.messages.lint import lint

    saved = rooms()
    saved["Room4.des"] = room(product(1, part("Top", 356, 600)))
    found = judge(nest(*nest_parts(shelf="")), cut=[*CUT, (1, "Made-up White", "Top", 356.0, 600.0)],
                  design_rooms=saved)
    message = templates.do_not_cut("Testerson_101426", "3-4 Classic White", reasons=(found.note,), trial=True)

    assert found.code == NOT_VERIFIED and lint(message) == []
    assert check_line(templates.open_do_not_cut("Testerson_101426", "3-4 Classic White",
                                                reasons=(found.note,), trial=False)) == []
