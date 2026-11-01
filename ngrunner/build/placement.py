"""
Jim's build choices that a drawing doesn't show, as code: the room's top line, the gap under a stacked
upper, and where a run starts.

Why: on 147 real walls, Claude read the elevation drawings right except for small errors, but 109 of its
119 silent misses were offsets from Jim's build choices, not reading (docs/decisions.md, "P5's reader").
A reader can't see them, so the builder applies them as rules (AGENTS.md rule 3). Each rule here is a
pure function that cites the line of Jim's docs it ports (his toolkit of 2026-10-31; RULES outranks the
rest), and where his rules don't settle a case it says "could not verify" with the reason, never a guess
(rule 8). All lengths are mm, as RULES 67 keeps them; a drawn value is the reader's, converted.

  height      the IC height of that piece, snapped to 32k+20 (RULES 291); more than 1.3 mm off the grid
              could not verify (gate 4, RULES 318)
  mount       FN, LN or RN on the panel's own IC line hangs it (WM); NN, ND or none stands it on the
              floor (FM). Never the height (RULES 290, 325-326). Ported as convert.rules.WALL_MOUNT
  top line    one T per room, and every WM panel hangs on it, Elev = T - height (RULES 330). T is read
              in order (RULES 333-339; his check is top_line.py's room_top): an FM panel reaching up
              beside the WM run (its top above the run's lowest bottom) gives its top, the highest if
              several; with none, the run's own top "as built" (RULES 337); and a T within 1 in of 2132
              is 2132 (RULES 338). A builder has no "as built" top yet, only the drawing's, which no rule
              of Jim's turns into T, so a run with no FM panel beside it whose drawn tops are not all
              within 1 in of 2132 could not verify. BUILD 244 and 294 ("T = 2132, always") predate RULES
              333 (2026-10-18), and RULES outranks them (questionnaire Q2)
  stacked gap an upper of a stacked tower with a hanging notch is a WM panel and hangs on T (RULES 310,
              330; Jim's frozen jobs X and Y). One with none is an FM panel drawn above the floor: it
              stands on the closet counter, built 3/4 in (RULES 218, 221), which is 19.304 mm (RULES 73),
              so Elev = the base under it + 19.304, as Jim's own build of real Job E places it. With no
              WS line on the wall, or bases of two heights under it, it could not verify
  run start   where a run starts when the drawing leaves a gap at the wall's start. No rule of Jim's
              settles it: his builds of the same drawn gap start the run tight to the wall, centre it,
              or keep it as drawn (BUILD 274 and 276 say how to read the WIDTH row, not where to build).
              A gap of 3 in or more is a Wall Filler or an opening (RULES 264, BUILD 276) and stays as
              drawn; a run drawn tight (within the reader's 1/8 in) stays as drawn; any gap between
              could not verify. It never moves a vertical
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ngrunner.convert.rules import WALL_MOUNT

MM = 25.4
GRID_MM = 32.0                # RULES 291: closet VT heights are on 32k+20
GRID_OFFSET_MM = 20.0
SNAP_TOL_MM = 1.3             # RULES 318, gate 4: every height on 32k+20 within 1.3 mm
NORM_TOP_MM = 2132.0          # RULES 338 (top_line.py's T)
NEAR_NORM_MM = 25.4           # RULES 338: "within 1″ of 2132" (top_line.py's NEAR)
COUNTER_MM = 19.304           # RULES 73: a 3/4″ panel; RULES 218, 221: a closet WS is built 3/4″
FILLER_MM = 76.2              # RULES 264: the 3″ Wall Filler, the narrowest piece Jim stands at a wall end
TIGHT_MM = 0.125 * MM         # the reader's calibrated x tolerance: a drawn gap it can't tell from none
OFF_FLOOR_MM = 1.0 * MM       # a vertical drawn more than 1 in up stands off the floor

Mount = Literal["FM", "WM"]
Status = Literal["placed", "as drawn", "could not verify", "not needed"]
By = Literal["drawing", "top line", "stacked gap"]


@dataclass(frozen=True)
class Finding:
    """What one rule said: its value (mm) when it placed something, and why, citing Jim's line."""
    rule: str  # "top line" | "stacked gap" | "run start" | "height" | "mount"
    status: Status
    value_mm: float | None = None
    why: str = ""


