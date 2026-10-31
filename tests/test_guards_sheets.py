"""
The sheet-size check: what a clean read can say, and what only RULES' lines can ask.

Why: a program past its sheet raises DO NOT CUT, so a wrong rule stops real work. A program inside
the size proves nothing about the right sheet, since a small program fits any sheet, so "nothing
past" must never read as a pass. Only the lines of RULES' own tables carry a rule: real Egger
colours sit on either size, so any other folder name has to say it can't tell. The tie between these
lines and RULES' words is in tests/test_docs_p1b.py. The tests marked `fixtures` read real programs
by index and print no real name.
"""
from __future__ import annotations

import collections
import dataclasses
import os
from pathlib import Path

import pytest

from ngrunner.guards.sheets import RULES_LINES, SizeFinding, expected_size, size_finding
from ngrunner.guards.tap import Tap, read_tap
from test_guards_tap import real_programs, real_taps

WHITE = "3-4 Egger Classic White"


def tap(reach: tuple[float, float] | None, name: str = "TESS01.TAP", *, complete: bool = True,
        inches: bool = True) -> Tap:
    return Tap(name=name, sha256="0" * 64, size=1, inches=inches, complete=complete, family="sheet",
               reach=reach, holes=0, near_pairs=())


# ----- each RULES line


@pytest.mark.parametrize("folder, size", [
    ("3-4 Egger Classic White", (49, 97)),
    ("1 in Egger Classic White", (49, 97)),
    ("3-4 Egger Black U999 ST19 Woodgrain", (61, 109)),
    ("1 in Egger Black U999 ST19 Woodgrain", (49, 97)),
    ("3-4 Tafisa Spring Blossom_L543", (49, 97)),
    ("3-4 Tafisa Casting at First Light_M2007", (49, 97)),
    ("3-4 AraucoPrism-SF210-Antique-White-Suede", (49, 97)),
    ("3-4 Arauco Prism SF210 Antique White", (49, 97)),
    ("3-4 Am Lam Wh", (49, 97)),
    ("1 in Am Lam Wh", (49, 97)),
    ("1-4 Egger Classic White", (61, 109)),
    ("1-4 Tafisa Apris Ski_M2015", (49, 97)),
    ("1-4 Tafisa Classic White", (49, 97)),
    ("White 1-4 Vinyl", (49, 97)),
])
def test_a_rules_line_gives_its_size(folder, size):
    assert expected_size(folder) == size


def test_a_folder_name_is_read_whatever_its_case():
    assert expected_size("3-4 EGGER classic white") == (49, 97)
    assert expected_size("1-4 tafisa river rock") == (49, 97)


@pytest.mark.parametrize("folder", [
    "New folder", "GCode", "Testerson_101426", "", " ",
    # Other Egger colours really sit on either size: only RULES' lines can be asked.
    "3-4 Egger Grey Textile", "3-4 Egger Beige Textile", "3-4 Egger Light Vicenza Oak",
    "3-4 Egger Dust Grey", "3-4 Egger Black", "3-4 Egger White", "1-4 Egger Beige Textile",
    "1-4 Egger White", "1 in Egger Beige Textile",
    # Names RULES has no line for.
    "3-4 Charcoal", "3-4 Made Up Walnut", "1-4 Made Up Walnut", "1-4 Am Lam Wh",
    "1-4 Am Lam AMWH", "3-4 Casting First Light", "1 in Tafisa Apris Ski",
    "1 in Arauco Prism", "1-4 Arauco",
    # A thickness that isn't the name's start.
    "_3-4 Egger Classic White", "Old 3-4 Am Lam Wh", "Egger Classic White", "Tafisa 3-4",
])
def test_any_other_folder_name_has_no_rule(folder):
    assert expected_size(folder) is None
    finding = size_finding(folder, [tap((60.75, 108.75))])
    assert finding.code == "no_rule"
    assert finding.programs == ()


# ----- what a program does to a finding


def test_a_program_inside_the_size_gives_nothing_past():
    assert size_finding(WHITE, [tap((48.75, 96.75))]) == SizeFinding("nothing_past", ())


def test_nothing_past_reads_as_a_limit_and_never_as_a_pass():
    finding = size_finding(WHITE, [tap((48.75, 96.75))])
    assert finding.reads == "nothing past 49x97"
    for word in ("ok", "right", "correct", "fits", "pass", "checked", "good"):
        assert word not in finding.reads


def test_a_small_program_on_the_big_sheet_rule_says_nothing_past_not_right_sheet():
    finding = size_finding("3-4 Egger Black U999 ST19 Woodgrain", [tap((10.0, 10.0))])
    assert finding.code == "nothing_past"
    assert finding.reads == "nothing past 61x109"


