"""
Board parts: which PULLED rows are cut from a sheet, as what kind, how thick they are built, in
which colour, and the rows the rules don't place.

Why it exists: this is the step Jim's toolkit lacks (docs/p2/build-spec.md, section 1 item 2). His
takeoff.py starts from parts the chat typed; his parse_pulled.py reads every row but gives no
thickness, built material or colour. Here every rule comes from rules.py, which mirrors his
takeoff.py and cites RULES, and nothing is filled in by judgment.

Never a guess (ruling R4): a row that looks like board but that the rules don't place is
Unplaced, with a reason in plain words, and reads "could not verify". A wrong board count is a
wrong order and a wrong cut, so a missing size, a thickness the rules don't name, a 1in part in a
colour, a kind not on the list or a colour the summary doesn't settle is never filled in.

A client's details can't travel from here. A part holds codes and numbers only. An Unplaced keeps
its row for the caller, and its reason is made of fixed words, codes and numbers; named() says
where to find the row on the summary without quoting its description.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from ngrunner.convert import rules
from ngrunner.convert.summary_lines import SECTIONS, SummaryLine

Source = Literal["pulled", "pulled_hardware"]


@dataclass(frozen=True)
class BoardPart:
    """One PULLED row cut from a sheet. a_in and b_in are IC's two face dimensions, b the banded
    one (RULES 173). printed_thk is IC's thickness (0.0 on a hardware line, which prints none);
    built_thk is what the rules build it at. colour is the IC colour code its bucket takes."""

    kind: str
    qty: int
    a_in: float
    b_in: float
    printed_thk: float
    built_thk: float
    codes: tuple[str, ...]
    colour: str
    wall_mount: bool
    source: Source

    @property
    def sqft(self) -> float:
        return self.qty * self.a_in * self.b_in / 144.0

    @property
    def tape_ft(self) -> float:
        return self.qty * rules.tape_ft(self.kind, self.a_in, self.b_in, self.wall_mount)


@dataclass(frozen=True)
class Unplaced:
    """A row the rules don't place: the "could not verify" list. reason is plain words, codes
    and numbers, never the row's own text."""

    line: SummaryLine
    reason: str

    def named(self) -> str:
        """Where to find the row on the summary, and why it isn't placed."""
        item = f" of item {self.line.item}" if _ITEM_CODE.fullmatch(self.line.item) else ""
        return f"Page {self.line.page + 1}: {self.line.qty}{item}, {self.reason}"

    def safe(self) -> Unplaced:
        """The same, with the row's text left out: what a takeoff keeps."""
        item = self.line.item if _ITEM_CODE.fullmatch(self.line.item) else ""
        section = self.line.section if self.line.section in SECTIONS else ""
        return Unplaced(replace(self.line, section=section, item=item, desc="", dims=""),
                        self.reason)


# ----- reading one row

_ITEM_CODE = re.compile(r"\d{8,10}|[A-Z]{2,5}(?:-[A-Z0-9]{1,4})?")
_ITEM_KIND = re.compile(r"(?P<kind>[A-Z]{2,5})(?:-[A-Z0-9]+)?")
_SIZE3 = re.compile(r"(?<![\w.])(?P<a>\d+(?:\.\d+)?)x(?P<b>\d+(?:\.\d+)?)x(?P<t>\d+(?:-\d+/\d+|/\d+|\.\d+)?)"
                    r"(?![\w/.])")
_SIZE2 = re.compile(r"(?<![\w.])\d+(?:\.\d+)?x\d+(?:\.\d+)?(?![\w.])")
# takeoff.TYPE's alternatives, from rules.TYPE_KINDS. Its second words are matched in any case, so
# TR BASE reads as TR Base.
_KIND = re.compile(r"^(?P<kind>" + "|".join(re.escape(k) for k in rules.TYPE_KINDS) + r")\b",
                   re.IGNORECASE)
_NOMINAL = re.compile(r"0\d")                     # 08x24: a size written with a leading zero
_HAMPER = re.compile(r"^HP\b")
_SHELF_FORM = re.compile(r"^Shelf \d")
_CODE_START = re.compile(r"^(?P<code>[A-Z]{2,5})\b")
_CODE = re.compile(r"[A-Z][A-Z0-9]*(?:[-/][A-Z0-9]+)*")
_COLOUR_CANDIDATE = re.compile(r"[A-Z]{2}")


def board_part(line: SummaryLine) -> BoardPart | Unplaced | None:
    """The row as a board part, Unplaced with its reason, or None when it isn't a board row."""
    desc = line.desc.strip()
    if rules.DISREGARD.search(desc):
        return None                                   # Scribe and Quarter Round (RULES 251)
    kind = _kind_of(desc)
    item_kind = _item_kind(line.item)
    if kind is None:
        if item_kind in rules.ITEM_BOARD_CODES and not rules.NOT_BOARD_WORDS.match(desc):
            kind = "FF Cleat" if item_kind == "FF" else item_kind   # Door Euro: the item says DR
        else:
            return _not_a_kind(line, desc)
    elif item_kind in rules.ITEM_BOARD_CODES and item_kind != kind.split()[0]:
        return Unplaced(line, "its item code and description name different kinds")
    return _read(line, desc, kind)


