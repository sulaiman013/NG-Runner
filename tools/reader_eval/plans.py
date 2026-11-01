"""
The plan reader (ngrunner/build/plan.py) scored on every real room: each design's plan read against its
WALL/AREA table and compared with the room Jim built from it, wall by wall in file order, as his regression
compare does (regression.py 188-220: length within 0.5 mm, angle within 0.01 degree). Nothing here calls a
model, and nothing is written but the report.

  sets      frozen   X, Y and Z, Jim's three regression jobs
            A-J      Jobs A to J (.local-fixtures\\p1b's case key)
            P5       the P5 jobs (.local-fixtures\\reader\\p5jobs)
            answers  the six job folders of .local-fixtures\\answers20261031, tagged S1 to S6 in name order: a
                     summary (ng_jobs\\*_summary.pdf) is paired with the folder its own PROJECT NAME names
                     (one is filed in another job's folder, Jim's answer to Q29), and one that is a P5 job's
                     own summary, byte for byte, is counted there only
  the room  the one the design was built as, by pairing.py's rules (its name, part of its name, or the only
            design and the only room). None: "no room"
  verdict   as cut differs   the room's walls are not the table's (each length within 0.07 in, as pairing.py
                             pairs a wall): Jim built other walls than the design's, so it can't score a chain
            could not verify the reader's own reason
            right            in file order, every wall's length (so the chain's order), angle within 0.01
                             degree and start relative to the first wall's within 0.5 mm; and its number, where
                             the room keeps IC's numbers (Jim renumbers some: "renumbered")
            turned           all of that once the whole room is turned by one angle: Jim built it turned from
                             the plan as drawn, which no part of the room changes
            wrong            anything else
"""
from __future__ import annotations

import hashlib
import math
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import SimpleNamespace

from ngrunner.build.plan import PlanReading, read_plan
from ngrunner.build.room import TableRow
from ngrunner.build.walls import chain, table_rows
from ngrunner.intake.summary_check import check_summary
from tools.reader_eval import cases, pairing
from tools.reader_eval.prep import name_key, name_key_hash, project_name
from tools.reader_eval.truth import RoomTruth, read_room

MM = 25.4
JIM_MM = 0.5  # regression.py compare: positions and sizes within 0.5 mm
JIM_ANG = 0.01  # ... and angles within 0.01 degree
SETS = ("frozen", "A-J", "P5", "answers")


@dataclass(frozen=True)
class Design:
    name: str  # in memory only, never printed
    page: int  # its WALL/AREA page, 1-based
    rows: tuple[TableRow, ...]


@dataclass(frozen=True)
class PlanResult:
    set: str
    case: str
    design: int  # the design's order in its summary, from 0
    reading: PlanReading
    verdict: str  # as the module notes say
    detail: str = ""


def designs(summary: bytes) -> list[Design]:
    """Each design of a summary as NG Runner's summary check reads it: its name, WALL/AREA page and table."""
    return [Design(d["name"], d["wall_area_pages"][0] if d["wall_area_pages"] else 0, table_rows(d["walls"]))
            for d in check_summary(summary).details.get("designs", [])]


def room_of(design: str, names: Sequence[str], rooms: Mapping[str, RoomTruth]) -> str | None:
    """The room the design was built as, by pairing.py's own rules for a crop's design (its module notes)."""
    stand_in = SimpleNamespace(design_key=name_key_hash(design), design_name_key=name_key(design),
                               summary_designs=tuple(name_key(n) for n in names))
    found = pairing._room(stand_in, rooms)  # type: ignore[arg-type]
    return found[0] if isinstance(found, tuple) else None


def _turn(a: float, b: float) -> float:
    return (b - a + 180) % 360 - 180


def score(rows: Sequence[TableRow], reading: PlanReading, room: RoomTruth | None) -> tuple[str, str]:
    """The verdict for one design's reading against its as-cut room (module notes), and what differs."""
    if room is None:
        return "no room", ""
    table, cut = sorted(r.width_in for r in rows), sorted(w.length_in for w in room.walls)
    if len(table) != len(cut) or any(abs(a - b) > pairing.LENGTH_TOL_IN for a, b in zip(table, cut)):
        return "as cut differs", ""
    if reading.plan is None:
        return "could not verify", reading.why
    width = {r.name: r.width_in for r in rows}
    order, angs = reading.plan.order, reading.plan.angles
    if any(abs(width[n] - w.length_in) * MM > JIM_MM for n, w in zip(order, room.walls)):
        return "wrong", f"order {list(order)}, as cut {[w.number for w in room.walls]}"
    as_ic = all(w.number in width and abs(width[w.number] - w.length_in) <= pairing.LENGTH_TOL_IN for w in room.walls)
    if as_ic and any(n != w.number for n, w in zip(order, room.walls)):
        return "wrong", f"numbers {list(order)}, as cut {[w.number for w in room.walls]}"
    turn = _turn(angs[0], room.walls[0].ang)
    if any(abs(_turn(a + turn, w.ang)) > JIM_ANG for a, w in zip(angs, room.walls)):
        return "wrong", f"angles {list(angs)}, as cut {[w.ang for w in room.walls]}"
    starts = chain([round(width[n] * MM, 4) for n in order], angs)
    t = math.radians(turn)
    first = room.walls[0].pos_mm
    for (x, y), w in zip(starts, room.walls):
        ours = (x * math.cos(t) - y * math.sin(t), x * math.sin(t) + y * math.cos(t))
        if abs(ours[0] - (w.pos_mm[0] - first[0])) > JIM_MM or abs(ours[1] - (w.pos_mm[1] - first[1])) > JIM_MM:
            return "wrong", f"wall {w.position} starts elsewhere"
    note = "" if as_ic else "renumbered"
    if abs(turn) <= JIM_ANG:
        return "right", note
    return "turned", ", ".join(x for x in (f"turned {turn % 360:g} degrees", note) if x)


