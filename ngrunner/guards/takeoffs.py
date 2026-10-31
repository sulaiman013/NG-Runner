"""
Telling Barbara and Will what a job's takeoff says to order (P1b build spec 5.8; rulings 12a-c),
from NG Runner's own takeoff on intake when it has one (P3's "takeoff on intake").

Why it exists apart: what a heads-up says is guards/headsup.py's and messages/templates.py's, and
this is only which takeoff it comes from and when it goes. The source, per job folder:
- NG Runner's complete takeoff of the summary the job is filed on now (pipeline.job_takeoffs). While
  it is the source, the chat's JSON is only noted as read, so a change to it sends nothing.
- When NG Runner's takeoff isn't complete, the chat's IC Conversion JSON if it reads, whole, saying
  it is the chat's and how many lines NG Runner's could not verify (Sulaiman's ruling, 2026-10-31:
  that list is what Barbara and Will order from today). Only when neither gives a whole list does
  the heads-up say "could not verify the takeoff", and how many lines. Never a partial list, never
  a mix of the two, and never a line's own text, which can quote anything.
- With no takeoff of NG Runner's, the chat's JSON, as in P1b: a garage or mixed job stays in chat,
  and so does a summary NG Runner couldn't read, one replaced by hand, and a job filed before the
  takeoff on intake.
A chat JSON that won't read sends nothing of its own, raises one DEV item and leaves a note on the
board. A heads-up from the chat's JSON says so on its first line, and the guards' takeoffs state
keeps "source": "chat" for a folder whose NG Runner takeoff it stands in for, which the board shows.
A JSON is read on a worker, and only when its listing changes, so one needed again (NG Runner's
takeoff of a new summary isn't complete) is asked for again, and read at the next pass.

One message per content, through the heads_ups table: the chat's by the JSON's hash, NG Runner's by
a hash of what its message says, so a restart, a re-sync or a summary whose takeoff reads the same
sends nothing, and a real change sends one "revised", whichever source sent the one before.

Nothing reaches Barbara or Will by surprise: order_flags_on stays off until Sulaiman turns it on. A
takeoff that arrives while it is off is noted as seen and not sent, so turning it on doesn't send
every job at once. A shadow run tells Sulaiman what would have gone, as it does with everything else,
and one copy is enough. What was there at install was only listed (watch.py), so it sends nothing.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from ngrunner.convert import rules
from ngrunner.guards import headsup
from ngrunner.guards.kept import TAKEOFFS
from ngrunner.guards.scan import Plan, Scan, TakeoffRead, stat_words
from ngrunner.intake.pipeline import all_filed_facts, job_takeoffs, queue_tell
from ngrunner.messages import Message, templates
from ngrunner.store import (
    TakeoffRecord, close_dev, get_heads_up, list_dev, open_dev, record_heads_up, set_state,
)

UNREADABLE_CODE = "takeoff_unreadable"
_LIVE_RECIPIENTS = ("barbara", "will")
_SHADOW_RECIPIENTS = ("barbara",)      # the messenger sends it to Sulaiman, marked
_WASTE_PERCENT = round((rules.WASTE - 1.0) * 100)


def take_takeoffs(ctx: Any, plan: Plan, found: Scan, now: datetime) -> None:
    own = own_takeoffs(ctx.store)
    said = {folder: dict(values) for folder, values in plan.takeoffs.items()}
    for folder, listed in found.takeoffs_listed.items():
        said.setdefault(folder, {})["seen"] = stat_words(listed)
    reads: dict[str, TakeoffRead] = {}
    for read in found.takeoffs:
        kept = said.setdefault(read.folder, {})
        kept["read"], kept["sha"] = stat_words(read.listed), read.sha256
        if read.folder.casefold() in own:
            reads[read.folder.casefold()] = read        # NG Runner's takeoff decides, below
        else:
            kept["bad"] = read.takeoff is None
            _take_one(ctx, read, now)
    for key, (folder, record) in own.items():
        kept = _kept(said, folder)
        if record.complete:
            if kept is not None:
                kept["bad"] = False                     # NG Runner's own takeoff is the source
                kept.pop("source", None)
            _take_own(ctx, folder, record, now)
        else:
            _take_incomplete(ctx, folder, record, reads.get(key), kept, now)
    if said != plan.takeoffs:
        set_state(ctx.store, TAKEOFFS, json.dumps(said, ensure_ascii=False))


def own_takeoffs(db: Any) -> dict[str, tuple[str, TakeoffRecord]]:
    """By folder (casefolded): the folder and NG Runner's takeoff of the summary filed there now,
    for each job where one was made. Every other job's source is the chat's JSON."""
    filed = all_filed_facts(db)
    return {filed[sid]["folder"].casefold(): (filed[sid]["folder"], record)
            for sid, record in job_takeoffs(db, filed).items() if record.state == "made"}


def _kept(said: dict[str, dict[str, Any]], folder: str) -> dict[str, Any] | None:
    """What the guards keep of this folder's chat JSON, whatever case its name is listed in."""
    return next((values for name, values in said.items() if name.casefold() == folder.casefold()),
                None)


# ----- the chat's JSON, as in P1b, and as the fallback


def _take_one(ctx: Any, read: TakeoffRead, now: datetime, *, folder: str | None = None,
              unverified: int | None = None) -> None:
    """The chat's list, once per content. unverified: it stands in for NG Runner's takeoff, which
    could not verify that many lines; folder: that job's folder, so both sources share one row."""
    store, folder = ctx.store, folder or read.folder
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
        takeoff = read.takeoff
        flags = headsup.order_flags(takeoff, total_line=ctx.settings.order_flag_total)
        _send(ctx, tx, folder, read.sha256, went, now,
              lambda revised: templates.from_the_chats_takeoff(
                  headsup.heads_up(folder, takeoff, flags, revised=revised),
                  could_not_verify=unverified))


