r"""
Jim's crosscheck.py, run from the adopted toolkit copy on a staged copy of one job, and what it wrote read
into a verdict per checker (P2 build spec 2 and 3.4, ruling R1).

Why staged. crosscheck takes <Mozaik job folder> <NG Jobs job folder> [IC Conversion], writes
<Job>_crosscheck.txt into the second, and runs each checker on the job's Room#.des. Run on the real
folders it would write into NG Jobs on G: and read rooms Mozaik may be saving. So the rooms, the job's
parameter files and the IC Conversion are copied into a folder of NG Runner's own, read with every
share flag, and crosscheck is given only those copies: never the job in place, never G:. parm_check
compares with the "Lux Closet Template Claude" job beside the job, so its parameter files are staged
beside the copy when there is one; with none it compares nothing, and says clean (readers.py).

What it runs. crosscheck's own CHECKS list, read from its source by ast and never imported, is the plan:
14 checkers on the copy of 2026-10-31, ic_code_check advisory. INDEX says it runs every checker; it
leaves out hinge_check, sheet_size_check and br_cam_check --nest (stage map D17, question Q17a). Those
are LEFT_OUT, named on every result. The two nest checks run in the guards (toolkit.nest_checks_from);
hinge_check, garage only, runs nowhere yet.

How it is read, failing closed. Only what crosscheck writes and prints is taken, and only when it all
fits: its exit code with its verdict line, its rooms line with the rooms staged, its sections with its
own list, its flag list with the sections, and nothing on stderr but Python 3.14's warning about
datetime.utcnow() in crosscheck itself. Anything else is could_not_verify for every checker. Each
checker's section is then read by readers.py.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ngrunner.checks.readers import CheckerVerdict, read_checker
from ngrunner.guards.facts import COULD_NOT_VERIFY, FAILED, PASSED, ToolRun
from ngrunner.guards.reads import read_shared
from ngrunner.toolkit import Snapshot, ToolkitError, refuse_unless_local, run_tool, stage_files

CROSSCHECK_SCRIPT = "scripts/crosscheck.py"
WM_VARIANT_SCRIPT = "scripts/wm_variant_check.py"
TEMPLATE_JOB = "Lux Closet Template Claude"      # crosscheck passes <job>\..\<this> to parm_check
LEFT_OUT = ("hinge_check", "sheet_size_check", "br_cam_check --nest")
CROSSCHECK_TIMEOUT_S = 600

_ROOM = re.compile(r"Room(\d+)\.des")
_PARMS = "-JobParms.dat"
_SECTION = re.compile(r"===== (\S+)(   \[ADVISORY\])?")
_VERDICT = re.compile(r"CROSSCHECK (?:CLEAN|FLAGGED: (?P<bad>\w+(?:, \w+)*))"
                      r"(?:   \(advisory only: (?P<advisory>\w+(?:, \w+)*)\))?")
_ROOMS_LINE = re.compile(r"  rooms   (.*)")
_UTCNOW = (re.compile(r".*[\\/]crosscheck\.py:\d+: DeprecationWarning: datetime\.datetime\.utcnow\(\) is"
                      r" deprecated and scheduled for removal in a future version\..*"),
           re.compile(r"\s+out = \[.*utcnow\(\).*"))


@dataclass(frozen=True)
class Planned:
    """One checker crosscheck runs, as its CHECKS list says: its name, and whether it is advisory."""

    name: str
    advisory: bool


@dataclass(frozen=True)
class StagedJob:
    """A job copied for crosscheck. job: the copy of its Mozaik folder (rooms and parameter files),
    ngjob: the folder crosscheck writes into, conversion: the copy of the IC Conversion. rooms and
    conversion_sha256: the bytes staged, which is what a verdict stands on."""

    job: Path
    ngjob: Path
    conversion: Path
    rooms: tuple[tuple[str, str], ...]
    conversion_sha256: str


@dataclass(frozen=True)
class Crosscheck:
    """What crosscheck said of one job. code: PASSED only when every checker that isn't advisory
    passed; FAILED when any found something to fix; else COULD_NOT_VERIFY. words: NG Runner's own.
    ran: the checkers it ran, in order; left_out: those INDEX names that it doesn't run. The rest say
    what it ran on: the copy, the rooms and the IC Conversion, by hash."""

    code: str
    words: str
    verdicts: tuple[CheckerVerdict, ...]
    ran: tuple[str, ...] = ()
    left_out: tuple[str, ...] = LEFT_OUT
    snapshot_sha256: str = ""
    rooms: tuple[tuple[str, str], ...] = ()
    conversion_sha256: str = ""


# ----- what the copy's crosscheck runs, read and never imported


def planned(snapshot: Snapshot) -> tuple[Planned, ...]:
    """crosscheck.py's CHECKS, as (name, advisory), by ast. () when it isn't a list of tuples whose
    first item is a name and third True or False: then nothing of it can be read."""
    try:
        tree = ast.parse((snapshot.path / CROSSCHECK_SCRIPT).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return ()
    for node in tree.body:
        if (isinstance(node, ast.Assign) and [getattr(t, "id", None) for t in node.targets] == ["CHECKS"]
                and isinstance(node.value, ast.List)):
            plan = [_planned(item) for item in node.value.elts]
            return () if None in plan or not plan else tuple(plan)       # type: ignore[arg-type]
    return ()


def _planned(item: ast.expr) -> Planned | None:
    if not isinstance(item, ast.Tuple) or len(item.elts) != 3:
        return None
    name, _, advisory = item.elts
    if (isinstance(name, ast.Constant) and isinstance(name.value, str)
            and isinstance(advisory, ast.Constant) and isinstance(advisory.value, bool)):
        return Planned(name.value, advisory.value)
    return None


def library_found(snapshot: Snapshot) -> bool:
    """Whether wm_variant_check's LIB is a folder as the script would see it, run from the copy's
    scripts folder. On the copy of 2026-10-31 it is a Linux path, which Windows reads on the copy's own
    drive, where it isn't: its library check is then skipped though it prints that it matches."""
    try:
        tree = ast.parse((snapshot.path / WM_VARIANT_SCRIPT).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return False
    for node in tree.body:
        if (isinstance(node, ast.Assign) and [getattr(t, "id", None) for t in node.targets] == ["LIB"]
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            return Path(os.path.join(str(snapshot.path / "scripts"), node.value.value)).is_dir()
    return False


# ----- staging


def stage_job(job: Path, conversion: Path, into: Path, *, template: Path | None = None,
              read: Callable[[Path], bytes] = read_shared) -> StagedJob:
    """Copies of a job's rooms and parameter files at into/mozaik/<job>, its IC Conversion in
    into/ngjob, and the template job's parameter files beside the job, as crosscheck looks for them.
    into must be new to the job: never inside it or beside the IC Conversion, and on a local disk."""
    refuse_unless_local(into, "A check's staged copy")
    for place in (job, conversion.parent):
        if _inside(into, place):
            raise ToolkitError("A job is checked on a staged copy, never in place, so nothing was staged.")
    try:
        names = sorted(os.listdir(job))
    except OSError as exc:
        raise ToolkitError(f"The job's folder could not be read: {exc.strerror or 'Windows would not allow it.'}") from exc
    rooms = sorted((name for name in names if _ROOM.fullmatch(name)), key=lambda n: int(_ROOM.fullmatch(n)[1]))
    if not rooms:
        raise ToolkitError("The job has no rooms to check.")
    parms = [name for name in names if name.endswith(_PARMS)]
    staged_job = into / "mozaik" / job.name
    copies = stage_files([job / name for name in (*rooms, *parms)], staged_job, read=read)
    staged_conversion, = stage_files([conversion], into / "ngjob", read=read)
    if template is not None and template.is_dir():
        stage_files([template / name for name in sorted(os.listdir(template)) if name.endswith(_PARMS)],
                    into / "mozaik" / TEMPLATE_JOB, read=read)
    hashes = tuple((copy.name, _sha256(copy)) for copy in copies[:len(rooms)])
    return StagedJob(staged_job, into / "ngjob", staged_conversion, hashes, _sha256(staged_conversion))


def _inside(path: Path, folder: Path) -> bool:
    path, folder = path.resolve(), folder.resolve()
    return path == folder or folder in path.parents


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ----- running it


def run_crosscheck(snapshot: Snapshot, job: Path, conversion: Path, work: Path, *,
                   template: Path | None = None, python: Path | None = None,
                   timeout_s: int = CROSSCHECK_TIMEOUT_S, run: Callable[..., ToolRun] | None = None) -> Crosscheck:
    """crosscheck from the copy on a staged copy of one job, in a folder of its own under work, which
    goes when it ends. Raises ToolkitError when nothing could be staged or run; what it said that
    can't be read is could_not_verify."""
    refuse_unless_local(work, "A check's staged copy")
    work.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="crosscheck-", dir=work))
    try:
        staged = stage_job(job, conversion, stage, template=template)
        said = (run or run_tool)(snapshot, CROSSCHECK_SCRIPT,
                                 [str(staged.job), str(staged.ngjob), str(staged.conversion)],
                                 timeout_s=timeout_s, python=python)
        written = staged.ngjob / f"{staged.job.name}_crosscheck.txt"
        text = written.read_text(encoding="utf-8") if written.is_file() else ""
        result = read_crosscheck(text, said, plan=planned(snapshot), rooms=tuple(name for name, _ in staged.rooms),
                                 library_found=library_found(snapshot))
        return replace(result, snapshot_sha256=snapshot.sha256, rooms=staged.rooms,
                       conversion_sha256=staged.conversion_sha256)
    finally:
        shutil.rmtree(stage, ignore_errors=True)        # made by this call


