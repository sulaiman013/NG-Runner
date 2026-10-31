"""
decide(), the nest guard's safety core (P1b build spec 5.3): every row of its table, first match
wins, on evidence built by hand, with the size rule stood in for here so these tests hold whatever
the real table in guards/sheets.py says. What can never happen is proven over every combination of
evidence, not on a few examples: no NEST_CHECKED without a nest, both tools and a design match, so
nothing reaches it on today's data. Job names are synthetic.
"""
from __future__ import annotations

import itertools
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from ngrunner.guards import nest_state
from ngrunner.guards.facts import (
    BR_CAM_NEST, DESIGN_MATCHES, SHEET_SIZE, CheckedFiles, DesignMatch, OptFacts, OptSheet,
    ToolVerdict,
)
from ngrunner.guards.nest_state import (
    MaterialEvidence, NestState, Verdict, decide, doubled_programs, expected_sheet,
    past_size_programs, sheet_of,
)
from ngrunner.guards.labels import LabelsMatch
from ngrunner.guards.tap import NearPair, Tap
from ngrunner.messages import templates as t
from ngrunner.messages.lint import check_line
from ngrunner.store import (
    NEST_BASES, NEST_STATES, add_card, add_clearance, add_nest, answer_card, cleared_programs,
    get_nest, open_store,
)

CLASSIC = "3-4 Classic White"         # the stand-in rule: 49x97
QUARTER = "1-4 Classic White"         # the stand-in rule: 61x109
NO_RULE = "New folder"
JOB = "Testerson_101426"
AT = datetime(2026, 10, 31, 4, 42, tzinfo=timezone.utc)      # 9:42 PM Pacific
FITS_49 = (48.75, 96.75)
FITS_61 = (60.75, 108.75)
FITS_4X10 = (48.75, 120.75)
PAST_ALL = (60.75, 120.75)


def sha(mark: int) -> str:
    return f"{mark:064x}"


OPT_SHA, ROOM_SHA = sha(1000), sha(1001)
ROOMS = (("Room1.des", ROOM_SHA),)
PAIR = NearPair(tool="T6", depth=0.13, apart_in=0.001, x=10.0, y=20.0)


def tap(name: str = "TESS01.TAP", *, mark: int = 1, reach: tuple[float, float] | None = FITS_49,
        pairs: int = 0, complete: bool = True, inches: bool = True,
        family: str = "sheet", sheets: int = 1) -> Tap:
    """A program as tap.read_tap describes one, made by hand."""
    return Tap(name=name, sha256=sha(mark), size=2048, inches=inches, complete=complete,
               family=family, reach=reach, holes=120, near_pairs=(PAIR,) * pairs, sheets=sheets)


class StandInSheets:
    """Just what decide needs of guards/sheets.py (build spec 5.2), with a fixed table."""

    TABLE = {CLASSIC: (49.0, 97.0), QUARTER: (61.0, 109.0)}
    OVER_IN = 0.01

    @classmethod
    def expected_size(cls, material_folder: str) -> tuple[float, float] | None:
        return cls.TABLE.get(material_folder)

    @classmethod
    def size_finding(cls, material_folder: str, taps) -> SimpleNamespace:
        rule = cls.TABLE.get(material_folder)
        if rule is None:
            return SimpleNamespace(code="no_rule", programs=())
        past = tuple(p.name for p in taps if p.reach is not None and (
            p.reach[0] > rule[0] + 0.01 or p.reach[1] > rule[1] + 0.01))
        return SimpleNamespace(code="past_size" if past else "nothing_past", programs=past)


@pytest.fixture(autouse=True)
def stand_in_size_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nest_state, "sheets", StandInSheets)


def checked_files(taps=(), *, opt: str = OPT_SHA, rooms=ROOMS) -> CheckedFiles:
    return CheckedFiles(opt_sha256=opt, manifest=tuple((p.name, p.sha256) for p in taps),
                        room_hashes=rooms)


NESTED = (OptSheet(width_mm=1244.6, length_mm=2463.8, quan=1, flipped=False),)


def nested_opt(sha256: str = OPT_SHA, *, parts: int = 40, sheets=NESTED) -> OptFacts:
    return OptFacts(sha256=sha256, parts=parts, sheets=sheets)


def tool(name: str = SHEET_SIZE, code: str = "passed", line: str = "",
         checked: CheckedFiles | None = None) -> ToolVerdict:
    return ToolVerdict(tool=name, code=code, line=line, checked=checked)


def evidence(*taps: Tap, material: str = CLASSIC, **fields) -> MaterialEvidence:
    return MaterialEvidence(material=material, taps=tuple(taps), seen_at_install=False,
                            read_at=AT, **fields)


def a_pass(*taps: Tap, **over) -> MaterialEvidence:
    """Evidence that does reach NEST_CHECKED; a test changes one thing about it."""
    taps = taps or (tap(),)
    on = checked_files(taps)
    fields = dict(opt=nested_opt(), tools=(tool(SHEET_SIZE, checked=on),
                                           tool(BR_CAM_NEST, checked=on)),
                  design=DesignMatch(DESIGN_MATCHES, checked=on), room_hashes=ROOMS)
    return evidence(*taps, **{**fields, **over})


# ----- row 1: seen at install and unchanged


