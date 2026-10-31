"""
The order heads-up (docs/p1b/build-spec.md, sections 3.4 and 5.8; rulings 12a, 12b and 12c).

Above all it can't leak a client's details: the reader picks named fields, and the two it spells
back (the material and the tape colour) must look like a material. Tested with a fake address in
every field, and on real files against each file's own client fields, reported by count only.
An unreadable takeoff is None, never a partial list.
"""
from __future__ import annotations

import copy
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from conftest import FIXTURES
from ngrunner.guards.headsup import (
    UNREADABLE_NOTE,
    Takeoff,
    heads_up,
    order_flags,
    read_takeoff,
    unreadable_dev,
)
from ngrunner.guards.tracker import read_tracker
from ngrunner.messages import Message
from ngrunner.messages.lint import lint
from ngrunner.messages.templates import dev

JOB = "Testerson_101426"
ADDRESS = "123 Testerson Lane, Testville, CA 90001"
LINE = 25000.0
TAPE_NAME = '{} 1mm — 15/16" / 1"'


def bucket(material: str = "Egger Classic White", thk: float = 0.75, waste: float = 12,
           raw: float = 11.2, lf: float = 240.0) -> dict[str, Any]:
    return {"material": material, "thk_in": thk, "parts": [{"name": "FEnd", "ic_in": 14.0}],
            "sqft": 193.8, "sqm": 18.0, "calc_sheets_raw": raw, "sheets_with_waste": waste,
            "sheet_math": "11.2 x 1.1 = 12.3", "edgeband_lf": lf}


def tape(colour: str = "Egger Classic White", feet: float = 240.0) -> dict[str, Any]:
    return {"material": TAPE_NAME.format(colour), "linear_feet": feet}


def takeoff_json(**changes: Any) -> dict[str, Any]:
    """The usable shape section 3.4 measured, with made-up values."""
    data: dict[str, Any] = {
        "schema_version": "2.0", "job": JOB, "client": "Testerson", "designer": "TT",
        "job_type": "closet", "address": ADDRESS, "material_rule": "IC to Egger",
        "ic_color": "Arctic White (WH)", "walls": [], "sheet_size_in": "49 x 97",
        "waste_factor": 1.1, "total_board_parts": 3, "buckets": [bucket()],
        "parts": [{"name": "FEnd"}], "edgeband": {"rule": "by colour", "rows": [tape()]},
        "verticals": [], "runs": [], "designs": ["Primary"], "pulled_classified": [],
        "estimate": {"total": 7999.05, "designs": [], "discount": 0, "discount_label": ""},
        "flags": ["a free-text line"],
    }
    data.update(changes)
    return data


def read(data: Any) -> Takeoff | None:
    return read_takeoff(json.dumps(data).encode("utf-8"))


def good(**changes: Any) -> Takeoff:
    result = read(takeoff_json(**changes))
    assert result is not None
    return result


def everything_said(message: Message) -> str:
    return "\n".join((message.title, *message.lines))


# ----- the usable shape


def test_the_named_blocks_are_read() -> None:
    t = good(buckets=[bucket("Tafisa Apres Skis", 0.75, 12, 11.2, 240.0),
                      bucket("Tafisa Apres Skis", 0.25, 4, 3.1, 0.0)],
             edgeband={"rows": [tape("Tafisa Apres Skis", 240.0)]},
             ic_color="New Natural (AP)")
    assert [(s.material, s.thk_in, s.sheets_with_waste, s.calc_sheets_raw) for s in t.sheets] == [
        ("Tafisa Apres Skis", 0.75, 12, 11.2), ("Tafisa Apres Skis", 0.25, 4, 3.1)]
    assert [(r.colour, r.width, r.feet) for r in t.tape] == [("Tafisa Apres Skis", '15/16"', 240.0)]
    assert t.total == 7999.05
    assert t.ic_color == "New Natural (AP)"


@pytest.mark.parametrize("total, read_as", [
    (7999.05, 7999.05), (7999, 7999.0), ("$7,999.05", 7999.05), ("$25,036.28", 25036.28),
    ("7999.05", 7999.05),
])
def test_a_total_reads_as_a_number_or_as_a_string(total: Any, read_as: float) -> None:
    assert good(estimate={"total": total}).total == read_as


