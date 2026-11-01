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

  more tests   P01 to P40 (2026-10-31): whole Mozaik job folders in .local-fixtures\\reader\\p5jobs\\
               <tag>\\<job>\\, their names in p5jobs\\key.json. Each one's summary is in NG Jobs (top
               level or Old) under the file name the sender's MANIFEST.txt records, often a doubled
               prefix. It is used only when its page 1 PROJECT NAME plainly matches the job (letters
               and digits equal); otherwise the job is skipped, and counted, never guessed.

collect() copies each summary, read-only on G:, to .local-fixtures\\reader\\cases\\<case>.pdf with
index.json beside it (each copy's source kind and sha256, or why it was skipped; no names), so the
rest never needs G:.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

from tools.reader_eval.prep import name_key, project_name

ROOT = Path(__file__).resolve().parents[2]
TEST_CASES = tuple("ABCDEFGHIJ")
WORKED_CASES = tuple("XYZ")
_MANIFEST_ROW = re.compile(r"^(?P<job>\S.*?)\.zip\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+(?P<summary>\S.*?\.pdf)\s*$")
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


def p5_dir(env: Mapping[str, str] = os.environ) -> Path:
    return reader_dir(env) / "p5jobs"


def _p5_key(env: Mapping[str, str]) -> dict[str, str]:
    path = p5_dir(env) / "key.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def test_cases(env: Mapping[str, str] = os.environ) -> tuple[str, ...]:
    """The test set: Jobs A to J, then the P5 jobs when their folder is here."""
    return TEST_CASES + tuple(sorted(_p5_key(env)))


def job_names(env: Mapping[str, str] = os.environ) -> dict[str, str]:
    """Case -> job name, from the local keys: for checks only, never printed."""
    names: dict[str, str] = {}
    path = fixtures(env) / "p1b" / "case_key.json"
    if path.is_file():
        names.update(json.loads(path.read_text(encoding="utf-8")))
    names.update(_p5_key(env))
    if not names:
        raise CaseError("no case key: .local-fixtures\\p1b\\case_key.json is missing")
    return names


def manifest_summaries(text: str) -> dict[str, str]:
    """Job -> the summary's real file name, from the rows of the sender's MANIFEST.txt table."""
    return {m["job"].strip(): m["summary"].strip()
            for line in text.splitlines() if (m := _MANIFEST_ROW.match(line.strip()))}


def ng_jobs_folder(env: Mapping[str, str] = os.environ) -> Path | None:
    if env.get("NGR_NG_JOBS"):
        return Path(env["NGR_NG_JOBS"])
    for pattern in _NG_JOBS_GLOBS:
        for found in sorted(glob.glob(pattern)):
            if os.path.isdir(found):
                return Path(found)
    return None


def _p5_source(tag: str, env: Mapping[str, str]) -> tuple[str, Path]:
    job = _p5_key(env)[tag]
    manifest = p5_dir(env) / "MANIFEST.txt"
    name = manifest_summaries(manifest.read_text(encoding="utf-8")).get(job) if manifest.is_file() else None
    ng = ng_jobs_folder(env)
    if name and ng is not None:
        for kind, folder in (("ng_jobs", ng / job), ("ng_jobs_old", ng / "Old" / job)):
            if (folder / name).is_file():
                return kind, folder / name
    raise CaseError(f"case {tag}: no summary in NG Jobs")


def _source(letter: str, env: Mapping[str, str]) -> tuple[str, Path]:
    if letter in _p5_key(env):
        return _p5_source(letter, env)
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


def collect(env: Mapping[str, str] = os.environ, which: Sequence[str] | None = None) -> dict[str, str]:
    """
    Copy each case's summary into .local-fixtures\\reader\\cases. Returns case -> source kind, or
    "skipped: <why>" for a P5 job whose summary is missing or is plainly another job's.
    """
    out = reader_dir(env) / "cases"
    out.mkdir(parents=True, exist_ok=True)
    index_path = out / "index.json"
    index: dict[str, dict[str, str]] = (json.loads(index_path.read_text(encoding="utf-8"))
                                        if index_path.is_file() else {})
    p5 = _p5_key(env)
    found: dict[str, str] = {}
    for case in which if which is not None else test_cases(env) + WORKED_CASES:
        try:
            kind, path = _source(case, env)
        except CaseError:
            if case not in p5:
                raise
            index[case] = {"skipped": "no summary in NG Jobs"}
            found[case] = f"skipped: {index[case]['skipped']}"
            continue
        data = path.read_bytes()
        if case in p5 and name_key(project_name(data) or "") != name_key(p5[case]):
            index[case] = {"skipped": "the summary is another job's"}
            found[case] = f"skipped: {index[case]['skipped']}"
            (out / f"{case}.pdf").unlink(missing_ok=True)
            continue
        target = out / f"{case}.pdf"
        shutil.copyfile(path, target)
        index[case] = {"source": kind, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        found[case] = kind
    index_path.write_text(json.dumps(index, indent=1), encoding="utf-8")
    return found


def collected(env: Mapping[str, str] = os.environ) -> tuple[str, ...]:
    """The cases whose summary is collected, test cases first, in order."""
    path = reader_dir(env) / "cases" / "index.json"
    index = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return tuple(c for c in test_cases(env) + WORKED_CASES if "sha256" in index.get(c, {}))


def summary(letter: str, env: Mapping[str, str] = os.environ) -> bytes:
    path = reader_dir(env) / "cases" / f"{letter}.pdf"
    if not path.is_file():
        raise CaseError(f"case {letter}: not collected yet (run: python -m tools.reader_eval.run collect)")
    return path.read_bytes()


def rooms(letter: str, env: Mapping[str, str] = os.environ) -> dict[str, bytes]:
    """The case's Room#.des files by stem ("Room1"), read by bytes."""
    if letter in _p5_key(env):
        paths = sorted((p5_dir(env) / letter).glob("*/Room*.des"))
    elif letter in WORKED_CASES:
        frozen = _frozen(env)
        paths = sorted((frozen[letter] / "rooms").glob("Room*.des")) if letter in frozen else []
    else:
        paths = sorted((toolkit(env) / "jobs" / letter).glob("*/Room*.des"))
    if not paths:
        raise CaseError(f"case {letter}: no rooms in the toolkit")
    return {p.stem: p.read_bytes() for p in paths}
