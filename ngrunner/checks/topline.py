r"""
NG Runner's own check of a built room's top line, with P5's placement rule read as it is (P3, "Audit of
chat-built jobs": the top line; ngrunner/build/placement.py, never changed from here).

Why NG Runner checks it too, beside Jim's top_line --check inside crosscheck: his check moves WM panels
to a top it picks, and says how many it would move. On a room with no FM panel beside its WM run it
picks the most common WM top, so a room hung on two tops passes his check for the panels on the
commoner one, and his check never looks at the Elev grid. The rules it ports, all in RULES (his
toolkit of 2026-10-31; RULES outranks the rest):

  one top     every WM panel in a room hangs on that room's single top line T, Elev = T - Height; two
              WM tops in one room is a defect (RULES 330-331)
  T           in order (RULES 333-339): the top of an FM panel reaching up beside the WM run, the
              highest if several; else the run's own top as built (RULES 337); a T within 1 in of 2132
              is 2132 (RULES 338). placement.top_line is that order for a builder, which has no "as
              built" top: where it says could not verify, a built room's T is its run's top as built,
              so one top is T and more than one is the defect
  gate 5      every WM panel's Height + Elev == T, and Elev % 32 == 0 (RULES 319). On a T of 32k+20,
              Elev is on 32k for every height on the grid (RULES 341), so a WM panel on T with its Elev
              off the grid is a height off the grid (gate 4, RULES 318). A run's top as built that
              is itself off 32k+20 is one RULES 337 accepts and gate 5 refuses: could not verify,
              as P5a placement left it for Jim (docs/decisions.md, "P5a placement")

Which products are panels is Jim's: his top_line.py hangs "WM Panel 48", "WM 48 REA" and "Wall Mount",
takes "FM" then a digit as floor-mount, and leaves out anything wider than 25.4 mm.
tests/test_checks_topline_real.py ties these to his source by ast, never by import.

Room0 is the parts locker and has no top line (crosscheck runs top_line on product rooms only). A room
that doesn't read as a design is room_files' finding (findings.py), not this check's.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ngrunner.build.placement import GRID_MM, GRID_OFFSET_MM, top_line
from ngrunner.guards.bak import DesignUnreadable, parse_room
from ngrunner.guards.facts import COULD_NOT_VERIFY, FAILED, PASSED

WM_PANEL = re.compile(r"(WM Panel 48|WM 48 REA|Wall Mount)")   # Jim's top_line.py: hung
FM_PANEL = re.compile(r"FM ?\d")                                # Jim's top_line.py: floor-mount
PANEL_MAX_WIDTH_MM = 25.4                                       # Jim's top_line.py: wider is no panel
SAME_MM = 0.05          # docs/decisions.md, the nest-to-design match: within 0.05 mm is the same

_ROOM = re.compile(r"Room(\d+)\.des", re.IGNORECASE)

# What a room's top line came to
OFF_TOP, TWO_TOPS, ELEV_GRID, AS_BUILT = "off_top", "two_tops", "elev_grid", "top_as_built"
NONE = "none"           # no WM panel: nothing hangs, so there is no top line to check


@dataclass(frozen=True)
class Panel:
    cab: int
    elev_mm: float
    height_mm: float

    @property
    def top_mm(self) -> float:
        return self.elev_mm + self.height_mm


@dataclass(frozen=True)
class RoomTop:
    """One product room's top line. code: PASSED, FAILED, COULD_NOT_VERIFY, or NONE with no WM panel.
    t_mm: the room's T when the rules give one. off_top: the cabinets of WM panels not on T;
    off_grid: those on T with Elev off 32k. tops: the WM tops found, when there are several.
    found: OFF_TOP, TWO_TOPS, ELEV_GRID or AS_BUILT, each what Jim reads it as. hung: each WM panel as
    (cabinet, Elev, height), what a finding is bound to."""

    room: int
    code: str
    t_mm: float | None = None
    off_top: tuple[int, ...] = ()
    off_grid: tuple[int, ...] = ()
    tops: tuple[float, ...] = ()
    found: tuple[str, ...] = ()
    hung: tuple[tuple[int, float, float], ...] = ()


class RoomUnreadable(Exception):
    """A room whose panels can't be read for their top line; the words are plain."""


