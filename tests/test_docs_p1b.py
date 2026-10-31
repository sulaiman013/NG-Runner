"""
Phase 1b's stand-in rules say what Jim's RULES says, and its committed files name no client.

Why: AGENTS.md rule 4 says Jim's rules come from his toolkit, and until the toolkit is here the
sheet sizes and the near-pair limit are a stand-in typed from RULES. So a change to those words in
RULES must fail a test, or NG Runner would go on judging real programs by an old rule. The checks
read his spec snapshot (local only, NGR_SPEC_DIR, as tests/test_docs_p0.py does) and skip when it
is absent. Their failures show NG Runner's own lines and RULES' line numbers, never his text.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

from ngrunner.guards.sheets import RULES_LINES
from ngrunner.guards.tap import NEAR_IN

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(os.environ.get("NGR_FIXTURES", ROOT / ".local-fixtures"))
SPEC_DIR = Path(os.environ.get(
    "NGR_SPEC_DIR", r"C:\Sulaiman Automation - Assembly Reports\NG IC MOZAIK"))
SHEET_SIZE_HEADING = re.compile(r"^## Sheet size\b")
THIS_PHASE_FILES = (
    "tools/collect_p1b_fixtures.py", "ngrunner/guards/tap.py", "ngrunner/guards/sheets.py",
    "ngrunner/guards/reads.py", "tests/test_guards_tap.py", "tests/test_guards_sheets.py",
    "tests/test_guards_reads.py", "tests/test_docs_p1b.py",
    "ngrunner/guards/opt.py", "ngrunner/guards/observer.py", "ngrunner/guards/nestfiles.py",
    "ngrunner/toolkit.py", "tests/helpers_opt.py", "tests/test_guards_opt.py",
    "tests/test_guards_observer.py", "tests/test_guards_nestfiles.py", "tests/test_toolkit.py",
    "google/test/notchecked.test.js", "ngrunner/guards/tool_checks.py",
    "tests/test_guards_tool_checks.py",
)


@dataclass(frozen=True)
class RulesLine:
    """One material in a table of RULES' sheet-size section, with the line it is on."""

    colour: str
    material: str
    width: float
    length: float
    line: int


# ----- reading RULES' sheet-size tables


def _plain(cell: str) -> str:
    return cell.replace("**", "").strip()


def sheet_size_section(lines: list[str]) -> list[tuple[int, str]]:
    """The lines under RULES' sheet-size heading, up to the next heading, with line numbers."""
    start = next((no for no, line in enumerate(lines, 1) if SHEET_SIZE_HEADING.match(line)), None)
    if start is None:
        return []
    section = []
    for no, line in enumerate(lines[start:], start + 1):
        if line.startswith("#"):
            break
        section.append((no, line))
    return section


def rules_lines(lines: list[str]) -> list[RulesLine]:
    """Every material in those tables: each row's first cell is the colour, and each other cell
    names a material in backticks and its size as WxL. A cell of a dash has no material."""
    found = []
    for no, line in sheet_size_section(lines):
        if not line.startswith("|") or re.fullmatch(r"[|\s:-]+", line):
            continue
        colour, *cells = [_plain(cell) for cell in line.strip().strip("|").split("|")]
        for cell in cells:
            material = re.search(r"`([^`]+)`", cell)
            size = re.search(r"(\d+)\s*[×x]\s*(\d+)", cell)
            if material and size:
                found.append(RulesLine(colour, material[1], float(size[1]), float(size[2]), no))
    return found


def _codes_lines() -> set[tuple[str, str, float, float]]:
    return {(line.rules_colour, line.rules_material, *line.size) for line in RULES_LINES}


def mismatches(rules: list[RulesLine], ours: set[tuple[str, str, float, float]]) -> list[str]:
    """What differs between RULES' tables and the code's: its own lines, and RULES' line numbers."""
    theirs = {(r.colour, r.material, r.width, r.length): r.line for r in rules}
    problems = [f"sheets.py has a line RULES doesn't: {line}"
                for line in sorted(ours - set(theirs))]
    problems += [f"RULES line {theirs[key]} has no line in sheets.py"
                 for key in sorted(set(theirs) - ours)]
    return problems


# ----- the check itself finds what it is for


_SAMPLE = """\
## Something else
| not this | `3-4 Other` 10×10 |

## Sheet size — made up
Words.

| colour | 3/4 | 1 in |
|---|---|---|
| Egger **Maple** | `3-4 Egger` **49×97** | `1 in Egger` 49×97 |
| Tafisa (any colour) | `3-4 Tafisa` 61×109 | — |

| the job's colour | 1/4 back |
|---|---|
| Egger Maple | `1-4 Egger` Maple, **61×109** (5×9), library id 1 |

## Next
| not this either | `3-4 Other` 10×10 |
"""
_SAMPLE_LINES = {
    ("Egger Maple", "3-4 Egger", 49.0, 97.0), ("Egger Maple", "1 in Egger", 49.0, 97.0),
    ("Tafisa (any colour)", "3-4 Tafisa", 61.0, 109.0), ("Egger Maple", "1-4 Egger", 61.0, 109.0),
}


def test_the_tables_are_read_from_their_own_section():
    found = rules_lines(_SAMPLE.splitlines())
    assert {(r.colour, r.material, r.width, r.length) for r in found} == _SAMPLE_LINES
    assert [r.line for r in found] == [9, 9, 10, 14]


