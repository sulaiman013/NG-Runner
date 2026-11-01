"""
P5a's room model (ngrunner/build/) built from the evaluation's material and compared with the as-cut rooms.

Why: P5a's exit test is "3 archived builds reproduced", Jim's frozen jobs X, Y and Z, and his own check of
a rebuild is regression.py's compare (his lines 188-220): the walls by file order, their length and height
within 0.5 mm and their angle within 0.01 degree, and each panel found on its wall by X within 0.5 mm, then
its product, height, depth and Elev within 0.5 mm. This module makes the same comparison of the model, value
by value, so every difference and every could-not-verify shows. Nothing here calls a model.

  frozen  build_frozen(): each frozen room from its design's WALL/AREA table (NG Runner's own summary check
          reads it), each paired wall's own as-cut panels standing in for the reading (payload.answer_of,
          as the reader's worked walls show them), and the room's own wall order and angles standing in
          for the plan, which nothing in NG Runner reads yet. So the reading and the plan hold by
          construction, and the test is everything the builder adds: the walls from the table, the chain,
          and every panel's height, depth, mount, Elev, notch side, radius, REA and product
  DEV     build_read(): the first 26 walls (Jobs A to J) from Claude's cached readings, with no plan.
          Each panel is compared with its as-cut panel within the evaluation's tolerances (x 1/8 in, Elev
          and top 1/4 in, height 0.65 in; depth 0.5 mm, Jim's, as the evaluation has none), paired left
          to right as scoring.py pairs them

Every value is right, wrong, could not verify, or not needed. A wrong value is one the model settled that
the as-cut room doesn't have: the builder's silent error, which matters most.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ngrunner.build import placement as pl
from ngrunner.build.room import ICLine, Plan, ReadVertical, ReadWall, RoomModel, TableRow
from ngrunner.build.schedule import build_room
from ngrunner.build.walls import table_rows
from ngrunner.intake.summary_check import check_summary
from tools.reader_eval import cases, payload, scoring
from tools.reader_eval.pairing import HEIGHT_TOL_IN, Pair
from tools.reader_eval.prep import WallCrop, name_key_hash
from tools.reader_eval.scoring import Reading
from tools.reader_eval.truth import Panel, RoomTruth, Wall, read_room

MM = pl.MM
JIM_MM = 0.5  # regression.py compare: positions and sizes within 0.5 mm
JIM_ANG = 0.01  # ... and angles within 0.01 degree
EVAL_TOL_MM = {"x": scoring.TOL_X_IN * MM, "elev": scoring.TOL_Y_IN * MM, "top": scoring.TOL_Y_IN * MM,
               "height": HEIGHT_TOL_IN * MM, "depth": JIM_MM}
FROZEN_TOL_MM = {"x": JIM_MM, "elev": JIM_MM, "top": JIM_MM, "height": JIM_MM, "depth": JIM_MM}
VALUES = ("x", "elev", "top", "height", "depth", "mount", "side", "radius", "rea", "product")


# ----- the evaluation's material as the builder's inputs


def lines_of(crop: WallCrop) -> tuple[ICLine, ...]:
    return tuple(ICLine(p.qty, p.desc) for p in crop.parts)


def read_wall(crop: WallCrop, answer: Mapping[str, Any] | None) -> ReadWall:
    """A crop's wall: its parts lines, and the answer's verticals when there is one."""
    if answer is None:
        return ReadWall(crop.wall, lines_of(crop))
    return ReadWall(crop.wall, lines_of(crop),
                    tuple(ReadVertical(v["x_in"], v["bottom_in"], v["top_in"], v["height"], v["radius"],
                                       tuple(v["unsure"])) for v in answer["verticals"]),
                    bool(answer["count_unsure"]), tuple(answer["unreadable"]))


def tables(summary: bytes) -> dict[str, tuple[TableRow, ...]]:
    """Each design's WALL/AREA table as NG Runner's summary check reads it, by its name's key hash."""
    return {name_key_hash(d["name"]): table_rows(d["walls"])
            for d in check_summary(summary).details.get("designs", [])}


def plan_of(room: RoomTruth) -> Plan:
    """The as-cut room's chain, standing in for the plan: its walls' numbers in file order and their angles."""
    return Plan(tuple(w.number for w in room.walls), tuple(w.ang for w in room.walls))


# ----- one value against the truth


