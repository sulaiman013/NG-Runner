"""
Reading a CNC program: how far it cuts, and which holes it drills twice.

Why: a false DO NOT CUT stops real work, and a missed doubled cam housing ruins sheets, so every
parsing rule of docs/p1b/build-spec.md section 3.1 has its own test on a made-up program. The tests
marked `fixtures` read real programs from .local-fixtures (tools/collect_p1b_fixtures.py), name them
by index and print no real name. The whole-drive run needs NGR_NG_JOBS set, as it is slow.
"""
from __future__ import annotations

import collections
import dataclasses
import hashlib
import json
import os
import random
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import pytest

from ngrunner.guards.tap import Family, NearPair, Tap, _lines, _walk, family_of, read_tap

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(os.environ.get("NGR_FIXTURES", ROOT / ".local-fixtures"))
TAPS = FIXTURES / "p1b" / "taps"
SHEET_NAME = "TESS01.TAP"
MERGED_NAME = "Tes1-3.tap"

START = ["G00 G17 G20 G40", "G49 G80", "G90 G61", "G28 Z0. W0.", "G90 G54"]
TOOL6 = ["M06 T6", "G43 H6 Z1.5100", "S12000 M03"]
END = ["G0 Z3.0000", "M09", "M05", "G28 Z0.0 W0.0", "G90", "G00 X0.0000 Y108.0000", "M200",
       "M30", "%"]


def program(*body: str | Iterable[str], start: Iterable[str] = START,
            end: Iterable[str] = END) -> bytes:
    """A made-up program in the real layout: its lines, then the park move, M30 and %."""
    lines = list(start)
    for part in body:
        lines += [part] if isinstance(part, str) else list(part)
    lines += end
    return "\r\n".join(lines).encode("ascii")


def hole(x: float, y: float, depth: float = 0.13) -> list[str]:
    """A hole as Mozaik posts it: down at speed over the spot, plunge, back up at the same spot."""
    return [f"G0 X{x:.4f} Y{y:.4f} Z1.5100", f"G0 X{x:.4f} Y{y:.4f} Z1.0100",
            f"G1 Z{depth:.4f} F150.0", f"G0 X{x:.4f} Y{y:.4f} Z1.5100"]


def cut_to(x: float, y: float) -> list[str]:
    """Down, then one cutting move: its own start is the origin, so only (x, y) can set a reach."""
    return ["G0 X0.0000 Y0.0000 Z1.5100", "G1 Z0.5000 F100.0", f"G1 X{x:.4f} Y{y:.4f} F738.0",
            "G0 Z1.5100"]


def read(*body: str | Iterable[str], name: str = SHEET_NAME) -> Tap:
    return read_tap(name, program(TOOL6, *body))


# ----- the sheet: only where a tool really cuts, after a tool change


def test_reach_is_the_largest_x_and_y_of_the_cutting_moves():
    tap = read("G0 X1.0000 Y1.0000 Z1.5100", "G1 Z0.5000 F100.0", "G1 X48.7500 Y2.0000 F738.0",
               "G3 X48.0000 Y96.7500 I0.0000 J-0.1975 F738.0",
               "G2 X3.0000 Y9.0000 I0.1000 J0.1000 F738.0", "G0 Z1.5100")
    assert tap.reach == (48.75, 96.75)


def test_a_program_with_no_cutting_move_has_no_reach():
    assert read("G0 X10.0000 Y20.0000 Z1.5100", "G0 Z3.0000").reach is None


def test_the_park_move_is_never_the_sheet():
    tap = read(cut_to(10.0, 20.0))        # the real program's last move is G00 X0.0000 Y108.0000
    assert b"G00 X0.0000 Y108.0000" in program(TOOL6, cut_to(10.0, 20.0))
    assert tap.reach == (10.0, 20.0)


def test_a_rapid_is_never_the_sheet():
    tap = read("G0 X200.0000 Y300.0000 Z1.5100", cut_to(10.0, 20.0),
               "G00 X200.0000 Y300.0000")
    assert tap.reach == (10.0, 20.0)


def test_a_drill_bank_reference_is_never_the_sheet():
    drilling = ["(Multi Drill Section)", "M13",
                "G81 B31 G99 G54.1P1 X53.0000 Y4.2165 W0.1694 R1.0100 F60.0",
                "X53.5000 Y150.0000 W0.1694", "Y160.0000 W0.1694", "X54.0000  W0.1694",
                "G81 B0", "G80", "M15", "G53W", "G90 G54"]
    tap = read_tap(SHEET_NAME, program(drilling, TOOL6, cut_to(10.0, 20.0)))
    assert tap.reach == (10.0, 20.0)


