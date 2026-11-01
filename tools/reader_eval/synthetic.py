"""
Made-up IC summaries and Mozaik rooms for the reader evaluation's tests.

Why a writer of its own: the tests must prove containment (no text over a crop, no client detail
in a payload) on files whose every byte is known, and no real summary may enter git. Coordinates
and wording follow real renders (read off them, 2026-10-31): a portrait elevation page holds the
drawing, a Notes block, and the wall's parts table under a "Wall N / Design: ..." sub-header; a
landscape one holds only the drawing, with the table, in two column groups, on the next page.
Every name, address, email and phone here is invented (Testerson, example.com, 555-01xx).
"""
from __future__ import annotations

import io
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from PIL import Image, ImageDraw

CLIENT = "Testerson"
PROJECT = "Testerson_101426"
DESIGN = "Testerson - Primary"
DESIGNER = "Dana Draftwell"
ADDRESS = ("12 Madeup Lane", "Sampletown, CA 90000")
EMAIL = "pat.testerson@example.com"
PHONE = "(555) 010-0199"
STORE = ("Inspired Closets", "100 Fakery Blvd", "Sampleville, CA 90001", "(555) 010-0100 | www.fakestore.test")
NOTE = f"Client {CLIENT} wants the outlet moved, gate code 4421"


# ----- a tiny PDF writer (pypdf is for reading)


@dataclass
class Run:
    x: float
    y: float
    size: float
    text: str
    matrix: tuple[float, float, float, float] = (1, 0, 0, 1)  # a b c d of the text matrix


@dataclass
class Picture:
    name: str
    x: float
    y: float
    w: float
    h: float
    image: Image.Image


@dataclass
class Page:
    width: float = 612
    height: float = 792
    runs: list[Run] = field(default_factory=list)
    pictures: list[Picture] = field(default_factory=list)
    annots: list[tuple[float, float, float, float]] = field(default_factory=list)  # rects
    form_over: tuple[float, float, float, float] | None = None  # a form XObject drawn here

    def text(self, x: float, y: float, size: float, text: str) -> None:
        self.runs.append(Run(x, y, size, text))


