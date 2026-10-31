"""
The IC Conversion workbook laid out from the conversion JSON, and its formulas evaluated in Python
(tools/workbook/sheets.py; design: "workbook checked in Python, with no LibreOffice on the PC").

The format is Jim's shared writer's: four sheets, Parts (mm), Material Takeoff, Verticals and PULLED
classified, the format of 23 of the 27 current workbooks in the pairs (measured 2026-10-31). The
conversion here is made up, with made-up names; nothing real is read.
"""
from __future__ import annotations

import copy

import pytest

from tools.workbook import sheets
from tools.workbook.sheets import Formula, Unevaluable, evaluate, layout, named

PART_HEADERS = ["Qty", "Type", "Grain", "Design", "Wall", "IC description", "Material", "Mount",
                "Radius", "IC a (in)", "IC b (in)", "IC a (mm)", "IC b (mm)", "BUILT a (mm)",
                "BUILT b (mm)", "BUILT a (imp)", "BUILT b (imp)", "SqFt", "EB LF", "Review"]
PULLED_HEADERS = ["Section", "Qty", "Item #", "Description", "Bucket", "Family", "Note"]
VERTICAL_HEADERS = ["Design", "Wall", "#", "Mount", "IC line", "Product", "Height mm", "Depth mm",
                    "Elev", "X mm (left face)", "X imp"]


def part(kind: str, qty: int, a: float, b: float, *, desc: str, built=None, review: str = "",
         sqft: float, eb: float, material: str = "Egger Classic White", thick: float = 0.75) -> dict:
    return {"design": "", "wall": "", "type": kind, "qty": qty, "desc": desc, "tokens": "",
            "mount": "FM" if kind == "VT" else "", "radius": False, "grain": "",
            "ic_in": [a, b], "ic_mm": [round(a * 25.4, 2), round(b * 25.4, 2)],
            "build_mm": built or [round(a * 25.4, 2), round(b * 25.4, 2)], "thick_in": thick,
            "material": material, "sqft": sqft, "eb_lf": eb, "review": review}


def conversion() -> dict:
    """A made-up closet: 2 verticals and 4 shelves in white, one 1/4 back with no sheet size, and
    one line the converter could not verify."""
    return {
        "schema_version": "2.0", "job": "Testerson_101426", "client": None, "designer": None,
        "address": None, "job_type": "closet", "designs": ["Testerson - Primary Closet"],
        "ic_color": "WH", "material_rule": "made-up rule words",
        "walls": {"Testerson - Primary Closet": [{"wall": "1", "len_in": 60.0, "height_in": 96.0,
                                                   "ang": None}]},
        "sheet_size_in": "by bucket", "waste_factor": 1.1, "total_board_parts": 7,
        "buckets": [
            {"material": "Egger Classic White", "ic_colour": "WH", "thk_in": 0.75, "thk_label": "3/4",
             "parts": 6, "sqft": 25.67, "sqm": 2.3848, "calc_sheets_raw": 0.8022,
             "sheets_with_waste": 1, "sheet_in": [49.0, 97.0],
             "sheet_math": "ceil(25.67 x 1.10 / 33.0069) = ceil(0.855) = 1", "edgeband_lf": 22.0,
             "tape": '15/16" / 1"'},
            {"material": "Egger Classic White", "ic_colour": "WH", "thk_in": 0.25, "thk_label": "1/4",
             "parts": 1, "sqft": 9.33, "sqm": 0.8668, "calc_sheets_raw": 0.2917,
             "sheets_with_waste": None, "sheet_in": None,
             "sheet_math": "could not verify: RULES' table gives no sheet for it", "edgeband_lf": 0.0,
             "tape": "none (BP)"},
        ],
        "edgeband": {"rows": [{"material": 'Egger Classic White 1mm — 15/16" / 1"', "linear_feet": 22.0},
                              {"material": 'Egger Classic White 1mm — none (BP)', "linear_feet": 0.0}],
                     "rule": "made-up tape rule words"},
        "verticals": [], "runs": {}, "estimate": {"total": 1234.56},
        "flags": ["Page 4: 2 of item CK-WH, a CK printed 0.6in thick: could not verify."],
        "parts": [
            part("VT", 2, 14.0, 84.0, desc="VT 14x84x3/4 FD WH", built=[356, 2132], sqft=16.333, eb=14.0),
            part("SH", 4, 14.0, 24.0, desc="SH 14x24x3/4 FIX WH", built=[356, 609.6], sqft=9.333, eb=8.0),
            part("BP", 1, 14.0, 96.0, desc="BP 14x96x3/8 WH", sqft=9.333, eb=0.0, thick=0.25),
        ],
        "pulled_classified": [
            {"section": "CTO", "qty": 2, "item": "VT-WH", "description": "VT 14x84x3/4 FD WH",
             "bucket": "board", "family": "VT", "class_note": "cut from sheet",
             "price_key": "VT-WH — VT 14x84x3/4 FD WH", "page": 3, "col": 0},
            {"section": "Components", "qty": 4, "item": "SH-WH", "description": "SH 14x24x3/4 FIX WH",
             "bucket": "board", "family": "SH", "class_note": "cut from sheet",
             "price_key": "SH-WH — SH 14x24x3/4 FIX WH", "page": 3, "col": 1},
            {"section": "Components", "qty": 1, "item": "BP-WH", "description": "BP 14x96x3/8 WH",
             "bucket": "board", "family": "BP", "class_note": "cut from sheet, built 1/4",
             "price_key": "BP-WH — BP 14x96x3/8 WH", "page": 3, "col": 0},
            {"section": "Components", "qty": 2, "item": "CK-WH", "description": "CK DBack 3.6x22.2x0.6 WH",
             "bucket": "could not verify", "family": "",
             "class_note": "a CK printed 0.6in thick, which the rules don't say is board",
             "price_key": "CK-WH — CK DBack 3.6x22.2x0.6 WH", "page": 3, "col": 1},
        ],
        "source": {"summary_pdf": "Testerson_101426_summary.pdf", "sha256_16": "0123456789abcdef"},
        "unplaced": [{"page": 4, "qty": 2, "item": "CK-WH", "description": "CK DBack 3.6x22.2x0.6 WH",
                      "reason": "a CK printed 0.6in thick, which the rules don't say is board"}],
        "ng_convert": {"complete": False, "branch": "closet"},
    }


