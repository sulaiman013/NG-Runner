"""
Room#.des from the room model: the template job's Room1 with its walls, joints and products replaced, in
memory. Nothing here writes a file: gates.py checks the bytes, then commit.py writes them through
commit_write.

Why: P5a builds the rooms Jim's chat builds by hand (design section 7, P5). His method is BUILD Steps 3 to
5 (lines 140-296) and SCHEMA's attribute sets (lines 7-37), and his own builders do it in code
(build_jobs_0914.py 14-123, gen_rooms.py 87-118). Ported from his toolkit of 2026-10-31, never copied:

  the seed      the template job's Room1, never Room0 (BUILD 144-146), read at run time from the template
                job's folder ("Lux Closet Template Claude", BUILD 144) and never committed. Its Walls,
                WallJoints and Products are replaced whole, found with a balanced-element scanner, not a
                non-greedy regex (BUILD 146-147; desxml.py)
  the Room tag  UniqueID 50000 + the room's number (BUILD 149; frozen X and Z run 50001 up), Name the IC
                design's, IdTagCount the highest IDTag of a wall or a product (SCHEMA 32-37)
  RoomParms     H_Walls, the tallest wall (gen_rooms.py 111-113)
  RoomSet       the material templates (SCHEMA 46-85) and the drawer fronts (SCHEMA 59). The model doesn't
                hold them: the room's colour decides them, so they come in as RoomSettings (colour.settle),
                the <BaseMatTempSel> run lifted whole from a known-good room in that colour (SCHEMA 75-78,
                build_jobs_0914.py 39-50) and the drawer front, or the room is not written. A run handed in
                rather than lifted, or one its room no longer holds byte for byte, is refused: never
                hand-authored (colour.still_holds). Never cabinet id 88 (SCHEMA 82-84) and never a per-part
                MatORId (BUILD 157-159)
  walls         each <Wall> a clone of the template's own, only IDTag, Len, Height, PosX, PosY, Ang and
                WallNumber changed (SCHEMA 9-30: "clone this template and mutate only the numeric values")
  joints        each <WallJoint> a clone of one in Jim's reference rooms (the template room has none), only
                Wall1 and Wall2 changed: the IDTags of each neighbouring pair (gen_rooms.py 94-96)
  products      products.py, one per panel, on Wall="<its wall's place in the file>_1": frozen Z's two rooms
                whose chain isn't the table's order put their panels on the file position, not the number
  numbers       f"{v:.3f}" with trailing zeros stripped, never %g (BUILD 290-291); gates.py audits every
                value written against the model
  the file      UTF-8, CRLF throughout, with the template's own first line, its version (15)

A room whose model holds any could-not-verify value, or that needs a setting it wasn't given, is not
written: write_room says which values block it (AGENTS.md rule 8). A value Jim's rules flag Review (a depth
kept IC-exact, SCHEMA 166) is written and listed for him.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from xml.sax.saxutils import escape

from ngrunner.build.colour import FRONTS, HANDED_IN, Lifted, still_holds
from ngrunner.build.colour import NO_DRAWER_FRONT as NO_DRAWER_FRONT  # the colour's, re-exported
from ngrunner.build.colour import NO_MATERIALS as NO_MATERIALS
from ngrunner.build.colour import RoomSettings as RoomSettings
from ngrunner.build.desxml import CRLF, Element, XmlError, attrs, crlf, elements, num, one, set_attr
from ngrunner.build.products import Library, clone
from ngrunner.build.room import Fact, PanelSpec, RoomModel, WallSpec

TEMPLATE_JOB = "Lux Closet Template Claude"  # BUILD 144: the closet template job
TEMPLATE_ROOM = "Room1.des"  # BUILD 145: never Room0
ROOM_UID_BASE = 50000  # BUILD 149: UniqueID 50001+
WALL_SUFFIX = "_1"  # build_verticals.py 132: Wall="N_1"
DRAWER_ATTRS = ("NonColumnTopDrawer", "NonColumnMidDrawer", "NonColumnBotDrawer")  # build_jobs_0914.py 87-88
MATERIAL = "BaseMatTempSel"
CABINET = "3"  # SCHEMA 52-54: the Cabinet category
NOT_THE_CABINET = "88"  # SCHEMA 82-84: the blank template's cabinet id, never a real job's
_QUOTE = {'"': "&quot;"}


class TemplateError(Exception):
    """The template job's room isn't what the writer needs: plain words."""


# ----- the template


@dataclass(frozen=True)
class RoomTemplate:
    """What the writer takes from the template job's Room1 and from Jim's reference rooms."""
    version: str  # the file's first line
    text: str  # the template room, decoded, CRLF
    wall: str  # its one <Wall> element, the clone source
    joint: str | None  # a <WallJoint /> of a reference room; None when none had one
    source_lib: str  # its products' SourceLib, which is its AutofillLib (BUILD 289)
    fm84: str  # its "FM 84 " product, the FM panel Jim's builder clones (build_jobs_0914.py 33-37)
    categories: tuple[str, ...]  # its <BaseMatTempSel> CategoryIds, in order

    @property
    def wall_attrs(self) -> tuple[str, ...]:
        return tuple(attrs(self.wall[:self.wall.index(">") + 1]))

    @property
    def joint_attrs(self) -> tuple[str, ...]:
        return tuple(attrs(self.joint)) if self.joint else ()


def load_template(room1: bytes, reference_rooms: Iterable[bytes] = ()) -> RoomTemplate:
    """The template job's Room1 (BUILD 144-150), and the first <WallJoint> of Jim's reference rooms."""
    try:
        text = crlf(room1.decode("utf-8"))
        version, _, rest = text.partition(CRLF)
        if not version.strip().isdigit() or not rest.startswith("<?xml"):
            raise TemplateError("the template room doesn't start with its version line and the XML declaration")
        room = one(text, "Room")
        inside = (room.head_end, room.end)
        walls = one(text, "Walls", inside)
        wall = elements(text, "Wall", within=(walls.head_end, walls.end))
        if len(wall) != 1:
            raise TemplateError(f"the template room has {len(wall)} walls where its clone needs one")
        room_set = one(text, "RoomSet", inside)
        lib = attrs(room_set.head(text)).get("AutofillLib", "")
        products = one(text, "Products", inside)
        found = elements(text, "Product", within=(products.head_end, products.end))
        if not found or {attrs(p.head(text)).get("SourceLib") for p in found} != {lib}:
            raise TemplateError("the template room's products don't all come from its own library, so SourceLib "
                                "can't be read from it (BUILD 289)")
        fm = [p for p in found if attrs(p.head(text)).get("ProdName", "").strip() == "FM 84"]
        if len(fm) != 1:
            raise TemplateError("the template room has no single FM 84 to clone")
        cats = tuple(attrs(m.head(text)).get("CategoryId", "")
                     for m in elements(text, MATERIAL, within=(room_set.head_end, room_set.end)))
        joint = None
        for ref in reference_rooms:
            ref_text = ref.decode("utf-8", "replace")
            joints = elements(ref_text, "WallJoint")
            if joints:
                joint = joints[0].head(ref_text)
                break
    except UnicodeDecodeError as exc:
        raise TemplateError("the template room isn't UTF-8") from exc
    except XmlError as exc:
        raise TemplateError(f"the template room can't be read: {exc}") from exc
    return RoomTemplate(version.strip(), text, wall[0].of(text), joint, lib, fm[0].of(text), cats)


# ----- what the model doesn't hold: the colour (colour.py)


def materials_problem(run: str, template: RoomTemplate) -> str | None:
    """Why a lifted material run can't go into the room; None when it can."""
    text = crlf(run.strip())
    try:
        found = elements(text, MATERIAL)
    except XmlError:
        found = []
    rest = text
    for e in reversed(found):
        rest = rest[:e.start] + rest[e.end:]
    if not found or rest.strip():
        return "the materials given are not a run of <BaseMatTempSel> elements"
    cats = tuple(attrs(e.head(text)).get("CategoryId", "") for e in found)
    if sorted(cats) != sorted(template.categories):
        return (f"the materials given hold categories {', '.join(cats)}, and the template's room "
                f"{', '.join(template.categories)}")
    for e in found:
        if attrs(e.head(text)).get("CategoryId") == CABINET:
            sel = elements(text, "MaterialTemplateSelection", within=(e.head_end, e.end))
            if sel and attrs(sel[0].head(text)).get("RootTemplateId") == NOT_THE_CABINET:
                return "the materials given keep the blank template's cabinet id 88, which no real job uses (SCHEMA 82-84)"
    if "MatORId=" in text:
        return "the materials given carry a per-part override (MatORId), which beats the room's template (BUILD 157-159)"
    return None