@pytest.mark.parametrize("total", [None, "", "ask Jim", True, float("nan"), float("inf"), [1], {},
                                   "call at 7999 please", "$12,000 or so", "Unit 7999"])
def test_a_total_that_can_not_be_read_is_none_and_costs_nothing_else(total: Any) -> None:
    t = good(estimate={"total": total})
    assert t.total is None
    assert t.sheets


@pytest.mark.parametrize("estimate", [None, [], "7999", 7999])
def test_an_estimate_block_not_as_measured_has_no_total(estimate: Any) -> None:
    assert good(estimate=estimate).total is None


def test_no_estimate_block_has_no_total() -> None:
    data = takeoff_json()
    del data["estimate"]
    assert read(data).total is None


@pytest.mark.parametrize("version", ["2.0", "1.3", "9.9", None])
def test_the_version_is_ignored_since_it_does_not_tell_the_shape(version: Any) -> None:
    assert good(schema_version=version).sheets


def test_a_file_with_no_version_but_the_blocks_reads() -> None:
    data = takeoff_json()
    del data["schema_version"]
    assert read(data) is not None


def test_a_byte_order_mark_is_no_obstacle() -> None:
    assert read_takeoff(b"\xef\xbb\xbf" + json.dumps(takeoff_json()).encode("utf-8")) is not None


# ----- None: blocks missing or not as measured, never a partial list


def test_the_bucket_dict_keyed_by_material_gives_none() -> None:
    assert read(takeoff_json(buckets={"Egger Classic White": bucket()})) is None


def test_the_bucket_dict_keyed_by_thickness_with_no_material_gives_none() -> None:
    by_thickness = {"0.75": {"calc_sheets_raw": 3.0, "sheets_with_waste": 4},
                    "0.25": {"calc_sheets_raw": 1.0, "sheets_with_waste": 2}}
    assert read(takeoff_json(buckets=by_thickness)) is None


def _without(key: str) -> dict[str, Any]:
    data = takeoff_json()
    del data[key]
    return data


@pytest.mark.parametrize("data", [
    _without("buckets"), _without("edgeband"), _without("waste_factor"),
    takeoff_json(buckets=[]), takeoff_json(buckets=None), takeoff_json(buckets="Egger"),
    takeoff_json(edgeband=None), takeoff_json(edgeband=[]), takeoff_json(edgeband={}),
    takeoff_json(edgeband={"rule": "x"}), takeoff_json(edgeband={"rows": {}}),
    takeoff_json(edgeband={"rows": []}), takeoff_json(edgeband={"rows": "none"}),
    takeoff_json(waste_factor="1.1"), takeoff_json(waste_factor=0.9),
    takeoff_json(waste_factor=True),
    [], "a string", 7, None,
])
def test_a_shape_not_as_measured_gives_none(data: Any) -> None:
    assert read(data) is None


@pytest.mark.parametrize("data", [b"", b"not json", b"\xff\xfe\x00", b"{", b'{"buckets": [}'])
def test_bytes_that_are_no_json_give_none(data: bytes) -> None:
    assert read_takeoff(data) is None


def _bad_bucket(**changes: Any) -> dict[str, Any]:
    return bucket() | changes


def _missing(key: str) -> dict[str, Any]:
    made = bucket()
    del made[key]
    return made


@pytest.mark.parametrize("bad", [
    "just text", 7, None, _missing("material"), _missing("thk_in"), _missing("sheets_with_waste"),
    _missing("calc_sheets_raw"), _bad_bucket(material=None), _bad_bucket(material=7),
    _bad_bucket(material=""), _bad_bucket(thk_in="3/4"), _bad_bucket(thk_in=None),
    _bad_bucket(thk_in=True), _bad_bucket(thk_in=0), _bad_bucket(thk_in=0.3337),
    _bad_bucket(sheets_with_waste="12"), _bad_bucket(sheets_with_waste=True),
    _bad_bucket(sheets_with_waste=0), _bad_bucket(sheets_with_waste=-1),
    _bad_bucket(sheets_with_waste=float("nan")), _bad_bucket(calc_sheets_raw=None),
    _bad_bucket(calc_sheets_raw="3"), _bad_bucket(calc_sheets_raw=float("inf")),
])
def test_one_bad_bucket_among_good_ones_gives_none_never_a_partial_list(bad: Any) -> None:
    assert read(takeoff_json(buckets=[bucket(), bad])) is None
    assert read(takeoff_json(buckets=[bad, bucket()])) is None


