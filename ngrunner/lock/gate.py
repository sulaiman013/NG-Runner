"""
Check-before-lock: whether one of Jim's lock clicks may go ahead, and if not, exactly what is missing
(design 5: "Lock is refused while an unsettled ACT item is open, or without a current check"; design
3.5's Lock gate; decisions 7c and 8).

Why one pure function: a lock is the last gate before the tracker says a job is done, so what lets it
through must be one plain list of tests that a reviewer can read, with the facts read elsewhere. It
reads what the store keeps, by attribute: each nest as guards' verdict (store.NestRecord: material,
state, room_hashes, the design identities its pass rests on), and the job's check as P3 itself gives it
(checks.jobchecks.job_checks: a JobCheck, its state, its newest run, and how many of its findings are
still open). Whether that run stands for the job as it is now is P3's own jobchecks.counts(), on the
rooms' sha256 and the IC Conversion's read now, so the gate and P3 can never disagree about it.

The three clicks (decision 7c, questionnaire 32):
- Lock design records the calculated rows as in progress before the nest: it needs a current check.
- Finish lock completes one material: a current check, and that material's nest Nest checked.
- Lock does both at once: a current check, and every nest of the job Nest checked.

"Current" means for these very rooms: the check finished, nothing made it stale since, it ran on the
bytes each room holds now and on the IC Conversion there now (counts()), and on the design each room
holds now, the one every nest pass rests on too. A check that failed counts only once every finding of
it is settled (P3 says how many aren't); one that could not verify is no check, and neither is one that
failed and also could not verify something. A pass whose rooms' design has changed since is no pass.

Lock without check (decision 8): Jim may confirm a lock with no current check, which is kept as "lock
without check". It waives the check side only, and the result still names what it waived. Nothing
waives a nest: a material not Nest checked never locks.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from ngrunner.checks import jobchecks
from ngrunner.checks.jobchecks import JobCheck
from ngrunner.guards.tracker import plain_material

CLICKS = ("lock_design", "finish_lock", "lock")
NEST_CHECKED = "nest_checked"
_STATE_WORDS = {"not_checked": "NOT CHECKED", "do_not_cut": "DO NOT CUT",
                "save_nest": "its nest isn't saved in the Optimizer", "not_posted": "not posted yet"}
_ROOM = re.compile(r"Room([0-9]+)\.des", re.IGNORECASE)


class NestLike(Protocol):
    @property
    def material(self) -> str: ...
    @property
    def state(self) -> str: ...
    @property
    def room_hashes(self) -> Mapping[str, str]: ...


_NOT_DONE = ("asked", "running", "waiting", "not_run")


@dataclass(frozen=True)
class Missing:
    """One thing a lock is waiting for. waivable: the check side, which "lock without check" waives."""

    code: str
    words: str
    waivable: bool


@dataclass(frozen=True)
class GateResult:
    """allowed: the click may go ahead. missing: everything that stands in its way, or that "lock
    without check" waived. without_check: it is allowed only because Jim chose that, to be kept."""

    allowed: bool
    missing: tuple[Missing, ...]
    without_check: bool = False


def lock_gate(click: str, *, nests: Sequence[NestLike], check: JobCheck | None,
              room_sha256: Mapping[str, str], room_identity: Mapping[str, str],
              conversion_sha256: str | None, material: str | None = None,
              without_check: bool = False) -> GateResult:
    """Whether click may go ahead. check: the job's check as jobchecks.job_checks() gives it, or None.
    room_sha256 and room_identity are the job's rooms as they are on disk now, and conversion_sha256
    its IC Conversion's (None when it has none). material: Finish lock's one."""
    if click not in CLICKS:
        raise ValueError(f"a lock click is one of: {', '.join(CLICKS)}")
    if click == "finish_lock" and not material:
        raise ValueError("Finish lock is for one material")
    missing = [*_check_side(check, room_sha256, room_identity, conversion_sha256)]
    if click == "finish_lock":
        missing += _nest_side([n for n in nests if n.material == material], room_identity, material)
    elif click == "lock":
        missing += _nest_side(list(nests), room_identity, None)
    blocking = [m for m in missing if not (without_check and m.waivable)]
    waived = without_check and any(m.waivable for m in missing)
    return GateResult(not blocking, tuple(missing), waived and not blocking)


# ----- the check side


def _check_side(check: JobCheck | None, sha: Mapping[str, str], identity: Mapping[str, str],
                conversion: str | None) -> list[Missing]:
    run = None if check is None else check.run
    if check is None or run is None or check.state == "none":
        return [_check("no_check", "No check of this job's design has run, so it can't be locked yet")]
    if check.state in _NOT_DONE:
        return [_check("check_not_done", "The job's check hasn't finished")]
    if check.state == "stale":
        return [_check("check_stale", "A room was saved after the job's check ran, so the check is"
                                      " out of date")]
    if not jobchecks.counts(run, rooms=dict(sha), conversion_sha256=conversion or ""):
        changed = _rooms_changed(dict(run.rooms), dict(sha))
        if conversion != run.conversion_sha256:
            changed.append(_check("conversion_changed", "The IC Conversion changed since the check ran, and"
                                                        " the check is of the job against it"))
        return changed or [_check("check_stale", "The job changed since its check ran, so the check is"
                                                 " out of date")]
    if dict(run.identities) != dict(identity):
        return [_check("check_other_design", "The check ran on another version of the design than"
                                             " the rooms hold now")]
    if check.state == "failed" and check.to_fix > 0:
        return [_check("items_open", f"The check found {_count(check.to_fix, 'thing')} to fix that"
                                     f" {'is' if check.to_fix == 1 else 'are'}n't settled")]
    if check.state not in ("passed", "failed") or check.unverified > 0:
        return [_check("check_unsure", "The check could not verify everything it runs, so it is no"
                                       " check of the design")]
    return []


def _rooms_changed(checked: dict[str, str], now: dict[str, str]) -> list[Missing]:
    found = []
    for room in sorted(set(checked) | set(now), key=_room_order):
        if room not in now:
            found.append(_check("room_changed", f"{_room(room)} is gone since the check ran"))
        elif room not in checked:
            found.append(_check("room_changed", f"{_room(room)} is new since the check ran"))
        elif checked[room] != now[room]:
            found.append(_check("room_changed", f"{_room(room)} has changed since the check ran"))
    return found


def _check(code: str, words: str) -> Missing:
    return Missing(code, words, waivable=True)


# ----- the nest side


def _nest_side(nests: Sequence[NestLike], identity: Mapping[str, str],
               material: str | None) -> list[Missing]:
    if not nests:
        where = f"{plain_material(material)}: no nest has been read" if material else \
            "No nest of this job has been read"
        return [Missing("no_nest", where, waivable=False)]
    found = []
    for nest in nests:
        name = plain_material(nest.material)
        state = str(getattr(nest.state, "value", nest.state))
        if state != NEST_CHECKED:
            words = _STATE_WORDS.get(state, "its nest state could not be verified")
            found.append(Missing("nest_not_checked", f"{name}: {words}, not Nest checked", waivable=False))
        elif any(identity.get(room) != held for room, held in nest.room_hashes.items()) or not nest.room_hashes:
            found.append(Missing("nest_stale", f"{name}: a room's design changed after its nest was"
                                               " checked", waivable=False))
    return found


# ----- words


def _room(name: str) -> str:
    found = _ROOM.fullmatch(name)
    return f"Room {int(found[1])}" if found else "A room"


def _room_order(name: str) -> tuple[int, str]:
    found = _ROOM.fullmatch(name)
    return (int(found[1]) if found else 1 << 30, name)


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"