# ----- the room


@dataclass(frozen=True)
class WrittenRoom:
    """A room's Room#.des, in memory, with the values written that Jim's rules flag for review, and the colour
    it was written in (gates.py's colour gate holds the bytes to it; commit.py records where it came from)."""
    number: int
    content: bytes
    review: tuple[str, ...] = ()
    settings: RoomSettings | None = None

    @property
    def file_name(self) -> str:
        return f"Room{self.number}.des"


@dataclass(frozen=True)
class BlockedRoom:
    """A room that is not written, and the values that block it: (where, what, why)."""
    number: int
    blocked_by: tuple[tuple[str, str, str], ...]

    @property
    def file_name(self) -> str:
        return f"Room{self.number}.des"


def blocking(model: RoomModel, settings: RoomSettings, template: RoomTemplate,
             library: Library) -> list[tuple[str, str, str]]:
    """Every value the room needs that isn't settled (AGENTS.md rule 8): the model's, then the writer's."""
    out = list(model.unverified())
    out += colour_blocking(settings, template)
    if len(model.walls) > 1 and template.joint is None:
        out.append(("room", "joints", "no reference room of Jim's has a <WallJoint> to clone (SCHEMA 9-30)"))
    for p in model.panels():
        name = p.product.value
        if name is not None and name not in library:
            why = library.problems.get(name, f"Jim's templates hold no {name}")
            out.append((f"wall {p.wall} panel {p.index + 1}", "product", why))
    for s in model.schedules:
        if model.wall(s.wall) is None and s.panels:
            out.append((f"wall {s.wall}", "wall", "its wall isn't in the WALL/AREA table"))
    return out


