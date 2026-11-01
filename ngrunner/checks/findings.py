r"""
What a check run found, as items Jim can act on (P3: "Repair items with a ready chat line", "Audit of
chat-built jobs"; design 4, "Format rules" and "Noise").

Why it exists: the check runner (crosscheck.py, readers.py) says, per checker, passed, failed or could
not verify, and keeps what each printed. Jim needs more than a verdict and less than the printout: one
numbered line per thing to do, naming the parts by tag (R2C3), with the line he pastes into his chat to
fix it, and for what wasn't checked, what and why. And none of it may quote a checker: they print room
and client names, product names and file paths. So each fault line is read for structure only: its
room, its cabinet number, a size, and which kind of fault it is (a table here, from the print
statements in the checkers' source and their real output on Cases A to J). Words are templates.py's.

What an item is:
    fix               Jim's chat fixes it: it has a ready chat line. A finding Jim's crosscheck doesn't
                      flag (a product off Outset 0, banding or a top line its checkers would change) is
                      one too: NG Runner flags it itself (readers.py, answered from Jim's files, 26).
    mozaik            Only Jim in Mozaik can fix it (a bore row left from an old section height, a line
                      bore off the panel): the item says so, and has no chat line.
    could_not_verify  What wasn't checked, and why: a library on a path this PC doesn't have, no
                      reference job, a crash, a line not known, a check his crosscheck doesn't run (the
                      hinge count, on a garage), or the top's numbering waiting for Q1. Never settled:
                      it is a fact about the run.
    advisory          ic_code_check's, as in crosscheck: shown, never to fix, never deciding.

Failing closed: a fault line no table here names is still an item ("found something to fix"), with the
tags it holds, and a checker whose output couldn't be read is one could-not-verify item. Nothing a
checker said is dropped on the way to Jim, but known noise.

Known noise (design 4, "Noise"): what a checker prints that Jim's own RULES say is never a fault. It is
counted and kept with the run (Findings.noise, shown in the board's check), never an item, never why a
check fails. Two kinds, each only in its exact shape; a line that differs in any way is still an item:
    tr_base_4_1    panel_xcheck's MISSING TR Base at 4.1 in (104.1 mm) and EXTRA at 4 in (101.6 mm), both
                   96 in long and as many, in the TR/FF family. RULES 266-268: "TR Base is ALWAYS 4x96 -
                   101.6 x 2438.4 ... IC prints it 4.1 ... never flag the 4.1 vs 4.0 difference - not as an
                   open question, not as a panel-check mismatch". His panel_xcheck matches it on 101.6
                   itself when the IC Conversion's line says TR Base; an older Conversion's doesn't.
    sys_holes_w_4  parm_check's SysHolesW at 4 ("1 Hole per shelf") in the job's NG Closets-JobParms.dat
                   (RULES 573-576 name that file). RULES 604-614 (Jim, 2026-10-21): his own hardware
                   connection on the upper sections, set on purpose. parm_check names a line by its place
                   in that file (its KNOWN), so the same line of another file is no SysHolesW for sure.
A failed checker is read again without its noise, as it would have printed it then: still failed on what
is left, or judged as a clean run is (readers.py), so noise beside a line not known is could not verify.
Rail covers and the Hang Rail are not known noise: RULES says both that they never appear in a panel
check (245-247) and that their count is checked (662-665), which is Jim's question 39.

The audit of a chat-built job, before its sections go in (design 7, P3): the panel set, the top line,
the operations and the parameters, which crosscheck already runs. Its other checkers are about sections
and wait for the "sections done" check. NG Runner adds one check of its own to both, which crosscheck
leaves out and the design asks of a built room (3.5, Verticals: "CRLF"): each room file written the way
Mozaik writes it, a version line then CRLF throughout. A version never seen is could not verify; LF line
ends, or a file that doesn't read as a design, are to fix (FAILURES 17: that is how a text-mode edit
shows). Legal WM names (also 3.5) are left out: which names are legal is one of the docs'
contradictions (stage map D5), for Jim.

NG Runner's own top line (topline.py, P5's placement rule read as it is): one T per room and every WM
panel on it (RULES 330-339), with gate 5's Elev grid (RULES 319), on both kinds of run. Jim's top_line
--check says the same of a room it would change, so where his item names a room, NG Runner's own
"off the top line" items for that room are left out: one item per fault. Its Elev grid, which his
check never looks at, always stands.

Q1: until Jim settles how the top is numbered (RULES 580-582 against BUILD 170-180), parm_check can't
pass or fail on the top. A parameter file that matches the template proves nothing about the top while
the template's own value is the question, and a top that differs is could not verify, never a settle.
Another parameter that differs is still to fix. Q1_SETTLED is that answer; it is False.

hinge_check (garage doors of 30 inches or less get 2 hinges) runs nowhere: crosscheck leaves it out
(stage map D17, Q17a). It should run on garage jobs once they leave chat; until then a garage job's
check says its hinge count could not be verified.

A fingerprint is a sha256 of the checker, the kind of fault and the very lines it stands for: the same
finding on the same files has the same one, and a moved part or value gives another. A settle is
bound to it (ngrunner.checks.jobchecks).
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from ngrunner.checks.crosscheck import LEFT_OUT, Crosscheck
from ngrunner.checks import topline
from ngrunner.checks.readers import BY_HAND, NG_RUNNER_FLAGS, SPECS, read_checker
from ngrunner.guards.bak import DesignUnreadable, parse_room
from ngrunner.guards.facts import COULD_NOT_VERIFY, FAILED, PASSED

FIX, MOZAIK, CNV, ADVISORY = "fix", "mozaik", "could_not_verify", "advisory"
ITEM_KINDS = (FIX, MOZAIK, CNV, ADVISORY)
AUDIT_CHECKERS = ("panel_xcheck", "top_line", "op_schema_check", "parm_check")
ROOM_FILES = "room_files"                 # NG Runner's own: each room as Mozaik writes it
ROOM_TOP = "room_top"                     # NG Runner's own: one top line per room (topline.py)
HINGE = "hinge_check"
Q1_SETTLED = False                        # docs/decisions.md, P3: Q1 is tested live in Mozaik first
TOP_PARAMETERS = frozenset({"BTop", "WTop", "BotNail"})     # RULES 637-640, Q1
NEVER_SETTLED = frozenset({"bore_off_panel"})                # design 4, Noise: never silenceable
TR_BASE_4_1, SPARSE_UPPERS = "tr_base_4_1", "sys_holes_w_4"  # known noise, by Jim's RULES (above)

_ROOM = re.compile(r"Room(\d+)\.des", re.IGNORECASE)
_C = r"C(?P<c>\d+)"
_VERSION = b"15"

# Each checker's fault lines, by kind of fault: (a whole-line pattern, its code, fix or mozaik). A line
# may name two (section_align joins its flags with "; ").
_FAULTS: Mapping[str, tuple[tuple[str, str, str], ...]] = {
    "section_align": (
        (rf"    !! {_C} .*off the 32mm grid.*", "off_grid", FIX),
        (rf"    !! {_C} .*vs the panels'.*", "top_off", FIX),
        (rf"    !! {_C} +Y=\S+ +face=.*", "bore_pitch", FIX),
        (rf"    !! {_C} +Y=\S+ +FACES OUT OF REGISTER.*", "faces_off", FIX),
        (rf"    !! {_C} +Y=\S+ +X=.*implies a section at.*", "stale_rows", MOZAIK),
    ),
    "section_span": ((rf"   !! {_C} .*", "span", FIX),),
    "drill_depth_check": (
        (rf"   !! {_C} .*line bore OFF THE PANEL.*", "bore_off_panel", MOZAIK),
        (rf"   !! {_C} .*NO line bore rows at all.*", "bores_stripped", FIX),
        (rf"   !! {_C} .*holes wrong: Depth=.*", "bore_depth", FIX),
    ),
    "part_geom_check": ((rf"   !! {_C} .*", "part_size", FIX),),
    "br_cam_check": (
        (rf"   !! BR {_C} .*", "bridge_cams", FIX),
        (rf"   !! {_C} .*HOLES ON TOP OF EACH OTHER.*", "doubled_holes", FIX),
    ),
    "op_schema_check": ((r"  !! \S+ in .*", "op_schema", FIX),),
    "wm_variant_check": (
        (rf"   !! {_C} .*geometry says.*", "wm_variant", FIX),
        (rf"   !! {_C} .*active groove.*", "wm_groove", FIX),
    ),
    "notch_check": (
        (rf"   !! {_C} .*NOTCH ON THE WRONG FACE", "notch_face", FIX),
        (r"   !! .*WM banding not taped \+ locked on top/front/bottom", "wm_banding", FIX),
    ),
    "parm_check": ((r"        (?P<p>\S+) +.+ this job = .* reference = .*\(want .*\)", "parameters", FIX),),
    "panel_xcheck": (
        (r"   MISSING \((?P<w>[\d.]+), (?P<l>[\d.]+)\) x(?P<q>\d+).*", "missing_part", FIX),
        (r"   EXTRA   \((?P<w>[\d.]+), (?P<l>[\d.]+)\) x(?P<q>\d+).*", "extra_part", FIX),
    ),
    "ic_code_check": ((r"   !! .*", "ic_code", ADVISORY),),
}
# Lines that explain the fault before them, or head a list of them: part of a finding, never one.
_EXPLAINS: Mapping[str, tuple[str, ...]] = {
    "section_align": (r"        suggested Elev .+", r"    STALE CACHED ROWS - .+",
                      r"    section in Mozaik so its ops recalc; .+"),
    "br_cam_check": (r"        -> (?!NOTE: ).+",),
    "op_schema_check": (r"       INVENTED attribute\(s\): .+", r"       MISSING attribute\(s\):  .+",
                        r"       Mozaik writes: .*", r"       we wrote:      .*",
                        r"  checked \d+ user-written operation element\(s\) across \d+ file\(s\): \d+ REJECTED"),
    "parm_check": (r"  !! \S.* \d+ parameter\(s\) differ", r"RESULT: \d+ parameter file\(s\) drifted - fix before building"),
}
# What crosscheck doesn't flag, which NG Runner flags itself as a fault (26), and what asks a person to look.
_UNFLAGGED: Mapping[str, tuple[tuple[str, str], ...]] = {
    "outset_check": ((r"  FLAG .*?Room(?P<r>\d+)\.des +C(?P<c>\d+) .*", "outset"),),
    "eb_tops": ((r"   Room(?P<r>\d+)\.des +(?!already )\S.*", "banding"),),
    "top_line": ((r"   Room(?P<r>\d+)\.des +(?!already )\S.*", "top_line"),),
}
_BY_HAND: Mapping[str, tuple[tuple[str, str], ...]] = {
    "op_schema_check": ((r"  \?\? \S+ - no Mozaik-written example to compare against; check it by hand",
                         "op_by_hand"),),
    "wm_variant_check": ((rf"   -- {_C} .*variant check deferred", "variant_deferred"),),
}


@dataclass(frozen=True)
class Item:
    """One thing a check found. n: its number in the run. tags: R2C3, or R2 for a room as a whole.
    sizes: (width mm, length mm, how many) of a part the panel set names by size. count: how many it
    stands for, where that is what is said (parameters, lines). why: for could_not_verify, the reader's
    words or the run's. settles: Jim may settle it for this job. Nothing here is what a checker printed."""

    n: int
    kind: str
    checker: str
    what: str
    tags: tuple[str, ...] = ()
    sizes: tuple[tuple[float, float, int], ...] = ()
    count: int = 0
    why: str = ""
    fingerprint: str = ""
    settles: bool = False