def room_number(name: str) -> int | None:
    found = _ROOM.fullmatch(name)
    return int(found[1]) if found else None


def panels(data: bytes) -> tuple[list[Panel], list[Panel]]:
    """The room's floor-mount and wall-hung panels, as Jim's top_line.py tells them apart. Raises
    RoomUnreadable for a room that isn't a design, or a panel without a number where one must be."""
    try:
        root = parse_room(data)
    except DesignUnreadable as exc:
        raise RoomUnreadable(str(exc)) from None
    fm: list[Panel] = []
    wm: list[Panel] = []
    for product in root.iter("Product"):
        name = product.get("ProdName", "")
        hung, standing = WM_PANEL.match(name), FM_PANEL.match(name)
        if not (hung or standing):
            continue
        try:
            if float(product.get("Width", "99")) > PANEL_MAX_WIDTH_MM:
                continue
            panel = Panel(int(product.get("CabNo", "")), float(product.get("Elev", "")),
                          float(product.get("Height", "")))
        except ValueError:
            raise RoomUnreadable("a panel's cabinet number, Elev or height doesn't read as a number") from None
        (wm if hung else fm).append(panel)
    return fm, wm


def room_top(room: int, data: bytes) -> RoomTop:
    """One room's top line, by the rules in the module notes. Raises RoomUnreadable."""
    fm, wm = panels(data)
    if not wm:
        return RoomTop(room, NONE)
    hung = tuple(sorted((p.cab, p.elev_mm, p.height_mm) for p in wm))
    rule = top_line([p.top_mm for p in fm], [p.elev_mm for p in wm], [p.top_mm for p in wm])
    if rule.value_mm is not None:
        return _on(room, rule.value_mm, wm, hung)
    tops = _distinct([p.top_mm for p in wm])
    if len(tops) > 1:                                   # RULES 330-331: no one top "as built"
        return RoomTop(room, FAILED, tops=tops, found=(TWO_TOPS,), hung=hung)
    t = tops[0]                                          # RULES 337: the run's own top, as built
    if not _on_grid(t - GRID_OFFSET_MM):
        return RoomTop(room, COULD_NOT_VERIFY, t, tops=tops, found=(AS_BUILT,), hung=hung)
    return _on(room, t, wm, hung)


def rooms_top(rooms: Mapping[str, bytes]) -> tuple[RoomTop, ...]:
    """Each product room's top line, in room order. Room0 and a room that doesn't read are left out:
    the second is room_files' finding."""
    out = []
    for name, data in sorted(rooms.items(), key=lambda pair: room_number(pair[0]) or 0):
        number = room_number(name)
        if not number:
            continue
        try:
            out.append(room_top(number, data))
        except RoomUnreadable:
            continue
    return tuple(out)


def _on(room: int, t: float, wm: Sequence[Panel], hung: tuple[tuple[int, float, float], ...]) -> RoomTop:
    off_top = tuple(sorted({p.cab for p in wm if abs(p.top_mm - t) > SAME_MM}))
    off_grid = tuple(sorted({p.cab for p in wm if p.cab not in off_top and not _on_grid(p.elev_mm)}))
    found = ((OFF_TOP,) if off_top else ()) + ((ELEV_GRID,) if off_grid else ())
    return RoomTop(room, FAILED if found else PASSED, t, off_top, off_grid, found=found, hung=hung)


def _on_grid(mm: float) -> bool:
    return abs(mm - round(mm / GRID_MM) * GRID_MM) <= SAME_MM


def _distinct(values: Sequence[float]) -> tuple[float, ...]:
    """The values, those within SAME_MM of one already kept counting as it."""
    kept: list[float] = []
    for value in sorted(values):
        if not kept or value - kept[-1] > SAME_MM:
            kept.append(value)
    return tuple(kept)
