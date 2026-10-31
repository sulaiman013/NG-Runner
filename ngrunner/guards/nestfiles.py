r"""
What a job's folder in Mozaik Shared\Jobs holds, for the guards: its nests, rooms and lock files.

Why it is apart from scan.py: that file lists NG Jobs, where the programs land. These are the files
that say what the programs should be, in another folder on another drive, and scan.py calls this for
the two things it needs: a listing every pass, and a read of what has settled. It holds no store and
has no clock of its own, as scan.py doesn't: the pass runs on a worker.

The rules, since none of this has met a real file yet (docs/p1b/build-spec.md, section 1):
- A .opt is read only once listings have shown it unchanged for two minutes, hashing the same twice,
  and parsing (design 5, Reads, and guards/opt.py). That goes whether Mozaik is running or not: nothing
  gates on the observer in P1b, and a wait of two minutes costs nothing.
- What was there at the first listing was there at install, and raises nothing until it changes. That
  listing is its own first scan, so an upgrade from before this part alarms nobody either.
- A nest gone from a listing is gone after two polls, and what is known of it is kept: one that comes
  back unchanged is what it was. A cloud drive starting up shows an empty folder, and every old nest
  with no sheets must not ask to be saved when it fills again.
- A folder that can't be listed says nothing, least of all that its nests are gone.

Measured on 10 real jobs (2026-10-31): a nest is Jobs\<Job>\<Material>.opt, and the program folder
NG Jobs\<Job>\<Material>, the same name: 19 of 19 with programs on G:. ASSUMPTION: a _Gar job's names
compare the same; none of the 10 is one. One that doesn't match reads as its own material with no
programs, which is NOT CHECKED, and never a pass.
Measured: rooms are Room0.des to Room#.des (FAILURES 12, SCHEMA 41), each with a version line too.
Settled on the 30 real .des/.bak pairs (2026-10-31): a re-save of the same design changes the file
(20 of 30 differ in bytes: annotations, Room 0's renumbered products, two counters) and not the
cutlist (des_cutlist printed the same from all 30). So a room is known by its design
(design.room_identity), not its bytes, and a pass stands through Mozaik re-saving it on close. The one
pair whose identity differs (Case G's Room 1) differs in groove flags, which the nest's own operations
show: a real change, and the pass goes. A room that doesn't read as a design has no identity, and then
no pass can stand on it.
"""
from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ngrunner.clock import from_iso, to_iso
from ngrunner.guards.bak import DesignUnreadable
from ngrunner.guards.design import room_identity
from ngrunner.guards.facts import CheckedFiles, DesignMatch, OptFacts, ToolVerdict
from ngrunner.guards.observer import LOCK_FILE, LOCK_FILES, OPTIMIZER_LOCK, Locks
from ngrunner.guards.opt import OPT_SETTLES_AFTER, OptUnreadable, read_opt_file
from ngrunner.guards.reads import Listed, Unreadable, listed_of, read_shared, stat_words
from ngrunner.messages import templates as words

JOBS_FOLDER = "Jobs"
OLD = "old"
_OPT_ENDING = ".opt"
_ROOM_FILE = re.compile(r"^Room\d+\.des$", re.IGNORECASE)


@dataclass(frozen=True)
class NestListed:
    """A .opt as a listing gives it, and where it is."""

    path: Path
    listed: Listed


@dataclass(frozen=True)
class JobFiles:
    """One job's folder in Mozaik Shared\\Jobs, by listing. opts: by material's name."""

    path: Path
    opts: Mapping[str, NestListed] = field(default_factory=dict)
    rooms: Mapping[str, Listed] = field(default_factory=dict)
    locks: Mapping[str, Listed] = field(default_factory=dict)

    def rooms_stamp(self) -> str:
        """Whether the rooms changed: their names, sizes and times, hashed, so a listing can say."""
        lines = [f"{name}|{stat.size}|{to_iso(stat.mtime)}" for name, stat in sorted(self.rooms.items())]
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()


@dataclass(frozen=True)
class NestRead:
    """One material's nest as read for a verdict. problem: the words for the board when it couldn't
    be read, and why what happened, for Sulaiman. retry: read it again next pass. rooms: the room files
    on disk now, as (name, sha256), empty when any can't be read, which can't pass. tools, design:
    what the checks said, and checks_ran that they ran on these very files just now. check_failed:
    the name of what stopped them."""

    listed: Listed
    opt: OptFacts | None = None
    problem: str | None = None
    why: str | None = None
    retry: bool = False
    rooms: tuple[tuple[str, str], ...] = ()
    tools: tuple[ToolVerdict, ...] = ()
    design: DesignMatch | None = None
    checks_ran: bool = False
    check_failed: str | None = None


@dataclass(frozen=True)
class ToCheck:
    """What Jim's checks and the design match are to run on, when they can (group 3)."""

    folder: str
    material: str
    opt_path: Path
    job_dir: Path
    program_dir: Path
    files: CheckedFiles


