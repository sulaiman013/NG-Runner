"""
P5a's room writer on the real material, read-only: Jim's frozen jobs X, Y and Z written from the room model,
gated, compared by his own regression.py compare and checked by his own checkers, all run from an adopted
copy of his toolkit in the tools' Python; the first 26 walls' rooms, and which values block writing them;
and the ties to his scripts and SCHEMA (docs/decisions.md, "P5a builder, slice 2").

The frozen rooms' own panels stand in for the reading and their own chains for the plan, as slice 1 has them
(tools/reader_eval/built.py), and their own material run and drawer front for the room's colour, which
nothing in NG Runner reads yet. The template job's Room1 is read where Jim sent it (.local-fixtures\\answers*),
never copied into the repo. Rooms are written only into the test's own folders. Results measured on
2026-11-01 are pinned, so a regression fails, never fitted to. Real jobs are named by case letter only.
"""
from __future__ import annotations

import ast
import os
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

from helpers_toolkit import stage_tree, tools_python
from ngrunner import toolkit
from ngrunner.build import commit as cm
from ngrunner.build import gates as gt
from ngrunner.build import products as pr
from ngrunner.build import writer as wr
from ngrunner.build.desxml import attrs, start_tags
from ngrunner.clock import FakeClock
from tools.reader_eval import budget, built, cases, providers, run

pytestmark = pytest.mark.fixtures

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(os.environ.get("NGR_FIXTURES", ROOT / ".local-fixtures"))
NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
CLEAN = "Regression clean — nothing moved."


# ----- the material


@pytest.fixture(scope="module")
def adopted(tmp_path_factory: pytest.TempPathFactory) -> toolkit.Snapshot:
    """An adopted copy of Jim's toolkit, as NG Runner adopts one: his regression clean on that very copy."""
    python = tools_python()
    base = tmp_path_factory.mktemp("p5w")
    folder = base / "data" / "toolkit"
    tried = toolkit.try_adoption(stage_tree(base / "jims"), folder, adopted=None, clock=FakeClock(NOW),
                                 regress=lambda s: toolkit.run_regression(s, python=python))
    assert tried.outcome == toolkit.ADOPTED and tried.sha256, tried.words
    path = folder / tried.sha256
    return toolkit.Snapshot(path, tried.sha256, NOW, sum(1 for p in path.rglob("*") if p.is_file()))


@pytest.fixture(scope="module")
def jobs(adopted: toolkit.Snapshot) -> dict[str, Path]:
    """X, Y and Z: the frozen jobs in the adopted copy, in name order (cases.py)."""
    found = sorted(p for p in (adopted.path / "reference" / "regression").iterdir() if (p / "summary.pdf").is_file())
    return dict(zip("XYZ", found))


@pytest.fixture(scope="module")
def template(jobs: dict[str, Path]) -> wr.RoomTemplate:
    found = sorted(FIXTURES.glob(f"answers*/{wr.TEMPLATE_JOB}/mozaik/{wr.TEMPLATE_ROOM}"))
    if not found:
        pytest.skip("the template job isn't on this PC (.local-fixtures/answers*/Lux Closet Template Claude)")
    refs = [p.read_bytes() for job in jobs.values() for p in sorted((job / "rooms").glob("Room*.des"))]
    return wr.load_template(found[-1].read_bytes(), refs)


@pytest.fixture(scope="module")
def library(adopted: toolkit.Snapshot, template: wr.RoomTemplate) -> pr.Library:
    return pr.load_library(adopted.path / "templates", template.fm84)


@pytest.fixture(scope="module")
def world() -> run.World:
    if not (cases.reader_dir() / "cases" / "index.json").is_file():
        pytest.skip("no collected summaries: run python -m tools.reader_eval.run collect")
    try:
        return run.load()
    except cases.CaseError as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def frozen(world: run.World) -> list[tuple[built.RoomResult, list]]:
    crops = {c: p.crops for c, p in world.preps.items()}
    summaries = {c: cases.summary(c) for c in "XYZ"}
    results = built.build_frozen({c: r.pairs for c, r in world.pairings.items()}, crops, summaries,
                                 built.load_rooms("XYZ"))
    if {r.case for r in results} != {"X", "Y", "Z"}:
        pytest.skip("the frozen jobs are not all paired here")
    return [(r, [built.read_wall(c, None) for c in crops[r.case] if c.design == r.design]) for r in results]


