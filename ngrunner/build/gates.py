"""
The gates on a written room: its bytes read back and held to the model, to the wall's IC lines and to
Jim's rules, before anything is committed.

Why: "GATE before the rooms are written" (RULES 313-322), and every build of Jim's ends in gates that stop
it (BUILD 255-258, 296; the audit at the end of each per-job build script). A room commits only when every
gate here passed; one that failed or could not verify stops it (commit.py). Each gate reads the written
file, never the writer's intentions, so a writer bug shows here. Ported from his toolkit of 2026-10-31,
never copied:

  file          UTF-8, CRLF only, the template's version line, and an XML parser reads it (build_jobs_0914.py
                159-163)
  settled       the model holds no could-not-verify value (AGENTS.md rule 8): the writer refuses such a room,
                and this holds it again on the bytes' own model
  attributes    every <Wall> and <WallJoint> has the cloned attribute set exactly (SCHEMA 9-30)
  walls         each wall and joint as the model has it, every value within 0.0005 mm of the model's (BUILD
                291: "audit every written value"); IdTagCount the highest IDTag (SCHEMA 32-37); the walls
                clockwise, net turning negative (RULES 454-457; regression.py 78-82)
  panels        each product as its model panel: name, wall, X, Elev, Height, Depth (BUILD 291)
  gates 1 to 5  per wall: panels == the VT lines' quantities (1), FM and WM counts by the notch token (2),
                radius products == VT RAD lines (3); every height on 32k+20 within 1.3 mm (4); every WM
                panel's top on the room's one T and its Elev on 32k (5) (RULES 313-319). T is read as
                top_line.py's room_top reads it (RULES 333-339)
  gate 6        per wall, left to right, each placed panel's height against the drawing as Jim's
                elev_panels.py measures it (RULES 320-322); a wall it can't measure could not verify. His
                script renders the page with poppler's pdftoppm by name (its line 23), run unchanged: the
                pdftoppm.exe NG Runner's settings name (Settings.pdftoppm), else one on PATH, put first on
                the PATH his script sees. With none, or one that isn't poppler's, nothing is run and gate 6
                could not verify: never a stand-in renderer. A page his wall-box finder can't read (a
                portrait page) could not verify, and says so
  colour        the written <BaseMatTempSel> run is, byte for byte, the run lifted from a known-good room,
                which that room still holds, and is its shade's (SCHEMA 46-85); the three drawer fronts are
                the settled one (RULES 230-233; colour.py)
  legal names   every ProdName one of the legal ones, never the June REA names (RULES 386-390, 415-417)
  drill codes   per wall, the sides of the panels that need one (WM, REA) are its lines' FD/LH/RH codes as a
                set (RULES 371-372, 388-390; panel_drill.py gate)
  notch         a WM Panel 48 ships its hang notch twice, one active and one hidden; the active one full
                ("FEnd.th") on Thru and skinned on Left and Right, and as its library product has it
                (RULES 355-359, 404-409; SCHEMA 126-147; wm_variant_check.py a). An REA's active notch is on
                its line bores' face (RULES 405, notch_side.py)
  groove        every WM Panel 48's hang-notch cached X = height - 191.9 (RULES 400-403). An REA's that isn't
                is reported, never corrected (RULES 426)
  WM banding    every WM panel's outline: an edge reaching the front half taped and locked, the rest bare
                (RULES 431-447, wm_eb.py audit)
  REA           its drilling byte for byte its library product's (RULES 423-427, rea_panel.drilling_untouched),
                and its slant resized (RULES 419-421, rea_panel.gate)
  line bores    none on a panel but an REA (BUILD 288)
  part          a panel's one part is the panel: W == Depth, L == Height (part_geom_check.py)
  SourceLib     every product's the template room's (BUILD 289); no MatORId anywhere (BUILD 157-159)
"""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ngrunner.build import placement as pl
from ngrunner.build.colour import FRONTS, Lifted, RoomSettings, material_run, run_problem, still_holds
from ngrunner.build.desxml import XmlError, attrs, crlf, elements, one, start_tags
from ngrunner.build.products import (
    GROOVE_EQ, GROOVE_OFFSET_MM, LEGAL, SLANT_ALONG_MM, SLANT_INTO_MM, SUPERSEDED, Library, is_rea, is_wm,
    operations, outline_edges,
)
from ngrunner.build.room import Fact, ICLine, ReadWall, RoomModel
from ngrunner.build.schedule import DRILL_SIDE, drill, vt_lines
from ngrunner.build.walls import net_turn
from ngrunner.build.writer import DRAWER_ATTRS, WALL_SUFFIX, RoomTemplate, WrittenRoom, placed_panels