@pytest.mark.parametrize("kind, fields", [
    ("clean", dict(taps=(tap(),))),
    ("doubled", dict(taps=(tap(pairs=8),))),
    ("past its size", dict(taps=(tap(reach=FITS_61),))),
    ("both", dict(taps=(tap(reach=FITS_61, pairs=8),))),
    ("no programs", dict(taps=())),
    ("a failed tool", dict(taps=(tap(),), tools=(tool(code="failed", line="A fault."),))),
    ("parts and no sheets", dict(taps=(tap(),), opt=nested_opt(sheets=()))),
])
def test_row_1_a_material_seen_at_install_raises_no_alarm_whatever_it_holds(kind, fields) -> None:
    verdict = decide(MaterialEvidence(material=CLASSIC, seen_at_install=True, **fields))

    assert verdict.state == NestState.NOT_CHECKED, kind
    assert verdict.reasons == ("Posted before NG Runner; not checked",)
    assert verdict.basis == "programs"


def test_row_1_a_material_seen_at_install_is_never_a_pass_even_with_every_check_passing() -> None:
    assert decide(replace(a_pass(), seen_at_install=True)).state == NestState.NOT_CHECKED


# ----- what "new" means: an old program beside a new one is still old


def seen(*taps: Tap) -> frozenset[tuple[str, str]]:
    return frozenset((p.name, p.sha256) for p in taps)


def test_an_old_doubled_program_beside_a_new_clean_one_raises_no_alarm() -> None:
    # A re-cut posted beside programs from before Jim's 10-20 fix: doubled holes can't be cleared,
    # so an alarm here would stay red for good over a program already cut.
    old = tap("TESS01.TAP", mark=1, pairs=8)
    new = tap("TESSRM1.TAP", mark=2)

    verdict = decide(evidence(old, new, before_install=seen(old)))

    assert verdict.state == NestState.NOT_CHECKED
    assert verdict.reasons == (t.programs_read_note(at=AT, sheet="49x97"),)


def test_an_old_program_past_its_size_beside_a_new_clean_one_raises_no_alarm() -> None:
    old = tap("TESS01.TAP", mark=1, reach=FITS_61)

    verdict = decide(evidence(old, tap("TESSRM1.TAP", mark=2),
                              before_install=seen(old)))

    assert verdict.state == NestState.NOT_CHECKED


def test_an_old_program_that_changes_is_new_again_since_its_hash_is_not_the_one_seen() -> None:
    earlier = tap("TESS01.TAP", mark=1)
    reposted = tap("TESS01.TAP", mark=2, pairs=8)

    verdict = decide(evidence(reposted, before_install=seen(earlier)))

    assert verdict.state == NestState.DO_NOT_CUT


def test_the_same_bytes_posted_under_a_new_name_are_a_new_post() -> None:
    old = tap("TESS01.TAP", mark=1, pairs=8)
    copied = tap("TESSRM1.TAP", mark=1, pairs=8)

    verdict = decide(evidence(old, copied, before_install=seen(old)))

    assert verdict.state == NestState.DO_NOT_CUT
    assert verdict.reasons == ("8 cam holes are doubled in program TESSRM1",)


def test_a_new_doubled_program_beside_an_old_clean_one_still_alarms_and_names_only_it() -> None:
    old = tap("TESS01.TAP", mark=1)
    new = tap("TESS02.TAP", mark=2, pairs=3)

    verdict = decide(evidence(old, new, before_install=seen(old)))

    assert verdict.reasons == ("3 cam holes are doubled in program TESS02",)


def test_programs_all_from_before_install_read_as_that_even_when_not_told_so_by_the_flag() -> None:
    old = tap(mark=1, pairs=8)

    verdict = decide(evidence(old, before_install=seen(old)))

    assert verdict.state == NestState.NOT_CHECKED
    assert verdict.reasons == (t.POSTED_BEFORE,)


# ----- row 2: no program and no .opt


def test_row_2_no_program_and_no_nest_is_not_posted_with_no_line() -> None:
    verdict = decide(evidence())

    assert verdict.state == NestState.NOT_POSTED
    assert verdict.reasons == ()
    assert verdict.basis == "programs"


def test_row_2_holds_whatever_else_is_said_about_a_material_with_nothing_posted() -> None:
    verdict = decide(evidence(tools=(tool(code="failed", line="A fault."),),
                              design=DesignMatch("some_missing")))

    assert verdict.state == NestState.NOT_POSTED


def test_a_nest_saved_with_no_programs_yet_is_not_not_posted() -> None:
    assert decide(evidence(opt=nested_opt())).state == NestState.NOT_CHECKED


# ----- row 3: a new program has near pairs


def test_row_3_doubled_holes_are_do_not_cut_counted_and_named() -> None:
    verdict = decide(evidence(tap("TESS01.TAP", mark=1), tap("TESS02.TAP", mark=2, pairs=5),
                              tap("TESS03.TAP", mark=3, pairs=3)))

    assert verdict.state == NestState.DO_NOT_CUT
    assert verdict.reasons == ("8 cam holes are doubled in programs TESS02 and TESS03",)
    assert verdict.basis == "programs"


def test_row_3_comes_before_a_nest_with_no_sheets_and_lists_every_fault_to_fix() -> None:
    """The state's order holds, but Jim re-nests once: he hears of all of it at once."""
    verdict = decide(evidence(tap(reach=FITS_61, pairs=1), opt=nested_opt(sheets=()),
                              tools=(tool(code="failed", line="A fault."),)))

    assert verdict.state == NestState.DO_NOT_CUT
    assert verdict.reasons == ("1 cam hole is doubled in program TESS01",
                               "A program is on 61x109 and this colour is 49x97", "A fault.")
    assert verdict.basis == "programs"


def test_doubled_holes_and_a_size_fault_in_two_programs_list_both() -> None:
    verdict = decide(evidence(tap("TESS01.TAP", mark=1, pairs=2),
                              tap("TESS02.TAP", mark=2, reach=FITS_61)))

    assert verdict.state == NestState.DO_NOT_CUT
    assert verdict.reasons == ("2 cam holes are doubled in program TESS01",
                               "A program is on 61x109 and this colour is 49x97")


