r"""
Copies the real programs Phase 1b's tests read into .local-fixtures, read-only on the drive.

Why it exists: the program checks are proven on what the machine would really cut, and those files
name clients, so they can never enter git (AGENTS.md rule 5). This script picks them by case letter
and by the shape of a program's name, and the letters' real job names live only in
.local-fixtures\p1b\case_key.json (gitignored), so the script itself can be committed. See
docs/p1b/build-spec.md, section 6, the taps rows.

Usage:  python tools\collect_p1b_fixtures.py

It only reads NG Jobs: everything it writes goes under .local-fixtures\p1b\taps. Copies are named by
index, and index.json beside them holds each one's real name, row and case. It prints counts only,
so a run names no job.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_NG_JOBS_GLOBS = (
    r"G:\.shortcut-targets-by-id\*\Inspired Closets WC\Sales Folder\CNC operations\NG Jobs",
    r"G:\My Drive\Inspired Closets WC\Sales Folder\CNC operations\NG Jobs",
    r"G:\Shared drives\Inspired Closets WC\Sales Folder\CNC operations\NG Jobs",
)
_PER_SHEET = re.compile(r"S[0-9]{2}\.TAP$")


class CollectError(Exception):
    """Something the fixtures need is not on the drive. The message names cases, never jobs."""


@dataclass(frozen=True)
class Program:
    """One program on the drive, as a case found it."""

    path: Path
    material: str


Pick = Callable[[Program], bool]


def _any(_: Program) -> bool:
    return True


def _per_sheet(p: Program) -> bool:
    return bool(_PER_SHEET.search(p.path.name))


def _flip(p: Program) -> bool:
    return "flip" in p.path.name.casefold()


def _in(thickness: str, pick: Pick = _any) -> Pick:
    return lambda p: p.material.startswith(thickness) and pick(p)


def _name(pattern: str) -> Pick:
    found = re.compile(pattern)
    return lambda p: bool(found.search(p.path.name))


# ----- what to copy (build spec section 6): row, case, role, which programs, how many

_ROWS: tuple[tuple[str, str, str, Pick, int | None], ...] = (
    ("doubled", "D", "per_sheet", _in("3-4", _per_sheet), 3),
    ("doubled", "D", "flip", _in("3-4", _flip), 1),
    ("fixed", "C", "per_sheet", _in("3-4", _per_sheet), 9),
    ("fixed", "C", "quarter", _in("1-4"), 3),
    ("fixed", "C", "same_folder", lambda p: _in("3-4")(p) and not _per_sheet(p), None),
    ("fixed", "F", "per_sheet", _in("3-4", _per_sheet), 3),
    ("fixed", "G", "per_sheet", _in("3-4", _per_sheet), 6),
    ("hand_added", "B", "whole_folder", _any, None),
    # The row asks for H's 4x10 programs. The rest of that folder comes too, so a test can show
    # that a finding names those programs and not the others beside them.
    ("hand_added", "H", "whole_folder", _in("3-4"), None),
    ("sized_49", "A", "per_sheet", _in("3-4", _per_sheet), 4),
)
# One of each kind of name. Searched in these cases first, then every other job.
_FAMILIES: tuple[tuple[str, str, str], ...] = (
    ("merged", "D", r"[0-9]-[0-9]+\.tap$"),
    ("remake_rm", "F", r"RM\.TAP$"),
    ("remake_rd", "C", r"RD\.TAP$"),
    ("remake_fix", "E", r"[Ff]ix\.TAP$"),
    ("remake_pile", "G", r"pile\.TAP$"),
    ("flip", "C", r"-Flip\.TAP$"),
    ("long", "D", r"_S[0-9]+R[0-9]+(-Flip)?\.TAP$"),
    ("long_plain", "", r"_S[0-9]+R[0-9]+\.TAP$"),
    ("hand", "B", r"Hooks\.TAP$"),
    # The names that settled what "merged" means (ngrunner/guards/tap.py says how).
    ("merged_bare", "", r"^[A-Za-z]+[0-9]{3}\.tap$"),
    ("dashed_upper", "", r"^[A-Za-z]+[0-9]+-[0-9]+\.TAP$"),
)


# ----- finding things on the drive


def ng_jobs_folder(env: dict[str, str] | os._Environ[str] = os.environ) -> Path:
    """NGR_NG_JOBS when set, else the first NG Jobs folder found on the drive."""
    if env.get("NGR_NG_JOBS"):
        return Path(env["NGR_NG_JOBS"])
    for pattern in _NG_JOBS_GLOBS:
        for found in sorted(glob.glob(pattern)):
            if os.path.isdir(found):
                return Path(found)
    raise CollectError("NG Jobs was not found on the drive (set NGR_NG_JOBS).")


def job_folder(ng_jobs: Path, job: str) -> Path | None:
    """A job's folder: at the top of NG Jobs, or anywhere under Old."""
    top = ng_jobs / job
    if top.is_dir():
        return top
    for root, dirs, _ in os.walk(ng_jobs / "Old"):
        if job in dirs:
            return Path(root) / job
    return None