Status = Literal["passed", "failed", "could not verify"]
AUDIT_MM = 0.0005  # three places written: a value read back is the model's within half the last place
TOP_TOL_MM = 0.05  # regression.py 98: a WM top on T
ELEV_GRID_TOL = 0.002  # regression.py 100: Elev / 32 off a whole number by more than this
GEOM_TOL_MM = 1e-3  # rea_panel.py gate, wm_panel's groove X
FULL_NOTCH = "FEnd.th"  # RULES 357
SKINNED_NOTCH = "FEnd.th-HangNotchSkin"  # RULES 358-359
X_TOL_IN = 1.0  # gate 6: a drawn vertical at a panel's X, its outer face one face short at a wall (elev_panels)
OVERLAP_IN = 1.0  # gate 6: a drawn line and a panel share more than 1 in of height
BOX_TOL_IN = 0.5  # gate 6: a line reaching the wall's top, within half an inch, is the wall's box
BOX_FLOOR_IN = 2.0  # ... and the floor, within 2 in (frozen Z: 98.5 down to 2.0)
ELEV_PANELS = "scripts/elev_panels.py"
PDFTOPPM = "pdftoppm.exe"  # what elev_panels.py's subprocess.run(["pdftoppm", ...]) finds on Windows
POPPLER = "poppler"  # pdftoppm -v names its makers: The Poppler Developers
NO_PDFTOPPM = "the drawing check needs poppler's pdftoppm, which isn't on this PC"
REQUIRED = ("file", "settled", "attributes", "walls", "panels", "gate 1", "gate 2", "gate 3", "gate 4", "gate 5",
            "gate 6", "colour", "legal names", "drill codes", "notch", "groove", "WM banding", "REA", "line bores",
            "part", "SourceLib")  # every gate a room must pass before it is committed (commit.py)


@dataclass(frozen=True)
class Gate:
    name: str
    status: Status
    items: tuple[str, ...] = ()
    cites: str = ""


@dataclass(frozen=True)
class RoomGates:
    """Every gate on one written room, and what Jim's rules say to report without stopping it."""
    room: str
    gates: tuple[Gate, ...]
    review: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.missing() and all(g.status == "passed" for g in self.gates)

    def missing(self) -> list[str]:
        """The gates that should have run on the room and didn't."""
        ran = {g.name for g in self.gates}
        return [name for name in REQUIRED if name not in ran]

    def gate(self, name: str) -> Gate:
        return next(g for g in self.gates if g.name == name)

    def not_passed(self) -> list[Gate]:
        return [g for g in self.gates if g.status != "passed"]


# ----- the drawing, as Jim's elev_panels.py measures it


@dataclass(frozen=True)
class DrawnVertical:
    """One line of elev_panels.py's table: x, top and bottom, inches."""
    x_in: float
    top_in: float
    bottom_in: float

    @property
    def length_in(self) -> float:
        return self.top_in - self.bottom_in


@dataclass(frozen=True)
class Measured:
    """A wall's drawing as measured, or why it couldn't be."""
    verticals: tuple[DrawnVertical, ...] | None
    why: str = ""


_ROW = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*$")
_HEADER = re.compile(r"^\s*x \(in\)\s+top\s+bottom\s+drawn len\s*$")


