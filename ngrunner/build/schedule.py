"""
A room's per-wall panel schedule, and the room itself: walls from the WALL/AREA table (walls.py), and
each wall's verticals from a reading of its drawing, its own IC parts lines and Jim's rules.

Why: RULES 285-322, "The schedule is read PER PANEL": build an explicit per-wall schedule, one row per
vertical with its own mount, height, Elev, variant and radius, before placing anything. The drawing gives
the order and the parts lines the pieces (BUILD 237-239), and placement.py the build choices a drawing
doesn't show. Each value is a Fact (room.py); where Jim's rules or the inputs don't settle one it could not
verify, with the reason, never a guess. Ported from Jim's toolkit of 2026-10-31, never copied:

  its IC lines  the vertical's own: its wall's VT lines (VT, VT RAD, Custom Vert) of the height the reader
                named, VT RAD when it saw a radius and the others when it saw none
  height        RULES 291: the IC height snapped to 32k+20 (placement.snap_height_mm; RULES 318)
  mount         RULES 290, 324-326: FN, LN or RN on its lines hangs it (placement.mount); lines that
                disagree could not verify, and so does a line with no notch whose vertical is drawn above the
                floor with no base under it (the line says floor, the drawing says hung)
  depth         its lines' one depth, snapped to SCHEMA's grid within 1.3 mm, else kept IC-exact and
                flagged Review (SCHEMA 161-166, BUILD 286). Lines of two depths could not verify: the
                DEPTH row says which (RULES 304-306, BUILD 279-280), and the reading doesn't give it
  REA           RULES 411-413: REA on its lines
  radius        RULES 294: from a VT RAD line, never the drawing alone. The reader's yes or no says which
                vertical, held to the wall's VT RAD count (gate 3, RULES 317)
  x             the reading's left face, rounded to 3 places (BUILD 283-284); could not verify where the
                run start does (placement.run_start)
  Elev          placement: an FM panel on the floor 0 (RULES 292, BUILD 243), a WM panel T - height on
                the room's one top line (RULES 329-339), an FM upper on the counter (placement.fm_upper)
  stacked       the reading: another vertical within 1/2 in of its x, one above the other (RULES 310-311)
  side          Thru / Left / Right, the notch (RULES 351-372, BUILD 245-248). A face is covered by a wall
                within 6.5 in or a section within 7 in. Jim inserts the sections by hand after the
                verticals (RULES 459-460), so here a section is a bay: the gap from the face to the nearest
                vertical beside it (one overlapping it by more than 1 mm, swap_rea.py 39) is a section,
                touching the face, when its width is an IC shelf width of the wall within 1.3 mm (BUILD
                274-277, 296). So a section seen past another panel never covers a face. Covered both
                sides: Thru; open left: Left; open right: Right; open both could not verify (swap_rea.py
                54: "decide by hand"). A plain FM panel has no notch (RULES 397-398): not needed. Where the
                run start could not verify, each place Jim's builds put such a run (as drawn, tight,
                centred) is tried, and a side they don't all give could not verify. The drill codes of its
                lines (FD Thru, LH Left, RH Right; RULES 371-372) must give the same sides as a set, height
                by height; where they don't, RULES 372 says the plan wins and RULES 62 ranks IC codes above
                geometry inference, so Jim's docs disagree and it could not verify
  product       FM 84 (BUILD 243, RULES 397-398); FM 84 REA <side> (RULES 415-417); WM Panel 48 <side>,
                Radius after it for a radius (RULES 377-387); WM 48 REA <side>, the same (RULES 415-416). A
                floor panel over 84 in could not verify: Jim's docs name only FM 84, and in the real rooms
                here his builds put 54 such panels on FM 84 and 32 on FM 90, at the same heights
  the gates     per wall (RULES 313-318): the verticals read are the wall's VT quantities (1) and its VT
                heights as a set (4, which gate 6 needs to give each vertical its own line); a failed 1 or
                4, or a reading unsure of the count, means no vertical's own line can be told, so every
                value from the lines could not verify. Gate 2 (FM and WM counts) then holds by construction,
                each vertical's mount being its own lines'; gate 3 (VT RAD count) settles every radius on the
                wall or none; gate 5 is placement's top line
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from ngrunner.build import placement as pl
from ngrunner.build.placement import MM, Finding
from ngrunner.build.room import (PANEL_WIDTH_MM, Fact, ICLine, Mount, PanelSpec, Plan, ReadVertical, ReadWall,
                                 RoomModel, Side, Stacked, TableRow, WallSchedule, given, not_needed, review,
                                 unverified)
from ngrunner.build.walls import build_walls

_VT = re.compile(r"^(?P<kind>VT RAD|VT|Custom Vert)\b.*?\b(?P<depth>\d+(?:\.\d+)?)x(?P<height>\d+(?:\.\d+)?)x")
_SH = re.compile(r"^SH\b.*?\b\d+(?:\.\d+)?x(?P<width>\d+(?:\.\d+)?)x")
_REA = re.compile(r"\bREA\b")
_WS = re.compile(r"^WS\b")
_NO_HEIGHT = ("unclear", "none of these")

DEPTH_GRID_MM = (254.0, 292.0, 302.0, 356.0, 406.5, 484.0, 558.8, 606.0, 762.0)  # SCHEMA 165
DEPTH_TOL_MM = 1.3  # SCHEMA 166, BUILD 286
WALL_NEAR_MM = 165.1  # RULES 361-362: a wall within 6.5 in covers a face
SECTION_TOL_MM = 1.3  # BUILD 296: every section within 1.3 mm of its IC shelf width
OVERLAP_MM = 1.0  # swap_rea.py 39: a product beside a panel overlaps it by more than 1 mm
BESIDE_MM = 0.5  # a neighbour's face this far into the panel still counts as beside it
STACK_X_MM = 0.5 * MM  # a stacked tower's two verticals stand within 1/2 in of one x
DRILL_SIDE: dict[str, Side] = {"FD": "Thru", "LH": "Left", "RH": "Right"}  # RULES 371-372, SCHEMA 152-154
EXISTS = "the reading isn't sure this vertical is there"
FM84_MM = 2132.0  # the FM 84 template's own height (BUILD 243, build_verticals.py 7)
TALL_FM = ("a floor panel over 84 in: Jim's docs name only FM 84 (BUILD 243), and his builds put such a panel on FM 84 "
           "in some jobs and FM 90 in others, so which product is his call")

Line = tuple[ICLine, re.Match[str]]
Span = tuple[float, float, float, float]  # x0, x1, bottom, top, mm


# ----- reading the parts lines


def vt_lines(lines: Sequence[ICLine]) -> list[Line]:
    """The wall's vertical lines: VT, VT RAD and Custom Vert, with their depth and height as printed."""
    return [(line, m) for line in lines if (m := _VT.match(line.desc))]


