"""
Jim's cnc_job_sheet.py on the ten real jobs, Cases A to J, through ngrunner.lock.job_sheet: run from a
copy of his toolkit in the tools' Python, in a Job Object, on a staged copy of each job's JobDat.dat and
nests, what it wrote read back, and its counts set against NG Runner's own reading of the same nests.

Measured 2026-10-31: on all ten, every count on the sheet his script writes equals NG Runner's count of
that nest's sheets, flip passes left out, including Case B's sheets added by hand on another size and
Case J's two 3/4 colours, its 1/4 and its 1in. But on four, B, C, E and J, his Special Notes call a sheet
a flip sheet that has no flip pass, only another G-code run (question 18, RULES 716-722): those four are
items for Jim and aren't filed. Cases C and D hold the real flip passes, which his note names rightly.
The IC Conversion is a made-up one with no LED line: it only adds a note. Marked fixtures; nothing of
his output is shown, only codes, counts and sheet numbers.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from helpers_toolkit import CASES, job_folder, stage_tree, tools_python
from ngrunner.clock import FakeClock
from ngrunner.lock.job_sheet import MATCHES, MISMATCH, make_job_sheet, nest_counts
from ngrunner.toolkit import take_snapshot

pytestmark = pytest.mark.fixtures

NOW = datetime(2026, 10, 31, 13, 0, tzinfo=UTC)
SHEETS = {"A": 5, "B": 14, "C": 12, "D": 4, "E": 14, "F": 5, "G": 7, "H": 21, "I": 2, "J": 55}
# Sheets his note calls flip sheets with no flip pass in their nest, by the nest's thickness.
NOT_FLIPS = {"B": [("3/4", "1")], "C": [("1/4", "1"), ("3/4", "8")], "E": [("1/4", "2"), ("3/4", "8")],
             "J": [("3/4", "1 and 3"), ("3/4", "15")]}


@pytest.fixture(scope="module")
def copy(tmp_path_factory):
    tools_python()
    tree = stage_tree(tmp_path_factory.mktemp("jims"))
    return take_snapshot(tree, tmp_path_factory.mktemp("data") / "toolkit" / "copy", clock=FakeClock(NOW))


@pytest.fixture(scope="module")
def conversion(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("ngjob") / "Made_up_IC_Conversion.json"
    path.write_text('{"pulled_classified": []}', encoding="utf-8")
    return path


@pytest.mark.parametrize("case", CASES)
def test_jims_job_sheet_counts_equal_ng_runners_own_reading_of_the_nests(copy, conversion, tmp_path,
                                                                          case: str) -> None:
    job = job_folder(case)
    before = {p.name: p.read_bytes() for p in job.iterdir() if p.is_file()}

    made = make_job_sheet(copy, job, conversion, work=tmp_path / "work", day="Friday", date="10/30",
                          python=tools_python())

    counts_differ = [(i.thickness, i.sheet_count, i.nest_count) for i in made.items
                     if (i.sheet_count, i.nest_count) != (None, None)]
    assert counts_differ == []
    not_flips = sorted((i.thickness, re.search(r"calls sheets? (.+?) a? ?flip sheets?", i.words)[1])
                       for i in made.items)
    assert not_flips == NOT_FLIPS.get(case, [])
    assert made.code == (MISMATCH if case in NOT_FLIPS else MATCHES)
    assert made.docx is not None and made.docx[:2] == b"PK"
    counted = nest_counts({p.stem: p.read_bytes() for p in job.glob("*.opt")})
    assert sum(c.sheets for c in counted) == SHEETS[case]
    assert {p.name: p.read_bytes() for p in job.iterdir() if p.is_file()} == before   # only ever read