def colour_blocking(settings: RoomSettings, template: RoomTemplate) -> list[tuple[str, str, str]]:
    """What of the room's colour isn't settled, or isn't a run lifted from a known-good room (AGENTS.md rule 8)."""
    out = []
    materials, front = settings.materials, settings.drawer_front
    if not isinstance(materials, Fact):
        out.append(("room", "materials", HANDED_IN))
    elif not materials.settled:
        out.append(("room", "materials", materials.why))
    elif (why := still_holds(materials.value)) is not None:
        out.append(("room", "materials", why))
    elif (problem := materials_problem(materials.value.run, template)) is not None:  # type: ignore[union-attr]
        out.append(("room", "materials", problem))
    if not isinstance(front, Fact) or front.value is None:
        out.append(("room", "drawer front", front.why if isinstance(front, Fact) else NO_DRAWER_FRONT))
    elif front.value not in FRONTS:
        out.append(("room", "drawer front", "not a drawer front RULES 230-231 name"))
    return out


def reviews(model: RoomModel, settings: RoomSettings | None = None) -> tuple[str, ...]:
    """The values written but flagged Review by Jim's rules (SCHEMA 166, RULES 233)."""
    return (*(f"wall {p.wall} panel {p.index + 1}: {name.removesuffix('_mm')}: {fact.why}"
              for p in model.panels() for name, fact in p.facts() if fact.status == "review"),
            *(settings.reviews() if settings is not None else ()))


def placed_panels(model: RoomModel) -> Iterator[tuple[WallSpec, PanelSpec]]:
    """Every panel with its wall, in the order the file holds them: the walls' order, then left to right."""
    for wall in model.walls:
        for s in model.schedules:
            if s.wall == wall.name:
                for p in s.panels:
                    yield wall, p