@dataclass(frozen=True)
class Verdict:
    """A checker's verdict as the run counts it: after the audit's choice and Q1. why: NG Runner's words."""

    checker: str
    code: str
    advisory: bool = False
    why: str = ""


@dataclass(frozen=True)
class Noise:
    """Known noise in a run: what a checker printed that Jim's RULES say is never a fault. what: which
    (TR_BASE_4_1, SPARSE_UPPERS). count: how many parts or parameters it stands for."""

    checker: str
    what: str
    count: int


@dataclass(frozen=True)
class Findings:
    """A run's code (passed only when every deciding check passed), its items in the order Jim reads
    them, its verdicts, what crosscheck leaves out, and its known noise, which is never an item."""

    code: str
    items: tuple[Item, ...] = ()
    verdicts: tuple[Verdict, ...] = ()
    left_out: tuple[str, ...] = LEFT_OUT
    noise: tuple[Noise, ...] = ()
    words: str = field(default="", compare=False)


# ----- from the runner's verdicts to items


def findings_of(result: Crosscheck, *, kind: str = "check", room_files: Mapping[str, bytes] | None = None,
                garage: bool = False, q1_settled: bool | None = None) -> Findings:
    """What result found, as items. kind "audit" reads only AUDIT_CHECKERS. room_files: each room's bytes
    as they ran, for NG Runner's own checks of how they are written and of their top line. garage: say
    the hinge count wasn't checked."""
    if kind not in ("check", "audit"):
        raise ValueError("a run is a check or an audit")
    q1_settled = Q1_SETTLED if q1_settled is None else q1_settled
    found: list[Item] = []
    verdicts: list[Verdict] = []
    noise: list[Noise] = []
    if _whole_run_failed(result):
        found.append(_item(CNV, "", "not_read", why=result.words))
        verdicts += [Verdict(v.checker, COULD_NOT_VERIFY, v.advisory, result.words) for v in result.verdicts
                     if kind == "check" or v.checker in AUDIT_CHECKERS]
    else:
        for verdict in result.verdicts:
            if kind == "audit" and verdict.checker not in AUDIT_CHECKERS:
                continue
            verdict, quiet = _quieted(verdict)
            noise += quiet
            code, items = _read_verdict(verdict, q1_settled)
            verdicts.append(Verdict(verdict.checker, code, verdict.advisory, verdict.why))
            found += items
    if room_files is not None:
        code, items = _room_files(room_files)
        verdicts.append(Verdict(ROOM_FILES, code))
        found += items
        code, items = _room_top(room_files, found)
        verdicts.append(Verdict(ROOM_TOP, code))
        found += items
    if garage:
        verdicts.append(Verdict(HINGE, COULD_NOT_VERIFY, why="not run"))
        found.append(_item(CNV, HINGE, "not_run"))
    ordered = sorted(found, key=lambda item: ITEM_KINDS.index(item.kind) if item.kind != MOZAIK else 0)
    numbered = tuple(_numbered(item, n) for n, item in enumerate(ordered, start=1))
    return Findings(_overall(verdicts), numbered, tuple(verdicts), tuple(result.left_out), tuple(noise),
                    words=result.words)


