"""
The labels-to-design fallback (docs/p1b/build-spec.md, sections 3.2 and 5.9).

It is one direction only and date-only, so it can never set Nest checked or raise DO NOT CUT:
some_missing reads "could not verify". Real label files are read only by the tests marked
fixtures, by case letter, and report counts, never names.
"""
from __future__ import annotations

import ast
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from conftest import FIXTURES
from helpers_pdf import _Page, _write_pdf
from ngrunner.guards.labels import (
    LabelsMatch,
    LabelPart,
    Piece,
    catalog_date,
    catalog_pieces,
    labels_in_design,
    newest_per_material,
    read_catalog,
    read_labels,
)
from ngrunner.messages.lint import check_line

JOB = "Testerson_101426"
HEAD = f"{JOB} Next GenCab Co"
DESIGN = ("Testerson - ", "Primary ", "Closet")


def page(*lines: str) -> _Page:
    made = _Page(288, 144)
    for i, line in enumerate(lines):
        made.text(10, 130 - 10 * i, 8, line)
    return made


def label_page(part: str = "R1C3", size: str = "FEnd (L) 19 1/16 x 83 15/16",
               material: str = "3-4 Egger Classic White Custom*", sheet: str = "1",
               number: str = "3") -> _Page:
    return page(HEAD, *DESIGN, part, size, material, sheet, f"{number}Part #", "Sheet #")


def remnant_page() -> _Page:
    return page(HEAD, "Remnant 29 13/16 x 108 1/8", "1-4 Egger Classic White NONE", "1", "1Part #",
                "Sheet #")


def pdf(*pages: _Page) -> bytes:
    return _write_pdf(list(pages), {})


def label(part: str = "R1C3", name: str = "FEnd (L)", w: float = 19.0625, l: float = 83.9375,
          material: str = "3-4 Egger Classic White") -> LabelPart:
    return LabelPart(part=part, name=name, w_in=w, l_in=l, material=material, sheet="1")


def piece(part: str = "R1C3", name: str = "FEnd (L)", w: float = 19.0625,
          l: float = 83.9375) -> Piece:
    return Piece(part=part, name=name, w_in=w, l_in=l, qty=1)


def catalog_row(job: str = JOB, part: str = "R1C3", pieces: str = "FEnd (L)~484.2~2132~1",
                modified: str = "2026-10-20") -> dict[str, str]:
    room = part[1:part.index("C")]
    return {"Job": job, "Room": room, "RoomName": "Testerson secret room", "Part": part,
            "Product": "NG Tower", "W": "635", "H": "96", "D": "523.4", "Wall": "1_1",
            "Pieces": pieces, "JobModified": modified}


# ----- reading labels


def test_a_label_page_gives_its_part_size_material_and_sheet() -> None:
    assert read_labels(pdf(label_page())) == (
        LabelPart(part="R1C3", name="FEnd (L)", w_in=19.0625, l_in=83.9375,
                  material="3-4 Egger Classic White", sheet="1"),)


def test_sizes_read_whole_mixed_and_fractional() -> None:
    parts = read_labels(pdf(label_page(size="WM Panel Rad R 14 x 60"),
                            label_page(size="Filler 3/4 x 96"),
                            label_page(size="Sleeper 10 3 3/4 x 19")))
    assert [(p.name, p.w_in, p.l_in) for p in parts] == [
        ("WM Panel Rad R", 14.0, 60.0), ("Filler", 0.75, 96.0), ("Sleeper 10", 3.75, 19.0)]


def test_the_edgeband_word_is_not_part_of_the_material() -> None:
    parts = read_labels(pdf(label_page(material="3-4 Egger Classic White A-3,3,3,3"),
                            label_page(material="1-4 Egger Classic White None"),
                            label_page(material="1 Am Lam Wh Custom*")))
    assert [p.material for p in parts] == ["3-4 Egger Classic White", "1-4 Egger Classic White",
                                           "1 Am Lam Wh"]


