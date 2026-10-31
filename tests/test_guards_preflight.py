"""
The lock pre-flight, the crosscheck reader and the job sheet reader (build spec, section 5.5).

It reports and never decides: nothing here blocks a lock in P1b, and status words are reported,
never judged. A snapshot over 20 minutes old makes the whole result read "could not verify". Real
files are read only by the tests marked fixtures, and report counts, never names.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ngrunner.guards.labels import labels_in_design, LabelPart, Piece
from ngrunner.guards.preflight import (
    _FIXED,
    CODES,
    CheckFile,
    Preflight,
    PreflightItem,
    could_not_read_tracker,
    preflight,
    read_check_file,
    read_job_sheet,
)
from ngrunner.guards.tracker import read_tracker
from ngrunner.messages.lint import check_line
from conftest import FIXTURES
from test_guards_tracker import (
    CUSTOMER,
    JOB,
    NOTE,
    OTHER,
    TAKEN,
    job_row,
    sheet_row,
    tape_row,
    write_snapshot,
)

NOW = TAKEN + timedelta(minutes=5)
OLD = TAKEN + timedelta(minutes=20, seconds=1)
GOOD_CHECK = CheckFile(at=datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc), verdict="clean",
                       checkers=(), job=JOB)
DESIGN_DAY = date(2026, 10, 20)


@dataclass(frozen=True)
class Nest:
    """A stand-in for the store's NestRecord: only what the pre-flight may depend on."""

    material: str
    state: str
    reasons: tuple[str, ...] = ()


CHECKED = Nest("3-4 Egger Classic White", "nest_checked")


def snapshot(tmp_path: Path, **tabs):
    return read_tracker(write_snapshot(tmp_path, **tabs))


def run(snap, *, job: str = JOB, nests=(CHECKED,), check: CheckFile | None = GOOD_CHECK,
        catalog: date | None = DESIGN_DAY, now: datetime = NOW, **more) -> Preflight:
    return preflight(job, snap, nests, check, catalog, now=now, **more)


def codes(result: Preflight) -> list[str]:
    return [item.code for item in result.items]


def texts(result: Preflight, code: str) -> list[str]:
    return [item.text for item in result.items if item.code == code]


def not_status(result: Preflight) -> list[str]:
    return [c for c in codes(result) if c != "status_unknown"]


# ----- a clean job, and the shape of a result


def test_a_clean_job_reports_only_its_status_word(tmp_path: Path) -> None:
    assert codes(run(snapshot(tmp_path))) == ["status_unknown"]


def test_a_result_holds_its_items_and_the_snapshot_time_and_nothing_that_could_block(
        tmp_path: Path) -> None:
    result = run(snapshot(tmp_path))
    assert isinstance(result, Preflight)
    assert isinstance(result.items[0], PreflightItem)
    assert result.snapshot_taken == TAKEN
    assert {f.name for f in fields(Preflight)} == {"items", "snapshot_taken"}


def test_every_code_has_a_fixed_line_to_fall_back_on() -> None:
    assert set(_FIXED) == CODES


# ----- a stale snapshot: the whole result reads "could not verify"


def test_a_snapshot_over_20_minutes_old_gives_only_could_not_verify(tmp_path: Path) -> None:
    snap = snapshot(tmp_path, sheets=[sheet_row(price="", cost=""), sheet_row(), sheet_row()],
                    jobs=[job_row(status="⚠ Review")])
    result = run(snap, nests=(Nest("3-4 Egger Classic White", "do_not_cut"),), check=None,
                 now=OLD)
    assert codes(result) == ["snapshot_old"]
    assert "could not verify" in result.items[0].text.casefold()
    assert result.snapshot_taken == TAKEN


def test_a_snapshot_exactly_20_minutes_old_still_counts(tmp_path: Path) -> None:
    assert "snapshot_old" not in codes(run(snapshot(tmp_path),
                                           now=TAKEN + timedelta(minutes=20)))


def test_a_snapshot_dated_far_ahead_of_the_clock_also_could_not_be_verified(tmp_path: Path) -> None:
    assert codes(run(snapshot(tmp_path), now=TAKEN - timedelta(minutes=21))) == ["snapshot_old"]


def test_a_naive_now_is_refused(tmp_path: Path) -> None:
    snap = snapshot(tmp_path)
    with pytest.raises(ValueError):
        run(snap, now=datetime(2026, 10, 30, 23, 55))


