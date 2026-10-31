"""
Board parts (ngrunner/convert/parts.py): which PULLED rows are cut from a sheet, how thick they are
built, their colour and their tape, and the rows the rules don't place (docs/p2/build-spec.md,
sections 3.3, 5.2 and 6.2).

Every line here is made up. Each rule of 5.2 has one line, and so does each Unplaced reason: a row
the rules don't place reads "could not verify", never a guess.
"""
from __future__ import annotations

import pytest

from ngrunner.convert import rules
from ngrunner.convert.parts import BoardPart, Unplaced, board_part, settle_colours
from ngrunner.convert.summary_lines import SummaryLine


def line(desc: str, qty: int = 1, item: str = "", *, page: int = 9) -> SummaryLine:
    if not item:
        head = desc.split()[0]
        item = f"{head}-WH" if head.isupper() else "400000999"
    return SummaryLine("CTO", item, qty, desc, page, 0)


def part(desc: str, qty: int = 1, item: str = "") -> BoardPart:
    found = board_part(line(desc, qty, item))
    assert isinstance(found, BoardPart), found
    return found


def unplaced(desc: str, qty: int = 1, item: str = "") -> Unplaced:
    found = board_part(line(desc, qty, item))
    assert isinstance(found, Unplaced), found
    return found


# ----- what is board, and how it is read


def test_a_shelf_reads_its_kind_size_thickness_codes_and_colour() -> None:
    shelf = part("SH 14x23.9x3/4 FIX WH", qty=8)
    assert (shelf.kind, shelf.qty, shelf.a_in, shelf.b_in) == ("SH", 8, 14.0, 23.9)
    assert (shelf.printed_thk, shelf.built_thk, shelf.colour) == (0.75, 0.75, "WH")
    assert shelf.codes == ("FIX", "WH")
    assert (shelf.wall_mount, shelf.source) == (False, "pulled")


@pytest.mark.parametrize("desc, kind", [
    ("VT RAD 14x42.4x3/4 FD FN WH", "VT"),
    ("CK Kick 2.5x23.9x3/4 DFC WH", "CK"),
    ("FF Cleat 3x80.2x3/4 WH", "FF Cleat"),
    ("FF Cbnt Cleat 3x37.8x3/4 WH", "FF Cleat"),
    ("TR Base 4.1x96x3/4 FL WH", "TR Base"),
    ("TR BASE 4x96x3/4 FL RR 2m", "TR Base"),
    ("DW Hook 4.9x23.9x3/4 FL WH", "DW"),
    ("DR Deco 28.4x7.5x3/4 FL WH", "DR"),
    ("DK 23.2x46.5x3/4 WH", "DK"),
    ("Custom Vert 14x80x3/4 FD WH", "VT"),
])
def test_the_kind_comes_as_takeoff_py_names_it(desc: str, kind: str) -> None:
    assert part(desc).kind == kind


def test_the_colour_is_the_first_colour_code_after_the_size() -> None:
    assert part("WS AP 101 17.5x41.2x3/4 GD AP").colour == "AP"
    assert part("TR BASE 4x96x3/4 FL RR 2m").colour == "RR"
    assert part("SH 14x14.5x3/4 ADJ ML").colour == "ML"


def test_a_vertical_with_a_notch_code_is_wall_mounted() -> None:
    for code in ("FN", "RN", "LN"):
        assert part(f"VT 14x47.4x3/4 FD {code} WH").wall_mount
    assert not part("VT 14x76.4x3/4 FD WH").wall_mount
    assert not part("VT 14x76.4x3/4 FD NN WH").wall_mount


# ----- built thickness (5.2's rules)


def test_a_back_panel_under_half_an_inch_is_built_quarter() -> None:
    back = part("BP 29.9x34.5x3/8 WH")
    assert (back.printed_thk, back.built_thk) == (0.375, 0.25)
    assert part("BP Cbnt 46.5x74.9x3/4 WH").built_thk == 0.75


def test_a_rail_cover_is_built_three_quarter_at_its_face_size() -> None:
    cover = part("TR Rail Cover 3.5x96x0.4 RAIL WH", qty=7)
    assert (cover.kind, cover.a_in, cover.b_in, cover.printed_thk, cover.built_thk) == (
        "TR Rail Cover", 3.5, 96.0, 0.4, 0.75)
    assert cover.colour == "WH"