def test_clearing_the_size_of_both_faults_leaves_the_doubled_holes_alone() -> None:
    doubled = tap("TESS01.TAP", mark=1, pairs=2)
    past = tap("TESS02.TAP", mark=2, reach=FITS_61)

    verdict = decide(evidence(doubled, past, cleared=frozenset({past.sha256})))

    assert verdict.state == NestState.DO_NOT_CUT
    assert verdict.reasons == ("2 cam holes are doubled in program TESS01",)


def test_row_3_comes_before_every_check_passing() -> None:
    assert decide(a_pass(tap(pairs=1))).state == NestState.DO_NOT_CUT


# ----- doubled holes can never be cleared


def test_a_clearance_of_the_programs_own_hash_never_clears_doubled_holes() -> None:
    doubled = tap(pairs=8)

    verdict = decide(evidence(doubled, cleared=frozenset({doubled.sha256})))

    assert verdict.state == NestState.DO_NOT_CUT
    assert verdict.reasons == ("8 cam holes are doubled in program TESS01",)


def test_a_clearance_of_a_size_leaves_doubled_holes_in_the_same_program_standing() -> None:
    both = tap(reach=FITS_61, pairs=2)

    verdict = decide(evidence(both, cleared=frozenset({both.sha256})))

    assert verdict.state == NestState.DO_NOT_CUT
    assert verdict.reasons == ("2 cam holes are doubled in program TESS01",)


# ----- row 4: a new program is past its size, not cleared


def test_row_4_a_program_past_its_size_is_do_not_cut_in_the_build_specs_words() -> None:
    verdict = decide(evidence(tap(reach=FITS_61)))

    assert verdict.state == NestState.DO_NOT_CUT
    assert verdict.reasons == ("A program is on 61x109 and this colour is 49x97",)
    assert verdict.basis == "programs"


def test_row_4_counts_programs_and_gives_each_sheet_its_own_reason() -> None:
    verdict = decide(evidence(tap("TESS01.TAP", mark=1, reach=FITS_61),
                              tap("TESS02.TAP", mark=2, reach=FITS_61),
                              tap("TESS03.TAP", mark=3, reach=FITS_4X10),
                              tap("TESS04.TAP", mark=4)))

    assert verdict.reasons == ("2 programs are on 61x109 and this colour is 49x97",
                               "A program is on 49x121 and this colour is 49x97")


def test_row_4_never_guesses_a_sheet_nothing_fits() -> None:
    verdict = decide(evidence(tap(reach=PAST_ALL)))

    assert verdict.reasons == (
        "A program runs past every sheet NG Runner knows, and this colour is 49x97",)


def test_row_4_is_measured_against_this_materials_own_size() -> None:
    assert decide(evidence(tap(reach=FITS_61), material=QUARTER)).state == NestState.NOT_CHECKED
    verdict = decide(evidence(tap(reach=FITS_4X10), material=QUARTER))
    assert verdict.reasons == ("A program is on 49x121 and this colour is 61x109",)


def test_row_4_comes_before_a_nest_with_no_sheets_and_lists_a_failed_tool_too() -> None:
    verdict = decide(evidence(tap(reach=FITS_61), opt=nested_opt(sheets=()),
                              tools=(tool(code="failed", line="A fault."),)))

    assert verdict.reasons == ("A program is on 61x109 and this colour is 49x97", "A fault.")
    assert verdict.basis == "programs"


def test_row_4_comes_before_every_check_passing() -> None:
    assert decide(a_pass(tap(reach=FITS_61))).state == NestState.DO_NOT_CUT


def test_no_rule_for_a_material_raises_no_size_alarm_and_says_it_could_not_verify() -> None:
    verdict = decide(evidence(tap(reach=FITS_61), material=NO_RULE))

    assert verdict.state == NestState.NOT_CHECKED
    assert verdict.reasons == (t.programs_read_note(at=AT, sheet=None),)


# ----- a clearance is bound to the program's hash, and clears only a size


def test_a_clearance_of_this_hash_clears_this_programs_size() -> None:
    past = tap(reach=FITS_61)

    verdict = decide(evidence(past, cleared=frozenset({past.sha256})))

    assert verdict.state == NestState.NOT_CHECKED
    assert verdict.reasons == (t.programs_read_note(at=AT, sheet="49x97", cleared=1),)


def test_a_clearance_never_survives_a_changed_hash() -> None:
    cleared = tap("TESS07.TAP", mark=7, reach=FITS_61)
    reposted = tap("TESS07.TAP", mark=8, reach=FITS_61)   # same name, same size, other bytes

    assert decide(evidence(cleared, cleared=frozenset({cleared.sha256}))).state == (
        NestState.NOT_CHECKED)
    assert decide(evidence(reposted, cleared=frozenset({cleared.sha256}))).state == (
        NestState.DO_NOT_CUT)


def test_a_clearance_of_one_program_leaves_another_past_its_size_standing() -> None:
    cleared = tap("TESS07.TAP", mark=7, reach=FITS_61)
    other = tap("TESS08.TAP", mark=8, reach=FITS_61)

    verdict = decide(evidence(cleared, other, cleared=frozenset({cleared.sha256})))

    assert verdict.state == NestState.DO_NOT_CUT
    assert verdict.reasons == ("A program is on 61x109 and this colour is 49x97",)
    assert past_size_programs(evidence(cleared, other, cleared=frozenset({cleared.sha256}))) == (
        other,)


def test_a_clearance_does_not_silence_a_failed_tool() -> None:
    past = tap(reach=FITS_61)

    verdict = decide(evidence(past, cleared=frozenset({past.sha256}),
                              tools=(tool(code="failed", line="The nest has a fault."),)))

    assert verdict.state == NestState.DO_NOT_CUT
    assert verdict.reasons == ("The nest has a fault.",)