def shelf_widths_mm(lines: Sequence[ICLine]) -> list[float]:
    """Each SH line's width (its second size), mm: what a section's clear width is (BUILD 274-277)."""
    return [float(m["width"]) * MM for line in lines if (m := _SH.match(line.desc))]


def drill(description: str) -> str:
    """FD, LH or RH: the drill code on a VT line; none printed is FD (RULES 355-359)."""
    tokens = description.upper().split()
    return "LH" if "LH" in tokens else "RH" if "RH" in tokens else "FD"


def snap_depth(depth_in: float) -> Fact[float]:
    """A VT depth on SCHEMA's grid within 1.3 mm, else IC-exact and flagged Review (SCHEMA 165-166)."""
    mm = depth_in * MM
    near = min(DEPTH_GRID_MM, key=lambda g: abs(g - mm))
    if abs(near - mm) <= DEPTH_TOL_MM:
        return given(near, "IC line", f"its line's {depth_in:g} in, on the depth grid (SCHEMA 165-166)")
    return review(round(mm, 3), "IC line", f"its line's {depth_in:g} in is off the depth grid: kept IC-exact, "
                                           "flagged Review (SCHEMA 166, BUILD 286)")


def own_lines(v: ReadVertical, vts: Sequence[Line]) -> tuple[list[Line], str | None]:
    """The vertical's own IC lines, by the height the reader named and its radius; or why there are none."""
    if v.height in _NO_HEIGHT or "height" in v.unsure:
        return [], "the reading named no VT height for it"
    same = [(line, m) for line, m in vts if m["height"] == v.height]
    if not same:
        return [], f"its wall has no VT line {v.height} in high"
    if v.radius not in ("yes", "no") or "radius" in v.unsure:
        return same, None
    rad = v.radius == "yes"
    own = [(line, m) for line, m in same if (m["kind"] == "VT RAD") == rad]
    if not own:
        return [], f"its wall has no {'VT RAD' if rad else 'square VT'} line {v.height} in high"
    return own, None


