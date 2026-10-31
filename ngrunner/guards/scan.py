r"""
One pass over NG Jobs for the guards: what is there now, and whatever of it needs reading.

Why it is a pure pass with no store: G: can stall for minutes, and filing must never wait on it
(P1b build spec 4, "Schedule"). So this runs on a worker, with a Plan made beforehand, and hands back
a Scan; watch.py takes that on the scheduler's thread and does all the writing. It stops between
files on a stop request, and a Scan cut short says so, since what it didn't reach isn't missing.

What it lists (build spec 5.4): the top level of NG Jobs but Old, each folder's subfolders, and their
.tap files by name, size and time. A subfolder's own folders aren't looked into: 13 real programs sit
there, and what they are is not known (reported).

A program acts once two polls agree on its size and time, so a half-synced file waits. A material
with anything to judge has all its programs read, since a verdict rests on all of them, including
those from before install, whose hashes the verdict needs. A file that can't be read yet is left out
and tried again next pass: it is never taken for a clean one.

The same pass reads the takeoff of any folder whose JSON has settled, and, when the pre-flight is
due, the tracker, the parts catalog, the crosscheck and any labels that changed.

It lists Mozaik Shared\Jobs too (guards/nestfiles.py), for the nests, rooms and lock files, and reads
those it may: a material being judged gets its nest and rooms, and any lock file that changed is read
for the owner log. Mozaik Shared out of reach costs nothing here: no nest is known then, which is NOT
CHECKED. The checks themselves (Plan.checks, group 3) run here too, on this worker, as they can take
minutes; a stored pass with the same hashes needs no new run.
"""
from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ngrunner.guards.facts import CheckedFiles
from ngrunner.guards.headsup import Takeoff, read_takeoff
from ngrunner.guards.labels import LabelPart, read_catalog, read_labels
from ngrunner.guards.nestfiles import (
    JobFiles, NestChecks, NestListed, NestRead, ToCheck, in_ng_spelling, list_jobs, locks_changed,
    nests_due, read_nest,
)
from ngrunner.guards.observer import Locks, mozaik_running, read_locks
from ngrunner.guards.preflight import CheckFile, read_check_file
from ngrunner.guards.reads import Listed, Unreadable, listed_of, read_shared, stat_words
from ngrunner.guards.tap import Tap, read_tap
from ngrunner.guards.tracker import TrackerSnapshot, TrackerUnreadable, read_tracker
from ngrunner.store import ProgramRecord

OLD = "old"
TAKEOFF_ENDING = "_ic_conversion.json"
LABELS_ENDING = "labels.pdf"
CHECK_ENDING = "_crosscheck.txt"
CATALOG = "parts_catalog.csv"
_NOT_PROGRAMS = ("~$",)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_log = logging.getLogger("ngrunner.guards")

Place = tuple[str, str]                # a folder and one of its materials
Key = tuple[str, str, str]             # and a program in it


@dataclass(frozen=True)
class Plan:
    """What a pass needs to know, read from the store first.

    first_scan: nothing has been recorded yet, so this pass only lists.
    known: every program the store holds.
    missing_before: programs a listing already failed to find; a second miss means it is gone.
    gone: programs already acted on as gone: they judge nothing until they come back.
    redo: materials to judge again, whether or not anything changed.
    takeoffs: each folder's takeoff as last seen and last read: {"seen": [..], "read": [..]}.
    tracker_folder: where the tracker's snapshots are; None with no setting.
    lock_folders: the folders to read for the pre-flight now; empty when it isn't due.
    labels_seen: each folder's labels files as last read, so an unchanged file isn't read again.
    mozaik_jobs: Mozaik Shared\\Jobs; None leaves Mozaik Shared out of this pass.
    nest_files: what listings there last showed (guards/kept.py); None before the first listing.
    passes: each stored pass, by what it rests on.
    posted: materials whose verdict counts, so a change to their job's rooms judges them again.
    checks: what runs Jim's checks (group 3); None with no toolkit, and so no NEST_CHECKED.
    now: the time of this pass, for how long a nest has been unchanged.
    stop: asked between files.
    """

    ng_jobs: Path
    first_scan: bool = False
    known: Mapping[Key, ProgramRecord] = field(default_factory=dict)
    missing_before: frozenset[Key] = frozenset()
    gone: frozenset[Key] = frozenset()
    redo: frozenset[Place] = frozenset()
    takeoffs: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    tracker_folder: Path | None = None
    lock_folders: tuple[str, ...] = ()
    labels_seen: Mapping[str, str] = field(default_factory=dict)
    mozaik_jobs: Path | None = None
    nest_files: Mapping[str, Mapping[str, Any]] | None = None
    passes: Mapping[Place, CheckedFiles] = field(default_factory=dict)
    posted: frozenset[Place] = frozenset()
    checks: NestChecks | None = None
    now: datetime | None = None
    stop: Callable[[], bool] = lambda: False