def test_a_clearance_does_not_make_a_pass_of_anything() -> None:
    past = tap(reach=FITS_61)

    verdict = decide(evidence(past, cleared=frozenset({past.sha256}), opt=nested_opt()))

    assert verdict.state == NestState.NOT_CHECKED


def test_a_cleared_program_can_still_reach_a_pass_when_every_check_passes() -> None:
    # BUILD allows mixed sizes, so the question is only whether a size is expected or cleared.
    past = tap(reach=FITS_61)

    assert decide(a_pass(past, cleared=frozenset({past.sha256}))).state == NestState.NEST_CHECKED


# ----- row 5: a tool says it failed


def test_row_5_a_failed_tool_is_do_not_cut_in_the_tools_own_fixed_line() -> None:
    verdict = decide(evidence(tap(), tools=(
        tool(SHEET_SIZE, "failed", "The nest is on a sheet its colour doesn't use."),)))

    assert verdict.state == NestState.DO_NOT_CUT
    assert verdict.reasons == ("The nest is on a sheet its colour doesn't use.",)
    assert verdict.basis == "nest"


def test_row_5_each_failed_tool_gets_its_line() -> None:
    verdict = decide(evidence(tap(), tools=(tool(SHEET_SIZE, "failed", "Sheet fault."),
                                            tool(BR_CAM_NEST, "failed", "Cam fault."))))

    assert verdict.reasons == ("Sheet fault.", "Cam fault.")


def test_row_5_a_failed_tool_with_no_line_still_says_so() -> None:
    verdict = decide(evidence(tap(), tools=(tool(SHEET_SIZE, "failed", "  "),)))

    assert verdict.state == NestState.DO_NOT_CUT
    assert verdict.reasons == ("A check of the nest failed",)


def test_row_5_comes_before_a_nest_with_no_sheets_and_before_another_tool_passing() -> None:
    verdict = decide(evidence(tap(), opt=nested_opt(sheets=()), tools=(
        tool(SHEET_SIZE, "failed", "Sheet fault."), tool(BR_CAM_NEST, "passed"))))

    assert verdict.state == NestState.DO_NOT_CUT
    assert verdict.reasons == ("Sheet fault.",)


def test_row_5_a_failure_from_files_since_changed_still_stands_until_it_is_run_again() -> None:
    # Never a pass on its way out: only a fresh run of the tool can take the alarm down.
    stale = checked_files((tap(mark=1),))
    verdict = decide(evidence(tap(mark=2), tools=(tool(SHEET_SIZE, "failed", "Sheet fault.",
                                                       checked=stale),)))

    assert verdict.state == NestState.DO_NOT_CUT


# ----- row 6: .opt has parts and no sheets


def test_row_6_parts_and_no_sheets_asks_for_the_nest_to_be_saved() -> None:
    verdict = decide(evidence(tap(), opt=nested_opt(sheets=())))

    assert verdict.state == NestState.SAVE_NEST
    assert verdict.reasons == ("Save the nest in the Optimizer so I can check it",)
    assert verdict.basis == "nest"


def test_row_6_holds_with_no_programs_yet() -> None:
    assert decide(evidence(opt=nested_opt(sheets=()))).state == NestState.SAVE_NEST


def test_row_6_comes_before_every_other_check_passing() -> None:
    assert decide(a_pass(opt=nested_opt(sheets=()))).state == NestState.SAVE_NEST


def test_a_nest_with_no_parts_and_no_sheets_is_not_asked_to_be_saved_and_is_no_pass() -> None:
    assert decide(evidence(tap(), opt=nested_opt(parts=0, sheets=()))).state == (
        NestState.NOT_CHECKED)


# ----- row 7: .opt, both tools pass, design matches, all hashes current


def test_row_7_everything_passing_on_the_files_on_disk_is_nest_checked() -> None:
    programs = [tap(f"TESS0{n}.TAP", mark=n) for n in range(1, 7)]

    verdict = decide(a_pass(*programs))

    assert verdict.state == NestState.NEST_CHECKED
    assert verdict.reasons == ("Nest checked 9:42 PM, programs TESS01-06",)
    assert verdict.basis == "nest"


def test_row_7_does_not_care_what_order_a_check_listed_the_files_in() -> None:
    first, second = tap("TESS01.TAP", mark=1), tap("TESS02.TAP", mark=2)
    rooms = (("Room1.des", sha(50)), ("Room2.des", sha(51)))
    on = CheckedFiles(OPT_SHA, ((second.name, second.sha256), (first.name, first.sha256)),
                      tuple(reversed(rooms)))

    verdict = decide(a_pass(first, second, tools=(tool(SHEET_SIZE, checked=on),
                                                  tool(BR_CAM_NEST, checked=on)),
                            design=DesignMatch(DESIGN_MATCHES, checked=on), room_hashes=rooms))

    assert verdict.state == NestState.NEST_CHECKED


ONE = tap()
ON = checked_files((ONE,))
SHEET_OK, CAM_OK = tool(SHEET_SIZE, checked=ON), tool(BR_CAM_NEST, checked=ON)
OTHER_OPT = checked_files((ONE,), opt=sha(2000))
OTHER_PROGRAM = checked_files((tap(mark=2),))
OTHER_NAME = checked_files((tap("TESS09.TAP"),))
EXTRA_PROGRAM = checked_files((ONE, tap("TESS02.TAP", mark=2)))
OTHER_ROOM = checked_files((ONE,), rooms=(("Room1.des", sha(2001)),))
NO_ROOM = checked_files((ONE,), rooms=())
MATCH = DesignMatch(DESIGN_MATCHES, checked=ON)