def _take_incomplete(ctx: Any, folder: str, record: TakeoffRecord, read: TakeoffRead | None,
                     kept: dict[str, Any] | None, now: datetime) -> None:
    """NG Runner's takeoff isn't complete: the chat's whole list when its JSON reads, else "could
    not verify the takeoff". A JSON not read this pass still stands in when its list is the one
    last told; one that went unsent while NG Runner's was complete is asked for again."""
    if read is not None and kept is not None:
        kept["bad"] = read.takeoff is None
        if read.takeoff is not None:
            kept["source"] = "chat"
            _take_one(ctx, read, now, folder=folder, unverified=record.could_not_verify)
            return
        line = headsup.unreadable_dev(folder, at=now)
        open_dev(ctx.store, at=now, code=line.code, text=f"{folder}: {line.text}.")
    elif kept is not None and kept.get("read") and not kept.get("bad"):
        before = get_heads_up(ctx.store, folder)
        if kept.get("sha") and before is not None and before.json_sha256 == kept["sha"]:
            kept["source"] = "chat"                     # its list is the one last told
            return
        kept.pop("read")                                # read it again at the next pass
        kept.pop("source", None)
        return
    if kept is not None:
        kept.pop("source", None)
    _take_own(ctx, folder, record, now, close_unreadable=False)


# ----- NG Runner's own takeoff


def _take_own(ctx: Any, folder: str, record: TakeoffRecord, now: datetime, *,
              close_unreadable: bool = True) -> None:
    """NG Runner's own: its list, or "could not verify the takeoff". A chat JSON that won't read
    says nothing more once NG Runner's complete takeoff is the source, so its DEV item closes."""
    store = ctx.store
    content = _content_sha(record)
    before = get_heads_up(store, folder)
    if before is not None and before.json_sha256 == content:
        return
    went = None if before is None else before.sent_at
    with store.transaction() as tx:
        if close_unreadable:
            _close_unreadable(tx, folder)
        _send(ctx, tx, folder, content, went, now,
              lambda revised: _own_message(ctx, folder, record, revised=revised))


def _own_message(ctx: Any, folder: str, record: TakeoffRecord, *, revised: bool) -> Message:
    """The list when the takeoff is complete; otherwise how many lines could not be verified."""
    result = record.result or {}
    buckets: Sequence[Mapping[str, Any]] = result.get("buckets", ())
    if not record.complete:
        return templates.takeoff_unverified(folder, lines=record.could_not_verify, revised=revised)
    flags = headsup.order_flags(_for_flags(buckets, result.get("estimate_total")),
                                total_line=ctx.settings.order_flag_total)
    return templates.takeoff_heads_up(folder, buckets=buckets, flags=flags,
                                      waste_percent=_WASTE_PERCENT, revised=revised)


def _for_flags(buckets: Sequence[Mapping[str, Any]], total: float | None) -> headsup.Takeoff:
    """NG Runner's takeoff as P1b's order_flags reads one, so a flag says what P1b's says. There is
    no IC colour text to read a swap from: the takeoff names its colours by IC code."""
    thick = templates.thick_white(buckets)
    sheets = tuple(headsup.SheetLine(templates.takeoff_material(b["colour"], thick_white=thick),
                                     b["thk_in"], b["sheets_with_waste"], b["calc_sheets_32"])
                   for b in buckets)
    return headsup.Takeoff(sheets, (), total, "", _WASTE_PERCENT)


def _content_sha(record: TakeoffRecord) -> str:
    """What NG Runner's heads-up would say, hashed: so a summary whose takeoff reads the same, from
    another render or another rules version, sends nothing."""
    result = record.result or {}
    said: dict[str, Any] = {"source": "ngrunner", "complete": record.complete,
                            "could_not_verify": record.could_not_verify}
    if record.complete:
        said["buckets"] = [[b["colour"], b["thk_in"], b["sheets_with_waste"], b["tape_lf"]]
                           for b in result.get("buckets", ())]
        said["estimate_total"] = result.get("estimate_total")
    return hashlib.sha256(json.dumps(said, sort_keys=True).encode("utf-8")).hexdigest()


# ----- either source


def _send(ctx: Any, tx: Any, folder: str, content: str, went: datetime | None, now: datetime,
          message_of: Callable[[bool], Message]) -> None:
    """Queue the message to whoever hears in this mode, and note what it went out on. With nobody
    to tell, it is noted as seen, keeping when the last one went, so the next still says revised."""
    recipients = _recipients(ctx)
    if not recipients:
        record_heads_up(tx, folder=folder, json_sha256=content, sent_at=went)
        return
    message = message_of(went is not None)
    for to in recipients:
        queue_tell(tx, message, to=to, now=now, ref=f"heads_up:{content[:24]}:{to}")
    record_heads_up(tx, folder=folder, json_sha256=content, sent_at=now)


def _recipients(ctx: Any) -> tuple[str, ...]:
    if ctx.mode == "shadow":
        return _SHADOW_RECIPIENTS
    return _LIVE_RECIPIENTS if ctx.settings.order_flags_on else ()


def _close_unreadable(db: Any, folder: str) -> None:
    for item in list_dev(db):
        if item.code == UNREADABLE_CODE and item.text.startswith(f"{folder}:"):
            close_dev(db, item.id)