def read_measure(stdout: str) -> Measured:
    """elev_panels.py's printed table (its lines 76-78), failing closed on anything else."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines or not _HEADER.match(lines[0]):
        return Measured(None, "elev_panels.py didn't print its table")
    out = []
    for line in lines[1:]:
        m = _ROW.match(line)
        if m is None:
            return Measured(None, f"elev_panels.py printed a line it never prints: {line.strip()[:60]}")
        x, top, bottom, length = map(float, m.groups())
        if abs((top - bottom) - length) > 0.11:
            return Measured(None, "elev_panels.py printed a length that isn't its top less its bottom")
        out.append(DrawnVertical(x, top, bottom))
    return Measured(tuple(out))


def find_pdftoppm(setting: Path | None = None, *, environ: Mapping[str, str] = os.environ,
                  timeout_s: int = 15) -> Path | str:
    """Poppler's pdftoppm.exe for elev_panels.py, or why there is none, in words gate 6 reports. setting:
    Settings.pdftoppm, the exe or its folder (the one hook for a pinned poppler); without it, PATH is searched.
    One that isn't pdftoppm.exe, or whose -v doesn't name poppler, is refused: never a stand-in renderer."""
    if setting is not None:
        exe = setting / PDFTOPPM if setting.is_dir() else setting
        if exe.name.casefold() != PDFTOPPM or not exe.is_file():
            return "the pdftoppm NG Runner's settings name isn't there, so the drawing check can't run"
    else:
        found = shutil.which(PDFTOPPM, path=environ.get("PATH", ""))
        if found is None:
            return NO_PDFTOPPM
        exe = Path(found)
    return exe if is_poppler(exe, timeout_s=timeout_s) else (
        "the pdftoppm found here isn't poppler's (its -v doesn't name it), so the drawing check didn't run")


def is_poppler(exe: Path, *, timeout_s: int = 15) -> bool:
    """Whether exe says it is poppler's pdftoppm: `pdftoppm -v` names The Poppler Developers."""
    try:
        said = subprocess.run([str(exe), "-v"], stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout_s,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return POPPLER in (said.stdout + said.stderr).decode("utf-8", "replace").casefold()


def page_shape(pdf: Path, page: int) -> str | None:
    """portrait or landscape, as the page is shown (its /Rotate applied); None when it can't be read."""
    import pypdf

    try:
        shown = pypdf.PdfReader(str(pdf)).pages[page - 1]
        width, height = float(shown.mediabox.width), float(shown.mediabox.height)
        if int(shown.get("/Rotate", 0) or 0) % 180:
            width, height = height, width
    except Exception:  # noqa: BLE001 - pypdf raises many kinds on a file it can't read; the shape is then unknown
        return None
    return "portrait" if height > width else "landscape"


def no_wall_box(pdf: Path, page: int) -> str:
    """Why a page Jim's wall-box finder stopped on could not verify, in plain words (frozen job X's pages)."""
    shape = page_shape(pdf, page)
    where = f"a {shape} page" if shape else "its page"
    return (f"Jim's elev_panels.py found no wall box on page {page}, {where}: his finder needs the wall's box to "
            "fill 38% of the page's height, so this wall's drawing could not be measured")


def measure_wall(snapshot: object, pdf: Path, page: int, wall_in: float, wall_h_in: float, *,
                 pdftoppm: Path | str | None, python: Path | None = None, timeout_s: int = 120) -> Measured:
    """Jim's elev_panels.py from the adopted toolkit copy, unchanged, on one elevation page (RULES 321-322).
    pdftoppm: find_pdftoppm's answer. Without an exe nothing is run, and the reason is the measurement's."""
    from ngrunner import toolkit

    if not isinstance(pdftoppm, Path):
        return Measured(None, pdftoppm or NO_PDFTOPPM)
    try:
        args = [str(pdf), str(page), f"{wall_in:g}", f"{wall_h_in:g}"]
        said = toolkit.run_tool(snapshot, ELEV_PANELS, args, timeout_s=timeout_s, python=python,  # type: ignore[arg-type]
                                on_path=(pdftoppm.parent,))
    except toolkit.ToolkitError as exc:
        return Measured(None, f"elev_panels.py could not be run: {exc}")
    if said.exit_code != 0:
        if "no wall box found" in said.stderr:
            return Measured(None, no_wall_box(pdf, page))
        if "pdftoppm" in said.stderr:
            return Measured(None, "elev_panels.py couldn't render the page with pdftoppm")
        if said.timed_out:
            return Measured(None, "elev_panels.py didn't finish")
        tail = (said.stderr.strip().splitlines() or ["no reason given"])[-1]
        return Measured(None, f"elev_panels.py stopped: {tail[:120]}")
    return read_measure(said.stdout)


# ----- the written room, read back


@dataclass(frozen=True)
class Written:
    text: str
    room: dict[str, str]
    walls: list[dict[str, str]]
    wall_tags: list[str]
    joints: list[dict[str, str]]
    joint_tags: list[str]
    products: list[tuple[dict[str, str], str]]  # (the Product tag's attributes, its whole element)


def read_back(content: bytes) -> Written | str:
    """The written room's parts, or why it can't be read."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return "it isn't UTF-8"
    try:
        room = one(text, "Room")
        inside = (room.head_end, room.end)
        walls = one(text, "Walls", inside)
        joints = one(text, "WallJoints", inside)
        prods = one(text, "Products", inside)
        wall_tags = start_tags(walls.of(text), "Wall")
        joint_tags = start_tags(joints.of(text), "WallJoint")
        products = [(attrs(p.head(text)), p.of(text))
                    for p in elements(text, "Product", within=(prods.head_end, prods.end))]
    except XmlError as exc:
        return str(exc)
    return Written(text, attrs(room.head(text)), [attrs(t) for t in wall_tags], wall_tags,
                   [attrs(t) for t in joint_tags], joint_tags, products)


def _f(value: str | None) -> float:
    try:
        return float(value or "nan")
    except ValueError:
        return math.nan


def _off(a: float, b: float | None, tol: float = AUDIT_MM) -> bool:
    return b is None or not (abs(a - b) <= tol)


def _gate(name: str, items: Sequence[str], cites: str, *, unsure: Sequence[str] = ()) -> Gate:
    if items:
        return Gate(name, "failed", tuple(items), cites)
    if unsure:
        return Gate(name, "could not verify", tuple(unsure), cites)
    return Gate(name, "passed", (), cites)


def _name(a: Mapping[str, str]) -> str:
    return a.get("ProdName", "").strip()


def _where(a: Mapping[str, str]) -> str:
    return f"wall {a.get('Wall', '?')} at X {a.get('X', '?')} {_name(a)}"


# ----- the gates


def file_gate(content: bytes, template: RoomTemplate) -> Gate:
    items = []
    if content.count(b"\n") != content.count(b"\r\n"):
        items.append("a line ends without CRLF")
    first, _, rest = content.partition(b"\r\n")
    if first.decode("utf-8", "replace") != template.version:
        items.append(f"its first line isn't the template's version {template.version}")
    try:
        ET.fromstring(rest)
    except ET.ParseError as exc:
        items.append(f"an XML parser can't read it: {exc}")
    return _gate("file", items, "build_jobs_0914.py 159-163; BUILD 199-207")


def settled_gate(model: RoomModel) -> Gate:
    return _gate("settled", [f"{where}: {what}: {why}" for where, what, why in model.unverified()],
                 "AGENTS.md rule 8: a value that can't be verified is never written")


def attributes_gate(w: Written, template: RoomTemplate) -> Gate:
    items = [f"wall {a.get('IDTag', '?')} has another attribute set than the template's wall"
             for a in w.walls if tuple(a) != template.wall_attrs]
    items += [f"joint {a.get('Wall1', '?')}-{a.get('Wall2', '?')} has another attribute set than the cloned joint"
              for a in w.joints if tuple(a) != template.joint_attrs]
    return _gate("attributes", items, "SCHEMA 9-30")


def walls_gate(w: Written, model: RoomModel) -> Gate:
    items = []
    if len(w.walls) != len(model.walls):
        items.append(f"{len(w.walls)} walls written, {len(model.walls)} in the model")
    for a, m in zip(w.walls, model.walls):
        pos = m.pos_mm.value or (math.nan, math.nan)
        for attr, want in (("Len", m.length_mm.value), ("Height", m.height_mm.value), ("Ang", m.angle.value),
                           ("PosX", pos[0]), ("PosY", pos[1]), ("IDTag", m.id_tag.value)):
            if _off(_f(a.get(attr)), None if want is None else float(want)):
                items.append(f"wall {m.name}: {attr} {a.get(attr)} written, the model's {want}")
        if a.get("WallNumber") != m.name:
            items.append(f"wall {m.name}: WallNumber {a.get('WallNumber')} written")
    want_joints = [(str(x), str(y)) for x, y in model.joints.value or ()]
    if [(j.get("Wall1"), j.get("Wall2")) for j in w.joints] != want_joints:
        items.append("the joints aren't the model's neighbouring pairs")
    angles = [_f(a.get("Ang")) for a in w.walls]
    if len(angles) > 1 and not net_turn(angles) < 0:
        items.append(f"the walls turn {net_turn(angles):g} degrees in all, not clockwise")
    tags = [int(_f(a.get("IDTag"))) for a in w.walls] + [int(_f(a.get("IDTag"))) for a, _ in w.products]
    if tags and w.room.get("IdTagCount") != str(max(tags)):
        items.append(f"IdTagCount {w.room.get('IdTagCount')}, and the highest IDTag is {max(tags)}")
    return _gate("walls", items, "BUILD 199-221, 291; SCHEMA 32-37; RULES 454-457")


def panels_gate(w: Written, model: RoomModel) -> Gate:
    placed = list(placed_panels(model))
    items = []
    if len(placed) != len(w.products):
        items.append(f"{len(w.products)} products written, {len(placed)} panels in the model")
    for (a, _), (wall, p) in zip(w.products, placed):
        where = f"wall {p.wall} panel {p.index + 1}"
        if _name(a) != p.product.value:
            items.append(f"{where}: {_name(a)} written, the model's {p.product.value}")
        if a.get("Wall") != f"{wall.position.value}{WALL_SUFFIX}":
            items.append(f"{where}: on Wall {a.get('Wall')}, its wall is {wall.position.value}{WALL_SUFFIX} in the file")
        for attr, fact in (("X", p.x_mm), ("Elev", p.elev_mm), ("Height", p.height_mm), ("Depth", p.depth_mm)):
            if _off(_f(a.get(attr)), fact.value):
                items.append(f"{where}: {attr} {a.get(attr)} written, the model's {fact.value}")
    return _gate("panels", items, "BUILD 283-291")


def _lines_by_wall(walls: Sequence[ReadWall]) -> dict[str, tuple[ICLine, ...]]:
    return {w.name: w.lines for w in walls}


def _on_wall(w: Written, model: RoomModel) -> dict[str, list[dict[str, str]]]:
    by_pos = {f"{m.position.value}{WALL_SUFFIX}": m.name for m in model.walls}
    out: dict[str, list[dict[str, str]]] = {m.name: [] for m in model.walls}
    for a, _ in w.products:
        out.setdefault(by_pos.get(a.get("Wall", ""), a.get("Wall", "?")), []).append(a)
    return out


def counts_gates(w: Written, model: RoomModel, walls: Sequence[ReadWall]) -> list[Gate]:
    """Gates 1, 2 and 3, per wall (RULES 315-317)."""
    lines = _lines_by_wall(walls)
    one_items: list[str] = []
    two_items: list[str] = []
    three_items: list[str] = []
    unsure: list[str] = []
    for wall, placed in _on_wall(w, model).items():
        vts = vt_lines(lines.get(wall, ()))
        if wall not in lines and placed:
            unsure.append(f"wall {wall}: its IC lines weren't given")
            continue
        if any(line.qty is None for line, _ in vts):
            unsure.append(f"wall {wall}: a VT line's quantity is unreadable")
            continue
        listed = sum(line.qty or 0 for line, _ in vts)
        if len(placed) != listed:
            one_items.append(f"wall {wall}: {len(placed)} verticals placed, its VT lines list {listed}")
        fm = sum(line.qty or 0 for line, _ in vts if pl.mount(line.desc) == "FM")
        wm = listed - fm
        built_wm = sum(_name(a).startswith("WM") for a in placed)
        if (len(placed) - built_wm, built_wm) != (fm, wm):
            two_items.append(f"wall {wall}: {len(placed) - built_wm} FM and {built_wm} WM placed, its lines say "
                             f"{fm} and {wm}")
        rad = sum(line.qty or 0 for line, m in vts if m["kind"] == "VT RAD")
        built_rad = sum("Radius" in _name(a) for a in placed)
        if built_rad != rad:
            three_items.append(f"wall {wall}: {built_rad} radius products, {rad} VT RAD on its lines")
    return [_gate("gate 1", one_items, "RULES 315", unsure=unsure),
            _gate("gate 2", two_items, "RULES 316, 324-326", unsure=unsure),
            _gate("gate 3", three_items, "RULES 317", unsure=unsure)]


def heights_gate(w: Written) -> Gate:
    items = []
    for a, _ in w.products:
        h = _f(a.get("Height"))
        if pl.snap_height_mm(h / pl.MM) is None:
            items.append(f"{_where(a)}: height {a.get('Height')} off the 32k+20 grid")
    return _gate("gate 4", items, "RULES 291, 318")


def room_top(fm: Sequence[tuple[float, float]], wm: Sequence[tuple[float, float]]) -> float | None:
    """The room's one T as top_line.py's room_top reads it from (Elev, Height) pairs (RULES 333-339)."""
    if not wm:
        return None
    low = min(e for e, _ in wm)
    tall = [e + h for e, h in fm if e + h > low]
    if tall:
        t = max(tall)
    else:
        tops = [round(e + h, 1) for e, h in wm]
        t = max(sorted(set(tops)), key=tops.count)
    return pl.NORM_TOP_MM if abs(t - pl.NORM_TOP_MM) <= pl.NEAR_NORM_MM else t


def top_gate(w: Written) -> Gate:
    fm = [(_f(a.get("Elev")), _f(a.get("Height"))) for a, _ in w.products if not is_wm(_name(a))]
    wm = [(a, _f(a.get("Elev")), _f(a.get("Height"))) for a, _ in w.products if is_wm(_name(a))]
    t = room_top(fm, [(e, h) for _, e, h in wm])
    items = []
    for a, e, h in wm:
        if t is not None and abs(e + h - t) > TOP_TOL_MM:
            items.append(f"{_where(a)}: top {e + h:g}, the room's top line is {t:g}")
        if abs(e / 32 - round(e / 32)) > ELEV_GRID_TOL:
            items.append(f"{_where(a)}: Elev {e:g} off the 32 mm grid")
    for a, _ in w.products:
        if not is_wm(_name(a)) and _f(a.get("Elev")) != 0 and not _name(a).startswith("FM"):
            items.append(f"{_where(a)}: not a panel NG Runner places")
    return _gate("gate 5", items, "RULES 319, 329-346; top_line.py room_top")


def is_box_line(d: DrawnVertical, wall_in: float, wall_h_in: float) -> bool:
    """A line of the wall's own box, which elev_panels.py prints with the panels: the wall's full height, at
    either end of the wall (measured on frozen Z's four walls: 98 in lines at x 0.1 and at the wall's length)."""
    full = d.top_in >= wall_h_in - BOX_TOL_IN and d.bottom_in <= BOX_FLOOR_IN
    return full and (d.x_in <= X_TOL_IN or d.x_in >= wall_in - X_TOL_IN)


def gate_six(w: Written, model: RoomModel, walls: Sequence[ReadWall],
             measured: Mapping[str, Measured] | None) -> Gate:
    """Gate 6 (RULES 320-322): per wall, left to right, every placed panel against the drawing as Jim's
    elev_panels.py measures it. The wall's own box lines aside, a panel matches the lines drawn within 1 in
    of its X whose band overlaps its own; the longest of them, read to the nearest of the wall's VT heights
    (the script's "drawn length -> IC height"), must be its own height, and every line drawn must belong to
    a panel. Radius isn't checked here: on frozen Z's walls a radius panel's lines read as long as a square
    one's, so this table can't tell them, and gate 3 and the reading hold it. Depth isn't drawn. A wall the
    script can't measure, or a line that reads as two heights, could not verify: NG Runner's reading of
    RULES 320-322 and the script's notes, failing closed."""
    lines = _lines_by_wall(walls)
    sizes = {m.name: (m.length_mm.value, m.height_mm.value) for m in model.walls}
    items: list[str] = []
    unsure: list[str] = []
    for wall, placed in _on_wall(w, model).items():
        if not placed:
            continue
        got = (measured or {}).get(wall)
        length, height = sizes.get(wall, (None, None))
        if got is None or got.verticals is None or length is None or height is None:
            unsure.append(f"wall {wall}: {got.why.strip() if got else 'its drawing was not measured'}")
            continue
        drawn = [d for d in got.verticals if not is_box_line(d, length / pl.MM, height / pl.MM)]
        heights = sorted({float(m["height"]) for _, m in vt_lines(lines.get(wall, ()))})
        used: set[int] = set()
        for a in sorted(placed, key=lambda a: (_f(a.get("X")), _f(a.get("Elev")))):
            x, e, h = _f(a.get("X")) / pl.MM, _f(a.get("Elev")) / pl.MM, _f(a.get("Height")) / pl.MM
            near = [i for i, d in enumerate(drawn) if abs(d.x_in - x) <= X_TOL_IN
                    and min(d.top_in, e + h) - max(d.bottom_in, e) > OVERLAP_IN]
            if not near:
                items.append(f"wall {wall}: nothing drawn at X {x:.2f} in for its {_name(a)}")
                continue
            used.update(near)
            longest = max((drawn[i] for i in near), key=lambda d: d.length_in)
            ranked = sorted(heights, key=lambda ic: abs(ic - longest.length_in))
            if not ranked or (len(ranked) > 1 and math.isclose(abs(ranked[0] - longest.length_in),
                                                                abs(ranked[1] - longest.length_in), abs_tol=0.05)):
                unsure.append(f"wall {wall}: the line drawn at X {x:.2f} in reads as no one VT height")
                continue
            own = pl.snap_height_mm(ranked[0])
            if own is None or abs(own - _f(a.get("Height"))) > pl.SNAP_TOL_MM:
                items.append(f"wall {wall}: drawn {longest.length_in:.1f} in long at X {x:.2f} in, read as "
                             f"{ranked[0]:g} in, and {_name(a)} is {_f(a.get('Height')):g} mm")
        for i, d in enumerate(drawn):
            if i not in used:
                items.append(f"wall {wall}: a vertical drawn at X {d.x_in:.2f} in has no panel placed")
    return _gate("gate 6", items, "RULES 320-322; elev_panels.py", unsure=unsure)


def colour_gate(w: Written, settings: RoomSettings | None) -> Gate:
    """The room's colour on the bytes: the written run is the lifted one byte for byte, the room it was lifted
    from still holds it, it is its shade's, and the three drawer fronts are the settled one (colour.py)."""
    cites = "SCHEMA 46-85; RULES 211-233, 279; colour.py"
    front = settings.drawer_front if settings is not None else None
    materials = settings.materials if settings is not None else None
    if not isinstance(materials, Fact) or not materials.settled or not isinstance(front, Fact) or front.value is None:
        return _gate("colour", [], cites, unsure=["the room's colour wasn't settled when it was written"])
    lifted = materials.value
    items = []
    if (why := still_holds(lifted)) is not None:
        items.append(why)
    written = material_run(w.text)
    if not isinstance(lifted, Lifted) or written is None or written != crlf(lifted.run):
        items.append("the written material run isn't, byte for byte, the one lifted from its known-good room")
    elif (problem := run_problem(written, lifted.shade)) is not None:
        items.append(f"the written material run: {problem}")
    try:
        head = attrs(one(w.text, "RoomSet").head(w.text))
    except XmlError:
        head = {}
    if {head.get(a) for a in DRAWER_ATTRS} != {front.value} or front.value not in FRONTS:
        items.append(f"its drawer fronts are {', '.join(sorted(str(head.get(a)) for a in DRAWER_ATTRS))}, "
                     f"not {front.value}")
    return _gate("colour", items, cites)


def legal_gate(w: Written) -> Gate:
    items = [f"{_where(a)}: not a legal product" + (" (superseded, RULES 416-417)" if _name(a) in SUPERSEDED else "")
             for a, _ in w.products if _name(a) not in LEGAL]
    return _gate("legal names", items, "RULES 386-390, 415-417")


def _side(name: str) -> str | None:
    return next((s for s in ("Thru", "Left", "Right") if f" {s}" in f" {name} "), None)


def drill_gate(w: Written, model: RoomModel, walls: Sequence[ReadWall]) -> Gate:
    lines = _lines_by_wall(walls)
    items, unsure = [], []
    for wall, placed in _on_wall(w, model).items():
        sided = [a for a in placed if is_wm(_name(a)) or is_rea(_name(a))]
        vts = [(line, m) for line, m in vt_lines(lines.get(wall, ()))
               if pl.mount(line.desc) == "WM" or re.search(r"\bREA\b", line.desc)]
        if any(line.qty is None for line, _ in vts):
            unsure.append(f"wall {wall}: a VT line's quantity is unreadable")
            continue
        want = Counter(DRILL_SIDE[drill(line.desc)] for line, _ in vts for _ in range(line.qty or 0))
        got = Counter(_side(_name(a)) for a in sided)
        if got != want:
            items.append(f"wall {wall}: built {_counted(got)}, its lines' drill codes {_counted(want)}")
    return _gate("drill codes", items, "RULES 371-372, 388-390; panel_drill.py gate", unsure=unsure)


def _counted(c: Counter[str | None]) -> str:
    return ", ".join(f"{k} {n}" for k, n in sorted(c.items(), key=lambda kv: str(kv[0]))) or "none"


def _grooves(block: str) -> list[dict[str, str]]:
    return [attrs(t) for t in start_tags(block, "OperationGroove")]


def _bores(block: str) -> list[dict[str, str]]:
    return [attrs(t) for t in start_tags(block, "OperationLineBore")]


def notch_gate(w: Written, library: Library) -> Gate:
    items, unsure = [], []
    for a, block in w.products:
        name = _name(a)
        if name.startswith("WM Panel 48"):
            notches = [g for g in _grooves(block) if f'X_Eq="{g.get("X_Eq")}"' == GROOVE_EQ]
            active = [g for g in notches if g.get("Hide") == "False"]
            hidden = [g for g in notches if g.get("Hide") == "True"]
            if (len(active), len(hidden)) != (1, 1):
                items.append(f"{_where(a)}: {len(active)} active and {len(hidden)} hidden hang notches, not 1 and 1")
                continue
            want = FULL_NOTCH if _side(name) == "Thru" else SKINNED_NOTCH
            if active[0].get("Depth_Eq") != want:
                items.append(f"{_where(a)}: its active notch is {active[0].get('Depth_Eq')}, "
                             f"a {_side(name)} panel's is {want}")
            if name in library:
                lib = [g for g in _grooves(library[name].text) if g.get("Hide") == "False" and g.get("X_Eq")]
                keys = ("Depth_Eq", "Ang", "FlipSideOp")
                if lib and tuple(active[0].get(k) for k in keys) != tuple(lib[0].get(k) for k in keys):
                    items.append(f"{_where(a)}: its active notch isn't its library product's")
        elif name.startswith("WM 48 REA"):
            active = [g for g in _grooves(block) if g.get("Hide") == "False"]
            faces = {b.get("FlipSideOp") for b in _bores(block)}
            if len(faces) != 1 or len(active) != 1:
                unsure.append(f"{_where(a)}: bores on both faces or none, or not one active notch: by hand (RULES 408)")
            elif active[0].get("FlipSideOp") not in faces:
                items.append(f"{_where(a)}: its hang notch is cut from the other face than its line bores")
    return _gate("notch", items, "RULES 355-359, 404-409; SCHEMA 126-147; notch_side.py", unsure=unsure)


def groove_gate(w: Written) -> tuple[Gate, list[str]]:
    items, review = [], []
    for a, block in w.products:
        name, h = _name(a), _f(a.get("Height"))
        for g in _grooves(block):
            if f'X_Eq="{g.get("X_Eq")}"' != GROOVE_EQ and not is_rea(name):
                continue
            if abs(_f(g.get("X")) - (h - GROOVE_OFFSET_MM)) <= GEOM_TOL_MM:
                continue
            if is_rea(name):
                review.append(f"{_where(a)}: REA notch cached X {g.get('X')}, not height - 191.9; left as it is "
                              "(RULES 426: report it, do not correct it)")
            else:
                items.append(f"{_where(a)}: hang notch cached X {g.get('X')}, not height - 191.9 = "
                             f"{h - GROOVE_OFFSET_MM:g}")
    return _gate("groove", items, "RULES 400-403, 426"), review


def banding_gate(w: Written) -> Gate:
    items = []
    for a, block in w.products:
        if not is_wm(_name(a)):
            continue
        for shape in elements(block, "PartShapeXml"):
            inner = shape.of(block)
            pts = [attrs(t) for t in re.findall(r"<ShapePoint\b[^>]*>", inner)]
            for p, (start, end, want) in zip(pts, outline_edges(inner)):
                band, lock = p.get("EBand", "0"), p.get("EBandLock")
                ok = (band != "0" and lock == "True") if want else band == "0"
                if not ok:
                    items.append(f"{_where(a)}: the edge {start} to {end} is "
                                 f"{'not taped and locked' if want else 'taped'}, against the WM rule")
    return _gate("WM banding", items, "RULES 431-447; wm_eb.py")


def rea_gate(w: Written, library: Library) -> Gate:
    items = []
    for a, block in w.products:
        name = _name(a)
        if not is_rea(name):
            continue
        if name in library and operations(block) != library[name].operations():
            items.append(f"{_where(a)}: its drilling isn't its library product's, byte for byte")
        h, d = _f(a.get("Height")), _f(a.get("Depth"))
        shape = elements(block, "PartShapeXml")
        pts = [attrs(t) for t in re.findall(r"<ShapePoint\b[^>]*>", shape[0].of(block))] if shape else []
        top = [p for p in pts if p.get("SideName") == "Top"]
        front = [p for p in pts if p.get("SideName") == "Front"]
        slant = [p for p in pts if abs(_f(p.get("X")) - h) <= GEOM_TOL_MM
                 and abs(_f(p.get("Y")) - (d - SLANT_INTO_MM)) <= GEOM_TOL_MM]
        if not (top and front and slant) or abs(_f(top[0].get("X")) - h) > GEOM_TOL_MM \
                or abs(_f(front[0].get("X")) - (h - SLANT_ALONG_MM)) > GEOM_TOL_MM \
                or abs(_f(front[0].get("Y")) - d) > GEOM_TOL_MM:
            items.append(f"{_where(a)}: its slant isn't at depth - 54.38 and height - 228.6")
    return _gate("REA", items, "RULES 411-427; rea_panel.py")


def bores_gate(w: Written) -> Gate:
    items = [f"{_where(a)}: carries line bores" for a, block in w.products
             if not is_rea(_name(a)) and "<OperationLineBore" in block]
    return _gate("line bores", items, "BUILD 288; RULES 352-353, 423")


def part_gate(w: Written) -> Gate:
    items = []
    for a, block in w.products:
        parts = start_tags(block, "CabProdPart")
        if len(parts) != 1:
            items.append(f"{_where(a)}: {len(parts)} parts, a panel has one")
            continue
        p = attrs(parts[0])
        if _off(_f(p.get("W")), _f(a.get("Depth")), GEOM_TOL_MM) or _off(_f(p.get("L")), _f(a.get("Height")), GEOM_TOL_MM):
            items.append(f"{_where(a)}: its part is {p.get('W')} x {p.get('L')}, "
                         f"the panel {a.get('Depth')} x {a.get('Height')}")
    return _gate("part", items, "part_geom_check.py")


def library_gate(w: Written, template: RoomTemplate) -> Gate:
    items = [f"{_where(a)}: SourceLib {a.get('SourceLib')}, the template's is {template.source_lib}"
             for a, _ in w.products if a.get("SourceLib") != template.source_lib]
    if "MatORId=" in w.text:
        items.append("a per-part material override (MatORId) is in the room")
    if w.text.count("SourceLib=") != len(w.products):
        items.append("a SourceLib stands somewhere other than on a product")
    return _gate("SourceLib", items, "BUILD 157-159, 289; RULES 417")


# ----- all of them


def gate_room(written: WrittenRoom, model: RoomModel, walls: Sequence[ReadWall], template: RoomTemplate,
              library: Library, measured: Mapping[str, Measured] | None = None) -> RoomGates:
    """Every gate on one written room. walls: the room's walls as read, for their IC lines; measured: each
    wall's drawing as elev_panels.py measured it, by wall name."""
    file = file_gate(written.content, template)
    w = read_back(written.content)
    if isinstance(w, str):
        return RoomGates(written.file_name, (file, Gate("read back", "failed", (w,))), written.review)
    groove, review = groove_gate(w)
    gates = (file, settled_gate(model), attributes_gate(w, template), walls_gate(w, model), panels_gate(w, model),
             *counts_gates(w, model, walls), heights_gate(w), top_gate(w), gate_six(w, model, walls, measured),
             colour_gate(w, written.settings), legal_gate(w), drill_gate(w, model, walls), notch_gate(w, library),
             groove, banding_gate(w), rea_gate(w, library), bores_gate(w), part_gate(w), library_gate(w, template))
    return RoomGates(written.file_name, gates, (*written.review, *review))


def report(gates: RoomGates) -> list[str]:
    """What stopped a room, one line each: a gate that didn't run, then each item of one that didn't pass."""
    return ([f"{gates.room} {name}: the gate didn't run" for name in gates.missing()]
            + [f"{gates.room} {g.name} {g.status}: {item}" for g in gates.not_passed() for item in (g.items or ("",))])

