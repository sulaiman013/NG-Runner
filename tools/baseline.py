r"""
The Phase 0 baseline, measured from files instead of asked (docs/decisions.md, "Baseline").

What NG Runner is meant to change shows in the NG Jobs folders already: whether each job's IC
summary is there, how many crosscheck rounds a job took (each "(rev N)" header is a round with
chat), whether a round came back clean, and the time from the summary to the first clean round.
This script reads those facts, read-only, so the same numbers can be taken again once NG Runner
runs and the relief shown rather than claimed.

Usage:  python tools\baseline.py [--ng-jobs <folder>] [--out <file.json>]

It never writes inside NG Jobs. The JSON it writes names jobs, so it goes to NG Runner's own data
folder by default and never into git; the summary it prints holds counts only.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

_JOB_FOLDER = re.compile(r"(?i)^(?P<name>.+?_(?P<date>[0-9]{6}))(?P<gar>_gar)?$")
_REVISION = re.compile(r"(?im)crosscheck[^\n]*\(rev\s*(?P<n>[0-9]{1,3})\)")
_CLEAN = re.compile(r"(?m)^\s*CROSSCHECK CLEAN\s*$")
_NG_JOBS_GLOBS = (
    r"G:\.shortcut-targets-by-id\*\Inspired Closets WC\Sales Folder\CNC operations\NG Jobs",
    r"G:\My Drive\Inspired Closets WC\Sales Folder\CNC operations\NG Jobs",
    r"G:\Shared drives\Inspired Closets WC\Sales Folder\CNC operations\NG Jobs",
)


# ----- one job folder


@dataclass
class JobFacts:
    """What one NG Jobs folder shows today. Times are file modification times, in UTC."""

    folder: str
    job_date: str | None            # the MMDDYY in the folder name, as a date (None if invalid)
    garage: bool
    archived: bool                  # under NG Jobs\Old
    summary_at: str | None          # <folder>_summary.pdf
    conversions: int                # IC Conversion json/xlsx files
    crosschecks: int                # files with "crosscheck" in the name
    rounds: int                     # crosscheck rounds: the highest "(rev N)", else 1 per file
    clean: bool                     # any round ended "CROSSCHECK CLEAN"
    first_clean_at: str | None
    labels: int                     # PDFs with "label" in the name
    last_activity_at: str | None    # newest file in the folder
    days_summary_to_clean: float | None = None
    notes: list[str] = field(default_factory=list)


def scan_folder(folder: Path, *, archived: bool = False) -> JobFacts | None:
    """The facts of one job folder, or None when the folder is not named like a job."""
    m = _JOB_FOLDER.match(folder.name)
    if m is None:
        return None
    files = [p for p in folder.rglob("*") if p.is_file()]
    summary = folder / f"{folder.name}_summary.pdf"
    crosschecks = [p for p in files if "crosscheck" in p.name.lower()]
    rounds, clean_times = _rounds(crosschecks)
    facts = JobFacts(
        folder=folder.name,
        job_date=_job_date(m["date"]),
        garage=bool(m["gar"]),
        archived=archived,
        summary_at=_mtime(summary) if summary.is_file() else None,
        conversions=sum(1 for p in files if "ic_conversion" in p.name.lower()
                        and p.suffix.lower() in (".json", ".xlsx")),
        crosschecks=len(crosschecks),
        rounds=rounds,
        clean=bool(clean_times),
        first_clean_at=min(clean_times) if clean_times else None,
        labels=sum(1 for p in files if "label" in p.name.lower() and p.suffix.lower() == ".pdf"),
        last_activity_at=max((_mtime(p) for p in files), default=None),
    )
    if facts.summary_at and facts.first_clean_at:
        delta = datetime.fromisoformat(facts.first_clean_at) - datetime.fromisoformat(facts.summary_at)
        facts.days_summary_to_clean = round(delta.total_seconds() / 86400, 2)
    return facts


def _rounds(crosschecks: list[Path]) -> tuple[int, list[str]]:
    rounds, clean_times = 0, []
    for path in crosschecks:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        revisions = [int(m["n"]) for m in _REVISION.finditer(text)]
        rounds += max(revisions) if revisions else 1
        if _CLEAN.search(text):
            clean_times.append(_mtime(path))
    return rounds, clean_times


def _job_date(mmddyy: str) -> str | None:
    try:
        return date(2000 + int(mmddyy[4:]), int(mmddyy[:2]), int(mmddyy[2:4])).isoformat()
    except ValueError:
        return None


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


# ----- the whole NG Jobs folder


def scan(ng_jobs: Path) -> list[JobFacts]:
    """Every job folder directly in NG Jobs and in NG Jobs\\Old, read-only."""
    jobs: list[JobFacts] = []
    for parent, archived in ((ng_jobs, False), (ng_jobs / "Old", True)):
        if not parent.is_dir():
            continue
        for child in sorted(parent.iterdir()):
            if child.is_dir():
                facts = scan_folder(child, archived=archived)
                if facts is not None:
                    jobs.append(facts)
    return jobs


def summarise(jobs: list[JobFacts]) -> dict[str, object]:
    """Counts and medians only: safe to print, names no job."""
    checked = [j for j in jobs if j.crosschecks]
    to_clean = [j.days_summary_to_clean for j in jobs if j.days_summary_to_clean is not None]
    return {
        "job_folders": len(jobs),
        "archived": sum(j.archived for j in jobs),
        "garage": sum(j.garage for j in jobs),
        "with_summary": sum(j.summary_at is not None for j in jobs),
        "with_conversion": sum(j.conversions > 0 for j in jobs),
        "with_crosscheck": len(checked),
        "crosscheck_rounds_median": statistics.median([j.rounds for j in checked]) if checked else None,
        "crosscheck_rounds_max": max((j.rounds for j in checked), default=None),
        "clean_share": round(sum(j.clean for j in checked) / len(checked), 2) if checked else None,
        "days_summary_to_clean_median": statistics.median(to_clean) if to_clean else None,
    }


def find_ng_jobs() -> Path | None:
    for pattern in _NG_JOBS_GLOBS:
        for hit in glob.glob(pattern):
            if os.path.isdir(hit):
                return Path(hit)
    return None


def write_report(jobs: list[JobFacts], out: Path, ng_jobs: Path) -> None:
    """The per-job JSON, outside NG Jobs. It names jobs: never commit it."""
    if _inside(out, ng_jobs):
        raise ValueError("the report must not be written inside NG Jobs")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"taken_at": datetime.now(timezone.utc).isoformat(),
                               "summary": summarise(jobs), "jobs": [asdict(j) for j in jobs]},
                              indent=1), encoding="utf-8")


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Take the Phase 0 baseline from NG Jobs, read-only.")
    parser.add_argument("--ng-jobs", type=Path, help="the NG Jobs folder (found on G: if left out)")
    parser.add_argument("--out", type=Path, help="where the per-job JSON goes")
    args = parser.parse_args(argv)
    ng_jobs = args.ng_jobs or find_ng_jobs()
    if ng_jobs is None or not ng_jobs.is_dir():
        print("NG Jobs was not found; pass --ng-jobs.", file=sys.stderr)
        return 2
    jobs = scan(ng_jobs)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "NGRunner" / "baseline"
    out = args.out or local / f"baseline-{stamp}.json"
    write_report(jobs, out, ng_jobs)
    for key, value in summarise(jobs).items():
        print(f"{key}: {value}")
    print(f"per-job report (names jobs, keep it local): {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
