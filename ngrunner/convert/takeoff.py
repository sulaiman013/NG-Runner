"""
The takeoff: board parts summed by colour and built thickness, with sheets, the 4x8 basis and tape.

Why it exists: Barbara and Will order sheets and tape from a job's takeoff, which today comes from
the chat's hand-typed parts (docs/p2/build-spec.md, sections 5.3 and 9). This is takeoff.py's sum
on parts read from the summary itself, with three differences, each a ruling:

  - A bucket's sheet comes from RULES section 2's table by its colour and thickness (the caller's
    sheet_size). With none, its sheet count is None and it reads "could not verify": takeoff.py's
    fallback to 49x97 is a guess, and a wrong sheet is a wrong order (R4, R5).
  - Both sheet numbers are carried, named: sheets_with_waste (RULES 141) and calc_sheets_32, the
    area / 32 that the tracker's Calc Sheets holds (R6).
  - A garage, mixed or unclear job takes no board part at all: every row is Unplaced, since
    BUILD's garage buckets aren't ported yet (R4). The caller says which branch the job is.

A takeoff with an Unplaced row or a bucket with no sheet is not complete: its numbers are true for
what they cover, and the could_not_verify lines say what they leave out. It keeps codes and numbers
only, so its repr, and anything made from it, can't carry a client's details.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ngrunner.convert import rules
from ngrunner.convert.parts import BoardPart, Unplaced

Branch = Literal["closet", "garage", "mixed", "unclear"]
BRANCHES: tuple[Branch, ...] = ("closet", "garage", "mixed", "unclear")
_THICKNESS_WORDS = {0.25: "1/4", 0.375: "3/8", 0.75: "3/4", 1.0: "1in"}


@dataclass(frozen=True)
class Bucket:
    """One colour at one built thickness, project-wide. sqft is rounded as takeoff.py rounds it;
    sheet_in and sheets_with_waste are None when RULES' table gives no sheet."""

    colour: str
    thk_in: float
    parts: int
    sqft: float
    sheet_in: tuple[float, float] | None
    sheets_with_waste: int | None
    calc_sheets_32: float
    tape_lf: float


@dataclass(frozen=True)
class Takeoff:
    """A job's takeoff. board_parts and tape_lf count the placed parts only; unplaced holds the
    rows that couldn't be placed, their text left out."""

    buckets: tuple[Bucket, ...]
    board_parts: int
    tape_lf: float
    estimate_total: float | None
    gate_pulled_equals_designs: bool
    unplaced: tuple[Unplaced, ...]
    branch: Branch

    @property
    def complete(self) -> bool:
        """Every row placed, every bucket sized, and the job a closet."""
        return (self.branch == "closet" and not self.unplaced
                and all(b.sheets_with_waste is not None for b in self.buckets))

    @property
    def could_not_verify(self) -> tuple[str, ...]:
        """One plain line for each thing the takeoff leaves out."""
        lines = [u.named() for u in self.unplaced]
        lines += [f"No sheet size for {_thickness(b.thk_in)} {b.colour}: could not verify its"
                  " sheets." for b in self.buckets if b.sheets_with_waste is None]
        return tuple(lines)


def takeoff(parts: Sequence[BoardPart | Unplaced], *, sheet_size: rules.SheetSize, branch: Branch,
            estimate_total: float | None = None,
            gate_pulled_equals_designs: bool = False) -> Takeoff:
    """
    Sums the parts. sheet_size gives a colour code's sheet at a built thickness, in inches, or
    None (rules.job_sheet_size for a job). branch is required, so no caller can skip the garage
    ruling: a job that isn't a closet must come with every row Unplaced, and a part on one
    raises ValueError, as does a part whose colour was never settled (parts.settle_colours).
    """
    if branch not in BRANCHES:
        raise ValueError(f"unknown branch {branch!r}")
    boards = [p for p in parts if isinstance(p, BoardPart)]
    lost = tuple(p.safe() for p in parts if isinstance(p, Unplaced))
    if branch != "closet" and boards:
        raise ValueError("a job that isn't a closet takes no board part (ruling R4)")
    if any(not p.colour for p in boards):
        raise ValueError("a part with no colour: settle_colours gives it its room's colour")
    grouped: dict[tuple[str, float], list[BoardPart]] = defaultdict(list)
    for p in boards:
        grouped[(p.colour, p.built_thk)].append(p)
    buckets = tuple(_bucket(colour, thk, grouped[(colour, thk)], sheet_size)
                    for colour, thk in sorted(grouped))
    return Takeoff(buckets, sum(p.qty for p in boards), round(sum(p.tape_ft for p in boards), 1),
                   estimate_total, gate_pulled_equals_designs, lost, branch)


def _bucket(colour: str, thk: float, parts: list[BoardPart], sheet_size: rules.SheetSize) -> Bucket:
    area = sum(p.sqft for p in parts)
    sheet = sheet_size(colour, thk)
    return Bucket(colour, thk, sum(p.qty for p in parts), round(area, 2), sheet,
                  rules.sheets_with_waste(area, sheet) if sheet else None,
                  rules.calc_sheets_32(area), round(sum(p.tape_ft for p in parts), 1))


def _thickness(inches: float) -> str:
    return _THICKNESS_WORDS.get(inches, f"{inches:g}in")