def programs_of(folder: Path) -> Iterator[Program]:
    for material in sorted(p for p in folder.iterdir() if p.is_dir()):
        for path in sorted(p for p in material.iterdir() if p.is_file()):
            if path.suffix.casefold() == ".tap":
                yield Program(path, material.name)


def all_programs(ng_jobs: Path) -> Iterator[Program]:
    for root, dirs, files in os.walk(ng_jobs):
        dirs.sort()
        for name in sorted(files):
            if name.casefold().endswith(".tap"):
                yield Program(Path(root) / name, Path(root).name)


# ----- choosing


def choose_rows(by_case: dict[str, list[Program]]) -> Iterator[tuple[str, str, str, Program]]:
    for row, case, role, pick, expected in _ROWS:
        chosen = [p for p in by_case[case] if pick(p)]
        if expected is not None and len(chosen) != expected:
            raise CollectError(f"Case {case} has {len(chosen)} {role} programs for {row}, "
                               f"and the build spec expects {expected}.")
        if not chosen:
            raise CollectError(f"Case {case} has no {role} programs for {row}.")
        for program in chosen:
            yield row, case, role, program


def choose_families(by_case: dict[str, list[Program]],
                    everything: Iterable[Program]) -> Iterator[tuple[str, str, str, Program]]:
    everything = list(everything)
    for kind, case, pattern in _FAMILIES:
        found = _name(pattern)
        from_case = [p for p in by_case.get(case, []) if found(p)]
        if from_case:
            yield "families", case, kind, from_case[0]
            continue
        from_all = [p for p in everything if found(p)]
        if not from_all:
            raise CollectError(f"No program on the drive has the {kind} kind of name.")
        yield "families", "", kind, from_all[0]


# ----- copying


def copy_programs(chosen: Iterable[tuple[str, str, str, Program]], dest: Path) -> list[dict]:
    """Copy each program under dest by index, returning the index's entries."""
    entries: list[dict] = []
    for index, (row, case, role, program) in enumerate(chosen):
        data = program.path.read_bytes()
        file = f"{row}/{index:03d}{program.path.suffix}"
        target = dest / file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        when = datetime.fromtimestamp(program.path.stat().st_mtime, UTC)
        entries.append({"index": index, "file": file, "row": row, "case": case, "role": role,
                        "material": program.material, "name": program.path.name,
                        "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                        "modified": when.isoformat()})
    return entries


def collect(key: dict[str, str], ng_jobs: Path, dest: Path) -> list[dict]:
    """Copy every taps row into dest, a fresh folder, and write index.json. Reads the drive only."""
    if dest.resolve().is_relative_to(ng_jobs.resolve()):
        raise CollectError("The fixtures folder can't be inside NG Jobs: that folder is read-only.")
    by_case: dict[str, list[Program]] = {}
    for case, job in sorted(key.items()):
        folder = job_folder(ng_jobs, job)
        if folder is None:
            raise CollectError(f"Case {case}'s job folder is not in NG Jobs.")
        by_case[case] = list(programs_of(folder))
    chosen = [*choose_rows(by_case), *choose_families(by_case, all_programs(ng_jobs))]
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    entries = copy_programs(chosen, dest)
    (dest / "index.json").write_text(json.dumps(entries, indent=1), encoding="utf-8")
    return entries


def main() -> int:
    fixtures = Path(os.environ.get("NGR_FIXTURES", ROOT / ".local-fixtures"))
    key_file = fixtures / "p1b" / "case_key.json"
    if not key_file.is_file():
        print("The case key is missing: .local-fixtures\\p1b\\case_key.json", file=sys.stderr)
        return 1
    key = json.loads(key_file.read_text(encoding="utf-8"))
    try:
        entries = collect(key, ng_jobs_folder(), fixtures / "p1b" / "taps")
    except (CollectError, OSError) as exc:
        reason = exc if isinstance(exc, CollectError) else "NG Jobs could not be read."
        print(f"Nothing collected. {reason}", file=sys.stderr)
        return 1
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["row"]] = counts.get(entry["row"], 0) + 1
    print(f"Collected {len(entries)} programs: "
          + ", ".join(f"{row} {n}" for row, n in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