def _one[T](values: set[T]) -> T | None:
    return next(iter(values)) if len(values) == 1 else None


# ----- the notch side (RULES 351-372)


def faces(spans: Sequence[Span], i: int, wall_len_mm: float, shelf_widths: Sequence[float]) -> tuple[bool, bool]:
    """Whether vertical i's left and right faces are covered (module notes, side)."""
    x0, x1, z0, z1 = spans[i]
    beside = [s for k, s in enumerate(spans) if k != i and min(z1, s[3]) - max(z0, s[2]) > OVERLAP_MM]
    lefts = [s[1] for s in beside if s[1] <= x0 + BESIDE_MM]
    rights = [s[0] for s in beside if s[0] >= x1 - BESIDE_MM]

    def section(gap: float) -> bool:
        return any(abs(gap - w) <= SECTION_TOL_MM for w in shelf_widths)

    left = x0 <= WALL_NEAR_MM or (bool(lefts) and section(x0 - max(lefts)))
    right = wall_len_mm - x1 <= WALL_NEAR_MM or (bool(rights) and section(min(rights) - x1))
    return left, right


def side_of(left: bool, right: bool) -> Side | None:
    """RULES 364-368: covered both sides Thru, open left Left, open right Right; open both: None."""
    return "Thru" if left and right else "Left" if right else "Right" if left else None


def product(mount: Mount, rea: bool, radius: bool, side: Side | None) -> str | None:
    """The library product for a vertical (module notes); None where Jim's library has none."""
    if mount == "FM":
        if radius:
            return None  # every radius product is a WM panel (RULES 377-387, 415-416)
        if not rea:
            return "FM 84"
        return f"FM 84 REA {side}" if side else None
    if side is None:
        return None
    return f"{'WM 48 REA' if rea else 'WM Panel 48'} {side}" + (" Radius" if radius else "")


# ----- one wall, before the room's placement


@dataclass
class _Vertical:
    read: ReadVertical
    lines: list[Line]
    height: Fact[float]
    mount: Fact[Mount]
    depth: Fact[float]
    rea: Fact[bool]
    radius: Fact[bool]


@dataclass
class _Wall:
    read: ReadWall
    verticals: list[_Vertical]
    findings: list[Finding]
    placeable: bool  # every vertical has an x, a bottom and a top


def _gates(wall: ReadWall, vts: Sequence[Line]) -> tuple[str | None, str | None]:
    """Why no vertical's own line can be told (gates 1 and 4), and why no radius can (gate 3); None: fine."""
    vs = wall.verticals or ()
    lines_why: str | None = None
    if wall.count_unsure or wall.unreadable:
        lines_why = "the reading isn't sure how many verticals the wall has"
    elif any(line.qty is None for line, _ in vts):
        lines_why = "a VT line's quantity is unreadable, so gate 1 can't be checked (RULES 315)"
    elif len(vs) != (listed := sum(line.qty or 0 for line, _ in vts)):
        lines_why = f"gate 1: {len(vs)} verticals read, and the wall's VT lines list {listed} (RULES 315)"
    else:
        known = Counter(v.height for v in vs if v.height not in _NO_HEIGHT and "height" not in v.unsure)
        if known - Counter(m["height"] for line, m in vts for _ in range(line.qty or 0)):
            lines_why = ("gate 4: the heights read are not the wall's VT heights, so no vertical's own line can be "
                         "told (RULES 315-320)")
    radius_why: str | None = None
    if any(v.radius not in ("yes", "no") or "radius" in v.unsure for v in vs):
        radius_why = "the reading isn't sure which vertical is a radius, so gate 3 can't be checked (RULES 317)"
    elif any(line.qty is None for line, m in vts if m["kind"] == "VT RAD"):
        radius_why = "a VT RAD line's quantity is unreadable, so gate 3 can't be checked (RULES 317)"
    else:
        seen = sum(v.radius == "yes" for v in vs)
        listed = sum(line.qty or 0 for line, m in vts if m["kind"] == "VT RAD")
        if seen != listed:
            radius_why = (f"gate 3: the reading sees {seen} radius verticals, and the wall's VT RAD lines list "
                          f"{listed} (RULES 317)")
    return lines_why, radius_why


