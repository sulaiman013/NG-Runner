"""
The nest guard's verdict on one material of one job (docs/p1b/build-spec.md, section 5.3). This is
the safety core, so it is one pure function: no store, no clock, no files. Evidence in, verdict out.

Why it is a table, first match wins: a nest guard that says the wrong thing costs a sheet of wood or
a false alarm that teaches Jim to ignore it. So every row is one plain test, in a fixed order, and
what isn't known is never a pass:

1. A material seen at install and unchanged reads NOT_CHECKED and alarms nobody: without this, 65
   old jobs would go red at once.
2. No program and no .opt: NOT_POSTED.
3. A new program with near pairs: DO_NOT_CUT. Nothing can clear this.
4. A new program past its size that Jim hasn't cleared: DO_NOT_CUT. A clearance names one program's
   hash, so a re-post asks again, and it clears only this row (docs/decisions.md, Q3a).
5. A check that says it failed, or a nest that is not the saved design (the design's "Not verified",
   guards/design.py): DO_NOT_CUT, as the design has it red, to George and in the way of Finish lock.
   Rows 3, 4 and 5 give one state, and a verdict lists every fault of the three, doubled holes first,
   so Jim re-nests once and not once per fault. Clearing a size only takes that fault off the list.
6. A nest with parts and no sheets: SAVE_NEST.
7. A nest, both checks passing, the design matching, every program it names in the folder, and all of
   it on the files on disk right now: NEST_CHECKED. With no toolkit nothing reaches this, however
   good the nest looks, since only Jim's own checks can pass one.
8. Anything else: NOT_CHECKED, saying what was read and that the nest was not, and what stopped it:
   a nest that can't be read, or a program it names that isn't in the folder.

A clean read of the programs can never say more than row 8 does: a program check can raise DO NOT
CUT, and only Jim's own checks can say "Nest checked". No verdict means it may be cut, and the lint
refuses any line that says so. Nothing read from a .opt raises DO NOT CUT by itself, and a doubt about
reading a file must cost a NOT CHECKED, never a false alarm. The one way a nest's contents can raise one
is the design match, which reads both the nest and the saved design cleanly and finds them different;
one it couldn't make is could_not_verify, a NOT CHECKED with its note.

The words are in messages.templates, since a DO NOT CUT says the same on the board and in its
message. Which programs a row found are in doubled_programs() and past_size_programs(), for the card
a verdict opens.

Two facts can look alike and must never be taken for each other. design is the real match, the nest
against the saved design's cutlist (facts.DesignMatch, guards/design.py), which runs only from an
adopted toolkit copy. labels is
the fallback (guards.labels.LabelsMatch), which can only add "could not verify": decide reads nothing
else from it, so no reading of it can reach NEST_CHECKED, whichever field it lands in.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from ngrunner.guards import sheets
from ngrunner.guards.facts import (
    BR_CAM_NEST, COULD_NOT_VERIFY, DESIGN_MATCHES, FAILED, NOT_VERIFIED, PASSED, SHEET_SIZE,
    CheckedFiles, DesignMatch, OptFacts, ToolVerdict,
)
from ngrunner.guards.opt import reconcile
from ngrunner.guards.tap import Tap
from ngrunner.messages import templates as words

if TYPE_CHECKING:
    from ngrunner.guards.labels import LabelsMatch

BASIS_PROGRAMS = "programs"
BASIS_NEST = "nest"
_BOTH_TOOLS = frozenset({SHEET_SIZE, BR_CAM_NEST})
# The sheets real programs fit, smallest first (build spec 3.1): how a program's reach is put into
# words. A rule's size comes from guards/sheets.py, never from here.
_KNOWN_SHEETS = ((49.0, 97.0), (61.0, 109.0), (49.0, 121.0))
_LABELS_DID_NOT_FIT = ("some_missing", "could_not_read")     # guards.labels' codes
_A_CHECK_FAILED = "A check of the nest failed"
_NOT_THE_DESIGN = "Not verified: the nest is not the saved design"      # as guards/design.py says it


class NestState(StrEnum):
    NOT_POSTED = auto()
    NOT_CHECKED = auto()
    SAVE_NEST = auto()
    DO_NOT_CUT = auto()
    NEST_CHECKED = auto()


@dataclass(frozen=True)
class MaterialEvidence:
    """Everything known about one material, as facts already read.

    material: its folder's name, as guards/sheets.py takes it.
    taps: every program in it now.
    seen_at_install: all of them were there at the first scan, unchanged.
    opt, tools, design: the nest, Jim's checks and the design match. None or empty: not known.
    labels: what the labels fallback found, which can only add a "could not verify" note.
    opt_problem: why a .opt that is there couldn't be read, in plain words. opt is None then.
    cleared: the sha256 of each program whose size Jim cleared on this job and material
        (store.cleared_programs).
    before_install: (name, sha256) of each program in taps that was there at the first scan and
        hasn't changed. It raises no alarm even with a new program beside it, since doubled holes
        can't be cleared and a re-cut mustn't turn an old program red for good. Its name counts
        too: the same bytes posted under a new name are a new post.
    room_hashes: the room files on disk now, as (name, sha256): what a pass must have run on.
    read_at: when the programs were read, for the notes; decide has no clock to ask.
    """

    material: str
    taps: tuple[Tap, ...]
    seen_at_install: bool
    opt: OptFacts | None = None
    tools: tuple[ToolVerdict, ...] = ()
    design: DesignMatch | None = None
    cleared: frozenset[str] = frozenset()
    before_install: frozenset[tuple[str, str]] = frozenset()
    room_hashes: tuple[tuple[str, str], ...] = ()
    read_at: datetime | None = None
    labels: LabelsMatch | None = None
    opt_problem: str | None = None


@dataclass(frozen=True)
class Verdict:
    """state: the row that matched. reasons: its notes, in plain words, each one line, none for
    NOT_POSTED. basis: what it rests on, "programs" or "nest". trial: its lines say "(trial)".
    Always True from decide, which has no store to count real nests in; once 5 have had a verdict
    the caller replaces it (build spec 4, "Trial labels")."""

    state: NestState
    reasons: tuple[str, ...]
    basis: str
    trial: bool


def decide(ev: MaterialEvidence) -> Verdict:
    """The verdict on one material: the first row of the table that matches (module docstring)."""
    if ev.seen_at_install:
        return _verdict(NestState.NOT_CHECKED, (words.POSTED_BEFORE,))
    if not ev.taps and ev.opt is None and ev.opt_problem is None:
        return _verdict(NestState.NOT_POSTED, ())
    faults = _faults(ev)
    if faults:
        in_programs = doubled_programs(ev) or past_size_programs(ev)
        return _verdict(NestState.DO_NOT_CUT, faults, BASIS_PROGRAMS if in_programs else BASIS_NEST)
    if ev.opt is not None and ev.opt.parts > 0 and not ev.opt.sheets:
        return _verdict(NestState.SAVE_NEST, (words.SAVE_THE_NEST,), BASIS_NEST)
    if _nest_is_checked(ev):
        note = words.nest_checked_note(ev.read_at, [tap.name for tap in ev.taps])
        return _verdict(NestState.NEST_CHECKED, (note,), BASIS_NEST)
    return _verdict(NestState.NOT_CHECKED, _not_checked_notes(ev))


def _faults(ev: MaterialEvidence) -> tuple[str, ...]:
    """Every reason for a DO NOT CUT (rows 3, 4 and 5), each fault in its own words."""
    notes: list[str] = []
    doubled = doubled_programs(ev)
    if doubled:
        holes = sum(len(tap.near_pairs) for tap in doubled)
        notes.append(words.doubled_holes(holes, [tap.name for tap in doubled]))
    past = past_size_programs(ev)
    if past:
        notes += _past_size_notes(ev.material, past)
    notes += [tool.line.strip() or _A_CHECK_FAILED for tool in ev.tools if tool.code == FAILED]
    if ev.design is not None and ev.design.code == NOT_VERIFIED:
        notes.append(ev.design.note.strip() or _NOT_THE_DESIGN)
    return tuple(notes)


def doubled_programs(ev: MaterialEvidence) -> tuple[Tap, ...]:
    """The new programs that hold near pairs: doubled cam holes. No clearance touches this."""
    if ev.seen_at_install:
        return ()
    return tuple(tap for tap in _judged(ev) if tap.near_pairs)


def past_size_programs(ev: MaterialEvidence) -> tuple[Tap, ...]:
    """The new programs past this material's size and not cleared: what its one sheet_size card
    asks about. A clearance counts only for the hash it names, so a re-post is back here."""
    if ev.seen_at_install:
        return ()
    judged = _judged(ev)
    past = _past_size_names(ev.material, judged)
    return tuple(tap for tap in judged if tap.name in past and tap.sha256 not in ev.cleared)


def sheet_of(tap: Tap) -> str | None:
    """The smallest sheet tap's cutting moves fit, as "61x109". None when it has no reach or runs
    past every sheet known. Reach is a lower bound: this says how far a program goes, and never that
    it is on the right sheet."""
    if tap.reach is None:
        return None
    x, y = tap.reach
    for width, length in _KNOWN_SHEETS:
        if x <= width + sheets.OVER_IN and y <= length + sheets.OVER_IN:
            return _sheet_words((width, length))
    return None


def expected_sheet(material: str) -> str | None:
    """The sheet this material's rule expects, as "49x97"; None when no rule covers it."""
    size = sheets.expected_size(material)
    return None if size is None else _sheet_words(size)


