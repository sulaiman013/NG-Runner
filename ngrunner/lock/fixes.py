r"""
Fixers as diffs: a fix planned against a staged copy, applied only after Jim approves that very
finding, undone by its inverse patch, and judged after his next save by the fix's own check (design 3.2
"Undo", 3.3 "Write", 3.5 "Fixes", 5 "Approvals and survival"; decisions 7a and 7b; RULES section 6).

The life of a fix:
1. plan_fix: the room is read with every share flag and its bytes staged in a folder of NG Runner's own
   on this PC's disk. Jim's fixer runs from the adopted toolkit copy on that copy, never on the room.
   Its output becomes a byte patch (patch.diff) and the elements it touched, read by the fixer's own
   reading (each value by its place in the design, not its offset). The plan is refused unless the
   output passes RULES section 6's byte gate (patch.des_problem), every byte it changed is one of those
   elements' values, and the fix's own check flags each element's panel on the room and none on the
   fixed copy. Then the job's nests are read for parts the fix touches: those flipped by hand in the
   Optimizer (the 10-20 case) are named, and so is each material whose nest holds a touched part,
   which must be re-nested (decision 7b: "[Fix and re-nest <material>]"). The plan's fingerprint is
   the finding: fixer, job, room, the elements with their values, and the hand flips.
2. apply_fix: only with Jim's approval of that fingerprint (decision 7a: one click while on trial).
   The room must still be the bytes the plan was made from; if Jim saved it since, it is planned again
   (replan: the same fingerprint applies again, a finding gone closes, a different one asks again).
   The fixed bytes go through commit_write with P4's rules, which back the room up first and replace it
   only at the hash read. The fix is kept with its patch, its elements, the programs there when it was
   written and its backups.
3. undo_fix: the inverse patch while the room is still exactly what the fix wrote, which gives back the
   original bytes; after a save that kept NG Runner's values, just those values are put back. Refused
   once any value is no longer NG Runner's, and once a program was made from the fixed design.
4. survival: after the next save, the fix's own check on that room, and each element read where it is
   now. Held: NG Runner's values and nothing flagged. Back to the exact old values with a Mozaik session
   open across the write: "Mozaik undid", urgent, with one redo offered. Back, or changed, in a session
   after it: "You changed it in Mozaik", logged. Anything that can't be read: could not verify.

Which fixer: any of Jim's that writes a room and has a check that can confirm it (the Fixer protocol
below; lock/fixers.py names each, and docs/decisions.md says which of his scripts qualify). Each says
what its script changed by its own reading of the room (lock/des.py), whether its check flagged exactly
those parts before and none after (cleared), and whether its check still names one (names). Where a
check names parts by what they show rather than by CabNo, as top_line's, eb_tops' and notch_check's
banding lines do, what it said is a Said: how many lines name each identity, and each part's identity,
so a line that names a part the fix didn't touch, or a touched part no line names, refuses the plan.

P4's build writes shadow copies only (lock.P4_SHADOW): the real Mozaik Shared is never written here.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from ngrunner.clock import Clock
from ngrunner.guards.opt import OptUnreadable, parsed_opt
from ngrunner.guards.reads import Unreadable, read_shared
from ngrunner.guards.tracker import plain_material
from ngrunner.lock import P4_SHADOW
from ngrunner.lock.patch import Patch, PatchRefused, apply_patch, des_problem, diff, inverse, sha256
from ngrunner.store import Store
from ngrunner.store.locks import FixRecord, add_fix, get_fix, mark_fix_undone, record_survival
from ngrunner.toolkit import Snapshot, run_tool
from ngrunner.writes import FileWrite, Roots, commit_recorded

_ROOM = re.compile(r"Room([0-9]+)\.des", re.IGNORECASE)


class FixRefused(Exception):
    """A fix that can't be planned as asked. Its text is plain, with no path."""


@dataclass(frozen=True)
class Element:
    """One value a fix changes: attribute on the operation with ID id, in the product with CabNo cab."""

    cab: str
    id: str
    attribute: str
    before: str
    after: str


