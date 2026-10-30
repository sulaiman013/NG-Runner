"""Job matching (design section 5 "Matching"; disk-truth section 5).

Synthetic names only. The tests marked "fixtures" read real file names at test time from
.local-fixtures and (listed, never opened) the NG Jobs folder on G:, found through a Drive
shortcut, My Drive or a shared drive unless NGR_NG_JOBS names it, and report by index, never
by name. One of them checks that no synthetic name here or in ngrunner/match.py pairs a real
job's date with another of its words, and names only the synthetic ones it finds.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pypdf import PdfReader

from ngrunner.match import (
    FabRow,
    Opportunity,
    match_by_folder,
    match_fab_row,
    match_file_to_job,
    normalize_job_file,
    room_words,
)


@dataclass(frozen=True)
class Job:
    """Stands in for the job model: matching reads only the name and the aliases."""

    studio_id: str
    name: str
    aliases: tuple[str, ...] = ()


JOBS = [
    Job("1", "Testerson_101426"),
    Job("2", "Fakeley_101426"),                # same date as job 1
    Job("3", "Fakeley_pantry_030926"),         # same surname as job 2, a room word in the name
    Job("4", "Mockford_030926_Gar"),           # same date as job 3, "_Gar" added by the pipeline
    Job("5", "Testova_082326"),                # surnames that collide
    Job("6", "Testyan_082926"),
    Job("7", "VanTest_Loft_031126"),           # mixed case, and a namesake with another date
    Job("8", "Vantest_071826"),
    Job("9", "Dummitt_011926", aliases=("Zample_Remodel_011926",)),   # renamed in Studio
    Job("10", "Samplin_092826"),
    Job("11", "MockfordJane_091026"),          # person words run together
]


# ----- normalize_job_file: decorations


@pytest.mark.parametrize("filename", [
    "Testerson_101426.pdf",                    # Assembly Reports default
    "Testerson_101426 (2026-09-22).pdf",       # Assembly Reports dated copy
    "Testerson_101426 (2026-09-22 #2).pdf",
    "Testerson_101426_summary.pdf",            # NG Jobs archive name
    "Testerson_101426 summary.pdf",            # renamed by hand
    "TESTERSON_101426.PDF",                    # mixed case
    "Testerson 101426.pdf",                    # space instead of underscore
    "Testerson_101426 (1).pdf",                # a browser's second download
    "Testerson_101426 - Copy.pdf",             # Explorer's copy
    r"C:\Users\x\Downloads\Testerson_101426.pdf",
])
def test_decorations_are_stripped_down_to_the_job_name(filename):
    assert normalize_job_file(filename).base == "testerson_101426"


@pytest.mark.parametrize("filename, kind", [
    ("Testerson_101426.pdf", "plain"),
    ("Testerson_101426 (2026-09-22 #2).pdf", "dated"),
    ("Testerson_101426_summary.pdf", "pipeline"),
    ("Testerson_101426 summary.pdf", "hand"),
    ("Testerson 101426", "hand"),
    ("Testerson remake asembly.pdf", "hand"),
])
def test_kind_says_how_the_name_was_made(filename, kind):
    assert normalize_job_file(filename).kind == kind


# ----- normalize_job_file: Studio's own names, <client>_<project>_summary.pdf


@pytest.mark.parametrize("filename, base", [
    ("testerson_testerson_101426_summary.pdf", "testerson_101426"),
    ("samplin_testerson_090926_summary.pdf", "testerson_090926"),         # client is not the surname
    ("fakeley_fakeley_pantry_030926_summary.pdf", "fakeley_pantry_030926"),  # room word stays
    ("mockford_mockfordjane_091026_summary.pdf", "mockfordjane_091026"),   # person words run together
    ("testerson_testerson_101426_summary (1).pdf", "testerson_101426"),    # downloaded twice
])
def test_studio_names_lose_their_client_slug(filename, base):
    n = normalize_job_file(filename)
    assert (n.base, n.kind) == (base, "studio")


def test_lowercase_archive_name_is_not_stripped_down_to_a_bare_date():
    n = normalize_job_file("dummitt_061626_summary.pdf")
    assert (n.base, n.kind) == ("dummitt_061626", "pipeline")


# ----- normalize_job_file: variants are runs of the parent job


@pytest.mark.parametrize("filename, variant", [
    ("Testerson_101426_Gar_summary.pdf", "gar"),    # added by the pipeline
    ("Testerson_101426remake", "remake"),
    ("Testerson_101426rev2", "rev2"),               # no separator
    ("Testerson_101426_rev2.pdf", "rev2"),
    ("Testerson_101426 Rev 3.pdf", "rev3"),
    ("Testerson_101426-2.pdf", "-2"),
    ("Testerson_101426redo2", "redo2"),
    ("Testerson_101426_Redo2", "redo2"),
    ("Testerson_101426 pickup.pdf", "pickup"),
    ("Testerson_101426_top", "top"),
    ("Testerson_101426 GB 5-13-26.pdf", "gb_5_13_26"),
    ("Testerson_101426_Claude", "claude"),
    ("Testerson_101426_Gar_rev2", "gar+rev2"),
    ("testerson_testerson_101426rev2_summary.pdf", "rev2"),
])
def test_variant_splits_off_after_the_date(filename, variant):
    n = normalize_job_file(filename)
    assert (n.base, n.variant) == ("testerson_101426", variant)


@pytest.mark.parametrize("filename, base", [
    ("Testerson_101426.pdf", "testerson_101426"),
    ("Testerson_top_101426.pdf", "testerson_top_101426"),            # before the date: part of the name
    ("Testerson_101426_garage.pdf", "testerson_101426_garage"),      # not a variant word
    ("Testerson_101426_2.pdf", "testerson_101426_2"),                # "-N" needs its hyphen
    ("Testerson_101426remakeS25.TAP", "testerson_101426remakes25_tap"),  # a program, not a summary
    ("Test Lane Garage", "test_lane_garage"),                        # no date, nothing to split
])
def test_anything_else_stays_in_the_base(filename, base):
    n = normalize_job_file(filename)
    assert (n.base, n.variant) == (base, None)


# ----- match_file_to_job


def test_a_file_match_that_forgets_the_salesforce_names_fails_closed():
    # Without them every unlisted-sale guard would be off, so the argument has no default.
    with pytest.raises(TypeError):
        match_file_to_job("Testerson_101426-2_summary.pdf", JOBS)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        match_file_to_job(  # type: ignore[call-arg]
            "Testerson_101426.des", JOBS, folder="Testerson_101426")


def test_a_folder_match_that_forgets_the_salesforce_names_fails_closed():
    with pytest.raises(TypeError):
        match_by_folder("Testerson_101426", JOBS)  # type: ignore[call-arg]


@pytest.mark.parametrize("names", ["Testerson_101426-2", b"Testerson_101426-2", None])
def test_salesforce_names_that_are_no_list_are_refused(names):
    # One name would be read as its letters, and None as no list, turning every guard off.
    jobs = [Job("1", "Testerson_101426")]
    with pytest.raises(TypeError):
        match_file_to_job("Testerson_101426-2_summary.pdf", jobs, opportunity_names=names)
    with pytest.raises(TypeError):
        match_file_to_job("Testerson_101426_summary.pdf", jobs, opportunity_names=names)
    with pytest.raises(TypeError):
        match_file_to_job("Room1.des", jobs, folder="Testerson_101426", opportunity_names=names)
    with pytest.raises(TypeError):
        match_by_folder("Testerson_101426", jobs, opportunity_names=names)


@pytest.mark.parametrize("filename, folder", [
    ("Testerson_101426-2_rev2.pdf", None),
    ("Testerson_101426-2_rev2.pdf", "Testerson_101426-2"),
    ("Testerson_101426-2_rev2.pdf", "Testerson_101426"),
    ("Testerson_101426_2.des", "Testerson_101426"),
    ("testerson_pat_testerson_101426_summary.pdf", "Testerson_101426"),
])
def test_salesforce_names_count_on_every_pass_when_they_can_be_read_only_once(filename, folder):
    jobs = [Job("1", "Testerson_101426")]
    names = ["Testerson_101426", "Testerson_101426-2", "Testerson_101426_2", "Pat_Testerson_101426"]
    once = match_file_to_job(filename, jobs, folder=folder, opportunity_names=iter(names))
    assert once == match_file_to_job(filename, jobs, folder=folder, opportunity_names=names)


@pytest.mark.parametrize("filename, studio_id", [
    ("Testerson_101426 (2026-09-22 #2).pdf", "1"),
    ("testerson_testerson_101426_summary.pdf", "1"),
    ("TESTERSON_101426.PDF", "1"),
    ("Fakeley_pantry_030926.pdf", "3"),
    ("fakeley_fakeley_pantry_030926_summary.pdf", "3"),
    ("Mockford_030926 (2026-09-22).pdf", "4"),           # Studio's name has no "_Gar"
    ("mockford_mockford_030926_summary.pdf", "4"),
    ("Mockford_030926_Gar_summary.pdf", "4"),
    ("vantest_vantest_loft_031126_summary.pdf", "7"),
    ("Vantest_loft_031126.pdf", "7"),
    ("Samplin 092826.pdf", "10"),                         # space instead of underscore
    ("samplin_samplin_092826_summary.pdf", "10"),
    ("mockford_mockfordjane_091026_summary.pdf", "11"),
    ("Testyan_082926rev2.pdf", "6"),                      # a revision belongs to its job
    ("Testova_082326remake.pdf", "5"),
    ("dummitt_zample_remodel_011926_summary.pdf", "9"),  # renamed in Studio: found by its alias
])
def test_file_matches_its_job(filename, studio_id):
    r = match_file_to_job(filename, JOBS, opportunity_names=())
    assert (r.status, r.job and r.job.studio_id) == ("matched", studio_id)


@pytest.mark.parametrize("filename", [
    "Testersen_101426.pdf",          # surname typo: never fuzzy
    "Testerson_101462.pdf",          # date typo
    "Testerson.pdf",                 # surname alone
    "101426.pdf",                    # date alone, and two jobs share it
    "Fakeley_030926.pdf",            # room word missing
    "Testerson_030926.pdf",          # one job's surname with another job's date
    "Testova_082926.pdf",            # colliding surname with the other one's date
    "Testyan remake asembly.pdf",    # hand-named: only its folder can say
    "Testerson_101426.pdf.part",     # unfinished download
])
def test_near_misses_never_match(filename):
    r = match_file_to_job(filename, JOBS, opportunity_names=())
    assert (r.status, r.job, bool(r.reason)) == ("none", None, True)


def test_renamed_project_is_never_matched_on_its_client_slug_and_date():
    jobs = [Job("9", "Dummitt_011926")]                  # the later name is not recorded yet
    r = match_file_to_job("dummitt_zample_remodel_011926_summary.pdf", jobs, opportunity_names=())
    assert r.status == "none"


def test_match_through_a_later_name_says_so():
    r = match_file_to_job("Zample_Remodel_011926.pdf", JOBS, opportunity_names=())
    assert r.reason == "alias"


def test_two_jobs_with_one_name_are_ambiguous():
    jobs = [Job("1", "Testerson_101426"), Job("12", "Testerson_101426")]
    r = match_file_to_job("Testerson_101426.pdf", jobs, opportunity_names=())
    assert (r.status, r.job, r.candidates) == ("ambiguous", None, tuple(jobs))


def test_closet_and_garage_jobs_sharing_a_base_are_told_apart_only_by_their_exact_name():
    jobs = [Job("1", "Testerson_101426"), Job("13", "Testerson_101426_Gar")]
    def by_name(filename: str):
        return match_file_to_job(filename, jobs, opportunity_names=())

    assert by_name("Testerson_101426.pdf").job is jobs[0]
    assert by_name("Testerson_101426_Gar_summary.pdf").job is jobs[1]
    assert by_name("Testerson_101426rev2.pdf").status == "ambiguous"


@pytest.mark.parametrize("filename", [
    "Testerson_101426_summary.pdf",      # the parent's own name
    "Testerson_101426rev2.pdf",          # a revision of the parent
])
def test_a_job_that_is_itself_a_variant_never_takes_the_parents_files(filename):
    jobs = [Job("27", "Testerson_101426-2")]             # "-N" can be a job of its own
    r = match_file_to_job(filename, jobs, opportunity_names=())
    assert (r.status, r.job, r.reason) == ("none", None, "no_job")


def test_the_parents_folder_is_not_a_variant_jobs_folder():
    r = match_by_folder("Testerson_101426", [Job("27", "Testerson_101426-2")], opportunity_names=())
    assert (r.status, r.job, r.reason) == ("none", None, "no_job")


PARENT_AND_RUN = [Job("1", "Testerson_101426"), Job("27", "Testerson_101426-2")]
RUNS_OF_THE_RUN = [
    "Testerson_101426-2 remake_summary.pdf",
    "Testerson_101426-2remake.pdf",
    "Testerson_101426-2_rev2.pdf",
    "Testerson_101426-2_Gar_rev2_summary.pdf",
]


@pytest.mark.parametrize("filename", RUNS_OF_THE_RUN)
def test_a_run_of_a_run_job_is_never_given_to_the_parent(filename):
    r = match_file_to_job(filename, PARENT_AND_RUN, opportunity_names=())
    got = (r.status, r.reason, [j.studio_id for j in r.candidates])
    assert got == ("ambiguous", "several_jobs", ["27", "1"])     # the longer run first


@pytest.mark.parametrize("filename", RUNS_OF_THE_RUN)
def test_a_run_of_a_run_job_belongs_to_that_job_when_the_parent_is_not_listed(filename):
    r = match_file_to_job(filename, PARENT_AND_RUN[1:], opportunity_names=())
    assert (r.status, r.job and r.job.studio_id, r.reason) == ("matched", "27", "base")


@pytest.mark.parametrize("folder, status, candidates", [
    ("Testerson_101426-2", "matched", ["27"]),
    ("Testerson_101426", "ambiguous", ["1", "27"]),
])
def test_a_run_of_a_run_job_is_filed_only_in_that_jobs_folder(folder, status, candidates):
    r = match_file_to_job("Testerson_101426-2_rev2.pdf", PARENT_AND_RUN, folder=folder,
                          opportunity_names=())
    assert (r.status, [j.studio_id for j in r.candidates]) == (status, candidates)


@pytest.mark.parametrize("filename", [
    "Testerson_101426-2_summary.pdf", "Testerson_101426-2 (2026-09-22).pdf",
    "Testerson_101426-2_Gar_summary.pdf",
])
def test_the_summary_of_a_sale_named_like_a_run_is_not_given_to_the_parent(filename):
    jobs = PARENT_AND_RUN[:1]                          # NG Runner has no job for the "-2" sale
    sales = ["Testerson_101426", "Testerson_101426-2"]
    r = match_file_to_job(filename, jobs, opportunity_names=sales)
    assert (r.status, r.job, r.reason) == ("none", None, "no_job")


@pytest.mark.parametrize("folder, expected", [
    (None, ("none", "no_job", ())),
    ("Testerson_101426", ("ambiguous", "folder_disagrees", ("1",))),       # the parent's folder
    ("Testerson_101426_Gar", ("ambiguous", "folder_disagrees", ("1",))),   # its garage folder
])
@pytest.mark.parametrize("filename", [
    "Testerson_101426-2_rev2.pdf",                 # a revision of the "-2" sale
    "Testerson_101426-2 remake_summary.pdf",       # a remake of it
    "Testerson_101426-2-2_summary.pdf",            # a run of it
])
def test_a_run_of_a_sale_named_like_a_run_is_not_given_to_the_parent(filename, folder, expected):
    jobs = PARENT_AND_RUN[:1]                          # NG Runner has no job for the "-2" sale
    sales = ["Testerson_101426", "Testerson_101426-2"]
    r = match_file_to_job(filename, jobs, folder=folder, opportunity_names=sales)
    assert (r.status, r.reason, tuple(j.studio_id for j in r.candidates)) == expected


@pytest.mark.parametrize("names, expected", [
    (["Testerson_101426", "Testerson_101426-2"], ("none", None)),       # the "-2" sale's folder
    (["Testerson_101426"], ("matched", PARENT_AND_RUN[0])),            # no "-2" sale: a run folder
])
@pytest.mark.parametrize("folder", [
    "Testerson_101426-2", "Testerson_101426-2_Gar", "Testerson_101426-2 remake",
])
@pytest.mark.parametrize("filename", [
    "Room1.des", "Testerson remake asembly.pdf", "Testerson_101426.des",
])
def test_a_folder_named_for_a_sale_with_no_job_is_never_the_parents(
    filename, folder, names, expected
):
    r = match_file_to_job(filename, PARENT_AND_RUN[:1], folder=folder, opportunity_names=names)
    assert (r.status, r.job) == expected


@pytest.mark.parametrize("folder", [
    "Testerson_101426-2", "Testerson_101426-2_Gar", "Testerson_101426-2 remake",
])
@pytest.mark.parametrize("filename", [
    "Testerson_101426_summary.pdf", "Testerson_101426.pdf", "Testerson_101426_rev2.pdf",
    "testerson_testerson_101426_summary.pdf",
])
def test_a_file_naming_a_job_in_the_folder_of_a_sale_with_no_job_asks(filename, folder):
    # With the "-2" sale listed this is "folder_disagrees"; with it unlisted, so is this.
    jobs = PARENT_AND_RUN[:1]
    sales = ["Testerson_101426", "Testerson_101426-2"]
    r = match_file_to_job(filename, jobs, folder=folder, opportunity_names=sales)
    assert (r.status, r.job, r.reason, r.candidates) == (
        "ambiguous", None, "folder_disagrees", (jobs[0],))


@pytest.mark.parametrize("jobs", [
    [Job("1", "Testerson_101426")],
    [Job("1", "Testerson_101426"), Job("12", "Testerson_101426")],   # the name is unclear as well
])
def test_a_file_naming_a_job_in_the_folder_of_another_familys_sale_with_no_job_asks(jobs):
    r = match_file_to_job("Testerson_101426_summary.pdf", jobs, folder="Fakeley_101426",
                          opportunity_names=["Testerson_101426", "Fakeley_101426"])
    assert (r.status, r.job, r.reason, r.candidates) == (
        "ambiguous", None, "folder_disagrees", tuple(jobs))


@pytest.mark.parametrize("folder, names", [
    ("Fakeley_101426", ["Testerson_101426"]),        # no job, and no sale Salesforce knows of
    ("Testerson_101426-2", []),                      # no list: only a run's folder of the job
    ("Old", ["Testerson_101426", "Old"]),            # no job name, whatever a sale is called
    ("Parts requested", ["Testerson_101426"]),
])
def test_a_folder_that_is_no_known_sale_leaves_the_file_to_its_name(folder, names):
    jobs = [Job("1", "Testerson_101426")]
    r = match_file_to_job("Testerson_101426_summary.pdf", jobs, folder=folder,
                          opportunity_names=names)
    assert (r.status, r.job, r.reason) == ("matched", jobs[0], "exact")


@pytest.mark.parametrize("filename", [
    "Testerson_101426 remake_summary.pdf", "Testerson_101426_rev2.pdf",
])
def test_a_run_of_the_parent_stays_the_parents_when_salesforce_has_a_run_named_sale(filename):
    sales = ["Testerson_101426", "Testerson_101426-2"]     # "-2" never leads "remake" or "rev2"
    r = match_file_to_job(filename, PARENT_AND_RUN[:1], opportunity_names=sales)
    assert (r.status, r.job, r.reason) == ("matched", PARENT_AND_RUN[0], "base")


@pytest.mark.parametrize("jobs", [PARENT_AND_RUN[:1], [Job("13", "Testerson_101426_Gar")]])
def test_a_run_still_goes_to_its_parent_when_salesforce_has_no_such_sale(jobs):
    r = match_file_to_job("Testerson_101426-2_summary.pdf", jobs,
                          opportunity_names=["Testerson_101426"])   # a "_Gar" job is that sale
    assert (r.status, r.job, r.reason) == ("matched", jobs[0], "base")


def test_gar_added_by_the_pipeline_still_finds_a_job_that_is_a_variant():
    jobs = [Job("27", "Testerson_101426-2")]
    r = match_file_to_job("Testerson_101426-2_Gar_summary.pdf", jobs, opportunity_names=())
    assert (r.status, r.job) == ("matched", jobs[0])


@pytest.mark.parametrize("filename, jobs", [
    # Job 21's own archive name, lowercase, also reads as Studio's name for job 22.
    ("testerson_fakeley_101426_summary.pdf",
     [Job("21", "Testerson_Fakeley_101426"), Job("22", "Fakeley_101426")]),
    # Studio's name with a two-word client slug ("Pat Testerson") reads as either job.
    ("pat_testerson_garage_101426_summary.pdf",
     [Job("23", "Garage_101426"), Job("24", "Testerson_Garage_101426")]),
])
def test_lowercase_name_whose_readings_name_different_jobs_is_offered_not_matched(filename, jobs):
    r = match_file_to_job(filename, jobs, opportunity_names=())
    assert (r.status, r.job, r.reason, r.candidates) == (
        "ambiguous", None, "client_slug_unclear", tuple(jobs))


def test_every_split_of_the_client_slug_is_a_reading():
    # Studio's name for client "Pat De Test": the project could start after any of its words.
    jobs = [Job("25", "Test_Garage_101426"), Job("26", "Garage_101426")]
    r = match_file_to_job("pat_de_test_garage_101426_summary.pdf", jobs, opportunity_names=())
    assert (r.status, r.reason, r.candidates) == ("ambiguous", "client_slug_unclear", tuple(jobs))


@pytest.mark.parametrize("filename, listed, salesforce", [
    # Studio's name for client "Testerson", project "Pat_Testerson_101426", a sale not listed.
    ("testerson_pat_testerson_101426_summary.pdf", "Testerson_101426", "Pat_Testerson_101426"),
    # The lowercase archive name of the unlisted "Testgar_Fakelop_101426".
    ("testgar_fakelop_101426_summary.pdf", "Fakelop_101426", "Testgar_Fakelop_101426"),
])
def test_a_reading_that_names_an_unlisted_sale_makes_the_name_unclear(filename, listed, salesforce):
    jobs = [Job("40", listed)]
    r = match_file_to_job(filename, jobs, opportunity_names=[listed, salesforce])
    assert (r.status, r.reason, r.candidates) == ("ambiguous", "unlisted_sale", (jobs[0],))


# Studio's names whose main reading is a sale NG Runner has no job for, while another reading
# is a listed job: (file, listed job, the unlisted sale).
UNLISTED_READINGS = [
    ("testerson_pat_testerson_101426_summary.pdf", "Testerson_101426", "Pat_Testerson_101426"),
    ("fakeley_testerson_fakeley_101426_summary.pdf", "Fakeley_101426", "Testerson_Fakeley_101426"),
    ("testerson_testerson_fakeley_101426_summary.pdf", "Fakeley_101426",
     "Testerson_Fakeley_101426"),
    ("loft_vantest_loft_031126_summary.pdf", "Loft_031126", "VanTest_Loft_031126"),
    ("vantest_vantest_loft_031126_summary.pdf", "Loft_031126", "VanTest_Loft_031126"),
]


@pytest.mark.parametrize("suffix", ["", "_Gar"])
@pytest.mark.parametrize("filename, listed, salesforce", UNLISTED_READINGS)
def test_a_folder_never_settles_a_name_that_may_be_an_unlisted_sales(
    filename, listed, salesforce, suffix
):
    jobs = [Job("40", listed)]
    r = match_file_to_job(filename, jobs, folder=listed + suffix,
                          opportunity_names=[listed, salesforce])
    assert (r.status, r.job, r.reason, r.candidates) == (
        "ambiguous", None, "folder_disagrees", (jobs[0],))


def test_readings_that_name_no_sale_leave_the_listed_job_matched():
    jobs = [Job("40", "Testerson_101426")]
    r = match_file_to_job("testerson_pat_testerson_101426_summary.pdf", jobs,
                          opportunity_names=["Testerson_101426", "Fakeley_101426"])
    assert (r.status, r.job) == ("matched", jobs[0])


def test_lowercase_name_that_names_one_job_by_any_reading_is_matched():
    jobs = [Job("3", "fakeley_pantry_030926")]           # a lowercase job name; no "Pantry_030926"
    r = match_file_to_job("fakeley_pantry_030926_summary.pdf", jobs, opportunity_names=())
    assert (r.status, r.job, r.reason) == ("matched", jobs[0], "exact")


def test_name_without_a_date_is_never_matched_even_when_equal():
    jobs = [Job("14", "Test Lane Garage")]
    r = match_file_to_job("Test Lane Garage.pdf", jobs, opportunity_names=())
    assert (r.status, r.candidates) == ("ambiguous", (jobs[0],))


# ----- match_by_folder: a hand-named file belongs to its job folder


@pytest.mark.parametrize("folder, studio_id", [
    ("Samplin 092826", "10"),                    # a space in the folder name
    ("Mockford_030926_Gar", "4"),
    ("Testyan_082926rev2", "6"),                 # a revision's folder
    ("Testova_082326remake", "5"),               # a remake's folder
    (r"G:\NG Jobs\Testerson_101426", "1"),       # a path: its last part is the folder
])
def test_job_folder_matches_its_job(folder, studio_id):
    r = match_by_folder(folder, JOBS, opportunity_names=())
    assert (r.status, r.job and r.job.studio_id) == ("matched", studio_id)


@pytest.mark.parametrize("folder", ["Old", "Parts requested", "bench", "LED test", "Test Lane Garage"])
def test_shop_folders_are_not_jobs(folder):
    r = match_by_folder(folder, JOBS, opportunity_names=())
    assert (r.status, r.reason) == ("none", "not_a_job_name")


@pytest.mark.parametrize("folder", [
    "Testerson_101426-2", "Testerson_101426-2_Gar", "Testerson_101426-2 remake",
])
@pytest.mark.parametrize("names, expected", [
    (["Testerson_101426", "Testerson_101426-2"], ("none", None, "no_job")),  # the "-2" sale's
    (["Testerson_101426"], ("matched", PARENT_AND_RUN[0], "base")),          # a run's folder
    ((), ("matched", PARENT_AND_RUN[0], "base")),                            # no list to ask
])
def test_a_folder_named_for_a_sale_with_no_job_is_never_the_parents_by_folder_alone(
    folder, names, expected
):
    r = match_by_folder(folder, PARENT_AND_RUN[:1], opportunity_names=names)
    assert (r.status, r.job, r.reason) == expected


def test_hand_named_file_belongs_to_its_job_folder():
    # The file even names the colliding surname; the folder still decides.
    r = match_file_to_job("Testyan remake asembly.pdf", JOBS, folder="Testova_082326remake",
                          opportunity_names=())
    assert (r.status, r.job and r.job.studio_id, r.reason) == ("matched", "5", "folder")


def test_file_named_for_another_job_inside_a_job_folder_is_not_filed():
    r = match_file_to_job("Testerson_101426_summary.pdf", JOBS, folder="Testova_082326",
                          opportunity_names=())
    assert (r.status, {j.studio_id for j in r.candidates}) == ("ambiguous", {"1", "5"})


def test_summary_of_a_job_not_in_the_list_inside_a_job_folder_is_not_filed():
    r = match_file_to_job("Zeddley_010126_summary.pdf", JOBS, folder="Testova_082326",
                          opportunity_names=())
    assert (r.status, r.candidates, r.reason) == ("ambiguous", (JOBS[4],), "folder_disagrees")


def test_renamed_project_in_its_own_folder_is_asked_about_once():
    jobs = [Job("9", "Dummitt_011926")]                  # the later name is not recorded yet
    r = match_file_to_job("dummitt_zample_remodel_011926_summary.pdf", jobs, folder="Dummitt_011926",
                          opportunity_names=())
    assert (r.status, r.candidates) == ("ambiguous", (jobs[0],))


@pytest.mark.parametrize("filename", [
    "Testova_082326_IC_Conversion.json",
    "Testova 082326 labels.pdf",
    "Testova_082326remakeS25.TAP",
    "Testova_082326 (2026-09-22).pdf",
    "testova_testova_082326remake_summary.pdf",
    "Testova_082326.des",
    "Testova_082326_crosscheck.txt",
    "Testova_082326_NGRunner.json",
    "Testova_082326_NGRunner_log.txt",
    "Testova_082326_Gar_IC_Conversion.json",
    "Room1.des",
])
def test_the_jobs_own_files_stay_in_its_folder(filename):
    r = match_file_to_job(filename, JOBS, folder="Testova_082326remake", opportunity_names=())
    assert (r.status, r.job and r.job.studio_id) == ("matched", "5")


@pytest.mark.parametrize("filename, folder, studio_id", [
    ("Loft_031126_IC_Conversion.json", "VanTest_Loft_031126", "7"),  # the end of the job's name
    ("pantry_030926 labels.pdf", "Fakeley_pantry_030926", "3"),
    ("Remodel_011926_crosscheck.txt", "Dummitt_011926", "9"),       # part of the job's later name
    ("Mockford_030926_crosscheck.txt", "Mockford_030926_Gar", "4"),  # the start of the job's name
])
def test_a_name_that_is_only_part_of_the_folder_jobs_own_name_stays_in_its_folder(
    filename, folder, studio_id
):
    r = match_file_to_job(filename, JOBS, folder=folder, opportunity_names=())
    assert (r.status, r.job and r.job.studio_id, r.reason) == ("matched", studio_id, "folder")


@pytest.mark.parametrize("filename, extra", [
    ("Loft_031126_IC_Conversion.json", [Job("29", "Loft_031126")]),  # part of the name, but a job
    ("Oft_031126_IC_Conversion.json", []),                          # not at a word boundary
    ("VanTest_031126_IC_Conversion.json", []),                      # surname and date only
])
def test_a_name_that_is_more_than_part_of_the_folder_jobs_name_is_not_filed(filename, extra):
    r = match_file_to_job(filename, [*JOBS, *extra], folder="VanTest_Loft_031126",
                          opportunity_names=())
    expected = ("ambiguous", "folder_disagrees", (JOBS[6], *extra))
    assert (r.status, r.reason, r.candidates) == expected


@pytest.mark.parametrize("filename, home", [
    # A summary is named for its job, so a piece of the folder job's name is another job.
    ("Loft_031126_summary.pdf", Job("7", "VanTest_Loft_031126")),
    ("Loft_031126.pdf", Job("7", "VanTest_Loft_031126")),
    ("Fakeley_101426_summary.pdf", Job("21", "Testerson_Fakeley_101426")),
    ("Testerson_101426_summary.pdf", Job("31", "Pat_Testerson_101426")),
    ("Remodel_011926_summary.pdf", Job("9", "Dummitt_011926", aliases=("Zample_Remodel_011926",))),
    # Its whole name is not the folder job's: the name goes on after the date.
    ("Testerson_101426_2_summary.pdf", Job("1", "Testerson_101426")),
    ("Testerson_101426_2 (2026-09-22).pdf", Job("1", "Testerson_101426")),
])
def test_a_summary_named_for_any_other_job_name_is_not_filed_in_a_job_folder(filename, home):
    r = match_file_to_job(filename, [home], folder=home.name, opportunity_names=())
    assert (r.status, r.reason, r.candidates) == ("ambiguous", "folder_disagrees", (home,))


@pytest.mark.parametrize("filename, folder, home, other", [
    # The parent's summary inside a separate "-2" job's folder: they share a base.
    ("Testerson_101426_summary.pdf", "Testerson_101426-2", "27", "1"),
    # A separate "_Gar" job's summary inside the closet job's folder.
    ("Testerson_101426_Gar_summary.pdf", "Testerson_101426", "1", "13"),
])
def test_a_file_named_for_another_listed_job_sharing_its_base_is_not_filed(
    filename, folder, home, other
):
    jobs = [Job("1", "Testerson_101426"), Job("13", "Testerson_101426_Gar"),
            Job("27", "Testerson_101426-2")]
    r = match_file_to_job(filename, jobs, folder=folder, opportunity_names=())
    got = (r.status, r.reason, [j.studio_id for j in r.candidates])
    assert got == ("ambiguous", "folder_disagrees", [home, other])


TWO_SALES = [Job("1", "Testerson_101426"), Job("30", "Testerson_101426_2")]   # the name goes on


def test_file_named_on_after_the_date_matches_the_job_with_that_whole_name():
    r = match_file_to_job("Testerson_101426_2_summary.pdf", TWO_SALES, opportunity_names=())
    assert (r.status, r.job and r.job.studio_id, r.reason) == ("matched", "30", "exact")


@pytest.mark.parametrize("filename", [
    "Testerson_101426_2_summary.pdf",
    "Testerson_101426_2.pdf",
    "Testerson_101426_2 (2026-09-22).pdf",
    "Testerson_101426_2.des",
    "Testerson_101426_2_IC_Conversion.json",
])
def test_files_of_a_job_whose_name_goes_on_are_not_filed_in_the_shorter_ones_folder(filename):
    r = match_file_to_job(filename, TWO_SALES, folder="Testerson_101426", opportunity_names=())
    assert (r.status, r.reason, [j.studio_id for j in r.candidates]) == (
        "ambiguous", "folder_disagrees", ["1", "30"])


@pytest.mark.parametrize("filename", [
    "Testerson_101426_2.pdf", "Testerson_101426_2.des", "Testerson_101426-2_IC_Conversion.json",
])
def test_files_of_a_sale_only_salesforce_lists_are_not_filed_in_the_shorter_jobs_folder(filename):
    jobs = TWO_SALES[:1]                                  # NG Runner has only the first sale
    sales = ["Testerson_101426", "Testerson_101426_2", "Testerson_101426-2"]
    r = match_file_to_job(filename, jobs, folder="Testerson_101426", opportunity_names=sales)
    assert (r.status, r.reason, r.candidates) == ("ambiguous", "folder_disagrees", (jobs[0],))


def test_the_folder_jobs_own_files_stay_when_salesforce_lists_only_it():
    r = match_file_to_job("Testerson_101426.des", TWO_SALES[:1], folder="Testerson_101426",
                          opportunity_names=["Testerson_101426", "Fakeley_101426"])
    assert (r.status, r.job) == ("matched", TWO_SALES[0])


@pytest.mark.parametrize("filename", [
    "Testerson_101426_2_summary.pdf",
    "Testerson_101426_2.des",
    "Testerson_101426_2_IC_Conversion.json",
])
def test_own_files_of_a_job_whose_name_goes_on_stay_in_its_folder(filename):
    r = match_file_to_job(filename, TWO_SALES, folder="Testerson_101426_2", opportunity_names=())
    assert (r.status, r.job and r.job.studio_id) == ("matched", "30")


@pytest.mark.parametrize("filename", [
    "Testerson_101426_summary.pdf",
    "Testerson_101426.des",
    "Testerson_101426remake_summary.pdf",
])
def test_the_parents_files_in_a_separate_run_jobs_folder_are_not_filed_there(filename):
    jobs = [Job("27", "Testerson_101426-2")]                 # the parent is not listed
    r = match_file_to_job(filename, jobs, folder="Testerson_101426-2", opportunity_names=())
    assert (r.status, r.reason, r.candidates) == ("ambiguous", "folder_disagrees", (jobs[0],))


@pytest.mark.parametrize("filename", [
    "Testerson_101426-2.des",
    "Testerson_101426-2_IC_Conversion.json",
    "Testerson_101426-2_Gar_crosscheck.txt",
    "Testerson_101426-2remakeS25.TAP",
])
def test_a_run_jobs_own_files_stay_in_its_folder(filename):
    jobs = [Job("27", "Testerson_101426-2")]
    r = match_file_to_job(filename, jobs, folder="Testerson_101426-2", opportunity_names=())
    assert (r.status, r.job) == ("matched", jobs[0])


@pytest.mark.parametrize("filename", ["Testerson_101426-2.des", "Testerson_101426-2_crosscheck.txt"])
@pytest.mark.parametrize("listed", [True, False])
def test_a_run_jobs_files_are_not_filed_with_a_job_whose_name_only_slugifies_alike(filename, listed):
    run_job, home = Job("27", "Testerson_101426-2"), Job("30", "Testerson_101426_2")
    jobs = [home, run_job] if listed else [home]
    sales = [] if listed else ["Testerson_101426-2", "Testerson_101426_2"]
    r = match_file_to_job(filename, jobs, folder=home.name, opportunity_names=sales)
    assert (r.status, r.reason, r.candidates) == (
        "ambiguous", "folder_disagrees", (home, run_job) if listed else (home,))


@pytest.mark.parametrize("filename, folder, home, other", [
    ("Testerson_101426_Gar.des", "Testerson_101426", "1", "13"),       # the garage job's file
    ("Testerson_101426_Gar_rev2.pdf", "Testerson_101426", "1", "13"),
    ("Testerson_101426.des", "Testerson_101426_Gar", "13", "1"),       # it may be either's
])
def test_a_closet_job_and_its_separate_garage_job_never_take_each_others_files(
    filename, folder, home, other
):
    jobs = [Job("1", "Testerson_101426"), Job("13", "Testerson_101426_Gar")]
    r = match_file_to_job(filename, jobs, folder=folder, opportunity_names=())
    got = (r.status, r.reason, [j.studio_id for j in r.candidates])
    assert got == ("ambiguous", "folder_disagrees", [home, other])


@pytest.mark.parametrize("listed", [True, False])
@pytest.mark.parametrize("filename", [
    "Otherson_101426_IC_Conversion.json",
    "Otherson_101426.des",
    "Otherson_101426_crosscheck.txt",
    "Otherson_101426_NGRunner.json",
    "Otherson 101426 labels.pdf",
])
def test_another_jobs_files_inside_a_job_folder_are_not_filed(filename, listed):
    home, other = Job("1", "Testerson_101426"), Job("28", "Otherson_101426")
    jobs = [home, other] if listed else [home]
    r = match_file_to_job(filename, jobs, folder="Testerson_101426", opportunity_names=())
    assert (r.status, r.job, r.reason, r.candidates) == (
        "ambiguous", None, "folder_disagrees", (home, other) if listed else (home,))


@pytest.mark.parametrize("names", [(), ("Testerson_Fakeley_101426", "Fakeley_101426")])
@pytest.mark.parametrize("folder, home", [("Testerson_Fakeley_101426", 0), ("Fakeley_101426", 1)])
def test_lowercase_name_that_reads_as_two_jobs_is_filed_by_the_folder_it_sits_in(
    folder, home, names
):
    # Both readings are listed jobs (the M1 case), so the folder may settle it.
    jobs = [Job("21", "Testerson_Fakeley_101426"), Job("22", "Fakeley_101426")]
    r = match_file_to_job("testerson_fakeley_101426_summary.pdf", jobs, folder=folder,
                          opportunity_names=names)
    assert (r.status, r.job, r.reason) == ("matched", jobs[home], "folder")


def test_lowercase_archive_name_in_its_own_folder_is_filed():
    jobs = [Job("3", "fakeley_pantry_030926")]
    r = match_file_to_job("fakeley_pantry_030926_summary.pdf", jobs, folder="fakeley_pantry_030926",
                          opportunity_names=())
    assert (r.status, r.job) == ("matched", jobs[0])


def test_file_named_like_two_other_jobs_inside_a_job_folder_is_not_filed():
    jobs = [Job("1", "Testerson_101426"), Job("12", "Testerson_101426"), Job("5", "Testova_082326")]
    r = match_file_to_job("Testerson_101426.pdf", jobs, folder="Testova_082326",
                          opportunity_names=())
    assert (r.status, r.candidates) == ("ambiguous", (jobs[2], jobs[0], jobs[1]))


def test_file_outside_any_job_folder_is_matched_by_its_name():
    r = match_file_to_job("Testerson_101426.pdf", JOBS, folder="Old", opportunity_names=())
    assert (r.status, r.job and r.job.studio_id) == ("matched", "1")


# ----- match_fab_row

T0 = datetime(2026, 10, 27, 22, 5, tzinfo=timezone.utc)   # the row first appears
FRESH = T0 + timedelta(minutes=11)                        # a list read from Salesforce after it

OPPS = [
    Opportunity("Testerson_101426", "101", "Pat Example", "Ready to Order"),
    Opportunity("Fakeley_100226", "102", "Quinn Sample", "Ready to Install"),
    Opportunity("Fakeley_102826", "103", "Pat Example", "Ready to Order"),
    Opportunity("Fakeley_pantry_030926", "104", "Pat Q Example", "Ready to Order"),
    Opportunity("Testova_082326", "105", "Pat Example", "Ready to Order"),
    Opportunity("Testyan_082926", "106", "Pat Example", "Ready to Order"),
    Opportunity("Mockford_101426", "107", "Pat Example", "Propose"),            # not sold yet
    Opportunity("Dummitt_090926", "108", "Pat Example", "Ready to Install"),    # has an NG job
    Opportunity("MockfordJane_091026", "109", "Quinn Sample", "Ready to Order"),
]
NG = {"108"}


def _row(text: str, designer: str = "PE", first_seen: datetime = T0) -> FabRow:
    return FabRow(text, designer, first_seen)


def test_list_read_before_the_row_appeared_is_stale_even_for_an_exact_name():
    r = match_fab_row(_row("Testerson_101426"), OPPS, NG, T0 - timedelta(minutes=1))
    assert (r.status, r.job) == ("stale_list", None)


def test_no_list_yet_is_stale():
    assert match_fab_row(_row("Testerson_101426"), OPPS, NG, None).status == "stale_list"


def test_times_without_a_time_zone_are_refused():
    naive = datetime(2026, 10, 27, 15, 5)
    with pytest.raises(ValueError):
        match_fab_row(_row("Testerson_101426", first_seen=naive), OPPS, NG, FRESH)
    with pytest.raises(ValueError):
        match_fab_row(_row("Testerson_101426"), OPPS, NG, datetime(2026, 10, 27, 15, 30))


def test_a_row_without_a_first_seen_time_is_refused_like_one_without_a_time_zone():
    with pytest.raises(ValueError):
        match_fab_row(FabRow("Testerson_101426", "PE", None), OPPS, NG, FRESH)


@pytest.mark.parametrize("text", [
    "Testerson_101426", "testerson_101426", "TESTERSON 101426", " Testerson_101426 ",
])
def test_exact_name_matches_whoever_the_designer_is(text):
    r = match_fab_row(_row(text, designer=""), OPPS, NG, FRESH)
    assert (r.status, r.job and r.job.studio_id, r.reason) == ("matched", "101", "exact_name")


def test_exact_name_matches_an_opportunity_not_marked_sold_yet():
    r = match_fab_row(_row("Mockford_101426", designer="QS"), OPPS, NG, FRESH)
    assert (r.status, r.job and r.job.studio_id) == ("matched", "107")


def test_exact_name_with_the_gar_branch_matches_the_job_and_keeps_the_branch():
    r = match_fab_row(_row("Testerson_101426_Gar"), OPPS, NG, FRESH)
    assert (r.status, r.job and r.job.studio_id, r.normalized.variant) == ("matched", "101", "gar")


@pytest.mark.parametrize("text, variant", [
    ("Testerson_101426-2", "-2"),
    ("Testerson_101426 remake", "remake"),
    ("Testerson_101426 top", "top"),
    ("Testerson_101426rev2", "rev2"),
    ("Testerson_101426_Gar_Redo2", "gar+redo2"),
])
def test_exact_name_with_a_run_asks_whether_it_is_a_run_of_the_parent(text, variant):
    r = match_fab_row(_row(text), OPPS, NG, FRESH)
    got = (r.status, r.job, r.reason, [o.studio_id for o in r.candidates], r.normalized.variant)
    assert got == ("ambiguous", None, "variant_of_parent", ["101"], variant)


def test_exact_name_and_run_both_equal_match():
    opps = [*OPPS, Opportunity("Testerson_101426-2", "111", "Quinn Sample", "Ready to Order")]
    r = match_fab_row(_row("Testerson_101426-2", designer=""), opps, NG, FRESH)
    assert (r.status, r.job and r.job.studio_id, r.reason) == ("matched", "111", "exact_name")


@pytest.mark.parametrize("text, longer", [
    ("Testerson_101426_2", "Testerson_101426_2"),
    ("Testerson_101426_Garage", "Testerson_101426_Garage"),
    ("Testerson_101426 Garage", "Testerson_101426_Garage"),
])
def test_exact_name_with_text_after_the_date_is_that_opportunity_not_the_shorter_one(text, longer):
    opps = [Opportunity("Testerson_101426", "201", "Pat Example", "Ready to Order"),
            Opportunity(longer, "202", "Pat Example", "Ready to Order")]
    r = match_fab_row(_row(text), opps, set(), FRESH)
    assert (r.status, r.job and r.job.studio_id, r.reason) == ("matched", "202", "exact_name")


@pytest.mark.parametrize("owner, stage", [
    ("Pat Example", "Propose"),         # it may be the newer sale as well
    ("Pat Example", "Lost"),            # only its being the whole name stops these two
    ("Quinn Sample", "Propose"),
])
def test_typed_text_that_is_one_opportunitys_whole_name_never_matches_another_one(owner, stage):
    opps = [Opportunity("Testerson Garage", "201", owner, stage),   # no date at all
            Opportunity("Testerson_051526", "202", "Pat Example", "Ready to Order")]
    r = match_fab_row(_row("Testerson Garage"), opps, set(), FRESH)
    assert (r.status, r.job, r.reason) == ("ambiguous", None, "unconfirmed")


def test_typed_date_keeps_an_opportunity_whose_name_goes_on_after_that_date():
    opps = [Opportunity("Testerson_101426", "201", "Pat Example", "Ready to Order"),
            Opportunity("Testerson_101426_2", "202", "Pat Example", "Ready to Order")]
    r = match_fab_row(_row("Testerson_101426 closet"), opps, set(), FRESH)
    assert (r.status, [o.studio_id for o in r.candidates]) == ("ambiguous", ["201", "202"])


@pytest.mark.parametrize("designer", ["", "PE"])
def test_parent_name_never_matches_an_opportunity_that_is_a_run(designer):
    # Not by rule (a), and not by rule (b) either: the row does not name the run "-2".
    opps = [Opportunity("Testerson_101426-2", "111", "Pat Example", "Ready to Order")]
    r = match_fab_row(_row("Testerson_101426", designer), opps, set(), FRESH)
    assert (r.status, r.reason) == ("ambiguous", "unconfirmed")


def test_two_opportunities_with_one_exact_name_are_narrowed_only_by_the_surname_rule():
    opps = [
        Opportunity("Testerson_101426", "101", "Pat Example", "Ready to Order"),
        Opportunity("Testerson_101426", "110", "Quinn Sample", "Lost"),
    ]
    assert match_fab_row(_row("Testerson_101426"), opps, set(), FRESH).job is opps[0]
    r = match_fab_row(_row("Testerson_101426", designer=""), opps, set(), FRESH)
    assert (r.status, r.candidates) == ("ambiguous", tuple(opps))


@pytest.mark.parametrize("text, designer, studio_id", [
    ("Testerson", "PE", "101"),
    ("Testerson closet", "PE", "101"),      # Barbara's free text
    ("testerson", "Pat", "101"),
    ("Fakeley", "QS", "102"),               # three Fakeleys, one of them this designer's
    ("Fakeley closet", "QS", "102"),
    ("Fakeley_030926", "PE", "104"),        # typed without the room word: the date narrows it
    ("Testova", "PE", "105"),               # never the colliding Testyan
])
def test_one_sold_opportunity_with_the_surname_and_designer_and_no_ng_job_matches(
    text, designer, studio_id
):
    r = match_fab_row(_row(text, designer), OPPS, NG, FRESH)
    assert (r.status, r.job and r.job.studio_id) == ("matched", studio_id)
    assert r.reason == "surname_designer"


@pytest.mark.parametrize("text, designer, status, offered", [
    ("Testerson", "QS", "ambiguous", ["101"]),         # owned by another designer
    ("Testerson", "", "ambiguous", ["101"]),           # no designer: the surname alone is never enough
    ("Fakeley", "PE", "ambiguous", ["103", "104", "102"]),  # PE fits Pat and Pat Q Example
    ("Mockford", "PE", "ambiguous", ["107", "109"]),   # not sold yet; a namesake is offered after it
    ("Dummitt", "PE", "ambiguous", ["108"]),           # already has an NG job
    ("Testersen", "PE", "none", []),                   # a typo never fuzzy-matches
    ("Testersen_101426", "PE", "ambiguous", ["101"]),  # ...but the typed date can offer it
    ("Testerson_101526", "PE", "ambiguous", ["101"]),  # the typed date disagrees
    ("Testovy", "PE", "none", []),
    ("101426", "PE", "none", []),                      # a date alone
    ("", "PE", "none", []),
])
def test_anything_less_is_offered_or_refused_never_matched(text, designer, status, offered):
    r = match_fab_row(_row(text, designer), OPPS, NG, FRESH)
    assert (r.status, r.job, [o.studio_id for o in r.candidates]) == (status, None, offered)


VAN_TESTA = Opportunity("Van Testa_041326", "301", "Pat Example", "Propose")    # not sold yet
VAN_DER = Opportunity("Van Der Fakeo_021726", "302", "Pat Example", "Ready to Install")
SOLD = {"stage": "Ready to Order"}
HYPHENATED = Opportunity("Testerson-Fakeley_041326", "303", "Pat Example", "Propose")
ONE_WORD = Opportunity("Testerson_051526", "304", "Pat Example", "Ready to Install")
CLOSET_FAKELEY = Opportunity("Closet_Fakeley_041326", "305", "Pat Example", "Ready to Order")
GB_FAKELEY = Opportunity("GB - Fakeley_041326", "307", "Pat Example", "Ready to Install")
QUINNS = Opportunity("Testerson_101426", "306", "Quinn Sample", "Ready to Order")


@pytest.mark.parametrize("text, opps, ng", [
    ("Van Testa", [VAN_TESTA, VAN_DER], set()),                      # the typed family: not sold
    ("Van Testa", [replace(VAN_TESTA, **SOLD, owner="Quinn Sample"), VAN_DER], set()),
    ("Van Testa", [replace(VAN_TESTA, **SOLD), VAN_DER], {"301"}),   # it has an NG job already
    ("Van Testa", [VAN_DER], set()),                                 # it is not in the list
    ("Van Testa", [replace(VAN_TESTA, name="Van_Testa_041326"), VAN_DER], set()),
    ("Van Testa", [replace(VAN_TESTA, name="VanTesta_041326"), VAN_DER], set()),
    ("Testerson Fakeley", [HYPHENATED, ONE_WORD], set()),            # a double-barrelled surname
    ("Closet Testerson", [CLOSET_FAKELEY, QUINNS], set()),           # free text typed first
    ("GB Testerson", [GB_FAKELEY, QUINNS], set()),                   # a run word typed first
])
def test_a_shared_first_word_alone_never_matches_another_family(text, opps, ng):
    r = match_fab_row(_row(text), opps, ng, FRESH)
    assert (r.status, r.job) == ("ambiguous", None)


@pytest.mark.parametrize("name", [
    "Van Testa_041326", "Van_Testa_041326", "VanTesta_041326", "Van-Testa_041326",
    "Van Testa Garage_041326",
])
def test_two_typed_words_match_the_one_family_that_carries_both(name):
    opps = [replace(VAN_TESTA, name=name, **SOLD), replace(VAN_DER, owner="Quinn Sample")]
    r = match_fab_row(_row("Van Testa"), opps, set(), FRESH)
    assert (r.status, r.job and r.job.studio_id) == ("matched", "301")


def test_the_family_that_carries_the_typed_words_is_offered_before_a_first_word_rival():
    r = match_fab_row(_row("Van Testa"), [VAN_DER, VAN_TESTA], set(), FRESH)
    assert (r.status, [o.studio_id for o in r.candidates]) == ("ambiguous", ["301", "302"])


def test_two_families_that_both_fit_are_offered_not_matched():
    r = match_fab_row(_row("Van Testa"), [replace(VAN_TESTA, **SOLD), VAN_DER], set(), FRESH)
    assert (r.status, [o.studio_id for o in r.candidates]) == ("ambiguous", ["301", "302"])


# ----- match_fab_row: rule (b) is strict, so every word of the row must be explained

DOUBLE = Opportunity("Testerson Fakeley_092826", "501", "Pat Example", "Ready to Order")
SINGLE = Opportunity("Testerson_051526", "502", "Pat Example", "Ready to Install")


@pytest.mark.parametrize("real, ng", [
    (replace(DOUBLE, stage="Propose"), set()),            # the typed family is not sold yet
    (replace(DOUBLE, owner="Quinn Sample"), set()),       # it is another designer's
    (DOUBLE, {"501"}),                                    # it has an NG job already
    (None, set()),                                        # it is not in the list
])
@pytest.mark.parametrize("text", [
    "Testerson Fakeley", "Testerson Fakeley closet", "Testerson & Fakeley", "Testerson/Fakeley",
    "Testerson-Fakeley", "TestersonFakeley",
])
def test_two_typed_words_never_match_a_family_that_has_only_the_first(text, real, ng):
    opps = [o for o in (real, SINGLE) if o is not None]
    r = match_fab_row(_row(text), opps, ng, FRESH)
    assert (r.status, r.job) == ("ambiguous", None)
    assert any(o is SINGLE for o in r.candidates)          # still offered, after the typed family


@pytest.mark.parametrize("text", ["Fakeley Testerson", "Fakeley Testerson closet"])
def test_a_second_surname_typed_never_matches_the_first_surnames_family(text):
    opps = [Opportunity("Fakeley_051526", "503", "Pat Example", "Ready to Install")]
    r = match_fab_row(_row(text), opps, set(), FRESH)
    assert (r.status, r.job, [o.studio_id for o in r.candidates]) == ("ambiguous", None, ["503"])


@pytest.mark.parametrize("text", ["Testerson", "Testerson_101426", "testerson 101426"])
@pytest.mark.parametrize("name", ["Testerson_101426-2", "Testerson_101426_2"])
def test_a_typed_parent_name_never_matches_a_sale_whose_name_goes_on_after_the_date(text, name):
    # The parent is not in the list: the "-2" or "_2" sale is offered, never taken for it.
    opps = [Opportunity(name, "504", "Pat Example", "Ready to Order")]
    r = match_fab_row(_row(text), opps, set(), FRESH)
    assert (r.status, r.job, r.reason, [o.studio_id for o in r.candidates]) == (
        "ambiguous", None, "unconfirmed", ["504"])


@pytest.mark.parametrize("real, ng", [
    (None, set()),                                                  # it is not in the list
    ({"stage": "Propose"}, set()),                                  # older, and not sold yet
    ({"stage": "Ready to Order", "owner": "Quinn Sample"}, set()),  # another designer's
    ({"stage": "Ready to Order"}, {"508"}),                         # it has an NG job already
])
@pytest.mark.parametrize("name", ["Testerson {}_031126", "Testerson-{}_031126", "Testerson{}_031126"])
@pytest.mark.parametrize("text", ["Testerson {}", "Testerson-{}", "Testerson {} closet"])
@pytest.mark.parametrize("word", ["Guest", "Master"])
def test_a_room_like_word_that_is_also_a_surname_never_hides_a_second_family(
    word, text, name, real, ng
):
    newer = Opportunity("Testerson_101426", "509", "Pat Example", "Ready to Order")
    older = Opportunity(name.format(word), "508", "Pat Example", "Propose")
    opps = [replace(older, **real), newer] if real else [newer]
    r = match_fab_row(_row(text.format(word)), opps, ng, FRESH)
    assert (r.status, r.job) == ("ambiguous", None)
    assert any(o is newer for o in r.candidates)


SAME_INITIALS = "Pia Exampleton"            # "PE" names her as well as Pat Example


@pytest.mark.parametrize("opps", [
    [Opportunity("Testerson_101426", "505", "Pat Example", "Ready to Order"),
     Opportunity("Testerson_051526", "506", SAME_INITIALS, "Propose")],
    [Opportunity("Testerson_101426", "505", "Pat Example", "Propose"),
     Opportunity("Testerson_051526", "506", SAME_INITIALS, "Ready to Install")],
    [Opportunity("Testerson_101426", "505", "Pat Example", "Ready to Order"),
     Opportunity("Testerson Fakeley_092826", "506", SAME_INITIALS, "Propose")],  # a rival
])
def test_initials_that_fit_two_owners_name_neither(opps):
    r = match_fab_row(_row("Testerson", "PE"), opps, set(), FRESH)
    assert (r.status, r.job, r.reason) == ("ambiguous", None, "unconfirmed")


def test_a_designer_named_in_full_is_told_apart_from_one_with_the_same_initials():
    opps = [Opportunity("Testerson_101426", "505", "Pat Example", "Ready to Order"),
            Opportunity("Testerson_051526", "506", SAME_INITIALS, "Propose")]
    r = match_fab_row(_row("Testerson", "Pat Example"), opps, set(), FRESH)
    assert (r.status, r.job and r.job.studio_id) == ("matched", "505")


TYPED_NAME_SOLD = Opportunity("Testerson_101426", "510", "Pat Example", "Ready to Order")


@pytest.mark.parametrize("other", [
    {"owner": "Pat Example", "stage": "Propose"},      # the designer's: it may be the sale
    {"owner": "Pat Example", "stage": "Complete"},
    {"owner": "Pat Example", "stage": "Ready to Order"},
    {"owner": SAME_INITIALS, "stage": "Propose"},      # "PE" names two owners, so neither
    {"owner": SAME_INITIALS, "stage": "Lost"},
])
def test_several_opportunities_with_the_typed_name_ask_when_another_may_be_the_sale(other):
    opps = [TYPED_NAME_SOLD, replace(TYPED_NAME_SOLD, studio_id="511", **other)]
    r = match_fab_row(_row("Testerson_101426"), opps, set(), FRESH)
    assert (r.status, r.job, r.reason, r.candidates) == (
        "ambiguous", None, "several_opportunities", tuple(opps))


@pytest.mark.parametrize("designer, other", [
    ("PE", {"owner": "Pat Example", "stage": "Lost"}),     # never to be sold
    ("PE", {"owner": "Pat Example", "stage": "Dead Lead"}),
    ("PE", {"owner": "Quinn Sample", "stage": "Propose"}),  # another designer's
    ("Pat Example", {"owner": SAME_INITIALS, "stage": "Propose"}),  # the full name tells them apart
])
def test_several_opportunities_with_the_typed_name_narrow_to_the_only_one_that_can_be_it(
    designer, other
):
    opps = [TYPED_NAME_SOLD, replace(TYPED_NAME_SOLD, studio_id="511", **other)]
    r = match_fab_row(_row("Testerson_101426", designer), opps, set(), FRESH)
    assert (r.status, r.job, r.reason) == ("matched", TYPED_NAME_SOLD, "exact_name")


# Letters the fold cannot take down to a-z, so no reader can read them. Dropped, they would make
# another family's name of what is left (finding M5).
UNREADABLE_LETTERS = [
    ("Østerson", "Sterson_101426"), ("østerson closet", "Sterson_101426"),
    ("Đặng", "Ang_101426"), ("Łata", "Ata_101426"), ("Þorson", "Orson_101426"),
    ("Ðoe", "Oe_101426"), ("Æson", "Son_101426"), ("Œster", "Ster_101426"),
    ("Tıger", "Tger_101426"), ("Ŧesterson", "Esterson_101426"),
    ("Testerson Ø", "Testerson_101426"), ("Testerson Иван", "Testerson_101426"),
]


@pytest.mark.parametrize("text, name", UNREADABLE_LETTERS)
def test_a_letter_no_reader_can_read_makes_the_row_ask(text, name):
    opps = [Opportunity(name, "512", "Pat Example", "Ready to Order")]
    r = match_fab_row(_row(text), opps, set(), FRESH)
    assert (r.status, r.job, r.reason, [o.studio_id for o in r.candidates]) == (
        "ambiguous", None, "unconfirmed", ["512"])


@pytest.mark.parametrize("text", ["Sterson", "Østerson"])
def test_a_letter_no_reader_can_read_in_the_name_makes_the_row_ask(text):
    opps = [Opportunity("Østerson_101426", "512", "Pat Example", "Ready to Order")]
    r = match_fab_row(_row(text), opps, set(), FRESH)
    assert (r.status, r.job, r.reason, [o.studio_id for o in r.candidates]) == (
        "ambiguous", None, "unconfirmed", ["512"])


def test_a_letter_no_reader_can_read_never_stops_an_exact_name():
    opps = [Opportunity("Østerson_101426", "512", "Pat Example", "Propose")]
    r = match_fab_row(_row("Østerson_101426", designer=""), opps, set(), FRESH)
    assert (r.status, r.job, r.reason) == ("matched", opps[0], "exact_name")


@pytest.mark.parametrize("text, name", [
    ("Tésterson", "Testerson_101426"),
    ("TESTERSÖN closet", "Testerson_101426"),
    ("Testerson", "Téstersön_101426"),
    ("Straße", "Strasse_101426"),
])
def test_accents_the_fold_can_read_still_match(text, name):
    opps = [Opportunity(name, "512", "Pat Example", "Ready to Order")]
    r = match_fab_row(_row(text), opps, set(), FRESH)
    assert (r.status, r.job and r.job.studio_id, r.reason) == ("matched", "512", "surname_designer")


@pytest.mark.parametrize("owner, designer, matched", [
    ("Østen Example", "Østen", True), ("Østen Example", "ØE", True),
    ("Østen Example", "Sten", False), ("Østen Example", "SE", False),
    ("Sten Example", "Østen", False), ("Sten Example", "ØE", False),
])
def test_a_designers_letter_no_reader_can_read_is_never_dropped(owner, designer, matched):
    opps = [Opportunity("Testerson_101426", "513", owner, "Ready to Order")]
    r = match_fab_row(_row("Testerson", designer), opps, set(), FRESH)
    assert (r.status == "matched") is matched


@pytest.mark.parametrize("text, name", [
    ("Fakeley closet", "Fakeley_pantry_030926"),
    ("Fakeley garage", "Fakeley_pantry_030926"),
    ("Fakeley closets 3/9/26", "Fakeley_pantry_030926"),
    ("Van Testa closet", "Van Testa Garage_041326"),
])
def test_room_words_that_differ_from_the_names_never_name_the_job(text, name):
    opps = [Opportunity(name, "507", "Pat Example", "Ready to Order")]
    r = match_fab_row(_row(text), opps, set(), FRESH)
    assert (r.status, r.job, [o.studio_id for o in r.candidates]) == ("ambiguous", None, ["507"])


@pytest.mark.parametrize("text, name", [
    # A bare surname, the design's own example.
    ("Testerson", "Testerson_101426"),
    ("testerson", "Testerson_101426"),
    # Room words, in the row, the name or both.
    ("Testerson closet", "Testerson_101426"),
    ("Testerson garage", "Testerson_101426"),
    ("Testerson walk-in closet", "Testerson_101426"),
    ("Testerson walk in closets", "Testerson_101426"),
    ("Testerson reach-in", "Testerson_101426"),
    ("Testerson primary closet", "Testerson_101426"),
    ("Testerson his & hers closets", "Testerson_101426"),
    ("Testerson laundry room, pantry", "Testerson_101426"),
    ("Testerson Gar", "Testerson_101426"),             # the garage branch
    ("Fakeley", "Fakeley_pantry_030926"),
    ("Fakeley pantry", "Fakeley_pantry_030926"),
    ("Fakeley closet", "Fakeley_030926"),
    ("Fakeley garage", "Fakeley_030926_Garage"),
    # A word that is also a surname is a name word: the family that carries it is named.
    ("Testerson Guest", "Testerson Guest_101426"),
    ("Testerson Master closet", "Testerson Master_101426"),
    # A typed date, in each form it is read in.
    ("Testerson 10/14/26", "Testerson_101426"),
    ("Testerson 10-14-26", "Testerson_101426"),
    ("Testerson 10.14.26", "Testerson_101426"),
    ("Testerson 2026-10-14", "Testerson_101426"),
    ("Testerson 10/14/2026", "Testerson_101426"),
    ("Testerson 10142026", "Testerson_101426"),
    ("Testerson 101426 closet", "Testerson_101426"),
    ("Testerson closet 10/14/26", "Testerson_101426"),
    ("Fakeley_030926", "Fakeley_pantry_030926"),
    # Two-word, hyphenated and camelCase families.
    ("Van Testa", "Van Testa_041326"),
    ("Van Testa", "Van_Testa_041326"),
    ("Van Testa", "VanTesta_041326"),
    ("Van Testa", "Van-Testa_041326"),
    ("Van Testa", "Van Testa Garage_041326"),
    ("Van Testa garage", "Van Testa Garage_041326"),
    ("VanTesta", "Van Testa_041326"),
    ("Van-Testa", "Van Testa_041326"),
    ("Testerson Fakeley", "Testerson-Fakeley_041326"),
    ("Testerson-Fakeley", "Testerson-Fakeley_041326"),
    ("Testerson-Fakeley", "Testerson Fakeley_041326"),
    ("Testerson & Fakeley", "Testerson Fakeley_041326"),
    ("MockfordJane", "MockfordJane_091026"),
    ("Mockford Jane", "MockfordJane_091026"),
])
def test_a_row_whose_every_word_is_explained_still_matches(text, name):
    opps = [Opportunity(name, "601", "Pat Example", "Ready to Order"),
            Opportunity("Testova_082326", "602", "Pat Example", "Ready to Order"),   # unrelated
            Opportunity("Van Der Fakeo_021726", "603", "Quinn Sample", "Ready to Order")]
    r = match_fab_row(_row(text), opps, set(), FRESH)
    assert (r.status, r.job and r.job.studio_id, r.reason) == ("matched", "601", "surname_designer")


@pytest.mark.parametrize("text", ["Testerson", "Testerson remake", "Testerson closet"])
def test_a_remake_row_of_an_installed_job_is_offered_with_that_job_first(text):
    opps = [Opportunity("Testerson_051526", "604", "Pat Example", "Complete")]
    r = match_fab_row(_row(text), opps, set(), FRESH)
    assert (r.status, r.job, [o.studio_id for o in r.candidates]) == ("ambiguous", None, ["604"])


@pytest.mark.parametrize("text, variant", [
    ("Testerson remake", "remake"),
    ("Testerson closet remake", "remake"),    # a run word anywhere after the surname
    ("Testerson -2", "-2"),
    ("Testerson rev 2", "rev2"),
    ("Testerson_101426 closet redo", "redo"),
    # A run typed before a date that "_Gar" or "-N" follows is never dropped (finding M3).
    ("Testerson re-make 101426 gar", "remake+gar"),
    ("Testerson -2 101426 Gar", "-2+gar"),
    ("Testerson II 101426 Gar", "-2+gar"),
    ("Testerson redo 101426 Gar", "redo+gar"),
    ("Testerson warranty 101426 gar", "warranty+gar"),
    ("Testerson go back 101426_Gar", "gb+gar"),
    ("Testerson re-make 10/14/26 gar", "remake+gar"),
])
def test_surname_with_a_run_asks_whether_it_is_a_run_of_the_parent(text, variant):
    r = match_fab_row(_row(text), OPPS, NG, FRESH)
    got = (r.status, r.job, r.reason, [o.studio_id for o in r.candidates], r.normalized.variant)
    assert got == ("ambiguous", None, "variant_of_parent", ["101"], variant)


def test_a_run_typed_before_the_date_is_kept_when_a_sale_of_that_date_carries_another_run():
    opps = [Opportunity("Testerson_101426-2", "111", "Pat Example", "Ready to Order")]
    r = match_fab_row(_row("Testerson remake 101426-2"), opps, set(), FRESH)
    got = (r.status, r.job, r.reason, [o.studio_id for o in r.candidates], r.normalized.variant)
    assert got == ("ambiguous", None, "unconfirmed", ["111"], "remake+-2")


@pytest.mark.parametrize("text", [
    # A number other than the date may name a run or another job: offered, never matched.
    "Testerson #2", "Testerson 2", "Testerson (2)", "Testerson_101426_2", "Testerson 2nd",
    "Testerson remake2", "Testerson v2", "Testerson R2", "Testerson phase 2", "Testerson part 2",
    # Run words in other spellings.
    "Testerson II", "Testerson second", "Testerson re-make", "Testerson re make",
    "Testerson go back", "Testerson warranty", "Testerson add on", "Testerson addon",
    # Any other word after the surname is not explained, so the row asks (finding N8b).
    "Testerson re-do", "Testerson re do", "Testerson G.B.", "Testerson revision",
    "Testerson revised", "Testerson remakes", "Testerson re-made", "Testerson service",
    "Testerson repair", "Testerson fix", "Testerson punch list", "Testerson reorder",
    "Testerson replacement", "Testerson missing parts", "Testerson gob", "Testerson tops",
    "Testerson change order", "Testerson extra", "Testerson additional", "Testerson add",
    "Testerson two", "Testerson IV", "Testerson part two",
    # A word that is also a surname may be a second family name (finding M1).
    "Testerson master closet", "Testerson Guest", "Testerson Master",
])
def test_a_row_that_may_name_a_run_is_never_matched_as_a_new_job(text):
    opps = [Opportunity("Testerson_101426", "101", "Pat Example", "Ready to Order")]
    r = match_fab_row(_row(text), opps, set(), FRESH)
    assert (r.status, r.job, [o.studio_id for o in r.candidates]) == ("ambiguous", None, ["101"])


@pytest.mark.parametrize("text, variant", [
    ("Testerson II", "-2"), ("Testerson second", "-2"), ("Testerson re-make", "remake"),
    ("Testerson re make", "remake"), ("Testerson go back", "gb"), ("Testerson goback", "gb"),
    ("Testerson warranty", "warranty"), ("Testerson add on", "addon"),
    ("Testerson add-on", "addon"),
    # More spellings, for a clearer card.
    ("Testerson re-do", "redo"), ("Testerson re do", "redo"), ("Testerson G.B.", "gb"),
    ("Testerson g b", "gb"), ("Testerson revision", "rev"), ("Testerson revised", "rev"),
    ("Testerson remakes", "remake"), ("Testerson re-made", "remake"),
    ("Testerson remade", "remake"),
    ("Testerson IV", "-4"), ("Testerson vii", "-7"), ("Testerson X", "-10"),
    ("Testerson two", "-2"), ("Testerson five", "-5"), ("Testerson ten", "-10"),
])
def test_run_words_in_other_spellings_ask_whether_it_is_a_run_of_the_parent(text, variant):
    r = match_fab_row(_row(text), OPPS, NG, FRESH)
    got = (r.status, r.reason, [o.studio_id for o in r.candidates], r.normalized.variant)
    assert got == ("ambiguous", "variant_of_parent", ["101"], variant)


@pytest.mark.parametrize("text", ["Testerson Gar", "Testerson garage"])
def test_surname_with_the_gar_branch_or_a_room_word_still_matches(text):
    r = match_fab_row(_row(text), OPPS, NG, FRESH)
    assert (r.status, r.job and r.job.studio_id, r.reason) == ("matched", "101", "surname_designer")


def test_a_word_that_only_starts_like_a_run_is_no_run_but_still_asks():
    # "Topanga" is no "top", and no room word either: the row may name another family.
    r = match_fab_row(_row("Testerson Topanga closet"), OPPS, NG, FRESH)
    got = (r.status, r.reason, [o.studio_id for o in r.candidates], r.normalized.variant)
    assert got == ("ambiguous", "unconfirmed", ["101"], None)


@pytest.mark.parametrize("newer", ["Propose", "Design", "Complete", None])
def test_surname_rule_asks_when_the_designer_has_a_newer_sale_of_the_family(newer):
    # Salesforce lags: the newer one, still at Propose, may be the sale this row is for.
    opps = [Opportunity("Testerson_101426", "201", "Pat Example", newer or "Ready to Order"),
            Opportunity("Testerson_051526", "202", "Pat Example", "Ready to Install")]
    ng = {"201"} if newer is None else set()           # None: sold, with an NG job already
    r = match_fab_row(_row("Testerson"), opps, ng, FRESH)
    assert (r.status, r.job, r.reason, [o.studio_id for o in r.candidates]) == (
        "ambiguous", None, "unconfirmed", ["202", "201"])


@pytest.mark.parametrize("newer", [
    {"stage": "Lost"}, {"stage": "Dead Lead"},        # never to be sold
    {"stage": "Propose", "owner": "Quinn Sample"},    # another designer's
    {"stage": "Propose", "name": "Testerson_021526"},  # older, not newer
])
def test_surname_rule_takes_the_sold_job_when_no_newer_sale_of_the_designers_may_be_it(newer):
    opps = [replace(Opportunity("Testerson_101426", "201", "Pat Example", "Propose"), **newer),
            Opportunity("Testerson_051526", "202", "Pat Example", "Ready to Install")]
    r = match_fab_row(_row("Testerson"), opps, set(), FRESH)
    assert (r.status, r.job and r.job.studio_id, r.reason) == ("matched", "202", "surname_designer")


@pytest.mark.parametrize("stage", ["Propose", "Design", "Incomplete", "Complete", "A new stage"])
@pytest.mark.parametrize("newer", [
    "Pat Testerson_101426",            # named with a first name
    "Testerson Fakeley_101426",        # or with a second surname
    "TestersonFakeley_101426",
    "Testerson-Fakeley_101426",
    "Pat Testerson_051526",            # the same day: a name date can't order the two
    "Pat Testerson",                   # no date: it can't show it is older
])
def test_surname_rule_asks_when_the_designer_has_a_newer_rival_that_may_be_the_sale(newer, stage):
    # The sale may have been named another way, and Salesforce lags: it may be this row's.
    opps = [Opportunity("Testerson_051526", "203", "Pat Example", "Ready to Install"),
            Opportunity(newer, "204", "Pat Example", stage)]
    r = match_fab_row(_row("Testerson"), opps, set(), FRESH)
    assert (r.status, r.job, r.reason, [o.studio_id for o in r.candidates]) == (
        "ambiguous", None, "unconfirmed", ["203", "204"])


def test_a_newer_shorter_family_may_be_the_sale_for_a_row_that_types_two_surnames():
    opps = [Opportunity("Testerson Fakeley_051526", "203", "Pat Example", "Ready to Install"),
            Opportunity("Testerson_101426", "204", "Pat Example", "Propose")]
    r = match_fab_row(_row("Testerson Fakeley"), opps, set(), FRESH)
    assert (r.status, r.job, r.reason) == ("ambiguous", None, "unconfirmed")


@pytest.mark.parametrize("text, rival", [
    ("Testerson", {"stage": "Lost"}),                    # never to be sold
    ("Testerson", {"stage": "Dead Lead"}),
    ("Testerson", {"owner": "Quinn Sample"}),            # another designer's
    ("Testerson", {"name": "Pat Testerson_021526"}),     # older, not newer
    ("Testerson 5/15/26", {}),                           # another date than the one typed
])
def test_a_rival_that_cannot_be_the_designers_newer_sale_leaves_the_match(text, rival):
    opps = [Opportunity("Testerson_051526", "203", "Pat Example", "Ready to Install"),
            replace(Opportunity("Pat Testerson_101426", "204", "Pat Example", "Propose"), **rival)]
    r = match_fab_row(_row(text), opps, set(), FRESH)
    assert (r.status, r.job and r.job.studio_id, r.reason) == ("matched", "203", "surname_designer")


def test_typing_the_date_picks_the_newer_sale_still_proposed():
    opps = [Opportunity("Testerson_101426", "201", "Pat Example", "Proposed"),
            Opportunity("Testerson_051526", "202", "Pat Example", "Ready to Install")]
    r = match_fab_row(_row("Testerson_101426"), opps, set(), FRESH)
    assert (r.status, r.job and r.job.studio_id, r.reason) == ("matched", "201", "exact_name")


@pytest.mark.parametrize("other", [
    "Testerson Garage_101426", "Testerson_101426", "Testerson Garage",
])
def test_a_sale_of_the_same_date_or_of_no_date_may_be_newer_so_the_row_asks(other):
    # One name date cannot order two sales of the same day, and a name without a date cannot
    # show it is older: either may be the sale this row is for, still at Propose.
    opps = [Opportunity("Testerson Closet_101426", "201", "Pat Example", "Ready to Order"),
            Opportunity(other, "202", "Pat Example", "Propose")]
    r = match_fab_row(_row("Testerson"), opps, set(), FRESH)
    assert (r.status, r.job, r.reason) == ("ambiguous", None, "unconfirmed")


TWO_DATES = [Opportunity("Testerson_101426", "401", "Pat Example", "Ready to Install"),
             Opportunity("Testerson_051526", "402", "Pat Example", "Ready to Install")]


@pytest.mark.parametrize("opps, ng", [
    (TWO_DATES, {"401"}),                                           # its job exists already
    ([replace(TWO_DATES[0], stage="Propose"), TWO_DATES[1]], set()),  # it is not sold yet
])
@pytest.mark.parametrize("text", [
    "Testerson 10/14/26", "Testerson 10-14-26", "Testerson 10.14.26", "Testerson 2026-10-14",
    "Testerson 10/14/2026", "Testerson 10142026", "Testerson 10-14-26 closet",
    "Testerson Oct 14", "Testerson 10/14",          # a date it can't read: offered, never matched
])
def test_a_date_typed_in_another_form_never_lets_another_date_match(text, opps, ng):
    r = match_fab_row(_row(text), opps, ng, FRESH)
    assert (r.status, r.job) == ("ambiguous", None)


@pytest.mark.parametrize("text", [
    "Testerson 5/15/26", "Testerson 05-15-2026", "Testerson 2026-05-15", "Testerson 05152026",
])
def test_a_date_typed_in_another_form_picks_its_opportunity(text):
    r = match_fab_row(_row(text), TWO_DATES, set(), FRESH)
    assert (r.status, r.job and r.job.studio_id) == ("matched", "402")


def test_an_opportunity_of_the_typed_date_is_offered_first():
    r = match_fab_row(_row("Testerson 10/14/26"), TWO_DATES, {"401"}, FRESH)
    assert [o.studio_id for o in r.candidates] == ["401", "402"]


@pytest.mark.parametrize("ng, expected", [
    ({"701"}, ("matched", "702")),     # the older one has its NG job: the new sale is the row's
    ({"702"}, ("ambiguous", None)),    # the newer one has: the row may be for it again
])
def test_the_one_opportunity_without_an_ng_job_is_matched_unless_a_newer_one_has_one(ng, expected):
    opps = [Opportunity("Fakeley_030926", "701", "Pat Example", "Ready to Install"),
            Opportunity("Fakeley_102826", "702", "Pat Example", "Ready to Order")]
    r = match_fab_row(_row("Fakeley"), opps, ng, FRESH)
    assert (r.status, r.job and r.job.studio_id) == expected


@pytest.mark.parametrize("ng", ["108", b"108", None])
def test_ng_jobs_that_are_no_list_are_refused(ng):
    # One ID would be read as its digits, so the job it names would look as if it had none.
    with pytest.raises(TypeError):
        match_fab_row(_row("Dummitt"), OPPS, ng, FRESH)


@pytest.mark.parametrize("sold", ["Ready to Order", None])
def test_sold_stages_that_are_no_list_are_refused(sold):
    with pytest.raises(TypeError):
        match_fab_row(_row("Testerson"), OPPS, NG, FRESH, sold_stages=sold)


def test_ng_jobs_count_whole_when_they_can_be_read_only_once():
    r = match_fab_row(_row("Dummitt"), OPPS, iter(["108", "Testerson_101426"]), FRESH)
    assert (r.status, [o.studio_id for o in r.candidates]) == ("ambiguous", ["108"])
    r = match_fab_row(_row("Testerson"), OPPS, iter(["108", "Testerson_101426"]), FRESH)
    assert (r.status, [o.studio_id for o in r.candidates]) == ("ambiguous", ["101"])


def test_ng_job_known_only_by_its_folder_name_counts():
    r = match_fab_row(_row("Testerson"), OPPS, {"Testerson_101426_Gar"}, FRESH)
    assert (r.status, r.job) == ("ambiguous", None)


def test_ng_job_known_only_by_a_folder_named_with_text_after_the_date_counts():
    opps = [Opportunity("Testerson_101426_2", "202", "Pat Example", "Ready to Order")]
    r = match_fab_row(_row("Testerson"), opps, {"Testerson_101426_2"}, FRESH)
    assert (r.status, r.job) == ("ambiguous", None)


@pytest.mark.parametrize("opps", [
    [Opportunity("Testerson_101426", "201", "Pat Example", "Ready to Order")],
    # Never a reason to pick another one: the folder may well be the first one's.
    [Opportunity("Testerson_101426", "201", "Pat Example", "Ready to Order"),
     Opportunity("Testerson_051526", "202", "Pat Example", "Ready to Order")],
])
def test_ng_folder_that_only_starts_with_an_opportunitys_name_blocks_the_surname_rule(opps):
    r = match_fab_row(_row("Testerson"), opps, {"Testerson_101426_Garage"}, FRESH)
    assert (r.status, r.job) == ("ambiguous", None)


def test_an_older_sale_that_may_have_its_ng_job_still_keeps_the_new_sale_from_matching():
    # The folder may be another sale's, so the older one may still be the job this row is for.
    opps = [Opportunity("Testerson_051526", "202", "Pat Example", "Ready to Order"),
            Opportunity("Testerson_101426", "201", "Pat Example", "Ready to Order")]
    r = match_fab_row(_row("Testerson"), opps, {"Testerson_051526_Garage"}, FRESH)
    assert (r.status, r.job, r.reason) == ("ambiguous", None, "several_opportunities")


@pytest.mark.parametrize("folder", [
    "Testerson_101426",            # the parent's own folder: the "-2" sale may have none yet
    "Testerson_101426-2_rev2",     # a run of one of the two, it can't say which
])
def test_folder_of_one_of_two_related_sales_never_pushes_the_match_to_a_third(folder):
    opps = [Opportunity("Testerson_101426", "201", "Pat Example", "Ready to Order"),
            Opportunity("Testerson_101426-2", "202", "Pat Example", "Ready to Order"),
            Opportunity("Testerson_051526", "203", "Pat Example", "Ready to Order")]
    r = match_fab_row(_row("Testerson"), opps, {folder}, FRESH)
    assert (r.status, r.job) == ("ambiguous", None)


MINE = Opportunity("Testerson_101426", "514", "Pat Example", "Ready to Order")


@pytest.mark.parametrize("owner", ["", "   ", " - "])
@pytest.mark.parametrize("other", [
    Opportunity("Testerson_051526", "515", "", "Ready to Order"),          # another candidate
    Opportunity("Testerson Fakeley_092826", "515", "", "Ready to Order"),  # rivals that fit
    Opportunity("Pat Testerson_051526", "515", "", "Ready to Install"),
    Opportunity("Testerson_121526", "515", "", "Propose"),                 # a newer sale
    Opportunity("Pat Testerson_121526", "515", "", "Design"),              # named another way
])
def test_an_opportunity_with_no_owner_may_be_the_designers_so_the_row_asks(other, owner):
    opps = [MINE, replace(other, owner=owner)]
    r = match_fab_row(_row("Testerson"), opps, set(), FRESH)
    assert (r.status, r.job) == ("ambiguous", None)
    assert sorted(o.studio_id for o in r.candidates) == ["514", "515"]


@pytest.mark.parametrize("other, ng", [
    (Opportunity("Testerson_051526", "515", "", "Propose"), set()),         # older, and not sold
    (Opportunity("Testerson_121526", "515", "", "Lost"), set()),            # never to be sold
    (Opportunity("Pat Testerson_121526", "515", "", "Dead Lead"), set()),
    (Opportunity("Testerson_051526", "515", "", "Ready to Order"), {"515"}),  # has its NG job
    (Opportunity("Fakeley_121526", "515", "", "Ready to Order"), set()),    # another family
])
def test_an_opportunity_with_no_owner_that_could_not_stop_the_designers_own_leaves_the_match(
    other, ng
):
    r = match_fab_row(_row("Testerson"), [MINE, other], ng, FRESH)
    assert (r.status, r.job, r.reason) == ("matched", MINE, "surname_designer")


@pytest.mark.parametrize("text", ["Testerson", "Testerson closet", "Testerson 10/14/26"])
@pytest.mark.parametrize("designer", ["PE", ""])
def test_an_opportunity_with_no_owner_is_never_matched_by_the_surname_rule(text, designer):
    opps = [replace(MINE, owner="")]
    r = match_fab_row(_row(text, designer), opps, set(), FRESH)
    assert (r.status, r.job, r.reason, r.candidates) == ("ambiguous", None, "unconfirmed", tuple(opps))


def test_an_exact_name_still_matches_an_opportunity_with_no_owner():
    opps = [replace(MINE, owner="")]
    r = match_fab_row(_row("Testerson_101426"), opps, set(), FRESH)
    assert (r.status, r.job, r.reason) == ("matched", opps[0], "exact_name")


@pytest.mark.parametrize("opps", [
    # The other may be the designer's, and the sale.
    [MINE, replace(MINE, studio_id="515", owner="", stage="Propose")],
    # The sold one may be anyone's, so it can't stand for the name.
    [replace(MINE, owner=""), replace(MINE, studio_id="515", stage="Lost")],
    [replace(MINE, owner=""), replace(MINE, studio_id="515", owner="Quinn Sample", stage="Propose")],
])
def test_several_opportunities_with_the_typed_name_ask_when_one_has_no_owner(opps):
    r = match_fab_row(_row("Testerson_101426"), opps, set(), FRESH)
    assert (r.status, r.job, r.reason, r.candidates) == (
        "ambiguous", None, "several_opportunities", tuple(opps))


def test_a_row_of_200_characters_is_still_read():
    r = match_fab_row(_row("Testerson".ljust(200)), OPPS, NG, FRESH)
    assert (r.status, r.job and r.job.studio_id, r.reason) == ("matched", "101", "surname_designer")


@pytest.mark.parametrize("text", [
    "Testerson".ljust(201),
    "Testerson_101426".ljust(201),              # an exact name is not read either
    "Testerson " * 2000,
    "Testerson 101426 gar " * 1000,             # dates and run words, the slowest to read
    "Testerson closet walk-in " * 2400,
])
def test_a_row_longer_than_200_characters_asks_at_once_and_is_never_read(text):
    started = time.perf_counter()
    r = match_fab_row(_row(text), OPPS, NG, FRESH)
    assert time.perf_counter() - started < 5      # reading the longest takes far longer
    assert (r.status, r.job, r.reason, r.candidates) == ("none", None, "row_too_long", ())
    assert r.normalized.base == ""


def test_a_row_too_long_to_read_waits_for_no_list():
    r = match_fab_row(_row("Testerson".ljust(201)), OPPS, NG, None)
    assert (r.status, r.reason) == ("none", "row_too_long")


def test_a_row_too_long_to_read_still_needs_times_with_a_zone():
    with pytest.raises(ValueError):
        match_fab_row(_row("Testerson".ljust(201), first_seen=datetime(2026, 10, 27)), OPPS, NG, FRESH)


@pytest.mark.parametrize("text, rooms", [
    ("", ()),
    ("Testerson", ()),
    ("Testerson closet", ("closet",)),
    ("Testerson closets", ("closet",)),                         # one spelling each
    ("Testerson Pantries, garages", ("pantry", "garage")),      # in the order typed
    ("Testerson walk-in closets", ("walkin", "closet")),
    ("Testerson walk in, reach-in", ("walkin", "reachin")),
    ("Testerson closet and closets", ("closet",)),              # once each
    ("Closet", ()),                                             # never the first word
    ("Closet Fakeley", ()),
    ("O'Closet garage", ("garage",)),                           # an apostrophe never ends a word
    ("Testerson Guest", ()),                                    # surnames, not room words
    ("Testerson Master", ()),
    ("Testerson_101426 garage", ("garage",)),
    ("Testerson garage 10/14/26", ("garage",)),
    ("Testerson_101426_Gar", ()),                               # a branch flag, not a room word
    ("Testerson remake", ()),
])
def test_room_words_are_the_ones_the_row_is_read_to_carry(text, rooms):
    assert room_words(text) == rooms


def test_room_words_of_a_row_too_long_to_read_are_none_and_never_stall():
    started = time.perf_counter()
    assert room_words("Testerson closet walk-in " * 2400) == ()
    assert time.perf_counter() - started < 5


def test_sold_stages_can_be_set():
    r = match_fab_row(_row("Mockford"), OPPS, NG, FRESH, sold_stages={"Propose"})
    assert (r.status, r.job and r.job.studio_id) == ("matched", "107")


@pytest.mark.parametrize("designer, matched", [
    ("PE", True), ("pe", True), ("P.E.", True), ("P E", True), ("PQE", True),
    ("Pat", True), ("Example", True), ("Pat Example", True), ("pat q example", True),
    ("QS", False), ("P", False), ("Pa", False), ("Patrick", False), ("Example Pat", False),
    ("Quinn Example", False), ("PE/QS", False),
])
def test_designer_names_the_owner_by_initials_or_name(designer, matched):
    opps = [Opportunity("Testerson_101426", "101", "Pat Q Example", "Ready to Order")]
    r = match_fab_row(_row("Testerson", designer), opps, set(), FRESH)
    assert (r.status == "matched") is matched


# ----- real files: local only, reported by index

# Where Google Drive for desktop shows NG Jobs, in the order tried. A shortcut's folder is named
# by a Drive ID that differs per PC, so it is found by listing, never written down here.
NG_JOBS_ON_DRIVE = (
    ".shortcut-targets-by-id/*/Inspired Closets WC/Sales Folder/CNC operations/NG Jobs",
    "My Drive/Inspired Closets WC/Sales Folder/CNC operations/NG Jobs",
    "Shared drives/Inspired Closets WC/Sales Folder/CNC operations/NG Jobs",
)


def _find_ng_jobs(drive: Path, env: Mapping[str, str]) -> Path | None:
    """NGR_NG_JOBS when it is set, else the first NG Jobs folder found on the drive, else None."""
    if env.get("NGR_NG_JOBS"):
        return Path(env["NGR_NG_JOBS"])
    for pattern in NG_JOBS_ON_DRIVE:
        try:
            found = sorted(p for p in drive.glob(pattern) if p.is_dir())
        except OSError:
            continue
        if found:
            return found[0]
    return None


def _by_name(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.pdf"), key=lambda p: p.name.casefold())


def _archived_jobs(summaries_dir: Path) -> tuple[list[Path], list[Job]]:
    """One job per archived <Job>_summary.pdf; its studio_id is just its index."""
    paths = _by_name(summaries_dir / "ngjobs")
    names = [re.sub(r"_summary$", "", p.stem, flags=re.IGNORECASE) for p in paths]
    return paths, [Job(str(i), name) for i, name in enumerate(names)]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _page1_project_name(path: Path) -> str | None:
    """Page 1's "PROJECT NAME <name>", or None if absent; "" if the PDF can't be read."""
    try:
        text = PdfReader(path).pages[0].extract_text() or ""
    except Exception:  # noqa: BLE001 - an error message could carry the path, which names a client
        return ""
    found = re.search(r"PROJECT NAME\s+(\S+)", text)
    return found[1] if found else None


@pytest.mark.fixtures
def test_real_downloads_match_one_archived_job_or_say_why(summaries_dir):
    archived, jobs = _archived_jobs(summaries_dir)
    same_bytes = {_sha256(p): job for p, job in zip(archived, jobs)}
    downloads = _by_name(summaries_dir / "downloads")
    rows, broken, disagree = [], [], []
    for i, path in enumerate(downloads):
        r = match_file_to_job(path.name, jobs, opportunity_names=())
        twin = same_bytes.get(_sha256(path))
        if r.status == "matched":
            ok = len(r.candidates) == 1 and any(r.job is j for j in jobs)
        else:
            ok = r.job is None and bool(r.reason)
        if not ok:
            broken.append(i)
        if twin is not None and r.job is not twin:      # wrong job, or a byte-identical summary missed
            disagree.append(i)
        rows.append(f"{i:>3}  {r.normalized.kind:<8} {r.status:<10} {r.reason:<20}"
                    f" {r.job.studio_id if r.job else '-':>7} {twin.studio_id if twin else '-':>10}")
    matched = sum(" matched " in row for row in rows)
    print(f"\n{len(downloads)} downloads vs {len(jobs)} archived jobs: {matched} matched,"
          f" {len(downloads) - matched} unmatched with a reason")
    print("  #  kind     status     reason               job# same-bytes#")
    print("\n".join(rows))
    counts = (len(downloads), len(jobs))     # counts, so a failure never prints a name
    assert min(counts) > 0, f"downloads, archived jobs: {counts}"
    assert not broken, f"downloads {broken}: neither one job nor a reason"
    assert not disagree, f"downloads {disagree}: name match differs from the byte-identical archive"


@pytest.mark.fixtures
def test_real_names_normalize_to_their_page1_project_name(summaries_dir):
    checked, unlabelled, unreadable, wrong = 0, [], [], []
    for sub in ("downloads", "ngjobs"):
        for i, path in enumerate(_by_name(summaries_dir / sub)):
            label = _page1_project_name(path)
            if not label:
                (unreadable if label == "" else unlabelled).append(f"{sub}#{i}")
                continue
            checked += 1
            expected = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
            if normalize_job_file(path.name).base != expected:
                wrong.append(f"{sub}#{i}")
    print(f"\n{checked} names checked against page 1;"
          f" without a PROJECT NAME: {unlabelled}; unreadable: {unreadable}")
    assert checked > 0
    assert not unreadable, f"PDFs that could not be read: {unreadable}"
    assert not wrong, f"base differs from the page-1 PROJECT NAME: {wrong}"


_UNDER_DRIVE = "Inspired Closets WC/Sales Folder/CNC operations/NG Jobs"


@pytest.mark.parametrize("present, expected", [
    ([], None),
    (["Shared drives"], "Shared drives"),
    (["My Drive", "Shared drives"], "My Drive"),
    ([".shortcut-targets-by-id/AnyId", "My Drive"], ".shortcut-targets-by-id/AnyId"),
])
def test_ng_jobs_is_found_through_a_shortcut_then_my_drive_then_a_shared_drive(
    tmp_path, present, expected
):
    for top in present:
        (tmp_path / top / _UNDER_DRIVE).mkdir(parents=True)
    found = _find_ng_jobs(tmp_path, {})
    assert found == (tmp_path / expected / _UNDER_DRIVE if expected else None)


def test_ng_jobs_set_by_hand_is_used_as_given(tmp_path):
    (tmp_path / "My Drive" / _UNDER_DRIVE).mkdir(parents=True)
    assert _find_ng_jobs(tmp_path, {"NGR_NG_JOBS": r"D:\elsewhere"}) == Path(r"D:\elsewhere")


@pytest.fixture(scope="module")
def ng_jobs_folders() -> list[str]:
    """Names of the folders in NG Jobs on G: (listed, never opened)."""
    ng_jobs = _find_ng_jobs(Path("G:/"), os.environ)
    if ng_jobs is None:
        pytest.skip("NG Jobs not found on G: (set NGR_NG_JOBS)")
    try:
        with os.scandir(ng_jobs) as entries:
            names = sorted((e.name for e in entries if e.is_dir()), key=str.casefold)
    except OSError:
        pytest.skip("NG Jobs is not reachable from here (set NGR_NG_JOBS)")
    if not names:
        pytest.skip("NG Jobs is empty")
    return names


# Words any job name may carry: sharing one with a real job says nothing about which job it is.
_GENERIC_WORDS = frozenset({"gar", "garage", "closet", "summary", "remake", "redo", "rev", "top",
                            "pickup", "claude", "copy"})
_NAME_WITH_DATE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9_'-]*_\d{6}(?!\d)")
_DATE6 = re.compile(r"(?<!\d)\d{6}(?!\d)")


def _telling(name: str) -> set[str]:
    """The parts of a name that could point to a real job: its uncommon words, and "~" with the
    first two letters of a camelCase surname ("~va" for "VanTest")."""
    words = {w.casefold() for w in re.findall(r"[A-Z]?[a-z]+", name) if len(w) >= 3}
    camel = re.match(r"[A-Z][a-z]+(?:[A-Z][a-z]+)+", name)
    return (words - _GENERIC_WORDS) | ({"~" + camel[0][:2].casefold()} if camel else set())


@pytest.mark.fixtures
def test_synthetic_names_never_pair_a_real_jobs_date_with_another_of_its_words(
    summaries_dir, ng_jobs_folders
):
    real = [*ng_jobs_folders, *(p.stem for p in summaries_dir.rglob("*.pdf"))]
    index = [(set(_DATE6.findall(r)), _telling(r)) for r in real]
    sources = (Path(__file__), Path(__file__).parent.parent / "ngrunner" / "match.py")
    synthetic = sorted({n for s in sources for n in _NAME_WITH_DATE.findall(s.read_text("utf-8"))})
    reskins = [s for s in synthetic if any(
        set(_DATE6.findall(s)) & dates and _telling(s) & told for dates, told in index)]
    print(f"\n{len(synthetic)} synthetic names checked against {len(real)} real names")
    assert synthetic and real
    assert not reskins, f"synthetic names too close to a real job (made-up names only): {reskins}"


@pytest.mark.fixtures
def test_real_ng_jobs_folders_belong_to_at_most_one_archived_job(summaries_dir, ng_jobs_folders):
    _, jobs = _archived_jobs(summaries_dir)
    results = [match_by_folder(folder, jobs, opportunity_names=()) for folder in ng_jobs_folders]
    print(f"\n{len(results)} folders vs {len(jobs)} archived jobs")
    print("  #  status     reason               job#")
    print("\n".join(f"{i:>3}  {r.status:<10} {r.reason:<20} {r.job.studio_id if r.job else '-':>4}"
                    for i, r in enumerate(results)))
    ambiguous = [i for i, r in enumerate(results) if r.status == "ambiguous"]
    own_folder = {f.casefold(): r for f, r in zip(ng_jobs_folders, results)}
    missed = [j.studio_id for j in jobs
              if j.name.casefold() in own_folder and own_folder[j.name.casefold()].job is not j]
    assert not ambiguous, f"folders {ambiguous} are ambiguous"
    assert not missed, f"archived jobs {missed} do not match their own folder"