def test_a_drill_bank_reference_after_a_tool_change_is_still_not_the_sheet():
    drilling = ["G81 B31 G99 G54.1P1 X53.0000 Y4.2165 W0.1694 R1.0100 F60.0",
                "X53.5000 Y150.0000 W0.1694", "G81 B0", "G80"]
    assert read(cut_to(10.0, 20.0), drilling, cut_to(11.0, 21.0)).reach == (11.0, 21.0)
    # straight after a cut, so the G1 before it would read those lines as cuts if G81 were missed
    assert read("G0 X1.0000 Y1.0000 Z1.5100", "G1 Z0.5000 F100.0", "G1 X10.0000 Y20.0000 F738.0",
                drilling).reach == (10.0, 20.0)


def test_a_home_or_machine_move_is_never_the_sheet():
    tap = read(cut_to(10.0, 20.0), "G1 X5.0000 Y5.0000 F738.0", "G28 X90.0000 Y90.0000",
               "G53 X0.0000 Y108.0000", "G53 G1 X80.0000 Y80.0000", "G91 G28 X70.0000 Y70.0000")
    assert tap.reach == (10.0, 20.0)


def test_a_cut_before_any_tool_change_is_not_the_sheet():
    tap = read_tap(SHEET_NAME, program("G1 X90.0000 Y90.0000 F738.0", TOOL6, cut_to(10.0, 20.0)))
    assert tap.reach == (10.0, 20.0)


def test_a_tool_change_naming_no_tool_is_still_a_tool_change():
    assert read_tap(SHEET_NAME, program("M06", cut_to(10.0, 20.0))).reach == (10.0, 20.0)


def test_naming_a_tool_without_changing_to_it_is_not_a_tool_change():
    """How two old test files start: they have no M06, and aren't complete either."""
    assert read_tap(SHEET_NAME, program("T6", cut_to(10.0, 20.0))).reach is None


def test_a_cut_with_no_g_word_is_still_a_cut():
    tap = read("G0 X1.0000 Y1.0000 Z1.5100", "G1 Z0.5000 F100.0", "G1 X2.0000 F738.0",
               "Y65.3943 F738.0", "X30.5000 F738.0", "G0 Z1.5100")
    assert tap.reach == (30.5, 65.3943)


def test_nothing_moves_on_a_line_with_no_g_word_after_g80():
    tap = read("G0 X1.0000 Y1.0000 Z1.5100", "G1 Z0.5000 F100.0", "G1 X10.0000 Y20.0000 F738.0",
               "G80", "X99.0000 Y99.0000")
    assert tap.reach == (10.0, 20.0)


def test_a_rapid_with_no_g_word_is_still_a_rapid():
    assert read(cut_to(10.0, 20.0), "G0 X1.0000 Y1.0000 Z1.5100", "X99.0000 Y99.0000").reach == (
        10.0, 20.0)


def test_a_line_with_no_g_word_after_an_arc_is_still_a_cut():
    tap = read(cut_to(10.0, 20.0), "G3 X11.0000 Y21.0000 I0.0000 J1.0000 F738.0", "X40.0000 F738.0")
    assert tap.reach == (40.0, 21.0)


def test_a_cut_counts_where_it_starts_too():
    """The tool reaches its start point cutting, whichever axis the next line names."""
    tap = read("G0 X45.0000 Y100.0000 Z1.5100", "G1 Z0.5000 F100.0", "G1 X2.0000 F738.0",
               "G1 Y3.0000 F738.0", "G0 Z1.5100")
    assert tap.reach == (45.0, 100.0)
    # a rapid already at depth: nothing but the start of the first cut says it was there
    tap = read("G0 X45.0000 Y100.0000 Z0.5000", "G1 X2.0000 Y3.0000 F738.0", "G0 Z1.5100")
    assert tap.reach == (45.0, 100.0)


def test_a_program_that_only_drills_reaches_its_last_hole():
    """Four real programs drill and never move sideways while cutting: no reach would hide them."""
    assert read(hole(41.8386, 90.5591), hole(20.0, 10.0)).reach == (41.8386, 90.5591)


def test_a_cut_along_one_axis_with_no_other_position_still_reaches():
    assert read("G1 X99.0000 F738.0").reach == (99.0, 0.0)