@dataclass(frozen=True)
class Said:
    """What a fix's own check said of one room, where its lines name a part by what it shows (a name, a
    size) rather than its CabNo. flagged: how many lines name each identity. keys: each part of the room,
    by the key a fix's elements know it by (the fixer's key), and the identity a line would name it by."""

    flagged: Mapping[str, int]
    keys: Mapping[str, str]


def said_of(lines: Sequence[str], keys: Mapping[str, str]) -> Said | None:
    """A Said, or None when a line names nothing in the room: a check that speaks of a part the room
    doesn't hold can't be read."""
    there = set(keys.values())
    return None if any(line not in there for line in lines) else Said(Counter(lines), keys)


def said_cleared(before: Said, after: Said, keys: Sequence[str] | set[str]) -> bool:
    """The check named exactly the touched parts before, each as often as there are of them, and after,
    neither a touched part nor anything it named before: a line for a part the fix didn't touch, a touched
    part no line names, or a line that is still there, is no fix. Two parts that look alike to the check
    are told apart by the after: the one it still names is the one the fix didn't fix."""
    touched = set(keys)
    if not touched or any(k not in before.keys or k not in after.keys for k in touched):
        return False
    named = Counter({k: v for k, v in before.flagged.items() if v})
    return (Counter(before.keys[k] for k in touched) == named and not said_names(after, touched)
            and not any(after.flagged.get(identity) for identity in named))


def said_names(said: Said, keys: Sequence[str] | set[str]) -> bool:
    """The check names one of these parts, or one of them can't be found to ask about."""
    return any(k not in said.keys or said.flagged.get(said.keys[k]) for k in keys)


class Fixer(Protocol):
    """One of Jim's fixers, run from the adopted copy on a staged room (lock/notch.py was the first).
    finding: the P3 finding it fixes, (checker, what), as checks/findings.py names it."""

    name: str
    finding: tuple[str, str]

    def draft(self, snapshot: Snapshot, base: bytes, *, room_name: str, work: Path, run: Callable[..., Any],
              python: Path | None = None) -> bytes: ...

    def elements(self, base: bytes, result: bytes) -> tuple[Element, ...]: ...

    def locate(self, data: bytes, element: Element) -> tuple[int, int] | None: ...

    def flags(self, snapshot: Snapshot, data: bytes, *, room_name: str, work: Path, run: Callable[..., Any],
              python: Path | None = None) -> Any | None: ...

    def cleared(self, before: Any, after: Any, elements: Sequence[Element]) -> bool: ...

    def names(self, said: Any, elements: Sequence[Element]) -> bool: ...

    def tag(self, number: int, element: Element) -> str: ...

    def short(self, element: Element) -> str: ...

    def words(self, room_name: str, tags: tuple[str, ...], elements: Sequence[Element] = (),
              data: bytes = b"") -> str: ...


class CabFixer:
    """What a fixer whose elements are on products says of them: R1C3 on the card, C3 after a save."""

    def tag(self, number: int, element: Element) -> str:
        return f"R{number}C{element.cab}"

    def short(self, element: Element) -> str:
        return f"C{element.cab}"


@dataclass(frozen=True)
class FixPlan:
    fixer: str
    folder: str
    room: str
    patch: Patch
    elements: tuple[Element, ...]
    tags: tuple[str, ...]
    hand_flipped: tuple[str, ...]
    renest: tuple[str, ...]
    snapshot_sha256: str
    fingerprint: str
    words: tuple[str, ...]


@dataclass(frozen=True)
class FixResult:
    ok: bool
    fix: FixRecord | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Survival:
    code: str             # waiting, held, mozaik_undid, you_changed, could_not_verify
    words: str
    urgent: bool = False
    redo: bool = False    # one redo is offered


# ----- planning


