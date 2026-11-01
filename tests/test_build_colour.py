"""
P5a's room colour (ngrunner/build/colour.py): the shade from the design's own parts list, the drawer front from
its GV codes, and the material run lifted whole from the newest known-good job built in that shade, never
hand-authored and never from a job Jim's standing material gate refuses (SCHEMA 46-85, RULES 211-233, 279).

Every job, line and run is made up (helpers_build.py); the template ids and texture names are SCHEMA's and
material_lookup's, which name no client.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import helpers_build as hb
from ngrunner.build import colour as co
from ngrunner.convert.summary_lines import SummaryLine
from ngrunner.store import open_store

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
ORE = co.Shade("LG", "Tafisa", co.TEXTURES["LG"])
ORE_RUN = hb.material_run(co.FAMILY_IDS["Tafisa"], co.TEXTURES["LG"])


def line(desc: str, qty: int = 1) -> SummaryLine:
    head = desc.split()[0]
    return SummaryLine("CTO", f"{head}-WH" if head.isupper() else "400000999", qty, desc, 9, 0)


WHITE_ROOM = (line("VT 14x83.9x3/4 FD WH", 3), line("SH 14x29.6x3/4 FIX WH", 4), line("DW 6.2x24.5x3/4 FL WH", 2),
              line("Rail Cover 96", 1), line("Hanging Rod 30in", 2))


# ----- the shade (SCHEMA 54-60, 85; RULES 211-216)


def test_a_room_whose_every_board_row_prints_one_colour_is_that_colours_shade() -> None:
    found = co.room_shade(WHITE_ROOM, WHITE_ROOM, branch="closet")
    assert found.value == hb.WHITE and found.source == "IC line"
    assert found.value.ids == {"3": "94", "1": "87", "2": "41", "4": "89", "5": "90"}  # never 88 (SCHEMA 82-84)
    ore = co.room_shade([line("VT 14x83.9x3/4 FD LG", 2), line("WS 24x60x1-1/8 LG")], [], branch="closet")
    assert ore.value == ORE and ore.value.ids["4"] == "82"  # Tafisa's own Tops (RULES 214-215)
    charcoal = co.room_shade([line("VT 14x83.9x3/4 FD CC")], [], branch="closet").value
    assert charcoal == co.Shade("CC", "Egger", "Black U999 ST19 Woodgrain")  # RULES 123, material_lookup


def test_white_on_a_job_holding_a_white_part_built_1in_is_amwh() -> None:
    job = (*WHITE_ROOM, line("SH 14x29.6x1-1/8 ADJ WH"))  # elsewhere on the job: RULES 214, rules.job_brand
    found = co.room_shade(WHITE_ROOM, job, branch="closet").value
    assert found == co.Shade("WH", "AMWH", None)
    assert found.ids == {"3": "91", "1": "65", "2": "41", "4": "53", "5": "86"} and found.key == "wh_amwh"


@pytest.mark.parametrize("design, words", [
    ((line("VT 14x83.9x3/4 FD WH"), line("SH 14x29.6x3/4 FIX LG")), "print 2 colours (Arctic White, Ore)"),
    ((line("VT 14x83.9x3/4 FD WH"), line("WS 24x60x1-1/8 SU")), "print 2 colours"),  # a second-colour top
    ((line("VT 14x83.9x3/4 FD WH"), line("SH 14x29.6x3/4 FIX MS")), "print 2 colours (Arctic White, Navy)"),
    ((line("VT 14x83.9x3/4 FD MS"),), "Navy can't be placed"),
    ((line("VT 14x83.9x3/4 FD QZ"),), "a code the rules don't know"),
    ((line("VT 14x83.9x3/4 FD IV"),), "SCHEMA names no Arauco template set"),
    ((line("Hanging Rod 30in"),), "no board row of the design prints a colour"),
])
def test_a_room_whose_colour_isnt_one_the_rules_build_could_not_verify(design, words: str) -> None:
    found = co.room_shade(design, design, branch="closet")
    assert found.status == "could not verify" and words in found.why and "QZ" not in found.why


@pytest.mark.parametrize("branch, words", [("garage", "garage template"), ("unclear", "closet or garage")])
def test_a_garage_or_unclear_design_could_not_verify(branch: str, words: str) -> None:
    found = co.room_shade(WHITE_ROOM, WHITE_ROOM, branch=branch)
    assert found.status == "could not verify" and words in found.why


# ----- the drawer front (RULES 230-233, SCHEMA 59)


def test_the_drawer_front_is_h_unless_every_front_is_gv_and_a_mixed_room_is_h_flagged() -> None:
    assert co.drawer_front(WHITE_ROOM).value == co.H_FRONT
    assert co.drawer_front([line("Hanging Rod 30in")]).value == co.H_FRONT  # no fronts: the default
    every = co.drawer_front([line("DW 6.2x24.5x3/4 GV FL WH", 3)])
    assert (every.value, every.status) == (co.VG_FRONT, "set")
    mixed = co.drawer_front([line("DW 6.2x24.5x3/4 GV FL WH", 6), line("DW 7.4x24.5x3/4 FL WH", 2)])
    assert (mixed.value, mixed.status) == (co.H_FRONT, "review") and "6 of the room's 8" in mixed.why
    settings = co.RoomSettings(hb.settings().materials, mixed)
    assert settings.reviews() == (f"room: drawer front: {mixed.why}",)


# ----- the run: lifted from the newest known-good job (SCHEMA 75-76, RULES 279)


def test_the_run_is_lifted_whole_from_the_newest_known_good_job_in_the_shade(tmp_path: Path) -> None:
    old = hb.job_folder(tmp_path, "Testerson_010126", modified=1700000000.0)
    new = hb.job_folder(tmp_path, "Testerson_020226", modified=1790000000.5)
    ore = hb.job_folder(tmp_path, "Testerson_030326", run=ORE_RUN, modified=1795000000.0)
    sources = co.KnownGood([old, ore, new])
    found = sources.find(hb.WHITE)
    lifted = found.value
    assert found.settled and lifted.job == new and lifted.room == "Room1.des" and lifted.modified == 1790000000.5
    assert lifted.run == hb.MATERIALS and lifted.sha256 == co.sha256(hb.MATERIALS)
    assert co.material_run((new / "Room1.des").read_bytes().decode("utf-8")) == lifted.run
    assert sources.find(ORE).value.job == ore and sources.find(hb.WHITE) is found  # each shade looked for once
    assert co.still_holds(lifted) is None


def test_the_job_being_built_is_never_its_own_source(tmp_path: Path) -> None:
    own = hb.job_folder(tmp_path, "Testerson_101426", modified=1799999999.0)
    other = hb.job_folder(tmp_path, "Testerson_020226", modified=1790000000.0)
    assert co.KnownGood([own, other], exclude=["testerson_101426"]).find(hb.WHITE).value.job == other
    alone = co.KnownGood([own], exclude=[own.name]).find(hb.WHITE)
    assert alone.status == "could not verify"
    assert alone.why == "no known-good job is built in Arctic White (WH) (SCHEMA 75-76)"


@pytest.mark.parametrize("make, counted", [
    (dict(room0=hb.material_run(co.FAMILY_IDS["Tafisa"], co.TEXTURES["LG"])), True),  # Room0's categories differ
    (dict(more={"Room2.des": hb.with_run(hb.template_room(), hb.MATERIALS).replace(
        b'IdTagCount="19"', b'IdTagCount="19" MatORId="3"')}), True),  # MatORId in a room file
    (dict(more={"Room2.des": hb.with_run(hb.template_room(), hb.material_run().replace(
        "Testerson 3", "Testerson cabinets"))}), True),  # its white rooms carry two runs
    (dict(more={co.LOCK_FILE: b""}), False),  # open in Mozaik
    (dict(modified=None), False),  # no ModifiedDate: no Mozaik job
])
def test_a_job_jims_standing_gate_or_the_rules_refuse_is_never_a_source(tmp_path: Path, make: dict,
                                                                        counted: bool) -> None:
    bad = hb.job_folder(tmp_path, "Testerson_090926", **{"modified": 1799000000.0, **make})
    good = hb.job_folder(tmp_path, "Testerson_020226", modified=1790000000.0)
    assert co.KnownGood([bad, good]).find(hb.WHITE).value.job == good
    alone = co.KnownGood([bad]).find(hb.WHITE)
    assert alone.status == "could not verify"
    assert ("1 job carries its run, and none passes Jim's standing material gate" in alone.why) == counted


@pytest.mark.parametrize("run", [
    hb.material_run({**co.FAMILY_IDS["Egger"], "3": "88"}),  # the blank template's cabinet (SCHEMA 82-84)
    hb.material_run(co.FAMILY_IDS["Tafisa"]),  # Classic White on Tafisa's templates: never swap a brand (RULES 213)
    hb.material_run().replace(co.TEXTURES["WH"], co.TEXTURES["LG"], 1),  # a second colour on one category
    hb.material_run().replace('PartType="Shelf"', 'PartType="Shelf" MatORId="5"', 1),
])
def test_a_room_whose_run_isnt_the_shades_is_never_a_source(tmp_path: Path, run: str) -> None:
    job = hb.job_folder(tmp_path, run=run)
    found = co.KnownGood([job]).find(hb.WHITE)
    assert found.status == "could not verify" and "carry its run" not in found.why


def test_a_room_holding_no_products_is_never_a_source(tmp_path: Path) -> None:
    job = hb.job_folder(tmp_path)
    text = hb.with_run(hb.template_room(), hb.MATERIALS).decode("utf-8")
    start, end = text.index("<Products>"), text.index("</Products>") + len("</Products>")
    for room in ("Room0.des", "Room1.des"):
        (job / room).write_bytes((text[:start] + "<Products />" + text[end:]).encode("utf-8"))
    assert co.KnownGood([job]).find(hb.WHITE).status == "could not verify"


def test_an_amwh_run_carries_no_colours_texture() -> None:
    amwh = co.Shade("WH", "AMWH", None)
    plain = hb.material_run(co.FAMILY_IDS["AMWH"], "Testerson White")
    assert co.run_problem(plain, amwh) is None
    assert "a colour's texture" in co.run_problem(hb.material_run(co.FAMILY_IDS["AMWH"], co.TEXTURES["WH"]), amwh)


# ----- settle: what the writer is given


def test_settle_gives_the_writer_a_lifted_run_and_the_drawer_front(tmp_path: Path) -> None:
    sources = co.KnownGood([hb.job_folder(tmp_path)])
    settings = co.settle(WHITE_ROOM, WHITE_ROOM, branch="closet", sources=sources)
    assert settings.materials.value.run == hb.MATERIALS and settings.drawer_front.value == co.H_FRONT
    ore = co.settle([line("VT 14x83.9x3/4 FD LG")], [], branch="closet", sources=sources)
    assert ore.materials.status == "could not verify" and "Ore (LG)" in ore.materials.why
    two = co.settle([line("VT 14x83.9x3/4 FD LG"), line("SH 14x29.6x3/4 FIX WH")], [], branch="closet",
                    sources=sources)
    assert two.materials.status == "could not verify" and "2 colours" in two.materials.why


# ----- provenance: NG Runner's own data, never git's


def test_which_job_each_rooms_colour_came_from_is_kept_in_the_state_table(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    store = open_store(tmp_path / "data" / "ngrunner.db")
    found = co.KnownGood([hb.job_folder(tmp_path / "jobs")]).find(hb.WHITE)
    settings = co.RoomSettings(found, hb.settings().drawer_front)
    co.record_sources(store, "Testerson_101426", [("Room1.des", settings), ("Room2.des", co.RoomSettings())], at=NOW)
    held = co.recorded_sources(store)
    assert held == {"Testerson_101426\\Room1.des": {
        "colour": "WH", "family": "Egger", "from_job": hb.JOB, "from_room": "Room1.des",
        "run_sha256": co.sha256(hb.MATERIALS), "at": "2026-11-01T09:00:00+00:00"}}


def test_the_textures_are_one_per_colour_code_the_rules_know() -> None:
    from ngrunner.convert import rules

    assert set(co.TEXTURES) == set(rules.COLOUR_BRANDS) and len(set(co.TEXTURES.values())) == len(co.TEXTURES)