def test_the_reading_of_one_snapshot_changes_with_the_clock_alone(tmp_path: Path) -> None:
    snap = snapshot(tmp_path)
    assert "snapshot_old" not in codes(run(snap, now=NOW))
    assert codes(run(snap, now=OLD)) == ["snapshot_old"]


# ----- rows


def test_a_job_with_no_tracker_rows_says_so(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path), job=OTHER, check=None, catalog=None)
    assert "no_rows" in codes(result)


def test_a_job_missing_from_one_tab_names_the_tab(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path, jobs=[job_row(job=OTHER)]))
    assert len(texts(result, "no_rows")) == 1
    assert "Job Summary" in texts(result, "no_rows")[0]


def test_a_material_twice_on_the_sheet_tab_is_a_duplicate_row(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path, sheets=[sheet_row(), sheet_row(), sheet_row(job=OTHER)]))
    assert codes(result).count("duplicate_row") == 1
    assert "Egger Classic White" in texts(result, "duplicate_row")[0]


def test_a_material_twice_on_the_tape_tab_is_a_duplicate_row(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path, tape=[tape_row(), tape_row()]))
    assert codes(result).count("duplicate_row") == 1


def test_materials_are_compared_exactly_as_the_tracker_does(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path, sheets=[sheet_row(),
                                            sheet_row(material="Egger Classic White ")]))
    assert "duplicate_row" not in codes(result)


# ----- prices


def test_a_nested_sheet_row_with_no_price_is_not_priced(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path, sheets=[
        sheet_row(material="Tafisa Spring Blossom_Ore", optimizer="2", price="", cost="")]))
    assert texts(result, "not_priced") == [
        "2 sheets of Tafisa Spring Blossom Ore have no price in the tracker"]


def test_one_nested_sheet_with_no_price_reads_in_the_singular(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path, sheets=[sheet_row(optimizer="1", price="", cost="")]))
    assert texts(result, "not_priced") == [
        "1 sheet of Egger Classic White has no price in the tracker"]


def test_a_sheet_row_not_nested_yet_is_not_an_unpriced_row(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path, sheets=[sheet_row(optimizer="", price="", cost="")]))
    assert "not_priced" not in codes(result)


def test_a_tape_row_with_feet_and_no_price_is_not_priced(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path, tape=[
        tape_row(material='Arauco 1mm — 15/16" / 1"', calc="634", optimizer="634", price="",
                 cost="")]))
    assert texts(result, "not_priced") == [
        '634 feet of 15/16" Arauco tape have no price in the tracker']


def test_a_tape_row_with_no_feet_is_not_an_unpriced_row(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path, tape=[tape_row(calc="0", optimizer="0", price="", cost="")]))
    assert "not_priced" not in codes(result)


@pytest.mark.parametrize("row", [
    sheet_row(optimizer="7", price="$40.00", cost="$0.00"),
    sheet_row(optimizer="7", price="$0.00", cost="$0.00"),
    sheet_row(optimizer="7", price="$40.00", cost=""),
])
def test_a_nested_sheet_row_costing_nothing_is_a_zero_cost_row(tmp_path: Path, row) -> None:
    assert "zero_cost" in codes(run(snapshot(tmp_path, sheets=[row])))


def test_a_tape_row_with_feet_costing_nothing_is_a_zero_cost_row(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path, tape=[tape_row(optimizer="100", cost="$0.00")]))
    assert codes(result).count("zero_cost") == 1


def test_a_tape_row_with_only_calc_feet_has_no_cost_yet_and_that_is_no_fault(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path, tape=[tape_row(calc="179.3", optimizer="", cost="")]))
    assert "zero_cost" not in codes(result)
    assert "not_priced" not in codes(result)


def test_a_zero_quantity_row_costing_nothing_is_no_problem(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path, tape=[tape_row(calc="0", optimizer="0", cost="$0.00")]))
    assert "zero_cost" not in codes(result)


def test_a_key_spelled_twice_is_reported_for_a_job_using_it(tmp_path: Path) -> None:
    snap = snapshot(tmp_path,
                    sheets=[sheet_row(material="Egger Classic White (1/4) 5x9")],
                    sheet_keys=[["Egger Classic White (1/4) 5x9 ", "$60.00", "", ""],
                                ["Egger Classic White (1/4) 5x9", "$65.00", "", ""]])
    result = run(snap)
    assert codes(result).count("key_spelled_twice") == 1
    assert "Egger Classic White (1/4) 5x9" in texts(result, "key_spelled_twice")[0]


