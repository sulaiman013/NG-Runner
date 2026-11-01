"""
P5a's room writer on the real material, read-only: Jim's frozen jobs X, Y and Z written from the room model,
gated, compared by his own regression.py compare and checked by his own checkers, all run from an adopted
copy of his toolkit in the tools' Python; the first 26 walls' rooms, and which values block writing them;
and the ties to his scripts and SCHEMA (docs/decisions.md, "P5a builder, slice 2").

The frozen rooms' own panels stand in for the reading and their own chains for the plan, as slice 1 has them
(tools/reader_eval/built.py). Each room's colour is colour.py's (slice 3): its shade from its design's own
lines, its run lifted from the newest known-good job among every real job here (helpers_colour.py), never the
frozen room's own. The template job's Room1 is read where Jim sent it (.local-fixtures\\answers*),
never copied into the repo. Rooms are written only into the test's own folders. Results measured on
2026-11-01 are pinned, so a regression fails, never fitted to. Real jobs are named by case letter or P-tag only.
"""
from __future__ import annotations

import ast
import os
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

import helpers_colour as hc
from helpers_toolkit import real_jobs, stage_tree, tools_python
from ngrunner import toolkit
from ngrunner.build import commit as cm
from ngrunner.build import gates as gt
from ngrunner.build import products as pr
from ngrunner.build import writer as wr
from ngrunner.build.desxml import attrs, crlf, start_tags
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


def key_of(world: run.World, case: str, design: int) -> str:
    """The design's name_key_hash, from its crops: the design is never named."""
    (key,) = {c.design_key for c in world.preps[case].crops if c.design == design}
    return key


@pytest.fixture(scope="module")
def colours(frozen, jobs, world) -> dict[tuple[str, str], wr.RoomSettings]:
    """Each frozen room's colour as colour.py settles it, its own job left out of the known-good ones."""
    if not real_jobs():
        pytest.skip("no real Mozaik jobs here to lift a colour from")
    known = {case: hc.sources(jobs[case].name) for case in "XYZ"}
    return {(r.case, r.room): hc.colour_of(r.case, key_of(world, r.case, r.design), known[r.case]) for r, _ in frozen}


@pytest.fixture(scope="module")
def written(frozen, template, library, colours) -> dict[str, list[tuple[built.RoomResult, list, object]]]:
    out: dict[str, list] = {}
    for r, walls in frozen:
        room = wr.write_room(r.model, int(r.room.removeprefix("Room")), template, library, colours[(r.case, r.room)])
        out.setdefault(r.case, []).append((r, walls, room))
    return out


def renderer() -> Path | str:
    """Gate 6's pdftoppm: NGR_PDFTOPPM when set (as NG Runner's pdftoppm setting would name the pinned poppler that
    tools/make_tools_venv.py unpacks), else PATH, where this PC has none. Unset by default: running the downloaded
    program is for Sulaiman to allow (2026-11-01, docs/decisions.md "P5a builder, slice 3")."""
    given = os.environ.get("NGR_PDFTOPPM", "").strip()
    return gt.find_pdftoppm(Path(given) if given else None)


@pytest.fixture(scope="module")
def measured(adopted, jobs, world, written) -> dict[tuple[str, str], dict[str, gt.Measured]]:
    """Each written wall's drawing as Jim's elev_panels.py measures it, from the adopted copy."""
    python = tools_python()
    pdftoppm = renderer()
    pages = {(c, cr.design, cr.wall): cr for c in "XZ" for cr in world.preps[c].crops}
    out = {}
    for case in "XZ":
        for r, _, room in written[case]:
            got = {}
            for s in r.model.schedules:
                if s.panels:
                    crop = pages[(case, r.design, s.wall)]
                    got[s.wall] = gt.measure_wall(adopted, jobs[case] / "summary.pdf", crop.page, crop.width_in,
                                                  crop.height_in, pdftoppm=pdftoppm, python=python)
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
    # the drill codes differ, and its colour: Ore, in which no real job here passes Jim's standing gate
    assert what == Counter({"x_mm": 7, "elev_mm": 4, "side": 2, "product": 2, "run start": 1, "materials": 1})
    (why,) = [why for _, what, why in y.blocked_by if what == "materials"]
    assert why.startswith("no known-good job is built in Ore (LG) (SCHEMA 75-76): 3 jobs carry its run")
    assert all(not r.review for case in "XZ" for _, _, r in written[case])


def test_x_and_zs_colour_is_white_lifted_from_case_f_never_from_their_own_rooms(written, colours, jobs) -> None:
    from ngrunner.build import colour as co

    tags = {path: tag for tag, path in real_jobs().items()}
    for case in "XZ":
        for r, _, room in written[case]:
            settings = colours[(case, r.room)]
            lifted = settings.materials.value
            assert (lifted.shade, tags[lifted.job], lifted.room) == (co.Shade("WH", "Egger", "Classic White"), "F",
                                                                     "Room1.des")
            assert (settings.drawer_front.value, settings.drawer_front.status) == (co.H_FRONT, "set")
            assert co.material_run(room.content.decode("utf-8")) == lifted.run
            own = co.material_run((jobs[case] / "rooms" / f"{r.room}.des").read_text(encoding="utf-8"))
            its_own_job = lifted.job.name.casefold() == jobs[case].name.casefold()  # compared, never printed
            assert not its_own_job and crlf(own or "") == lifted.run  # the run Jim built it in, lifted from F


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


