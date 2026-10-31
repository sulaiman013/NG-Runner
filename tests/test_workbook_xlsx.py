"""
Saving the workbook (openpyxl, which only the tools' Python and Jim's container have) and reading an
.xlsx back with the standard library, so a saved file is checked cell by cell in Python
(tools/workbook/xlsx.py).

The reader is tested here on a file made by hand, so it runs in NG Runner's own venv. Saving needs
openpyxl: those tests run in the tools' Python (NGR_TOOLS_PYTHON or the development venv) and skip
when it is absent, as do the ones that read the chat's real workbooks (marked fixtures).
"""
from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from helpers_toolkit import tools_python
from test_workbook_sheets import conversion
from tools.workbook import sheets, xlsx
from tools.workbook.sheets import Formula, layout

ROOT = Path(__file__).resolve().parent.parent

# ----- reading, on a file made by hand

_TYPES = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
          'content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
_BOOK = ('<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.'
         'openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Odd &amp; one" sheetId="1" '
         'r:id="rId7"/></sheets></workbook>')
_RELS = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship '
         'Id="rId7" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
         'Target="/xl/worksheets/sheet9.xml"/></Relationships>')
_STRINGS = ('<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Qty</t></si><si><r>'
            '<t>rich </t></r><r><t>text</t></r></si></sst>')
_STYLES = ('<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font/>'
           '<font><b/><color rgb="FFC00000"/></font></fonts><cellXfs count="2"><xf fontId="0"/><xf fontId="1"/>'
           '</cellXfs></styleSheet>')
_SHEET = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cols><col min="2" max="3" '
          'width="44.5" customWidth="1"/></cols><sheetData><row r="1"><c r="A1" t="s" s="1"><v>0</v></c><c r="B1" '
          't="s"><v>1</v></c><c r="C1" t="inlineStr"><is><t>inline</t></is></c></row><row r="2"><c r="A2"><v>356'
          '</v></c><c r="B2"><v>759.46</v></c><c r="C2" t="b"><v>1</v></c></row><row r="4"><c r="A4" s="1"><f>SUM'
          '(A2:A3)</f></c><c r="B4" t="str"><f>A1&amp;"x"</f><v>Qtyx</v></c></row></sheetData></worksheet>')


