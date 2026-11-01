"""
The plan view of a design, read as a wall chain: which wall comes after which, and each wall's angle.

Why: BUILD Step 4 (lines 199-221) takes each wall's length and height from the WALL/AREA table and
"only TURN DIRECTIONS" from the plan, the top view IC draws on the WALL/AREA page as one embedded raster.
Without it the chain's order, each wall's angle, its start and the joints could not verify (walls.py), and
the chain is not always the table's order (two of frozen job Z's four rooms run 5, 1, 2, 3, 4 and
4, 1, 2, 3, 5). This module reads that raster by code, no model: pixels give the shape, the table gives
every length, and anything the two don't settle together is "could not verify" with the reason, never a
guess (AGENTS.md rule 8). Re-implemented from Jim's gen_rooms.py (toolkit of 2026-10-31), never copied:

  the raster    the WALL/AREA page's one large embedded image, at its native pixels (pypdf decodes it, as
                tools/reader_eval/prep.py does; nothing renders the page). gen_rooms.py 18-22 renders a
                clip of the page instead; the embedded image is the same drawing without a renderer
  wall strips   each wall is a flat-filled rectangle, its outline drawn in ink. gen_rooms.py 23-24 masks
                grey 150-193 (cabinets are ~200); 19 of the 112 real plans measured on 2026-11-01 fill
                their walls otherwise (beige, light grey, near white) or not at all. So the wall colour is
                the plan's most used flat colour other than white, the cabinet grey (204) and ink, tried
                in that order until one gives the table's count of strips; a plan with no such colour
                draws its walls as outlines, and its strips are the white boxes the ink closes in. The
                strips must then be the table's walls, one each. A piece of a strip a label cuts off
                (inside another strip's box) is no strip (gen_rooms.py 27 drops small components)
  orientation   a strip runs along its long side (gen_rooms.py 29-30), except that a stub shorter than
                the walls are thick lies across: the side that is the plan's wall thickness is the
                thickness. A square strip takes it from a neighbour: touching only at a corner, or with
                its end on the neighbour's side, it turns; meeting the neighbour's end it could run on or
                own an inward corner, so that alone settles nothing
  the chain     strips joined end to end, head to tail, between the two free ends (gen_rooms.py 40-67).
                No free end is a closed room, opened before the table's first wall: frozen X is one, and
                Jim's room starts there. Three strips at one end, a wall meeting another's side, or
                several runs (BUILD 219-221: the offset between runs comes only from pixels) could not
                verify
  the lengths   a strip's pixels against the table's inches, at one scale fitted through zero over every
                strip (gen_rooms.py 43 calibrates on the longest alone). Where the chain turns inward, IC
                draws the corner square on one wall and stops the other short of it; that one is measured
                to the far side of the square. Every strip must be a table wall within LENGTH_TOL_IN,
                each wall once (gen_rooms.py 70-73 lays the table's order on the chain instead; frozen Z
                shows the chain is not always that order)
  the labels    where walls of near-equal length could swap, the wall numbers IC prints on the plan
                decide: each one read against digits drawn in Pillow's own font (IC prints a plain sans
                serif), then the swap whose labels lie nearest their walls, by at least a label's height
                over any other. A contested wall with no label read, or two read, could not verify
  the angles    pixel right 0, left 180, down 270, up 90: Mozaik is Y-up and the plan Y-down
                (gen_rooms.py 77, RULES 454-456). Walls run clockwise, net signed turning negative
                (RULES 454-457, regression.py 78-82). A wall drawn at any other angle could not verify:
                BUILD 213-218 takes it from a geometric constraint, never from pixels. One wall alone,
                drawn across the page, is Ang 0, as every one-wall room measured here is built
"""
from __future__ import annotations

import io
import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from pypdf import PdfReader

from ngrunner.build.room import Plan, TableRow
from ngrunner.build.walls import net_turn