def _vertical(v: ReadVertical, vts: Sequence[Line], lines_why: str | None, radius_why: str | None) -> _Vertical:
    if "exists" in v.unsure:
        return _Vertical(v, [], unverified(EXISTS), unverified(EXISTS), unverified(EXISTS), unverified(EXISTS),
                         unverified(EXISTS))
    if radius_why is not None:
        radius: Fact[bool] = unverified(radius_why)
    else:
        radius = given(v.radius == "yes", "reading", "the drawing says which vertical, the wall's VT RAD lines "
                                                     "that one is there (RULES 294, gate 3)")
    lines, why = ([], lines_why) if lines_why else own_lines(v, vts)
    if why is not None:
        return _Vertical(v, [], unverified(why), unverified(why), unverified(why), unverified(why), radius)
    snapped = pl.snap_height_mm(float(v.height))
    height: Fact[float] = (given(snapped, "IC line", "its IC height on the 32k+20 grid (RULES 291)")
                           if snapped is not None else
                           unverified("its IC height is more than 1.3 mm off the 32k+20 grid (RULES 318)"))
    mounts = {pl.mount(line.desc) for line, _ in lines}
    mount: Fact[Mount] = unverified("its IC lines disagree on a hanging notch (RULES 290)")
    if (one := _one(mounts)) is not None:
        mount = given(one, "IC line", "FN, LN or RN on its line hangs it (RULES 290, 325-326)" if one == "WM"
                      else "no hanging notch on its line: it stands on the floor (RULES 290, 326)")
    depths = {float(m["depth"]) for _, m in lines}
    depth: Fact[float] = (snap_depth(depths.pop()) if len(depths) == 1 else unverified(
        f"its IC lines give depths {', '.join(f'{d:g}' for d in sorted(depths))} in: the DEPTH row says which "
        "(RULES 304-306), and the reading doesn't give it"))
    reas = {bool(_REA.search(line.desc)) for line, _ in lines}
    rea: Fact[bool] = unverified("its IC lines disagree on REA (RULES 411-413)")
    if (r := _one(reas)) is not None:
        rea = given(r, "IC line", "REA on its line (RULES 411-413)" if r else "no REA on its line")
    return _Vertical(v, lines, height, mount, depth, rea, radius)


def _prepare(wall: ReadWall) -> _Wall:
    vts = vt_lines(wall.lines)
    lines_why, radius_why = _gates(wall, vts)
    findings = [Finding("IC lines", "could not verify", None, lines_why)] if lines_why else []
    if radius_why:
        findings.append(Finding("radius count", "could not verify", None, radius_why))
    verticals = [_vertical(v, vts, lines_why, radius_why) for v in wall.verticals or ()]
    placeable = all(None not in (v.x_in, v.bottom_in, v.top_in) for v in wall.verticals or ())
    return _Wall(wall, verticals, findings, placeable)