def test_a_remnant_page_is_an_offcut_not_a_part() -> None:
    assert len(read_labels(pdf(label_page(), remnant_page()))) == 1


def test_room_0_labels_read_with_or_without_a_count() -> None:
    parts = read_labels(pdf(label_page(part="R0N1", size="Base 4 x 96"),
                            label_page(part=r"R0N3(1\14)", size="Hang Rail 4 1/8 x 96")))
    assert [(p.part, p.name) for p in parts] == [("R0N1", "Base"), ("R0N3", "Hang Rail")]


def test_a_design_name_holding_a_comma_or_digit_is_not_read_as_the_part() -> None:
    parts = read_labels(pdf(page(HEAD, "Testerson - Sample's ", "Closet 2", "R4C7",
                                 "Top 14 x 23 7/8", "3-4 Egger Classic White A-1,0,0,0", "11",
                                 "76Part #", "Sheet #")))
    assert [(p.part, p.name) for p in parts] == [("R4C7", "Top")]


def test_no_design_name_is_kept_on_a_label() -> None:
    assert "Testerson" not in repr(read_labels(pdf(label_page())))


@pytest.mark.parametrize("bad", [
    page(HEAD, "Testerson - Primary", "FEnd (L) 19 1/16 x 83 15/16", "3-4 Egger Classic White None",
         "1", "3Part #", "Sheet #"),                        # no R#C#
    label_page(size="FEnd (L) 19.06 x 83.94"),              # a decimal size
    label_page(size="FEnd (L) 484 x 2,132"),                # a metric one with a comma
    label_page(size="just words"),
    page(HEAD, "R1C3"),                                     # cut short
    page(HEAD, "R1C3", "R1C4", "FEnd (L) 19 1/16 x 83 15/16", "3-4 Egger Classic White None", "1",
         "3Part #", "Sheet #"),                             # two parts on one page
    page(HEAD, "R1C3", "FEnd (L) 19 1/16 x 83 15/16", "3-4 Egger Classic White None", "1",
         "3Part #", "Sheet #", "R1C4", "Top 14 x 30", "3-4 Egger Classic White None", "2",
         "4Part #", "Sheet #"),                             # two whole labels: one would be lost
    page(),
])
def test_one_unreadable_page_gives_nothing_never_a_partial_list(bad: _Page) -> None:
    assert read_labels(pdf(label_page(), bad)) == ()


@pytest.mark.parametrize("data", [b"", b"not a pdf", b"%PDF-1.4 and then nothing"])
def test_bytes_that_are_no_pdf_give_nothing(data: bytes) -> None:
    assert read_labels(data) == ()


def test_a_file_of_only_offcuts_gives_nothing() -> None:
    assert read_labels(pdf(remnant_page())) == ()


# ----- the catalog


def test_catalog_pieces_read_name_sizes_and_quantity_in_inches() -> None:
    rows = [catalog_row(pieces="FEnd (L)~484.2~2132~1 | Top~355.6~762~2")]
    assert catalog_pieces(rows, JOB) == (
        Piece(part="R1C3", name="FEnd (L)", w_in=484.2 / 25.4, l_in=2132 / 25.4, qty=1),
        Piece(part="R1C3", name="Top", w_in=355.6 / 25.4, l_in=30.0, qty=2),
    )


def test_catalog_pieces_are_only_the_jobs_own() -> None:
    rows = [catalog_row(), catalog_row(job="Sampleton_101526", part="R9C9")]
    assert {p.part for p in catalog_pieces(rows, JOB)} == {"R1C3"}


def test_a_catalog_job_name_is_compared_exactly() -> None:
    assert catalog_pieces([catalog_row(job=f"{JOB}_Gar")], JOB) == ()


def test_a_piece_name_holding_a_tilde_still_reads() -> None:
    rows = [catalog_row(pieces="Top~old~355.6~762~1")]
    assert catalog_pieces(rows, JOB)[0].name == "Top~old"