def test_words_written_together_or_with_leading_zeros_or_line_numbers_read_the_same():
    tap = read_tap(SHEET_NAME, program(
        "N30 M6T6", "N31 G43H6Z1.5100", "N32 g01 x1.5 y2.5 F100", "N33 G01X12.25Y33.5",
        "N34 G0 Z3.",
        start=["N1 M400", "N2 M00", "N3 G00 G17G20G40", "N6 G28 Z0.W0."]))
    assert tap.reach == (12.25, 33.5)
    assert tap.inches is True


def test_a_comment_never_feeds_a_number():
    comments = ["(3-4 Egger Classic White W=61 L=109 X99.0000 Y99.0000)",
                "G1 X77.0000 Y77.0000 (G1 X88 Y88",
                "(G20)"]
    tap = read_tap(SHEET_NAME, program(comments[0], TOOL6, cut_to(10.0, 20.0),
                                       "(G1 X99.0000 Y99.0000)", start=["G00 G17 G40"]))
    assert tap.reach == (10.0, 20.0)
    assert read_tap(SHEET_NAME, program(comments[2], start=["G00 G17 G40"])).inches is False
    # a comment left open runs to the end of its line, and the move before it still counts
    assert read(cut_to(10.0, 20.0), comments[1]).reach == (77.0, 77.0)


# ----- a hole: a straight plunge, then a retract at the same spot


def test_a_hole_is_a_plunge_with_no_sideways_move_then_a_retract_at_the_same_spot():
    assert read(hole(34.5276, 15.2638)).holes == 1


def test_a_retract_naming_only_z_is_at_the_same_spot():
    """Mozaik's older post writes the retract this way, in 4,830 real holes."""
    tap = read("G0 X5.0000 Y6.0000 Z1.5100", "G0 Z1.0100", "G1 Z0.1300 F105.0", "G0 Z1.5100")
    assert tap.holes == 1


def test_a_retract_naming_one_axis_at_the_same_place_is_at_the_same_spot():
    tap = read("G0 X5.0000 Y6.0000 Z1.5100", "G1 Z0.1300 F105.0", "G0 Y6.0000 Z1.5100")
    assert tap.holes == 1


def test_a_router_lead_in_is_not_a_hole():
    lead_in = ["G0 X2.0834 Y1.7927 Z1.4980", "G0 Z0.9980", "G1 Z0.7480 F105.0",
               "G1 X2.7905 Y2.4998 Z0.5118 F520.0", "X5.6189 Y5.3282 F520.0", "G0 Z1.4980"]
    assert read(lead_in).holes == 0


def test_a_ramp_back_to_the_same_spot_is_not_a_hole():
    assert read("G0 X5.0000 Y5.0000 Z1.5100", "G1 X5.0000 Y5.0000 Z0.1300 F100.0",
                "G0 X5.0000 Y5.0000 Z1.5100").holes == 0


@pytest.mark.parametrize("after", [
    ("G1 X9.0000 Y9.0000 F738.0", "G0 Z1.5100"),       # cuts sideways: a pocket, not a hole
    ("G0 X9.0000 Y9.0000 Z1.5100",),                   # leaves for somewhere else
    ("G0 X6.0000 Z1.5100",),                           # another X, at the same Y
    ("G0 Y6.0000 Z1.5100",),                           # another Y, at the same X
    ("G0 X5.0000 Y5.0000",),                           # no retract at all
    ("G0 X5.0000 Y5.0000 Z0.0500",),                   # goes down, not up
    ("G0 X5.0000 Y5.0000 Z0.1300",),                   # stays at depth
    ("G1 X5.0000 Y5.0000 Z1.5100 F100.0",),            # comes back up at feed speed, not a rapid
    ("G1 Z1.5100 F100.0",),                            # the same, naming only Z
    ("M06 T7", "G43 H7 Z1.5100", "G0 X5.0000 Y5.0000 Z1.5100"),   # a new tool first
    (),                                                # the file just ends (END's G0 Z is dropped)
], ids=["cuts", "leaves", "other_x", "other_y", "no_z", "down", "level", "feed_up", "feed_up_z",
     "tool_change", "end"])
def test_a_plunge_with_no_retract_at_the_same_spot_is_not_a_hole(after):
    tail = ["M09", "M30", "%"]
    tap = read_tap(SHEET_NAME, program(TOOL6, "G0 X5.0000 Y5.0000 Z1.5100", "G1 Z0.1300 F150.0",
                                       after, end=tail))
    assert tap.holes == 0


