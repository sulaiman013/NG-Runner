"""
What a lock's rows call each board and tape on Jim's tracker, which takeoff the rows are made from, and
which row a nest's sheets go in.

Why it is its own module, and why it is so strict. tracker_inbox.inbox_rows leaves the naming to its
caller: RULES names colours by their Tafisa name, and its HARD PRECONDITION says every material must already
be in the tracker's Pricing block character for character, or the row has no price and the job silently
costs $0. So:

- The first source is NG Runner's own takeoff, when it is complete: each bucket is named by RULES' names
  (convert/tracker_names.py), with the numbers NG Runner's converter made.
- Only when NG Runner's own takeoff isn't complete (a line it could not place, a garage or mixed job, none
  made) do the rows come from the chat's IC Conversion instead, whole: its buckets' numbers under its own
  names, and its own tape keys, as the chat types the tracker's rows today. A board with tape takes the
  conversion's one tape key whose colour is that very name, and that name is its EB Colour too.
- Never a mix of the two (as with the order heads-up): NG Runner's numbers never go under the chat's names,
  and a complete takeoff of NG Runner's that can't be named is refused, never handed to the chat's.
- Whichever the source, every sheet and tape name must be in the snapshot's Pricing block exactly, once,
  with a price, or the lock is refused saying which name. A Pricing block that can't be read is no snapshot
  at all (guards/tracker.read_tracker refuses it whole), so the lock waits for one that reads.

A nest's sheets go in the row of the one bucket of its gauge's thickness (3-4, 1-4, 1 in). Nothing here
reads a file: the caller reads the IC Conversion and the snapshot.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from ngrunner.convert.tracker_names import Unnamed, bucket_names, job_amwh
from ngrunner.guards.tracker import PriceKey, TrackerSnapshot, plain_material, split_tape_name
from ngrunner.tracker_inbox import TrackerNames

Names = Callable[[str, float], TrackerNames | None]
OURS, CHATS = "ng_runner", "chat"
NO_TAKEOFF = ("NG Runner has no complete takeoff of this job, and the chat's IC Conversion lists no whole"
              " takeoff of its own, so its tracker rows can't be made; lock it in chat as before")
NO_NAMES = "The tracker's row for a nest's board isn't certain from the takeoff, so its sheets can't go on it"
_GAUGES = (("3-4 ", 0.75), ("1-4 ", 0.25), ("1 in ", 1.0))
_SAME = 0.01                                    # inches: 0.75 and 0.7500001 are one thickness
_WHO = {OURS: "NG Runner names it", CHATS: "the chat's IC Conversion names it"}


@dataclass(frozen=True)
class LockNames:
    """The takeoff a lock's rows are made from, in the shape tracker_inbox reads, what each of its buckets
    is called on the tracker, and which takeoff it is: NG Runner's own (OURS) or the chat's (CHATS)."""

    takeoff: SimpleNamespace
    names: Names
    source: str


def lock_names(record: Any, conversion: bytes | None, snap: TrackerSnapshot) -> LockNames | str:
    """The rows' source and names for a lock, or why there are none, in plain words. record: NG Runner's
    takeoff of the job as the store keeps it (store.TakeoffRecord), or None; conversion: the chat's IC
    Conversion's bytes, or None; snap: the tracker's snapshot, whose Pricing block every name must be in."""
    if record is not None and record.state == "made" and record.complete and record.result is not None:
        return own_names(record.result, snap)
    return chat_names(conversion, snap)


def own_names(result: Mapping[str, Any], snap: TrackerSnapshot) -> LockNames | str:
    """NG Runner's complete takeoff, each bucket named by RULES' names and held to the Pricing block."""
    takeoff = takeoff_facts(result)
    amwh = job_amwh((b.colour, b.thk_in) for b in takeoff.buckets)
    named: dict[tuple[str, float], TrackerNames] = {}
    for bucket in takeoff.buckets:
        found = bucket_names(bucket.colour, bucket.thk_in, amwh=amwh, tape=(_number(bucket.tape_lf) or 0) > 0)
        if isinstance(found, Unnamed):
            return f"{found.why}, so the rows can't be made"
        named[(bucket.colour, bucket.thk_in)] = TrackerNames(*found)
    return _held(takeoff, named, snap, OURS)


def chat_names(conversion: bytes | None, snap: TrackerSnapshot) -> LockNames | str:
    """The chat's IC Conversion, whole: its buckets under its own names, held to the Pricing block."""
    read = _read(conversion)
    if read is None:
        return NO_TAKEOFF
    buckets, tapes = read
    named: dict[tuple[str, float], TrackerNames] = {}
    for bucket in buckets:
        if bucket.tape_lf <= 0:
            named[(bucket.colour, bucket.thk_in)] = TrackerNames(bucket.colour, "", "")
            continue
        own = [tape for tape in tapes if (split_tape_name(tape) or ("",))[0] == bucket.colour]
        if len(own) != 1:
            return (f"The chat's IC Conversion names no one tape of its own for {plain_material(bucket.colour)},"
                    " so the rows can't be made")
        named[(bucket.colour, bucket.thk_in)] = TrackerNames(bucket.colour, own[0], bucket.colour)
    return _held(SimpleNamespace(buckets=tuple(buckets), unplaced=()), named, snap, CHATS)