def rows(sheet: sheets.Sheet) -> list[list]:
    return [[sheet.value(r, c) for c in range(1, sheet.max_column + 1)] for r in range(1, sheet.max_row + 1)]


def column_a(sheet: sheets.Sheet) -> list:
    return [sheet.value(r, 1) for r in range(1, sheet.max_row + 1)]


def label_value(sheet: sheets.Sheet, label: str, nth: int = 0):
    found = [sheet.value(r, 2) for r in range(1, sheet.max_row + 1) if sheet.value(r, 1) == label]
    return found[nth]


# ----- the format


def test_the_book_has_his_four_sheets_in_his_order_with_his_headers() -> None:
    book = layout(conversion())

    assert [s.title for s in book.sheets] == ["Parts (mm)", "Material Takeoff", "Verticals",
                                              "PULLED classified"]
    assert rows(book.sheet("Parts (mm)"))[0] == PART_HEADERS
    assert rows(book.sheet("PULLED classified"))[0] == PULLED_HEADERS
    assert rows(book.sheet("Verticals"))[0] == VERTICAL_HEADERS
    parts = book.sheet("Parts (mm)")
    assert all(parts.cell(1, c).bold for c in range(1, 21))
    assert parts.cell(1, 3).colour == "C00000"                       # his red Grain header
    assert parts.widths["F"] == 44 and book.sheet("Material Takeoff").widths["B"] == 130


def test_each_part_is_one_row_of_its_json_values_and_the_total_row_sums_them() -> None:
    conv = conversion()
    parts = layout(conv).sheet("Parts (mm)")

    body = rows(parts)[1:4]
    assert [r[0] for r in body] == [2, 4, 1] and [r[1] for r in body] == ["VT", "SH", "BP"]
    assert body[0][5] == "VT 14x84x3/4 FD WH" and body[0][13:17] == [356, 2132, '14"', '83 15/16"']
    assert [r[17] for r in body] == [16.333, 9.333, 9.333] and [r[18] for r in body] == [14.0, 8.0, 0.0]
    total = rows(parts)[4]
    assert total[0] == Formula("=SUM(A2:A4)") and total[17] == Formula("=SUM(R2:R4)")
    assert total[18] == Formula("=SUM(S2:S4)") and total[5] == "TOTAL BOARD PARTS (7)"
    assert parts.cell(5, 1).bold