def test_a_key_spelled_twice_is_not_on_a_job_that_does_not_use_it(tmp_path: Path) -> None:
    snap = snapshot(tmp_path, sheet_keys=[["Other Colour ", "$60.00", "", ""],
                                          ["Other Colour", "$65.00", "", ""],
                                          ["Egger Classic White", "$40.00", "", ""]])
    assert "key_spelled_twice" not in codes(run(snap))


# ----- status words: reported, never judged


@pytest.mark.parametrize("word", ["🔲 In progress", "🔒 LOCKED", "LOCKED", "⚠ Review",
                                  "✅ Panel check CLEAN", "Whatever Jim typed"])
def test_any_status_word_is_reported_as_typed_and_never_judged(tmp_path: Path, word: str) -> None:
    result = run(snapshot(tmp_path, jobs=[job_row(status=word)]))
    text = texts(result, "status_unknown")
    assert len(text) == 1
    assert word in text[0]
    for judged in ("wrong", "bad", "invalid", "should", "must", "correct", "fix"):
        assert judged not in text[0].replace(word, "").casefold()


def test_a_blank_status_is_reported_too(tmp_path: Path) -> None:
    assert len(texts(run(snapshot(tmp_path, jobs=[job_row(status="")])), "status_unknown")) == 1


def test_no_status_item_without_a_job_summary_row(tmp_path: Path) -> None:
    assert "status_unknown" not in codes(run(snapshot(tmp_path, jobs=[job_row(job=OTHER)])))


# ----- nest state


@pytest.mark.parametrize("state, words", [
    ("not_checked", "NOT CHECKED"),
    ("do_not_cut", "DO NOT CUT"),
    ("save_nest", "Save the nest"),
    ("not_posted", "not posted"),
])
def test_a_material_not_checked_is_reported_with_its_state(tmp_path: Path, state: str,
                                                           words: str) -> None:
    result = run(snapshot(tmp_path), nests=(Nest("3-4 Egger Classic White", state),))
    text = texts(result, "nest_state")
    assert len(text) == 1
    assert words in text[0]
    assert "3-4 Egger Classic White" in text[0]


def test_a_nest_checked_material_has_no_item(tmp_path: Path) -> None:
    assert "nest_state" not in codes(run(snapshot(tmp_path), nests=(CHECKED,)))


def test_each_material_gets_its_own_item(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path), nests=(
        Nest("3-4 Egger Classic White", "not_checked"),
        Nest("1-4 Egger Classic White", "do_not_cut"), CHECKED))
    assert codes(result).count("nest_state") == 2


def test_a_failed_nest_says_not_to_fill_optimizer_sheets_from_it_and_blocks_nothing(
        tmp_path: Path) -> None:
    result = run(snapshot(tmp_path), nests=(Nest("3-4 Egger Classic White", "do_not_cut"),))
    assert "Optimizer Sheets" in texts(result, "nest_state")[0]
    assert {f.name for f in fields(result)} == {"items", "snapshot_taken"}


def test_a_reason_from_the_store_is_shown(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path), nests=(
        Nest("3-4 Egger Classic White", "do_not_cut",
             ("8 cam holes are doubled in programs TESS02",)),))
    assert "8 cam holes are doubled" in texts(result, "nest_state")[0]


def test_a_reason_the_lint_refuses_is_dropped_and_the_state_still_shows(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path), nests=(
        Nest("3-4 Egger Classic White", "do_not_cut", (r"see C:\Users\someone\secret.txt",)),))
    text = texts(result, "nest_state")[0]
    assert check_line(text) == []
    assert "secret" not in text
    assert "DO NOT CUT" in text


@pytest.mark.parametrize("state", ["DO_NOT_CUT", "do_not_cut"])
def test_a_state_reads_in_any_case(tmp_path: Path, state: str) -> None:
    result = run(snapshot(tmp_path), nests=(Nest("3-4 Egger Classic White", state),))
    assert "DO NOT CUT" in texts(result, "nest_state")[0]


