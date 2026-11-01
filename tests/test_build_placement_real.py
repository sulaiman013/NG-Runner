"""
P5a's placement rules on the real material, read-only: Claude's cached readings of the 147 evaluation
walls re-scored with the rules (tools/reader_eval/placed.py), Jim's three frozen jobs, and the ties to
his scripts. No model is called: a wall with no cached answer is not read. Counts measured on 2026-10-31
are pinned as ceilings, so a regression fails, never fitted to (docs/decisions.md, "P5a placement").

His scripts are read through `ast`, never imported or run, and none is named here: the per-job build
scripts are named for clients, and the repo is public.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from ngrunner.build import placement as pl
from tools.reader_eval import budget, cases, placed, providers, run

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
def done(world: run.World) -> list[placed.Placed]:
    provider = providers.PROVIDERS["anthropic"](effort="high")
    result = run.read_walls(world, run.test_walls(world), run.worked_walls(world), provider, None,
                            budget.CostGuard(budget.DEFAULT_CAP_USD), answers=cases.reader_dir() / "answers",
                            call=False, say=lambda _: None)
    if len(result.readings) < 147:
        pytest.skip("Claude's cached answers to the 147 walls are not all here")
    return placed.place_readings(result.readings, {c: p.crops for c, p in world.preps.items()})


# ----- before and after, on the 147 walls


def test_no_wall_and_no_value_is_newly_wrong(done: list[placed.Placed]) -> None:
    pooled = placed.tally(done)[0]
    assert pooled.newly_wrong_walls == [] and pooled.newly_wrong_values == 0


def test_on_the_first_26_walls_every_build_choice_left_is_on_a_wall_the_rules_stop(done: list[placed.Placed]) -> None:
    dev = [p for p in done if p.dev]
    pooled, cats, _, _ = placed.tally(dev)
    assert len(dev) == 26
    assert (pooled.build_choice_before, pooled.build_choice_after) == (33, 15)  # 2026-10-31
    assert pooled.silent_on_passed_after == 0
    assert cats["stacked tower"].build_choice_after == 0  # Job E's uppers stand on the counter


def test_on_the_121_new_walls_only_reading_and_rooms_cut_on_the_old_elev_rule_pass_unseen(
        done: list[placed.Placed]) -> None:
    test = [p for p in done if not p.dev]
    pooled = placed.tally(test)[0]
    assert len(test) == 121
    assert pooled.build_choice_after <= 70 and pooled.fixed_values >= 12  # 2026-10-31: 76 -> 70, 12 fixed
    assert pooled.silent_on_passed_after - pooled.of_which_cut_off_grid <= 5  # the reading's own errors
    assert pooled.could_not_verify <= 28


# ----- Jim's frozen jobs


@pytest.fixture(scope="module")
def frozen(world: run.World) -> dict[str, list[placed.FrozenRoom]]:
    rooms = placed.frozen_check({c: r.pairs for c, r in world.pairings.items()},
                                {c: p.crops for c, p in world.preps.items()})
    found: dict[str, list[placed.FrozenRoom]] = {}
    for room in rooms:
        found.setdefault(room.case, []).append(room)
    if set(found) != {"X", "Y", "Z"}:
        pytest.skip("the frozen jobs are not all paired here")
    return found


@pytest.mark.parametrize("job", ["X", "Z"])
def test_the_rules_give_back_the_frozen_top_lines_and_hung_panels(frozen: dict[str, list[placed.FrozenRoom]],
                                                                  job: str) -> None:
    for room in frozen[job]:
        assert room.top_line.value_mm is not None and room.frozen_tops_mm == (room.top_line.value_mm,)
        assert room.hung and all(room.hung) and room.hung_unverified == 0
        assert set(room.run_starts) == {"as drawn"}


def test_the_frozen_job_cut_on_the_old_elev_rule_could_not_verify(frozen: dict[str, list[placed.FrozenRoom]]) -> None:
    (room,) = frozen["Y"]
    assert room.top_line.status == "could not verify" and room.hung_unverified == 4
    assert room.run_starts == ("could not verify",)


# ----- the ties to Jim's files


def _tree(path: Path) -> ast.Module:
    if not path.is_file():
        pytest.skip("no local toolkit: set NGR_TOOLKIT")
    return ast.parse(path.read_text(encoding="utf-8"), filename=path.name)


def _top_level(tree: ast.Module) -> dict[str, ast.expr]:
    return {t.id: node.value for node in tree.body if isinstance(node, ast.Assign)
            for t in node.targets if isinstance(t, ast.Name)}


def test_the_top_lines_norm_and_its_1_in_are_top_line_pys() -> None:
    found = _top_level(_tree(SCRIPTS / "top_line.py"))
    assert ast.literal_eval(found["T"]) == pl.NORM_TOP_MM and ast.literal_eval(found["NEAR"]) == pl.NEAR_NORM_MM


def test_room_top_takes_the_highest_fm_top_above_the_lowest_wm_bottom() -> None:
    tree = _tree(SCRIPTS / "top_line.py")
    body = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "room_top")
    source = ast.unparse(body)
    assert "low = min((e for e, h in wm))" in source and "e + h > low" in source and "t = max(tall)" in source


def test_jims_stacked_build_stands_its_uppers_on_a_counter_of_19_304() -> None:
    if not SCRIPTS.is_dir():
        pytest.skip("no local toolkit: set NGR_TOOLKIT")
    tops = []
    for path in sorted(SCRIPTS.glob("build_*.py")):
        found = _top_level(_tree(path))
        if "EU" in found and ast.unparse(found["EU"]) == "HB + TOP":
            tops.append(ast.literal_eval(found["TOP"]))
    assert tops == [pl.COUNTER_MM]
