r"""
One settled list for all jobs: what Jim settled as not a problem for any job, in `NG Jobs\_NG Runner\`, which
Jim's chat reads alongside each job's own notes (design 3.3, "Settled entry", and section 6, "Settle defaults
to this job. 'All jobs' must name a condition. It takes effect at once, is written to the shared settled list
the chat reads, and is folded into RULES and regression in Jim's next chat session, with his accept").

Why a list of its own: a job's notes (<Job>_NGRunner.json) carry its own settles, bound to that job's very
finding, design and IC Conversion, so a real change asks again. A settle for all jobs is bound to none of
those: it stands for every finding of its kind (the checker and what it found) on any job, under the
condition Jim's words give, and the chat must see it before it raises that kind of finding anywhere. The
store keeps it (store.checks, scope all_jobs), jobchecks counts it as settled on every job, each job's notes
say which of its items it settles, and this file is the one list of them all.

Where it is written: through commit_write, as P4's shadow writes are, into NG Runner's own copy of
`NG Jobs\_NG Runner\` on this PC (config.shadow_roots), never the real NG Jobs, until P4 goes live. The
interlock refuses anything below the real folders whatever roots it is handed. It is written when the list
changes, and again whenever the copy is missing or isn't what the store says (keep, each minute), and only
then. What it holds: each settle's checker and what, NG Runner's words for that kind of finding, Jim's
condition and his button's words, when, whether it is active, and whether RULES has absorbed it (never yet:
that is Jim's accept, in his chat). No job name, client or address.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ngrunner.checks.findings import FIX, Item
from ngrunner.clock import to_iso
from ngrunner.messages import templates
from ngrunner.store import SettleRecord, list_settles

_log = logging.getLogger("ngrunner.checks")

FOLDER = "_NG Runner"                   # writes.py's settled folder in NG Jobs
FILE = "NG Runner settled.json"
SCHEMA = 1
ALL_JOBS = "all_jobs"
WHERE = f"NG Jobs\\{FOLDER}\\{FILE}"     # as the chat's notes name it


def all_jobs(settles: Iterable[SettleRecord]) -> list[SettleRecord]:
    """The settles that stand for every job, oldest first, one per kind of finding (the first Jim gave)."""
    seen: dict[tuple[str, str], SettleRecord] = {}
    for settle in settles:
        if settle.scope == ALL_JOBS and settle.checker and settle.what:
            seen.setdefault((settle.checker, settle.what), settle)
    return list(seen.values())


def entry(settle: SettleRecord) -> dict[str, Any]:
    """One settle for all jobs as the list and each job's notes carry it."""
    return {"scope": "all jobs", "checker": settle.checker, "what": settle.what,
            "words": kind_words(settle.checker or "", settle.what or ""), "condition": settle.words,
            "how": settle.how, "said": templates.SETTLE_LABELS.get(settle.how, ""), "at": to_iso(settle.at),
            "active": True, "absorbed_by_rules": False}


def kind_words(checker: str, what: str) -> str:
    """NG Runner's words for a kind of finding, with no job's tags: "The notch is on the wrong face"."""
    return templates.check_item_words(Item(0, FIX, checker, what))


def list_bytes(settles: Iterable[SettleRecord]) -> bytes:
    """The list's file: UTF-8 JSON, the same bytes for the same settles."""
    body = {"schema": SCHEMA, "about": ("What Jim settled as not a problem for any job, each under the condition"
                                        " in his words. Read it alongside a job's own notes."),
            "settled": [entry(s) for s in all_jobs(settles)]}
    return (json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def keep(ctx: Any) -> str | None:
    """Write NG Runner's copy of the list when the store holds a settle for all jobs and the copy is missing
    or isn't what the store says; None when it is current, else why it wasn't written."""
    from ngrunner.config import roots, shadow_roots
    from ngrunner.lock import P4_SHADOW
    from ngrunner.lock.work import _Real            # here: lock.work reads the checks, so not at the top
    from ngrunner.interlock import p4_interlock
    from ngrunner.writes import FileWrite, commit_recorded

    settles = list_settles(ctx.store)
    if not all_jobs(settles):
        return None
    content = list_bytes(settles)
    place = shadow_roots(ctx.paths)
    path = place.ng_jobs / FOLDER / FILE
    try:
        current = _hash(path)
    except OSError:
        return "NG Runner's copy of the settled list could not be read, so it was not written again"
    if current == hashlib.sha256(content).hexdigest():
        return None
    real = getattr(ctx, "roots", None) or roots(ctx.settings, ctx.paths)
    check = p4_interlock(_Real(ctx.store, real, ctx.clock, getattr(ctx.settings, "tracker_folder", None)))
    result = commit_recorded([FileWrite(path, content, current)], store=ctx.store, clock=ctx.clock, roots=place,
                             interlock=check, phase=P4_SHADOW)
    if not result.ok:
        _log.warning("the settled list was not written: %s", result.reason)
        return result.reason or "The settled list was not written"
    return None


def _hash(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None