def _whole_run_failed(result: Crosscheck) -> bool:
    """crosscheck's own output couldn't be read: every verdict carries the run's words."""
    return result.code == COULD_NOT_VERIFY and all(v.why == result.words for v in result.verdicts)


def _read_verdict(verdict: Any, q1_settled: bool) -> tuple[str, list[Item]]:
    checker, code = verdict.checker, verdict.code
    if verdict.advisory:
        return code, ([] if code == PASSED else [_advisory(verdict)])
    if checker == "parm_check" and not q1_settled:
        return _parameters_before_q1(verdict)
    if code == PASSED:
        return code, []
    if code == FAILED and verdict.why == NG_RUNNER_FLAGS and checker in _UNFLAGGED:
        return code, _matched(checker, verdict.text, _UNFLAGGED[checker], FIX) or [
            _item(CNV, checker, "checker", why="found something to fix, and NG Runner could not tell what")]
    if code == FAILED:
        return code, _faults(checker, verdict.text)
    if verdict.why == BY_HAND and checker in _BY_HAND:
        return code, _matched(checker, verdict.text, _BY_HAND[checker], CNV, why=verdict.why) or [
            _item(CNV, checker, "checker", why=verdict.why)]
    return COULD_NOT_VERIFY, [_item(CNV, checker, "checker", why=verdict.why)]