def test_the_stores_own_record_and_the_real_states_are_read(tmp_path: Path) -> None:
    from ngrunner.guards.nest_state import NestState
    from ngrunner.store import NestRecord

    record = NestRecord(folder=JOB, material="3-4 Egger Classic White", studio_id=None,
                        state=NestState.DO_NOT_CUT.value, basis="programs",
                        reasons=("8 cam holes are doubled in programs TESS02",), opt_sha256=None,
                        manifest={}, room_hashes={}, posted_at=None, checked_at=None, trial=True,
                        george_told_at=None, updated_at=TAKEN)
    text = texts(run(snapshot(tmp_path), nests=(record,)), "nest_state")[0]
    assert "DO NOT CUT" in text
    assert "8 cam holes are doubled" in text


@pytest.mark.parametrize("name", ["NOT_POSTED", "NOT_CHECKED", "SAVE_NEST", "DO_NOT_CUT",
                                  "NEST_CHECKED"])
def test_every_real_state_is_one_the_preflight_knows(tmp_path: Path, name: str) -> None:
    from ngrunner.guards.nest_state import NestState

    state = NestState[name]
    result = run(snapshot(tmp_path), nests=(Nest("3-4 Egger Classic White", state),))
    assert ("nest_state" in codes(result)) is (name != "NEST_CHECKED")
    assert not any("could not verify" in text.casefold() for text in texts(result, "nest_state"))


def test_a_state_the_preflight_does_not_know_could_not_be_verified(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path), nests=(Nest("3-4 Egger Classic White", "something_new"),))
    assert "could not verify" in texts(result, "nest_state")[0].casefold()


def test_no_nest_records_at_all_is_reported_never_taken_as_clean(tmp_path: Path) -> None:
    assert codes(run(snapshot(tmp_path), nests=())).count("nest_state") == 1


# ----- the crosscheck


def test_no_check_file_is_reported(tmp_path: Path) -> None:
    assert "no_check" in codes(run(snapshot(tmp_path), check=None))


def test_a_check_of_another_job_counts_as_no_check(tmp_path: Path) -> None:
    other = CheckFile(at=GOOD_CHECK.at, verdict="clean", checkers=(), job=OTHER)
    assert "no_check" in codes(run(snapshot(tmp_path), check=other))


def test_a_flagged_check_names_its_checkers(tmp_path: Path) -> None:
    flagged = CheckFile(at=GOOD_CHECK.at, verdict="flagged",
                        checkers=("drill depth check", "parm check"), job=JOB)
    result = run(snapshot(tmp_path), check=flagged)
    assert "drill depth check" in texts(result, "check_flagged")[0]
    assert "parm check" in texts(result, "check_flagged")[0]


def test_a_flagged_check_with_code_like_checker_names_still_passes_the_lint(tmp_path: Path) -> None:
    flagged = CheckFile(at=GOOD_CHECK.at, verdict="flagged", checkers=("drill_depth_check",),
                        job=JOB)
    result = run(snapshot(tmp_path), check=flagged)
    assert check_line(texts(result, "check_flagged")[0]) == []
    assert "drill depth check" in texts(result, "check_flagged")[0]


def test_a_check_older_than_the_design_is_reported(tmp_path: Path) -> None:
    check = CheckFile(at=datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc), verdict="clean",
                      checkers=(), job=JOB)
    result = run(snapshot(tmp_path), check=check, catalog=DESIGN_DAY)
    assert "check_older_than_design" in codes(result)


def test_a_check_on_the_design_day_could_not_be_verified(tmp_path: Path) -> None:
    check = CheckFile(at=datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc), verdict="clean",
                      checkers=(), job=JOB)
    result = run(snapshot(tmp_path), check=check, catalog=DESIGN_DAY)
    assert "could not verify" in texts(result, "check_same_day")[0].casefold()


def test_the_days_compared_are_pacific_days(tmp_path: Path) -> None:
    # 03:49 UTC on 10-21 is 8:49 PM on 10-20 in Los Angeles: the design's day, not the day after
    check = CheckFile(at=datetime(2026, 10, 21, 3, 49, tzinfo=timezone.utc), verdict="clean",
                      checkers=(), job=JOB)
    assert "check_same_day" in codes(run(snapshot(tmp_path), check=check, catalog=DESIGN_DAY))


def test_a_check_newer_than_the_design_has_no_item(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path), check=GOOD_CHECK, catalog=DESIGN_DAY)
    for code in ("no_check", "check_flagged", "check_older_than_design", "check_same_day"):
        assert code not in codes(result)


