"""
What a lock's rows call each board and tape on Jim's tracker, and which row a nest's sheets go in.

Why it is its own module, and why it is so strict. tracker_inbox.inbox_rows leaves the naming to its
caller: RULES says every material must match the tracker's Pricing block character for character, and
names colours by their Tafisa name, which NG Runner's takeoff can't give (it knows a colour by its IC
code). Today the chat types each batch by hand from its own IC Conversion JSON, whose buckets carry the
tracker's sheet names and whose edgeband rows carry its tape keys. So the names come from there, and only
where they can't be anyone else's:

- a board takes the conversion's name for its thickness only when that thickness is one bucket in NG
  Runner's takeoff and one named bucket in the conversion;
- its tape takes the conversion's one tape key whose colour is that very name, and that name is its EB
  Colour too. A board with tape and no such key has no name;
- a nest's sheets go in the row of the one bucket of its gauge's thickness (3-4, 1-4, 1 in).

Anything else gives no name, and the lock refuses with what is missing (before_drop then holds each
name to Pricing exactly besides). This is a stopgap of NG Runner's own until Jim's rule for naming
reaches his toolkit: docs/decisions.md is to settle it. Nothing here reads a file.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any

from ngrunner.guards.tracker import split_tape_name
from ngrunner.tracker_inbox import TrackerNames

Names = Callable[[str, float], TrackerNames | None]
NO_NAMES = ("The tracker's name for a board isn't certain from the chat's IC Conversion, so the rows can't be"
            " made")
_GAUGES = (("3-4 ", 0.75), ("1-4 ", 0.25), ("1 in ", 1.0))
_SAME = 0.01                                    # inches: 0.75 and 0.7500001 are one thickness


def takeoff_facts(result: Mapping[str, Any]) -> SimpleNamespace:
    """NG Runner's takeoff as the store keeps it (TakeoffRecord.result), in the shape tracker_inbox reads:
    buckets with attributes, and what it could not place."""
    buckets = tuple(SimpleNamespace(**bucket) for bucket in result.get("buckets") or ())
    return SimpleNamespace(buckets=buckets, unplaced=tuple(result.get("unplaced") or ()))


def tracker_names(conversion: bytes | None, takeoff: Any) -> Names:
    """names(colour, thickness) for takeoff's buckets, from the chat's IC Conversion JSON's bytes."""
    sheets, tapes = _read(conversion)
    ours = [bucket for bucket in takeoff.buckets if _number(getattr(bucket, "thk_in", None)) is not None]

    def names(colour: str, thk_in: float) -> TrackerNames | None:
        mine = [b for b in ours if _same(b.thk_in, thk_in)]
        theirs = [name for name, thickness in sheets if _same(thickness, thk_in)]
        if len(mine) != 1 or mine[0].colour != colour or len(theirs) != 1:
            return None
        sheet = theirs[0]
        if not (_number(getattr(mine[0], "tape_lf", 0)) or 0) > 0:
            return TrackerNames(sheet, "", "")
        own = [tape for tape in tapes if (split_tape_name(tape) or ("",))[0] == sheet]
        return TrackerNames(sheet, own[0], sheet) if len(own) == 1 else None

    return names


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


def _read(conversion: bytes | None) -> tuple[list[tuple[str, float]], list[str]]:
    """The conversion's named buckets as (name, thickness), and its tape keys; nothing when it can't be
    read as the chat writes it."""
    try:
        parsed = json.loads((conversion or b"").decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return [], []
    if not isinstance(parsed, dict) or not isinstance(parsed.get("buckets"), list):
        return [], []
    sheets = [(b["material"].strip(), thickness) for b in parsed["buckets"]
              if isinstance(b, dict) and isinstance(b.get("material"), str) and b["material"].strip()
              and (thickness := _number(b.get("thk_in"))) is not None]
    edgeband = parsed.get("edgeband")
    rows = edgeband.get("rows") if isinstance(edgeband, dict) else None
    tapes = [row["material"] for row in rows or () if isinstance(row, dict) and isinstance(row.get("material"), str)
             and split_tape_name(row["material"]) is not None]
    return sheets, tapes


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _same(a: Any, b: Any) -> bool:
    x, y = _number(a), _number(b)
    return x is not None and y is not None and abs(x - y) < _SAME