NestChecks = Callable[[ToCheck], tuple[tuple[ToolVerdict, ...], DesignMatch | None]]


# ----- listing


def list_jobs(root: Path) -> tuple[dict[str, JobFiles], set[str]]:
    """Every job folder under root but Old, and those that couldn't be listed. Raises OSError when
    root itself can't be listed: nothing is known then, least of all that the nests are gone."""
    with os.scandir(root) as entries:
        names = [entry.name for entry in entries if entry.is_dir(follow_symlinks=False)
                 and entry.name.casefold() != OLD]
    jobs: dict[str, JobFiles] = {}
    unlisted: set[str] = set()
    for name in sorted(names):
        try:
            jobs[name] = _list_job(root / name)
        except OSError:
            unlisted.add(name)
    return jobs, unlisted


def _list_job(path: Path) -> JobFiles:
    opts: dict[str, NestListed] = {}
    rooms: dict[str, Listed] = {}
    locks: dict[str, Listed] = {}
    with os.scandir(path) as entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            name = entry.name
            if name.casefold().endswith(_OPT_ENDING):
                opts[name[: -len(_OPT_ENDING)]] = NestListed(Path(entry.path), listed_of(entry))
            elif _ROOM_FILE.match(name):
                rooms[name] = listed_of(entry)
            elif name in LOCK_FILES:
                locks[name] = listed_of(entry)
    return JobFiles(path=path, opts=opts, rooms=rooms, locks=locks)


def in_ng_spelling(jobs: Mapping[str, JobFiles], unlisted: set[str],
                   ng_folders: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, JobFiles], set[str]]:
    """The same names as NG Jobs spells them. Windows doesn't care about case, and a nest must land on
    its programs' row and not on one of its own."""
    folders = {folder.casefold(): folder for folder in ng_folders}
    spelled: dict[str, JobFiles] = {}
    for name, job in jobs.items():
        folder = folders.get(name.casefold(), name)
        materials = {material.casefold(): material for material in ng_folders.get(folder, {})}
        spelled[folder] = JobFiles(path=job.path, rooms=job.rooms, locks=job.locks, opts={
            materials.get(material.casefold(), material): nest for material, nest in job.opts.items()})
    return spelled, {folders.get(name.casefold(), name) for name in unlisted}


# ----- what has settled, and what is due


def is_settled(said: Mapping[str, Any] | None, listed: Listed, now: datetime) -> bool:
    """Listings have shown this very file for two minutes."""
    return (said is not None and said.get("seen") == stat_words(listed)
            and now - from_iso(said["since"]) >= OPT_SETTLES_AFTER)


def rooms_moved(said: Mapping[str, Any], job: JobFiles, now: datetime) -> bool:
    """The rooms have settled into something not yet acted on."""
    rooms = said.get("rooms")
    stamp = job.rooms_stamp()
    return (rooms is not None and rooms.get("seen") == stamp and rooms.get("read") != stamp
            and now - from_iso(rooms["since"]) >= OPT_SETTLES_AFTER)


def nests_due(said: Mapping[str, Mapping[str, Any]], jobs: Mapping[str, JobFiles],
              unlisted: set[str], posted: frozenset[tuple[str, str]], now: datetime
              ) -> set[tuple[str, str]]:
    """The materials a verdict is due on because of what is in Mozaik Shared: a nest settled into
    something not yet read, or gone for two polls, and, for one whose verdict counts, rooms changed."""
    due: set[tuple[str, str]] = set()
    for folder in (*jobs, *(name for name in said if name not in jobs)):
        if folder in unlisted:
            continue
        job = jobs.get(folder)
        kept = said.get(folder, {})
        opts_said = kept.get("opts", {})
        moved = job is not None and rooms_moved(kept, job, now)
        for material, nest in (job.opts.items() if job else ()):
            one = opts_said.get(material)
            counts = (folder, material) in posted
            if is_settled(one, nest.listed, now) and (
                    one.get("read") != stat_words(nest.listed)
                    or (counts and (moved or one.get("missing")))):
                due.add((folder, material))
        for material, one in opts_said.items():
            if (job is None or material not in job.opts) and one.get("missing") == 1 and (
                    (folder, material) in posted):
                due.add((folder, material))
    return due


def nest_moved_since_read(said: Mapping[str, Any], job: JobFiles | None, material: str) -> bool:
    """Whether what a stored pass rests on looks changed, by listing alone. It goes back to NOT
    CHECKED at once on this: a pass must never stand while its files are moving, and reading them
    waits for them to settle."""
    if job is None or material not in job.opts:
        return True
    one = said.get("opts", {}).get(material)
    rooms = said.get("rooms")
    return (one is None or one.get("read") != stat_words(job.opts[material].listed)
            or rooms is None or rooms.get("read") != job.rooms_stamp())


