"""
Tuning GPT-6 Luna as P5's reader, honestly (Sulaiman, 2026-10-31).

Why: Luna costs about a tenth of Claude per wall, so if it can read as safely, P5 is far cheaper.
The test stays honest by keeping two sets apart:
  DEV   the first 26 test walls (Jobs A to J) and the frozen jobs' walls (X, Y, Z): every change is
        tried and measured here, one at a time, and kept only when it lowers DEV's silent reading
        errors (diagnose.py: build-choice offsets left out)
  TEST  the P5 jobs' walls: read once, at the end, with nothing changed after

The changes (each a Variant switch):
  worked   8 to 12 worked walls, chosen where Luna fails (stacked tower, radius, bridge, flush end
           first), from the frozen jobs and DEV jobs only, never the target's own job, never TEST,
           each with a one-line note in Jim's terms on how it was read (note_for)
  rules    Jim's rules from RULES and BUILD in the instructions, and what not to infer
           (payload.instructions)
  tiles    the WIDTH/DEPTH strip and each vertical's area as enlarged tiles (design 3.6; prep.tiles),
           each a part of the contained crop
  reads    the same wall read 3 times; a value the reads disagree on becomes unsure (agree)
  checks   code checks before acceptance: counts and heights against the parts list, verticals that
           overlap, and spacing against the WIDTH strip the model reads into "bays" (check)

Every change can only add doubt or information; agree() and check() never take doubt away, so a
wrong value can turn from silent to flagged, never the other way.
"""
from __future__ import annotations

import copy
import hashlib
import json
import statistics
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tools.reader_eval.pairing import Pair
from tools.reader_eval.payload import FIELDS
from tools.reader_eval.prep import WallCrop
from tools.reader_eval.scoring import TOL_X_IN, TOL_Y_IN, Reading, _valid

PRIORITY = ("stacked tower", "radius", "bridge", "flush end", "REA", "hutch", "plain", "no verticals")
PANEL_IN = 0.75
STACK_X_IN = 0.5


# ----- worked walls


def choose_worked(pool: Sequence[Pair], *, target_case: str, n: int) -> list[Pair]:
    """n worked walls from pool, none from the target's job, covering the failing categories first."""
    if any(p.case.startswith("P") for p in pool):
        raise ValueError("a TEST job's wall can never be a worked wall")
    candidates = [p for p in pool if p.case != target_case and p.wall.panels]
    chosen: list[Pair] = []
    shown: set[str] = set()
    while candidates and len(chosen) < n:
        def rank(p: Pair) -> tuple[int, int, int]:
            new = [PRIORITY.index(c) for c in p.wall.categories if c not in shown and c in PRIORITY]
            return (min(new) if new else len(PRIORITY), -len(p.wall.panels), candidates.index(p))
        best = min(candidates, key=rank)
        chosen.append(best)
        shown |= set(best.wall.categories)
        candidates.remove(best)
    return chosen


def _n(value: float) -> str:
    return f"{round(value, 2):g}"


def note_for(pair: Pair) -> str:
    """One line on how the worked wall reads, in Jim's terms: numbers and fixed words only."""
    ps = pair.wall.panels
    if not ps:
        return "No verticals on this wall: report none."
    parts = [f"{len(ps)} verticals, left to right, at x {', '.join(_n(p.x_in) for p in ps)};"
             " each x is every bay's WIDTH before it plus 3/4 for each vertical before it."]
    if any(p.x_in <= 0.25 for p in ps):
        parts.append("The first vertical stands tight to the wall at x 0.")
    floor = [p for p in ps if p.bottom_in <= 1.0]
    hung = [p for p in ps if p.bottom_in > 1.0]
    if floor:
        parts.append(f"Floor verticals stand on 0 (tops {', '.join(sorted({_n(p.top_in) for p in floor}))}).")
    if hung:
        spans = sorted({(_n(p.bottom_in), _n(p.top_in)) for p in hung})
        parts.append("Wall-hung verticals hang " + "; ".join(f"from {b} to {t}" for b, t in spans) + ".")
    for p in ps:
        if p.radius:
            parts.append(f"The VT RAD is the one at x {_n(p.x_in)}: its front edge is drawn clipped at both ends.")
    stacked = sorted({_n(p.x_in) for i, p in enumerate(ps) for q in ps[i + 1:]
                      if abs(p.x_in - q.x_in) <= STACK_X_IN and (p.top_in <= q.bottom_in or q.top_in <= p.bottom_in)})
    if stacked:
        parts.append(f"A stacked tower at x {', '.join(stacked)}: the lower vertical first.")
    return " ".join(parts)