def handmade(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", _TYPES)
        z.writestr("xl/workbook.xml", _BOOK)
        z.writestr("xl/_rels/workbook.xml.rels", _RELS)
        z.writestr("xl/sharedStrings.xml", _STRINGS)
        z.writestr("xl/styles.xml", _STYLES)
        z.writestr("xl/worksheets/sheet9.xml", _SHEET)
    return path


def test_the_reader_takes_strings_numbers_formulas_booleans_fonts_and_widths(tmp_path: Path) -> None:
    book = xlsx.read(handmade(tmp_path / "made.xlsx"))

    s = book.sheet("Odd & one")
    assert [s.value(1, c) for c in (1, 2, 3)] == ["Qty", "rich text", "inline"]
    assert s.cell(1, 1).bold and s.cell(1, 1).colour == "C00000" and not s.cell(1, 2).bold
    assert s.value(2, 1) == 356 and isinstance(s.value(2, 1), int) and s.value(2, 2) == 759.46
    assert s.value(2, 3) is True
    assert s.value(4, 1) == Formula("=SUM(A2:A3)") and s.value(4, 2) == Formula('=A1&"x"')
    assert s.widths == {"B": 44.5, "C": 44.5}


def test_a_file_that_isnt_a_workbook_is_unreadable_in_plain_words(tmp_path: Path) -> None:
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(xlsx.WorkbookUnreadable, match="could not be read"):
        xlsx.read(bad)


def test_check_reads_a_book_against_the_conversion_without_saving_it() -> None:
    assert xlsx.check_book(layout(conversion()), conversion()) == []


def test_check_finds_a_line_that_could_not_be_verified_filled_in_as_a_part() -> None:
    conv = conversion()
    book = layout(conv)
    parts = book.sheet("Parts (mm)")
    parts.cells = {(r + (1 if r >= 5 else 0), c): v for (r, c), v in parts.cells.items()}   # a row in
    for col, value in enumerate([2, "CK", None, None, None, "CK DBack 3.6x22.2x0.6 WH"], 1):
        parts.put(5, col, value)

    problems = xlsx.check_book(book, conv)

    assert "Parts (mm) holds 4 part rows where the conversion has 3." in problems


def test_check_finds_a_sheet_count_written_where_could_not_verify_belongs() -> None:
    conv = conversion()
    book = layout(conv)
    mt = book.sheet("Material Takeoff")
    rows = [r for r in range(1, mt.max_row + 1) if mt.value(r, 1) == "  — SHEETS TO ORDER"]
    mt.put(rows[1], 2, 1)
    assert "Material Takeoff: Egger Classic White 1/4 sheets read 1, not could not verify." in xlsx.check_book(book, conv)


def test_check_finds_a_total_that_doesnt_add_up() -> None:
    conv = conversion()
    book = layout(conv)
    book.sheet("Parts (mm)").put(2, 1, 3)
    problems = xlsx.check_book(book, conv)
    assert "Parts (mm)'s total row says 8 board parts, not 7." in problems


def test_check_finds_a_formula_it_cannot_evaluate() -> None:
    conv = conversion()
    book = layout(conv)
    book.sheet("Parts (mm)").put(5, 18, Formula("=SUMPRODUCT(R2:R4)"))
    assert any("could not be evaluated" in p for p in xlsx.check_book(book, conv))


def test_a_book_survives_its_json_form() -> None:
    book = layout(conversion())
    again = sheets.load(json.loads(json.dumps(sheets.dump(book))))
    assert [(s.title, s.cells, s.widths) for s in again.sheets] == [(s.title, s.cells, s.widths) for s in book.sheets]


# ----- saving, in the tools' Python


def save_in_tools_python(book: sheets.Book, out: Path) -> None:
    spec = out.with_suffix(".json")
    spec.write_text(json.dumps(sheets.dump(book)), encoding="utf-8")
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); from tools.workbook import sheets, xlsx; "
            "xlsx.save(sheets.load(json.load(open(sys.argv[2], encoding='utf-8'))), sys.argv[3])")
    done = subprocess.run([str(tools_python()), "-I", "-B", "-X", "utf8", "-c", code, str(ROOT), str(spec), str(out)],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr


@pytest.mark.fixtures
def test_a_saved_workbook_reads_back_cell_for_cell_and_checks_clean(tmp_path: Path) -> None:
    conv = conversion()
    book = layout(conv)
    out = tmp_path / "Testerson_101426_IC_Conversion.xlsx"

    save_in_tools_python(book, out)
    back = xlsx.read(out)

    assert [s.title for s in back.sheets] == [s.title for s in book.sheets]
    for mine, theirs in zip(book.sheets, back.sheets):
        assert {k: v for k, v in theirs.cells.items() if v.value is not None} == {
            k: v for k, v in mine.cells.items() if v.value is not None}, mine.title
        assert theirs.widths == mine.widths
    assert xlsx.check(out, conv) == []
    with zipfile.ZipFile(out) as z:
        assert 'fullCalcOnLoad="1"' in z.read("xl/workbook.xml").decode("utf-8")


@pytest.mark.fixtures
def test_check_on_a_saved_file_finds_what_was_filled_in(tmp_path: Path) -> None:
    conv = conversion()
    book = layout(conv)
    book.sheet("Parts (mm)").put(4, 1, 5)                               # one more back than the summary
    out = tmp_path / "filled.xlsx"
    save_in_tools_python(book, out)
    assert "Parts (mm)'s total row says 11 board parts, not 7." in xlsx.check(out, conv)


@pytest.mark.fixtures
def test_the_reader_reads_the_chats_real_workbooks_as_openpyxl_does() -> None:
    from conftest import FIXTURES
    pairs = FIXTURES / "p2" / "pairs"
    files = sorted(pairs.glob("*/conversion.xlsx"), key=lambda p: int(p.parent.name))
    if not files:
        pytest.skip("no local pairs")
    code = ("import json, sys, openpyxl\nout = {}\nfor path in sys.argv[1:]:\n"
            "    wb = openpyxl.load_workbook(path)\n"
            "    out[path] = {ws.title: {c.coordinate: c.value for row in ws.iter_rows() for c in row"
            " if c.value is not None and not hasattr(c.value, 'text')} for ws in wb.worksheets}\n"
            "print(json.dumps(out, default=str))")
    done = subprocess.run([str(tools_python()), "-I", "-B", "-X", "utf8", "-c", code, *map(str, files)],
                          capture_output=True, text=True, encoding="utf-8", timeout=600)
    assert done.returncode == 0, done.stderr
    theirs = json.loads(done.stdout)
    checked = 0
    for path in files:
        book = xlsx.read(path)
        for title, cells in theirs[str(path)].items():
            s = book.sheet(title)
            mine = {sheets.coordinate(r, c): (v.value.text if isinstance(v.value, Formula) else v.value)
                    for (r, c), v in s.cells.items() if v.value is not None}
            assert mine == cells, (path.parent.name, title)
            checked += len(cells)
    assert len(files) == 52 and checked > 20_000
