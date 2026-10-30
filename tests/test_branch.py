"""
Tests for the P1a closet-or-garage check (ngrunner.intake.branch).

Designs are (name, category) pairs as page 1 lists them; names are made up. The categories are
Studio's own (Garage, Cabinet, Closet, His and Her Master...), which are not client data.
"""
from __future__ import annotations

from collections import Counter

import pytest

from helpers_pdf import summary_pdf
from ngrunner.intake.branch import branch_of
from ngrunner.intake.summary_check import check_summary

GARAGE = ("Testerson - Garage", "Garage")
CLOSET = ("Testerson - Primary Closet", "His and Her Master")


def test_a_job_whose_designs_are_all_garage_designs_is_a_garage():
    branch = branch_of([GARAGE, ("Testerson - Overhead Racks", "Garage")])
    assert branch.kind == "garage"
    assert branch.reason == "All 2 designs on page 1 are garage designs."


def test_a_job_with_no_garage_design_is_a_closet_cabinets_and_storage_included():
    # Studio's Cabinet and Hall/Storage categories are used for closets, bars and linen rooms
    # too: only a garage word makes a garage design.
    branch = branch_of([CLOSET, ("Testerson - Wall Cabinets", "Cabinet"),
                        ("Testerson - Linen", "Hall/Storage")])
    assert branch.kind == "closet"
    assert branch.reason == "None of the 3 designs on page 1 is a garage design."


def test_a_job_with_garage_and_other_designs_is_unclear():
    # An archived job like this one (closets and a garage) was filed as a closet job: only Jim
    # can say, so it is asked (the closet/garage card), and only this job waits.
    branch = branch_of([CLOSET, ("Testerson - Office", "Office"), ("Testerson - Garage Cabinets", "Garage")])
    assert branch.kind == "unclear"
    assert branch.reason == "Page 1 lists 1 garage design and 2 other designs."


def test_a_single_design_is_named_in_the_reason():
    assert branch_of([GARAGE]).reason == ('The only design on page 1, "Testerson - Garage",'
                                          " is a garage design.")
    assert branch_of([CLOSET]).reason == ('The only design on page 1, "Testerson - Primary Closet",'
                                          " is not a garage design.")


def test_a_job_whose_page_1_lists_no_designs_is_unclear():
    branch = branch_of([])
    assert branch.kind == "unclear"
    assert branch.reason == "Page 1 lists no designs, so closet or garage could not be verified."


# ----- what a garage word is


@pytest.mark.parametrize("design", [
    ("Testerson", "Garage"),
    ("Testerson - Garage", "Cabinet"),
    ("TESTERSON GARAGES", "All"),
    ("Testerson - Garáge2", "Closet"),
    ("Testerson_Garage_Shelves", ""),
], ids=["the category alone", "the name alone", "any case, and the plural", "accents and a number glued on",
        "underscores between words"])
def test_a_garage_word_in_the_name_or_the_category_makes_a_garage_design(design):
    assert branch_of([design]).kind == "garage"


@pytest.mark.parametrize("design", [
    ("Gar - Primary Closet", "His and Her Master"),
    ("Testerson - Garagewood Pantry", "Pantry"),
    ("Edgarson - Walk-In", "Closet"),
], ids=["Gar, a surname, though it is the pipeline's short form", "a garage word inside another word",
        "gar inside a name"])
def test_only_a_whole_garage_word_makes_a_garage_design(design):
    # Design names usually start with the client's surname, so the pipeline's "_Gar" short form is
    # no design word: a client named Gar would make every design a garage.
    assert branch_of([design]).kind == "closet"


# ----- the Fab row's room words (GLOSSARY: Room word)


