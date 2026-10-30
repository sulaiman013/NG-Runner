"""
Tests for the strict IC summary check (ngrunner.intake.summary_check).

Synthetic tests use made-up names only ("Testerson_101426"). Tests marked `fixtures` read the
real summaries in .local-fixtures (gitignored), derive every expectation at runtime, and refer
to files by index, never by name.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import io
import json
import re
import zlib
from collections import defaultdict
from dataclasses import dataclass, field, replace
from itertools import combinations
from pathlib import Path

import pytest
from pypdf import PdfReader
from pypdf.generic import DictionaryObject, StreamObject

from ngrunner.intake.summary_check import (
    SummaryCheck,
    check_summary,
    content_fingerprint,
    parse_wall_area,
    pulled_layout_of,
    split_design_list,
)

# ----- synthetic summaries
# A tiny PDF writer (pypdf is for reading) and a summary builder that places text and pictures
# where Studio puts them in its two page orientations, so the checks run end to end in git.


@dataclass
class _FakePage:
    width: float
    height: float
    texts: list[tuple[float, float, float, str]] = field(default_factory=list)  # x, y, size, text
    images: list[tuple[str, float, float, float, float]] = field(default_factory=list)  # name, x, y, w, h

    def text(self, x: float, y: float, size: float, text: str) -> None:
        self.texts.append((x, y, size, text))


def _pdf_string(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _encoded(data: bytes, filters: tuple[str, ...]) -> bytes:
    """Stream data that `filters`, applied in order as a PDF reader does, decode back to `data`."""
    for name in reversed(filters):
        if name == "/FlateDecode":
            data = zlib.compress(data, 0)  # stored, not squeezed: damage tests can find the text
        elif name == "/ASCII85Decode":
            data = base64.a85encode(data, wrapcol=64) + b"~>"
        else:
            raise ValueError(name)
    return data


@dataclass(frozen=True)
class _Art:
    """How the writer stores pictures and fonts, where a test needs more than its plain defaults."""
    picture: str = "/Width 2 /Height 2 /ColorSpace /DeviceGray /BitsPerComponent 8"
    pixels: bytes | None = None  # as stored before the filters; None: 4 bytes, the first the picture's initial
    picture_parms: str | None = None  # each picture's /DecodeParms (`pixels` written to match)
    mask: bool = False  # each picture is drawn through one shared /SMask
    font_file: str | None = None  # e.g. "/FontFile2": a font with this font file and a /ToUnicode map
    metadata: bool = False  # the catalog has XML /Metadata, as a linearized render's does
    indirect_length: bool = False  # each stream's /Length is a reference to the object after it


_MASK_PIXELS = b"M\x80\xc0\xff"
_FONT_PROGRAM = b"\x00\x01\x00\x00 a made-up font program"
_TO_UNICODE = (b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n1 begincodespacerange\n"
               b"<00> <FF>\nendcodespacerange\n1 beginbfrange\n<00> <FF> <0000>\nendbfrange\nendcmap\nend\nend\n")


def _write_pdf(pages: list[_FakePage], info: dict[str, str], filters: tuple[str, ...] = (),
               decode_parms: str | None = None, art: _Art = _Art()) -> bytes:
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    def stream(head: str, data: bytes, parms: str | None = decode_parms) -> int:
        data = _encoded(data, filters)
        chain = f" /Filter [{' '.join(filters)}]" if filters else ""
        chain += f" /DecodeParms {parms}" if parms is not None else ""
        length = f"{len(objects) + 2} 0 R" if art.indirect_length else f"{len(data)}"
        number = add(f"<< {head}{chain} /Length {length} >>\nstream\n".encode() + data + b"\nendstream")
        if art.indirect_length:
            add(b"%d " % len(data))
        return number

    if art.font_file:
        head = {"/FontFile": f"/Length1 10 /Length2 {len(_FONT_PROGRAM) - 10} /Length3 0",
                "/FontFile2": f"/Length1 {len(_FONT_PROGRAM)}",
                "/FontFile3": "/Subtype /OpenType"}[art.font_file]
        program, to_unicode = stream(head, _FONT_PROGRAM), stream("", _TO_UNICODE)
        descriptor = add(f"<< /Type /FontDescriptor /FontName /Fake /Flags 32 /FontBBox [0 0 1000 1000]"
                         f" /ItalicAngle 0 /Ascent 800 /Descent -200 /CapHeight 700 /StemV 80"
                         f" {art.font_file} {program} 0 R >>".encode())
        font = add(f"<< /Type /Font /Subtype /TrueType /BaseFont /Fake /Encoding /WinAnsiEncoding"
                   f" /FontDescriptor {descriptor} 0 R /ToUnicode {to_unicode} 0 R >>".encode())
    else:
        font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    tree = add(b"")  # the page tree, filled in once the pages exist
    mask = stream(f"/Type /XObject /Subtype /Image {_Art.picture}", _MASK_PIXELS) if art.mask else None
    # a colour profile, or any object a colour space may refer to
    icc = stream("/N 3", b"a made-up colour profile") if "{icc}" in art.picture else None
    described = art.picture.format(icc=icc)
    pictures: dict[str, int] = {}
    kids: list[int] = []
    for page in pages:
        ops = [f"BT /F1 {size:g} Tf 1 0 0 1 {x:g} {y:g} Tm ({_pdf_string(t)}) Tj ET"
               for x, y, size, t in page.texts]
        for name, x, y, w, h in page.images:
            if name not in pictures:  # by default 2 x 2 grey pixels, the first one the picture's initial
                pixels = name.encode()[:1] + b"\x80\xc0\xff" if art.pixels is None else art.pixels
                masked = f" /SMask {mask} 0 R" if mask else ""
                pictures[name] = stream(f"/Type /XObject /Subtype /Image {described}{masked}", pixels,
                                        art.picture_parms)
            ops.append(f"q {w:g} 0 0 {h:g} {x:g} {y:g} cm /{name} Do Q")
        contents = stream("", "\n".join(ops).encode("cp1252"))
        xobjects = " ".join(f"/{n} {pictures[n]} 0 R" for n in sorted({i[0] for i in page.images}))
        kids.append(add((f"<< /Type /Page /Parent {tree} 0 R /MediaBox [0 0 {page.width:g} {page.height:g}]"
                         f" /Resources << /Font << /F1 {font} 0 R >> /XObject << {xobjects} >> >>"
                         f" /Contents {contents} 0 R >>").encode()))
    objects[tree - 1] = (f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}]"
                         f" /Count {len(kids)} >>").encode()
    metadata = ""
    if art.metadata:
        metadata = f" /Metadata {stream('/Type /Metadata /Subtype /XML', b'<x:xmpmeta/>')} 0 R"
    catalog = add(f"<< /Type /Catalog /Pages {tree} 0 R{metadata} >>".encode())
    meta = add(("<< " + " ".join(f"/{k} ({_pdf_string(v)})" for k, v in info.items()) + " >>").encode())
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


@dataclass(frozen=True)
class _Geometry:
    """Where Studio puts things, read off real renders (points; origin bottom left)."""
    size: tuple[float, float]
    label_x: float  # page 1 label column; values sit 126 pt to the right
    project_y: float  # page 1 PROJECT NAME row
    head: tuple[float, float, float]  # design page heading: Design x, Project x, y
    plan: tuple[float, float, float, float]
    table: tuple[float, float, float, float]  # STYLES x, WALL/AREA x, y, font size
    title_y: float  # elevation title ("Wall 2")
    drawing: tuple[float, float, float, float]
    pulled_y: float
    pulled_header: tuple[tuple[str, float], ...]
    pulled_cells: tuple[tuple[float, float, float], ...]  # (qty x, item x, description x) per list
    footer: tuple[float, float, float, float]  # footer x, y, page-number x, y
    logo: tuple[float, float, float, float]


PORTRAIT = _Geometry(
    size=(612, 792), label_x=229.2, project_y=563, head=(60, 399.2, 733), plan=(103, 474, 407, 252),
    table=(60, 441.6, 452.2, 8), title_y=705, drawing=(208, 403, 196, 288), pulled_y=701,
    pulled_header=(("PULLED", 61.1), ("ITEM #", 114), ("QUANTITY", 194.7), ("DESCRIPTION", 258),
                   ("DIMENSIONS", 456)),
    pulled_cells=((240.4, 114, 258),), footer=(500.1, 93.6, 512.7, 36), logo=(54, 50, 58, 54))
LANDSCAPE = _Geometry(
    size=(792, 612), label_x=218.4, project_y=383, head=(20.4, 614.7, 574.6), plan=(144, 316, 504, 252),
    table=(20.4, 306.6, 289.8, 12), title_y=546.6, drawing=(14, 225, 763, 308), pulled_y=558,
    pulled_header=(("PULLED", 28.4), ("QTY", 68.1), ("ITEM #", 99.9), ("DESCRIPTION", 155.5),
                   ("DIMENSIONS", 298.7), ("PULLED", 410), ("QTY", 449.7), ("ITEM #", 481.5),
                   ("DESCRIPTION", 537.1), ("DIMENSIONS", 680.2)),
    pulled_cells=((68.1, 99.9, 155.5), (449.7, 481.5, 537.1)), footer=(177.2, 14.4, 736.7, 14.4),
    logo=(14, 14, 43, 40))


@dataclass(frozen=True)
class FakeDesign:
    name: str = "Testerson - Primary Closet"
    category: str = "His and Her Master"
    walls: tuple[str, ...] = ('1: 60" X 96"', '2: 72 1/2" X 96"', '3: 60" X 96"')
    elevations: tuple[str, ...] = ("2",)  # walls that get an elevation page
    without_drawing: tuple[str, ...] = ()  # of those, walls whose page has no drawing
    has_summary_page: bool = True


def fake_summary(*, geometry: _Geometry = PORTRAIT, project_name: str | None = "Testerson_101426",
                 designs: tuple[FakeDesign, ...] = (FakeDesign(),), design_list: list[str] | None = None,
                 pulled_items: int = 3, pulled_wraps: int = 0, pulled_pages: int = 1,
                 pulled_header: list[str] | None = None, numbered: bool = True,
                 numbered_out_of: int | None = None, filters: tuple[str, ...] = (),
                 decode_parms: str | None = None, art: _Art = _Art(),
                 created: str = "D:20261014120000-07'00'") -> bytes:
    g = geometry
    w, h = g.size
    project = project_name or "Testerson_101426"
    pages: list[_FakePage] = []

    def new_page() -> _FakePage:
        pages.append(_FakePage(w, h))
        return pages[-1]

    # page 1: the cover, labels on the left, values beside them
    cover, value_x, y0 = new_page(), g.label_x + 126, g.project_y
    cover.text(g.label_x - 116, y0 + 1, 12, "Pat Example")
    for dy, label, value in ((139, "CONTACT ADDRESS", "Pat Testerson"), (92, "CONTACT NAME", "Pat"),
                             (46, "EMAIL", "pat@example.com")):
        cover.text(g.label_x, y0 + dy, 10, label)
        cover.text(value_x, y0 + dy, 10, value)
    if project_name is not None:
        cover.text(g.label_x, y0, 10, "PROJECT NAME")
        if project_name:
            cover.text(value_x, y0, 10, project_name)
    cover.text(g.label_x, y0 - 23, 10, "DESIGNS IN PROJECT")
    lines = design_list if design_list is not None else [f"{d.name} - ({d.category})" for d in designs]
    for i, line in enumerate(lines):
        cover.text(value_x, y0 - 23 - 12 * i, 10, line)
    phone_y = y0 - 23 - 12 * max(len(lines) - 1, 0) - 23
    cover.text(g.label_x, phone_y, 10, "HOME")
    cover.text(value_x, phone_y, 10, "(555) 010-0199")

    def heading(page: _FakePage, design: FakeDesign) -> None:
        page.text(g.head[0], g.head[2], 14, f"Design: {design.name}")
        page.text(g.head[1], g.head[2], 14, f"Project: {project}")

    for n, design in enumerate(designs):
        if design.has_summary_page:
            page = new_page()
            heading(page, design)
            page.images.append((f"Plan{n}", *g.plan))
            styles_x, table_x, table_y, size = g.table
            page.text(styles_x, table_y, size, "STYLES, HARDWARE, AND FINISHES")
            page.text(table_x, table_y, size, "WALL/AREA NAME (W X H)")
            page.text(styles_x, table_y - 14, 8, "Shelf: Arctic White")
            for i, wall in enumerate(design.walls):
                page.text(table_x, table_y - 14 * (i + 1), 8, wall)
        for wall in design.elevations:
            page = new_page()
            heading(page, design)
            page.text(g.head[0], g.title_y, 18, f"Wall {wall}")
            if wall not in design.without_drawing:
                page.images.append((f"Elev{n}w{wall}", *g.drawing))
            page.text(g.head[0], g.title_y - 340, 10, f"Wall {wall}")
            page.text(g.head[0] + 72, g.title_y - 358, 10, "SH-WH")

    estimate = new_page()
    estimate.text(g.head[0], g.title_y - 5, 18, "Estimate")
    estimate.text(w / 2, g.title_y - 5, 18, f"Project: {project}")
    estimate.text(g.head[0], g.title_y - 200, 12, "Total")

    for p in range(pulled_pages):
        page = new_page()
        page.text(g.head[0], g.pulled_y + 26, 14, "Project Parts List")
        page.text(g.head[1], g.pulled_y + 26, 14, f"Project: {project}")
        header = list(g.pulled_header)
        if pulled_header is not None and p == pulled_pages - 1:  # override the last PULLED page
            header = [(word, 50 + 55 * i) for i, word in enumerate(pulled_header)]
        for word, x in header:
            page.text(x, g.pulled_y, 10, word)
        for i in range(pulled_items):
            qty_x, item_x, desc_x = g.pulled_cells[i % len(g.pulled_cells)]
            row_y = g.pulled_y - 36 - 18 * (i // len(g.pulled_cells))
            page.text(item_x, row_y, 10, "SH-WH" if i % 2 else "400000155")
            if i < pulled_wraps:  # an item name wrapped onto a second line under ITEM #
                page.text(item_x, row_y - 9, 10, "Open Door")
            page.text(qty_x, row_y, 10, str(i + 1))
            page.text(desc_x, row_y, 10, "Shelf Pin")

    total = numbered_out_of or len(pages)
    for k, page in enumerate(pages, 1):
        page.images.append(("Logo", *g.logo))
        page.text(g.footer[0], g.footer[1], 8, "Inspired Closets")
        if numbered:
            page.text(g.footer[2], g.footer[3], 8, f"Page {k} of {total}")
    return _write_pdf(pages, {"Producer": "test", "Title": f"{project} Install Summary",
                              "CreationDate": created, "ModDate": created}, filters, decode_parms, art)


# ----- check_summary on complete summaries


def test_complete_portrait_summary_passes():
    result = check_summary(fake_summary(geometry=PORTRAIT))
    assert result.missing == []
    assert result.ok is True
    assert result.project_name == "Testerson_101426"
    assert result.designs == ["Testerson - Primary Closet"]
    assert result.pulled_layout == "B"
    assert result.page_count == 5  # cover, WALL/AREA page, one elevation, estimate, PULLED


def test_complete_landscape_summary_with_two_designs_passes():
    garage = FakeDesign(name="Testerson - Garage", category="Garage", elevations=("1", "3"))
    result = check_summary(fake_summary(geometry=LANDSCAPE, designs=(FakeDesign(), garage)))
    assert result.missing == []
    assert result.ok is True
    assert result.designs == ["Testerson - Primary Closet", "Testerson - Garage"]
    assert result.pulled_layout == "A"


# ----- check_summary rejections: every problem is named, none is guessed past


def rejected(pdf: bytes) -> list[str]:
    """Check a render that must fail; return its problem codes (stable, unlike the wording)."""
    result = check_summary(pdf)
    assert result.ok is False
    assert len(result.missing) == len(result.details["codes"]) > 0
    assert all(isinstance(m, str) and m.strip() and "\n" not in m for m in result.missing)
    return result.details["codes"]


def test_page_one_without_project_name_is_rejected():
    result = check_summary(fake_summary(project_name=None))
    assert result.details["codes"] == ["no_project_name"]
    assert result.missing == ["Page 1 does not show PROJECT NAME."]
    assert result.project_name is None and result.ok is False


def test_project_name_label_with_nothing_beside_it_is_rejected():
    # A plain text dump would read the next label ("DESIGNS IN PROJECT") as the name.
    result = check_summary(fake_summary(project_name=""))
    assert result.details["codes"] == ["blank_project_name"]
    assert result.project_name is None


def test_early_render_listing_no_designs_is_rejected():
    assert "no_designs" in rejected(fake_summary(designs=()))


def test_design_list_that_cannot_be_read_is_rejected():
    codes = rejected(fake_summary(design_list=["Testerson - Primary Closet - (His and Her Master)",
                                               "Testerson - Pantry without a category"]))
    assert "unread_design_list" in codes


def test_two_designs_with_one_name_cannot_be_told_apart():
    codes = rejected(fake_summary(designs=(FakeDesign(), FakeDesign())))
    assert codes == ["same_name_designs"]


def test_design_without_its_wall_area_page_is_rejected():
    garage = FakeDesign(name="Testerson - Garage", category="Garage", has_summary_page=False)
    result = check_summary(fake_summary(designs=(FakeDesign(), garage)))
    assert result.details["codes"] == ["no_wall_area"]
    assert result.missing == ['Design "Testerson - Garage" has no WALL/AREA table.']


def test_wall_area_table_with_no_walls_is_rejected():
    assert rejected(fake_summary(designs=(FakeDesign(walls=()),))) == ["empty_wall_area"]


def test_design_without_elevations_is_rejected():
    result = check_summary(fake_summary(designs=(FakeDesign(elevations=()),)))
    assert result.details["codes"] == ["no_elevations"]
    assert result.missing == ['Design "Testerson - Primary Closet" has no elevation drawings.']


def test_elevation_page_without_its_drawing_is_rejected():
    design = FakeDesign(elevations=("1", "2"), without_drawing=("2",))
    result = check_summary(fake_summary(designs=(design,)))
    assert result.details["codes"] == ["no_drawing"]
    assert result.missing == [
        'Design "Testerson - Primary Closet", Wall 2: the elevation page has no drawing.']


def test_design_pages_missing_from_the_page_one_list_are_rejected():
    garage = FakeDesign(name="Testerson - Garage", category="Garage")
    codes = rejected(fake_summary(designs=(FakeDesign(), garage),
                                  design_list=["Testerson - Primary Closet - (His and Her Master)"]))
    assert codes == ["unlisted_design"]


def test_summary_without_pulled_list_is_rejected():
    result = check_summary(fake_summary(pulled_pages=0))
    assert result.details["codes"] == ["no_pulled"]
    assert result.pulled_layout is None


def test_pulled_list_with_no_items_is_rejected():
    assert rejected(fake_summary(geometry=LANDSCAPE, pulled_items=0)) == ["empty_pulled"]


def test_pulled_facts_count_lines_under_item_number_not_items():
    # Real renders wrap long item names, so this counts what it can honestly count: lines of text
    # under ITEM #. That is all the empty-list check needs.
    result = check_summary(fake_summary(pulled_items=3, pulled_wraps=1))
    assert result.ok is True
    assert result.details["pulled"] == {"pages": [5], "item_lines": 4}


def test_pulled_list_in_an_unknown_layout_is_rejected():
    result = check_summary(fake_summary(pulled_header=["PULLED", "DESCRIPTION", "QTY", "ITEM #"]))
    assert result.details["codes"] == ["unknown_pulled_layout"]
    assert result.pulled_layout is None


def test_pulled_pages_that_mix_layouts_are_rejected():
    assert rejected(fake_summary(pulled_pages=2, pulled_header=PULLED_A)) == ["mixed_pulled_layout"]


def test_page_numbers_that_do_not_match_the_file_are_rejected():
    result = check_summary(fake_summary(numbered_out_of=19))
    assert result.details["codes"] == ["page_numbers"]
    assert result.missing == ["The file has 5 pages but they are numbered out of 19."]


def test_pages_without_page_numbers_cannot_be_verified_complete():
    # Every real render numbers its pages; without "Page k of N" a lost page would go unseen.
    result = check_summary(fake_summary(numbered=False))
    assert result.details["codes"] == ["no_page_numbers"]
    assert result.missing == ["Could not verify that no pages are missing: the pages are not numbered."]
    assert result.details["page_numbers"] == "absent"


def test_a_web_page_saved_as_pdf_is_rejected():
    result = check_summary(b"<html><body>Not found</body></html>")
    assert result.details["codes"] == ["not_pdf"]
    assert result.page_count == 0 and result.project_name is None
    assert result.designs == [] and result.pulled_layout is None


def test_a_cut_off_download_is_rejected():
    pdf = fake_summary()
    assert rejected(pdf[: len(pdf) // 2]) == ["cut_off"]
    assert rejected(pdf.replace(b"%%EOF", b"")) == ["cut_off"]


def test_bytes_that_only_look_like_a_pdf_are_rejected():
    assert rejected(b"%PDF-1.4\nthis is not really a PDF\n%%EOF\n") == ["unreadable"]


def test_a_damaged_pdf_is_rejected_even_when_pypdf_could_patch_it_up():
    # Fuzzing a real render found this: one byte flipped inside an object (there, a font's widths)
    # made pypdf drop the rest of that object and read on, so the text changed and the check
    # passed. Here the flip lands in the font's /Encoding: the apostrophe would read as a curly one.
    pdf = fake_summary(designs=(FakeDesign(category="Women's Closet"),))
    damaged = pdf.replace(b"/Encoding /WinAnsiEncoding", b"/Encoding UWinAnsiEncoding", 1)
    assert len(damaged) == len(pdf) and damaged != pdf  # one byte: every offset still points true
    assert rejected(damaged) == ["unreadable"]
    with pytest.raises(ValueError):
        content_fingerprint(damaged)


# Damage that strict pypdf reads past: refused as unreadable, in words of its own.
DAMAGED = (["unreadable"], ["The PDF is damaged."])


def verdict(pdf: bytes) -> tuple[list[str], list[str]]:
    result = check_summary(pdf)
    return result.details["codes"], result.missing


def _flipped(pdf: bytes, old: bytes, new: bytes) -> bytes:
    """`pdf` with `old` turned into `new` where a stream stores it, the stream's checksum left as it was."""
    if old in pdf:  # a stored Flate stream keeps its text as it is
        return pdf.replace(old, new, 1)
    for found in re.finditer(rb"stream\n(.+?)~>\nendstream", pdf, re.S):  # ASCII85 around stored Flate
        stored = base64.a85decode(found[1], ignorechars=b"\n")
        if old in stored:
            encoded = base64.a85encode(stored.replace(old, new, 1), wrapcol=64)
            return pdf[:found.start(1)] + encoded + pdf[found.end(1):]
    raise AssertionError(f"{old!r} is in no stream")


