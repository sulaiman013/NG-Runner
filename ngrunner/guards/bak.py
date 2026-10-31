"""
The .bak reader: for one room, is its backup newer or larger than its design, and what differs
between the two, part by part.

Why it exists: RULES 81 says a .bak larger or newer than its .des means the .des is stale, and a
newer .bak is normal after Mozaik closes (Q19 quotes both sides). The design (section "The .bak
rule") answers it with a structural diff, and that rule waits for P3 and Jim's answer to Q19. So
this module only reads and reports: a BakFinding is facts and one plain line, never a verdict, and
nothing here can gate a check, a write or a card. It writes nothing, and it never raises for a room
it can't read; the line says so.

Files are read as bytes, as section 3.5 measured them: a version line 15, CRLF, then the XML, CRLF
throughout. Anything else could not be compared, and a file with line ends Mozaik doesn't write is
reported, since that is how a text-mode edit shows (FAILURES 17).

The diff is symmetric. Products are matched by UniqueID; parts (CabProdPart, DrawerProdPart,
DoorProdPart) by tag, name, type and their place among those alike; operations by tag and ID.
Numbers are compared as numbers, so 0 and 0.00 are the same. Only the annotation elements RULES 710
and BUILD 809 name are set apart; every other difference is listed. Which of them matter is Q19's to
settle, not this module's. Names in the words are plain: no attribute names, no paths.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

VERSION = b"15"                   # every .des, .bak, .sbk and .bak2-4 in Jobs A to J (3.5)
ANNOTATIONS = frozenset({"Annotations", "AnnotationSet", "AnnotationLocator"})   # RULES 710
_ROOM_FILE = re.compile(r"Room(\d+)", re.IGNORECASE)

Side = Literal["only in the design", "only in the backup", "differs"]


class DesignUnreadable(Exception):
    """A file that doesn't read as a Mozaik design as measured. Its text is plain, with no path."""


# ----- what a finding holds


@dataclass(frozen=True)
class PartChange:
    """A part of one product that differs. part is "<name> (<tag> <n>)", n counting the parts
    alike. fields name what differs: an attribute, an operation and its attribute
    ("OperationGroove 1 Hide"), or a child element."""

    product: str
    part: str
    change: Side
    fields: tuple[str, ...]


@dataclass(frozen=True)
class ProductChange:
    """A product only in one file, or differing in its own attributes and settings (not in its
    parts, which are PartChanges)."""

    product: str
    change: Side
    fields: tuple[str, ...]


@dataclass(frozen=True)
class RoomDiff:
    same_bytes: bool
    line_ends: tuple[str, ...]        # "design", "backup": not CRLF throughout
    room_fields: tuple[str, ...]      # the Room element's own attributes
    sections: tuple[str, ...]         # the room's other elements: Walls, RoomParms...
    products: tuple[ProductChange, ...]
    parts: tuple[PartChange, ...]
    annotations: bool

    @property
    def matches(self) -> bool:
        """Nothing differs but how the bytes are written."""
        return not (self.room_fields or self.sections or self.products or self.parts
                    or self.annotations)

    @property
    def only_annotations(self) -> bool:
        return self.annotations and not (self.room_fields or self.sections or self.products
                                         or self.parts)


@dataclass(frozen=True)
class BakFinding:
    """One room. backup_newer is by file time, None when a time can't be read; diff is None when
    the two could not be compared. words is the one line a person reads."""

    room: str
    design_bytes: int | None
    backup_bytes: int | None
    backup_newer: bool | None
    backup_larger: bool | None
    diff: RoomDiff | None
    words: str

    @property
    def newer_or_larger(self) -> bool | None:
        """RULES 81's condition, as a fact. What it means waits for Q19."""
        if self.backup_newer is None or self.backup_larger is None:
            return None
        return self.backup_newer or self.backup_larger


# ----- reading a job and a room