def test_a_check_with_no_design_date_to_compare_could_not_be_verified(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path), check=GOOD_CHECK, catalog=None)
    assert "could not verify" in texts(result, "check_same_day")[0].casefold()


# ----- the labels fallback only ever adds an item


def _labels_result(pieces: tuple[Piece, ...]):
    label = LabelPart(part="R1C2", name="Top", w_in=14.0, l_in=30.0,
                      material="3-4 Egger Classic White", sheet="1")
    return labels_in_design((label,), pieces)


def test_labels_missing_from_the_design_add_could_not_verify(tmp_path: Path) -> None:
    missing = _labels_result((Piece(part="R1C2", name="Top", w_in=14.0, l_in=40.0, qty=1),))
    result = run(snapshot(tmp_path), labels=missing)
    text = texts(result, "labels_not_in_design")
    assert len(text) == 1
    assert "could not verify" in text[0].casefold()
    assert "DO NOT CUT" not in text[0]


@pytest.mark.parametrize("pieces", [
    (Piece(part="R1C2", name="Top", w_in=14.0, l_in=30.0, qty=1),), ()])
def test_labels_found_or_unreadable_add_no_item(tmp_path: Path, pieces) -> None:
    result = run(snapshot(tmp_path), labels=_labels_result(pieces))
    assert "labels_not_in_design" not in codes(result)


def test_labels_found_never_clear_a_nest_item(tmp_path: Path) -> None:
    found = _labels_result((Piece(part="R1C2", name="Top", w_in=14.0, l_in=30.0, qty=1),))
    result = run(snapshot(tmp_path), nests=(Nest("3-4 Egger Classic White", "not_checked"),),
                 labels=found)
    assert codes(result).count("nest_state") == 1


# ----- every text a person sees


def _everything(tmp_path: Path) -> Preflight:
    snap = snapshot(
        tmp_path,
        sheets=[sheet_row(material="Tafisa Spring Blossom_L543 (3/4)", price="", cost=""),
                sheet_row(material="Tafisa Spring Blossom_L543 (3/4)", price="", cost=""),
                sheet_row(material="Egger Classic White (1/4) 5x9", cost="$0.00")],
        tape=[tape_row(material='Tafisa Moonlight_Blue Stone 1mm — 15/16" / 1"', price="", cost=""),
              tape_row(material='Egger Classic White 2mm — 1-1/4" / 1-5/16"', cost="$0.00")],
        jobs=[job_row(status="⚠ Review")],
        sheet_keys=[["Egger Classic White (1/4) 5x9 ", "$60.00", "", ""],
                    ["Egger Classic White (1/4) 5x9", "$65.00", "", ""]])
    flagged = CheckFile(at=datetime(2026, 10, 1, tzinfo=timezone.utc), verdict="flagged",
                        checkers=("drill_depth_check",), job=JOB)
    label = LabelPart(part="R1C2", name="Top", w_in=14.0, l_in=30.0, material="x", sheet="1")
    return run(snap, nests=(Nest("3-4 Tafisa Spring Blossom_L543", "do_not_cut",
                                 ("A program is on 61x109 and this colour is 49x97",)),
                            Nest("1-4 Egger Classic White", "not_checked")),
               check=flagged, labels=labels_in_design(
                   (label,), (Piece(part="R9C9", name="Top", w_in=1.0, l_in=1.0, qty=1),)))


def test_every_code_in_a_bad_job_shows_and_every_text_passes_the_lint(tmp_path: Path) -> None:
    result = _everything(tmp_path)
    seen = set(codes(result))
    assert {"duplicate_row", "not_priced", "zero_cost", "key_spelled_twice", "status_unknown",
            "nest_state", "check_flagged", "check_older_than_design",
            "labels_not_in_design"} <= seen
    for item in result.items:
        assert check_line(item.text) == [], item.code


def test_a_material_the_lint_refuses_is_replaced_never_shown(tmp_path: Path) -> None:
    hostile = r"C:\Users\someone\secret folder\1mm job"
    result = run(snapshot(tmp_path, sheets=[sheet_row(material=hostile, price="", cost="")]))
    for item in result.items:
        assert check_line(item.text) == []
        assert "secret" not in item.text
    assert "not_priced" in codes(result)


