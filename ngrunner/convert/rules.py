"""
Jim's takeoff rules, in one place: what the converter may count, how thick it is built, its tape
and its sheet.

Why it exists: AGENTS.md rule 4 says Jim's rules come from his toolkit. His takeoff.py holds them
for a conversion JSON the chat typed; nothing of his reads a summary through to a takeoff
(docs/p2/build-spec.md, sections 1 and 4). So this module mirrors his constants and tape rules in
NG Runner's own words, and tests/test_convert_ties.py reads his takeoff.py and material_lookup.py
through `ast` (never by import) and fails when any value mirrored here moves: that is how his edits
reach this code. His source is never copied here, since the repo is public.

What takeoff.py leaves out comes from RULES section 2 and BUILD step 2, each line cited where it is
used, and from the answers read from Jim's own files on 2026-10-31 (docs/decisions.md, "Answered from
Jim's own files"). Two departures from takeoff.py are deliberate (ruling R4): a bucket with no sheet
size in RULES' table has no sheet count, never 49x97 by default; and a part the rules don't name (a
kind, a thickness, a colour) is Unplaced by parts.py instead of counted with no tape.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable
from fractions import Fraction
from typing import Literal, Protocol

# ----- mirrored from takeoff.py (tests/test_convert_ties.py holds each to his file)

WASTE = 1.10                                       # takeoff.WASTE
TAKEOFF_PY_FALLBACK_SHEET = (49.0, 97.0)           # takeoff.SHEET_SQFT's sheet: never used (R4)
DISREGARD = re.compile(r"\b(scribe|quarter\s*round)\b", re.IGNORECASE)   # takeoff.DISREGARD
# takeoff.TYPE's kinds, in its order.
TYPE_KINDS = ("TR Rail Cover", "TR Base", "FF Cleat", "VT", "SH", "DR", "DW", "CK", "BP", "WS",
              "DK", "TR", "FF")
# normalise()'s renames: (kind found, a word the description must hold or "", the kind it becomes).
# Custom is no kind of TYPE's: normalise() takes a description's first word when TYPE finds none.
RENAMES = (("TR", "Rail Cover", "TR Rail Cover"), ("TR", "Base", "TR Base"), ("FF", "", "FF Cleat"),
           ("Custom", "Vert", "VT"))
WALL_MOUNT = re.compile(r"\b(FN|RN|LN)\b")         # takeoff.is_wm
BACK_PANEL_UNDER = 0.5                             # normalise(): a BP under this is built ...
BACK_PANEL_BUILT = 0.25                            # ... at 1/4
RAIL_COVER_BUILT = 0.75                            # takeoff(): a TR Rail Cover buckets at 3/4

TapeRule = Literal["none", "long_edge", "both_long_edges", "all_four", "vertical"]
# takeoff.eb_lf, kind by kind. IC's second dimension is the banded one (RULES 173).
TAPE: dict[str, TapeRule] = {
    "CK": "none", "FF Cleat": "none", "BP": "none",
    "TR Rail Cover": "both_long_edges",
    "TR Base": "long_edge", "SH": "long_edge", "DK": "long_edge",
    "DW": "all_four", "DR": "all_four", "WS": "all_four",
    "VT": "vertical",
}


def tape_ft(kind: str, a_in: float, b_in: float, wall_mount: bool) -> float:
    """takeoff.eb_lf for one part, in feet. A kind with no rule raises: parts.py never lets one
    reach here (an HP, or a TR that is neither Base nor Rail Cover, is Unplaced)."""
    rule = TAPE[kind]
    if rule == "none":
        return 0.0
    if rule == "both_long_edges":
        return 2 * b_in / 12.0
    if rule == "long_edge":
        return b_in / 12.0
    if rule == "all_four":
        return 2 * (a_in + b_in) / 12.0
    return ((b_in + 2 * a_in) if wall_mount else b_in) / 12.0


# ----- RULES section 2 and BUILD step 2: what takeoff.py doesn't say

# The kinds counted as board (BUILD 77, BUDGET 30). HP is on BUDGET's board list but has no tape
# rule anywhere, so parts.py names it Unplaced.
BOARD_KINDS = frozenset({*TAPE, "HP"})
# An item code's kind (the code before its colour, SH-WH; classify.py's BOARD_TYPES).
ITEM_BOARD_CODES = frozenset({"VT", "SH", "DK", "DR", "DW", "CK", "FF", "WS", "TR", "BP", "HP"})
# Lines that are not cut from a sheet, though they print a size: drawer boxes and bottoms, glass
# inserts and aluminium-frame doors (BUILD 79; classify.py's drawer-hardware and glass rules), and a
# glass shelf, SHG ... CLR, which RULES 243 lists among the genuine buyouts (glass): bought in, not cut.
NOT_BOARD_CODES = frozenset({"DB", "DF", "DD", "INS", "DRAF", "DWAF", "SHG"})
NOT_BOARD_WORDS = re.compile(r"^(Wrap|Bottom|Basket)\b")
GLASS_DOOR_CODE = "SH-G"                           # a DR with this code is a glass door (BUILD 79)
# The rail cover printed as hardware: White Rail Cover, item 400000570 (RULES 224). It must start
# the description, so a kit or a cap that names a rail cover is no board.
RAIL_COVER_LINE = re.compile(r"^(?:[A-Z][a-z]+ )?Rail Cover\b")
RAIL_COVER_FACE = (3.5, 96.0)                      # RULES 224: 3.5 x 96 = 2.333 SqFt
CLOSET_WS_PRINTED = (0.75, 1.0, 1.125)             # RULES 221: a closet WS printed so ...
CLOSET_WS_BUILT = 0.75                             # ... is built 3/4 (its colour: parts.settle_colours)
QUARTER, THREE_QUARTER, ONE_INCH = 0.25, 0.75, 1.0
WHITE = "WH"                                       # RULES 217: 1in+ exists only in white
# A white shelf printed 1-1/8 is built 1in, AMWH: RULES 217 (1in+ exists only in white) and 214
# ("White with something 1in+ -> AMWH"). A shelf printed 1-1/8 in a colour is still Jim's.
WHITE_SHELF_PRINTED, WHITE_SHELF_BUILT = 1.125, ONE_INCH

# IC colour codes that no material in Jim's library carries, and that have no price: RULES 146 says a
# colour that can't be built is said so and named, never substituted. Navy (IC MS): his library has
# Navy textures, but no panel stock carries one (his own notes on the job that printed it, where the
# chat built Ore instead). What Navy is built in is Jim's to say; until then the line is never placed.
NO_MATERIAL: dict[str, str] = {"MS": "Navy"}

# Description codes that are not a colour (SCHEMA's list, and those read on the 52 real pairs).
# SH among them is a door's or drawer face's style (Shaker), in SCHEMA's Style field.
FLAG_CODES = frozenset({"FD", "RH", "LH", "FN", "NN", "RN", "LN", "GV", "HV", "FL", "ET", "HP", "SC",
                        "XD", "GH", "ND", "TO", "TB", "CR", "EE", "GD", "SH"})

# IC colour code -> the brand row of RULES' tables. Mirrored from material_lookup.ALIASES (its
# code and brand columns; tests/test_convert_ties.py holds them to his file). Arctic White is
# Egger Classic White there, unless a white part is built 1in or thicker: then AMWH (job_brand).
Brand = Literal["Egger Classic White", "Egger Black U999", "Tafisa", "Arauco", "AMWH"]
COLOUR_BRANDS: dict[str, Brand] = {
    "SU": "Tafisa", "WF": "Tafisa", "RR": "Tafisa", "SW": "Tafisa", "LG": "Tafisa",
    "DA": "Tafisa", "WG": "Tafisa", "AP": "Tafisa", "MJ": "Tafisa", "MO": "Tafisa",
    "ML": "Tafisa", "CC": "Egger Black U999", "WH": "Egger Classic White", "IV": "Arauco",
}

_49x97, _61x109 = (49.0, 97.0), (61.0, 109.0)
# RULES section 2's sheet tables, by brand row and built thickness (RULES 120-126, 131-135).
# A pair not here has no size: Tafisa and Arauco at 1in print "—", Egger Black at 1/4 isn't listed.
RULES_SHEETS: dict[tuple[Brand, float], tuple[float, float]] = {
    ("Egger Classic White", THREE_QUARTER): _49x97,     # RULES 122
    ("Egger Classic White", ONE_INCH): _49x97,          # RULES 122
    ("Egger Classic White", QUARTER): _61x109,          # RULES 133: 1-4 Egger, 5x9
    ("Egger Black U999", THREE_QUARTER): _61x109,       # RULES 123
    ("Egger Black U999", ONE_INCH): _49x97,             # RULES 123
    ("Tafisa", THREE_QUARTER): _49x97,                  # RULES 124
    ("Tafisa", QUARTER): _49x97,                        # RULES 134: 1-4 Tafisa in that colour
    ("Arauco", THREE_QUARTER): _49x97,                  # RULES 125
    ("AMWH", THREE_QUARTER): _49x97,                    # RULES 126
    ("AMWH", ONE_INCH): _49x97,                         # RULES 126
    ("AMWH", QUARTER): _49x97,                          # RULES 135: White 1/4 Vinyl
}

SheetSize = Callable[[str, float], tuple[float, float] | None]


def sheet_size(colour: str, thk_in: float) -> tuple[float, float] | None:
    """The sheet RULES' tables give an IC colour code at a built thickness, in inches, or None.
    White is read as Egger Classic White; job_sheet_size reads it as AMWH where a job makes it so."""
    brand = COLOUR_BRANDS.get(colour)
    return None if brand is None else RULES_SHEETS.get((brand, thk_in))


class _Built(Protocol):
    colour: str
    built_thk: float


def thick_white(parts: Iterable[_Built]) -> bool:
    """Whether a job holds a white part built 1in or thicker, which makes all its white AMWH."""
    return any(p.colour == WHITE and p.built_thk >= ONE_INCH for p in parts)


def job_brand(colour: str, *, amwh: bool) -> Brand | None:
    """The brand row an IC colour code takes on one job. amwh: the job holds a white part built 1in
    or thicker (thick_white). Then every white part of the job is AMWH, its 1/4 backs included:
    RULES 214 ("White with something 1in+ -> AMWH"), and the RULES of 2026-10-06, which states it of
    the whole job ("one thick white part pushes the whole job to AMWH", about white sheet stock).
    Jim's yes on the whole job is awaited (docs/decisions.md, 2026-10-31)."""
    if amwh and colour == WHITE:
        return "AMWH"
    return COLOUR_BRANDS.get(colour)


