"""
What Jim's two nest checks said, read from what they print (guards/tool_checks.py).

The made-up runs here are in the form both scripts printed when Sulaiman ran them on copies of the 10
real jobs (2026-10-31), with made-up names. The tests marked `fixtures` read that real output, by case
letter, from .local-fixtures/toolkit/runs_*/results.json, and tie each line to the scripts' source.
What matters most is what they pin about anything else: a line not seen there, or an exit code that
doesn't fit what was printed, is could_not_verify, never a pass.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from conftest import FIXTURES
from ngrunner.guards import tool_checks
from ngrunner.guards.facts import BR_CAM_NEST, COULD_NOT_VERIFY, FAILED, PASSED, SHEET_SIZE, CheckedFiles
from ngrunner.guards.tool_checks import (
    br_cam_nest, br_cam_nest_by_material, sheet_size, sheet_size_by_material,
)
from ngrunner.messages.lint import check_line
from ngrunner.toolkit import READERS, ToolRun, tool_verdict

NAME = "3-4 Egger Classic White"
CHECKED = CheckedFiles("a" * 64, (("TESS01.TAP", "b" * 64),), (("Room1.des", "c" * 64),))


def run(*lines: str, code: int = 0, stderr: str = "") -> ToolRun:
    return ToolRun(exit_code=code, stdout="".join(f"{line}\n" for line in lines), stderr=stderr,
                   timed_out=False)


def size_line(mark: str = "ok", *, on=(49, 97), want=(49, 97), name: str = NAME) -> str:
    return f'   {mark} {name[:44]:<44} nested {on[0]}" x {on[1]}"   expected {want[0]}" x {want[1]}"'


def counts(sheets: int = 9) -> tuple[str, str]:
    return (f"      {sheets} physical sheet(s) on {sheets} pattern(s), 0 flip pass(es), {sheets} g-code run(s)",
            f"      SHEET COUNT FOR THE TRACKER AND THE JOB SHEET = {sheets}")


FIX = ("        -> fix the SHEET SIZE on this material in the library, then re-optimize"
       " and regenerate its TAP. Do not cut this material as nested.")
ALL_THERE = "      ok every program the nest names is on disk, nothing stale: TESS01.TAP, TESS02.TAP"
MISSING = "      !! TESS03.TAP is named by the nest and is NOT on disk"
STALE = "      !! Tes1-6.tap is on disk and the nest does not name it - stale?"
NO_FOLDER = r"      !! no folder G:\made-up\NG Jobs\Testerson_101426\3-4 Egger Classic White - the g-code was never posted"
NO_HEADER = "   ?? 3-4 Egger Classic White.opt - no OptimizeMaterial element (wrong codec?)"
OPT = "3-4 Egger Classic White.opt"
CLEAN = "   no doubled housings in this nest"
TWIN = ("   !! R1C8    Fixed Shelf      TWO 20mm HOLES ON TOP OF EACH OTHER at X=9.525/9.5 Y=260"
        " - 0.025mm apart (x{n} on the sheet)")
TWINS_FIX = ("        -> the NEST doubles the housing even though the .des is clean: two joint"
             " fastener templates match these sections. Fix the templates and RE-OPTIMIZE;"
             " do not cut this nest.")


# ----- sheet_size_check --taps


@pytest.mark.parametrize("given", [
    run(size_line(), *counts(), ALL_THERE),
    run(size_line(), *counts(), STALE, STALE, code=1),
    run(size_line(on=(61, 109), want=(61, 109), name="1-4 Egger Classic White"), *counts(1), STALE, code=1),
    run(size_line(name="A material with a very long name, longer than forty four letters"), *counts(),
        ALL_THERE),
], ids=["all there", "only merged programs it doesn't name", "1/4 on its sheet", "a long name, cut"])
def test_a_size_on_its_sheet_with_its_programs_there_passes(given: ToolRun) -> None:
    assert sheet_size(given)[0] == PASSED


def test_a_program_the_nest_doesnt_name_is_no_failure_though_the_tool_exits_1() -> None:
    # The trap: by its exit code, all 10 real jobs would be DO NOT CUT, for merged programs.
    assert sheet_size(run(size_line(), *counts(), STALE, code=1)) == (PASSED, "")


def test_a_wrong_sheet_failed_and_says_so_from_the_numbers_printed() -> None:
    code, line = sheet_size(run(size_line("!!", on=(61, 109), want=(49, 97)), FIX, *counts(2), STALE, code=1))

    assert code == FAILED
    assert line == "The nest is on 61x109 and this colour is 49x97"
    assert check_line(line) == []


def test_a_wrong_sheet_stands_whatever_the_programs_say() -> None:
    given = run(size_line("!!", on=(49, 120), want=(49, 97)), FIX, *counts(1), MISSING, STALE, code=1)

    assert sheet_size(given)[0] == FAILED


@pytest.mark.parametrize("lines", [(MISSING,), (MISSING, STALE), (NO_FOLDER,)],
                         ids=["missing", "missing and extra", "no folder"])
def test_a_program_not_on_disk_says_nothing_about_the_size_and_isnt_a_pass(lines) -> None:
    assert sheet_size(run(size_line(), *counts(), *lines, code=1))[0] == COULD_NOT_VERIFY


def test_an_ok_of_a_nest_with_no_sheets_is_of_its_header_only_and_no_pass() -> None:
    # Case I: ok, 0 physical sheets, and no folder for its programs.
    assert sheet_size(run(size_line(), *counts(0), NO_FOLDER, code=1))[0] == COULD_NOT_VERIFY
    assert sheet_size(run(size_line(), *counts(0), ALL_THERE))[0] == COULD_NOT_VERIFY


def test_an_ok_with_no_taps_block_is_no_pass_as_it_could_be_of_a_nest_with_no_sheets() -> None:
    assert sheet_size(run(size_line()))[0] == COULD_NOT_VERIFY


def test_a_fault_with_no_taps_block_still_stands() -> None:
    assert sheet_size(run(size_line("!!", on=(61, 109), want=(49, 97)), FIX, code=1))[0] == FAILED


@pytest.mark.parametrize("given", [
    run(),                                          # no file named: it prints nothing and exits 0
    run(NO_HEADER),                                 # ?? and exit 0, for a file it couldn't read
    run(NO_HEADER, size_line(), *counts(), ALL_THERE),
    run("ALL CLEAN"),
    run(size_line().replace("nested", "nest"), *counts(), ALL_THERE),
    run(size_line().replace("   ok", "  ok"), *counts(), ALL_THERE),
    run(size_line("OK"), *counts(), ALL_THERE),
    run(size_line(), *counts(), ALL_THERE, "something else it might print"),
    run(size_line(), counts()[0]),                  # the --taps block cut short
    run(size_line(), counts()[1], counts()[0], ALL_THERE),
    run(size_line(), *counts()),                    # says nothing of the programs
    run(size_line(), *counts(), ALL_THERE, STALE, code=1),
    run(size_line(), counts(9)[0], counts(8)[1], ALL_THERE),
    run(size_line("!!", on=(61, 109), want=(49, 97)), *counts(), code=1),   # no line telling what to do
    run(size_line("!!", on=(61, 109), want=(49, 97)), *counts(), STALE, code=1),
    run(size_line(), *counts(), ALL_THERE, stderr="Traceback (most recent call last):"),
], ids=["nothing", "?? of a file", "?? beside a good one", "other words", "a word changed",
        "another margin", "another mark", "a line more", "taps cut short", "taps out of order",
        "taps saying nothing", "all there and stale", "two sheet counts", "!! with no fix line",
        "!! with no fix line and its taps whole", "something on stderr"])
def test_anything_not_seen_is_could_not_verify(given: ToolRun) -> None:
    assert sheet_size(given)[0] == COULD_NOT_VERIFY


@pytest.mark.parametrize("given", [
    run(size_line(), *counts(), ALL_THERE, code=1),
    run(size_line("!!", on=(61, 109), want=(49, 97)), FIX, *counts(), ALL_THERE, code=0),
    run(size_line(), *counts(), STALE, code=0),
    run(size_line(), *counts(), MISSING, code=0),
    run(size_line(), *counts(), ALL_THERE, code=2),
], ids=["clean exit 1", "fault exit 0", "stale exit 0", "missing exit 0", "another code"])
def test_an_exit_code_that_doesnt_fit_what_was_printed_is_could_not_verify(given: ToolRun) -> None:
    assert sheet_size(given)[0] == COULD_NOT_VERIFY


def test_several_materials_get_an_answer_each_and_one_alone_is_needed_for_a_staged_copy() -> None:
    given = run(size_line(), *counts(), STALE,
                size_line("!!", on=(61, 109), want=(49, 97), name="1-4 Tafisa Apres Skis"), FIX,
                *counts(2), MISSING, code=1)

    assert sheet_size_by_material(given) == {
        NAME: (PASSED, ""), "1-4 Tafisa Apres Skis": (FAILED, "The nest is on 61x109 and this colour is 49x97")}
    assert sheet_size(given)[0] == COULD_NOT_VERIFY


# ----- br_cam_check --nest


def test_a_nest_with_no_doubled_housings_passes() -> None:
    assert br_cam_nest(run(OPT, CLEAN)) == (PASSED, "")


def test_a_nest_with_doubled_holes_failed_and_counts_them_as_the_tool_does() -> None:
    code, line = br_cam_nest(run(OPT, TWIN.format(n=2), TWIN.format(n=1), TWINS_FIX, code=1))

    assert code == FAILED
    assert line == "3 holes are doubled in the nest"
    assert check_line(line) == []


def test_one_doubled_hole_reads_as_one() -> None:
    assert br_cam_nest(run(OPT, TWIN.format(n=1), TWINS_FIX, code=1))[1] == "1 hole is doubled in the nest"


def test_several_nests_get_an_answer_each() -> None:
    given = run("1 in Am Lam Wh.opt", CLEAN, OPT, TWIN.format(n=1), TWINS_FIX, code=1)

    assert br_cam_nest_by_material(given) == {
        "1 in Am Lam Wh": (PASSED, ""), NAME: (FAILED, "1 hole is doubled in the nest")}


@pytest.mark.parametrize("given", [
    run(),                                          # a folder with no .opt: nothing, and exit 0
    run(OPT),
    run(CLEAN),
    run(OPT, CLEAN, OPT, CLEAN),                    # the same nest twice
    run(OPT, CLEAN, code=1),
    run(OPT, TWIN.format(n=1), TWINS_FIX, code=0),
    run(OPT, TWIN.format(n=1), code=1),
    run(OPT, TWINS_FIX, code=1),
    run(OPT, TWIN.format(n=0), TWINS_FIX, code=1),  # a part with Quan 0
    run(OPT, "ALL CLEAN"),
    run("a file.txt", CLEAN),
    run(OPT, CLEAN, stderr="Traceback (most recent call last):"),
    run(OPT, CLEAN, code=2),
], ids=["nothing", "a name alone", "no name", "the same nest twice", "clean exit 1", "fault exit 0",
        "fault with no line after", "a line with no fault", "a zero count", "other words",
        "not a nest's name", "something on stderr", "another code"])
def test_anything_not_seen_in_br_cams_output_is_could_not_verify(given: ToolRun) -> None:
    assert br_cam_nest(given)[0] == COULD_NOT_VERIFY


# ----- through the seam


def test_the_readers_are_these_two_and_no_other() -> None:
    # des_cutlist prints a table, and no reader of it exists: so the design match, and a pass, wait.
    assert READERS == {SHEET_SIZE: sheet_size, BR_CAM_NEST: br_cam_nest}


def test_a_pass_through_the_seam_names_what_it_ran_on() -> None:
    verdict = tool_verdict(SHEET_SIZE, run(size_line(), *counts(), ALL_THERE), CHECKED)

    assert (verdict.tool, verdict.code, verdict.checked) == (SHEET_SIZE, PASSED, CHECKED)


def test_a_failure_through_the_seam_says_its_line() -> None:
    verdict = tool_verdict(BR_CAM_NEST, run(OPT, TWIN.format(n=8), TWINS_FIX, code=1), CHECKED)

    assert (verdict.code, verdict.line) == (FAILED, "8 holes are doubled in the nest")


def test_a_run_that_never_finished_reads_as_nothing_whatever_it_printed() -> None:
    hung = ToolRun(exit_code=None, stdout=size_line() + "\n", stderr="", timed_out=True)

    assert tool_verdict(SHEET_SIZE, hung, CHECKED).code == COULD_NOT_VERIFY


def test_nothing_a_tool_prints_reaches_a_person_but_the_numbers() -> None:
    name = "A client called Testerson at 12 Made Up Street"

    for line in (sheet_size(run(size_line("!!", on=(61, 109), want=(49, 97), name=name), FIX, code=1))[1],
                 br_cam_nest(run(f"{name}.opt", TWIN.format(n=1), TWINS_FIX, code=1))[1]):
        assert "Testerson" not in line and "Made Up" not in line


def test_the_module_says_where_its_lines_come_from() -> None:
    assert "the 10 real jobs" in Path(tool_checks.__file__).read_text(encoding="utf-8")


# ----- the real output of the 10 real jobs (2026-10-31), by case letter. Nothing here runs a script


def _results() -> dict:
    found = sorted((FIXTURES / "toolkit").glob("runs_*/results.json"))
    if not found:
        pytest.skip("the real output of Jim's checks isn't on this PC")
    return json.loads(found[-1].read_text(encoding="utf-8"))


def _real(case: str, tool: str) -> ToolRun:
    said = _results()[case][tool]
    return ToolRun(exit_code=said["exit"], stdout=said["out"], stderr=said.get("err") or "", timed_out=False)


P, F, U = PASSED, FAILED, COULD_NOT_VERIFY
# What each material's nest check says, from its real output. Sheet size: Case C's and H's 1/4 and Case
# E's Spring Blossom name programs not on disk; Case I's Spring Blossom has no sheets.
SHEET_SIZE_SAYS = {
    "A": {"3-4 Egger Classic White": P},
    "B": {"3-4 Egger Classic White": P, "3-4 Tafisa Sunday Brunch-Inter__M2005": P},
    "C": {"1-4 Egger Classic White": U, "3-4 Egger Classic White": U},
    "D": {"3-4 Egger Classic White": P},
    "E": {"1-4 Tafisa Apris Ski_M2015": F, "3-4 Tafisa Apris Ski_M2015": F, "3-4 Tafisa Spring Blossom_L543": U},
    "F": {"3-4 Egger Classic White": P},
    "G": {"3-4 Egger Classic White": P},
    "H": {"1 in Am Lam Wh": F, "1-4 Egger White": U, "3-4 Am Lam Wh": P},
    "I": {"3-4 Egger Black U999 ST19 Woodgrain": P, "3-4 Tafisa Spring Blossom_L543": U},
    "J": {"1 in Am Lam Wh": P, "1-4 Tafisa Classic White": P, "3-4 Am Lam Wh": P, "3-4 Egger Classic White": P},
}
DOUBLED = {("A", "3-4 Egger Classic White"): 16, ("B", "3-4 Tafisa Sunday Brunch-Inter__M2005"): 36,
           ("D", "3-4 Egger Classic White"): 8, ("J", "3-4 Egger Classic White"): 40}


@pytest.mark.fixtures
@pytest.mark.parametrize("case", sorted(SHEET_SIZE_SAYS))
def test_the_real_sheet_size_output_reads_material_by_material(case: str) -> None:
    given = _real(case, "sheet_size_check")
    assert given.exit_code == 1                     # on all 10: the exit code can't be the verdict

    said = {material: code for material, (code, _) in sheet_size_by_material(given).items()}

    assert said == SHEET_SIZE_SAYS[case]


@pytest.mark.fixtures
@pytest.mark.parametrize("case", sorted(SHEET_SIZE_SAYS))
def test_the_real_nest_check_output_reads_material_by_material(case: str) -> None:
    said = br_cam_nest_by_material(_real(case, "br_cam_check_nest"))

    assert set(said) == set(SHEET_SIZE_SAYS[case])
    for material, (code, line) in said.items():
        holes = DOUBLED.get((case, material), 0)
        assert code == (FAILED if holes else PASSED), material
        assert line == ("" if not holes else f"{holes} holes are doubled in the nest")


@pytest.mark.fixtures
def test_the_real_wrong_sheets_say_what_they_are_in_ng_runners_words() -> None:
    said = {(case, material): line for case in ("E", "H")
            for material, (code, line) in sheet_size_by_material(_real(case, "sheet_size_check")).items()
            if code == FAILED}

    assert said == {("E", "1-4 Tafisa Apris Ski_M2015"): "The nest is on 61x109 and this colour is 49x97",
                    ("E", "3-4 Tafisa Apris Ski_M2015"): "The nest is on 61x109 and this colour is 49x97",
                    ("H", "1 in Am Lam Wh"): "The nest is on 49x120 and this colour is 49x97"}


def _script(name: str) -> str:
    copies = sorted((FIXTURES / "toolkit").glob("*/NG_Automation_scripts/scripts"))
    if not copies:
        pytest.skip("the toolkit copy isn't on this PC")
    return (copies[-1] / name).read_text(encoding="utf-8")


@pytest.mark.fixtures
@pytest.mark.parametrize("script, printed", [
    ("sheet_size_check.py", ('nested %.0f" x %.0f"   expected %.0f" x %.0f"', "'   %s %-44s",
                             "-> fix the SHEET SIZE on this material in the library, then re-optimize",
                             " and regenerate its TAP. Do not cut this material as nested.",
                             '"   ?? %s - no OptimizeMaterial element (wrong codec?)"',
                             "%d physical sheet(s) on %d pattern(s), %d flip pass(es), %d g-code run(s)",
                             "SHEET COUNT FOR THE TRACKER AND THE JOB SHEET = %d",
                             "!! no folder %s - the g-code was never posted",
                             "!! %s is named by the nest and is NOT on disk",
                             "!! %s is on disk and the nest does not name it - stale?",
                             "ok every program the nest names is on disk, nothing stale: %s",
                             "re.search(r'<OptimizeMaterial[^>]*>', s)",
                             "sys.exit(1 if check(a, ng) else 0)")),
    ("br_cam_check.py", ("no doubled housings in this nest", "print(os.path.basename(opt))",
                         "!! %-7s %-16s TWO %smm HOLES ON TOP OF EACH OTHER at X=%g/%g Y=%g",
                         "- %.3fmm apart (x%d on the sheet)",
                         "-> the NEST doubles the housing even though the .des is clean: two joint",
                         " fastener templates match these sections. Fix the templates and RE-OPTIMIZE;",
                         " do not cut this nest.",
                         "sys.exit(1 if sum(check_nest(o) for o in opts) else 0)")),
])
def test_what_the_readers_expect_is_what_his_scripts_print(script: str, printed: tuple[str, ...]) -> None:
    # The re.search above is why it reads one size per .opt: the first <OptimizeMaterial>'s only.
    source = _script(script)
    gone = [number for number, piece in enumerate(printed) if piece not in source]

    assert not gone, f"{script} no longer prints what tool_checks.py reads: piece(s) {gone}"


@pytest.mark.fixtures
@pytest.mark.parametrize("script", ["sheet_size_check.py", "br_cam_check.py", "units.py"])
def test_what_they_import_is_all_in_the_build(script: str) -> None:
    # Build spec 5.13: any module a script imports must be in the build. These need only what is there.
    imported = set()
    for node in ast.walk(ast.parse(_script(script))):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])

    assert imported <= {"sys", "re", "os", "glob", "units"}, sorted(imported)
