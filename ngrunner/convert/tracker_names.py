"""
What NG Runner's own takeoff calls each bucket on Jim's Materials Tracker: its sheet row, its tape row and
the tape's EB Colour, by RULES' names.

Why it exists: a lock adds and sets the tracker's rows by name, and the tracker prices a row only when its
material is in the Pricing block character for character (RULES' HARD PRECONDITION: no row there and the
job silently costs $0). The chat types those names by hand from its own IC Conversion, and only 23 of the
52 real conversions name their buckets at all. NG Runner's converter knows each bucket by its IC colour code
and built thickness, so this names it the way RULES says, and lock/names.py then holds every name to the
Pricing block of the tracker's snapshot and refuses the lock, saying which name, when one isn't there.

The names, mirrored from Jim's files (tests/test_convert_tracker_names.py ties the tables to them):
- RULES section 2, "Colours are named by their TAFISA name, everywhere we write them": the IC colour word
  becomes its Tafisa name (Ore is Spring Blossom), and IC "Charcoal" and "Eggshell Ivory" keep their own
  material names (Egger Black U999 ST19 Woodgrain, Arauco Prism SF210 Antique White). The IC code to IC
  colour word is material_lookup's ALIASES, as rules.COLOUR_BRANDS mirrors its brands.
- The tracker's own forms, which no doc of Jim's writes down, read from the Pricing block of its snapshot
  (docs/decisions.md, "Tracker names from NG Runner's own takeoff"): a Tafisa sheet is "Tafisa <Tafisa
  name>_<IC word> (3/4)"; Egger Black and Arauco are "<name> (3/4)"; AMWH is "Am Lam Wh (3/4)" and
  "Am Lam Wh (1 in)", its 1/4 "White 1/4 Vinyl" (RULES 135); Egger Classic White at 3/4 is "Egger Classic
  White", the spelling the chat's rows use, and its 1/4 backs on 61x109 "Egger Classic White (1/4) 5x9"
  (RULES 133, and the tracker's "a different sheet size gets its own priced row", BUILD).
- Tape is "<material> 1mm — <width>", the Pricing block's own hint: 3/4 takes 15/16" / 1", 1 in takes
  1-1/4" / 1-5/16". Every Tafisa colour takes the one "Tafisa" tape, whose EB Colour then names the colour
  as BUILD 587-596 asks, "<Tafisa name> (<IC word>)" (BUILD's form less the product code, which NG Runner
  can't verify); Egger, Arauco and AMWH carry their own names. A 1/4 back takes no tape.

Never a guess (ruling R4): a colour RULES gives no Tafisa name (Mocha, Blue Stone), a code with no brand,
or a thickness with no form here is Unnamed, saying why, and nothing is spelled for it.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple

from ngrunner.convert import rules

# RULES section 2: the Tafisa name of each IC colour word (Jim, 2026-10-20), exactly as RULES spells it.
TAFISA_NAMES: dict[str, str] = {
    "Ore": "Spring Blossom", "Morning Mist": "White Chocolate", "New Natural": "Apres Skis",
    "Fort": "Southwester", "Interlochen": "Sunday Brunch", "Marquette": "Weekend Getaway",
    "Houghton": "Winter Fun!", "Warm Sand": "Casting at First Light", "Bed Rock": "Milky Way",
    "River Rock": "River Rock",
}
# RULES section 2: the two colour words that are not Tafisa keep their own material name.
OWN_NAMES: dict[str, str] = {"Charcoal": "Egger Black U999 ST19 Woodgrain",
                             "Eggshell Ivory": "Arauco Prism SF210 Antique White"}
# material_lookup.ALIASES: each IC code's colour word, as IC prints it.
IC_WORDS: dict[str, str] = {
    "SU": "Interlochen", "WF": "Houghton", "RR": "River Rock", "SW": "Fort", "LG": "Ore", "DA": "Morning Mist",
    "WG": "Marquette", "AP": "New Natural", "MJ": "Warm Sand", "MO": "Mocha", "ML": "Blue Stone",
    "CC": "Charcoal", "WH": "Arctic White", "IV": "Eggshell Ivory",
}
THICKNESS = {rules.THREE_QUARTER: "3/4", rules.ONE_INCH: "1 in", rules.QUARTER: "1/4"}
TAPE_WIDTHS = {rules.THREE_QUARTER: '15/16" / 1"', rules.ONE_INCH: '1-1/4" / 1-5/16"'}
TAPE_GAUGE = "1mm"
# The tracker's own forms for the two white brands, by built thickness.
AMWH_SHEETS = {rules.THREE_QUARTER: "Am Lam Wh (3/4)", rules.ONE_INCH: "Am Lam Wh (1 in)",
               rules.QUARTER: "White 1/4 Vinyl"}
EGGER_WHITE_SHEETS = {rules.THREE_QUARTER: "Egger Classic White", rules.QUARTER: "Egger Classic White (1/4) 5x9"}
_SAME = 0.01


class Named(NamedTuple):
    """A bucket's tracker names: its sheet row, its tape row ("" for a bucket with no tape) and the tape's
    EB Colour ("" with no tape)."""

    sheet: str
    tape: str
    tape_colour: str


class Unnamed(NamedTuple):
    """Why a bucket has no tracker name, in plain words."""

    why: str


def bucket_names(colour: str, thk_in: float, *, amwh: bool, tape: bool) -> Named | Unnamed:
    """The tracker names of one bucket: colour is its IC code, thk_in its built thickness, amwh whether the
    job's white is AMWH (job_amwh), tape whether the bucket has any tape."""
    thickness = next((label for inches, label in THICKNESS.items() if abs(inches - thk_in) < _SAME), None)
    brand = rules.job_brand(colour, amwh=amwh)
    word = IC_WORDS.get(colour)
    if brand is None or word is None:
        return Unnamed(f"IC colour {colour} has no brand in RULES, so it has no tracker name")
    if thickness is None:
        return Unnamed(f"RULES gives no tracker name for {word} board {thk_in:g}in thick")
    if brand == "AMWH":
        sheet, material, eb = _form(AMWH_SHEETS, thk_in), "Am Lam Wh", "Am Lam Wh"
    elif brand == "Egger Classic White":
        sheet, material, eb = _form(EGGER_WHITE_SHEETS, thk_in), "Egger Classic White", "Egger Classic White"
    elif brand == "Tafisa":
        tafisa = TAFISA_NAMES.get(word)
        if tafisa is None:
            return Unnamed(f"RULES gives IC colour {word} no Tafisa name, so it has no tracker name")
        sheet, material, eb = f"Tafisa {tafisa}_{word} ({thickness})", "Tafisa", f"{tafisa} ({word})"
    else:
        material = OWN_NAMES.get(word, "")
        if not material:
            return Unnamed(f"RULES gives IC colour {word} no material name, so it has no tracker name")
        sheet, eb = f"{material} ({thickness})", material
    if sheet is None:
        return Unnamed(f"RULES gives no tracker name for {word} board {thickness} thick")
    if not tape:
        return Named(sheet, "", "")
    width = next((w for inches, w in TAPE_WIDTHS.items() if abs(inches - thk_in) < _SAME), None)
    if width is None:
        return Unnamed(f"RULES gives no tape for {word} board {thickness} thick, and it has tape")
    return Named(sheet, f"{material} {TAPE_GAUGE} — {width}", eb)


def job_amwh(buckets: Iterable[tuple[str, float]]) -> bool:
    """Whether a job's white is AMWH: it has a white bucket built 1in or thicker (rules.thick_white)."""
    return any(colour == rules.WHITE and thk_in >= rules.ONE_INCH for colour, thk_in in buckets)


def _form(forms: dict[float, str], thk_in: float) -> str | None:
    return next((name for inches, name in forms.items() if abs(inches - thk_in) < _SAME), None)
