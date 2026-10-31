"""
The order heads-up: what a job's takeoff says to order, for Barbara and Will.

Why it exists: they order sheets and tape from the chat's IC Conversion JSON, and a wrong or missing
line costs a reorder (docs/p1b/build-spec.md, sections 3.4 and 5.8; rulings 12a, 12b and 12c). It is
a takeoff estimate, so every message says the nest decides and names its basis.

A client's details can't travel. These files hold an address, a client's name and contact notes,
and read_takeoff picks named fields instead of passing a block along. The two texts it spells back
(a material and a tape colour) are refused if they read like an address, or hold a client field of
the same file, and one refusal costs the whole takeoff. ic_color is free text that real files use
for a site's or a room's name, so it is never echoed: the swap line comes from a fixed table, and
ic_color stays out of Takeoff's repr too.

Only the list form of buckets with edgeband.rows reads, whatever schema_version says, since 14 of
the 34 files saying 2.0 lack the blocks. Anything else is None, never a partial list: an order from
half a list is worse than the "could not verify" that sends someone to look.

The lint refuses an underscore and a bare 1mm, so a material's underscore reads as a space and tape
loses its gauge. Apart from that, colours read as the JSON spells them until material_lookup is
adopted.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from typing import Any, NamedTuple

from ngrunner.guards.tracker import plain_material, split_tape_name
from ngrunner.messages import Message
from ngrunner.messages.templates import DevLine

UNREADABLE_NOTE = "could not verify the takeoff"        # the board's note on a None
ESTIMATE = "takeoff estimate; the nest decides"         # the design requires these words
KIND = "heads_up"       # what templates.FIXED knows it by
_CLIENT_FIELDS = frozenset({"address", "client", "contact_notes", "site", "email"})


class SheetLine(NamedTuple):
    material: str
    thk_in: float
    sheets_with_waste: float
    calc_sheets_raw: float


class TapeLine(NamedTuple):
    colour: str
    width: str
    feet: float


@dataclass(frozen=True)
class Takeoff:
    """What a heads-up may say. waste_percent is the basis of sheets_with_waste."""

    sheets: tuple[SheetLine, ...]
    tape: tuple[TapeLine, ...]
    total: float | None
    ic_color: str = field(repr=False)
    waste_percent: int


# ----- reading


def read_takeoff(data: bytes) -> Takeoff | None:
    """The named blocks of a conversion JSON, or None when any is missing or not as measured."""
    try:
        parsed = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(parsed, dict):
        return None
    waste = _waste_percent(parsed.get("waste_factor"))
    if waste is None:
        return None
    own = _client_values(parsed)
    sheets = _sheet_lines(parsed.get("buckets"), own)
    tape = _tape_lines(parsed.get("edgeband"), own)
    if sheets is None or tape is None:
        return None
    color = parsed.get("ic_color")
    return Takeoff(sheets, tape, _total(parsed.get("estimate")),
                   color if isinstance(color, str) else "", waste)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _waste_percent(value: Any) -> int | None:
    """1.1 as 10. A basis that can't be read can't be named, and a heads-up must name it."""
    factor = _number(value)
    if factor is None or not 1.0 <= factor <= 2.0:
        return None
    return round((factor - 1.0) * 100)


_TOTAL_TEXT = re.compile(r"\$?\s*(?P<amount>[0-9][0-9,]*(?:\.[0-9]+)?)")


def _total(estimate: Any) -> float | None:
    """estimate.total is a number or text like $7,999.05. If neither, the job just has no total
    flag: the sheets and tape still read."""
    total = estimate.get("total") if isinstance(estimate, dict) else None
    if isinstance(total, str):
        found = _TOTAL_TEXT.fullmatch(total.strip())
        return float(found["amount"].replace(",", "")) if found else None
    return _number(total)


def _sheet_lines(buckets: Any, own: Sequence[str]) -> tuple[SheetLine, ...] | None:
    if not isinstance(buckets, list) or not buckets:
        return None
    lines = [_sheet_line(bucket, own) for bucket in buckets]
    return None if None in lines else tuple(lines)


def _sheet_line(bucket: Any, own: Sequence[str]) -> SheetLine | None:
    if not isinstance(bucket, dict):
        return None
    material = bucket.get("material")
    thick = _number(bucket.get("thk_in"))
    with_waste = _number(bucket.get("sheets_with_waste"))
    raw = _number(bucket.get("calc_sheets_raw"))
    if (not _may_spell(material, own) or thick is None or _thickness(thick) is None
            or with_waste is None or with_waste <= 0 or raw is None or raw < 0):
        return None
    return SheetLine(material, thick, with_waste, raw)


def _tape_lines(edgeband: Any, own: Sequence[str]) -> tuple[TapeLine, ...] | None:
    rows = edgeband.get("rows") if isinstance(edgeband, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    lines = [_tape_line(row, own) for row in rows]
    return None if None in lines else tuple(lines)


def _tape_line(row: Any, own: Sequence[str]) -> TapeLine | None:
    if not isinstance(row, dict) or not isinstance(row.get("material"), str):
        return None
    name = split_tape_name(row["material"])
    feet = _number(row.get("linear_feet"))
    if name is None or feet is None or feet < 0 or not _may_spell(name[0], own):
        return None
    return TapeLine(name[0], name[1], feet)


# ----- may this text be spelled back to a person?


_MATERIAL = re.compile(r"[A-Za-z][A-Za-z0-9 _()/.&'’-]{0,79}")
_NUMBER_THEN_WORD = re.compile(r"(?<![\w/(])[0-9]+\s+(?!in\b)[A-Za-z]")     # 12 Fake, not (1 in)
_LONG_NUMBER = re.compile(r"(?<![A-Za-z])[0-9]{4,}")                        # a ZIP; not M2007
_STREET = re.compile(
    r"\b(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Ct|Court|Way|Pl|Place|Cir"
    r"|Circle|Ter|Terrace|Pkwy|Parkway|Hwy|Highway|Apt|Suite|Ste|Unit|Lot|Box|Residence|House)\b",
    re.IGNORECASE,
)


def _may_spell(text: Any, own: Sequence[str]) -> bool:
    """A material's shape, and none of this file's client values in it. A real material that trips
    this reads "could not verify", which is the safe way to be wrong."""
    if not isinstance(text, str) or not _MATERIAL.fullmatch(text):
        return False
    if _NUMBER_THEN_WORD.search(text) or _LONG_NUMBER.search(text) or _STREET.search(text):
        return False
    plain = plain_material(text)
    return not any(re.search(rf"(?<!\w){re.escape(value)}(?!\w)", plain, re.IGNORECASE)
                   for value in own)


def _client_values(parsed: Any) -> list[str]:
    """Every client field's text in the file, at any depth, whole and by its comma parts: 4
    characters or more, since a shorter part (CA) says nothing about a person."""
    found = _client_texts(parsed)
    parts = {plain_material(part) for text in found for part in (text, *text.split(","))}
    return sorted(part for part in parts if len(part) >= 4)


def _client_texts(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [text for key, item in value.items()
                for text in (_texts(item) if key in _CLIENT_FIELDS else _client_texts(item))]
    if isinstance(value, list):
        return [text for item in value for text in _client_texts(item)]
    return []


def _texts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _texts(item)]
    if isinstance(value, list):
        return [text for item in value for text in _texts(item)]
    return []


# ----- the flags: lines, never alarms


# Ruling 12b: a colour swap named once, in fixed words, never from ic_color's own text. A file's
# ic_color can hold a site's or a room's name, so this is matched and never quoted.
_COLOUR_SWAPS = (
    ("Arctic White", "Egger Classic White"),                # on 13 of 20 real jobs
    ("Charcoal", "Egger Black U999 ST19 Woodgrain"),        # ruling Q4
)
_CLASSIC_WHITE = "classic white"


def order_flags(t: Takeoff, *, total_line: float) -> tuple[str, ...]:
    """What goes on a heads-up beside the lists: any colour but Classic White (12a), a colour swap
    (12b), and a total at or above the line (12c). A size swap isn't flagged in P1b, because the
    takeoff has no field for it and its free text isn't read."""
    flags: list[str] = []
    others = dict.fromkeys(plain_material(line.material) for line in t.sheets
                           if _CLASSIC_WHITE not in line.material.casefold())
    if others:
        flags.append(f"Not all Classic White: {', '.join(others)}")
    flags += _swaps(t)
    if t.total is not None and t.total >= total_line:
        flags.append(f"Estimate total ${t.total:,.2f} is at or above ${total_line:,.0f}")
    return tuple(flags)


