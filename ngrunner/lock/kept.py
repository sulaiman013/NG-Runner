"""
What P4 at work keeps in the state table, and what the board reads of it (lock/work.py writes it, on the
scheduler's thread only; lock/view.py and the job model read it).

Why a module of its own: the board must say what each lock click is waiting for, whether a batch may be
re-sent, which fix waits for Jim and which job sheet didn't match, from the store alone, while the work
that decides those runs elsewhere and its reads of Drive run on a worker. The store's own P4 tables
(store/locks.py) hold what was written; this holds what is asked for and what was last seen:

    requests        Jim's clicks waiting for their files to be read: a lock click, a re-send, an Undo
    after.<batch>   what the after-drop step last said of a batch: its state, its words, the rows a
                    snapshot showed missing or different, and whether "Re-send only the missing row"
                    may be offered (drop.AfterDrop.can_resend), with the snapshot it judged by
    plan.<card>     a fix's plan (fixes.FixPlan) as its card asked it, so an approval applies exactly it
    pending         fixes approved and not written yet, and Undos asked for: they wait on the interlock
    sheet.<folder>  the job sheet at lock: matches, mismatch or could not verify, and its items
    planned         which run's findings were planned already, so each is planned once
    acted          the P4 cards whose answer has been acted on

Nothing here reads a file, and nothing here decides anything.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from typing import Any

from ngrunner.clock import from_iso, to_iso
from ngrunner.lock.fixes import Element, FixPlan
from ngrunner.lock.patch import Patch
from ngrunner.store import Db, get_state, set_state, states_under

REQUESTS = "lock.requests"
PENDING = "lock.pending"
PLANNED = "lock.planned"
ACTED = "lock.acted"
WAITING_TOLD = "lock.batch_waiting_told"
_AFTER = "lock.after."
_PLAN = "lock.plan."
_SHEET = "lock.sheet."
_REDO = "lock.redo_of"


# ----- Jim's clicks waiting for their files


def requests(db: Db) -> list[dict[str, Any]]:
    """Oldest first. Each has an id, a kind (click, resend, undo), when it was asked, and its own fields."""
    return list(json.loads(get_state(db, REQUESTS) or "[]"))


def add_request(db: Db, *, kind: str, at: datetime, **fields: Any) -> dict[str, Any]:
    found = requests(db)
    request = {"id": max((r["id"] for r in found), default=0) + 1, "kind": kind, "at": to_iso(at), **fields}
    set_state(db, REQUESTS, json.dumps([*found, request], ensure_ascii=False))
    return request


def drop_request(db: Db, request_id: int) -> None:
    set_state(db, REQUESTS, json.dumps([r for r in requests(db) if r["id"] != request_id], ensure_ascii=False))


# ----- what the after-drop step last said of a batch


def after_of(db: Db, batch_id: str) -> dict[str, Any] | None:
    text = get_state(db, _AFTER + _id_key(batch_id))
    return None if text is None else json.loads(text)


def keep_after(db: Db, batch_id: str, said: Mapping[str, Any]) -> None:
    text = json.dumps({**said, "batch": batch_id}, sort_keys=True, ensure_ascii=False)
    if text != get_state(db, _AFTER + _id_key(batch_id)):
        set_state(db, _AFTER + _id_key(batch_id), text)


def all_after(db: Db) -> dict[str, dict[str, Any]]:
    said = [json.loads(text) for text in states_under(db, _AFTER).values()]
    return {record["batch"]: record for record in said}


# ----- a fix's plan, as its card asked it


def plan_json(plan: FixPlan) -> dict[str, Any]:
    return {"fixer": plan.fixer, "folder": plan.folder, "room": plan.room, "base": plan.patch.base_sha256,
            "result": plan.patch.result_sha256, "patch": plan.patch.as_json(),
            "elements": [asdict(e) for e in plan.elements], "tags": list(plan.tags),
            "hand_flipped": list(plan.hand_flipped), "renest": list(plan.renest),
            "snapshot": plan.snapshot_sha256, "fingerprint": plan.fingerprint, "words": list(plan.words)}


def plan_from(said: Mapping[str, Any]) -> FixPlan:
    return FixPlan(said["fixer"], said["folder"], said["room"],
                   Patch.from_json(said["base"], said["result"], said["patch"]),
                   tuple(Element(**e) for e in said["elements"]), tuple(said["tags"]),
                   tuple(said["hand_flipped"]), tuple(said["renest"]), said["snapshot"], said["fingerprint"],
                   tuple(said["words"]))


def keep_plan(db: Db, card_id: str, plan: FixPlan, *, studio_id: str, redo_of: int | None = None) -> None:
    set_state(db, _PLAN + _id_key(card_id), json.dumps(
        {**plan_json(plan), "card": card_id, "studio_id": studio_id, "redo_of": redo_of}, ensure_ascii=False))


def plan_of(db: Db, card_id: str) -> dict[str, Any] | None:
    text = get_state(db, _PLAN + _id_key(card_id))
    return None if text is None else json.loads(text)


def all_plans(db: Db) -> dict[str, dict[str, Any]]:
    """By card ID, each fix plan a card asked."""
    said = [json.loads(text) for text in states_under(db, _PLAN).values()]
    return {record["card"]: record for record in said}


# ----- fixes approved and not written yet, and Undos asked for


def pending(db: Db) -> list[dict[str, Any]]:
    return list(json.loads(get_state(db, PENDING) or "[]"))


def add_pending(db: Db, *, kind: str, at: datetime, folder: str, **fields: Any) -> dict[str, Any]:
    found = pending(db)
    item = {"id": max((p["id"] for p in found), default=0) + 1, "kind": kind, "since": to_iso(at),
            "folder": folder, **fields}
    set_state(db, PENDING, json.dumps([*found, item], ensure_ascii=False))
    return item


def update_pending(db: Db, pending_id: int, **fields: Any) -> None:
    set_state(db, PENDING, json.dumps([{**p, **fields} if p["id"] == pending_id else p for p in pending(db)],
                                      ensure_ascii=False))


def drop_pending(db: Db, pending_id: int) -> None:
    set_state(db, PENDING, json.dumps([p for p in pending(db) if p["id"] != pending_id], ensure_ascii=False))


def folders_at_work(db: Db) -> set[str]:
    """The job folders a fix or an Undo is waiting on: what a lease is kept for."""
    return {p["folder"] for p in pending(db)}


# ----- the job sheet at lock


def sheet_of(db: Db, folder: str) -> dict[str, Any] | None:
    text = get_state(db, _SHEET + _key(folder))
    return None if text is None else json.loads(text)


def keep_sheet(db: Db, folder: str, said: Mapping[str, Any]) -> None:
    set_state(db, _SHEET + _key(folder), json.dumps({**said, "folder": folder}, ensure_ascii=False))


def all_sheets(db: Db) -> dict[str, dict[str, Any]]:
    said = [json.loads(text) for text in states_under(db, _SHEET).values()]
    return {record["folder"]: record for record in said}


# ----- sets of names, kept once each


def names_in(db: Db, key: str) -> set[str]:
    return set(json.loads(get_state(db, key) or "[]"))


def add_name(db: Db, key: str, name: str) -> None:
    set_state(db, key, json.dumps(sorted(names_in(db, key) | {name})))


# ----- a redo's fix, and what it redoes


def redo_of(db: Db) -> dict[str, int]:
    """By fix id, as text: the fix it redid. A redo is offered once, never on a redo."""
    return dict(json.loads(get_state(db, _REDO) or "{}"))


def note_redo(db: Db, fix_id: int, of: int) -> None:
    set_state(db, _REDO, json.dumps({**redo_of(db), str(fix_id): of}))


def since(item: Mapping[str, Any], key: str = "since") -> datetime:
    return from_iso(item[key])


def _id_key(name: str) -> str:
    """A batch's or a card's ID as a state key (lowercase letters, digits, dots): TES-3 is tes.3."""
    return name.lower().replace("-", ".")


def _key(folder: str) -> str:
    """A folder as a state key: its name can hold anything, and a key can't."""
    return hashlib.sha256(folder.encode("utf-8")).hexdigest()[:16]

