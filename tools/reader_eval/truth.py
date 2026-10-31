"""
The reader evaluation's truth: what was actually cut, per wall, read from a Room#.des by bytes.

Why the as-cut .des: design 3.6 scores the reader against it ("Truth for scoring: the as-cut
.des"), and Jobs A to J plus the three frozen jobs give 39 real rooms (docs/p2/build-spec.md 3.5).
Read by bytes, as AGENTS.md rule 7 says: a .des is a version line, then XML, CRLF throughout.

What counts as a vertical is Jim's rule, not ours: regression.py's PANEL prefixes and its "at most
25.4 mm wide" (Jim's toolkit, 2026-10-31; test_reader_real.py ties the two by ast, never by import).
Where a vertical stands:
  wall    a product's Wall="<n>_1" names the n-th <Wall> element in file order. Measured on the 39
          real rooms (2026-10-31): read so, 288 of 289 products fit inside their wall (the other
          overhangs by 1.5 mm); read as WallNumber or IDTag, 13 do not. Jim's regression.py never
          maps it, comparing the raw string.
  x       X, the panel's left face from the wall's start, in mm; inches here.
  bottom  Elev. top: Elev + Height.
Panels come left to right by (x, bottom), so a stacked tower reads lower then upper.

Categories (design 3.6: plain, radius, stacked tower, hutch, REA, bridge, flush end), derived here
by rule, and a wall can carry several, each scored apart:
  stacked tower  two verticals within 1/2 in of each other's x, one above the other
  REA, radius    a vertical whose product name says REA, or Radius
  bridge         any product on the wall matched by \\bBR\\b (RULES: "Matched on \\bBR\\b")
  hutch          any product on the wall with "hutch" in its name
  flush end      a vertical within 1/4 in of either end of the wall
  plain          verticals and none of stacked, REA, radius, bridge or hutch
  no verticals   none
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tools.reader_eval.prep import name_key_hash

MM = 25.4
PANEL = re.compile(r"^(FM ?84|FM 90|WM Panel 48|WM 48 REA|Wall Mount)")  # Jim's regression.py
PANEL_MAX_WIDTH_MM = 25.4  # regression.py: num(a.get('Width')) <= 25.4
STACK_X_IN = 0.5
FLUSH_IN = 0.25
_ATTRS = re.compile(rb'(\w+)="([^"]*)"')
_VERSION = re.compile(rb"^\d+\r?\n<\?xml")
_BRIDGE = re.compile(r"\bBR\b")


class TruthError(Exception):
    """The file is not a .des this reader understands; nothing is guessed from it."""


@dataclass(frozen=True)
class Panel:
    prod: str
    wall_ref: str  # the raw Wall attribute, e.g. "3_1"
    x_mm: float
    elev_mm: float
    height_mm: float
    width_mm: float
    rot: float

    @property
    def x_in(self) -> float:
        return self.x_mm / MM

    @property
    def bottom_in(self) -> float:
        return self.elev_mm / MM

    @property
    def top_in(self) -> float:
        return (self.elev_mm + self.height_mm) / MM

    @property
    def height_in(self) -> float:
        return self.height_mm / MM

    @property
    def radius(self) -> bool:
        return "Radius" in self.prod

    @property
    def rea(self) -> bool:
        return "REA" in self.prod

    @property
    def schedule(self) -> dict[str, Any]:
        """The panel as Jim's baseline.json records it (mm, rounded to 0.01)."""
        return {"prod": self.prod, "wall": self.wall_ref, "x": round(self.x_mm, 2),
                "elev": round(self.elev_mm, 2), "h": round(self.height_mm, 2)}


@dataclass(frozen=True)
class Wall:
    position: int  # 1-based, in <Walls> file order: what Wall="<n>_1" names
    number: str  # WallNumber, as Mozaik shows it
    length_in: float
    height_in: float
    panels: tuple[Panel, ...]
    products: tuple[str, ...]  # every product's name on this wall, panels included

    @property
    def has_rotated(self) -> bool:
        return any(p.rot != 0 for p in self.panels)

    @property
    def categories(self) -> tuple[str, ...]:
        if not self.panels:
            return ("no verticals",)
        found: list[str] = []
        ps = self.panels
        if any(abs(a.x_in - b.x_in) <= STACK_X_IN and (a.top_in <= b.bottom_in or b.top_in <= a.bottom_in)
               for i, a in enumerate(ps) for b in ps[i + 1:]):
            found.append("stacked tower")
        if any(p.rea for p in ps):
            found.append("REA")
        if any(p.radius for p in ps):
            found.append("radius")
        if any(_BRIDGE.search(name) for name in self.products):
            found.append("bridge")
        if any("hutch" in name.casefold() for name in self.products):
            found.append("hutch")
        if not found:
            found.append("plain")
        if any(p.x_in <= FLUSH_IN or p.x_in + p.width_mm / MM >= self.length_in - FLUSH_IN for p in ps):
            found.append("flush end")
        return tuple(found)


@dataclass(frozen=True)
class RoomTruth:
    key: str  # name_key_hash of the room's Name: compared with a design's, never shown
    walls: tuple[Wall, ...]


def _attrs(tag: bytes) -> dict[str, str]:
    return {k.decode("utf-8"): v.decode("utf-8") for k, v in _ATTRS.findall(tag)}


def _num(attrs: dict[str, str], key: str, where: str) -> float:
    try:
        return float(attrs[key])
    except (KeyError, ValueError):
        raise TruthError(f"{where} has no readable {key}") from None


def read_room(data: bytes) -> RoomTruth:
    """The room's walls, each with its verticals left to right. Raises TruthError, never guesses."""
    if not _VERSION.match(data):
        raise TruthError("not a .des: no version line before the XML")
    room = re.search(rb"<Room [^>]*>", data)
    if room is None:
        raise TruthError("no <Room> element")
    walls_raw = [_attrs(m.group(0)) for m in re.finditer(rb"<Wall [^>]*>", data)]
    sizes = [(w.get("WallNumber", "?"), _num(w, "Len", "a wall") / MM, _num(w, "Height", "a wall") / MM)
             for w in walls_raw]
    panels: dict[int, list[Panel]] = {i: [] for i in range(1, len(sizes) + 1)}
    products: dict[int, list[str]] = {i: [] for i in range(1, len(sizes) + 1)}
    for m in re.finditer(rb"<Product [^>]*>", data):
        a = _attrs(m.group(0))
        ref = a.get("Wall", "")
        position = ref.split("_", 1)[0]
        if not position.isdigit() or int(position) == 0:
            continue  # Room0's loose parts stand on no wall
        if int(position) not in panels:
            raise TruthError(f"a product names wall {ref}, which the room does not have")
        name = a.get("ProdName", "").strip()
        products[int(position)].append(name)
        width = _num(a, "Width", "a product")
        if PANEL.match(a.get("ProdName", "")) and width <= PANEL_MAX_WIDTH_MM:
            panels[int(position)].append(Panel(name, ref, _num(a, "X", "a panel"), _num(a, "Elev", "a panel"),
                                               _num(a, "Height", "a panel"), width,
                                               float(a.get("Rot") or 0)))
    walls = tuple(Wall(i, number, length, height,
                       tuple(sorted(panels[i], key=lambda p: (p.x_mm, p.elev_mm))), tuple(products[i]))
                  for i, (number, length, height) in enumerate(sizes, 1))
    return RoomTruth(name_key_hash(_attrs(room.group(0)).get("Name", "")), walls)