def plan_fix(fixer: Fixer, snapshot: Snapshot, room_path: Path, *, folder: str, work: Path,
             nests: Mapping[str, bytes], run: Callable[..., Any] = run_tool, python: Path | None = None,
             read: Callable[[Path], bytes] = read_shared) -> FixPlan | None:
    """The fix fixer would make to room_path, as a plan for Jim, or None when there is nothing to fix.
    nests holds each of the job's nests' bytes by material. Raises FixRefused."""
    room = room_path.name
    if not _ROOM.fullmatch(room):
        raise FixRefused("Only a room's .des is fixed.")
    try:
        base = read(room_path)
    except Unreadable as exc:
        raise FixRefused(f"The room could not be read: {exc}") from exc
    result = fixer.draft(snapshot, base, room_name=room, work=work, run=run, python=python)
    if result == base:
        return None
    problem = des_problem(base, result)
    if problem:
        raise FixRefused(f"Jim's fixer gave a room that {problem}, so no fix is planned.")
    elements = fixer.elements(base, result)
    if not elements or _substituted(fixer, base, elements) != result:
        raise FixRefused("Jim's fixer changed more than the values it fixes, so no fix is planned.")
    before = fixer.flags(snapshot, base, room_name=room, work=work, run=run, python=python)
    after = fixer.flags(snapshot, result, room_name=room, work=work, run=run, python=python)
    if before is None or after is None or not fixer.cleared(before, after, elements):
        raise FixRefused("The fix's own check doesn't show it fixed, so no fix is planned.")
    number = int(_ROOM.fullmatch(room)[1])
    tags = tuple(dict.fromkeys(fixer.tag(number, e) for e in _in_cab_order(elements)))
    flipped, renest = hand_flips(nests, tags)
    fingerprint = _fingerprint(fixer.name, folder, room, elements, flipped)
    return FixPlan(fixer.name, folder, room, diff(base, result), elements, tags, flipped, renest,
                   snapshot.sha256, fingerprint, _words(fixer, room, tags, flipped, renest, elements, base))