WHITE = (255, 255, 255)
CABINET = (204, 204, 204)  # gen_rooms.py 5: "cabinets ~200"; every real plan fills them 204, 204, 204
INK = 110  # a pixel darker than this is ink: outlines and text
MIN_STRIP_PX = 60  # a wall strip has at least this many pixels of its colour
MIN_COLOUR_PX = 2000  # a colour on fewer pixels fills no wall (gen_rooms.py 27 drops strips under 2000 px)
COLOURS_TRIED = 3  # the most used flat colours, in order, until one gives as many strips as the table
GAP_PX = 3  # two strips touch when their fills are this close: the ink outline lies between them
THICK_TOL_PX = 4  # a side within this of the plan's wall thickness is the thickness
LENGTH_TOL_IN = 0.75  # a strip is a table wall when its length is within this of the wall's (module notes)
MAX_NAMINGS = 5040  # 7 walls of one length: past this the namings are not weighed
RECTANGLE = 0.8  # a strip fills at least this share of its box; an angled wall fills far less
OUTLINED = 0.95  # an outlined wall's white inside fills its box; a digit's hole (an oval) does not

# The labels: the wall numbers, the plan's largest text.
OPENING_PX = 3  # opening the ink by this removes the 1-2 px outlines a label is drawn over
GLYPH_GRID_PX = 48  # glyphs and digits are compared at this height
GLYPH_MIN_IOU = 0.5
GLYPH_MARGIN = 0.1  # the best digit beats the next by this much
HOLE_SHARE = 0.01  # a hole is at least this share of its glyph's box (a 4's is the smallest, about 5%)
LABEL_SIZE = 0.85  # a label digit is at least this share of the largest digit read
JOIN_GAP = 0.35  # two digits closer than this share of their height are one number


@dataclass(frozen=True)
class PlanReading:
    """What the plan settled for one design, or why it settled nothing (plan is None, why says why).

    plan       the walls by table name in chain order and each one's angle (Mozaik's Y-up frame)
    free_ends  the names of the chain's first and last walls, whose start and end touch nothing; () for
               a closed room, opened before the table's first wall
    joints     each neighbouring pair in the chain, by name (one WallJoint each, gen_rooms.py 94-96)
    labels     the wall numbers read on the plan, when its lengths left walls to tell apart, for the record
    """
    plan: Plan | None
    why: str = ""
    free_ends: tuple[str, ...] = ()
    joints: tuple[tuple[str, str], ...] = ()
    scale_px_per_in: float | None = None
    labels: tuple[str, ...] = field(default=(), compare=False)

    @property
    def read(self) -> bool:
        return self.plan is not None


def _failed(why: str, labels: Iterable[str] = ()) -> PlanReading:
    return PlanReading(None, why, labels=tuple(labels))


# ----- the raster


def plan_raster(summary: bytes, page: int) -> Image.Image | None:
    """The largest image a page of the summary embeds (1-based page), as RGB; None when it has none."""
    pages = PdfReader(io.BytesIO(summary)).pages
    if not 1 <= page <= len(pages):
        return None
    images = [im.image for im in pages[page - 1].images]
    if not images:
        return None
    img = max(images, key=lambda im: im.size[0] * im.size[1])
    if img.mode != "RGB":
        flat = Image.new("RGB", img.size, "white")
        flat.paste(img, mask=img.getchannel("A") if "A" in img.getbands() else None)
        img = flat
    return img


# ----- connected pixels