def test_a_rail_cover_hardware_line_is_built_3_5_by_96_at_three_quarter() -> None:
    cover = part("White Rail Cover", qty=3, item="400000570")
    assert (cover.kind, cover.qty, cover.a_in, cover.b_in, cover.built_thk) == (
        "TR Rail Cover", 3, 3.5, 96.0, 0.75)
    assert (cover.colour, cover.source, cover.printed_thk) == ("", "pulled_hardware", 0.0)


def test_a_closet_worksurface_at_1_1_8_is_built_three_quarter() -> None:
    top = part("WS WH 101 20.1x63.1x1-1/8 WH")
    assert (top.printed_thk, top.built_thk) == (1.125, 0.75)


def test_a_white_one_inch_part_stays_one_inch() -> None:
    assert part("SH 22.4x46.5x1 ADJ WH").built_thk == 1.0


# ----- not board


@pytest.mark.parametrize("desc", [
    "DR 19.8x54.1x3/4 SH-G WH",                    # a glass door
    "Scribe Molding 1-3/16 x 96\" WH",             # disregarded (RULES 251)
    "Quarter Round 3/4 x 96 WH",
    "DB 15.2x17.5x1/4 MP",                         # drawer bottom
    "DF 9.4x18.1x15.7 MP",                         # drawer box
    "DD SM 22x24.7x3.3 PLY",
    "Wrap 4x16x14 Maple",
    "INS 16x50.3 G CLR",                           # glass insert
    "DRAF 12.2x59.1 LH AF004 GLD",                 # aluminium-frame door
    "Shelf Pin",
    "Rod Round 96in Chrome",
    "L Bracket 37.5x13.5mm (100 pk)",
    "Rail Stl 96in",
    "Basket 11x24x14 MNL",
])
def test_a_line_not_cut_from_a_sheet_is_not_board(desc: str) -> None:
    assert board_part(line(desc)) is None


# ----- Unplaced: never a guess


@pytest.mark.parametrize("desc, words", [
    ("SH WH", "no size"),
    ("DW 08x24 FL WH", "no thickness"),                           # the nominal DW form
    ("Shelf 14x18 Fix WH", "no thickness"),                       # the Shelf form
    ("CK DBack 3.6x22.2x0.6 NN WH", "0.6"),
    ("TR 5x96x0.6 COVE WH", "0.6"),
    ("SH 22.4x46.5x1 ADJ LG", "1in"),                             # 1in only exists in white
    ("SHG 13x23.7x1/4 CLR", "kind"),                              # a kind not in the list
    ("HP 30.5x22.6x3/4 FL WH", "edge banding"),                   # no tape rule
    ("TR 4x96x3/4 FL WH", "trim"),                                # TR, neither base nor cover
    ("VT 14x76.4x0.7 FD WH", "0.7"),                              # a thickness never named
    ("SH 19.1x23.9x1-1/8 ADJ WH", "1-1/8"),                       # 1-1/8 only for a WS
    ("SH 14x23.9x1/4 FIX WH", "1/4"),                             # only backs are 1/4
    ("SH 14x23.9x3/4 FIX", "colour"),                             # no colour code
])
def test_a_line_the_rules_dont_place_is_unplaced_with_its_reason(desc: str, words: str) -> None:
    found = unplaced(desc)
    assert words in found.reason
    assert found.line.desc == desc


def test_an_item_code_that_names_another_kind_is_unplaced() -> None:
    assert "item code" in unplaced("SH 14x23.9x3/4 FIX WH", item="VT-WH").reason


def test_a_door_named_in_words_takes_its_kind_from_the_item_code() -> None:
    found = board_part(line("Door Euro 23.8x15.6x0.7", item="DR-LG"))
    assert isinstance(found, Unplaced) and "0.7" in found.reason


def test_the_named_line_says_page_quantity_and_item_and_no_description() -> None:
    found = unplaced("Shelf 14x18 Fix WH", qty=2, item="300005086")
    assert found.named().startswith("Page 10: 2 of item 300005086, ")
    assert "14x18" not in found.named() and "Fix" not in found.named()


def test_an_item_that_is_not_a_code_is_left_out_of_the_name() -> None:
    found = board_part(SummaryLine("CTO", "Testerson Lane", 1, "SHG 13x23.7x1/4 CLR", 3, 0))
    assert isinstance(found, Unplaced)
    assert "Testerson" not in found.named()