def check_job(folder: Path) -> tuple[BakFinding, ...]:
    """Every room with a design in folder, Room0 first, in number order."""
    try:
        rooms = sorted((int(found[1]), path.stem) for path in folder.iterdir()
                       if path.suffix.lower() == ".des"
                       and (found := _ROOM_FILE.fullmatch(path.stem)))
    except OSError:
        return (BakFinding("", None, None, None, None, None,
                           "The job's folder could not be read, so no room was compared."),)
    return tuple(check_room(folder, stem) for _, stem in rooms)


def check_room(folder: Path, room: str) -> BakFinding:
    """room's design against its backup. Never raises: what can't be read is said in words."""
    name = _room_words(room)
    design_path, backup_path = folder / f"{room}.des", folder / f"{room}.bak"
    design_stat, backup_stat = _stat(design_path), _stat(backup_path)
    if design_stat is None:
        return BakFinding(room, None, None, None, None, None, f"{name} has no design.")
    if backup_stat is None:
        return BakFinding(room, design_stat[0], None, None, None, None, f"{name} has no backup.")
    sizes = (design_stat[0], backup_stat[0])
    larger = sizes[1] > sizes[0]
    newer = backup_stat[1] > design_stat[1]
    try:
        diff = diff_designs(_read(design_path, "design"), _read(backup_path, "backup"))
    except DesignUnreadable as err:
        return BakFinding(room, *sizes, newer, larger, None,
                          f"{name}'s design and backup could not be compared: {err}.")
    return BakFinding(room, *sizes, newer, larger, diff, _words(name, sizes, newer, diff))


def _stat(path: Path) -> tuple[int, float] | None:
    try:
        found = path.stat()
    except OSError:
        return None
    return (found.st_size, found.st_mtime) if path.is_file() else None


