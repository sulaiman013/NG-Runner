"""
Real renders fetched from Studio, local only: what filing will actually see.

The summary check must keep passing on today's sold jobs, and two fetches of one job must keep
agreeing, whatever the check or the fingerprint grows to cover (AGENTS.md: Before filing goes live,
item 1). The files are named by index, so a failure here names no job.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ngrunner.intake.fetch import Render, agree
from ngrunner.intake.summary_check import check_summary

pytestmark = pytest.mark.fixtures

FIRST = datetime(2026, 10, 30, 2, 0, tzinfo=UTC)


def _renders(summaries_dir: Path, folder: str, pattern: str) -> list[Path]:
    paths = sorted((summaries_dir / folder).glob(pattern))
    if not paths:
        pytest.skip(f"no real renders in the fixtures' {folder} folder")
    return paths


def test_every_real_sold_job_render_passes_the_summary_check(summaries_dir):
    failed = [path.name for path in _renders(summaries_dir, "sold", "*.pdf")
              if not check_summary(path).ok]
    assert not failed


def test_two_real_fetches_ten_minutes_apart_agree(summaries_dir):
    # They are separate renders, not one file twice: Studio's bytes differ on every request.
    differ = []
    for first in _renders(summaries_dir, "pairs", "*_a.pdf"):
        second = first.with_name(first.name.replace("_a.pdf", "_b.pdf"))
        assert first.read_bytes() != second.read_bytes()
        verdict = agree(Render(first, FIRST, "landscape"),
                        Render(second, FIRST + timedelta(minutes=10), "landscape"))
        if not verdict.ok:
            differ.append((first.name, verdict.code))
    assert not differ
