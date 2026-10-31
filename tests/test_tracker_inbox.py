"""
The tracker inbox (docs/p2/build-spec.md, contract 5.6): the rows a takeoff becomes, the file they
are written to, and whether they landed.

Synthetic takeoffs here have the shape of contract 5.3 and made-up jobs. Real files are read only
by the tests marked fixtures: the 21 archived inbox files and a snapshot of the tracker, copied by
tools/collect_p2_documents.py into .local-fixtures/p2/tracker, named by index, counted and never
printed.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ngrunner.guards.tracker import read_tracker
from ngrunner.messages.lint import check_line
from ngrunner.tracker_inbox import (
    HEADER,
    ROW_WIDTHS,
    InboxRefused,
    TrackerNames,
    inbox_bytes,
    inbox_rows,
    landed,
    write_inbox,
)
from conftest import FIXTURES, ROOT
from test_guards_tracker import job_row, sheet_row, tape_row, write_snapshot

JOB = "Testerson_101426"
OTHER = "Sampleton_101526"
IN_PROGRESS = "🔲 In progress"
WHITE_TAPE = 'Egger Classic White 1mm — 15/16" / 1"'
TAFISA_TAPE = 'Tafisa 1mm — 15/16" / 1"'
NAMES = {
    ("WH", 0.75): TrackerNames("Egger Classic White", WHITE_TAPE, "Egger Classic White"),
    ("WH", 0.25): TrackerNames("Egger Classic White (1/4) 5x9", "", ""),
    ("ORE", 0.75): TrackerNames("Tafisa Spring Blossom_Ore (3/4)", TAFISA_TAPE,
                                "Spring Blossom_L543 (Ore)"),
    ("WCH", 0.75): TrackerNames("Tafisa White Chocolate (3/4)", TAFISA_TAPE, "White Chocolate"),
}


def names(colour: str, thk_in: float) -> TrackerNames | None:
    return NAMES.get((colour, thk_in))


# ----- a takeoff in contract 5.3's shape


@dataclass(frozen=True)
class Bucket:
    colour: str
    thk_in: float
    parts: int
    sqft: float
    sheet_in: tuple[float, float] | None = (49.0, 97.0)
    sheets_with_waste: int | None = None
    calc_sheets_32: float = 0.0
    tape_lf: float = 0.0


@dataclass(frozen=True)
class Takeoff:
    buckets: tuple[Bucket, ...]
    board_parts: int = 0
    tape_lf: float = 0.0
    estimate_total: float | None = None
    gate_pulled_equals_designs: bool = True
    unplaced: tuple[object, ...] = ()
    branch: str = "closet"


def bucket(colour: str = "WH", thk: float = 0.75, sqft: float = 262.4,
           tape: float = 376.2) -> Bucket:
    return Bucket(colour, thk, 40, sqft, calc_sheets_32=sqft / 32, tape_lf=tape,
                  sheets_with_waste=99)       # never the tracker's number: a trap


TAKEOFF = Takeoff((bucket(), bucket(thk=0.25, sqft=7.16, tape=0.0)))


def rows_of(**kw) -> list[list[str]]:
    kw.setdefault("verb", "append")
    kw.setdefault("names", names)
    kw.setdefault("status", IN_PROGRESS)
    return inbox_rows(kw.pop("job", JOB), kw.pop("takeoff", TAKEOFF), **kw)


# ----- the rows


def test_the_converters_own_takeoff_gives_the_same_rows() -> None:
    # Contract 5.3's types, from ngrunner.convert, built by keyword so a renamed field fails here.
    convert = pytest.importorskip("ngrunner.convert.takeoff")
    made = [convert.Bucket(colour=b.colour, thk_in=b.thk_in, parts=b.parts, sqft=b.sqft,
                           sheet_in=b.sheet_in, sheets_with_waste=b.sheets_with_waste,
                           calc_sheets_32=b.calc_sheets_32, tape_lf=b.tape_lf)
            for b in TAKEOFF.buckets]
    takeoff = convert.Takeoff(buckets=tuple(made), board_parts=80, tape_lf=376.2,
                              estimate_total=None, gate_pulled_equals_designs=True, unplaced=(),
                              branch="closet")
    assert rows_of(takeoff=takeoff) == rows_of()


def test_the_header_is_always_the_first_row_as_build_gives_it() -> None:
    assert HEADER == ("TAB", "JOB", "FIELD1", "FIELD2", "FIELD3", "FIELD4", "FIELD5", "FIELD6")
    for verb in ("append", "set"):
        assert rows_of(verb=verb)[0] == list(HEADER)


def test_append_rows_carry_the_calc_numbers_with_the_optimizer_left_blank() -> None:
    rows = rows_of()
    assert rows[1:] == [
        ["SHEETGOODS", JOB, "", "Egger Classic White", "24.3778", "262.40", "8.20", ""],
        ["SHEETGOODS", JOB, "", "Egger Classic White (1/4) 5x9", "0.6652", "7.16", "0.22", ""],
        ["EDGEBAND", JOB, "", WHITE_TAPE, "376.2", "", "Egger Classic White"],
        ["JOBSUMMARY", JOB, "", IN_PROGRESS, ""],
    ]


def test_set_rows_have_no_customer_column() -> None:
    rows = rows_of(verb="set")
    assert rows[1:] == [
        ["SHEETGOODS_SET", JOB, "Egger Classic White", "24.3778", "262.40", "8.20", ""],
        ["SHEETGOODS_SET", JOB, "Egger Classic White (1/4) 5x9", "0.6652", "7.16", "0.22", ""],
        ["EDGEBAND_SET", JOB, WHITE_TAPE, "376.2", "", "Egger Classic White"],
        ["JOBSUMMARY_SET", JOB, IN_PROGRESS, ""],
    ]


def test_each_verb_has_builds_column_count() -> None:
    assert ROW_WIDTHS == {"SHEETGOODS": 8, "EDGEBAND": 7, "JOBSUMMARY": 5,
                          "SHEETGOODS_SET": 7, "EDGEBAND_SET": 6, "JOBSUMMARY_SET": 4}
    for verb in ("append", "set"):
        for row in rows_of(verb=verb)[1:]:
            assert len(row) == ROW_WIDTHS[row[0]]


def test_calc_sheets_is_area_over_32_never_the_sheets_with_waste() -> None:
    sheet = rows_of()[1]
    assert sheet[6] == "8.20"          # 262.4 / 32, as the tracker's Calc Sheets holds (R6)
    assert "99" not in sheet


def test_two_buckets_with_one_tracker_name_make_one_row_with_their_sums() -> None:
    both = dict(NAMES)
    both[("WH", 1.0)] = NAMES[("WH", 0.75)]
    takeoff = Takeoff((bucket(sqft=100.0, tape=50.0), bucket(thk=1.0, sqft=60.0, tape=20.0)))
    rows = inbox_rows(JOB, takeoff, verb="append", names=lambda c, t: both.get((c, t)),
                      status=IN_PROGRESS)
    sheets = [r for r in rows if r[0] == "SHEETGOODS"]
    assert sheets == [["SHEETGOODS", JOB, "", "Egger Classic White", "14.8645", "160.00", "5.00",
                       ""]]
    assert [r[4] for r in rows if r[0] == "EDGEBAND"] == ["70.0"]


def test_a_bucket_with_no_tape_gives_no_tape_row() -> None:
    takeoff = Takeoff((bucket(tape=0.0),))
    assert [r[0] for r in rows_of(takeoff=takeoff)[1:]] == ["SHEETGOODS", "JOBSUMMARY"]


def test_two_colours_sharing_one_tape_name_are_refused() -> None:
    # The ingest's _SET finds a row by job and material, so two rows with one name can never be
    # set: the second colour would have to be guessed.
    takeoff = Takeoff((bucket("ORE"), bucket("WCH")))
    with pytest.raises(InboxRefused) as caught:
        rows_of(takeoff=takeoff)
    assert "tape" in str(caught.value)


def test_a_bucket_with_no_tracker_name_is_refused_never_guessed() -> None:
    with pytest.raises(InboxRefused) as caught:
        rows_of(takeoff=Takeoff((bucket("PEW"),)))
    assert "PEW" in str(caught.value)
    assert check_line(str(caught.value)) == []


def test_a_bucket_with_tape_but_no_tape_name_is_refused() -> None:
    with pytest.raises(InboxRefused):
        rows_of(takeoff=Takeoff((bucket(thk=0.25, tape=12.0),)))


def test_a_takeoff_with_lines_it_could_not_place_is_refused() -> None:
    with pytest.raises(InboxRefused) as caught:
        rows_of(takeoff=Takeoff((bucket(),), unplaced=("a line", "another")))
    assert "2 lines" in str(caught.value)
    assert check_line(str(caught.value)) == []


def test_a_takeoff_with_no_board_is_refused() -> None:
    with pytest.raises(InboxRefused):
        rows_of(takeoff=Takeoff(()))


def test_optimizer_numbers_fill_the_optimizer_columns() -> None:
    rows = rows_of(verb="set", status="", optimizer={"Egger Classic White": 9, WHITE_TAPE: 376.2})
    assert rows[1][6] == "9"
    assert rows[3][4] == "376.2"
    assert rows[2][6] == ""            # no nest number given: the ingest leaves that cell alone


def test_an_optimizer_number_for_a_name_not_in_the_takeoff_is_refused() -> None:
    with pytest.raises(InboxRefused):
        rows_of(optimizer={"Egger Classic Whte": 9})


def test_append_needs_a_status_and_set_without_one_leaves_the_job_row_alone() -> None:
    with pytest.raises(InboxRefused):
        rows_of(status="")
    assert "JOBSUMMARY_SET" not in [r[0] for r in rows_of(verb="set", status="")]


@pytest.mark.parametrize("job", ["", "  ", "Testerson_101426\nSHEETGOODS"])
def test_a_job_that_is_not_one_line_is_refused(job: str) -> None:
    with pytest.raises(InboxRefused):
        rows_of(job=job)


def test_an_unknown_verb_is_refused() -> None:
    with pytest.raises(InboxRefused):
        rows_of(verb="add")


# ----- client data never goes in

CLIENT = "Fakeclient Residence"
ADDRESS = "123 Fake Street, Sometown"
EMAIL = "fake.client@example.com"


@dataclass(frozen=True)
class TakeoffWithClient(Takeoff):
    client: str = CLIENT
    customer: str = CLIENT
    address: str = ADDRESS
    email: str = EMAIL
    site: str = ADDRESS
    notes: list[str] = field(default_factory=lambda: [ADDRESS])


@pytest.mark.parametrize("verb", ["append", "set"])
def test_no_client_field_reaches_a_row_and_the_customer_column_is_blank(verb: str) -> None:
    rows = rows_of(verb=verb, takeoff=TakeoffWithClient(TAKEOFF.buckets))
    data = inbox_bytes(rows).decode("utf-8")
    for text in (CLIENT, ADDRESS, EMAIL, "Fakeclient"):
        assert text not in data
    for row in rows[1:]:
        if not row[0].endswith("_SET"):
            assert row[2] == ""        # BUILD's customer column, left blank on purpose


# ----- the file's bytes


def test_the_bytes_are_utf8_with_no_bom_crlf_and_a_final_crlf() -> None:
    data = inbox_bytes(rows_of())
    assert not data.startswith(b"\xef\xbb\xbf")
    assert data.startswith(b"TAB,JOB,FIELD1,")
    assert data.endswith(b"\r\n")
    assert data.count(b"\n") == data.count(b"\r\n") == 5
    assert b'"Egger Classic White 1mm \xe2\x80\x94 15/16"" / 1"""' in data    # quotes doubled
    assert list(csv.reader(io.StringIO(data.decode("utf-8"), newline=""))) == rows_of()