@pytest.mark.parametrize("filters", [("/FlateDecode",), ("/ASCII85Decode", "/FlateDecode")],
                         ids=["Flate", "ASCII85 then Flate, as Studio writes them"])
def test_a_bit_flipped_in_a_compressed_stream_is_refused_though_pypdf_reads_past_it(filters):
    # Re-verification of real renders: pypdf's Flate decoder drops a stream's checksum and
    # inflates what it can, so one flipped bit read on as other text, digits included, and the
    # check passed. Here the bit turns wall 2's 72 1/2" into 73 1/2".
    pdf = fake_summary(filters=filters)
    assert check_summary(pdf).ok
    damaged = _flipped(pdf, b'2: 72 1/2"', b'2: 73 1/2"')
    assert len(damaged) == len(pdf) and damaged != pdf
    assert verdict(damaged) == DAMAGED
    with pytest.raises(ValueError):
        content_fingerprint(damaged)


def test_a_stream_whose_checksum_alone_is_damaged_is_refused():
    # pypdf cuts a failing checksum off and reads on, here with the text intact. A wrong checksum
    # cannot say whether it or the text was hit, so the file is refused either way.
    pdf = fake_summary(filters=("/FlateDecode",))
    end = pdf.index(b"\nendstream", pdf.index(b'2: 72 1/2"'))  # where the WALL/AREA page's stream ends
    damaged = pdf[:end - 1] + bytes([pdf[end - 1] ^ 0x01]) + pdf[end:]  # the adler32's last byte
    assert verdict(damaged) == DAMAGED


