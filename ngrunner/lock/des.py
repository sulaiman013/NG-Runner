r"""
A room's .des as the fixers read it: each value by its place in the design, never by its offset, and the
staged copy each of Jim's fixers and checks runs on.

Why one module: every fixer says what Jim's script changed by the same few places, a product by its CabNo,
a part by its order in the product, a point of a part's outline by its order in that outline, a countertop
by its IDTag, and an attribute on one of them. Read the same way for the plan, for Undo after a save and
for survival, a value is the same value wherever the bytes around it moved. Only bytes are read: a .des is
pure CRLF with a version line before its XML (RULES section 6), and nothing here parses or re-writes it.

Nothing of Jim's is here: these are the element and attribute names Mozaik writes, read for where a
value is, and never a rule of his.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from ngrunner.lock.fixes import FixRefused
from ngrunner.toolkit import ToolkitError, refuse_unless_local

PRODUCT = re.compile(rb"<Product [^>]*>")
CAB = re.compile(rb'\sCabNo="([^"]*)"')
PART = re.compile(rb"<CabProdPart [^>]*>")
PART_SHAPE = re.compile(rb"<PartShapeXml[ >][^>]*>")
TOP = re.compile(rb"<Top [^>]*>")
IDTAG = re.compile(rb'\sIDTag="([^"]*)"')
TOP_SHAPE = re.compile(rb"<ShapeXml[ >][^>]*>")
POINT = re.compile(rb"<ShapePoint [^>]*/>")
ROOM_NAME = re.compile(r"Room[0-9]+\.des")


def products(data: bytes) -> Iterator[tuple[re.Match[bytes], int]]:
    """Each <Product ...> tag, and where its product ends."""
    for m in PRODUCT.finditer(data):
        end = data.find(b"</Product>", m.end())
        yield m, (len(data) if end < 0 else end)


def attr(pattern: re.Pattern[bytes], tag: bytes) -> str:
    found = pattern.search(tag)
    return found.group(1).decode("utf-8", errors="replace") if found else ""


def attribute(name: str) -> re.Pattern[bytes]:
    """name="value" in a tag, the whole name: Elev, never MaxElev."""
    return re.compile(rb'\s' + re.escape(name.encode("ascii")) + rb'="([^"]*)"')


def attributes(tag: bytes) -> dict[str, str]:
    return {k.decode("ascii"): v.decode("utf-8", errors="replace")
            for k, v in re.findall(rb'\s(\w+)="([^"]*)"', tag)}


def the_product(data: bytes, cab: str) -> tuple[re.Match[bytes], int] | None:
    """The one product with this CabNo, or None when there isn't exactly one."""
    found = [(m, end) for m, end in products(data) if attr(CAB, m.group(0)) == cab]
    return found[0] if len(found) == 1 else None


def value_span(data: bytes, tag_start: int, tag: bytes, name: str) -> tuple[int, int] | None:
    """Where attribute name's value is, in a tag that starts at tag_start. None when it isn't there once."""
    found = list(attribute(name).finditer(tag))
    if len(found) != 1:
        return None
    return tag_start + found[0].start(1), tag_start + found[0].end(1)


def parts(data: bytes, start: int, end: int) -> list[tuple[int, int]]:
    """Each <CabProdPart> between start and end, as (its tag's start, where it ends)."""
    out = []
    for m in PART.finditer(data, start, end):
        close = data.find(b"</CabProdPart>", m.end(), end)
        out.append((m.start(), end if close < 0 else close))
    return out


def shape_points(data: bytes, start: int, end: int, shape: re.Pattern[bytes],
                 close: bytes) -> list[re.Match[bytes]] | None:
    """The <ShapePoint/> tags of the one outline (shape, ended by close) between start and end. None when
    there isn't exactly one outline there."""
    shapes = list(shape.finditer(data, start, end))
    if len(shapes) != 1:
        return None
    stop = data.find(close, shapes[0].end(), end)
    return list(POINT.finditer(data, shapes[0].end(), end if stop < 0 else stop))


def tops(data: bytes) -> Iterator[tuple[re.Match[bytes], int]]:
    """Each countertop's <Top ...> tag (not <Tops>), and where it ends."""
    for m in TOP.finditer(data):
        end = data.find(b"</Top>", m.end())
        yield m, (len(data) if end < 0 else end)


def room_number(room_name: str) -> int | None:
    found = re.fullmatch(r"Room([0-9]+)\.des", room_name, re.IGNORECASE)
    return int(found[1]) if found else None


def room_words(room_name: str) -> str:
    number = room_number(room_name)
    return "A room" if number is None else f"Room {number}"