@pytest.mark.parametrize("pieces", ["", "  ", "garbage", "Top~wide~762~1", "Top~355.6~762"])
def test_a_piece_that_can_not_be_read_gives_no_piece_never_a_guessed_one(pieces: str) -> None:
    assert catalog_pieces([catalog_row(pieces=pieces)], JOB) == ()


def test_no_room_name_travels() -> None:
    assert "secret" not in repr(catalog_pieces([catalog_row()], JOB))


def test_read_catalog_handles_the_byte_order_mark_and_new_lines_in_cells() -> None:
    data = ('﻿Job,Room,RoomName,Part,Product,W,H,D,Wall,Pieces,JobModified\r\n'
            f'{JOB},1,"Room\n1",R1C3,NG,1,1,1,1_1,Top~355.6~762~1,2026-10-20\r\n').encode("utf-8")
    assert [row["Part"] for row in read_catalog(data)] == ["R1C3"]
    assert read_catalog(data)[0]["Job"] == JOB


@pytest.mark.parametrize("data", [b"\xff\xfe\x00", b"", b"Job,Other\r\nx,y\r\n"])
def test_a_catalog_not_as_measured_gives_no_rows(data: bytes) -> None:
    assert read_catalog(data) == ()


def test_the_design_date_is_the_jobs_newest_date() -> None:
    rows = [catalog_row(modified="2026-10-18"), catalog_row(modified="2026-10-20"),
            catalog_row(job="Sampleton_101526", modified="2026-10-29")]
    assert catalog_date(rows, JOB) == date(2026, 10, 20)


def test_no_date_when_the_job_has_no_row_or_no_readable_date() -> None:
    assert catalog_date([], JOB) is None
    assert catalog_date([catalog_row(job="Sampleton_101526")], JOB) is None
    assert catalog_date([catalog_row(modified="soon")], JOB) is None


# ----- the match


def test_every_label_in_the_catalog_is_all_found() -> None:
    match = labels_in_design((label(),), (piece(),))
    assert match.code == "all_found"
    assert match.missing == ()
    assert match.compared == 1


def test_a_label_matches_with_width_and_length_either_way_round() -> None:
    assert labels_in_design((label(w=83.9375, l=19.0625),), (piece(),)).code == "all_found"


def test_a_label_within_an_eighth_of_an_inch_is_found() -> None:
    assert labels_in_design((label(w=19.0625 + 0.125),), (piece(),)).code == "all_found"


def test_a_label_past_an_eighth_of_an_inch_is_missing() -> None:
    assert labels_in_design((label(w=19.0625 + 0.1875),), (piece(),)).code == "some_missing"


def test_the_length_is_held_to_the_same_eighth() -> None:
    assert labels_in_design((label(l=83.9375 + 0.1875),), (piece(),)).code == "some_missing"


def test_catalog_millimetres_are_compared_to_the_sixteenth() -> None:
    pieces = catalog_pieces([catalog_row(pieces="FEnd (L)~484.2~2132~1")], JOB)
    assert labels_in_design((label(),), pieces).code == "all_found"


def test_the_same_size_on_another_part_is_missing() -> None:
    match = labels_in_design((label(part="R1C4"),), (piece(part="R1C3"),))
    assert match.code == "some_missing"
    assert match.missing == ("R1C4 FEnd (L)",)


def test_a_removed_label_from_the_design_is_missing() -> None:
    labels = (label(), label(part="R2C1", name="Top", w=14.0, l=30.0))
    assert labels_in_design(labels, (piece(),)).code == "some_missing"


def test_the_other_direction_is_not_checked_since_drawer_boxes_are_never_nested() -> None:
    pieces = (piece(), piece(part="R9C9", name="Drawer Box"))
    assert labels_in_design((label(),), pieces).code == "all_found"


def test_room_0_is_compared_by_name_and_size_as_r0n_and_r0c_differ() -> None:
    match = labels_in_design((label(part="R0N3", name="Base", w=4.0, l=96.0),),
                             (piece(part="R0C7", name="Base", w=4.0, l=96.0),))
    assert match.code == "all_found"