def _faults(checker: str, text: str) -> list[Item]:
    """A failed checker's fault lines, grouped by kind of fault across the rooms, each group one item:
    one line for Jim per thing to do, naming every part it covers."""
    spec, table = SPECS.get(checker), _FAULTS.get(checker, ())
    explains = tuple(re.compile(p) for p in _EXPLAINS.get(checker, ()))
    groups: dict[tuple[str, str], _Group] = {}
    room: int | None = None
    last: _Group | None = None
    for line in text.splitlines():
        room = _room_of(line, spec, room)
        if any(p.fullmatch(line) for p in explains):
            if last is not None:
                last.lines.append(line)
            continue
        if not _is_fault(spec, line):
            continue
        named = [(what, kind, match) for pattern, what, kind in table if (match := re.fullmatch(pattern, line))]
        for what, kind, match in named or [("other", FIX, re.search(rf"\b{_C}\b", line))]:
            last = groups.setdefault((what, kind), _Group(checker, what, kind))
            last.add(line, match, room)
    if not groups:              # flagged, and no line names a fault: the reader would not have said failed
        return [_item(CNV, checker, "checker", why="found something to fix, and NG Runner could not tell what")]
    return [group.item() for group in groups.values()]


def _matched(checker: str, text: str, table: Sequence[tuple[str, str]], kind: str, *, why: str = ""
             ) -> list[Item]:
    spec = SPECS.get(checker)
    groups: dict[str, _Group] = {}
    room: int | None = None
    for line in text.splitlines():
        room = _room_of(line, spec, room)
        for pattern, what in table:
            match = re.fullmatch(pattern, line)
            if match:
                at = int(match["r"]) if "r" in match.groupdict() and match["r"] else room
                groups.setdefault(what, _Group(checker, what, kind, why=why)).add(line, match, at)
    return [group.item() for group in groups.values()]