def _held(takeoff: SimpleNamespace, named: Mapping[tuple[str, float], TrackerNames], snap: TrackerSnapshot,
          source: str) -> LockNames | str:
    """Every name in the Pricing block, or the first that isn't, said plainly."""
    twice = set(snap.keys_spelled_twice())
    for names in named.values():
        for name, keys in ((names.sheet, snap.sheet_prices), (names.tape, snap.tape_prices)):
            if name:
                problem = priced(name, keys, twice, who=_WHO[source])
                if problem:
                    return f"{problem}, so the rows can't be made"

    def names(colour: str, thk_in: float) -> TrackerNames | None:
        return next((n for (c, t), n in named.items() if c == colour and _same(t, thk_in)), None)

    return LockNames(takeoff, names, source)


def priced(name: str, keys: Sequence[PriceKey], twice: set[str], *, who: str) -> str | None:
    """Why name can't price a row: not in the block exactly, in it twice, or with no price; None when it
    can. RULES' HARD PRECONDITION, as the drop holds it again just before it writes."""
    exact = [key for key in keys if key.material == name]
    shown = _shown(name)
    if name.strip() in twice:
        return f"{shown} is in the tracker's Pricing twice, under two spellings"
    if not exact:
        return f"{shown} isn't in the tracker's Pricing exactly as {who}"
    if exact[0].price is None:
        return f"{shown} has no price in the tracker's Pricing"
    return None


def takeoff_facts(result: Mapping[str, Any]) -> SimpleNamespace:
    """NG Runner's takeoff as the store keeps it (TakeoffRecord.result), in the shape tracker_inbox reads:
    buckets with attributes, and what it could not place."""
    buckets = tuple(SimpleNamespace(**bucket) for bucket in result.get("buckets") or ())
    return SimpleNamespace(buckets=buckets, unplaced=tuple(result.get("unplaced") or ()))


def nest_thickness(material: str) -> float | None:
    """A nest's thickness, from its folder's gauge: 3-4, 1-4 or 1 in. None for any other name."""
    for gauge, inches in _GAUGES:
        if material.startswith(gauge):
            return inches
    return None


def tracker_name_of_nest(material: str, takeoff: Any, names: Names) -> str | None:
    """The tracker row a nest's sheet count goes in: the one bucket of its thickness, by its name."""
    thickness = nest_thickness(material)
    if thickness is None:
        return None
    mine = [b for b in takeoff.buckets if _same(getattr(b, "thk_in", None), thickness)]
    if len(mine) != 1:
        return None
    named = names(mine[0].colour, mine[0].thk_in)
    return None if named is None else named.sheet


def _read(conversion: bytes | None) -> tuple[list[SimpleNamespace], list[str]] | None:
    """The chat's whole takeoff: every bucket with its name, thickness and numbers, and its tape keys;
    None unless every bucket reads, as the chat writes them."""
    try:
        parsed = json.loads((conversion or b"").decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("buckets"), list) or not parsed["buckets"]:
        return None
    buckets = []
    for b in parsed["buckets"]:
        if not isinstance(b, dict) or not isinstance(b.get("material"), str) or not b["material"].strip():
            return None
        numbers = [_number(b.get(key)) for key in ("thk_in", "sqft", "calc_sheets_raw", "edgeband_lf")]
        if any(n is None or n < 0 for n in numbers):
            return None
        thk, sqft, calc, tape = numbers
        buckets.append(SimpleNamespace(colour=b["material"], thk_in=thk, sqft=sqft, calc_sheets_32=calc,
                                       tape_lf=tape))
    edgeband = parsed.get("edgeband")
    rows = edgeband.get("rows") if isinstance(edgeband, dict) else None
    tapes = [row["material"] for row in rows or () if isinstance(row, dict) and isinstance(row.get("material"), str)
             and split_tape_name(row["material"]) is not None]
    return buckets, tapes


def _shown(name: str) -> str:
    """A tracker name as a person reads it, as the drop says it: a tape by its colour and width."""
    tape = split_tape_name(name)
    return f"{plain_material(tape[0])} tape {tape[1]}" if tape else plain_material(name)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _same(a: Any, b: Any) -> bool:
    x, y = _number(a), _number(b)
    return x is not None and y is not None and abs(x - y) < _SAME
