"""
The sheet-size check on programs: is anything cut past the size this material's folder says?

Why it exists: a program past its sheet is the one wrong-sheet fault a program can prove on its own
(docs/p1b/build-spec.md, sections 3.1 and 5.2). A program inside the size proves nothing, since a
small one fits any sheet. So "nothing_past" never means "on the right sheet": it reads "nothing past
49x97".

AGENTS.md rule 4 says Jim's rules come from his toolkit, so this table is a stand-in until
material_lookup is adopted. It holds only the lines of RULES' sheet-size tables (Q3's default), and
tests/test_docs_p1b.py fails when those words change. Any other folder name has no rule: real Egger
colours sit on both sizes, so BUILD's "any other Egger colour 61x109" can't be used. Nor is a size
past the rule always a fault, since Jim adds sheets by hand and mixed sizes are legal (BUILD 576):
clearing one program is the state's job, not this one's.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from ngrunner.guards.tap import Tap

OVER_IN = 0.01      # the allowance past a size: a real flip pass reaches 48.94 x 97.0
_MEASURE_DP = 6     # keeps the allowance clear of a subtraction's float noise

SizeCode = Literal["nothing_past", "past_size", "no_rule", "could_not_read"]


@dataclass(frozen=True)
class SheetLine:
    """One material in RULES' tables: how its folder's name starts, its size, and RULES' words for
    it (the row's first column, and the material in backticks), which the docs test holds to
    RULES."""

    prefix: str
    size: tuple[float, float]
    rules_colour: str
    rules_material: str


_SMALL, _BIG = (49.0, 97.0), (61.0, 109.0)
_BLACK = 'Egger Black U999 (IC "Charcoal")'
RULES_LINES: tuple[SheetLine, ...] = (
    SheetLine("3-4 Egger Classic White", _SMALL, "Egger Classic White", "3-4 Egger"),
    SheetLine("1 in Egger Classic White", _SMALL, "Egger Classic White", "1 in Egger"),
    SheetLine("3-4 Egger Black U999", _BIG, _BLACK, "Egger 3-4"),
    SheetLine("1 in Egger Black U999", _SMALL, _BLACK, "Egger 1 in"),
    SheetLine("3-4 Tafisa", _SMALL, "Tafisa (any colour)", "3-4 Tafisa"),
    SheetLine("3-4 Arauco", _SMALL, "Arauco", "3-4 Arauco"),
    SheetLine("3-4 Am Lam Wh", _SMALL, "AMWH", "3-4 Am Lam Wh"),
    SheetLine("1 in Am Lam Wh", _SMALL, "AMWH", "1 in Am Lam Wh"),
    SheetLine("1-4 Egger Classic White", _BIG, "Egger Classic White", "1-4 Egger"),
    SheetLine("1-4 Tafisa", _SMALL, "any Tafisa colour", "1-4 Tafisa"),
    # A folder name can't hold RULES' slash. No real folder has this name yet.
    SheetLine("White 1-4 Vinyl", _SMALL, "AMWH / white, not Egger", "White 1/4 Vinyl"),
)


@dataclass(frozen=True)
class SizeFinding:
    """What the check found. programs: those past the size, or those it couldn't read. limit: the
    size judged against, None with no rule. could_not_read is a program not in inches or not whole,
    and past nothing so far, where "nothing past" would be a guess."""

    code: SizeCode
    programs: tuple[str, ...]
    limit: tuple[float, float] | None = field(default=None, compare=False)

    @property
    def reads(self) -> str:
        """The finding in plain words, none of which says the sheet is right."""
        if self.code == "no_rule":
            return "could not verify: no sheet size is known for this folder"
        if self.code == "could_not_read":
            return "could not verify: a program could not be read"
        word = "nothing past" if self.code == "nothing_past" else "past"
        return f"{word} {_size_words(self.limit)}"


def expected_size(material_folder: str) -> tuple[float, float] | None:
    """The size RULES gives a material's folder, or None when none of its lines covers that name."""
    name = material_folder.casefold()
    for line in RULES_LINES:
        if name.startswith(line.prefix.casefold()):
            return line.size
    return None


def size_finding(material_folder: str, taps: Sequence[Tap]) -> SizeFinding:
    """Judge one material's programs against its size. A program's reach only grows as it is
    written, so one already past is past however much of it is here. Units it can't trust are never
    compared."""
    limit = expected_size(material_folder)
    if limit is None:
        return SizeFinding("no_rule", ())
    past = tuple(tap.name for tap in taps if tap.inches and _is_past(tap.reach, limit))
    if past:
        return SizeFinding("past_size", past, limit)
    unread = tuple(tap.name for tap in taps if not (tap.inches and tap.complete))
    if unread:
        return SizeFinding("could_not_read", unread, limit)
    return SizeFinding("nothing_past", (), limit)


def _is_past(reach: tuple[float, float] | None, limit: tuple[float, float]) -> bool:
    if reach is None:
        return False
    return (round(reach[0] - limit[0], _MEASURE_DP) > OVER_IN
            or round(reach[1] - limit[1], _MEASURE_DP) > OVER_IN)


def _size_words(size: tuple[float, float] | None) -> str:
    return "x".join(f"{side:g}" for side in size or ())