def _num(kind: str, model: Any, fact_status: str, truth: float, tol: Mapping[str, float]) -> str:
    if fact_status in ("could not verify", "not needed"):
        return fact_status
    return "right" if model is not None and abs(model - truth) <= tol[kind] else "wrong"


def _same(fact_status: str, model: Any, truth: Any) -> str:
    if fact_status in ("could not verify", "not needed"):
        return fact_status
    return "right" if model == truth else "wrong"


def truth_side(prod: str) -> str | None:
    return next((s for s in ("Thru", "Left", "Right") if f" {s}" in f" {prod}"), None)


def truth_mount(prod: str) -> str:
    return "WM" if prod.startswith(("WM", "Wall Mount")) else "FM"


def panel_values(model: Any, truth: Panel, tol: Mapping[str, float]) -> dict[str, str]:
    """Each value of a model panel (PanelSpec) against its as-cut panel."""
    prod = truth.prod.strip()
    top_status = "right"
    if not (model.elev_mm.settled and model.height_mm.settled):
        top_status = "could not verify"
    side = truth_side(prod)
    return {
        "x": _num("x", model.x_mm.value, model.x_mm.status, truth.x_mm, tol),
        "elev": _num("elev", model.elev_mm.value, model.elev_mm.status, truth.elev_mm, tol),
        "top": _num("top", model.top_mm, top_status, truth.elev_mm + truth.height_mm, tol),
        "height": _num("height", model.height_mm.value, model.height_mm.status, truth.height_mm, tol),
        "depth": _num("depth", model.depth_mm.value, model.depth_mm.status, truth.depth_mm, tol),
        "mount": _same(model.mount.status, model.mount.value, truth_mount(prod)),
        "side": ("right" if side is None else "wrong") if model.side.status == "not needed"
                else _same(model.side.status, model.side.value, side),
        "radius": _same(model.radius.status, model.radius.value, truth.radius),
        "rea": _same(model.rea.status, model.rea.value, truth.rea),
        "product": _same(model.product.status, model.product.value, prod),
    }


# ----- a room against the truth


@dataclass
class PanelResult:
    wall_id: str  # case/page, or case/room/wall for a wall no crop drew
    index: int | None  # the truth panel's, left to right; None for a panel the model has and the room doesn't
    prod: str
    values: dict[str, str]  # value -> right | wrong | could not verify | not needed; {} when missing or extra
    why: dict[str, str] = field(default_factory=dict)  # value -> the model's reason, where it could not verify

    @property
    def status(self) -> str:
        if not self.values:
            return "missing" if self.index is not None else "extra"
        found = set(self.values.values())
        return "wrong" if "wrong" in found else "could not verify" if "could not verify" in found else "right"


@dataclass
class WallResult:
    wall_id: str
    categories: tuple[str, ...]
    panels: list[PanelResult]
    gates: list[str]  # the wall's own could-not-verify findings (its gates, its run start)

    def count(self, status: str) -> int:
        return sum(p.status == status for p in self.panels)


@dataclass
class RoomResult:
    case: str
    design: int
    room: str
    model: RoomModel
    walls: list[str]  # per as-cut wall, what differs; [] when it is right
    wall_values: Counter[str]  # value -> statuses, over the walls: "length right" and so on
    panels: list[WallResult]


def _why(model: Any) -> dict[str, str]:
    """The model panel's reasons, by the value names compared here (x_mm is x; top is its Elev's or height's)."""
    out = {name.removesuffix("_mm"): fact.why for name, fact in model.facts() if fact.status == "could not verify"}
    if "elev" in out or "height" in out:
        out["top"] = out.get("elev") or out["height"]
    return out


