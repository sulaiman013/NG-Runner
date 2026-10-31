r"""
Copy the tracker files Phase 2's inbox is tested on from Drive into .local-fixtures\p2\tracker.

Why it exists: the tracker inbox writer is golden-tested against every inbox file Jim's ingest has
archived, and "did it land" against a snapshot of the tracker (docs/p2/build-spec.md, sections 5.6
and 6). Those files name clients, so they can only live in the gitignored fixtures folder, and a
frozen copy keeps the tests still: the snapshot on Drive is rewritten every 15 minutes.

Read-only on Drive: it opens source files to read and writes only under --dest, which it refuses to
point at the Drive folder it reads. It names no client: the archives are copied by index, oldest
first (inbox\00.csv, 01.csv, ...), and index.json holds each one's archived name, which is only a
time stamp. The snapshot keeps its own five names. A copy already there stays as it is, unless
--refresh asks for a new one: then the counts in the tests must be measured again.

Usage:  python tools\collect_p2_documents.py [--cnc <CNC operations folder>] [--dest <folder>]
                                             [--refresh]

It prints counts only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TRACKER_DIR = "NG Conversion program Claude"
_SNAPSHOTS = ("SheetGoods", "Edgeband", "JobSummary", "Pricing", "meta")
_ARCHIVES = "ingested_tracker_inbox_*.csv"


def _p1b_collector():
    """tools\\collect_p1b_documents.py, for its search of G: for the CNC operations folder."""
    spec = importlib.util.spec_from_file_location(
        "ngr_tools_collect_p1b", Path(__file__).with_name("collect_p1b_documents.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module             # dataclasses look their module up while it loads
    spec.loader.exec_module(module)
    return module


class CollectError(Exception):
    """Something the person running this must fix; its text holds no job name."""


# ----- the plan


def plan_copies(tracker_dir: Path) -> list[tuple[Path, str, str]]:
    """(source, path under dest, archived name) for each archive, oldest first, and each
    snapshot file. Archive names are time stamps, so their order is the order they landed."""
    archives = sorted(tracker_dir.glob(_ARCHIVES), key=lambda p: p.name)
    if not archives:
        raise CollectError("the tracker folder holds no archived inbox file")
    plan = [(path, f"inbox/{i:02d}.csv", path.name) for i, path in enumerate(archives)]
    for tab in _SNAPSHOTS:
        name = f"tracker_snapshot_{tab}.csv"
        if not (tracker_dir / name).is_file():
            raise CollectError(f"the tracker's {tab} snapshot is missing")
        plan.append((tracker_dir / name, f"snapshot/{name}", ""))
    return plan


def run_copies(plan: list[tuple[Path, str, str]], dest: Path, *,
               refresh: bool) -> tuple[int, int]:
    """Make the copies and write inbox\\index.json. Returns (copied, kept)."""
    copied = kept = 0
    index = []
    for source, relative, archived in plan:
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not refresh:
            kept += 1
        else:
            shutil.copy2(source, target)        # keeps the file's time
            copied += 1
        if archived:
            index.append({"file": target.name, "archived": archived,
                          "modified": datetime.fromtimestamp(target.stat().st_mtime)
                          .isoformat(timespec="seconds")})
    text = json.dumps(index, indent=1, ensure_ascii=False) + "\n"
    (dest / "inbox" / "index.json").write_text(text, encoding="utf-8", newline="\n")
    return copied, kept


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy P2's tracker files into the fixtures.")
    parser.add_argument("--cnc", help="the CNC operations folder (found on G: when left out)")
    parser.add_argument("--dest", default=str(ROOT / ".local-fixtures" / "p2" / "tracker"))
    parser.add_argument("--refresh", action="store_true", help="replace copies already there")
    args = parser.parse_args(argv)
    try:
        cnc = (Path(args.cnc) if args.cnc else _p1b_collector().find_cnc(None)).resolve()
        dest = Path(args.dest).resolve()
        if dest.is_relative_to(cnc):
            raise CollectError("--dest is inside the Drive folder this only reads")
        copied, kept = run_copies(plan_copies(cnc / _TRACKER_DIR), dest, refresh=args.refresh)
        print(f"tracker: {copied} copied, {kept} already there")
    except Exception as err:  # noqa: BLE001 - only our own words or an error's name: an
        # OSError's text holds a path, and a path can name a job. The P1b collector's search
        # raises its own CollectError, in the same plain words.
        message = str(err) if type(err).__name__ == "CollectError" else type(err).__name__
        print(f"Could not collect the tracker files: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