def _read(path: Path, side: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        raise DesignUnreadable(f"the {side} file could not be read") from None


# ----- the diff


def diff_designs(design: bytes, backup: bytes) -> RoomDiff:
    """What differs between two designs' bytes, both ways. Raises DesignUnreadable."""
    ours, theirs = _parse(design, "design"), _parse(backup, "backup")
    line_ends = tuple(side for side, data in (("design", design), ("backup", backup))
                      if data.count(b"\n") != data.count(b"\r\n"))
    return RoomDiff(
        same_bytes=design == backup,
        line_ends=line_ends,
        room_fields=_attribute_changes(ours, theirs),
        sections=_section_changes(ours, theirs),
        products=_product_changes(ours, theirs),
        parts=_part_changes(ours, theirs),
        annotations=_annotations(ours) != _annotations(theirs),
    )


def _parse(data: bytes, side: str) -> ElementTree.Element:
    head, sep, rest = data.partition(b"\r\n")
    if not sep or not head.isdigit():
        raise DesignUnreadable(f"the {side} does not start with Mozaik's version line")
    if head != VERSION:
        raise DesignUnreadable(f"the {side} is in a version this reader has not seen")
    try:
        root = ElementTree.fromstring(rest)
    except ElementTree.ParseError:
        raise DesignUnreadable(f"the {side} is not a design Mozaik wrote") from None
    if root.tag != "Room":
        raise DesignUnreadable(f"the {side} is not a design Mozaik wrote")
    return root


def _value(text: str) -> tuple[str, float | str]:
    """An attribute as it compares: a number as a number, anything else exactly."""
    try:
        number = float(text)
    except ValueError:
        return ("text", text)
    return ("number", number) if math.isfinite(number) else ("text", text)


def _canon(element: ElementTree.Element, skip: frozenset[str] = ANNOTATIONS) -> tuple:
    """An element with its children, as it compares, leaving out the tags in skip."""
    return (element.tag,
            tuple(sorted((key, _value(value)) for key, value in element.attrib.items())),
            (element.text or "").strip(),
            tuple(_canon(child, skip) for child in element if not _skipped(child.tag, skip)))


_PARTS = "*ProdPart"        # in a skip set: CabProdPart, DrawerProdPart and DoorProdPart


def _skipped(tag: str, skip: frozenset[str]) -> bool:
    return tag in skip or (_PARTS in skip and tag.endswith("ProdPart"))


def _attribute_changes(ours: ElementTree.Element, theirs: ElementTree.Element) -> tuple[str, ...]:
    keys = sorted(set(ours.attrib) | set(theirs.attrib))
    return tuple(key for key in keys
                 if _value(ours.get(key, "")) != _value(theirs.get(key, ""))
                 or (key in ours.attrib) != (key in theirs.attrib))


def _keyed(elements: Iterable[ElementTree.Element], key) -> dict[tuple, ElementTree.Element]:
    """Elements by key and their place among those with the same key."""
    seen: dict[tuple, int] = {}
    out: dict[tuple, ElementTree.Element] = {}
    for element in elements:
        base = key(element)
        seen[base] = seen.get(base, 0) + 1
        out[(*base, seen[base])] = element
    return out


def _children_changes(ours: ElementTree.Element, theirs: ElementTree.Element,
                      skip: frozenset[str]) -> tuple[str, ...]:
    """The tags of children that differ or are in one only, in the order first met."""
    mine = _keyed((c for c in ours if not _skipped(c.tag, skip)), lambda c: (c.tag,))
    other = _keyed((c for c in theirs if not _skipped(c.tag, skip)), lambda c: (c.tag,))
    changed = [key[0] for key in (*mine, *(k for k in other if k not in mine))
               if key not in mine or key not in other
               or _canon(mine[key], skip) != _canon(other[key], skip)]
    return tuple(dict.fromkeys(changed))


def _section_changes(ours: ElementTree.Element, theirs: ElementTree.Element) -> tuple[str, ...]:
    return _children_changes(ours, theirs, ANNOTATIONS | {"Products"})


def _products(root: ElementTree.Element) -> dict[tuple, ElementTree.Element]:
    holder = root.find("Products")
    return _keyed(holder if holder is not None else (), lambda p: (p.get("UniqueID", ""),))


def _product_changes(ours: ElementTree.Element,
                     theirs: ElementTree.Element) -> tuple[ProductChange, ...]:
    mine, other = _products(ours), _products(theirs)
    changes = [ProductChange(key[0], "only in the backup" if key not in mine
                             else "only in the design", ())
               for key in (*mine, *other) if (key in mine) != (key in other)]
    skip = ANNOTATIONS | {_PARTS}
    for key in (k for k in mine if k in other):
        found = (_attribute_changes(mine[key], other[key])
                 + _children_changes(mine[key], other[key], skip))
        if found:
            changes.append(ProductChange(key[0], "differs", found))
    return tuple(sorted(changes, key=lambda c: (c.change != "differs", c.product)))


def _parts(product: ElementTree.Element) -> dict[tuple, ElementTree.Element]:
    return _keyed((e for e in product.iter() if e.tag.endswith("ProdPart")),
                  lambda p: (p.tag, p.get("Name", ""), p.get("Type", "")))


def _part_label(key: tuple) -> str:
    tag, name, kind, place = key
    return f"{name or kind} ({tag} {place})"


def _part_changes(ours: ElementTree.Element, theirs: ElementTree.Element) -> tuple[PartChange, ...]:
    mine, other = _products(ours), _products(theirs)
    changes: list[PartChange] = []
    for key in (k for k in mine if k in other):
        a, b = _parts(mine[key]), _parts(other[key])
        for part in (*a, *(k for k in b if k not in a)):
            if part not in a or part not in b:
                side: Side = "only in the backup" if part not in a else "only in the design"
                changes.append(PartChange(key[0], _part_label(part), side, ()))
                continue
            found = _part_fields(a[part], b[part])
            if found:
                changes.append(PartChange(key[0], _part_label(part), "differs", found))
    return tuple(changes)


def _part_fields(ours: ElementTree.Element, theirs: ElementTree.Element) -> tuple[str, ...]:
    """A part's attributes, then its operations by tag and ID, then its other children."""
    found = list(_attribute_changes(ours, theirs))
    mine, other = _operations(ours), _operations(theirs)
    for key in (*mine, *(k for k in other if k not in mine)):
        label = f"{key[0]} {key[1]}"
        if key not in mine:
            found.append(f"{label} only in the backup")
        elif key not in other:
            found.append(f"{label} only in the design")
        else:
            found += [f"{label} {name}" for name in _attribute_changes(mine[key], other[key])]
            found += [f"{label} {tag}" for tag in _children_changes(mine[key], other[key],
                                                                     ANNOTATIONS)]
    found += _children_changes(_without_operations(ours), _without_operations(theirs),
                               ANNOTATIONS)
    return tuple(found)


def _operations(part: ElementTree.Element) -> dict[tuple, ElementTree.Element]:
    holder = part.find("PartOpsXml")
    ops = (op for op in (holder if holder is not None else ()) if op.tag.startswith("Operation"))
    return _keyed(ops, lambda op: (op.tag, op.get("ID", "")))


def _without_operations(part: ElementTree.Element) -> ElementTree.Element:
    """The part with its operations taken out, so they are not counted twice."""
    copy = ElementTree.Element(part.tag)
    for child in part:
        if child.tag == "PartOpsXml":
            kept = ElementTree.SubElement(copy, child.tag, child.attrib)
            kept.extend(op for op in child if not op.tag.startswith("Operation"))
        else:
            copy.append(child)
    return copy


def _annotations(root: ElementTree.Element) -> list[tuple]:
    return [_canon(element, frozenset()) for element in _outermost(root, ANNOTATIONS)]


def _outermost(element: ElementTree.Element, tags: frozenset[str]) -> Iterator[ElementTree.Element]:
    for child in element:
        if child.tag in tags:
            yield child
        else:
            yield from _outermost(child, tags)


# ----- words


def _room_words(room: str) -> str:
    found = _ROOM_FILE.fullmatch(room)
    return f"Room {int(found[1])}" if found else "The room"


def _words(name: str, sizes: tuple[int, int], newer: bool, diff: RoomDiff) -> str:
    if diff.same_bytes:
        return f"{name}'s backup is identical to its design."
    size = ("larger than" if sizes[1] > sizes[0] else "smaller than" if sizes[1] < sizes[0]
            else "the same size as")
    head = f"{name}'s backup is {size} its design" + (" and newer" if newer else "")
    clauses = _content(diff)
    for side in diff.line_ends:
        clauses.append(f"the {side} has line ends Mozaik does not write")
    return f"{head}; {'; '.join(clauses)}."


def _content(diff: RoomDiff) -> list[str]:
    if diff.matches:
        return ["their contents match part for part"]
    if diff.only_annotations:
        return ["only the annotations differ"]
    clauses: list[str] = []
    if diff.parts:
        products = len({c.product for c in diff.parts})
        verb = "differs" if len(diff.parts) == 1 else "differ"
        clauses.append(f"{_count(len(diff.parts), 'part')} in {_count(products, 'product')}"
                       f" {verb}")
    for side in ("only in the design", "only in the backup"):
        n = sum(1 for c in diff.products if c.change == side)
        if n:
            clauses.append(f"{_count(n, 'product')} {'is' if n == 1 else 'are'} {side}")
    changed = [c for c in diff.products if c.change == "differs"]
    numbering = [c for c in changed if c.fields == ("CabNo",)]
    if numbering:
        clauses.append(f"cabinet numbers differ on {_count(len(numbering), 'product')}")
    if len(changed) > len(numbering):
        n = len(changed) - len(numbering)
        clauses.append(f"{_count(n, 'product')} {'differs' if n == 1 else 'differ'} in size,"
                       " place or settings")
    if diff.room_fields or diff.sections:
        shown = dict.fromkeys(_SECTION_WORDS.get(tag, "other room settings")
                              for tag in diff.sections)
        if diff.room_fields:
            shown.setdefault("the room's settings")
        clauses += [f"{words} differ" for words in shown]
    if diff.annotations:
        clauses.append("the annotations differ")
    return clauses


_SECTION_WORDS = {
    "Walls": "the walls", "WallJoints": "the wall joints", "RoomParms": "the room's settings",
    "RoomSet": "the room's settings", "Tops": "the tops", "Moldings": "the moldings",
    "ToeSkins": "the toe skins", "Notes": "the notes", "RoomNotes": "the notes",
    "Appls": "the appliances", "Fixts": "the fixtures", "RenderSettings": "the view settings",
}


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"
