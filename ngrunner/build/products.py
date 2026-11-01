"""
A panel's <Product>: the library product the model names, cloned and resized, as Jim's builder makes it.

Why: "Clone the LIBRARY product for the chosen variant ... never rename one variant into another, the
difference is the hang-notch groove" (BUILD 250-253); a radius is real geometry, not a name (SCHEMA
96-124). So each panel is a copy of his own product, read at run time (never committed: the repo is
public and the products are his), changed only where his rules say. Ported from his toolkit of
2026-10-31, never copied:

  which product  the model's name (schedule.product) is the file: "WM Panel 48 Left Radius" is
                 templates\\WM_Panel_48_Left_Radius.xml of the adopted toolkit copy, as wm_panel.py 5-7 and
                 rea_panel.py 26-28 map them. FM 84 is the template job's own FM 84, the one his builder
                 clones (build_jobs_0914.py 33-37), not the older templates\\FM_84.xml. The June REA names
                 (FM 84 REA, FM 84 Left REA, FM 84 Right REA) are superseded and never used (RULES 416-417)
  resizing       by value, not by token: every geometry value (X, Y, L, W, Height, Depth) on the Product,
                 its CabProdPart and its ShapePoints that is the template's height or depth becomes the
                 panel's, within 0.05 mm, since a re-saved product mixes 1204 and 1204.001 and the
                 template room's FM 84 2132 and 2132.012 (RULES 420-421, rea_panel.py 95-107). On an REA
                 the slant moves with it: start Y = depth - 54.38, end X = height - 228.6 (RULES 419-421).
                 Width is 19 (BUILD 284), on the Product and its top outline
  the groove     every hang-notch groove's cached X = height - 191.9 (RULES 400-403, wm_panel.py 66-70);
                 never on an REA (RULES 423-426)
  line bores     stripped with a balanced scanner, Mozaik's to regenerate (BUILD 288, RULES 352-353),
                 except on an REA, whose drilling is an override nothing touches (RULES 423-427)
  WM banding     on every WM panel's outline, by geometry: an edge reaching the front half (max Y > D/2)
                 is taped and locked, a slot Jim picked (1-6) kept and only a 0 raised to 1; any other
                 edge bare (RULES 431-447, wm_eb.py 12-21, 35-60). FM panels keep the template's banding
                 (RULES 429, 448)
  identity       UniqueID, IDTag, CabNo, X, Elev, Wall and SourceLib, the template room's (BUILD 289,
                 RULES 417)
"""
from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from ngrunner.build.desxml import CRLF, attrs, crlf, elements, map_tags, num, one, set_attr
from ngrunner.build.room import PANEL_WIDTH_MM

SIDES = ("Thru", "Left", "Right")
WM_NAMES = tuple(f"WM Panel 48 {s}{r}" for s in SIDES for r in ("", " Radius"))  # RULES 377-387
WM_REA_NAMES = tuple(f"WM 48 REA {s}{r}" for s in SIDES for r in ("", " Radius"))  # RULES 415-416
FM_REA_NAMES = tuple(f"FM 84 REA {s}" for s in SIDES)  # RULES 415-417
FM_NAME = "FM 84"
LEGAL = (FM_NAME, *FM_REA_NAMES, *WM_NAMES, *WM_REA_NAMES)  # RULES 386-390, 415-417
SUPERSEDED = ("FM 84 REA", "FM 84 Left REA", "FM 84 Right REA")  # RULES 416-417: never inserted

GROOVE_OFFSET_MM = 191.9  # RULES 401: the groove's cached X = height - 191.9
GROOVE_EQ = 'X_Eq="PartL-191.9"'
SLANT_ALONG_MM = 228.6  # RULES 412: 9 in along the panel
SLANT_INTO_MM = 54.38  # RULES 412: into the depth
REMAP_TOL_MM = 0.05  # a re-saved product's 2132.012 is its 2132
GEOMETRY = ("X", "Y", "L", "W", "Height", "Depth")
_BAND = re.compile(r'(?<=\s)EBand="([^"]*)"')
_LOCK = re.compile(r'(?<=\s)EBandLock="[^"]*"')


def template_file(name: str) -> str:
    """The file a product name lives in, in the toolkit's templates (wm_panel.py 5-7, rea_panel.py 26-28)."""
    return name.replace(" ", "_") + ".xml"