def _parameters_before_q1(verdict: Any) -> tuple[str, list[Item]]:
    """parm_check while Q1 is open: the top is could not verify, and any other parameter that differs
    is still to fix. A file that matches the template proves nothing of the top, as the template's own
    top is the question."""
    if verdict.code == COULD_NOT_VERIFY:
        return COULD_NOT_VERIFY, [_item(CNV, "parm_check", "checker", why=verdict.why)]
    if verdict.code == PASSED:
        return COULD_NOT_VERIFY, [_item(CNV, "parm_check", "q1")]
    pattern = re.compile(_FAULTS["parm_check"][0][0])
    named = [(found["p"], line) for line in verdict.text.splitlines() if (found := pattern.fullmatch(line))]
    top = [line for name, line in named if name in TOP_PARAMETERS]
    rest = [line for name, line in named if name not in TOP_PARAMETERS]
    out = []
    if rest:
        out.append(Item(0, FIX, "parm_check", "parameters", count=len(rest), settles=True,
                        fingerprint=_fingerprint("parm_check", "parameters", rest)))
    if top:
        out.append(Item(0, CNV, "parm_check", "q1", count=len(top), fingerprint=_fingerprint("parm_check", "q1", top)))
    if not named:                 # failed, and no line names a parameter: what the runner found stands
        out = _faults("parm_check", verdict.text)
    return (FAILED if any(item.kind == FIX for item in out) else COULD_NOT_VERIFY), out


def _advisory(verdict: Any) -> Item:
    lines = [line for line in verdict.text.splitlines() if line.startswith(("   !! ", "   ?? "))]
    return Item(0, ADVISORY, verdict.checker, "ic_code", count=len(lines),
                fingerprint=_fingerprint(verdict.checker, "ic_code", lines))


def _room_files(rooms: Mapping[str, bytes]) -> tuple[str, list[Item]]:
    """Each room written the way Mozaik writes it: version line 15, CRLF throughout, a <Room>."""
    items = []
    for name, data in sorted(rooms.items(), key=lambda pair: _number(pair[0])):
        tag = f"R{_number(name)}"
        head = data.partition(b"\r\n")[0] if b"\r\n" in data else data.partition(b"\n")[0]
        if head.isdigit() and head != _VERSION:
            items.append(Item(0, CNV, ROOM_FILES, "version", (tag,),
                              fingerprint=_fingerprint(ROOM_FILES, "version", [name, head.decode()])))
            continue
        try:
            parse_room(data)
            crlf = data.count(b"\n") == data.count(b"\r\n")
        except DesignUnreadable:
            crlf = False
        if not crlf:
            items.append(Item(0, FIX, ROOM_FILES, "room_file", (tag,), settles=True,
                              fingerprint=_fingerprint(ROOM_FILES, "room_file",
                                                       [name, hashlib.sha256(data).hexdigest()])))
    if any(item.kind == FIX for item in items):
        return FAILED, items
    return (COULD_NOT_VERIFY if items else PASSED), items