def test_the_check_finds_a_changed_size_a_new_line_and_a_lost_line():
    assert not mismatches(rules_lines(_SAMPLE.splitlines()), _SAMPLE_LINES)
    changed = _SAMPLE.replace("`3-4 Tafisa` 61×109", "`3-4 Tafisa` 49×97")
    assert len(mismatches(rules_lines(changed.splitlines()), _SAMPLE_LINES)) == 2
    added = _SAMPLE.replace("| Tafisa (any", "| Arauco | `3-4 Arauco` 49×97 | — |\n| Tafisa (any")
    assert len(mismatches(rules_lines(added.splitlines()), _SAMPLE_LINES)) == 1
    lost = _SAMPLE.replace("`1 in Egger` 49×97", "—")
    problems = mismatches(rules_lines(lost.splitlines()), _SAMPLE_LINES)
    assert problems == ["sheets.py has a line RULES doesn't: "
                        "('Egger Maple', '1 in Egger', 49.0, 97.0)"]


def test_a_rules_line_number_is_shown_but_never_its_words():
    added = _SAMPLE.replace("| Tafisa (any", "| Walnutty | `3-4 Secret` 49×97 | — |\n| Tafisa (any")
    (problem,) = mismatches(rules_lines(added.splitlines()), _SAMPLE_LINES)
    assert problem == "RULES line 10 has no line in sheets.py"


# ----- against Jim's real words


@pytest.fixture(scope="module")
def rules_text() -> list[str]:
    path = SPEC_DIR / "RULES.md"
    if not path.is_file():
        pytest.skip("Jim's spec snapshot is not here (set NGR_SPEC_DIR)")
    return path.read_text(encoding="utf-8-sig").splitlines()


@pytest.mark.fixtures
def test_every_sheet_size_line_is_in_rules_and_every_rules_line_is_here(rules_text):
    found = rules_lines(rules_text)
    assert found, "RULES' sheet-size section has moved: this check found no line in it"
    problems = mismatches(found, _codes_lines())
    assert not problems, "\n".join(problems)


@pytest.mark.fixtures
def test_the_near_pair_limit_is_rules_half_a_millimetre(rules_text):
    assert NEAR_IN == round(0.5 / 25.4, 4)
    said = any("within 0.5mm of each other" in line for line in rules_text)
    assert said, "RULES no longer says two holes within 0.5mm count as doubled: see tap.py"


@pytest.mark.fixtures
def test_rules_still_calls_different_diameters_a_counterbore(rules_text):
    """Why tap.py pairs one tool only: different tools at one spot aren't a doubled hole."""
    said = "of DIFFERENT diameter are a counterbore" in " ".join(rules_text)
    assert said, "RULES no longer says different diameters are a counterbore: see tap.py"


# ----- no client in what this phase commits


def _collector() -> ModuleType:
    """tools/ isn't a package: it's loaded by its path."""
    spec = importlib.util.spec_from_file_location("collect_p1b_fixtures",
                                                  ROOT / "tools" / "collect_p1b_fixtures.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_collector_only_writes_into_the_fixtures_folder(tmp_path):
    """It reads the drive only: a fixtures folder inside NG Jobs is refused, and nothing changes."""
    collector = _collector()
    ng_jobs = tmp_path / "NG Jobs"
    job = ng_jobs / "Testerson_101426" / "3-4 Egger Classic White"
    job.mkdir(parents=True)
    (job / "TESS01.TAP").write_bytes(b"made up")
    before = sorted(str(p) for p in ng_jobs.rglob("*"))
    with pytest.raises(collector.CollectError, match="read-only"):
        collector.collect({"D": "Testerson_101426"}, ng_jobs, ng_jobs / "taps")
    assert sorted(str(p) for p in ng_jobs.rglob("*")) == before


def test_the_collector_says_which_case_is_missing_and_names_no_job(tmp_path):
    collector = _collector()
    ng_jobs = tmp_path / "NG Jobs"
    ng_jobs.mkdir()
    with pytest.raises(collector.CollectError) as caught:
        collector.collect({"D": "Testerson_101426"}, ng_jobs, tmp_path / "taps")
    assert "Case D" in str(caught.value)
    assert "Testerson" not in str(caught.value)
    assert not (tmp_path / "taps").exists()


@pytest.mark.fixtures
def test_this_phases_files_name_no_job_from_the_case_key():
    key_file = FIXTURES / "p1b" / "case_key.json"
    if not key_file.is_file():
        pytest.skip("no case key here")
    jobs = json.loads(key_file.read_text(encoding="utf-8")).values()
    words = {word.casefold() for job in jobs for word in (job, job.split("_")[0])}
    named = [name for name in THIS_PHASE_FILES
             if any(word in (ROOT / name).read_text(encoding="utf-8").casefold() for word in words)]
    assert not named


def test_this_phases_files_are_not_ignored_and_the_fixtures_are():
    """The real programs stay out of git and the code goes in, whatever .gitignore grows to say."""
    git = ["git", "-C", str(ROOT), "check-ignore", "--quiet"]
    for name in THIS_PHASE_FILES:
        assert subprocess.run([*git, name], check=False).returncode == 1, name
    for local_only in (".local-fixtures/p1b/taps/index.json", ".local-fixtures/p1b/case_key.json",
                       ".local-fixtures/toolkit/20261031/NG_Automation_scripts/scripts/br_cam_check.py"):
        assert subprocess.run([*git, local_only], check=False).returncode == 0