def test_a_status_the_lint_refuses_is_replaced_never_shown(tmp_path: Path) -> None:
    result = run(snapshot(tmp_path, jobs=[job_row(status="see jim@example.com")]))
    assert "status_unknown" in codes(result)
    for item in result.items:
        assert check_line(item.text) == []
        assert "example.com" not in item.text


def test_no_customer_or_note_reaches_any_item(tmp_path: Path) -> None:
    for item in _everything(tmp_path).items:
        assert CUSTOMER not in item.text
        assert NOTE not in item.text


# ----- the crosscheck file


def _check_file(tmp_path: Path, body: str, name: str = "Testerson_101426_crosscheck.txt") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


CLEAN_FILE = (f"{JOB} - crosscheck 2026-10-21T03:49Z\n  rooms   Room0.des, Room1.des\n"
              f"  against /mnt/user-data/outputs/{JOB}_IC_Conversion.json\n  CROSSCHECK CLEAN\n\n"
              "== drill_depth_check ==\nall good\n")


def test_a_clean_check_file_is_read(tmp_path: Path) -> None:
    check = read_check_file(_check_file(tmp_path, CLEAN_FILE))
    assert check == CheckFile(at=datetime(2026, 10, 21, 3, 49, tzinfo=timezone.utc),
                              verdict="clean", checkers=(), job=JOB)


def test_a_flagged_check_file_gives_its_checkers(tmp_path: Path) -> None:
    body = CLEAN_FILE.replace("CROSSCHECK CLEAN",
                              "CROSSCHECK FLAGGED: drill_depth_check, parm_check")
    check = read_check_file(_check_file(tmp_path, body))
    assert check.verdict == "flagged"
    assert check.checkers == ("drill_depth_check", "parm_check")


def test_a_check_file_with_windows_line_ends_is_read(tmp_path: Path) -> None:
    path = tmp_path / "x.txt"
    path.write_bytes(CLEAN_FILE.replace("\n", "\r\n").encode("utf-8"))
    assert read_check_file(path).verdict == "clean"


@pytest.mark.parametrize("body", [
    "",
    "not a check at all\n",
    "# Testerson crosscheck (rev 3)\n\nCROSSCHECK CLEAN\n",
    f"{JOB} - crosscheck yesterday\n a\n b\n CROSSCHECK CLEAN\n",
    f"{JOB} - crosscheck 2026-10-21T03:49Z\n a\n b\n PANELS LOOK FINE\n",
    f"{JOB} - crosscheck 2026-10-21T03:49Z\n CROSSCHECK CLEAN\n",
    f"{JOB} - crosscheck 2026-10-21T03:49Z\n a\n b\n CROSSCHECK FLAGGED:\n",
    f"{JOB} - crosscheck 2026-10-21T03:49Z\n a\n b\n c\n CROSSCHECK CLEAN\n",
    f"{JOB} - crosscheck 2026-10-21T03:49Z\n a\n b\n",
])
def test_older_or_other_forms_count_as_no_check(tmp_path: Path, body: str) -> None:
    assert read_check_file(_check_file(tmp_path, body)) is None


def test_a_missing_check_file_counts_as_no_check(tmp_path: Path) -> None:
    assert read_check_file(tmp_path / "absent.txt") is None


def test_a_check_file_that_is_not_text_counts_as_no_check(tmp_path: Path) -> None:
    path = tmp_path / "x.txt"
    path.write_bytes(b"\xff\xfe\x00\x01not text")
    assert read_check_file(path) is None


# ----- the job sheet


WORD = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _docx(path: Path, paragraphs: list[str]) -> Path:
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>'
                   for p in paragraphs)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml",
                   f'<?xml version="1.0" encoding="UTF-8"?><w:document {WORD}><w:body>{body}'
                   "</w:body></w:document>")
    return path


def test_a_job_sheet_gives_its_name_abbreviation_install_and_counts(tmp_path: Path) -> None:
    sheet = read_job_sheet(_docx(tmp_path / "s.docx", [
        f"Job Name: {JOB}", "Job Abbr: TES", "Install Date: 10/5", "",
        "¾”: 6", "Color: Classic 4x8", "File name: ", "",
        "¾”: 1", "Color: Classic 5x9", "File name:", "",
        "¼”: 2", "Color: White vinyl", "",
        "1”: 1 ", "", "Special Notes: Fakeclient wants a call first"]))
    assert (sheet.name, sheet.abbr, sheet.install) == (JOB, "TES", "10/5")
    assert [(c.thickness, c.sheets, c.color) for c in sheet.counts] == [
        ("3/4", 6, "Classic 4x8"), ("3/4", 1, "Classic 5x9"), ("1/4", 2, "White vinyl"),
        ("1", 1, "")]


