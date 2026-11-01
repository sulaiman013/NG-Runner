r"""
What each of Jim's checkers said, read from what it prints, failing closed (P2 build spec 2, "Shared
check runner: reading checker output").

Written from the print statements in the checkers' source (the toolkit copy of 2026-10-31), then checked
against what they printed when NG Runner ran crosscheck on staged copies of the ten real jobs, Cases A to
J, and against Jim's own crosscheck files of Cases C to G (tests/test_checks_real.py). A line is matched
whole, never searched in.

Why crosscheck's own verdict isn't enough. It flags a checker that exits non-zero or prints a line with
!!, MISSING, EXTRA, "drifted" or "differ" (its FLAG pattern), and calls everything else clean. Measured
on the real cases, that passes things that are not passes. Each was answered from Jim's own files on
2026-10-31 (docs/decisions.md, "Answered from Jim's own files", the question's number in brackets):
- outset_check prints FLAG and "N product(s) NOT at Outset 0 - review" and exits 0, so it is never
  flagged. eb_tops --check and top_line --check print "would change: N" and exit 0. Each is a rule
  of his (RULES: Outset 0, banding, the top line) that crosscheck's pattern misses, and Outset is one
  he always wants flagged: NG Runner flags them itself, failed, in words of its own (26).
- parm_check with no reference job prints "-- no counterpart" for every file and "RESULT: CLEAN" (its
  lines 63 and 82). A clean that compared nothing is could_not_verify, never clean (24).
- wm_variant_check's library is a Linux path, /mnt/user-data/uploads/... (its line 26): on Windows it
  isn't there, so its library check is skipped and it still prints "notch equation matches library".
  It counts only for what it could see, the geometry, so a fault there fails and a clean read is no
  pass: its library comparison wasn't run (23).
- A checker that stops with a traceback is flagged as if it found a fault (panel_xcheck on three real
  conversions it can't read, at its lines 87-89). Here that is could_not_verify, never "no findings":
  it found nothing either way (25).
- ?? lines, a NOTE that asks for the job to be opened, and a deferred check say a person must look.

So a pass needs: not flagged, every line one the source prints on a clean run, its own clean line for
every room it was given, and what it compares with in reach. A flag with a line that names a fault
is failed, and so is a finding crosscheck doesn't flag. Anything else, a line not seen there included,
is could_not_verify. ic_code_check is advisory, as in crosscheck, and is read the same way but never
decides the job.

A person sees only NG Runner's own words: the checkers print room and client names.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from ngrunner.guards.facts import COULD_NOT_VERIFY, FAILED, PASSED

_DES_PATH = r".+[\\/]Room\d+\.des"
_DES_NAME = r"Room\d+\.des"
_BLANK = r"\s*"

# Words for a person: fixed, so nothing of a job's own text reaches them.
FOUND_A_FAULT = "found something to fix"
STOPPED = "stopped without saying what it found"
UNFLAGGED_FAULT = "said something is wrong that Jim's crosscheck doesn't flag"
NG_RUNNER_FLAGS = "found something to fix that Jim's crosscheck doesn't flag, so NG Runner flags it"
BY_HAND = "said a person must check something by hand"
UNKNOWN_LINE = "printed a line NG Runner doesn't know"
NOT_EVERY_ROOM = "didn't name every room it was given"
NO_PASS_LINE = "printed no line that says it passed"
NO_READER = "has no reader in NG Runner"
NO_REFERENCE = "had no reference job to compare with, so it compared nothing"
NO_LIBRARY = ("reads its product library from a path this PC doesn't have, so it compared nothing with"
              " the library")


@dataclass(frozen=True)
class CheckerVerdict:
    """One checker's verdict on a job. code: PASSED, FAILED or COULD_NOT_VERIFY. why: NG Runner's
    words, empty for a pass. text: what it printed, kept for a repair item and never shown as it is,
    as it names rooms and clients."""

    checker: str
    code: str
    advisory: bool = False
    why: str = ""
    text: str = field(default="", repr=False)


@dataclass(frozen=True)
class _Spec:
    """What one checker prints, as whole-line patterns.

    ok: lines of a clean run. room: the line that opens a room's block. rooms: which rooms crosscheck
    gives it: "product" (Room0 apart), "all", or "none" (it names none). pass_line: a line a pass must
    print, once per room when per_room. counts_rooms: pass_line's first group is how many rooms it
    read. fault: lines naming a fault. not_flagged: findings crosscheck doesn't flag, which NG Runner
    flags itself (failed). by_hand: lines asking a person to look. needs: a line a pass must print at
    least once, with its own words."""

    ok: tuple[str, ...]
    fault: tuple[str, ...]
    room: str | None = None
    rooms: str = "product"
    pass_line: str | None = None
    per_room: bool = False
    counts_rooms: bool = False
    not_flagged: tuple[str, ...] = ()
    by_hand: tuple[str, ...] = ()
    needs: tuple[str, str] | None = None


_ROOM_LINE = r"   " + _DES_NAME + r" +"

SPECS: Mapping[str, _Spec] = {
    "section_align": _Spec(
        room=_DES_PATH + r"  \(.*\)",
        ok=(_BLANK, r"  WALL \S+   panel top line\(s\) = .+",
            r"    ok C\S* +.+ Elev=.+ top=.+",
            r"  --- bore rows on the verticals ---", r"    all bore rows clean"),
        pass_line=r"    all bore rows clean", per_room=True,
        fault=(r"    !! .+", r"        suggested Elev .+",
               r"    STALE CACHED ROWS - .+", r"    section in Mozaik so its ops recalc; .+")),
    "section_span": _Spec(
        room=_DES_PATH,
        ok=(_BLANK, r"   every bay is filled wall-of-vertical to wall-of-vertical"),
        pass_line=r"   every bay is filled wall-of-vertical to wall-of-vertical", per_room=True,
        fault=(r"   !! C.+",)),
    "drill_depth_check": _Spec(
        room=_DES_PATH,
        ok=(_BLANK, r"   every bore depth matches the adjacent section heights",
            r"   -- C\S* +.+ no line bore rows; \d+ discrete \d+mm cam-bolt holes \(sparse hardware connection\)"),
        pass_line=r"   every bore depth matches the adjacent section heights", per_room=True,
        fault=(r"   !! C.+",)),
    "outset_check": _Spec(
        rooms="none",
        ok=(_BLANK, r"PASS - every product at Outset 0"),
        pass_line=r"PASS - every product at Outset 0",
        fault=(),
        not_flagged=(r"  FLAG .+", r"\d+ product\(s\) NOT at Outset 0 - review")),
    "part_geom_check": _Spec(
        room=_DES_PATH,
        ok=(_BLANK, r"   every panel part matches its product's depth and height"),
        pass_line=r"   every panel part matches its product's depth and height", per_room=True,
        fault=(r"   !! C.+",)),
    "br_cam_check": _Spec(
        room=_DES_NAME,
        ok=(_BLANK, r"   ok BR C\S* +.+ NoShelfHoles=\S+ +vertical C\S* +cams at Y \S+"),
        fault=(r"   !! .+", r"        -> (?!NOTE: ).+"),
        by_hand=(r"        -> NOTE: .+",)),
    "op_schema_check": _Spec(
        rooms="all",
        ok=(_BLANK, r"  checked \d+ user-written operation element\(s\) across (\d+) file\(s\): ALL MATCH"),
        pass_line=r"  checked \d+ user-written operation element\(s\) across (\d+) file\(s\): ALL MATCH",
        counts_rooms=True,
        fault=(r"  !! .+", r"       INVENTED attribute\(s\): .+", r"       MISSING attribute\(s\):  .+",
               r"       Mozaik writes: .*", r"       we wrote:      .*",
               r"  checked \d+ user-written operation element\(s\) across \d+ file\(s\): \d+ REJECTED"),
        by_hand=(r"  \?\? .+ - no Mozaik-written example to compare against; check it by hand",)),
    "wm_variant_check": _Spec(
        room=_DES_PATH,
        ok=(_BLANK, r"   ok C\S* +.+ notch equation matches library, geometry agrees"),
        fault=(r"   !! C.+",),
        by_hand=(r"   -- C\S* +.+ no sections on wall .+ yet - variant check deferred",)),
    "notch_check": _Spec(
        # A "--" line is BUILD 8c's "leave it alone and report": the source never counts it a fault,
        # as it would never clear on an REA job. It reads as the source reads it.
        room=_DES_NAME,
        ok=(_BLANK, r"   every notch is on its line-bore face; WM banding taped and locked",
            r"   -- C\S* +.+ notch on face .*, line bores on .* - .+"),
        pass_line=r"   every notch is on its line-bore face; WM banding taped and locked", per_room=True,
        fault=(r"   !! .+",)),
    "eb_tops": _Spec(
        ok=(_BLANK, _ROOM_LINE + r"already correct", r"would change: 0 part\(s\)/slot\(s\)"),
        pass_line=_ROOM_LINE + r"already correct", per_room=True,
        needs=(r"would change: 0 part\(s\)/slot\(s\)", NO_PASS_LINE),
        fault=(),
        not_flagged=(_ROOM_LINE + r"COUNTERTOP .+", _ROOM_LINE + r"banding SLOT 6 -> .+",
                     _ROOM_LINE + r"\S.* slot \d+ .+", r"would change: [1-9]\d* part\(s\)/slot\(s\)")),
    "top_line": _Spec(
        ok=(_BLANK, _ROOM_LINE + r"already on its top line \(\S+\)", r"would change: 0 WM panels"),
        pass_line=_ROOM_LINE + r"already on its top line \(\S+\)", per_room=True,
        needs=(r"would change: 0 WM panels", NO_PASS_LINE),
        fault=(),
        not_flagged=(_ROOM_LINE + r".+ H=\S+ +Elev +\S+ -> +\S+  \(top \S+ -> \S+\)",
                     r"would change: [1-9]\d* WM panels")),
    "parm_check": _Spec(
        rooms="none",
        ok=(_BLANK, r"  OK \S.* identical", r"  -- \S.* no counterpart in the reference job",
            r"RESULT: CLEAN - parameters match the reference job"),
        pass_line=r"RESULT: CLEAN - parameters match the reference job",
        needs=(r"  OK \S.* identical", NO_REFERENCE),
        fault=(r"  !! \S.* \d+ parameter\(s\) differ", r"        \S+ +.+ this job = .* reference = .*\(want .*\)",
               r"RESULT: \d+ parameter file\(s\) drifted - fix before building")),
    "panel_xcheck": _Spec(
        rooms="none",
        ok=(_BLANK, r"IC board parts \d+   Mozaik cutlist \d+", r"-- \S.*: IC \d+  Mozaik \d+",
            r"     \(.+\) x\d+ <- \(.+\)  \(max [\d.]+mm\).*",
            r"     \(.+\) x\d+ <- bridge \(sized by Jim, matched on count - RULES BR\).*"),
        pass_line=r"IC board parts \d+   Mozaik cutlist \d+",
        fault=(r"   MISSING .+", r"   EXTRA   .+")),
    "ic_code_check": _Spec(
        room=_DES_NAME + r"   \(.*\)",
        ok=(_BLANK, r"   ok wall .+ IC wants .+ built .+", r"        C\S* +.+ (half drilled \(blind\)|THROUGH bores)",
            r"   -- wall .+ IC spec covers walls .+ together \(qty not per-wall\) - not judged",
            r"   ok C\S* +.+ H=.+ IC .+ -> .+"),
        # Its disagreements are ?? lines and a paragraph saying to raise them: never a fault by itself.
        fault=(r"   !! .+",),
        by_hand=(r"   \?\? .+", r"   \d+ group\(s\) disagree with the IC description\. ADVISORY ONLY - Jim's rules",
                 r"   override the IC codes, and IC descriptions carry known anomalies\. Raise it, do",
                 r"   not change the build on this alone\.")),
}


def read_checker(name: str, text: str, *, flagged: bool, product_rooms: int, all_rooms: int,
                 library_found: bool, advisory: bool = False) -> CheckerVerdict:
    """One checker's output as a verdict. flagged: crosscheck flagged it (a non-zero exit or one of
    its flag words). product_rooms, all_rooms: how many rooms it was given, without and with Room0.
    library_found: whether wm_variant_check's library is where it looks."""
    spec = SPECS.get(name)
    if spec is None:
        return _verdict(name, COULD_NOT_VERIFY, NO_READER, text, advisory)
    kinds = [_kind(spec, line) for line in text.splitlines()]
    if flagged:
        return _verdict(name, FAILED if "fault" in kinds else COULD_NOT_VERIFY,
                        FOUND_A_FAULT if "fault" in kinds else STOPPED, text, advisory)
    for kind, words in (("fault", UNFLAGGED_FAULT), (None, UNKNOWN_LINE)):
        if kind in kinds:
            return _verdict(name, COULD_NOT_VERIFY, words, text, advisory)
    if "not_flagged" in kinds:
        return _verdict(name, FAILED, NG_RUNNER_FLAGS, text, advisory)
    if "by_hand" in kinds:
        return _verdict(name, COULD_NOT_VERIFY, BY_HAND, text, advisory)
    why = _why_not_a_pass(spec, name, text, product_rooms, all_rooms, library_found)
    return _verdict(name, COULD_NOT_VERIFY if why else PASSED, why, text, advisory)