# ----- three reads that must agree


def strip_bays(answer: dict[str, Any]) -> dict[str, Any]:
    """The answer in payload.SCHEMA's shape: the bays are for the checks only."""
    return {k: v for k, v in answer.items() if k != "bays"}


def _ok(answer: Any) -> bool:
    return isinstance(answer, dict) and _valid(strip_bays(answer))


def agree(answers: Sequence[dict[str, Any] | None], *, tol_x: float = TOL_X_IN,
          tol_y: float = TOL_Y_IN) -> dict[str, Any]:
    """
    One answer from several reads of the same wall: each value their median when every read agrees
    within tolerance, else listed as unsure. A read that failed, or reads that disagree on how many
    verticals there are, doubt the count.
    """
    valid = [a for a in answers if _ok(a)]
    if not valid:
        return {"verticals": [], "count_unsure": True, "unreadable": ["other"]}
    out = copy.deepcopy(valid[0])
    out["count_unsure"] = any(a["count_unsure"] for a in valid)
    out["unreadable"] = sorted({u for a in valid for u in a["unreadable"]})
    if len(valid) < len(answers) or len({len(a["verticals"]) for a in valid}) > 1:
        out["count_unsure"] = True
        return out
    for i, v in enumerate(out["verticals"]):
        reads = [a["verticals"][i] for a in valid]
        unsure = {u for r in reads for u in r["unsure"]}
        for field, tol in (("x_in", tol_x), ("bottom_in", tol_y), ("top_in", tol_y)):
            nums = [r[field] for r in reads if r[field] is not None]
            if len(nums) < len(reads):
                unsure.add(field)
            if nums:
                v[field] = statistics.median(nums)
                if max(nums) - min(nums) > tol:
                    unsure.add(field)
        for field in ("height", "radius"):
            if len({r[field] for r in reads}) > 1:
                unsure.add(field)
        v["unsure"] = [f for f in (*FIELDS, "exists") if f in unsure]
    if all(isinstance(a.get("bays"), list) for a in valid) and len({len(a["bays"]) for a in valid}) == 1:
        out["bays"] = [statistics.median(xs) if all(x is not None for x in xs) else None
                       for xs in zip(*(a["bays"] for a in valid))]
    return out


# ----- code checks before acceptance


def _doubt(v: dict[str, Any], field: str) -> None:
    if field not in v["unsure"]:
        v["unsure"] = [f for f in (*FIELDS, "exists") if f in {*v["unsure"], field}]


def check(answer: dict[str, Any], crop: WallCrop, *, tol_x: float = TOL_X_IN) -> dict[str, Any]:
    """
    The answer with doubt added wherever code finds it inconsistent; values are never changed and
    doubt is never removed. Checks: the count against the parts list, heights the parts list
    allows, verticals that overlap (not a stacked tower), and the spacing between verticals against
    the WIDTH strip's bays the model read (when it gave them).
    """
    out = copy.deepcopy(answer)
    vs = out["verticals"]
    if crop.vt_count is not None and len(vs) != crop.vt_count:
        out["count_unsure"] = True
    allowed = set(crop.vt_heights)
    for v in vs:
        if v["height"] not in allowed:
            _doubt(v, "height")
    for a, b in zip(vs, vs[1:]):
        if a["x_in"] is None or b["x_in"] is None:
            continue
        same_x = abs(a["x_in"] - b["x_in"]) <= STACK_X_IN
        apart = (None not in (a["top_in"], b["bottom_in"], b["top_in"], a["bottom_in"])
                 and (a["top_in"] <= b["bottom_in"] or b["top_in"] <= a["bottom_in"]))
        if not (same_x and apart) and b["x_in"] - a["x_in"] < PANEL_IN - tol_x:
            _doubt(a, "x_in")
            _doubt(b, "x_in")
    bays = out.get("bays")
    if isinstance(bays, list) and bays and all(isinstance(x, int | float) for x in bays):
        positions: list[list[dict[str, Any]]] = []  # verticals grouped by x: a stacked tower is one place
        for v in vs:
            if v["x_in"] is None:
                continue
            if positions and abs(positions[-1][0]["x_in"] - v["x_in"]) <= STACK_X_IN:
                positions[-1].append(v)
            else:
                positions.append([v])
        gaps = [b[0]["x_in"] - a[0]["x_in"] - PANEL_IN for a, b in zip(positions, positions[1:])]
        if gaps:
            best: list[int] | None = None
            for offset in range(len(bays) - len(gaps) + 1):
                bad = [k for k, g in enumerate(gaps) if abs(g - bays[offset + k]) > tol_x]
                if best is None or len(bad) < len(best):
                    best = bad
            for k in best if best is not None else range(len(gaps)):
                for v in positions[k + 1]:
                    _doubt(v, "x_in")
    return out


