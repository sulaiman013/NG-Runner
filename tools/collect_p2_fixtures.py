r"""
Copies the real pairs the converter is scored on into .local-fixtures\p2\pairs, read-only on the drive.

Why it exists: the converter (ngrunner/convert) is proven on what Jim's chat really made from real
summaries, and those files name clients, so they can never enter git (AGENTS.md rule 5). A pair is
an NG Jobs folder holding an IC summary and the chat's IC Conversion JSON made from it; its workbook
comes along when there is one. See docs/p2/build-spec.md, sections 4 (Fixtures) and 6.

Usage:  python tools\collect_p2_fixtures.py

It reads the top of NG Jobs and the folders directly under its Old folder, and writes only under
p2\pairs in the fixtures folder (NGR_FIXTURES, .local-fixtures by default; AGENTS.md: Fixtures). Each pair is copied into a folder named by its index, as summary.pdf,
conversion.json and conversion.xlsx, and index.json beside them holds each index's place ("top" or
"old") and nothing else. Indexes run newest JSON first, the order the build spec's pair codes were
given in (PAA is 0, PAB 1, ...). It prints counts only, so a run names no job, and it refuses to
run over an earlier copy: delete .local-fixtures\p2\pairs to collect again.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_NG_JOBS_GLOBS = (
    r"G:\.shortcut-targets-by-id\*\Inspired Closets WC\Sales Folder\CNC operations\NG Jobs",
    r"G:\My Drive\Inspired Closets WC\Sales Folder\CNC operations\NG Jobs",
    r"G:\Shared drives\Inspired Closets WC\Sales Folder\CNC operations\NG Jobs",
)


class CollectError(Exception):
    """Something the fixtures need is missing. The message names no job."""


@dataclass(frozen=True)
class Pair:
    """One NG Jobs folder's summary, conversion JSON and (optional) workbook."""

    place: str              # "top" or "old"
    summary: Path
    conversion: Path
    workbook: Path | None
    json_mtime: float


# ----- finding them


def ng_jobs_folder(env: dict[str, str] | os._Environ[str] = os.environ) -> Path:
    """NGR_NG_JOBS when set, else the first NG Jobs folder found on the drive."""
    if env.get("NGR_NG_JOBS"):
        return Path(env["NGR_NG_JOBS"])
    for pattern in _NG_JOBS_GLOBS:
        for found in sorted(glob.glob(pattern)):
            if os.path.isdir(found):
                return Path(found)
    raise CollectError("NG Jobs was not found on the drive (set NGR_NG_JOBS).")


def pair_in(folder: Path, place: str) -> Pair | None:
    """The folder's pair, or None. One conversion JSON exactly; the first summary by name when
    an old folder holds two (as the build spec's measurement took them)."""
    files = sorted((f for f in folder.iterdir() if f.is_file()), key=lambda f: f.name)
    jsons = [f for f in files if f.name.casefold().endswith("_ic_conversion.json")]
    summaries = [f for f in files if f.name.casefold().endswith("_summary.pdf")]
    if len(jsons) != 1 or not summaries:
        return None
    books = [f for f in files if f.suffix.casefold() == ".xlsx" and "conversion" in f.name.casefold()]
    return Pair(place, summaries[0], jsons[0], books[0] if books else None,
                jsons[0].stat().st_mtime)


def find_pairs(ng_jobs: Path) -> list[Pair]:
    """Every pair at the top and directly under Old, newest JSON first."""
    found: list[Pair] = []
    for place, root in (("top", ng_jobs), ("old", ng_jobs / "Old")):
        if not root.is_dir():
            continue
        for folder in sorted(p for p in root.iterdir() if p.is_dir()):
            pair = pair_in(folder, place)
            if pair is not None:
                found.append(pair)
    found.sort(key=lambda p: -p.json_mtime)
    return found


# ----- copying them


def collect(ng_jobs: Path, dest: Path) -> list[Pair]:
    """Copies every pair into dest by index and writes dest\\index.json. Refuses an existing dest."""
    if dest.exists():
        raise CollectError(f"{dest} already exists: delete it to collect again.")
    pairs = find_pairs(ng_jobs)
    if not pairs:
        raise CollectError("NG Jobs holds no summary with its IC Conversion JSON.")
    for index, pair in enumerate(pairs):
        folder = dest / str(index)
        folder.mkdir(parents=True)
        shutil.copyfile(pair.summary, folder / "summary.pdf")
        shutil.copyfile(pair.conversion, folder / "conversion.json")
        if pair.workbook is not None:
            shutil.copyfile(pair.workbook, folder / "conversion.xlsx")
    index_rows = [{"place": pair.place} for pair in pairs]
    (dest / "index.json").write_text(json.dumps(index_rows, indent=1), encoding="utf-8")
    return pairs


def main(argv: list[str] | None = None) -> int:
    del argv
    dest = Path(os.environ.get("NGR_FIXTURES", ROOT / ".local-fixtures")) / "p2" / "pairs"
    try:
        pairs = collect(ng_jobs_folder(), dest)
    except (CollectError, OSError) as exc:
        print(f"Could not collect: {exc}", file=sys.stderr)
        return 1
    top = sum(p.place == "top" for p in pairs)
    books = sum(p.workbook is not None for p in pairs)
    print(f"{len(pairs)} pairs copied: {top} at the top, {len(pairs) - top} in Old,"
          f" {books} with a workbook.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