def write_room(model: RoomModel, number: int, template: RoomTemplate, library: Library,
               settings: RoomSettings) -> WrittenRoom | BlockedRoom:
    """Room<number>.des from the model, or the values that block it."""
    if number < 1:
        return BlockedRoom(number, (("room", "number", "Room0 is the job's, not a design's (BUILD 152-160)"),))
    blocked = blocking(model, settings, template, library)
    if blocked:
        return BlockedRoom(number, tuple(blocked))
    lifted, front = settings.materials.value, settings.drawer_front.value
    assert isinstance(lifted, Lifted) and front is not None
    assert model.wall_height_mm.value is not None
    text = template.text
    room = one(text, "Room")
    inside = (room.head_end, room.end)
    products_xml, last_tag = _products(model, number, template, library)
    count = max([*(w.id_tag.value for w in model.walls if w.id_tag.value is not None), last_tag])
    room_set = one(text, "RoomSet", inside)
    mats = elements(text, MATERIAL, within=(room_set.head_end, room_set.end))
    if not mats:
        raise TemplateError("the template room has no material templates to replace")
    parms = one(text, "RoomParms", inside)
    room_head = room.head(text)
    for attr, value in (("UniqueID", str(ROOM_UID_BASE + number)), ("Name", escape(model.name, _QUOTE)),
                        ("IdTagCount", str(count))):
        room_head = set_attr(room_head, attr, value)
    set_head = room_set.head(text)
    for attr in DRAWER_ATTRS:
        set_head = set_attr(set_head, attr, escape(front, _QUOTE))
    changes: list[tuple[int, int, str]] = [
        (room.start, room.head_end, room_head),
        (parms.start, parms.head_end, set_attr(parms.head(text), "H_Walls", num(model.wall_height_mm.value))),
        (room_set.start, room_set.head_end, set_head),
        (mats[0].start, mats[-1].end, crlf(lifted.run)),  # whole, as it stands in its room (SCHEMA 75-76)
        (*_span(one(text, "Walls", inside)), _walls(model.walls, template)),
        (*_span(one(text, "WallJoints", inside)), _joints(model, template)),
        (*_span(one(text, "Products", inside)), products_xml),
    ]
    for start, end, new in sorted(changes, key=lambda c: c[0], reverse=True):  # from the end: spans still hold
        text = text[:start] + new + text[end:]
    return WrittenRoom(number, crlf(text).encode("utf-8"), reviews(model, settings), settings)


def _span(e: Element) -> tuple[int, int]:
    return e.start, e.end


def _container(name: str, items: Sequence[str]) -> str:
    if not items:
        return f"<{name} />"
    return f"<{name}>{CRLF}" + CRLF.join(items) + f"{CRLF}  </{name}>"


def _walls(walls: Sequence[WallSpec], template: RoomTemplate) -> str:
    head_end = template.wall.index(">") + 1
    head, body = template.wall[:head_end], template.wall[head_end:]
    out = []
    for w in walls:
        tag_no, length, height, angle, pos = (w.id_tag.value, w.length_mm.value, w.height_mm.value,
                                              w.angle.value, w.pos_mm.value)
        assert tag_no is not None and length is not None and height is not None and angle is not None and pos
        tag = head
        for attr, value in (("IDTag", str(tag_no)), ("Len", num(length)), ("Height", num(height)),
                            ("PosX", num(pos[0])), ("PosY", num(pos[1])), ("Ang", num(angle)),
                            ("WallNumber", w.name)):
            tag = set_attr(tag, attr, value)
        out.append("    " + tag + body)
    return _container("Walls", out)


def _joints(model: RoomModel, template: RoomTemplate) -> str:
    out = []
    for a, b in model.joints.value or ():
        assert template.joint is not None
        out.append("    " + set_attr(set_attr(template.joint, "Wall1", str(a)), "Wall2", str(b)))
    return _container("WallJoints", out)


def _products(model: RoomModel, number: int, template: RoomTemplate, library: Library) -> tuple[str, int]:
    """Each panel cloned from its library product; the product's own lines kept as they are, so an REA's
    drilling stays byte for byte its template's (RULES 423-427)."""
    first = max((w.id_tag.value for w in model.walls if w.id_tag.value is not None), default=0)
    out, tag = [], first
    for k, (wall, p) in enumerate(placed_panels(model), start=1):
        tag = first + k
        values = (p.product.value, p.height_mm.value, p.depth_mm.value, p.x_mm.value, p.elev_mm.value,
                  wall.position.value)
        assert None not in values
        name, height, depth, x, elev, position = values
        xml = clone(library[str(name)], height=float(height), depth=float(depth), x=float(x),  # type: ignore[arg-type]
                    elev=float(elev), wall=f"{position}{WALL_SUFFIX}", id_tag=tag,  # type: ignore[arg-type]
                    unique_id=(ROOM_UID_BASE + number) * 1000 + k, cab_no=k, source_lib=template.source_lib)
        out.append("    " + crlf(xml))
    return _container("Products", out), tag