def test_every_gate_on_x_and_z_passes_but_gate_6_which_needs_poppler_and_x_s_pages_a_box(
        written, template, library, measured) -> None:
    """Without pdftoppm (this PC's suite, 2026-11-01) his script is never run and gate 6 could not verify on every
    wall. With the pinned poppler named, X's portrait pages still have no wall box his finder reads (Jim's question
    42), and Z's rooms are expected to pass every gate, as slice 2's probe measured Z's walls."""
    pdftoppm = renderer()
    for case in "XZ":
        for r, walls, room in written[case]:
            gates = gt.gate_room(room, r.model, walls, template, library, measured[(case, r.room)])
            assert gates.gate("colour").status == "passed" and len(gates.gates) == len(gt.REQUIRED) == 21
            whys = gates.gate("gate 6").items
            if isinstance(pdftoppm, str):
                assert {g.name: g.status for g in gates.not_passed()} == {"gate 6": "could not verify"}, gt.report(gates)
                assert pdftoppm == gt.NO_PDFTOPPM and whys and all(w.endswith(gt.NO_PDFTOPPM) for w in whys), whys
            elif case == "X":
                assert {g.name: g.status for g in gates.not_passed()} == {"gate 6": "could not verify"}, gt.report(gates)
                assert whys and all("a portrait page" in w for w in whys), whys
            else:
                assert gates.passed, gt.report(gates)


def test_a_room_reaches_the_shadow_copy_only_when_gate_6_passed_on_every_wall(
        written, template, library, measured, tmp_path: Path) -> None:
    shadow = cm.p5_shadow_roots(tmp_path / "data")
    poppler = isinstance(renderer(), Path)
    for case in "XZ":
        rooms = [cm.Gated(room, gt.gate_room(room, r.model, walls, template, library, measured[(case, r.room)]))
                 for r, walls, room in written[case]]
        result = cm.commit_rooms(case, rooms, roots=shadow, interlock=lambda paths: None)
        if poppler and case == "Z":  # into the test's own copy only
            assert result.ok, result.reason
            assert sorted(p.name for p in (shadow.mozaik_shared / "Jobs" / "Z").iterdir()) == [
                "Room1.des", "Room2.des", "Room3.des", "Room4.des"]
        else:
            assert not result.ok and "gate 6 could not verify" in result.reason
    if not poppler:
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


def test_every_dev_room_waits_for_its_plan_9_of_18_now_have_their_colour_and_4_wait_for_nothing_else(
        dev_rooms, world, template, library) -> None:
    tags = {path: tag for tag, path in real_jobs().items()}
    colours = [hc.colour_of(r.case, key_of(world, r.case, r.design), hc.sources(hc.own_job(r.case)))
               for r in dev_rooms]
    rooms = [wr.write_room(r.model, 1, template, library, s) for r, s in zip(dev_rooms, colours)]
    assert len(rooms) == 18 and all(isinstance(r, wr.BlockedRoom) for r in rooms)
    kinds = [Counter(kind(what, why) for _, what, why in r.blocked_by) for r in rooms]  # type: ignore[union-attr]
    assert all(k["the plan"] for k in kinds)
    # the colour: white from Case F on 8 rooms, from P39 on Case F's own; none for the rest (measured 2026-11-01)
    assert Counter(tags[s.materials.value.job] for s in colours if s.materials.settled) == Counter({"F": 8, "P39": 1})
    unsettled = Counter(colour_reason(s.materials.why) for s in colours if not s.materials.settled)
    assert unsettled == Counter({"no known-good job in AMWH": 3, "no known-good job in New Natural": 2,
                                 "no known-good job in Interlochen": 1, "no known-good job in Ore": 1,
                                 "three colours": 1, "a garage room": 1})
    by_room = Counter(name for k in kinds for name in k if name not in ("the plan", "the colour"))
    assert by_room == Counter({"run start": 8, "FM 84 or FM 90": 5, "a wall not read": 3,
                               "the reading's own doubt": 3, "top line": 1})  # as slice 2 measured them
    assert sum(1 for k in kinds if set(k) == {"the plan"}) == 4  # Jobs C (2), D and G


def colour_reason(why: str) -> str:
    for word in ("AMWH", "New Natural", "Interlochen", "Ore"):
        if why.startswith("no known-good job") and word in why:
            return f"no known-good job in {word}"
    return "three colours" if "print 3 colours" in why else "a garage room" if "garage" in why else why


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