def job_sheet_size(parts: Iterable[_Built]) -> SheetSize:
    """sheet_size for one job. On a job with a white part built 1in or thicker, white is AMWH
    (job_brand), so its 1/4 white backs go on White 1/4 Vinyl at 49x97 (RULES 135), not 1-4 Egger."""
    amwh = thick_white(parts)

    def size(colour: str, thk_in: float) -> tuple[float, float] | None:
        brand = job_brand(colour, amwh=amwh)
        return None if brand is None else RULES_SHEETS.get((brand, thk_in))

    return size


def sheets_with_waste(sqft: float, sheet_in: tuple[float, float]) -> int:
    """RULES 141 and takeoff.py: ceil(area x 1.10 / sheet), once per bucket, on the area rounded
    to 2 places as takeoff.py rounds it first."""
    return math.ceil(round(sqft, 2) * WASTE / (sheet_in[0] * sheet_in[1] / 144.0))


def calc_sheets_32(sqft: float) -> float:
    """Area / 32: the 4x8 basis of the chat's calc_sheets_raw and the tracker's Calc Sheets (R6)."""
    return round(sqft / 32.0, 4)


# ----- thickness words

_THICKNESS = re.compile(r"(?P<whole>\d+)-(?P<frac>\d+/\d+)|(?P<simple>\d+/\d+|\d+(?:\.\d+)?)")