def _swaps(t: Takeoff) -> list[str]:
    said = t.ic_color.casefold()
    built = [line.material.casefold() for line in t.sheets]
    return [f"IC {ic_name}, built as {material}" for ic_name, material in _COLOUR_SWAPS
            if re.search(rf"(?<!\w){re.escape(ic_name.casefold())}(?!\w)", said)
            and any(name.startswith(material.casefold()) for name in built)]


# ----- the message


def heads_up(job: str, t: Takeoff, flags: Sequence[str], *, revised: bool) -> Message:
    """The heads-up for job. revised: this content replaces one already sent."""
    title = f"{job}: {'revised ' if revised else ''}order heads-up ({ESTIMATE})"
    opening = f"{'Revised t' if revised else 'T'}{ESTIMATE[1:]}."
    first = f"{opening} Counted as sheets with {t.waste_percent}% waste."
    items = [f"{job}: {', '.join(_sheet_words(line) for line in t.sheets)} ({ESTIMATE})."]
    items += [f"{job}: {_count(feet, 'foot', 'feet')} of {width} tape, {plain_material(colour)}"
              f" ({ESTIMATE})." for (colour, width), feet in _tape_totals(t.tape).items()]
    items += [f"{job}: {_ended(flag)}" for flag in flags]
    numbered = tuple(f"{number}. {item}" for number, item in enumerate(items, start=1))
    return Message(KIND, title, (first, *numbered))


