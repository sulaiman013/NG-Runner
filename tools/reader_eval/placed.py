"""
The reader re-scored with P5a's placement rules (ngrunner/build/placement.py), from cached answers only.

Why: most of the reader's silent misses were Jim's build choices, not reading (diagnose.py). This puts a
reading through the rules the builder will apply, in place of the drawing's raw positions wherever a rule
places a vertical, and scores the result against the same as-cut truth with the same tolerances, so the
two scores compare. Nothing here calls a model: the readings come from run.read_walls(call=False).

  a room     one design of the summary. Each of its crops gives its wall's IC parts lines, and each
             paired wall with an answer gives its drawn verticals. FM panels on its other walls (not
             paired, or not read) count for the top line as their IC heights standing on the floor
             (RULES 292), as a builder that read every wall would see them
  a vertical its IC facts come from its own wall's parts lines of the height the reader named: the
             height snapped to 32k+20, and FM or WM when those lines agree (else the rules leave it)
  the split  DEV is the first 26 walls (Jobs A to J), from which with Jim's docs the rules were
             decided; TEST is the 121 walls of the P5 jobs, reported apart

The frozen check runs the same rules on Jim's three frozen jobs, with their own as-cut panels standing in
for the drawing (no reader has read them: they are the worked examples), and says per job whether the
rules give back the frozen top lines, stacked uppers and run starts.
"""
from __future__ import annotations

import copy
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ngrunner.build import placement as pl
from tools.reader_eval import diagnose, scoring
from tools.reader_eval.pairing import Pair
from tools.reader_eval.prep import WallCrop
from tools.reader_eval.scoring import Reading, WallScore

MM = pl.MM
_WS = re.compile(r"^WS\b")
_SAME_MM = 0.5  # a placed value this close to the frozen one is the frozen one


# ----- a reading to drawn walls, and back


def _mounts(crop: WallCrop, height: str) -> set[pl.Mount]:
    return {pl.mount(p.desc) for p, m in crop.vt_lines if m["height"] == height}


def drawn_wall(crop: WallCrop, answer: Mapping[str, Any]) -> pl.DrawnWall:
    """The reader's verticals for one wall, in mm, with each one's IC height and mount."""
    out = []
    for v in answer["verticals"]:
        try:
            height = pl.snap_height_mm(float(v["height"]))
        except ValueError:
            height = None
        mounts = _mounts(crop, v["height"])
        out.append(pl.Drawn(v["x_in"] * MM, v["bottom_in"] * MM, v["top_in"] * MM, height,
                            mounts.pop() if len(mounts) == 1 else None))
    return pl.DrawnWall(tuple(out), any(_WS.match(p.desc) for p in crop.parts))


def floor_fm_tops(crop: WallCrop) -> list[float]:
    """The tops of the FM panels a wall's parts lines list, standing on the floor (RULES 292)."""
    tops = []
    for p, m in crop.vt_lines:
        height = pl.snap_height_mm(float(m["height"]))
        if pl.mount(p.desc) == "FM" and height is not None:
            tops += [height] * (p.qty or 0)
    return tops


def _answer(answer: Mapping[str, Any], wall: pl.WallPlacement) -> dict[str, Any]:
    out = copy.deepcopy(dict(answer))
    for v, placed in zip(out["verticals"], wall.verticals):
        if placed.by != "drawing":
            v["bottom_in"], v["top_in"] = placed.bottom_mm / MM, placed.top_mm / MM
    return out


@dataclass(frozen=True)
class Placed:
    """One read wall, before and after the rules."""
    pair: Pair
    before: Reading
    after: Reading
    wall: pl.WallPlacement | None  # None: no answer to place
    top_line: pl.Finding | None

    @property
    def wall_id(self) -> str:
        return f"{self.pair.case}/{self.pair.crop.page}"

    @property
    def dev(self) -> bool:
        return not self.pair.case.startswith("P")

    @property
    def could_not_verify(self) -> tuple[pl.Finding, ...]:
        return self.wall.could_not_verify if self.wall is not None else ()