@pytest.mark.parametrize("bad", [
    "just text", None, {"linear_feet": 3.0}, {"material": TAPE_NAME.format("Ore")},
    {"material": 7, "linear_feet": 3.0}, {"material": "Ore tape", "linear_feet": 3.0},
    {"material": 'Ore 1mm - 15/16"', "linear_feet": 3.0},
    {"material": TAPE_NAME.format("Ore"), "linear_feet": "3"},
    {"material": TAPE_NAME.format("Ore"), "linear_feet": True},
    {"material": TAPE_NAME.format("Ore"), "linear_feet": -1},
    {"material": TAPE_NAME.format("Ore"), "linear_feet": float("nan")},
])
def test_one_bad_tape_row_among_good_ones_gives_none_never_a_partial_list(bad: Any) -> None:
    assert read(takeoff_json(edgeband={"rows": [tape(), bad]})) is None


# ----- a client's details can't travel


def _fill_every_string(value: Any, text: str) -> Any:
    if isinstance(value, dict):
        return {k: _fill_every_string(v, text) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill_every_string(v, text) for v in value]
    return text if isinstance(value, str) else value


def test_a_fake_address_in_every_string_field_never_reaches_a_message() -> None:
    data = _fill_every_string(takeoff_json(flags=["x"], site="x", email="x", contact_notes="x"),
                              ADDRESS)
    t = read(data)
    # material and tape colour would be spelled back, so an address there refuses the file
    assert t is None


def test_a_fake_address_in_every_field_not_spelled_back_never_reaches_a_message() -> None:
    data = _fill_every_string(takeoff_json(site="x", email="x", contact_notes="x"), ADDRESS)
    good_bits = takeoff_json()
    data["buckets"][0]["material"] = good_bits["buckets"][0]["material"]
    data["edgeband"]["rows"] = good_bits["edgeband"]["rows"]
    t = read(data)
    assert t is not None
    message = heads_up(JOB, t, order_flags(t, total_line=LINE), revised=False)
    said = everything_said(message)
    for word in ("Testerson Lane", "Testville", "90001", "123 "):
        assert word not in said
    assert lint(message) == []


@pytest.mark.parametrize("field", ["client", "address", "contact_notes", "site", "email",
                                   "designer", "job_type", "material_rule", "ic_color",
                                   "sheet_size_in", "flags", "designs", "walls", "verticals",
                                   "runs", "parts", "job"])
def test_a_marked_field_never_reaches_a_message(field: str) -> None:
    marker = "Zanzibarmarker"
    t = good(**{field: marker})
    message = heads_up(JOB, t, order_flags(t, total_line=LINE), revised=True)
    assert marker not in everything_said(message)
    assert marker not in repr(t.sheets) + repr(t.tape)


@pytest.mark.parametrize("field", ["sheet_math", "parts", "sqft", "sqm"])
def test_a_marked_bucket_field_never_reaches_a_message(field: str) -> None:
    marker = "Zanzibarmarker"
    t = good(buckets=[bucket() | {field: marker}])
    assert marker not in everything_said(heads_up(JOB, t, (), revised=False))


def test_the_ic_colour_free_text_is_never_echoed() -> None:
    # real files hold words like a site's name in this field ("Name: Arctic White | Name: Colour")
    t = good(ic_color="Zanzibar House: Arctic White | 123 Testerson Lane: Interlochen (SU)")
    said = everything_said(heads_up(JOB, t, order_flags(t, total_line=LINE), revised=False))
    assert "Zanzibar" not in said
    assert "Testerson Lane" not in said
    assert "Interlochen" not in said


def test_the_ic_colour_free_text_stays_out_of_a_repr() -> None:
    assert "Zanzibar" not in repr(good(ic_color="Zanzibar House: Arctic White"))


