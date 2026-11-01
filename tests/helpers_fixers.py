"""
Made-up rooms and fakes of Jim's fixers and checks, for the fixers NG Runner plans with (lock/topline.py,
lock/banding.py). Each fake replays fixed bytes and fixed lines, as tests/test_lock_fixes.py's FakeJim
does: no rule of his is written here, only what his scripts print, in the shape his print statements give
(read from his source), for rooms made up here. Job names are made up.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ngrunner.guards.facts import ToolRun

CRLF = "\r\n"


def des(*body: str) -> bytes:
    """A room as Mozaik writes one: version line 15, CRLF throughout, then the XML."""
    lines = ["15", '<?xml version="1.0" encoding="utf-8"?>', '<Room Name="Made up" Quan="1">', *body, "</Room>", ""]
    return CRLF.join(lines).encode("utf-8")


# ----- the top line


def topline_room(e4: str = "1127.1", e6: str = "1127.125", *, h4: str = "1204", count: str = "1",
                 w6: str = "19") -> bytes:
    """An FM panel C2 reaching 2132, and two WM panels, C4 and C6, 1204 high: off the line at these Elevs."""
    return des("  <Products>",
               '    <Product ProdName="FM 84 " CabNo="2" Width="19" Height="2132" Elev="0" Depth="356">',
               "    </Product>",
               f'    <Product ProdName="WM Panel 48 Thru" CabNo="4" Width="19" Height="{h4}" Elev="{e4}" Depth="356">',
               "    </Product>",
               f'    <Product ProdName="WM Panel 48 Thru" CabNo="6" Width="{w6}" Height="1204" Elev="{e6}" Depth="356">',
               "    </Product>",
               "  </Products>",
               f'  <Annotations Count="{count}" />')


TOP_WRONG = topline_room()
TOP_FIXED = topline_room("928", "928")
TOP_HIT = "   Room1.des    WM Panel 48 Thru           H=1204    Elev  1127.1 ->   928.0  (top 2331 -> 2132)"
TOP_HIT_AT_928 = "   Room1.des    WM Panel 48 Thru           H=1204    Elev   928.0 ->   900.0  (top 2132 -> 2104)"
TOP_ALREADY = "   Room1.des    already on its top line (2132)"


def top_check(hits: int, line: str = TOP_HIT) -> str:
    lines = [line] * hits if hits else [TOP_ALREADY]
    return "\n".join([*lines, "", f"would change: {hits} WM panels"]) + "\n"


class FakeTopLine:
    """Jim's top_line.py, replayed: --check prints a panel line for each WM panel at Elev 1127.1 or 1127.125
    (two, as TOP_WRONG is written), and without it the room is rewritten as fixes says."""

    def __init__(self, *, fixed: bytes = TOP_FIXED, check: dict[bytes, str] | None = None) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.fixes = {TOP_WRONG: fixed, **{topline_room(count=c): topline_room("928", "928", count=c) for c in "2345"}}
        self.checks = check or {}

    def __call__(self, snapshot: Any, script: str, args: list[str], *, timeout_s: int = 120,
                 python: Any = None) -> ToolRun:
        self.calls.append((script, list(args)))
        room = Path(args[-1])
        data = room.read_bytes()
        if args[0] == "--check":
            said = self.checks.get(data)
            if said is None:
                said = top_check(data.count(b'Elev="1127.1"') + data.count(b'Elev="1127.125"'))
            return ToolRun(0, said, "", False)
        if data in self.fixes:
            room.write_bytes(self.fixes[data])
        return ToolRun(0, "\nchanged: 2 WM panels\n", "", False)


# ----- WM banding


def point(i: int, x: str, y: str, band: str, lock: str, side: str = "") -> str:
    return (f'          <ShapePoint ID="{i}" X="{x}" Y="{y}" PtType="0" Data="0" EdgeType="0" Anchor="" EBand="{band}"'
            f' X_Eq="" Y_Eq="" Data_Eq="" LAdj="0" RAdj="0" TAdj="0" BAdj="0" Scribe="0" Source="0" BoreHoles="0"'
            f' EBandLock="{lock}" SideName="{side}" />')


def wm_room(*, top: tuple[str, str] = ("1", "True"), front: tuple[str, str] = ("1", "False"),
            bottom: tuple[str, str] = ("0", "False"), back: tuple[str, str] = ("0", "False"),
            outline: str = "0", count: str = "1", name: str = "WM Panel 48 Thru") -> bytes:
    """A WM panel C7: its part's outline (back, top, front, bottom edge points) and its product's own
    outline. As written, its front isn't locked and its bottom isn't taped."""
    return des("  <Products>",
               f'    <Product ProdName="{name}" CabNo="7" Width="19.304" Height="756" Elev="1376">',
               "      <CabProdParts>",
               '        <CabProdPart Name=" WM Panel" Type="FEnd" X="0" Y="356">',
               '        <PartShapeXml Name="" Type="0">',
               point(0, "0", "0", *back),
               point(1, "756", "0", *top, side="Top"),
               point(2, "756", "356", *front, side="Front"),
               point(3, "0", "356", *bottom, side="Bottom"),
               "        </PartShapeXml>",
               "        </CabProdPart>",
               "      </CabProdParts>",
               point(0, "0", "0", outline, "False"),
               point(1, "19.05", "0", "0", "False"),
               "    </Product>",
               "  </Products>",
               f'  <Annotations Count="{count}" />')


WM_WRONG = wm_room()
WM_FIXED = wm_room(front=("1", "True"), bottom=("1", "True"))
WM_LINE = "   !! WM Panel 48 Thru         WM banding not taped + locked on top/front/bottom"
NOTCH_CLEAN = "   every notch is on its line-bore face; WM banding taped and locked"


class FakeWm:
    """wm_eb.enforce_room, called (toolkit.run_room_call), and notch_check.py run, replayed: the call writes
    fixed for WM_WRONG, and the check prints a banding line while C7's bottom is untaped."""

    def __init__(self, *, fixed: bytes = WM_FIXED, still_flags: bool = False, lines: list[str] | None = None,
                 call_fails: bool = False) -> None:
        self.fixed, self.still_flags, self.lines, self.call_fails = fixed, still_flags, lines, call_fails
        self.calls: list[tuple[str, ...]] = []

    def call(self, snapshot: Any, module: str, function: str, room: Path, out: Path, *, timeout_s: int = 120,
             python: Any = None) -> ToolRun:
        self.calls.append((module, function, str(room), str(out)))
        if self.call_fails:
            return ToolRun(1, "", "Traceback (most recent call last):\nValueError: no\n", False)
        data = room.read_bytes()
        replies = {WM_WRONG: self.fixed, **{wm_room(count=c): wm_room(front=("1", "True"), bottom=("1", "True"),
                                                                       count=c) for c in "2345"}}
        out.write_bytes(replies.get(data, data))
        return ToolRun(0, "", "", False)

    def run(self, snapshot: Any, script: str, args: list[str], *, timeout_s: int = 120, python: Any = None) -> ToolRun:
        self.calls.append((script, *args))
        room = Path(args[0])
        data = room.read_bytes()
        bad = b'EBand="0" X_Eq="" Y_Eq="" Data_Eq="" LAdj="0" RAdj="0" TAdj="0" BAdj="0" Scribe="0" Source="0"' \
              b' BoreHoles="0" EBandLock="False" SideName="Bottom"' in data or self.still_flags
        lines = self.lines if self.lines is not None else ([WM_LINE] if bad else [NOTCH_CLEAN])
        faults = sum(1 for line in lines if line.startswith("   !! "))
        return ToolRun(1 if faults else 0, "\n".join([room.name, *lines]) + "\n", "", False)


