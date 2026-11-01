r"""
A written room committed: through commit_write, with an interlock, into NG Runner's own shadow copy of
Mozaik Shared only, and only when every gate on it passed.

Why: AGENTS.md rule 1 (every write outside the app's data folder goes through commit_write) and rule 2
(never while Mozaik could be using the files). P5a writes rooms Mozaik will open, so until P5 goes live it
writes them as P4 writes its fixes (lock/__init__.py): into a copy on this PC's own disk, never in the
real Mozaik Shared or NG Jobs. Three locks hold that, each on its own:

  the rooms     commit_rooms takes only rooms whose gates all passed (gates.py): a blocked room, a failed
                gate or a could-not-verify one commits nothing, and says which
  the chokepoint  writes.py's "P5 shadow" rules: only Jobs\<Job>\Room<n>.des (n of 1 or more) in a
                Mozaik Shared root that is on this PC's own disk and not named Mozaik Shared; nothing in
                NG Jobs, the control folder or the tracker; a room NG Runner didn't write is never replaced
  the interlock p5_interlock: nothing below the folders NG Runner is configured with (the real ones),
                whatever the commit's roots say; nothing while NG Runner is paused, while any Mozaik
                process runs here or that can't be told, or while the job's folder holds a lock file
                (interlock.py's P4 checks, guards/observer.py's names)
"""
from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ngrunner.build.gates import RoomGates, report
from ngrunner.build.writer import BlockedRoom, WrittenRoom
from ngrunner.guards.observer import LOCK_FILE, OPTIMIZER_LOCK, mozaik_running
from ngrunner.interlock import PAUSED_REASON, is_paused
from ngrunner.writes import CommitResult, FileWrite, Roots, commit_write

P5_SHADOW = "P5 shadow"  # the phase P5a's rooms are written in: shadow copies only (writes.py)
P5_LIVE = "P5"  # the real write: no rules yet, so every write is refused
SHADOW_FOLDER = "P5 shadow"  # in NG Runner's data folder
SHADOW_PLACES = ("NG Jobs copy", "Mozaik copy", "control copy", "data")
JOBS = "Jobs"
REAL_REASON = "NG Runner writes rooms only into its own copy until P5 goes live, so nothing is written in {where}."
_REAL = (("ng_jobs", "NG Jobs"), ("mozaik_shared", "Mozaik Shared"), ("control", "the control folder"))


def p5_shadow_roots(app_data: Path) -> Roots:
    """Folders of their own in NG Runner's data folder, made when missing: P5a's copy of Mozaik Shared, and
    copies of the rest that it never writes in. None is named like a real one."""
    places = [app_data / SHADOW_FOLDER / name for name in SHADOW_PLACES]
    for place in places:
        place.mkdir(parents=True, exist_ok=True)
    ng_jobs, mozaik, control, data = places
    return Roots(ng_jobs, mozaik, control, data)


def room_path(roots: Roots, job: str, room: WrittenRoom) -> Path:
    return roots.mozaik_shared / JOBS / job / room.file_name


# ----- the interlock


@dataclass
class P5Interlock:
    """What commit_write asks before a P5 room is written. ctx needs .store and .roots, the folders NG
    Runner is configured with: the real ones, which P5's build never writes."""
    ctx: Any
    mozaik_running: Callable[[], bool | None] = mozaik_running

    def __call__(self, targets: list[Path]) -> str | None:
        for target in map(Path, targets):
            for attr, words in _REAL:
                real = getattr(self.ctx.roots, attr, None)
                if real is not None and _below(target, Path(real)):
                    return REAL_REASON.format(where=words)
        if is_paused(self.ctx.store):
            return PAUSED_REASON
        running = self.mozaik_running()
        if running is None:
            return "NG Runner can't tell whether Mozaik is running, so no room is written."
        if running:
            return "Mozaik is running at this PC, so no room is written until it closes."
        for folder in {Path(t).parent for t in targets}:
            if any((folder / lock).exists() for lock in (LOCK_FILE, OPTIMIZER_LOCK)):
                return f"{folder.name} is open in Mozaik (its lock file is there), so its rooms aren't written."
        return None


def p5_interlock(ctx: Any, *, running: Callable[[], bool | None] = mozaik_running) -> P5Interlock:
    return P5Interlock(ctx, running)


def _below(target: Path, root: Path) -> bool:
    parts = _folded(target)
    for spelling in (root, Path(os.path.realpath(root))):
        start = _folded(spelling)
        if parts[:len(start)] == start and len(parts) > len(start):
            return True
    return False


def _folded(path: Path) -> tuple[str, ...]:
    return tuple(part.casefold() for part in Path(os.path.abspath(path)).parts)


# ----- the commit


@dataclass(frozen=True)
class Gated:
    """A written room and its gates, as gates.gate_room left them."""
    room: WrittenRoom
    gates: RoomGates


def why_not(rooms: Sequence[Gated | BlockedRoom]) -> list[str]:
    """Why these rooms can't be committed, one line each; [] when every one can."""
    out: list[str] = []
    for r in rooms:
        if isinstance(r, BlockedRoom):
            out += [f"{r.file_name} not written: {where}: {what}: {why}" for where, what, why in r.blocked_by]
        elif r.gates.room != r.room.file_name:
            out.append(f"{r.room.file_name}: its gates are {r.gates.room}'s")
        elif r.gates.missing() or any(g.status != "passed" for g in r.gates.gates):
            out += report(r.gates)
    return out


def commit_rooms(job: str, rooms: Sequence[Gated | BlockedRoom], *, roots: Roots,
                 interlock: Callable[[list[Path]], str | None], expected: dict[str, str | None] | None = None,
                 phase: str = P5_SHADOW) -> CommitResult:
    """Every room of the job into roots.mozaik_shared\\Jobs\\<job>\\, all or none, only when every one passed
    every gate. expected: each room's sha256 as last read, by file name, for one NG Runner wrote before;
    a room not named is new."""
    problems = why_not(rooms)
    if problems:
        return CommitResult(False, reason="; ".join(problems))
    writes = [FileWrite(room_path(roots, job, r.room), r.room.content, (expected or {}).get(r.room.file_name))
              for r in rooms if isinstance(r, Gated)]
    if not writes:
        return CommitResult(False, reason="no room to write")
    return commit_write(writes, roots=roots, interlock=interlock, phase=phase)
