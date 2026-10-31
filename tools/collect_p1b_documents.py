r"""
Copy the documents Phase 1b's guards are tested on from Drive into .local-fixtures\p1b.

Why it exists: the labels, conversion JSONs, tracker snapshots, job sheets and crosscheck files are
the real shapes the guards read (docs/p1b/build-spec.md, section 6), and they name clients, so
they can only live in the gitignored fixtures folder. A frozen copy also keeps the tests still:
the tracker snapshot on Drive is rewritten every 15 minutes, so its counts would move under them.

Read-only on Drive: it opens source files to read and writes only under --dest, which it refuses to
point at the Drive folder it reads. It names no client: which jobs matter comes from
case_key.json (gitignored, case letter to real job name), and the copies are named by case or by
index, so a test that fails can't show a real name in a path. The index.json beside each folder's
files holds each one's role and real job name, for the tests to use and never to print. tools
\collect_p1b_fixtures.py copies the programs the same way, and keeps its own index.

A copy already there stays as it is, unless --refresh asks for a new one: then the counts in the
tests must be measured again.

Usage:  python tools\collect_p1b_documents.py [--cnc <CNC operations folder>] [--dest <folder>]
                                              [--since 2026-09-20] [--refresh]

It prints counts only.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_CNC_GLOBS = (
    r"G:\.shortcut-targets-by-id\*\Inspired Closets WC\Sales Folder\CNC operations",
    r"G:\My Drive\Inspired Closets WC\Sales Folder\CNC operations",
    r"G:\Shared drives\Inspired Closets WC\Sales Folder\CNC operations",
)
_TRACKER_DIR = "NG Conversion program Claude"
_SNAPSHOTS = ("SheetGoods", "Edgeband", "JobSummary", "Pricing", "meta")
_LABEL_CASES = "CDFGK"          # the jobs examined in section 3.2
_READBACK_CASE = "A"
_DEFAULT_SINCE = "2026-09-20"   # the JSONs section 3.4 measured
ROOT = Path(__file__).resolve().parent.parent


class CollectError(Exception):
    """Something the person running this must fix; its text holds no job name."""


# ----- one planned copy


@dataclass(frozen=True)
class Copy:
    source: Path
    folder: str         # labels, conv, tracker, jobsheets or checks
    name: str           # its name in that folder: a case letter or an index, never a job
    role: str
    job: str = ""       # the real job name, for index.json only
    case: str = ""


def _modified(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)


def _job_of(path: Path, ng_jobs: Path) -> str:
    """The job folder holding path: top level of NG Jobs or of Old. "" for a file in neither."""
    parts = path.relative_to(ng_jobs).parts[:-1]
    if parts and parts[0].casefold() == "old":
        parts = parts[1:]
    return parts[0] if parts else ""


def _walk(ng_jobs: Path) -> list[Path]:
    found = []
    for folder, _, names in os.walk(ng_jobs):
        found += [Path(folder) / name for name in names]
    return found


# ----- what section 6 asks for


def _labels(ng_jobs: Path, key: dict[str, str]) -> list[Copy]:
    """Every Labels PDF of the examined cases. A job can hold several (a re-cut's labels), so
    each is copied, oldest first, and the test says which it reads."""
    plan: list[Copy] = []
    for case in _LABEL_CASES:
        folder = ng_jobs / key[case]
        files = sorted((p for p in folder.iterdir()
                        if p.is_file() and p.suffix.lower() == ".pdf"
                        and "label" in p.name.casefold()), key=lambda p: p.stat().st_mtime)
        if not files:
            raise CollectError(f"case {case} has no labels file")
        plan += [Copy(p, "labels", f"{case}_{i}.pdf", "labels", key[case], case)
                 for i, p in enumerate(files)]
    return plan


def _catalog(tracker_dir: Path) -> list[Copy]:
    return [Copy(tracker_dir / "parts_catalog.csv", "labels", "parts_catalog.csv", "catalog")]


def _conversions(files: list[Path], ng_jobs: Path, since: datetime) -> list[Copy]:
    """The conversion JSONs since the given day, oldest first, so the last is the newest job's."""
    chosen = sorted((p for p in files
                     if "ic_conversion" in p.name.casefold() and p.suffix.lower() == ".json"
                     and _modified(p) >= since), key=lambda p: p.stat().st_mtime)
    return [Copy(p, "conv", f"{i:02d}.json", "newest" if i == len(chosen) - 1 else "conversion",
                 _job_of(p, ng_jobs)) for i, p in enumerate(chosen)]


def _tracker(tracker_dir: Path) -> list[Copy]:
    """The 5 snapshot files, and the newest inbox archive with a header and without one."""
    plan = [Copy(tracker_dir / f"tracker_snapshot_{tab}.csv", "tracker",
                 f"tracker_snapshot_{tab}.csv", "snapshot") for tab in _SNAPSHOTS]
    archives = sorted(tracker_dir.glob("ingested_tracker_inbox_*.csv"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    wanted = {True: "ingested_header", False: "ingested_no_header"}
    for path in archives:
        role = wanted.pop(_has_inbox_header(path), None)
        if role:
            plan.append(Copy(path, "tracker", f"{role}.csv", role))
    return plan


def _has_inbox_header(path: Path) -> bool:
    with path.open("rb") as stream:
        return stream.readline().lstrip(b"\xef\xbb\xbf").startswith(b"TAB,JOB")


def _job_sheets(files: list[Path], ng_jobs: Path) -> list[Copy]:
    chosen = sorted((p for p in files if p.suffix.lower() == ".docx"
                     and "job sheet" in p.name.casefold()), key=lambda p: p.as_posix().casefold())
    return [Copy(p, "jobsheets", f"{i:02d}.docx", "job_sheet", _job_of(p, ng_jobs))
            for i, p in enumerate(chosen)]


def _checks(files: list[Path], ng_jobs: Path, key: dict[str, str]) -> list[Copy]:
    """The newest-form crosscheck files, and Case A's nest readback."""
    chosen = sorted((p for p in files if p.name.casefold().endswith("_crosscheck.txt")),
                    key=lambda p: p.as_posix().casefold())
    plan = [Copy(p, "checks", f"{i:02d}.txt", "crosscheck", _job_of(p, ng_jobs))
            for i, p in enumerate(chosen)]
    readbacks = [p for p in files if _job_of(p, ng_jobs) == key[_READBACK_CASE]
                 and "readback" in p.name.casefold()]
    plan += [Copy(p, "checks", f"{_READBACK_CASE}_readback_{i}.txt", "readback",
                  key[_READBACK_CASE], _READBACK_CASE) for i, p in enumerate(readbacks)]
    return plan


def plan_copies(cnc: Path, key: dict[str, str], since: datetime) -> list[Copy]:
    ng_jobs, tracker_dir = cnc / "NG Jobs", cnc / _TRACKER_DIR
    files = _walk(ng_jobs)
    return (_labels(ng_jobs, key) + _catalog(tracker_dir) + _conversions(files, ng_jobs, since)
            + _tracker(tracker_dir) + _job_sheets(files, ng_jobs) + _checks(files, ng_jobs, key))


# ----- doing it


def run_copies(plan: list[Copy], dest: Path, *, refresh: bool) -> dict[str, tuple[int, int]]:
    """Make the copies and write each folder's index.json. Returns folder -> (copied, kept)."""
    counts: dict[str, list[int]] = {}
    index: dict[str, list[dict[str, str]]] = {}
    for item in plan:
        target = dest / item.folder / item.name
        target.parent.mkdir(parents=True, exist_ok=True)
        tally = counts.setdefault(item.folder, [0, 0])
        if target.exists() and not refresh:
            tally[1] += 1
        else:
            shutil.copy2(item.source, target)   # keeps the file's time, which selected it
            tally[0] += 1
        index.setdefault(item.folder, []).append({
            "file": item.name,
            "role": item.role,
            "job": item.job,
            "case": item.case,
            "modified": _modified(target).isoformat(timespec="seconds"),
        })
    for folder, entries in index.items():
        text = json.dumps(entries, indent=1, ensure_ascii=False) + "\n"
        (dest / folder / "index.json").write_text(text, encoding="utf-8", newline="\n")
    return {folder: (copied, kept) for folder, (copied, kept) in counts.items()}


def find_cnc(given: str | None) -> Path:
    if given:
        return Path(given)
    if os.environ.get("NGR_NG_JOBS"):
        return Path(os.environ["NGR_NG_JOBS"]).parent
    for pattern in _CNC_GLOBS:
        for found in sorted(glob.glob(pattern)):
            return Path(found)
    raise CollectError("the CNC operations folder was not found on G: (use --cnc)")


def _read_key(path: Path) -> dict[str, str]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CollectError("case_key.json is missing from the fixtures folder") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy P1b's real documents into the fixtures.")
    parser.add_argument("--cnc", help="the CNC operations folder (found on G: when left out)")
    parser.add_argument("--dest", default=str(ROOT / ".local-fixtures" / "p1b"))
    parser.add_argument("--since", default=_DEFAULT_SINCE, help="the oldest conversion JSON's day")
    parser.add_argument("--refresh", action="store_true", help="replace copies already there")
    args = parser.parse_args(argv)
    try:
        cnc, dest = find_cnc(args.cnc).resolve(), Path(args.dest).resolve()
        if dest.is_relative_to(cnc):
            raise CollectError("--dest is inside the Drive folder this only reads")
        key = _read_key(dest / "case_key.json")
        plan = plan_copies(cnc, key, datetime.fromisoformat(args.since))
        for folder, (copied, kept) in sorted(run_copies(plan, dest, refresh=args.refresh).items()):
            print(f"{folder}: {copied} copied, {kept} already there")
    except (CollectError, OSError, ValueError, KeyError) as err:
        # Only this module's own words or an error's name: an OSError's text holds a job's path.
        message = str(err) if isinstance(err, CollectError) else type(err).__name__
        print(f"Could not collect the documents: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