_SAYS_ITS_THICKNESS = re.compile(r"\([0-9][0-9/ ]*(?:in)?\)|(?<![\w/])[0-9]+/[0-9]+(?![\w/])")


def _sheet_words(line: SheetLine) -> str:
    name = plain_material(line.material)
    size = "" if _SAYS_ITS_THICKNESS.search(name) else f"{_thickness(line.thk_in)} "
    return f"{_count(line.sheets_with_waste, 'sheet', 'sheets')} {size}{name}"


def _thickness(inches: float) -> str | None:
    """3/4, 1/4, 1\" or 1-1/8\": no mark on a plain fraction, as Jim's lines read. None when it
    isn't a fraction a sheet comes in."""
    if inches <= 0:
        return None
    fraction = Fraction(inches).limit_denominator(16)
    if abs(float(fraction) - inches) > 1e-6:
        return None
    whole, rest = divmod(fraction, 1)
    if not rest:
        return f'{whole}"'
    return f'{whole}-{rest}"' if whole else str(rest)


def _tape_totals(tape: Sequence[TapeLine]) -> dict[tuple[str, str], float]:
    """Feet per colour and width, the way tape is ordered. A colour with no feet gets no line."""
    totals: dict[tuple[str, str], float] = {}
    for line in tape:
        key = (line.colour, line.width)
        totals[key] = totals.get(key, 0.0) + line.feet
    return {key: feet for key, feet in totals.items() if feet > 0}


def _count(n: float, one: str, many: str) -> str:
    return f"{_plain_number(n)} {one if n == 1 else many}"


def _plain_number(n: float) -> str:
    """As the JSON has it, to a tenth, with no trailing .0: 240, 283.1."""
    return f"{n:.1f}".rstrip("0").rstrip(".")


def _ended(text: str) -> str:
    return text if text.endswith((".", "!", "?")) else f"{text}."


def unreadable_dev(job: str, *, at: datetime) -> DevLine:
    """The one DEV item a None raises. The board shows UNREADABLE_NOTE."""
    return DevLine(at=at, text="The takeoff could not be read, so no order heads-up went out",
                   job=job, code="takeoff_unreadable")
