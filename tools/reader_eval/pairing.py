"""
Pairing each crop with the .des wall it draws, surely or not at all (docs/p2/build-spec.md 5.7).

Why so strict: a score is only as good as its truth. A crop paired with the wrong wall, or with a
wall Jim built differently from the drawing, would count a right reading as an error (or a wrong
one as right). So a crop is paired only when every step below holds, and otherwise left out with a
code, counted in the report, never guessed:

  room     the one .des room whose Name has the design's key (letters and digits, any case; Mozaik
           drops apostrophes). None: no_room. More than one: several_rooms.
  wall     the WALL/AREA table's width for the crop's wall (no_wall_size without one) equals one
           wall's length in that room, within 0.07 in (IC prints eighths). IC's wall numbers are
           not Mozaik's (real Job D starts at 2), so the number never decides. None:
           no_wall_of_that_length. Several: paired only when their verticals are the same, so the
           truth can't differ (several_walls_of_that_length otherwise).
  as drawn the parts list must agree with what was cut, or the .des is not the drawing's truth:
           the VT count (vertical_count_differs; count_unreadable when a quantity is blank), the
           VT RAD count (radius_count_differs), and every VT height, each within 0.65 in (Mozaik
           snaps IC heights to the 32k+20 mm grid, at most 16 mm away; heights_differ). A wall
           with no parts lines at all is no_parts_list; a rotated vertical, rotated_vertical.
  once     two crops on one wall: both wall_paired_twice.

Also counted: walls with verticals, in rooms a design names, that no crop paired.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tools.reader_eval.prep import WallCrop
from tools.reader_eval.truth import RoomTruth, Wall

LENGTH_TOL_IN = 0.07
HEIGHT_TOL_IN = 0.65
SAME_TOL_IN = 0.05


@dataclass(frozen=True)
class Pair:
    case: str
    crop: WallCrop
    room: str  # the room's file stem, e.g. "Room1"
    wall: Wall


@dataclass(frozen=True)
class LeftOut:
    case: str
    page: int
    wall_name: str
    code: str


@dataclass(frozen=True)
class UnpairedWall:
    case: str
    room: str
    position: int
    verticals: int


@dataclass(frozen=True)
class Pairing:
    pairs: tuple[Pair, ...]
    left_out: tuple[LeftOut, ...]
    unpaired_walls: tuple[UnpairedWall, ...]


def _same_panels(a: Wall, b: Wall) -> bool:
    return len(a.panels) == len(b.panels) and all(
        p.prod == q.prod and abs(p.x_in - q.x_in) <= SAME_TOL_IN and abs(p.bottom_in - q.bottom_in) <= SAME_TOL_IN
        and abs(p.top_in - q.top_in) <= SAME_TOL_IN for p, q in zip(a.panels, b.panels))


def _by_table(crop: WallCrop, walls: Sequence[Wall]) -> Wall | None:
    """
    The wall the crop's WALL/AREA table maps it to, when exactly one wall results.

    Every mapping of IC numbers to Mozaik WallNumbers is tried, shifted (IC n is Mozaik n - k) or
    reversed (IC n is Mozaik c - n), and a mapping fits only when every row of the table lands on a
    wall of its width. A mirror-image room fits both ways and so stays unsure.
    """
    by_number: dict[int, Wall] = {}
    for wall in walls:
        if not wall.number.isdigit() or int(wall.number) in by_number:
            return None
        by_number[int(wall.number)] = wall
    if not crop.table or not by_number or not all(name.isdigit() for name, _ in crop.table):
        return None
    rows = [(int(name), width) for name, width in crop.table]
    target = int(crop.wall) if crop.wall.isdigit() else None
    if target is None or target not in {n for n, _ in rows}:
        return None
    span = max(by_number) + max(n for n, _ in rows) + 1
    landed: set[int] = set()
    for direction, shift in [(1, k) for k in range(-span, span + 1)] + [(-1, c) for c in range(-span, span + 1)]:
        def where(n: int) -> int:
            return n - shift if direction == 1 else shift - n
        if all(where(n) in by_number and abs(by_number[where(n)].length_in - w) <= LENGTH_TOL_IN for n, w in rows):
            landed.add(where(target))
    return by_number[landed.pop()] if len(landed) == 1 else None


def _as_drawn(crop: WallCrop, wall: Wall) -> str | None:
    """Why the .des is not this drawing's truth, or None when the parts list agrees with it."""
    if not crop.parts:
        return "no_parts_list"
    if wall.has_rotated:
        return "rotated_vertical"
    if crop.vt_count is None or crop.vt_rad_count is None:
        return "count_unreadable"
    if crop.vt_count != len(wall.panels):
        return "vertical_count_differs"
    if crop.vt_rad_count != sum(p.radius for p in wall.panels):
        return "radius_count_differs"
    ic = sorted(float(m["height"]) for p, m in crop.vt_lines for _ in range(p.qty or 0))
    cut = sorted(p.height_in for p in wall.panels)
    if any(abs(a - b) > HEIGHT_TOL_IN for a, b in zip(ic, cut)):
        return "heights_differ"
    return None


def _match(crop: WallCrop, rooms: Mapping[str, RoomTruth]) -> tuple[str, Wall] | str:
    named = [label for label, room in rooms.items() if room.key == crop.design_key]
    if not named:
        return "no_room"
    if len(named) > 1:
        return "several_rooms"
    if crop.width_in is None:
        return "no_wall_size"
    label = named[0]
    fits = [w for w in rooms[label].walls if abs(w.length_in - crop.width_in) <= LENGTH_TOL_IN]
    if not fits:
        return "no_wall_of_that_length"
    if any(not _same_panels(fits[0], other) for other in fits[1:]):
        by_table = _by_table(crop, rooms[label].walls)
        if by_table is None or by_table not in fits:
            return "several_walls_of_that_length"
        fits = [by_table]
    wall = fits[0]
    why = _as_drawn(crop, wall)
    return why if why else (label, wall)


def pair(case: str, crops: Sequence[WallCrop], rooms: Mapping[str, RoomTruth]) -> Pairing:
    """Pair every crop of one case (job) with its wall in that job's rooms, or leave it out."""
    found: list[tuple[WallCrop, str, Wall]] = []
    left: list[LeftOut] = []
    for crop in crops:
        result = _match(crop, rooms)
        if isinstance(result, str):
            left.append(LeftOut(case, crop.page, crop.wall, result))
        else:
            found.append((crop, *result))
    twice = Counter((label, wall.position) for _, label, wall in found)
    pairs: list[Pair] = []
    for crop, label, wall in found:
        if twice[(label, wall.position)] > 1:
            left.append(LeftOut(case, crop.page, crop.wall, "wall_paired_twice"))
        else:
            pairs.append(Pair(case, crop, label, wall))
    named_rooms = {label for label, room in rooms.items() if any(room.key == c.design_key for c in crops)}
    paired = {(p.room, p.wall.position) for p in pairs}
    unpaired = tuple(UnpairedWall(case, label, w.position, len(w.panels))
                     for label in sorted(named_rooms) for w in rooms[label].walls
                     if w.panels and (label, w.position) not in paired)
    return Pairing(tuple(pairs), tuple(sorted(left, key=lambda x: x.page)), unpaired)
