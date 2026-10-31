r"""
What Jim's two nest checks said, read from what they print (P1b build spec 5.13).

Written from the format strings in sheet_size_check and br_cam_check (the toolkit copy of 2026-10-31),
and checked against what both printed when Sulaiman ran them on copies of the 10 real jobs that day
(tests/test_guards_tool_checks.py, marked fixtures, has each case). NG Runner itself has not run them:
a run inside the app goes through toolkit.run_tool, on staged copies, once a copy is adopted.

Anything but what those lines print is could_not_verify, and so is an exit code that doesn't fit what
was printed: nothing here can make a pass of a line it hasn't seen. A run may hold several materials,
as each tool takes several .opt; each gets its own answer, keyed by the name the tool printed.

Why not just the exit code. It is wrong both ways, measured on the 10 real jobs:
- sheet_size_check --taps exits 1 on all 10, almost all from its reconcile lines, which fire on every
  merged program an operator posts: by its exit code every job would be DO NOT CUT. Those lines are only
  a log line here (watch.py logs a program the nest doesn't name), and a program the nest names that
  isn't on disk says nothing about the size: not a pass, and not a fault, as guards/opt.py has it.
- It says "ok" of a nest with no sheets (Case I), since it reads the header only, and it prints ?? and
  exits 0 for a file with no <OptimizeMaterial>. So an "ok" counts only beside a count of at least one
  physical sheet, which only --taps prints, and a ?? is could_not_verify.
- It reads one size per .opt, the first <OptimizeMaterial>'s, and never a sheet's own: a sheet Jim adds
  by hand is a block of its own size, so it can't see one (Case B's three 61x109 sheets on a 49x97
  header, Case H's six 49x120). NG Runner's program check sees those, and it misses a header on the wrong
  sheet whose parts fit (Case E's 1/4 and Case H's 1in). So both run, either's fault is DO NOT CUT, and a
  disagreement goes to DEV (guards/watch.py).
- br_cam_check --nest's exit code matches what it printed on all 10. Its counts of doubled holes equal
  NG Runner's program reader's exactly, material by material: 16, 36, 8 and 40, and 0 on the other 16.

A person sees only the numbers from these lines, in NG Runner's own words, as a material's name can
hold anything.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from ngrunner.guards.facts import COULD_NOT_VERIFY, FAILED, PASSED, ToolRun
from ngrunner.messages import templates as words

Answer = tuple[str, str]                 # (code, its line)
_NOT_SURE: Answer = (COULD_NOT_VERIFY, "")

_SIZE = re.compile(r'   (ok|!!) (.{44}) nested (\d+)" x (\d+)"   expected (\d+)" x (\d+)"')
_FIX_THE_SIZE = ("        -> fix the SHEET SIZE on this material in the library, then re-optimize"
                 " and regenerate its TAP. Do not cut this material as nested.")
_NO_HEADER = re.compile(r"   \?\? .+ - no OptimizeMaterial element \(wrong codec\?\)")
_RUN_COUNTS = re.compile(
    r"      (\d+) physical sheet\(s\) on \d+ pattern\(s\), \d+ flip pass\(es\), \d+ g-code run\(s\)")
_SHEET_COUNT = re.compile(r"      SHEET COUNT FOR THE TRACKER AND THE JOB SHEET = (\d+)")
_NO_FOLDER = re.compile(r"      !! no folder .+ - the g-code was never posted")
_ALL_THERE = re.compile(r"      ok every program the nest names is on disk, nothing stale: .*")
_MISSING = re.compile(r"      !! .+ is named by the nest and is NOT on disk")
_STALE = re.compile(r"      !! .+ is on disk and the nest does not name it - stale\?")

_NEST_NAME = re.compile(r"(\S.*)\.opt", re.IGNORECASE)
_CLEAN = "   no doubled housings in this nest"
_TWIN = re.compile(r"   !! .{7,} .{16,} TWO \S+mm HOLES ON TOP OF EACH OTHER at X=\S+/\S+ Y=\S+"
                   r" - \d+\.\d{3}mm apart \(x(\d+) on the sheet\)")
_FIX_THE_TEMPLATES = ("        -> the NEST doubles the housing even though the .des is clean: two joint"
                      " fastener templates match these sections. Fix the templates and RE-OPTIMIZE;"
                      " do not cut this nest.")


# ----- sheet_size_check <one or more .opt> --taps <NG Jobs job folder>


def sheet_size(run: ToolRun) -> Answer:
    """One staged .opt's answer, which is how NG Runner runs it."""
    return _the_one(sheet_size_by_material(run))