def test_a_hole_taken_in_steps_is_one_hole_at_its_last_depth():
    tap = read("G0 X5.0000 Y5.0000 Z1.5100", "G1 Z0.5000 F150.0", "G1 Z0.1300 F150.0",
               "G0 X5.0000 Y5.0000 Z1.5100", hole(5.0005, 5.0, depth=0.13))
    assert tap.holes == 2
    assert [p.depth for p in tap.near_pairs] == [0.13]


def test_a_plunge_on_a_line_with_no_g_word_is_a_plunge():
    tap = read("G0 X5.0000 Y5.0000 Z1.5100", "G1 X5.0000 Y5.0000 Z0.8000 F150.0", "Z0.1300 F150.0",
               "G0 Z1.5100")
    assert tap.holes == 1


def test_a_hole_needs_a_known_spot_and_a_tool():
    assert read("G1 Z0.1300 F150.0", "G0 Z1.5100").holes == 0
    assert read_tap(SHEET_NAME, program(hole(5.0, 5.0))).holes == 0   # no tool change yet


def test_the_tool_length_line_is_not_a_plunge():
    """G43 carries a Z, and the G1 of the last tool is still in force when it comes."""
    tap = read(cut_to(5.0, 5.0), "G1 X5.0000 Y5.0000 F738.0", "M06 T1", "G43 H1 Z1.5100",
               "G0 X5.0000 Y5.0000 Z2.0000")
    assert tap.holes == 0


# ----- a near pair: the same tool and depth, more than 0 and at most 0.0197in apart


def test_the_doubled_cam_housing_is_found():
    tap = read(hole(34.5276, 15.2638), hole(34.5276, 15.2648))
    assert tap.holes == 2
    assert tap.near_pairs == (NearPair("T6", 0.13, 0.001, 34.5276, 15.2638),)


def test_a_pair_at_the_limit_is_near_and_just_past_it_is_not():
    assert read(hole(1.0, 1.0), hole(1.0, 1.0197)).near_pairs[0].apart_in == 0.0197
    assert read(hole(1.0, 1.0), hole(1.0197, 1.0)).near_pairs[0].apart_in == 0.0197
    assert read(hole(1.0, 1.0), hole(1.0, 1.0198)).near_pairs == ()
    assert read(hole(1.0, 1.0), hole(1.0198, 1.0)).near_pairs == ()


def test_the_limit_is_measured_as_the_crow_flies():
    """0.014in each way is 0.0198in apart: past the limit, though each axis alone is inside it."""
    assert read(hole(1.0, 1.0), hole(1.014, 1.014)).near_pairs == ()
    assert read(hole(1.0, 1.0), hole(1.013, 1.013)).near_pairs[0].apart_in == 0.018385


def test_the_same_spot_twice_is_not_a_near_pair():
    assert read(hole(1.0, 1.0), hole(1.0, 1.0)).near_pairs == ()


def test_different_tools_at_one_spot_are_a_counterbore_not_a_pair():
    tap = read(hole(1.0, 1.0), "M06 T5", "G43 H5 Z1.5100", hole(1.001, 1.0))
    assert tap.holes == 2
    assert tap.near_pairs == ()


def test_the_same_tool_loaded_twice_still_pairs():
    tap = read(hole(1.0, 1.0), "M06 T5", "G43 H5 Z1.5100", hole(9.0, 9.0), "M06 T6",
               "G43 H6 Z1.5100", hole(1.001, 1.0))
    assert [p.tool for p in tap.near_pairs] == ["T6"]


def test_different_depths_are_not_a_pair():
    tap = read(hole(1.0, 1.0, depth=0.13), hole(1.001, 1.0, depth=0.20))
    assert tap.holes == 2
    assert tap.near_pairs == ()


def test_a_hole_far_off_between_two_near_ones_doesnt_hide_the_pair():
    """Sorted by X, the middle one comes between them, though it is nowhere near."""
    tap = read(hole(1.0, 1.0), hole(1.0005, 50.0), hole(1.001, 1.0))
    assert [(p.x, p.y) for p in tap.near_pairs] == [(1.0, 1.0)]


def test_every_pair_of_three_close_holes_is_counted_in_the_order_they_are_drilled():
    tap = read(hole(1.002, 1.0), hole(1.0, 1.0), hole(1.001, 1.0))
    assert [(p.x, p.apart_in) for p in tap.near_pairs] == [
        (1.002, 0.002), (1.002, 0.001), (1.0, 0.001)]


def test_the_tool_is_named_the_same_however_it_is_written():
    for change in ("M06 T6", "M6 T6", "M06 T06", "T6 M06", "T6\r\nM06", "M06T6"):
        tap = read_tap(SHEET_NAME, program(change, hole(1.0, 1.0), hole(1.001, 1.0)))
        assert [p.tool for p in tap.near_pairs] == ["T6"], change