# ----- reading what it wrote


def read_crosscheck(text: str, run: ToolRun, *, plan: Sequence[Planned], rooms: Sequence[str],
                    library_found: bool) -> Crosscheck:
    """What crosscheck wrote (text) and how it ran, against its plan and the rooms staged, as one
    verdict per checker. Anything that doesn't fit what crosscheck prints is could_not_verify for all."""
    problem = _run_problem(text, run, plan, rooms)
    if problem:
        return _nothing(plan, problem)
    lines = text.splitlines()
    verdict = _VERDICT.fullmatch(lines[3].strip())
    sections = _sections(lines[4:])
    if [(name, advisory) for name, advisory, _ in sections] != [(p.name, p.advisory) for p in plan]:
        return _nothing(plan, "Jim's crosscheck ran other checkers than its own list says.")
    bad = set((verdict["bad"] or "").split(", ")) - {""}
    advised = set((verdict["advisory"] or "").split(", ")) - {""}
    if (not bad <= {p.name for p in plan if not p.advisory}
            or not advised <= {p.name for p in plan if p.advisory}):
        return _nothing(plan, "Jim's crosscheck flagged checkers that aren't in its list.")
    product = sum(name != "Room0.des" for name in rooms)
    verdicts = tuple(read_checker(name, said, flagged=name in bad | advised, product_rooms=product,
                                  all_rooms=len(rooms), library_found=library_found, advisory=advisory)
                     for name, advisory, said in sections)
    return Crosscheck(_overall(verdicts), _words(verdicts), verdicts, ran=tuple(name for name, _, _ in sections))