def test_special_notes_are_never_kept(tmp_path: Path) -> None:
    sheet = read_job_sheet(_docx(tmp_path / "s.docx", [
        f"Job Name: {JOB}", "Special Notes: Fakeclient wants a call first"]))
    assert "Fakeclient" not in repr(sheet)


def test_nothing_below_special_notes_is_read_even_if_it_looks_like_a_line(tmp_path: Path) -> None:
    sheet = read_job_sheet(_docx(tmp_path / "s.docx", [
        f"Job Name: {JOB}", "1”: 1", "Special Notes: call first", "Color: Fakeclient gate code",
        "¾”: 99", "Job Abbr: FAKE"]))
    assert "Fakeclient" not in repr(sheet)
    assert [c.sheets for c in sheet.counts] == [1]
    assert sheet.abbr == ""


def test_an_install_date_on_following_lines_is_joined_as_typed(tmp_path: Path) -> None:
    sheet = read_job_sheet(_docx(tmp_path / "s.docx", [
        f"Job Name: {JOB}", "Job Abbr: TES", "Install Date: ", "Friday", "10/16", "", "¾”: 8"]))
    assert sheet.install == "Friday 10/16"


def test_labels_with_a_space_before_the_colon_read_too(tmp_path: Path) -> None:
    sheet = read_job_sheet(_docx(tmp_path / "s.docx", [
        f"Job Name : {JOB}", "Job Abbr : TES", "Install Date : 10/5", "3/4”: 4", "Color: Classic"]))
    assert (sheet.name, sheet.abbr, sheet.install) == (JOB, "TES", "10/5")
    assert [(c.thickness, c.sheets) for c in sheet.counts] == [("3/4", 4)]


def test_a_count_left_blank_is_none_never_zero(tmp_path: Path) -> None:
    sheet = read_job_sheet(_docx(tmp_path / "s.docx",
                                 ["Job Name: ", "¾”: ", "Color: ", "¼”", "Color"]))
    assert sheet.name == ""
    assert [c.sheets for c in sheet.counts] == [None, None]


def _zip_holding(path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)


@pytest.mark.parametrize("make", [
    lambda p: p.write_bytes(b"not a zip at all"),
    lambda p: _zip_holding(p, {}),                                  # a zip with no document in it
    lambda p: _zip_holding(p, {"word/other.xml": "<a/>"}),
    lambda p: _zip_holding(p, {"word/document.xml": "<not closed"}),
])
def test_a_file_that_is_no_job_sheet_gives_none(tmp_path: Path, make) -> None:
    path = tmp_path / "s.docx"
    make(path)
    assert read_job_sheet(path) is None


def test_a_missing_job_sheet_gives_none(tmp_path: Path) -> None:
    assert read_job_sheet(tmp_path / "absent.docx") is None


# ----- real files


def _real_index(folder: str) -> list[dict[str, str]]:
    path = FIXTURES / "p1b" / folder / "index.json"
    if not path.is_file():
        pytest.skip(f"no local {folder} copy: run tools/collect_p1b_documents.py")
    return json.loads(path.read_text(encoding="utf-8"))


def _real_snapshot():
    _real_index("tracker")
    return read_tracker(FIXTURES / "p1b" / "tracker")


def _catalog_days() -> dict[str, date]:
    from ngrunner.guards.labels import catalog_date, read_catalog
    _real_index("labels")
    rows = read_catalog((FIXTURES / "p1b" / "labels" / "parts_catalog.csv").read_bytes())
    return {job: catalog_date(rows, job) for job in {row["Job"] for row in rows}}


@pytest.mark.fixtures
def test_on_the_real_snapshot_a_preflight_of_every_job_counts_as_measured() -> None:
    snap = _real_snapshot()
    now = snap.taken + timedelta(minutes=5)     # a fixed now: the copy on disk does not move
    seen: dict[str, int] = {}
    sheet_rows = tape_rows = 0
    for job in {row.job for row in (*snap.sheets, *snap.tape, *snap.jobs)}:
        for item in preflight(job, snap, (), None, None, now=now).items:
            seen[item.code] = seen.get(item.code, 0) + 1
            problems = len(check_line(item.text))
            assert problems == 0, item.code
            if item.code == "not_priced":
                sheet_rows += " feet " not in item.text
                tape_rows += " feet " in item.text
    assert "snapshot_old" not in seen
    assert seen.get("duplicate_row", 0) == 0
    assert (sheet_rows, tape_rows) == (6, 1)
    assert seen.get("zero_cost", 0) == 0
    assert seen["key_spelled_twice"] == 1
    assert seen["status_unknown"] == 60     # one per Job Summary row, each reported as typed


