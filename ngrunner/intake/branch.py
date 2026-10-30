"""
Closet or garage: which branch a job is built as (GLOSSARY: Branch), for P1a's folder name only.

Why: the pipeline files a garage job as <Job>_Gar and a closet job as <Job>, so the IC summary
must say which it is before it is filed (design 3.5, Branch: "P1a: garage words in design
names"; P3 brings the full classifier). Code decides, from one closed list of garage words read
in each page-1 design's name and Studio category, and never guesses: a job whose designs or Fab
row point both ways is "unclear", which raises the closet/garage card, and only that job waits.

Studio's categories are a fixed list (Garage, Cabinet, Closet, His and Her Master, Hall/Storage,
Office, Pantry...). Archived jobs file their Garage-category designs as _Gar, while Cabinet and
Hall/Storage designs are closets, bars and linen rooms as often as not, so only a garage word
makes a garage design. A job with garage and other designs was filed as a closet once, so it is
asked, never decided here.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from ngrunner.intake.summary_check import SummaryCheck
from ngrunner.match import ROOM_WORDS, slugify

BranchKind = Literal["closet", "garage", "unclear"]
# One design as page 1 lists it: (name, category), or check_summary's record of it (a mapping
# with "name" and "category", as in SummaryCheck.details["designs"]).
Design = tuple[str, str] | Mapping[str, Any]

# The garage words. Closed on purpose: no fuzzy matching, no stems.
GARAGE_WORDS = frozenset({"garage", "garages"})


@dataclass(frozen=True)
class Branch:
    """kind: "garage", "closet" or "unclear"; reason: one plain sentence saying why."""

    kind: BranchKind
    reason: str


def branch_of(designs: SummaryCheck | Iterable[Design], row_text: str | None = None) -> Branch:
    """
    Closet or garage, from page 1's designs, checked against the Fab row's room words.

    designs   the governing render's SummaryCheck (it must have passed), its details["designs"],
              or (name, category) pairs. Each design needs its category: SummaryCheck.designs
              alone (names only) is refused with TypeError.
    row_text  the Fab row's job text (or any typed job name), for its room words.

    kind is "garage" when every design's name or category carries a garage word (GARAGE_WORDS,
    whole words, any case), "closet" when none does, and "unclear" otherwise: designs of both
    kinds, no designs, a check that did not pass, or a row whose room words claim the other
    branch. The row's words are read after its first word (the surname): a garage word or the
    pipeline's _Gar flag claims garage; any other room word (match.ROOM_WORDS) claims the closet
    side, so a garage job typed "cabinets" is asked about, never guessed.
    """
    if isinstance(designs, SummaryCheck):
        if not designs.ok:
            return Branch("unclear", "The IC summary has not passed its check, so closet or garage"
                                     " could not be verified.")
        designs = designs.details.get("designs", [])
    by_designs = _by_designs([_design(entry) for entry in designs])
    claim = _row_claim(row_text or "")
    if by_designs.kind == "unclear" or claim is None or claim[0] == by_designs.kind:
        return by_designs
    side, word = claim
    if side == "garage":
        return Branch("unclear", f'The Fab row says "{word}", but no design on page 1 is a garage design.')
    return Branch("unclear", f'The Fab row says "{word}", but every design on page 1 is a garage design.')


def _design(entry: Design) -> tuple[str, str]:
    """(name, category) of one design entry; TypeError when either is missing."""
    name = category = None
    if isinstance(entry, Mapping):
        name, category = entry.get("name"), entry.get("category")
    elif isinstance(entry, (tuple, list)) and len(entry) == 2:
        name, category = entry
    if not (isinstance(name, str) and isinstance(category, str)):
        raise TypeError("each design needs its name and its category: a (name, category) pair, or"
                        " check_summary's details['designs'] (SummaryCheck.designs holds names only)")
    return name, category


def _by_designs(listed: list[tuple[str, str]]) -> Branch:
    if not listed:
        return Branch("unclear", "Page 1 lists no designs, so closet or garage could not be verified.")
    garage = [name for name, category in listed if _says_garage(name) or _says_garage(category)]
    others = len(listed) - len(garage)
    if garage and others:
        return Branch("unclear", f"Page 1 lists {_count(len(garage), 'garage design')}"
                                 f" and {_count(others, 'other design')}.")
    if len(listed) == 1:
        verdict = "a garage design" if garage else "not a garage design"
        return Branch("garage" if garage else "closet",
                      f'The only design on page 1, "{listed[0][0]}", is {verdict}.')
    if garage:
        return Branch("garage", f"All {len(listed)} designs on page 1 are garage designs.")
    return Branch("closet", f"None of the {len(listed)} designs on page 1 is a garage design.")


def _count(n: int, thing: str) -> str:
    return f"{n} {thing}{'s' * (n != 1)}"


# ----- the row's room words

# The pipeline's own garage flag (NG Jobs' "_Gar"): a typed name can carry it, a design never does.
_GAR_FLAG = "gar"
# match.ROOM_WORDS as runs of words ("walk in" and "walk-in" are walk, in), longest first.
_ROOM_RUNS = sorted({tuple(slugify(word).split("_")) for word in ROOM_WORDS}, key=len, reverse=True)
_APOSTROPHES = str.maketrans("", "", "'’")


def _row_claim(text: str) -> tuple[BranchKind, str] | None:
    """
    The branch a Fab row's room words claim, with the word that claims it, or None.

    Read as match.py reads a row: words after its first (the first is the surname, whatever it
    is), an apostrophe joining a word. A garage word or the _Gar flag claims garage, whatever
    else the row says; otherwise any other room word claims the closet side, since everything
    that is not a garage is built as a closet. A room word that suits either (cabinets, storage)
    therefore asks rather than guesses.
    """
    words = slugify(text.translate(_APOSTROPHES)).split("_")[1:]
    garage = next((w for w in words if w in GARAGE_WORDS or w == _GAR_FLAG), None)
    if garage is not None:
        return "garage", garage
    for start in range(len(words)):
        for run in _ROOM_RUNS:
            if tuple(words[start:start + len(run)]) == run:
                return "closet", " ".join(run)
    return None


def _words(text: str) -> list[str]:
    """The words of a text, folded: runs of letters, so digits and punctuation split them."""
    return re.findall(r"[a-z]+", slugify(text))


def _says_garage(text: str) -> bool:
    return any(word in GARAGE_WORDS for word in _words(text))