def test_room_0_with_another_name_is_missing() -> None:
    match = labels_in_design((label(part="R0N3", name="Base", w=4.0, l=96.0),),
                             (piece(part="R0C7", name="Hook Board", w=4.0, l=96.0),))
    assert match.code == "some_missing"


def test_room_0_with_another_size_is_missing() -> None:
    match = labels_in_design((label(part="R0N3", name="Base", w=4.0, l=96.0),),
                             (piece(part="R0C7", name="Base", w=4.0, l=48.0),))
    assert match.code == "some_missing"


def test_room_0_names_compare_only_inside_room_0() -> None:
    match = labels_in_design((label(part="R0N3", name="Base", w=4.0, l=96.0),),
                             (piece(part="R1C7", name="Base", w=4.0, l=96.0),))
    assert match.code == "some_missing"


def test_a_room_0_label_with_no_room_0_in_the_catalog_is_missing_not_skipped() -> None:
    match = labels_in_design((label(part="R0N1", name="Base", w=4.0, l=96.0), label()), (piece(),))
    assert match.code == "some_missing"
    assert match.missing == ("R0N1 Base",)


def test_a_counter_top_has_nothing_to_compare_with_so_it_is_counted_apart() -> None:
    # the catalog lists cabinets only (R#C#), so R8N0 can't be in it, whatever the design holds
    match = labels_in_design((label(), label(part="R8N0", name="Counter Top", w=15.5, l=29.875)),
                             (piece(),))
    assert match.code == "all_found"
    assert (match.compared, match.not_compared) == (1, 1)
    assert "1" in match.note and "not compared" in match.note


def test_only_labels_nothing_can_compare_could_not_be_read_as_a_match() -> None:
    match = labels_in_design((label(part="R8N0", name="Counter Top"),), (piece(),))
    assert match.code == "could_not_read"


def test_no_labels_could_not_be_read() -> None:
    assert labels_in_design((), (piece(),)).code == "could_not_read"


def test_no_catalog_pieces_could_not_be_read() -> None:
    assert labels_in_design((label(),), ()).code == "could_not_read"


def test_a_file_with_no_part_numbers_gives_could_not_read() -> None:
    bad = page(HEAD, "Testerson - Primary", "FEnd (L) 19 1/16 x 83 15/16",
               "3-4 Egger Classic White None", "1", "3Part #", "Sheet #")
    assert labels_in_design(read_labels(pdf(bad)), (piece(),)).code == "could_not_read"


# ----- it never passes a nest and never raises DO NOT CUT


def _all_three() -> list[LabelsMatch]:
    return [labels_in_design((label(),), (piece(),)),
            labels_in_design((label(part="R1C9"),), (piece(),)),
            labels_in_design((), ())]


def test_some_missing_reads_could_not_verify() -> None:
    assert "could not verify" in labels_in_design((label(part="R1C9"),), (piece(),)).note.casefold()


def test_could_not_read_reads_could_not_verify() -> None:
    assert "could not verify" in labels_in_design((), ()).note.casefold()


@pytest.mark.parametrize("words", ["nest checked", "do not cut", "ok to cut", "safe to cut",
                                   "passed"])
def test_no_reading_ever_sounds_like_a_verdict_on_the_nest(words: str) -> None:
    for match in _all_three():
        assert words not in match.note.casefold()


def test_even_all_found_says_the_nest_itself_is_not_checked() -> None:
    note = labels_in_design((label(),), (piece(),)).note
    assert "the nest itself is not checked" in note.casefold()


def test_every_reading_is_marked_as_never_proving_the_nest() -> None:
    for match in _all_three():
        assert match.proves_nest is False


def test_the_mark_can_not_be_set_on_a_match() -> None:
    with pytest.raises(TypeError):
        LabelsMatch(code="all_found", proves_nest=True)  # type: ignore[call-arg]
    match = labels_in_design((label(),), (piece(),))
    with pytest.raises(AttributeError):
        match.proves_nest = True  # type: ignore[misc]