@pytest.mark.parametrize("row, designs, kind", [
    ("Testerson garage", [GARAGE], "garage"),
    ("Testerson closet", [CLOSET], "closet"),
    ("Testerson walk-in", [CLOSET], "closet"),
    ("Testerson", [GARAGE], "garage"),
    ("Testerson", [CLOSET], "closet"),
    ("", [GARAGE], "garage"),
    ("Testerson garage storage", [GARAGE], "garage"),
    ("Master", [GARAGE], "garage"),
], ids=["garage, and garage designs", "closet, and closet designs", "walk-in, and closet designs",
        "no room word: garage designs", "no room word: closet designs", "an empty row",
        "a garage word says garage, whatever else the row says", "a surname that is a room word"])
def test_a_row_that_agrees_with_the_designs_or_says_nothing_leaves_them_to_decide(row, designs, kind):
    assert branch_of(designs, row).kind == kind


@pytest.mark.parametrize("row, designs, word", [
    ("Testerson garage", [CLOSET], "garage"),
    ("Testerson_101426_Gar", [CLOSET], "gar"),
    ("Testerson closet", [GARAGE], "closet"),
    ("Testerson Pantry", [GARAGE], "pantry"),
    ("Testerson cabinets", [GARAGE], "cabinets"),
], ids=["garage, and no garage design", "the pipeline's _Gar, and no garage design",
        "closet, and only garage designs", "pantry, and only garage designs",
        "a room word that is not a garage word is the closet side, so it is asked"])
def test_a_row_whose_room_words_disagree_with_the_designs_is_unclear(row, designs, word):
    branch = branch_of(designs, row)
    assert branch.kind == "unclear"
    assert f'The Fab row says "{word}", but ' in branch.reason


# ----- what it reads: check_summary's facts


def test_it_reads_check_summarys_design_list_or_the_check_itself():
    mixed = check_summary(summary_pdf(designs=(CLOSET, GARAGE)))
    assert mixed.ok
    assert branch_of(mixed.details["designs"]).kind == "unclear"
    assert branch_of(mixed).kind == "unclear"
    assert branch_of(check_summary(summary_pdf(designs=(GARAGE,))), "Testerson garage").kind == "garage"


def test_a_check_that_did_not_pass_is_unclear():
    early = check_summary(summary_pdf(designs=(GARAGE, CLOSET), finished=1))
    branch = branch_of(early)
    assert branch.kind == "unclear"
    assert branch.reason == ("The IC summary has not passed its check, so closet or garage"
                             " could not be verified.")


@pytest.mark.parametrize("designs", [["Testerson - Garage"], [("Testerson - Garage",)],
                                     [{"name": "Testerson - Garage"}], [("Testerson", None)]],
                         ids=["names alone", "a name in a tuple", "a mapping without its category",
                              "a category that is not text"])
def test_designs_without_their_categories_are_refused(designs):
    # SummaryCheck.designs holds the names only; the categories are in details["designs"]. Names
    # alone would miss a garage design whose name says nothing (Studio's category says Garage).
    with pytest.raises(TypeError):
        branch_of(designs)


# ----- real summaries (.local-fixtures): Jim's own filing is the answer; files named by index


@pytest.mark.fixtures
def test_no_archived_summary_gets_the_other_branch_than_the_one_jim_filed_it_as(summaries_dir):
    paths = sorted((summaries_dir / "ngjobs").glob("*.pdf"), key=lambda p: p.name.lower())
    if not paths:
        pytest.skip("no archived NG Jobs summaries in the fixtures")
    seen: Counter[tuple[str, str]] = Counter()
    wrong = []
    for i, path in enumerate(paths):
        filed_as = "garage" if path.stem.removesuffix("_summary").endswith("_Gar") else "closet"
        kind = branch_of(check_summary(path)).kind
        seen[filed_as, kind] += 1
        if kind not in (filed_as, "unclear"):  # unclear asks Jim: never a wrong folder
            wrong.append(f"n{i:02d}")
    assert wrong == []
    assert seen["garage", "garage"] == sum(n for (filed, _), n in seen.items() if filed == "garage")
