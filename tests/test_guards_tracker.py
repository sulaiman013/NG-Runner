"""
The tracker snapshot reader (docs/p1b/build-spec.md, sections 3.5 and 5.5).

Synthetic CSVs here use made-up jobs and the file's real shape. Real snapshots are read only by the
tests marked fixtures, from .local-fixtures/p1b/tracker, and report counts, never names.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ngrunner.guards.tracker import (
    TrackerSnapshot,
    TrackerUnreadable,
    plain_material,
    read_tracker,
    split_tape_name,
)
from ngrunner.messages.lint import check_line
from conftest import FIXTURES

JOB = "Testerson_101426"
OTHER = "Sampleton_101526"
CUSTOMER = "Fakecustomer Residence"
NOTE = "Fakenote about the gate code"
TAKEN = datetime(2026, 10, 30, 23, 51, 34, tzinfo=timezone.utc)

SHEET_HEADER = ["Job", "Customer", "Material", "Calc\nSqM", "Calc\nSqFt", "Calc\nSheets",
                "Optimizer\nSheets", "$/Sheet", "Material\nCost ($)", "Waste\n(sheets)", "Waste %",
                "Hours\n(calc)", "Labor\nCost ($)", "Sheet+Labor\nSubtotal ($)", "Sheet\nStatus",
                "EB\nStatus", ""]
TAPE_HEADER = ["Job", "Customer", "Material", "Calc\nLin Ft", "Optimizer\nLin Ft", "$/LF",
               "EB Cost ($)", "Waste\n(LF)", "Waste %", "Status", "EB Colour"]
JOB_HEADER = ["Job", "Customer", "Sheet Mat ($)", "EB Cost ($)", "Labor ($)", "Total Cost ($)",
              "Status", "Notes"]


def sheet_row(job: str = JOB, material: str = "Egger Classic White", calc: str = "6.1",
              optimizer: str = "7", price: str = "$40.00", cost: str = "$280.00",
              status: str = "Ready") -> list[str]:
    return [job, CUSTOMER, material, "18.0", "193.8", calc, optimizer, price, cost, "0.9", "12.9%",
            "1.17", "$93.33", "$373.33", status, "Ready", ""]


def tape_row(job: str = JOB, material: str = 'Egger Classic White 1mm — 15/16" / 1"',
             calc: str = "179.3", optimizer: str = "179.3", price: str = "$0.2700",
             cost: str = "$48.41", status: str = "Ready") -> list[str]:
    return [job, CUSTOMER, material, calc, optimizer, price, cost, "0", "0.0%", status,
            "Egger Classic White"]


def job_row(job: str = JOB, status: str = "🔲 In progress") -> list[str]:
    return [job, CUSTOMER, "$280.00", "$48.41", "$93.33", "$421.74", status, NOTE]


def write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as out:
        csv.writer(out).writerows(rows)


def write_snapshot(folder: Path, *, sheets: list[list[str]] | None = None,
                   tape: list[list[str]] | None = None, jobs: list[list[str]] | None = None,
                   sheet_keys: list[list[str]] | None = None,
                   tape_keys: list[list[str]] | None = None,
                   taken: str = "2026-10-30T23:51:34Z") -> Path:
    """A snapshot in the shape section 3.5 measured: title rows, then a header, a closing row."""
    folder.mkdir(parents=True, exist_ok=True)
    rows = [sheet_row()] if sheets is None else sheets
    write_csv(folder / "tracker_snapshot_SheetGoods.csv",
              [["TRACKER TITLE"] + [""] * 5, ["Labor note"] + [""] * 5, [""] * 6, SHEET_HEADER,
               *rows, ["TOTALS", "", "", "", "", "", "580", "", "$29,213.00"]])
    rows = [tape_row()] if tape is None else tape
    write_csv(folder / "tracker_snapshot_Edgeband.csv",
              [["TAPE TITLE"] + [""] * 5, [""] * 6, TAPE_HEADER, *rows,
               ["TOTALS", "", "", "", "", "", "$2,793.12"]])
    rows = [job_row()] if jobs is None else jobs
    write_csv(folder / "tracker_snapshot_JobSummary.csv",
              [["SUMMARY TITLE"] + [""] * 5, [""] * 6, JOB_HEADER, *rows,
               ["GRAND TOTAL", "", "$29,213.00", "$2,793.12", "$7,733.33", "$39,739.45", "", ""]])
    sheet_keys = [["Egger Classic White", "$40.00", "", ""]] if sheet_keys is None else sheet_keys
    tape_keys = [['Egger Classic White 1mm — 15/16" / 1"', "$0.2700", "", ""]] \
        if tape_keys is None else tape_keys
    write_csv(folder / "tracker_snapshot_Pricing.csv",
              [["PRICING TITLE", "", "", ""], ["LABOR", "", "", ""], ["Labor rate", "80", "", ""],
               ["SHEET GOODS — price per sheet", "", "", ""],
               ["Material  (this exact text is what the tracker matches)", "$/Sheet", "Notes", ""],
               *sheet_keys,
               ["▲ add sheet-good materials anywhere above this line", "", "", ""],
               ["Name = material + gauge + TAPE", "", "", ""],
               ["EDGEBAND — price per linear foot", "", "", ""],
               ["Material + gauge + tape width  (this exact text)", "$/LF", "Notes", ""],
               *tape_keys,
               ["▲ add edgeband anywhere above this line", "", "", ""],
               ["Item  (this exact text is what the tracker matches)", "$/each", "", ""],
               ["Drawer wrap", "$45.00", "", ""],
               ["THIRD PARTY PARTS — price per each", "", "", ""],
               ["100000595 — Cam Double Bolt 1 piece", "", "", ""]])
    write_csv(folder / "tracker_snapshot_meta.csv",
              [["key", "value"], ["sheet_name", "Tracker"], ["sheet_id", "not-a-real-id"],
               ["sheet_lastupdate", "2026-10-21T19:39:36Z"], ["snapshot_taken", taken],
               ["tabs_exported", "4"]])
    return folder


# ----- reading a snapshot


def test_the_snapshot_time_and_last_update_are_read_in_utc(tmp_path: Path) -> None:
    snap = read_tracker(write_snapshot(tmp_path))
    assert snap.taken == TAKEN
    assert snap.last_update == datetime(2026, 10, 21, 19, 39, 36, tzinfo=timezone.utc)
    assert snap.taken.utcoffset() == timedelta(0)


def test_a_sheet_row_is_read_by_its_header_with_display_values_turned_to_numbers(
        tmp_path: Path) -> None:
    snap = read_tracker(write_snapshot(tmp_path, sheets=[
        sheet_row(calc="6.1", optimizer="7", price="$1,071.33", cost="$7,499.31",
                  status="Complete")]))
    row = snap.sheets[0]
    assert (row.job, row.material) == (JOB, "Egger Classic White")
    assert (row.calc_sheets, row.optimizer_sheets) == (6.1, 7.0)
    assert (row.price, row.cost) == (1071.33, 7499.31)
    assert row.status == "Complete"


def test_a_tape_row_is_read_with_a_dash_meaning_no_number(tmp_path: Path) -> None:
    snap = read_tracker(write_snapshot(tmp_path, tape=[
        tape_row(calc="—", optimizer="", price="$0.2700", cost="", status="")]))
    row = snap.tape[0]
    assert row.material == 'Egger Classic White 1mm — 15/16" / 1"'
    assert row.calc_feet is None
    assert row.optimizer_feet is None
    assert row.price == 0.27
    assert row.cost is None


def test_a_job_row_keeps_its_status_exactly_as_typed(tmp_path: Path) -> None:
    snap = read_tracker(write_snapshot(tmp_path, jobs=[job_row(status="🔒 LOCKED ")]))
    assert snap.jobs[0].job == JOB
    assert snap.jobs[0].status == "🔒 LOCKED "


def test_void_rows_are_not_materials(tmp_path: Path) -> None:
    snap = read_tracker(write_snapshot(
        tmp_path,
        sheets=[sheet_row(material="VOID -- corrected, folded into the row above"), sheet_row()],
        tape=[tape_row(material="VOID -- corrected", calc="0"), tape_row()]))
    assert [r.material for r in snap.sheets] == ["Egger Classic White"]
    assert len(snap.tape) == 1


def test_blank_rows_are_skipped_and_the_closing_row_ends_the_tab(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    folder = write_snapshot(tmp_path / "x", tape=[[""] * 11, tape_row()])
    snap = read_tracker(folder)
    assert len(snap.tape) == 1
    assert all(r.job == JOB for r in snap.sheets)   # TOTALS is not a job


def test_nothing_after_the_closing_row_is_read(tmp_path: Path) -> None:
    write_snapshot(tmp_path)
    path = tmp_path / "tracker_snapshot_SheetGoods.csv"
    with path.open("a", encoding="utf-8", newline="") as out:
        csv.writer(out).writerow(sheet_row(job=OTHER))
    assert {r.job for r in read_tracker(tmp_path).sheets} == {JOB}


def test_a_header_cell_with_a_new_line_inside_it_still_finds_its_column(tmp_path: Path) -> None:
    # newline="" matters: without it the cell's \n would be folded and the column lost
    snap = read_tracker(write_snapshot(tmp_path, sheets=[sheet_row(calc="12.5")]))
    assert snap.sheets[0].calc_sheets == 12.5


def test_customer_and_notes_are_never_kept(tmp_path: Path) -> None:
    snap = read_tracker(write_snapshot(tmp_path))
    text = repr(snap)
    assert CUSTOMER not in text
    assert NOTE not in text
    assert "not-a-real-id" not in text


def test_a_snapshot_is_a_plain_frozen_value(tmp_path: Path) -> None:
    snap = read_tracker(write_snapshot(tmp_path))
    assert isinstance(snap, TrackerSnapshot)
    with pytest.raises(AttributeError):
        snap.taken = TAKEN  # type: ignore[misc]


# ----- Pricing


def test_pricing_holds_only_the_two_price_blocks(tmp_path: Path) -> None:
    snap = read_tracker(write_snapshot(
        tmp_path,
        sheet_keys=[["Egger Classic White", "$40.00", "", ""],
                    ["White 1/4 Vinyl", "$46.00", "", ""]],
        tape_keys=[['Ore 1mm — 15/16" / 1"', "$0.2700", "", ""]]))
    assert [(k.material, k.price) for k in snap.sheet_prices] == [
        ("Egger Classic White", 40.0), ("White 1/4 Vinyl", 46.0)]
    assert [(k.material, k.price) for k in snap.tape_prices] == [('Ore 1mm — 15/16" / 1"', 0.27)]


def test_a_hint_line_is_not_a_price_key(tmp_path: Path) -> None:
    snap = read_tracker(write_snapshot(tmp_path))
    names = [k.material for k in (*snap.sheet_prices, *snap.tape_prices)]
    assert not any(n.startswith(("▲", "Name =", "Material")) for n in names)
    assert "Drawer wrap" not in names


def test_blank_lines_inside_a_price_block_are_skipped(tmp_path: Path) -> None:
    snap = read_tracker(write_snapshot(
        tmp_path, sheet_keys=[["Egger Classic White", "$40.00", "", ""], ["", "", "", ""],
                              ["Am Lam Wh (3/4)", "$53.00", "", ""]]))
    assert [k.material for k in snap.sheet_prices] == ["Egger Classic White", "Am Lam Wh (3/4)"]


def test_a_key_spelled_with_and_without_a_trailing_space_is_found(tmp_path: Path) -> None:
    snap = read_tracker(write_snapshot(
        tmp_path, sheet_keys=[["Egger Classic White (1/4) 5x9 ", "$60.00", "", ""],
                              ["Egger Classic White (1/4) 5x9", "$65.00", "", ""],
                              ["Egger Classic White", "$40.00", "", ""]]))
    assert snap.keys_spelled_twice() == ("Egger Classic White (1/4) 5x9",)


def test_keys_are_kept_exactly_since_the_tracker_matches_them_exactly(tmp_path: Path) -> None:
    snap = read_tracker(write_snapshot(
        tmp_path, sheet_keys=[["Egger Classic White ", "$60.00", "", ""]]))
    assert snap.sheet_prices[0].material == "Egger Classic White "


def test_a_key_with_no_price_is_found(tmp_path: Path) -> None:
    snap = read_tracker(write_snapshot(
        tmp_path, sheet_keys=[["Tafisa Apris Ski (1/4) 4x8", "", "", ""],
                              ["Egger Classic White", "$40.00", "", ""]]))
    assert snap.keys_without_price() == ("Tafisa Apris Ski (1/4) 4x8",)


def test_status_spellings_are_compared_exactly_and_blanks_do_not_count(tmp_path: Path) -> None:
    snap = read_tracker(write_snapshot(tmp_path, jobs=[
        job_row("A_101426", "🔒 LOCKED"), job_row("B_101426", "LOCKED"),
        job_row("C_101426", "LOCKED"), job_row("D_101426", ""), job_row("E_101426", "✅ Complete")]))
    assert set(snap.status_spellings()) == {"🔒 LOCKED", "LOCKED", "✅ Complete"}


# ----- an unreadable snapshot raises, in plain words, instead of a partial read


def _drop(folder: Path, tab: str) -> None:
    (folder / f"tracker_snapshot_{tab}.csv").unlink()


def _rewrite(folder: Path, tab: str, rows: list[list[str]]) -> None:
    write_csv(folder / f"tracker_snapshot_{tab}.csv", rows)


@pytest.mark.parametrize("tab", ["SheetGoods", "Edgeband", "JobSummary", "Pricing", "meta"])
def test_a_missing_tab_is_unreadable(tmp_path: Path, tab: str) -> None:
    _drop(write_snapshot(tmp_path), tab)
    with pytest.raises(TrackerUnreadable):
        read_tracker(tmp_path)


def test_a_missing_folder_is_unreadable(tmp_path: Path) -> None:
    with pytest.raises(TrackerUnreadable):
        read_tracker(tmp_path / "nowhere")


@pytest.mark.parametrize("tab", ["SheetGoods", "Edgeband", "JobSummary"])
def test_a_tab_with_no_header_row_is_unreadable(tmp_path: Path, tab: str) -> None:
    write_snapshot(tmp_path)
    _rewrite(tmp_path, tab, [["TITLE"], [JOB, "", ""], ["TOTALS"], ["GRAND TOTAL"]])
    with pytest.raises(TrackerUnreadable):
        read_tracker(tmp_path)


@pytest.mark.parametrize("tab, header", [
    ("SheetGoods", [h for h in SHEET_HEADER if h != "$/Sheet"]),
    ("Edgeband", [h for h in TAPE_HEADER if h != "Calc\nLin Ft"]),
    ("JobSummary", [h for h in JOB_HEADER if h != "Status"]),
])
def test_a_renamed_column_is_unreadable_never_a_guess(tmp_path: Path, tab: str,
                                                      header: list[str]) -> None:
    write_snapshot(tmp_path)
    _rewrite(tmp_path, tab, [header, ["TOTALS"], ["GRAND TOTAL"]])
    with pytest.raises(TrackerUnreadable):
        read_tracker(tmp_path)


@pytest.mark.parametrize("tab, header", [
    ("SheetGoods", SHEET_HEADER), ("Edgeband", TAPE_HEADER), ("JobSummary", JOB_HEADER)])
def test_a_tab_cut_short_with_no_closing_row_is_unreadable(tmp_path: Path, tab: str,
                                                           header: list[str]) -> None:
    write_snapshot(tmp_path)
    _rewrite(tmp_path, tab, [header, [JOB] + [""] * (len(header) - 1)])
    with pytest.raises(TrackerUnreadable):
        read_tracker(tmp_path)


@pytest.mark.parametrize("taken", ["", "soon", "2026-10-30T23:51:34"])
def test_a_snapshot_with_no_usable_time_is_unreadable(tmp_path: Path, taken: str) -> None:
    write_snapshot(tmp_path, taken=taken)
    with pytest.raises(TrackerUnreadable):
        read_tracker(tmp_path)


def test_pricing_with_a_block_missing_is_unreadable(tmp_path: Path) -> None:
    write_snapshot(tmp_path)
    _rewrite(tmp_path, "Pricing", [["SHEET GOODS — price per sheet"], ["Material", "$/Sheet"]])
    with pytest.raises(TrackerUnreadable):
        read_tracker(tmp_path)


def test_a_price_block_with_no_end_line_is_unreadable_as_it_would_run_on(tmp_path: Path) -> None:
    write_snapshot(tmp_path)
    _rewrite(tmp_path, "Pricing", [
        ["SHEET GOODS — price per sheet"], ["Material", "$/Sheet"], ["Egger Classic White", "$40"],
        ["EDGEBAND — price per linear foot"], ["Material", "$/LF"], ["Ore 1mm", "$0.27"]])
    with pytest.raises(TrackerUnreadable):
        read_tracker(tmp_path)


def test_bytes_that_are_not_utf8_are_unreadable(tmp_path: Path) -> None:
    write_snapshot(tmp_path)
    (tmp_path / "tracker_snapshot_meta.csv").write_bytes(b"key,value\r\n\xff\xfe,\x80\r\n")
    with pytest.raises(TrackerUnreadable):
        read_tracker(tmp_path)


@pytest.mark.parametrize("break_it", [
    lambda d: _drop(d, "Pricing"),
    lambda d: _rewrite(d, "JobSummary", [["Title"]]),
    lambda d: write_snapshot(d, taken="soon"),
])
def test_the_reason_is_in_plain_words_with_no_path(tmp_path: Path, break_it) -> None:
    write_snapshot(tmp_path)
    break_it(tmp_path)
    with pytest.raises(TrackerUnreadable) as caught:
        read_tracker(tmp_path)
    assert check_line(str(caught.value)) == []
    assert str(tmp_path) not in str(caught.value)


# ----- words for a person


@pytest.mark.parametrize("text, words", [
    ("Tafisa Spring Blossom_Ore", "Tafisa Spring Blossom Ore"),
    ("Tafisa Casting at First Light_M2007", "Tafisa Casting at First Light M2007"),
    ("Egger Classic White ", "Egger Classic White"),
    ("Am Lam Wh (3/4)", "Am Lam Wh (3/4)"),
])
def test_a_material_reads_plainly_and_passes_the_lint(text: str, words: str) -> None:
    assert plain_material(text) == words
    assert check_line(words) == []


@pytest.mark.parametrize("name, parts", [
    ('Tafisa 1mm — 15/16" / 1"', ("Tafisa", '15/16"')),
    ('Am Lam Wh 1mm — 1-1/4" / 1-5/16"', ("Am Lam Wh", '1-1/4"')),
    ('Charcoal 2mm — 1-1/4" / 1-5/16"', ("Charcoal", '1-1/4"')),
    ('Tafisa Southwester_Fort (3/4) 1mm — 15/16" / 1"',
     ("Tafisa Southwester_Fort (3/4)", '15/16"')),
])
def test_a_tape_name_splits_into_its_colour_and_width(name: str, parts: tuple[str, str]) -> None:
    assert split_tape_name(name) == parts


@pytest.mark.parametrize("name", ["", "Tafisa", 'Tafisa 15/16" tape', "1mm — 15/16\"",
                                  'Tafisa 1mm - 15/16"', 'Tafisa 1mm — wide'])
def test_a_tape_name_not_in_the_measured_form_gives_none(name: str) -> None:
    assert split_tape_name(name) is None


# ----- real files


def _real_tracker() -> Path:
    folder = FIXTURES / "p1b" / "tracker"
    if not (folder / "tracker_snapshot_meta.csv").is_file():
        pytest.skip("no local tracker copy: run tools/collect_p1b_documents.py")
    return folder


@pytest.mark.fixtures
def test_the_real_snapshot_reads_with_its_rows_counted() -> None:
    snap = read_tracker(_real_tracker())
    # section 3.5's 101 sheet and 88 tape rows hold 2 and 3 VOID rows, which are not materials
    rows = (len(snap.sheets), len(snap.tape), len(snap.jobs))
    assert rows == (99, 85, 60)


@pytest.mark.fixtures
def test_the_real_pricing_has_its_keys_and_its_one_duplicate() -> None:
    snap = read_tracker(_real_tracker())
    counts = (len(snap.sheet_prices), len(snap.tape_prices), len(snap.keys_spelled_twice()),
              len(snap.keys_without_price()))
    # section 3.5 says 16 tape keys; 15 sit above the tape block's end
    assert counts == (25, 15, 1, 1)


@pytest.mark.fixtures
def test_the_real_job_summary_has_seven_status_spellings() -> None:
    spellings = len(read_tracker(_real_tracker()).status_spellings())
    assert spellings == 7


@pytest.mark.fixtures
def test_the_real_snapshot_has_no_customer_in_its_values() -> None:
    folder = _real_tracker()
    with (folder / "tracker_snapshot_JobSummary.csv").open(encoding="utf-8", newline="") as source:
        rows = list(csv.reader(source))
    at = next(i for i, row in enumerate(rows) if row and row[0] == "Job")
    customers = {row[1] for row in rows[at + 1:] if len(row) > 1 and len(row[1]) >= 4}
    text = repr(read_tracker(folder).jobs)
    # a job's own name holds its surname, so only what the job names don't already say counts
    jobs = {row[0].casefold() for row in rows[at + 1:] if row}
    extra = [c for c in customers
             if c in text and not any(c.casefold() in job for job in jobs)]
    found = len(extra)
    assert found == 0


@pytest.mark.fixtures
def test_the_two_inbox_archives_are_the_forms_they_are_named_for() -> None:
    folder = _real_tracker()
    index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    roles = {entry["role"]: entry["file"] for entry in index}
    with_header = (folder / roles["ingested_header"]).read_bytes()
    without = (folder / roles["ingested_no_header"]).read_bytes()
    assert with_header.lstrip(b"\xef\xbb\xbf").startswith(b"TAB,JOB")
    assert not without.lstrip(b"\xef\xbb\xbf").startswith(b"TAB,JOB")
