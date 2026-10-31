"""
Scoring the reader against the as-cut truth (design 3.6 "Acceptance is per wall category"; the P2
exit "evaluation report with accept and error rates per category, share of jobs fully built, and
cost"; the P5 exit "one silent error resets the count").

Two questions, kept apart:

  accepted?  gate(): code checks only, no truth, as the night build would run them. A wall is
             declined when the model refused, was cut off or broke the schema, flagged any doubt
             (unsure, null, "unclear", count_unsure, unreadable), or gave what the parts list rules
             out: another number of verticals, another VT RAD count, a height not allowed, the
             heights not equal to the parts list's, verticals not left to right, or a value outside
             the wall. This is the parts-list half of the two-source rule only: the second reading of
             position (elev_panels) is not in this evaluation, so accepted here is an upper bound.
  right?     score_wall(): every value the model gave, against the as-cut .des.
             order   the verticals, in order: right when their number is the truth's
             x, bottom, top   within tol_x (1/8 in) and tol_y (1/4 in), calibrated 2026-10-31 on
                     the first 10 test walls of both models, before any rate decided anything
                     (.local-fixtures/reader/calibration.json): a correct reading came within 0.01 in
                     on x and 0.08 in on top and bottom (both models compute from the printed WIDTH
                     strip and VT heights, not pixels), IC and cut heights differ by at most 0.045
                     in on the 26 test walls, and the smallest real shift seen was a whole run moved
                     1/4 in. The first guesses, 1 in and 1.5 in, would have hidden a 0.6 in drop of
                     a whole wall. Every deviation is kept, so a re-score costs no calls
             height  the VT height it named, within 0.65 in of the cut height
             radius  yes or no, as cut
           With as many verticals as the truth, they pair by index, left to right. With another
           number, an alignment by x (12 in window) finds the missing and the invented ones.

Each value is right, wrong_flagged, wrong_silent or not_given (null, "unclear", "none of these", or
a vertical the model missed: only the order value counts that). A wrong value is flagged when the
model listed that field in the vertical's unsure, or doubted the vertical itself ("exists"); the
order value, and an invented vertical's values, also when it set count_unsure, filled unreadable,
or doubted any vertical's existence. Everything else wrong is silent, and counted apart.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from tools.reader_eval.pairing import HEIGHT_TOL_IN, Pair
from tools.reader_eval.payload import FIELDS, UNREADABLE
from tools.reader_eval.prep import WallCrop

TOL_X_IN = 0.125  # calibrated 2026-10-31 (module notes)
TOL_Y_IN = 0.25
MATCH_WINDOW_IN = 12.0
INSIDE_IN = 1.0  # how far outside the wall a value may be before the gate calls it outside


@dataclass(frozen=True)
class Reading:
    """What a provider returned for one wall."""
    status: str  # answered | refused | truncated | schema_failure | error | not_run
    answer: Mapping[str, Any] | None
    cost_usd: float
    detail: str = ""  # plain words, no names
    cached: bool = False
    latency_s: float | None = None  # seconds from sending to the whole reply
    network: bool = False  # the transport failed: the one case run.py retries, once
    cost_known: bool = True  # False when no usage came back: the guard then charges the worst case


@dataclass(frozen=True)
class Outcome:
    kind: str  # order | x | bottom | top | height | radius
    status: str  # right | wrong_flagged | wrong_silent | not_given
    deviation: float | None = None  # |given - cut| in inches, for x, bottom, top and height


@dataclass(frozen=True)
class WallScore:
    case: str
    wall_id: str
    categories: tuple[str, ...]
    accepted: bool
    decline: str | None
    outcomes: tuple[Outcome, ...]
    cost_usd: float
    latency_s: float | None = None

    @property
    def wrong(self) -> int:
        return sum(o.status.startswith("wrong") for o in self.outcomes)

    @property
    def silent(self) -> int:
        return sum(o.status == "wrong_silent" for o in self.outcomes)


# ----- the gate: what code alone would accept


def _valid(answer: Any) -> bool:
    if not isinstance(answer, Mapping) or set(answer) != {"verticals", "count_unsure", "unreadable"}:
        return False
    if not isinstance(answer["verticals"], list) or not isinstance(answer["count_unsure"], bool):
        return False
    if not isinstance(answer["unreadable"], list) or any(u not in UNREADABLE for u in answer["unreadable"]):
        return False
    for v in answer["verticals"]:
        if not isinstance(v, Mapping) or set(v) != {*FIELDS, "unsure"}:
            return False
        if any(v[k] is not None and not isinstance(v[k], int | float) for k in ("x_in", "bottom_in", "top_in")):
            return False
        if not isinstance(v["height"], str) or v["radius"] not in ("yes", "no", "unclear"):
            return False
        if not isinstance(v["unsure"], list) or any(u not in (*FIELDS, "exists") for u in v["unsure"]):
            return False
    return True


def gate(reading: Reading, crop: WallCrop) -> str | None:
    """None when code would accept the wall; else why it is declined (module notes)."""
    if reading.status != "answered":
        return reading.status
    answer = reading.answer
    if not _valid(answer):
        return "schema_failure"
    assert answer is not None
    vs = answer["verticals"]
    if answer["unreadable"]:
        return "unreadable"
    if answer["count_unsure"]:
        return "count_unsure"
    if any(v["unsure"] or None in (v["x_in"], v["bottom_in"], v["top_in"])
           or v["height"] in ("unclear", "none of these") or v["radius"] == "unclear" for v in vs):
        return "unsure"
    if any(v["height"] not in crop.vt_heights for v in vs):
        return "height_not_allowed"
    if len(vs) != crop.vt_count:
        return "count_differs_from_parts_list"
    if sum(v["radius"] == "yes" for v in vs) != crop.vt_rad_count:
        return "radius_count_differs"
    listed = sorted(float(m["height"]) for p, m in crop.vt_lines for _ in range(p.qty or 0))
    if sorted(float(v["height"]) for v in vs) != listed:
        return "heights_differ_from_parts_list"
    if any(a["x_in"] > b["x_in"] for a, b in zip(vs, vs[1:])):
        return "not_left_to_right"
    width = crop.width_in if crop.width_in is not None else float("inf")
    height = crop.height_in if crop.height_in is not None else float("inf")
    if any(v["x_in"] < -INSIDE_IN or v["x_in"] > width + INSIDE_IN or v["bottom_in"] < -INSIDE_IN
           or v["top_in"] > height + INSIDE_IN or v["top_in"] <= v["bottom_in"] for v in vs):
        return "outside_the_wall"
    return None


# ----- the values against the truth


def _align(truth_x: Sequence[float], given_x: Sequence[float | None]) -> list[tuple[int | None, int | None]]:
    """Order-keeping alignment of truth and given verticals by x; unmatched sides are None."""
    n, m = len(truth_x), len(given_x)
    if n == m:
        return [(i, i) for i in range(n)]

    def gain(i: int, j: int) -> float:
        g = given_x[j]
        if g is None:
            return 0.25
        dx = abs(g - truth_x[i])
        return 1 - dx / (2 * MATCH_WINDOW_IN) if dx <= MATCH_WINDOW_IN else -1.0

    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            options = [best[i + 1][j], best[i][j + 1]]
            if gain(i, j) > 0:
                options.append(gain(i, j) + best[i + 1][j + 1])
            best[i][j] = max(options)
    out: list[tuple[int | None, int | None]] = []
    i = j = 0
    while i < n and j < m:
        if gain(i, j) > 0 and best[i][j] == gain(i, j) + best[i + 1][j + 1]:
            out.append((i, j))
            i, j = i + 1, j + 1
        elif best[i][j] == best[i + 1][j]:
            out.append((i, None))
            i += 1
        else:
            out.append((None, j))
            j += 1
    out += [(k, None) for k in range(i, n)] + [(None, k) for k in range(j, m)]
    return out


def _value(kind: str, given: float | None, cut: float, tol: float, flagged: bool) -> Outcome:
    if given is None:
        return Outcome(kind, "not_given")
    dev = abs(given - cut)
    if dev <= tol:
        return Outcome(kind, "right", dev)
    return Outcome(kind, "wrong_flagged" if flagged else "wrong_silent", dev)


def score_wall(pair: Pair, reading: Reading, *, tol_x: float = TOL_X_IN, tol_y: float = TOL_Y_IN) -> WallScore:
    """Every value the model gave for this wall, against the as-cut panels (module notes)."""
    wall_id = f"{pair.case}/{pair.crop.page}"
    decline = gate(reading, pair.crop)
    if reading.status != "answered" or not _valid(reading.answer):
        return WallScore(pair.case, wall_id, pair.wall.categories, False, decline, (), reading.cost_usd,
                         reading.latency_s)
    assert reading.answer is not None
    given = list(reading.answer["verticals"])
    panels = pair.wall.panels
    doubt_set = (reading.answer["count_unsure"] or bool(reading.answer["unreadable"])
                 or any("exists" in v["unsure"] for v in given))
    outcomes: list[Outcome] = []
    if len(given) == len(panels):
        outcomes.append(Outcome("order", "right"))
    else:
        outcomes.append(Outcome("order", "wrong_flagged" if doubt_set else "wrong_silent"))
    for i, j in _align([p.x_in for p in panels], [v["x_in"] for v in given]):
        if j is None:
            continue  # a vertical the model missed: the order value already counts it
        v = given[j]
        doubt = set(v["unsure"])
        if i is None:  # an invented vertical: each value it gives is wrong
            flagged = "exists" in doubt or doubt_set
            outcomes += [Outcome(k, "not_given" if v[f] is None else ("wrong_flagged" if flagged else "wrong_silent"))
                         for k, f in (("x", "x_in"), ("bottom", "bottom_in"), ("top", "top_in"))]
            continue
        p = panels[i]
        exists = "exists" in doubt
        outcomes += [_value("x", v["x_in"], p.x_in, tol_x, exists or "x_in" in doubt),
                     _value("bottom", v["bottom_in"], p.bottom_in, tol_y, exists or "bottom_in" in doubt),
                     _value("top", v["top_in"], p.top_in, tol_y, exists or "top_in" in doubt)]
        height = v["height"]
        try:
            given_height = float(height)
        except ValueError:
            given_height = None
        outcomes.append(_value("height", given_height, p.height_in, HEIGHT_TOL_IN, exists or "height" in doubt))
        if v["radius"] == "unclear":
            outcomes.append(Outcome("radius", "not_given"))
        elif (v["radius"] == "yes") == p.radius:
            outcomes.append(Outcome("radius", "right"))
        else:
            outcomes.append(Outcome("radius", "wrong_flagged" if exists or "radius" in doubt else "wrong_silent"))
    return WallScore(pair.case, wall_id, pair.wall.categories, decline is None, decline, tuple(outcomes),
                     reading.cost_usd, reading.latency_s)


# ----- the report


@dataclass
class CategoryStats:
    walls: int = 0
    accepted: int = 0
    accepted_wrong: int = 0  # accepted walls with any wrong value: wrong placements
    silent_errors: int = 0
    wrong_values: int = 0

    @property
    def accept_rate(self) -> float | None:
        return self.accepted / self.walls if self.walls else None

    @property
    def error_rate(self) -> float | None:
        return self.accepted_wrong / self.accepted if self.accepted else None


@dataclass
class Report:
    categories: dict[str, CategoryStats]
    pooled: CategoryStats
    silent_errors: int
    wrong_placements: int
    jobs: int
    jobs_fully_built: int
    cost_total: float
    cost_per_wall: float | None
    declines: dict[str, int]
    values: dict[str, dict[str, int]]
    deviations: dict[str, dict[str, float]] = field(default_factory=dict)
    tolerances: dict[str, float] = field(default_factory=dict)
    walls: list[dict[str, Any]] = field(default_factory=list)
    latency: dict[str, float] = field(default_factory=dict)  # seconds: median, max, over calls with one

    def to_json(self) -> str:
        data = asdict(self)
        for name, stats in [*self.categories.items(), ("pooled", self.pooled)]:
            target = data["categories"].get(name) if name != "pooled" else data["pooled"]
            target.update(accept_rate=stats.accept_rate, error_rate=stats.error_rate)
        return json.dumps(data, indent=1)

    def text(self) -> str:
        def pct(v: float | None) -> str:
            return "n/a" if v is None else f"{100 * v:.0f}%"
        lines = [f"Walls scored: {self.pooled.walls}. Accepted by the code checks: {self.pooled.accepted} "
                 f"({pct(self.pooled.accept_rate)}).",
                 f"Wrong placements (accepted walls with a wrong value): {self.wrong_placements}.",
                 f"Silent errors: {self.silent_errors}.",
                 f"Jobs fully built: {self.jobs_fully_built} of {self.jobs}.",
                 f"Cost: ${self.cost_total:.4f} in all, "
                 + ("n/a" if self.cost_per_wall is None else f"${self.cost_per_wall:.4f}") + " per wall.",
                 "Per category (walls, accepted, accept rate, wrong placements, error rate, silent errors):"]
        for name, c in sorted(self.categories.items()):
            lines.append(f"  {name}: {c.walls}, {c.accepted}, {pct(c.accept_rate)}, {c.accepted_wrong}, "
                         f"{pct(c.error_rate)}, {c.silent_errors}")
        lines.append("Declined: " + (", ".join(f"{k} {v}" for k, v in sorted(self.declines.items())) or "none"))
        lines.append("Values (right / wrong flagged / wrong silent / not given):")
        for kind, counts in sorted(self.values.items()):
            lines.append(f"  {kind}: {counts.get('right', 0)} / {counts.get('wrong_flagged', 0)} / "
                         f"{counts.get('wrong_silent', 0)} / {counts.get('not_given', 0)}")
        if self.latency:
            lines.append(f"Latency: median {self.latency['median']:.1f} s, max {self.latency['max']:.1f} s "
                         f"over {self.latency['n']:.0f} calls.")
        for kind, dev in sorted(self.deviations.items()):
            lines.append(f"  {kind} deviation, inches: median {dev['median']:.2f}, max {dev['max']:.2f}")
        return "\n".join(lines)


def summarize(scores: Iterable[WallScore], *, required: Mapping[str, set[str]],
              tolerances: Mapping[str, float] | None = None) -> Report:
    """Pool wall scores into the report. required: per job, every wall id a full build needs."""
    scores = list(scores)
    cats: dict[str, CategoryStats] = defaultdict(CategoryStats)
    pooled = CategoryStats()
    values: dict[str, Counter[str]] = defaultdict(Counter)
    devs: dict[str, list[float]] = defaultdict(list)
    for s in scores:
        for stats in [pooled, *(cats[c] for c in s.categories)]:
            stats.walls += 1
            stats.accepted += s.accepted
            stats.accepted_wrong += s.accepted and s.wrong > 0
            stats.silent_errors += s.silent
            stats.wrong_values += s.wrong
        for o in s.outcomes:
            values[o.kind][o.status] += 1
            if o.deviation is not None:
                devs[o.kind].append(o.deviation)
    good = {s.wall_id for s in scores if s.accepted and s.wrong == 0}
    built = sum(1 for walls in required.values() if walls and walls <= good)
    cost = sum(s.cost_usd for s in scores)
    return Report(dict(cats), pooled, pooled.silent_errors, pooled.accepted_wrong, len(required), built, cost,
                  cost / len(scores) if scores else None,
                  dict(Counter(s.decline for s in scores if s.decline)),
                  {k: dict(v) for k, v in values.items()},
                  {k: {"median": statistics.median(v), "max": max(v)} for k, v in devs.items()},
                  dict(tolerances or {"x": TOL_X_IN, "top_bottom": TOL_Y_IN}),
                  [{"wall": s.wall_id, "categories": list(s.categories), "accepted": s.accepted,
                    "decline": s.decline, "wrong": s.wrong, "silent": s.silent, "cost_usd": s.cost_usd,
                    "latency_s": s.latency_s} for s in scores],
                  ({"median": statistics.median(lat), "max": max(lat), "n": len(lat)}
                   if (lat := [s.latency_s for s in scores if s.latency_s is not None]) else {}))
