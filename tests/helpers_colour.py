"""
The real summaries' designs and each one's colour as colour.py settles it, for the fixtures tests: read only,
every job named by case letter or P-tag, never by name. The known-good jobs are every real Mozaik job folder
here (helpers_toolkit.real_jobs), standing for Mozaik Shared\\Jobs, the job being built left out by its folder's
name, as NG Runner leaves it out.
"""
from __future__ import annotations

from functools import cache
from typing import Any

from helpers_toolkit import real_jobs
from ngrunner.build import colour as co
from ngrunner.build.room import unverified
from ngrunner.convert.summary_lines import SummaryLine, design_lines, pulled_lines, read_pages
from ngrunner.intake.branch import branch_of
from ngrunner.intake.summary_check import check_summary
from tools.reader_eval import cases
from tools.reader_eval.prep import name_key_hash


@cache
def designs(case: str) -> tuple[tuple[Any, ...], dict[str, tuple[SummaryLine, ...] | None], tuple[SummaryLine, ...]]:
    """The case's page-1 designs (the summary check's records), each one's parts lines (colour.lines_by_design)
    and the PULLED lines."""
    data = cases.summary(case)
    pages = read_pages(data)
    found = tuple(check_summary(data).details.get("designs", []))
    return found, co.lines_by_design(design_lines(pages), [d["name"] for d in found]), pulled_lines(pages)


def sources(own: str | None = None) -> co.KnownGood:
    """The real jobs, newest first, never own (the job being built, by its folder's name)."""
    return co.KnownGood(list(real_jobs().values()), exclude=[own] if own else [])


def settle(case: str, design: Any, known: co.KnownGood) -> co.RoomSettings:
    """One page-1 design's colour."""
    _, lines, job = designs(case)
    return co.settle(lines[design["name"]], job, branch=branch_of([design]).kind, sources=known)


def colour_of(case: str, design_key: str, known: co.KnownGood) -> co.RoomSettings:
    """The colour of the case's design whose name hashes to design_key (a crop's), or could not verify."""
    found = [d for d in designs(case)[0] if name_key_hash(d["name"]) == design_key]
    if len(found) != 1:
        return co.RoomSettings(unverified(co.UNTOLD), unverified(co.UNTOLD))
    return settle(case, found[0], known)


def own_job(case: str) -> str | None:
    """The case's own job folder's name, when it is one of the real jobs here."""
    found = real_jobs().get(case)
    return None if found is None else found.name
