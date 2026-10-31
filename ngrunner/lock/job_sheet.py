r"""
The CNC job sheet before a lock (RULES section 7; design 3.5's Lock stage): Jim's cnc_job_sheet.py run
on a staged copy of the job, what it really wrote read back, and its sheet counts set against NG
Runner's own reading of the same nests. Filed in the job's NG Jobs folder only when every count agrees
and there is no sheet there yet (design 3.2: "the job sheet when absent"; RULES: "Never overwrite one
Jim already made").

Why compare. The sheet is what the CNC operator cuts from, and its counts come from the nests by
pattern matching (his script reads them with regular expressions, his comment says "never from the
takeoff"). NG Runner reads the very same nests with its own reader (guards/opt.py, which the 20 real
nests settled): the sheets per nest, flip passes not counted (RULES 716-721), and each nest's thickness
from its own header, not its name. Where the two disagree, or the template has no line for a nest (it
has two 3/4 lines, one 1/4 and one 1in line), that is an item for Jim. Nothing is ever corrected.

How it runs (ruling R1). The job's JobDat.dat and nests and the IC Conversion are copied into a folder
of NG Runner's own on this PC's disk, his script runs from the adopted toolkit copy in a Job Object, and
its .docx is read there. Its output is read failing closed: a run that didn't finish cleanly, no file,
or a file that isn't a job sheet is could_not_verify, and so is a nest NG Runner can't read.

Its words name thickness, colour and counts only: the sheet's job name, install date and Special Notes
are never kept or shown.
"""
from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from ngrunner.clock import Clock
from ngrunner.guards.opt import OptUnreadable, parsed_opt, read_opt
from ngrunner.guards.preflight import JobSheet, read_job_sheet
from ngrunner.guards.reads import Unreadable, read_shared
from ngrunner.lock import P4_SHADOW
from ngrunner.store import Store
from ngrunner.toolkit import Snapshot, ToolkitError, refuse_unless_local, run_tool, stage_files
from ngrunner.writes import CommitResult, FileWrite, Roots, commit_recorded

JOB_SHEET_SCRIPT = "scripts/cnc_job_sheet.py"
JOB_SHEET_SUFFIX = " CNC Job Sheet.docx"
MATCHES, MISMATCH, COULD_NOT_VERIFY = "matches", "mismatch", "could_not_verify"
_JOB_FILES = ("JobDat.dat",)
_THICKNESS_MM = (("3/4", 18.5, 20.5), ("1/4", 5.0, 7.5), ("1", 24.5, 27.0))
_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_ONE_INCH = re.compile(r"\s*1[”\"″]\s*:\s*(.*)")
_ONE_INCH_COUNT = re.compile(r"([0-9]+) (\S.*)")


@dataclass(frozen=True)
class NestCount:
    """One nest as NG Runner reads it: its file's stem, its thickness from its own header, its sheets."""

    stem: str
    thickness: str
    sheets: int


@dataclass(frozen=True)
class SheetItem:
    """A count the sheet and the nests don't agree on. sheet_count or nest_count is None where that
    side has no line for it."""

    thickness: str
    colour: str
    sheet_count: int | None
    nest_count: int | None
    words: str


@dataclass(frozen=True)
class JobSheetRun:
    code: str
    items: tuple[SheetItem, ...]
    docx: bytes | None
    words: str


class _NotVerified(Exception):
    pass


def make_job_sheet(snapshot: Snapshot, job: Path, conversion: Path, *, work: Path, day: str, date: str,
                   run: Callable[..., Any] = run_tool, python: Path | None = None,
                   read: Callable[[Path], bytes] = read_shared) -> JobSheetRun:
    """Jim's job sheet for the Mozaik job folder job, made on a staged copy and compared with the
    nests. day and date are the install day as the Fab Schedule has them."""
    try:
        refuse_unless_local(work, "A job sheet's staged copy")
        work.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="jobsheet-", dir=work))
    except (ToolkitError, OSError) as exc:
        return _not_verified(f"The job sheet could not be staged: {exc}")
    try:
        nests = sorted(p for p in job.iterdir() if p.suffix.casefold() == ".opt")
        staged_job = stage / "mozaik" / job.name
        copies = stage_files([job / name for name in _JOB_FILES] + nests, staged_job, read=read)
        staged_conversion, = stage_files([conversion], stage / "ngjob", read=read)
        out = stage / "out" / f"{job.name}{JOB_SHEET_SUFFIX}"
        out.parent.mkdir()
        said = run(snapshot, JOB_SHEET_SCRIPT, [str(staged_job), str(staged_conversion), day, date, str(out)],
                   python=python)
        if said.timed_out or said.exit_code != 0 or said.stderr.strip() or not out.is_file():
            return _not_verified("Jim's job sheet script didn't finish cleanly")
        docx = out.read_bytes()
        counts = nest_counts({p.stem: p.read_bytes() for p in copies[len(_JOB_FILES):]})
        sheet = read_job_sheet(out)
        if sheet is None or not sheet.counts:
            return _not_verified("What Jim's job sheet script wrote isn't a job sheet NG Runner can read")
        items = compare(sheet, _one_inch_line(docx), counts)
    except (ToolkitError, OSError, Unreadable) as exc:
        return _not_verified(f"The job sheet could not be made: {exc}")
    except _NotVerified as exc:
        return _not_verified(str(exc))
    finally:
        shutil.rmtree(stage, ignore_errors=True)        # made by this call
    if items:
        return JobSheetRun(MISMATCH, items, docx, "The job sheet's counts don't all match the nests")
    return JobSheetRun(MATCHES, (), docx, "The job sheet's counts match the nests")


