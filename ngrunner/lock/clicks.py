"""
Jim's three lock clicks: Lock design, Finish lock and Lock (decision 7c, questionnaire 32; GLOSSARY:
Lock design / Finish lock / Lock), each made of check-before-lock (gate.py), rows built by
tracker_inbox.inbox_rows, and one drop (drop.py).

Why the clicks are split this way (RULES 539-540 against BUILD 334-335, both quoted in decision 7c):
- Lock design adds the job's rows with the takeoff's calculated numbers and Jim's "In progress", before
  any nest: it needs a current check, or Jim's "lock without check".
- Finish lock sets one material's row once that material's nest reads Nest checked: the nest's sheet
  count goes into Optimizer Sheets, which RULES says is never filled from a nest that failed. The last
  material's batch also sets each tape's Optimizer Lin Ft to its calc feet (BUILD: the nest reports no
  tape) and the job's status to Jim's "LOCKED".
- Lock does both when every nest already passed: one batch, adding the rows when the tracker has none
  for the job yet, else setting them.
Only Jim's click starts any of them (decision 8). Each is refused, with what is missing, when its gate
refuses, when the takeoff can't make whole rows, or when the drop's own checks refuse; nothing is
written then. A click that went through is kept (store.locks.add_lock_click), with whether it was a
lock without check.

"Finished" for the last-material rule means a Finish lock batch the snapshots confirmed: no batch is
dropped while another is in flight, so one not confirmed is doubt, which is Jim's to look at.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ngrunner.checks.jobchecks import JobCheck
from ngrunner.clock import Clock
from ngrunner.guards.tracker import TrackerSnapshot, plain_material
from ngrunner.lock.drop import IN_PROGRESS, LOCKED, DropResult, drop_batch
from ngrunner.lock.gate import GateResult, NestLike, lock_gate
from ngrunner.store import Store
from ngrunner.store.locks import add_lock_click, list_batches
from ngrunner.tracker_inbox import InboxRefused, TakeoffFacts, TrackerNames, inbox_rows
from ngrunner.writes import Roots

Names = Callable[[str, float], TrackerNames | None]


@dataclass(frozen=True)
class ClickResult:
    """ok: the click's batch was dropped. gate: what check-before-lock said. drop: the drop, when one
    was tried. words: why not, in plain words, or what was done."""

    ok: bool
    gate: GateResult
    drop: DropResult | None
    words: str


def lock_design(job: str, *, takeoff: TakeoffFacts, names: Names, snap: TrackerSnapshot,
                nests: Sequence[NestLike], check: JobCheck | None, room_sha256: Mapping[str, str],
                room_identity: Mapping[str, str], conversion_sha256: str | None, without_check: bool,
                store: Store, roots: Roots, interlock: Callable[[list[Path]], str | None],
                clock: Clock, source: str | None = None) -> ClickResult:
    """Add the job's calculated rows, In progress. check: the job's check as jobchecks.job_checks()
    gives it; the rooms' and the IC Conversion's hashes are as they are on disk now."""
    gate = lock_gate("lock_design", nests=nests, check=check, room_sha256=room_sha256,
                     room_identity=room_identity, conversion_sha256=conversion_sha256,
                     without_check=without_check)
    return _click("lock_design", job, None, gate,
                  lambda: inbox_rows(job, takeoff, verb="append", names=names, status=IN_PROGRESS),
                  "append", snap, store, roots, interlock, clock, source=source)


def finish_lock(job: str, material: str, *, tracker_name: str, sheets: float, takeoff: TakeoffFacts,
                names: Names, snap: TrackerSnapshot, nests: Sequence[NestLike], check: JobCheck | None,
                room_sha256: Mapping[str, str], room_identity: Mapping[str, str],
                conversion_sha256: str | None, without_check: bool, store: Store, roots: Roots,
                interlock: Callable[[list[Path]], str | None], clock: Clock,
                source: str | None = None) -> ClickResult:
    """Set one material's nest numbers: material is the nest's (its folder's name), tracker_name the
    row's, sheets the nest's sheet count. The last material also locks the job."""
    gate = lock_gate("finish_lock", nests=nests, check=check, room_sha256=room_sha256,
                     room_identity=room_identity, conversion_sha256=conversion_sha256, material=material,
                     without_check=without_check)

    def rows() -> list[list[str]]:
        plain = inbox_rows(job, takeoff, verb="set", names=names, status="")
        materials = [row[2] for row in plain[1:] if row[0] == "SHEETGOODS_SET"]
        if tracker_name not in materials:
            raise InboxRefused(f"The takeoff has no row for {plain_material(tracker_name)}")
        finished = {b.material for b in list_batches(store, folder=job)
                    if b.kind == "finish_lock" and b.state == "confirmed"}
        last = set(materials) - finished <= {tracker_name}
        optimizer = {tracker_name: sheets, **(_tape_calc(plain) if last else {})}
        made = inbox_rows(job, takeoff, verb="set", names=names, status=LOCKED if last else "",
                          optimizer=optimizer)
        return [made[0]] + [row for row in made[1:]
                            if (row[0] == "SHEETGOODS_SET" and row[2] == tracker_name)
                            or (last and row[0] in ("EDGEBAND_SET", "JOBSUMMARY_SET"))]

    return _click("finish_lock", job, material, gate, rows, "set", snap, store, roots, interlock, clock,
                  batch_material=tracker_name, source=source)


def lock(job: str, *, sheets: Mapping[str, float], takeoff: TakeoffFacts, names: Names,
         snap: TrackerSnapshot, nests: Sequence[NestLike], check: JobCheck | None,
         room_sha256: Mapping[str, str], room_identity: Mapping[str, str], conversion_sha256: str | None,
         without_check: bool, store: Store, roots: Roots, interlock: Callable[[list[Path]], str | None],
         clock: Clock, source: str | None = None) -> ClickResult:
    """Lock design and every Finish lock at once: sheets holds each tracker row's nest sheet count."""
    gate = lock_gate("lock", nests=nests, check=check, room_sha256=room_sha256,
                     room_identity=room_identity, conversion_sha256=conversion_sha256,
                     without_check=without_check)
    has_rows = any(row.job.strip() == job.strip() for row in (*snap.sheets, *snap.tape, *snap.jobs))
    verb = "set" if has_rows else "append"

    def rows() -> list[list[str]]:
        plain = inbox_rows(job, takeoff, verb="set", names=names, status="")
        materials = [row[2] for row in plain[1:] if row[0] == "SHEETGOODS_SET"]
        lacking = [m for m in materials if m not in sheets]
        if lacking:
            raise InboxRefused(f"No nest number is known for {plain_material(lacking[0])}")
        return inbox_rows(job, takeoff, verb=verb, names=names, status=LOCKED,
                          optimizer={**{m: sheets[m] for m in materials}, **_tape_calc(plain)})

    return _click("lock", job, None, gate, rows, verb, snap, store, roots, interlock, clock, source=source)


def _tape_calc(set_rows: list[list[str]]) -> dict[str, float]:
    """Each tape's calc feet, read back from the set rows the takeoff makes: the nest reports no tape,
    so Optimizer Lin Ft takes the calc feet (BUILD)."""
    return {row[2]: float(row[3]) for row in set_rows[1:] if row[0] == "EDGEBAND_SET"}


def _click(click: str, job: str, material: str | None, gate: GateResult,
           rows: Callable[[], list[list[str]]], verb: str, snap: TrackerSnapshot, store: Store, roots: Roots,
           interlock: Callable[[list[Path]], str | None], clock: Clock,
           batch_material: str | None = None, source: str | None = None) -> ClickResult:
    if not gate.allowed:
        blocking = [m.words for m in gate.missing if not (gate.without_check and m.waivable)] or \
            [m.words for m in gate.missing]
        return ClickResult(False, gate, None, "; ".join(blocking))
    try:
        made = rows()
    except InboxRefused as exc:
        return ClickResult(False, gate, None, str(exc))
    dropped = drop_batch(job, made, kind=click, verb=verb, material=batch_material, snap=snap, store=store,
                         roots=roots, interlock=interlock, clock=clock, source=source)
    if not dropped.ok:
        return ClickResult(False, gate, dropped, dropped.reason or "The batch was not dropped")
    add_lock_click(store, folder=job, click=click, material=material, without_check=gate.without_check,
                   at=clock.now(), batch_id=dropped.batch.id)
    said = "Lock without check: dropped" if gate.without_check else "Dropped"
    return ClickResult(True, gate, dropped, f"{said} the batch; waiting for the tracker to take it")