@pytest.mark.parametrize("text", [
    "123 Testerson Lane", "44 Main St", "Testerson Residence, Testville", "Lot 12 Testerson Way",
    "Unit 5 Fake Court", "Testerson Road", "Oak Drive", "jim@example.com", "(310) 555-0123",
    "310-555-0123", "Testville CA 90001", "PO Box 12", "https://example.com/x", r"C:\Users\x",
    "a" * 90,
])
def test_a_material_that_reads_like_an_address_refuses_the_file(text: str) -> None:
    assert read(takeoff_json(buckets=[bucket(text)])) is None
    assert read(takeoff_json(edgeband={"rows": [tape(text)]})) is None


def test_a_number_then_a_word_is_refused_by_itself() -> None:
    assert read(takeoff_json(buckets=[bucket("Bldg 12 Fakename")])) is None


def test_a_long_number_is_refused_by_itself() -> None:
    assert read(takeoff_json(buckets=[bucket("Bigtown CA 12345")])) is None


@pytest.mark.parametrize("text", ["Egger Black U999 ST19 Woodgrain", "Light_M2007 Oak", "Am Lam (1 in)"])
def test_a_code_or_a_thickness_is_not_taken_for_an_address(text: str) -> None:
    assert read(takeoff_json(buckets=[bucket(text)])) is not None


OWN_DETAIL = "Zanzibar Heights"     # no street word or number, so only the own-value line can refuse it


def test_a_material_with_no_client_detail_in_its_file_reads_as_the_control() -> None:
    data = takeoff_json()
    data["buckets"][0]["material"] = f"{OWN_DETAIL} Oak"
    assert read(data) is not None


@pytest.mark.parametrize("field, value", [
    ("client", "Zanzibar"), ("site", OWN_DETAIL), ("address", "Zanzibar"),
    ("contact_notes", "Zanzibar"), ("email", "Zanzibar"),
])
def test_a_material_holding_its_files_client_detail_refuses_the_file(field: str,
                                                                     value: str) -> None:
    data = takeoff_json(**{field: value})
    data["buckets"][0]["material"] = f"{OWN_DETAIL} Oak"
    assert read(data) is None


def test_a_tape_colour_holding_its_files_client_detail_refuses_the_file() -> None:
    assert read(takeoff_json(client="Zanzibar",
                             edgeband={"rows": [tape(f"{OWN_DETAIL} Oak")]})) is None


def test_a_client_detail_nested_in_the_file_is_compared_too() -> None:
    data = takeoff_json(designs=[{"name": "x", "site": OWN_DETAIL}])
    data["buckets"][0]["material"] = f"{OWN_DETAIL} Oak"
    assert read(data) is None


def test_a_client_field_that_is_a_block_is_compared_too() -> None:
    data = takeoff_json(client={"name": OWN_DETAIL, "more": [OWN_DETAIL]})
    data["buckets"][0]["material"] = f"{OWN_DETAIL} Oak"
    assert read(data) is None


def test_a_part_of_a_word_is_no_client_detail() -> None:
    # a client called White must not be found inside Whiteley; a shorter part (CA) says nothing
    data = takeoff_json(client="Whit", address="CA")
    data["buckets"][0]["material"] = "Egger Whiteley Oak"
    assert read(data) is not None


def test_a_comma_part_of_the_address_is_compared_on_its_own() -> None:
    data = takeoff_json(address=f"9 Mill Row, {OWN_DETAIL}, CA")
    data["buckets"][0]["material"] = f"{OWN_DETAIL} Oak"
    assert read(data) is None


@pytest.mark.parametrize("material", [
    "Egger Classic White", "Tafisa Spring Blossom_Ore", "Tafisa Casting at First Light_M2007",
    "Am Lam Wh (1 in)", "Am Lam Wh (3/4)", "White 1/4 Vinyl", "Charcoal (AMLM)",
    "Egger Black U999 ST19 Woodgrain", "Tafisa Southwester_Fort (3/4)", "Tafisa White Choc MMist",
    "Tafisa Moonlight_Blue Stone", "Arauco Prism Antique White SF28",
])
def test_every_material_spelling_measured_reads_and_passes_the_lint(material: str) -> None:
    t = good(buckets=[bucket(material)], edgeband={"rows": [tape(material)]})
    assert lint(heads_up(JOB, t, order_flags(t, total_line=LINE), revised=False)) == []


