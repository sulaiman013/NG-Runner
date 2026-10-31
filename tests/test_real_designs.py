"""
The nest-to-design match on the 10 real jobs Jim sent (2026-10-31): Jim's des_cutlist run here, through
toolkit.run_tool, from a copy of his toolkit taken into a temp folder, on staged copies of each job's
rooms (rulings R1 and R2), never in .local-fixtures. All marked `fixtures`: real files never enter git,
jobs are named by case letter only, and these skip when the material is absent.

What each expectation rests on was measured before guards/design.py was written, by separate scripts
reading the same files (the report of 2026-10-31): the 20 nests' parts and operations against the
rooms, and des_cutlist's output against the .bak of every room.
"""
from __future__ import annotations

import hashlib
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ngrunner.clock import FakeClock
from ngrunner.guards import sheets
from ngrunner.guards.design import (
    COUNTER_TOP, match, read_cutlist, read_design, read_nest, room_identity, room_numbers,
)
from ngrunner.guards.facts import (
    BR_CAM_NEST, COULD_NOT_VERIFY, DESIGN_MATCHES, NOT_VERIFIED, SHEET_SIZE, CheckedFiles, ToolVerdict,
)
from ngrunner.guards.nest_state import MaterialEvidence, NestState, decide
from ngrunner.guards.opt import read_opt
from ngrunner.guards.tool_checks import br_cam_nest_by_material, sheet_size_by_material
from ngrunner.messages.lint import check_line
from ngrunner.toolkit import run_tool, take_snapshot
from test_real_nests import CASES, KNOWN_BAD, _toolkit, every_nest, job_folder, nests, printed, programs

pytestmark = pytest.mark.fixtures

CLASSIC = "3-4 Egger Classic White"


@pytest.fixture(scope="module")
def copy(tmp_path_factory):
    """Jim's scripts, copied whole into a temp folder: what run_tool runs from."""
    scripts = _toolkit() / "NG_Automation_scripts"
    if not (scripts / "scripts" / "des_cutlist.py").is_file():
        pytest.skip("Jim's des_cutlist isn't on this PC")
    return take_snapshot(scripts, tmp_path_factory.mktemp("toolkit") / "copy",
                         clock=FakeClock(datetime(2026, 10, 31, tzinfo=timezone.utc)))


def rooms_of(case: str, ending: str = ".des") -> dict[str, bytes]:
    """Each room of a case as Room#.des: from its .des, or from its backup of that ending."""
    folder = job_folder(case)
    return {des.name: (folder / f"{des.stem}{ending}").read_bytes() for des in sorted(folder.glob("Room*.des"))
            if (folder / f"{des.stem}{ending}").is_file()}


def cutlist_run(copy, tmp: Path, rooms: dict[str, bytes]):
    stage = tmp / "rooms"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    for name, data in rooms.items():
        (stage / name).write_bytes(data)
    return run_tool(copy, "scripts/des_cutlist.py", [str(stage)])


@pytest.fixture(scope="module")
def cutlists(copy, tmp_path_factory) -> dict[str, object]:
    tmp = tmp_path_factory.mktemp("staged")
    return {case: cutlist_run(copy, tmp / case, rooms_of(case)) for case in CASES}


# ----- des_cutlist, run here, read whole


def test_des_cutlist_runs_here_and_every_line_it_printed_reads_on_all_10(cutlists) -> None:
    totals = {}
    for case, run in cutlists.items():
        assert (run.exit_code, run.stderr, run.timed_out) == (0, "", False), case
        totals[case] = read_cutlist(run).total

    assert totals == {"A": 37, "B": 103, "C": 89, "D": 15, "E": 104, "F": 16, "G": 46, "H": 83, "I": 22,
                      "J": 317}


def test_the_stand_in_reads_the_same_parts_as_des_cutlist_on_all_10(cutlists) -> None:
    for case in CASES:
        pieces = read_design(rooms_of(case))
        ours = Counter((p.name, round(p.w_mm, 1), round(p.l_mm, 1)) for p in pieces if p.name != COUNTER_TOP)
        theirs = Counter()
        for row in read_cutlist(cutlists[case]).rows:
            if row.name != COUNTER_TOP:
                theirs[(row.name, row.w_mm, row.l_mm)] += row.qty
        assert ours == theirs, case