def hand_flips(nests: Mapping[str, bytes], tags: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(the tags flipped by hand in a nest, the materials whose nests hold any of tags). A nest that
    can't be read stops the plan: what it holds can't be known, and a fix on a flipped part is the
    10-20 mistake."""
    flipped: set[str] = set()
    materials: list[str] = []
    wanted = set(tags)
    for material, data in sorted(nests.items()):
        try:
            root = parsed_opt(data)
        except OptUnreadable as exc:
            raise FixRefused(f"The nest of {plain_material(material)} could not be read for hand flips,"
                             " so no fix is planned.") from exc
        parts = {p.get("PartID"): p.get("AssyNo") for p in root.findall("OptimizePart")
                 if p.get("AssyNo") in wanted}
        if parts:
            materials.append(material)
        flipped |= {parts[loc.get("PartID")] for loc in root.iter("OptimizePartLocation")
                    if loc.get("PartID") in parts and loc.get("Flipped") == "True"}
    return tuple(t for t in tags if t in flipped), tuple(materials)


def replan(plan: FixPlan, fresh: FixPlan | None) -> str:
    """After the room changed: "same" (the approval stands, and fresh is applied), "gone" (it closes as
    already fixed) or "changed" (Jim is asked again)."""
    if fresh is None:
        return "gone"
    return "same" if fresh.fingerprint == plan.fingerprint else "changed"


# ----- applying


def apply_fix(plan: FixPlan, *, approved: str, programs: Mapping[str, Sequence[str]], store: Store,
              roots: Roots, interlock: Callable[[list[Path]], str | None], clock: Clock,
              read: Callable[[Path], bytes] = read_shared) -> FixResult:
    """Write plan into its room, once Jim approved exactly it. programs: each material's programs'
    sha256 there now, kept so Undo knows when a newer one was made."""
    if approved != plan.fingerprint:
        return FixResult(False, reason="This fix isn't the one Jim approved, so nothing was written.")
    path = _room_path(roots, plan.folder, plan.room)
    try:
        current = read(path)
    except Unreadable as exc:
        return FixResult(False, reason=f"The room could not be read: {exc}")
    if sha256(current) != plan.patch.base_sha256:
        return FixResult(False, reason=("The room was saved since the fix was planned, so it is planned"
                                        " again before anything is written."))
    try:
        fixed = apply_patch(plan.patch, current)
    except PatchRefused as exc:
        return FixResult(False, reason=str(exc))
    result = commit_recorded([FileWrite(path, fixed, plan.patch.base_sha256)], store=store, clock=clock,
                             roots=roots, interlock=interlock, phase=P4_SHADOW)
    if not result.ok:
        return FixResult(False, reason=result.reason)
    _note_own(interlock, path)
    record = add_fix(store, folder=plan.folder, room=plan.room, fixer=plan.fixer,
                     fingerprint=plan.fingerprint, base_sha256=plan.patch.base_sha256,
                     result_sha256=plan.patch.result_sha256, patch=plan.patch.as_json(),
                     elements=[asdict(e) for e in plan.elements],
                     programs={m: list(h) for m, h in programs.items()},
                     backups=[str(b) for b in result.backups], applied_at=clock.now())
    return FixResult(True, record)


# ----- Undo


def undo_fix(record: FixRecord, fixer: Fixer, *, programs: Mapping[str, Sequence[str]], store: Store,
             roots: Roots, interlock: Callable[[list[Path]], str | None], clock: Clock,
             read: Callable[[Path], bytes] = read_shared) -> FixResult:
    """Put the room back as the fix found it, through the same door it was written by."""
    if record.state != "applied":
        return FixResult(False, reason="This fix was undone already.")
    newer = [m for m, hashes in programs.items() if set(hashes) - set(record.programs.get(m, ()))]
    if newer:
        return FixResult(False, reason=(f"Undo is refused: a program of {plain_material(newer[0])} was made"
                                        " from the fixed design."))
    path = _room_path(roots, record.folder, record.room)
    try:
        current = read(path)
    except Unreadable as exc:
        return FixResult(False, reason=f"The room could not be read: {exc}")
    elements = [Element(**e) for e in record.elements]
    try:
        if sha256(current) == record.result_sha256:
            patch = Patch.from_json(record.base_sha256, record.result_sha256, record.patch)
            back = apply_patch(inverse(patch), current)
        else:
            back = _put_back(fixer, current, elements)
    except (PatchRefused, FixRefused) as exc:
        return FixResult(False, reason=str(exc))
    result = commit_recorded([FileWrite(path, back, sha256(current))], store=store, clock=clock,
                             roots=roots, interlock=interlock, phase=P4_SHADOW)
    if not result.ok:
        return FixResult(False, reason=result.reason)
    _note_own(interlock, path)
    mark_fix_undone(store, record.id, at=clock.now())
    return FixResult(True, get_fix(store, record.id))


def _put_back(fixer: Fixer, current: bytes, elements: Sequence[Element]) -> bytes:
    for e in elements:
        span = fixer.locate(current, e)
        if span is None or current[span[0]:span[1]].decode("utf-8", "replace") != e.after:
            raise FixRefused("Undo is offered only while the fix's values are NG Runner's, and they are"
                             " no longer.")
    back = _substituted(fixer, current, [Element(e.cab, e.id, e.attribute, e.after, e.before)
                                         for e in elements])
    problem = des_problem(current, back)
    if problem:
        raise FixRefused(f"Putting the values back would give a room that {problem}.")
    return back


# ----- survival


def survival(record: FixRecord, fixer: Fixer, snapshot: Snapshot, *, room_bytes: bytes, room_name: str,
             work: Path, sessions: Sequence[tuple[datetime, datetime | None]], store: Store, at: datetime,
             run: Callable[..., Any] = run_tool, python: Path | None = None, keep: bool = True) -> Survival:
    """Whether the fix survived Jim's next save of its room, room_bytes as the room is now. sessions:
    when Mozaik ran at this PC, as (start, end or None while it runs). keep: record what it found in
    the store; the running app judges on a worker with keep=False and records it on the scheduler's
    thread (store.locks.record_survival)."""
    if sha256(room_bytes) == record.result_sha256:
        return Survival("waiting", "The room hasn't been saved since the fix")
    elements = [Element(**e) for e in record.elements]
    tags = ", ".join(dict.fromkeys(fixer.short(e) for e in _in_cab_order(elements)))
    values = []
    for e in elements:
        span = fixer.locate(room_bytes, e)
        values.append(None if span is None else room_bytes[span[0]:span[1]].decode("utf-8", "replace"))
    flags = fixer.flags(snapshot, room_bytes, room_name=room_name, work=work, run=run, python=python)
    if None in values or flags is None:
        found = Survival("could_not_verify", f"Could not verify the fix on {tags} after the save")
    elif all(v == e.after for v, e in zip(values, elements)):
        found = Survival("held", f"The fix on {tags} held after the save") \
            if not fixer.names(flags, elements) else \
            Survival("could_not_verify", f"Could not verify the fix on {tags}: its own check still names it")
    elif all(v == e.before for v, e in zip(values, elements)):
        overlapped = any(start <= record.applied_at and (end is None or end >= record.applied_at)
                         for start, end in sessions)
        found = Survival("mozaik_undid", f"Mozaik undid the fix on {tags}: the save put the old values back",
                         urgent=True, redo=True) if overlapped else \
            Survival("you_changed", f"You changed it in Mozaik: {tags} is back as before the fix")
    else:
        found = Survival("you_changed", f"You changed it in Mozaik: {tags} holds other values now")
    if keep and found.code != "waiting":
        record_survival(store, record.id, code=found.code, words=found.words, at=at)
    return found


# ----- helpers


def _substituted(fixer: Fixer, data: bytes, elements: Sequence[Element]) -> bytes:
    spans = []
    for e in elements:
        span = fixer.locate(data, e)
        if span is None or data[span[0]:span[1]].decode("utf-8", "replace") != e.before:
            raise FixRefused("A value the fix touches isn't where the fixer says, so no fix is planned.")
        spans.append((span, e.after.encode("utf-8")))
    out, at = [], 0
    for (start, end), new in sorted(spans):
        out += [data[at:start], new]
        at = end
    return b"".join([*out, data[at:]])


def _fingerprint(fixer: str, folder: str, room: str, elements: Sequence[Element],
                 flipped: Sequence[str]) -> str:
    body = {"fixer": fixer, "folder": folder, "room": room, "elements": [asdict(e) for e in elements],
            "hand_flipped": list(flipped)}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def _words(fixer: Fixer, room: str, tags: tuple[str, ...], flipped: tuple[str, ...],
           renest: tuple[str, ...], elements: Sequence[Element], base: bytes) -> tuple[str, ...]:
    lines = [fixer.words(room, tags, elements, base)]
    if renest:
        names = ", ".join(plain_material(m) for m in renest)
        lines.append(f"Fix and re-nest {names}: the fix changes parts its nest holds, so it reads NOT"
                     " CHECKED until it is nested again")
    if flipped:
        lines.append(f"{', '.join(flipped)} {'is' if len(flipped) == 1 else 'are'} flipped by hand in the"
                     " nest now; after the fix, nest it again without that flip")
    return tuple(lines)


def _room_path(roots: Roots, folder: str, room: str) -> Path:
    return roots.mozaik_shared / "Jobs" / folder / room


def _note_own(interlock: object, path: Path) -> None:
    note = getattr(interlock, "note_own", None)
    if callable(note):
        note(path)


def _cab_order(cab: str) -> tuple[int, str]:
    return (int(cab), cab) if cab.isdecimal() else (1 << 30, cab)


def _in_cab_order(elements: Sequence[Element]) -> list[Element]:
    return sorted(elements, key=lambda e: _cab_order(e.cab))