def _hung_without_notch(w: _Wall) -> None:
    """An FM line's vertical drawn above the floor with no base under it: the IC line says it stands on the
    floor (RULES 290, 326) and the drawing says it hangs, so its mount could not verify, and it doesn't
    count as an FM panel for the room's top line."""
    drawn = _drawn(w)
    for v, d in zip(w.verticals, drawn.verticals):
        if (v.mount.settled and v.mount.value == "FM" and d.bottom_mm > pl.OFF_FLOOR_MM
                and "bottom_in" not in v.read.unsure and not pl.bases_under(d, drawn)):
            v.mount = unverified(f"its IC line has no hanging notch, so it stands on the floor (RULES 290, 326), but "
                                 f"it is drawn {d.bottom_mm / MM:.2f} in up with no base under it: the line and the "
                                 "drawing disagree")


def _drawn(w: _Wall) -> pl.DrawnWall:
    out = []
    for v in w.verticals:
        r = v.read
        assert r.x_in is not None and r.bottom_in is not None and r.top_in is not None
        out.append(pl.Drawn(r.x_in * MM, r.bottom_in * MM, r.top_in * MM,
                            v.height.value if v.height.settled else None,
                            v.mount.value if v.mount.settled else None))
    return pl.DrawnWall(tuple(out), any(_WS.match(line.desc) for line in w.read.lines))


def floor_fm_tops(lines: Sequence[ICLine]) -> list[float]:
    """The tops of the FM panels a wall's lines list, standing on the floor (RULES 292): for the top line
    when the wall wasn't read. A printed line is one panel at least, whatever its quantity reads."""
    tops = []
    for line, m in vt_lines(lines):
        height = pl.snap_height_mm(float(m["height"]))
        if pl.mount(line.desc) == "FM" and height is not None:
            tops.append(height)
    return tops


# ----- one wall, placed


def _elev(v: _Vertical, d: pl.Drawn, placed: pl.Placed, wall: pl.DrawnWall, t: Finding) -> Fact[float]:
    if "exists" in v.read.unsure:
        return unverified(EXISTS)
    for fact in (v.mount, v.height):
        if not fact.settled:
            return unverified(fact.why)
    if v.mount.value == "WM":
        if placed.by == "top line" and t.value_mm is not None:
            return given(placed.bottom_mm, "rule", f"the room's top line {t.value_mm / MM:.2f} in less its height "
                                                   f"(RULES 330): {t.why}")
        return unverified(t.why)
    if "bottom_in" in v.read.unsure:
        return unverified("the reading isn't sure where it starts, so on the floor or on the counter can't be told")
    if d.bottom_mm <= pl.OFF_FLOOR_MM:
        return given(0.0, "rule", "a floor panel: Elev 0 (RULES 292, BUILD 243)")
    found = pl.fm_upper(d, wall)
    if placed.by == "stacked gap" and found.value_mm is not None:
        return given(found.value_mm, "rule", found.why)
    return unverified(found.why)


def _stacked(i: int, read: Sequence[ReadVertical]) -> Fact[Stacked]:
    me = read[i]
    if me.x_in is None or me.bottom_in is None or me.top_in is None:
        return unverified("the reading gave no position for it")
    above = below = False
    for k, o in enumerate(read):
        if k == i or o.x_in is None or o.bottom_in is None or o.top_in is None:
            continue
        if abs(o.x_in - me.x_in) * MM <= STACK_X_MM:
            above |= o.bottom_in >= me.top_in
            below |= o.top_in <= me.bottom_in
    value: Stacked = "lower" if above else "upper" if below else "no"
    return given(value, "reading", "another vertical within 1/2 in of its x, one above the other (RULES 310-311)"
                 if value != "no" else "no other vertical above or below it")


def _spans(w: _Wall, elevs: Sequence[Fact[float]]) -> list[Span]:
    out = []
    for v, e in zip(w.verticals, elevs):
        r = v.read
        assert r.x_in is not None and r.bottom_in is not None and r.top_in is not None
        x0 = r.x_in * MM
        if e.value is not None and v.height.value is not None:
            z0, z1 = e.value, e.value + v.height.value
        else:
            z0, z1 = r.bottom_in * MM, r.top_in * MM
        out.append((x0, x0 + PANEL_WIDTH_MM, z0, z1))
    return out