# ----- writing: only to the path a caller passes

_WRITE_EVENTS = {"os.rename", "os.replace", "os.remove", "os.rmdir", "os.mkdir", "os.link",
                 "os.symlink", "os.truncate", "shutil.copyfile", "shutil.move", "shutil.rmtree"}
_RECORD: list[tuple[str, str]] | None = None


def _audit(event: str, args: tuple) -> None:
    if _RECORD is None:
        return
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        mode, flags = args[1], args[2]
        writing = (any(ch in mode for ch in "wax+") if isinstance(mode, str)
                   else bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND)))
        if writing:
            _RECORD.append(("open", os.fsdecode(args[0])))
    elif event in _WRITE_EVENTS:
        _RECORD.append((event, os.fsdecode(args[0]) if args else ""))


sys.addaudithook(_audit)


def writes_while(action) -> list[tuple[str, str]]:
    """Every write the process makes while action runs, as (event, path)."""
    global _RECORD
    _RECORD = []
    try:
        action()
    finally:
        found, _RECORD = _RECORD, None
    return found


def _tree(folder: Path) -> set[str]:
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*")}


def test_write_inbox_writes_its_bytes_to_the_path_and_nowhere_else(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    (sandbox / "out").mkdir(parents=True)
    path = sandbox / "out" / "tracker_inbox_testbatch.csv"
    rows = rows_of()
    writes = writes_while(lambda: write_inbox(path, rows))
    assert writes == [("open", str(path))]
    assert _tree(sandbox) == {"out", "out/tracker_inbox_testbatch.csv"}
    assert path.read_bytes() == inbox_bytes(rows)


def test_write_inbox_never_writes_over_a_file_already_there(tmp_path: Path) -> None:
    path = tmp_path / "tracker_inbox.csv"
    path.write_bytes(b"someone else's batch")
    with pytest.raises(InboxRefused):
        write_inbox(path, rows_of())
    assert path.read_bytes() == b"someone else's batch"


@pytest.mark.parametrize("folder", ["NG Conversion program Claude", "ng conversion PROGRAM claude"])
def test_write_inbox_refuses_the_trackers_own_folder_in_p2(tmp_path: Path, folder: str) -> None:
    target = tmp_path / folder / "inbox"
    target.mkdir(parents=True)
    writes = writes_while(lambda: pytest.raises(InboxRefused, write_inbox,
                                                 target / "tracker_inbox.csv", rows_of()))
    assert writes == []
    assert _tree(tmp_path / folder) == {"inbox"}


def test_write_inbox_refuses_a_relative_path(tmp_path: Path) -> None:
    with pytest.raises(InboxRefused):
        write_inbox(Path("tracker_inbox.csv"), rows_of())


def test_write_inbox_does_not_make_a_missing_folder(tmp_path: Path) -> None:
    writes = writes_while(lambda: pytest.raises(
        InboxRefused, write_inbox, tmp_path / "nowhere" / "tracker_inbox.csv", rows_of()))
    assert not (tmp_path / "nowhere").exists()
    assert all(event == "open" for event, _ in writes)     # the attempt itself, nothing made


@pytest.mark.parametrize("rows", [
    [["SHEETGOODS", JOB, "", "Egger Classic White", "1", "2", "3", ""]],      # no header
    [list(HEADER), ["SHEETGOODS", JOB, "", "Egger Classic White", "1"]],     # too short
    [list(HEADER), ["SHEETGOOD", JOB, "", "Egger Classic White", "1", "2", "3", ""]],
    [list(HEADER)],                                                          # nothing to apply
])
def test_write_inbox_refuses_rows_the_ingest_would_lose_or_skip(tmp_path: Path, rows) -> None:
    with pytest.raises(InboxRefused):
        write_inbox(tmp_path / "tracker_inbox.csv", rows)
    assert not (tmp_path / "tracker_inbox.csv").exists()


# ----- did it land

DROPPED = datetime(2026, 10, 30, 23, 40, tzinfo=timezone.utc)      # before the snapshot's 23:51


def _snap(tmp_path: Path, **kw):
    return read_tracker(write_snapshot(tmp_path, **kw))


def _landed_snapshot(tmp_path: Path):
    return _snap(tmp_path, sheets=[
        sheet_row(material="Egger Classic White", calc="8.2", optimizer=""),
        sheet_row(material="Egger Classic White (1/4) 5x9", calc="0.2", optimizer=""),
        sheet_row(job=OTHER)],
        tape=[tape_row(material=WHITE_TAPE, calc="376.2", optimizer="")],
        jobs=[job_row(status=IN_PROGRESS), job_row(job=OTHER)])


def test_rows_found_with_their_numbers_have_landed(tmp_path: Path) -> None:
    result = landed(JOB, rows_of(), _landed_snapshot(tmp_path), dropped=DROPPED)
    assert [(r.tab, r.state) for r in result.rows] == [
        ("SHEETGOODS", "landed"), ("SHEETGOODS", "landed"), ("EDGEBAND", "landed"),
        ("JOBSUMMARY", "landed")]
    assert result.all_landed
    assert not result.early


def test_numbers_are_compared_as_the_tracker_shows_them(tmp_path: Path) -> None:
    # The snapshot holds display values: 8.20 shows as 8.2 and 0.22 as 0.2, so a number within
    # half its last shown place is the same number. 8.3 is not.
    snap = _snap(tmp_path, sheets=[sheet_row(material="Egger Classic White", calc="8.3")])
    rows = [list(HEADER), ["SHEETGOODS", JOB, "", "Egger Classic White", "1", "2", "8.20", ""]]
    (row,) = landed(JOB, rows, snap, dropped=DROPPED).rows
    assert row.state == "differs"
    assert "8.2" in row.why and "8.3" in row.why
    assert check_line(row.why) == []


def test_a_row_not_on_the_tracker_is_missing(tmp_path: Path) -> None:
    snap = _snap(tmp_path, sheets=[sheet_row(job=OTHER)], tape=[tape_row(job=OTHER)],
                 jobs=[job_row(job=OTHER)])
    result = landed(JOB, rows_of(), snap, dropped=DROPPED)
    assert {r.state for r in result.rows} == {"missing"}
    assert not result.all_landed
    assert all(check_line(r.why) == [] for r in result.rows)


def test_two_rows_for_one_job_and_material_differ(tmp_path: Path) -> None:
    snap = _snap(tmp_path, sheets=[sheet_row(material="Egger Classic White", calc="8.2")] * 2)
    rows = [list(HEADER), ["SHEETGOODS", JOB, "", "Egger Classic White", "1", "2", "8.20", ""]]
    (row,) = landed(JOB, rows, snap, dropped=DROPPED).rows
    assert row.state == "differs"
    assert "2 rows" in row.why


def test_material_and_job_are_matched_with_their_edges_trimmed_as_the_ingest_does(
        tmp_path: Path) -> None:
    snap = _snap(tmp_path, sheets=[sheet_row(job=JOB + " ", material="Egger Classic White ",
                                             calc="8.2")])
    rows = [list(HEADER), ["SHEETGOODS", JOB, "", "Egger Classic White", "1", "2", "8.20", ""]]
    assert landed(JOB, rows, snap, dropped=DROPPED).rows[0].state == "landed"


def test_a_set_row_is_judged_only_on_the_fields_it_gave(tmp_path: Path) -> None:
    snap = _snap(tmp_path, sheets=[sheet_row(material="Egger Classic White", calc="5.1",
                                             optimizer="9")])
    rows = [list(HEADER), ["SHEETGOODS_SET", JOB, "Egger Classic White", "", "", "", "9"]]
    assert landed(JOB, rows, snap, dropped=DROPPED).rows[0].state == "landed"
    rows[1][6] = "10"
    assert landed(JOB, rows, snap, dropped=DROPPED).rows[0].state == "differs"


def test_a_job_status_is_compared_as_typed_apart_from_spacing(tmp_path: Path) -> None:
    snap = _snap(tmp_path, jobs=[job_row(status="🔒  LOCKED ")])
    rows = [list(HEADER), ["JOBSUMMARY_SET", JOB, "🔒 LOCKED", ""]]
    assert landed(JOB, rows, snap, dropped=DROPPED).rows[0].state == "landed"
    rows[1][2] = "LOCKED"
    assert landed(JOB, rows, snap, dropped=DROPPED).rows[0].state == "differs"


def test_only_the_jobs_own_rows_are_judged_and_the_header_is_skipped(tmp_path: Path) -> None:
    rows = rows_of() + [["SHEETGOODS", OTHER, "", "Egger Classic White", "1", "2", "3", ""]]
    result = landed(JOB, rows, _landed_snapshot(tmp_path), dropped=DROPPED)
    assert [r.line for r in result.rows] == [2, 3, 4, 5]


def test_a_row_with_a_verb_the_ingest_does_not_know_differs(tmp_path: Path) -> None:
    rows = [list(HEADER), ["SHEETGOOD", JOB, "", "Egger Classic White", "1", "2", "8.2", ""]]
    (row,) = landed(JOB, rows, _landed_snapshot(tmp_path), dropped=DROPPED).rows
    assert row.state == "differs"
    assert check_line(row.why) == []


def test_a_snapshot_taken_before_the_drop_judges_nothing(tmp_path: Path) -> None:
    # It can't show rows that were not there yet, and calling them missing would invite a second
    # drop: two rows for one material, which the tracker then counts twice.
    result = landed(JOB, rows_of(), _landed_snapshot(tmp_path),
                    dropped=datetime(2026, 10, 31, 0, 0, tzinfo=timezone.utc))
    assert result.early
    assert result.rows == ()
    assert not result.all_landed


def test_a_naive_drop_time_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        landed(JOB, rows_of(), _landed_snapshot(tmp_path), dropped=datetime(2026, 10, 30))


# ----- the collector


_spec = importlib.util.spec_from_file_location(
    "ngr_tools_collect_p2", ROOT / "tools" / "collect_p2_documents.py")
collect = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = collect
_spec.loader.exec_module(collect)


def test_the_collector_copies_archives_by_index_and_the_snapshot_by_name(tmp_path: Path) -> None:
    source = tmp_path / "NG Conversion program Claude"
    source.mkdir()
    for stamp in ("20261020-090844", "20260929-102343"):
        (source / f"ingested_tracker_inbox_{stamp}.csv").write_bytes(b"TAB,JOB\r\n")
    (source / "SKIPPED_tracker_inbox_20261001-000000.csv").write_bytes(b"x")
    for tab in ("SheetGoods", "Edgeband", "JobSummary", "Pricing", "meta"):
        (source / f"tracker_snapshot_{tab}.csv").write_bytes(b"x")
    dest = tmp_path / "dest"
    collect.run_copies(collect.plan_copies(source), dest, refresh=False)
    assert sorted(p.name for p in (dest / "inbox").iterdir()) == ["00.csv", "01.csv", "index.json"]
    index = json.loads((dest / "inbox" / "index.json").read_text(encoding="utf-8"))
    assert [e["archived"] for e in index] == ["ingested_tracker_inbox_20260929-102343.csv",
                                              "ingested_tracker_inbox_20261020-090844.csv"]
    assert len(list((dest / "snapshot").iterdir())) == 5


def test_the_collector_refuses_a_destination_inside_drive(tmp_path: Path, capsys) -> None:
    source = tmp_path / "CNC operations"
    assert collect.main(["--cnc", str(source), "--dest", str(source / "x")]) == 1
    assert "inside the Drive folder" in capsys.readouterr().err


# ----- real files

REAL = FIXTURES / "p2" / "tracker"


def _archives() -> list[bytes]:
    folder = REAL / "inbox"
    if not (folder / "index.json").is_file():
        pytest.skip("no local tracker inbox copies: run tools/collect_p2_documents.py")
    return [path.read_bytes() for path in sorted(folder.glob("[0-9][0-9].csv"))]


def _parse(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))