def place_readings(readings: Sequence[tuple[Pair, Reading]],
                   crops: Mapping[str, Sequence[WallCrop]]) -> list[Placed]:
    """Every reading put through the placement rules, room by room (module notes)."""
    rooms: dict[tuple[str, int], list[tuple[Pair, Reading]]] = defaultdict(list)
    for pair, reading in readings:
        rooms[(pair.case, pair.crop.design)].append((pair, reading))
    done: dict[str, Placed] = {}
    for (case, design), members in rooms.items():
        read = [(p, r) for p, r in members if r.status == "answered" and scoring._valid(r.answer)]
        for p, r in members:
            if not (r.status == "answered" and scoring._valid(r.answer)):
                done[f"{p.case}/{p.crop.page}"] = Placed(p, r, r, None, None)
        pages = {p.crop.page for p, _ in read}
        others = [t for c in crops.get(case, ()) if c.design == design and c.page not in pages
                  for t in floor_fm_tops(c)]
        room = pl.place_room([drawn_wall(p.crop, r.answer) for p, r in read if r.answer is not None],
                             other_fm_tops_mm=others)
        for (p, r), wall in zip(read, room.walls):
            assert r.answer is not None
            after = Reading(r.status, _answer(r.answer, wall), r.cost_usd, r.detail, r.cached, r.latency_s)
            done[f"{p.case}/{p.crop.page}"] = Placed(p, r, after, wall, room.top_line)
    return [done[f"{p.case}/{p.crop.page}"] for p, _ in readings]


# ----- before and after


@dataclass
class Tally:
    walls: int = 0
    build_choice_before: int = 0  # silent wrong values diagnose.py puts down to a build choice
    build_choice_after: int = 0
    reading_before: int = 0  # silent wrong values it puts down to the reading
    reading_after: int = 0
    wrong_walls_before: int = 0  # walls with any wrong value, silent or flagged
    wrong_walls_after: int = 0
    newly_wrong_walls: list[str] = field(default_factory=list)
    newly_wrong_values: int = 0
    fixed_values: int = 0
    could_not_verify: int = 0  # walls where a rule said so
    silent_on_passed_after: int = 0  # silent errors left on walls no rule stopped: these would reach Jim unseen
    of_which_cut_off_grid: int = 0  # ... on the bottom or top of a cut WM panel whose Elev is off 32k (RULES 319)


def _scores(placed: Placed) -> tuple[WallScore, WallScore, diagnose.Diagnosis, diagnose.Diagnosis]:
    return (scoring.score_wall(placed.pair, placed.before), scoring.score_wall(placed.pair, placed.after),
            diagnose.diagnose(placed.pair, placed.before), diagnose.diagnose(placed.pair, placed.after))


def _cut_off_grid(pair: Pair, score: WallScore) -> int:
    """Silent wrong bottoms and tops whose cut panel is a WM panel with its Elev off the 32 mm grid: what
    RULES 319's gate 5 refuses today. With as many verticals as the truth, outcomes come per panel, in
    order, after the order value (scoring.score_wall)."""
    panels = pair.wall.panels
    if len(score.outcomes) != 1 + 5 * len(panels):
        return 0
    count = 0
    for i, panel in enumerate(panels):
        elev = panel.elev_mm % pl.GRID_MM
        off = not panel.prod.startswith("FM") and min(elev, pl.GRID_MM - elev) > pl.SNAP_TOL_MM
        values = score.outcomes[1 + 5 * i: 6 + 5 * i]
        count += off * sum(o.kind in ("bottom", "top") and o.status == "wrong_silent" for o in values)
    return count