def test_des_cutlists_colour_is_no_stand_in_for_a_parts_material(cutlists) -> None:
    # Case I: every part is nested on Egger Black, and the cutlist names 15 of 22 by Spring Blossom, the
    # first texture in the room's block, which belongs to its plastic parts. So material is compared
    # only where Jim set one on the part by hand.
    colours = Counter()
    for row in read_cutlist(cutlists["I"]).rows:
        colours[row.colour] += row.qty
    black = read_nest("3-4 Egger Black U999 ST19 Woodgrain", nests("I")["3-4 Egger Black U999 ST19 Woodgrain"])

    assert sorted(colours.values()) == [7, 15]
    assert len(black.pieces) == 22 and len({p.texture for p in black.pieces}) == 1


# ----- a re-save of the same design: the file changes, the cutlist doesn't


def test_the_cutlist_survives_a_resave_and_the_rooms_identity_does_too(copy, cutlists, tmp_path) -> None:
    bytes_differ = identity_differs = rooms_compared = 0
    for case in CASES:
        saved, backup = rooms_of(case), rooms_of(case, ".bak")
        assert set(backup) == set(saved), case
        again = cutlist_run(copy, tmp_path / case, backup)
        assert again.stdout == cutlists[case].stdout, case
        for name in saved:
            rooms_compared += 1
            bytes_differ += saved[name] != backup[name]
            if room_identity(name, saved[name]) != room_identity(name, backup[name]):
                identity_differs += 1
                assert (case, name) == ("G", "Room1.des")       # groove Hide flags on 5 panels

    assert (rooms_compared, bytes_differ, identity_differs) == (30, 20, 1)


def test_case_gs_nest_sides_with_its_saved_design_and_not_its_backup(cutlists) -> None:
    # The one .bak whose design differs: the nest's grooves are the .des's, so it matches the saved
    # design, and would not match the backup's.
    data = nests("G")[CLASSIC]
    saved, backup = rooms_of("G"), {**rooms_of("G"), "Room1.des": rooms_of("G", ".bak")["Room1.des"]}
    cut = read_cutlist(cutlists["G"])

    def against(rooms):
        return match(CLASSIC, [read_nest(CLASSIC, data)], cut, read_design(rooms), room_numbers(rooms),
                     checked=None)

    assert against(saved).code == DESIGN_MATCHES
    assert (against(backup).code, against(backup).parts) == (
        NOT_VERIFIED, ("R1C3", "R1C5", "R1C7", "R1C18", "R1C20"))


# ----- the match on all 20 real nests


def judged(case: str, cutlists) -> dict[str, object]:
    rooms = rooms_of(case)
    every = [read_nest(name, data) for name, data in nests(case).items()]
    cut, design = read_cutlist(cutlists[case]), read_design(rooms)
    return {nest.name: match(nest.name, every, cut, design, room_numbers(rooms), checked=None) for nest in every}


MATCHES = {("B", CLASSIC), ("B", "3-4 Tafisa Sunday Brunch-Inter__M2005"), ("C", "1-4 Egger Classic White"),
           ("C", CLASSIC), ("D", CLASSIC), ("E", "1-4 Tafisa Apris Ski_M2015"),
           ("E", "3-4 Tafisa Apris Ski_M2015"), ("E", "3-4 Tafisa Spring Blossom_L543"), ("F", CLASSIC),
           ("G", CLASSIC)}
NOT_THE_DESIGN = {
    ("A", CLASSIC): {"R1C9", "R1C11"},                          # groove Hide flags, two panels
    ("H", "1 in Am Lam Wh"): {"R1N0"},                          # a counter top of no size in the design
    ("H", "1-4 Egger White"): {"R2C4"},                         # a back of another size
    ("H", "3-4 Am Lam Wh"): {"R2C1", "R2C2"},                   # 4 shelves 1/8in shallower; a line bore
    ("I", "3-4 Egger Black U999 ST19 Woodgrain"): {"R1C2", "R1C4"},   # pull holes hidden on 2 drawers
    ("J", "1 in Am Lam Wh"): set(),
    ("J", "1-4 Tafisa Classic White"): {"R1C7"},                # a back of another size
    ("J", "3-4 Am Lam Wh"): {"R3C1"},                           # line bores 10 mm, not 15
    ("J", CLASSIC): {"R1C2", "R2C1", "R2C3", "R2C5", "R2C13", "R2C15"},   # a shelf; groove Hide flags
}