# ----- countertops


CROSSCHECK = '''CHECKS = [
    ("notch_check",      lambda j, c: product_rooms(j),                     False),
    ("eb_tops",          lambda j, c: ["--check", "3763"] + product_rooms(j), False),
]
'''


def countertop_room(*, back: tuple[str, str] = ("0", "False"), part_band: str = "1", slot6: str = "1",
                    count: str = "1", second: tuple[str, str] | None = None) -> bytes:
    """A countertop T32, 939.8 by 431.8, its four edge points (the back one not taped), a WM panel C7 whose
    part is taped in slot part_band, and the room's banding slot 6. second: another countertop T33's back."""
    tops = ['    <Top Type="1" Wall="2_1" Length="939.7999" D="431.8" Thick="19.05" IDTag="32">',
            '      <ShapeXml Name="" Type="0">',
            point(0, "0", "0", "1", "True"),
            point(0, "939.7999", "0", "1", "True"),
            point(0, "939.7999", "431.8", *back),
            point(0, "0", "431.8", "1", "True"),
            "      </ShapeXml>",
            "    </Top>"]
    if second is not None:
        tops += ['    <Top Type="1" Wall="3_1" Length="1374.775" D="589.28" Thick="19.05" IDTag="33">',
                 '      <ShapeXml Name="" Type="0">',
                 point(0, "0", "0", "1", "True"),
                 point(0, "1374.775", "0", "1", "True"),
                 point(0, "1374.775", "589.28", *second),
                 point(0, "0", "589.28", "1", "True"),
                 "      </ShapeXml>",
                 "    </Top>"]
    return des(f'  <BaseMatTempSel CategoryId="5" PartType="EdgeBand6" Id="{slot6}" ManuallyChanged="False"'
               ' MissingTextureName="Made up" />',
               "  <Products>",
               '    <Product ProdName="WM Panel 48 Thru" CabNo="7" Width="19.304" Height="756" Elev="1376">',
               '        <CabProdPart Name=" WM Panel" Type="FEnd" X="0" Y="356">',
               '        <PartShapeXml Name="" Type="0">',
               point(0, "0", "0", "0", "False"),
               point(1, "756", "0", part_band, "True", side="Top"),
               "        </PartShapeXml>",
               "        </CabProdPart>",
               "    </Product>",
               "  </Products>",
               "  <Tops>", *tops, "  </Tops>",
               f'  <Annotations Count="{count}" />')