@dataclass(frozen=True)
class TakeoffRead:
    """A settled takeoff, read: its hash, and what it says, or None when it doesn't read."""

    folder: str
    listed: Listed
    sha256: str
    takeoff: Takeoff | None


@dataclass
class LockFacts:
    """What the pre-flight reads. problem: why the snapshot couldn't be read, in plain words."""

    snapshot: TrackerSnapshot | None = None
    problem: str | None = None
    catalog: tuple[Mapping[str, str], ...] = ()
    checks: dict[str, CheckFile | None] = field(default_factory=dict)
    labels: dict[str, tuple[str, list[tuple[datetime, tuple[LabelPart, ...]]]]] = field(
        default_factory=dict)


@dataclass
class Scan:
    """What one pass found. reachable: NG Jobs itself could be listed. stopped: it was cut short,
    so nothing can be concluded from it. failed: the name of what went wrong, when it did.
    unlisted: folders that couldn't be listed: nothing about them is known, least of all that their
    programs are gone. to_judge: materials whose programs were read, for a verdict.
    mozaik: the job folders in Mozaik Shared by NG Jobs' spelling; None when it couldn't be listed.
    nests_due: materials a verdict is due on because of a nest or a room. nests: what was read of
    each nest. locks: who the lock files name, for folders where one changed."""

    reachable: bool = True
    stopped: bool = False
    failed: str | None = None
    folders: dict[str, dict[str, dict[str, Listed]]] = field(default_factory=dict)
    unlisted: set[str] = field(default_factory=set)
    other_failures: list[str] = field(default_factory=list)   # a part's own, by its plain name
    to_judge: set[Place] = field(default_factory=set)
    taps: dict[Key, Tap] = field(default_factory=dict)
    unread: set[Key] = field(default_factory=set)
    takeoffs_listed: dict[str, Listed] = field(default_factory=dict)
    takeoffs: list[TakeoffRead] = field(default_factory=list)
    lock: LockFacts | None = None
    mozaik: dict[str, JobFiles] | None = None
    mozaik_unlisted: set[str] = field(default_factory=set)
    mozaik_looked: bool = False
    mozaik_running: bool | None = None
    nests_due: set[Place] = field(default_factory=set)
    nests: dict[Place, NestRead] = field(default_factory=dict)
    locks: dict[str, Locks] = field(default_factory=dict)

    def listed(self, place: Place) -> dict[str, Listed]:
        return self.folders.get(place[0], {}).get(place[1], {})

    def is_whole(self) -> bool:
        return (self.reachable and not self.stopped and self.failed is None
                and not self.unlisted)


class _Stop(Exception):
    """A stop was asked for, between two files."""


def scan(plan: Plan) -> Scan:
    """One pass. It raises nothing a file can cause: see Scan."""
    found = Scan()
    try:
        _list_all(plan, found)
        if found.reachable:
            _on_its_own("nest files", lambda: _list_mozaik(plan, found), found)
        if found.reachable and not plan.first_scan:
            _read_programs(plan, found)
            _on_its_own("nests", lambda: _read_nests(plan, found), found)
            _on_its_own("takeoffs", lambda: _read_takeoffs(plan, found), found)
            _on_its_own("lock pre-flight", lambda: _read_for_preflight(plan, found), found)
    except _Stop:
        found.stopped = True
    return found


def _on_its_own(what: str, work: Callable[[], None], found: Scan) -> None:
    """What goes wrong with a takeoff or the tracker must cost no verdict: the programs are done
    already, and a bad file would otherwise blind the nest guard every minute."""
    try:
        work()
    except _Stop:
        raise
    except Exception as exc:  # noqa: BLE001 - see above; the name goes to DEV, and never its text
        _log.exception("Reading for the guards' %s failed", what)
        found.other_failures.append(f"{what} ({type(exc).__name__})")


def _checkpoint(plan: Plan) -> None:
    if plan.stop():
        raise _Stop


# ----- listing


def _list_all(plan: Plan, found: Scan) -> None:
    try:
        folders = sorted(_folders_in(plan.ng_jobs))
    except OSError:
        found.reachable = False
        return
    for name in folders:
        _checkpoint(plan)
        try:
            _list_folder(plan.ng_jobs / name, name, found)
        except OSError:
            found.unlisted.add(name)
            found.folders.pop(name, None)


def _folders_in(root: Path) -> list[str]:
    with os.scandir(root) as entries:
        return [entry.name for entry in entries if entry.is_dir(follow_symlinks=False)
                and entry.name.casefold() != OLD]