def is_rea(name: str) -> bool:
    return " REA" in f" {name}"


def is_wm(name: str) -> bool:
    """The WM Panels folder's families NG Runner builds (RULES 439-440)."""
    return name.startswith(("WM Panel 48", "WM 48 REA"))


@dataclass(frozen=True)
class ProductTemplate:
    """One library product as it was read: its name, text and drawn size."""
    name: str
    text: str
    height: float
    depth: float
    width: float

    @property
    def rea(self) -> bool:
        return is_rea(self.name)

    @property
    def wm(self) -> bool:
        return is_wm(self.name)

    def operations(self) -> list[str]:
        return operations(self.text)


class Library(Mapping[str, ProductTemplate]):
    """The products a room can be built from, by name, and what couldn't be read."""

    def __init__(self, products: Mapping[str, ProductTemplate], problems: Mapping[str, str] | None = None) -> None:
        self._products = dict(products)
        self.problems = dict(problems or {})

    def __getitem__(self, name: str) -> ProductTemplate:
        return self._products[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._products)

    def __len__(self) -> int:
        return len(self._products)

    def names(self) -> tuple[str, ...]:
        return tuple(self._products)


def read_product(name: str, text: str) -> ProductTemplate:
    """A product's text checked to be the product named (ValueError otherwise)."""
    text = crlf(text.strip())
    head = one(text, "Product").head(text)
    a = attrs(head)
    if a.get("ProdName", "").strip() != name:
        raise ValueError(f"{template_file(name)} holds {a.get('ProdName', '').strip()!r}, not {name!r}")
    if len(elements(text, "CabProdPart")) != 1:
        raise ValueError(f"{name} has more than one part, so it isn't a bare panel")
    return ProductTemplate(name, text, float(a["Height"]), float(a["Depth"]), float(a["Width"]))


def load_library(templates: Path, fm84: str) -> Library:
    """Every legal product from the adopted toolkit copy's templates folder, and FM 84 from the template
    room (fm84: writer.RoomTemplate.fm84). A product that's missing or isn't what its name says is left
    out, with why: a room that needs it isn't written."""
    found: dict[str, ProductTemplate] = {}
    problems: dict[str, str] = {}
    try:
        found[FM_NAME] = read_product(FM_NAME, fm84)
    except (ValueError, KeyError) as exc:
        problems[FM_NAME] = str(exc)
    for name in LEGAL:
        if name == FM_NAME:
            continue
        path = templates / template_file(name)
        try:
            found[name] = read_product(name, path.read_bytes().decode("utf-8"))
        except FileNotFoundError:
            problems[name] = f"Jim's templates have no {template_file(name)}"
        except (OSError, UnicodeDecodeError, ValueError, KeyError) as exc:
            problems[name] = f"{template_file(name)} could not be read: {exc}"
    return Library(found, problems)


# ----- the parts of a product


def operations(text: str) -> list[str]:
    """Every <Operation*> element, in order: the drilling an REA keeps byte for byte (RULES 423-427)."""
    names = sorted({m for m in re.findall(r"<(Operation\w+)[\s/>]", text)})
    spans = [e for n in names for e in elements(text, n)]
    return [text[e.start:e.end] for e in sorted(spans, key=lambda e: e.start)]


def strip(text: str, name: str) -> str:
    """text without its name elements, each found by the balanced scanner, and the line each stood on."""
    for e in reversed(elements(text, name)):
        start = text.rfind("\n", 0, e.start) + 1
        if text[start:e.start].strip():
            start = e.start
        end = e.end
        if text.startswith(CRLF, end) and start != e.start:
            end += len(CRLF)
        text = text[:start] + text[end:]
    return text


def _remap(pairs: list[tuple[float, float]]) -> object:
    def value(v: str) -> str:
        try:
            f = float(v)
        except ValueError:
            return v
        for old, new in pairs:
            if abs(f - old) <= REMAP_TOL_MM:
                return num(new)
        return v

    def tag(t: str) -> str:
        return re.sub(r'(?<=\s)(X|Y|L|W|Height|Depth)="([^"]*)"', lambda m: f'{m[1]}="{value(m[2])}"', t)
    return tag