def stand_in(job: Path, room: str) -> wr.RoomSettings:
    """The frozen room's own colour, standing in for what nothing reads yet: its material run, its drawer front."""
    text = (job / "rooms" / f"{room}.des").read_text(encoding="utf-8")
    start, end = text.find("<BaseMatTempSel"), text.rfind("</BaseMatTempSel>") + len("</BaseMatTempSel>")
    return wr.RoomSettings(text[start:end], re.search(r'NonColumnTopDrawer="([^"]*)"', text)[1])  # type: ignore[index]


@pytest.fixture(scope="module")
def written(frozen, jobs, template, library) -> dict[str, list[tuple[built.RoomResult, list, object]]]:
    out: dict[str, list] = {}
    for r, walls in frozen:
        room = wr.write_room(r.model, int(r.room.removeprefix("Room")), template, library, stand_in(jobs[r.case], r.room))
        out.setdefault(r.case, []).append((r, walls, room))
    return out


@pytest.fixture(scope="module")
def measured(adopted, jobs, world, written) -> dict[tuple[str, str], dict[str, gt.Measured]]:
    """Each written wall's drawing as Jim's elev_panels.py measures it, from the adopted copy."""
    python = tools_python()
    pages = {(c, cr.design, cr.wall): cr for c in "XZ" for cr in world.preps[c].crops}
    out = {}
    for case in "XZ":
        for r, _, room in written[case]:
            got = {}
            for s in r.model.schedules:
                if s.panels:
                    crop = pages[(case, r.design, s.wall)]
                    got[s.wall] = gt.measure_wall(adopted, jobs[case] / "summary.pdf", crop.page, crop.width_in,
                                                  crop.height_in, python=python)
            out[(case, r.room)] = got
    return out


# ----- frozen X, Y and Z


def test_x_and_z_are_written_and_y_is_blocked_by_the_values_it_could_not_verify(written) -> None:
    assert [type(room).__name__ for _, _, room in written["X"]] == ["WrittenRoom"]
    assert [type(room).__name__ for _, _, room in written["Z"]] == ["WrittenRoom"] * 4
    ((_, _, y),) = written["Y"]
    assert isinstance(y, wr.BlockedRoom) and y.file_name == "Room1.des"
    what = Counter(what for _, what, _ in y.blocked_by)
    # its run start (0.78 in out), its top line (2408 on the old Elev rule), two sides where geometry and
    # the drill codes differ, and its own material run, on the blank template's cabinet id 88 (SCHEMA 82-84)
    assert what == Counter({"x_mm": 7, "elev_mm": 4, "side": 2, "product": 2, "run start": 1, "materials": 1})
    assert all(not r.review for case in "XZ" for _, _, r in written[case])


def test_jims_regression_compare_reproduces_x_and_z_and_finds_y_not_rebuilt(adopted, jobs, written,
                                                                           tmp_path: Path) -> None:
    said = {}
    for case in "XYZ":
        folder = tmp_path / case
        folder.mkdir()
        for _, _, room in written[case]:
            if isinstance(room, wr.WrittenRoom):
                (folder / room.file_name).write_bytes(room.content)
        said[case] = toolkit.run_tool(adopted, "scripts/regression.py", ["compare", jobs[case].name, str(folder)],
                                      python=tools_python())
    assert (said["X"].exit_code, said["X"].stdout.strip()) == (0, CLEAN)
    assert (said["Z"].exit_code, said["Z"].stdout.strip()) == (0, CLEAN)
    assert (said["Y"].exit_code, said["Y"].stdout.strip()) == (1, "1. Room1.des: missing from the rebuild")


