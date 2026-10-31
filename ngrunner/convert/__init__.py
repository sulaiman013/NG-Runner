"""
The shared converter: an IC summary to its board parts and their takeoff (docs/p2/build-spec.md).

Why it exists: Jim's chat types every job's parts into a per-job script by hand, and no script of
his reads the summary through to a takeoff. summary_lines reads the summary's rows, parts judges
each row against Jim's rules (rules.py, ported from his takeoff.py and RULES section 2), and
takeoff sums the board parts by colour and thickness. A row the rules don't place is Unplaced with
its reason, and reads "could not verify": never a guess.

convert() is the whole path for one summary. Its caller says which branch the job is, from P1a's
check of the same render (takeoff_branch): only a closet is converted, and a garage or mixed job
stays in chat until BUILD's garage rule is ported (ruling R4).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ngrunner.convert import rules
from ngrunner.convert.parts import BoardPart, Unplaced, board_part, settle_colours
from ngrunner.convert.summary_lines import (SummaryLine, design_lines, estimate_total, pulled_lines,
                                            read_pages)
from ngrunner.convert.takeoff import Branch, Takeoff, takeoff
from ngrunner.intake.branch import Design, branch_of
from ngrunner.intake.summary_check import SummaryCheck

__all__ = ["Takeoff", "convert", "takeoff_branch"]

_NOT_A_CLOSET: dict[Branch, str] = {
    "garage": "a garage job: it stays in chat until BUILD's garage rule is ported (ruling R4)",
    "mixed": "a closet and garage job: it stays in chat until BUILD's garage rule is ported"
             " (ruling R4)",
    "unclear": "closet or garage could not be verified, so no row is placed",
}


def convert(data: bytes, *, branch: Branch) -> Takeoff:
    """The takeoff of one IC summary's bytes. Raises SummaryUnreadable when the summary can't be
    read as measured. branch is required: takeoff_branch() of the same render."""
    pages = read_pages(data)
    lines = pulled_lines(pages)
    designs = design_lines(pages)
    judged = [(line, found) for line in lines if (found := board_part(line)) is not None]
    if branch == "closet":
        items: Sequence[BoardPart | Unplaced] = settle_colours(judged, designs)
        sheet = rules.job_sheet_size([p for p in items if isinstance(p, BoardPart)])
    else:
        items = tuple(Unplaced(line, _NOT_A_CLOSET[branch]) for line, _ in judged)
        sheet = rules.sheet_size
    return takeoff(items, sheet_size=sheet, branch=branch, estimate_total=estimate_total(pages),
                   gate_pulled_equals_designs=_gate(lines, designs))


def _gate(pulled: Sequence[SummaryLine], designs: Mapping[str, Sequence[SummaryLine]]) -> bool:
    """BUILD 65's gate, on board rows: the PULLED list holds as many as the design lists."""
    def count(lines: Iterable[SummaryLine]) -> int:
        return sum(line.qty for line in lines if board_part(line) is not None)
    return count(pulled) == sum(count(lines) for lines in designs.values())


def takeoff_branch(designs: SummaryCheck | Iterable[Design]) -> Branch:
    """Closet, garage, mixed or unclear, from P1a's branch_of. It reads a mixed summary as one
    closet job (BUILD step 0), which the takeoff can't: so a closet with any garage design is
    "mixed" here."""
    if isinstance(designs, SummaryCheck):
        if not designs.ok:
            return "unclear"
        designs = designs.details.get("designs", [])
    listed = list(designs)
    whole = branch_of(listed).kind
    if whole != "closet":
        return whole
    return "mixed" if any(branch_of([one]).kind == "garage" for one in listed) else "closet"