NOT_A_PASS = {
    "no nest": dict(opt=None),
    "a nest with no parts": dict(opt=nested_opt(parts=0)),
    "the size tool missing": dict(tools=(CAM_OK,)),
    "the cam tool missing": dict(tools=(SHEET_OK,)),
    "no tools at all": dict(tools=()),
    "one tool passing twice": dict(tools=(SHEET_OK, SHEET_OK)),
    "the size tool could not verify": dict(tools=(
        tool(SHEET_SIZE, "could_not_verify", checked=ON), CAM_OK)),
    "the cam tool could not verify": dict(tools=(
        SHEET_OK, tool(BR_CAM_NEST, "could_not_verify", checked=ON))),
    "a tool with a code nobody knows": dict(tools=(tool(SHEET_SIZE, "PASSED", checked=ON), CAM_OK)),
    "a tool with a blank code": dict(tools=(tool(SHEET_SIZE, "", checked=ON), CAM_OK)),
    "a third tool that could not verify": dict(tools=(
        SHEET_OK, CAM_OK, tool("another_check", "could_not_verify", checked=ON))),
    "a tool that says what it ran on nowhere": dict(tools=(tool(SHEET_SIZE), CAM_OK)),
    "the size tool on another nest": dict(tools=(tool(SHEET_SIZE, checked=OTHER_OPT), CAM_OK)),
    "the cam tool on another nest": dict(tools=(SHEET_OK, tool(BR_CAM_NEST, checked=OTHER_OPT))),
    "the size tool on another program": dict(tools=(
        tool(SHEET_SIZE, checked=OTHER_PROGRAM), CAM_OK)),
    "the cam tool on another program": dict(tools=(
        SHEET_OK, tool(BR_CAM_NEST, checked=OTHER_PROGRAM))),
    "a tool on a program of another name": dict(tools=(
        tool(SHEET_SIZE, checked=OTHER_NAME), CAM_OK)),
    "a tool on a program since removed": dict(tools=(
        tool(SHEET_SIZE, checked=EXTRA_PROGRAM), CAM_OK)),
    "the size tool on another room": dict(tools=(tool(SHEET_SIZE, checked=OTHER_ROOM), CAM_OK)),
    "the cam tool on another room": dict(tools=(SHEET_OK, tool(BR_CAM_NEST, checked=OTHER_ROOM))),
    "no design match": dict(design=None),
    "the labels fallback finding everything": dict(design=DesignMatch("all_found", checked=ON)),
    "the labels fallback with no hashes": dict(design=DesignMatch("all_found")),
    "labels missing from the design": dict(design=DesignMatch("some_missing", checked=ON)),
    "labels that could not be read": dict(design=DesignMatch("could_not_read", checked=ON)),
    "a design match with a code nobody knows": dict(design=DesignMatch("MATCHES", checked=ON)),
    "a design match that says what it ran on nowhere": dict(design=DesignMatch(DESIGN_MATCHES)),
    "a design match on another nest": dict(design=DesignMatch(DESIGN_MATCHES, checked=OTHER_OPT)),
    "a design match on another program": dict(design=DesignMatch(DESIGN_MATCHES,
                                                                 checked=OTHER_PROGRAM)),
    "a design match on another room": dict(design=DesignMatch(DESIGN_MATCHES, checked=OTHER_ROOM)),
    "the nest changed since it was checked": dict(opt=nested_opt(sha(2000))),
    "a room changed since it was checked": dict(room_hashes=(("Room1.des", sha(2001)),)),
    "a room added since it was checked": dict(room_hashes=(*ROOMS, ("Room2.des", sha(2002)))),
    "no rooms known": dict(room_hashes=()),
    "no rooms known, and checks that saw none": dict(
        room_hashes=(),
        tools=(tool(SHEET_SIZE, checked=NO_ROOM), tool(BR_CAM_NEST, checked=NO_ROOM)),
        design=DesignMatch(DESIGN_MATCHES, checked=NO_ROOM)),
}


def test_the_evidence_these_cases_change_is_a_pass_to_begin_with() -> None:
    assert decide(a_pass(ONE)).state == NestState.NEST_CHECKED


@pytest.mark.parametrize("fields", NOT_A_PASS.values(), ids=NOT_A_PASS.keys())
def test_row_7_takes_all_of_it_and_any_one_thing_short_is_not_a_pass(fields) -> None:
    verdict = decide(a_pass(ONE, **fields))

    assert verdict.state == NestState.NOT_CHECKED
    assert "not checked" in verdict.reasons[0]


def test_row_7_stands_on_its_own_and_doesnt_lean_on_row_6_having_come_first() -> None:
    # decide can't show this, since row 6 takes a nest with no sheets first: a pass must still need
    # sheets of itself, so moving or removing row 6 could never turn this into one.
    assert nest_state._nest_is_checked(a_pass()) is True
    assert nest_state._nest_is_checked(a_pass(opt=nested_opt(sheets=()))) is False


def test_a_program_changed_since_the_checks_is_not_a_pass() -> None:
    assert decide(a_pass(ONE, tools=(SHEET_OK, CAM_OK), design=MATCH)).state == (
        NestState.NEST_CHECKED)
    reposted = tap(mark=2)

    assert decide(a_pass(reposted, tools=(SHEET_OK, CAM_OK), design=MATCH)).state == (
        NestState.NOT_CHECKED)


def test_a_program_added_since_the_checks_is_not_a_pass() -> None:
    assert decide(a_pass(ONE, tap("TESS02.TAP", mark=2), tools=(SHEET_OK, CAM_OK),
                         design=MATCH)).state == NestState.NOT_CHECKED


