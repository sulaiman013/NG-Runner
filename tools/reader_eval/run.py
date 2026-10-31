r"""
The reader evaluation's command line (docs/p2/build-spec.md 5.7; design 3.6, decisions 10, 16, 17).

    .venv\Scripts\python.exe -m tools.reader_eval.run collect     copy the 13 summaries, read-only
    .venv\Scripts\python.exe -m tools.reader_eval.run prep        crops, parts lines, containment
    .venv\Scripts\python.exe -m tools.reader_eval.run pair        truth and pairing, with counts
    .venv\Scripts\python.exe -m tools.reader_eval.run dry-run [--walls 3] [--provider openai]
    .venv\Scripts\python.exe -m tools.reader_eval.run eval --provider openai [--limit 10] [--cap 3]
    .venv\Scripts\python.exe -m tools.reader_eval.run score --provider openai [--tol-x 1] [--tol-y 1.5]

Everything it writes goes under .local-fixtures\reader\ (gitignored): the crops, prep.json,
truth.json, the cached answers and the reports. What it prints names case letters and pages only.

dry-run sends nothing and reads no key: it prints exactly what would be sent for the first walls of
the test set (each image as its size and hash, every word in full), then checks its own output for
every job name in the local case key. eval reads the provider's key from Windows Credential Manager
(NGRunner/openai_key or NGRunner/anthropic_key) and stops, having sent nothing, when it is absent.
Each call is first checked by the cost guard (budget.py); an answer already cached for the same
request (the sha256 of the request body, without the key) is reused and costs nothing, so score
can re-score with other tolerances for free.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tools.reader_eval import budget, cases, keys, pairing, payload, prep, providers, scoring, truth

DEFAULT_WORKED = 4  # worked walls per request: decision 17 says 3 to 5


@dataclass
class World:
    preps: dict[str, prep.SummaryPrep]
    pairings: dict[str, pairing.Pairing]


def load() -> World:
    preps = {c: prep.prepare(cases.summary(c)) for c in cases.TEST_CASES + cases.WORKED_CASES}
    pairings = {c: pairing.pair(c, preps[c].crops, {stem: truth.read_room(data)
                                                    for stem, data in cases.rooms(c).items()})
                for c in preps}
    return World(preps, pairings)


def test_walls(world: World) -> list[pairing.Pair]:
    return [p for c in cases.TEST_CASES if c in world.pairings
            for p in sorted(world.pairings[c].pairs, key=lambda p: p.crop.page)]


def worked_walls(world: World, n: int = DEFAULT_WORKED) -> list[pairing.Pair]:
    """Worked walls from the frozen jobs only: each adds a category not yet shown, most first."""
    if not 3 <= n <= 5:
        raise ValueError("decision 17: 3 to 5 worked walls")
    pool = [p for c in cases.WORKED_CASES if c in world.pairings
            for p in sorted(world.pairings[c].pairs, key=lambda p: p.crop.page) if p.wall.panels]
    chosen: list[pairing.Pair] = []
    shown: set[str] = set()
    while pool and len(chosen) < n:
        best = max(pool, key=lambda p: (len(set(p.wall.categories) - shown), -pool.index(p)))
        chosen.append(best)
        shown |= set(best.wall.categories)
        pool.remove(best)
    return chosen


def request_for(world: World, wall: pairing.Pair, worked: Sequence[pairing.Pair]) -> payload.WallRequest:
    """The wall's request; each text is checked against its own summary's deny list."""
    return payload.build(f"{wall.case}/{wall.crop.page}", wall, worked, world.preps[wall.case].deny,
                         {w.case: world.preps[w.case].deny for w in worked})


# ----- commands


def cmd_collect(_: argparse.Namespace) -> int:
    found = cases.collect()
    print(f"Collected {len(found)} summaries: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(found.values()).items())))
    return 0


def cmd_prep(_: argparse.Namespace) -> int:
    out = cases.reader_dir()
    rows, total = [], Counter()
    for c in cases.TEST_CASES + cases.WORKED_CASES:
        result = prep.prepare(cases.summary(c))
        folder = out / "crops" / c
        folder.mkdir(parents=True, exist_ok=True)
        for crop in result.crops:
            (folder / f"p{crop.page}.png").write_bytes(crop.png)
            rows.append({"case": c, "page": crop.page, "design": crop.design, "wall": crop.wall,
                         "width_in": crop.width_in, "height_in": crop.height_in, "pixels": crop.pixels,
                         "native": crop.native, "trim": crop.trim, "sha256": crop.sha256,
                         "parts": [p.as_text() for p in crop.parts], "vt_count": crop.vt_count,
                         "vt_rad_count": crop.vt_rad_count, "vt_heights": crop.vt_heights})
        total.update(pages=result.pages, elevation_pages=result.elevation_pages, crops=len(result.crops),
                     refused=len(result.refused), trimmed=sum(c2.trim != (0, 0, 0, 0) for c2 in result.crops),
                     unlabelled_tables=result.unlabelled_tables, orphan_wall_tables=result.orphan_wall_tables)
        for r in result.refused:
            total[f"refused {r.code}"] += 1
            print(f"  case {c} page {r.page}: refused, {r.code}")
        for crop in result.crops:
            if hits := [result.deny.hits(p.as_text()) for p in crop.parts if result.deny.hits(p.as_text())]:
                total["walls whose parts repeat a deny word"] += 1
                print(f"  case {c} page {crop.page}: a parts line repeats a word from {sorted(set(sum(hits, ())))}")
    (out / "prep.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print("Prep: " + ", ".join(f"{k} {v}" for k, v in total.items()))
    return 0


def cmd_pair(_: argparse.Namespace) -> int:
    world = load()
    rows, left, unpaired, cats, group = [], Counter(), 0, Counter(), Counter()
    for c, result in world.pairings.items():
        kind = "test" if c in cases.TEST_CASES else "worked"
        group[f"{kind} pairs"] += len(result.pairs)
        for p in result.pairs:
            if kind == "test":
                cats.update(p.wall.categories)
            rows.append({"case": c, "page": p.crop.page, "wall": p.crop.wall, "room": p.room,
                         "position": p.wall.position, "length_in": round(p.wall.length_in, 3),
                         "categories": p.wall.categories,
                         "panels": [{"prod": q.prod, "x_in": round(q.x_in, 3), "bottom_in": round(q.bottom_in, 3),
                                     "top_in": round(q.top_in, 3), "height_in": round(q.height_in, 3),
                                     "radius": q.radius} for q in p.wall.panels]})
        for x in result.left_out:
            left[x.code] += 1
            group[f"{kind} left out"] += 1
            print(f"  case {c} page {x.page} (wall {x.wall_name}): left out, {x.code}")
        unpaired += len(result.unpaired_walls)
        for u in result.unpaired_walls:
            print(f"  case {c} {u.room} wall position {u.position}: {u.verticals} verticals, no crop paired")
    out = cases.reader_dir()
    (out / "truth.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    crops = sum(len(p.crops) for p in world.preps.values())
    walls = sum(len(r.walls) for c in world.pairings for r in [truth.read_room(d) for d in cases.rooms(c).values()])
    print(f"Walls in the .des: {walls}. Crops: {crops}. Pairs: {sum(len(r.pairs) for r in world.pairings.values())}. "
          f"Left out: {sum(left.values())} ({', '.join(f'{k} {v}' for k, v in sorted(left.items()))}). "
          f"Walls with verticals no crop paired: {unpaired}.")
    print("By group: " + ", ".join(f"{k} {v}" for k, v in sorted(group.items())))
    print("Test-set categories: " + ", ".join(f"{k} {v}" for k, v in sorted(cats.items())))
    return 0


def _self_check(text: str) -> list[str]:
    """Which job names from the local case key appear in text (case-insensitive); [] when clean."""
    try:
        names = cases.job_names()
    except cases.CaseError:
        return ["(no case key to check against)"]
    found = []
    folded = text.casefold()
    for letter, job in names.items():
        for word in {job, job.split("_")[0]}:
            if len(word) >= 3 and word.casefold() in folded:
                found.append(f"case {letter}")
    return sorted(set(found))


def cmd_dry_run(args: argparse.Namespace) -> int:
    world = load()
    worked = worked_walls(world, args.worked)
    provider = providers.PROVIDERS[args.provider](effort=args.effort) if args.provider else None
    shown: list[str] = []
    for wall in test_walls(world)[: args.walls]:
        try:
            req = request_for(world, wall, worked)
        except payload.ContainmentError as exc:
            shown.append(f"=== {wall.case}/{wall.crop.page}: BLOCKED, nothing would be sent: {exc} ===")
            continue
        settings = ""
        if provider is not None:
            settings = f"{provider.settings()}\nworst-case cost of this call: ${provider.worst_case_usd(req):.4f}"
        shown.append(payload.dry_run(req, settings))
    text = "\n\n".join(shown)
    print(text)
    leaks = _self_check(text)
    print(f"\nSelf-check of this output against the local case key: "
          + ("no job name or surname found." if not leaks else f"FOUND {', '.join(leaks)}"))
    return 0 if not leaks else 1


def _cache_path(answers: Path, provider: providers._Base, req: payload.WallRequest) -> Path:
    """Where the answer to this exact request is kept: the sha256 of its body (no key in it)."""
    digest = hashlib.sha256(json.dumps(provider.body(req), sort_keys=True).encode("utf-8")).hexdigest()
    return answers / provider.name / f"{digest}.json"


def _required(world: World, scored: set[str]) -> dict[str, set[str]]:
    """Per test job whose every paired wall was read in this run: every wall a full build needs."""
    required: dict[str, set[str]] = {}
    for c in cases.TEST_CASES:
        result = world.pairings.get(c)
        if result is None:
            continue
        ids = {f"{c}/{p.crop.page}" for p in result.pairs}
        if ids and ids <= scored:
            required[c] = ids | {f"{c}/{x.page}:left out" for x in result.left_out} | {
                f"{c}/{u.room}/{u.position}:unpaired" for u in result.unpaired_walls}
    return required


@dataclass
class RunResult:
    scores: list[scoring.WallScore]
    blocked: Counter[str]
    not_run: int
    stopped: str | None


def read_walls(world: World, walls: Sequence[pairing.Pair], worked: Sequence[pairing.Pair],
               provider: providers._Base, key: str | None, guard: budget.CostGuard, *, answers: Path,
               call: bool, tol_x: float = scoring.TOL_X_IN, tol_y: float = scoring.TOL_Y_IN,
               say: Callable[[str], object] = print) -> RunResult:
    """
    Read each wall: its cached answer when this exact request was answered before (no call, no
    cost), else, when call is set, one call the cost guard allows first. Once the guard refuses,
    no further call is made in this run. A request containment blocks is never sent.
    """
    result = RunResult([], Counter(), 0, None)
    for wall in walls:
        try:
            req = request_for(world, wall, worked)
        except payload.ContainmentError as exc:
            result.blocked[str(exc).split(": ", 1)[-1]] += 1
            continue
        path = _cache_path(answers, provider, req)
        if path.is_file():
            saved = json.loads(path.read_text(encoding="utf-8"))
            reading = scoring.Reading(saved["status"], saved["answer"], saved["cost_usd"], saved["detail"], True)
        elif not call or result.stopped:
            reading = scoring.Reading("not_run", None, 0.0, result.stopped or "no cached answer")
        else:
            try:
                guard.before(provider.worst_case_usd(req))
            except budget.CapReached as exc:
                result.stopped = f"stopped by the cost cap: {exc}"
                say(f"{req.wall_id}: {result.stopped}")
                reading = scoring.Reading("not_run", None, 0.0, result.stopped)
            else:
                if key is None:
                    raise keys.KeyProblem("no key: nothing may be sent")
                reading = provider.read(req, key)
                guard.after(None if reading.status == "error" else reading.cost_usd)
                say(f"{req.wall_id}: {reading.status} (${reading.cost_usd:.4f}; run total ${guard.spent:.4f})")
                if reading.status != "error":
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps({"wall": req.wall_id, "model": provider.model,
                                                "effort": provider.effort, "status": reading.status,
                                                "answer": reading.answer, "cost_usd": reading.cost_usd,
                                                "detail": reading.detail, "at": datetime.now(UTC).isoformat()}),
                                    encoding="utf-8")
        if reading.status == "not_run":
            result.not_run += 1
        else:
            result.scores.append(scoring.score_wall(wall, reading, tol_x=tol_x, tol_y=tol_y))
    return result


def _evaluate(args: argparse.Namespace, *, call: bool) -> int:
    world = load()
    provider = providers.PROVIDERS[args.provider](effort=args.effort)
    key = None
    if call:
        key = keys.read_key(provider.key_name)
        if not key:
            print(f"No key in Windows Credential Manager under {keys.target(provider.key_name)}: nothing was sent.")
            return 2
    guard = budget.CostGuard(args.cap)
    result = read_walls(world, test_walls(world)[: args.limit], worked_walls(world, args.worked), provider, key,
                        guard, answers=cases.reader_dir() / "answers", call=call, tol_x=args.tol_x, tol_y=args.tol_y)
    report = scoring.summarize(result.scores, required=_required(world, {s.wall_id for s in result.scores}),
                               tolerances={"x": args.tol_x, "top_bottom": args.tol_y})
    out = cases.reader_dir() / "reports"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    blocked = result.blocked
    head = (f"Provider {provider.name}, model {provider.model}, effort {provider.effort}. "
            f"Run spend ${guard.spent:.4f} of a ${guard.cap:.2f} cap, {guard.calls} calls, "
            f"{result.not_run} walls not run. Blocked by the containment scan: {sum(blocked.values())}"
            + (f" ({'; '.join(f'{k}: {v}' for k, v in blocked.items())})" if blocked else "") + ".")
    (out / f"{stamp}-{provider.name}.json").write_text(report.to_json(), encoding="utf-8")
    (out / f"{stamp}-{provider.name}.txt").write_text(f"{head}\n{report.text()}\n", encoding="utf-8")
    print(head)
    print(report.text())
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.reader_eval.run")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect")
    sub.add_parser("prep")
    sub.add_parser("pair")
    for name in ("dry-run", "eval", "score"):
        p = sub.add_parser(name)
        p.add_argument("--provider", choices=sorted(providers.PROVIDERS), required=name != "dry-run")
        p.add_argument("--effort", default="high")
        p.add_argument("--worked", type=int, default=DEFAULT_WORKED)
        p.add_argument("--walls", type=int, default=3)
        p.add_argument("--limit", type=int, default=10)
        p.add_argument("--cap", type=float, default=budget.DEFAULT_CAP_USD)
        p.add_argument("--tol-x", type=float, default=scoring.TOL_X_IN)
        p.add_argument("--tol-y", type=float, default=scoring.TOL_Y_IN)
    args = parser.parse_args(argv)
    commands = {"collect": cmd_collect, "prep": cmd_prep, "pair": cmd_pair, "dry-run": cmd_dry_run,
                "eval": lambda a: _evaluate(a, call=True), "score": lambda a: _evaluate(a, call=False)}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
