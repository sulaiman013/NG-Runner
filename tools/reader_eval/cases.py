"""
Where the reader evaluation's real material is, by case letter only.

Why letters: every summary and room names a client, and the repo is public (AGENTS.md rule 5). The
letters' job names live only in .local-fixtures\\p1b\\case_key.json (gitignored), and this module
reads them at run time; nothing it prints or writes in the repo names a job.

  test set     Jobs A to J: the as-cut rooms in Jim's toolkit (<toolkit>\\jobs\\<letter>\\), each
               job's summary from NG Jobs on G: (top level, else Old), else the raw Assembly
               Reports download in .local-fixtures\\summaries\\downloads (Job J's only copy).
  worked walls X, Y and Z: Jim's three frozen regression jobs (<toolkit>\\NG_Automation_reference\\
               reference\\regression\\, in name order), each with its summary and rooms. Worked walls
               come only from these, so they are never test walls (decision 17).

collect() copies each summary, read-only on G:, to .local-fixtures\\reader\\cases\\<letter>.pdf with
index.json beside it (each copy's source kind and sha256, no names), so the rest never needs G:.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_CASES = "ABCDEFGHIJ"
WORKED_CASES = "XYZ"
_NG_JOBS_GLOBS = (
    r"G:\.shortcut-targets-by-id\*\Inspired Closets WC\Sales Folder\CNC operations\NG Jobs",
    r"G:\My Drive\Inspired Closets WC\Sales Folder\CNC operations\NG Jobs",
    r"G:\Shared drives\Inspired Closets WC\Sales Folder\CNC operations\NG Jobs",
)


class CaseError(Exception):
    """Material a case needs is missing; the message names the case letter, never the job."""


def fixtures(env: Mapping[str, str] = os.environ) -> Path:
    return Path(env.get("NGR_FIXTURES") or ROOT / ".local-fixtures")


def toolkit(env: Mapping[str, str] = os.environ) -> Path:
    return Path(env.get("NGR_TOOLKIT") or fixtures(env) / "toolkit" / "20261031")


def reader_dir(env: Mapping[str, str] = os.environ) -> Path:
    return fixtures(env) / "reader"


def _frozen(env: Mapping[str, str]) -> dict[str, Path]:
    regression = toolkit(env) / "NG_Automation_reference" / "reference" / "regression"
    jobs = sorted(p for p in regression.glob("*") if (p / "summary.pdf").is_file()) if regression.is_dir() else []
    return dict(zip(WORKED_CASES, jobs))


def job_names(env: Mapping[str, str] = os.environ) -> dict[str, str]:
    """Case letter -> job name, from the local case key: for checks only, never printed."""
    path = fixtures(env) / "p1b" / "case_key.json"
    if not path.is_file():
        raise CaseError("no case key: .local-fixtures\\p1b\\case_key.json is missing")
    return json.loads(path.read_text(encoding="utf-8"))


def ng_jobs_folder(env: Mapping[str, str] = os.environ) -> Path | None:
    if env.get("NGR_NG_JOBS"):
        return Path(env["NGR_NG_JOBS"])
    for pattern in _NG_JOBS_GLOBS:
        for found in sorted(glob.glob(pattern)):
            if os.path.isdir(found):
                return Path(found)
    return None


def _source(letter: str, env: Mapping[str, str]) -> tuple[str, Path]:
    if letter in WORKED_CASES:
        frozen = _frozen(env)
        if letter not in frozen:
            raise CaseError(f"case {letter}: the frozen regression job is not in the toolkit")
        return "frozen", frozen[letter] / "summary.pdf"
    job = job_names(env).get(letter)
    if not job:
        raise CaseError(f"case {letter} is not in the case key")
    ng = ng_jobs_folder(env)
    if ng is not None:
        for kind, folder in (("ng_jobs", ng / job), ("ng_jobs_old", ng / "Old" / job)):
            if (folder / f"{job}_summary.pdf").is_file():
                return kind, folder / f"{job}_summary.pdf"
    downloads = sorted((fixtures(env) / "summaries" / "downloads").glob(f"*{job.lower()}_summary.pdf"))
    if downloads:
        return "download", downloads[0]
    raise CaseError(f"case {letter}: no summary found in NG Jobs or the local downloads")


def collect(env: Mapping[str, str] = os.environ) -> dict[str, str]:
    """Copy every case's summary into .local-fixtures\\reader\\cases; returns letter -> source kind."""
    out = reader_dir(env) / "cases"
    out.mkdir(parents=True, exist_ok=True)
    index: dict[str, dict[str, str]] = {}
    for letter in TEST_CASES + WORKED_CASES:
        kind, path = _source(letter, env)
        target = out / f"{letter}.pdf"
        shutil.copyfile(path, target)
        index[letter] = {"source": kind, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    (out / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    return {letter: v["source"] for letter, v in index.items()}


def summary(letter: str, env: Mapping[str, str] = os.environ) -> bytes:
    path = reader_dir(env) / "cases" / f"{letter}.pdf"
    if not path.is_file():
        raise CaseError(f"case {letter}: not collected yet (run: python -m tools.reader_eval.run collect)")
    return path.read_bytes()


def rooms(letter: str, env: Mapping[str, str] = os.environ) -> dict[str, bytes]:
    """The case's Room#.des files by stem ("Room1"), read by bytes."""
    if letter in WORKED_CASES:
        frozen = _frozen(env)
        paths = sorted((frozen[letter] / "rooms").glob("Room*.des")) if letter in frozen else []
    else:
        paths = sorted((toolkit(env) / "jobs" / letter).glob("*/Room*.des"))
    if not paths:
        raise CaseError(f"case {letter}: no rooms in the toolkit")
    return {p.stem: p.read_bytes() for p in paths}