def _shifts(spans: Sequence[Span], wall_len_mm: float, start: Finding) -> list[float]:
    """Where Jim's builds put a run (placement.run_start): as drawn only, when the run start is settled."""
    if start.status != "could not verify":
        return [0.0]
    first, end_gap = min(s[0] for s in spans), wall_len_mm - max(s[1] for s in spans)
    return [0.0, -first, (end_gap - first) / 2]


def _sides(w: _Wall, spans: Sequence[Span], wall_len_mm: float | None, start: Finding) -> list[Fact[Side]]:
    """Each vertical's side by geometry, held to its lines' drill codes height by height (module notes)."""
    widths = shelf_widths_mm(w.read.lines)
    out: list[Fact[Side]] = []
    geo: list[Side | None] = []
    for i, v in enumerate(w.verticals):
        geo.append(None)
        if "exists" in v.read.unsure:
            out.append(unverified(EXISTS))
            continue
        blocked = next((f for f in (v.mount, v.rea) if not f.settled), None)
        if blocked is not None:
            out.append(unverified(blocked.why))
            continue
        if v.mount.value == "FM" and not v.rea.value:
            out.append(not_needed("a floor panel has no notch: LH or RH on it is Mozaik's holes (RULES 397-398)"))
            continue
        if wall_len_mm is None:
            out.append(unverified("its wall isn't in the WALL/AREA table, so how near the wall's ends are "
                                  "can't be told"))
            continue
        found = {side_of(*faces([(a + s, b + s, c, d) for a, b, c, d in spans], i, wall_len_mm, widths))
                 for s in _shifts(spans, wall_len_mm, start)}
        if len(found) > 1:
            out.append(unverified(f"its side depends on where the run starts ({start.why})"))
        elif None in found:
            out.append(unverified("no section beside it and no wall within 6.5 in on either side, so which face is "
                                  "finished is Jim's call (swap_rea.py 54: decide by hand)"))
        else:
            geo[i] = found.pop()
            out.append(given(geo[i], "rule", "by geometry: a section in the bay or a wall within 6.5 in covers a face "
                                             "(RULES 361-368)"))
    groups: dict[tuple[str, str], list[int]] = {}
    for i, v in enumerate(w.verticals):
        if out[i].status != "not needed" and v.lines:
            groups.setdefault((v.read.height, v.read.radius), []).append(i)
    for members in groups.values():
        lines = w.verticals[members[0]].lines
        if any(line.qty is None for line, _ in lines):
            ic: Counter[Side] | None = None
        else:
            ic = Counter(DRILL_SIDE[drill(line.desc)] for line, _ in lines for _ in range(line.qty or 0))
        settled = Counter(s for i in members if (s := geo[i]) is not None)
        if ic is not None and not settled - ic and len(members) <= ic.total():
            for i in members:
                if geo[i] is not None:
                    out[i] = given(geo[i], "rule", out[i].why + "; its IC lines' drill codes agree (RULES 371-372)")
            continue
        said = ", ".join(sorted(f"{k} {n}" for k, n in settled.items())) or "none"
        codes = "unreadable" if ic is None else ", ".join(sorted(f"{k} {n}" for k, n in ic.items()))
        for i in members:
            if geo[i] is not None:
                out[i] = unverified(f"by geometry {said}, by its IC lines' drill codes {codes}: RULES 372 says the "
                                    "plan wins, RULES 62 ranks IC codes above geometry inference, and the sections "
                                    "the geometry reads are not placed yet")
    return out


def _product(v: _Vertical, side: Fact[Side]) -> Fact[str]:
    if "exists" in v.read.unsure:
        return unverified(EXISTS)
    for fact in (v.mount, v.rea, v.radius):
        if not fact.settled:
            return unverified(fact.why)
    assert v.mount.value is not None and v.rea.value is not None and v.radius.value is not None
    needs_side = v.mount.value == "WM" or v.rea.value
    if needs_side and not side.settled:
        return unverified(side.why)
    if v.mount.value == "FM":
        if not v.height.settled:
            return unverified(v.height.why)
        if v.height.value is not None and v.height.value > FM84_MM:
            return unverified(TALL_FM)
    name = product(v.mount.value, v.rea.value, v.radius.value, side.value if needs_side else None)
    if name is None:
        return unverified("a VT RAD line on a floor panel: every radius product in Jim's library is a WM panel "
                          "(RULES 377-387)")
    return given(name, "rule", "BUILD 243, RULES 377-398, 411-417")