# ----- the evidence


def _new(ev: MaterialEvidence) -> tuple[Tap, ...]:
    return tuple(tap for tap in ev.taps if (tap.name, tap.sha256) not in ev.before_install)


def _judged(ev: MaterialEvidence) -> tuple[Tap, ...]:
    """The programs a verdict may rest on: new, whole and in inches. One half-written or not in
    inches is neither a fault nor a pass, only something not known yet."""
    return tuple(tap for tap in _new(ev) if _can_be_read(tap))


def _can_be_read(tap: Tap) -> bool:
    return tap.complete and tap.inches


def _past_size_names(material: str, taps: tuple[Tap, ...]) -> frozenset[str]:
    finding = sheets.size_finding(material, taps)
    return frozenset(finding.programs) if finding.code == "past_size" else frozenset()


def _nest_is_checked(ev: MaterialEvidence) -> bool:
    """Row 7, all of it. Each pass must have run on the very files on disk now, so a change to the
    nest, a program or a room takes it back to NOT_CHECKED. A check that doesn't say what it ran on
    counts for nothing, and neither does anything short of both tools and a real design match: the
    labels fallback is not the design's match. A program the nest names must be in the folder."""
    if ev.opt is None or ev.opt.parts <= 0 or ev.opt.physical_sheets <= 0 or not ev.opt.runs:
        return False
    if reconcile(ev.opt, ev.taps).missing:
        return False
    if not ev.taps or not ev.room_hashes or not all(_can_be_read(tap) for tap in ev.taps):
        return False
    if not _BOTH_TOOLS <= {tool.tool for tool in ev.tools}:
        return False
    if ev.design is None or ev.design.code != DESIGN_MATCHES:
        return False
    now = CheckedFiles(ev.opt.sha256, tuple((tap.name, tap.sha256) for tap in ev.taps),
                       ev.room_hashes)
    return (all(tool.code == PASSED and tool.checked == now for tool in ev.tools)
            and ev.design.checked == now)