def tally(placed: Iterable[Placed]) -> tuple[Tally, dict[str, Tally], Counter[str], Counter[str]]:
    """Pooled and per-category tallies, walls a rule changed by rule, and could-not-verify by rule."""
    pooled, cats = Tally(), defaultdict(Tally)
    changed: Counter[str] = Counter()
    unverified: Counter[str] = Counter()
    for p in placed:
        sb, sa, db, da = _scores(p)
        newly = sum(a.status.startswith("wrong") and not b.status.startswith("wrong")
                    for a, b in zip(sa.outcomes, sb.outcomes))
        fixed = sum(b.status.startswith("wrong") and not a.status.startswith("wrong")
                    for a, b in zip(sa.outcomes, sb.outcomes))
        if p.wall is not None:
            changed.update({v.by for v in p.wall.verticals if v.by != "drawing"})
            unverified.update({f.rule for f in p.could_not_verify})
        for t in [pooled, *(cats[c] for c in p.pair.wall.categories)]:
            t.walls += 1
            t.build_choice_before += db.silent_offset
            t.build_choice_after += da.silent_offset
            t.reading_before += db.silent_reading
            t.reading_after += da.silent_reading
            t.wrong_walls_before += sb.wrong > 0
            t.wrong_walls_after += sa.wrong > 0
            if sb.wrong == 0 and sa.wrong > 0:
                t.newly_wrong_walls.append(p.wall_id)
            t.newly_wrong_values += newly
            t.fixed_values += fixed
            t.could_not_verify += bool(p.could_not_verify)
            if not p.could_not_verify:
                t.silent_on_passed_after += sa.silent
                t.of_which_cut_off_grid += _cut_off_grid(p.pair, sa)
    return pooled, dict(cats), changed, unverified


def report(placed: Sequence[Placed]) -> str:
    """The before and after, DEV and TEST apart, per category: case tags only, no names."""
    lines = ["P5a placement rules on the cached readings (no model call). Silent values: build choice "
             "(diagnose.py's run start, top line, stacked gap) and reading; before -> after."]
    head = ("  {:<14} walls {:>3} | build choice {:>3} -> {:>3} | reading {:>2} -> {:>2} | wrong walls {:>3} -> {:>3}"
            " | newly wrong walls {:>2}, values {:>2}; fixed values {:>3} | could not verify {:>3} walls"
            " | silent left on walls passed {:>3}, {:>2} of them on cut WM panels off the 32 mm Elev grid")
    for name, group in (("DEV (Jobs A to J)", [p for p in placed if p.dev]),
                        ("TEST (P5 jobs)", [p for p in placed if not p.dev])):
        pooled, cats, changed, unverified = tally(group)
        lines.append(f"{name}:")
        for label, t in [("all", pooled), *sorted(cats.items())]:
            lines.append(head.format(label, t.walls, t.build_choice_before, t.build_choice_after, t.reading_before,
                                     t.reading_after, t.wrong_walls_before, t.wrong_walls_after,
                                     len(t.newly_wrong_walls), t.newly_wrong_values, t.fixed_values,
                                     t.could_not_verify, t.silent_on_passed_after, t.of_which_cut_off_grid))
        lines.append("  Verticals placed by a rule, walls: " + (", ".join(f"{k} {v}" for k, v in sorted(changed.items()))
                                                               or "none"))
        lines.append("  Could not verify, walls: " + (", ".join(f"{k} {v}" for k, v in sorted(unverified.items()))
                                                     or "none"))
        lines.append("  Newly wrong walls: " + (", ".join(pooled.newly_wrong_walls) or "none"))
    lines.append("Per wall, where anything changed or could not verify:")
    for p in placed:
        if p.wall is None:
            continue
        sb, sa, db, da = _scores(p)
        by = sorted({v.by for v in p.wall.verticals if v.by != "drawing"})
        cnv = sorted({f.rule for f in p.could_not_verify})
        if by or cnv or sb.wrong or sa.wrong:
            lines.append(f"  {p.wall_id}: {'+'.join(db.kinds)} -> {'+'.join(da.kinds)}; silent {sb.silent} -> "
                         f"{sa.silent}; placed by {', '.join(by) or 'nothing'}; could not verify "
                         f"{', '.join(cnv) or 'nothing'}")
    return "\n".join(lines)