# ----- merged programs: a sheet check, and no near pairs


def test_a_merged_program_has_no_near_pairs_though_its_file_holds_some():
    body = (TOOL6, hole(1.0, 1.0), hole(1.001, 1.0), cut_to(48.75, 96.75))
    assert len(read_tap(SHEET_NAME, program(*body)).near_pairs) == 1
    merged = read_tap(MERGED_NAME, program(*body))
    assert merged.family == "merged"
    assert merged.near_pairs == ()


def test_a_merged_program_still_gets_its_sheet_and_its_holes():
    merged = read_tap(MERGED_NAME, program(TOOL6, hole(1.0, 1.0), hole(1.001, 1.0),
                                           cut_to(60.75, 108.75)))
    assert merged.reach == (60.75, 108.75)
    assert merged.holes == 2


@pytest.mark.parametrize("name, family", [
    # What Mozaik writes: a sheet.
    ("TESS01.TAP", "sheet"), ("TESS12.TAP", "sheet"),
    ("TesS01.TAP", "sheet"), ("Testerson_101426S01.TAP", "sheet"), ("TES2S01.TAP", "sheet"),
    ("Testerson_101426_GarS03.TAP", "sheet"),
    # Long form and flip passes.
    ("Testerson_101426_3-4_Egger_Classic_White_S01R02.TAP", "long"),
    ("Testerson_101426_3-4_Egger_Classic_White_S01R02-Flip.TAP", "long"),
    ("Testerson_3-4_Am_Lam_Wh_S07R01.TAP", "long"), ("TesS01R01.TAP", "long"),
    ("TES1-Flip.TAP", "long"), ("Flip_TES.TAP", "long"), ("TES2Flip.TAP", "long"),
    # Lower-case: what the merging tool writes, whatever it was called.
    ("Tes1-3.tap", "merged"), ("Tes2-3.tap", "merged"), ("Tes11-12-4x10a.tap", "merged"),
    ("Tes1-4-1-2.tap", "merged"), ("Tes.25-1-4.tap", "merged"), ("TESQIcombo.tap", "merged"),
    ("Tes3-4WHComb6.tap", "merged"), ("Tes123.tap", "merged"), ("Tes.tap", "merged"),
    ("TesRM.tap", "merged"), ("TESS01.tap", "merged"),
    # Remakes, redos, fixes and piles.
    ("TESdwrRM.TAP", "remake"), ("TES8RD.TAP", "remake"), ("TESS02RD.TAP", "remake"),
    ("TESFix.TAP", "remake"), ("TESfixRM.TAP", "remake"), ("TESFixed.TAP", "remake"),
    ("TESRMpile.TAP", "remake"), ("TESPile.TAP", "remake"), ("TESQIRM02.TAP", "remake"),
    ("TESFix-cleat.TAP", "remake"),
    # Everything else, including sheets Jim added by hand.
    ("TESHooks.TAP", "hand"), ("TESVert.TAP", "hand"), ("TES3.TAP", "hand"), ("TESS1.TAP", "hand"),
    ("TESS075x9.TAP", "hand"), ("TESS114x1o.TAP", "hand"), ("TES.Tap", "hand"),
    ("Board_x.TAP", "hand"), ("Tes.TXT", "hand"),
])
def test_family_of(name: str, family: Family):
    assert family_of(name) == family


@pytest.mark.parametrize("name, family", [
    # Upper-case names holding 3-4, a size or a sheet range: Mozaik's, never overlaid (0 of 51).
    ("Testerson_101426_3-4_Am_Lam_Wh_S01R01.TAP", "long"),
    ("Testerson_101426-3S01.TAP", "sheet"),
    ("Tes1-4BK2.TAP", "hand"), ("Tes29-93.TAP", "hand"), ("TES7-1.TAP", "hand"),
    # A surname holding "comb" is not a merged program: only the lower-case extension says so.
    ("Holcomb_101426S01.TAP", "sheet"),
    ("Holcomb_101426_3-4_Am_Lam_Wh_S01R01.TAP", "long"),
    ("Holcomb.TAP", "hand"),
])
def test_a_name_that_looks_merged_with_mozaiks_extension_is_not(name: str, family: Family):
    assert family_of(name) == family
    doubled = read_tap(name, program(TOOL6, hole(1.0, 1.0), hole(1.001, 1.0)))
    assert len(doubled.near_pairs) == 1