# ----- the message


def test_the_message_reads_as_the_spec_shows_it() -> None:
    t = good(buckets=[bucket("Tafisa Apres Skis", 0.75, 12), bucket("Tafisa Apres Skis", 0.25, 4)],
             edgeband={"rows": [tape("Tafisa Apres Skis", 240.0)]})
    message = heads_up(JOB, t, (), revised=False)
    assert message.lines[1:] == (
        "1. Testerson_101426: 12 sheets 3/4 Tafisa Apres Skis, 4 sheets 1/4 Tafisa Apres Skis"
        " (takeoff estimate; the nest decides).",
        '2. Testerson_101426: 240 feet of 15/16" tape, Tafisa Apres Skis'
        " (takeoff estimate; the nest decides).",
    )
    assert lint(message) == []


def test_the_first_line_says_it_is_an_estimate_and_names_the_basis() -> None:
    message = heads_up(JOB, good(), (), revised=False)
    assert "takeoff estimate; the nest decides" in message.lines[0].casefold()
    assert "sheets with 10% waste" in message.lines[0]


def test_the_basis_comes_from_the_file_never_a_guess() -> None:
    message = heads_up(JOB, good(waste_factor=1.15), (), revised=False)
    assert "sheets with 15% waste" in message.lines[0]
    assert "10%" not in everything_said(message)


def test_the_count_shown_is_the_one_with_waste_not_the_raw_one() -> None:
    said = everything_said(heads_up(JOB, good(buckets=[bucket(waste=12, raw=11.2)]), (),
                                    revised=False))
    assert "12 sheets" in said
    assert "11.2" not in said


def test_one_sheet_reads_in_the_singular() -> None:
    assert "1 sheet 3/4 Egger" in everything_said(
        heads_up(JOB, good(buckets=[bucket(waste=1)]), (), revised=False))


def test_a_revised_takeoff_says_so() -> None:
    first = heads_up(JOB, good(), (), revised=False)
    second = heads_up(JOB, good(), (), revised=True)
    assert "revised" not in everything_said(first).casefold()
    assert "revised" in second.title.casefold()
    assert lint(second) == []


def test_the_title_names_the_job_and_every_line_passes_the_lint() -> None:
    message = heads_up(JOB, good(), (), revised=False)
    assert JOB in message.title
    assert message.kind == "heads_up"       # what templates.FIXED knows it by
    assert message.urgent is False
    assert lint(message) == []


def test_a_thickness_is_not_said_twice_when_the_material_says_it() -> None:
    said = everything_said(heads_up(JOB, good(buckets=[
        bucket("Am Lam Wh (1 in)", 1.0, 3), bucket("White 1/4 Vinyl", 0.25, 4),
        bucket("Tafisa Southwester_Fort (3/4)", 0.75, 7)]), (), revised=False))
    assert "3 sheets Am Lam Wh (1 in)" in said
    assert "4 sheets White 1/4 Vinyl" in said
    assert "7 sheets Tafisa Southwester Fort (3/4)" in said


def test_a_one_inch_material_says_its_thickness_in_inches() -> None:
    said = everything_said(heads_up(JOB, good(buckets=[bucket("Am Lam Wh", 1.0, 3)]), (),
                                    revised=False))
    assert '3 sheets 1" Am Lam Wh' in said


def test_an_underscore_becomes_a_space_since_the_lint_refuses_it() -> None:
    message = heads_up(JOB, good(buckets=[bucket("Tafisa Spring Blossom_Ore")],
                                 edgeband={"rows": [tape("Tafisa Spring Blossom_Ore")]}), (),
                       revised=False)
    assert "Tafisa Spring Blossom Ore" in everything_said(message)
    assert "_" not in everything_said(message).replace(JOB, "")
    assert lint(message) == []


def test_tape_drops_the_gauge_the_lint_refuses() -> None:
    message = heads_up(JOB, good(), (), revised=False)
    assert "1mm" not in everything_said(message)
    assert lint(message) == []