def test_a_damaged_drawing_is_refused_though_reading_the_text_never_opens_it():
    # Damage in a picture or a font never shows in the text, so two fetches' fingerprints agree
    # over it: only this check keeps it out of NG Jobs.
    pdf = fake_summary(filters=("/ASCII85Decode", "/FlateDecode"))
    damaged = _flipped(pdf, b"E\x80\xc0\xff", b"E\x81\xc0\xff")  # one pixel of an elevation drawing
    assert len(damaged) == len(pdf) and damaged != pdf
    assert verdict(damaged) == DAMAGED


def _number(pdf: bytes, key: str) -> int:
    """The number of the object that dictionary key `key` first refers to, e.g. "Elev0w2" or "ToUnicode"."""
    return int(re.search(rb"/%s (\d+) 0 R" % key.encode(), pdf)[1])


def _start(pdf: bytes, number: int) -> int:
    """Where object `number` starts."""
    return pdf.index(b"\n%d 0 obj\n" % number) + 1


def _in_dictionary(pdf: bytes, key: str, old: bytes, new: bytes) -> bytes:
    """`pdf` with `old` turned into `new` (padded to its length) in the stream dictionary `key` refers to."""
    start = _start(pdf, _number(pdf, key))
    end = pdf.index(b"stream\n", start)
    assert old in pdf[start:end]
    return pdf[:start] + pdf[start:end].replace(old, new.ljust(len(old)), 1) + pdf[end:]


@pytest.mark.parametrize("old, new", [
    (b"/FlateDecode", b"/FlateDecodd"),  # one bit
    (b"/FlateDecode", b"/ZstdDecode"),
    (b"/FlateDecode", b"/Fl"),  # Flate's short name, which only an inline image may use
    (b"[/FlateDecode]", b"7"),
], ids=["one bit flipped in its name", "an unknown filter", "an inline-image abbreviation", "a number"])
def test_a_drawing_whose_filter_is_damaged_is_refused_though_reading_the_text_never_decodes_it(old, new):
    # Re-verification: one bit flipped in the first picture's /FlateDecode passed on every archived
    # render. Text extraction never decodes a picture, and its page still draws it, so the drawing
    # check counted a drawing that no reader can decode.
    pdf = fake_summary(filters=("/FlateDecode",))
    damaged = _in_dictionary(pdf, "Elev0w2", old, new)  # wall 2's elevation drawing
    assert len(damaged) == len(pdf) and damaged != pdf
    assert verdict(damaged) == DAMAGED


def test_a_stream_nothing_decodes_is_refused_for_a_damaged_filter_too():
    pdf = fake_summary(filters=("/FlateDecode",), art=_Art(metadata=True))
    assert check_summary(pdf).missing == []
    assert verdict(_in_dictionary(pdf, "Metadata", b"/FlateDecode", b"/FlateDecodd")) == DAMAGED


# ----- a stream that one bit turns into a plain dictionary

_WIDE = _Art(picture="/Width 16 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8", pixels=bytes(range(16)))


@pytest.mark.parametrize("letter", range(len("stream")))
@pytest.mark.parametrize("key", ["Elev0w2", "FontFile2", "ToUnicode", "Metadata"],
                         ids=["a drawing", "a font file", "a /ToUnicode map", "a stream nothing else reads"])
def test_a_damaged_stream_keyword_is_refused_though_pypdf_reads_a_plain_dictionary(key, letter):
    # Re-verification: one bit in any letter of 'stream' passed on every archived render, for every
    # picture and font file. pypdf finds no stream, so no check of streams saw it, and the page's
    # Do still counted as a drawing.
    pdf = fake_summary(filters=("/ASCII85Decode", "/FlateDecode"),
                       art=_Art(font_file="/FontFile2", metadata=True))
    assert check_summary(pdf).missing == []
    keyword = pdf.index(b"stream\n", _start(pdf, _number(pdf, key)))
    damaged = bytearray(pdf)
    damaged[keyword + letter] ^= 0x01
    assert verdict(bytes(damaged)) == DAMAGED


def test_a_length_object_read_on_the_way_to_its_stream_must_end_at_its_endobj_too():
    # pypdf reads a stream's indirect /Length as it reads the stream, so it never parses that
    # object again when its own turn comes.
    pdf = fake_summary(art=_Art(indirect_length=True))
    assert check_summary(pdf).missing == []
    assert verdict(pdf.replace(b"\n4 \nendobj", b"\n4>\nendobj", 1)) == DAMAGED


@pytest.mark.parametrize("old, new", [(b"/Length 16", b"/Length 1>"), (b"/Width 16", b"/Width 1>")],
                         ids=["in /Length", "in /Width"])
def test_a_digit_turned_into_the_end_of_a_dictionary_is_refused_though_pypdf_stops_reading_there(old, new):
    # Re-verification: "6" and ">" are one bit apart, and pypdf ends a dictionary at ">" and any
    # byte. It then finds no 'stream' and never looks for 'endobj', so a drawing no reader can
    # decode still counted as a drawing, on every archived render.
    pdf = fake_summary(art=_WIDE)
    assert check_summary(pdf).missing == []
    assert verdict(_in_dictionary(pdf, "Elev0w2", old, new)) == DAMAGED


# ----- a picture must decode to what its dictionary describes

_SOME_FILTERS = [("/FlateDecode",), ("/ASCII85Decode", "/FlateDecode")]
_SOME_FILTER_IDS = ["Flate", "ASCII85 then Flate, as Studio writes them"]


@pytest.mark.parametrize("filters", _SOME_FILTERS, ids=_SOME_FILTER_IDS)
def test_a_drawing_whose_filter_key_is_renamed_is_refused_though_no_checksum_reaches_it(filters):
    # Re-verification: one bit renames the key, which leaves a stream with no filter. It passed for
    # every picture of every archived render, with the stream decoding to other bytes.
    pdf = fake_summary(filters=filters)
    assert verdict(_in_dictionary(pdf, "Elev0w2", b"/Filter", b"/Filteq")) == DAMAGED


@pytest.mark.parametrize("old, new", [
    (b"/Width 16", b"/Width 17"),
    (b"/Height 1", b"/Height 3"),
    (b"/BitsPerComponent 8", b"/BitsPerComponent 9"),
    (b"/DeviceGray", b"/DeviceRGB"),
    (b"/DeviceGray", b"/DeviceGraz"),
    (b"/DeviceGray", b"/G"),  # /DeviceGray's short name, which only an inline image may use
    (b"/Width", b"/Widti"),
    (b"/Height", b"/Heighu"),
    (b"/BitsPerComponent", b"/BitsPerComponens"),
    (b"/ColorSpace", b"/ColorSpacd"),
], ids=["a wrong width", "a wrong height", "wrong bits", "another colour space", "a damaged colour space",
        "an inline-image colour space", "no width", "no height", "no bits", "no colour space"])