def test_its_formulas_evaluate_in_python_to_the_takeoffs_totals() -> None:
    book = layout(conversion())

    values = evaluate(book)

    assert values[("Parts (mm)", "A5")] == 7
    assert values[("Parts (mm)", "R5")] == pytest.approx(34.999)
    assert values[("Parts (mm)", "S5")] == pytest.approx(22.0)


def test_it_writes_only_formulas_excel_and_his_checks_both_take() -> None:
    """BUDGET: never N(), nothing after Excel 2007. His shared writer uses SUM only."""
    book = layout(conversion())
    formulas = [c.value.text for s in book.sheets for c in s.cells.values() if isinstance(c.value, Formula)]
    assert formulas and all(f.startswith("=SUM(") and f.count("(") == 1 for f in formulas)


def test_the_takeoff_sheet_reads_as_his_label_by_label() -> None:
    mt = layout(conversion()).sheet("Material Takeoff")

    labels = column_a(mt)
    assert labels[0] == "Testerson_101426 — MATERIAL TAKEOFF" and mt.cell(1, 1).bold
    assert labels[1:7] == ["Client", "Designer", "Designs", "Branch", "IC colour", "Material rule"]
    for header in ("WALLS (IC WALL/AREA tables)", "EDGE BANDING", "VERTICAL RUNS", "FLAGS", "ESTIMATE"):
        assert header in labels and mt.cell(labels.index(header) + 1, 1).bold
    assert label_value(mt, "  Egger Classic White 3/4 — parts") == 6
    assert label_value(mt, "  — SqFt / SqM") == "25.67 / 2.3848"
    assert label_value(mt, "  — Calc Sheets (SqFt/32 raw)") == 0.8022
    assert label_value(mt, "  — SHEETS TO ORDER") == 1
    assert label_value(mt, "  — sheet math").startswith("ceil(25.67 x 1.10")
    assert label_value(mt, '  Egger Classic White 1mm — tape 15/16" / 1"') == 22.0
    assert label_value(mt, "  Rule") == "made-up tape rule words"
    assert label_value(mt, "  Total") == 1234.56
    assert label_value(mt, "  Testerson - Primary Closet") == 'W1 60" x 96"'


def test_the_schedule_paste_block_lists_sized_buckets_by_sheets_and_names_the_rest() -> None:
    mt = layout(conversion()).sheet("Material Takeoff")
    assert label_value(mt, "Schedule paste block") == (
        "1 - 3/4 Egger Classic White | could not verify - 1/4 Egger Classic White")


def test_the_client_rows_are_left_blank_never_guessed() -> None:
    mt = layout(conversion()).sheet("Material Takeoff")
    assert label_value(mt, "Client") is None and label_value(mt, "Designer") is None


# ----- could not verify: listed as such, never filled in


def test_a_bucket_with_no_sheet_size_reads_could_not_verify_and_never_a_number() -> None:
    mt = layout(conversion()).sheet("Material Takeoff")
    assert label_value(mt, "  Egger Classic White 1/4 — parts") == 1
    assert label_value(mt, "  — SHEETS TO ORDER", 1) == "could not verify"
    assert label_value(mt, "  — sheet math", 1).startswith("could not verify")


def test_a_line_that_could_not_be_verified_is_listed_as_such_and_never_becomes_a_part_row() -> None:
    book = layout(conversion())
    mt, parts, pulled = (book.sheet(t) for t in ("Material Takeoff", "Parts (mm)", "PULLED classified"))

    labels = column_a(mt)
    at = labels.index("COULD NOT VERIFY (never filled in)")
    assert mt.cell(at + 1, 1).bold
    assert labels[at + 1] == "  1" and mt.value(at + 2, 2) == (
        "Page 4: 2 of item CK-WH (CK DBack 3.6x22.2x0.6 WH), a CK printed 0.6in thick, which the"
        " rules don't say is board")
    assert "CK" not in [parts.value(r, 2) for r in range(2, parts.max_row + 1)]
    assert parts.value(6, 6) == "NOT COUNTED: 2 pieces on 1 line could not verify (Material Takeoff)"
    assert [pulled.value(r, 5) for r in range(2, pulled.max_row + 1)] == [
        "board", "board", "board", "could not verify"]


def test_a_complete_conversion_has_no_could_not_verify_section_and_no_not_counted_row() -> None:
    conv = conversion()
    conv["unplaced"], conv["flags"] = [], []
    book = layout(conv)
    assert "COULD NOT VERIFY (never filled in)" not in column_a(book.sheet("Material Takeoff"))
    assert book.sheet("Parts (mm)").max_row == 5


