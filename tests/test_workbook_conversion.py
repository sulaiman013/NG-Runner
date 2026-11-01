"""
NG Runner's converter on a summary's bytes, given back as the IC Conversion JSON in Jim's current shape
(tools/workbook/conversion.py): his shared writer's v2 keys, BUDGET's source block, and the unplaced
list (Q17b).

Made-up summaries only (tests/helpers_pdf.py), with made-up names. The takeoff itself is NG Runner's
(ngrunner/convert, tested on its own); what is tested here is that the JSON says exactly that takeoff,
part by part, and that a line it could not verify is never filled in.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from helpers_pdf import summary_pdf
from ngrunner.convert import convert, rules, takeoff_branch
from ngrunner.intake.summary_check import check_summary
from ngrunner.messages.templates import takeoff_material
from tools.workbook.conversion import ConversionRefused, board_rows, conversion

JOB = "Testerson_101426"
NAME = f"{JOB}_summary.pdf"
PARTS = [
    ("VT-WH", 2, "VT 14x83.9x3/4 FD WH"),           # snaps: 356 x 2132
    ("VT-WH", 1, "VT 14x84x3/4 FD WH"),             # height 1.6mm off the grid: kept, Review
    ("VT-WH", 1, "VT 14x47.4x3/4 FD FN WH"),        # wall-mounted
    ("SH-WH", 5, "SH 19.1x29.9x3/4 ADJ WH"),        # depth 485.14 snaps to 484
    ("SH-WH", 1, "SH 11x24x3/4 FIX WH"),            # depth 279.4: 12.6mm off, kept, Review
    ("CK-WH", 2, "CK DBack 3.6x22.2x0.6 NN WH"),    # 0.6in: could not verify
    ("400000570", 1, "White Rail Cover"),           # a rail cover printed as hardware
    ("400000155", 8, "Shelf Pin"),                  # not board
]
V2_KEYS = {"schema_version", "job", "client", "designer", "job_type", "address", "designs", "ic_color",
           "material_rule", "walls", "sheet_size_in", "waste_factor", "total_board_parts", "buckets",
           "edgeband", "verticals", "runs", "estimate", "flags", "parts", "pulled_classified"}
PART_KEYS = {"design", "wall", "type", "qty", "desc", "tokens", "mount", "radius", "grain", "ic_in",
             "ic_mm", "build_mm", "thick_in", "material", "sqft", "eb_lf", "review"}


def summary(parts=PARTS, **kw) -> bytes:
    return summary_pdf(parts=parts, **kw)


def made(data: bytes | None = None, **kw) -> dict:
    return conversion(summary() if data is None else data, summary_name=NAME, **kw)


def by_desc(conv: dict, desc: str) -> dict:
    return next(p for p in conv["parts"] if p["desc"].startswith(desc))


# ----- his shape, saying NG Runner's takeoff


def test_a_closet_summary_gives_his_v2_shape_and_its_job_from_project_name() -> None:
    conv = made()

    assert V2_KEYS <= set(conv) and conv["schema_version"] == "2.0"
    assert conv["job"] == JOB and conv["job_type"] == "closet"
    assert conv["designs"] == ["Testerson - Primary Closet"]
    assert all(set(p) == PART_KEYS for p in conv["parts"])
    assert conv["source"] == {"summary_pdf": NAME, "sha256_16": hashlib.sha256(summary()).hexdigest()[:16]}
    assert conv["waste_factor"] == 1.1 and conv["sheet_size_in"] == "49 x 97"


def test_its_numbers_are_ng_runners_takeoff_bucket_by_bucket() -> None:
    data = summary()
    t = convert(data, branch=takeoff_branch(check_summary(data)))
    conv = made(data)

    assert conv["total_board_parts"] == t.board_parts == 11
    assert [(b["ic_colour"], b["thk_in"], b["parts"], b["sqft"], b["sheets_with_waste"], b["calc_sheets_raw"],
             b["edgeband_lf"]) for b in conv["buckets"]] == [
        (b.colour, b.thk_in, b.parts, b.sqft, b.sheets_with_waste, b.calc_sheets_32, b.tape_lf) for b in t.buckets]
    assert conv["edgeband"]["rows"] == [{"material": 'Egger Classic White 1mm — 15/16" / 1"',
                                         "linear_feet": t.buckets[0].tape_lf}]
    assert conv["ng_convert"]["complete"] is t.complete is False


def test_its_parts_add_up_to_the_takeoff() -> None:
    conv = made()
    b = conv["buckets"][0]
    assert sum(p["qty"] for p in conv["parts"]) == b["parts"]
    assert sum(p["sqft"] for p in conv["parts"]) == pytest.approx(b["sqft"], abs=0.01)
    assert sum(p["eb_lf"] for p in conv["parts"]) == pytest.approx(b["edgeband_lf"], abs=0.05)


def test_a_bucket_carries_his_sheet_math_and_both_sheet_numbers() -> None:
    b = made()["buckets"][0]
    assert b["material"] == "Egger Classic White" and b["thk_label"] == "3/4" and b["sheet_in"] == [49.0, 97.0]
    assert b["sheet_math"] == (f"ceil({b['sqft']} x 1.10 / 33.0069) = "
                               f"ceil({b['sqft'] * 1.1 / (49 * 97 / 144):.3f}) = {b['sheets_with_waste']}")
    assert b["sqm"] == pytest.approx(b["sqft"] * 0.09290304, abs=0.001)
    assert b["tape"] == '15/16" / 1"'


# ----- each part as he writes it


def test_vertical_and_shelf_depths_and_vertical_heights_snap_as_schema_says_and_nothing_else() -> None:
    conv = made()
    assert by_desc(conv, "VT 14x83.9")["build_mm"] == [356, 2132]
    assert by_desc(conv, "VT 14x83.9")["review"] == ""
    assert by_desc(conv, "SH 19.1x29.9")["build_mm"] == [484, 759.46]       # the span stays exact
    rail = by_desc(conv, "White Rail Cover")
    assert rail["build_mm"] == [88.9, 2438.4] and rail["ic_in"] == [3.5, 96.0]


def test_a_size_beyond_the_tolerance_is_kept_ic_exact_and_flagged_review() -> None:
    conv = made()
    tall = by_desc(conv, "VT 14x84x3/4")
    assert tall["build_mm"] == [356, 2133.6] and tall["review"] == (
        "height 2133.6mm is 1.6mm off the 32mm grid: kept IC-exact, Review")
    shallow = by_desc(conv, "SH 11x24")
    assert shallow["build_mm"] == [279.4, 609.6] and shallow["review"] == (
        "depth 279.4mm is 12.6mm from the nearest standard depth: kept IC-exact, Review")


def test_mount_radius_and_tokens_are_read_from_the_description() -> None:
    conv = made()
    assert by_desc(conv, "VT 14x47.4")["mount"] == "WM" and by_desc(conv, "VT 14x83.9")["mount"] == "FM"
    assert "FN" in by_desc(conv, "VT 14x47.4")["tokens"].split()
    assert by_desc(conv, "SH 19.1")["mount"] == "" and by_desc(conv, "SH 19.1")["radius"] is False
    rad = made(summary([("VT-WH", 1, "VT RAD 14x83.9x3/4 FD WH")]))
    assert rad["parts"][0]["radius"] is True


def test_the_rail_cover_printed_as_hardware_is_built_from_the_rooms_three_quarter() -> None:
    rail = by_desc(made(), "White Rail Cover")
    assert rail["type"] == "TR Rail Cover" and rail["thick_in"] == 0.75
    assert rail["desc"] == "White Rail Cover (3/4 at 3.5x96, RULES 224)" and rail["eb_lf"] == 16.0


# ----- could not verify: never filled in


def test_a_line_that_could_not_be_verified_never_becomes_a_part() -> None:
    conv = made()
    assert "CK" not in {p["type"] for p in conv["parts"]}
    assert conv["unplaced"] == [{"page": 4, "qty": 2, "item": "CK-WH", "description": "CK DBack 3.6x22.2x0.6 NN WH",
                                 "reason": "a CK printed 0.6in thick, which the rules don't say is board"}]
    assert conv["flags"][0] == ("Page 4: 2 of item CK-WH (CK DBack 3.6x22.2x0.6 NN WH), a CK printed 0.6in"
                                " thick, which the rules don't say is board. Could not verify.")
    ck = next(r for r in conv["pulled_classified"] if r["item"] == "CK-WH")
    assert ck["bucket"] == "could not verify" and "0.6in" in ck["class_note"]


def test_a_bucket_with_no_sheet_size_has_no_sheet_count_and_says_so() -> None:
    conv = made(summary([("SH-CC", 2, "SH 14x24x3/4 FIX CC"), ("BP-CC", 1, "BP 14x96x3/8 CC")]))
    backs = next(b for b in conv["buckets"] if b["thk_in"] == 0.25)       # RULES lists no 1/4 Egger Black
    assert backs["sheets_with_waste"] is None and backs["sheet_in"] is None
    assert backs["sheet_math"] == "could not verify: RULES' sheet table gives no size for it here (ruling R5)"
    assert backs["material"] == "Egger Black U999"
    assert conv["sheet_size_in"] == "by bucket"
    assert "No sheet size for 1/4 Egger Black U999: could not verify its sheets." in conv["flags"]


def test_a_white_part_built_1in_makes_the_whole_jobs_white_amwh_with_its_backs_on_vinyl() -> None:
    """Question 10 (RULES 214 and the RULES of 2026-10-06; Jim's yes awaited): the 1/4 white backs go on
    White 1/4 Vinyl at 49x97 (RULES 135), and every white bucket is named AMWH."""
    conv = made(summary([("SH-WH", 2, "SH 14x24x1 FIX WH"), ("BP-WH", 1, "BP 14x96x3/8 WH")]))
    assert [(b["thk_in"], b["material"], b["sheet_in"], b["sheets_with_waste"]) for b in conv["buckets"]] == [
        (0.25, "AMWH (IC WH)", [49.0, 97.0], 1), (1.0, "AMWH (IC WH)", [49.0, 97.0], 1)]
    assert conv["ng_convert"]["complete"] and conv["sheet_size_in"] == "49 x 97"


def test_a_complete_closet_says_so_and_has_nothing_unplaced() -> None:
    conv = made(summary([("SH-WH", 4, "SH 14x24x3/4 FIX WH"), ("VT-WH", 2, "VT 14x83.9x3/4 FD WH")]))
    assert conv["ng_convert"]["complete"] is True and conv["unplaced"] == [] and conv["flags"] == []


# ----- the PULLED list, every line in one bucket


def test_every_pulled_line_is_classified_and_the_board_bucket_equals_the_board_parts() -> None:
    conv = made(classify=lambda row: ("third_party", "pins", "bought, not cut"))
    assert sum(r["qty"] for r in conv["pulled_classified"]) == sum(q for _, q, _ in PARTS)
    assert sum(r["qty"] for r in conv["pulled_classified"] if r["bucket"] == "board") == conv["total_board_parts"]
    pin = next(r for r in conv["pulled_classified"] if r["item"] == "400000155")
    assert (pin["bucket"], pin["family"], pin["class_note"]) == ("third_party", "pins", "bought, not cut")
    assert pin["price_key"] == "400000155 — Shelf Pin"


def test_a_line_classify_calls_board_that_the_converter_does_not_cut_is_an_open_question() -> None:
    conv = made(classify=lambda row: ("board", "SH", "cut from sheet"))
    pin = next(r for r in conv["pulled_classified"] if r["item"] == "400000155")
    assert pin["bucket"] == "unclassified" and pin["class_note"] == (
        "classify.py calls it board, and ng_convert doesn't count it as board: an open question")


def test_without_classify_the_other_lines_say_they_were_not_classified() -> None:
    pin = next(r for r in made()["pulled_classified"] if r["item"] == "400000155")
    assert pin["bucket"] == "" and pin["class_note"] == "not classified: classify.py wasn't given"


def test_board_rows_name_each_board_lines_quantity_item_and_verdict() -> None:
    rows = board_rows(summary())
    assert rows[(2, "CK-WH", ("could not verify", "a CK printed 0.6in thick, which the rules don't say is board"))] == 1
    assert sum(rows.values()) == 7                                  # the board lines, the pin left out


# ----- what it refuses, and what it never reads


@pytest.mark.parametrize("designs, words", [
    ((("Testerson - Garage", "Garage"),), "a garage job"),
    ((("Testerson - Garage", "Garage"), ("Testerson - Closet", "Closet")), "a closet and garage job"),
])
def test_a_garage_or_mixed_summary_is_refused(designs, words) -> None:
    with pytest.raises(ConversionRefused, match=words):
        made(summary(designs=designs))


def test_a_summary_that_fails_its_check_is_refused_with_its_reasons() -> None:
    early = summary(designs=(("Testerson - Primary Closet", "Closet"), ("Testerson - Hall", "Closet")),
                    finished=1)
    with pytest.raises(ConversionRefused, match="did not pass its check: .*Testerson - Hall"):
        made(early)


def test_a_job_name_that_isnt_the_summarys_is_refused() -> None:
    with pytest.raises(ConversionRefused, match="PROJECT NAME"):
        made(job="Otherson_101426")
    assert made(job=JOB)["job"] == JOB


def test_page_1s_contact_details_never_reach_it_and_the_client_fields_stay_blank() -> None:
    conv = made()
    said = json.dumps(conv)
    assert "(555) 010-0199" not in said and "HOME" not in said
    assert conv["client"] is None and conv["designer"] is None and conv["address"] is None


def test_walls_come_from_the_wall_tables_the_summary_check_read() -> None:
    assert made()["walls"] == {"Testerson - Primary Closet": [
        {"wall": "1", "len_in": 60.0, "height_in": 96.0, "ang": None},
        {"wall": "2", "len_in": 72.5, "height_in": 96.0, "ang": None}]}


def test_what_it_doesnt_make_is_named_and_left_empty() -> None:
    conv = made()
    assert conv["verticals"] == [] and conv["runs"] == {}
    assert {"verticals", "runs", "client", "designer", "address"} <= set(conv["ng_convert"]["not_made"])


@pytest.mark.parametrize("code", sorted(rules.COLOUR_BRANDS) + ["ZZ"])
def test_material_names_are_ng_runners_takeoff_names(code: str) -> None:
    from tools.workbook.conversion import material_name
    assert material_name(code, thick_white=False) == takeoff_material(code)
    assert material_name(code, thick_white=True) == takeoff_material(code, thick_white=True)


def test_the_same_bytes_give_the_same_json() -> None:
    assert json.dumps(made(), sort_keys=True) == json.dumps(made(), sort_keys=True)
    assert made()["ng_convert"]["rules"] == rules.fingerprint()