def test_tape_with_no_feet_gets_no_line() -> None:
    t = good(buckets=[bucket(), bucket("White 1/4 Vinyl", 0.25)],
             edgeband={"rows": [tape(), tape("White 1/4 Vinyl", 0.0)]})
    assert sum("feet of" in line for line in heads_up(JOB, t, (), revised=False).lines) == 1


def test_tape_of_one_colour_and_width_is_added_up() -> None:
    t = good(edgeband={"rows": [tape("Am Lam Wh", 34.9), tape("Am Lam Wh", 162.3)]})
    said = everything_said(heads_up(JOB, t, (), revised=False))
    assert '197.2 feet of 15/16" tape, Am Lam Wh' in said
    assert "34.9" not in said


def test_tape_all_at_zero_means_no_tape_line_and_still_a_valid_message() -> None:
    t = good(edgeband={"rows": [tape(feet=0.0)]})
    message = heads_up(JOB, t, (), revised=False)
    assert not any("feet of" in line for line in message.lines)
    assert lint(message) == []


def test_a_flag_is_a_numbered_item_after_the_lists() -> None:
    message = heads_up(JOB, good(), ("Estimate total $25,036.28 is at or above $25,000",),
                       revised=False)
    assert message.lines[-1] == (
        "3. Testerson_101426: Estimate total $25,036.28 is at or above $25,000.")
    assert lint(message) == []


def test_no_message_ever_says_ok_to_cut() -> None:
    t = good()
    message = heads_up(JOB, t, order_flags(t, total_line=LINE), revised=False)
    said = everything_said(message).casefold()
    for words in ("ok to cut", "okay to cut", "safe to cut", "ready to cut", "good to cut"):
        assert words not in said


# ----- the flags


def test_all_classic_white_at_a_small_total_has_only_the_swap_line() -> None:
    assert order_flags(good(), total_line=LINE) == (
        "IC Arctic White, built as Egger Classic White",)


def test_a_material_that_is_not_classic_white_gets_a_line_naming_each_once() -> None:
    t = good(ic_color="", buckets=[
        bucket("Tafisa Morning Mist"), bucket("Tafisa Morning Mist", 1.0),
        bucket("Egger Classic White"), bucket("Charcoal (AMLM)")])
    flags = order_flags(t, total_line=LINE)
    assert flags == ("Not all Classic White: Tafisa Morning Mist, Charcoal (AMLM)",)


def test_the_swap_is_named_once_however_many_buckets_carry_it() -> None:
    t = good(buckets=[bucket(), bucket("Egger Classic White", 0.25)])
    flags = order_flags(t, total_line=LINE)
    assert flags.count("IC Arctic White, built as Egger Classic White") == 1


def test_the_swap_needs_the_built_material_on_the_job() -> None:
    t = good(ic_color="Arctic White (WH)", buckets=[bucket("Am Lam Wh")])
    assert not any("built as" in flag for flag in order_flags(t, total_line=LINE))


def test_the_charcoal_ruling_is_named_as_a_swap() -> None:
    t = good(ic_color="Charcoal (CC)", buckets=[bucket("Egger Black U999 ST19 Woodgrain")])
    flags = order_flags(t, total_line=LINE)
    assert "IC Charcoal, built as Egger Black U999 ST19 Woodgrain" in flags


def test_a_colour_kept_by_name_is_no_swap() -> None:
    t = good(ic_color="Ore (LG)", buckets=[bucket("Tafisa Spring Blossom_Ore")])
    assert not any("built as" in flag for flag in order_flags(t, total_line=LINE))


@pytest.mark.parametrize("total, flagged", [(24999.99, False), (25000.0, True), (25036.28, True)])
def test_the_total_flag_is_at_or_above_the_line(total: float, flagged: bool) -> None:
    flags = order_flags(good(estimate={"total": total}), total_line=LINE)
    assert any("Estimate total" in flag for flag in flags) is flagged


def test_the_line_is_a_setting() -> None:
    assert any("Estimate total" in f for f in order_flags(good(), total_line=5000.0))


def test_the_total_line_reads_in_dollars() -> None:
    flags = order_flags(good(estimate={"total": "$25,036.28"}), total_line=LINE)
    assert "Estimate total $25,036.28 is at or above $25,000" in flags