@dataclass(frozen=True)
class Drawn:
    """One vertical as the drawing gives it (the reader's values, mm), with its IC line's facts:
    height_mm is snap_height_mm() of its IC height, None when off the grid; mount is None when the
    IC lines it could be disagree."""
    x_mm: float
    bottom_mm: float
    top_mm: float
    height_mm: float | None
    mount: Mount | None


@dataclass(frozen=True)
class DrawnWall:
    verticals: tuple[Drawn, ...]
    has_counter: bool  # the wall's IC parts list has a WS line


@dataclass(frozen=True)
class Placed:
    x_mm: float
    bottom_mm: float
    top_mm: float
    by: By  # the drawing, or the rule that placed it


@dataclass(frozen=True)
class WallPlacement:
    verticals: tuple[Placed, ...]
    findings: tuple[Finding, ...]

    @property
    def could_not_verify(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.status == "could not verify")


@dataclass(frozen=True)
class RoomPlacement:
    top_line: Finding
    walls: tuple[WallPlacement, ...]


def _in(mm: float) -> str:
    return f"{mm / MM:.2f} in"


# ----- a piece's height and mount


def snap_height_mm(height_in: float) -> float | None:
    """The IC height on Mozaik's 32k+20 grid (RULES 291), or None when more than 1.3 mm from it."""
    mm = height_in * MM
    snapped = round((mm - GRID_OFFSET_MM) / GRID_MM) * GRID_MM + GRID_OFFSET_MM
    return snapped if abs(snapped - mm) <= SNAP_TOL_MM else None


def mount(description: str) -> Mount:
    """FM or WM from the notch token on the panel's own IC line (RULES 290, 325-326)."""
    return "WM" if WALL_MOUNT.search(description) else "FM"


# ----- the rules


def top_line(fm_tops_mm: Sequence[float], wm_bottoms_mm: Sequence[float],
             wm_tops_mm: Sequence[float]) -> Finding:
    """The room's T (RULES 333-339): fm_tops_mm are every FM panel's top in the room, the WM values
    are the room's drawn WM bottoms and tops."""
    if not wm_bottoms_mm:
        return Finding("top line", "not needed", why="no WM panel in the room")
    low = min(wm_bottoms_mm)
    beside = [t for t in fm_tops_mm if t > low]
    if beside:
        t = max(beside)
        if abs(t - NORM_TOP_MM) <= NEAR_NORM_MM:
            t = NORM_TOP_MM
        return Finding("top line", "placed", t,
                       f"the top of the FM panel beside the WM run, {_in(t)} (RULES 334; RULES 338 within 1 in)")
    if wm_tops_mm and all(abs(t - NORM_TOP_MM) <= NEAR_NORM_MM for t in wm_tops_mm):
        return Finding("top line", "placed", NORM_TOP_MM,
                       "no FM panel beside the WM run (RULES 337), and it is drawn within 1 in of 83.94 in "
                       "(RULES 338)")
    drawn = sorted({round(t / MM, 2) for t in wm_tops_mm})
    return Finding("top line", "could not verify", None,
                   "no FM panel beside the WM run sets the top line, and the run is drawn at "
                   + ", ".join(f"{t:.2f} in" for t in drawn)
                   + ", not within 1 in of 83.94 in: RULES 337 takes the run's top as built, and nothing is built yet")


def hung_elev_mm(top_line_mm: float, height_mm: float) -> float:
    """A WM panel's Elev: T - height (RULES 330)."""
    return top_line_mm - height_mm


def counter_elev(base_heights_mm: Sequence[float], *, has_counter: bool) -> Finding:
    """An FM upper's Elev: on the closet counter over its base (module notes, stacked gap)."""
    heights = set(base_heights_mm)
    if not heights:
        return Finding("stacked gap", "could not verify", None,
                       "drawn above the floor with no base under it, and its IC line has no hanging notch to "
                       "hang it by (RULES 326)")
    if not has_counter:
        return Finding("stacked gap", "could not verify", None,
                       "an FM upper drawn above its base, and no WS on the wall for it to stand on")
    if len(heights) != 1:
        return Finding("stacked gap", "could not verify", None,
                       "an FM upper on the counter, but the bases under it differ, so the counter's height can't "
                       "be told")
    elev = heights.pop() + COUNTER_MM
    return Finding("stacked gap", "placed", elev,
                   f"an FM upper on the counter, 3/4 in over its base: Elev {_in(elev)} (RULES 73, 221)")