class staged:
    """A folder of its own under work, holding data as room_name, gone when the run ends."""

    def __init__(self, work: Path, room_name: str, data: bytes) -> None:
        if not ROOM_NAME.fullmatch(room_name):
            raise FixRefused("Only a room's .des is fixed.")
        self.work, self.room_name, self.data = work, room_name, data

    def __enter__(self) -> tuple[Path, Path]:
        try:
            refuse_unless_local(self.work, "A fix's staged copy")
        except ToolkitError as exc:
            raise FixRefused(str(exc)) from exc
        self.work.mkdir(parents=True, exist_ok=True)
        self.stage = Path(tempfile.mkdtemp(prefix="fix-", dir=self.work))
        room = self.stage / "in" / self.room_name
        room.parent.mkdir()
        with open(room, "xb") as out:
            out.write(self.data)
        return self.stage, room

    def __exit__(self, *exc: object) -> None:
        shutil.rmtree(self.stage, ignore_errors=True)       # made by this run


# ----- the points of an outline: a part's, by its product and order, or a countertop's, by its IDTag


def point_places(data: bytes) -> dict[int, tuple[str, str]]:
    """Each <ShapePoint/> NG Runner can name, by where its tag starts: (the product's CabNo and
    "<part>.<point>", each counted from 0 in its product and its part's outline), or ("" and
    "T<IDTag>.<point>") for a countertop's. Any other point (a product's own outline, say) isn't named."""
    places: dict[int, tuple[str, str]] = {}
    for m, end in products(data):
        cab = attr(CAB, m.group(0))
        for k, (start, stop) in enumerate(parts(data, m.end(), end)):
            for j, point in enumerate(shape_points(data, start, stop, PART_SHAPE, b"</PartShapeXml>") or ()):
                places[point.start()] = (cab, f"{k}.{j}")
    for m, end in tops(data):
        tag = attr(IDTAG, m.group(0))
        for j, point in enumerate(shape_points(data, m.end(), end, TOP_SHAPE, b"</ShapeXml>") or ()):
            places[point.start()] = ("", f"T{tag}.{j}")
    return places


def point_changes(base: bytes, result: bytes, *, allowed: frozenset[str],
                  who: str) -> list[tuple[str, str, str, str, str]]:
    """Each attribute that differs on a <ShapePoint/> of base and result, as (cab, id, attribute, before,
    after) by point_places. The two must hold the same points in the same order, each point changed named
    once in both, and only allowed attributes may differ. who: the fixer, for the refusal's words."""
    more = FixRefused(f"{who} changed more than the banding it fixes, so no fix is planned.")
    old, new = list(POINT.finditer(base)), list(POINT.finditer(result))
    if len(old) != len(new):
        raise more
    here, there = point_places(base), point_places(result)
    named, named_after = Counter(here.values()), Counter(there.values())
    found = []
    for x, y in zip(old, new):
        if x.group(0) == y.group(0):
            continue
        a, b = attributes(x.group(0)), attributes(y.group(0))
        moved = {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)}
        if not moved <= allowed or a.keys() != b.keys():
            raise more
        place = here.get(x.start())
        if place is None or there.get(y.start()) != place:
            raise FixRefused(f"{who} changed a point NG Runner can't name, so no fix is planned.")
        if named[place] != 1 or named_after[place] != 1:
            raise FixRefused(f"{who} changed a point whose name another point has too, so no fix is planned.")
        found += [(*place, k, a[k], b[k]) for k in sorted(moved)]
    return found


def point_span(data: bytes, cab: str, id_: str, attribute_name: str) -> tuple[int, int] | None:
    """Where attribute_name's value is on the point point_places names (cab, id_). None when that point,
    or its product, part, countertop or outline, isn't there exactly once."""
    head, _, index = id_.rpartition(".")
    if not index.isdecimal():
        return None
    if cab:
        product = the_product(data, cab)
        if product is None or not head.isdecimal():
            return None
        found = parts(data, product[0].end(), product[1])
        if int(head) >= len(found):
            return None
        points = shape_points(data, *found[int(head)], PART_SHAPE, b"</PartShapeXml>")
    else:
        if not head.startswith("T"):
            return None
        hits = [(m, end) for m, end in tops(data) if attr(IDTAG, m.group(0)) == head[1:]]
        if len(hits) != 1:
            return None
        points = shape_points(data, hits[0][0].end(), hits[0][1], TOP_SHAPE, b"</ShapeXml>")
    if points is None or int(index) >= len(points):
        return None
    point = points[int(index)]
    return value_span(data, point.start(), point.group(0), attribute_name)
