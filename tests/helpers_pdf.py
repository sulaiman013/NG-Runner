"""
Synthetic IC summaries for the fetch and branch tests: made-up names only, built in memory.

Why a writer of its own: tests/test_summary_check.py has the full one, but its owner changes it
while other slices are built, so these tests do not lean on it. This one makes only what
check_summary needs to pass a render, placed where Studio puts it in each report layout (the
coordinates were read off real renders): page 1's PROJECT NAME and design list, each design's
WALL/AREA page and one elevation drawing, a PULLED list, and "Page k of N" on every page. It
also makes the early renders Studio hands back before it has finished: every design listed on
page 1, only the first few drawn.

Given parts, the PULLED page lists those rows instead, and ends every cell's line as Studio's own
renders do (T* after each Tj), so pypdf reads one cell per line, as on a real render, and the
shared converter (ngrunner/convert) can read the list. The default page is left as it always was.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

DEFAULT_DESIGNS = (("Testerson - Primary Closet", "His and Her Master"),)

# ----- a tiny PDF writer (pypdf is for reading)


@dataclass
class _Page:
    width: float
    height: float
    texts: list[tuple[float, float, float, str]] = field(default_factory=list)  # x, y, size, text
    pictures: list[tuple[str, float, float, float, float]] = field(default_factory=list)  # name, x, y, w, h
    ends_lines: bool = False  # each text ends its line (T*), as on Studio's renders

    def text(self, x: float, y: float, size: float, text: str) -> None:
        self.texts.append((x, y, size, text))


def _escaped(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_pdf(pages: Sequence[_Page], info: dict[str, str]) -> bytes:
    """One PDF with a classic index, every stream stored as it is (no filters)."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    def stream(head: str, data: bytes) -> int:
        return add(f"<< {head} /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream")

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    tree = add(b"")  # the page tree, filled in once the pages exist
    pictures: dict[str, int] = {}
    kids: list[int] = []
    for page in pages:
        ends = " T*" if page.ends_lines else ""
        leading = f" {_leading(page)} TL" if page.ends_lines else ""
        ops = [f"BT /F1 {size:g} Tf{leading} 1 0 0 1 {x:g} {y:g} Tm ({_escaped(t)}) Tj{ends} ET"
               for x, y, size, t in page.texts]
        for name, x, y, w, h in page.pictures:
            if name not in pictures:  # 2 x 2 grey pixels
                pictures[name] = stream("/Type /XObject /Subtype /Image /Width 2 /Height 2"
                                        " /ColorSpace /DeviceGray /BitsPerComponent 8", b"\x00\x80\xc0\xff")
            ops.append(f"q {w:g} 0 0 {h:g} {x:g} {y:g} cm /{name} Do Q")
        contents = stream("", "\n".join(ops).encode("cp1252"))
        xobjects = " ".join(f"/{n} {pictures[n]} 0 R" for n in sorted({p[0] for p in page.pictures}))
        kids.append(add((f"<< /Type /Page /Parent {tree} 0 R /MediaBox [0 0 {page.width:g} {page.height:g}]"
                         f" /Resources << /Font << /F1 {font} 0 R >> /XObject << {xobjects} >> >>"
                         f" /Contents {contents} 0 R >>").encode()))
    objects[tree - 1] = (f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}]"
                         f" /Count {len(kids)} >>").encode()
    catalog = add(f"<< /Type /Catalog /Pages {tree} 0 R >>".encode())
    meta = add(("<< " + " ".join(f"/{k} ({_escaped(v)})" for k, v in info.items()) + " >>").encode())
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R /Info {meta} 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


# ----- where Studio puts things (points, origin bottom left), per report layout


@dataclass(frozen=True)
class _Geometry:
    size: tuple[float, float]
    label_x: float  # page 1 label column; values sit 126 pt to the right
    project_y: float  # page 1 PROJECT NAME row
    head: tuple[float, float]  # design page heading "Design: <name>": x, y
    table: tuple[float, float]  # WALL/AREA NAME (W X H): x, y
    title_y: float  # elevation title ("Wall 1")
    drawing: tuple[float, float, float, float]
    pulled_y: float
    pulled_header: tuple[tuple[str, float], ...]
    pulled_cells: tuple[tuple[float, float, float], ...]  # (qty x, item x, description x) per list
    page_number: tuple[float, float]


_LAYOUTS = {
    "portrait": _Geometry(
        size=(612, 792), label_x=229.2, project_y=563, head=(60, 733), table=(441.6, 452.2),
        title_y=705, drawing=(208, 403, 196, 288), pulled_y=701,
        pulled_header=(("PULLED", 61.1), ("ITEM #", 114), ("QUANTITY", 194.7), ("DESCRIPTION", 258),
                       ("DIMENSIONS", 456)),
        pulled_cells=((240.4, 114, 258),), page_number=(512.7, 36)),
    "landscape": _Geometry(
        size=(792, 612), label_x=218.4, project_y=383, head=(20.4, 574.6), table=(306.6, 289.8),
        title_y=546.6, drawing=(14, 225, 763, 308), pulled_y=558,
        pulled_header=(("PULLED", 28.4), ("QTY", 68.1), ("ITEM #", 99.9), ("DESCRIPTION", 155.5),
                       ("DIMENSIONS", 298.7), ("PULLED", 410), ("QTY", 449.7), ("ITEM #", 481.5),
                       ("DESCRIPTION", 537.1), ("DIMENSIONS", 680.2)),
        pulled_cells=((68.1, 99.9, 155.5), (449.7, 481.5, 537.1)), page_number=(736.7, 14.4)),
}