@pytest.mark.fixtures
def test_the_real_archives_are_utf8_with_no_bom_and_most_have_builds_header() -> None:
    archives = _archives()
    assert len(archives) == 21
    assert not any(data.startswith(b"\xef\xbb\xbf") for data in archives)
    firsts = [tuple(_parse(data)[0]) for data in archives]
    assert sum(first[:2] == ("TAB", "JOB") for first in firsts) == 16
    assert firsts.count(HEADER) == 15       # and 1 older header with a CUSTOMER column
    # 5 have no header, so the ingest lost their first directive (BUILD 817-837)
    assert sum(first[:2] != ("TAB", "JOB") for first in firsts) == 5


@pytest.mark.fixtures
def test_every_real_row_has_the_verb_and_column_count_the_writer_uses() -> None:
    rows = [row for data in _archives() for row in _parse(data) if row[:2] != ["TAB", "JOB"]]
    assert len(rows) == 100
    assert all(len(row) == ROW_WIDTHS[row[0]] for row in rows)
    assert {row[0] for row in rows} == set(ROW_WIDTHS)


@pytest.mark.fixtures
def test_the_writer_gives_the_real_archives_bytes_back() -> None:
    # 14 are CRLF with a final CRLF, as Python's csv writes them: the same bytes exactly. 7 of
    # the early ones (09-29 to 10-07) are LF, the chat's older form: 4 of them are the same
    # bytes with LF for CRLF, and the first day's 3 leave a tape name's inch marks unquoted,
    # which the writer quotes.
    archives = _archives()
    crlf = [data for data in archives if b"\r\n" in data]
    assert len(crlf) == 14
    assert all(inbox_bytes(_parse(data)) == data for data in crlf)
    older = [data for data in archives if b"\r\n" not in data]
    assert len(older) == 7
    assert all(data.endswith(b"\n") for data in older)
    same = [inbox_bytes(_parse(data)) == data.replace(b"\n", b"\r\n") for data in older]
    assert same == [False, False, False, True, True, True, True]
    assert all(_parse(inbox_bytes(_parse(data)).decode("utf-8").encode("utf-8")) == _parse(data)
               for data in older)


