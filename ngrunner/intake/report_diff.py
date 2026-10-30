"""What differs between two renders of one job's IC summary, in words Jim can act on.

Why it exists: Studio renders a report from the live catalog, so a report can read differently with
no redesign at all. Measured 2026-10-30 on real jobs: 3 of 4 of Jim's copies from 10-18 differed from
a fresh fetch only in rod part numbers and names, with identical drawings, while the 4th was a real
redesign. The fingerprint can only say "differs", and a catalog update can raise that on every filed
job holding the part, so a card has to say which kind of difference this is or Jim must open two PDFs
for each one.

It describes and never decides. Whichever way it reads, nothing is filed until Jim chooses, and when
the words can't be sure it says only what it counted. Reading order and pictures come from
summary_check, so this sees exactly what the fingerprint saw.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ngrunner.intake.summary_check import Source, read_contents

_PART_NUMBER = re.compile(r"\d{9}", re.ASCII)   # a part row opens with its nine-digit number
_NAME_AFTER = 2                                 # number, quantity, then the part's name
_EXAMPLE_LIMIT = 60                             # a longer name is not worth a card's line


@dataclass(frozen=True)
class ReportChange:
    """lines: text lines that changed, the larger of removed and added. only_parts: every changed
    line is a part row's number or name, so quantities and sizes held. drawings_same: the same
    pictures in the same order. renamed: distinct (old name, new name) pairs, in report order, when
    only_parts."""

    lines: int
    drawings_same: bool
    only_parts: bool
    renamed: tuple[tuple[str, str], ...]

    @property
    def any(self) -> bool:
        return bool(self.lines) or not self.drawings_same

    def words(self) -> str:
        """One plain sentence or two for a card; "" when nothing differs."""
        if not self.any:
            return ""
        if self.drawings_same and self.only_parts:
            return "Only part numbers or names changed, which points to a catalog update, not a redesign" \
                   + _examples(self.renamed) + ". The drawings and sizes are the same."
        if self.drawings_same:
            return f"{_lines(self.lines)} of text changed. The drawings are the same."
        if self.lines:
            return f"The drawings changed, and {_lines(self.lines)} of text."
        return "The drawings changed. The text is the same."


def _lines(count: int) -> str:
    return "1 line" if count == 1 else f"{count} lines"


def _examples(renamed: Sequence[tuple[str, str]]) -> str:
    shown = [pair for pair in renamed if max(map(len, pair)) <= _EXAMPLE_LIMIT][:1]
    if not shown:
        return ""
    old, new = shown[0]
    rest = len(renamed) - 1
    more = "" if not rest else f", and {rest} more" if rest > 1 else ", and 1 more"
    return f": {old} is now {new}{more}"


# ----- comparing


def describe_change(old: Source, new: Source) -> ReportChange:
    """old and new as summary_check reads them: a path or the PDF's bytes. Raises ValueError when
    either can't be read, as content_fingerprint does."""
    return compare(read_contents(old), read_contents(new))


def compare(old: Sequence[tuple[Sequence[str], Sequence[str]]],
            new: Sequence[tuple[Sequence[str], Sequence[str]]]) -> ReportChange:
    """Each report as its pages, each page (lines in reading order, picture digests in drawn order)."""
    old_lines, new_lines = _flat(old, 0), _flat(new, 0)
    gone, came = Counter(old_lines) - Counter(new_lines), Counter(new_lines) - Counter(old_lines)
    numbers_and_names = _part_lines(old_lines) | _part_lines(new_lines)
    changed = (gone + came).keys()
    only_parts = bool(changed) and sum(gone.values()) == sum(came.values()) \
        and all(line in numbers_and_names for line in changed)
    return ReportChange(
        lines=max(sum(gone.values()), sum(came.values())),
        drawings_same=_flat(old, 1) == _flat(new, 1),
        only_parts=only_parts,
        renamed=_renamed(old_lines, new_lines, gone, came) if only_parts else (),
    )


def _flat(pages: Iterable[tuple[Sequence[str], Sequence[str]]], part: int) -> list[str]:
    return [item for page in pages for item in page[part]]


def _part_lines(lines: Sequence[str]) -> set[str]:
    """Every part row's number and name. A number alone proves nothing else about the lines near it,
    so a name is only taken from where a part row keeps it."""
    found: set[str] = set()
    for at, line in enumerate(lines):
        if _PART_NUMBER.fullmatch(line):
            found.add(line)
            if at + _NAME_AFTER < len(lines):
                found.add(lines[at + _NAME_AFTER])
    return found


def _renamed(old_lines: Sequence[str], new_lines: Sequence[str], gone: Counter[str],
             came: Counter[str]) -> tuple[tuple[str, str], ...]:
    """The old and new names side by side: the changed names in report order, paired by position."""
    old_names = _changed_names(old_lines, gone)
    new_names = _changed_names(new_lines, came)
    pairs = zip(old_names, new_names)
    return tuple(dict.fromkeys(pairs))


def _changed_names(lines: Sequence[str], changed: Counter[str]) -> list[str]:
    return [line for line in lines if changed[line] and not _PART_NUMBER.fullmatch(line)]
