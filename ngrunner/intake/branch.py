"""
Closet or garage: which branch a job is built as (GLOSSARY: Branch), for P1a's folder name only.

Why: the pipeline files a garage job as <Job>_Gar and a closet job as <Job>, so the IC summary
must say which it is before it is filed (design 3.5, Branch: "P1a: garage words in design
names"; P3 brings the full classifier). Code decides, from one closed list of garage words read
in each page-1 design's name and Studio category, and never guesses.

Jim's BUILD.md Step 0 settles the mixed case: a mixed summary is one job, never split, and never
gets _Gar. So only a summary whose every design is a garage design gets _Gar, whatever closet-side
room word the row carries, and closets, or closets and garage mixed, are a closet with no card.

Studio's categories are a fixed list (Garage, Cabinet, Closet, His and Her Master, Hall/Storage,
Office, Pantry...). Archived jobs file their Garage-category designs as _Gar, while Cabinet and
Hall/Storage designs are closets, bars and linen rooms as often as not, so only a garage word
makes a garage design. Unclear, which raises the closet/garage card, means one of two things: the
row or the Opportunity Name says garage and no design carries a garage word, or a design can't be
classified (a blank category and no garage word in its name). Only that job waits.

Branch.reason reaches that card, and Jim's phone gets job names and dates only (decision 10), so
a reason never quotes a design's name.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from ngrunner.intake.summary_check import SummaryCheck

# _typed_variant is the row's variant as match_fab_row reads it (MatchResult.normalized.variant),
# so the _Gar flag is read one way only. match.py has no public name for it yet.
from ngrunner.match import _typed_variant, room_words, slugify

BranchKind = Literal["closet", "garage", "unclear"]
# One design as page 1 lists it: (name, category), or check_summary's record of it (a mapping
# with "name" and "category", as in SummaryCheck.details["designs"]).
Design = tuple[str, str] | Mapping[str, Any]

# The garage words. Closed on purpose: no fuzzy matching, no stems.
GARAGE_WORDS = frozenset({"garage", "garages"})
_GARAGE_ROOM = "garage"   # the one spelling match.room_words gives "garage" and "garages"
_GAR_FLAG = "gar"         # the pipeline's own garage flag in a row's variant: "_Gar"


@dataclass(frozen=True)
class Branch:
    """kind: "garage", "closet" or "unclear"; reason: one plain sentence saying why, which never
    quotes a design's name."""

    kind: BranchKind
    reason: str


def branch_of(designs: SummaryCheck | Iterable[Design], row_text: str | None = None) -> Branch:
    """
    Closet or garage, from page 1's designs, checked against what a Fab row says.

    designs   the governing render's SummaryCheck (it must have passed), its details["designs"],
              or (name, category) pairs. Each design needs its category: SummaryCheck.designs
              alone (names only) is refused with TypeError.
    row_text  the Fab row's job text, or the Opportunity Name (the pipeline asks about both).

    garage    every design's name or category carries a garage word (GARAGE_WORDS, whole words,
              any case), whatever closet-side room word the row carries;
    closet    none does, or the summary mixes closet and garage designs: one job, no card;
    unclear   the row says garage (a room word or the _Gar flag, read after its first word, so a
              surname is never one) and no design carries a garage word; or a design has a blank
              category and no garage word in its name; or page 1 lists no designs; or the check
              did not pass.
    """
    if isinstance(designs, SummaryCheck):
        if not designs.ok:
            return Branch("unclear", "The IC summary has not passed its check, so closet or garage"
                                     " could not be verified.")
        designs = designs.details.get("designs", [])
    listed = [_design(entry) for entry in designs]
    if not listed:
        return Branch("unclear", "Page 1 lists no designs, so closet or garage could not be"
                                 " verified.")
    garage = sum(_says_garage(name) or _says_garage(category) for name, category in listed)
    blank = sum(not category.strip() and not _says_garage(name) for name, category in listed)
    if blank:
        return Branch("unclear", f"Page 1 lists {_count(blank, 'design')} with no category and no"
                                 " garage word in the name.")
    if not garage and _row_says_garage(row_text or ""):
        return Branch("unclear", "The Fab row or job name says garage, but no design on page 1 is a"
                                 " garage design.")
    if garage == len(listed):
        return Branch("garage", _all_garage(garage))
    if garage:
        return Branch("closet", f"Page 1 lists {_count(garage, 'garage design')} and"
                                f" {_count(len(listed) - garage, 'other design')}: a mixed summary"
                                " is one job.")
    return Branch("closet", _no_garage(len(listed)))


def _all_garage(count: int) -> str:
    if count == 1:
        return "The only design on page 1 is a garage design."
    return f"All {count} designs on page 1 are garage designs."


def _no_garage(count: int) -> str:
    if count == 1:
        return "The only design on page 1 is not a garage design."
    return f"None of the {count} designs on page 1 is a garage design."


def _design(entry: Design) -> tuple[str, str]:
    """(name, category) of one design entry; TypeError when either is missing."""
    name = category = None
    if isinstance(entry, Mapping):
        name, category = entry.get("name"), entry.get("category")
    elif isinstance(entry, (tuple, list)) and len(entry) == 2:
        name, category = entry
    if not (isinstance(name, str) and isinstance(category, str)):
        raise TypeError("each design needs its name and its category: a (name, category) pair, or"
                        " check_summary's details['designs'] (SummaryCheck.designs holds names"
                        " only)")
    return name, category


def _count(n: int, thing: str) -> str:
    return f"{n} {thing}{'s' * (n != 1)}"


def _row_says_garage(text: str) -> bool:
    """A garage room word or the _Gar flag, as match reads a row: never its first word."""
    return (_GARAGE_ROOM in room_words(text)
            or _GAR_FLAG in (_typed_variant(text) or "").split("+"))


def _words(text: str) -> list[str]:
    """The words of a text, folded: runs of letters, so digits and punctuation split them."""
    return re.findall(r"[a-z]+", slugify(text))


def _says_garage(text: str) -> bool:
    return any(word in GARAGE_WORDS for word in _words(text))
