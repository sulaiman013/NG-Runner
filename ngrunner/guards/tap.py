"""
Reading a CNC program (.TAP): how far it cuts, and which holes it drills twice.

Why it exists: the programs on Drive are what the machine will cut, and the one nest fact NG Runner
can read without Jim's toolkit (docs/p1b/build-spec.md, sections 3.1 and 5.1). A clean read can only
say "nothing past this size": it never proves the right sheet.

The sheet comes only from where a tool really cuts. A G81 line is a drill-bank reference (it reaches
X 53 on a 49in sheet), the last G00 is a park move at Y 108 whatever the sheet, and G0, G28 and G53
cut nothing. A line with no G word carries on with the last one, and real programs lean on that for
both cuts and drill-bank references, so the G word in force is tracked rather than read per line.

A merged program is the lower-case .tap the merging tool writes, whatever it was named: the spec's
<a>-<b>.tap and comb cover most of them, not all. Measured on all 1,663 real programs, 2026-10-30:
each of the 228 .tap files carries that tool's line numbers, 202 hold several sheets, and on 105 of
the 106 named <a>-<b> the stops between sheets count b-a+1. None of the 1,435 .TAP files has a stop,
a tool loaded twice or a hole repeated exactly, and that includes the 51 with <a>-<b> in the name,
such as a long form's 3-4: 12 of those hold doubled holes, and reading them as merged would hide
them.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Literal

Family = Literal["sheet", "long", "merged", "remake", "hand"]

NEAR_IN = 0.0197                 # 0.5 mm, RULES' "within 0.5mm": this close is one doubled hole
_MEASURE_DP = 6                  # a file's 4 decimals, kept clear of a subtraction's float noise
_MEASURE_NOISE = 1e-6

_COMMENT = re.compile(r"\([^)]*(?:\)|$)")
_WORD = re.compile(r"([A-Z])([-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+))")
_M30 = re.compile(r"(?:N[0-9]+\s*)?M0*30")

_RAPID, _LINE = 0, 1
_CUTTING = (_LINE, 2, 3)
_MOTION = (_RAPID, *_CUTTING)
_END_CANNED = 80
_CANNED = frozenset(range(73, 90)) - {_END_CANNED}
_NOT_A_MOVE = frozenset({4, 28, 43, 53})     # a dwell, home, tool length, machine coordinates
_INCH, _METRIC = 20, 21

_LONG = re.compile(r"S[0-9]+R[0-9]+|flip", re.IGNORECASE)
_SHEET = re.compile(r"S[0-9]{2}$")
_REMAKE = re.compile(r"R[Mm]|RD|(?i:fix|pile)")


@dataclass(frozen=True)
class NearPair:
    tool: str
    depth: float
    apart_in: float
    x: float
    y: float


@dataclass(frozen=True)
class Tap:
    """One program. complete: it ends M30 then %. reach: the largest X and Y of its cutting moves,
    None when it has none. near_pairs is empty for a merged program."""

    name: str
    sha256: str
    size: int
    inches: bool
    complete: bool
    family: Family
    reach: tuple[float, float] | None
    holes: int
    near_pairs: tuple[NearPair, ...]


def read_tap(name: str, data: bytes) -> Tap:
    """Read one program. It never raises on content: a bad file comes back with complete=False."""
    lines = _lines(data)
    seen = _walk(lines)
    family = family_of(name)
    return Tap(
        name=name, sha256=hashlib.sha256(data).hexdigest(), size=len(data), inches=seen.inches,
        complete=_is_complete(lines), family=family, reach=seen.reach(), holes=len(seen.holes),
        near_pairs=() if family == "merged" else _near_pairs(seen.holes),
    )


def family_of(name: str) -> Family:
    """Which kind of program its file name says it is. Only "merged" changes what is checked, and
    only the extension decides that: a surname can hold "comb", and a long form holds "3-4"."""
    stem = name.rpartition(".")[0]
    if name.endswith(".tap"):
        return "merged"
    if _LONG.search(stem):
        return "long"
    if _SHEET.search(stem):
        return "sheet"
    if _REMAKE.search(stem):
        return "remake"
    return "hand"


# ----- the lines of a program


def _lines(data: bytes) -> list[str]:
    """Each line in capitals with its comment gone: a comment can hold a job's name, or numbers."""
    return [_COMMENT.sub("", line).strip().upper() for line in data.decode("latin-1").splitlines()]


def _is_complete(lines: list[str]) -> bool:
    tail = [line for line in lines if line][-2:]
    return len(tail) == 2 and tail[1] == "%" and _M30.fullmatch(tail[0]) is not None


def _tool_name(number: str) -> str:
    """T6, however it was written: T6, T06 or T6.0. By digits, so any length of them is safe."""
    return "T" + (number.lstrip("+-").split(".")[0].lstrip("0") or "0")


# ----- one pass over the lines: where the tool goes and what it cuts