@pytest.mark.fixtures
def test_the_real_archives_rows_measured_against_a_frozen_snapshot() -> None:
    folder = REAL / "snapshot"
    if not (folder / "tracker_snapshot_meta.csv").is_file():
        pytest.skip("no local tracker snapshot copy: run tools/collect_p2_documents.py")
    snap = read_tracker(folder)
    dropped = datetime(2026, 10, 21, tzinfo=timezone.utc)      # after the last archive
    counts = {"landed": 0, "missing": 0, "differs": 0}
    newest: dict[tuple[str, str, str], str] = {}
    for data in _archives():
        rows = _parse(data)
        for job in dict.fromkeys(row[1] for row in rows if row[:2] != ["TAB", "JOB"]):
            result = landed(job, rows, snap, dropped=dropped)
            assert not result.early
            for row in result.rows:
                counts[row.state] += 1
                newest[(job, row.tab.removesuffix("_SET"), row.material)] = row.state
                assert check_line(row.why or "x") == []
    assert counts == MEASURED_ROWS
    assert _tally(newest.values()) == MEASURED_NEWEST


def _tally(states) -> dict[str, int]:
    out = {"landed": 0, "missing": 0, "differs": 0}
    for state in states:
        out[state] += 1
    return out


# Measured 2026-10-31 on the snapshot taken 2026-10-31 09:21 UTC. Of the 100 rows, the 18 that
# differ are rows a later batch or Jim corrected; the 10 missing are the ingest's own smoke test
# (deleted, as its note asked) and rows since renamed. Taking each job and material's newest
# row only, 58 landed, 7 are missing and 1 differs.
MEASURED_ROWS = {"landed": 72, "missing": 10, "differs": 18}
MEASURED_NEWEST = {"landed": 58, "missing": 7, "differs": 1}