def _walls(model: RoomModel, room: RoomTruth, pairs: Sequence[Pair]) -> tuple[list[str], Counter[str]]:
    """The model's walls against the as-cut ones: as Jim's compare has them, by file order with a plan;
    without one, each paired wall's length and height only (no chain to compare)."""
    out: list[str] = []
    counts: Counter[str] = Counter()
    chained = all(w.position.settled for w in model.walls)
    if chained:
        if len(model.walls) != len(room.walls):
            out.append(f"{len(model.walls)} walls built, {len(room.walls)} as cut")
        origin = model.walls[0].pos_mm.value if model.walls else None
        cut_origin = room.walls[0].pos_mm if room.walls else (0.0, 0.0)
        for m, t in zip(model.walls, room.walls):
            for label, ok in (("number", m.name == t.number),
                              ("length", abs((m.length_mm.value or 0) - t.length_in * MM) <= JIM_MM),
                              ("height", abs((m.height_mm.value or 0) - t.height_in * MM) <= JIM_MM),
                              ("angle", abs((m.angle.value or 0) - t.ang) <= JIM_ANG)):
                counts[f"{label} {'right' if ok else 'wrong'}"] += 1
                if not ok:
                    out.append(f"wall {t.position}: {label} differs")
            pos, origin_ok = m.pos_mm.value, origin is not None
            if pos is not None and origin_ok:
                rel = (pos[0] - origin[0], pos[1] - origin[1])
                cut = (t.pos_mm[0] - cut_origin[0], t.pos_mm[1] - cut_origin[1])
                ok = abs(rel[0] - cut[0]) <= JIM_MM and abs(rel[1] - cut[1]) <= JIM_MM
                counts[f"start {'right' if ok else 'wrong'}"] += 1
                if not ok:
                    out.append(f"wall {t.position}: start differs")
        if model.walls and room.walls and model.walls[0].pos_mm.value is not None:
            first = model.walls[0].pos_mm.value
            if abs(first[0] - room.walls[0].pos_mm[0]) > JIM_MM or abs(first[1] - room.walls[0].pos_mm[1]) > JIM_MM:
                counts["chain shifted"] += 1
        return out, counts
    for p in pairs:
        m = model.wall(p.crop.wall)
        if m is None:
            out.append(f"wall {p.wall.position}: not in the table")
            continue
        for label, value, cut in (("length", m.length_mm.value, p.wall.length_in * MM),
                                  ("height", m.height_mm.value, p.wall.height_in * MM)):
            ok = value is not None and abs(value - cut) <= JIM_MM
            counts[f"{label} {'right' if ok else 'wrong'}"] += 1
            if not ok:
                out.append(f"wall {p.wall.position}: {label} differs")
        counts["angle could not verify"] += 1
    return out, counts


def _frozen_wall(model: RoomModel, pair: Pair, wall_id: str) -> WallResult:
    """Jim's compare (regression.py 207-219): each as-cut panel found on its wall by X within 0.5 mm. A panel
    whose x could not verify is taken at its place in the drawing's order, which the standing-in reading
    gives as the room's own (payload.answer_of)."""
    schedule = next((s for s in model.schedules if s.wall == pair.crop.wall), None)
    unused = list(schedule.panels) if schedule else []
    results = []
    for i, truth in enumerate(pair.wall.panels):
        hit = next((u for u in unused if u.x_mm.value is not None and abs(u.x_mm.value - truth.x_mm) <= JIM_MM), None)
        if hit is None:
            hit = next((u for u in unused if u.x_mm.value is None and u.index == i), None)
        if hit is None:
            results.append(PanelResult(wall_id, i, truth.prod, {}))
            continue
        unused.remove(hit)
        results.append(PanelResult(wall_id, i, truth.prod, panel_values(hit, truth, FROZEN_TOL_MM), _why(hit)))
    results += [PanelResult(wall_id, None, u.product.value or "?", {}) for u in unused]
    gates = [f"{f.rule}: {f.why}" for f in (schedule.findings if schedule else ()) if f.status == "could not verify"]
    return WallResult(wall_id, pair.wall.categories, results, gates)


def _read_wall_result(model: RoomModel, pair: Pair, wall_id: str) -> WallResult:
    """A read wall, paired left to right as scoring.py pairs a reading with its truth."""
    schedule = next((s for s in model.schedules if s.wall == pair.crop.wall), None)
    built = list(schedule.panels) if schedule else []
    truths = pair.wall.panels
    results = []
    for i, j in scoring._align([t.x_in for t in truths],
                               [(p.x_mm.value / MM if p.x_mm.value is not None else None) for p in built]):
        if i is None:
            results.append(PanelResult(wall_id, None, built[j].product.value or "?", {}))
        elif j is None:
            results.append(PanelResult(wall_id, i, truths[i].prod, {}))
        else:
            results.append(PanelResult(wall_id, i, truths[i].prod, panel_values(built[j], truths[i], EVAL_TOL_MM),
                                       _why(built[j])))
    gates = [f"{f.rule}: {f.why}" for f in (schedule.findings if schedule else ()) if f.status == "could not verify"]
    return WallResult(wall_id, pair.wall.categories, results, gates)