def test_no_total_has_no_total_flag() -> None:
    assert not any("Estimate total" in f for f in order_flags(good(estimate=None), total_line=0.0))


def test_a_size_swap_is_not_flagged_in_p1b() -> None:
    t = good(flags=['1-1/8" vertical built at 3/4'])
    assert not any("1-1/8" in flag for flag in order_flags(t, total_line=LINE))


def test_every_flag_passes_the_lint_as_an_item() -> None:
    t = good(ic_color="Arctic White (WH)", estimate={"total": "$25,036.28"},
             buckets=[bucket(), bucket("Tafisa Spring Blossom_Ore", 0.75, 3),
                      bucket("Am Lam Wh (1 in)", 1.0, 3)])
    assert lint(heads_up(JOB, t, order_flags(t, total_line=LINE), revised=False)) == []


# ----- could not verify


def test_the_board_note_says_could_not_verify() -> None:
    assert UNREADABLE_NOTE == "could not verify the takeoff"


def test_the_dev_item_names_the_job_in_plain_words() -> None:
    at = datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)
    item = unreadable_dev(JOB, at=at)
    assert item.job == JOB
    assert item.at == at
    assert item.code == "takeoff_unreadable"
    assert lint(dev([item], day=date(2026, 10, 30))) == []


# ----- real files


def _real_conv() -> list[dict[str, str]]:
    path = FIXTURES / "p1b" / "conv" / "index.json"
    if not path.is_file():
        pytest.skip("no local conv copy: run tools/collect_p1b_documents.py (AGENTS.md: Fixtures)")
    return json.loads(path.read_text(encoding="utf-8"))


def _bytes(entry: dict[str, str]) -> bytes:
    return (FIXTURES / "p1b" / "conv" / entry["file"]).read_bytes()


CLIENT_FIELDS = ("address", "client", "contact_notes", "site", "email")