# ----- tape (takeoff.py's eb_lf, per part)


@pytest.mark.parametrize("desc, feet", [
    ("SH 14x24x3/4 FIX WH", 2.0),                   # one long edge: the second dimension
    ("TR Base 4.1x96x3/4 FL WH", 8.0),
    ("VT 14x84x3/4 FD WH", 7.0),                    # a floor vertical: its height
    ("VT 14x48x3/4 FD FN WH", (48 + 28) / 12),      # wall-hung: height + 2 x depth
    ("DW 12x24x3/4 FL WH", 6.0),                    # all four edges
    ("DR 12x24x3/4 FL WH", 6.0),
    ("WS WH 101 12x24x1-1/8 WH", 6.0),
    ("TR Rail Cover 3.5x96x0.4 RAIL WH", 16.0),     # both long edges
    ("CK Kick 2.5x24x3/4 DFC WH", 0.0),             # none
    ("FF Cleat 3x80x3/4 WH", 0.0),
    ("BP 24x48x3/8 WH", 0.0),
])
def test_each_kind_takes_takeoff_py_tape(desc: str, feet: float) -> None:
    assert part(desc).tape_ft == pytest.approx(feet)


def test_area_is_quantity_times_the_face() -> None:
    assert part("SH 12x24x3/4 FIX WH", qty=3).sqft == pytest.approx(6.0)


# ----- the room's colour (RULES 221 and 224)


def _judged(*descs: tuple[str, int]) -> list[tuple[SummaryLine, BoardPart | Unplaced]]:
    out = []
    for desc, qty in descs:
        found = board_part(line(desc, qty, "400000570" if desc == "White Rail Cover" else ""))
        assert found is not None
        out.append((line(desc, qty, "400000570" if desc == "White Rail Cover" else ""), found))
    return out


def test_on_a_one_colour_job_a_rail_cover_and_a_worksurface_take_its_colour() -> None:
    judged = _judged(("VT 14x84x3/4 FD AP", 4), ("White Rail Cover", 2),
                     ("WS AP 101 20x40x1-1/8 AP", 1), ("BP 24x48x3/8 AP", 1))
    settled = settle_colours(judged, {})
    assert [(p.kind, p.colour) for p in settled if isinstance(p, BoardPart)] == [
        ("VT", "AP"), ("TR Rail Cover", "AP"), ("WS", "AP"), ("BP", "AP")]


def test_on_a_two_colour_job_the_design_lists_settle_a_rail_cover() -> None:
    judged = _judged(("VT 14x84x3/4 FD AP", 4), ("VT 14x84x3/4 FD DA", 2), ("White Rail Cover", 3))
    designs = {
        "one": [line("VT 14x84x3/4 FD AP", 4), line("White Rail Cover", 2, "400000570")],
        "two": [line("VT 14x84x3/4 FD DA", 2), line("White Rail Cover", 1, "400000570")],
    }
    settled = settle_colours(judged, designs)
    covers = sorted((p.colour, p.qty) for p in settled
                    if isinstance(p, BoardPart) and p.kind == "TR Rail Cover")
    assert covers == [("AP", 2), ("DA", 1)]


def test_on_a_two_colour_job_a_rail_cover_the_designs_dont_list_is_unplaced() -> None:
    judged = _judged(("VT 14x84x3/4 FD AP", 4), ("VT 14x84x3/4 FD DA", 2), ("White Rail Cover", 3))
    designs = {"one": [line("VT 14x84x3/4 FD AP", 4)], "two": [line("VT 14x84x3/4 FD DA", 2)]}
    settled = settle_colours(judged, designs)
    lost = [p for p in settled if isinstance(p, Unplaced)]
    assert len(lost) == 1 and "rail cover" in lost[0].reason


def test_a_worksurface_in_a_room_of_two_colours_is_unplaced() -> None:
    judged = _judged(("VT 14x84x3/4 FD AP", 4), ("SH 14x24x3/4 FIX LG", 2),
                     ("WS LG 101 20x40x1-1/8 LG", 1))
    designs = {"one": [line("VT 14x84x3/4 FD AP", 4), line("SH 14x24x3/4 FIX LG", 2),
                       line("WS LG 101 20x40x1-1/8 LG", 1)]}
    settled = settle_colours(judged, designs)
    lost = [p for p in settled if isinstance(p, Unplaced)]
    assert len(lost) == 1 and "worksurface" in lost[0].reason


