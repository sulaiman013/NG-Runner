"""
The check runner (P2 build spec 2, "Shared check runner"): Jim's crosscheck.py run from the adopted copy
on a staged copy of one job, and each checker's output read into a verdict that fails closed, as
guards/tool_checks.py does for his two nest checks.

What a pass needs here: crosscheck didn't flag the checker, and every line it printed is one its source
prints on a clean run, with its own clean line for every room it was given. A flag with a line that
names a fault is failed. Anything else is could_not_verify: a line not known, a "??", a fault Jim's
crosscheck doesn't flag (outset_check's FLAG, eb_tops' and top_line's "would change"), a checker that
stopped with an error, or one that couldn't see what it compares with. The output here is synthetic, in
the shapes his source prints, with made-up names; tests/test_checks_real.py runs the real ones.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ngrunner.checks import (
    LEFT_OUT, Planned, read_checker, read_crosscheck, run_crosscheck, stage_job,
)
from ngrunner.checks import crosscheck as cc
from ngrunner.clock import FakeClock
from ngrunner.guards.facts import COULD_NOT_VERIFY, FAILED, PASSED, ToolRun
from ngrunner.toolkit import ToolkitError, take_snapshot

NOW = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)
JOB = "Testerson_101426"
PLAN = tuple(Planned(name, name == "ic_code_check") for name in (
    "section_align", "section_span", "drill_depth_check", "outset_check", "part_geom_check", "br_cam_check",
    "op_schema_check", "wm_variant_check", "notch_check", "eb_tops", "top_line", "parm_check",
    "panel_xcheck", "ic_code_check"))
ROOM1 = r"C:\stage\mozaik\Testerson_101426\Room1.des"
ROOM2 = r"C:\stage\mozaik\Testerson_101426\Room2.des"

CLEAN = {
    "section_align": f"{ROOM1}  (Testerson - Primary)\n\n  WALL 2_1   panel top line(s) = 96\" (2438.4mm)\n"
                     "    ok C3   48 S Hang 1S             Elev=37 1/4\"     top=96\"        \n"
                     "    ok C1   84 BR                    Elev=36\"         top=96\"          (bridge - sits below"
                     " the panel top by design)\n\n  --- bore rows on the verticals ---\n    all bore rows clean",
    "section_span": f"{ROOM1}\n   every bay is filled wall-of-vertical to wall-of-vertical",
    "drill_depth_check": f"{ROOM1}\n   -- C2   FM 84                    no line bore rows; 4 discrete 5mm cam-bolt"
                         " holes (sparse hardware connection)\n   every bore depth matches the adjacent section heights",
    "outset_check": "\nPASS - every product at Outset 0",
    "part_geom_check": f"{ROOM1}\n   every panel part matches its product's depth and height",
    "br_cam_check": "Room1.des\n   ok BR C1   84 BR        NoShelfHoles=True   vertical C4   cams at Y 319,96",
    "op_schema_check": "  checked 12 user-written operation element(s) across 2 file(s): ALL MATCH",
    "wm_variant_check": f"{ROOM1}\n   ok C2   WM Panel 48 Thru         notch equation matches library, geometry agrees",
    "notch_check": "Room1.des\n   every notch is on its line-bore face; WM banding taped and locked",
    "eb_tops": "   Room1.des    already correct\n\nwould change: 0 part(s)/slot(s)",
    "top_line": "   Room1.des    already on its top line (2438)\n\nwould change: 0 WM panels",
    "parm_check": "  OK NG Closets-JobParms.dat                      identical\n"
                  "  -- Closets-JobParms.dat                         no counterpart in the reference job\n\n"
                  "RESULT: CLEAN - parameters match the reference job",
    "panel_xcheck": "IC board parts 18   Mozaik cutlist 16\n\n-- DW: IC 1  Mozaik 1\n"
                    "     (124.5, 309.9) x1 <- (127.0, 309.9)  (max 2.50mm)  DW Hook 4.9x12.2x3/4 FL WH\n"
                    "-- SH: IC 10  Mozaik 9\n"
                    "     (294.6, 356) x1 <- bridge (sized by Jim, matched on count - RULES BR)  SH 14x11.6x3/4 FIX WH\n"
                    "-- VT: IC 5  Mozaik 5",
    "ic_code_check": "Room1.des   (Testerson - Primary)\n"
                     "   ok wall 2    H=60\"       IC wants 1xRight, 1xThru              built 1xRight, 1xThru             \n"
                     "        C2   WM Panel 48 Thru         half drilled (blind)",
}


def crosscheck_file(sections: dict[str, str], *, flagged=(), advisory=(), rooms="Room0.des, Room1.des",
                    plan=PLAN) -> str:
    verdict = "CROSSCHECK CLEAN" if not flagged else "CROSSCHECK FLAGGED: " + ", ".join(flagged)
    if advisory:
        verdict += "   (advisory only: " + ", ".join(advisory) + ")"
    out = [f"{JOB} - crosscheck 2026-10-31T09:00Z", f"  rooms   {rooms}",
           f"  against C:\\stage\\ngjob\\{JOB}_IC_Conversion.json", "  " + verdict + "\n", ""]
    for check in plan:
        out += [f"===== {check.name}" + ("   [ADVISORY]" if check.advisory else ""),
                sections[check.name].rstrip(), ""]
    return "\n".join(out)


def stdout_of(text: str) -> str:
    return text.splitlines()[3].strip() + "\nwritten: C:\\stage\\ngjob\\x_crosscheck.txt\n"


def read(sections=CLEAN, *, flagged=(), advisory=(), exit_code=None, stderr="", rooms="Room0.des, Room1.des",
         staged_rooms=("Room0.des", "Room1.des"), library=True, plan=PLAN, text=None):
    text = crosscheck_file(sections, flagged=flagged, advisory=advisory, rooms=rooms) if text is None else text
    code = (1 if flagged else 0) if exit_code is None else exit_code
    run = ToolRun(code, stdout_of(text) if text else "", stderr, False)
    return read_crosscheck(text, run, plan=plan, rooms=staged_rooms, library_found=library)


def codes(result) -> dict[str, str]:
    return {v.checker: v.code for v in result.verdicts}


# ----- a whole run


def test_a_clean_run_passes_every_checker_and_names_what_ran_and_what_was_left_out() -> None:
    result = read()

    assert result.code == PASSED
    assert set(codes(result).values()) == {PASSED}
    assert result.ran == tuple(p.name for p in PLAN)
    assert result.left_out == LEFT_OUT == ("hinge_check", "sheet_size_check", "br_cam_check --nest")
    assert [v.advisory for v in result.verdicts] == [p.advisory for p in PLAN]


def test_a_flag_with_a_fault_line_is_failed_and_the_job_fails() -> None:
    sections = {**CLEAN, "section_span": f"{ROOM1}\n   !! C3   48 DH                     starts at 12.000,"
                                         " 6.350mm past C2 (bay 0.000..900.000)"}

    result = read(sections, flagged=("section_span",))

    assert codes(result)["section_span"] == FAILED and result.code == FAILED
    assert {c for n, c in codes(result).items() if n != "section_span"} == {PASSED}


def test_a_failure_says_nothing_of_the_jobs_own_words() -> None:
    sections = {**CLEAN, "section_span": f"{ROOM1}\n   !! C3   Testerson Special         starts at 12.000"}

    result = read(sections, flagged=("section_span",))

    assert "Testerson" not in repr(result) and "Testerson" not in result.words
    assert all("Testerson" not in v.why for v in result.verdicts)


def test_an_advisory_flag_never_fails_the_job() -> None:
    sections = {**CLEAN, "ic_code_check": CLEAN["ic_code_check"] + "\n   !! wall 3    H=60\"       IC wants 1xLeft"
                                          "                       built 1xThru\n\n   1 group(s) disagree with the IC"
                                          " description. ADVISORY ONLY - Jim's rules\n   override the IC codes, and IC"
                                          " descriptions carry known anomalies. Raise it, do\n   not change the build on"
                                          " this alone."}

    result = read(sections, advisory=("ic_code_check",))

    assert codes(result)["ic_code_check"] == FAILED and result.code == PASSED


def test_a_checker_that_stopped_with_an_error_is_could_not_verify_not_a_fault() -> None:
    sections = {**CLEAN, "panel_xcheck": "Traceback (most recent call last):\n  File \"x.py\", line 9, in <module>\n"
                                         "KeyError: 'build_mm'"}

    result = read(sections, flagged=("panel_xcheck",))

    assert codes(result)["panel_xcheck"] == COULD_NOT_VERIFY and result.code == COULD_NOT_VERIFY


@pytest.mark.parametrize("checker, line", [
    ("section_span", "   every bay is filled, mostly"),
    ("outset_check", "PASS - every product at Outset 0 (mostly)"),
    ("panel_xcheck", "IC board parts 18   Mozaik cutlist 16   (and a new line)"),
    ("br_cam_check", "   something it never printed before"),
    ("eb_tops", "   Room1.des    already correct, said differently"),
])
def test_a_line_no_reader_knows_is_could_not_verify(checker: str, line: str) -> None:
    result = read({**CLEAN, checker: CLEAN[checker] + "\n" + line})

    assert codes(result)[checker] == COULD_NOT_VERIFY and result.code == COULD_NOT_VERIFY


@pytest.mark.parametrize("checker, text", [
    ("outset_check", "  FLAG Room1.des     C7    wall 2_1    48 DH                      Outset=12.7\n\n"
                     "1 product(s) NOT at Outset 0 - review"),
    ("eb_tops", "   Room1.des    COUNTERTOP 24.000in x 96.000in  four edges, slot 1\n\nwould change: 1 part(s)/slot(s)"),
    ("top_line", "   Room1.des    WM Panel 48 Thru Radius    H=1216    Elev   1184.0 ->  1222.4  (top 2400 -> 2438)\n\n"
                 "would change: 1 WM panels"),
], ids=["outset FLAG", "eb_tops would change", "top_line would change"])
def test_a_fault_jims_crosscheck_doesnt_flag_is_could_not_verify_never_a_pass(checker: str, text: str) -> None:
    result = read({**CLEAN, checker: text})

    verdict = next(v for v in result.verdicts if v.checker == checker)
    assert verdict.code == COULD_NOT_VERIFY and "doesn't flag" in verdict.why


@pytest.mark.parametrize("checker, text", [
    ("op_schema_check", "  ?? OperationGroove - no Mozaik-written example to compare against; check it by hand\n"
                        "  checked 12 user-written operation element(s) across 2 file(s): ALL MATCH"),
    ("wm_variant_check", f"{ROOM1}\n   -- C2   WM Panel 48 Thru         no sections on wall 2 yet - variant check deferred"),
    ("br_cam_check", "Room1.des\n   ok BR C1   84 BR        NoShelfHoles=True   vertical C4   cams at Y NONE\n"
                     "        -> NOTE: the vertical has no line bore rows cached - open the job so Mozaik regenerates"
                     " them, then re-run"),
])
def test_a_checker_that_says_check_it_by_hand_is_could_not_verify(checker: str, text: str) -> None:
    result = read({**CLEAN, checker: text})

    assert codes(result)[checker] == COULD_NOT_VERIFY


def test_parm_check_with_no_reference_job_passes_nothing_though_it_says_clean() -> None:
    no_reference = ("  -- Closets-JobParms.dat                         no counterpart in the reference job\n"
                    "  -- NG Closets-JobParms.dat                      no counterpart in the reference job\n\n"
                    "RESULT: CLEAN - parameters match the reference job")

    verdict = next(v for v in read({**CLEAN, "parm_check": no_reference}).verdicts if v.checker == "parm_check")

    assert verdict.code == COULD_NOT_VERIFY and "reference" in verdict.why


def test_wm_variant_check_with_its_library_out_of_reach_passes_nothing_though_it_says_ok() -> None:
    verdict = next(v for v in read(library=False).verdicts if v.checker == "wm_variant_check")

    assert verdict.code == COULD_NOT_VERIFY and "library" in verdict.why


def test_wm_variant_check_still_fails_on_what_it_could_see() -> None:
    sections = {**CLEAN, "wm_variant_check": f"{ROOM1}\n   !! C2   WM Panel 48 Thru         geometry says Left"
                                             " (left=False right=True, bridges excluded)"}

    result = read(sections, flagged=("wm_variant_check",), library=False)

    assert codes(result)["wm_variant_check"] == FAILED


def test_a_checker_that_doesnt_name_every_room_it_was_given_is_could_not_verify() -> None:
    result = read(rooms="Room0.des, Room1.des, Room2.des", staged_rooms=("Room0.des", "Room1.des", "Room2.des"))

    assert codes(result)["section_span"] == COULD_NOT_VERIFY
    assert codes(result)["op_schema_check"] == COULD_NOT_VERIFY      # across 2 file(s), and 3 were given
    assert codes(result)["outset_check"] == PASSED                   # it names no room


def test_every_room_named_passes() -> None:
    two = {name: text.replace("\n   every", f"\n   every") for name, text in CLEAN.items()}
    two["section_span"] = CLEAN["section_span"] + "\n\n" + CLEAN["section_span"].replace("Room1", "Room2")
    two["op_schema_check"] = CLEAN["op_schema_check"].replace("2 file(s)", "3 file(s)")

    result = read(two, rooms="Room0.des, Room1.des, Room2.des", staged_rooms=("Room0.des", "Room1.des", "Room2.des"))

    assert codes(result)["section_span"] == PASSED and codes(result)["op_schema_check"] == PASSED


def test_a_line_that_names_a_fault_crosscheck_didnt_flag_is_could_not_verify() -> None:
    sections = {**CLEAN, "notch_check": "Room1.des\n   !! WM Panel 48 Thru         WM banding not taped + locked on"
                                        " top/front/bottom"}

    assert codes(read(sections))["notch_check"] == COULD_NOT_VERIFY


def test_a_checker_with_no_reader_is_could_not_verify(monkeypatch) -> None:
    plan = (*PLAN, Planned("new_check", False))
    sections = {**CLEAN, "new_check": "ALL GOOD"}
    text = crosscheck_file(sections, plan=plan)

    result = read_crosscheck(text, ToolRun(0, stdout_of(text), "", False), plan=plan,
                             rooms=("Room0.des", "Room1.des"), library_found=True)

    assert codes(result)["new_check"] == COULD_NOT_VERIFY and result.code == COULD_NOT_VERIFY


@pytest.mark.parametrize("name, kwargs", [
    ("no file", dict(text="")),
    ("exit 1 on clean", dict(exit_code=1)),
    ("exit 2", dict(exit_code=2)),
    ("an error", dict(stderr="Traceback (most recent call last):\n  oops\n")),
    ("other rooms", dict(rooms="Room0.des, Room1.des, Room3.des")),
    ("other sections", dict(plan=PLAN[1:])),
])
def test_a_run_that_isnt_what_crosscheck_prints_is_could_not_verify_throughout(name: str, kwargs: dict) -> None:
    result = read(**kwargs)

    assert result.code == COULD_NOT_VERIFY
    assert set(codes(result).values()) <= {COULD_NOT_VERIFY}


def test_a_flag_list_that_doesnt_fit_the_sections_is_could_not_verify() -> None:
    text = crosscheck_file(CLEAN).replace("CROSSCHECK CLEAN", "CROSSCHECK FLAGGED: section_span")
    run = ToolRun(1, stdout_of(text), "", False)

    result = read_crosscheck(text, run, plan=PLAN, rooms=("Room0.des", "Room1.des"), library_found=True)

    assert codes(result)["section_span"] == COULD_NOT_VERIFY       # flagged, and no line names a fault


def test_python_3_14s_warning_about_utcnow_is_the_one_thing_it_may_say_on_stderr() -> None:
    warning = ("C:\\copy\\scripts\\crosscheck.py:61: DeprecationWarning: datetime.datetime.utcnow() is deprecated and"
               " scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in"
               " UTC: datetime.datetime.now(datetime.UTC).\n"
               "  out = [stamp(datetime.datetime.utcnow())]\n")    # Python quotes the line; this one is made up

    assert read(stderr=warning).code == PASSED
    assert read(stderr=warning + "and more\n").code == COULD_NOT_VERIFY


def test_a_timed_out_run_is_could_not_verify() -> None:
    text = crosscheck_file(CLEAN)

    result = read_crosscheck(text, ToolRun(None, "", "", True), plan=PLAN, rooms=("Room0.des", "Room1.des"),
                             library_found=True)

    assert result.code == COULD_NOT_VERIFY


def test_a_single_checker_reader_fails_closed_on_empty_output() -> None:
    assert read_checker("section_span", "", flagged=False, product_rooms=1, all_rooms=2,
                        library_found=True).code == COULD_NOT_VERIFY


# ----- the plan, read from crosscheck.py by ast and never imported


def test_the_plan_is_read_from_crosscheck_py_without_running_it(tmp_path: Path) -> None:
    source = tmp_path / "_NG_Automation" / "scripts"
    source.mkdir(parents=True)
    (source / "crosscheck.py").write_text(
        "raise SystemExit('importing me is running me')\n"
        "CHECKS = [\n"
        "    (\"first_check\", lambda j, c: [j], False),\n"
        "    (\"advice\",      lambda j, c: [c], True),\n"
        "]\n", encoding="utf-8")
    copy = take_snapshot(source.parent, tmp_path / "data" / "toolkit" / "x", clock=FakeClock(NOW))

    assert cc.planned(copy) == (Planned("first_check", False), Planned("advice", True))


def test_a_crosscheck_py_whose_plan_cant_be_read_has_none(tmp_path: Path) -> None:
    source = tmp_path / "_NG_Automation" / "scripts"
    source.mkdir(parents=True)
    (source / "crosscheck.py").write_text("CHECKS = make_them()\n", encoding="utf-8")
    copy = take_snapshot(source.parent, tmp_path / "data" / "toolkit" / "x", clock=FakeClock(NOW))

    assert cc.planned(copy) == ()


def test_the_library_wm_variant_check_reads_is_found_or_not_as_the_script_would(tmp_path: Path) -> None:
    source = tmp_path / "_NG_Automation" / "scripts"
    source.mkdir(parents=True)
    library = tmp_path / "Products"
    library.mkdir()
    for lib, found in ((r"/mnt/user-data/uploads/Mozaik Shared/Product Libraries/NG Closets/Products", False),
                       (str(library), True)):
        (source / "wm_variant_check.py").write_text(f"import os\nLIB = r\"{lib}\"\n", encoding="utf-8")
        copy = take_snapshot(source.parent, tmp_path / "data" / "toolkit" / f"x{found}", clock=FakeClock(NOW))
        assert cc.library_found(copy) is found


# ----- staging a job, and never running in place


@pytest.fixture
def job(tmp_path: Path):
    folder = tmp_path / "Mozaik Shared" / "Jobs" / JOB
    folder.mkdir(parents=True)
    for name in ("Room0.des", "Room1.des", "Room10.des", "Room2.des"):
        (folder / name).write_bytes(b"15\r\n<?xml version=\"1.0\"?>" + name.encode())
    (folder / "NG Closets-JobParms.dat").write_bytes(b"parms")
    (folder / "3-4 Egger Classic White.opt").write_bytes(b"8\r\n<?xml?>")
    conversion = tmp_path / "NG Jobs" / JOB / f"{JOB}_IC_Conversion.json"
    conversion.parent.mkdir(parents=True)
    conversion.write_text("{}", encoding="utf-8")
    template = tmp_path / "Mozaik Shared" / "Jobs" / "Lux Closet Template Claude"
    template.mkdir()
    (template / "NG Closets-JobParms.dat").write_bytes(b"template parms")
    (template / "Room1.des").write_bytes(b"not wanted")
    return folder, conversion, template


def test_a_job_is_staged_as_crosscheck_wants_it_and_the_originals_are_left(job, tmp_path: Path) -> None:
    folder, conversion, template = job
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()}

    staged = stage_job(folder, conversion, tmp_path / "data" / "staging" / "x", template=template)

    assert staged.job.name == JOB and staged.job.parent.name == "mozaik"
    assert sorted(p.name for p in staged.job.iterdir()) == ["NG Closets-JobParms.dat", "Room0.des", "Room1.des",
                                                             "Room10.des", "Room2.des"]
    assert [name for name, _ in staged.rooms] == ["Room0.des", "Room1.des", "Room2.des", "Room10.des"]
    assert dict(staged.rooms)["Room1.des"] == hashlib.sha256((folder / "Room1.des").read_bytes()).hexdigest()
    assert staged.conversion.parent == staged.ngjob and staged.conversion.read_text(encoding="utf-8") == "{}"
    assert sorted(p.name for p in (staged.job.parent / "Lux Closet Template Claude").iterdir()) == [
        "NG Closets-JobParms.dat"]
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()} == before


def test_a_staging_folder_inside_the_job_or_beside_its_conversion_is_refused(job) -> None:
    folder, conversion, _ = job
    for into in (folder / "stage", conversion.parent / "stage"):
        with pytest.raises(ToolkitError, match="in place"):
            stage_job(folder, conversion, into)
        assert not into.exists()


def test_a_job_with_no_rooms_is_refused(job, tmp_path: Path) -> None:
    folder, conversion, _ = job
    empty = tmp_path / "Mozaik Shared" / "Jobs" / "Nothing_101426"
    empty.mkdir()

    with pytest.raises(ToolkitError):
        stage_job(empty, conversion, tmp_path / "data" / "staging" / "x")


FAKE_CROSSCHECK = """
import os, sys
job, ngjob, conv = sys.argv[1:4]
open(os.environ["NGR_TEST_ARGV"], "w", encoding="utf-8").write(repr(sys.argv[1:]))
name = os.path.basename(os.path.normpath(job))
text = open(os.environ["NGR_TEST_FILE"], encoding="utf-8").read()
open(os.path.join(ngjob, name + "_crosscheck.txt"), "w", encoding="utf-8").write(text)
print(text.splitlines()[3].strip()); print("written:", os.path.join(ngjob, name + "_crosscheck.txt"))
sys.exit(0)
"""


def test_crosscheck_runs_on_the_staged_copy_never_in_place_and_leaves_nothing(job, tmp_path: Path,
                                                                              monkeypatch) -> None:
    folder, conversion, template = job
    source = tmp_path / "_NG_Automation" / "scripts"
    source.mkdir(parents=True)
    (source / "crosscheck.py").write_text(FAKE_CROSSCHECK + "CHECKS = [\n" + "".join(
        f"    ({p.name!r}, lambda j, c: [j], {p.advisory}),\n" for p in PLAN) + "]\n", encoding="utf-8")
    (source / "wm_variant_check.py").write_text(f"LIB = r\"{tmp_path}\"\n", encoding="utf-8")
    copy = take_snapshot(source.parent, tmp_path / "data" / "toolkit" / "x", clock=FakeClock(NOW))
    said = tmp_path / "file.txt"
    said.write_text(crosscheck_file(CLEAN, rooms="Room0.des, Room1.des, Room2.des, Room10.des"), encoding="utf-8")
    argv = tmp_path / "argv.txt"
    monkeypatch.setenv("NGR_TEST_FILE", str(said))
    monkeypatch.setenv("NGR_TEST_ARGV", str(argv))
    work = tmp_path / "data" / "staging"
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()}

    result = run_crosscheck(copy, folder, conversion, work, template=template)

    given = [Path(p) for p in eval(argv.read_text(encoding="utf-8"))]
    assert all(work in p.parents for p in given)              # staged copies only, never the job itself
    assert given[0].name == JOB and given[2].name == conversion.name
    assert result.snapshot_sha256 == copy.sha256
    assert [name for name, _ in result.rooms] == ["Room0.des", "Room1.des", "Room2.des", "Room10.des"]
    assert result.conversion_sha256 == hashlib.sha256(b"{}").hexdigest()
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in folder.iterdir()} == before
    assert not any(work.iterdir())                            # what it staged goes when it ends
    assert result.ran == tuple(p.name for p in PLAN)


def test_a_crosscheck_that_cant_run_is_could_not_verify(job, tmp_path: Path) -> None:
    folder, conversion, _ = job
    source = tmp_path / "_NG_Automation" / "scripts"
    source.mkdir(parents=True)
    (source / "crosscheck.py").write_text("import sys\nsys.exit('no IC Conversion - crosscheck cannot run')\n"
                                          "CHECKS = [('a', None, False)]\n", encoding="utf-8")
    copy = take_snapshot(source.parent, tmp_path / "data" / "toolkit" / "x", clock=FakeClock(NOW))

    result = run_crosscheck(copy, folder, conversion, tmp_path / "data" / "staging")

    assert result.code == COULD_NOT_VERIFY and result.verdicts and {v.code for v in result.verdicts} == {COULD_NOT_VERIFY}


def test_a_work_folder_that_isnt_on_a_local_disk_runs_nothing(job, tmp_path: Path, monkeypatch) -> None:
    from ngrunner import toolkit
    import win32file

    folder, conversion, _ = job
    monkeypatch.setattr(toolkit, "_volume", lambda root: (win32file.DRIVE_FIXED, "FAT32"))

    with pytest.raises(ToolkitError, match="local disk"):
        stage_job(folder, conversion, tmp_path / "data" / "staging" / "x")