# ----- the frozen jobs


def build_frozen(pairs: Mapping[str, Sequence[Pair]], crops: Mapping[str, Sequence[WallCrop]],
                 summaries: Mapping[str, bytes], rooms: Mapping[str, Mapping[str, RoomTruth]],
                 jobs: Sequence[str] = ("X", "Y", "Z")) -> list[RoomResult]:
    """Each frozen room built with its own panels as the reading and its own chain as the plan (module notes)."""
    out = []
    for case in jobs:
        table = tables(summaries[case])
        by_design: dict[int, list[Pair]] = defaultdict(list)
        for p in pairs.get(case, ()):
            by_design[p.crop.design].append(p)
        for design, members in sorted(by_design.items()):
            stems = {p.room for p in members}
            if len(stems) != 1:
                continue
            stem = stems.pop()
            room = rooms[case][stem]
            crop = members[0].crop
            rows = table.get(crop.design_key, ())
            paired = {p.crop.page for p in members}
            walls = [read_wall(p.crop, payload.answer_of(p)) for p in members]
            walls += [read_wall(c, None) for c in crops.get(case, ()) if c.design == design and c.page not in paired]
            model = build_room(f"{case} room {design + 1}", rows, walls, plan_of(room))
            diffs, counts = _walls(model, room, members)
            results = [_frozen_wall(model, p, f"{case}/{p.crop.page}") for p in sorted(members, key=lambda p: p.crop.page)]
            out.append(RoomResult(case, design, stem, model, diffs, counts, results))
    return out


# ----- the read walls


def build_read(readings: Sequence[tuple[Pair, Reading]], crops: Mapping[str, Sequence[WallCrop]],
               summaries: Mapping[str, bytes], rooms: Mapping[str, Mapping[str, RoomTruth]]) -> list[RoomResult]:
    """Each room the readings touch, built from them with no plan, and compared wall by wall (module notes)."""
    by_room: dict[tuple[str, int], list[tuple[Pair, Reading]]] = defaultdict(list)
    for pair, reading in readings:
        by_room[(pair.case, pair.crop.design)].append((pair, reading))
    out = []
    table_of: dict[str, dict[str, tuple[TableRow, ...]]] = {}
    for (case, design), members in by_room.items():
        if case not in table_of:
            table_of[case] = tables(summaries[case])
        valid = [(p, r) for p, r in members if r.status == "answered" and scoring._valid(r.answer)]
        pages = {p.crop.page for p, _ in valid}
        walls = [read_wall(p.crop, r.answer) for p, r in valid]
        walls += [read_wall(c, None) for c in crops.get(case, ()) if c.design == design and c.page not in pages]
        crop = members[0][0].crop
        model = build_room(f"{case} room {design + 1}", table_of[case].get(crop.design_key, ()), walls)
        stems = {p.room for p, _ in members}
        stem = stems.pop() if len(stems) == 1 else members[0][0].room
        room = rooms[case][stem]
        diffs, counts = _walls(model, room, [p for p, _ in members])
        results = [_read_wall_result(model, p, f"{p.case}/{p.crop.page}") for p, _ in members]
        out.append(RoomResult(case, design, stem, model, diffs, counts, results))
    return out


def load_rooms(case_list: Iterable[str]) -> dict[str, dict[str, RoomTruth]]:
    return {c: {stem: read_room(data) for stem, data in cases.rooms(c).items()} for c in case_list}


# ----- the report


def _reasons(walls: Iterable[WallResult]) -> Counter[str]:
    """Could-not-verify values by the rule or input behind them, in a few words each."""
    out: Counter[str] = Counter()
    for w in walls:
        for p in w.panels:
            for value, why in p.why.items():
                if p.values.get(value) == "could not verify":
                    out[f"{value}: {reason(why)}"] += 1
    return out


