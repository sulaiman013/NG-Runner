"""
The tracker's names for a lock on the 52 real pairs (local only, marked fixtures), against the real tracker
snapshot's Pricing block: NG Runner's own takeoff of each summary, named by RULES' names, and the chat's IC
Conversion only where NG Runner's own takeoff isn't complete. Nothing names a pair: a failure says its index.

Measured 2026-10-31. Before (the strict stopgap, names from the chat's IC Conversion only, on NG Runner's
complete takeoffs): 11 of the 52 could be named, 8 of them with every name in Pricing. After: 31 named, every
name in Pricing exactly, 30 from NG Runner's own takeoff and 1 from the chat's. Of the 21 refused, each says
which name or why: 7 have no whole takeoff on either side, 2 have Egger Classic White (1/4) 5x9 in Pricing
twice (with and without a trailing space, at two prices), 1 each is Arauco, Houghton (RULES' "Winter Fun!"
against the tracker's "Winter Fun") and River Rock in RULES' form, which Pricing spells otherwise, 1 is Blue
Stone, which RULES gives no Tafisa name, and 8 are the chat's own names or tape keys, which Pricing lacks.
A count here is a floor or a measured fact, never a target.
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from ngrunner.guards import headsup
from ngrunner.guards.tracker import read_tracker
from ngrunner.intake import pipeline
from ngrunner.lock.names import CHATS, OURS, LockNames, lock_names
from ngrunner.messages.lint import check_line

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(os.environ.get("NGR_FIXTURES", ROOT / ".local-fixtures"))
PAIRS = FIXTURES / "p2" / "pairs"
SNAPSHOT = FIXTURES / "p2" / "tracker" / "snapshot"

pytestmark = pytest.mark.fixtures


@functools.cache
def _named() -> tuple[LockNames | str, ...]:
    index = PAIRS / "index.json"
    if not index.is_file() or not (SNAPSHOT / "tracker_snapshot_Pricing.csv").is_file():
        pytest.skip("no local pairs or tracker snapshot: run tools/collect_p2_fixtures.py and"
                    " collect_p2_documents.py (AGENTS.md: Fixtures)")
    snap = read_tracker(SNAPSHOT)
    out = []
    for i in range(len(json.loads(index.read_text(encoding="utf-8")))):
        path = PAIRS / str(i) / "summary.pdf"
        made = pipeline._make_takeoff(i, "4242", None, hashlib.sha256(path.read_bytes()).hexdigest(), (path,))
        t = made.takeoff
        record = None if t is None else SimpleNamespace(state="made", complete=t.complete,
                                                        result=pipeline._takeoff_result(t))
        conversion = PAIRS / str(i) / "conversion.json"
        out.append(lock_names(record, conversion.read_bytes() if conversion.is_file() else None, snap))
    return tuple(out)


def test_ng_runners_own_takeoff_names_most_real_locks_and_every_name_is_in_pricing():
    named = _named()
    sources = Counter(found.source for found in named if isinstance(found, LockNames))
    snap = read_tracker(SNAPSHOT)
    sheet_keys = {key.material for key in snap.sheet_prices}
    tape_keys = {key.material for key in snap.tape_prices}

    assert len(named) == 52
    assert sources[OURS] >= 30 and sources[OURS] + sources[CHATS] >= 31
    for i, found in enumerate(named):
        if not isinstance(found, LockNames):
            continue
        for bucket in found.takeoff.buckets:
            names = found.names(bucket.colour, bucket.thk_in)
            assert names is not None and names.sheet in sheet_keys, i
            assert names.tape == "" or names.tape in tape_keys, i


def test_every_refusal_says_which_name_or_why_in_plain_words_and_quotes_no_client():
    for i, found in enumerate(_named()):
        if isinstance(found, LockNames):
            continue
        assert check_line(found) == [], i
        conversion = PAIRS / str(i) / "conversion.json"
        if conversion.is_file():
            parsed = json.loads(conversion.read_bytes().decode("utf-8-sig"))
            assert not [v for v in headsup._client_values(parsed) if v and v.casefold() in found.casefold()], i