# ----- the material


def _answers(env: Mapping[str, str]) -> list[tuple[str, bytes, dict[str, RoomTruth]]]:
    """The answers set's summaries, each with the rooms of the folder its PROJECT NAME names (module notes)."""
    base = cases.fixtures(env) / "answers20261031"
    if not base.is_dir():
        return []
    folders = sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_"))
    tags = {p.name: f"S{k}" for k, p in enumerate(folders, 1)}
    out = []
    for pdf in sorted(base.glob("*/ng_jobs/*_summary.pdf")):
        data = pdf.read_bytes()
        home = [p for p in folders if name_key(p.name) == name_key(project_name(data) or "")]
        if len(home) != 1:
            continue
        rooms = {p.stem: read_room(p.read_bytes()) for p in sorted((home[0] / "mozaik").glob("Room*.des"))
                 if p.stem != "Room0"}
        out.append((tags[home[0].name], data, rooms))
    return out


def material(env: Mapping[str, str] = os.environ) -> list[tuple[str, str, bytes, dict[str, RoomTruth]]]:
    """(set, case, summary, rooms) for every collected case and the answers set (module notes)."""
    p5 = set(cases.test_cases(env)) - set(cases.TEST_CASES)
    out = []
    seen: set[str] = set()
    for case in cases.collected(env):
        data = cases.summary(case, env)
        seen.add(hashlib.sha256(data).hexdigest())
        kind = "frozen" if case in cases.WORKED_CASES else "P5" if case in p5 else "A-J"
        out.append((kind, case, data, {stem: read_room(raw) for stem, raw in cases.rooms(case, env).items()}))
    out += [("answers", tag, data, rooms) for tag, data, rooms in _answers(env)
            if hashlib.sha256(data).hexdigest() not in seen]
    return out


def score_all(env: Mapping[str, str] = os.environ) -> list[PlanResult]:
    results = []
    for kind, case, data, rooms in material(env):
        found = designs(data)
        names = [d.name for d in found]
        for k, d in enumerate(found):
            reading = read_plan(data, d.page, d.rows)
            stem = room_of(d.name, names, rooms)
            verdict, detail = score(d.rows, reading, rooms[stem] if stem else None)
            results.append(PlanResult(kind, case, k, reading, verdict, detail))
    return results


# ----- the report


def reason(why: str) -> str:
    """The kind of could-not-verify, in a few words, for counting."""
    for key, label in (("isn't a right angle", "a wall drawn at an angle"),
                       ("could not be read", "a contested wall's label unreadable"),
                       ("too near each other's walls", "labels too near to settle equal walls"),
                       ("reads twice", "a label read twice"), ("separate runs", "several runs"),
                       ("wall strips where", "strips not the table's walls"),
                       ("outlined walls", "strips not the table's walls"),
                       ("square stub", "a square stub's direction"), ("three walls", "three walls at one end"),
                       ("another wall's side", "a wall meeting another's side"),
                       ("don't match the WALL/AREA", "lengths not the table's"), ("up the page", "one wall up the page"),
                       ("neither way clockwise", "not clockwise")):
        if key in why:
            return label
    return why[:60]


def report(results: Sequence[PlanResult]) -> str:
    lines = ["The plan reader on every real room, compared as Jim's regression compare does (0.5 mm, 0.01 degree).",
             "  set      designs  no room  as cut differs (read)  scored  right  turned  could not verify  wrong"]
    for kind in SETS:
        rs = [r for r in results if r.set == kind]
        if not rs:
            continue
        c = Counter(r.verdict for r in rs)
        differs_read = sum(r.verdict == "as cut differs" and r.reading.read for r in rs)
        scored = c["right"] + c["turned"] + c["could not verify"] + c["wrong"]
        lines.append(f"  {kind:<8} {len(rs):>7}  {c['no room']:>7}  {c['as cut differs']:>9} ({differs_read:>2})    "
                     f"{scored:>6}  {c['right']:>5}  {c['turned']:>6}  {c['could not verify']:>16}  {c['wrong']:>5}")
    lines.append("  Could not verify, by reason: " + (", ".join(
        f"{k} {v}" for k, v in sorted(Counter(reason(r.detail) for r in results
                                               if r.verdict == "could not verify").items())) or "none"))
    for r in results:
        if r.verdict != "right" and r.verdict not in ("no room", "as cut differs"):
            lines.append(f"  {r.case} design {r.design + 1}: {r.verdict}" + (f": {r.detail}" if r.detail else ""))
    renumbered = [f"{r.case} design {r.design + 1}" for r in results if "renumbered" in r.detail]
    lines.append("  Rooms Jim renumbered (compared by length, not number): " + (", ".join(renumbered) or "none"))
    return "\n".join(lines)


def answers_names(env: Mapping[str, str] = os.environ) -> list[str]:
    """The answers set's job names, for the report's own leak check only: never printed."""
    base = cases.fixtures(env) / "answers20261031"
    return [p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")] if base.is_dir() else []

