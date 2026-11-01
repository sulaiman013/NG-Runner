"""
P3's check as P4's lock gate reads it: a checks.jobchecks.JobCheck, over a run in the shape the store keeps
(store.CheckRunRecord), for the gate and click tests. The gate asks jobchecks.counts() whether the run
stands for the rooms on disk now, and the JobCheck how many of its findings are still open.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from ngrunner.checks.jobchecks import JobCheck

CONVERSION = "f" * 64          # the IC Conversion's sha256 the run ran on, and on disk now


@dataclass(frozen=True)
class Run:
    """What counts() and the gate read of store.CheckRunRecord."""

    state: str = "done"
    code: str | None = "passed"
    rooms: Mapping[str, str] = field(default_factory=dict)
    identities: Mapping[str, str] = field(default_factory=dict)
    stale_at: datetime | None = None
    conversion_sha256: str | None = CONVERSION


def job_check(*, rooms: Mapping[str, str], identities: Mapping[str, str], state: str = "done",
              code: str | None = "passed", stale_at: datetime | None = None, conversion: str | None = CONVERSION,
              to_fix: int = 0, unverified: int = 0) -> JobCheck:
    """A job's check as job_checks() gives it: its state from the run, as jobchecks._job_check names it."""
    run = Run(state, code, dict(rooms), dict(identities), stale_at, conversion)
    if state != "done":
        shown = state
    elif stale_at is not None:
        shown = "stale"
    else:
        shown = {"passed": "passed", "failed": "failed"}.get(code or "", "could_not_verify")
    return JobCheck(run, shown, "", to_fix=to_fix, unverified=unverified,
                    counts=state == "done" and stale_at is None)
