"""
Reading a nest's .opt, and setting what it names against the programs on disk (P1b build spec 5.11).

No real .opt has been read on this PC. So every file here is synthetic, written from the line of Jim's
docs or the line of his own parsers each test cites (the toolkit copy of 2026-10-31, read and never run),
and what matters most is that anything else fails closed: an .opt that doesn't look as they say raises
OptUnreadable, which is NOT CHECKED, and never a pass, a crash or a DO NOT CUT. His docs warn of the exact
trap: "decoding UTF-16 as UTF-8 yields a zero count that looks like a finding and is not" (BUILD 567).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from helpers_opt import (
    CLASSIC_MM, EGGER_MM, MOZAIK_NESTED, MOZAIK_NOT_NESTED, NESTED, NOT_NESTED, material,
    mozaik_nest, nest_text, sheet, utf8, utf16,
)
from ngrunner.guards.facts import OptFacts
from ngrunner.guards.opt import (
    OPT_SETTLES_AFTER, OptUnreadable, read_opt, read_opt_file, reconcile,
)
from ngrunner.guards.reads import CloudOnly
from ngrunner.guards.tap import Tap


def tap(name: str) -> Tap:
    return Tap(name=name, sha256="0" * 64, size=10, inches=True, complete=True, family="sheet",
               reach=(48.75, 96.75), holes=1, near_pairs=())


def opt_naming(*runs: str) -> OptFacts:
    return read_opt(utf8(nest_text(sheets=(sheet(),), runs=runs)))


# ----- the form real nests have: 20 of 20 real files read on 2026-10-31 (tests/test_real_nests.py)


def test_a_real_nest_is_utf8_with_mozaiks_version_line_first_and_reads() -> None:
    facts = read_opt(MOZAIK_NESTED)

    assert (facts.encoding, facts.version) == ("utf-8", "8")
    assert (facts.physical_sheets, facts.runs, facts.parts) == (1, ("TESS01.TAP",), 3)
    assert facts.sheets[0].width_mm == CLASSIC_MM[0]


def test_a_real_nest_not_run_reads_as_parts_and_no_sheets() -> None:
    facts = read_opt(MOZAIK_NOT_NESTED)

    assert (facts.parts, facts.sheets, facts.runs) == (21, (), ())


@pytest.mark.parametrize("version", ["9", "7", "10", "0"])
def test_a_version_not_seen_is_unreadable_never_guessed(version: str) -> None:
    # 20 of 20 real ones say 8. A new Mozaik may change the form: then Sulaiman hears, and nothing passes.
    with pytest.raises(OptUnreadable):
        read_opt(mozaik_nest(version=version))


CRLF = b"\r\n"


@pytest.mark.parametrize("first", [b"8 ", b"v8", b"", b"8" + CRLF + b"8", b"8.1"],
                         ids=["a space", "a letter", "blank", "two lines", "a decimal"])
def test_a_first_line_not_a_version_alone_is_unreadable(first: bytes) -> None:
    with pytest.raises(OptUnreadable):
        read_opt(first + CRLF + MOZAIK_NESTED.partition(CRLF)[2])


def test_a_version_line_ending_in_a_bare_newline_reads_too() -> None:
    assert read_opt(MOZAIK_NESTED.replace(b"8" + CRLF, b"8\n", 1)).physical_sheets == 1


def test_a_utf16_nest_with_a_bom_and_the_version_line_reads_too() -> None:
    # RULES 705 has UTF-16 on some. None of the 20 is, so the BOM is still sniffed.
    text = MOZAIK_NESTED.decode("utf-8")

    assert read_opt(b"\xff\xfe" + text.encode("utf-16-le")).encoding == "utf-16"


def test_a_hand_added_sheet_is_a_block_of_its_own_size_as_in_case_b() -> None:
    facts = read_opt(mozaik_nest(sheets=((CLASSIC_MM, ("TESS01.TAP",)), (EGGER_MM, ("TESS075x9.TAP",)))))

    assert [(s.width_mm, s.length_mm) for s in facts.sheets] == [CLASSIC_MM, EGGER_MM]
    assert facts.physical_sheets == 2


def test_a_flip_pass_nested_in_its_run_is_one_flip_and_no_board_as_in_cases_c_and_d() -> None:
    facts = read_opt(mozaik_nest(sheets=((CLASSIC_MM, ("TESS01.TAP", "TESS01-Flip.TAP")),
                                         (CLASSIC_MM, ("TESS02.TAP",))), flip_on=0))

    assert [s.flipped for s in facts.sheets] == [False, True, False]
    assert facts.physical_sheets == 2
    assert facts.runs == ("TESS01.TAP", "TESS01-Flip.TAP", "TESS02.TAP")


def test_flipped_on_a_parts_location_is_never_read_as_a_sheet() -> None:
    data = mozaik_nest().replace(b'<OptimizePartLocation PartID="0" Flipped="False"',
                                 b'<OptimizePartLocation PartID="0" Flipped="Yes"')

    assert read_opt(data).physical_sheets == 1


# ----- the encoding: RULES 705-706, BUILD 565-568


@pytest.mark.parametrize("data, encoding", [
    (utf16(nest_text(sheets=(sheet(),))), "utf-16"),                                   # RULES 705
    (b"\xfe\xff" + nest_text(sheets=(sheet(),)).encode("utf-16-be"), "utf-16"),        # RULES 705
    (utf8(nest_text(sheets=(sheet(),))), "utf-8"),                                     # BUILD 566
    (b"\xef\xbb\xbf" + utf8(nest_text(sheets=(sheet(),))), "utf-8"),
], ids=["utf-16le bom", "utf-16be bom", "plain utf-8", "utf-8 with a bom"])
def test_the_encoding_is_sniffed_from_the_bom_and_each_form_reads_the_same(data, encoding) -> None:
    facts = read_opt(data)

    assert facts.encoding == encoding
    assert (facts.parts, facts.placed, facts.runs, facts.holes) == (3, 3, ("TESS01.TAP",), 2)
    assert facts.physical_sheets == 1


def test_utf_16_with_no_bom_is_unreadable_and_never_a_zero_count() -> None:
    # BUILD 567: "decoding UTF-16 as UTF-8 yields a zero count that looks like a finding and is not".
    with pytest.raises(OptUnreadable):
        read_opt(nest_text(sheets=(sheet(),)).encode("utf-16-le"))


def test_a_stray_byte_in_a_utf8_nest_costs_nothing_as_jims_own_check_reads_it() -> None:
    # sheet_size_check decodes the no-BOM branch with errors="replace": a part's name in another code
    # page must not make a nest unreadable. Its hash stays the file's own bytes.
    data = utf8(nest_text(sheets=(sheet(),))).replace(b'Name="Shelf"', b'Name="Caf\xe9 Shelf"', 1)

    assert read_opt(data).physical_sheets == 1


@pytest.mark.parametrize("data", [
    b"", b"\xff\xfe", b"\xff\xfe\x00", b"\xff\xfe<\x00O", b"\xef\xbb\xbf", b"\x80\x81\x82",
    b"not xml at all", utf8("<OptimizeJob><OptimizeSheet"), utf16("<OptimizeJob>"),
], ids=["empty", "bom only", "odd utf-16", "cut off utf-16", "utf-8 bom only", "not text",
        "not xml", "cut off xml", "cut off, utf-16"])
def test_what_wont_decode_or_parse_is_unreadable_in_plain_words_and_never_a_crash(data) -> None:
    with pytest.raises(OptUnreadable) as caught:
        read_opt(data)

    words = str(caught.value)
    assert words and "Traceback" not in words and "xml" not in words.lower()
    assert "Error" not in words


@pytest.mark.parametrize("text", [
    "<Something/>", "<OptimizeJob/>", "<OptimizeJob><Other/></OptimizeJob>",
    '<OptimizeJob><OptimizeGCodeRun Filename="TESS01.TAP"/></OptimizeJob>',
], ids=["another file", "empty", "other elements", "runs alone"])
def test_a_file_with_no_parts_and_no_sheets_is_not_a_nest_and_never_reads_as_zero(text) -> None:
    # BUILD 786 has two answers only: sheets, or parts and no sheets. Nothing at all is neither.
    with pytest.raises(OptUnreadable):
        read_opt(utf8(text))


# ----- sheets: RULES 707, BUILD 576, 786


def test_the_nest_test_is_sheets_summing_each_ones_quan() -> None:
    # RULES 707, BUILD 576 and 786: at least one <OptimizeSheet>, summing each one's Quan.
    facts = read_opt(utf8(nest_text(sheets=(sheet(quan=4), sheet(quan=3)))))

    assert facts.physical_sheets == 7
    assert [s.quan for s in facts.sheets] == [4, 3]


def test_a_flipped_block_is_the_same_board_and_doesnt_count() -> None:
    # RULES 716-721: the sum of Quan over the Flipped="False" blocks only. 10 blocks, 9 sheets.
    blocks = [sheet() for _ in range(9)] + [sheet(flipped="True")]

    facts = read_opt(utf8(nest_text(sheets=blocks)))

    assert len(facts.sheets) == 10
    assert facts.physical_sheets == 9
    assert [s.flipped for s in facts.sheets] == [False] * 9 + [True]


def test_the_size_is_the_optimize_material_headers_as_jims_own_check_reads_it() -> None:
    # sheet_size_check: Width and Length of <OptimizeMaterial>, in mm. RULES 143 calls it the header.
    facts = read_opt(utf8(nest_text(sheets=(sheet(), sheet(quan=2)),
                                    header=material(width=EGGER_MM[0], length=EGGER_MM[1]))))

    assert [(s.width_mm, s.length_mm) for s in facts.sheets] == [EGGER_MM, EGGER_MM]


def test_a_block_with_a_size_of_its_own_keeps_it() -> None:
    # BUILD 576: mixed sizes are legal, and how a hand-added sheet shows is unseen (build spec 8, step 7).
    facts = read_opt(utf8(nest_text(sheets=(sheet(), sheet(width=EGGER_MM[0], length=EGGER_MM[1])))))

    assert [(s.width_mm, s.length_mm) for s in facts.sheets] == [CLASSIC_MM, EGGER_MM]


def test_a_sheet_takes_the_header_before_it_where_a_nest_holds_two() -> None:
    # ASSUMPTION in opt.py: Jim's check reads only the first, so another is unseen by it.
    text = nest_text(sheets=(sheet(),)).replace("</OptimizeJob>", material(
        width=EGGER_MM[0], length=EGGER_MM[1]) + sheet(pattern=2) + "</OptimizeJob>")

    facts = read_opt(utf8(text))

    assert [(s.width_mm, s.length_mm) for s in facts.sheets] == [CLASSIC_MM, EGGER_MM]


def test_a_nest_with_sheets_and_no_header_is_unreadable_as_his_check_says_of_the_wrong_codec() -> None:
    # sheet_size_check prints ?? for it, "wrong codec?", and goes on: here it is never a count.
    with pytest.raises(OptUnreadable):
        read_opt(utf8(nest_text(sheets=(sheet(),), header="")))


def test_a_nest_not_run_needs_no_header_since_it_has_no_sheet_to_size() -> None:
    assert read_opt(utf8(nest_text(sheets=(), header=""))).sheets == ()


@pytest.mark.parametrize("header", [
    material(width=None), material(length=None), material(width="wide"), material(length=""),
    material(width=0), material(length=-5), material(width="nan"), material(length="inf"),
    material(width=49, length=97),
], ids=["no width", "no length", "words", "blank", "zero", "negative", "nan", "inf", "in inches"])
def test_a_header_not_as_documented_makes_the_nest_unreadable_and_never_a_size(header) -> None:
    # BUILD 554 gives the sizes in mm, and BUILD 567 warns of mistaking one unit for the other.
    with pytest.raises(OptUnreadable):
        read_opt(utf8(nest_text(sheets=(sheet(),), header=header)))


def test_a_block_with_no_quan_is_one_sheet_as_jims_own_check_counts_it() -> None:
    # sheet_size_check: Quan is read if there, and 1 if not.
    assert read_opt(utf8(nest_text(sheets=(sheet(quan=None),)))).physical_sheets == 1


def test_a_block_with_no_flipped_is_a_board_as_jims_own_check_counts_it() -> None:
    # sheet_size_check: only Flipped="True" is a flip.
    facts = read_opt(utf8(nest_text(sheets=(sheet(flipped=None), sheet(flipped="True")))))

    assert [s.flipped for s in facts.sheets] == [False, True]
    assert facts.physical_sheets == 1


@pytest.mark.parametrize("bad", [
    dict(quan=""), dict(quan="many"), dict(quan="2.5"), dict(quan=0), dict(quan=-1),
    dict(flipped=""), dict(flipped="true"), dict(flipped="False "), dict(flipped="1"),
    dict(width="wide"), dict(length=""), dict(width=0), dict(length=-5), dict(width="nan"), dict(length="inf"),
], ids=lambda bad: ",".join(f"{k}={v!r}" for k, v in bad.items()))
def test_a_sheet_not_as_documented_makes_the_nest_unreadable_and_never_a_count(bad) -> None:
    with pytest.raises(OptUnreadable):
        read_opt(utf8(nest_text(sheets=(sheet(**bad),))))


@pytest.mark.parametrize("size", [dict(width=49, length=97), dict(width=48.75, length=96.75)])
def test_a_size_in_inches_is_not_as_documented(size) -> None:
    # BUILD 554 gives the sizes in mm, and BUILD 567 warns of mistaking one unit for the other.
    with pytest.raises(OptUnreadable):
        read_opt(utf8(nest_text(sheets=(sheet(**size),))))


def test_only_flip_passes_with_no_board_are_not_as_documented() -> None:
    with pytest.raises(OptUnreadable):
        read_opt(utf8(nest_text(sheets=(sheet(flipped="True"),))))


# ----- not nested: BUILD 696, 776-787


def test_parts_and_no_sheets_is_not_nested_however_big_the_file_is() -> None:
    # BUILD 776-778: 178 KB, 19 OptimizePart elements, zero <OptimizeSheet>.
    facts = read_opt(NOT_NESTED)

    assert (facts.parts, facts.sheets, facts.placed, facts.physical_sheets) == (19, (), 0, 0)
    assert facts.runs == ()


def test_run_id_says_nothing_either_way() -> None:
    # BUILD 781-784: RunId="-1" appears on nested and unnested alike.
    unnested = nest_text(parts=2, sheets=())
    nested = nest_text(parts=2, sheets=(sheet(),))
    assert 'RunId="-1"' in unnested and 'RunId="-1"' in nested

    assert read_opt(utf8(unnested)).sheets == ()
    assert read_opt(utf8(nested)).physical_sheets == 1
    assert read_opt(utf8(nested.replace('RunId="-1"', 'RunId="7"'))).physical_sheets == 1


def test_sheets_with_no_parts_are_not_as_documented() -> None:
    with pytest.raises(OptUnreadable):
        read_opt(utf8(nest_text(parts=0, sheets=(sheet(),))))


def test_sheets_are_the_only_test_of_a_nest_however_many_parts_have_a_location() -> None:
    # BUILD 784: the only test is whether <OptimizeSheet> elements exist. No script of his reads a
    # location, so nothing here may rest on them: they are counted, and judge nothing.
    assert read_opt(utf8(nest_text(parts=3, placed=0, sheets=(sheet(),)))).physical_sheets == 1
    assert read_opt(utf8(nest_text(parts=3, placed=3, sheets=()))).sheets == ()


# ----- programs named: RULES 722-726


def test_the_nest_names_its_own_programs_in_order_each_once() -> None:
    facts = read_opt(utf8(nest_text(sheets=(sheet(),),
                                    runs=("TESS01.TAP", "TESS02-Flip.TAP", "TESS01.TAP"))))

    assert facts.runs == ("TESS01.TAP", "TESS02-Flip.TAP")


@pytest.mark.parametrize("text", [
    "<OptimizeGCodeRun/>", '<OptimizeGCodeRun Filename=""/>', '<OptimizeGCodeRun Filename="  "/>',
], ids=["no name", "blank", "spaces"])
def test_a_run_with_no_name_names_no_program(text) -> None:
    # sheet_size_check only takes a run with a Filename. A nest may be saved before it is posted.
    good = nest_text(sheets=(sheet(),))

    facts = read_opt(utf8(good.replace("</OptimizeJob>", f"{text}</OptimizeJob>")))

    assert facts.runs == ("TESS01.TAP",)


def test_a_nest_whose_runs_name_nothing_names_no_program_and_so_can_never_pass() -> None:
    text = nest_text(sheets=(sheet(),), runs=())

    assert read_opt(utf8(text)).runs == ()


def test_elements_are_found_wherever_they_nest_and_whatever_the_root_is_called() -> None:
    # His docs name the elements and not how they nest, so nothing may rest on that.
    text = ('<A><E><OptimizeMaterial Width="1244.6" Length="2463.8"/></E>'
            '<B><OptimizePart/><C><OptimizePart/></C></B>'
            '<OptimizeSheet Quan="2" Flipped="False">'
            '<OptimizePartLocation/><OptimizePartLocation/></OptimizeSheet>'
            '<D><OptimizeGCodeRun Filename="TESS01.TAP"/></D></A>')

    facts = read_opt(utf8(text))

    assert (facts.parts, facts.placed, facts.physical_sheets, facts.runs) == (
        2, 2, 2, ("TESS01.TAP",))


def test_a_namespace_changes_nothing() -> None:
    text = nest_text(sheets=(sheet(),)).replace("<OptimizeJob", '<OptimizeJob xmlns="urn:made-up"', 1)

    assert read_opt(utf8(text)).physical_sheets == 1


# ----- what a reading holds


def test_the_hash_is_of_the_files_own_bytes() -> None:
    assert read_opt(NESTED).sha256 == hashlib.sha256(NESTED).hexdigest()


def test_an_encoding_declaration_that_disagrees_doesnt_matter_the_bom_decides() -> None:
    # Mozaik's own XML declaration may say utf-16 on a file decoded already: the bytes decide.
    text = nest_text(sheets=(sheet(),)).replace('<?xml version="1.0"?>',
                                                '<?xml version="1.0" encoding="utf-16"?>')
    assert read_opt(utf16(text)).physical_sheets == 1
    assert read_opt(utf8(text)).physical_sheets == 1


def test_holes_are_counted_inside_their_parts_as_br_cam_check_finds_them() -> None:
    # br_cam_check --nest: <OperationHole> is read inside each <OptimizePart>.
    assert read_opt(utf8(nest_text(sheets=(sheet(),), holes=5))).holes == 5


def test_the_facts_hold_counts_and_names_and_no_part_data() -> None:
    assert "EdgeBand" not in repr(read_opt(NESTED))


# ----- reconcile: RULES 722-726, build spec 5.11


def test_a_program_the_nest_names_and_isnt_on_disk_is_missing() -> None:
    # RULES 724: each of those filenames must exist in the material's folder.
    found = reconcile(opt_naming("TESS01.TAP", "TESS02.TAP"), [tap("TESS01.TAP")])

    assert found.missing == ("TESS02.TAP",)
    assert found.extra == ()


def test_a_program_on_disk_the_nest_doesnt_name_is_extra() -> None:
    # 65 of 551 real programs are hand-named (build spec 3.1), so it is only ever a log line.
    found = reconcile(opt_naming("TESS01.TAP"), [tap("TESS01.TAP"), tap("TESfixRM.TAP")])

    assert found.missing == ()
    assert found.extra == ("TESfixRM.TAP",)


def test_case_never_matters_as_both_tap_and_upper_case_occur_on_the_same_disk() -> None:
    # Build spec 5.4: both .TAP (1,435) and .tap (228) occur.
    found = reconcile(opt_naming("TESS01.TAP", "tess02.tap"), [tap("tess01.tap"), tap("TESS02.TAP")])

    assert (found.missing, found.extra) == ((), ())


@pytest.mark.parametrize("named", [
    r"C:\Users\made-up\Mozaik\TESS01.TAP", "//made-up/share/TESS01.TAP",
], ids=["a windows path", "a share"])
def test_a_name_with_a_path_is_the_same_program_as_jims_own_check_takes_it(named) -> None:
    # sheet_size_check takes the base name of Filename, either slash.
    found = reconcile(opt_naming(named), [tap("TESS01.TAP")])

    assert (found.missing, found.extra) == ((), ())


def test_a_name_with_no_extension_is_not_the_program_as_jims_own_check_compares_whole_names() -> None:
    # sheet_size_check compares Filename with the names of .tap files in the folder.
    found = reconcile(opt_naming("TESS01"), [tap("TESS01.TAP")])

    assert found.missing == ("TESS01",)


def test_a_flip_pass_is_its_own_program() -> None:
    # RULES 723: a two-sided pattern adds a -Flip pass.
    found = reconcile(opt_naming("TESS01.TAP", "TESS01-Flip.TAP"), [tap("TESS01.TAP")])

    assert found.missing == ("TESS01-Flip.TAP",)


def test_a_nest_naming_nothing_has_nothing_missing_and_all_extra() -> None:
    not_nested = read_opt(NOT_NESTED)

    found = reconcile(not_nested, [tap("TESS01.TAP")])

    assert (found.missing, found.extra) == ((), ("TESS01.TAP",))


# ----- reading one in place: design 5, Reads


def test_a_nest_is_read_only_once_unchanged_for_two_minutes() -> None:
    assert OPT_SETTLES_AFTER.total_seconds() == 120


def test_a_file_is_read_through_the_shared_reader_and_hashed_twice(tmp_path: Path) -> None:
    path = tmp_path / "3-4 Classic White.opt"
    path.write_bytes(NESTED)
    reads: list[Path] = []

    def read(given: Path) -> bytes:
        reads.append(given)
        return given.read_bytes()

    assert read_opt_file(path, read=read).sha256 == hashlib.sha256(NESTED).hexdigest()
    assert reads == [path, path]


def test_a_file_that_changes_between_its_two_reads_is_unreadable_for_now(tmp_path: Path) -> None:
    path = tmp_path / "3-4 Classic White.opt"
    path.write_bytes(NESTED)
    answers = iter((NESTED, NESTED + b" "))

    with pytest.raises(OptUnreadable) as caught:
        read_opt_file(path, read=lambda _: next(answers))

    assert caught.value.retry is True


def test_a_file_only_in_the_cloud_is_unreadable_for_now_in_plain_words(tmp_path: Path) -> None:
    def read(_: Path) -> bytes:
        raise CloudOnly("3-4 Classic White.opt is only in the cloud.", 362)

    with pytest.raises(OptUnreadable) as caught:
        read_opt_file(tmp_path / "3-4 Classic White.opt", read=read)

    assert caught.value.retry is True
    assert "cloud" in str(caught.value)


def test_a_file_gone_is_unreadable_for_now_never_a_crash(tmp_path: Path) -> None:
    with pytest.raises(OptUnreadable) as caught:
        read_opt_file(tmp_path / "none here.opt")

    assert caught.value.retry is True


def test_a_file_that_wont_parse_needs_no_retry_until_it_changes(tmp_path: Path) -> None:
    path = tmp_path / "3-4 Classic White.opt"
    path.write_bytes(b"not a nest")

    with pytest.raises(OptUnreadable) as caught:
        read_opt_file(path)

    assert caught.value.retry is False
