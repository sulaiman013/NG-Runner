"""
The reader evaluation's cases: the 40 jobs added for P5 (.local-fixtures\\reader\\p5jobs, tags P01 to
P40) beside the first ten. Each new job's summary is found by the sender's manifest, which records
its real file name (often a doubled prefix), and is used only when its page 1 PROJECT NAME plainly
matches the job; otherwise the job is skipped and counted. Nothing returned or printed names a job.
A made-up tree in tmp_path: no real file is read here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.reader_eval import cases, prep
from tools.reader_eval import synthetic as s

MANIFEST = """NG RUNNER P5 - DRAWING TEST JOB SET
ZIP (job)                     FILES      BYTES  RAD  BRG  UPR  RMS  IC SUMMARY FOUND IN NG JOBS
-------------------------------------------------------------------------------------------------------------------
Testerson_101426.zip                 25      59254    0    0    1    2  testerson_testerson_101426_summary.pdf
Testerson_101526.zip                 22      88205    2    1    0    2  Testerson_101526_summary.pdf
Quill Pat_101426.zip             22      88205    0    0    0    2  Quill_Pat_101426_summary(1).pdf
-------------------------------------------------------------------------------------------------------------------
40 zips                        1083    7177895
"""


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    fixtures, ng = tmp_path / "fixtures", tmp_path / "NG Jobs"
    p5 = fixtures / "reader" / "p5jobs"
    (fixtures / "p1b").mkdir(parents=True)
    (fixtures / "p1b" / "case_key.json").write_text(json.dumps({"A": "Alpha_010126"}), encoding="utf-8")
    p5.mkdir(parents=True)
    (p5 / "key.json").write_text(json.dumps({"P01": "Testerson_101426", "P02": "Testerson_101526",
                                             "P03": "Quill Pat_101426"}), encoding="utf-8")
    (p5 / "MANIFEST.txt").write_text(MANIFEST, encoding="utf-8")
    for tag, job in (("P01", "Testerson_101426"), ("P02", "Testerson_101526")):
        (p5 / tag / job).mkdir(parents=True)
        (p5 / tag / job / "Room1.des").write_bytes(s.room_des())
    (ng / "Testerson_101426").mkdir(parents=True)
    (ng / "Testerson_101426" / "testerson_testerson_101426_summary.pdf").write_bytes(s.summary_pdf())
    (ng / "Old" / "Testerson_101526").mkdir(parents=True)  # filed, but it is another job's summary
    (ng / "Old" / "Testerson_101526" / "Testerson_101526_summary.pdf").write_bytes(
        s.summary_pdf(project="Testerson_101426"))
    return {"NGR_FIXTURES": str(fixtures), "NGR_NG_JOBS": str(ng), "NGR_TOOLKIT": str(tmp_path / "toolkit")}


def test_the_manifest_gives_each_jobs_real_summary_file_name() -> None:
    assert cases.manifest_summaries(MANIFEST) == {
        "Testerson_101426": "testerson_testerson_101426_summary.pdf",
        "Testerson_101526": "Testerson_101526_summary.pdf",
        "Quill Pat_101426": "Quill_Pat_101426_summary(1).pdf"}


def test_page_one_project_name_is_read_and_nothing_else() -> None:
    assert prep.project_name(s.summary_pdf(project="Quill_101426")) == "Quill_101426"


def test_a_summary_is_used_only_when_its_project_name_plainly_matches_the_job(env: dict[str, str]) -> None:
    found = cases.collect(env, which=("P01", "P02", "P03"))
    assert found == {"P01": "ng_jobs", "P02": "skipped: the summary is another job's",
                     "P03": "skipped: no summary in NG Jobs"}
    out = cases.reader_dir(env) / "cases"
    assert (out / "P01.pdf").is_file() and not (out / "P02.pdf").exists() and not (out / "P03.pdf").exists()
    index = (out / "index.json").read_text(encoding="utf-8")
    assert "Testerson" not in index and "Quill" not in index
    assert cases.collected(env) == ("P01",)


def test_the_new_jobs_join_the_test_set_after_the_first_ten(env: dict[str, str]) -> None:
    assert cases.test_cases(env) == (*"ABCDEFGHIJ", "P01", "P02", "P03")
    assert set(cases.test_cases(env)).isdisjoint(cases.WORKED_CASES)


def test_a_new_jobs_rooms_come_from_its_job_folder(env: dict[str, str]) -> None:
    assert set(cases.rooms("P01", env)) == {"Room1"}
    with pytest.raises(cases.CaseError, match="P03"):
        cases.rooms("P03", env)


def test_the_name_key_covers_the_new_jobs_for_the_self_checks(env: dict[str, str]) -> None:
    names = cases.job_names(env)
    assert names["A"] == "Alpha_010126" and names["P02"] == "Testerson_101526"


def test_without_the_new_folder_the_test_set_is_the_first_ten(tmp_path: Path) -> None:
    assert cases.test_cases({"NGR_FIXTURES": str(tmp_path)}) == tuple("ABCDEFGHIJ")
