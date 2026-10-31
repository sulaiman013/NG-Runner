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
5. A check that says it failed: DO_NOT_CUT.
6. A nest with parts and no sheets: SAVE_NEST.
7. A nest, both checks passing, the design matching, and all of it on the files on disk right now:
   NEST_CHECKED. On today's data nothing reaches this, since no .opt can be read yet.
8. Anything else: NOT_CHECKED, saying what was read and that the nest was not.

A clean read of the programs can never say more than row 8 does: a program check can raise DO NOT
CUT, and only Jim's own checks can say "Nest checked". No verdict means OK to cut.

The words are in messages.templates, since a DO NOT CUT says the same on the board and in its
message. Which programs a row found are in doubled_programs() and past_size_programs(), for the
cards a verdict opens: first match gives one row's notes, and a material with both faults still has
a size to ask Jim about.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, auto

from ngrunner.guards import sheets
from ngrunner.guards.facts import (
    BR_CAM_NEST, COULD_NOT_VERIFY, DESIGN_MATCHES, FAILED, PASSED, SHEET_SIZE, CheckedFiles,
    DesignMatch, OptFacts, ToolVerdict,
)
from ngrunner.guards.tap import Tap
from ngrunner.messages import templates as words

BASIS_PROGRAMS = "programs"
BASIS_NEST = "nest"
_BOTH_TOOLS = frozenset({SHEET_SIZE, BR_CAM_NEST})
# The sheets real programs fit, smallest first (build spec 3.1): how a program's reach is put into
# words. A rule's size comes from guards/sheets.py, never from here.
_KNOWN_SHEETS = ((49.0, 97.0), (61.0, 109.0), (49.0, 121.0))
_LABELS_DID_NOT_FIT = ("some_missing", "could_not_read")
_A_CHECK_FAILED = "A check of the nest failed"


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
    if not ev.taps and ev.opt is None:
        return _verdict(NestState.NOT_POSTED, ())
    doubled = doubled_programs(ev)
    if doubled:
        holes = sum(len(tap.near_pairs) for tap in doubled)
        note = words.doubled_holes(holes, [tap.name for tap in doubled])
        return _verdict(NestState.DO_NOT_CUT, (note,))
    past = past_size_programs(ev)
    if past:
        return _verdict(NestState.DO_NOT_CUT, _past_size_notes(ev.material, past))
    failed = [tool for tool in ev.tools if tool.code == FAILED]
    if failed:
        return _verdict(NestState.DO_NOT_CUT,
                        tuple(tool.line.strip() or _A_CHECK_FAILED for tool in failed), BASIS_NEST)
    if ev.opt is not None and ev.opt.parts > 0 and not ev.opt.sheets:
        return _verdict(NestState.SAVE_NEST, (words.SAVE_THE_NEST,), BASIS_NEST)
    if _nest_is_checked(ev):
        note = words.nest_checked_note(ev.read_at, [tap.name for tap in ev.taps])
        return _verdict(NestState.NEST_CHECKED, (note,), BASIS_NEST)
    return _verdict(NestState.NOT_CHECKED, _not_checked_notes(ev))


def doubled_programs(ev: MaterialEvidence) -> tuple[Tap, ...]:
    """The new programs that hold near pairs: doubled cam holes. No clearance touches this."""
    if ev.seen_at_install:
        return ()
    return tuple(tap for tap in _judged(ev) if tap.near_pairs)


def past_size_programs(ev: MaterialEvidence) -> tuple[Tap, ...]:
    """The new programs past this material's size and not cleared: one sheet_size card each. A
    clearance counts only for the hash it names, so a re-post is back here."""
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
    labels fallback is not the design's match."""
    if ev.opt is None or ev.opt.parts <= 0 or not ev.opt.sheets:
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
    """Row 8: what was read, then anything a check or the labels could not verify."""
    notes = [_what_was_read(ev)]
    notes += [tool.line.strip() for tool in ev.tools
              if tool.code == COULD_NOT_VERIFY and tool.line.strip()]
    if ev.design is not None and ev.design.code in _LABELS_DID_NOT_FIT:
        notes.append(words.LABELS_NOT_VERIFIED)
    return tuple(notes)


def _what_was_read(ev: MaterialEvidence) -> str:
    new, judged = _new(ev), _judged(ev)
    unread = [tap.name for tap in new if not _can_be_read(tap)]
    if unread:
        return words.programs_unread_note(unread)
    if not judged:
        return words.POSTED_BEFORE if ev.taps else words.NO_PROGRAMS_YET
    cleared = _past_size_names(ev.material, judged)   # row 4 didn't match, so Jim cleared them all
    merged = sum(1 for tap in judged if tap.family == "merged")
    return words.programs_read_note(at=ev.read_at, sheet=expected_sheet(ev.material),
                                    cleared=len(cleared), merged=merged)


def _sheet_words(size: tuple[float, float]) -> str:
    return f"{size[0]:g}x{size[1]:g}"
