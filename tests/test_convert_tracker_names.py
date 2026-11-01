"""
The tracker's names for NG Runner's own takeoff (convert/tracker_names.py): each bucket by RULES' names,
the Tafisa name for a Tafisa colour, the two colours that keep their own material name, AMWH and Egger
Classic White in the tracker's own forms, and tape in the Pricing block's form. A colour RULES gives no
Tafisa name is never spelled. The tables are tied to Jim's RULES and material_lookup (read as text and by
ast, never imported, and no line of his copied); those tests skip without his toolkit.
"""
from __future__ import annotations

import ast
import re

import pytest

from ngrunner.convert import rules
from ngrunner.convert.tracker_names import (
    IC_WORDS, OWN_NAMES, TAFISA_NAMES, Named, Unnamed, bucket_names, job_amwh,
)
from test_convert_ties import DOCS, _assigned, _tree

THREE_QUARTER, ONE_INCH, QUARTER = 0.75, 1.0, 0.25
TAPE_34, TAPE_1 = '1mm — 15/16" / 1"', '1mm — 1-1/4" / 1-5/16"'


def test_a_tafisa_colour_is_named_by_its_tafisa_name_with_the_one_tafisa_tape() -> None:
    assert bucket_names("LG", THREE_QUARTER, amwh=False, tape=True) == Named(
        "Tafisa Spring Blossom_Ore (3/4)", f"Tafisa {TAPE_34}", "Spring Blossom (Ore)")
    assert bucket_names("SU", THREE_QUARTER, amwh=False, tape=True) == Named(
        "Tafisa Sunday Brunch_Interlochen (3/4)", f"Tafisa {TAPE_34}", "Sunday Brunch (Interlochen)")
    assert bucket_names("AP", QUARTER, amwh=False, tape=False) == Named("Tafisa Apres Skis_New Natural (1/4)", "", "")


def test_charcoal_and_eggshell_ivory_keep_their_own_material_names() -> None:
    assert bucket_names("CC", THREE_QUARTER, amwh=False, tape=True) == Named(
        "Egger Black U999 ST19 Woodgrain (3/4)", f"Egger Black U999 ST19 Woodgrain {TAPE_34}",
        "Egger Black U999 ST19 Woodgrain")
    assert bucket_names("IV", THREE_QUARTER, amwh=False, tape=True) == Named(
        "Arauco Prism SF210 Antique White (3/4)", f"Arauco Prism SF210 Antique White {TAPE_34}",
        "Arauco Prism SF210 Antique White")


def test_white_is_egger_classic_white_or_on_a_job_with_thick_white_amwh() -> None:
    assert bucket_names("WH", THREE_QUARTER, amwh=False, tape=True) == Named(
        "Egger Classic White", f"Egger Classic White {TAPE_34}", "Egger Classic White")
    assert bucket_names("WH", QUARTER, amwh=False, tape=False) == Named("Egger Classic White (1/4) 5x9", "", "")
    assert bucket_names("WH", THREE_QUARTER, amwh=True, tape=True) == Named(
        "Am Lam Wh (3/4)", f"Am Lam Wh {TAPE_34}", "Am Lam Wh")
    assert bucket_names("WH", ONE_INCH, amwh=True, tape=True) == Named(
        "Am Lam Wh (1 in)", f"Am Lam Wh {TAPE_1}", "Am Lam Wh")
    assert bucket_names("WH", QUARTER, amwh=True, tape=False) == Named("White 1/4 Vinyl", "", "")
    assert job_amwh([("WH", 0.75), ("WH", 1.0)]) and not job_amwh([("WH", 0.75), ("LG", 1.0)])


def test_what_rules_doesnt_name_is_never_spelled() -> None:
    for colour in ("MO", "ML"):                     # Mocha and Blue Stone: no Tafisa name in RULES
        found = bucket_names(colour, THREE_QUARTER, amwh=False, tape=True)
        assert isinstance(found, Unnamed) and "no Tafisa name" in found.why, colour
    assert isinstance(bucket_names("ZZ", THREE_QUARTER, amwh=False, tape=True), Unnamed)
    assert isinstance(bucket_names("WH", 0.375, amwh=False, tape=False), Unnamed)
    assert isinstance(bucket_names("WH", ONE_INCH, amwh=False, tape=True), Unnamed)   # never: thick white is AMWH
    assert isinstance(bucket_names("LG", QUARTER, amwh=False, tape=True), Unnamed)    # a 1/4 with tape


def test_every_ic_code_of_the_rules_has_its_colour_word() -> None:
    assert set(IC_WORDS) == set(rules.COLOUR_BRANDS)


# ----- tied to Jim's files


@pytest.mark.fixtures
def test_the_tafisa_names_and_own_names_are_rules_section_2_exactly() -> None:
    path = DOCS / "RULES.md"
    if not path.is_file():
        pytest.skip("no local toolkit: set NGR_TOOLKIT (docs/p2/build-spec.md, section 4)")
    text = path.read_text(encoding="utf-8")
    start = text.index("## Colours are named by their TAFISA name")
    section = text[start:text.index("\n## ", start + 1)]
    pairs = {}
    for line in section.splitlines():
        if line.startswith("    ") and "→" in line:            # the map is the section's indented block
            for part in line.split("·"):
                word, name = (side.strip() for side in part.split("→"))
                pairs[word] = re.sub(r"\s*\(.*\)$", "", name)
    own = dict(re.findall(r'IC "([^"]+)" is\s+\*\*([^*]+)\*\*', section))

    assert pairs == TAFISA_NAMES
    assert own == OWN_NAMES


@pytest.mark.fixtures
def test_each_codes_colour_word_is_material_lookups() -> None:
    aliases = _assigned(_tree("material_lookup.py"), "ALIASES")
    assert isinstance(aliases, ast.Dict)
    theirs = {value.elts[0].value: key.value for key, value in zip(aliases.keys, aliases.values, strict=True)}

    assert {code: word.casefold() for code, word in IC_WORDS.items()} == theirs