# ----- Jim's frozen jobs


@dataclass(frozen=True)
class FrozenRoom:
    case: str
    design: int
    top_line: pl.Finding
    frozen_tops_mm: tuple[float, ...]  # the frozen WM panels' tops
    hung: tuple[bool, ...]  # per WM panel and FM upper: the rule placed it where it is frozen
    hung_unverified: int  # WM panels and FM uppers the rules left as drawn (could not verify)
    run_starts: tuple[str, ...]  # per wall: the run start's status


def _frozen_drawn(pair: Pair) -> pl.DrawnWall:
    """The frozen wall's own panels, standing in for the drawing, with their IC facts from its parts."""
    out = []
    for panel in pair.wall.panels:
        lines = [(p, m) for p, m in pair.crop.vt_lines
                 if (h := pl.snap_height_mm(float(m["height"]))) is not None and abs(h - panel.height_mm) <= _SAME_MM]
        mounts = {pl.mount(p.desc) for p, _ in lines}
        out.append(pl.Drawn(panel.x_mm, panel.elev_mm, panel.elev_mm + panel.height_mm,
                            panel.height_mm if lines else None, mounts.pop() if len(mounts) == 1 else None))
    return pl.DrawnWall(tuple(out), any(_WS.match(p.desc) for p in pair.crop.parts))


def frozen_check(pairs: Mapping[str, Sequence[Pair]], crops: Mapping[str, Sequence[WallCrop]],
                 jobs: Sequence[str] = ("X", "Y", "Z")) -> list[FrozenRoom]:
    found = []
    for case in jobs:
        by_design: dict[int, list[Pair]] = defaultdict(list)
        for p in pairs.get(case, ()):
            if p.wall.panels:
                by_design[p.crop.design].append(p)
        for design, members in sorted(by_design.items()):
            pages = {p.crop.page for p in members}
            others = [t for c in crops.get(case, ()) if c.design == design and c.page not in pages
                      for t in floor_fm_tops(c)]
            drawn = [_frozen_drawn(p) for p in members]
            room = pl.place_room(drawn, other_fm_tops_mm=others)
            hung, unverified, tops = [], 0, []
            for wall, placed in zip(drawn, room.walls):
                for d, v in zip(wall.verticals, placed.verticals):
                    off_floor = d.mount == "WM" or (d.mount == "FM" and d.bottom_mm > pl.OFF_FLOOR_MM)
                    if d.mount == "WM":
                        tops.append(d.top_mm)
                    if not off_floor:
                        continue
                    if v.by == "drawing":
                        unverified += 1
                    else:
                        hung.append(abs(v.bottom_mm - d.bottom_mm) <= _SAME_MM and abs(v.top_mm - d.top_mm) <= _SAME_MM)
            found.append(FrozenRoom(case, design, room.top_line, tuple(sorted(set(round(t, 1) for t in tops))),
                                    tuple(hung), unverified,
                                    tuple(next(f.status for f in w.findings if f.rule == "run start")
                                          for w in room.walls)))
    return found


def frozen_report(rooms: Sequence[FrozenRoom]) -> str:
    lines = ["Jim's frozen jobs, their own panels standing in for the drawing:"]
    for r in rooms:
        t = r.top_line
        frozen = ", ".join(f"{v:.1f}" for v in r.frozen_tops_mm) or "no WM panel"
        rule = f"{t.value_mm:.1f}" if t.value_mm is not None else t.status
        lines.append(f"  {r.case} room {r.design + 1}: top line by the rules {rule}, frozen {frozen}; "
                     f"panels off the floor placed where frozen {sum(r.hung)} of {len(r.hung)}, left as drawn "
                     f"(could not verify) {r.hung_unverified}; run starts: {', '.join(r.run_starts)}")
    return "\n".join(lines)