# ----- reading


def read_nest(nest: NestListed, job: JobFiles, said: Mapping[str, Any] | None, now: datetime,
              *, read: Callable[[Path], bytes] = read_shared) -> NestRead:
    """One material's nest and its job's rooms. It raises nothing a file can cause."""
    if not is_settled(said, nest.listed, now):
        return NestRead(nest.listed, problem=words.NEST_SETTLING, retry=True)
    try:
        opt = read_opt_file(nest.path, read=read)
    except OptUnreadable as exc:
        return NestRead(nest.listed, problem=words.NEST_UNREADABLE, why=str(exc), retry=exc.retry)
    return NestRead(nest.listed, opt=opt, rooms=_room_hashes(job, read))


def _room_hashes(job: JobFiles, read: Callable[[Path], bytes]) -> tuple[tuple[str, str], ...]:
    """Each room as (name, its design's identity): what a pass rests on, and the same through a
    re-save of the same design. Empty when any room can't be read or isn't a design: no pass then."""
    try:
        return tuple((name, room_identity(name, read(job.path / name))) for name in sorted(job.rooms))
    except (Unreadable, DesignUnreadable):
        return ()


def locks_changed(job: JobFiles, said: Mapping[str, Any]) -> bool:
    """So nothing is read every minute for what can't have changed."""
    return {name: stat_words(stat) for name, stat in job.locks.items()} != said.get("locks", {})


# ----- what is kept: one JSON value in the state table, as guards/kept.py holds it


def next_said(said: Mapping[str, Mapping[str, Any]] | None, jobs: Mapping[str, JobFiles],
              unlisted: set[str], reads: Mapping[tuple[str, str], NestRead],
              locks: Mapping[str, Locks], now: datetime) -> dict[str, dict[str, Any]]:
    """What to keep after this pass. said None: the first listing, so all of it is from before."""
    out: dict[str, dict[str, Any]] = {}
    before = said or {}
    for folder in (*jobs, *(name for name in before if name not in jobs)):
        kept = before.get(folder, {})
        if folder in unlisted:
            out[folder] = dict(kept)
            continue
        job = jobs.get(folder) or JobFiles(path=Path())
        opts = {material: _next_opt(kept.get("opts", {}).get(material), nest,
                                    reads.get((folder, material)), now, first=said is None)
                for material, nest in job.opts.items()}
        for material, one in kept.get("opts", {}).items():
            if material not in job.opts:        # 1: missed once. 2: gone, and acted on if it counts
                opts[material] = {**one, "missing": min(one.get("missing", 0) + 1, 2)}
        kept_now = {"opts": opts, "rooms": _next_rooms(kept.get("rooms"), job, now, said is None),
                    "locks": {name: stat_words(stat) for name, stat in job.locks.items()},
                    "held": _held(kept.get("held", []), locks.get(folder), job, now)}
        if job.opts or job.rooms or job.locks or opts:
            out[folder] = kept_now
    return out


def _next_opt(one: Mapping[str, Any] | None, nest: NestListed, read: NestRead | None, now: datetime,
              *, first: bool) -> dict[str, Any]:
    words_now = stat_words(nest.listed)
    if first:
        return {"seen": words_now, "since": to_iso(now), "read": words_now, "install": True}
    if one is None:
        kept = {"seen": words_now, "since": to_iso(now), "read": None, "install": False}
    elif one["seen"] != words_now:
        kept = {"seen": words_now, "since": to_iso(now), "read": one.get("read"), "install": False}
    else:
        kept = {name: value for name, value in one.items() if name != "missing"}
    if read is not None and not read.retry and read.listed == nest.listed:
        kept["read"] = words_now
    return kept


def _next_rooms(rooms: Mapping[str, Any] | None, job: JobFiles, now: datetime,
                first: bool) -> dict[str, Any]:
    stamp = job.rooms_stamp()
    if first or rooms is None:
        return {"seen": stamp, "since": to_iso(now), "read": stamp if first else None}
    if rooms["seen"] != stamp:
        return {"seen": stamp, "since": to_iso(now), "read": rooms.get("read")}
    if now - from_iso(rooms["since"]) >= OPT_SETTLES_AFTER:
        return {**rooms, "read": stamp}
    return dict(rooms)


def _held(before: list[Any], locks: Locks | None, job: JobFiles, now: datetime) -> list[Any]:
    """The lock files that say a job is open, for the board: file, owner, and since when, kept as a
    time so the board can say how old it is at any later moment."""
    if locks is None:
        return [entry for entry in before if entry[0] in job.locks]
    held = []
    for file, owner, age in ((LOCK_FILE, locks.lck_owner, locks.lck_age),
                             (OPTIMIZER_LOCK, locks.opt_lck_owner, locks.opt_lck_age)):
        if age is not None:
            held.append([file, owner, to_iso(now - age)])
    return held