def test_a_picture_that_does_not_decode_to_what_its_dictionary_describes_is_refused(old, new):
    # Reading the text never decodes a picture, so these pass strict pypdf: a reader would draw
    # the pixels wrongly or not at all.
    pdf = fake_summary(art=_WIDE)
    assert check_summary(pdf).missing == []
    assert verdict(_in_dictionary(pdf, "Elev0w2", old, new)) == DAMAGED


@pytest.mark.parametrize("picture, size", [
    ("/Width 2 /Height 2 /ColorSpace /DeviceGray /BitsPerComponent 8", 4),
    ("/Width 3 /Height 2 /ColorSpace /DeviceRGB /BitsPerComponent 8", 18),
    ("/Width 2 /Height 1 /ColorSpace /DeviceCMYK /BitsPerComponent 8", 8),
    ("/Width 2 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 16", 12),
    ("/Width 9 /Height 2 /ColorSpace /DeviceGray /BitsPerComponent 1", 4),  # rows end on whole bytes
    ("/Width 9 /Height 2 /ImageMask true", 4),
    ("/Width 2 /Height 1 /ColorSpace [/DeviceRGB] /BitsPerComponent 8", 6),
    ("/Width 2 /Height 1 /ColorSpace [/CalGray << /WhitePoint [1 1 1] >>] /BitsPerComponent 8", 2),
    ("/Width 2 /Height 1 /ColorSpace [/CalRGB << /WhitePoint [1 1 1] >>] /BitsPerComponent 8", 6),
    ("/Width 2 /Height 1 /ColorSpace [/Lab << /WhitePoint [1 1 1] >>] /BitsPerComponent 8", 6),
    ("/Width 2 /Height 2 /ColorSpace [/Indexed /DeviceRGB 1 <000000FFFFFF>] /BitsPerComponent 8", 4),
    ("/Width 2 /Height 1 /ColorSpace [/ICCBased {icc} 0 R] /BitsPerComponent 8", 6),
    ("/Width 2 /Height 1 /ColorSpace [/Separation /Spot /DeviceGray {icc} 0 R] /BitsPerComponent 8", 2),
    ("/Width 2 /Height 1 /ColorSpace [/DeviceN [/Spot /Other] /DeviceGray {icc} 0 R] /BitsPerComponent 8", 4),
], ids=["grey", "RGB", "CMYK", "16 bits", "1 bit", "a stencil", "a colour space in an array", "CalGray",
        "CalRGB", "Lab", "indexed", "ICC", "a spot colour", "DeviceN"])
def test_a_picture_of_each_kind_the_pdf_standard_allows_passes_at_its_own_size(picture, size):
    art = _Art(picture=picture, pixels=bytes(size))
    assert check_summary(fake_summary(art=art)).missing == []
    assert verdict(fake_summary(art=replace(art, pixels=bytes(size + 1)))) == DAMAGED


@pytest.mark.parametrize("codec", ["/DCTDecode", "/JPXDecode", "/CCITTFaxDecode", "/JBIG2Decode"],
                         ids=["JPEG", "JPEG 2000", "CCITT", "JBIG2"])
def test_a_picture_in_its_own_image_format_is_not_measured(codec):
    # pypdf does not decode these to samples, so there is no size to compare. No real render has one.
    pdf = fake_summary(filters=("/ASCII85Decode", "/FlateDecode"))
    coded = _in_dictionary(pdf, "Elev0w2", b"/ASCII85Decode /FlateDecode", codec.encode())
    assert check_summary(coded).missing == []


_PNG_ROWS = "<< /Predictor 15 /Colors 1 /BitsPerComponent 8 /Columns 2 >>"
# 2 rows of 2 grey pixels, each row's first byte its PNG filter
_PREDICTED = _Art(pixels=b"\x00\x10\x20\x00\x30\x40", picture_parms=_PNG_ROWS)


@pytest.mark.parametrize("filters, parms", [(("/FlateDecode",), _PNG_ROWS),
                                            (("/ASCII85Decode", "/FlateDecode"), f"[null {_PNG_ROWS}]")],
                         ids=_SOME_FILTER_IDS)
def test_a_picture_is_measured_after_its_predictor(filters, parms):
    pdf = fake_summary(filters=filters, art=replace(_PREDICTED, picture_parms=parms))
    assert check_summary(pdf).missing == []


@pytest.mark.parametrize("art, old, new", [
    (_PREDICTED, b"/Predictor 15", b"/Predictor 1"),
    (_PREDICTED, b"/Predictor", b"/Predictos"),
    (_PREDICTED, b"/Columns 2", b"/Columns 3"),
    (_PREDICTED, b"/Columns", b"/Columnr"),
    (_PREDICTED, b"/Colors 1", b"/Colors 3"),
    (_PREDICTED, b"/BitsPerComponent 8 /Columns", b"/BitsPerComponent 1 /Columns"),
    (_Art(picture_parms="<< /Predictor 1 >>"), b"/Predictor 1", b"/Predictor 3"),
], ids=["predictor off", "predictor key renamed", "wrong columns", "columns key renamed", "wrong colors",
        "wrong bits", "no such predictor"])
def test_a_picture_whose_filter_parameters_are_damaged_is_refused(art, old, new):
    # Re-verification: /DecodeParms were checked for their shape only, so one bit in a value passed
    # with the pixels decoding differently or not at all.
    pdf = fake_summary(filters=("/FlateDecode",), art=art)
    assert check_summary(pdf).missing == []
    assert verdict(_in_dictionary(pdf, "Elev0w2", old, new)) == DAMAGED


# ----- a font file must decode to its stated length, a /ToUnicode map to a CMap


@pytest.mark.parametrize("key", ["FontFile2", "ToUnicode"], ids=["a font file", "a /ToUnicode map"])
def test_a_font_stream_whose_filter_key_is_renamed_is_refused_though_no_checksum_reaches_it(key):
    # Re-verification: this passed for every font file of every archived render and for most maps,
    # and for two maps the text changed: 1 or 2 lines of page 1 were no longer the file's.
    pdf = fake_summary(filters=("/ASCII85Decode", "/FlateDecode"), art=_Art(font_file="/FontFile2"))
    assert check_summary(pdf).missing == []
    assert verdict(_in_dictionary(pdf, key, b"/Filter", b"/Filteq")) == DAMAGED


@pytest.mark.parametrize("old, new", [
    (b"/Length1 %d" % len(_FONT_PROGRAM), b"/Length1 %d" % (len(_FONT_PROGRAM) + 1)),
    (b"/Length1", b"/Length0"),
], ids=["a wrong length", "no length"])
def test_a_font_file_that_does_not_decode_to_its_stated_length_is_refused(old, new):
    pdf = fake_summary(art=_Art(font_file="/FontFile2"))
    assert verdict(_in_dictionary(pdf, "FontFile2", old, new)) == DAMAGED


def test_a_type_1_font_file_is_measured_by_its_three_parts():
    pdf = fake_summary(art=_Art(font_file="/FontFile"))
    assert check_summary(pdf).missing == []
    assert verdict(_in_dictionary(pdf, "FontFile", b"/Length2 ", b"/Length0 ")) == DAMAGED


def test_a_compact_font_file_has_no_length_to_compare():
    assert check_summary(fake_summary(art=_Art(font_file="/FontFile3"))).missing == []


def test_a_font_file_entry_that_leads_to_no_font_file_is_refused():
    pdf = fake_summary(art=_Art(font_file="/FontFile2"))
    reference = b"/FontFile2 %d 0 R" % _number(pdf, "FontFile2")
    other = b"/FontFile2 %d 0 R" % _number(pdf, "ToUnicode")  # a stream, with no /Length1
    assert len(other) == len(reference) and verdict(pdf.replace(reference, other)) == DAMAGED


@pytest.mark.parametrize("word", [b"begincmap", b"endcmap"])
def test_a_to_unicode_stream_that_does_not_decode_to_a_cmap_is_refused(word):
    pdf = fake_summary(art=_Art(font_file="/FontFile2"))
    assert verdict(pdf.replace(word, word[:-1] + bytes([word[-1] ^ 0x01]), 1)) == DAMAGED


def test_a_to_unicode_entry_that_leads_to_no_stream_is_refused():
    pdf = fake_summary(art=_Art(font_file="/FontFile2"))
    reference = b"/ToUnicode %d 0 R" % _number(pdf, "ToUnicode")
    other = b"/ToUnicode %d 0 R" % _number(pdf, "FontDescriptor")
    assert len(other) == len(reference) and verdict(pdf.replace(reference, other)) == DAMAGED


def test_a_to_unicode_entry_may_name_a_predefined_map():
    pdf = fake_summary(art=_Art(font_file="/FontFile2"))
    reference = b"/ToUnicode %d 0 R" % _number(pdf, "ToUnicode")
    assert check_summary(pdf.replace(reference, b"/ToUnicode /Id".ljust(len(reference)))).missing == []


@pytest.mark.parametrize("filters, parms", [
    (("/FlateDecode",), "<< /Predictor 1 >>"),
    (("/ASCII85Decode", "/FlateDecode"), "[null << /Predictor 1 >>]"),
    (("/FlateDecode",), "null"),  # with two filters pypdf 6.21.4 applies only the first: unreadable
], ids=["a dictionary for one filter", "one entry per filter", "null"])
def test_filter_parameters_that_match_their_filters_pass(filters, parms):
    assert check_summary(fake_summary(filters=filters, decode_parms=parms)).missing == []


@pytest.mark.parametrize("filters, parms", [
    (("/FlateDecode",), "7"),
    (("/ASCII85Decode", "/FlateDecode"), "<< /Predictor 1 >>"),
    (("/ASCII85Decode", "/FlateDecode"), "[null]"),
    (("/ASCII85Decode", "/FlateDecode"), "[null 7]"),
    ((), "<< /Predictor 1 >>"),
], ids=["a number", "one dictionary for two filters", "one entry for two filters",
        "an entry that is a number", "parameters with no filter"])
def test_filter_parameters_that_do_not_match_their_filters_are_refused(filters, parms):
    assert verdict(fake_summary(filters=filters, decode_parms=parms)) == DAMAGED


def test_data_after_the_end_marker_is_refused():
    # pypdf reads a PDF from its end marker back, so anything glued on after it went unseen.
    pdf = fake_summary()
    assert check_summary(pdf + b"\r\n \n").ok  # line ends after %%EOF are not data
    assert verdict(pdf + b"<html><body>504 Gateway Time-out</body></html>\n") == DAMAGED


