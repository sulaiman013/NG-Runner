"""
The room model: one room as P5a builds it, in memory, before any file is written.

Why: P5a's builder turns a reading of the drawings and Jim's rules into a room (design section 7, P5;
docs/p2/build-spec.md). Slice 2 writes Room#.des from this model, so it holds every value the .des
needs, each as a Fact saying where it came from, and "could not verify" with the reason wherever Jim's
rules or the inputs don't settle it, never a guess (AGENTS.md rule 8). Nothing here reads or writes a
file. walls.py builds the walls, schedule.py the panels and the room; this module is only their types.

  sources     "table"    the design's WALL/AREA table on the summary (lengths and heights, BUILD 201-202)
              "plan"     the plan view: the chain's order and each wall's angle (BUILD 201-202). Nothing
                         in NG Runner reads the plan yet, so without one these could not verify
              "reading"  the elevation drawing as the reader gave it (x, the drawn run, radius)
              "IC line"  the wall's own parts lines (height, depth, mount, REA, drill code)
              "rule"     one of Jim's rules applied by code (placement.py, and the rules in schedule.py)
  statuses    "set"               settled, with its source
              "review"            settled by Jim's rule but his rule says to flag it (SCHEMA 166: a depth
                                  off the grid is kept IC-exact and flagged Review)
              "could not verify"  not settled; the reason says why, and value is None
              "not needed"        the field doesn't apply (a plain FM panel has no notch side)

All lengths are mm, as RULES 67 keeps them.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, fields
from typing import Literal

from ngrunner.build.placement import Finding

Source = Literal["table", "plan", "reading", "IC line", "rule"]
Status = Literal["set", "review", "could not verify", "not needed"]
Mount = Literal["FM", "WM"]
Side = Literal["Thru", "Left", "Right"]
Stacked = Literal["lower", "upper", "no"]

PANEL_WIDTH_MM = 19.0  # BUILD 284 ("Width 19"); the templates ship Width="19" (SCHEMA 116)
WALL_THICKNESS_MM = 152.4  # RULES 457, BUILD 202, SCHEMA 15
SHAPE_TYPE = 0  # RULES 457, BUILD 202


@dataclass(frozen=True)
class Fact[T]:
    """One value of the model, with where it came from, or why it could not be verified."""
    value: T | None
    source: Source | None
    status: Status
    why: str = ""

    @property
    def settled(self) -> bool:
        return self.status in ("set", "review")


def given[T](value: T, source: Source, why: str = "") -> Fact[T]:
    return Fact(value, source, "set", why)


def review[T](value: T, source: Source, why: str) -> Fact[T]:
    return Fact(value, source, "review", why)


def unverified[T](why: str) -> Fact[T]:
    return Fact(None, None, "could not verify", why)


def not_needed[T](why: str) -> Fact[T]:
    return Fact(None, None, "not needed", why)


# ----- the inputs


@dataclass(frozen=True)
class TableRow:
    """One row of a design's WALL/AREA table: the wall's name and its size, inches as printed."""
    name: str
    width_in: float
    height_in: float


@dataclass(frozen=True)
class Plan:
    """What only the plan view gives (BUILD 201-202, 209-211): the walls by name in chain order, and each one's
    angle in Mozaik's Y-up frame (RULES 454-456)."""
    order: tuple[str, ...]
    angles: tuple[float, ...]


@dataclass(frozen=True)
class ICLine:
    """One line of a wall's parts table: its quantity (None when unreadable) and its description."""
    qty: int | None
    desc: str


@dataclass(frozen=True)
class ReadVertical:
    """One vertical as the reader gave it (the reader evaluation's schema, tools/reader_eval/payload.py):
    inches from the drawing, the VT height it named as printed, radius yes/no/unclear, and its doubts."""
    x_in: float | None
    bottom_in: float | None
    top_in: float | None
    height: str
    radius: str
    unsure: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReadWall:
    """A wall of the design: its parts lines, and its verticals when the reader read it (None if not),
    with the reading's doubts about the whole wall (its count_unsure and unreadable)."""
    name: str
    lines: tuple[ICLine, ...]
    verticals: tuple[ReadVertical, ...] | None = None
    count_unsure: bool = False
    unreadable: tuple[str, ...] = ()