@pytest.mark.fixtures
def test_the_real_snapshot_read_too_late_gives_only_could_not_verify() -> None:
    snap = _real_snapshot()
    job = snap.jobs[0].job
    late = snap.taken + timedelta(minutes=20, seconds=1)
    found = [item.code for item in preflight(job, snap, (), None, None, now=late).items]
    assert found == ["snapshot_old"]


@pytest.mark.fixtures
def test_the_real_crosscheck_files_read_and_compare_to_their_design_days() -> None:
    index = [e for e in _real_index("checks") if e["role"] == "crosscheck"]
    days = _catalog_days()
    files = len(index)
    assert files == 5
    older = same = verdicts_flagged = named_right = 0
    snap = _real_snapshot()
    now = snap.taken + timedelta(minutes=5)
    for entry in index:
        check = read_check_file(FIXTURES / "p1b" / "checks" / entry["file"])
        read = check is not None
        assert read, entry["file"]
        named_right += check.job == entry["job"]
        verdicts_flagged += check.verdict == "flagged"
        # .get, not []: a KeyError would print the job's real name
        found = [i.code for i in preflight(entry["job"], snap, (), check, days.get(entry["job"]),
                                           now=now).items]
        older += "check_older_than_design" in found
        same += "check_same_day" in found
    assert named_right == 5
    assert verdicts_flagged == 2
    assert (older, same) == (2, 3)


@pytest.mark.fixtures
def test_every_real_job_sheet_parses() -> None:
    index = _real_index("jobsheets")
    files = len(index)
    assert files == 63
    sheets = [read_job_sheet(FIXTURES / "p1b" / "jobsheets" / e["file"]) for e in index]
    parsed = sum(s is not None for s in sheets)
    assert parsed == 63
    # one is the blank form that sits at the top of NG Jobs, with no job's name in it
    named = sum(bool(s.name) for s in sheets)
    assert named == 62


@pytest.mark.fixtures
def test_case_b_job_sheet_has_one_line_reading_5x9() -> None:
    index = _real_index("jobsheets")    # first: it skips when the fixtures are absent
    key = json.loads((FIXTURES / "p1b" / "case_key.json").read_text(encoding="utf-8"))
    entries = [e for e in index if e["job"] == key["B"]]
    found = len(entries)
    assert found == 1
    sheet = read_job_sheet(FIXTURES / "p1b" / "jobsheets" / entries[0]["file"])
    lines = sum("5x9" in c.color for c in sheet.counts)
    assert lines == 1


@pytest.mark.fixtures
def test_no_real_job_sheet_keeps_its_special_notes() -> None:
    from ngrunner.guards.preflight import _paragraphs
    index = _real_index("jobsheets")
    leaked = 0
    for entry in index:
        path = FIXTURES / "p1b" / "jobsheets" / entry["file"]
        notes = [p.split(":", 1)[1].strip() for p in _paragraphs(path.read_bytes())
                 if p.startswith("Special Notes") and ":" in p]
        kept = repr(read_job_sheet(path))
        leaked += sum(1 for note in notes if len(note) >= 8 and note in kept)
    assert leaked == 0


def test_a_tracker_that_wont_read_gives_one_line_in_plain_words_and_nothing_else() -> None:
    result = could_not_read_tracker("The tracker's Pricing snapshot was cut short", NOW)
    assert codes(result) == ["tracker_unreadable"]
    assert texts(result, "tracker_unreadable") == [
        "The tracker's Pricing snapshot was cut short, so this pre-flight could not verify anything"]
    assert check_line(result.items[0].text) == []


def test_a_tracker_reason_the_lint_would_stop_reads_as_the_fixed_line() -> None:
    result = could_not_read_tracker(r"G:\a\path was cut short", NOW)
    assert texts(result, "tracker_unreadable") == [_FIXED["tracker_unreadable"]]