def test_a_file_holding_the_pdf_twice_is_refused():
    # The second copy's end marker closes the file, and its index points into the first copy.
    pdf = fake_summary()
    assert verdict(pdf + pdf) == DAMAGED


def test_an_index_entry_pointing_away_from_its_object_is_refused():
    # Even strict, pypdf searches the file for an object its index misplaces, and reads on.
    pdf = fake_summary()
    first = pdf.index(b" 00000 n", pdf.rindex(b"\nxref\n")) - 10  # the first object's 10-digit offset
    assert pdf[first:first + 10] == b"0000000009"
    damaged = pdf[:first + 8] + b"1" + pdf[first + 9:]  # its tens digit: 9 becomes 19
    assert verdict(damaged) == DAMAGED


def _index_entry(pdf: bytes, number: int) -> int:
    """Where object `number`'s 20-byte entry starts in the index the synthetic writer gives a file."""
    table = pdf.rindex(b"\nxref\n0 ") + len(b"\nxref\n")
    return pdf.index(b"\n", table) + 1 + 20 * number


def _recounted(pdf: bytes, extra: int) -> bytes:
    """`pdf` with its index's subsection header counting `extra` entries more than it holds."""
    table = pdf.rindex(b"\nxref\n0 ") + len(b"\nxref\n")
    end = pdf.index(b"\n", table)
    first, count = map(int, pdf[table:end].split())
    return pdf[:table] + b"%d %d" % (first, count + extra) + pdf[end:]


_MALFORMED_INDEXES = {  # each is given the file and where the first object's entry starts
    "a letter in an offset":
        lambda pdf, entry: pdf[:entry + 8] + b"O" + pdf[entry + 9:],  # "00000000O9 00000 n"
    "a short entry":
        lambda pdf, entry: pdf[:entry + 18] + pdf[entry + 19:],  # 19 bytes: no space before its line end
    "a subsection header whose count is wrong": lambda pdf, entry: _recounted(pdf, 1),
    "an entry neither in use (n) nor free (f)":
        lambda pdf, entry: pdf[:entry + 17] + b"o" + pdf[entry + 18:],  # "0000000009 00000 o"
}


@pytest.mark.parametrize("damage", _MALFORMED_INDEXES.values(), ids=_MALFORMED_INDEXES)
def test_an_index_that_is_not_strictly_well_formed_is_refused_though_pypdf_repairs_it(damage):
    # Strict pypdf repairs each of these and reads on: it looks for a garbled entry's object in
    # the file (6.21.4 then swaps its offset and generation, the only reason the first was ever
    # refused), reads a 19-byte entry as 20, rebuilds an index whose count is wrong from a search
    # of the whole file, and searches the file for an object whose entry it cannot use.
    pdf = fake_summary()
    assert pdf[_index_entry(pdf, 1):_index_entry(pdf, 2)] == b"0000000009 00000 n \n"
    assert verdict(damage(pdf, _index_entry(pdf, 1))) == DAMAGED


def _stream_updated(pdf: bytes, prev: bytes | None = None) -> bytes:
    """`pdf` plus an update whose index is a cross-reference stream listing only itself, /Prev the old section."""
    old = int(re.search(rb"startxref\n(\d+)\n%%EOF\n$", pdf)[1])
    trailer = re.search(rb"trailer\n<< (.*) >>\n", pdf[old:])[1]
    size = int(re.search(rb"/Size (\d+)", trailer)[1])
    rows = b"\x01" + len(pdf).to_bytes(4, "big") + b"\x00"  # in use, at this offset, generation 0
    newest = trailer.replace(b"/Size %d" % size, b"/Size %d" % (size + 1))
    head = b"<< /Type /XRef %s /Prev %s /W [1 4 1] /Index [%d 1] /Length %d >>" % (
        newest, prev or b"%d" % old, size, len(rows))
    return (pdf + b"%d 0 obj\n%s\nstream\n%s\nendstream\nendobj\n" % (size, head, rows)
            + b"startxref\n%d\n%%%%EOF\n" % len(pdf))


def test_a_classic_index_section_behind_a_cross_reference_stream_passes_when_well_formed():
    assert check_summary(_stream_updated(fake_summary())).missing == []


@pytest.mark.parametrize("damage", _MALFORMED_INDEXES.values(), ids=_MALFORMED_INDEXES)
def test_a_classic_index_section_behind_a_cross_reference_stream_is_read_as_strictly_as_any(damage):
    # Re-verification: the walk stopped at any object header, taking it for a cross-reference
    # stream, so a section chained behind one was left to pypdf's lenient reading.
    pdf = fake_summary()
    assert verdict(_stream_updated(damage(pdf, _index_entry(pdf, 1)))) == DAMAGED


@pytest.mark.parametrize("chained", [
    lambda pdf: _updated(pdf, prev=9),
    lambda pdf: re.sub(rb"startxref\n\d+\n%%EOF\n$", b"startxref\n9\n%%EOF\n", pdf),
    lambda pdf: _stream_updated(pdf, prev=b"9"),
    lambda pdf: _stream_updated(pdf, prev=b"%d" % len(pdf)),
    lambda pdf: _stream_updated(pdf, prev=b"1 0 R"),
    lambda pdf: _stream_updated(pdf).replace(b"/Type /XRef", b"/Type /XReg"),
], ids=["a trailer's /Prev leading to an ordinary object", "startxref leading to an ordinary object",
        "a cross-reference stream's /Prev leading to an ordinary object",
        "a cross-reference stream chained to itself", "a cross-reference stream's /Prev that is not an offset",
        "an object that is not quite a cross-reference stream"])
def test_an_index_chain_leading_anywhere_but_to_an_index_is_refused(chained):
    # A /Prev at an ordinary object: pypdf dropped the older section with a warning and searched
    # the file for its objects. (Object 1 of the synthetic writer starts at byte 9.)
    pdf = fake_summary()
    assert pdf[9:].startswith(b"1 0 obj\n")
    assert verdict(chained(pdf)) == DAMAGED


def _line_ends(pdf: bytes, entry_end: bytes, line_end: bytes) -> bytes:
    """`pdf` with its index and trailer rewritten: entries ending in `entry_end`, other lines `line_end`."""
    start = pdf.rindex(b"\nxref\n") + 1  # nothing before it moves, so startxref still points at it
    lines = pdf[start:].split(b"\n")[:-1]
    return pdf[:start] + b"".join(line[:-1] + entry_end if re.fullmatch(rb"\d{10} \d{5} [nf] ", line)
                                  else line + line_end for line in lines)


@pytest.mark.parametrize("entry_end, line_end", [(b" \n", b"\n"), (b" \r", b"\r"), (b"\r\n", b"\r\n")],
                         ids=["LF", "CR", "CR LF, as linearized renders are written"])
def test_an_index_may_end_its_lines_in_each_way_the_pdf_standard_allows(entry_end, line_end):
    assert check_summary(_line_ends(fake_summary(), entry_end, line_end)).missing == []


def _updated(pdf: bytes, prev: int | None = None) -> bytes:
    """`pdf` plus an update: a newer index section, listing object 1 again, whose /Prev is the old one."""
    old = int(re.search(rb"startxref\n(\d+)\n%%EOF\n$", pdf)[1])
    trailer = re.search(rb"trailer\n<< (.*) >>\n", pdf[old:])[1]
    entry = pdf[_index_entry(pdf, 1):_index_entry(pdf, 2)]
    return pdf + (b"xref\n1 1\n" + entry + b"trailer\n<< " + trailer + b" /Prev %d >>\n" % (prev or old)
                  + b"startxref\n%d\n%%%%EOF\n" % len(pdf))


def test_an_older_index_section_is_read_as_strictly_as_the_newest():
    # A linearized render has two index sections: the newest lists the first page's objects, and
    # its /Prev leads to the one listing the rest, which pypdf reads with the same leniency.
    updated = _updated(fake_summary())
    assert check_summary(updated).missing == []
    entry = _index_entry(updated, 2)  # in the older section
    assert verdict(updated[:entry + 17] + b"o" + updated[entry + 18:]) == DAMAGED


# ----- PDF 1.5: no real render uses these, but what the module notes say of them must hold


def _compressed(pdf: bytes, *, hybrid: bool = False) -> bytes:
    """
    `pdf` rewritten as PDF 1.5: its dictionaries in one object stream, listed by a cross-reference stream.

    hybrid: a classic section lists the rest, its trailer's /XRefStm leading to that stream, as the
    PDF standard allows so that older readers can read the file.
    """
    bodies = {int(m[1]): m[2] for m in re.finditer(rb"(\d+) 0 obj\n(.*?)\nendobj\n", pdf, re.S)}
    trailer = re.search(rb"trailer\n<< (.*) >>\n", pdf)[1]
    size = max(bodies) + 3
    holder, listing = size - 2, size - 1
    held = [number for number, body in bodies.items() if b"\nstream\n" not in body]
    first, inner = b"", b""
    for number in held:
        first += b"%d %d " % (number, len(inner))
        inner += bodies[number] + b"\n"
    first += b"\n"
    plain = {number: body for number, body in bodies.items() if number not in held}
    plain[holder] = (b"<< /Type /ObjStm /N %d /First %d /Length %d >>\nstream\n%s\nendstream"
                     % (len(held), len(first), len(first + inner), first + inner))
    out, offsets = bytearray(b"%PDF-1.5\n"), {}
    for number, body in sorted(plain.items()):
        offsets[number] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    at = offsets[listing] = len(out)
    keys = re.sub(rb"/Size \d+", b"/Size %d" % size, trailer)

    def row(number: int) -> bytes:
        if number in offsets:
            return b"\x01" + offsets[number].to_bytes(4, "big") + b"\x00"
        if number in held:
            return b"\x02" + holder.to_bytes(4, "big") + bytes([held.index(number)])
        return b"\x00\x00\x00\x00\x00\xff"  # free

    if not hybrid:
        rows = b"".join(row(number) for number in range(size))
        out += (b"%d 0 obj\n<< /Type /XRef %s /W [1 4 1] /Length %d >>\nstream\n%s\nendstream\nendobj\n"
                % (listing, keys, len(rows), rows) + b"startxref\n%d\n%%%%EOF\n" % at)
        return bytes(out)
    rows = b"".join(row(number) for number in held)
    index = b" ".join(b"%d 1" % number for number in held)
    out += (b"%d 0 obj\n<< /Type /XRef /Size %d /Index [%s] /W [1 4 1] /Length %d >>\n"
            b"stream\n%s\nendstream\nendobj\n" % (listing, size, index, len(rows), rows))
    section = len(out)
    out += b"xref\n0 1\n0000000000 65535 f \n" + b"".join(
        b"%d 1\n%010d 00000 n \n" % (number, offsets[number]) for number in sorted(offsets))
    return bytes(out + b"trailer\n<< %s /XRefStm %d >>\nstartxref\n%d\n%%%%EOF\n" % (keys, at, section))