def run_start(first_x_mm: float) -> Finding:
    """Whether the drawn start of a wall's run is settled (module notes): it never moves the run."""
    if first_x_mm <= TIGHT_MM:
        return Finding("run start", "as drawn", first_x_mm, "the run is drawn tight to the wall's start")
    if first_x_mm >= FILLER_MM - TIGHT_MM:
        return Finding("run start", "as drawn", first_x_mm,
                       f"the run starts {_in(first_x_mm)} along the wall, behind a Wall Filler or an opening "
                       "(RULES 264, BUILD 276)")
    return Finding("run start", "could not verify", None,
                   f"the run is drawn {_in(first_x_mm)} from the wall's start, under a 3 in Wall Filler: Jim's "
                   "builds start such a run as drawn, tight to the wall, or centred, and no rule says which")


# ----- a room, placed


def bases_under(v: Drawn, wall: DrawnWall) -> list[float]:
    """The heights of the wall's FM panels on the floor drawn below v: what an FM upper would stand over."""
    return [b.height_mm for b in wall.verticals if b.mount == "FM" and b.bottom_mm <= OFF_FLOOR_MM
            and b.height_mm is not None and b.top_mm <= v.bottom_mm]


def fm_upper(v: Drawn, wall: DrawnWall) -> Finding:
    """Where an FM vertical drawn above the floor stands: on the counter over its wall's bases."""
    return counter_elev(bases_under(v, wall), has_counter=wall.has_counter)


def place_room(walls: Sequence[DrawnWall], *, other_fm_tops_mm: Sequence[float] = ()) -> RoomPlacement:
    """
    Every drawn wall of one room placed by the rules above. other_fm_tops_mm: the tops of FM panels
    on the room's walls that weren't drawn here (each its snapped height, standing on the floor).
    A vertical no rule places keeps its drawn values; nothing ever changes its x.
    """
    uppers: dict[tuple[int, int], Finding] = {}
    fm_tops = list(other_fm_tops_mm)
    wm_bottoms: list[float] = []
    wm_tops: list[float] = []
    for w, wall in enumerate(walls):
        for i, v in enumerate(wall.verticals):
            if v.mount == "FM" and v.bottom_mm > OFF_FLOOR_MM:
                uppers[(w, i)] = found = fm_upper(v, wall)
                height = v.height_mm if v.height_mm is not None else v.top_mm - v.bottom_mm
                fm_tops.append(found.value_mm + height if found.value_mm is not None else v.top_mm)
            elif v.mount == "FM":
                fm_tops.append(v.height_mm if v.height_mm is not None else v.top_mm)  # FM: Elev 0 (RULES 292)
            elif v.mount == "WM":
                wm_bottoms.append(v.bottom_mm)
                wm_tops.append(v.top_mm)
    t = top_line(fm_tops, wm_bottoms, wm_tops)

    placed_walls: list[WallPlacement] = []
    for w, wall in enumerate(walls):
        out: list[Placed] = []
        findings: list[Finding] = []
        for i, v in enumerate(wall.verticals):
            drawn = Placed(v.x_mm, v.bottom_mm, v.top_mm, "drawing")
            if v.mount is None:
                findings.append(Finding("mount", "could not verify", None,
                                        "the IC lines of this vertical's height disagree on FM or WM"))
                out.append(drawn)
            elif v.height_mm is None:
                findings.append(Finding("height", "could not verify", None,
                                        "its IC height is more than 1.3 mm off the 32k+20 grid (RULES 318)"))
                out.append(drawn)
            elif v.mount == "WM" and t.value_mm is not None:
                out.append(Placed(v.x_mm, hung_elev_mm(t.value_mm, v.height_mm), t.value_mm, "top line"))
            elif v.mount == "WM":
                if t not in findings:
                    findings.append(t)
                out.append(drawn)
            elif (w, i) in uppers:
                found = uppers[(w, i)]
                if found.value_mm is None:
                    findings.append(found)
                    out.append(drawn)
                else:
                    out.append(Placed(v.x_mm, found.value_mm, found.value_mm + v.height_mm, "stacked gap"))
            else:
                out.append(drawn)
        if wall.verticals:
            findings.insert(0, run_start(min(v.x_mm for v in wall.verticals)))
        placed_walls.append(WallPlacement(tuple(out), tuple(findings)))
    return RoomPlacement(t, tuple(placed_walls))