def _kind(spec: _Spec, line: str) -> str | None:
    for kind, patterns in (("room", (spec.room,) if spec.room else ()), ("fault", spec.fault),
                           ("not_flagged", spec.not_flagged), ("by_hand", spec.by_hand), ("ok", spec.ok)):
        if any(re.fullmatch(pattern, line) for pattern in patterns):
            return kind
    return None


def _why_not_a_pass(spec: _Spec, name: str, text: str, product_rooms: int, all_rooms: int,
                    library_found: bool) -> str:
    lines = text.splitlines()
    if not any(line.strip() for line in lines):
        return NO_PASS_LINE
    given = {"product": product_rooms, "all": all_rooms, "none": 0}[spec.rooms]
    if spec.room is not None and sum(bool(re.fullmatch(spec.room, line)) for line in lines) != given:
        return NOT_EVERY_ROOM
    if spec.pass_line is not None:
        found = [match for line in lines if (match := re.fullmatch(spec.pass_line, line))]
        wanted = given if spec.per_room else 1
        if len(found) != wanted:
            return NOT_EVERY_ROOM if spec.per_room and found else NO_PASS_LINE
        if spec.counts_rooms and int(found[0][1]) != given:
            return NOT_EVERY_ROOM
    if spec.needs is not None and not any(re.fullmatch(spec.needs[0], line) for line in lines):
        return spec.needs[1]
    if spec.room is None and spec.pass_line is None:
        return NO_PASS_LINE
    if name == "wm_variant_check" and not library_found:
        return NO_LIBRARY
    return ""


def _verdict(name: str, code: str, why: str, text: str, advisory: bool) -> CheckerVerdict:
    return CheckerVerdict(checker=name, code=code, advisory=advisory, why=why, text=text)
