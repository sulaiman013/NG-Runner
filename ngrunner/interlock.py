r"""
The P1a interlock: what commit_write asks before it writes, and again just before the renames
(integration spec section 2; design section 5).

Why it is this small: in P1a NG Runner writes nothing a Mozaik job reads, so the Mozaik conditions
wait for P1b. What P1a must not do is file while paused, write into a folder that was there at
install (an OBSERVED job gets no writes: watch and check only), or write anywhere but NG Jobs and
the control folder. A pause stops filing and nothing else: heartbeats, the digest, messages and
backups all go to the control folder, and go on, or the 45-minute alarm would fire on a paused PC.
So paused applies only to NG Jobs and control\shadow\. HELD is not here: nothing can set it in P1a.
Shadow mode writes nothing in NG Jobs, whoever filed the job there: this is where no path can
forget it (docs/decisions.md).

It reads the store each time it is asked, since a pause or a new OBSERVED job can come while a
commit waits its turn. It takes anything with .store and .roots, so the messenger and the
supervisor can use it as well as the pipeline's Ctx.

p4_interlock is P4's: the first writes of files Mozaik reads, a room's .des fix (design 5, "Mozaik
interlock"), and of the tracker's inbox. P4's build writes only shadow copies, so it refuses anything
below the folders NG Runner is configured with, whatever the commit's roots say. A pause stops every
P4 write. A room waits while any Mozaik process runs here or that can't be told, while the owner log
has seen anyone but its first owner (decision 4), while its job has a lock file, until two polls two
minutes apart have found none, while its job has no closed marker (decision 7e: such jobs get their
fixes as steps for Jim), while anything in its folder changed in the last two minutes (NG Runner's own
write and temp files aside), while its backup is newer or larger and holds another design (decision
7d, Q19: it may hold unsaved work), and while NG Runner holds no lease on it. A batch and a job sheet
are files Mozaik never reads, so Mozaik doesn't hold them up. One built "releasing" lets only a lease
file through, and through a pause: Pause and release-all hand every lease back to Jim's chat.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ngrunner.guards.bak import DesignUnreadable, check_room
from ngrunner.guards.design import room_identity
from ngrunner.guards.observer import LOCK_FILE, OPTIMIZER_LOCK, UNS_FILE, mozaik_running
from ngrunner.guards.reads import Unreadable, read_shared
from ngrunner.store import Db, get_state, list_jobs, list_owner_log

PAUSED = "paused"                    # state key: "yes" or "no" (the supervisor's)
INSTALL_FOLDERS = "install.folders"  # state key: the NG Jobs folders there at install, as JSON
SHADOW = "shadow"                    # control\shadow\: what shadow mode files
GARAGE_SUFFIX = "_Gar"

PAUSED_REASON = "NG Runner is paused, so nothing is filed until it is resumed at the PC."
SHADOW_REASON = "NG Runner is in shadow mode, so nothing is written in NG Jobs."


def p1a_interlock(ctx: Any) -> Callable[[list[Path]], str | None]:
    """The interlock for commit_write: given the target paths, a plain reason not to write them,
    or None when they may be written. ctx needs .store and .roots, and .mode when it can file. If
    the store can't be read this raises, and commit_write refuses: a safety check that cannot run
    means wait."""
    store, roots = ctx.store, ctx.roots

    def check(targets: list[Path]) -> str | None:
        watched: set[str] | None = None
        for target in targets:
            place = _place(Path(target), roots)
            if place is None:
                return (f"{Path(target).name} is outside NG Jobs and the control folder, so NG"
                        " Runner does not write it in this phase.")
            filing, folder = place
            if filing and is_paused(store):
                return PAUSED_REASON
            if getattr(ctx, "mode", None) == SHADOW and _below(Path(target), roots.ng_jobs):
                return SHADOW_REASON
            if folder is not None:
                watched = _observed_folders(store) if watched is None else watched
                if folder.casefold() in watched:
                    return (f"{folder} was there when NG Runner was installed, so NG Runner only"
                            " watches it and writes nothing into it.")
        return None

    return check


def is_paused(db: Db) -> bool:
    """Whether filing is paused. Anything but a plain "no" reads as paused: pausing is safe."""
    return (get_state(db, PAUSED) or "no") != "no"


def _place(target: Path, roots: Any) -> tuple[bool, str | None] | None:
    """(is this filing, the job folder it is in), or None for a target outside both roots."""
    below = _below(target, roots.ng_jobs)
    if below is not None:
        return True, below[0] if len(below) > 1 else None
    below = _below(target, roots.control)
    if below is None:
        return None
    if below and below[0].casefold() == SHADOW:
        return True, below[1] if len(below) > 2 else None
    return False, None


def _below(target: Path, root: Path) -> tuple[str, ...] | None:
    """The names of target below root, however the root is spelled (commit_write gives targets
    spelled from its resolved root); None when it is not below it."""
    parts = _folded(target)
    for spelling in (root, Path(os.path.realpath(root))):
        start = _folded(spelling)
        if parts[:len(start)] == start and len(parts) > len(start):
            return target.parts[len(start):]
    return None


def _folded(path: Path) -> tuple[str, ...]:
    return tuple(part.casefold() for part in Path(os.path.abspath(path)).parts)


def _observed_folders(db: Db) -> set[str]:
    """The folders that get no writes, case-folded: those there at install, an OBSERVED job's."""
    found = {name.casefold() for name in json.loads(get_state(db, INSTALL_FOLDERS) or "[]")}
    for job in list_jobs(db):
        if job.mode == "observed":
            names = (job.folder, job.name, job.name + GARAGE_SUFFIX)
            found |= {name.casefold() for name in names if name}
    return found


