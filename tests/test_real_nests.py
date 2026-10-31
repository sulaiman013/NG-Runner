"""
The nest guard's readers on real Mozaik files: the 20 .opt of the 10 real jobs Jim sent (2026-10-31),
their programs on G:, and what his two checks printed of them. All marked `fixtures`: they read local
material that never enters git, name jobs by case letter only (the key is outside the repo), and skip
when it is absent. Nothing here runs a script of Jim's: his checks' output is what Sulaiman saved.

What real bytes settled, against the docs and the synthetic files (docs/decisions.md, "Real nests"):
every .opt is UTF-8 with a version line first, a hand-added sheet is a block of its own size, a flip pass
sits inside its run, and our program reader and Jim's nest check count the same doubled holes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from conftest import FIXTURES
from ngrunner.guards import sheets
from ngrunner.guards.facts import (
    BR_CAM_NEST, DESIGN_MATCHES, PASSED, SHEET_SIZE, CheckedFiles, DesignMatch, OptFacts, ToolVerdict,
)
from ngrunner.guards.nest_state import (
    MaterialEvidence, NestState, decide, doubled_programs, past_size_programs,
)
from ngrunner.guards.opt import read_opt, reconcile
from ngrunner.guards.tap import Tap, read_tap
from ngrunner.guards.tool_checks import br_cam_nest_by_material, sheet_size_by_material
from ngrunner.toolkit import ToolRun

pytestmark = pytest.mark.fixtures

CASES = "ABCDEFGHIJ"
CLASSIC, EGGER, LONG = (1244.6, 2463.8), (1549.4, 2768.6), (1244.6, 3048.0)


def _toolkit() -> Path:
    found = sorted(p for p in (FIXTURES / "toolkit").glob("2*") if (p / "jobs").is_dir())
    if not found:
        pytest.skip("the real job folders aren't on this PC")
    return found[-1]


def job_folder(case: str) -> Path:
    return next((_toolkit() / "jobs" / case).iterdir())


def nests(case: str) -> dict[str, bytes]:
    return {path.stem: path.read_bytes() for path in sorted(job_folder(case).glob("*.opt"))}


def every_nest() -> Iterator[tuple[str, str, bytes]]:
    for case in CASES:
        for material, data in nests(case).items():
            yield case, material, data


def results() -> dict:
    found = sorted((FIXTURES / "toolkit").glob("runs_*/results.json"))
    if not found:
        pytest.skip("the real output of Jim's checks isn't on this PC")
    return json.loads(found[-1].read_text(encoding="utf-8"))


def printed(case: str, tool: str) -> ToolRun:
    said = results()[case][tool]
    return ToolRun(said["exit"], said["out"], said.get("err") or "", False)


def _ng_jobs() -> Path | None:
    given = os.environ.get("NGR_NG_JOBS")
    if given:
        return Path(given)
    for base in (Path(r"G:\.shortcut-targets-by-id"), Path(r"G:\My Drive"), Path(r"G:\Shared drives")):
        if base.is_dir():
            for pattern in ("*/*/Sales Folder/CNC operations/NG Jobs", "*/Sales Folder/CNC operations/NG Jobs"):
                for found in base.glob(pattern):
                    return found
    return None


def programs(case: str, material: str) -> tuple[Tap, ...] | None:
    """The programs on G: for a case's material now, or None when it has no folder there."""
    root = _ng_jobs()
    key = FIXTURES / "p1b" / "case_key.json"
    if root is None or not key.is_file():
        pytest.skip("NG Jobs or the case key isn't on this PC")
    job = json.loads(key.read_text(encoding="utf-8"))[case]
    for place in (root / job / material, root / "Old" / job / material):
        if place.is_dir():
            return tuple(read_tap(p.name, p.read_bytes()) for p in sorted(place.iterdir())
                         if p.suffix.casefold() == ".tap")
    return None


