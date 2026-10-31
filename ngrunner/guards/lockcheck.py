"""
The lock pre-flight at work: what a pass read, put through guards/preflight.py and kept for the board
and the job's notes file (P1b build spec 5.5). It tells nobody and blocks nothing: Lock stays in chat
in P1b, so this only shows what the tracker and NG Runner hold about a job before he locks.

The labels fallback is read here too, as it needs the parts catalog beside the tracker's snapshots,
and PDFs are slow, so a folder's labels are read only when they or the catalog change. What they show
is kept with what it was read from, and never a design's name.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from ngrunner.clock import to_iso
from ngrunner.guards.kept import (
    LABELS_PREFIX, LOCK_AT, LOCK_PREFIX, ask_again, labels_of, place_key,
)
from ngrunner.guards.labels import LabelsMatch, catalog_pieces, labels_in_design, newest_per_material
from ngrunner.guards.labels import catalog_date as catalog_date_of
from ngrunner.guards.preflight import Preflight, could_not_read_tracker, preflight
from ngrunner.guards.scan import LockFacts, Plan, Scan
from ngrunner.store import (
    clear_state, close_dev, get_state, get_state_time, list_dev, list_nests, open_dev, set_state,
    set_state_time,
)

TRACKER_UNREADABLE = "tracker_unreadable"
_TRACKER_BAD = "guards.tracker_bad_since"
_TRACKER_BAD_AFTER = timedelta(hours=1)


def take_lock_facts(ctx: Any, plan: Plan, found: Scan, now: datetime) -> None:
    lock = found.lock
    if lock is None:
        return
    set_state_time(ctx.store, LOCK_AT, now)
    _note_tracker(ctx.store, lock.problem, now)
    for folder in plan.lock_folders:
        if folder in found.folders:
            _keep(ctx, folder, _labels(ctx, folder, lock), lock, now)


def _labels(ctx: Any, folder: str, lock: LockFacts) -> LabelsMatch | None:
    """What the labels show, read anew only when lock.labels has this folder's files."""
    store = ctx.store
    if folder not in lock.labels:
        return labels_of(store, folder)
    stamp, files = lock.labels[folder]
    before = labels_of(store, folder)
    match = labels_in_design(newest_per_material(files), catalog_pieces(lock.catalog, folder))
    set_state(store, place_key(LABELS_PREFIX, folder), json.dumps({
        "folder": folder, "stamp": stamp, "code": match.code, "missing": list(match.missing),
        "compared": match.compared, "not_compared": match.not_compared}, ensure_ascii=False))
    if before is None or before.code != match.code:
        ask_again(store, folder)
    return match


def _keep(ctx: Any, folder: str, labels: LabelsMatch | None, lock: LockFacts,
          now: datetime) -> None:
    store = ctx.store
    if lock.snapshot is None:
        result = could_not_read_tracker(lock.problem or "", now)
    else:
        result = preflight(folder, lock.snapshot, list_nests(store, folder=folder),
                           lock.checks.get(folder), catalog_date_of(lock.catalog, folder), now=now,
                           labels=labels)
    set_state(store, place_key(LOCK_PREFIX, folder), json.dumps(_as_saved(folder, result, now),
                                                               ensure_ascii=False))


def _as_saved(folder: str, result: Preflight, now: datetime) -> dict[str, Any]:
    return {"folder": folder, "at": to_iso(now),
            "items": [{"code": item.code, "text": item.text} for item in result.items]}


def _note_tracker(store: Any, problem: str | None, now: datetime) -> None:
    """One DEV item once the tracker has stayed unreadable for an hour, closed when it reads."""
    if problem is None:
        if get_state(store, _TRACKER_BAD) is not None:
            clear_state(store, _TRACKER_BAD)
            for item in list_dev(store):
                if item.code == TRACKER_UNREADABLE:
                    close_dev(store, item.id)
        return
    since = get_state_time(store, _TRACKER_BAD)
    if since is None:
        set_state_time(store, _TRACKER_BAD, now)
    elif now - since >= _TRACKER_BAD_AFTER:
        open_dev(store, at=now, code=TRACKER_UNREADABLE, text=problem)
