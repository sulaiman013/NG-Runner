"""
Synthetic plan views for the plan reader's tests, drawn with Pillow as IC draws its own (read off real
plans, 2026-11-01): each wall a flat-filled rectangle with an ink outline, as long as the wall and as thick
as the walls are, lying outside the room; walls turning outward meet at a corner point, walls running on
meet end to end, and where the chain turns inward the wall after the turn owns the corner square and the
wall before stops short of it. Each wall's number is printed large beside it and its length smaller.
Every room here is made up.
"""
from __future__ import annotations

import io
import math
import zlib
from collections.abc import Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

from ngrunner.build.room import TableRow

GREY = (183, 181, 179)  # the commonest wall fill on real plans
CABINET = (204, 204, 204)
INK = (0, 0, 0)

Wall = tuple[str, float, float]  # name, length in inches, Ang (Mozaik's Y-up frame)


def _turn(a: float, b: float) -> float:
    return (b - a + 180) % 360 - 180


def boxes(chain: Sequence[Wall], scale: float, thick_in: float, origin: tuple[float, float]
          ) -> list[tuple[int, int, int, int]]:
    """Each wall's rectangle in pixels (left, top, right, bottom), Y flipped as the plan is."""
    out = []
    x = y = 0.0
    for k, (_, length, ang) in enumerate(chain):
        dx, dy = round(math.cos(math.radians(ang))), round(math.sin(math.radians(ang)))
        nx, ny = -dy, dx  # outward: left of the direction of travel, the room on the right
        run = length
        if k + 1 < len(chain) and _turn(ang, chain[k + 1][2]) > 0:
            run -= thick_in  # an inward turn: the next wall owns the corner square
        corners = [(x, y), (x + run * dx, y + run * dy), (x + run * dx + thick_in * nx, y + run * dy + thick_in * ny),
                   (x + thick_in * nx, y + thick_in * ny)]
        px = [origin[0] + cx * scale for cx, _ in corners]
        py = [origin[1] - cy * scale for _, cy in corners]
        out.append((round(min(px)), round(min(py)), round(max(px)), round(max(py))))
        x += length * dx
        y += length * dy
    return out


def draw_plan(chain: Sequence[Wall], *, size: tuple[int, int] = (1800, 1400), scale: float = 8.0,
              thick_in: float = 3.0, origin: tuple[float, float] = (500.0, 1000.0), fill: tuple[int, int, int] = GREY,
              labels: Mapping[str, tuple[float, float] | None] | None = None, label_px: int = 90,
              cabinets: Sequence[tuple[int, int, int, int]] = ()) -> Image.Image:
    """A plan of the chain. labels: a wall's label position in pixels, None for no label; by default each
    one is printed beside its wall's middle, outside the room, its length below it."""
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    for box in cabinets:
        draw.rectangle(box, fill=CABINET, outline=INK)
    rects = boxes(chain, scale, thick_in, origin)
    for box in rects:
        draw.rectangle(box, fill=fill, outline=INK, width=1)
    big = ImageFont.load_default(size=label_px)
    small = ImageFont.load_default(size=label_px // 2)
    for (name, length, ang), box in zip(chain, rects):
        dx, dy = round(math.cos(math.radians(ang))), round(math.sin(math.radians(ang)))
        nx, ny = -dy, dx
        mid = ((box[0] + box[2]) / 2 + nx * (2.2 * label_px), (box[1] + box[3]) / 2 - ny * (2.2 * label_px))
        at = mid if labels is None or name not in labels else labels[name]
        if at is None:
            continue
        draw.text(at, name, font=big, fill=INK, anchor="mm")
        draw.text((at[0], at[1] + label_px), f'{length:g}"', font=small, fill=INK, anchor="mm")
    return img


def rows(chain: Sequence[Wall], height_in: float = 98.0) -> tuple[TableRow, ...]:
    """The WALL/AREA table of the chain's walls, in the order of their numbers, as IC prints it."""
    return tuple(TableRow(n, length, height_in) for n, length, _ in sorted(chain, key=lambda w: w[0]))


def as_pdf(img: Image.Image, pages: int = 1) -> bytes:
    """A PDF whose first page embeds the plan as its one image, losslessly (FlateDecode, as IC's are; Pillow's
    own PDF writer would store it as JPEG), and whose other pages are blank."""
    rgb = img.convert("RGB")
    data = zlib.compress(rgb.tobytes())
    w, h = rgb.size
    image = 3 + 2 * pages
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % (3 + 2 * k) for k in range(pages))
               + b"] /Count %d >>" % pages]
    for k in range(pages):
        resources = b"/Resources << /XObject << /Im0 %d 0 R >> >>" % image if k == 0 else b"/Resources << >>"
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 492] " + resources
                       + b" /Contents %d 0 R >>" % (4 + 2 * k))
        content = b"q 540 0 0 420 36 36 cm /Im0 Do Q" if k == 0 else b""
        objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    objects.append(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB "
                   b"/BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n" % (w, h, len(data))
                   + data + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % n + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    out.write(b"".join(b"%010d 00000 n \n" % o for o in offsets))
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()