def resize(t: ProductTemplate, height: float, depth: float) -> str:
    """The product's geometry at height x depth, by value (module notes)."""
    pairs = [(t.height, height), (t.depth, depth)]
    if t.rea:
        pairs += [(t.height - SLANT_ALONG_MM, height - SLANT_ALONG_MM), (t.depth - SLANT_INTO_MM, depth - SLANT_INTO_MM)]
    change = _remap(pairs)
    text = t.text
    for name in ("Product", "CabProdPart", "ShapePoint"):
        text = map_tags(text, name, change)
    return _width(text, t.width)


def _width(text: str, width: float) -> str:
    """Width 19 on the Product and on its top outline (BUILD 284)."""
    head = one(text, "Product")
    text = text[:head.start] + set_attr(head.head(text), "Width", num(PANEL_WIDTH_MM)) + text[head.head_end:]
    if abs(width - PANEL_WIDTH_MM) <= 1e-9:
        return text
    for top in reversed(elements(text, "TopShapeXml")):
        inner = map_tags(text[top.start:top.end], "ShapePoint", _remap([(width, PANEL_WIDTH_MM)]))
        text = text[:top.start] + inner + text[top.end:]
    return text


def grooves(text: str) -> str:
    """Every hang-notch groove's cached X at the part's height - 191.9 (RULES 400-403)."""
    part = attrs(one(text, "CabProdPart").head(text))
    height = float(part["L"])

    def fix(tag: str) -> str:
        return set_attr(tag, "X", num(height - GROOVE_OFFSET_MM)) if GROOVE_EQ in tag else tag
    return map_tags(text, "OperationGroove", fix)


def outline_edges(shape: str) -> list[tuple[tuple[float, float], tuple[float, float], bool]]:
    """Each edge of a part outline, from one ShapePoint to the next (wrapping), and whether the WM rule
    tapes it: it reaches the front half, max Y > D/2 (RULES 432-434, wm_eb.py 35-47)."""
    pts = [attrs(t) for t in re.findall(r"<ShapePoint\b[^>]*>", shape)]
    if len(pts) < 3:
        return []
    xy = [(float(p["X"]), float(p["Y"])) for p in pts]
    d = max(y for _, y in xy)
    return [(xy[i], xy[(i + 1) % len(xy)], max(xy[i][1], xy[(i + 1) % len(xy)][1]) > d / 2) for i in range(len(xy))]


def band(text: str) -> str:
    """The WM banding rule on every part outline of a product (module notes)."""
    out = text
    for shape in reversed(elements(out, "PartShapeXml")):
        inner = out[shape.start:shape.end]
        edges = outline_edges(inner)
        points = list(re.finditer(r"<ShapePoint\b[^>]*>", inner))
        pieces, last = [], 0
        for m, (_, _, want) in zip(points, edges):
            tag = m.group(0)
            if want:
                if _BAND.search(tag) and _BAND.search(tag)[1] == "0":  # type: ignore[index]
                    tag = _BAND.sub('EBand="1"', tag, count=1)
                tag = _LOCK.sub('EBandLock="True"', tag, count=1)
            elif (b := _BAND.search(tag)) and b[1] not in ("", "0"):
                tag = _BAND.sub('EBand="0"', tag, count=1)
            pieces += [inner[last:m.start()], tag]
            last = m.end()
        pieces.append(inner[last:])
        out = out[:shape.start] + "".join(pieces) + out[shape.end:]
    return out


def clone(t: ProductTemplate, *, height: float, depth: float, x: float, elev: float, wall: str, id_tag: int,
          unique_id: int, cab_no: int, source_lib: str) -> str:
    """One panel: the library product resized, its groove, bores and banding by Jim's rules, placed."""
    text = resize(t, height, depth)
    if not t.rea:
        text = grooves(strip(text, "OperationLineBore"))
    if t.wm:
        text = band(text)
    head = one(text, "Product")
    tag = head.head(text)
    for name, value in (("UniqueID", str(unique_id)), ("IDTag", str(id_tag)), ("CabNo", str(cab_no)),
                        ("X", num(x)), ("Elev", num(elev)), ("Wall", wall), ("SourceLib", source_lib),
                        ("Height", num(height)), ("Depth", num(depth))):
        tag = set_attr(tag, name, value)
    return text[:head.start] + tag + text[head.head_end:]
