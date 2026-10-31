"""
The IC summary's lines (ngrunner/convert/summary_lines.py): the PULLED list, the design lists and
the estimate total, read from pypdf's text (docs/p2/build-spec.md, sections 3.1, 5.1 and 6.1).

Synthetic pages are written the way pypdf gives the real ones: one cell per line, the portrait
(item first) and landscape (quantity first, two lists side by side) layouts. Real summaries are
read only under the fixtures mark, by index, and nothing here prints a real name.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ngrunner.convert.summary_lines import (SummaryLine, SummaryUnreadable, design_lines,
                                            estimate_total, layout, pulled_lines, read_pages)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(os.environ.get("NGR_FIXTURES", ROOT / ".local-fixtures"))
PAIRS = FIXTURES / "p2" / "pairs"

COVER = "PROJECT NAME\nTesterson_101426\nDESIGNS IN PROJECT\nTesterson - Primary Closet - (Closet)\n"
# The store's footer, made up: its real address and phone never enter the repo. The reader finds it
# by shape (a street, a city with state and ZIP, a phone, a web address, the page number).
FOOTER_PORTRAIT = ("Example Closets\n 123 Example Ave\nTestville, CA 90000-0000\n"
                   "(555) 010-0000 | www.example.com\nPage {n} of 9\n")
FOOTER_LANDSCAPE = ("Example Closets |  123 Example Ave Testville, CA 90000-0000 | (555) 010-0000"
                    " www.example.com\nPage {n} of 9\n")
HEAD_PORTRAIT = "PULLED\nITEM #\nQUANTITY\nDESCRIPTION\nDIMENSIONS\n"
HEAD_LANDSCAPE = ("PULLED\nQTY\nITEM #\nDESCRIPTION\nDIMENSIONS\n"
                  "PULLED\nQTY\nITEM #\nDESCRIPTION\nDIMENSIONS\n")

PORTRAIT = ("Project Parts List\n Project: Testerson_101426\n" + HEAD_PORTRAIT
            + "CTO\nVT-WH\n1\nVT 14x76.4x3/4 FD WH\nSH-WH\n4\nSH 17.8x19.1x3/4 ADJ WH\n"
            + "Hardware\n400000464\n1\nRod Round 96in Chrome\n39 3/4\"W x 1\"H\n"
            + "400000133\n8\nScrew Drawer Adjusting #8 x 1-1/8\" Zinc\n(1000 pk)\n"
            + FOOTER_PORTRAIT.format(n=7))

LANDSCAPE = ("Project Parts List\n Project: Testerson_101426\n" + HEAD_LANDSCAPE
             + "CTO\n1\nDR-WH\nDR 15.2x31.4x3/4 FL WH\n1\nBP-WH\nBP 25.8x54.6x3/8 WH\n"
             + "9\nTR-WH\nTR Base 4.1x96x3/4 FL WH\n"
             + "Components\n2\nSH-WH\nSH 23.9x29.9x3/4 FIX WH\n1\nCK-WH\nCK Kick 2.5x23.9x3/4 DFC WH\n"
             + "Hardware\n68\n400000151\nScrew PFH #6 x 5/8 Zinc (1000 pk)\n0.0\"H x 0.0\"W\n"
             + FOOTER_LANDSCAPE.format(n=8))


def _row(line: SummaryLine) -> tuple[str, str, int, str, int, int]:
    return (line.section, line.item, line.qty, line.desc, line.page, line.column)


# ----- the layout


def test_the_layout_comes_from_the_order_after_pulled() -> None:
    assert layout(PORTRAIT) == "item_first"
    assert layout(LANDSCAPE) == "qty_first"


@pytest.mark.parametrize("header", [
    "PULLED\nQTY\nDESCRIPTION\nITEM #\nDIMENSIONS\n",       # fields in an order never seen
    "PULLED\nITEM #\nQTY\nDESCRIPTION\nDIMENSIONS\n",       # QTY with the item first
    "PULLED\nQUANTITY\nITEM #\nDESCRIPTION\nDIMENSIONS\n",  # QUANTITY with the quantity first
])
def test_an_unknown_header_raises_in_plain_words(header: str) -> None:
    page = "Project Parts List\n" + header + "CTO\n1\nVT-WH\nVT 14x76.4x3/4 FD WH\n"
    with pytest.raises(SummaryUnreadable) as raised:
        layout(page)
    assert "parts list" in str(raised.value).casefold()
    assert "Testerson" not in str(raised.value)


# ----- the PULLED list


def test_a_portrait_page_reads_item_first_with_its_sections() -> None:
    lines = pulled_lines(["cover", PORTRAIT])
    assert [_row(line) for line in lines] == [
        ("CTO", "VT-WH", 1, "VT 14x76.4x3/4 FD WH", 1, 0),
        ("CTO", "SH-WH", 4, "SH 17.8x19.1x3/4 ADJ WH", 1, 0),
        ("Hardware", "400000464", 1, "Rod Round 96in Chrome", 1, 0),
        ("Hardware", "400000133", 8, 'Screw Drawer Adjusting #8 x 1-1/8" Zinc (1000 pk)', 1, 0),
    ]


def test_a_dimension_line_is_kept_apart_from_the_description() -> None:
    rod = pulled_lines(["cover", PORTRAIT])[2]
    assert rod.desc == "Rod Round 96in Chrome"
    assert rod.dims == '39 3/4"W x 1"H'


def test_a_continuation_line_joins_the_row_above() -> None:
    screw = pulled_lines(["cover", PORTRAIT])[3]
    assert screw.desc.endswith("Zinc (1000 pk)")


def test_a_landscape_page_reads_quantity_first_in_two_columns() -> None:
    lines = pulled_lines(["cover", LANDSCAPE])
    assert [_row(line) for line in lines] == [
        ("CTO", "DR-WH", 1, "DR 15.2x31.4x3/4 FL WH", 1, 0),
        ("CTO", "BP-WH", 1, "BP 25.8x54.6x3/8 WH", 1, 1),
        ("CTO", "TR-WH", 9, "TR Base 4.1x96x3/4 FL WH", 1, 0),
        ("Components", "SH-WH", 2, "SH 23.9x29.9x3/4 FIX WH", 1, 0),
        ("Components", "CK-WH", 1, "CK Kick 2.5x23.9x3/4 DFC WH", 1, 1),
        ("Hardware", "400000151", 68, "Screw PFH #6 x 5/8 Zinc (1000 pk)", 1, 0),
    ]


def test_a_row_inside_a_description_starts_a_new_row() -> None:
    page = (HEAD_LANDSCAPE + "Components\n1\nSH-WH\n"
            "SH 14x23.5x3/4 ADJ WH 1 300444237 SH 16x19x3/4 ADJ ML\n"
            "2\nSH-WH\nSH 14x14.5x3/4 ADJ WH\n" + FOOTER_LANDSCAPE.format(n=8))
    lines = pulled_lines(["cover", page])
    assert [(line.item, line.qty, line.desc, line.column) for line in lines] == [
        ("SH-WH", 1, "SH 14x23.5x3/4 ADJ WH", 0),
        ("300444237", 1, "SH 16x19x3/4 ADJ ML", 1),
        ("SH-WH", 2, "SH 14x14.5x3/4 ADJ WH", 0),
    ]


def test_a_number_inside_a_description_is_not_a_new_row() -> None:
    page = (HEAD_LANDSCAPE + "CTO\n1\nWS-WH\nWS WH 101 20.6x42.2x1-1/8 WH\n"
            "1\nTR-WH\nTR Base 4.1x96x3/4 FL WH\n" + FOOTER_LANDSCAPE.format(n=8))
    lines = pulled_lines(["cover", page])
    assert [line.desc for line in lines] == ["WS WH 101 20.6x42.2x1-1/8 WH",
                                             "TR Base 4.1x96x3/4 FL WH"]


def test_the_footer_is_dropped_in_both_forms() -> None:
    for page in (PORTRAIT, LANDSCAPE):
        text = " ".join(line.desc + " " + line.dims for line in pulled_lines(["cover", page]))
        assert "Example" not in text and "Page" not in text and "555" not in text


def test_a_last_row_above_the_footer_is_kept_whole() -> None:
    page = (HEAD_PORTRAIT + "Hardware\n400000155\n56\nShelf Pin\n400000999\n2\n12 Pack Hooks\n"
            + FOOTER_PORTRAIT.format(n=8))
    lines = pulled_lines(["cover", page])
    assert [(line.item, line.qty, line.desc) for line in lines] == [
        ("400000155", 56, "Shelf Pin"), ("400000999", 2, "12 Pack Hooks")]


def test_a_pulled_page_is_found_without_its_title() -> None:
    untitled = PORTRAIT.replace("Project Parts List\n", "")
    assert len(pulled_lines(["cover", "Design: X\nITEM #\n", untitled])) == 4


def test_the_project_line_never_reaches_a_line() -> None:
    lines = pulled_lines(["cover", PORTRAIT]) + pulled_lines(["cover", LANDSCAPE])
    assert not any("Testerson" in (line.desc + line.item + line.section + line.dims)
                   for line in lines)


def test_an_item_printed_over_two_lines_joins_its_row() -> None:
    page = (HEAD_PORTRAIT + "Third Party Parts\nSpot Drilling\nRod\n4\nSpot Drilling Rod on site\n"
            "Custom Cut\n1\n" + FOOTER_PORTRAIT.format(n=8))
    lines = pulled_lines(["cover", page])
    assert [(line.section, line.item, line.qty, line.desc) for line in lines] == [
        ("Third Party Parts", "Spot Drilling Rod", 4, "Spot Drilling Rod on site"),
        ("Third Party Parts", "Custom Cut", 1, ""),
    ]


def test_a_line_that_belongs_to_no_row_raises() -> None:
    page = HEAD_PORTRAIT + "CTO\nVT-WH\n1\nVT 14x76.4x3/4 FD WH\nHardware\nstray words\n"
    with pytest.raises(SummaryUnreadable):
        pulled_lines(["cover", page + FOOTER_PORTRAIT.format(n=8)])


def test_page_one_is_never_read() -> None:
    with pytest.raises(SummaryUnreadable):
        pulled_lines([PORTRAIT])


def test_no_pulled_page_raises_in_plain_words() -> None:
    with pytest.raises(SummaryUnreadable) as raised:
        pulled_lines([COVER, "Design: Testerson - Primary Closet\nWALL/AREA NAME (W X H)\n"])
    assert str(raised.value) == "The IC summary has no PULLED parts list."


def test_two_layouts_in_one_summary_raise() -> None:
    with pytest.raises(SummaryUnreadable):
        pulled_lines(["cover", PORTRAIT, LANDSCAPE])


def test_a_pulled_page_with_no_rows_raises() -> None:
    with pytest.raises(SummaryUnreadable):
        pulled_lines(["cover", HEAD_PORTRAIT + FOOTER_PORTRAIT.format(n=2)])


def test_a_line_repr_holds_no_description() -> None:
    line = pulled_lines(["cover", PORTRAIT])[0]
    assert "VT 14x76.4" not in repr(line)


# ----- the design lists


DESIGN_LIST = ("Design: Testerson - Primary Closet\n Project: Testerson_101426\nParts List\n"
               "Design: Testerson - Primary Closet\nProject: Testerson_101426\n"
               "ITEM #\nQUANTITY\nDESCRIPTION\nDIMENSIONS\n"
               "CTO\nVT-WH\n1\nVT 14x76.4x3/4 FD WH\nSH-WH\n4\nSH 17.8x19.1x3/4 ADJ WH\n"
               + FOOTER_PORTRAIT.format(n=4))
WALL_LIST = ("Wall 2\nDesign: Testerson - Primary Closet\nITEM #\nQUANTITY\nDESCRIPTION\nCOLOR\n"
             "VT-WH\n1\nVT 14x76.4x3/4 FD WH\nArctic White\n" + FOOTER_PORTRAIT.format(n=3))


def test_design_lists_read_by_design_and_skip_wall_lists() -> None:
    found = design_lines(["cover", WALL_LIST, DESIGN_LIST, PORTRAIT])
    assert list(found) == ["Testerson - Primary Closet"]
    assert [(line.item, line.qty) for line in found["Testerson - Primary Closet"]] == [
        ("VT-WH", 1), ("SH-WH", 4)]


def test_a_design_list_carried_onto_the_next_page_joins_its_design() -> None:
    more = ("Design: Testerson - Primary Closet\nProject: Testerson_101426\n"
            "ITEM #\nQUANTITY\nDESCRIPTION\nDIMENSIONS\n400000155\n8\nShelf Pin\n"
            + FOOTER_PORTRAIT.format(n=5))
    found = design_lines(["cover", DESIGN_LIST, more])
    assert [line.item for line in found["Testerson - Primary Closet"]] == [
        "VT-WH", "SH-WH", "400000155"]


# ----- the estimate total


ESTIMATE = ("Testerson Family\n12 Fakeview Lane\nTestville, California 90000\n tester@example.com\n"
            "(555) 010-0000\nEstimate\n Project: Testerson_101426\nDESIGN\nTOTAL\n"
            "Testerson - Primary Closet\n$7,999.05\nDiscount\n-$1,000.00\nTotal\n$6,999.05\n"
            "Deposit (50.00%)\n$3,499.53\n" + FOOTER_PORTRAIT.format(n=6))


def test_the_estimate_total_is_the_figure_after_total() -> None:
    assert estimate_total(["cover", PORTRAIT, ESTIMATE]) == 6999.05


def test_no_estimate_page_gives_none() -> None:
    assert estimate_total(["cover", PORTRAIT]) is None


def test_a_total_that_is_not_money_gives_none() -> None:
    assert estimate_total(["cover", ESTIMATE.replace("$6,999.05", "see attached")]) is None


def test_the_estimate_on_page_one_is_never_read() -> None:
    assert estimate_total([ESTIMATE, PORTRAIT]) is None


# ----- reading the PDF


def test_bytes_that_are_not_a_pdf_raise_in_plain_words() -> None:
    with pytest.raises(SummaryUnreadable) as raised:
        read_pages(b"not a pdf")
    assert str(raised.value) == "The IC summary could not be read as a PDF."


# ----- real summaries (local only, by index)


def _pairs() -> list[dict[str, str]]:
    index = PAIRS / "index.json"
    if not index.is_file():
        pytest.skip("no local pairs: run tools/collect_p2_fixtures.py (AGENTS.md: Fixtures)")
    return json.loads(index.read_text(encoding="utf-8"))


def _pages(i: int) -> tuple[str, ...]:
    return read_pages((PAIRS / str(i) / "summary.pdf").read_bytes())


@pytest.mark.fixtures
def test_every_real_summary_gives_one_layout_and_pulled_lines() -> None:
    pairs = _pairs()
    layouts = []
    for i in range(len(pairs)):
        pages = _pages(i)
        lines = pulled_lines(pages)
        assert lines, f"pair {i} gave no PULLED line"
        first = next(p for p in pages[1:] if "PULLED" in p[:300])
        layouts.append(layout(first))
    assert len(layouts) == 52
    assert (layouts.count("qty_first"), layouts.count("item_first")) == (24, 28)


@pytest.mark.fixtures
def test_every_real_estimate_equals_its_json_total() -> None:
    held = equal = 0
    for i in range(len(_pairs())):
        data = json.loads((PAIRS / str(i) / "conversion.json").read_bytes().decode("utf-8-sig"))
        theirs = _json_total(data)
        if theirs is None:
            continue
        held += 1
        equal += estimate_total(_pages(i)) == theirs
    assert (held, equal) == (46, 46)


def _json_total(data: dict) -> float | None:
    """estimate.total, else a top-level estimate_total (two real files keep it there)."""
    estimate = data.get("estimate")
    value = estimate.get("total") if isinstance(estimate, dict) else data.get("estimate_total")
    if isinstance(value, str):
        try:
            return float(value.replace("$", "").replace(",", ""))
        except ValueError:
            return None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def test_an_item_that_reads_like_a_section_follows_its_quantity() -> None:
    page = (HEAD_LANDSCAPE + "Accessories\n4\n400000130\nDrawer Slide 22in Full Ext Soft\n"
            "1\nMarketplace\nHandle/Knob\nMarketplace Handle/Knob\n2\n400000133\nScrew\n"
            + FOOTER_LANDSCAPE.format(n=8))
    lines = pulled_lines(["cover", page])
    assert [(line.item, line.qty) for line in lines] == [
        ("400000130", 4), ("Marketplace", 1), ("400000133", 2)]
    assert lines[0].desc == "Drawer Slide 22in Full Ext Soft"