def test_every_gate_on_x_and_z_passes_but_gate_6_which_jims_script_cant_measure_here(
        written, template, library, measured) -> None:
    for case in "XZ":
        for r, walls, room in written[case]:
            gates = gt.gate_room(room, r.model, walls, template, library, measured[(case, r.room)])
            assert {g.name: g.status for g in gates.not_passed()} == {"gate 6": "could not verify"}, gt.report(gates)
            # this PC has no pdftoppm; given one, X's pages still have no wall box his script can find
            whys = gates.gate("gate 6").items
            assert whys and all("pdftoppm" in w or "no wall box" in w for w in whys), whys


def test_nothing_of_x_or_z_reaches_even_the_shadow_copy_while_gate_6_could_not_verify(
        written, template, library, measured, tmp_path: Path) -> None:
    shadow = cm.p5_shadow_roots(tmp_path / "data")
    for case in "XZ":
        rooms = [cm.Gated(room, gt.gate_room(room, r.model, walls, template, library, measured[(case, r.room)]))
                 for r, walls, room in written[case]]
        result = cm.commit_rooms(case, rooms, roots=shadow, interlock=lambda paths: None)
        assert not result.ok and "gate 6 could not verify" in result.reason
    assert not list(shadow.mozaik_shared.rglob("*"))


def test_jims_own_checkers_find_nothing_to_change_in_the_written_rooms(adopted, written, tmp_path: Path) -> None:
    """A second opinion from his scripts, on staged copies: every panel's part is the panel, every WM panel is
    on its room's top line, and every WM panel's banding is his rule."""
    paths = []
    for case in "XZ":
        for _, _, room in written[case]:
            path = tmp_path / f"{case}_{room.file_name}"
            path.write_bytes(room.content)
            paths.append(str(path))
    python = tools_python()
    part = toolkit.run_tool(adopted, "scripts/part_geom_check.py", paths, python=python)
    assert part.exit_code == 0 and part.stdout.count("every panel part matches its product's depth and height") == 5
    top = toolkit.run_tool(adopted, "scripts/top_line.py", ["--check", *paths], python=python)
    assert top.exit_code == 0 and "would change: 0 WM panels" in top.stdout
    notch = toolkit.run_tool(adopted, "scripts/notch_side.py", [paths[1]], python=python)
    assert notch.exit_code == 0 and "WRONG FACE" not in notch.stdout


# ----- the first 26 walls: what blocks writing their rooms


@pytest.fixture(scope="module")
def dev_rooms(world: run.World) -> list[built.RoomResult]:
    provider = providers.PROVIDERS["anthropic"](effort="high")
    result = run.read_walls(world, run.test_walls(world), run.worked_walls(world), provider, None,
                            budget.CostGuard(budget.DEFAULT_CAP_USD), answers=cases.reader_dir() / "answers",
                            call=False, say=lambda _: None)
    readings = [(p, r) for p, r in result.readings if not p.case.startswith("P")]
    if len(readings) < 26:
        pytest.skip("Claude's cached answers to the first 26 walls are not all here")
    crops = {c: p.crops for c, p in world.preps.items()}
    return built.build_read(readings, crops, {c: cases.summary(c) for c in world.preps}, built.load_rooms(world.preps))


CHAIN = {"position", "angle", "pos_mm", "joints"}
COLOUR = {"materials", "drawer front"}


def kind(what: str, why: str) -> str:
    if what in CHAIN:
        return "the plan"
    if what in COLOUR:
        return "the colour"
    return "a wall not read" if "aren't read" in why else built.reason(why)


