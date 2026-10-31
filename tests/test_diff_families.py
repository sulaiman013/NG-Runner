"""
The family diff (docs/p2/build-spec.md, contract 5.5): a dev-only, read-only report on Jim's
per-job scripts, made by ast and never by importing them.

Every script here is made up for the test, with made-up jobs, so none of Jim's code is copied in.
The real run's report names clients and stays outside the repo.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import ROOT
from test_tracker_inbox import writes_while

_spec = importlib.util.spec_from_file_location("ngr_tools_diff_families",
                                               ROOT / "tools" / "diff_families.py")
diff_families = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = diff_families      # dataclasses look their module up while it loads
_spec.loader.exec_module(diff_families)


def write(folder: Path, name: str, source: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


# ----- families


@pytest.mark.parametrize("name, family", [
    ("ic_conversion_testerson.py", "ic_conversion"),
    ("ic_conversion_0914.py", "ic_conversion"),
    ("ic_workbook_testerson.py", "ic_workbook"),
    ("tracker_testerson_lock.py", "tracker"),
    ("build_testerson_rooms.py", "build"),
    ("verify_testerson.py", "verify"),
    ("wb_generic.py", "wb"),
    ("run_testerson.py", "run"),
    ("panelcheck.py", "shared"),            # the shared tool; its per-job copies are a family
    ("panelcheck_testerson2.py", "panelcheck"),
    ("fix_testerson_depth.py", "fix"),
    ("harvest_final.py", "harvest"),
    ("swap_testerson.py", "swap"),
    ("takeoff.py", "shared"),
    ("wm_variant_check.py", "shared"),
    ("builder.py", "shared"),
    ("runner_helper.py", "shared"),
])
def test_a_script_belongs_to_the_family_its_name_starts_with(name: str, family: str) -> None:
    assert diff_families.family_of(name) == family


# ----- one script, read by ast

SCRIPT = '''
"""Made-up conversion for Testerson_101426, written 2026-09-06; revised 2026-09-11."""
import json
import os.path
from math import gcd
from takeoff import takeoff as run_takeoff
import openpyxl

MM = 25.4
TOL = 12
DEPTH_GRID = (305, 356, 406)
OUT = "/mnt/user-data/outputs/Testerson_101426_IC_Conversion.json"
LOCAL = r"C:\\Users\\someone\\OneDrive - Fake\\Mozaik Shared\\Jobs"
NAME = "out.json"
PARTS = [
    ("SH", 14, 23.5, "WH"),
    ("VT", 14, 84, "WH"),
]


def snap_depth(mm):
    """Nearest depth on the grid."""
    best = min(DEPTH_GRID, key=lambda g: abs(g - mm))
    if abs(best - mm) <= TOL:
        return best
    return mm


def main():
    data = [p for p in PARTS if p[0] == "SH"]
    with open(OUT, "w") as out:
        json.dump(data, out)
'''


@pytest.fixture
def script(tmp_path: Path):
    return diff_families.read_script(write(tmp_path, "ic_conversion_testerson.py", SCRIPT))


def test_a_script_is_read_with_its_family_functions_and_names(script) -> None:
    assert script.family == "ic_conversion"
    assert [f.name for f in script.functions] == ["snap_depth", "main"]
    assert script.jobs == ("Testerson_101426",)
    assert script.dates == ("2026-09-06", "2026-09-11")
    assert script.problem == ""


def test_imports_are_sorted_into_standard_toolkit_and_other(tmp_path: Path) -> None:
    write(tmp_path, "takeoff.py", "WASTE = 0.10\n")
    found = diff_families.read_script(write(tmp_path, "ic_conversion_testerson.py", SCRIPT),
                                      toolkit={"takeoff"})
    assert found.imports == {"standard": ("json", "math", "os.path"), "toolkit": ("takeoff",),
                             "other": ("openpyxl",)}


def test_hard_coded_paths_are_found_and_plain_names_are_not(script) -> None:
    assert script.paths == {
        "linux": ("/mnt/user-data/outputs/Testerson_101426_IC_Conversion.json",),
        "windows": ("C:\\Users\\someone\\OneDrive - Fake\\Mozaik Shared\\Jobs",)}


def test_literal_data_lines_are_told_from_logic_lines(script) -> None:
    # Data: the six one-line constants and the four-line parts list. Logic: the five imports,
    # both defs and their seven body lines. Docstrings and blank lines are neither.
    assert (script.data_lines, script.logic_lines) == (10, 14)


def test_module_constants_and_what_each_function_reads(script) -> None:
    assert script.constants["TOL"] == "12"
    assert script.constants["DEPTH_GRID"] == "(305, 356, 406)"
    (snap,) = [f for f in script.functions if f.name == "snap_depth"]
    assert snap.reads == ("DEPTH_GRID", "TOL")


def _function(source: str, name: str = "snap_depth"):
    tree = ast.parse(textwrap.dedent(source))
    (node,) = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    return diff_families.read_function(node)


BASE = '''
def snap_depth(mm):
    best = min(DEPTH_GRID, key=lambda g: abs(g - mm))
    return best if abs(best - mm) <= TOL else mm
'''


def test_a_docstring_alone_changes_the_text_but_not_the_code() -> None:
    a = _function(BASE)
    b = _function(BASE.replace("(mm):\n", '(mm):\n    """Nearest depth."""\n'))
    assert a.text_hash != b.text_hash
    assert (a.code_hash, a.shape_hash) == (b.code_hash, b.shape_hash)


def test_renamed_locals_change_the_code_but_not_the_shape() -> None:
    a = _function(BASE)
    b = _function(BASE.replace("mm", "v").replace("best", "c").replace("g ", "x ")
                  .replace("g:", "x:"))
    assert a.code_hash != b.code_hash
    assert a.shape_hash == b.shape_hash


def test_a_different_global_or_rule_changes_the_shape() -> None:
    a = _function(BASE)
    assert _function(BASE.replace("DEPTH_GRID", "DEPTH_SET")).shape_hash != a.shape_hash
    assert _function(BASE.replace("<= TOL", "< TOL")).shape_hash != a.shape_hash


def test_a_script_that_does_not_parse_is_reported_and_not_read(tmp_path: Path) -> None:
    broken = diff_families.read_script(write(tmp_path, "tracker_testerson.py", "def f(:\n"))
    assert broken.problem == "does not parse"
    assert broken.functions == ()


# ----- drifted helpers

OLD = '''
"""Made-up, 2026-09-04."""
TOL = 12
def snap_depth(mm):
    best = min(DEPTH_GRID, key=lambda g: abs(g - mm))
    return best if abs(best - mm) <= TOL else mm
'''
RENAMED = '''
"""Made-up, 2026-09-05."""
TOL = 12
def snap_depth(v):
    c = min(DEPTH_GRID, key=lambda g: abs(g - v))
    return c if abs(c - v) <= TOL else v
'''
NEWEST = '''
"""Made-up, 2026-09-20."""
TOL = 6
def snap_depth(mm):
    best = min(DEPTH_GRID, key=lambda g: abs(g - mm))
    if mm < 0:
        raise ValueError(mm)
    return best if abs(best - mm) <= TOL else mm
'''
OTHER_RULE = '''
"""Made-up, 2026-09-10."""
TOL = 12
def snap_depth(mm):
    best = min(DEPTH_GRID, key=lambda g: abs(g - mm))
    return best if abs(best - mm) < TOL else round(mm, 2)
'''


@pytest.fixture
def family(tmp_path: Path) -> Path:
    folder = tmp_path / "scripts"
    write(folder, "ic_conversion_testerson.py", OLD)
    write(folder, "ic_conversion_sampleton.py", RENAMED)
    write(folder, "ic_conversion_fakeley.py", NEWEST)
    write(folder, "ic_conversion_mockford.py", OTHER_RULE)
    write(folder, "ic_conversion_dummitt.py", OLD.replace("2026-09-04", "2026-09-03"))
    write(folder, "tracker_testerson.py", NEWEST.replace("ValueError(mm)", "ValueError(0)"))
    return folder


def _drift(folder: Path, name: str = "snap_depth", family: str = "ic_conversion", **kw):
    scripts = diff_families.analyse(folder)
    dates = diff_families.date_scripts(scripts, **kw)
    (found,) = [d for d in diff_families.drifts(scripts, dates)
                if d.name == name and d.family == family]
    return found


def test_a_drifted_helper_lists_each_body_with_its_files(family: Path) -> None:
    drift = _drift(family)
    assert len(drift.bodies) == 4             # OLD twice, RENAMED, NEWEST, OTHER_RULE
    assert sorted(len(b.files) for b in drift.bodies) == [1, 1, 1, 2]
    assert drift.shapes == 3                  # RENAMED is OLD with other names


def test_the_newest_body_is_the_one_in_the_newest_dated_file(family: Path) -> None:
    drift = _drift(family)
    newest = next(b for b in drift.bodies if b.text_hash == drift.newest)
    assert newest.files == ("ic_conversion_fakeley.py",)
    assert newest.date == ("2026-09-20", "date in its text")


def test_each_other_body_is_a_subset_of_the_newest_or_its_extra_lines_are_named(
        family: Path) -> None:
    drift = _drift(family)
    by_file = {b.files[0]: b for b in drift.bodies}
    old = by_file["ic_conversion_dummitt.py"]
    assert old.subset_of_newest
    assert old.only_here == ()
    assert old.only_in_newest == ("if mm < 0:", "raise ValueError(mm)")
    assert by_file["ic_conversion_sampleton.py"].subset_of_newest       # names don't count
    other = by_file["ic_conversion_mockford.py"]
    assert not other.subset_of_newest
    assert other.only_here == ("return best if abs(best - mm) < TOL else round(mm, 2)",)


def test_the_constants_a_helper_reads_are_compared_across_its_files(family: Path) -> None:
    drift = _drift(family)
    assert drift.constants == {"TOL": {"12": ("ic_conversion_dummitt.py",
                                              "ic_conversion_mockford.py",
                                              "ic_conversion_sampleton.py",
                                              "ic_conversion_testerson.py"),
                                       "6": ("ic_conversion_fakeley.py",)}}


def test_helpers_are_compared_within_their_family_and_across_all(family: Path) -> None:
    across = _drift(family, family="all")
    assert len(across.bodies) == 5
    assert "tracker_testerson.py" in {f for b in across.bodies for f in b.files}


def test_a_conversion_json_on_ng_jobs_dates_the_script_that_names_its_job(
        family: Path, tmp_path: Path) -> None:
    ng_jobs = tmp_path / "NG Jobs"
    json_file = ng_jobs / "Old" / "Mockford_051526" / "Mockford_051526_IC_Conversion.json"
    json_file.parent.mkdir(parents=True)
    json_file.write_text("{}", encoding="utf-8")
    stamp = datetime(2026, 9, 25, 12, tzinfo=timezone.utc).timestamp()
    os.utime(json_file, (stamp, stamp))
    write(family, "ic_conversion_mockford.py",
          OTHER_RULE.replace('"""Made-up', '"""Made-up for Mockford_051526'))
    drift = _drift(family, ng_jobs=ng_jobs)
    newest = next(b for b in drift.bodies if b.text_hash == drift.newest)
    assert newest.files == ("ic_conversion_mockford.py",)
    assert newest.date[1] == "its job's conversion JSON"


def test_no_date_or_a_tie_means_the_newest_is_not_told(tmp_path: Path) -> None:
    folder = tmp_path / "scripts"
    write(folder, "ic_conversion_testerson.py", OLD.replace('"""Made-up, 2026-09-04."""', ""))
    write(folder, "ic_conversion_sampleton.py", OTHER_RULE.replace('"""Made-up, 2026-09-10."""',
                                                                   ""))
    assert _drift(folder).newest is None
    write(folder, "ic_conversion_testerson.py", OLD.replace("09-04", "09-10"))
    write(folder, "ic_conversion_sampleton.py", OTHER_RULE)
    assert _drift(folder).newest is None


def test_a_helper_with_one_body_is_not_drifted(tmp_path: Path) -> None:
    folder = tmp_path / "scripts"
    write(folder, "ic_conversion_testerson.py", OLD)
    write(folder, "ic_conversion_sampleton.py", OLD)
    scripts = diff_families.analyse(folder)
    assert diff_families.drifts(scripts, diff_families.date_scripts(scripts)) == []


# ----- the report, and only the report, is written

def test_the_report_goes_only_to_its_folder(family: Path, tmp_path: Path) -> None:
    out = tmp_path / "report"
    writes = writes_while(lambda: diff_families.main(["--scripts", str(family),
                                                      "--out", str(out)]))
    assert writes and all(Path(path).parent == out or Path(path) == out for _, path in writes)
    assert sorted(p.name for p in out.iterdir()) == ["families.json", "families.md"]
    report = json.loads((out / "families.json").read_text(encoding="utf-8"))
    assert report["families"]["ic_conversion"]["files"] == 5
    assert "snap_depth" in (out / "families.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("inside", ["repo", "scripts"])
def test_the_report_is_refused_inside_the_repo_or_the_scripts(family: Path, tmp_path: Path,
                                                              inside: str, capsys) -> None:
    out = (ROOT / "out" / "p2diff-test") if inside == "repo" else (family / "report")
    assert diff_families.main(["--scripts", str(family), "--out", str(out)]) == 1
    assert not out.exists()
    assert "outside" in capsys.readouterr().err


# ----- never an import of Jim's code

_RAN: list[str] = []


def _audit(event: str, args: tuple) -> None:
    if event == "exec" and args and hasattr(args[0], "co_filename"):
        _RAN.append(args[0].co_filename)
    elif event == "import" and len(args) > 1 and args[1]:
        _RAN.append(os.fsdecode(args[1]))


sys.addaudithook(_audit)


def test_reading_scripts_never_imports_or_runs_them(tmp_path: Path) -> None:
    folder = tmp_path / "scripts"
    marker = tmp_path / "ran.txt"
    write(folder, "ic_conversion_testerson.py", f'''
        open({str(marker)!r}, "w").write("ran")
        import a_module_that_is_not_there
        raise SystemExit(3)
        def snap_depth(mm):
            return mm
    ''')
    write(folder, "takeoff.py", f'open({str(marker)!r}, "w").write("ran")\n')
    _RAN.clear()
    scripts = diff_families.analyse(folder)
    diff_families.drifts(scripts, diff_families.date_scripts(scripts))
    assert not marker.exists()
    assert not [path for path in _RAN if Path(path).is_relative_to(folder)]
    assert [s.name for s in scripts] == ["ic_conversion_testerson.py", "takeoff.py"]


def test_the_tool_has_no_way_to_run_code() -> None:
    tree = ast.parse((ROOT / "tools" / "diff_families.py").read_text(encoding="utf-8"))
    modules = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    modules |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not modules & {"importlib", "runpy", "subprocess", "imp", "pkgutil", "multiprocessing"}
    calls = [n.func for n in ast.walk(tree) if isinstance(n, ast.Call)]
    builtins = {f.id for f in calls if isinstance(f, ast.Name)}
    methods = {f.attr for f in calls if isinstance(f, ast.Attribute)}
    assert not builtins & {"exec", "eval", "compile", "__import__", "breakpoint"}
    assert not methods & {"exec", "eval", "import_module", "run_path", "run_module", "exec_module",
                          "system", "popen", "Popen", "spawnv", "startfile"}
    assert {f.value.id for f in calls if isinstance(f, ast.Attribute) and f.attr == "compile"
            and isinstance(f.value, ast.Name)} <= {"re"}       # patterns, never code