def _own_values(value: Any) -> list[str]:
    """Every string a file holds under a client field, however deep."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in CLIENT_FIELDS:
                found += _strings(item)
            else:
                found += _own_values(item)
    elif isinstance(value, list):
        for item in value:
            found += _own_values(item)
    return found


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


def _word(word: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.IGNORECASE) is not None


@pytest.mark.fixtures
def test_of_the_27_real_jsons_20_give_a_message_and_7_give_none_the_newest_among_them() -> None:
    index = _real_conv()
    files = len(index)
    assert files == 27
    results = [(e, read_takeoff(_bytes(e))) for e in index]
    messages = sum(t is not None for _, t in results)
    unread = sum(t is None for _, t in results)
    newest_unread = sum(t is None and e["role"] == "newest" for e, t in results)
    assert (messages, unread, newest_unread) == (20, 7, 1)
    at = datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)
    items = [unreadable_dev(e["job"], at=at) for e, t in results if t is None]
    dev_items = len(items)      # one each
    problems = len(lint(dev(items, day=date(2026, 10, 30))))
    assert (dev_items, problems) == (7, 0)


@pytest.mark.fixtures
def test_every_real_message_passes_the_lint_and_says_it_is_an_estimate() -> None:
    bad = estimates = 0
    for entry in _real_conv():
        t = read_takeoff(_bytes(entry))
        if t is None:
            continue
        for revised in (False, True):
            message = heads_up(entry["job"], t, order_flags(t, total_line=LINE), revised=revised)
            bad += bool(lint(message))
            estimates += "takeoff estimate; the nest decides" in message.lines[0].casefold()
    assert bad == 0
    assert estimates == 40


@pytest.mark.fixtures
def test_no_real_message_holds_its_own_files_client_values() -> None:
    whole = words = checked = 0
    fields_seen = 0
    for entry in _real_conv():
        t = read_takeoff(_bytes(entry))
        data = json.loads(_bytes(entry))
        own = [v for v in _own_values(data) if v.strip()]
        fields_seen += len(own)
        if t is None:
            continue
        message = heads_up(entry["job"], t, order_flags(t, total_line=LINE), revised=False)
        said = everything_said(message)
        checked += 1
        whole += sum(v.casefold() in said.casefold() for v in own)
        # Word by word as well: an address's street name, a client's first name. Words a job name
        # or the file's materials legitimately hold are not a leak: a surname is in the job name by
        # design, and a street can share a word with a colour.
        allowed = set(re.findall(r"[^\W_]+", entry["job"].casefold()))
        for s in t.sheets:
            allowed |= set(re.findall(r"[^\W_]+", s.material.casefold()))
        for r in t.tape:
            allowed |= set(re.findall(r"[^\W_]+", r.colour.casefold()))
        allowed |= {"white", "black", "classic", "egger", "charcoal", "arctic"}     # fixed words
        for value in own:
            for word in re.findall(r"[^\W\d_]{4,}", value):
                if word.casefold() not in allowed:
                    words += _word(word, said)
    assert checked == 20
    assert fields_seen > 0      # the check had something to check
    assert whole == 0
    assert words == 0


@pytest.mark.fixtures
def test_no_real_takeoff_keeps_a_client_value_in_what_it_can_spell() -> None:
    leaks = 0
    for entry in _real_conv():
        t = read_takeoff(_bytes(entry))
        if t is None:
            continue
        own = [v for v in _own_values(json.loads(_bytes(entry))) if v.strip()]
        spelled = repr(t.sheets) + repr(t.tape) + repr(t)
        leaks += sum(v in spelled for v in own)
    assert leaks == 0


def _real_snapshot():
    if not (FIXTURES / "p1b" / "tracker" / "tracker_snapshot_meta.csv").is_file():
        pytest.skip("no local tracker copy: run tools/collect_p1b_documents.py")
    return read_tracker(FIXTURES / "p1b" / "tracker")


@pytest.mark.fixtures
def test_the_tie_to_the_tracker_is_16_of_20_on_tape_and_16_of_20_on_sheets() -> None:
    """Section 3.4's tie: on the files with list buckets and tracker rows. It reads the JSON's own
    fields since 3 of those 20 have no edgeband rows, so the heads-up itself gives them None."""
    snap = _real_snapshot()
    tape_tied = raw_tied = waste_only = neither = counted = 0
    for entry in _real_conv():
        data = json.loads(_bytes(entry))
        buckets = data.get("buckets")
        if not isinstance(buckets, list) or not buckets:
            continue
        sheets = [r for r in snap.sheets if r.job == entry["job"]]
        feet = [r for r in snap.tape if r.job == entry["job"]]
        if not sheets and not feet:
            continue
        counted += 1
        tape_tied += math.isclose(sum(b["edgeband_lf"] for b in buckets),
                                  sum(r.calc_feet or 0.0 for r in feet), abs_tol=0.5)
        tracker = sum(r.calc_sheets or 0.0 for r in sheets)
        if abs(sum(b["calc_sheets_raw"] for b in buckets) - tracker) <= 0.1:
            raw_tied += 1
        elif abs(sum(b["sheets_with_waste"] for b in buckets) - tracker) <= 0.1:
            waste_only += 1
        else:
            neither += 1
    assert counted == 20
    assert tape_tied == 16
    assert (raw_tied, waste_only, neither) == (12, 4, 4)


@pytest.mark.fixtures
def test_the_flags_on_the_20_real_takeoffs_count_as_measured() -> None:
    colour = swap = total = 0
    for entry in _real_conv():
        t = read_takeoff(_bytes(entry))
        if t is None:
            continue
        flags = order_flags(t, total_line=LINE)
        colour += any(f.startswith("Not all Classic White") for f in flags)
        swap += "IC Arctic White, built as Egger Classic White" in flags
        total += any(f.startswith("Estimate total") for f in flags)
    assert (colour, swap, total) == (12, 13, 1)


@pytest.mark.fixtures
def test_a_real_file_with_an_address_put_into_a_material_is_refused() -> None:
    for entry in _real_conv():
        if read_takeoff(_bytes(entry)) is None:
            continue
        data = copy.deepcopy(json.loads(_bytes(entry)))
        data["buckets"][0]["material"] = ADDRESS
        refused = read(data) is None
        assert refused, entry["file"]
        return
    pytest.fail("no readable real file")