def _room_top(rooms: Mapping[str, bytes], found: Sequence[Item]) -> tuple[str, list[Item]]:
    """Each product room's top line (topline.py). A room Jim's top_line item already names keeps only
    what his check doesn't look at: the Elev grid."""
    his = {tag for item in found if (item.checker, item.what) == ("top_line", "top_line") for tag in item.tags}
    items: list[Item] = []
    codes = []
    for top in topline.rooms_top(rooms):
        codes.append(top.code)
        room = f"R{top.room}"
        said = [room, f"{top.t_mm}", *(f"C{cab} {elev:.2f} {height:.2f}" for cab, elev, height in top.hung)]
        if topline.OFF_TOP in top.found and room not in his:
            items.append(_own(FIX, topline.OFF_TOP, tuple(f"{room}C{c}" for c in top.off_top), said))
        if topline.TWO_TOPS in top.found and room not in his:
            items.append(_own(FIX, topline.TWO_TOPS, (room,), said))
        if topline.ELEV_GRID in top.found:
            items.append(_own(FIX, topline.ELEV_GRID, tuple(f"{room}C{c}" for c in top.off_grid), said))
        if topline.AS_BUILT in top.found:
            items.append(_own(CNV, topline.AS_BUILT, (room,), said))
    if FAILED in codes:
        return FAILED, items
    return (COULD_NOT_VERIFY if COULD_NOT_VERIFY in codes else PASSED), items


def _own(kind: str, what: str, tags: tuple[str, ...], said: Sequence[str]) -> Item:
    return Item(0, kind, ROOM_TOP, what, tags, count=len(tags), settles=kind == FIX,
                fingerprint=_fingerprint(ROOM_TOP, what, [*tags, *said]))


def _overall(verdicts: Sequence[Verdict]) -> str:
    deciding = [v.code for v in verdicts if not v.advisory]
    if FAILED in deciding:
        return FAILED
    return PASSED if deciding and set(deciding) == {PASSED} else COULD_NOT_VERIFY


# ----- known noise: what Jim's RULES say a checker prints that is never a fault (the docstring's table)


_FAMILY = re.compile(r"-- (?P<f>\S.*): IC \d+  Mozaik \d+")
_TR_IC = re.compile(r"   MISSING \(104\.1, 2438\.4\) x(?P<q>\d+)(?:   .*)?")     # IC's 4.1 x 96 (RULES 262, 266)
_TR_BUILT = re.compile(r"   EXTRA   \(101\.6, 2438\.4\) x(?P<q>\d+)")           # the Lux 4in Base, 4 x 96
_PARM_FILE = re.compile(r"  !! (?P<f>\S.*?) +(?P<n>\d+) parameter\(s\) differ")
_PARAMETER = re.compile(_FAULTS["parm_check"][0][0])
_SPARSE = re.compile(r"        SysHolesW +.+ this job = 4 +reference = .*\(want .*\)")    # RULES 610
_DRIFTED = re.compile(r"RESULT: (?P<k>\d+) parameter file\(s\) drifted - fix before building")
_JOB_PARAMETERS = "NG Closets-JobParms.dat"                                         # RULES 573-576