def _run_problem(text: str, run: ToolRun, plan: Sequence[Planned], rooms: Sequence[str]) -> str:
    if not plan:
        return "NG Runner could not read which checkers Jim's crosscheck runs."
    if run.timed_out or run.exit_code is None:
        return "Jim's crosscheck didn't finish."
    if not _only_the_utcnow_warning(run.stderr):
        return "Jim's crosscheck stopped with an error."
    lines = text.splitlines()
    if len(lines) < 4:
        return "Jim's crosscheck wrote no result."
    verdict = _VERDICT.fullmatch(lines[3].strip())
    rooms_line = _ROOMS_LINE.fullmatch(lines[1])
    if verdict is None or rooms_line is None:
        return "Jim's crosscheck wrote a result NG Runner doesn't know."
    if rooms_line[1] != ", ".join(rooms):
        return "Jim's crosscheck read other rooms than were staged."
    if not any(name != "Room0.des" for name in rooms):
        return "The job has no rooms to check."
    said = run.stdout.splitlines()
    if len(said) != 2 or said[0] != lines[3].strip() or not said[1].startswith("written: "):
        return "Jim's crosscheck printed something NG Runner doesn't know."
    if run.exit_code != (1 if verdict["bad"] else 0):
        return "Jim's crosscheck's exit code doesn't fit what it wrote."
    return ""


def _only_the_utcnow_warning(stderr: str) -> bool:
    lines = stderr.splitlines()
    return not lines or (len(lines) == 2 and all(p.fullmatch(line) for p, line in zip(_UTCNOW, lines)))


def _sections(lines: Sequence[str]) -> list[tuple[str, bool, str]]:
    found: list[tuple[str, bool, list[str]]] = []
    for line in lines:
        header = _SECTION.fullmatch(line)
        if header:
            found.append((header[1], header[2] is not None, []))
        elif found:
            found[-1][2].append(line)
        elif line.strip():
            return []           # something before the first checker: not what crosscheck writes
    return [(name, advisory, "\n".join(said).rstrip()) for name, advisory, said in found]


def _overall(verdicts: Sequence[CheckerVerdict]) -> str:
    deciding = [v.code for v in verdicts if not v.advisory]
    if FAILED in deciding:
        return FAILED
    return PASSED if deciding and set(deciding) == {PASSED} else COULD_NOT_VERIFY


def _words(verdicts: Sequence[CheckerVerdict]) -> str:
    deciding = [v for v in verdicts if not v.advisory]
    failed = [v.checker for v in deciding if v.code == FAILED]
    unsure = [v.checker for v in deciding if v.code == COULD_NOT_VERIFY]
    if failed:
        return f"Jim's crosscheck found something to fix: {', '.join(failed)}."
    if unsure or not deciding:
        return f"NG Runner could not verify {', '.join(unsure) or 'any checker'}."
    return "Jim's crosscheck passed: every checker it runs read clean."


def _nothing(plan: Sequence[Planned], words: str) -> Crosscheck:
    return Crosscheck(COULD_NOT_VERIFY, words,
                      tuple(CheckerVerdict(p.name, COULD_NOT_VERIFY, p.advisory, words) for p in plan))
