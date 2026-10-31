"""
The checks (P2 build spec 2, P3): Jim's crosscheck.py and its checkers, run from the adopted copy of his
toolkit on a staged copy of one job (crosscheck.py), each checker's output read into a verdict that fails
closed (readers.py), the verdicts made into items Jim can act on, each naming parts by tag with a ready
chat line, or saying what could not be verified and why (findings.py), and the checks at work on
"sections done", Check now and the audit of a chat-built job, bound to the bytes they ran on, with Jim's
settles and the close prompt (jobchecks.py).

Why it is its own package: it is the "one check runner" of design 1, and P3's trial checks, repair items
and P4's check-before-lock all stand on it. Nothing here writes outside NG Runner's data folder: a check
runs on staged copies there, never in place and never on a cloud or network drive.
"""
from __future__ import annotations

from ngrunner.checks.crosscheck import (
    CROSSCHECK_SCRIPT, LEFT_OUT, TEMPLATE_JOB, Crosscheck, Planned, StagedJob, library_found, planned,
    read_crosscheck, run_crosscheck, stage_job,
)
from ngrunner.checks.readers import SPECS, CheckerVerdict, read_checker

__all__ = [
    "CROSSCHECK_SCRIPT", "LEFT_OUT", "SPECS", "TEMPLATE_JOB", "CheckerVerdict", "Crosscheck", "Planned",
    "StagedJob", "library_found", "planned", "read_checker", "read_crosscheck", "run_crosscheck", "stage_job",
]