def test_no_programs_is_not_a_pass_however_the_checks_read() -> None:
    on = checked_files(())

    verdict = decide(evidence(opt=nested_opt(), tools=(tool(SHEET_SIZE, checked=on),
                                                       tool(BR_CAM_NEST, checked=on)),
                              design=DesignMatch(DESIGN_MATCHES, checked=on), room_hashes=ROOMS))

    assert verdict.state == NestState.NOT_CHECKED


@pytest.mark.parametrize("bad", [tap(complete=False), tap(inches=False)],
                         ids=["half written", "not in inches"])
def test_a_program_that_cant_be_trusted_is_not_a_pass(bad: Tap) -> None:
    on = checked_files((bad,))

    verdict = decide(a_pass(bad, tools=(tool(SHEET_SIZE, checked=on),
                                        tool(BR_CAM_NEST, checked=on)),
                            design=DesignMatch(DESIGN_MATCHES, checked=on)))

    assert verdict.state == NestState.NOT_CHECKED


# ----- what can never happen, over every combination of evidence


OPTS = {"no nest": None, "no parts": nested_opt(parts=0, sheets=()),
        "unsaved": nested_opt(sheets=()), "nested": nested_opt(),
        "changed since": nested_opt(sha(3000))}
TOOL_CODES = {
    "absent": None, "passed": ("passed", ON), "stale": ("passed", OTHER_OPT),
    "unbound": ("passed", None), "unsure": ("could_not_verify", ON), "failed": ("failed", ON),
    "garbled": ("OK", ON),
}
DESIGNS = {"absent": None, "matches": MATCH,
           "stale": DesignMatch(DESIGN_MATCHES, checked=OTHER_OPT),
           "unbound": DesignMatch(DESIGN_MATCHES),
           "all_found": DesignMatch("all_found", checked=ON),
           "some_missing": DesignMatch("some_missing", checked=ON),
           "could_not_read": DesignMatch("could_not_read", checked=ON),
           "garbled": DesignMatch("yes", checked=ON)}
PROGRAMS = {"none": (), "clean": (ONE,), "doubled": (tap(pairs=2),), "past": (tap(reach=FITS_61),),
            "half written": (tap(complete=False),)}
ROOM_SETS = {"none": (), "current": ROOMS, "changed": (("Room1.des", sha(2001)),)}


def every_evidence():
    for opt, size_tool, cam_tool, design, programs, rooms in itertools.product(
        OPTS, TOOL_CODES, TOOL_CODES, DESIGNS, PROGRAMS, ROOM_SETS,
    ):
        tools = []
        for name, code in ((SHEET_SIZE, size_tool), (BR_CAM_NEST, cam_tool)):
            if TOOL_CODES[code] is not None:
                tools.append(tool(name, TOOL_CODES[code][0], checked=TOOL_CODES[code][1]))
        evidence_here = evidence(*PROGRAMS[programs], opt=OPTS[opt], tools=tuple(tools),
                                 design=DESIGNS[design], room_hashes=ROOM_SETS[rooms])
        yield (opt, size_tool, cam_tool, design, programs, rooms), evidence_here


def test_nest_checked_is_reached_by_exactly_one_combination_of_all_of_them() -> None:
    checked, total = [], 0
    for labels, ev in every_evidence():
        total += 1
        if decide(ev).state == NestState.NEST_CHECKED:
            checked.append(labels)

    assert total == 5 * 7 * 7 * 8 * 5 * 3
    assert checked == [("nested", "passed", "passed", "matches", "clean", "current")]


def test_with_todays_data_nothing_can_reach_nest_checked() -> None:
    # No .opt can be read yet, and the labels fallback is the only design evidence there is: so
    # no evidence possible today, whatever the tools and labels said, reaches a pass.
    today = [ev for labels, ev in every_evidence() if labels[0] == "no nest"]
    labels_only = [ev for labels, ev in every_evidence()
                   if labels[3] in ("absent", "all_found", "some_missing", "could_not_read")]

    assert today and labels_only
    assert {decide(ev).state for ev in today} == {
        NestState.NOT_POSTED, NestState.NOT_CHECKED, NestState.DO_NOT_CUT}
    assert all(decide(ev).state != NestState.NEST_CHECKED for ev in (*today, *labels_only))


def test_a_pass_never_comes_with_a_fault_standing() -> None:
    for labels, ev in every_evidence():
        verdict = decide(ev)
        if labels[4] in ("doubled", "past") or "failed" in labels[1:3]:
            assert verdict.state not in (NestState.NEST_CHECKED, NestState.SAVE_NEST), labels


def test_unknown_evidence_is_never_a_pass_and_never_an_alarm_on_its_own() -> None:
    for labels, ev in every_evidence():
        unknown = "garbled" in labels or "unsure" in labels[1:3]
        no_fault = labels[4] == "clean" and "failed" not in labels[1:3]
        if unknown and no_fault and labels[0] != "unsaved":
            assert decide(ev).state == NestState.NOT_CHECKED, labels


# ----- row 8: anything else


def test_row_8_a_clean_read_says_what_it_read_and_that_the_nest_is_not_checked() -> None:
    verdict = decide(evidence(tap()))

    assert verdict.state == NestState.NOT_CHECKED
    assert verdict.reasons == (
        "Programs read 9:42 PM, nothing past 49x97, 0 doubled holes; the nest itself is not"
        " checked",)
    assert verdict.basis == "programs"


def test_row_8_never_says_on_the_right_sheet() -> None:
    assert " on 49x97" not in decide(evidence(tap())).reasons[0]


def test_row_8_says_merged_programs_of_several_sheets_were_not_compared_for_doubled_holes() -> None:
    verdict = decide(evidence(tap("TESS1-2.tap", family="merged", sheets=2)))

    assert verdict.reasons == (t.programs_read_note(at=AT, sheet="49x97", merged=1),)