# ----- the notes


def _verdict(state: NestState, reasons: tuple[str, ...], basis: str = BASIS_PROGRAMS) -> Verdict:
    return Verdict(state=state, reasons=reasons, basis=basis, trial=True)


def _past_size_notes(material: str, past: tuple[Tap, ...]) -> tuple[str, ...]:
    """One note per sheet the programs read as, smallest first and any past them all last."""
    expected = expected_sheet(material) or "not known"
    known = [_sheet_words(size) for size in _KNOWN_SHEETS]
    on_each = [sheet_of(tap) for tap in past]
    return tuple(words.past_its_size(on_each.count(sheet), sheet, expected)
                 for sheet in (*known, None) if sheet in on_each)


def _not_checked_notes(ev: MaterialEvidence) -> tuple[str, ...]:
    """Row 8: what was read, what stopped the nest being read or fitting its programs, then anything
    a check or the labels could not verify."""
    notes = [_what_was_read(ev)]
    if ev.opt_problem is not None:
        notes.append(ev.opt_problem)
    if ev.opt is not None and ev.taps and (missing := reconcile(ev.opt, ev.taps).missing):
        notes.append(words.nest_names_missing(missing))     # none at all: not posted yet, row 8 says
    notes += [tool.line.strip() for tool in ev.tools
              if tool.code == COULD_NOT_VERIFY and tool.line.strip()]
    if ev.design is not None and ev.design.code == COULD_NOT_VERIFY and ev.design.note.strip():
        notes.append(ev.design.note.strip())
    if ev.labels is not None and ev.labels.code in _LABELS_DID_NOT_FIT:
        notes.append(ev.labels.note)
    return tuple(notes)


def _what_was_read(ev: MaterialEvidence) -> str:
    new, judged = _new(ev), _judged(ev)
    unread = [tap.name for tap in new if not _can_be_read(tap)]
    if unread:
        return words.programs_unread_note(unread)
    if not judged:
        return words.POSTED_BEFORE if ev.taps else words.NO_PROGRAMS_YET
    cleared = _past_size_names(ev.material, judged)   # row 4 didn't match, so Jim cleared them all
    merged = sum(1 for tap in judged if tap.overlays)
    return words.programs_read_note(at=ev.read_at, sheet=expected_sheet(ev.material),
                                    cleared=len(cleared), merged=merged)


def _sheet_words(size: tuple[float, float]) -> str:
    return f"{size[0]:g}x{size[1]:g}"