def test_what_the_converter_does_not_make_is_said_never_left_looking_empty() -> None:
    conv = conversion()
    book = layout(conv)
    mt, verticals = book.sheet("Material Takeoff"), book.sheet("Verticals")
    labels = column_a(mt)
    assert mt.value(labels.index("VERTICAL RUNS") + 2, 2) == (
        "not made by ng_convert: the verticals come from the drawings")
    assert verticals.value(2, 1) == "not made by ng_convert: the verticals come from the drawings"


# ----- reading named cells (ours, or his)


def test_named_cells_are_read_by_label_and_header() -> None:
    found = named(layout(conversion()))

    assert found["board_parts"] == 7 and found["sqft"] == pytest.approx(34.999)
    assert found["tape"] == pytest.approx(22.0) and found["estimate_total"] == 1234.56
    assert found["buckets"]["Egger Classic White 3/4"] == {
        "parts": 6, "sqft": 25.67, "sqm": 2.3848, "calc_sheets_raw": 0.8022, "sheets": 1}
    assert found["buckets"]["Egger Classic White 1/4"]["sheets"] == "could not verify"
    assert found["edgeband"] == {'Egger Classic White 1mm — tape 15/16" / 1"': 22.0,
                                 "Egger Classic White 1mm — tape none (BP)": 0.0}
    assert found["schedule"] == "1 - 3/4 Egger Classic White | could not verify - 1/4 Egger Classic White"
    assert [(r["type"], r["qty"]) for r in found["parts"]] == [("VT", 2), ("SH", 4), ("BP", 1)]
    assert found["pulled"] == {"rows": 4, "pieces": 9, "board": 7}


def test_named_cells_of_a_book_without_formulas_cached_still_evaluate() -> None:
    """His files are saved by openpyxl with no cached values, so his totals are evaluated too."""
    book = layout(conversion())
    assert named(book)["board_parts"] == 7


# ----- the evaluator


@pytest.mark.parametrize("text, want", [
    ("=SUM(A1:A3)", 6), ("=A1+A2*A3", 7), ("=(A1+A2)*A3", 9), ("=SUM(A1:A3)/2", 3),
    ("=ROUND(A3/7,2)", 0.43), ("=ROUNDUP(A3/2,0)", 2), ("=CEILING(A3/2,1)", 2), ("=ABS(-A2)", 2),
    ("=SUM(A1:B2)", 3), ("=-A1", -1), ("=2^3", 8),
])
def test_the_evaluator_takes_the_formulas_his_workbooks_hold(text: str, want: float) -> None:
    book = sheets.Book([sheets.Sheet("S")])
    s = book.sheet("S")
    for r, v in enumerate((1, 2, 3), 1):
        s.put(r, 1, v)
    s.put(1, 2, "words")                                  # text in a SUM counts nothing, as in Excel
    s.put(5, 1, Formula(text))
    assert evaluate(book)[("S", "A5")] == pytest.approx(want)


def test_a_formula_the_evaluator_does_not_know_is_unevaluable_never_a_number() -> None:
    book = sheets.Book([sheets.Sheet("S")])
    book.sheet("S").put(1, 1, Formula("=SUMPRODUCT((A2:A3>0)*1)"))
    with pytest.raises(Unevaluable):
        evaluate(book)


def test_a_formula_reading_another_sheet_is_followed() -> None:
    book = sheets.Book([sheets.Sheet("A b"), sheets.Sheet("C")])
    book.sheet("A b").put(2, 1, 5)
    book.sheet("C").put(1, 1, Formula("='A b'!A2*2"))
    assert evaluate(book)[("C", "A1")] == 10


def test_layout_never_changes_the_conversion_it_renders() -> None:
    conv = conversion()
    before = copy.deepcopy(conv)
    layout(conv)
    assert conv == before


def test_an_empty_text_is_a_blank_cell_as_an_xlsx_holds_it() -> None:
    """A PULLED row Studio printed with no description (at a page's end) has '' as its text; an .xlsx
    keeps no empty string, so the layout holds none either, or the read-back check could never pass."""
    conv = conversion()
    conv["pulled_classified"][0]["description"] = ""
    pulled = layout(conv).sheet("PULLED classified")
    assert (2, 4) not in pulled.cells and pulled.value(2, 4) is None