def test_a_program_that_fits_a_smaller_sheet_than_the_rule_isnt_flagged():
    """The other way round is a lower bound too: a 49x97 program under a 61x109 rule."""
    assert size_finding("1-4 Egger Classic White", [tap((48.75, 96.75))]).code == "nothing_past"


@pytest.mark.parametrize("reach, past", [
    ((48.75, 96.75), False),
    ((48.9375, 97.0), False),    # a real flip pass
    ((49.0, 97.0), False),
    ((49.01, 97.0), False),      # the 0.01in allowance
    ((49.0, 97.01), False),
    ((49.0101, 97.0), True),
    ((49.0, 97.0101), True),
    ((49.02, 96.0), True),
    ((48.0, 97.02), True),
    ((60.75, 108.75), True),
    ((45.6206, 108.6245), True),  # past on one axis only
    ((60.75, 50.0), True),
])
def test_a_program_is_past_only_beyond_the_allowance_on_either_axis(reach, past):
    finding = size_finding(WHITE, [tap(reach)])
    assert finding.code == ("past_size" if past else "nothing_past")


def test_the_big_sheet_has_the_same_allowance():
    assert size_finding("1-4 Egger Classic White", [tap((61.01, 109.01))]).code == "nothing_past"
    assert size_finding("1-4 Egger Classic White", [tap((61.0101, 109.0))]).code == "past_size"


def test_past_size_names_each_program_past_it_and_no_other():
    finding = size_finding(WHITE, [tap((48.75, 96.75), "TESS01.TAP"),
                                   tap((60.75, 108.75), "TESS075x9.TAP"),
                                   tap((10.0, 10.0), "TESHooks.TAP"),
                                   tap((49.5, 96.0), "TESS08.TAP")])
    assert finding.code == "past_size"
    assert finding.programs == ("TESS075x9.TAP", "TESS08.TAP")
    assert finding.reads == "past 49x97"


def test_a_merged_program_past_its_size_is_found():
    merged = dataclasses.replace(tap((60.75, 108.75), "Tes1-3.tap"), family="merged")
    assert size_finding(WHITE, [merged]) == SizeFinding("past_size", ("Tes1-3.tap",))


def test_a_program_with_no_cutting_move_is_not_past():
    assert size_finding(WHITE, [tap(None)]).code == "nothing_past"


def test_no_programs_is_nothing_past():
    assert size_finding(WHITE, []).code == "nothing_past"


def test_mixed_sizes_are_each_judged_alone():
    finding = size_finding("1-4 Egger Classic White", [tap((60.75, 108.75)), tap((48.75, 96.75))])
    assert finding.code == "nothing_past"


# ----- a program that can't be measured


def test_a_program_not_in_inches_could_not_be_read():
    """Its numbers aren't inches, so "nothing past" would be a guess, and past would be too."""
    finding = size_finding(WHITE, [tap((1200.0, 2400.0), "TESS01.TAP", inches=False),
                                   tap((48.75, 96.75), "TESS02.TAP")])
    assert finding == SizeFinding("could_not_read", ("TESS01.TAP",))
    assert "could not verify" in finding.reads


def test_a_half_written_program_could_not_be_read():
    finding = size_finding(WHITE, [tap((10.0, 10.0), "TESS01.TAP", complete=False)])
    assert finding == SizeFinding("could_not_read", ("TESS01.TAP",))


def test_a_half_written_program_already_past_its_size_is_past():
    """Reach only grows, so what it has already cut is enough to know."""
    finding = size_finding(WHITE, [tap((60.75, 108.75), "TESS01.TAP", complete=False)])
    assert finding == SizeFinding("past_size", ("TESS01.TAP",))


def test_past_comes_before_could_not_read():
    finding = size_finding(WHITE, [tap((60.75, 108.75), "TESS01.TAP"),
                                   tap((1.0, 1.0), "TESS02.TAP", inches=False)])
    assert finding == SizeFinding("past_size", ("TESS01.TAP",))


def test_no_rule_comes_before_any_program():
    assert size_finding("New folder", [tap((1.0, 1.0), inches=False)]).code == "no_rule"
    assert size_finding("New folder", []).reads.startswith("could not verify")


def test_the_finding_cant_be_changed_after_it_is_made():
    with pytest.raises(dataclasses.FrozenInstanceError):
        size_finding(WHITE, []).code = "past_size"  # type: ignore[misc]


def test_every_line_has_a_prefix_a_size_and_no_duplicates():
    prefixes = [line.prefix.casefold() for line in RULES_LINES]
    assert len(set(prefixes)) == len(prefixes)
    assert {line.size for line in RULES_LINES} == {(49.0, 97.0), (61.0, 109.0)}


# ----- real programs: by index, printing no name