def _quieted(verdict: Any) -> tuple[Any, list[Noise]]:
    """A failed checker's verdict without its known noise, and the noise. What is left is read as the
    checker would have printed it without the noise: still failed on a fault left, or else judged as a
    run crosscheck didn't flag (readers.read_checker), so noise beside a line not known can't pass. Only
    a failed verdict is read so: one that could not verify stays so, noise or not."""
    rule = _NOISE.get(verdict.checker)
    if rule is None or verdict.code != FAILED or verdict.advisory:
        return verdict, []
    text, noise = rule(verdict.text)
    if not noise:
        return verdict, []
    spec = SPECS[verdict.checker]
    if any(_is_fault(spec, line) for line in text.splitlines()):
        return replace(verdict, text=text), noise
    # Both checkers name no room (rooms "none"), so the rooms they were given don't enter their verdict.
    return read_checker(verdict.checker, text, flagged=False, product_rooms=0, all_rooms=0,
                        library_found=True), noise


def _tr_base_noise(text: str) -> tuple[str, list[Noise]]:
    """panel_xcheck: in the TR/FF family, one MISSING TR Base at 4.1 x 96 and one EXTRA at 4 x 96, as
    many: dropped, as his panel_xcheck prints it when the IC line says TR Base. A TR Base alone, of
    another length or count, or at another width is a fault (RULES 240-242: missing is missing)."""
    lines = text.splitlines()
    sides: dict[int, dict[str, list[tuple[int, int]]]] = {}       # TR/FF block: ic, built: (line, count)
    block: int | None = None
    for n, line in enumerate(lines):
        if head := _FAMILY.fullmatch(line):
            block = n if head["f"] == "TR/FF" else None
            continue
        for side, pattern in (("ic", _TR_IC), ("built", _TR_BUILT)):
            if block is not None and (found := pattern.fullmatch(line)):
                sides.setdefault(block, {}).setdefault(side, []).append((n, int(found["q"])))
    drop: set[int] = set()
    noise = []
    for pair in sides.values():
        ic, built = pair.get("ic", []), pair.get("built", [])
        if len(ic) == len(built) == 1 and ic[0][1] == built[0][1]:
            drop |= {ic[0][0], built[0][0]}
            noise.append(Noise("panel_xcheck", TR_BASE_4_1, ic[0][1]))
    return "\n".join(line for n, line in enumerate(lines) if n not in drop), noise


def _sparse_uppers_noise(text: str) -> tuple[str, list[Noise]]:
    """parm_check: SysHolesW at 4 in the job's NG Closets-JobParms.dat, dropped, and the rest as
    parm_check prints it had the job carried the template's value: a file left with no difference
    identical, and RESULT counting the files still drifted. Output whose counts don't add up is left
    as it is."""
    lines, out = text.splitlines(), []
    heads: dict[int, tuple[str, int]] = {}            # out's line: (file, how many it says differ)
    quiet: dict[int, int] = {}
    block: int | None = None
    for line in lines:
        if head := _PARM_FILE.fullmatch(line):
            block = len(out)
            heads[block] = (head["f"], int(head["n"]))
        elif not _PARAMETER.fullmatch(line):
            block = None
        elif block is not None and heads[block][0] == _JOB_PARAMETERS and _SPARSE.fullmatch(line):
            quiet[block] = quiet.get(block, 0) + 1
            continue
        out.append(line)
    drifted = [at for at, line in enumerate(out) if _DRIFTED.fullmatch(line)]
    if (not quiet or any(n > heads[at][1] for at, n in quiet.items()) or len(drifted) != 1
            or int(_DRIFTED.fullmatch(out[drifted[0]])["k"]) != len(heads)):    # each !! file is one drifted
        return text, []
    for at, n in quiet.items():
        name, said = heads[at]
        out[at] = f"  OK {name:<44} identical" if n == said else f"  !! {name:<44} {said - n} parameter(s) differ"
    left = len(heads) - sum(n == heads[at][1] for at, n in quiet.items())
    out[drifted[0]] = (f"RESULT: {left} parameter file(s) drifted - fix before building" if left
                       else "RESULT: CLEAN - parameters match the reference job")
    return "\n".join(out), [Noise("parm_check", SPARSE_UPPERS, sum(quiet.values()))]


_NOISE: Mapping[str, Callable[[str], tuple[str, list[Noise]]]] = {
    "panel_xcheck": _tr_base_noise, "parm_check": _sparse_uppers_noise}


# ----- small pieces


