"""
Splitting the reader's wrong values into reading errors and offsets from Jim's build choices.

Why: the truth is the as-cut .des, and where Jim builds by his own rules rather than as drawn, a
perfect reader of the drawing is still "wrong". The first 26 walls showed three such offsets, each
reproduced by a second, independent reader (2026-10-31): the room's top line (RULES: every wall-hung
vertical hangs from one T), where a run starts (a run built tight to a return wall), and the gap of a
stacked tower. A model reading the drawing cannot see them; the P5 builder must apply them as rules.
So each wall's wrong values are explained by rule, never by a model:

  run start    every vertical's x is off by one amount (within tol_x of each other), and the cut run
               is tight to an end of the wall (a vertical within 1/4 in of it)
  stacked gap  the only vertical values off are the uppers of stacked towers (a vertical above
               another at the same x), all by one amount; or, on a wall with a stacked tower, every
               hung vertical is off by one amount (an upper need not stand over a lower: real Job E)
  top line     every hung vertical (cut bottom above 1 in) is off by one amount, top and bottom,
               and the cut ones share one top
  reading error  every other wrong value: a vertical misplaced on its own, a wrong count, height or
               radius, a floor vertical off the floor

A wrong value is silent or flagged as scoring.py decides (the field, or the vertical's existence,
listed as unsure; for the count, count_unsure or unreadable), and the four sums are kept apart, so
silent_offset + silent_reading equals the wall's silent errors in scoring.py.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from tools.reader_eval.pairing import HEIGHT_TOL_IN, Pair
from tools.reader_eval.scoring import TOL_X_IN, TOL_Y_IN, Reading, _valid, score_wall
from tools.reader_eval.truth import MM

HUNG_IN = 1.0
FLUSH_IN = 0.25
STACK_X_IN = 0.5


@dataclass(frozen=True)
class Diagnosis:
    wall_id: str
    kinds: tuple[str, ...]  # "right", "no answer", or what explains its wrong values, sorted
    silent_offset: int = 0
    silent_reading: int = 0
    flagged_offset: int = 0
    flagged_reading: int = 0


def _uniform(devs: list[float], tol: float) -> float | None:
    """The one amount every deviation shares (beyond tol), or None."""
    if not devs:
        return None
    mid = statistics.median(devs)
    return mid if abs(mid) > tol and all(abs(d - mid) <= tol for d in devs) else None


def diagnose(pair: Pair, reading: Reading, *, tol_x: float = TOL_X_IN, tol_y: float = TOL_Y_IN) -> Diagnosis:
    wall_id = f"{pair.case}/{pair.crop.page}"
    if reading.status != "answered" or not _valid(reading.answer):
        return Diagnosis(wall_id, ("no answer",))
    assert reading.answer is not None
    given = list(reading.answer["verticals"])
    panels = pair.wall.panels
    if len(given) != len(panels):  # every wrong value on such a wall is the reader's
        score = score_wall(pair, reading, tol_x=tol_x, tol_y=tol_y)
        silent = sum(o.status == "wrong_silent" for o in score.outcomes)
        flagged = sum(o.status == "wrong_flagged" for o in score.outcomes)
        return Diagnosis(wall_id, ("reading error",), 0, silent, 0, flagged)

    # (vertical, field) -> (deviation, flagged) for every wrong value
    wrong: dict[tuple[int, str], bool] = {}
    devs: dict[str, list[tuple[int, float]]] = {"x_in": [], "bottom_in": [], "top_in": []}
    for i, (v, p) in enumerate(zip(given, panels)):
        doubt = set(v["unsure"])
        exists = "exists" in doubt
        for field, cut, tol in (("x_in", p.x_in, tol_x), ("bottom_in", p.bottom_in, tol_y),
                                ("top_in", p.top_in, tol_y)):
            if v[field] is None:
                continue
            d = v[field] - cut
            devs[field].append((i, d))
            if abs(d) > tol:
                wrong[(i, field)] = exists or field in doubt
        try:
            height = float(v["height"])
        except ValueError:
            height = None
        if height is not None and abs(height - p.height_in) > HEIGHT_TOL_IN:
            wrong[(i, "height")] = exists or "height" in doubt
        if v["radius"] in ("yes", "no") and (v["radius"] == "yes") != p.radius:
            wrong[(i, "radius")] = exists or "radius" in doubt

    explained: dict[tuple[int, str], str] = {}
    length = pair.wall.length_in
    tight = any(p.x_in <= FLUSH_IN or p.x_in + p.width_mm / MM >= length - FLUSH_IN for p in panels)
    if tight and _uniform([d for _, d in devs["x_in"]], tol_x) is not None and len(devs["x_in"]) == len(panels):
        explained.update({k: "run start" for k in wrong if k[1] == "x_in"})

    uppers = {i for i, p in enumerate(panels) for j, q in enumerate(panels)
              if i != j and abs(p.x_in - q.x_in) <= STACK_X_IN and q.top_in <= p.bottom_in + 0.01}
    vertical_wrong = {k for k in wrong if k[1] in ("bottom_in", "top_in")}
    upper_devs = [d for f in ("bottom_in", "top_in") for i, d in devs[f] if i in uppers]
    if (uppers and vertical_wrong and {k[0] for k in vertical_wrong} <= uppers
            and _uniform(upper_devs, tol_y) is not None):
        explained.update({k: "stacked gap" for k in vertical_wrong})
    else:
        hung = {i for i, p in enumerate(panels) if p.bottom_in > HUNG_IN}
        hung_devs = [d for f in ("bottom_in", "top_in") for i, d in devs[f] if i in hung]
        one_top = hung and max(panels[i].top_in for i in hung) - min(panels[i].top_in for i in hung) <= 0.05
        if one_top and _uniform(hung_devs, tol_y) is not None:
            label = "stacked gap" if uppers else "top line"  # a stacked-tower wall's hung verticals sit on its gap
            explained.update({k: label for k in vertical_wrong if k[0] in hung})

    silent_offset = sum(1 for k, f in wrong.items() if k in explained and not f)
    flagged_offset = sum(1 for k, f in wrong.items() if k in explained and f)
    silent_reading = sum(1 for k, f in wrong.items() if k not in explained and not f)
    flagged_reading = sum(1 for k, f in wrong.items() if k not in explained and f)
    kinds = set(explained.values()) | ({"reading error"} if silent_reading + flagged_reading else set())
    return Diagnosis(wall_id, tuple(sorted(kinds)) or ("right",), silent_offset, silent_reading,
                     flagged_offset, flagged_reading)