# ----- what a Tap says about itself


def test_complete_means_m30_then_percent():
    assert read().complete is True
    assert read_tap(SHEET_NAME, program(TOOL6, end=["N70 M30", "%"])).complete is True
    assert read_tap(SHEET_NAME, program(TOOL6, end=["m30", "%", "", "  "])).complete is True
    assert read_tap(SHEET_NAME, program(TOOL6, end=["M30 (end)", "%"])).complete is True


@pytest.mark.parametrize("end", [
    ["M30"],                      # cut off before the %
    ["%"],                        # no M30
    ["M300", "%"],                # another M code
    ["G0 X1.0000", "%"],
    ["M30", "G0 X1.0000", "%"],   # something between them
    ["%", "M30"],                 # the wrong way round
    ["M30", "%", "G0 X1.0000"],   # something after them
    [],
])
def test_a_program_that_doesnt_end_m30_then_percent_is_not_complete(end):
    assert read_tap(SHEET_NAME, program(TOOL6, end=end)).complete is False


def test_inches_means_the_program_says_g20():
    assert read().inches is True
    assert read_tap(SHEET_NAME, program(start=["G00 G17G20G40"])).inches is True
    assert read_tap(SHEET_NAME, program(start=["G00 G17 G40"])).inches is False
    assert read_tap(SHEET_NAME, program(start=["G00 G17 G21 G40"])).inches is False
    assert read_tap(SHEET_NAME, program(start=["G20", "G21"])).inches is False


def test_name_size_hash_and_family_come_from_the_file():
    data = program(TOOL6, hole(1.0, 1.0))
    tap = read_tap("TES8RD.TAP", data)
    assert (tap.name, tap.size, tap.family) == ("TES8RD.TAP", len(data), "remake")
    assert tap.sha256 == hashlib.sha256(data).hexdigest()


def test_an_empty_file_is_an_incomplete_program_with_nothing_in_it():
    tap = read_tap(SHEET_NAME, b"")
    assert (tap.complete, tap.inches, tap.reach, tap.holes, tap.near_pairs, tap.size) == (
        False, False, None, 0, (), 0)


_BROKEN_LINES = (
    "X", "X.", "X-", "X+.", "G", "G.", "M", "T", "N", "%%", "(", ")", "((", "G1 X", "G1 Z", "M06",
    "M06 T", "T.", "T-", "M6 T1.5", "G1 X1..2", "G1 X--2", "G1 X1e5 Y1e5", "\x00\x00", "\xff\xfe",
    "G1 X" + "9" * 400 + " Y-" + "9" * 400, "G0 X" + "9" * 400 + " Y1 Z1", "G1 Z" + "9" * 400,
    "M" + "9" * 5000, "T" + "9" * 5000, "G" + "1" * 5000,
    "M06 T6", "G0 X1 Y1 Z1", "G1 Z0.1", "G0 Z1", "G1 X1 Y1", "G2 X1", "G3", "G81 X1", "G80", "M30",
    "%", "G20", "G21", "G91",
)


def test_no_content_makes_it_raise():
    rng = random.Random(1337)
    for _ in range(400):
        lines = [rng.choice(_BROKEN_LINES) for _ in range(rng.randint(0, 60))]
        data = "\r\n".join(lines).encode("latin-1")
        assert isinstance(read_tap("TESS01.TAP", data), Tap)
        assert isinstance(read_tap("Tes1-3.tap", data), Tap)
    for _ in range(100):
        assert isinstance(read_tap("TESS01.TAP", rng.randbytes(rng.randint(0, 4000))), Tap)
    infinite = "G1 X" + "9" * 400 + " Y" + "9" * 400
    assert read_tap(SHEET_NAME, program(TOOL6, infinite)).reach == (float("inf"), float("inf"))
    # Two holes at an impossible place can't be measured apart: no pair, and nothing raised.
    twice = hole(1.0, 1.0)[0].replace("1.0000", "9" * 400)
    plunge = ["G1 Z0.1300 F150.0", "G0 Z1.5100"]
    assert read(twice, plunge, twice, plunge).near_pairs == ()


def test_a_bad_file_says_so_rather_than_raising():
    assert read_tap(SHEET_NAME, b"\x00\xff not a program \xfe").complete is False