@pytest.mark.parametrize("hybrid", [False, True], ids=["cross-reference stream only", "hybrid"])
def test_a_pdf_1_5_file_passes_and_has_the_fingerprint_of_its_classic_twin(hybrid):
    pdf = fake_summary(art=_Art(font_file="/FontFile2"))
    rewritten = _compressed(pdf, hybrid=hybrid)
    assert b"/ObjStm" in rewritten and check_summary(rewritten).missing == []
    assert content_fingerprint(rewritten) == content_fingerprint(pdf)


def test_a_cross_reference_stream_entry_pointing_away_from_its_object_is_refused():
    rewritten = _compressed(fake_summary())
    row = b"\x01\x00\x00\x00\x09\x00"  # in use, at byte 9: the first object in the file
    assert row in rewritten
    assert verdict(rewritten.replace(row, b"\x01\x00\x00\x00\x0a\x00", 1)) == DAMAGED


def test_what_an_object_stream_holds_is_checked_like_any_other_object():
    # The font dictionary, in the object stream, leads to a map that is no CMap.
    pdf = fake_summary(art=_Art(font_file="/FontFile2")).replace(b"begincmap", b"begincmaq", 1)
    assert verdict(_compressed(pdf)) == DAMAGED


def test_a_hybrid_file_that_lost_its_cross_reference_stream_is_refused():
    # pypdf reads on without a stream it cannot find, with only a warning, but no search finds the
    # objects that stream listed.
    rewritten = _compressed(fake_summary(), hybrid=True)
    assert rejected(rewritten.replace(b"/XRefStm", b"/XRefStn")) == ["unreadable"]


def test_an_update_holding_a_newer_copy_of_an_object_in_an_object_stream_passes():
    # It has no endobj of its own, and the older copy the classic section lists no longer counts.
    pdf = fake_summary()
    old = int(re.search(rb"startxref\n(\d+)\n%%EOF\n$", pdf)[1])
    keys = re.search(rb"trailer\n<< (.*) >>\n", pdf[old:])[1]
    size = int(re.search(rb"/Size (\d+)", keys)[1])
    info = _number(pdf, "Info")
    first, copy = b"%d 0 " % info, b"<< /Producer (newer) >>"
    holder = (b"%d 0 obj\n<< /Type /ObjStm /N 1 /First %d /Length %d >>\nstream\n%s\nendstream\nendobj\n"
              % (size, len(first), len(first + copy), first + copy))
    at = len(pdf) + len(holder)
    rows = (b"\x02" + size.to_bytes(4, "big") + b"\x00" + b"\x01" + len(pdf).to_bytes(4, "big") + b"\x00"
            + b"\x01" + at.to_bytes(4, "big") + b"\x00")
    newest = keys.replace(b"/Size %d" % size, b"/Size %d" % (size + 2))
    updated = pdf + holder + (
        b"%d 0 obj\n<< /Type /XRef %s /Prev %d /W [1 4 1] /Index [%d 1 %d 2] /Length %d >>\n"
        b"stream\n%s\nendstream\nendobj\n" % (size + 1, newest, old, info, size, len(rows), rows)
        + b"startxref\n%d\n%%%%EOF\n" % at)
    assert check_summary(updated).missing == []


# ----- objects the index does not list, which pypdf finds by searching the file


def _turned_free(pdf: bytes, number: int) -> bytes:
    """`pdf` with object `number`'s index entry marked free: one bit, "n" to "f"."""
    letter = _index_entry(pdf, number) + 17
    assert pdf[letter:letter + 1] == b"n"
    return pdf[:letter] + b"f" + pdf[letter + 1:]


_FONTED = fake_summary(filters=("/ASCII85Decode", "/FlateDecode"), art=_Art(font_file="/FontFile2"))


@pytest.mark.parametrize("key", ["Elev0w2", "Contents", "F1", "FontFile2", "ToUnicode", "Info", "Root"],
                         ids=["a drawing", "a page's text", "a font", "a font file", "a /ToUnicode map",
                              "the file's Info", "the catalog"])
def test_an_index_entry_turned_free_is_refused_though_pypdf_finds_the_object_by_searching(key):
    # Re-verification: this one bit passed on every archived render. Even strict, pypdf searches
    # the file for an object its index does not list, and the PDF standard reads it as null.
    assert check_summary(_FONTED).missing == []
    assert verdict(_turned_free(_FONTED, _number(_FONTED, key))) == DAMAGED


def test_an_object_that_only_a_reference_leads_to_counts_too():
    # Nothing here or in pypdf reads a file's XML metadata, so only following the reference shows
    # that its entry was turned free.
    pdf = fake_summary(art=_Art(metadata=True))
    assert check_summary(pdf).missing == []
    assert verdict(_turned_free(pdf, _number(pdf, "Metadata"))) == DAMAGED


def test_a_reference_leading_nowhere_is_refused():
    # Every reference in every real render leads to an object; strict pypdf cannot read one that
    # does not. (A digit flipped in a reference nothing reads would otherwise go unseen.)
    pdf = fake_summary(art=_Art(metadata=True))
    reference = b"/Metadata %d 0 R" % _number(pdf, "Metadata")
    assert rejected(pdf.replace(reference, b"/Metadata 99 0 R".ljust(len(reference)))) == ["unreadable"]


def test_damage_in_a_drawing_the_index_hides_is_refused():
    # Two faults. A bit in the drawing's data alone fails its checksum, but pypdf found the object
    # by searching only while it read the pages, after every checksum had been checked.
    pdf = fake_summary(filters=("/ASCII85Decode", "/FlateDecode"))
    number = _number(pdf, "Elev0w2")
    damaged = _flipped(_turned_free(pdf, number), b"E\x80\xc0\xff", b"E\x81\xc0\xff")
    assert len(damaged) == len(pdf) and damaged != pdf
    assert verdict(damaged) == DAMAGED


def test_a_renamed_prev_hiding_an_older_index_section_is_refused():
    # One bit, on the /Prev of a linearized render, passed: pypdf found every object of the section
    # it no longer knew about by searching the file.
    updated = _updated(fake_summary())
    assert check_summary(updated).missing == []
    assert verdict(updated.replace(b"/Prev", b"/Prew")) == DAMAGED


def test_a_pdf_with_no_pages_is_rejected():
    assert rejected(_write_pdf([], {"Producer": "test"})) == ["no_pages"]


# ----- content_fingerprint


def test_fingerprint_ignores_bytes_and_metadata():
    first = fake_summary(created="D:20260914161908-07'00'")
    again = fake_summary(created="D:20260922195158-07'00'")
    assert first != again
    assert content_fingerprint(first) == content_fingerprint(again)


def test_fingerprint_changes_when_a_wall_changes():
    before = fake_summary(designs=(FakeDesign(walls=('1: 60" X 96"', '2: 72 1/2" X 96"')),))
    after = fake_summary(designs=(FakeDesign(walls=('1: 60" X 96"', '2: 73" X 96"')),))
    assert content_fingerprint(before) != content_fingerprint(after)


def test_fingerprint_changes_when_studio_renames_the_project():
    assert (content_fingerprint(fake_summary(project_name="Testerson_101426"))
            != content_fingerprint(fake_summary(project_name="Testerson_Rebuild_101426")))


def test_a_partial_render_complete_in_itself_passes_but_its_fingerprint_differs():
    # Risk K8. An unfinished render can number its own pages and hold only whole designs, so the
    # check passes it; the Fetch gate's two fetches 10 minutes apart catch it by their fingerprints.
    garage = FakeDesign(name="Testerson - Garage", category="Garage")
    early, finished = fake_summary(designs=(FakeDesign(),)), fake_summary(designs=(FakeDesign(), garage))
    assert check_summary(early).ok and check_summary(finished).ok
    assert content_fingerprint(early) != content_fingerprint(finished)


def test_fingerprint_changes_when_a_drawing_changes():
    # A shelf moved shows in the drawing alone: two fetches must not agree over it. (No filters
    # here, so a changed pixel fails no checksum and the render still passes the check.)
    pdf = fake_summary()
    moved = pdf.replace(b"E\x80\xc0\xff", b"F\x80\xc0\xff", 1)
    assert moved != pdf and check_summary(moved).missing == []
    assert content_fingerprint(moved) != content_fingerprint(pdf)


def test_fingerprint_changes_when_a_mask_changes():
    pdf = fake_summary(art=_Art(mask=True))
    changed = pdf.replace(_MASK_PIXELS, b"N\x80\xc0\xff", 1)
    assert changed != pdf and check_summary(changed).missing == []
    assert content_fingerprint(changed) != content_fingerprint(pdf)


def test_fingerprint_counts_where_and_in_what_order_pictures_are_drawn():
    def pages(*drawn: list[str]) -> bytes:
        return _write_pdf([_FakePage(612, 792, images=[(name, 72, 72, 200, 200) for name in names])
                           for names in drawn], {"Producer": "test"})

    first = content_fingerprint(pages(["Aye", "Bee"], []))
    assert first == content_fingerprint(pages(["Aye", "Bee"], []))  # the same again, so the rest are not noise
    assert first != content_fingerprint(pages(["Bee", "Aye"], []))
    assert first != content_fingerprint(pages(["Aye"], ["Bee"]))
    assert first != content_fingerprint(pages(["Aye", "Bee", "Bee"], []))


def test_fingerprint_counts_what_a_picture_decodes_to_not_how_it_is_stored():
    art = _Art(pixels=b"\x10\x20\x30\x40")
    plain = content_fingerprint(fake_summary(art=art))
    assert content_fingerprint(fake_summary(filters=("/FlateDecode",), art=art)) == plain
    assert content_fingerprint(fake_summary(filters=("/ASCII85Decode", "/FlateDecode"), art=art)) == plain
    assert content_fingerprint(fake_summary(filters=("/FlateDecode",), art=_PREDICTED)) == plain


