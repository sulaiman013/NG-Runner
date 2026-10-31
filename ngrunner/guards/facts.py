"""
What the later parts of P1b will tell the nest guard, as plain frozen facts.

Why these exist now: decide() (guards/nest_state.py) must be written and proven before any .opt or
.des can be read on this PC (docs/p1b/build-spec.md, section 1), so these are the least the .opt
reader (5.11), the toolkit's checks (5.13) and the nest-to-design match have to say, and they will
grow fields when those parts are built. The one thing they add to the spec's lists is what each pass
looked at: a pass is bound to the .opt's hash, the programs' hashes and the rooms' hashes (5.3), so
each check says what it ran on, and decide compares that with what is on disk now.

Nothing here reads a file, and every code is compared exactly, so anything unexpected is not a pass.
"""
from __future__ import annotations

from dataclasses import dataclass

# What both of Jim's checks are called, and what each may say (5.13).
SHEET_SIZE = "sheet_size"
BR_CAM_NEST = "br_cam_nest"
PASSED = "passed"
FAILED = "failed"
COULD_NOT_VERIFY = "could_not_verify"

# The one code that means the nest matches the saved design. guards/labels.py's fallback says
# "all_found" at best, and that is not this: it can't see a missing part (5.9).
DESIGN_MATCHES = "matches"


@dataclass(frozen=True)
class CheckedFiles:
    """The files a check ran on. manifest holds (program name, sha256) and room_hashes (room file,
    sha256); each is kept sorted, so two lists of the same files compare equal in any order."""

    opt_sha256: str
    manifest: tuple[tuple[str, str], ...]
    room_hashes: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest", tuple(sorted(self.manifest)))
        object.__setattr__(self, "room_hashes", tuple(sorted(self.room_hashes)))


@dataclass(frozen=True)
class OptSheet:
    """One <OptimizeSheet>, as RULES and BUILD describe it. UNVERIFIED until a real .opt is read."""

    width_mm: float
    length_mm: float
    quan: int
    flipped: bool


@dataclass(frozen=True)
class OptFacts:
    """What a nest's .opt says (guards/opt.py). sha256 is the file's own. Parts with no sheets means
    not nested. placed: parts with a location on a sheet. runs: the programs it names, each once.
    holes: how many <OperationHole>, which nothing judges yet. encoding: as sniffed. version: Mozaik's,
    from the line before the XML, "" with none."""

    sha256: str
    parts: int
    sheets: tuple[OptSheet, ...] = ()
    encoding: str = ""
    placed: int = 0
    runs: tuple[str, ...] = ()
    holes: int = 0
    version: str = ""

    @property
    def physical_sheets(self) -> int:
        """RULES 716-721: the sum of Quan over the blocks not flipped. A flip is the same board."""
        return sum(sheet.quan for sheet in self.sheets if not sheet.flipped)


@dataclass(frozen=True)
class ToolRun:
    """How one run of a script went (toolkit.run_tool). exit_code is None when it never finished, so it
    can't read as a clean 0."""

    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool


@dataclass(frozen=True)
class ToolVerdict:
    """What one of Jim's checks said. code: PASSED, FAILED or COULD_NOT_VERIFY. line: its fixed
    words, for a person. checked: the files it ran on, without which a pass counts for nothing."""

    tool: str
    code: str
    line: str = ""
    checked: CheckedFiles | None = None


@dataclass(frozen=True)
class DesignMatch:
    """Whether the nest is the saved design. code: DESIGN_MATCHES or anything else, which is no
    match. checked: the files it compared, without which a match counts for nothing."""

    code: str
    checked: CheckedFiles | None = None