def thickness(text: str) -> float | None:
    """IC's printed thickness in inches: 3/4, 3/8, 1/4, 1, 1-1/8, 0.4, 0.6. None if not one."""
    found = _THICKNESS.fullmatch(text)
    if found is None:
        return None
    if found["whole"]:
        return float(int(found["whole"]) + Fraction(found["frac"]))
    return float(Fraction(found["simple"]))


# ----- the rules' fingerprint (a takeoff row records which rules made it)


def fingerprint() -> str:
    """sha256 of every table above, so a stored takeoff names the rules version that made it."""
    tables = {
        "waste": WASTE, "disregard": DISREGARD.pattern, "kinds": TYPE_KINDS, "renames": RENAMES,
        "wall_mount": WALL_MOUNT.pattern, "back_panel": [BACK_PANEL_UNDER, BACK_PANEL_BUILT],
        "rail_cover": [RAIL_COVER_BUILT, *RAIL_COVER_FACE], "tape": TAPE,
        "board": sorted(BOARD_KINDS), "not_board": sorted(NOT_BOARD_CODES),
        "flags": sorted(FLAG_CODES), "brands": COLOUR_BRANDS,
        "sheets": [[*key, *size] for key, size in sorted(RULES_SHEETS.items())],
        "ws": [*CLOSET_WS_PRINTED, CLOSET_WS_BUILT], "items": sorted(ITEM_BOARD_CODES),
        # 2026-10-31, from Jim's own files: a worksurface in its own printed colour, a white 1-1/8
        # shelf at 1in, a thick white part making the whole job's white AMWH, and Navy unplaced.
        "ws_colour": "printed", "white_shelf": [WHITE_SHELF_PRINTED, WHITE_SHELF_BUILT],
        "thick_white": "whole job AMWH", "no_material": NO_MATERIAL,
    }
    return hashlib.sha256(json.dumps(tables, sort_keys=True).encode("utf-8")).hexdigest()
