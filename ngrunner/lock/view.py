"""
A job's P4 panel on the board, from the store alone (the job model calls it; nothing here reads a file).

Why it is its own module: the board must say, before Jim clicks, exactly what each of his three lock
buttons is waiting for, and after, where each batch, fix, lease and job sheet stands. The button's words
are the lock gate's own (lock/gate.py, on what the store says now: lock/work.preview), and the click is
held to the same gate again on the files themselves, so the board and the click can't disagree about a
rule, only about what changed in between, which the click then says.

What shows:
- Lock design, then Finish lock for each material with a nest, then Lock, each ready, needing its check
  ("lock without check" may go ahead, with a confirmation that is logged), not yet, or asked;
- the lease, held until when, lapsed or released;
- where a lock's rows come from, when NG Runner's own takeoff isn't complete: the chat's IC Conversion,
  whole (lock/names.py);
- each batch, as the after-drop step last judged it, with [Re-send only the missing row] only where it
  said so; a shadow batch waiting for the chat's own rows, or closed against them: matched, differing, or
  not compared (lock/shadow.py);
- each fix waiting to be written, written (with Undo), undone, or what Jim's next save showed;
- the job sheet at lock: matches, differs (with what), or could not verify.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ngrunner.board.views import (
    BatchView, FixView, JobSheetView, LeaseView, LockButtonView, LockPanelView,
)
from ngrunner.checks.jobchecks import JobCheck
from ngrunner.lock import kept, shadow
from ngrunner.lock.work import folder_of, folders_of, job_nests, preview
from ngrunner.messages import templates
from ngrunner.store import JobRecord, NestRecord, Store, TakeoffRecord, list_nests
from ngrunner.store.locks import BatchRecord, FixRecord, LeaseRecord, list_batches, list_fixes, list_leases


@dataclass(frozen=True)
class P4Facts:
    """What the store holds of P4, read once for every row."""

    batches: tuple[BatchRecord, ...]
    leases: tuple[LeaseRecord, ...]
    fixes: tuple[FixRecord, ...]
    after: Mapping[str, Mapping[str, Any]]
    sheets: Mapping[str, Mapping[str, Any]]
    plans: Mapping[str, Mapping[str, Any]]
    pending: tuple[Mapping[str, Any], ...]
    requests: tuple[Mapping[str, Any], ...]
    nests: tuple[NestRecord, ...] = ()


def read(store: Store) -> P4Facts:
    """One read for every row: the board reads the store a fixed number of times however many jobs."""
    return P4Facts(tuple(list_batches(store)), tuple(list_leases(store)), tuple(list_fixes(store)),
                   kept.all_after(store), kept.all_sheets(store), kept.all_plans(store),
                   tuple(kept.pending(store)), tuple(kept.requests(store)), tuple(list_nests(store)))


def panel(store: Store, facts: P4Facts, job: JobRecord, *, filed: bool, takeoff: TakeoffRecord | None,
          checks: Mapping[str, JobCheck], now: datetime) -> LockPanelView | None:
    """The job's panel, or None for a job not filed with nothing of P4's."""
    names = {name.casefold() for name in folders_of(job)}
    mine = lambda folder: folder.casefold() in names  # noqa: E731 - one test, read four times
    batches = [b for b in facts.batches if mine(b.folder)]
    fixes = [f for f in facts.fixes if mine(f.folder)]
    leases = [lease for lease in facts.leases if mine(lease.folder)]
    waiting = [p for p in facts.pending if mine(p["folder"]) and p["kind"] == "apply"]
    sheet = next((s for folder, s in facts.sheets.items() if mine(folder)), None)
    if not (filed or batches or fixes or leases or waiting or sheet):
        return None
    return LockPanelView(
        buttons=_buttons(store, facts, job, checks, now),
        lease=_lease(leases[0], now) if leases else None,
        batches=tuple(_batch(b, facts) for b in batches),
        fixes=(*(_waiting(p) for p in waiting), *(_fix(f, facts) for f in fixes)),
        sheet=None if sheet is None else JobSheetView(sheet["code"], sheet["words"], tuple(sheet.get("items", ()))),
        note=templates.P4_SHADOW_NOTE, source="" if _complete(takeoff) else templates.LOCK_FROM_THE_CHAT)


def _buttons(store: Store, facts: P4Facts, job: JobRecord,
             checks: Mapping[str, JobCheck], now: datetime) -> tuple[LockButtonView, ...]:
    asked = any(r["kind"] == "click" and r.get("studio_id") == job.studio_id for r in facts.requests)
    nests = job_nests(store, job, facts.nests)
    clicks = [("lock_design", None), *(("finish_lock", n.material) for n in nests), ("lock", None)]
    buttons = []
    for click, material in clicks:
        label = templates.lock_label(click, material)
        if asked:
            buttons.append(LockButtonView(job.studio_id, click, material, label, "asked", (templates.LOCK_BUSY,)))
            continue
        gate = preview(store, job, click, material, now, checks=checks, nests=nests)
        missing = [m.words for m in gate.missing]
        if gate.allowed:
            state = "ready"
        elif all(m.waivable for m in gate.missing):
            state = "waivable"
        else:
            state = "blocked"
        buttons.append(LockButtonView(job.studio_id, click, material, label, state, tuple(missing)))
    return tuple(buttons)


def _complete(takeoff: TakeoffRecord | None) -> bool:
    """NG Runner's own takeoff is complete, so a lock's rows come from it; else from the chat's IC
    Conversion, whole, and the click says so if that gives none (lock/names.py)."""
    return takeoff is not None and takeoff.state == "made" and takeoff.complete


def _lease(record: LeaseRecord, now: datetime) -> LeaseView:
    if record.state == "active":
        state = "active" if now < record.expires_at else "lapsed"
        return LeaseView(state, templates.lease_words("active", at=record.taken_at, until=record.expires_at, now=now))
    return LeaseView("released", templates.lease_words("released", at=record.released_at or record.taken_at,
                                                       until=record.expires_at, now=now))


def _batch(batch: BatchRecord, facts: P4Facts) -> BatchView:
    said = facts.after.get(batch.id) or {}
    label = templates.BATCH_LABELS.get(batch.kind, "Batch")
    if batch.material:
        label = f"{label}, {templates.material_words(batch.material)}"
    waiting = shadow.WAITING if batch.shadow else "Waiting for the tracker to take the batch"
    words = said.get("words") or batch.words or waiting
    asked = any(r["kind"] == "resend" and r.get("batch") == batch.id for r in facts.requests)
    return BatchView(batch.id, label, batch.state, words,
                     can_resend=batch.state == "doubt" and bool(said.get("can_resend")) and not asked)


def _waiting(item: Mapping[str, Any]) -> FixView:
    words = "; ".join(item["plan"].get("words", ())) or "A fix"
    return FixView(None, "waiting", f"{words}. Approved; it is written once the room is clear to write")


def _fix(fix: FixRecord, facts: P4Facts) -> FixView:
    plan = next((p for p in facts.plans.values() if p.get("fingerprint") == fix.fingerprint), None)
    words = "; ".join(plan.get("words", ())) if plan else f"A fix of {fix.room}"
    undoing = any(p["kind"] == "undo" and p.get("fix") == fix.id for p in facts.pending)
    if fix.state == "undone":
        return FixView(fix.id, "undone", words)
    if fix.survival is not None:
        return FixView(fix.id, fix.survival if fix.survival in _SURVIVALS else "could_not_verify",
                       f"{words}. {fix.survival_words or ''}".strip(), can_undo=not undoing)
    return FixView(fix.id, "applied", words, can_undo=not undoing)


_SURVIVALS = ("held", "mozaik_undid", "you_changed", "could_not_verify")


def unverified_fixes(facts: P4Facts) -> list[str]:
    """The heartbeat's open risks from P4 (design 6.5): each fix approved and not written yet, each written
    and not yet judged by the room's next save, and each whose survival could not be verified. A fix that
    held, that Jim changed, or that Mozaik undid (a card of its own) is no open risk, nor one undone."""
    lines = [templates.fix_unverified(p["folder"], room=p["plan"].get("room", ""), state="approved")
             for p in facts.pending if p["kind"] == "apply"]
    for fix in facts.fixes:
        if fix.state != "applied":
            continue
        if fix.survival is None:
            lines.append(templates.fix_unverified(fix.folder, room=fix.room, state="written"))
        elif fix.survival == "could_not_verify":
            lines.append(templates.fix_unverified(fix.folder, room=fix.room, state="could_not_verify"))
    return lines


def doubts(facts: P4Facts) -> Sequence[BatchRecord]:
    """Batches in doubt: tracker problems, for Your list and the digest."""
    return [b for b in facts.batches if b.state == "doubt"]


def folder_label(batch: BatchRecord) -> str:
    return f"{batch.folder}: the {templates.BATCH_LABELS.get(batch.kind, 'lock')} batch is in doubt"


__all__ = ["P4Facts", "doubts", "folder_label", "folder_of", "panel", "read", "unverified_fixes"]
