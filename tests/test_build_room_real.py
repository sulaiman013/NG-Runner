"""
P5a's room model on the real material, read-only: Jim's three frozen jobs rebuilt and compared as his own
regression compare does, the first 26 walls (Jobs A to J) built from Claude's cached readings, and the ties
to his scripts. No model is called: a wall with no cached answer is not read. Results measured on
2026-11-01 are pinned, ceilings for what is wrong or open, so a regression fails, never fitted to
(docs/decisions.md, "P5a builder, slice 1").

His scripts are read through `ast` or as text, never imported or run, and only the shared ones are named:
the per-job build scripts are named for clients, and the repo is public.
"""
from __future__ import annotations

import ast
import os
from collections import Counter
from pathlib import Path

import pytest

from ngrunner.build import schedule as sc
from ngrunner.build import walls as wl
from tools.reader_eval import budget, built, cases, providers, run

pytestmark = pytest.mark.fixtures

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(os.environ.get("NGR_FIXTURES", ROOT / ".local-fixtures"))
SCRIPTS = Path(os.environ.get("NGR_TOOLKIT", FIXTURES / "toolkit" / "20261031")) / "NG_Automation_scripts" / "scripts"


@pytest.fixture(scope="module")
def world() -> run.World:
    if not (cases.reader_dir() / "cases" / "index.json").is_file():
        pytest.skip("no collected summaries: run python -m tools.reader_eval.run collect")
    try:
        return run.load()
    except cases.CaseError as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def material(world: run.World) -> tuple[dict, dict, dict]:
    crops = {c: p.crops for c, p in world.preps.items()}
    return crops, {c: cases.summary(c) for c in world.preps}, built.load_rooms(world.preps)


@pytest.fixture(scope="module")
def frozen(world: run.World, material: tuple[dict, dict, dict]) -> dict[str, list[built.RoomResult]]:
    crops, summaries, rooms = material
    found: dict[str, list[built.RoomResult]] = {}
    for result in built.build_frozen({c: r.pairs for c, r in world.pairings.items()}, crops, summaries, rooms):
        found.setdefault(result.case, []).append(result)
    if set(found) != {"X", "Y", "Z"}:
        pytest.skip("the frozen jobs are not all paired here")
    return found


@pytest.fixture(scope="module")
def dev(world: run.World, material: tuple[dict, dict, dict]) -> list[built.WallResult]:
    provider = providers.PROVIDERS["anthropic"](effort="high")
    result = run.read_walls(world, run.test_walls(world), run.worked_walls(world), provider, None,
                            budget.CostGuard(budget.DEFAULT_CAP_USD), answers=cases.reader_dir() / "answers",
                            call=False, say=lambda _: None)
    readings = [(p, r) for p, r in result.readings if not p.case.startswith("P")]
    if len(readings) < 26:
        pytest.skip("Claude's cached answers to the first 26 walls are not all here")
    crops, summaries, rooms = material
    return [w for room in built.build_read(readings, crops, summaries, rooms) for w in room.panels]


# ----- Jim's frozen jobs, as his regression compare has them


@pytest.mark.parametrize("job, rooms, panels", [("X", 1, 14), ("Z", 4, 16)])
def test_frozen_x_and_z_reproduce_every_wall_and_every_panel(frozen: dict[str, list[built.RoomResult]], job: str,
                                                             rooms: int, panels: int) -> None:
    results = frozen[job]
    assert len(results) == rooms
    for r in results:
        assert r.walls == [] and not any(k.endswith("wrong") for k in r.wall_values)
        assert r.model.top_line.value_mm == 2132.0 and r.model.joints.settled
    found = [p for r in results for w in r.panels for p in w.panels]
    assert len(found) == panels and {p.status for p in found} == {"right"}
    assert all(v in ("right", "not needed") for p in found for v in p.values.values())