def _kind_of(desc: str) -> str | None:
    """The kind as takeoff.py's TYPE and normalise() name it (rules.RENAMES), from the
    description. A rename's word is matched in any case, so TR BASE reads as TR Base."""
    if _HAMPER.match(desc):
        return "HP"
    found = _KIND.match(desc)
    words = desc.split()
    first = (next(k for k in rules.TYPE_KINDS if k.casefold() == found["kind"].casefold())
             if found else (words[0] if words else ""))
    for kind, word, renamed in rules.RENAMES:
        if first == kind and (not word or re.search(rf"\b{word}\b", desc, re.IGNORECASE)):
            return renamed
    return first if found else None


def _item_kind(item: str) -> str | None:
    found = _ITEM_KIND.fullmatch(item.strip())
    return found["kind"] if found else None


def _not_a_kind(line: SummaryLine, desc: str) -> BoardPart | Unplaced | None:
    """A row whose description names no board kind: a rail cover printed as hardware, the Shelf
    form, a code the rules don't name that prints a size, or (most rows) not board at all."""
    sized = _SIZE3.search(desc) or _SIZE2.search(desc)
    if rules.RAIL_COVER_LINE.search(desc) and not sized:
        a_in, b_in = rules.RAIL_COVER_FACE
        return BoardPart("TR Rail Cover", line.qty, a_in, b_in, 0.0, rules.RAIL_COVER_BUILT, (), "",
                         False, "pulled_hardware")
    if _SHELF_FORM.match(desc):
        return Unplaced(line, "a shelf printed with no thickness (the Shelf form)")
    code = _CODE_START.match(desc)
    if (code and sized and code["code"] not in rules.NOT_BOARD_CODES
            and _item_kind(line.item) not in rules.NOT_BOARD_CODES):
        return Unplaced(line, f"a {code['code']} line with a size: a kind the rules don't name")
    return None


def _read(line: SummaryLine, desc: str, kind: str) -> BoardPart | Unplaced | None:
    size = _SIZE3.search(desc)
    if kind == "DR" and rules.GLASS_DOOR_CODE in desc.split():
        return None                                   # a glass door is bought (BUILD 79)
    if kind == "HP":
        return Unplaced(line, "a hamper front (HP): the rules give it no edge banding")
    if size is None:
        if _SIZE2.search(desc):
            return Unplaced(line, f"a {kind} printed with no thickness")
        return Unplaced(line, f"a {kind} printed with no size")
    if _NOMINAL.match(size["a"]) or _NOMINAL.match(size["b"]):
        # The third description form (build spec 3.1, pair PBA): an 08 face is built 7.4.
        return Unplaced(line, f"a {kind} printed at a nominal size: its built size isn't printed")
    printed = rules.thickness(size["t"])
    if kind == "TR" and printed != 0.6:
        return Unplaced(line, "a trim line (TR) that is neither a base nor a rail cover")
    after = desc[size.end():].split()
    codes = tuple(t for t in desc[:size.start()].split()[1:] + after if _CODE.fullmatch(t))
    colour = next((t for t in after if _COLOUR_CANDIDATE.fullmatch(t) and t not in rules.FLAG_CODES),
                  None)
    if colour is not None and colour not in rules.COLOUR_BRANDS:
        # Not echoed: two capitals in a description may be anything, a state's among them.
        return Unplaced(line, f"a {kind} in a colour code the rules don't know")
    built, why = _built(kind, printed, size["t"], colour)
    if why:
        return Unplaced(line, why)
    if colour is None:
        return Unplaced(line, f"a {kind} with no colour code printed")
    return BoardPart(kind, line.qty, float(size["a"]), float(size["b"]), float(printed or 0.0),
                     built, codes, colour, bool(rules.WALL_MOUNT.search(desc)), "pulled")


def _built(kind: str, printed: float | None, text: str, colour: str | None) -> tuple[float, str]:
    """The built thickness, or a reason it can't be placed."""
    if printed == 0.6:
        return 0.0, f"a {kind} printed 0.6in thick, which the rules don't say is board"
    if kind == "TR Rail Cover":
        return rules.RAIL_COVER_BUILT, ""
    if kind == "BP" and printed is not None and printed < rules.BACK_PANEL_UNDER:
        return rules.BACK_PANEL_BUILT, ""
    if kind == "WS" and printed in rules.CLOSET_WS_PRINTED:
        return rules.CLOSET_WS_BUILT, ""
    if printed == rules.THREE_QUARTER:
        return rules.THREE_QUARTER, ""
    if printed == rules.ONE_INCH:
        if colour is not None and colour != rules.WHITE:
            return 0.0, f"a 1in {kind} in {colour}: 1in exists only in white (RULES 217)"
        return rules.ONE_INCH, ""
    return 0.0, f"a {kind} printed {text}in thick, which the rules don't name for it"


