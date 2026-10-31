"""
A client's details never reach a takeoff (docs/p2/build-spec.md, sections 4 and 6.4).

A made-up summary carries a made-up address, email and phone in its contact block, on its
Estimate page and in every description, and the whole path (convert) runs on its bytes. None of
it may reach the Takeoff, its repr, its fields as a store row would hold them, or the could-not-
verify lines a message would carry. The PDF is written here, in memory: no real file is read.
"""
from __future__ import annotations

import dataclasses
import json

from ngrunner.convert import convert

ADDRESS, TOWN, EMAIL, PHONE = "12 Fakeview Lane", "Testville, CA 90000", "tester@example.com", \
    "(555) 010-0000"
CONTACT = f"{ADDRESS} {TOWN} {EMAIL} {PHONE}"
NEVER = ("Fakeview", "Testville", "90000", EMAIL, "555", "010-0000", "Testerson")
FOOTER = ["Example Closets", " 123 Example Ave", "Somewhere, CA 90001-0000",
          "(555) 010-0001 | www.example.com"]
HEADER = ["ITEM #", "QUANTITY", "DESCRIPTION", "DIMENSIONS"]
ROWS = [  # item, qty, description: every one carries the client's details
    ("CTO", None, None),
    ("VT-WH", 2, f"VT 14x84x3/4 FD WH {CONTACT}"),
    ("SH-WH", 4, f"SH 14x24x3/4 FIX WH {CONTACT}"),
    ("CK", 2, f"CK DBack 3.6x22.2x0.6 NN WH {CONTACT}"),
    ("Hardware", None, None),
    ("400000570", 1, f"White Rail Cover {CONTACT}"),
    ("400000155", 8, f"Shelf Pin {CONTACT}"),
]


def _rows() -> list[str]:
    out: list[str] = []
    for item, qty, desc in ROWS:
        out += [item] if qty is None else [item, str(qty), str(desc)]
    return out


def _pages() -> list[list[str]]:
    cover = ["PROJECT NAME", "Testerson_101426", "Testerson Family", ADDRESS, TOWN, EMAIL, PHONE,
             "DESIGNS IN PROJECT", "Testerson - Primary Closet - (Closet)"]
    design = ["Design: Testerson - Primary Closet", " Project: Testerson_101426", "Parts List",
              *HEADER, *_rows(), *FOOTER]
    estimate = ["Testerson Family", ADDRESS, TOWN, EMAIL, PHONE, "Estimate",
                " Project: Testerson_101426", "DESIGN", "TOTAL", "Testerson - Primary Closet",
                "$1,234.56", "Total", "$1,234.56", "Deposit (50.00%)", "$617.28", *FOOTER]
    pulled = ["Project Parts List", " Project: Testerson_101426", "PULLED", *HEADER, *_rows(),
              *FOOTER]
    pages = [cover, design, estimate, pulled]
    return [page + [f"Page {n} of {len(pages)}"] for n, page in enumerate(pages, 1)]


def _pdf(pages: list[list[str]]) -> bytes:
    """One PDF, one line of text per row, top to bottom: as pypdf reads a real portrait page."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    tree = add(b"")
    kids = []
    for lines in pages:
        ops = []
        for k, text in enumerate(lines):
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"BT /F1 9 Tf 1 0 0 1 40 {770 - 12 * k} Tm ({escaped}) Tj ET")
        data = "\n".join(ops).encode("cp1252")
        contents = add(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
        kids.append(add((f"<< /Type /Page /Parent {tree} 0 R /MediaBox [0 0 612 792]"
                         f" /Resources << /Font << /F1 {font} 0 R >> >> /Contents {contents} 0 R >>"
                         ).encode()))
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


def test_the_made_up_summary_converts() -> None:
    t = convert(_pdf(_pages()), branch="closet")
    assert (t.board_parts, len(t.unplaced), t.estimate_total) == (7, 1, 1234.56)
    assert [(b.colour, b.thk_in, b.parts) for b in t.buckets] == [("WH", 0.75, 7)]
    assert t.gate_pulled_equals_designs


def test_no_client_detail_reaches_the_takeoff_its_row_or_its_lines() -> None:
    t = convert(_pdf(_pages()), branch="closet")
    seen = " ".join([repr(t), json.dumps(dataclasses.asdict(t)), *t.could_not_verify])
    assert [word for word in NEVER if word in seen] == []


def test_nor_on_a_garage_job_where_every_row_is_unplaced() -> None:
    t = convert(_pdf(_pages()), branch="garage")
    seen = " ".join([repr(t), json.dumps(dataclasses.asdict(t)), *t.could_not_verify])
    assert len(t.unplaced) == 4          # VT, SH, CK and the rail cover; shelf pins are no board
    assert [word for word in NEVER if word in seen] == []