def _list_folder(path: Path, folder: str, found: Scan) -> None:
    materials: dict[str, dict[str, Listed]] = {}
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                materials[entry.name] = _programs_in(Path(entry.path))
            elif entry.is_file(follow_symlinks=False):
                _note_takeoff(entry, folder, found)
    found.folders[folder] = materials


def _programs_in(path: Path) -> dict[str, Listed]:
    with os.scandir(path) as entries:
        return {entry.name: listed_of(entry) for entry in entries
                if entry.name.casefold().endswith(".tap") and entry.is_file(follow_symlinks=False)
                and not entry.name.startswith(_NOT_PROGRAMS)}


def _note_takeoff(entry: os.DirEntry[str], folder: str, found: Scan) -> None:
    if entry.name.casefold().endswith(TAKEOFF_ENDING):
        listed = listed_of(entry)
        current = found.takeoffs_listed.get(folder)
        if current is None or listed.mtime > current.mtime:
            found.takeoffs_listed[folder] = listed


# ----- programs


def _read_programs(plan: Plan, found: Scan) -> None:
    rows_of = rows_by_place(plan.known)
    for place in sorted({*_places(found, rows_of), *found.nests_due}):
        if (place[0] in found.unlisted
                or not _has_something_to_judge(plan, found, place, rows_of.get(place, {}))):
            continue
        found.to_judge.add(place)
        for name in sorted(found.listed(place)):
            key = (*place, name)
            if _is_settled(rows_of.get(place, {}).get(name), found.listed(place)[name]):
                _checkpoint(plan)
                _read_program(plan.ng_jobs.joinpath(*key), key, found.listed(place)[name], found)


def rows_by_place(known: Mapping[Key, ProgramRecord]) -> dict[Place, dict[str, ProgramRecord]]:
    """Each material's rows by program name. Rows only pile up, so this is made once per pass, and
    no material searches them all."""
    rows: dict[Place, dict[str, ProgramRecord]] = {}
    for (folder, material, name), row in known.items():
        rows.setdefault((folder, material), {})[name] = row
    return rows


def _places(found: Scan, rows_of: Mapping[Place, Any]) -> list[Place]:
    here = {(folder, material) for folder, materials in found.folders.items()
            for material in materials}
    return sorted(here | set(rows_of))


def _is_settled(row: ProgramRecord | None, listed: Listed) -> bool:
    return row is not None and (row.size, row.mtime) == (listed.size, listed.mtime)


def _has_something_to_judge(plan: Plan, found: Scan, place: Place,
                            rows: Mapping[str, ProgramRecord]) -> bool:
    """A material is judged when a new program has settled, a program came back or has been gone
    for two polls, an answer asked for it, or its nest or a room has moved."""
    if place in plan.redo or place in found.nests_due:
        return True
    listed = found.listed(place)
    for name, stat in listed.items():
        row = rows.get(name)
        if row is not None and _is_settled(row, stat) and (row.state == "new"
                                                           or (*place, name) in plan.gone):
            return True
    return any((*place, name) in plan.missing_before for name in rows if name not in listed)


def _read_program(path: Path, key: Key, listed: Listed, found: Scan) -> None:
    """A file that isn't the size the listing said has changed since, so it hasn't settled."""
    try:
        data = read_shared(path)
    except Unreadable:
        found.unread.add(key)
        return
    if len(data) == listed.size:
        found.taps[key] = read_tap(key[2], data)
    else:
        found.unread.add(key)


# ----- Mozaik Shared: the nests, rooms and lock files


def _list_mozaik(plan: Plan, found: Scan) -> None:
    if plan.mozaik_jobs is None or plan.now is None:
        return
    try:
        jobs, unlisted = list_jobs(plan.mozaik_jobs)
    except OSError:
        return                  # nothing is known, so nothing is concluded
    found.mozaik_looked, found.mozaik_running = True, mozaik_running()
    found.mozaik, found.mozaik_unlisted = in_ng_spelling(jobs, unlisted, found.folders)
    said = plan.nest_files or {}
    for folder, job in found.mozaik.items():
        if locks_changed(job, said.get(folder, {})):
            _checkpoint(plan)
            found.locks[folder] = read_locks(job.path, now=plan.now, read=read_shared)
    if plan.nest_files is not None and not plan.first_scan:
        found.nests_due = nests_due(plan.nest_files, found.mozaik, found.mozaik_unlisted,
                                    plan.posted, plan.now)