# ----- the run: one variant over DEV or, once, over TEST

GROSS_IN = 4.0  # a value off by more than any build-choice offset seen (3.34 in): a gross misplacement


@dataclass(frozen=True)
class Variant:
    name: str
    worked: int = 4
    pool: str = "frozen"  # "frozen", or "frozen+dev": also DEV walls a reference read right (drawn as cut)
    notes: bool = False
    rules: bool = False
    tiles: bool = False
    worked_tiles: bool = True  # tiles on the worked walls too; off keeps a request under the host's image limit
    reads: int = 1
    checks: bool = False
    effort: str = "high"


def dev_walls(world: Any) -> list[Pair]:
    """DEV: the first ten jobs' paired walls, then the frozen jobs' (never a P5 job's)."""
    from tools.reader_eval import cases, run
    first = [p for p in run.test_walls(world) if not p.case.startswith("P")]
    frozen = [p for c in cases.WORKED_CASES if c in world.pairings
              for p in sorted(world.pairings[c].pairs, key=lambda p: p.crop.page)]
    return first + frozen


def test_walls(world: Any) -> list[Pair]:
    """TEST: the P5 jobs' paired walls."""
    from tools.reader_eval import run
    return [p for p in run.test_walls(world) if p.case.startswith("P")]


def pool_for(world: Any, variant: Variant, drawn_as_cut: set[str]) -> list[Pair]:
    """The worked-wall pool: the frozen jobs' walls, and with "frozen+dev" the DEV walls in
    drawn_as_cut (those a reference read with every value right, so their as-cut answer is what the
    drawing shows). Never a TEST wall."""
    from tools.reader_eval import cases
    frozen = [p for c in cases.WORKED_CASES if c in world.pairings for p in world.pairings[c].pairs]
    if variant.pool == "frozen":
        return frozen
    dev = [p for p in dev_walls(world) if p.case not in cases.WORKED_CASES
           and f"{p.case}/{p.crop.page}" in drawn_as_cut]
    return frozen + dev


def request(world: Any, variant: Variant, wall: Pair, pool: Sequence[Pair]) -> Any:
    from tools.reader_eval import payload
    worked = choose_worked(pool, target_case=wall.case, n=variant.worked)
    notes = {(w.case, w.crop.page): note_for(w) for w in worked} if variant.notes else None
    return payload.build(f"{wall.case}/{wall.crop.page}", wall, worked, world.preps[wall.case].deny,
                         {w.case: world.preps[w.case].deny for w in worked},
                         instructions=payload.instructions(rules=variant.rules, tiles=variant.tiles,
                                                           bays=variant.checks),
                         schema=payload.SCHEMA_BAYS if variant.checks else payload.SCHEMA,
                         notes=notes, tiles=variant.tiles, worked_tiles=variant.worked_tiles)


WORKERS = 4  # calls at once; the guard counts every outstanding worst case (budget.CostGuard.reserve)


def _call(provider: Any, req: Any, key: str, guard: Any, path: Any, variant: Variant, attempt: int,
          state: dict[str, Any]) -> Reading | None:
    """One read: reserved first, one retry on a network error, the outcome cached whatever it is."""
    from tools.reader_eval import budget
    reading: Reading | None = None
    for _ in (1, 2):
        if state["stopped"]:
            return reading
        try:
            reserved = guard.reserve(provider.worst_case_usd(req))
        except budget.CapReached as exc:
            state["stopped"] = f"stopped by the cost cap: {exc}"
            return reading
        reading = provider.read(req, key)
        guard.settle(reserved, reading.cost_usd if reading.cost_known else None)
        with state["lock"]:
            state["calls"] += 1
        if not reading.network:
            break
    assert reading is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"wall": req.wall_id, "variant": variant.name, "attempt": attempt,
                                "status": reading.status, "answer": reading.answer,
                                "cost_usd": reading.cost_usd, "detail": reading.detail,
                                "latency_s": reading.latency_s, "at": datetime.now(UTC).isoformat()}),
                    encoding="utf-8")
    return reading


