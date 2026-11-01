r"""
The replay trial: NG Runner's check of a real job set finding by finding against what Jim's chat reported
on the same job (P3, "Trial checks: 15 compared with chat"; design 7's exit: "15 checks compared with
chat: 0 missed ACT items and 0 false cleans; noise <= 1 per job and 0 in the last 5").

Why offline first. The live trial compares NG Runner's check with the chat's on jobs as Jim finishes them,
on his PC. Before that, the chat's own saved check output sits in many jobs' NG Jobs folders: Jim's
crosscheck file as the chat ran it, and the chat's panel checks and reports. Replaying NG Runner's check
on a staged copy of the same job (ruling R1) and comparing shows where the two disagree, and which is
right by Jim's RULES, before any live job depends on it.

The unit compared is one finding: a checker, the kind of fault (findings.py's codes), and one part: a tag
(R2C3), a room (R2), or a size (WxL in mm) for the panel set. An item naming three parts is three
findings. Only checkers the chat's output speaks for are compared; what the chat found that no checker
of NG Runner's looks at (materials, the tracker, prices) is counted apart, as outside the check.

What the chat reported comes two ways. Jim's crosscheck file, as the chat saved it, is read here with
NG Runner's own readers, so both sides use one vocabulary and nothing is transcribed. The chat's own
reports (panel checks, verification notes) are free text: a person reads them into findings in the same
vocabulary (tests/test_trial_real.py holds that reading, by job code, with the line of RULES that says
who is right where the two differ). Nothing either side printed is kept here.

A comparison never decides who is right: that is a person's reading of RULES, recorded with the job.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ngrunner.checks.crosscheck import Planned, read_crosscheck
from ngrunner.checks.findings import CNV, FIX, MOZAIK, Findings, findings_of
from ngrunner.guards.facts import COULD_NOT_VERIFY, ToolRun

OUTSIDE = "outside"                 # a chat finding no checker of NG Runner's looks at
_RECHECK = re.compile(r"\n===== RECHECK")


@dataclass(frozen=True, order=True)
class Finding:
    """One finding: a checker, the kind of fault, and one part (a tag, a room, a size, or "")."""

    checker: str
    what: str
    part: str = ""


@dataclass(frozen=True)
class ChatReport:
    """What the chat reported on one job. covers: the checkers its output speaks for; findings: what it
    raised for Jim to act on, in NG Runner's vocabulary. Findings under OUTSIDE are about something no
    checker of NG Runner's looks at."""

    covers: tuple[str, ...]
    findings: frozenset[Finding]


@dataclass(frozen=True)
class Compared:
    """One job compared. both: raised by both. ours_only: by NG Runner alone. chat_only: by the chat
    alone, on a checker NG Runner read. unverified: by the chat alone, on a checker NG Runner could
    not verify (no miss, and no clean either). outside: the chat's findings outside the check.
    ours_clean / chat_clean: no finding on the checkers compared."""

    both: frozenset[Finding]
    ours_only: frozenset[Finding]
    chat_only: frozenset[Finding]
    unverified: frozenset[Finding]
    outside: frozenset[Finding]

    @property
    def agrees(self) -> bool:
        return not (self.ours_only or self.chat_only or self.unverified)


def findings_in(found: Findings, *, covers: Iterable[str] | None = None) -> frozenset[Finding]:
    """Each fault NG Runner found, one finding per part it names, on the checkers in covers (all when
    None). Could not verify and advisory items are no findings."""
    wanted = None if covers is None else set(covers)
    out: set[Finding] = set()
    for item in found.items:
        if item.kind not in (FIX, MOZAIK) or (wanted is not None and item.checker not in wanted):
            continue
        parts = [*item.tags, *(size_key(w, length) for w, length, _ in item.sizes)]
        out.update(Finding(item.checker, item.what, part) for part in parts or [""])
    return frozenset(out)


def unverified_checkers(found: Findings) -> frozenset[str]:
    """The checkers whose verdict is could not verify in this run."""
    return frozenset(v.checker for v in found.verdicts if v.code == COULD_NOT_VERIFY and not v.advisory)


def size_key(width_mm: float, length_mm: float) -> str:
    """A part's size as one finding's part, short side first, to 0.1 mm."""
    a, b = sorted((width_mm, length_mm))
    return f"{a:.1f}x{b:.1f}"


def compare(ours: Findings, chat: ChatReport) -> Compared:
    """NG Runner's findings against the chat's, on the checkers the chat's output speaks for."""
    covered = set(chat.covers)
    mine = findings_in(ours, covers=covered)
    theirs = frozenset(f for f in chat.findings if f.checker != OUTSIDE and f.checker in covered)
    outside = frozenset(f for f in chat.findings if f.checker == OUTSIDE)
    blind = unverified_checkers(ours)
    missing = theirs - mine
    return Compared(both=mine & theirs, ours_only=mine - theirs,
                    chat_only=frozenset(f for f in missing if f.checker not in blind),
                    unverified=frozenset(f for f in missing if f.checker in blind), outside=outside)


# ----- the chat's own crosscheck file, read with NG Runner's readers


def chat_crosscheck(text: str, *, plan: Sequence[Planned], kind: str = "check",
                    q1_settled: bool | None = None) -> tuple[ChatReport, Findings]:
    """Jim's crosscheck file as the chat saved it, read as NG Runner reads its own: what crosscheck
    wrote, without what the chat added below it (its RECHECK notes). Its exit code and printed lines
    aren't saved, so they are taken from its verdict line, as crosscheck makes them. The library and
    the reference job were in reach where the chat ran it."""
    text = _RECHECK.split(text, maxsplit=1)[0]
    lines = text.splitlines()
    rooms_line = re.fullmatch(r"  rooms   (.*)", lines[1]) if len(lines) > 1 else None
    verdict = lines[3].strip() if len(lines) > 3 else ""
    rooms = tuple(rooms_line[1].split(", ")) if rooms_line else ()
    run = ToolRun(1 if "FLAGGED" in verdict else 0, f"{verdict}\nwritten: x\n", "", False)
    result = read_crosscheck(text, run, plan=plan, rooms=rooms, library_found=True)
    found = findings_of(result, kind=kind, q1_settled=q1_settled)
    covers = tuple(p.name for p in plan if not p.advisory)
    return ChatReport(covers, findings_in(found)), found


def counts(compared: Iterable[Compared]) -> dict[str, int]:
    """Totals over jobs: findings both raised, NG Runner only, chat only, unverified, outside."""
    total = {"both": 0, "ours_only": 0, "chat_only": 0, "unverified": 0, "outside": 0}
    for one in compared:
        for name in total:
            total[name] += len(getattr(one, name))
    return total


__all__ = ["CNV", "OUTSIDE", "ChatReport", "Compared", "Finding", "chat_crosscheck", "compare", "counts",
           "findings_in", "size_key", "unverified_checkers"]