TOPS_WRONG = countertop_room()
TOPS_FIXED = countertop_room(back=("1", "True"))
TOPS_LINE = "   Room1.des    COUNTERTOP 37.000in x 17.000in  four edges, slot 1"
TOPS_LINE_33 = "   Room1.des    COUNTERTOP 54.125in x 23.200in  four edges, slot 1"
TOPS_CORRECT = "   Room1.des    already correct"


class FakeEbTops:
    """Jim's eb_tops.py, replayed: --check prints a countertop line for T32 (and T33) while its back edge is
    untaped, and without it the room is rewritten as fixes says."""

    def __init__(self, *, fixes: dict[bytes, bytes] | None = None, lines: list[str] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fixes = {TOPS_WRONG: TOPS_FIXED, **{countertop_room(count=c): countertop_room(back=("1", "True"), count=c)
                                                for c in "2345"}, **(fixes or {})}
        self.lines = lines

    def __call__(self, snapshot: Any, script: str, args: list[str], *, timeout_s: int = 120,
                 python: Any = None) -> ToolRun:
        self.calls.append(list(args))
        room = Path(args[-1])
        data = room.read_bytes()
        if args[0] == "--check":
            lines = self.lines
            if lines is None:
                untaped = point(0, "939.7999", "431.8", "0", "False").strip().encode()
                untaped33 = point(0, "1374.775", "589.28", "0", "False").strip().encode()
                lines = ([TOPS_LINE] if untaped in data else []) + ([TOPS_LINE_33] if untaped33 in data else [])
            hits = len(lines)
            said = (lines or [TOPS_CORRECT]) + ["", f"would change: {hits} part(s)/slot(s)"]
            return ToolRun(0, "\n".join(said) + "\n", "", False)
        if data in self.fixes:
            room.write_bytes(self.fixes[data])
        return ToolRun(0, "\nchanged: 1 part(s)/slot(s)\n", "", False)


def nest(material_tags: list[tuple[str, bool]]) -> bytes:
    """A made-up nest holding parts by AssyNo, each flipped by hand or not."""
    parts = "".join(f'<OptimizePart PartID="{n}" Quan="1" Name="Part" AssyNo="{tag}" Width="300" Length="900" />'
                    for n, (tag, _) in enumerate(material_tags, 1))
    places = "".join(f'<OptimizePartLocation PartID="{n}" X="1" Y="1" Flipped="{"True" if flipped else "False"}" />'
                     for n, (_, flipped) in enumerate(material_tags, 1))
    return ('8\r\n<?xml version="1.0" encoding="utf-8"?>\r\n<OptimizeMaterial DisplayName="x" Width="1244.6"'
            f' Length="2463.8" MaterialId="1"><RoomInfo Number="1" />{parts}<OptimizeSheet Quan="1"'
            f' Flipped="False" Width="1244.6" Length="2463.8">{places}</OptimizeSheet></OptimizeMaterial>'
            ).encode("utf-8")