def test_every_note_passes_the_lint() -> None:
    for match in _all_three():
        assert check_line(match.note) == []


def test_the_module_holds_no_verdict_and_imports_no_state() -> None:
    import ngrunner.guards.labels as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported = [n for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                for n in ([a.name for a in node.names] + [getattr(node, "module", "") or ""])]
    assert not any("nest_state" in name or "store" in name for name in imported)
    code = "\n".join(str(node.value) for node in ast.walk(tree)
                     if isinstance(node, ast.Constant) and isinstance(node.value, str)
                     and node is not tree.body[0].value)
    assert "NEST_CHECKED" not in code
    assert "DO_NOT_CUT" not in code


def _passing_evidence(design):
    """Everything the real decide() asks for, all passing, with the given match. The control below
    shows this does reach Nest checked, so the labels tests can't pass for some other reason."""
    nest_state = pytest.importorskip("ngrunner.guards.nest_state")
    from ngrunner.guards import facts
    from ngrunner.guards.tap import Tap

    tap = Tap(name="TESS01.TAP", sha256="a" * 64, size=2048, inches=True, complete=True,
              family="sheet", reach=(48.75, 96.75), holes=12, near_pairs=())
    rooms = (("Room1.des", "b" * 64),)
    checked = facts.CheckedFiles("c" * 64, ((tap.name, tap.sha256),), rooms)
    tools = (facts.ToolVerdict(facts.SHEET_SIZE, facts.PASSED, checked=checked),
             facts.ToolVerdict(facts.BR_CAM_NEST, facts.PASSED, checked=checked))
    sheet = facts.OptSheet(width_mm=1244.6, length_mm=2463.8, quan=1, flipped=False)
    return nest_state, nest_state.MaterialEvidence(
        material="3-4 Egger Classic White", taps=(tap,), seen_at_install=False,
        opt=facts.OptFacts("c" * 64, 3, (sheet,)), tools=tools,
        design=design(facts, checked), room_hashes=rooms)


def test_the_control_a_real_design_match_does_reach_nest_checked() -> None:
    nest_state, evidence = _passing_evidence(
        lambda facts, checked: facts.DesignMatch(facts.DESIGN_MATCHES, checked=checked))
    assert nest_state.decide(evidence).state == nest_state.NestState.NEST_CHECKED


@pytest.mark.parametrize("labels, pieces", [
    ((label(),), (piece(),)),                   # all_found
    ((label(part="R1C9"),), (piece(),)),        # some_missing
    ((), ()),                                   # could_not_read
])
def test_with_all_else_passing_no_labels_reading_decides_the_nest(labels, pieces) -> None:
    nest_state, evidence = _passing_evidence(lambda facts, checked: None)
    verdict = nest_state.decide(replace(evidence, labels=labels_in_design(labels, pieces)))
    assert verdict.state == nest_state.NestState.NOT_CHECKED


@pytest.mark.parametrize("labels, pieces", [
    ((label(),), (piece(),)), ((label(part="R1C9"),), (piece(),)), ((), ()),
])
def test_a_labels_reading_where_the_real_match_goes_decides_nothing_either(labels, pieces) -> None:
    nest_state, evidence = _passing_evidence(
        lambda facts, checked: labels_in_design(labels, pieces))
    assert nest_state.decide(evidence).state == nest_state.NestState.NOT_CHECKED


def test_the_real_match_and_the_labels_fallback_are_two_types_not_one() -> None:
    from ngrunner.guards import facts

    assert LabelsMatch is not facts.DesignMatch
    assert not hasattr(facts.DesignMatch("matches"), "proves_nest")


