"""
The shared check runner (P2 build spec 2): Jim's crosscheck.py and its checkers, run from the adopted
copy of his toolkit on a staged copy of one job (crosscheck.py), each checker's output read into a
verdict that fails closed (readers.py).

Why it is its own package: it is the "one check runner" of design 1, and P3's trial checks, repair
items and check-before-lock all stand on it. Nothing here writes outside NG Runner's data folder, and
nothing calls it yet: P3 wires it in.
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