def test_row_8_leaves_out_a_merged_program_of_one_sheet_since_its_holes_were_compared() -> None:
    verdict = decide(evidence(tap("TESS1.tap", family="merged", sheets=1)))

    assert verdict.reasons == (t.programs_read_note(at=AT, sheet="49x97"),)


def test_row_8_with_a_nest_and_no_programs_says_so() -> None:
    assert decide(evidence(opt=nested_opt())).reasons == (t.NO_PROGRAMS_YET,)


@pytest.mark.parametrize("bad", [tap("TESS02.TAP", mark=2, complete=False),
                                 tap("TESS02.TAP", mark=2, inches=False)],
                         ids=["half written", "not in inches"])
def test_a_program_that_cant_be_read_raises_nothing_and_is_named(bad: Tap) -> None:
    worse = replace(bad, near_pairs=(PAIR,), reach=FITS_61)

    verdict = decide(evidence(tap(mark=1), worse))

    assert verdict.state == NestState.NOT_CHECKED
    assert verdict.reasons == ("Could not read program TESS02 yet; the nest itself is not checked",)


def test_a_fault_in_a_program_that_can_be_read_still_stands_beside_one_that_cant() -> None:
    verdict = decide(evidence(tap("TESS01.TAP", mark=1, pairs=2),
                              tap("TESS02.TAP", mark=2, complete=False)))

    assert verdict.state == NestState.DO_NOT_CUT


def test_row_8_passes_on_what_a_tool_could_not_verify() -> None:
    verdict = decide(evidence(tap(), tools=(
        tool(SHEET_SIZE, "could_not_verify", "Could not verify the sheet size."),)))

    assert verdict.state == NestState.NOT_CHECKED
    assert verdict.reasons[1:] == ("Could not verify the sheet size.",)


@pytest.mark.parametrize("code", ["some_missing", "could_not_read"])
def test_labels_that_dont_fit_read_could_not_verify_and_raise_nothing(code: str) -> None:
    labels = LabelsMatch(code, missing=("R1C9 FEnd",) if code == "some_missing" else ())
    verdict = decide(evidence(tap(), labels=labels))

    assert verdict.state == NestState.NOT_CHECKED
    assert verdict.reasons[1:] == (labels.note,)
    assert "could not verify" in labels.note.casefold()


def test_labels_that_all_fit_add_nothing_and_are_no_pass() -> None:
    verdict = decide(evidence(tap(), labels=LabelsMatch("all_found", compared=3)))

    assert verdict.state == NestState.NOT_CHECKED and len(verdict.reasons) == 1


def test_with_every_check_passing_the_labels_can_stand_in_for_no_design_match() -> None:
    ev = replace(a_pass(tap()), design=None, labels=LabelsMatch("all_found", compared=3))

    assert decide(ev).state == NestState.NOT_CHECKED


def test_a_labels_reading_in_the_design_field_by_mistake_is_still_no_pass() -> None:
    ev = replace(a_pass(tap()), design=LabelsMatch("all_found", compared=3))

    assert decide(ev).state == NestState.NOT_CHECKED