@dataclass(frozen=True)
class _Blob:
    """Connected pixels: their box (inclusive) and their row runs (y, x0, x1)."""
    x0: int
    y0: int
    x1: int
    y1: int
    runs: tuple[tuple[int, int, int], ...]

    @property
    def w(self) -> int:
        return self.x1 - self.x0 + 1

    @property
    def h(self) -> int:
        return self.y1 - self.y0 + 1

    @property
    def pixels(self) -> int:
        return sum(x1 - x0 + 1 for _, x0, x1 in self.runs)

    @property
    def fill(self) -> float:
        return self.pixels / (self.w * self.h)

    @property
    def centre(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2


_RUN = re.compile(rb"[^\x00]+")


def blobs(mask: Image.Image, *, diagonal: bool = False) -> list[_Blob]:
    """The connected parts of a mode-L mask (non-zero is set), by row runs joined with union-find."""
    w, h = mask.size
    data = mask.tobytes()
    parent: list[int] = []
    runs: list[tuple[int, int, int]] = []

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    reach = 1 if diagonal else 0
    previous: list[tuple[int, int, int]] = []
    for y in range(h):
        current = []
        for m in _RUN.finditer(data, y * w, (y + 1) * w):
            x0, x1 = m.start() - y * w, m.end() - 1 - y * w
            k = len(runs)
            runs.append((y, x0, x1))
            parent.append(k)
            for pk, px0, px1 in previous:
                if px0 <= x1 + reach and x0 <= px1 + reach:
                    a, b = root(k), root(pk)
                    if a != b:
                        parent[a] = b
            current.append((k, x0, x1))
        previous = current
    groups: dict[int, list[tuple[int, int, int]]] = {}
    for k, run in enumerate(runs):
        groups.setdefault(root(k), []).append(run)
    return [_Blob(min(r[1] for r in rs), min(r[0] for r in rs), max(r[2] for r in rs), max(r[0] for r in rs),
                  tuple(rs)) for rs in groups.values()]


# ----- the wall strips


def wall_colours(img: Image.Image) -> list[tuple[int, int, int]]:
    """The plan's flat fill colours, most used first: never white, the cabinet grey or ink."""
    counts = img.getcolors(img.size[0] * img.size[1]) or []
    return [c for n, c in sorted(counts, key=lambda nc: -nc[0])
            if n >= MIN_COLOUR_PX and c not in (WHITE, CABINET) and sum(c) > 3 * INK]


def colour_mask(img: Image.Image, colour: tuple[int, int, int]) -> Image.Image:
    mask: Image.Image | None = None
    for band, level in zip(img.split(), colour):
        exact = band.point(lambda v, level=level: 255 if v == level else 0)
        mask = exact if mask is None else ImageChops.multiply(mask, exact)
    assert mask is not None
    return mask


def strips(img: Image.Image, colour: tuple[int, int, int]) -> list[_Blob]:
    """The wall strips of one fill colour: its parts, less any piece inside another part's box."""
    parts = [b for b in blobs(colour_mask(img, colour)) if b.pixels >= MIN_STRIP_PX]
    return [b for b in parts if not any(o is not b and o.x0 - 2 <= b.x0 and b.x1 <= o.x1 + 2
                                        and o.y0 - 2 <= b.y0 and b.y1 <= o.y1 + 2 for o in parts)]


def outlined(img: Image.Image) -> list[_Blob]:
    """The wall strips of a plan that fills no wall, drawing each as an outline only: the white boxes the
    ink closes in, square cornered, as thick as the longest of them (a wall: the gaps between cabinets are
    thinner, and the holes in digits are ovals)."""
    w, h = img.size
    closed = [b for b in blobs(colour_mask(img, WHITE)) if b.pixels >= MIN_STRIP_PX and b.fill >= OUTLINED
              and b.x0 > 0 and b.y0 > 0 and b.x1 < w - 1 and b.y1 < h - 1]
    if not closed:
        return []
    longest = max(closed, key=lambda b: max(b.w, b.h))
    t = min(longest.w, longest.h)
    return [b for b in closed if abs(b.w - t) <= THICK_TOL_PX or abs(b.h - t) <= THICK_TOL_PX]


def thickness(found: Sequence[_Blob]) -> float:
    """The plan's wall thickness, px: the short side of the strips twice as long as they are thick."""
    sides = [(min(b.w, b.h), max(b.w, b.h)) for b in found]
    long = sorted(a for a, b in sides if b >= 2 * a) or sorted(a for a, _ in sides)
    return long[len(long) // 2]


# ----- the chain


@dataclass(frozen=True)
class _Joint:
    a: int
    end_a: int  # 0: left or top end, 1: right or bottom end
    b: int
    end_b: int
    short: int | None  # the strip IC stopped short of the other's corner square (an inward turn), if any
    far: int  # the far side of that square along the short strip's axis, px


@dataclass(frozen=True)
class _Chain:
    order: tuple[int, ...]  # strips in chain order
    entered: tuple[int, ...]  # the end each strip is entered by
    axes: tuple[str, ...]  # per strip (by index): "H" across the page, "V" up and down it
    joints: tuple[_Joint, ...]
    loop: bool


def _axis(b: _Blob, t: float) -> str:
    """H or V by the side that is the wall thickness; "?" for a square strip, "!" for neither."""
    wt, ht = abs(b.w - t) <= THICK_TOL_PX, abs(b.h - t) <= THICK_TOL_PX
    if ht and not wt:
        return "H"
    if wt and not ht:
        return "V"
    return "?" if wt and ht else "!"


def _touch(a: _Blob, b: _Blob) -> tuple[int, int, int, int] | None:
    """Where two strips' fills come within 2 x GAP_PX of each other: that box, or None."""
    x0, y0 = max(a.x0, b.x0) - GAP_PX, max(a.y0, b.y0) - GAP_PX
    x1, y1 = min(a.x1, b.x1) + GAP_PX, min(a.y1, b.y1) + GAP_PX
    return (x0, y0, x1, y1) if x0 <= x1 and y0 <= y1 else None


def _corner(box: tuple[int, int, int, int]) -> bool:
    """Two strips meeting at a corner point only (a turn outward), not along an edge."""
    return box[2] - box[0] <= 4 * GAP_PX and box[3] - box[1] <= 4 * GAP_PX


def _end(b: _Blob, axis: str, box: tuple[int, int, int, int], t: float) -> int | None:
    """Which end of a strip a touch is at, or None when it is along the strip's side."""
    lo, hi = (b.x0, b.x1) if axis == "H" else (b.y0, b.y1)
    mid = (box[0] + box[2]) / 2 if axis == "H" else (box[1] + box[3]) / 2
    near0, near1 = abs(mid - lo), abs(mid - hi)
    if min(near0, near1) > t:
        return None
    return 0 if near0 <= near1 else 1


def trace(found: Sequence[_Blob], t: float) -> _Chain | str:
    """The strips as one chain, or why they are not one."""
    axes = [_axis(b, t) for b in found]
    if "!" in axes:
        return ("a wall is drawn at an angle that isn't a right angle, and BUILD 213-218 takes such an angle "
                "from a geometric constraint, never from pixels")
    touches = {(i, j): box for i in range(len(found)) for j in range(i + 1, len(found))
               if (box := _touch(found[i], found[j])) is not None}
    for _ in range(len(found)):
        for (i, j), box in touches.items():
            for a, b in ((i, j), (j, i)):
                if axes[a] == "?" and axes[b] in ("H", "V"):
                    across = "V" if axes[b] == "H" else "H"
                    if _corner(box):
                        axes[a] = across  # a turn outward: they meet at a corner point only
                    elif (box[2] - box[0] > box[3] - box[1]) == (axes[b] == "H"):
                        axes[a] = across  # its end against the neighbour's side: a turn inward
                    # else it meets the neighbour's end: running on, or owning an inward corner; not settled
    if "?" in axes:
        return "a square stub whose direction no neighbouring wall settles"
    joints: list[_Joint] = []
    seen: dict[tuple[int, int], tuple[int, int]] = {}
    for (i, j), box in touches.items():
        ei, ej = _end(found[i], axes[i], box, t), _end(found[j], axes[j], box, t)
        if ei is None or ej is None:
            return "a wall meets another wall's side, not its end"
        for key, other in (((i, ei), (j, ej)), ((j, ej), (i, ei))):
            if key in seen:
                return "three walls meet at one end"
            seen[key] = other
        short, far = None, 0
        if axes[i] != axes[j] and not _corner(box):
            # an inward turn: the strip whose end meets the other's side stops short of its corner square
            short, owner = (i, j) if (box[2] - box[0] < box[3] - box[1]) == (axes[i] == "H") else (j, i)
            o = found[owner]
            lo, hi = (o.x0, o.x1) if axes[short] == "H" else (o.y0, o.y1)
            mid = (found[short].x0 + found[short].x1) / 2 if axes[short] == "H" else (found[short].y0 + found[short].y1) / 2
            far = hi if abs(hi - mid) > abs(lo - mid) else lo
        joints.append(_Joint(i, ei, j, ej, short, far))
    free = [(i, e) for i in range(len(found)) for e in (0, 1) if (i, e) not in seen]
    if len(free) not in (0, 2):
        return f"the walls make {len(free) // 2} separate runs, and the offset between runs comes only from the plan's pixels (BUILD 219-221)"
    order: list[int] = []
    entered: list[int] = []
    at: tuple[int, int] | None = free[0] if free else (0, 0)
    while at is not None and at[0] not in order:
        order.append(at[0])
        entered.append(at[1])
        at = seen.get((at[0], 1 - at[1]))
    if len(order) != len(found):
        return "the walls make separate runs, and the offset between runs comes only from the plan's pixels (BUILD 219-221)"
    return _Chain(tuple(order), tuple(entered), tuple(axes), tuple(joints), not free)


def angles(chain: _Chain) -> list[float]:
    """Each wall's Ang in chain order: pixel right 0, left 180, down 270, up 90 (gen_rooms.py 77)."""
    return [(0.0 if e == 0 else 180.0) if chain.axes[i] == "H" else (270.0 if e == 0 else 90.0)
            for i, e in zip(chain.order, chain.entered)]


def reversed_chain(chain: _Chain) -> _Chain:
    return _Chain(chain.order[::-1], tuple(1 - e for e in chain.entered[::-1]), chain.axes, chain.joints, chain.loop)


def rotated(chain: _Chain, k: int) -> _Chain:
    """A closed chain opened before its k-th strip instead."""
    return _Chain(chain.order[k:] + chain.order[:k], chain.entered[k:] + chain.entered[:k], chain.axes,
                  chain.joints, chain.loop)


# ----- the lengths


def lengths_px(found: Sequence[_Blob], chain: _Chain) -> list[float]:
    """Each strip's length along its axis, px, by strip index; a strip stopped short of an inward
    corner's square is measured to the far side of that square."""
    out = [float(b.w if chain.axes[i] == "H" else b.h) for i, b in enumerate(found)]
    for j in chain.joints:
        if j.short is not None:
            b = found[j.short]
            lo, hi = (b.x0, b.x1) if chain.axes[j.short] == "H" else (b.y0, b.y1)
            out[j.short] = float(max(hi, j.far) - min(lo, j.far) + 1)
    return out


def scale(px: Sequence[float], inches: Sequence[float]) -> float:
    """Pixels per inch: least squares through zero over the strips and the walls sorted alike (each strip is
    one wall). No offset is fitted, so even two walls are a check of each other."""
    pairs = list(zip(sorted(px), sorted(inches)))
    return sum(p * i for p, i in pairs) / sum(i * i for _, i in pairs)


def namings(measured: Sequence[float], rows: Sequence[TableRow]) -> list[tuple[str, ...]] | None:
    """Every way to name the strips (in the order given) with table walls, each once, within LENGTH_TOL_IN;
    None when there are more than MAX_NAMINGS, too many to weigh."""
    fits = [[r.name for r in rows if abs(m - r.width_in) <= LENGTH_TOL_IN] for m in measured]
    out: list[tuple[str, ...]] = []

    def walk(k: int, used: tuple[str, ...]) -> bool:
        if k == len(fits):
            out.append(used)
            return len(out) <= MAX_NAMINGS
        return all(walk(k + 1, used + (name,)) for name in fits[k] if name not in used)
    return out if walk(0, ()) else None


# ----- the labels


_DIGITS: dict[str, tuple[Image.Image, int]] = {}


def _digit(d: str) -> tuple[Image.Image, int]:
    """Digit d drawn in Pillow's own font, cropped to its ink, and its holes."""
    if d not in _DIGITS:
        font = ImageFont.load_default(size=200)
        canvas = Image.new("L", (400, 400), 0)
        ImageDraw.Draw(canvas).text((100, 50), d, font=font, fill=255)
        bbox = canvas.getbbox()
        assert bbox is not None
        glyph = canvas.crop(bbox).point(lambda v: 255 if v >= 128 else 0)
        _DIGITS[d] = (glyph, holes(glyph))
    return _DIGITS[d]


def holes(glyph: Image.Image) -> int:
    """Background regions a glyph closes in: 0 has one, 8 two. A speck under HOLE_SHARE of the glyph's box
    is a pixel the threshold missed, not a hole."""
    pad = Image.new("L", (glyph.width + 2, glyph.height + 2), 255)
    pad.paste(ImageChops.invert(glyph), (1, 1))
    least = HOLE_SHARE * glyph.width * glyph.height
    return sum(1 for b in blobs(pad) if b.x0 > 0 and b.pixels >= least)


def _grid(glyph: Image.Image) -> Image.Image:
    return glyph.resize((max(1, round(glyph.width * GLYPH_GRID_PX / glyph.height)), GLYPH_GRID_PX),
                        Image.Resampling.BILINEAR)


def _iou(a: Image.Image, b: Image.Image) -> float:
    w = max(a.width, b.width)
    pa, pb = Image.new("L", (w, a.height), 0), Image.new("L", (w, b.height), 0)
    pa.paste(a, ((w - a.width) // 2, 0))
    pb.paste(b, ((w - b.width) // 2, 0))
    both = ImageChops.darker(pa, pb).histogram()
    either = ImageChops.lighter(pa, pb).histogram()
    inter = sum(v * n for v, n in enumerate(both))
    union = sum(v * n for v, n in enumerate(either))
    return inter / union if union else 0.0


def read_digit(glyph: Image.Image) -> str | None:
    """The digit a glyph is, or None: the best match among the digits with as many holes must be the best
    match of all, by GLYPH_MARGIN over the next with as many holes, and at least GLYPH_MIN_IOU."""
    grid = _grid(glyph)
    found = holes(glyph)
    scored = sorted(((_iou(grid, _grid(_digit(d)[0])), d, _digit(d)[1] == found) for d in "0123456789"),
                    reverse=True)
    alike = [s for s in scored if s[2]]
    if not alike or alike[0][1] != scored[0][1] or alike[0][0] < GLYPH_MIN_IOU:
        return None
    if len(alike) > 1 and alike[0][0] - alike[1][0] < GLYPH_MARGIN:
        return None
    return alike[0][1]


@dataclass(frozen=True)
class Label:
    text: str
    box: tuple[int, int, int, int]

    @property
    def centre(self) -> tuple[float, float]:
        return (self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2

    @property
    def height(self) -> int:
        return self.box[3] - self.box[1] + 1


def labels(img: Image.Image) -> list[Label]:
    """The wall numbers printed on the plan: its largest digits, side by side ones joined."""
    ink = img.convert("L").point(lambda v: 255 if v < INK else 0)
    ink = ink.filter(ImageFilter.MinFilter(OPENING_PX)).filter(ImageFilter.MaxFilter(OPENING_PX))
    read: list[tuple[_Blob, str]] = []
    for b in blobs(ink, diagonal=True):
        if 15 <= b.h <= 400 and b.w <= b.h and 0.1 <= b.fill <= 0.75:
            glyph = Image.new("L", (b.w, b.h), 0)
            draw = ImageDraw.Draw(glyph)
            for y, x0, x1 in b.runs:
                draw.line([(x0 - b.x0, y - b.y0), (x1 - b.x0, y - b.y0)], fill=255)
            digit = read_digit(glyph)
            if digit is not None:
                read.append((b, digit))
    if not read:
        return []
    tallest = max(b.h for b, _ in read)
    big = sorted(((b, d) for b, d in read if b.h >= LABEL_SIZE * tallest), key=lambda bd: bd[0].x0)
    out: list[Label] = []
    used: set[int] = set()
    for k, (b, d) in enumerate(big):
        if k in used:
            continue
        box, text = [b.x0, b.y0, b.x1, b.y1], d
        for m in range(k + 1, len(big)):
            nb, nd = big[m]
            overlap = min(box[3], nb.y1) - max(box[1], nb.y0)
            if m not in used and overlap >= 0.8 * b.h and 0 <= nb.x0 - box[2] <= JOIN_GAP * b.h:
                box = [box[0], min(box[1], nb.y0), nb.x1, max(box[3], nb.y1)]
                text += nd
                used.add(m)
        out.append(Label(text, (box[0], box[1], box[2], box[3])))
    return out


def _distance(point: tuple[float, float], b: _Blob) -> float:
    dx = max(b.x0 - point[0], 0.0, point[0] - b.x1)
    dy = max(b.y0 - point[1], 0.0, point[1] - b.y1)
    return math.hypot(dx, dy)


def by_labels(found: Sequence[_Blob], order: Sequence[int], options: Sequence[tuple[str, ...]],
              read: Sequence[Label]) -> tuple[str, ...] | str:
    """The one naming whose contested walls' labels lie nearest their strips, or why none does."""
    contested = sorted({n for k in range(len(order)) for n in {o[k] for o in options} if len({o[k] for o in options}) > 1},
                       key=lambda n: (len(n), n))
    texts = Counter(lab.text for lab in read)
    missing = [n for n in contested if texts[n] == 0]
    twice = [n for n in contested if texts[n] > 1]
    if missing:
        return (f"walls {', '.join(contested)} have lengths too close to tell apart, and the plan's label for "
                f"wall {', '.join(missing)} could not be read")
    if twice:
        return f"the plan's label {', '.join(twice)} reads twice, so it can't tell walls {', '.join(contested)} apart"
    where = {lab.text: lab.centre for lab in read}
    cost = sorted((sum(_distance(where[n], found[order[k]]) for k, n in enumerate(o) if n in contested), o)
                  for o in options)
    size = max(lab.height for lab in read)
    if len(cost) > 1 and cost[1][0] - cost[0][0] < size:
        return (f"walls {', '.join(contested)} have lengths too close to tell apart, and their labels lie too "
                "near each other's walls to settle which is which")
    return cost[0][1]


# ----- the whole reading


def read_raster(img: Image.Image, rows: Sequence[TableRow]) -> PlanReading:
    """One design's plan raster and its WALL/AREA table, read as the wall chain (module notes)."""
    if not rows:
        return _failed("the WALL/AREA table has no walls")
    if len({r.name for r in rows}) != len(rows):
        return _failed("the WALL/AREA table names a wall twice")
    if any(r.width_in <= 0 for r in rows):
        return _failed("the WALL/AREA table has a wall with no length")
    img = img.convert("RGB")
    colours = wall_colours(img)[:COLOURS_TRIED]
    tries = [strips(img, colour) for colour in colours] if colours else [outlined(img)]
    found = next((t for t in tries if len(t) == len(rows)), None)
    if found is None:
        kind = "wall strips" if colours else "outlined walls and no filled one"
        return _failed(f"the plan shows {len(tries[0])} {kind} where the WALL/AREA table has {len(rows)} walls")
    if any(b.fill < RECTANGLE for b in found):
        return _failed("a wall is drawn at an angle that isn't a right angle, and BUILD 213-218 takes such an "
                       "angle from a geometric constraint, never from pixels")
    t = thickness(found)
    chain = trace(found, t)
    if isinstance(chain, str):
        return _failed(chain)
    px = lengths_px(found, chain)
    per_in = scale(px, [r.width_in for r in rows])
    measured = [px[i] / per_in for i in chain.order]
    options = namings(measured, rows)
    if options is None:
        return _failed("too many walls of near-equal length to weigh which is which")
    if not options:
        return _failed("the plan's wall lengths don't match the WALL/AREA table's at any one scale")
    if len(options) == 1:
        chosen, read = options[0], []
    else:
        read = labels(img)
        picked = by_labels(found, chain.order, options, read)
        if isinstance(picked, str):
            return _failed(picked, (lab.text for lab in read))
        chosen = picked
    name_of = dict(zip(chain.order, chosen))
    if chain.loop:
        first = rows[0].name
        chain = rotated(chain, [name_of[i] for i in chain.order].index(first))
        if net_turn(angles(chain)) >= 0:
            back = reversed_chain(chain)
            chain = rotated(back, [name_of[i] for i in back.order].index(first))
    elif len(chain.order) > 1 and net_turn(angles(chain)) > 0:
        chain = reversed_chain(chain)
    angs = angles(chain)
    if len(angs) == 1 and chain.axes[chain.order[0]] == "H":
        angs = [0.0]
    elif len(angs) == 1:
        return _failed("one wall alone, drawn up the page: no rule says which way it runs")
    if len(angs) > 1 and net_turn(angs) >= 0:
        return _failed(f"the walls turn {net_turn(angs):g} degrees in all, neither way clockwise (RULES 454-457)")
    names = tuple(name_of[i] for i in chain.order)
    ends = () if chain.loop else (names[0], names[-1])
    return PlanReading(Plan(names, tuple(angs)), "", ends, tuple(zip(names, names[1:])), round(per_in, 4),
                       tuple(lab.text for lab in read))


def read_plan(summary: bytes, page: int, rows: Sequence[TableRow]) -> PlanReading:
    """The plan on a design's WALL/AREA page (1-based) read against that design's table."""
    img = plan_raster(summary, page)
    if img is None:
        return _failed("the WALL/AREA page holds no plan")
    return read_raster(img, rows)