def nest_counts(nests: Mapping[str, bytes]) -> list[NestCount]:
    """Each nest with sheets, as NG Runner reads it. A nest with parts and no sheets isn't on the
    sheet, as it isn't cut yet. One that can't be read stops the comparison."""
    found = []
    for stem, data in sorted(nests.items()):
        try:
            facts, root = read_opt(data), parsed_opt(data)
        except OptUnreadable as exc:
            raise _NotVerified(f"A nest could not be read: {exc}") from exc
        if facts.physical_sheets:
            found.append(NestCount(stem, _thickness(root.get("Thickness")), facts.physical_sheets))
    return found


def compare(sheet: JobSheet, one_inch: str | None, counts: list[NestCount]) -> tuple[SheetItem, ...]:
    """Every count the sheet and the nests don't agree on."""
    lines = [(c.thickness, c.color.strip(), c.sheets) for c in sheet.counts
             if c.thickness != "1" and (c.color.strip() or c.sheets is not None)]
    lines += _one_inch_counts(one_inch)
    items: list[SheetItem] = []
    used: set[int] = set()
    for nest in counts:
        match = [i for i, (thk, colour, _) in enumerate(lines)
                 if i not in used and thk == nest.thickness and colour and nest.stem.endswith(colour)]
        colour = _colour(nest.stem)
        if len(match) != 1:
            items.append(SheetItem(nest.thickness, colour, None, nest.sheets,
                                   f"{_inches(nest.thickness)} {colour}: {nest.sheets} sheets in the nest,"
                                   " and no line for it on the job sheet"))
            continue
        used.add(match[0])
        count = lines[match[0]][2]
        if count != nest.sheets:
            shown = "no count" if count is None else f"{count} sheets"
            items.append(SheetItem(nest.thickness, colour, count, nest.sheets,
                                   f"{_inches(nest.thickness)} {colour}: the job sheet says {shown}, the"
                                   f" nest has {nest.sheets}"))
    for i, (thk, colour, count) in enumerate(lines):
        if i not in used:
            items.append(SheetItem(thk, colour, count, None,
                                   f"{_inches(thk)} {colour or 'with no colour'}: on the job sheet, and no"
                                   " nest has it"))
    return tuple(items)


def write_job_sheet(folder: str, made: JobSheetRun, *, store: Store, roots: Roots,
                    interlock: Callable[[list[Path]], str | None], clock: Clock) -> CommitResult:
    """File a sheet whose counts all match, as <Job> CNC Job Sheet.docx, only where there is none."""
    if made.code != MATCHES or made.docx is None:
        return CommitResult(ok=False, reason="Only a job sheet whose counts match the nests is filed.")
    path = roots.ng_jobs / folder / f"{folder}{JOB_SHEET_SUFFIX}"
    return commit_recorded([FileWrite(path, made.docx, None)], store=store, clock=clock, roots=roots,
                           interlock=interlock, phase=P4_SHADOW)


# ----- reading


def _one_inch_line(docx: bytes) -> str | None:
    """The 1in line's text: its counts are typed with their colours, which read_job_sheet leaves."""
    try:
        with zipfile.ZipFile(BytesIO(docx)) as document:
            root = ElementTree.fromstring(document.read("word/document.xml"))
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise _NotVerified("What Jim's job sheet script wrote isn't a job sheet NG Runner can read") from exc
    for paragraph in root.iter(f"{_WORD}p"):
        text = "".join(run.text or "" for run in paragraph.iter(f"{_WORD}t"))
        found = _ONE_INCH.fullmatch(text)
        if found:
            return found[1].strip()
    return None


def _one_inch_counts(text: str | None) -> list[tuple[str, str, int | None]]:
    if not text:
        return []
    found = _ONE_INCH_COUNT.fullmatch(text)
    if found is None or re.search(r" [0-9]+ ", found[2]):
        raise _NotVerified("The job sheet's 1in line could not be read")
    return [("1", found[2].strip(), int(found[1]))]


def _thickness(text: str | None) -> str:
    try:
        mm = float(text or "")
    except ValueError:
        raise _NotVerified("A nest doesn't say how thick its sheets are") from None
    for name, low, high in _THICKNESS_MM:
        if low <= mm <= high:
            return name
    raise _NotVerified("A nest's sheets are a thickness the job sheet has no line for")


def _colour(stem: str) -> str:
    """A nest's colour as a person reads it: its name less its gauge."""
    return re.sub(r"^(3-4|1-4|1 in)\s*", "", stem)


def _inches(thickness: str) -> str:
    return {"3/4": '3/4"', "1/4": '1/4"', "1": '1"'}.get(thickness, thickness)


def _not_verified(words: str) -> JobSheetRun:
    return JobSheetRun(COULD_NOT_VERIFY, (), None, words)