def test_a_size_that_could_not_be_read_falls_through_to_not_checked(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """SizeFinding's fourth code: neither a fault nor a pass."""
    from ngrunner.guards.sheets import SizeFinding

    monkeypatch.setattr(StandInSheets, "size_finding", classmethod(
        lambda cls, material, taps: SizeFinding("could_not_read", ("TESS01.TAP",), (49.0, 97.0))))
    verdict = decide(evidence(tap(reach=FITS_61)))

    assert verdict.state == NestState.NOT_CHECKED


def test_row_8_with_no_time_given_leaves_the_time_out() -> None:
    verdict = decide(MaterialEvidence(material=CLASSIC, taps=(tap(),), seen_at_install=False))

    assert verdict.reasons == (t.programs_read_note(at=None, sheet="49x97"),)


# ----- every verdict


def test_decide_is_pure_the_same_evidence_gives_the_same_verdict() -> None:
    ev = a_pass()

    assert decide(ev) == decide(ev)
    assert decide(ev).state == NestState.NEST_CHECKED


def test_the_evidence_and_the_verdict_cant_be_changed_after_the_fact() -> None:
    ev, verdict = evidence(tap()), decide(evidence(tap()))

    with pytest.raises(FrozenInstanceError):
        ev.seen_at_install = True
    with pytest.raises(FrozenInstanceError):
        verdict.state = NestState.NEST_CHECKED
    assert isinstance(verdict, Verdict)


def test_every_verdict_is_on_trial_until_the_wiring_says_otherwise() -> None:
    # decide has no store to count real nests in, so it stays on the side that says less.
    assert {decide(ev).trial for _, ev in every_evidence()} == {True}


def test_every_reason_decide_can_give_passes_the_lint() -> None:
    reasons = {reason for _, ev in every_evidence() for reason in decide(ev).reasons}

    assert len(reasons) > 5
    assert {reason: check_line(reason) for reason in reasons if check_line(reason)} == {}


def test_every_state_a_verdict_can_hold_is_one_the_store_takes() -> None:
    assert list(NEST_STATES) == [state.value for state in NestState]
    assert {decide(ev).basis for _, ev in every_evidence()} == set(NEST_BASES)


def test_a_verdict_is_stored_and_read_back_whole(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ngrunner.db")
    verdict = decide(evidence(tap(pairs=2)))

    add_nest(store, folder=JOB, material=CLASSIC, state=verdict.state, basis=verdict.basis,
             reasons=verdict.reasons, trial=verdict.trial, updated_at=AT)

    stored = get_nest(store, JOB, CLASSIC)
    assert (stored.state, stored.basis, stored.reasons, stored.trial) == (
        "do_not_cut", "programs", verdict.reasons, True)
    assert NestState(stored.state) is NestState.DO_NOT_CUT


# ----- from the store's clearance to the verdict, as the wiring will do it


def cleared_by_jim(store, material: str, program: Tap, *, folder: str = JOB) -> None:
    made = add_card(store, prefix="TES", kind="sheet_size", text="Past its size.",
                    choices=t.SHEET_SIZE_CHOICES, created_at=AT)
    answer_card(store, made.id, answer=t.SHEET_SIZE_CHOICES[0], answer_words="1", answered_by="jim",
                answered_at=AT, channel="board")
    add_clearance(store, folder=folder, material=material, program_sha256=program.sha256,
                  card_id=made.id, at=AT + timedelta(minutes=1))


def test_jims_click_clears_that_program_on_that_job_and_a_repost_asks_again(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ngrunner.db")
    program = tap("TESS07.TAP", mark=7, reach=FITS_61)
    assert decide(evidence(program)).state == NestState.DO_NOT_CUT

    cleared_by_jim(store, CLASSIC, program)

    cleared = cleared_programs(store, JOB, CLASSIC)
    assert decide(evidence(program, cleared=cleared)).state == NestState.NOT_CHECKED
    reposted = tap("TESS07.TAP", mark=8, reach=FITS_61)
    assert decide(evidence(reposted, cleared=cleared)).state == NestState.DO_NOT_CUT


def test_a_click_on_one_job_clears_nothing_on_another(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ngrunner.db")
    program = tap("TESS07.TAP", mark=7, reach=FITS_61)

    cleared_by_jim(store, CLASSIC, program, folder="Fakeley_102026")

    cleared = cleared_programs(store, JOB, CLASSIC)
    assert decide(evidence(program, cleared=cleared)).state == NestState.DO_NOT_CUT


def test_jims_click_leaves_doubled_holes_standing(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ngrunner.db")
    program = tap("TESS07.TAP", mark=7, reach=FITS_61, pairs=4)

    cleared_by_jim(store, CLASSIC, program)

    cleared = cleared_programs(store, JOB, CLASSIC)
    assert cleared == {program.sha256}
    assert decide(evidence(program, cleared=cleared)).state == NestState.DO_NOT_CUT


# ----- what the wiring reads besides the verdict


def test_doubled_programs_are_the_new_ones_with_near_pairs() -> None:
    old = tap("TESS01.TAP", mark=1, pairs=1)
    new = tap("TESS02.TAP", mark=2, pairs=1)
    clean = tap("TESS03.TAP", mark=3)
    half = tap("TESS04.TAP", mark=4, pairs=1, complete=False)

    ev = evidence(old, new, clean, half, before_install=seen(old))

    assert doubled_programs(ev) == (new,)


def test_the_programs_to_ask_about_stand_behind_a_headline_of_doubled_holes() -> None:
    # A material with both faults leads with the holes, and Jim can still be asked about the size.
    doubled = tap("TESS01.TAP", mark=1, pairs=1)
    past = tap("TESS02.TAP", mark=2, reach=FITS_61)

    ev = evidence(doubled, past)

    assert decide(ev).reasons == ("1 cam hole is doubled in program TESS01",
                                  "A program is on 61x109 and this colour is 49x97")
    assert past_size_programs(ev) == (past,)


def test_a_material_seen_at_install_has_nothing_to_ask_about() -> None:
    ev = MaterialEvidence(material=CLASSIC, taps=(tap(reach=FITS_61, pairs=2),),
                          seen_at_install=True)

    assert past_size_programs(ev) == ()
    assert doubled_programs(ev) == ()


@pytest.mark.parametrize("reach, sheet", [
    (FITS_49, "49x97"), ((49.0, 97.0), "49x97"), ((49.01, 97.01), "49x97"),
    ((49.02, 97.0), "61x109"), (FITS_61, "61x109"), (FITS_4X10, "49x121"), (PAST_ALL, None),
    (None, None), ((10.0, 20.0), "49x97"),
])
def test_the_sheet_a_program_reads_as_is_the_smallest_it_fits(reach, sheet) -> None:
    assert sheet_of(tap(reach=reach)) == sheet


def test_a_materials_expected_sheet_reads_as_jim_writes_it() -> None:
    assert expected_sheet(CLASSIC) == "49x97"
    assert expected_sheet(QUARTER) == "61x109"
    assert expected_sheet(NO_RULE) is None


# ----- with the real size table


def test_decide_asks_the_real_size_table(monkeypatch: pytest.MonkeyPatch) -> None:
    from ngrunner.guards import sheets as real

    monkeypatch.setattr(nest_state, "sheets", real)
    three, one = "3-4 Egger Classic White", "1-4 Egger Classic White"
    try:
        real.expected_size(three)
    except NotImplementedError:
        pytest.skip("guards/sheets.py is another part of P1b, not built yet")

    def on(material: str, reach: tuple[float, float]) -> Verdict:
        return decide(MaterialEvidence(material=material, taps=(tap(reach=reach),),
                                       seen_at_install=False))

    assert on(three, FITS_61).state == NestState.DO_NOT_CUT
    assert on(three, FITS_61).reasons == ("A program is on 61x109 and this colour is 49x97",)
    assert on(one, FITS_61).state == NestState.NOT_CHECKED       # 1/4 on 61x109 must pass
    assert "nothing past 61x109" in on(one, FITS_61).reasons[0]
    assert "nothing past 49x97" in on(three, FITS_49).reasons[0]
    no_rule = on("3-4 Charcoal", FITS_61)                        # Q4: an old name has no rule
    assert no_rule.state == NestState.NOT_CHECKED
    assert "could not be verified" in no_rule.reasons[0]