# ----- every real .opt reads, and as Jim's own check counts it


def test_all_20_real_nests_read() -> None:
    read = [(case, material, read_opt(data)) for case, material, data in every_nest()]

    assert len(read) == 20
    assert {(facts.encoding, facts.version) for _, _, facts in read} == {("utf-8", "8")}


def test_each_reads_with_the_sheets_and_runs_jim_s_check_printed() -> None:
    counted = re.compile(r"   (?:ok|!!) (.{44}) nested.*?\n(?:.*\n)?"
                         r"      (\d+) physical sheet\(s\) on \d+ pattern\(s\), \d+ flip pass\(es\), (\d+) g-code run")
    for case in CASES:
        said = {name.rstrip(): (int(sheets_), int(runs)) for name, sheets_, runs
                in counted.findall(printed(case, "sheet_size_check").stdout)}
        read = {material: (facts.physical_sheets, len(facts.runs))
                for material, facts in ((m, read_opt(d)) for m, d in nests(case).items())}
        assert read == said, case


def test_case_a_header_and_every_sheet_are_49x97_as_its_programs_read() -> None:
    # Build spec 1.3: its readback said 61x109. That readback's script prints "61x109" as fixed words,
    # whatever the file says. The file says 49x97, and so do its programs.
    facts = read_opt(nests("A")["3-4 Egger Classic White"])

    assert {(s.width_mm, s.length_mm) for s in facts.sheets} == {CLASSIC}
    taps = programs("A", "3-4 Egger Classic White")
    assert sheets.size_finding("3-4 Egger Classic White", taps).code == "nothing_past"


def test_case_a_readbacks_61x109_is_fixed_words_in_jims_script() -> None:
    script = _toolkit() / "NG_Automation_scripts" / "scripts" / "opt_readback.py"
    if not script.is_file():
        pytest.skip("the toolkit's scripts aren't on this PC")
    assert "raw sheets on the 61x109 Egger sheet" in script.read_text(encoding="utf-8")


def test_case_b_hand_added_sheets_are_blocks_of_their_own_size_that_jims_check_doesnt_see() -> None:
    facts = read_opt(nests("B")["3-4 Egger Classic White"])

    sizes = [(s.width_mm, s.length_mm) for s in facts.sheets]
    assert (sizes.count(CLASSIC), sizes.count(EGGER), facts.physical_sheets) == (6, 3, 9)
    said = sheet_size_by_material(printed("B", "sheet_size_check"))["3-4 Egger Classic White"]
    assert said == (PASSED, "")                     # it reads the header's size only
    assert sheets.size_finding("3-4 Egger Classic White",
                               programs("B", "3-4 Egger Classic White")).code == "past_size"


def test_case_c_quarter_on_61x109_passes_both_ways() -> None:
    facts = read_opt(nests("C")["1-4 Egger Classic White"])

    assert {(s.width_mm, s.length_mm) for s in facts.sheets} == {EGGER}
    assert "ok 1-4 Egger Classic White" in printed("C", "sheet_size_check").stdout
    assert sheets.size_finding("1-4 Egger Classic White",
                               programs("C", "1-4 Egger Classic White")).code == "nothing_past"


def test_case_c_and_d_flip_passes_sit_in_their_runs_and_count_as_no_board() -> None:
    for case, physical in (("C", 11), ("D", 4)):
        facts = read_opt(nests(case)["3-4 Egger Classic White"])
        assert [s.flipped for s in facts.sheets].count(True) == 1
        assert facts.physical_sheets == physical


def test_case_h_nests_are_saved_now_and_case_i_has_parts_and_no_sheets() -> None:
    # BUILD 696 had Case H's 3/4 and 1/4 with parts and no sheets: both have been saved since.
    assert all(read_opt(data).physical_sheets for data in nests("H").values())
    facts = read_opt(nests("I")["3-4 Tafisa Spring Blossom_L543"])
    assert (facts.parts, facts.sheets, facts.runs) == (21, (), ())

    verdict = decide(MaterialEvidence(material="3-4 Tafisa Spring Blossom_L543", taps=(),
                                      seen_at_install=False, opt=facts))
    assert verdict.state == NestState.SAVE_NEST


