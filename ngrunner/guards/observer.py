r"""
The observer: whether Mozaik is running, who a job's lock files name, and the owner log (P1b build
spec 5.12, design 3.3).

Why it exists: decision 4 asks whether anyone but Jim opens shared Mozaik jobs, and a lock file names
who has a job only while it is there. So every string read goes in the log, and the first string other
than the first one ever seen goes to Sulaiman at once.

What it must never do: touch a lock. A lock left by a crash blocks every later open (FAILURES 49), and
deleting one is a person's call (design F1, F2), so this only reads. Nothing gates on it in P1b: the
interlock is unchanged (build spec section 4), so no answer here can stop or allow anything.

No real .UNS or .LCK byte has been read on this PC. The .UNS is documented as 7 bytes, JDELA\r\n
(FAILURES 12), and what goes in a .LCK is not documented at all (build spec 8, step 1). None of Jim's
scripts reads what a lock holds: they only look for the file, so the toolkit settles nothing here. So
whatever a lock holds reads as some string and breaks nothing, and each assumption says what to check.
Measured: 7 of 7 real MozaikJob.UNS are the 7 bytes JDELA\r\n (FAILURES 12). No real .LCK came: Case E
was open when its folder was zipped, and its lock wasn't in the zip. ASSUMPTION: a .UNS says who last
had the job. Jim's build scripts copy it from a template job, so on a job his chat built it names
whoever the template named. Only a .LCK is a sighting of someone: read decision 4's answer from those.
Check whether a real .UNS changes when another user closes a job.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psutil as _psutil

from ngrunner.guards.reads import Unreadable, read_shared
from ngrunner.store import Db, first_owner, log_owner, open_dev

UNS_FILE = "MozaikJob.UNS"
LOCK_FILE = "MozaikJob.LCK"
OPTIMIZER_LOCK = "OptimizeJob.LCK"
LOCK_FILES = (UNS_FILE, LOCK_FILE, OPTIMIZER_LOCK)
# ASSUMPTION: these are the process names on Jim's PC (build spec 3.6 has them from a folder listing,
# the live process is unseen, and no script of his names one). Check with Mozaik open; Health says what
# this finds.
MOZAIK_PROCESSES = ("Mozaik.exe", "Optimize.exe")
OTHER_OWNER = "lock_owner_other"
_OWNER_AT_MOST = 80
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Locks:
    """One job's lock files. An owner is None when the file isn't there or couldn't be read, and ""
    when it is empty, since an empty lock is itself a thing to know. lck_age and opt_lck_age are how
    old MozaikJob.LCK and OptimizeJob.LCK are. unread: lock files there that couldn't be read."""

    uns_owner: str | None
    lck_owner: str | None
    opt_lck_owner: str | None
    lck_age: timedelta | None
    opt_lck_age: timedelta | None = None
    unread: tuple[str, ...] = ()

    def owners(self) -> Iterator[tuple[str, str]]:
        """Each file with a string read from it, as (file, owner)."""
        for file, owner in ((UNS_FILE, self.uns_owner), (LOCK_FILE, self.lck_owner),
                            (OPTIMIZER_LOCK, self.opt_lck_owner)):
            if owner is not None:
                yield file, owner


def mozaik_running(*, names: Iterable[str] = MOZAIK_PROCESSES, psutil: Any = _psutil) -> bool | None:
    """Whether a Mozaik process is running; None when processes can't be read, which is never a no."""
    wanted = {name.casefold() for name in names}
    try:
        seen = [process.info.get("name") for process in psutil.process_iter(["name"])]
    except Exception:  # noqa: BLE001 - whatever stops the list, the answer is that it can't tell
        return None
    readable = [name for name in seen if name]
    if any(name.casefold() in wanted for name in readable):
        return True
    return False if readable else None


def read_locks(job_dir: Path, *, now: datetime,
               read: Callable[[Path], bytes] = read_shared) -> Locks:
    """Who the lock files in one job's folder name. It raises nothing a file can cause."""
    owners: dict[str, str | None] = {}
    ages: dict[str, timedelta | None] = {}
    unread = []
    for file in LOCK_FILES:
        path = job_dir / file
        ages[file] = _age(path, now)
        owners[file] = None
        if ages[file] is None:
            continue
        try:
            owners[file] = _owner(read(path))
        except Unreadable:
            unread.append(file)
    return Locks(uns_owner=owners[UNS_FILE], lck_owner=owners[LOCK_FILE],
                 opt_lck_owner=owners[OPTIMIZER_LOCK], lck_age=ages[LOCK_FILE],
                 opt_lck_age=ages[OPTIMIZER_LOCK], unread=tuple(unread))


def note_owners(db: Db, *, folder: str, locks: Locks, at: datetime) -> None:
    """Log every string read. One other than the first ever seen goes to DEV at once (decision 4). An
    empty lock names nobody, so it is logged and raises nothing."""
    for file, owner in locks.owners():
        log_owner(db, at=at, folder=folder, file=file, owner=owner)
        first = first_owner(db)
        if owner and first is not None and owner != first:
            open_dev(db, at=at, code=OTHER_OWNER,
                     text=f"{folder}: {file} names {owner}. Until now every lock named {first}.")


def _age(path: Path, now: datetime) -> timedelta | None:
    """How old the file is, by its own time; None when it isn't there. A clock ahead reads as new."""
    try:
        written = _EPOCH + timedelta(microseconds=path.stat().st_mtime_ns // 1000)
    except (OSError, OverflowError):
        return None
    return max(now - written, timedelta(0))


def _owner(data: bytes) -> str:
    """ASSUMPTION: a lock holds one user string, as JDELA\r\n in a .UNS (FAILURES 12). A .LCK may hold
    more: if so every open reads as a new owner and goes to DEV, which is the place to find out. Keep
    it to its first line then. Printable text only, as it is shown to a person."""
    text = data.decode("utf-8", errors="replace")
    return " ".join("".join(ch for ch in text if ch.isprintable() or ch.isspace()).split())[
        :_OWNER_AT_MOST]