def _escaped(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(pages: Sequence[Page]) -> bytes:
    """One PDF with a classic index; pictures Flate-compressed RGB, text in Helvetica."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    def stream(head: str, data: bytes) -> int:
        return add(f"<< {head} /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream")

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    tree = add(b"")
    kids: list[int] = []
    for page in pages:
        ops: list[str] = []
        named: dict[str, int] = {}
        for pic in page.pictures:
            rgb = pic.image.convert("RGB")
            named[pic.name] = stream(
                f"/Type /XObject /Subtype /Image /Width {rgb.width} /Height {rgb.height}"
                " /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode",
                zlib.compress(rgb.tobytes()))
            ops.append(f"q {pic.w:g} 0 0 {pic.h:g} {pic.x:g} {pic.y:g} cm /{pic.name} Do Q")
        if page.form_over is not None:
            fx, fy, fw, fh = page.form_over
            named["Fm0"] = stream(f"/Type /XObject /Subtype /Form /BBox [0 0 {fw:g} {fh:g}]",
                                  b"0 0 m 1 1 l S")
            ops.append(f"q 1 0 0 1 {fx:g} {fy:g} cm /Fm0 Do Q")
        for run in page.runs:
            a, b, c, d = run.matrix
            ops.append(f"BT /F1 {run.size:g} Tf {a:g} {b:g} {c:g} {d:g} {run.x:g} {run.y:g} Tm"
                       f" ({_escaped(run.text)}) Tj ET")
        contents = stream("", "\n".join(ops).encode("cp1252"))
        xobjects = " ".join(f"/{n} {o} 0 R" for n, o in named.items())
        annots = ""
        if page.annots:
            refs = [add(f"<< /Type /Annot /Subtype /Text /Rect [{r[0]:g} {r[1]:g} {r[2]:g} {r[3]:g}]"
                        f" /Contents (made-up note) >>".encode()) for r in page.annots]
            annots = " /Annots [" + " ".join(f"{r} 0 R" for r in refs) + "]"
        kids.append(add((f"<< /Type /Page /Parent {tree} 0 R /MediaBox [0 0 {page.width:g} {page.height:g}]"
                         f" /Resources << /Font << /F1 {font} 0 R >> /XObject << {xobjects} >> >>"
                         f" /Contents {contents} 0 R{annots} >>").encode()))
    objects[tree - 1] = (f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}]"
                         f" /Count {len(kids)} >>").encode()
    catalog = add(f"<< /Type /Catalog /Pages {tree} 0 R >>".encode())
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


# ----- a drawing that looks like an elevation: wall box, rulers, verticals, WIDTH strip


def elevation_image(width: int = 300, height: int = 400, *, panels: Sequence[float] = (0.0, 0.5),
                    ruler: int = 40) -> Image.Image:
    """White raster with a wall box (dark outline), a ruler band each side and vertical lines."""
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    left, right = ruler, width - ruler - 1
    top, bottom = int(height * 0.05), int(height * 0.9)
    draw.rectangle((left, top, right, bottom), outline="black", width=2)
    for y in range(top, bottom, max(4, height // 60)):  # ruler ticks, both sides
        draw.line((left - 12, y, left - 4, y), fill="black")
        draw.line((right + 4, y, right + 12, y), fill="black")
    for f in panels:
        x = left + 2 + int(f * (right - left - 6))
        draw.rectangle((x, top + int(height * 0.3), x + 3, bottom - 2), outline="black")
    draw.line((left, bottom + 20, right, bottom + 20), fill="black")  # the WIDTH strip
    return img


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ----- the summary


@dataclass(frozen=True)
class WallSpec:
    name: str
    width: str  # as the WALL/AREA table prints it, e.g. '59 3/4"'
    parts: tuple[tuple[str, int, str], ...] = (
        ("VT-WH", 1, "VT 14x60x3/4 FD FN WH"),
        ("VT-WH", 1, "VT RAD 14x60x3/4 RH  LN WH"),
        ("SH-WH", 2, "SH 14x34.3x3/4 ADJ WH"),
    )
    image_size: tuple[int, int] = (300, 400)
    panels: tuple[float, ...] = (0.0, 0.5)


DEFAULT_WALLS = (WallSpec("1", '42 3/8"', parts=(("DW-WH", 1, "DW Hook 4.9x12.2x3/4 FL WH"),), panels=()),
                 WallSpec("2", '59 3/4"'))


def summary_pdf(*, layout: str = "portrait", walls: Sequence[WallSpec] = DEFAULT_WALLS,
                design: str = DESIGN, text_over_drawing: str | None = None,
                rotated_text_over_drawing: bool = False, annotation_over_drawing: bool = False,
                second_drawing: bool = False, form_over_drawing: bool = False,
                notes: Sequence[str] = (NOTE,), description_extra: str | None = None,
                wrap_last_description: str | None = None, split_table: bool = False,
                company: str = "Madeup Holdings", page1_note: Sequence[str] = (),
                project: str = PROJECT) -> bytes:
    """
    A made-up summary: page 1 with the contact block, a WALL/AREA page, then one elevation page per
    wall (in `layout`), each with its parts table, and the store footer on every page.

    The faults the tests need: text_over_drawing puts that text inside the drawing's box on the
    last elevation page (rotated, when asked); annotation_over_drawing puts an annotation there;
    second_drawing adds another large raster to that page; form_over_drawing draws a form there.
    description_extra is added as one more parts line on every wall; wrap_last_description wraps
    the last line's description onto a line of its own; split_table carries the last wall's table
    over onto a page of its own with the header repeated. company is page 1's COMPANY value;
    page1_note adds page 1's free-text field (real renders carry a designer's sentences there).
    """
    portrait = layout == "portrait"
    size = (612.0, 792.0) if portrait else (792.0, 612.0)
    pages: list[Page] = []

    def new_page() -> Page:
        pages.append(Page(*size))
        return pages[-1]

    def heading(page: Page) -> None:
        if portrait:
            page.text(60, 733, 14, f"Design: {design}")
            page.text(399.2, 733, 14, f"Project: {project}")
        else:
            page.text(20.4, 574.6, 14, f"Design: {design}")
            page.text(576.9, 574.6, 14, f"Project: {project}")

    cover = new_page()
    cover.text(113.5, 564, 12, DESIGNER)
    cover.text(112.5, 542, 10, "(555) 010-0142")
    rows = (("CONTACT ADDRESS", ADDRESS), ("CONTACT NAME", (f"Pat {CLIENT}",)), ("COMPANY", (company,)),
            ("EMAIL", (EMAIL,)), ("LEAD SOURCE", ("Web Lead",)), ("PROJECT NAME", (project,)),
            ("DESIGNS IN PROJECT", (f"{design} - (Walk In Closet)",)), ("HOME", (PHONE,)))
    if page1_note:
        rows += (("PROJECT NOTES", tuple(page1_note)),)
    y = 702.0
    for label, values in rows:
        cover.text(229.2, y, 10, label)
        for i, value in enumerate(values):
            cover.text(355.2, y - 12 * i, 10, value)
        y -= 23 + 12 * (len(values) - 1)

    table = new_page()
    heading(table)
    table.text(441.6 if portrait else 306.6, 452.2 if portrait else 289.8, 8, "WALL/AREA NAME (W X H)")
    for i, wall in enumerate(walls):
        table.text(441.6 if portrait else 306.6, (452.2 if portrait else 289.8) - 14 * (i + 1), 8,
                   f'{wall.name}: {wall.width} X 108"')

    last = len(walls) - 1
    for w, wall in enumerate(walls):
        parts = list(wall.parts) + ([("XX", 1, description_extra)] if description_extra else [])
        elevation = new_page()
        heading(elevation)
        elevation.text(60 if portrait else 20.4, 705 if portrait else 528.6, 18, f"Wall {wall.name}")
        drawing = (208, 403, 196, 288) if portrait else (183, 82, 425, 432)
        image = elevation_image(*wall.image_size, panels=wall.panels)
        elevation.pictures.append(Picture(f"Elev{w}", *drawing, image))
        elevation.pictures.append(Picture(f"Logo{w}", 54, 50.4, 58.5, 54, Image.new("RGB", (30, 28), "blue")))
        if w == last:
            dx, dy, dw, dh = drawing
            if text_over_drawing is not None:
                if rotated_text_over_drawing:  # turned 90 degrees, its glyphs lean left over the edge
                    elevation.runs.append(Run(dx + 4, dy + 20, 10, text_over_drawing, (0, 1, -1, 0)))
                else:
                    elevation.text(dx + dw / 2, dy + dh / 2, 10, text_over_drawing)
            if annotation_over_drawing:
                elevation.annots.append((dx + 10, dy + 10, dx + 40, dy + 30))
            if second_drawing:
                elevation.pictures.append(Picture("Extra", dx, dy - 200, 200, 180, elevation_image(80, 80, ruler=10)))
            if form_over_drawing:
                elevation.form_over = (dx + 5, dy + 5, 20, 20)
        if portrait:
            table_page, y = elevation, 359.0
            if notes:
                elevation.text(66, y, 12, "Notes:")
                for i, note in enumerate(notes, 1):
                    elevation.text(66, y - 14 * i, 8, f"{i}: {note}")
                y -= 14 * (len(notes) + 1) + 30
            _table(table_page, y, [(wall.name, parts)], portrait=True, wrap=wrap_last_description,
                   split_from=(len(parts) - 1 if split_table and w == last else None), new_page=new_page,
                   heading=heading)
        else:
            _table(new_page(), 576.6, [(wall.name, parts)], portrait=False, wrap=wrap_last_description,
                   split_from=None, new_page=new_page, heading=heading)

    for k, page in enumerate(pages, 1):
        if portrait:
            page.text(500.1, 93.6, 8, STORE[0])
            page.text(491.0, 84.0, 8, STORE[1])
            page.text(443.3, 74.4, 8, STORE[2])
            page.text(399.5, 64.8, 8, STORE[3])
            page.text(512.7, 36, 8, f"Page {k} of {len(pages)}")
        else:
            page.text(177.2, 14.4, 8, " | ".join(STORE))
            page.text(732.3, 14.4, 8, f"Page {k} of {len(pages)}")
    return write_pdf(pages)


def _table(page: Page, y: float, tables: Sequence[tuple[str, Sequence[tuple[str, int, str]]]], *,
           portrait: bool, wrap: str | None, split_from: int | None,
           new_page: Callable[[], Page], heading: Callable[[Page], None]) -> None:
    """A wall's parts table: sub-header, column header, rows (portrait one group, landscape two)."""
    for wall_name, parts in tables:
        if portrait:
            groups = [(60.0, 132.0, 204.0, 456.0)]
            sub_design_x, qty_word, size = 362.1, "QUANTITY", 10.0
        else:
            groups = [(28.4, 91.9, 123.8, 314.6), (410.0, 473.6, 505.4, 696.1)]
            sub_design_x, qty_word, size = 407.4, "QTY", 8.0

        def header(p: Page, top: float) -> float:
            p.text(groups[0][0] if portrait else 20.4, top, 12 if not portrait else 10, f"Wall {wall_name}")
            p.text(sub_design_x, top, 12 if not portrait else 10, f"Design: {DESIGN}")
            hy = top - 18 if portrait else top - 27.2
            for item_x, qty_x, desc_x, color_x in groups:
                p.text(item_x, hy, size, "ITEM #")
                p.text(qty_x, hy, size, qty_word)
                p.text(desc_x, hy, size, "DESCRIPTION")
                p.text(color_x, hy, size, "COLOR")
            return hy

        top_row = header(page, y) - 18
        slot = 0  # rows fill the groups left to right, then go down one row
        for i, (item, qty, desc) in enumerate(parts):
            if split_from is not None and i == split_from:
                page = new_page()
                heading(page)
                top_row, slot = header(page, 731) - 18, 0
            g = groups[slot % len(groups)]
            row_y = top_row - 18 * (slot // len(groups))
            page.text(g[0], row_y, size, item)
            page.text(g[1], row_y, size, str(qty))
            page.text(g[2], row_y, size, desc)
            if desc.startswith(("VT", "SH", "DW", "DR", "CK")):
                page.text(g[3], row_y, size, "Arctic White")
            slot += 1
            if wrap and i == len(parts) - 1:  # the description's second line, just below it
                page.text(g[2], row_y - (size + 2), size, wrap)


# ----- Mozaik rooms


@dataclass(frozen=True)
class PanelSpec:
    prod: str
    x_mm: float
    elev_mm: float
    height_mm: float
    wall: int  # 1-based position of the wall in <Walls>
    width_mm: float = 19.0
    rot: float = 0.0


def room_des(*, name: str = DESIGN, walls: Sequence[tuple[int, float, float]] = ((1, 1076.325, 2743.2),
                                                                                     (2, 1517.65, 2743.2)),
             panels: Sequence[PanelSpec] = (PanelSpec("WM Panel 48 Thru", 0, 914.4, 1524, 2),
                                            PanelSpec("WM Panel 48 Right Radius", 888.95, 914.4, 1524, 2)),
             others: Sequence[tuple[str, float, float, int]] = (("48 S Hang 1S", 19, 869.95, 2),),
             version: bytes = b"15") -> bytes:
    """A Room#.des as Mozaik writes it: a version line, CRLF throughout. walls: (WallNumber, Len,
    Height) in file order; panels and others name their wall by its position in that order."""
    lines = [version.decode(), '<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
             f'<Room UniqueID="50001" RoomType="0" Name="{name}" Quan="1">', "  <Walls>"]
    for idtag, (number, length, height) in enumerate(walls, 1):
        lines.append(f'    <Wall IDTag="{idtag}" Len="{length}" Height="{height}" PosX="0" PosY="0" Ang="0"'
                     f' WallNumber="{number}" Thickness="152.4" ShapeType="0">')
        lines.append("    </Wall>")
    lines += ["  </Walls>", "  <Products>"]
    n = 1
    for p in panels:
        lines.append(f'    <Product UniqueID="5000{n}" ProdName="{p.prod}" IDTag="{n + 10}" Width="{p.width_mm}"'
                     f' Height="{p.height_mm}" Depth="356" X="{p.x_mm}" Elev="{p.elev_mm}" Rot="{p.rot}"'
                     f' Outset="0" Wall="{p.wall}_1" CabNo="{n}">')
        lines.append("    </Product>")
        n += 1
    for prod, x, width, wall in others:
        lines.append(f'    <Product UniqueID="5000{n}" ProdName="{prod}" IDTag="{n + 10}" Width="{width}"'
                     f' Height="1492" Depth="356" X="{x}" Elev="946.4" Rot="0" Outset="0" Wall="{wall}_1"'
                     f' CabNo="{n}">')
        lines.append("    </Product>")
        n += 1
    lines += ["  </Products>", "</Room>", ""]
    return "\r\n".join(lines).encode("utf-8")