def test_case_j_run_id_minus_one_is_on_all_four_and_all_four_are_nested() -> None:
    data = nests("J")

    assert len(data) == 4
    assert all(b'RunId="-1"' in raw for raw in data.values())
    assert all(read_opt(raw).physical_sheets for raw in data.values())


def test_case_h_long_sheets_are_blocks_of_their_own_size_jims_check_doesnt_see() -> None:
    facts = read_opt(nests("H")["3-4 Am Lam Wh"])

    sizes = [(s.width_mm, s.length_mm) for s in facts.sheets]
    assert (sizes.count(CLASSIC), sizes.count(LONG)) == (11, 6)


# ----- the nests against the programs on G:


def test_reconcile_against_g_finds_what_jims_taps_printed() -> None:
    named = re.compile(r"!! .+ is named by the nest and is NOT on disk")
    stale = re.compile(r"!! .+ is on disk and the nest does not name it - stale\?")
    for case in CASES:
        blocks = re.split(r"(?m)^   (?:ok|!!) ", printed(case, "sheet_size_check").stdout)[1:]
        said = {block[:44].rstrip(): (len(named.findall(block)), len(stale.findall(block))) for block in blocks}
        for material, data in nests(case).items():
            taps = programs(case, material)
            if taps is None:
                assert "no folder" in [b for b in blocks if b.startswith(material)][0]
                continue
            found = reconcile(read_opt(data), taps)
            assert (len(found.missing), len(found.extra)) == said[material], (case, material)


def test_a_program_the_nest_doesnt_name_is_mostly_merged_or_hand_named_so_it_is_only_a_log_line() -> None:
    # Measured 2026-10-31: 35 on 19 materials, 25 merged, 6 hand-named, 2 remakes, 1 long form and 1
    # per-sheet name. Every real job has some: as a fault, each would be a false DO NOT CUT.
    extras = [tap.family for case, material, data in every_nest()
              if (taps := programs(case, material)) is not None
              for tap in taps if tap.name in reconcile(read_opt(data), taps).extra]

    assert extras.count("merged") + extras.count("hand") >= 0.8 * len(extras)
    assert len(extras) > 20


# ----- doubled holes: our program reader against Jim's nest check, material by material


def test_doubled_holes_agree_exactly_with_jims_nest_check_on_all_10() -> None:
    agreed = {}
    for case in CASES:
        jim = br_cam_nest_by_material(printed(case, "br_cam_check_nest"))
        for material in nests(case):
            taps = programs(case, material) or ()
            ours = sum(len(tap.near_pairs) for tap in taps)
            code, line = jim[material]
            theirs = int(line.split()[0]) if line else 0
            assert ours == theirs, (case, material)
            agreed[(case, material)] = ours

    assert sorted(count for count in agreed.values() if count) == [8, 16, 36, 40]
    assert sum(1 for count in agreed.values() if not count) == 16


# ----- sheet size: the two checks catch different faults, so both run


def test_the_two_sheet_size_checks_disagree_exactly_where_jims_reads_one_size_per_nest() -> None:
    disagree = set()
    for case in CASES:
        jim = sheet_size_by_material(printed(case, "sheet_size_check"))
        for material in nests(case):
            taps = programs(case, material)
            if taps is None or jim[material][0] not in ("passed", "failed"):
                continue
            ours = sheets.size_finding(material, taps).code == "past_size"
            if ours != (jim[material][0] == "failed"):
                disagree.add((case, material, "ours" if ours else "jim's"))

    assert disagree == {("B", "3-4 Egger Classic White", "ours"), ("H", "3-4 Am Lam Wh", "ours"),
                        ("E", "1-4 Tafisa Apris Ski_M2015", "jim's"), ("H", "1 in Am Lam Wh", "jim's")}