def test_fingerprint_sees_a_picture_in_its_own_image_format_as_stored():
    pdf = fake_summary(filters=("/ASCII85Decode", "/FlateDecode"))
    coded = _in_dictionary(pdf, "Elev0w2", b"/ASCII85Decode /FlateDecode", b"/DCTDecode")
    changed = _flipped(coded, b"E\x80\xc0\xff", b"E\x81\xc0\xff")
    assert changed != coded and check_summary(changed).missing == []
    assert content_fingerprint(changed) != content_fingerprint(coded)


def test_a_damaged_drawing_has_no_fingerprint_to_agree_on():
    pdf = fake_summary(filters=("/ASCII85Decode", "/FlateDecode"))
    with pytest.raises(ValueError):
        content_fingerprint(_flipped(pdf, b"E\x80\xc0\xff", b"E\x81\xc0\xff"))


@pytest.mark.parametrize("damage", [
    lambda pdf: re.sub(rb"/Elev0w2 (\d+ 0 R)", rb"/Elev0w3 \1", pdf, count=1),
    lambda pdf: _in_dictionary(pdf, "Elev0w2", b"/Subtype /Image", b"/Subtype /Imagf"),
    lambda pdf: _in_dictionary(pdf, "Elev0w2", b"/Subtype", b"/Subtypf"),
    lambda pdf: _in_dictionary(pdf, "Elev0w2", b"/Subtype /Image", b"/Subtype /Form"),
], ids=["a name its page does not hold", "a damaged kind", "no kind",
        "a form, whose insides nothing here reads"])
def test_a_page_that_draws_anything_but_a_picture_it_holds_is_refused(damage):
    # Re-verification: one bit renaming the picture in the page's resources passed on every archived
    # render, and the drawing check still counted a drawing. Every real Do draws a picture its page holds.
    pdf = fake_summary()
    assert verdict(damage(pdf)) == DAMAGED


def test_check_reports_the_same_fingerprint():
    pdf = fake_summary(geometry=LANDSCAPE)
    assert check_summary(pdf).details["fingerprint"] == content_fingerprint(pdf)


def test_fingerprint_of_something_that_is_not_a_pdf_is_refused():
    with pytest.raises(ValueError):
        content_fingerprint(b"<html>Not found</html>")


# ----- details: the facts a Render record keeps (design 3.3)


def test_details_carry_the_render_facts_as_plain_data():
    pdf = fake_summary(geometry=LANDSCAPE, created="D:20261014120705-07'00'",
                       designs=(FakeDesign(elevations=("1", "2"), without_drawing=("2",)),))
    details = check_summary(pdf).details
    assert json.loads(json.dumps(details)) == details
    assert details["sha256"] == hashlib.sha256(pdf).hexdigest()
    assert details["creation_date"] == "D:20261014120705-07'00'"
    assert details["orientation"] == "landscape"
    assert details["page_numbers"] == "match"
    assert details["designs"] == [{
        "name": "Testerson - Primary Closet", "category": "His and Her Master", "wall_area_pages": [2],
        "walls": [{"name": "1", "width": '60"', "height": '96"'},
                  {"name": "2", "width": '72 1/2"', "height": '96"'},
                  {"name": "3", "width": '60"', "height": '96"'}],
        "elevations": [{"page": 3, "wall": "1", "drawing": True},
                       {"page": 4, "wall": "2", "drawing": False}]}]
    assert details["pulled"] == {"pages": [6], "item_lines": 3}


def test_unreadable_file_details_still_identify_the_bytes():
    details = check_summary(b"<html>Not found</html>").details
    assert details["sha256"] == hashlib.sha256(b"<html>Not found</html>").hexdigest()


def test_details_name_the_pypdf_that_read_the_text():
    # The fingerprint is only as stable as pypdf's text extraction: a stored one is recomputed
    # from its file when this differs from the pypdf installed now.
    installed = importlib.metadata.version("pypdf")
    assert check_summary(fake_summary()).details["pypdf"] == installed
    assert check_summary(b"<html>Not found</html>").details["pypdf"] == installed


# ----- real summaries (.local-fixtures): expectations derived at runtime, files named by index

# Codes that mean the file was never read far enough to judge its content.
_READ_FAILURES = {"not_pdf", "cut_off", "unreadable", "no_pages"}
# Codes that name content an incomplete render lacks.
_CONTENT_GAPS = {"no_designs", "no_wall_area", "empty_wall_area", "no_elevations", "no_drawing",
                 "no_pulled", "empty_pulled"}


@dataclass(frozen=True)
class _Real:
    label: str  # "n03" = 4th file of ngjobs/, "d25" = 26th of downloads/, sorted by name
    folder: str
    path: Path
    sha256: str
    text_sha256: str  # pypdf's plain text of every page: an independent "same content" test
    landscape: bool
    pages: int
    result: SummaryCheck

    @property
    def archived_as(self) -> str:
        """The job name Jim filed it under (`<Job>[_Gar]_summary.pdf`; _Gar is a folder suffix)."""
        return self.path.stem.removesuffix("_summary").removesuffix("_Gar")


@pytest.fixture(scope="module")
def real_summaries(summaries_dir: Path) -> list[_Real]:
    found = []
    for folder in ("ngjobs", "downloads"):
        paths = sorted((summaries_dir / folder).glob("*.pdf"), key=lambda p: p.name.lower())
        for i, path in enumerate(paths):
            data = path.read_bytes()
            reader = PdfReader(io.BytesIO(data))
            text = "\f".join(page.extract_text() for page in reader.pages)
            box = reader.pages[0].mediabox
            found.append(_Real(f"{folder[0]}{i:02d}", folder, path, hashlib.sha256(data).hexdigest(),
                               hashlib.sha256(text.encode("utf-8")).hexdigest(), box.width > box.height,
                               len(reader.pages), check_summary(path)))
    return found


@pytest.mark.fixtures
def test_every_archived_summary_passes(real_summaries: list[_Real]):
    archived = [r for r in real_summaries if r.folder == "ngjobs"]
    assert archived
    assert [(r.label, r.result.details["codes"]) for r in archived if not r.result.ok] == []
    assert all(r.result.designs and r.result.pulled_layout in ("A", "B") for r in archived)
    misread = [r.label for r in archived if r.result.project_name != r.archived_as]
    assert misread == [], "PROJECT NAME read differently from the name the job was archived under"


@pytest.mark.fixtures
def test_the_whole_file_checks_refuse_no_real_render(real_summaries: list[_Real]):
    # Strict reading, the damage checks (all "unreadable") and required page numbers each refuse
    # what lax reading let through; none may refuse what Studio really sends.
    refusals = _READ_FAILURES | {"no_page_numbers"}
    refused = [(r.label, sorted(set(r.result.details["codes"]) & refusals)) for r in real_summaries]
    assert [(label, codes) for label, codes in refused if codes] == []


def _smallest_of_each_layout(real_summaries: list[_Real]) -> list[_Real]:
    """The smallest archived render of each layout, so that flipping bits all through one takes little time."""
    archived = [r for r in real_summaries if r.folder == "ngjobs"]
    layouts = [[r for r in archived if r.landscape is landscape] for landscape in (True, False)]
    return [min(renders, key=lambda r: r.path.stat().st_size) for renders in layouts if renders]


def _flipped_bit(data: bytes, position: int, bit: int = 0x01) -> bytes:
    damaged = bytearray(data)
    damaged[position] ^= bit
    return bytes(damaged)