# ----- P4: rooms in Mozaik Shared, the tracker's inbox, the job sheet

QUIET = timedelta(seconds=120)          # design 5: two polls 2 minutes apart, nothing changed for 120 s
REAL_REASON = ("NG Runner writes only shadow copies until P4 goes live, so nothing is written in"
               " {where}.")
_REAL_PLACES = (("ng_jobs", "NG Jobs"), ("mozaik_shared", "Mozaik Shared"),
                ("control", "the control folder"), ("tracker", "the tracker's own folder"))
_ROOM = re.compile(r"Room[0-9]+\.des", re.IGNORECASE)
_TEMP_SUFFIX = ".ngr-tmp"
_LEASE_FILE = "_NG Runner is handling this job.txt"     # lock.leases.LEASE_NAME
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class P4Interlock:
    """The check commit_write calls for a P4 write. It remembers, per job folder, since when its polls
    have found no lock file, and which of the folder's files NG Runner itself just wrote (note_own),
    so its own rename doesn't read as Jim saving. One per batch of work: a new one starts the wait."""

    def __init__(self, ctx: Any, *, mozaik_running: Callable[[], bool | None],
                 lease_held: Callable[[str], bool], releasing: bool = False) -> None:
        self._ctx = ctx
        self._mozaik_running = mozaik_running
        self._lease_held = lease_held
        self._releasing = releasing
        self._clear_since: dict[str, datetime] = {}
        self._own: dict[str, int] = {}          # path key: the mtime NG Runner's write left on it

    def __call__(self, targets: list[Path]) -> str | None:
        for target in targets:
            target = Path(target)
            reason = self._real_place(target)
            if reason:
                return reason
        if self._releasing:
            if any(Path(t).name != _LEASE_FILE for t in targets):
                return "Only a lease is released through a pause, so nothing else was written."
            return None
        if is_paused(self._ctx.store):
            return PAUSED_REASON
        rooms = [Path(t) for t in targets if Path(t).suffix.casefold() == ".des"]
        if rooms:
            return (self._mozaik() or self._owners()
                    or next(filter(None, (self._room(r) for r in rooms)), None))
        return None

    def note_own(self, path: Path) -> None:
        """NG Runner has just written path: its own rename isn't a change in the folder."""
        try:
            self._own[_key(path)] = os.stat(path).st_mtime_ns
        except OSError:
            pass

    # -- the parts

    def _real_place(self, target: Path) -> str | None:
        roots = self._ctx.roots
        for attr, words in _REAL_PLACES:
            real = getattr(self._ctx, "tracker", None) if attr == "tracker" else getattr(roots, attr)
            if real is not None and _below(target, Path(real)) is not None:
                return REAL_REASON.format(where=words)
        return None

    def _mozaik(self) -> str | None:
        running = self._mozaik_running()
        if running is None:
            return "NG Runner can't tell whether Mozaik is running, so no room is written."
        if running:
            return "Mozaik is running at this PC, so no room is written until it closes."
        return None

    def _owners(self) -> str | None:
        """Decision 4: the owner log watches who opens jobs, and any owner but the first stops writes
        into existing folders at once, until Jim says who."""
        owners = {record.owner for record in list_owner_log(self._ctx.store) if record.owner}
        if len(owners) > 1:
            return ("A lock file has named someone other than the first owner seen (decision 4), so no"
                    " room is written until Jim confirms who opens shared jobs.")
        return None

    def _unsaved(self, room: Path) -> str | None:
        """Decision 7d (Q19): when RULES 81 fires, a backup newer or larger than its room, the two are
        compared by design identity, so a re-save of the same design is no finding. Another design, or
        one that can't be read, may be unsaved work: the room isn't written. NG Runner never picks the
        backup."""
        finding = check_room(room.parent, room.stem)
        if finding.backup_bytes is None:
            return None
        if finding.newer_or_larger is False:
            return None
        backup = room.with_suffix(".bak")
        try:
            same = room_identity(room.name, read_shared(room)) == room_identity(room.name, read_shared(backup))
        except (DesignUnreadable, Unreadable):
            same = False
        if same:
            return None
        return (f"{room.parent.name} may have unsaved work: its {_room_words(room.name)} backup is newer or"
                " larger and holds another design, so the room isn't written.")

    def _room(self, room: Path) -> str | None:
        folder = room.parent
        job = folder.name
        if not _ROOM.fullmatch(room.name):
            return f"{room.name} isn't a room's .des, so it isn't written."
        now = self._ctx.clock.now()
        key = _key(folder)
        if any((folder / lock).exists() for lock in (LOCK_FILE, OPTIMIZER_LOCK)):
            self._clear_since.pop(key, None)
            return f"{job} is open in Mozaik (its lock file is there), so its rooms aren't written."
        since = self._clear_since.setdefault(key, now)
        if now - since < QUIET:
            return (f"Waiting until {job} has had no lock file for 2 minutes, two polls apart, before"
                    " its rooms are written.")
        if not (folder / UNS_FILE).exists():
            return (f"{job} has no MozaikJob.UNS, so NG Runner writes nothing into it (decision 7e):"
                    " its fixes are steps for Jim to do in Mozaik.")
        changed = self._changed_lately(folder, now) or self._unsaved(room)
        if changed:
            return changed
        if not self._lease_held(job):
            return (f"NG Runner holds no lease on {job}, so its rooms aren't written: the chat may be"
                    " working on it.")
        return None

    def _changed_lately(self, folder: Path, now: datetime) -> str | None:
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return f"{folder.name}'s folder could not be read, so its rooms aren't written."
        for entry in entries:
            if entry.name.endswith(_TEMP_SUFFIX):
                continue
            try:
                mtime_ns = entry.stat(follow_symlinks=False).st_mtime_ns
            except OSError:
                return f"{folder.name}'s folder could not be read, so its rooms aren't written."
            if self._own.get(_key(Path(entry.path))) == mtime_ns:
                continue
            if now - (_EPOCH + timedelta(microseconds=mtime_ns // 1000)) < QUIET:
                return (f"{folder.name}'s folder changed in the last 2 minutes, so its rooms wait"
                        " until it has been quiet.")
        return None


def p4_interlock(ctx: Any, *, mozaik_running: Callable[[], bool | None] = mozaik_running,
                 lease_held: Callable[[str], bool] | None = None, releasing: bool = False) -> P4Interlock:
    """The interlock for a P4 commit. ctx needs .store, .roots (the folders NG Runner is configured
    with, which P4's build never writes), .clock, and .tracker, the real tracker folder or None.
    lease_held(job) says whether NG Runner holds an active lease on a job; by default ctx's own.
    releasing: for a lease being released (Pause, release-all), which nothing else may use."""
    held = lease_held if lease_held is not None else ctx.lease_held
    return P4Interlock(ctx, mozaik_running=mozaik_running, lease_held=held, releasing=releasing)


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _room_words(name: str) -> str:
    found = _ROOM.fullmatch(name)
    return f"Room {int(name[4:-4])}" if found else "room's"