# ----- Nest checked, on real cases


def _evidence(case: str, material: str, *, design: bool) -> MaterialEvidence | None:
    facts = read_opt(nests(case)[material])
    taps = programs(case, material)
    if taps is None:
        return None
    rooms = tuple((p.name, hashlib.sha256(p.read_bytes()).hexdigest())
                  for p in sorted(job_folder(case).glob("Room*.des")))
    now = CheckedFiles(facts.sha256, tuple((t.name, t.sha256) for t in taps), rooms)
    tools = []
    for tool, run in ((SHEET_SIZE, sheet_size_by_material(printed(case, "sheet_size_check"))),
                      (BR_CAM_NEST, br_cam_nest_by_material(printed(case, "br_cam_check_nest")))):
        code, line = run[material]
        tools.append(ToolVerdict(tool, code, line, checked=now))
    return MaterialEvidence(material=material, taps=taps, seen_at_install=False, opt=facts,
                            tools=tuple(tools), room_hashes=rooms,
                            design=DesignMatch(DESIGN_MATCHES, checked=now) if design else None)


KNOWN_BAD = {("A", "3-4 Egger Classic White"), ("B", "3-4 Egger Classic White"),
             ("B", "3-4 Tafisa Sunday Brunch-Inter__M2005"), ("D", "3-4 Egger Classic White"),
             ("E", "1-4 Tafisa Apris Ski_M2015"), ("E", "3-4 Tafisa Apris Ski_M2015"),
             ("H", "1 in Am Lam Wh"), ("H", "3-4 Am Lam Wh"), ("J", "3-4 Egger Classic White")}


def test_with_no_design_match_no_real_case_reaches_nest_checked() -> None:
    for case, material, _ in every_nest():
        ev = _evidence(case, material, design=False)
        assert ev is None or decide(ev).state != NestState.NEST_CHECKED, (case, material)


def test_every_known_bad_real_material_is_do_not_cut_even_with_a_design_match() -> None:
    for case, material in KNOWN_BAD:
        ev = _evidence(case, material, design=True)
        assert decide(ev).state == NestState.DO_NOT_CUT, (case, material)
        assert doubled_programs(ev) or past_size_programs(ev) or any(
            tool.code == "failed" for tool in ev.tools), (case, material)


def test_with_a_design_match_only_the_materials_both_checks_pass_reach_nest_checked() -> None:
    # Cases F and G are the nests from an unsaved state (build spec 7): only the design match can catch
    # that, so here, with one assumed, they pass. That is why a pass needs it.
    reached = set()
    for case, material, _ in every_nest():
        ev = _evidence(case, material, design=True)
        if ev is not None and decide(ev).state == NestState.NEST_CHECKED:
            reached.add((case, material))
            assert all(tool.code == PASSED for tool in ev.tools)
            assert not reconcile(ev.opt, ev.taps).missing

    assert not reached & KNOWN_BAD
    assert reached == {("F", "3-4 Egger Classic White"), ("G", "3-4 Egger Classic White"),
                       ("I", "3-4 Egger Black U999 ST19 Woodgrain"), ("J", "1 in Am Lam Wh"),
                       ("J", "1-4 Tafisa Classic White"), ("J", "3-4 Am Lam Wh")}


def test_a_real_nest_read_never_raises_a_do_not_cut_by_itself() -> None:
    for case, material, data in every_nest():
        facts: OptFacts = read_opt(data)
        taps = programs(case, material) or ()
        ev = MaterialEvidence(material=material, taps=tuple(t for t in taps if not t.near_pairs),
                              seen_at_install=False, opt=facts,
                              cleared=frozenset(t.sha256 for t in taps))
        assert decide(ev).state != NestState.DO_NOT_CUT, (case, material)