def _x(v: _Vertical, start: Finding) -> Fact[float]:
    r = v.read
    if "exists" in r.unsure:
        return unverified(EXISTS)
    if r.x_in is None or "x_in" in r.unsure:
        return unverified("the reading isn't sure where it stands")
    if start.status == "could not verify":
        return unverified(start.why)
    return given(round(r.x_in * MM, 3), "reading", f"its drawn left face (BUILD 283-284); {start.why}")


def _schedule(w: _Wall, wp: pl.WallPlacement | None, drawn: pl.DrawnWall | None, t: Finding,
              wall_len_mm: float | None) -> WallSchedule:
    name = w.read.name
    if w.read.verticals is None:
        listed = sum(line.qty or 1 for line, _ in vt_lines(w.read.lines))
        unread = Finding("reading", "could not verify", None, f"its {listed} verticals aren't read")
        return WallSchedule(name, False, (), (unread,) if listed else ())
    if wp is None or drawn is None:
        why = "the reading gave no position for a vertical on this wall"
        panels = tuple(PanelSpec(name, i, unverified(why), unverified(why), v.height, v.depth, v.mount,
                                 unverified(why), v.radius, v.rea, unverified(why), unverified(why))
                       for i, v in enumerate(w.verticals))
        return WallSchedule(name, True, panels, (*w.findings, Finding("positions", "could not verify", None, why)))
    if not drawn.verticals:
        return WallSchedule(name, True, (), tuple(w.findings))
    start = pl.run_start(min(d.x_mm for d in drawn.verticals))
    elevs = [_elev(v, d, p, drawn, t) for v, d, p in zip(w.verticals, drawn.verticals, wp.verticals)]
    sides = _sides(w, _spans(w, elevs), wall_len_mm, start)
    reads = [v.read for v in w.verticals]
    panels = tuple(PanelSpec(name, i, _x(v, start), elevs[i], v.height, v.depth, v.mount, sides[i], v.radius, v.rea,
                             unverified(EXISTS) if "exists" in v.read.unsure else _stacked(i, reads),
                             _product(v, sides[i]))
                   for i, v in enumerate(w.verticals))
    return WallSchedule(name, True, panels, tuple([*w.findings, start]))


# ----- the room


def build_room(name: str, rows: Sequence[TableRow], walls: Sequence[ReadWall], plan: Plan | None = None) -> RoomModel:
    """
    One room: its walls from the WALL/AREA table (and the plan, when one is given), and the schedule of
    every wall in `walls`, its verticals from the reading where it was read. The top line is the room's
    one T (RULES 329-339), over every wall of the room: a wall not read counts its FM lines standing on
    the floor (RULES 292), as a builder that read every wall would see them.
    """
    specs, joints, wall_height, findings = build_walls(rows, plan)
    prepared = [_prepare(w) for w in walls]
    placeable = [w for w in prepared if w.read.verticals is not None and w.placeable]
    for w in placeable:
        _hung_without_notch(w)
    others = [t for w in prepared if not (w.read.verticals is not None and w.placeable)
              for t in floor_fm_tops(w.read.lines)]
    drawn = [_drawn(w) for w in placeable]
    room = pl.place_room(drawn, other_fm_tops_mm=others)
    placed = {id(w): (wp, d) for w, wp, d in zip(placeable, room.walls, drawn)}
    lengths = {s.name: s.length_mm.value for s in specs}
    schedules = []
    for w in prepared:
        wp, d = placed.get(id(w), (None, None))
        schedules.append(_schedule(w, wp, d, room.top_line, lengths.get(w.read.name)))
    return RoomModel(name, specs, joints, wall_height, room.top_line, tuple(schedules), findings)