def test_frozen_z_chains_are_not_the_tables_order_so_the_plan_settles_them(
        world: run.World, frozen: dict[str, list[built.RoomResult]], material: tuple[dict, dict, dict]) -> None:
    _, summaries, rooms = material
    tables = built.tables(summaries["Z"])
    keys = {p.crop.design: p.crop.design_key for p in world.pairings["Z"].pairs}
    differ = [built.plan_of(rooms["Z"][r.room]).order != tuple(row.name for row in tables[keys[r.design]])
              for r in frozen["Z"]]
    assert differ.count(True) == 2  # two of its four rooms start their chain at a return numbered last


def test_frozen_y_built_before_todays_rules_could_not_verify(frozen: dict[str, list[built.RoomResult]]) -> None:
    """Y's top line is on the 32k+20 Elev rule RULES now calls an error, and its run starts 0.78 in out; its
    one wrong value is a panel today's geometry and its own RH line make Right, which Jim built Thru before
    the rule of 2026-09-21 (BUILD 260)."""
    (r,) = frozen["Y"]
    assert r.model.top_line.status == "could not verify"
    found = [p for w in r.panels for p in w.panels]
    assert Counter(p.status for p in found) == Counter({"could not verify": 6, "wrong": 1})
    (wrong,) = [p for p in found if p.status == "wrong"]
    assert {k for k, v in wrong.values.items() if v == "wrong"} == {"side", "product"}
    assert r.wall_values["chain shifted"] == 1  # Y's chain starts at (0, 12.7): gen_rooms.py 82-85 shifts it


# ----- the first 26 walls, from Claude's cached readings


def test_on_the_first_26_walls_nothing_is_settled_wrong(dev: list[built.WallResult]) -> None:
    panels = [p for w in dev for p in w.panels]
    assert len(dev) == 26 and len([p for p in panels if p.index is not None]) == 89
    assert Counter(p.status for p in panels) == Counter({"right": 50, "could not verify": 39})  # 2026-11-01


def test_on_the_first_26_walls_could_not_verify_is_the_run_start_the_top_line_and_fm_90(
        dev: list[built.WallResult]) -> None:
    reasons = built._reasons(dev)
    assert reasons == Counter({"x: run start": 21, "x: the reading's own doubt": 10, "elev: top line": 5,
                               "top: top line": 5, "product: FM 84 or FM 90": 13})


# ----- the ties to Jim's files


def _tree(name: str) -> ast.Module:
    path = SCRIPTS / name
    if not path.is_file():
        pytest.skip("no local toolkit: set NGR_TOOLKIT")
    return ast.parse(path.read_text(encoding="utf-8"), filename=name)


def _top_level(tree: ast.Module) -> dict[str, ast.expr]:
    return {t.id: node.value for node in tree.body if isinstance(node, ast.Assign)
            for t in node.targets if isinstance(t, ast.Name)}


def test_the_depth_grid_and_its_tolerance_are_build_verticals_pys() -> None:
    found = _top_level(_tree("build_verticals.py"))
    assert tuple(ast.literal_eval(found["GRID"])) == sc.DEPTH_GRID_MM and ast.literal_eval(found["TOL"]) == sc.DEPTH_TOL_MM


def test_the_6_and_a_half_inch_wall_is_swap_rea_pys() -> None:
    found = _top_level(_tree("swap_rea.py"))
    assert ast.literal_eval(found["WALL"]) == sc.WALL_NEAR_MM


def test_the_walls_are_gen_rooms_pys_table_inches_to_4_places_and_idtag_is_the_number() -> None:
    source = ast.unparse(_tree("gen_rooms.py"))
    assert "Lmm = round(Lin * MM, 4)" in source and "Hmm = round(Hin * MM, 4)" in source
    assert 'IDTag="{wn}"' in source and 'WallNumber="{wn}"' in source and 'Thickness="152.4"' in source


def test_the_comparison_tolerances_are_regression_pys_compare() -> None:
    tree = _tree("regression.py")
    body = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "compare")
    source = ast.unparse(body)
    assert f"> {built.JIM_MM}" in source and f"> {built.JIM_ANG}" in source


def test_clockwise_is_regression_pys_net_turn() -> None:
    tree = _tree("regression.py")
    body = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "gates")
    assert "(angs[k + 1] - angs[k] + 180) % 360 - 180" in ast.unparse(body)
    assert wl.net_turn((90.0, 0.0, 270.0)) < 0