def test_what_the_match_gives_on_each_of_the_20_real_nests(cutlists) -> None:
    got = {(case, name): found for case in CASES for name, found in judged(case, cutlists).items()}

    assert {place for place, found in got.items() if found.code == DESIGN_MATCHES} == MATCHES
    assert {place for place, found in got.items() if found.code == NOT_VERIFIED} == set(NOT_THE_DESIGN)
    assert {place for place, found in got.items() if found.code == COULD_NOT_VERIFY} == {
        ("I", "3-4 Tafisa Spring Blossom_L543")}               # parts and no sheets: never judged
    for place, tags in NOT_THE_DESIGN.items():
        assert tags <= set(got[place].parts), place
        assert check_line(got[place].note) == [], place
    assert all("Room 4 is in the saved design and not in the nest" in got[place].note
               for place in got if place[0] == "J")
    assert all("3 counter tops are in no nest" in got[place].note for place in got if place[0] == "H")


# ----- Nest checked, with an adopted toolkit assumed: Jim's two checks as he ran them, and the match


def evidence(case: str, material: str, found) -> MaterialEvidence | None:
    taps = programs(case, material)
    if taps is None:
        return None
    facts = read_opt(nests(case)[material])
    rooms = tuple(sorted((name, room_identity(name, data)) for name, data in rooms_of(case).items()))
    now = CheckedFiles(facts.sha256, tuple((t.name, t.sha256) for t in taps), rooms)
    tools = []
    for tool, said in ((SHEET_SIZE, sheet_size_by_material(printed(case, "sheet_size_check"))),
                       (BR_CAM_NEST, br_cam_nest_by_material(printed(case, "br_cam_check_nest")))):
        code, line = said[material]
        tools.append(ToolVerdict(tool, code, line, checked=now))
    design = type(found)(found.code, checked=now, parts=found.parts, note=found.note)
    return MaterialEvidence(material=material, taps=taps, seen_at_install=False, opt=facts,
                            tools=tuple(tools), room_hashes=rooms, design=design)


def test_with_the_match_only_cases_f_and_g_reach_nest_checked(cutlists) -> None:
    # Before it, 6 of 20 did with a match assumed: F, G, I's Egger Black and three of J's. The match
    # stops I's (pull holes hidden in the nest) and J's (Room 4 added after nesting, and more). F and G,
    # nested from an unsaved state on 10-20, have been nested again since from what was then saved
    # (their nests are dated 10-26 and 10-22): their nests are their saved designs.
    reached, alarmed = set(), set()
    for case in CASES:
        for material, found in judged(case, cutlists).items():
            ev = evidence(case, material, found)
            if ev is None:
                continue
            state = decide(ev).state
            if state == NestState.NEST_CHECKED:
                reached.add((case, material))
            if state == NestState.DO_NOT_CUT and found.code == NOT_VERIFIED:
                alarmed.add((case, material))

    assert reached == {("F", CLASSIC), ("G", CLASSIC)}
    assert not reached & KNOWN_BAD
    assert {("I", "3-4 Egger Black U999 ST19 Woodgrain"), ("J", "1 in Am Lam Wh"),
            ("J", "1-4 Tafisa Classic White"), ("J", "3-4 Am Lam Wh")} <= alarmed


def test_the_nests_of_each_real_job_come_from_one_optimizer_run() -> None:
    # Why a nest of the job not read by a pass is no gap in practice: they change together.
    import re

    for case in CASES:
        stamps = {re.search(rb'Timestamp="([\d.]+)"', data)[1].split(b".")[0] for data in nests(case).values()}
        assert len(stamps) == 1, case