class _Group:
    """Fault lines of one kind, as they come, and the parts they name."""

    def __init__(self, checker: str, what: str, kind: str, *, why: str = "") -> None:
        self.checker, self.what, self.kind, self.why = checker, what, kind, why
        self.lines: list[str] = []
        self.faults = 0
        self.tags: list[tuple[int, int]] = []           # (room, cabinet), -1 for none
        self.rooms: list[int] = []
        self.sizes: list[tuple[float, float, int]] = []

    def add(self, line: str, match: re.Match[str] | None, room: int | None) -> None:
        self.lines.append(line)
        self.faults += 1
        named = match.groupdict() if match is not None else {}
        at = -1 if room is None else room
        if named.get("c") and (at, int(named["c"])) not in self.tags:
            self.tags.append((at, int(named["c"])))
        elif not named.get("c") and room is not None and room not in self.rooms:
            self.rooms.append(room)
        if named.get("w") and named.get("l"):
            self.sizes.append((float(named["w"]), float(named["l"]), int(named.get("q") or 1)))

    def item(self) -> Item:
        cabinets = {room for room, _ in self.tags}
        tags = tuple(f"R{room}C{cab}" if room >= 0 else f"C{cab}" for room, cab in sorted(self.tags))
        tags += tuple(f"R{room}" for room in sorted(self.rooms) if room not in cabinets)
        return Item(0, self.kind, self.checker, self.what, tags, tuple(self.sizes),
                    count=self.faults,
                    why=self.why, fingerprint=_fingerprint(self.checker, self.what, self.lines),
                    settles=self.kind in (FIX, MOZAIK) and self.what not in NEVER_SETTLED)


def _item(kind: str, checker: str, what: str, *, why: str = "") -> Item:
    return Item(0, kind, checker, what, why=why, fingerprint=_fingerprint(checker, what, [why]))


def _numbered(item: Item, n: int) -> Item:
    return Item(n, item.kind, item.checker, item.what, item.tags, item.sizes, item.count, item.why,
                item.fingerprint, item.settles)


def _room_of(line: str, spec: Any, room: int | None) -> int | None:
    """The room a checker's output is in now: its room line, when it has one."""
    if spec is not None and spec.room is not None and re.fullmatch(spec.room, line):
        found = _ROOM.search(line)
        return int(found[1]) if found else room
    return room


def _is_fault(spec: Any, line: str) -> bool:
    return spec is not None and any(re.fullmatch(pattern, line) for pattern in spec.fault)


def _fingerprint(checker: str, what: str, lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join((checker, what, *sorted(lines))).encode("utf-8")).hexdigest()


def _number(name: str) -> int:
    found = _ROOM.search(name)
    return int(found[1]) if found else 0


# ----- stored as the run's result: codes, tags, numbers and fingerprints only


def to_json(found: Findings) -> dict[str, Any]:
    return {
        "code": found.code,
        "items": [{"n": i.n, "kind": i.kind, "checker": i.checker, "what": i.what, "tags": list(i.tags),
                   "sizes": [list(size) for size in i.sizes], "count": i.count, "why": i.why,
                   "fingerprint": i.fingerprint, "settles": i.settles} for i in found.items],
        "verdicts": [{"checker": v.checker, "code": v.code, "advisory": v.advisory, "why": v.why}
                     for v in found.verdicts],
        "left_out": list(found.left_out),
        "noise": [{"checker": n.checker, "what": n.what, "count": n.count} for n in found.noise],
    }


def from_json(data: Mapping[str, Any]) -> Findings:
    """A stored result; one stored before known noise was kept has none."""
    return Findings(
        code=data["code"],
        items=tuple(Item(i["n"], i["kind"], i["checker"], i["what"], tuple(i["tags"]),
                         tuple((float(w), float(length), int(q)) for w, length, q in i["sizes"]),
                         i["count"], i["why"], i["fingerprint"], i["settles"]) for i in data["items"]),
        verdicts=tuple(Verdict(v["checker"], v["code"], v["advisory"], v["why"]) for v in data["verdicts"]),
        left_out=tuple(data["left_out"]),
        noise=tuple(Noise(n["checker"], n["what"], int(n["count"])) for n in data.get("noise", ())),
    )