def _real(**match: str) -> tuple[list[dict], list[Tap]]:
    pairs = real_programs(**match)
    return [entry for entry, _ in pairs], real_taps(**match)


def _in_material(prefix: str, **match: str) -> tuple[str, list[dict], list[Tap]]:
    """One material's fixtures, and its folder name, by the start of that name."""
    entries, taps = _real(**match)
    kept = [(e, t) for e, t in zip(entries, taps) if e["material"].startswith(prefix)]
    assert kept, f"no {prefix} programs for {match}"
    assert len({e["material"] for e, _ in kept}) == 1
    return kept[0][0]["material"], [e for e, _ in kept], [t for _, t in kept]


@pytest.mark.fixtures
def test_case_cs_quarter_inch_on_the_big_sheet_gives_nothing_past():
    folder, _, taps = _in_material("1-4", case="C", row="fixed")
    assert all(t.reach[0] > 60 and t.reach[1] > 108 for t in taps)   # really on 61x109
    finding = size_finding(folder, taps)
    assert finding.code == "nothing_past"
    assert finding.reads == "nothing past 61x109"


@pytest.mark.fixtures
def test_case_cs_three_quarter_gives_nothing_past():
    folder, _, taps = _in_material("3-4", case="C", row="fixed")
    assert len(taps) == 15
    assert size_finding(folder, taps).code == "nothing_past"


@pytest.mark.fixtures
def test_case_bs_three_quarter_gives_past_size_naming_one_program():
    folder, entries, taps = _in_material("3-4 Egger", case="B", row="hand_added")
    assert len(taps) == 12
    finding = size_finding(folder, taps)
    assert finding.code == "past_size"
    assert len(finding.programs) == 1
    (named,) = [e["index"] for e in entries if e["name"] == finding.programs[0]]
    (expected,) = [e["index"] for e, t in zip(entries, taps) if t.reach == (60.75, 108.75)]
    assert named == expected


@pytest.mark.fixtures
def test_case_bs_second_material_gives_nothing_past():
    folder, _, taps = _in_material("3-4 Tafisa", case="B", row="hand_added")
    assert size_finding(folder, taps).code == "nothing_past"


@pytest.mark.fixtures
def test_case_hs_three_quarter_names_every_program_past_its_size():
    """Measured: the two 4x10 sheets, each re-posted under a plain name, the verticals cut on them
    and the merged program of the two. The build spec's "one program" isn't what this folder holds
    (reported to Sulaiman): the count is what is on the drive, and the test keeps it true."""
    folder, entries, taps = _in_material("3-4", case="H", row="hand_added")
    assert len(taps) == 21
    finding = size_finding(folder, taps)
    assert finding.code == "past_size"
    named = [e["index"] for e in entries if e["name"] in finding.programs]
    long_ones = [e["index"] for e, t in zip(entries, taps) if t.reach[1] > 97.01]
    assert named == long_ones
    assert len(named) == 7
    assert all(t.reach[0] <= 49.01 for t in taps)


@pytest.mark.fixtures
def test_case_as_four_programs_give_nothing_past():
    folder, _, taps = _in_material("3-4", case="A", row="sized_49")
    assert len(taps) == 4
    assert size_finding(folder, taps).code == "nothing_past"


@pytest.mark.fixtures
@pytest.mark.parametrize("case", ["D", "F", "G"])
def test_the_other_white_jobs_give_nothing_past(case):
    entries, taps = _real(case=case)
    kept = [t for e, t in zip(entries, taps) if e["row"] != "families"]
    assert size_finding(WHITE, kept).code == "nothing_past"


@pytest.mark.fixtures
def test_the_whole_drive(capsys):
    """Optional and slow. The counts go to the screen, with no folder's name."""
    ng_jobs = os.environ.get("NGR_NG_JOBS")
    if not ng_jobs:
        pytest.skip("slow: reads every program on the drive (set NGR_NG_JOBS to run it)")
    folders: collections.defaultdict[str, list[Tap]] = collections.defaultdict(list)
    for folder, _, files in os.walk(ng_jobs):
        for file in files:
            if file.lower().endswith(".tap"):
                folders[folder].append(read_tap(file, (Path(folder) / file).read_bytes()))
    codes = collections.Counter(size_finding(Path(folder).name, taps).code
                                for folder, taps in folders.items())
    programs = sum(len(taps) for folder, taps in folders.items()
                   if size_finding(Path(folder).name, taps).code != "no_rule")
    with capsys.disabled():
        print(f"\n{len(folders)} material folders: {sorted(codes.items())}; "
              f"{programs} programs have a rule")
    assert codes["past_size"] > 0       # nothing past anywhere: the check has gone blind
    assert codes["nothing_past"] > codes["past_size"]