def test_a_rail_cover_on_a_job_with_no_three_quarter_colour_is_unplaced() -> None:
    settled = settle_colours(_judged(("BP 24x48x3/8 AP", 1), ("White Rail Cover", 1)), {})
    assert isinstance(settled[-1], Unplaced)


def test_unplaced_lines_pass_through_settling_unchanged() -> None:
    judged = _judged(("VT 14x84x3/4 FD WH", 1), ("CK DBack 3.6x22.2x0.6 NN WH", 2))
    assert settle_colours(judged, {})[1] == judged[1][1]


def test_rules_never_default_a_sheet_and_the_fallback_is_only_recorded() -> None:
    assert rules.sheet_size("NV", 0.75) is None
    assert rules.TAKEOFF_PY_FALLBACK_SHEET == (49.0, 97.0)


def test_identical_rows_settle_together_and_only_what_the_rooms_list_is_settled() -> None:
    judged = _judged(("VT 14x84x3/4 FD AP", 4), ("VT 14x84x3/4 FD DA", 2),
                     ("White Rail Cover", 1), ("White Rail Cover", 1), ("White Rail Cover", 1))
    designs = {"one": [line("VT 14x84x3/4 FD AP", 2), line("White Rail Cover", 1, "400000570")],
               "two": [line("VT 14x84x3/4 FD AP", 2), line("White Rail Cover", 1, "400000570")],
               "three": [line("VT 14x84x3/4 FD DA", 2)]}
    settled = settle_colours(judged, designs)
    covers = [(p.colour, p.qty) for p in settled
              if isinstance(p, BoardPart) and p.kind == "TR Rail Cover"]
    lost = [p for p in settled if isinstance(p, Unplaced)]
    assert covers == [("AP", 2)]
    assert [(u.line.qty, "rail cover" in u.reason) for u in lost] == [(1, True)]


def test_rooms_listing_more_than_the_pulled_list_settle_nothing() -> None:
    judged = _judged(("VT 14x84x3/4 FD AP", 4), ("VT 14x84x3/4 FD DA", 2), ("White Rail Cover", 1))
    designs = {"one": [line("VT 14x84x3/4 FD AP", 4), line("White Rail Cover", 1, "400000570")],
               "two": [line("VT 14x84x3/4 FD DA", 2), line("White Rail Cover", 1, "400000570")]}
    settled = settle_colours(judged, designs)
    assert [type(p).__name__ for p in settled] == ["BoardPart", "BoardPart", "Unplaced"]


def test_a_door_style_code_is_not_read_as_a_colour() -> None:
    assert part("DW 12x24x3/4 SH WH").colour == "WH"           # SH: the Shaker style


def test_a_worksurface_printed_in_another_colour_than_its_room_is_unplaced() -> None:
    judged = _judged(("VT 14x84x3/4 FD IV", 4), ("WS SU 101 15.5x50.1x3/4 SU", 2),
                     ("WS IV 101 15.5x23.9x3/4 IV", 1))
    settled = settle_colours(judged, {})
    assert isinstance(settled[1], Unplaced)
    assert "RULES 221" in settled[1].reason and "RULES 190" in settled[1].reason
    assert isinstance(settled[2], BoardPart) and settled[2].colour == "IV"


def test_a_colour_code_the_rules_dont_know_is_unplaced_without_echoing_it() -> None:
    found = unplaced("SH 14x24x3/4 FIX NV")
    assert "colour code" in found.reason and "NV" not in found.reason
    # An address in a description can't become a colour: CA is no colour code.
    found = unplaced("SH 14x24x3/4 FIX 12 Fakeview Lane, Testville, CA 90000")
    assert "CA" not in found.reason and "Fakeview" not in found.named()


@pytest.mark.parametrize("desc", ["DW 08x24x3/4 SH WH", "DW 05x30x3/4 EE WH", "TR BASE 04x96x3/4 FL RR 2m"])
def test_a_nominal_size_written_with_a_leading_zero_is_unplaced(desc: str) -> None:
    """The third description form (pair PBA): an 08 drawer face is built 7.4, which nothing in
    the text gives, so its size would be a guess."""
    assert "nominal" in unplaced(desc).reason


def test_only_a_line_that_starts_as_a_rail_cover_is_built_as_one() -> None:
    assert board_part(line("Steel Rod and Rail Cover Kit", item="400000999")) is None