def read_variant(world: Any, variant: Variant, walls: Sequence[Pair], pool: Sequence[Pair], provider: Any,
                 key: str | None, guard: Any, *, answers: Any, call: bool, workers: int = WORKERS,
                 say: Callable[[str], object] = print) -> tuple[list[tuple[Pair, Reading]], dict[str, int]]:
    """Each wall read variant.reads times (each read cached under its own key, never sent twice; up to
    `workers` calls at once), combined by agree(), checked by check() when asked."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from tools.reader_eval import payload

    state: dict[str, Any] = {"stopped": None, "calls": 0, "lock": threading.Lock()}
    blocked = 0
    plan: list[tuple[Pair, list[Reading | None]]] = []
    jobs: list[tuple[int, int, Any, Any]] = []
    for wall in walls:
        try:
            req = request(world, variant, wall, pool)
        except payload.ContainmentError:
            blocked += 1
            continue
        digest = hashlib.sha256(json.dumps(provider.body(req), sort_keys=True).encode("utf-8")).hexdigest()
        reads: list[Reading | None] = []
        for attempt in range(1, variant.reads + 1):
            path = answers / provider.name / (f"{digest}.json" if attempt == 1 else f"{digest}-{attempt}.json")
            if path.is_file():
                saved = json.loads(path.read_text(encoding="utf-8"))
                reads.append(Reading(saved["status"], saved["answer"], saved["cost_usd"], saved["detail"], True,
                                     saved.get("latency_s")))
            else:
                reads.append(None)
                jobs.append((len(plan), attempt - 1, req, path))
        plan.append((wall, reads))
    if call and jobs:
        if key is None:
            raise ValueError("no key: nothing may be sent")
        waiting: dict[Any, list[tuple[int, int]]] = {}  # one call per distinct request, shared by every wall
        first: dict[Any, tuple[Any, int]] = {}          # that sends exactly it (identical walls do)
        for w, a, req, path in jobs:
            waiting.setdefault(path, []).append((w, a))
            first.setdefault(path, (req, a))
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool_:
            futures = {pool_.submit(_call, provider, req, key, guard, path, variant, a + 1, state): path
                       for path, (req, a) in first.items()}
            for future, path in futures.items():
                result = future.result()
                for w, a in waiting[path]:
                    plan[w][1][a] = result
        if state["stopped"]:
            say(state["stopped"])
    out: list[tuple[Pair, Reading]] = []
    not_run = 0
    for wall, reads in plan:
        if any(r is None for r in reads):
            not_run += 1
            continue
        done = [r for r in reads if r is not None]
        cost = sum(r.cost_usd for r in done)
        latency = sum(r.latency_s or 0.0 for r in done)
        if variant.reads == 1 and done[0].status != "answered":
            out.append((wall, Reading(done[0].status, None, cost, done[0].detail, latency_s=latency)))
            continue
        combined = agree([r.answer if r.status == "answered" else None for r in done])
        if variant.checks:
            combined = check(combined, wall.crop)
        out.append((wall, Reading("answered", strip_bays(combined), cost, latency_s=latency)))
    return out, {"blocked": blocked, "not_run": not_run, "calls": state["calls"]}


def summary(results: Sequence[tuple[Pair, Reading]]) -> dict[str, Any]:
    """The numbers a variant is judged by: silent errors split by diagnose, wrong placements, gross
    misplacements (a reading-error value off by more than GROSS_IN, or a wrong count), cost, latency."""
    from tools.reader_eval import diagnose, scoring
    scores = [scoring.score_wall(p, r) for p, r in results]
    diags = [diagnose.diagnose(p, r) for p, r in results]
    gross = sum(1 for s, d in zip(scores, diags) if "reading error" in d.kinds and (
        any(o.status.startswith("wrong") and (o.deviation or 0) > GROSS_IN for o in s.outcomes)
        or any(o.kind == "order" and o.status.startswith("wrong") for o in s.outcomes)))
    lat = [r.latency_s for _, r in results if r.latency_s]
    return {"walls": len(scores), "accepted": sum(s.accepted for s in scores),
            "wrong_placements": sum(s.accepted and s.wrong > 0 for s in scores),
            "silent": sum(s.silent for s in scores),
            "silent_reading": sum(d.silent_reading for d in diags),
            "silent_build": sum(d.silent_offset for d in diags),
            "flagged_reading": sum(d.flagged_reading for d in diags),
            "reading_error_walls": sum("reading error" in d.kinds for d in diags),
            "gross_walls": gross, "cost": round(sum(r.cost_usd for _, r in results), 4),
            "cost_per_wall": round(sum(r.cost_usd for _, r in results) / len(results), 5) if results else None,
            "latency_median_s": round(statistics.median(lat), 1) if lat else None,
            "declined": dict(sorted(Counter(s.decline for s in scores if s.decline).items()))}


# ----- once over TEST, and the command line


def final_guard(path: Any, variant: Variant) -> None:
    """TEST is read once, with one variant: the first final run records it; any other is refused."""
    from dataclasses import asdict
    from pathlib import Path as _Path
    path = _Path(path)
    wanted = asdict(variant)
    if path.is_file():
        if json.loads(path.read_text(encoding="utf-8")) != wanted:
            raise ValueError("the TEST walls are read once, with one variant: another was already run on them")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(wanted, indent=1), encoding="utf-8")


def drawn_as_cut(world: Any) -> set[str]:
    """DEV walls (Jobs A to J) that Claude, the reference, read with every value right: their as-cut
    answer is what the drawing shows, so they may serve as worked walls. From its cached answers."""
    from tools.reader_eval import cases, diagnose, providers, run
    from tools.reader_eval.scoring import Reading as _Reading
    claude = providers.AnthropicMessages()
    worked = run.worked_walls(world)
    out: set[str] = set()
    for wall in dev_walls(world):
        if wall.case in cases.WORKED_CASES:
            continue
        req = run.request_for(world, wall, worked)
        digest = hashlib.sha256(json.dumps(claude.body(req), sort_keys=True).encode("utf-8")).hexdigest()
        path = cases.reader_dir() / "answers" / claude.name / f"{digest}.json"
        if path.is_file():
            saved = json.loads(path.read_text(encoding="utf-8"))
            reading = _Reading(saved["status"], saved["answer"], saved["cost_usd"])
            if diagnose.diagnose(wall, reading).kinds == ("right",):
                out.add(f"{wall.case}/{wall.crop.page}")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    from tools.reader_eval import budget, cases, keys, providers, run
    parser = argparse.ArgumentParser(prog="python -m tools.reader_eval.tuning")
    parser.add_argument("--name", required=True)
    parser.add_argument("--set", choices=("dev", "test"), default="dev")
    parser.add_argument("--final", action="store_true", help="the one TEST run")
    parser.add_argument("--worked", type=int, default=4)
    parser.add_argument("--pool", choices=("frozen", "frozen+dev"), default="frozen")
    parser.add_argument("--notes", action="store_true")
    parser.add_argument("--rules", action="store_true")
    parser.add_argument("--tiles", action="store_true")
    parser.add_argument("--target-tiles-only", action="store_true")
    parser.add_argument("--reads", type=int, default=1)
    parser.add_argument("--checks", action="store_true")
    parser.add_argument("--effort", default="high")
    parser.add_argument("--call", action="store_true", help="send; without it, score from the cache only")
    parser.add_argument("--cap", type=float, default=3.0)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args(argv)
    if args.set == "test" and not args.final:
        parser.error("the TEST walls are read once, at the end: pass --final")
    variant = Variant(args.name, args.worked, args.pool, args.notes, args.rules, args.tiles,
                      not args.target_tiles_only, args.reads, args.checks, args.effort)
    if args.set == "test":
        final_guard(cases.reader_dir() / "tuning_final.json",
                    Variant("final", *[getattr(variant, f) for f in ("worked", "pool", "notes", "rules", "tiles",
                                                                     "worked_tiles", "reads", "checks", "effort")]))
    world = run.load()
    walls = (dev_walls(world) if args.set == "dev" else test_walls(world))[: args.limit]
    pool = pool_for(world, variant, drawn_as_cut(world) if args.pool == "frozen+dev" else set())
    provider = providers.OpenRouterLuna(effort=args.effort)
    key = keys.read_key(provider.key_name) if args.call else None
    if args.call and not key:
        print(f"No key in Windows Credential Manager under {keys.target(provider.key_name)}: nothing was sent.")
        return 2
    total = budget.SpendLedger(cases.reader_dir() / f"spend_{provider.name}.json", run.LEDGER_CAPS[provider.name])
    tune = budget.SpendLedger(cases.reader_dir() / f"spend_{provider.name}_tune.json", 8.0)
    guard = budget.CostGuard(args.cap, ledger=total, ledgers=(tune,))
    results, counts = read_variant(world, variant, walls, pool, provider, key, guard,
                                   answers=cases.reader_dir() / "answers", call=args.call)
    numbers = {"variant": args.name, "set": args.set, **summary(results), **counts,
               "run_spend": round(guard.spent, 4), "tuning_total": round(tune.spent, 4),
               "openrouter_total": round(total.spent, 4)}
    out = cases.reader_dir() / "tuning"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.name}-{args.set}.json").write_text(json.dumps(numbers, indent=1), encoding="utf-8")
    print(json.dumps(numbers))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
