"""
Telling Barbara and Will what a job's takeoff says to order (P1b build spec 5.8; rulings 12a-c).

Why it exists apart: what a heads-up says is guards/headsup.py's, and this is only when it goes. One
message per content of a job's IC Conversion JSON, through the heads_ups table, so a restart or a
re-sync sends nothing and a real change says "revised". A takeoff that won't read sends nothing,
raises one DEV item and leaves a note on the board: never a partial list.

Nothing reaches Barbara or Will by surprise: order_flags_on stays off until Sulaiman turns it on. A
takeoff that arrives while it is off is noted as seen and not sent, so turning it on doesn't send
every job at once. A shadow run tells Sulaiman what would have gone, as it does with everything else,
and one copy is enough. What was there at install was only listed (watch.py), so it sends nothing.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ngrunner.guards import headsup
from ngrunner.guards.kept import TAKEOFFS
from ngrunner.guards.scan import Plan, Scan, TakeoffRead, stat_words
from ngrunner.intake.pipeline import queue_tell
from ngrunner.store import (
    close_dev, get_heads_up, list_dev, open_dev, record_heads_up, set_state,
)

UNREADABLE_CODE = "takeoff_unreadable"
_LIVE_RECIPIENTS = ("barbara", "will")
_SHADOW_RECIPIENTS = ("barbara",)      # the messenger sends it to Sulaiman, marked


def take_takeoffs(ctx: Any, plan: Plan, found: Scan, now: datetime) -> None:
    said = {folder: dict(values) for folder, values in plan.takeoffs.items()}
    for folder, listed in found.takeoffs_listed.items():
        said.setdefault(folder, {})["seen"] = stat_words(listed)
    for read in found.takeoffs:
        said.setdefault(read.folder, {})["read"] = stat_words(read.listed)
        said[read.folder]["bad"] = read.takeoff is None
        _take_one(ctx, read, now)
    if said != plan.takeoffs:
        set_state(ctx.store, TAKEOFFS, json.dumps(said, ensure_ascii=False))


def _take_one(ctx: Any, read: TakeoffRead, now: datetime) -> None:
    store, folder = ctx.store, read.folder
    before = get_heads_up(store, folder)
    if before is not None and before.json_sha256 == read.sha256:
        return                                       # the same content, synced or touched again
    went = None if before is None else before.sent_at    # so the next one still says "revised"
    with store.transaction() as tx:
        if read.takeoff is None:
            record_heads_up(tx, folder=folder, json_sha256=read.sha256, sent_at=went)
            line = headsup.unreadable_dev(folder, at=now)
            open_dev(tx, at=now, code=line.code, text=f"{folder}: {line.text}.")
            return
        _close_unreadable(tx, folder)
        recipients = _recipients(ctx)
        if not recipients:
            record_heads_up(tx, folder=folder, json_sha256=read.sha256, sent_at=went)
            return
        flags = headsup.order_flags(read.takeoff, total_line=ctx.settings.order_flag_total)
        message = headsup.heads_up(folder, read.takeoff, flags, revised=went is not None)
        for to in recipients:
            queue_tell(tx, message, to=to, now=now, ref=f"heads_up:{read.sha256[:24]}:{to}")
        record_heads_up(tx, folder=folder, json_sha256=read.sha256, sent_at=now)


def _recipients(ctx: Any) -> tuple[str, ...]:
    if ctx.mode == "shadow":
        return _SHADOW_RECIPIENTS
    return _LIVE_RECIPIENTS if ctx.settings.order_flags_on else ()


def _close_unreadable(db: Any, folder: str) -> None:
    for item in list_dev(db):
        if item.code == UNREADABLE_CODE and item.text.startswith(f"{folder}:"):
            close_dev(db, item.id)