def _read_nests(plan: Plan, found: Scan) -> None:
    """Every material being judged gets its nest and rooms read, whatever brought it here, since a
    verdict rests on all of them. The first listing reads nothing: all that is from before install."""
    if found.mozaik is None or plan.nest_files is None or plan.now is None:
        return
    for place in sorted(found.to_judge):
        job = found.mozaik.get(place[0])
        nest = None if job is None else job.opts.get(place[1])
        if nest is not None:
            _checkpoint(plan)
            said = plan.nest_files.get(place[0], {}).get("opts", {}).get(place[1])
            read = read_nest(nest, job, said, plan.now, read=read_shared)
            found.nests[place] = _with_checks(plan, found, place, nest, job, read)


def _with_checks(plan: Plan, found: Scan, place: Place, nest: NestListed, job: JobFiles,
                 read: NestRead) -> NestRead:
    """What the checks say, only on a nest with sheets, all its programs and all its rooms read, and
    only when no stored pass already stands on these very files. A check that breaks is no pass."""
    if plan.checks is None or read.opt is None or not read.opt.sheets or not read.rooms:
        return read
    listed = found.listed(place)
    taps = [found.taps.get((*place, name)) for name in sorted(listed)]
    if not taps or None in taps:
        return read
    files = CheckedFiles(read.opt.sha256, tuple((tap.name, tap.sha256) for tap in taps), read.rooms)
    if plan.passes.get(place) == files:
        return read
    to_check = ToCheck(place[0], place[1], nest.path, job.path,
                       plan.ng_jobs / place[0] / place[1], files)
    try:
        tools, design = plan.checks(to_check)
    except Exception as exc:  # noqa: BLE001 - the name goes to DEV, and the material stays NOT CHECKED
        _log.exception("A check of a nest failed")
        return replace(read, checks_ran=True, check_failed=type(exc).__name__)
    return replace(read, tools=tuple(tools), design=design, checks_ran=True)


# ----- takeoffs


def _read_takeoffs(plan: Plan, found: Scan) -> None:
    for folder, listed in sorted(found.takeoffs_listed.items()):
        said = plan.takeoffs.get(folder, {})
        shape = stat_words(listed)
        if shape != said.get("seen") or shape == said.get("read"):
            continue
        _checkpoint(plan)
        try:
            data = read_shared(_takeoff_path(plan.ng_jobs / folder))
        except (OSError, Unreadable):
            continue
        found.takeoffs.append(TakeoffRead(folder, listed, hashlib.sha256(data).hexdigest(),
                                          read_takeoff(data)))


def _takeoff_path(folder: Path) -> Path:
    named = [path for path in folder.iterdir()
             if path.name.casefold().endswith(TAKEOFF_ENDING) and path.is_file()]
    return max(named, key=lambda path: path.stat().st_mtime_ns)


# ----- the pre-flight's files


def _read_for_preflight(plan: Plan, found: Scan) -> None:
    if not plan.lock_folders or plan.tracker_folder is None:
        return
    lock = LockFacts()
    try:
        lock.snapshot = read_tracker(plan.tracker_folder)
    except TrackerUnreadable as exc:
        lock.problem = str(exc)
    try:
        lock.catalog = read_catalog(read_shared(plan.tracker_folder / CATALOG))
    except Unreadable:
        lock.catalog = ()
    for folder in plan.lock_folders:
        _checkpoint(plan)
        if folder in found.folders:
            _read_folder_for_preflight(plan, folder, lock)
    found.lock = lock           # whole, or not at all: half of one would read as an answer


def _read_folder_for_preflight(plan: Plan, folder: str, lock: LockFacts) -> None:
    path = plan.ng_jobs / folder
    lock.checks[folder] = read_check_file(path / f"{folder}{CHECK_ENDING}")
    files = _labels_files(path)
    stamp = _stamp([*files, plan.tracker_folder / CATALOG])
    if files and stamp != plan.labels_seen.get(folder):
        lock.labels[folder] = (stamp, _read_labels_files(plan, files))


def _labels_files(folder: Path) -> list[Path]:
    try:
        return sorted(path for path in folder.iterdir()
                      if path.name.casefold().endswith(LABELS_ENDING) and path.is_file())
    except OSError:
        return []


def _stamp(files: Iterable[Path]) -> str:
    """Whether a folder's labels changed: their names, sizes and times, hashed, so no name is kept
    in the state table."""
    parts = []
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        parts.append(f"{path.name}|{stat.st_size}|{stat.st_mtime_ns}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _read_labels_files(plan: Plan, files: list[Path]
                       ) -> list[tuple[datetime, tuple[LabelPart, ...]]]:
    read: list[tuple[datetime, tuple[LabelPart, ...]]] = []
    for path in files:
        _checkpoint(plan)
        try:
            when = _EPOCH + timedelta(microseconds=path.stat().st_mtime_ns // 1000)
            read.append((when, read_labels(read_shared(path))))
        except (OSError, Unreadable):
            read.append((_EPOCH, ()))    # one unreadable file: labels can't be vouched for
    return read