def sheet_size_by_material(run: ToolRun) -> dict[str, Answer]:
    """Each material's answer, by the name it printed (cut to 44 letters, as it prints them). Empty
    when the output isn't what the source prints, or the exit code doesn't fit it: nothing then."""
    if run.stderr.strip() or run.timed_out:
        return {}
    blocks = _sheet_blocks(run.stdout.splitlines())
    if blocks is None or not blocks:
        return {}
    exit_wanted = 1 if any(block.at_fault or block.programs != "all there" for block in blocks) else 0
    if run.exit_code != exit_wanted:
        return {}
    return {block.material: block.answer() for block in blocks}


class _SizeBlock:
    """What the tool printed of one material: its size line, and what --taps found."""

    def __init__(self, size: re.Match[str]) -> None:
        self.material = size[2].rstrip()
        self.at_fault = size[1] == "!!"
        self.on, self.expected = f"{size[3]}x{size[4]}", f"{size[5]}x{size[6]}"
        self.physical: int | None = None
        self.programs: str | None = None      # "all there", "only extras", "not there", None: no --taps

    def answer(self) -> Answer:
        if self.at_fault:
            return FAILED, words.nest_on_wrong_sheet(self.on, self.expected)
        if not self.physical or self.programs not in ("all there", "only extras"):
            return _NOT_SURE        # an ok of a nest with no sheets is of its header only
        return PASSED, ""


def _sheet_blocks(lines: list[str]) -> list[_SizeBlock] | None:
    blocks: list[_SizeBlock] = []
    at = 0
    while at < len(lines):
        size = _SIZE.fullmatch(lines[at])
        if size is None:
            return None     # ?? of a file, or a line the source doesn't print: no answer at all
        block = _SizeBlock(size)
        at += 1
        if block.at_fault:
            if lines[at:at + 1] != [_FIX_THE_SIZE]:
                return None
            at += 1
        at = _taps_block(lines, at, block)
        if at < 0:
            return None
        blocks.append(block)
    return blocks


def _taps_block(lines: list[str], at: int, block: _SizeBlock) -> int:
    """Where the next material starts, having read what --taps printed of this one; -1 when what it
    printed isn't what the source prints. A material with no --taps block is no pass."""
    counts = _RUN_COUNTS.fullmatch(lines[at]) if at < len(lines) else None
    if counts is None:
        return at
    sheets = _SHEET_COUNT.fullmatch(lines[at + 1]) if at + 1 < len(lines) else None
    if sheets is None or sheets[1] != counts[1]:
        return -1
    block.physical = int(counts[1])
    at += 2
    found: list[str] = []
    while at < len(lines) and not _SIZE.fullmatch(lines[at]):
        line = lines[at]
        if _ALL_THERE.fullmatch(line):
            found.append("all there")
        elif _NO_FOLDER.fullmatch(line) or _MISSING.fullmatch(line):
            found.append("missing")
        elif _STALE.fullmatch(line):
            found.append("extra")
        else:
            return -1
        at += 1
    if found == ["all there"]:
        block.programs = "all there"
    elif found and "all there" not in found:
        block.programs = "not there" if "missing" in found else "only extras"
    else:
        return -1
    return at


# ----- br_cam_check --nest <one or more .opt>


def br_cam_nest(run: ToolRun) -> Answer:
    """One staged .opt's answer, which is how NG Runner runs it."""
    return _the_one(br_cam_nest_by_material(run))


def br_cam_nest_by_material(run: ToolRun) -> dict[str, Answer]:
    """Each nest's answer, by its file's name less .opt. Empty when the output isn't what the source
    prints, or the exit code doesn't fit it."""
    if run.stderr.strip() or run.timed_out:
        return {}
    lines = run.stdout.splitlines()
    answers: dict[str, Answer] = {}
    at, doubled_anywhere = 0, False
    while at < len(lines):
        name = _NEST_NAME.fullmatch(lines[at])
        if name is None or name[1] in answers:
            return {}
        at += 1
        if lines[at:at + 1] == [_CLEAN]:
            answers[name[1]] = (PASSED, "")
            at += 1
            continue
        holes = 0
        while at < len(lines) and (twin := _TWIN.fullmatch(lines[at])):
            holes += int(twin[1])
            at += 1
        if lines[at:at + 1] != [_FIX_THE_TEMPLATES] or not holes:
            return {}
        answers[name[1]] = (FAILED, words.nest_holes_doubled(holes))
        doubled_anywhere = True
        at += 1
    if not answers or run.exit_code != (1 if doubled_anywhere else 0):
        return {}
    return answers


def _the_one(answers: Mapping[str, Answer]) -> Answer:
    """A staged copy is one material, so an answer about any other number is no answer."""
    return next(iter(answers.values())) if len(answers) == 1 else _NOT_SURE