def test_a_taps_repr_holds_names_and_numbers_only():
    """Mozaik's older post writes the job and material into comments: none of that may travel."""
    secret = ("(Testerson_101426 12 Made Up Street, Anytown)", "(Load Face Down)", "(Closet Set)",
              "(3-4 Am Lam Wh W=49 L=97)", "(TESS01.TAP)")
    tap = read_tap("TESS01.TAP", program(secret, TOOL6, hole(1.0, 1.0), hole(1.001, 1.0)))
    text = repr(tap)
    for words in ("Testerson", "Street", "Anytown", "Load", "Closet", "Am Lam", "W=49"):
        assert words not in text
    # Nothing is left once the name, the field names, the hash and the numbers are taken out.
    left = text.replace("TESS01.TAP", "").replace(tap.sha256, "")
    left = re.sub(r"\b(Tap|NearPair|name|sha256|size|inches|complete|family|reach|holes|near_pairs"
                  r"|tool|depth|apart_in|x|y|True|False|None|sheet|T6)\b", "", left)
    assert not re.search(r"[A-Za-z]", left.replace("e-", "").replace("inf", ""))


def test_a_tap_cant_be_changed_after_reading():
    with pytest.raises(dataclasses.FrozenInstanceError):
        read().holes = 5  # type: ignore[misc]


# ----- real programs: by index, printing no name


def _fixture_index() -> list[dict]:
    index = TAPS / "index.json"
    if not index.is_file():
        pytest.skip("no real programs in the fixtures: run tools/collect_p1b_fixtures.py"
                    " (see AGENTS.md: Fixtures)")
    return json.loads(index.read_text(encoding="utf-8"))


def real_programs(**match: str) -> list[tuple[dict, bytes]]:
    """The fixtures with these index fields, as (entry, bytes)."""
    return [(entry, (TAPS / entry["file"]).read_bytes()) for entry in _fixture_index()
            if all(entry[field] == value for field, value in match.items())]


def real_taps(**match: str) -> list[Tap]:
    return [read_tap(entry["name"], data) for entry, data in real_programs(**match)]


@pytest.mark.fixtures
def test_every_real_fixture_parses_complete_and_in_inches():
    all_real = real_programs()
    assert len(all_real) >= 85
    bad = [entry["index"] for entry, data in all_real
           if not (tap := read_tap(entry["name"], data)).complete or not tap.inches]
    assert not bad


@pytest.mark.fixtures
def test_every_real_reach_is_within_the_largest_sheet():
    out = [entry["index"] for entry, data in real_programs()
           if (reach := read_tap(entry["name"], data).reach) is None
           or reach[0] > 61 or reach[1] > 121]
    assert not out


@pytest.mark.fixtures
def test_case_d_has_one_program_with_8_doubled_holes():
    counts = sorted(len(tap.near_pairs) for tap in real_taps(case="D", row="doubled"))
    assert counts == [0, 0, 0, 8]
    pairs = [p for tap in real_taps(case="D", row="doubled") for p in tap.near_pairs]
    assert {(p.tool, p.apart_in) for p in pairs} == {("T6", 0.001)}
    assert {round(p.depth, 2) for p in pairs} == {0.13}


@pytest.mark.fixtures
def test_case_ds_flip_pass_reaches_just_inside_the_sheet():
    assert [tap.reach for tap in real_taps(case="D", role="flip")] == [(48.9375, 97.0)]


@pytest.mark.fixtures
@pytest.mark.parametrize("case, programs", [("C", 18), ("F", 3), ("G", 6)])
def test_cases_after_the_fix_have_no_doubled_holes(case, programs):
    taps = real_taps(case=case, row="fixed")
    assert len(taps) == programs
    assert sum(len(tap.near_pairs) for tap in taps) == 0
    assert sum(tap.holes for tap in taps) > 0


@pytest.mark.fixtures
def test_the_programs_of_cases_a_and_b_read_as_they_were_measured():
    """A positive control beyond Case D: before the fix, doubled holes show on other jobs too."""
    a = real_taps(case="A", row="sized_49")
    assert sorted(len(tap.near_pairs) for tap in a) == [0, 0, 0, 16]
    b = real_taps(case="B", row="hand_added")
    assert sum(len(tap.near_pairs) for tap in b) == 36


@pytest.mark.fixtures
@pytest.mark.parametrize("kind, family", [
    ("merged", "merged"), ("merged_bare", "merged"),
    ("remake_rm", "remake"), ("remake_rd", "remake"), ("remake_fix", "remake"),
    ("remake_pile", "remake"), ("flip", "long"), ("long", "long"), ("long_plain", "long"),
    ("hand", "hand"), ("dashed_upper", "hand"),
])
def test_each_kind_of_real_name_gets_its_family(kind, family):
    taps = real_taps(row="families", role=kind)
    assert len(taps) == 1
    assert taps[0].family == family