# ----- the summary


def summary_pdf(*, project_name: str | None = "Testerson_101426", layout: str = "landscape",
                designs: Sequence[tuple[str, str]] = DEFAULT_DESIGNS, finished: int | None = None,
                created: str = "D:20261014120000-07'00'",
                parts: Sequence[tuple[str, int, str]] | None = None) -> bytes:
    """
    An IC summary for made-up job `project_name`, in `layout` ("landscape" or "portrait").

    designs: (name, category) for each design page 1 lists. finished: how many of them, from the
    first, have their pages yet (None: all), so an early render lists every design while only the
    first few are drawn. project_name None leaves the PROJECT NAME label off page 1; "" prints the
    label with nothing beside it. created is the PDF's CreationDate: renders that differ only in
    it hold the same text in different bytes. parts: the PULLED list's rows as (item, quantity,
    description), printed as Studio prints them (see the module notes); None keeps the old page.
    """
    g = _LAYOUTS[layout]
    w, h = g.size
    pages: list[_Page] = []

    def new_page() -> _Page:
        pages.append(_Page(w, h))
        return pages[-1]

    cover, value_x, y0 = new_page(), g.label_x + 126, g.project_y
    if project_name is not None:
        cover.text(g.label_x, y0, 10, "PROJECT NAME")
        if project_name:
            cover.text(value_x, y0, 10, project_name)
    cover.text(g.label_x, y0 - 23, 10, "DESIGNS IN PROJECT")
    for i, (name, category) in enumerate(designs):
        cover.text(value_x, y0 - 23 - 12 * i, 10, f"{name} - ({category})")
    phone_y = y0 - 23 - 12 * max(len(designs) - 1, 0) - 23
    cover.text(g.label_x, phone_y, 10, "HOME")
    cover.text(value_x, phone_y, 10, "(555) 010-0199")

    drawn = designs if finished is None else designs[:finished]
    for n, (name, _category) in enumerate(drawn):
        table = new_page()
        table.text(*g.head, 14, f"Design: {name}")
        table.text(*g.table, 8, "WALL/AREA NAME (W X H)")
        for i, wall in enumerate(('1: 60" X 96"', '2: 72 1/2" X 96"')):
            table.text(g.table[0], g.table[1] - 14 * (i + 1), 8, wall)
        elevation = new_page()
        elevation.text(*g.head, 14, f"Design: {name}")
        elevation.text(g.head[0], g.title_y, 18, "Wall 1")
        elevation.pictures.append((f"Elev{n}", *g.drawing))

    pulled = new_page()
    for word, x in g.pulled_header:
        pulled.text(x, g.pulled_y, 10, word)
    if parts is not None:
        _studio_rows(pulled, g, layout, parts)
    for i in range(0 if parts is not None else 3):
        qty_x, item_x, desc_x = g.pulled_cells[i % len(g.pulled_cells)]
        row_y = g.pulled_y - 36 - 18 * (i // len(g.pulled_cells))
        pulled.text(item_x, row_y, 10, "SH-WH")
        pulled.text(qty_x, row_y, 10, str(i + 1))
        pulled.text(desc_x, row_y, 10, "Shelf Pin")

    for k, page in enumerate(pages, 1):
        page.text(*g.page_number, 8, f"Page {k} of {len(pages)}")
    title = project_name or "Untitled"
    return _write_pdf(pages, {"Producer": "test", "Title": f"{title} Install Summary",
                              "CreationDate": created, "ModDate": created})


def _leading(page: _Page) -> float:
    """The line height T* moves by: 1.2 times the page's largest text, as Studio's own pages set."""
    return max((size for _, _, size, _ in page.texts), default=10) * 1.2


def _studio_rows(page: _Page, g: _Geometry, layout: str,
                 parts: Sequence[tuple[str, int, str]]) -> None:
    """The rows as Studio draws them: row by row, the left list's row before the right's, each
    cell in its reading order (quantity first in the landscape layout, item first in the portrait
    one), and every line ended, so pypdf reads one cell per line."""
    page.ends_lines = True
    lists = len(g.pulled_cells)
    if len(parts) > 25 * lists:
        raise ValueError("more parts than one PULLED page holds")
    for i, (item, qty, description) in enumerate(parts):
        qty_x, item_x, desc_x = g.pulled_cells[i % lists]
        row_y = g.pulled_y - 36 - 18 * (i // lists)
        cells = ((qty_x, str(qty)), (item_x, item)) if layout == "landscape" else (
            (item_x, item), (qty_x, str(qty)))
        for x, text in (*cells, (desc_x, description)):
            page.text(x, row_y, 10, text)