# ----- the model


@dataclass(frozen=True)
class WallSpec:
    """One <Wall> of the room (SCHEMA 14-19 is the attribute set slice 2 clones; these are its values).
    position is its place in the chain, 1-based: the file order, which a product's Wall="<n>_1" names."""
    name: str  # the table's wall name: WallNumber
    length_mm: Fact[float]
    height_mm: Fact[float]
    position: Fact[int]
    id_tag: Fact[int]
    angle: Fact[float]
    pos_mm: Fact[tuple[float, float]]  # PosX, PosY: where the wall starts


@dataclass(frozen=True)
class PanelSpec:
    """One vertical of the per-wall schedule (RULES 285-322, "The schedule is read PER PANEL")."""
    wall: str  # the table's name of the wall it stands on
    index: int  # left to right on that wall, from 0, as the drawing orders them
    x_mm: Fact[float]  # the left face, from the wall's start (BUILD 283-284)
    elev_mm: Fact[float]
    height_mm: Fact[float]
    depth_mm: Fact[float]
    mount: Fact[Mount]
    side: Fact[Side]  # Thru / Left / Right: the notch (RULES 351-372)
    radius: Fact[bool]
    rea: Fact[bool]
    stacked: Fact[Stacked]
    product: Fact[str]  # the library product slice 2 clones (RULES 377-398, 411-417)

    @property
    def top_mm(self) -> float | None:
        if self.elev_mm.value is None or self.height_mm.value is None:
            return None
        return self.elev_mm.value + self.height_mm.value

    def facts(self) -> Iterator[tuple[str, Fact[object]]]:
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, Fact):
                yield f.name, value


@dataclass(frozen=True)
class WallSchedule:
    """A wall's panels, left to right, and what its gates and the run start found (RULES 313-322)."""
    wall: str
    read: bool  # False: no reading of this wall, so no panels were built for it
    panels: tuple[PanelSpec, ...]
    findings: tuple[Finding, ...]


@dataclass(frozen=True)
class RoomModel:
    name: str  # the IC design's name (BUILD 149): in memory only, never printed by the tools
    walls: tuple[WallSpec, ...]  # in chain order with a plan, else in the table's order
    joints: Fact[tuple[tuple[int, int], ...]]  # each WallJoint's Wall1, Wall2 IDTags (SCHEMA 20-21)
    wall_height_mm: Fact[float]  # RoomParms H_Walls: the tallest wall (gen_rooms.py 111-113)
    top_line: Finding
    schedules: tuple[WallSchedule, ...]
    findings: tuple[Finding, ...]  # the room's own: the chain's gates (BUILD 221)

    def wall(self, name: str) -> WallSpec | None:
        return next((w for w in self.walls if w.name == name), None)

    def panels(self) -> Iterator[PanelSpec]:
        for schedule in self.schedules:
            yield from schedule.panels

    def unverified(self) -> list[tuple[str, str, str]]:
        """Every could-not-verify in the room, as (where, what, why): walls, panels, gates."""
        out: list[tuple[str, str, str]] = []
        for w in self.walls:
            for name in ("length_mm", "height_mm", "position", "id_tag", "angle", "pos_mm"):
                fact = getattr(w, name)
                if fact.status == "could not verify":
                    out.append((f"wall {w.name}", name, fact.why))
        for label, fact in (("joints", self.joints), ("wall height", self.wall_height_mm)):
            if fact.status == "could not verify":
                out.append(("room", label, fact.why))
        out += [("room", f.rule, f.why) for f in self.findings if f.status == "could not verify"]
        for schedule in self.schedules:
            out += [(f"wall {schedule.wall}", f.rule, f.why) for f in schedule.findings
                    if f.status == "could not verify"]
            for p in schedule.panels:
                out += [(f"wall {p.wall} panel {p.index + 1}", name, fact.why) for name, fact in p.facts()
                        if fact.status == "could not verify"]
        return out
