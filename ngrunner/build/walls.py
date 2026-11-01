"""
A room's walls, from the design's WALL/AREA table, as Jim's gen_rooms.py makes them.

Why: BUILD Step 4 (lines 199-221): "Lengths and heights come from the WALL/AREA table — exact, never
from pixels. Only TURN DIRECTIONS come from the plan. Author every wall in the table, at its stated
height." So the table settles each wall's length and height, and the plan settles the rest: the chain's
order (the walls head to tail between the plan's two free ends, gen_rooms.py 41-73, which is not always
the table's: frozen job Z's second room runs 5, 1, 2, 3, 4 and its fourth 4, 1, 2, 3, 5) and each wall's
angle. Nothing in NG Runner reads the plan view yet, so without a Plan every value it settles reads
"could not verify". Ported from Jim's toolkit of 2026-10-31, never copied:

  length, height  the table's inches x 25.4, rounded to 4 places (gen_rooms.py 78)
  IDTag           the wall's number, as WallNumber (gen_rooms.py 87-88, SCHEMA 14-15); a wall named
                  other than by digits could not verify
  the chain       head to tail: each wall starts where the one before it ends, end = start + Len x
                  (cos Ang, sin Ang) (BUILD 204-205, gen_rooms.py 79). The first wall starts at (0, 0), as
                  in frozen jobs X and Z: 13 of the 14 per-job build scripts in his toolkit that write
                  walls never shift the chain, and gen_rooms.py 82-85 and the 14th (frozen job Y's)
                  shift it so no coordinate is negative. His regression compare doesn't compare PosX or
                  PosY (regression.py 201-204), so either passes it
  clockwise       net signed turning negative (RULES 454-457, BUILD 221; regression.py 78-82 is his gate)
  joints          one per neighbouring pair in the chain, Wall1 and Wall2 their IDTags (gen_rooms.py
                  94-96, SCHEMA 20-21; frozen Z's joints name IDTags 5 and 1, not positions)
  H_Walls         the tallest wall's height (gen_rooms.py 111-113)
"""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

from ngrunner.build.placement import MM, Finding
from ngrunner.build.room import Fact, Plan, TableRow, WallSpec, given, unverified

_INCHES = re.compile(r'^\s*(?P<whole>\d+(?:\.\d+)?)(?: (?P<num>\d+)/(?P<den>\d+))?"?\s*$')
NO_PLAN = ("the plan view gives the chain's order and each wall's angle (BUILD 201-202), and nothing in "
           "NG Runner reads the plan yet")
CHAINED = "head to tail from (0, 0): end = start + Len x (cos Ang, sin Ang) (BUILD 204-205)"


def inches(text: str) -> float:
    """A size as the WALL/AREA table prints it ('124 3/8"', '98"') in inches; ValueError otherwise."""
    m = _INCHES.match(text)
    if m is None or (m["den"] is not None and int(m["den"]) == 0):
        raise ValueError(f"not a size in inches: {text!r}")
    return float(m["whole"]) + (int(m["num"]) / int(m["den"]) if m["den"] else 0.0)


def table_rows(walls: Iterable[Mapping[str, str]]) -> tuple[TableRow, ...]:
    """A design's walls as NG Runner's summary check reads them (its details' "walls": name, width,
    height, as printed) in table order."""
    return tuple(TableRow(w["name"], inches(w["width"]), inches(w["height"])) for w in walls)


def net_turn(angles: Sequence[float]) -> float:
    """The chain's net signed turning, degrees: negative is clockwise (regression.py 80)."""
    return sum((b - a + 180) % 360 - 180 for a, b in zip(angles, angles[1:]))


def chain(lengths_mm: Sequence[float], angles: Sequence[float]) -> list[tuple[float, float]]:
    """Each wall's start, head to tail from (0, 0) (BUILD 204-205)."""
    starts: list[tuple[float, float]] = []
    x = y = 0.0
    for length, angle in zip(lengths_mm, angles):
        starts.append((round(x, 4) + 0.0, round(y, 4) + 0.0))  # + 0.0: no -0.0
        x += length * math.cos(math.radians(angle))
        y += length * math.sin(math.radians(angle))
    return starts


def _plan_problem(rows: Sequence[TableRow], plan: Plan) -> str | None:
    names = [r.name for r in rows]
    if len(plan.angles) != len(plan.order):
        return "the plan gives a different number of angles and walls"
    if Counter(plan.order) != Counter(names):
        return "the plan's walls are not the table's walls, each once (BUILD 201-202: author every wall in the table)"
    if len(plan.order) > 1 and net_turn(plan.angles) >= 0:
        return (f"the plan's walls turn {net_turn(plan.angles):g} degrees in all, not clockwise (RULES 454-457: "
                "net signed turning negative)")
    return None


def build_walls(rows: Sequence[TableRow], plan: Plan | None = None
                ) -> tuple[tuple[WallSpec, ...], Fact[tuple[tuple[int, int], ...]], Fact[float], tuple[Finding, ...]]:
    """The room's walls (in chain order with a usable plan, else the table's), its joints, its H_Walls,
    and what the chain's gates found."""
    findings: list[Finding] = []
    twice = sorted(n for n, k in Counter(r.name for r in rows).items() if k > 1)
    if twice:
        problem: str | None = (f"the WALL/AREA table names wall {', '.join(twice)} more than once, so no plan "
                               "can be laid on it")
    else:
        problem = NO_PLAN if plan is None else _plan_problem(rows, plan)
    if problem is not None and problem != NO_PLAN:
        findings.append(Finding("chain", "could not verify", None, problem))
    by_name = {r.name: r for r in rows}
    if plan is not None and problem is None:
        order = [by_name[n] for n in plan.order]
        angles: list[float] | None = list(plan.angles)
        starts = chain([round(r.width_in * MM, 4) for r in order], plan.angles)
    else:
        order, angles, starts = list(rows), None, []
    walls: list[WallSpec] = []
    for k, row in enumerate(order):
        id_tag: Fact[int] = (given(int(row.name), "rule", "the wall's number (gen_rooms.py 87, SCHEMA 14)")
                             if row.name.isdigit() else
                             unverified(f"wall {row.name!r} is not named by a number, which its IDTag must be"))
        if angles is None:
            why = problem or NO_PLAN
            walls.append(WallSpec(row.name, given(round(row.width_in * MM, 4), "table"),
                                  given(round(row.height_in * MM, 4), "table"), unverified(why), id_tag,
                                  unverified(why), unverified(why)))
            continue
        walls.append(WallSpec(
            row.name, given(round(row.width_in * MM, 4), "table"), given(round(row.height_in * MM, 4), "table"),
            given(k + 1, "plan"), id_tag, given(angles[k], "plan"),
            given(starts[k], "rule", CHAINED)))
    if angles is None:
        joints: Fact[tuple[tuple[int, int], ...]] = unverified(problem or NO_PLAN)
    elif any(w.id_tag.value is None for w in walls):
        joints = unverified("a wall in the chain has no number for its IDTag")
    else:
        tags = [w.id_tag.value for w in walls if w.id_tag.value is not None]
        joints = given(tuple(zip(tags, tags[1:])), "rule", "one per neighbouring pair (gen_rooms.py 94-96)")
    height: Fact[float] = (given(max(round(r.height_in * MM, 4) for r in rows), "rule",
                                 "the tallest wall (gen_rooms.py 111-113)")
                           if rows else unverified("the WALL/AREA table has no walls"))
    return tuple(walls), joints, height, tuple(findings)