@pytest.mark.fixtures
def test_a_real_merged_program_with_near_pairs_in_it_reports_none():
    """Case D's merged program holds 8 pairs; read under a sheet's name they all show."""
    (entry, data), = real_programs(row="families", role="merged")
    assert read_tap(entry["name"], data).near_pairs == ()
    assert len(read_tap(SHEET_NAME, data).near_pairs) == 8


@pytest.mark.fixtures
def test_a_real_programs_comments_never_reach_its_repr():
    comment = re.compile(r"\(([^)]*)\)")
    leaked = []
    seen = 0
    for entry, data in real_programs():
        text = repr(read_tap(entry["name"], data))
        for words in comment.findall(data.decode("latin-1")):
            seen += 1
            if len(words) > 3 and words in text and words not in entry["name"]:
                leaked.append(entry["index"])
    assert seen > 0
    assert not leaked


# ----- the whole drive: optional, slow, and it reports what it measures


FIX_DAY = datetime(2026, 10, 20).timestamp()
_STOP = re.compile(r"\bM400\b|\bM0?0\b")
_TOOL_CHANGE = re.compile(r"\bM0?6\s*T([0-9]+)")


def _every_program(root: Path) -> Iterable[Path]:
    for folder, _, files in os.walk(root):
        for file in files:
            if file.lower().endswith(".tap"):
                yield Path(folder) / file


def _marks(data: bytes) -> tuple[int, bool]:
    """What a program's own content says about the sheets in it: the stops between them, and
    whether a tool is loaded again. A program straight from Mozaik has neither (1,435 of 1,435)."""
    text = data.decode("latin-1")
    tools = _TOOL_CHANGE.findall(text)
    return len(_STOP.findall(text)), len(tools) != len(set(tools))


def _exact_repeats(data: bytes) -> bool:
    holes = _walk(_lines(data)).holes
    return len(set(holes)) != len(holes)


@pytest.mark.fixtures
def test_the_whole_drive(capsys):
    ng_jobs = os.environ.get("NGR_NG_JOBS")
    if not ng_jobs:
        pytest.skip("slow: reads every program on the drive (set NGR_NG_JOBS to run it)")
    before = after = before_pairs = after_pairs = holes_after = pairs = 0
    upper_marked = merged_one_sheet = upper_repeats = merged_count = incomplete = 0
    dashed = dashed_pairs = 0
    tools: collections.Counter[str] = collections.Counter()
    gaps: collections.Counter[float] = collections.Counter()
    paths = list(_every_program(Path(ng_jobs)))
    for path in paths:
        data = path.read_bytes()
        tap = read_tap(path.name, data)
        incomplete += not (tap.complete and tap.inches)
        if tap.family == "merged":
            merged_count += 1
            stops, tool_again = _marks(data)
            merged_one_sheet += stops <= 2 and not tool_again
            continue
        stops, tool_again = _marks(data)
        upper_marked += stops > 0 or tool_again
        upper_repeats += _exact_repeats(data)
        if re.search(r"[0-9]-[0-9]", path.stem):
            dashed += 1
            dashed_pairs += bool(tap.near_pairs)
        if path.stat().st_mtime < FIX_DAY:
            before += 1
            before_pairs += bool(tap.near_pairs)
        else:
            after += 1
            after_pairs += bool(tap.near_pairs)
            holes_after += tap.holes
        pairs += len(tap.near_pairs)
        tools.update(p.tool for p in tap.near_pairs)
        gaps.update(p.apart_in for p in tap.near_pairs)
    with capsys.disabled():
        print(f"\n{len(paths)} programs read, {incomplete} not complete or not in inches, "
              f"{merged_count} merged ({merged_one_sheet} of them one sheet only)")
        print(f"not merged: {before} before the fix with {before_pairs} holding near pairs, "
              f"{after} after with {after_pairs} ({holes_after} holes)")
        print(f"{pairs} near pairs: {sorted(tools.items())}, {gaps[0.001]} of them exactly "
              f"0.001in apart; {dashed} names outside merged hold a digit-dash-digit, "
              f"{dashed_pairs} of them with near pairs")
    assert upper_marked == 0       # a program of Mozaik's holding several sheets: the rule is wrong
    assert upper_repeats == 0
    assert after_pairs == 0        # the fault Jim fixed on 10-20 is back, or the rule is wrong
    assert before_pairs > 0        # nothing found at all: the check has gone blind
