"""The Phase 0 baseline: facts read from NG Jobs files, read-only, counts printed without names."""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ngr_tools_baseline", ROOT / "tools" / "baseline.py")
baseline = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = baseline          # dataclasses look their module up while it loads
_spec.loader.exec_module(baseline)


def _file(path: Path, text: str = "", *, day: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    stamp = datetime(2026, 10, day, 12, 0, tzinfo=timezone.utc).timestamp()
    os.utime(path, (stamp, stamp))
    return path


@pytest.fixture
def ng_jobs(tmp_path: Path) -> Path:
    root = tmp_path / "NG Jobs"
    job = root / "Testerson_101426"
    _file(job / "Testerson_101426_summary.pdf", "%PDF", day=1)
    _file(job / "Testerson_101426_IC_Conversion.json", "{}", day=1)
    _file(job / "Testerson_101426_crosscheck.txt",
          "# Testerson_101426 — PANEL CROSSCHECK (rev 3)\n===== top_line\nCROSSCHECK CLEAN\n", day=4)
    _file(job / "Testerson labels.pdf", day=5)
    gar = root / "Fakeley_092826_Gar"
    _file(gar / "Fakeley_092826_Gar_summary.pdf", "%PDF", day=2)
    _file(gar / "Fakeley_092826_Gar_crosscheck.txt", "# Fakeley — PANEL CROSSCHECK\n2 items\n", day=3)
    _file(root / "Old" / "Mockford_051526" / "Mockford_051526_summary.pdf", "%PDF", day=1)
    _file(root / "Parts requested" / "list.txt", "not a job")
    _file(root / "Dummitt_999999" / "x.txt")                        # a date no calendar has
    return root


def test_each_job_folder_is_read_for_what_it_shows(ng_jobs):
    jobs = {j.folder: j for j in baseline.scan(ng_jobs)}
    assert set(jobs) == {"Testerson_101426", "Fakeley_092826_Gar", "Mockford_051526", "Dummitt_999999"}
    t = jobs["Testerson_101426"]
    assert (t.summary_at is not None, t.conversions, t.crosschecks, t.rounds, t.clean) == (True, 1, 1, 3, True)
    assert t.days_summary_to_clean == 3.0 and t.labels == 1 and t.job_date == "2026-10-14"
    f = jobs["Fakeley_092826_Gar"]
    assert (f.garage, f.rounds, f.clean, f.days_summary_to_clean) == (True, 1, False, None)
    assert jobs["Mockford_051526"].archived and jobs["Dummitt_999999"].job_date is None


def test_the_printed_summary_holds_counts_only(ng_jobs):
    s = baseline.summarise(baseline.scan(ng_jobs))
    assert s["job_folders"] == 4 and s["with_summary"] == 3 and s["garage"] == 1
    assert s["crosscheck_rounds_median"] == 2 and s["clean_share"] == 0.5
    assert not any(isinstance(v, str) for v in s.values())


def test_nothing_is_ever_written_inside_ng_jobs(ng_jobs, tmp_path):
    before = sorted((p, p.stat().st_mtime) for p in ng_jobs.rglob("*"))
    out = tmp_path / "report" / "baseline.json"
    assert baseline.main(["--ng-jobs", str(ng_jobs), "--out", str(out)]) == 0
    assert out.is_file() and sorted((p, p.stat().st_mtime) for p in ng_jobs.rglob("*")) == before
    with pytest.raises(ValueError, match="inside NG Jobs"):
        baseline.write_report([], ng_jobs / "Testerson_101426" / "x.json", ng_jobs)


def test_a_missing_ng_jobs_folder_is_a_plain_error(tmp_path, capsys):
    assert baseline.main(["--ng-jobs", str(tmp_path / "nowhere")]) == 2
    assert "NG Jobs was not found" in capsys.readouterr().err
