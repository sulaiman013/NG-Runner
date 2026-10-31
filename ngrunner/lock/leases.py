r"""
Leases: NG Runner's marker in a job's NG Jobs folder that it is handling the job, so Jim's chat leaves
the job's files alone (design 5, F8 and C1; questionnaire 15; GLOSSARY: Lease).

Why a file and a lapse. Jim's chat can't see NG Runner's store, only the job's folder, so the lease is a
file there, "_NG Runner is handling this job.txt", which the RULES line tells the chat to read: never
write a job whose lease is active and unexpired. A dead NG Runner must never lock the chat out (design
10, "Leases expire; release to chat"), so every lease lapses by itself LEASE_TERM after it was last
written, and NG Runner writes it again every RENEW_AFTER while it works: a lease lapses at most half an
hour after NG Runner stops. A lapsed lease is never renewed back to life, since the chat may have
started on the job meanwhile: it must be taken again, which only happens when a job is clear to work.

Releasing writes the file again as released, as commit_write never deletes. Pause, uninstall and
"Release all jobs to chat" release every lease (release_all); one that can't be released doesn't stop
the rest, and says why. A lease file someone else changed is never written over: commit_write
overwrites only a file still holding what NG Runner last wrote there.

What the file holds: its state, NG Runner's holder name (the PC's), and when it was taken and when it
expires, in UTC. No job name, customer or address: the folder says which job it is.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ngrunner.clock import Clock, utc
from ngrunner.lock import P4_SHADOW
from ngrunner.store import Store
from ngrunner.store.locks import get_lease, list_leases, put_lease
from ngrunner.writes import FileWrite, Roots, commit_recorded

LEASE_NAME = "_NG Runner is handling this job.txt"
LEASE_TERM = timedelta(hours=2, minutes=30)
RENEW_AFTER = timedelta(hours=2)            # renewed every 2 hours while NG Runner works
ACTIVE, RELEASED = "active", "released"
_FIRST_LINE = ("NG Runner is handling this job: don't change its files in chat while this lease is"
               " active and hasn't expired.")
_STAMP = "%Y-%m-%dT%H:%M:%SZ"
_LINE = re.compile(r"(State|Holder|Taken|Expires|Released): (.+)")


@dataclass(frozen=True)
class Lease:
    """A lease as its file holds it. released_at is None while it is active."""

    holder: str
    state: str
    taken_at: datetime
    expires_at: datetime
    released_at: datetime | None


@dataclass(frozen=True)
class LeaseResult:
    folder: str
    ok: bool
    lease: Lease | None = None
    reason: str | None = None


def is_active(lease: Lease | None, now: datetime) -> bool:
    """Active and not yet expired: what the chat must leave alone."""
    return lease is not None and lease.state == ACTIVE and utc(now) < lease.expires_at


def lease_held(store: Store, folder: str, now: datetime) -> bool:
    """Whether NG Runner holds an active, unexpired lease on folder, by its own record. The P4
    interlock asks this before any room of the job is written."""
    record = get_lease(store, folder)
    return record is not None and record.state == ACTIVE and utc(now) < record.expires_at


# ----- the file


def lease_bytes(lease: Lease) -> bytes:
    lines = [_FIRST_LINE, f"State: {lease.state}", f"Holder: {lease.holder}",
             f"Taken: {_stamp(lease.taken_at)}", f"Expires: {_stamp(lease.expires_at)}"]
    if lease.released_at is not None:
        lines.append(f"Released: {_stamp(lease.released_at)}")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def read_lease(data: bytes) -> Lease | None:
    """The lease a file holds, or None when it isn't one in this form, which is no lease at all."""
    try:
        lines = data.decode("utf-8").split("\r\n")
    except UnicodeDecodeError:
        return None
    if not lines or lines[0] != _FIRST_LINE or lines[-1] != "":
        return None
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        found = _LINE.fullmatch(line)
        if found is None or found[1] in fields:
            return None
        fields[found[1]] = found[2]
    try:
        lease = Lease(fields["Holder"], fields["State"], _time(fields["Taken"]),
                      _time(fields["Expires"]),
                      _time(fields["Released"]) if "Released" in fields else None)
    except (KeyError, ValueError):
        return None
    if lease.state not in (ACTIVE, RELEASED) or (lease.state == RELEASED) != (lease.released_at is not None):
        return None
    return lease