@pytest.mark.fixtures
def test_a_bit_flipped_in_any_compressed_stream_of_a_real_render_is_refused(real_summaries: list[_Real]):
    # The re-verification flipped bits in real renders' text streams, and 17 of 628 passed with
    # other text; pictures and fonts never reach the text at all. Here: the smallest archived
    # render of each layout, one bit in the middle of every compressed stream it holds.
    flipped = 0
    for render in _smallest_of_each_layout(real_summaries):
        data = render.path.read_bytes()
        reader = PdfReader(io.BytesIO(data))
        for number in sorted(reader.xref[0]):
            stream = reader.get_object(number)
            if isinstance(stream, StreamObject) and "/FlateDecode" in str(stream.get("/Filter")):
                stored = stream._data  # the bytes as the file holds them
                assert verdict(_flipped_bit(data, data.index(stored) + len(stored) // 2)) == DAMAGED, (
                    render.label, number)
                flipped += 1
    assert flipped


@pytest.mark.fixtures
def test_a_bit_flipped_in_the_stream_keyword_of_a_real_render_is_refused(real_summaries: list[_Real]):
    # Re-verification: every bit of every letter passed for all 233 pictures and 54 font files of
    # the archive. Here: one letter of every stream of the smallest render of each layout.
    flipped = 0
    for render in _smallest_of_each_layout(real_summaries):
        data = render.path.read_bytes()
        reader = PdfReader(io.BytesIO(data))
        for number, offset in sorted(reader.xref[0].items()):
            if isinstance(reader.get_object(number), StreamObject):
                damaged = _flipped_bit(data, data.index(b"stream", offset) + 2)
                assert verdict(damaged) == DAMAGED, (render.label, number)
                flipped += 1
    assert flipped


@pytest.mark.fixtures
def test_a_renamed_filter_key_on_a_real_picture_font_file_or_map_is_refused(real_summaries: list[_Real]):
    # Re-verification: 17,187 such flips passed on the archive, for every picture and font file and
    # for most maps. (A page's content stream shows it in the text it loses.) Pictures and fonts
    # are told by their own keys, maps by the fonts that use them.
    flipped = 0
    for render in _smallest_of_each_layout(real_summaries):
        data = render.path.read_bytes()
        reader = PdfReader(io.BytesIO(data))
        objects = {number: reader.get_object(number) for number in reader.xref[0]}
        maps = {obj.raw_get("/ToUnicode").idnum for obj in objects.values()
                if isinstance(obj, DictionaryObject) and "/ToUnicode" in obj}
        for number, offset in sorted(reader.xref[0].items()):
            obj = objects[number]
            if number in maps or (isinstance(obj, StreamObject) and ("/Subtype" in obj or "/Length1" in obj)):
                damaged = _flipped_bit(data, data.index(b"/Filter", offset) + 6)
                assert verdict(damaged) == DAMAGED, (render.label, number)
                flipped += 1
    assert flipped


@pytest.mark.fixtures
def test_a_renamed_picture_in_the_resources_of_a_real_page_is_refused(real_summaries: list[_Real]):
    # Re-verification: this passed on all 27 archived renders, and the page still counted as drawn.
    flipped = 0
    for render in _smallest_of_each_layout(real_summaries):
        data = render.path.read_bytes()
        for found in re.finditer(rb"/XObject\s*<<\s*/\S*(\S)\s+\d+ 0 R", data):
            assert verdict(_flipped_bit(data, found.start(1))) == DAMAGED, (render.label, found.start(1))
            flipped += 1
    assert flipped


@pytest.mark.fixtures
def test_a_changed_digit_in_a_real_picture_or_font_file_dictionary_is_refused(real_summaries: list[_Real]):
    flipped = 0
    for render in _smallest_of_each_layout(real_summaries):
        data = render.path.read_bytes()
        for found in re.finditer(rb"/(?:Width|Height|BitsPerComponent|Length1)\s+\d*(\d)(?=[\s/>])", data):
            assert verdict(_flipped_bit(data, found.start(1))) == DAMAGED, (render.label, found.start(1))
            flipped += 1
    assert flipped


@pytest.mark.fixtures
def test_an_index_entry_of_a_real_render_turned_free_is_refused_when_anything_refers_to_its_object(
        real_summaries: list[_Real]):
    # Re-verification: for a picture, a page's text or a font file this passed on all 27 archived
    # renders, and then a flipped bit in its data passed too. Every entry of the smallest render
    # of each layout; a linearized render's first dictionary and hint stream are referred to by nothing.
    flipped = 0
    for render in _smallest_of_each_layout(real_summaries):
        data = render.path.read_bytes()
        table = re.search(rb"(?<![a-z])xref\r?\n(\d+) (\d+)\r?\n", data)
        if b"/Linearized" in data or int(table[1]) != 0:
            continue
        for number in range(1, int(table[2])):
            letter = table.end() + 20 * number + 17
            assert data[letter:letter + 1] == b"n", (render.label, number)
            assert verdict(_flipped_bit(data, letter, 0x08))[0] == ["unreadable"], (render.label, number)
            flipped += 1
    assert flipped


@pytest.mark.fixtures
def test_a_renamed_prev_in_a_real_render_is_refused(real_summaries: list[_Real]):
    # Re-verification: on a linearized render this hid the section listing all but the first page,
    # and with it a flipped bit in any picture that section lists.
    chained = [r for r in real_summaries if r.path.read_bytes().count(b"startxref") > 1]
    if not chained:
        pytest.skip("no real render has an index section that only /Prev leads to")
    for render in chained:
        data = render.path.read_bytes()
        assert verdict(_flipped_bit(data, data.index(b"/Prev") + 4)) == DAMAGED, render.label


@pytest.mark.fixtures
def test_a_garbled_entry_in_an_index_section_only_prev_leads_to_is_refused(real_summaries: list[_Real]):
    # A linearized render's startxref leads to the first page's index section, and its /Prev to
    # the section listing everything else. pypdf finds the object of an entry it cannot use by
    # searching the file, so the check reads every section itself.
    chained = 0
    for render in real_summaries:
        data = render.path.read_bytes()
        newest = int(re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", data)[1])
        for older in re.finditer(rb"(?<![a-z])xref\r?\n\d+ \d+\r?\n", data):  # not startxref
            if older.start() != newest:
                damaged = bytearray(data)
                damaged[older.end() + 20 + 17] = ord("o")  # its second entry: neither n nor f
                assert verdict(bytes(damaged)) == DAMAGED, render.label
                chained += 1
    if not chained:
        pytest.skip("no real render has an index section that only /Prev leads to")


_FIRST_FILTER = re.compile(rb"/Filter\s*\[?\s*/[A-Za-z0-9]+")  # up to the end of the first filter's name


@pytest.mark.fixtures
def test_a_bit_flipped_in_a_picture_filter_name_of_a_real_render_is_refused(real_summaries: list[_Real]):
    # Re-verification: this passed on all 27 archived renders, the first picture of each. Here:
    # the smallest archived render of each layout, the last letter of every picture's first filter.
    flipped = 0
    for render in _smallest_of_each_layout(real_summaries):
        data = render.path.read_bytes()
        reader = PdfReader(io.BytesIO(data))
        for number, offset in sorted(reader.xref[0].items()):
            picture = reader.get_object(number)
            if isinstance(picture, StreamObject) and picture.get("/Subtype") == "/Image":
                name = _FIRST_FILTER.search(data, offset, data.index(b"stream", offset)).end() - 1
                # "/FlateDecode" becomes "/FlateDecodd"
                assert verdict(_flipped_bit(data, name)) == DAMAGED, (render.label, number)
                flipped += 1
    assert flipped


@pytest.mark.fixtures
def test_downloads_shorter_than_their_archived_render_are_rejected_with_a_reason(real_summaries: list[_Real]):
    # Pair each download with the archived render of the same job (same PROJECT NAME, same page
    # orientation, which changes the page count): fewer pages than the archive = an early render.
    archived = {(r.result.project_name.casefold(), r.landscape): r
                for r in real_summaries if r.folder == "ngjobs" and r.result.project_name}
    early = [(d, archived[key]) for d in real_summaries
             if d.folder == "downloads" and d.result.project_name
             and (key := (d.result.project_name.casefold(), d.landscape)) in archived
             and d.pages < archived[key].pages]
    if not early:
        pytest.skip("no download is shorter than the archived render of its job")
    for download, final in early:
        assert final.result.ok, final.label
        codes = set(download.result.details["codes"])
        assert not download.result.ok, f"{download.label} passed with fewer pages than {final.label}"
        assert codes & _CONTENT_GAPS and not codes & _READ_FAILURES, (download.label, sorted(codes))


@pytest.mark.fixtures
def test_fingerprint_matches_between_renders_that_differ_only_in_bytes(real_summaries: list[_Real]):
    by_text: dict[str, list[_Real]] = defaultdict(list)
    for r in real_summaries:
        by_text[r.text_sha256].append(r)
    pairs = [(a, b) for same in by_text.values() for a, b in combinations(same, 2) if a.sha256 != b.sha256]
    if not pairs:
        pytest.skip("no two byte-different renders with the same text among the fixtures")
    for a, b in pairs:
        assert content_fingerprint(a.path) == content_fingerprint(b.path), (a.label, b.label)


@pytest.mark.fixtures
def test_fingerprint_differs_between_renders_of_a_job_whose_text_differs(real_summaries: list[_Real]):
    by_job: dict[str, list[_Real]] = defaultdict(list)
    for r in real_summaries:
        if r.result.project_name:
            by_job[r.result.project_name.casefold()].append(r)
    pairs = [(a, b) for same in by_job.values() for a, b in combinations(same, 2)
             if a.text_sha256 != b.text_sha256]
    if not pairs:
        pytest.skip("no job has two renders with different text among the fixtures")
    for a, b in pairs:
        assert content_fingerprint(a.path) != content_fingerprint(b.path), (a.label, b.label)


@pytest.mark.fixtures
def test_fingerprint_reads_a_path_and_its_bytes_alike(real_summaries: list[_Real]):
    first = real_summaries[0]
    assert content_fingerprint(first.path.read_bytes()) == first.result.details["fingerprint"]


# ----- WALL/AREA NAME (W X H) entries


def test_wall_area_entry_with_whole_and_fractional_inches():
    assert parse_wall_area('1: 49" X 95 1/2"') == ("1", '49"', '95 1/2"')
    assert parse_wall_area('10: 100 15/16" X 96"') == ("10", '100 15/16"', '96"')


def test_wall_area_entry_with_a_named_wall_and_decimal_inches():
    assert parse_wall_area('Back wall: 72.5" X 108"') == ("Back wall", '72.5"', '108"')


def test_text_that_is_not_a_wall_area_entry():
    assert parse_wall_area("Exampleville, CA 90000-0000") is None
    assert parse_wall_area("Shelf: Arctic White") is None
    assert parse_wall_area('1: 49" by 95"') is None


# ----- page 1 design list


def test_design_list_one_design_per_line():
    designs, left_over = split_design_list(
        ["Testerson - Primary Closet - (His and Her Master)", "Testerson - Garage - (Garage)"])
    assert designs == [("Testerson - Primary Closet", "His and Her Master"),
                       ("Testerson - Garage", "Garage")]
    assert left_over == ""


def test_design_list_joins_a_category_wrapped_onto_the_next_line():
    designs, left_over = split_design_list(
        ["Testerson - Linen closet - (His and Her", "Master)", "Testerson - Pantry - (Pantry)"])
    assert designs == [("Testerson - Linen closet", "His and Her Master"),
                       ("Testerson - Pantry", "Pantry")]
    assert left_over == ""


def test_design_list_joins_a_name_that_wraps_before_its_category():
    designs, _ = split_design_list(["Testerson - TV unit - partial backing -", "(Entertainment)"])
    assert designs == [("Testerson - TV unit - partial backing", "Entertainment")]


def test_design_list_keeps_brackets_that_belong_to_the_name():
    designs, _ = split_design_list(
        ["TESTERSON - Closet - EGGER", "(Beige Textile) - LIGHTING - (Women's", "Closet)"])
    assert designs == [("TESTERSON - Closet - EGGER (Beige Textile) - LIGHTING", "Women's Closet")]


def test_design_list_text_that_never_closes_is_left_over_not_guessed():
    designs, left_over = split_design_list(["Testerson - Office - (Office)", "(555) 010-0199"])
    assert designs == [("Testerson - Office", "Office")]
    assert left_over == "(555) 010-0199"


def test_design_list_that_is_empty():
    assert split_design_list([]) == ([], "")


# ----- PULLED header layouts (BUILD.md Step 1)

PULLED_A = ["PULLED", "QTY", "ITEM #", "DESCRIPTION", "DIMENSIONS"] * 2
PULLED_B = ["PULLED", "ITEM #", "QUANTITY", "DESCRIPTION", "DIMENSIONS"]


def test_pulled_header_with_two_lists_quantity_first_is_layout_a():
    assert pulled_layout_of(PULLED_A) == "A"


def test_pulled_header_with_one_list_item_first_is_layout_b():
    assert pulled_layout_of(PULLED_B) == "B"


def test_pulled_header_in_any_other_shape_is_not_guessed():
    assert pulled_layout_of(["PULLED", "QTY", "ITEM #", "DESCRIPTION"]) is None  # A fields, one list
    assert pulled_layout_of(PULLED_B * 2) is None  # B fields, two lists
    assert pulled_layout_of(["PULLED", "DESCRIPTION", "QTY", "ITEM #"]) is None
    assert pulled_layout_of(["QTY", "ITEM #", "PULLED"]) is None
    assert pulled_layout_of([]) is None