@pytest.mark.parametrize("labels, pieces, says", [
    ((label(part="R1C9"),), (piece(),), True),
    ((), (), True),
    ((label(),), (piece(),), False),
])
def test_only_a_labels_reading_that_did_not_fit_adds_could_not_verify(labels, pieces,
                                                                      says) -> None:
    nest_state, evidence = _passing_evidence(lambda facts, checked: None)
    verdict = nest_state.decide(replace(evidence, labels=labels_in_design(labels, pieces)))
    said = " ".join(verdict.reasons).casefold()
    assert ("could not verify" in said and "parts catalog" in said) is says


# ----- the newest file per material


T1 = datetime(2026, 10, 18, tzinfo=timezone.utc)
T2 = datetime(2026, 10, 29, tzinfo=timezone.utc)


def test_a_material_comes_from_its_newest_file() -> None:
    old = (label(part="R1C1"), label(part="R1C2"))
    new = (label(part="R1C3"),)
    assert newest_per_material([(T1, old), (T2, new)]) == new


def test_each_material_keeps_its_own_newest_file() -> None:
    backs = (label(part="R1C1", material="1-4 Egger Classic White"),)
    old = (label(part="R1C2"),) + backs
    new = (label(part="R1C3"),)
    assert set(newest_per_material([(T1, old), (T2, new)])) == {new[0], backs[0]}


def test_an_unreadable_file_makes_the_whole_result_nothing() -> None:
    # never a partial list: its material might be the one that does not match
    assert newest_per_material([(T1, (label(),)), (T2, ())]) == ()


# ----- real files


CASES = "CFGK"      # all_found once Room 0 is compared by name and size
ALL_CASES = "CDFGK"


def _key() -> dict[str, str]:
    path = FIXTURES / "p1b" / "case_key.json"
    if not path.is_file():
        pytest.skip("no case key: see AGENTS.md: Fixtures")
    return json.loads(path.read_text(encoding="utf-8"))


def _real_labels_index() -> list[dict[str, str]]:
    path = FIXTURES / "p1b" / "labels" / "index.json"
    if not path.is_file():
        pytest.skip("no local labels copy: run tools/collect_p1b_documents.py")
    return json.loads(path.read_text(encoding="utf-8"))


def _main_file(case: str) -> Path:
    """The case's first labels file: each case's whole job. Case C's two later files are re-cuts."""
    entries = [e for e in _real_labels_index() if e["case"] == case]
    return FIXTURES / "p1b" / "labels" / entries[0]["file"]


_rows_cache: list[tuple] = []


def _real_rows():
    _real_labels_index()
    if not _rows_cache:
        catalog = FIXTURES / "p1b" / "labels" / "parts_catalog.csv"
        _rows_cache.append(read_catalog(catalog.read_bytes()))
    return _rows_cache[0]


def _pieces(case: str) -> tuple[Piece, ...]:
    return catalog_pieces(_real_rows(), _key()[case])


@pytest.mark.fixtures
def test_the_real_catalog_reads_with_its_jobs_and_every_part_a_cabinet() -> None:
    rows = _real_rows()
    counts = (len(rows), len({row["Job"] for row in rows}))
    assert counts == (3513, 159)
    cabinets = sum(row["Part"][0] == "R" and "C" in row["Part"] for row in rows)
    assert cabinets == 3513


@pytest.mark.fixtures
@pytest.mark.parametrize("case, labels, compared, counter_tops", [
    ("C", 89, 89, 0), ("F", 16, 16, 0), ("G", 46, 46, 0), ("K", 295, 293, 2),
])
def test_real_jobs_are_all_found_once_room_0_is_compared_by_name(case: str, labels: int,
                                                                 compared: int,
                                                                 counter_tops: int) -> None:
    parts = read_labels(_main_file(case).read_bytes())
    match = labels_in_design(parts, _pieces(case))
    assert len(parts) == labels
    assert match.code == "all_found"
    assert (match.compared, match.not_compared) == (compared, counter_tops)