def _stamp(when: datetime) -> str:
    return utc(when).strftime(_STAMP)


def _time(text: str) -> datetime:
    return datetime.strptime(text, _STAMP).replace(tzinfo=UTC)


# ----- taking, renewing and releasing


def take_lease(folder: str, *, store: Store, roots: Roots, interlock: Callable[[list[Path]], str | None],
               clock: Clock, holder: str) -> LeaseResult:
    """Take the lease on folder, or renew it when NG Runner already holds it."""
    now = _now(clock)
    record = get_lease(store, folder)
    if record is not None and record.state == ACTIVE and now < record.expires_at:
        return renew_lease(folder, store=store, roots=roots, interlock=interlock, clock=clock, holder=holder)
    lease = Lease(holder, ACTIVE, now, now + LEASE_TERM, None)
    return _write(folder, lease, store=store, roots=roots, interlock=interlock, clock=clock)


def renew_lease(folder: str, *, store: Store, roots: Roots, interlock: Callable[[list[Path]], str | None],
                clock: Clock, holder: str) -> LeaseResult:
    """Write the lease again once RENEW_AFTER has passed since it was last written; before that, do
    nothing. A lapsed or released lease is refused: it is taken again, never renewed."""
    now = _now(clock)
    record = get_lease(store, folder)
    if record is None or record.state != ACTIVE:
        return LeaseResult(folder, False, reason=f"NG Runner holds no lease on {folder} to renew.")
    if now >= record.expires_at:
        return LeaseResult(folder, False, reason=(f"The lease on {folder} lapsed, so the chat may be"
                                                  " working on it; it isn't renewed."))
    written_at = record.expires_at - LEASE_TERM
    current = Lease(record.holder, ACTIVE, record.taken_at, record.expires_at, None)
    if now - written_at < RENEW_AFTER:
        return LeaseResult(folder, True, current)
    lease = Lease(holder, ACTIVE, record.taken_at, now + LEASE_TERM, None)
    return _write(folder, lease, store=store, roots=roots, interlock=interlock, clock=clock)


def release_lease(folder: str, *, store: Store, roots: Roots,
                  interlock: Callable[[list[Path]], str | None], clock: Clock, holder: str) -> LeaseResult:
    """Hand the job back to the chat: the file says released."""
    now = _now(clock)
    record = get_lease(store, folder)
    if record is None or record.state != ACTIVE:
        return LeaseResult(folder, True, None)              # nothing held, so nothing to release
    lease = Lease(record.holder, RELEASED, record.taken_at, max(record.expires_at, now + timedelta(seconds=1)),
                  now)
    return _write(folder, lease, store=store, roots=roots, interlock=interlock, clock=clock)


def release_all(*, store: Store, roots: Roots, interlock: Callable[[list[Path]], str | None],
                clock: Clock, holder: str) -> list[LeaseResult]:
    """Release every lease NG Runner holds: Pause, uninstall and "Release all jobs to chat"."""
    return [release_lease(record.folder, store=store, roots=roots, interlock=interlock, clock=clock,
                          holder=holder)
            for record in list_leases(store, state=ACTIVE)]


def _write(folder: str, lease: Lease, *, store: Store, roots: Roots,
           interlock: Callable[[list[Path]], str | None], clock: Clock) -> LeaseResult:
    path = roots.ng_jobs / folder / LEASE_NAME
    content = lease_bytes(lease)
    expected = _current_hash(path)
    if isinstance(expected, LeaseResult):
        return LeaseResult(folder, False, reason=expected.reason)
    result = commit_recorded([FileWrite(path, content, expected)], store=store, clock=clock, roots=roots,
                             interlock=interlock, phase=P4_SHADOW)
    if not result.ok:
        return LeaseResult(folder, False, reason=result.reason)
    put_lease(store, folder=folder, holder=lease.holder, state=lease.state, taken_at=lease.taken_at,
              expires_at=lease.expires_at, released_at=lease.released_at,
              sha256=hashlib.sha256(content).hexdigest())
    return LeaseResult(folder, True, lease)


def _current_hash(path: Path) -> str | None | LeaseResult:
    """The hash of the lease file there now, which commit_write checks is NG Runner's own."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None
    except OSError:
        return LeaseResult(path.parent.name, False, reason="The lease file could not be read.")


def _now(clock: Clock) -> datetime:
    return utc(clock.now()).replace(microsecond=0)