def reason(why: str) -> str:
    """The kind of reason, for counting: the rule or the input that left a value open."""
    for key, label in (("where the run starts", "run start moves the side"), ("Wall Filler", "run start"),
                       ("sets the top line", "top line"), ("counter", "stacked gap"),
                       ("with no base under it", "IC line and drawing disagree on the mount"),
                       ("drill codes", "geometry and IC drill codes disagree"),
                       ("decide by hand", "no section or wall on either side"), ("DEPTH row", "two depths on its lines"),
                       ("over 84 in", "FM 84 or FM 90"), ("gate 1", "gate 1, count"), ("gate 3", "gate 3, radius count"),
                       ("gate 4", "gate 4, heights"), ("off the 32k+20", "height off grid"),
                       ("isn't sure", "the reading's own doubt"), ("disagree on a hanging notch", "its lines disagree on the notch"),
                       ("no VT", "no IC line of that height"), ("no square VT", "no IC line of that height")):
        if key in why:
            return label
    return why[:60]


def report_frozen(results: Sequence[RoomResult]) -> str:
    lines = ["Jim's frozen jobs: each room's own panels stand in for the reading and its own chain for the plan;"
             " compared as his regression compare does (0.5 mm, 0.01 degree)."]
    for r in results:
        walls = ", ".join(f"{k} {v}" for k, v in sorted(r.wall_values.items()))
        t = r.model.top_line
        top = f"{t.value_mm:.1f}" if t.value_mm is not None else t.status
        lines.append(f"  {r.case} room {r.design + 1} ({r.room}): walls {walls or 'none'}; top line {top}")
        lines += [f"    wall difference: {d}" for d in r.walls]
        for w in r.panels:
            lines.append(f"    wall {w.wall_id}: {len(w.panels)} panels; right {w.count('right')}, wrong "
                         f"{w.count('wrong')}, could not verify {w.count('could not verify')}, missing "
                         f"{w.count('missing')}, extra {w.count('extra')}")
            for p in w.panels:
                if p.status != "right":
                    bad = ", ".join(f"{k} {v}" for k, v in p.values.items() if v not in ("right", "not needed"))
                    lines.append(f"      panel {'-' if p.index is None else p.index + 1} ({p.prod}): {p.status}"
                                 + (f": {bad}" if bad else ""))
                    lines += [f"        {k}: {v}" for k, v in p.why.items() if p.values.get(k) == "could not verify"]
            lines += [f"      wall gate: {g}" for g in w.gates]
    return "\n".join(lines)


def report_read(results: Sequence[RoomResult], title: str) -> str:
    walls = sorted((w for r in results for w in r.panels), key=lambda w: (w.wall_id.split("/")[0],
                                                                        int(w.wall_id.split("/")[1])))
    lines = [title, "  wall      categories                      panels  right  could not verify  wrong  missing  "
                    "extra  gates"]
    for w in walls:
        lines.append(f"  {w.wall_id:<9} {'+'.join(w.categories):<32}{len([p for p in w.panels if p.index is not None]):>6}"
                     f"{w.count('right'):>7}{w.count('could not verify'):>18}{w.count('wrong'):>7}{w.count('missing'):>9}"
                     f"{w.count('extra'):>7}  {'; '.join(reason(g) for g in w.gates) or '-'}")
    values: dict[str, Counter[str]] = defaultdict(Counter)
    for w in walls:
        for p in w.panels:
            for k, v in p.values.items():
                values[k][v] += 1
    total = sum(len([p for p in w.panels if p.index is not None]) for w in walls)
    lines.append(f"  In all: {total} as-cut panels; right {sum(w.count('right') for w in walls)}, could not verify "
                 f"{sum(w.count('could not verify') for w in walls)}, wrong {sum(w.count('wrong') for w in walls)}, "
                 f"missing {sum(w.count('missing') for w in walls)}, extra {sum(w.count('extra') for w in walls)}")
    lines.append("  Values (right / could not verify / wrong / not needed):")
    for k in VALUES:
        c = values[k]
        lines.append(f"    {k:<8} {c['right']:>4} / {c['could not verify']:>4} / {c['wrong']:>3} / {c['not needed']:>3}")
    lines.append("  Could not verify, values by reason: " + (", ".join(
        f"{k} {v}" for k, v in sorted(_reasons(walls).items())) or "none"))
    wrong = [(w.wall_id, p) for w in walls for p in w.panels if p.status == "wrong"]
    lines.append("  Wrong values: " + ("; ".join(
        f"{wid} panel {p.index + 1 if p.index is not None else '-'} "
        + ", ".join(k for k, v in p.values.items() if v == "wrong") for wid, p in wrong) or "none"))
    return "\n".join(lines)