@pytest.mark.fixtures
def test_case_d_is_some_missing_because_its_catalog_has_no_room_0() -> None:
    """The spec expects all_found on this one too. Its one Room 0 label has no R0C to compare with:
    the catalog holds no Room 0 for this job at all, so "could not verify" is the true reading."""
    pieces = _pieces("D")
    assert not any(p.part.startswith("R0") for p in pieces)
    match = labels_in_design(read_labels(_main_file("D").read_bytes()), pieces)
    assert match.code == "some_missing"
    assert len(match.missing) == 1
    assert match.compared == 15


@pytest.mark.fixtures
@pytest.mark.parametrize("case", list(ALL_CASES))
def test_a_label_of_a_part_not_in_the_catalog_gives_some_missing_on_a_real_job(case: str) -> None:
    """A removed piece: every real job flags when the catalog no longer holds a part it nested."""
    parts = read_labels(_main_file(case).read_bytes())
    pieces = _pieces(case)
    target = next(p for p in parts if p.part[0:2] != "R0" and "C" in p.part)
    without = tuple(p for p in pieces if p.part != target.part)
    assert labels_in_design(parts, without).code == "some_missing"


@pytest.mark.fixtures
def test_a_label_removed_from_a_copy_of_a_real_file_still_reads() -> None:
    reader = PdfReader(_main_file("F"))
    writer = PdfWriter()
    for number, one in enumerate(reader.pages):
        if number != 0:
            writer.add_page(one)
    from io import BytesIO
    out = BytesIO()
    writer.write(out)
    full = read_labels(_main_file("F").read_bytes())
    fewer = read_labels(out.getvalue())
    assert len(fewer) in (len(full) - 1, len(full))     # page 1 may be an offcut
    assert labels_in_design(fewer, _pieces("F")).code == "all_found"


@pytest.mark.fixtures
def test_a_real_label_changed_to_a_part_nobody_designed_gives_some_missing() -> None:
    parts = read_labels(_main_file("F").read_bytes())
    changed = (LabelPart(part="R9C99", name=parts[0].name, w_in=parts[0].w_in, l_in=parts[0].l_in,
                         material=parts[0].material, sheet=parts[0].sheet), *parts[1:])
    match = labels_in_design(changed, _pieces("F"))
    assert match.code == "some_missing"
    assert len(match.missing) == 1


@pytest.mark.fixtures
def test_a_real_file_with_its_part_numbers_scrubbed_gives_could_not_read() -> None:
    reader = PdfReader(_main_file("F"))
    text = reader.pages[0].extract_text()
    lines = [line for line in text.splitlines()
             if not line.strip().startswith("R") or " " in line.strip()]
    parts = read_labels(pdf(page(*(line.strip() for line in lines))))
    found = labels_in_design(parts, _pieces("F")).code
    assert found == "could_not_read"


@pytest.mark.fixtures
def test_no_real_label_keeps_a_design_name() -> None:
    reader = PdfReader(_main_file("F"))
    first_lines = reader.pages[0].extract_text().splitlines()
    design_words = [line.strip() for line in first_lines[1:4] if len(line.strip()) >= 4
                    and not line.strip().startswith("R")]
    kept = repr(read_labels(_main_file("F").read_bytes()))
    assert sum(word in kept for word in design_words) == 0


@pytest.mark.fixtures
def test_newest_per_material_on_the_three_files_of_case_c() -> None:
    entries = [e for e in _real_labels_index() if e["case"] == "C"]
    files_found = len(entries)
    assert files_found == 3
    files = [(datetime.fromisoformat(e["modified"]).replace(tzinfo=timezone.utc),
              read_labels((FIXTURES / "p1b" / "labels" / e["file"]).read_bytes())) for e in entries]
    chosen = newest_per_material(files)
    # the re-cut files hold 3/4 only, so the 1/4 still comes from the job's first file
    assert {p.material.split()[0] for p in chosen} == {"1-4", "3-4"}
    assert len(chosen) < sum(len(parts) for _, parts in files)
    assert labels_in_design(chosen, _pieces("C")).code == "all_found"
