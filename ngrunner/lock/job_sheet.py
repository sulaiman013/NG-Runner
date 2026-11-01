r"""
The CNC job sheet before a lock (RULES section 7; design 3.5's Lock stage): Jim's cnc_job_sheet.py run
on a staged copy of the job, what it really wrote read back, and its sheet counts set against NG
Runner's own reading of the same nests. Filed in the job's NG Jobs folder only when every count agrees,
every sheet it calls a flip sheet is one, and there is no sheet there yet (design 3.2: "the job sheet
when absent"; RULES: "Never overwrite one Jim already made").

Why compare. The sheet is what the CNC operator cuts from, and its counts come from the nests by
pattern matching (his script reads them with regular expressions, his comment says "never from the
takeoff"). NG Runner reads the very same nests with its own reader (guards/opt.py, which the 20 real
nests settled): the sheets per nest, flip passes not counted (RULES 716-721), and each nest's thickness
from its own header, not its name. Where the two disagree, or the template has no line for a nest (it
has two 3/4 lines, one 1/4 and one 1in line), that is an item for Jim. Nothing is ever corrected.

Flip sheets. His script's Special Notes list "Flip sheets": every sheet block holding more than one
G-code run. RULES 716-722 say a flip is a Flipped="True" block, the same board turned over for its
second side, and that programs are counted apart from sheets: so a sheet with an extra run and no
flip pass (a remake, say) is no flip sheet. Measured on the 83 real nests here (2026-10-31): his rule
names 28 sheets, of which 9 have a flip pass, and 4 of the 10 real jobs A to J would get a sheet with a
wrong one on it. So every sheet the note names must have a Flipped="True" block in its nest, or the sheet
is an item for Jim and isn't filed (answered from Jim's own files, docs/decisions.md, 18). NG Runner
reads only that part of the notes, and keeps none of it.

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
from collections import defaultdict
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
_NOTES = re.compile(r"\s*Special Notes:\s*(.*)", re.DOTALL)
_FLIP_NOTE = "Flip sheets: "
_FLIP_ENTRY = re.compile(r"(?P<stem>\S.*) sheet (?P<n>[0-9]+)")


@dataclass(frozen=True)
class NestCount:
    """One nest as NG Runner reads it: its file's stem, its thickness from its own header, its sheets,
    and the PatternNumber of each sheet with a flip pass (a Flipped="True" block, RULES 716)."""

    stem: str
    thickness: str
    sheets: int
    flips: tuple[str, ...] = ()


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
        paragraphs = _paragraphs(docx)
        items = compare(sheet, _one_inch_line(paragraphs), counts)
        flips = flip_items(_flip_note(paragraphs), counts)
    except (ToolkitError, OSError, Unreadable) as exc:
        return _not_verified(f"The job sheet could not be made: {exc}")
    except _NotVerified as exc:
        return _not_verified(str(exc))
    finally:
        shutil.rmtree(stage, ignore_errors=True)        # made by this call
    if items:
        return JobSheetRun(MISMATCH, items + flips, docx, "The job sheet's counts don't all match the nests")
    if flips:
        return JobSheetRun(MISMATCH, flips, docx, "The job sheet calls sheets flip sheets that have no flip pass")
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
            flips = sorted({block.get("PatternNumber", "") for block in root.iter()
                            if _local(block.tag) == "OptimizeSheet" and block.get("Flipped") == "True"})
            found.append(NestCount(stem, _thickness(root.get("Thickness")), facts.physical_sheets, tuple(flips)))
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


def flip_items(listed: list[tuple[str, str]], counts: list[NestCount]) -> tuple[SheetItem, ...]:
    """Each nest whose sheets the job sheet calls flip sheets though the nest has no flip pass for
    them: a second G-code run is a program, not a flip (RULES 716-722). listed: (nest stem, sheet
    number) as the note names them."""
    nests = {count.stem: count for count in counts}
    wrong: dict[str, list[str]] = defaultdict(list)
    for stem, number in listed:
        if stem not in nests:
            raise _NotVerified("The job sheet's flip sheets note names a nest NG Runner didn't count")
        if number not in nests[stem].flips:
            wrong[stem].append(number)
    items = []
    for stem, numbers in wrong.items():
        nest, colour = nests[stem], _colour(stem)
        said = (f"sheet {numbers[0]} a flip sheet, and the nest has no flip pass for it" if len(numbers) == 1
                else f"sheets {', '.join(numbers[:-1])} and {numbers[-1]} flip sheets, and the nest has no flip pass"
                     " for them")
        items.append(SheetItem(nest.thickness, colour, None, None,
                               f"{_inches(nest.thickness)} {colour}: the job sheet calls {said}, only another G-code"
                               " run, which is a program, not a flip"))
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


def _paragraphs(docx: bytes) -> list[str]:
    """Each paragraph's text, in order."""
    try:
        with zipfile.ZipFile(BytesIO(docx)) as document:
            root = ElementTree.fromstring(document.read("word/document.xml"))
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise _NotVerified("What Jim's job sheet script wrote isn't a job sheet NG Runner can read") from exc
    return ["".join(run.text or "" for run in paragraph.iter(f"{_WORD}t")) for paragraph in root.iter(f"{_WORD}p")]


def _one_inch_line(paragraphs: list[str]) -> str | None:
    """The 1in line's text: its counts are typed with their colours, which read_job_sheet leaves."""
    for text in paragraphs:
        found = _ONE_INCH.fullmatch(text)
        if found:
            return found[1].strip()
    return None


def _flip_note(paragraphs: list[str]) -> list[tuple[str, str]]:
    """(nest stem, sheet number) for each sheet the Special Notes call a flip sheet, as his script
    writes them: notes joined by "; ", this one "Flip sheets: <nest> sheet <n>, ...". Nothing else of
    the notes is read or kept, as they can say anything."""
    notes = next((found[1] for text in paragraphs if (found := _NOTES.fullmatch(text))), "")
    flips = [note for note in notes.split("; ") if note.startswith(_FLIP_NOTE)]
    if not flips:
        return []
    if len(flips) > 1:
        raise _NotVerified("The job sheet's flip sheets note could not be read")
    listed = []
    for entry in flips[0][len(_FLIP_NOTE):].split(", "):
        found = _FLIP_ENTRY.fullmatch(entry)
        if found is None:
            raise _NotVerified("The job sheet's flip sheets note could not be read")
        listed.append((found["stem"], found["n"]))
    return listed


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


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _colour(stem: str) -> str:
    """A nest's colour as a person reads it: its name less its gauge."""
    return re.sub(r"^(3-4|1-4|1 in)\s*", "", stem)


def _inches(thickness: str) -> str:
    return {"3/4": '3/4"', "1/4": '1/4"', "1": '1"'}.get(thickness, thickness)


def _not_verified(words: str) -> JobSheetRun:
    return JobSheetRun(COULD_NOT_VERIFY, (), None, words)
