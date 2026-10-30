"""What differs between two renders, in words: a catalog update must not read like a redesign."""
from __future__ import annotations

from ngrunner.intake.report_diff import ReportChange, compare


def _row(number: str, quantity: str, name: str, size: str) -> list[str]:
    return [number, quantity, name, size]


CLOSET_ROD = _row("400000465", "4", "Rod Stl 96in Chrome Plated", '41 3/4"W x 1"H')
NEW_CLOSET_ROD = _row("400006165", "4", "Rod Stl Chrome Plated", '41 3/4"W x 1"H')
SHELF = _row("400001111", "2", "Shelf Fixed White", '24"W x 12"D')
OTHER_ROD = _row("400000464", "1", "Rod Round 96in Chrome", '39 3/4"W x 1"H')
NEW_OTHER_ROD = _row("400006163", "1", "Rod Round 92in Chrome", '39 3/4"W x 1"H')
HEAD = ["PROJECT NAME Testerson_101426", "Design: Testerson - Primary Closet"]


def _report(*rows: list[str], pictures: tuple[str, ...] = ("aaa", "bbb")) -> list[tuple[list[str], list[str]]]:
    lines = list(HEAD)
    for row in rows:
        lines.extend(row)
    return [(lines, list(pictures))]


def test_part_rows_renamed_with_the_same_drawings_read_as_a_catalog_update():
    change = compare(_report(CLOSET_ROD, SHELF), _report(NEW_CLOSET_ROD, SHELF))
    assert change.only_parts and change.drawings_same
    assert change.lines == 2
    assert change.renamed == (("Rod Stl 96in Chrome Plated", "Rod Stl Chrome Plated"),)
    assert change.words() == (
        "Only part numbers or names changed, which points to a catalog update, not a redesign: "
        "Rod Stl 96in Chrome Plated is now Rod Stl Chrome Plated. The drawings and sizes are the same.")


def test_several_renamed_parts_show_one_and_count_the_rest():
    change = compare(_report(CLOSET_ROD, OTHER_ROD), _report(NEW_CLOSET_ROD, NEW_OTHER_ROD))
    assert len(change.renamed) == 2
    assert change.words().count(" is now ") == 1
    assert ", and 1 more." in change.words()


def test_the_same_rename_on_many_rows_is_one_rename():
    change = compare(_report(CLOSET_ROD, CLOSET_ROD, CLOSET_ROD), _report(NEW_CLOSET_ROD, NEW_CLOSET_ROD, NEW_CLOSET_ROD))
    assert change.lines == 6
    assert len(change.renamed) == 1
    assert "more" not in change.words()


def test_a_changed_size_is_not_only_a_catalog_update():
    wider = _row("400000465", "4", "Rod Stl 96in Chrome Plated", '50"W x 1"H')
    change = compare(_report(CLOSET_ROD), _report(wider))
    assert not change.only_parts
    assert change.words() == "1 line of text changed. The drawings are the same."


def test_a_changed_quantity_is_not_only_a_catalog_update():
    more = _row("400000465", "9", "Rod Stl 96in Chrome Plated", '41 3/4"W x 1"H')
    assert not compare(_report(CLOSET_ROD), _report(more)).only_parts


def test_a_part_added_or_taken_away_is_not_only_a_catalog_update():
    assert not compare(_report(CLOSET_ROD, SHELF), _report(CLOSET_ROD)).only_parts
    assert not compare(_report(CLOSET_ROD), _report(CLOSET_ROD, SHELF)).only_parts


def test_a_renamed_design_is_not_only_a_catalog_update():
    old = _report(CLOSET_ROD)
    new = [(["PROJECT NAME Testerson_101426", "Design: Testerson - Wet Bar"] + NEW_CLOSET_ROD, ["aaa", "bbb"])]
    change = compare(old, new)
    assert not change.only_parts
    assert change.renamed == ()
    assert "catalog" not in change.words()


def test_changed_drawings_are_never_called_a_catalog_update():
    change = compare(_report(CLOSET_ROD), _report(NEW_CLOSET_ROD, pictures=("aaa", "ccc")))
    assert not change.drawings_same
    assert "catalog" not in change.words()
    assert change.words() == "The drawings changed, and 2 lines of text."


def test_drawings_in_another_order_count_as_changed():
    assert not compare(_report(SHELF, pictures=("aaa", "bbb")), _report(SHELF, pictures=("bbb", "aaa"))).drawings_same


def test_drawings_changed_alone():
    change = compare(_report(SHELF), _report(SHELF, pictures=("ccc",)))
    assert change.words() == "The drawings changed. The text is the same."


def test_the_same_report_has_no_words():
    change = compare(_report(SHELF), _report(SHELF))
    assert not change.any
    assert change.words() == ""


def test_a_very_long_part_name_is_counted_but_not_quoted():
    long_old = _row("400000465", "4", "L" * 70, '41 3/4"W x 1"H')
    long_new = _row("400006165", "4", "N" * 70, '41 3/4"W x 1"H')
    words = compare(_report(long_old), _report(long_new)).words()
    assert "L" * 70 not in words
    assert words == ("Only part numbers or names changed, which points to a catalog update, not a redesign. "
                     "The drawings and sizes are the same.")


def test_more_lines_on_one_side_count_the_larger_side():
    change = compare(_report(SHELF, CLOSET_ROD, OTHER_ROD), _report(SHELF))
    assert change.lines == 8
    assert isinstance(change, ReportChange)
