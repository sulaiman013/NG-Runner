"""
P5a's room colour (ngrunner/build/colour.py) on the real material, read only: its ties to Jim's files (the
texture names his material_lookup.py maps, the template ids SCHEMA's table gives), each real design's colour as
the code settles it with its run lifted from every real Mozaik job here (helpers_colour.py), and the drawer-front
rule held to every room Jim built with an NG front. Measured 2026-11-01 and pinned, so a change fails, never
fitted to. Jobs are named by case letter, P-tag or a1 to a5 (Jim's answers jobs), never by name; the source a
colour came from is reported the same way.
"""
from __future__ import annotations

import ast
import html
import re
from collections import Counter

import pytest

import helpers_colour as hc
from helpers_toolkit import real_jobs, real_toolkit
from ngrunner.build import colour as co
from tools.reader_eval import cases

pytestmark = pytest.mark.fixtures


# ----- the ties to Jim's files


def test_the_textures_are_material_lookups_aliases() -> None:
    """Each IC colour code's library texture, his material_lookup.ALIASES, read by ast and never imported."""
    tree = ast.parse((real_toolkit() / "NG_Automation_scripts" / "scripts" / "material_lookup.py").read_text(
        encoding="utf-8"))
    (aliases,) = [node.value for node in tree.body if isinstance(node, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "ALIASES" for t in node.targets)]
    assert {code: texture for code, texture, _ in ast.literal_eval(aliases).values()} == co.TEXTURES


def test_the_family_ids_are_schemas_table() -> None:
    """SCHEMA's material table, column by column (Egger, the garage's AMWH, Tafisa), and its closet AMWH line."""
    schema = (real_toolkit() / "NG_Automation_docs" / "SCHEMA.md").read_text(encoding="utf-8")
    schema = schema[schema.index("## Material template IDs"):]
    schema = schema[:schema.index("\n## ")]
    rows = {"Cabinet": "3", "Doors": "1", "Drawers": "2", "Tops (Cat 4)": "4", "Banding": "5"}
    columns: dict[str, dict[str, str]] = {"Egger": {}, "AMWH": {}, "Tafisa": {}}
    for name, cat in rows.items():
        (row,) = [line for line in schema.splitlines() if line.startswith(f"| {name} |")]
        for family, found in zip(("Egger", "AMWH", "Tafisa"), re.findall(r"id \**(\d+)\**", row)):
            columns[family][cat] = found
    closet = re.search(r"Closet-in-AMWH set.*?cat3 id (\d+).*?cat5 id (\d+)", schema, re.S)
    assert closet is not None
    assert co.FAMILY_IDS["Egger"] == columns["Egger"] and co.FAMILY_IDS["Tafisa"] == columns["Tafisa"]
    assert co.FAMILY_IDS["AMWH"] == {**columns["AMWH"], "3": closet[1], "5": closet[2]}


# ----- every real design's colour


@pytest.fixture(scope="module")
def table() -> list[tuple[str, str | None, str | None, co.RoomSettings]]:
    """(case, shade key, source tag, settings) for every page-1 design of every summary collected here."""
    if not (cases.reader_dir() / "cases" / "index.json").is_file() or not real_jobs():
        pytest.skip("no collected summaries or no real jobs here")
    tags = {path: tag for tag, path in real_jobs().items()}
    out = []
    for case in cases.collected():
        found, lines, job = hc.designs(case)
        known = hc.sources(hc.own_job(case) or _frozen_name(case))
        for design in found:
            settings = hc.settle(case, design, known)
            mine = lines[design["name"]]
            shade = None if mine is None else co.room_shade(mine, job, branch=hc.branch_of([design]).kind).value
            lifted = settings.materials.value
            out.append((case, None if shade is None else shade.key,
                        tags.get(lifted.job) if isinstance(lifted, co.Lifted) else None, settings))
    return out


def _frozen_name(case: str) -> str | None:
    if case not in "XYZ":
        return None
    frozen = sorted(p for p in (real_toolkit() / "NG_Automation_reference" / "reference" / "regression").iterdir()
                    if (p / "summary.pdf").is_file())
    return dict(zip("XYZ", frozen))[case].name


def test_white_rooms_take_case_fs_run_and_charcoal_p01s_and_no_other_colour_has_a_known_good_job(table) -> None:
    """The colour table of 2026-11-01: 110 designs, 70 with their colour. Every white closet room takes Case F's
    run (Case F's own, P39's); Charcoal P01's. Every other colour's jobs here fail Jim's standing gate (Room0
    keeps a per-part override or the blank template's set) or are the room's own job."""
    assert len(table) == 110
    sources = Counter((key, tag) for _, key, tag, s in table if s.materials.settled)
    assert sources == Counter({("wh", "F"): 68, ("wh", "P39"): 1, ("cc", "P01"): 1})
    waiting = Counter(key for _, key, _, s in table if not s.materials.settled)
    assert waiting == Counter({None: 15, "ap": 5, "ml": 4, "mj": 3, "wf": 3, "wh_amwh": 3, "lg": 2, "cc": 1,
                               "da": 1, "rr": 1, "su": 1, "sw": 1})
    held = [s.materials.why for _, key, _, s in table if key in ("ap", "lg", "wh_amwh")]
    assert held and all("none passes Jim's standing material gate" in why for why in held)


def test_every_rooms_drawer_front_is_settled_but_those_whose_lines_cant_be_told(table) -> None:
    fronts = Counter((s.drawer_front.value, s.drawer_front.status) for *_, s in table)
    assert fronts == Counter({(co.H_FRONT, "set"): 96, (co.H_FRONT, "review"): 6, (co.VG_FRONT, "set"): 4,
                              (None, "could not verify"): 4})


def test_the_drawer_front_rule_gives_every_ng_front_jim_built_and_his_mixed_room_flagged() -> None:
    """Every real room Jim built on NG H or NG VG Drawer with pilot whose name is its design's: the rule gives
    his front on all 72, the one mixed room (6 GV fronts of 8) as H with its GV fronts flagged (RULES 233)."""
    if not (cases.reader_dir() / "cases" / "index.json").is_file():
        pytest.skip("no collected summaries")
    said: Counter[tuple[bool, str]] = Counter()
    flagged = []
    for case in cases.collected():
        try:
            rooms = cases.rooms(case)
        except cases.CaseError:
            continue
        found, lines, _ = hc.designs(case)
        by_name = {" ".join(d["name"].split()): d["name"] for d in found}
        for stem, raw in rooms.items():
            text = raw.decode("utf-8", "replace")
            name = re.search(r'<Room [^>]*?Name="([^"]*)"', text)
            front = re.search(r'NonColumnTopDrawer="([^"]*)"', text)
            if stem == "Room0" or not name or not front or front[1] not in co.FRONTS:
                continue
            design = by_name.get(" ".join(html.unescape(name[1]).split()))
            if design is None or lines[design] is None:
                continue
            got = co.drawer_front(lines[design])
            said[(got.value == front[1], got.status)] += 1
            if got.status == "review":
                flagged.append((case, stem, got.why))
    assert said == Counter({(True, "set"): 71, (True, "review"): 1})
    assert flagged == [("J", "Room1", "6 of the room's 8 drawer fronts are GV: a mixed room takes the default, its "
                                      "GV fronts flagged (RULES 233)")]