def test_every_dev_room_waits_for_its_plan_and_its_colour_and_12_of_18_for_a_panel_value(
        dev_rooms, template, library) -> None:
    rooms = [wr.write_room(r.model, 1, template, library, wr.RoomSettings()) for r in dev_rooms]
    assert len(rooms) == 18 and all(isinstance(r, wr.BlockedRoom) for r in rooms)
    kinds = [Counter(kind(what, why) for _, what, why in r.blocked_by) for r in rooms]  # type: ignore[union-attr]
    assert all(k["the plan"] and k["the colour"] == 2 for k in kinds)
    by_room = Counter(name for k in kinds for name in k if name not in ("the plan", "the colour"))
    assert by_room == Counter({"run start": 8, "FM 84 or FM 90": 5, "a wall not read": 3,
                               "the reading's own doubt": 3, "top line": 1})  # measured 2026-11-01
    assert sum(1 for k in kinds if set(k) == {"the plan", "the colour"}) == 6


# ----- the ties to Jim's files


def _tree(adopted: toolkit.Snapshot, name: str) -> ast.Module:
    return ast.parse((adopted.path / "scripts" / name).read_text(encoding="utf-8"), filename=name)


def _top_level(tree: ast.Module) -> dict[str, ast.expr]:
    return {t.id: node.value for node in tree.body if isinstance(node, ast.Assign)
            for t in node.targets if isinstance(t, ast.Name)}


def test_the_notch_and_the_slant_are_wm_panel_and_rea_panel_pys(adopted) -> None:
    wm, rea = _top_level(_tree(adopted, "wm_panel.py")), _top_level(_tree(adopted, "rea_panel.py"))
    assert ast.literal_eval(wm["GROOVE_OFFSET"]) == ast.literal_eval(rea["GROOVE_OFFSET"]) == pr.GROOVE_OFFSET_MM
    assert ast.literal_eval(rea["SLANT_DX"]) == pr.SLANT_ALONG_MM and ast.literal_eval(rea["SLANT_DY"]) == pr.SLANT_INTO_MM


def test_each_product_is_the_file_his_scripts_take_it_from(adopted) -> None:
    wm, rea = _top_level(_tree(adopted, "wm_panel.py")), _top_level(_tree(adopted, "rea_panel.py"))
    files = [*ast.literal_eval(wm["VAR"]).values(), *ast.literal_eval(rea["FM"]).values(),
             *ast.literal_eval(rea["WM"]).values()]
    assert sorted(f + ".xml" for f in files) == sorted(pr.template_file(n) for n in pr.LEGAL if n != pr.FM_NAME)


def test_the_legal_wm_names_are_regression_pys(adopted) -> None:
    expr = _top_level(_tree(adopted, "regression.py"))["LEGAL_WM"]
    legal = eval(compile(ast.Expression(expr), "regression.py", "eval"), {"__builtins__": {}})  # his set, no names
    assert legal == set(pr.WM_NAMES) | set(pr.WM_REA_NAMES)


def test_the_wm_families_are_wm_ebs(adopted) -> None:
    pattern = ast.literal_eval(_top_level(_tree(adopted, "wm_eb.py"))["WM_FAMILIES"])
    assert all(bool(re.match(pattern, n)) == pr.is_wm(n) for n in pr.LEGAL)


def test_the_walls_and_joints_written_are_the_attribute_sets_of_jims_rooms(jobs, template) -> None:
    """SCHEMA 9-30: the template room's wall and the cloned joint have the attribute sets of every wall and
    joint in his frozen rooms, names and order."""
    walls, joints = set(), set()
    for job in jobs.values():
        for path in (job / "rooms").glob("Room*.des"):
            text = path.read_text(encoding="utf-8")
            walls |= {tuple(attrs(t)) for t in start_tags(text, "Wall")}
            joints |= {tuple(attrs(t)) for t in start_tags(text, "WallJoint")}
    assert walls == {template.wall_attrs} and joints == {template.joint_attrs}
    assert template.joint_attrs == ("Wall1", "Wall2", "IsInterior", "Wall1Corner", "Wall2Corner", "Wall2Along",
                                    "Wall2Front", "MiterBack")  # SCHEMA 20-21


def test_the_library_holds_all_16_products_from_the_adopted_copy(library, template) -> None:
    assert set(library.names()) == set(pr.LEGAL) and not library.problems
    assert template.source_lib == "NG Closets" and template.version == "15"