# ----- the room's colour


def settle_colours(judged: Sequence[tuple[SummaryLine, BoardPart | Unplaced]],
                   designs: Mapping[str, Sequence[SummaryLine]]) -> tuple[BoardPart | Unplaced, ...]:
    """
    Gives a worksurface and a rail cover printed with no colour their room's colour.

    RULES 221 builds a closet worksurface in its room's own colour, and RULES 224 puts a rail
    cover in its room's 3/4 bucket. On a job whose other 3/4 parts share one colour, that is the
    colour. On a job with more, the design lists say which rooms hold the row: rows printed alike
    are taken together, and each room that lists them gives its own colour to as many as it lists,
    when every such room's other 3/4 parts share one colour. What no room lists, or a room of two
    colours holds, is Unplaced, since any colour would be a guess.
    """
    colours = {p.colour for _, p in judged
               if isinstance(p, BoardPart) and not _needs_room(p) and p.built_thk == 0.75}
    rooms = _room_colours(designs) if len(colours) > 1 else []
    groups: dict[str, list[tuple[SummaryLine, BoardPart]]] = defaultdict(list)
    for line, found in judged:
        if isinstance(found, BoardPart) and _needs_room(found) and len(colours) > 1:
            groups[line.desc.strip()].append((line, found))
    out: list[BoardPart | Unplaced] = []
    for line, found in judged:
        if not isinstance(found, BoardPart) or not _needs_room(found):
            out.append(found)
        elif len(colours) == 1:
            out.append(_in_room(line, found, next(iter(colours))))
        elif not colours:
            out.append(found if found.colour else Unplaced(
                line, "a rail cover printed with no colour, on a job with no 3/4 board colour"))
        elif groups[line.desc.strip()][0][0] is line:
            out.extend(_split_by_room(groups[line.desc.strip()], rooms))
    return tuple(out)


def _needs_room(part: BoardPart) -> bool:
    return part.kind == "WS" or (part.kind == "TR Rail Cover" and not part.colour)


def _room_colours(designs: Mapping[str, Sequence[SummaryLine]]
                  ) -> list[tuple[str | None, Counter[str]]]:
    """Each design's own colour (None when its other 3/4 parts aren't one colour), and how many
    of each description it lists."""
    rooms = []
    for lines in designs.values():
        own: set[str] = set()
        held: Counter[str] = Counter()
        for line in lines:
            held[line.desc.strip()] += line.qty
            found = board_part(line)
            if isinstance(found, BoardPart) and not _needs_room(found) and found.built_thk == 0.75:
                own.add(found.colour)
        rooms.append((next(iter(own)) if len(own) == 1 else None, held))
    return rooms


def _split_by_room(group: list[tuple[SummaryLine, BoardPart]],
                   rooms: list[tuple[str | None, Counter[str]]]) -> list[BoardPart | Unplaced]:
    """Rows printed alike, given the colours of the rooms that list them; the rest Unplaced."""
    line, part = group[0]
    desc = line.desc.strip()
    total = sum(p.qty for _, p in group)
    by_colour: dict[str, int] = defaultdict(int)
    for colour, held in rooms:
        if held[desc] and colour is None:
            return [_unsettled(l, p) for l, p in group]
        if held[desc] and colour is not None:
            by_colour[colour] += held[desc]
    listed = sum(by_colour.values())
    if not listed or listed > total:
        return [_unsettled(l, p) for l, p in group]
    out = [_in_room(replace(line, qty=qty), replace(part, qty=qty), colour)
           for colour, qty in by_colour.items()]
    if listed < total:
        out.append(_unsettled(replace(line, qty=total - listed), part))
    return out


def _in_room(line: SummaryLine, part: BoardPart, room: str) -> BoardPart | Unplaced:
    """The part in its room's colour. A worksurface printed in another colour is Unplaced: RULES
    221 builds a closet worksurface in its room's own colour, and RULES 190 builds a part in a
    second colour in its own, so which one wins is Jim's call."""
    if part.kind == "WS" and part.colour != room:
        return Unplaced(line, f"a worksurface printed {part.colour} in a room built {room}: RULES"
                              " 221 builds it in the room's colour, RULES 190 a second colour in"
                              " its own")
    return replace(part, colour=room)


def _unsettled(line: SummaryLine, part: BoardPart) -> Unplaced:
    if part.kind == "WS":
        return Unplaced(line, "a worksurface on a job with more than one colour, whose room's"
                              " colour the design lists don't settle (RULES 221)")
    return Unplaced(line, "a rail cover printed with no colour, on a job with more than one"
                          " colour, whose room the design lists don't settle (RULES 224)")