@dataclass(frozen=True)
class _Hole:
    tool: str
    depth: float
    x: float
    y: float


@dataclass
class _Seen:
    """What a pass over the lines knows so far: the G word in force, where the tool is, what it has
    cut. A position is the last one a move named, as the machine holds it."""

    inches: bool = False
    mode: int | None = None
    x: float | None = None
    y: float | None = None
    tool: str | None = None
    next_tool: str | None = None
    plunge: tuple[float, float, float] | None = None
    far_x: float | None = None
    far_y: float | None = None
    holes: list[_Hole] = field(default_factory=list)

    def read(self, line: str) -> None:
        words = _WORD.findall(line)
        codes = [float(value) for letter, value in words if letter == "G"]
        self._note_units(codes)
        self._note_tool(words)
        if any(code in _NOT_A_MOVE for code in codes):
            return
        self._note_mode(codes)
        axes = {letter: float(value) for letter, value in words if letter in "XYZ"}
        if self.mode in _MOTION and axes:
            self._move(axes)

    def reach(self) -> tuple[float, float] | None:
        if self.far_x is None and self.far_y is None:
            return None
        return (self.far_x or 0.0, self.far_y or 0.0)

    def _note_units(self, codes: list[float]) -> None:
        for code in codes:
            if code == _INCH:
                self.inches = True
            elif code == _METRIC:
                self.inches = False

    def _note_tool(self, words: list[tuple[str, str]]) -> None:
        for letter, value in words:
            if letter == "T":
                self.next_tool = value
        if any(letter == "M" and float(value) == 6 for letter, value in words):
            self.tool = "T?" if self.next_tool is None else _tool_name(self.next_tool)
            self.plunge = None

    def _note_mode(self, codes: list[float]) -> None:
        for code in codes:
            if code in _MOTION or code in _CANNED:
                self.mode = int(code)
            elif code == _END_CANNED:
                self.mode = None

    def _move(self, axes: dict[str, float]) -> None:
        x, y = axes.get("X", self.x), axes.get("Y", self.y)
        self._settle_plunge(axes, x, y)
        if self.tool is not None and self.mode in _CUTTING:
            self._cuts_at(self.x, self.y)      # it cuts from where it is, and to where it goes
            self._cuts_at(x, y)
            if self.mode == _LINE and set(axes) == {"Z"} and x is not None and y is not None:
                self.plunge = (x, y, axes["Z"])
        self.x, self.y = x, y

    def _settle_plunge(self, axes: dict[str, float], x: float | None, y: float | None) -> None:
        """A plunge is a hole only if the very next move is a rapid back up at the same spot. That
        leaves out a router's lead-in, which ramps while moving, and would give thousands of
        pairs."""
        plunge, self.plunge = self.plunge, None
        if plunge is None or self.mode != _RAPID or self.tool is None:
            return
        spot_x, spot_y, depth = plunge
        if axes.get("Z", depth) > depth and (x, y) == (spot_x, spot_y):
            self.holes.append(_Hole(self.tool, depth, spot_x, spot_y))

    def _cuts_at(self, x: float | None, y: float | None) -> None:
        if x is not None:
            self.far_x = x if self.far_x is None else max(self.far_x, x)
        if y is not None:
            self.far_y = y if self.far_y is None else max(self.far_y, y)


def _walk(lines: Iterable[str]) -> _Seen:
    seen = _Seen()
    for line in lines:
        seen.read(line)
    return seen


# ----- near pairs


def _near_pairs(holes: Iterable[_Hole]) -> tuple[NearPair, ...]:
    """Every two holes of one tool and one depth, more than 0 and at most NEAR_IN apart, in the
    order the program drills them. Different tools at one spot are a counterbore, which RULES
    allows, so they are never compared."""
    by_cut: dict[tuple[str, float], list[tuple[float, float, int]]] = {}
    for index, hole in enumerate(holes):
        by_cut.setdefault((hole.tool, hole.depth), []).append((hole.x, hole.y, index))
    found: list[tuple[int, int, NearPair]] = []
    for (tool, depth), spots in by_cut.items():
        for first, second, apart in _close_spots(sorted(spots)):
            earlier, later = sorted((first, second), key=lambda spot: spot[2])
            found.append((earlier[2], later[2],
                          NearPair(tool, depth, apart, earlier[0], earlier[1])))
    return tuple(pair for _, _, pair in sorted(found, key=lambda entry: entry[:2]))


def _close_spots(spots: list[tuple[float, float, int]]) -> Iterator[tuple[tuple, tuple, float]]:
    """Spots sorted by X: each one need only be compared with those within the limit in X."""
    for at, spot in enumerate(spots):
        for other in spots[at + 1:]:
            if other[0] - spot[0] > NEAR_IN + _MEASURE_NOISE:
                break
            apart = round(math.hypot(other[0] - spot[0], other[1] - spot[1]), _MEASURE_DP)
            if 0 < apart <= NEAR_IN:
                yield spot, other, apart
