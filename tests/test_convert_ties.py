"""
The tie to Jim's toolkit (ruling R2): rules.py mirrors his takeoff.py, material_lookup's colour
codes and RULES' sheet tables, and these tests fail the moment any value mirrored there moves in
his files. That is how his edits reach the converter (docs/p2/build-spec.md, sections 4 and 6.5).

His scripts are read through `ast`, never imported or run, and no line of his is copied here: the
repo is public. Each test skips when the toolkit isn't on this PC (NGR_TOOLKIT, or the copy in
.local-fixtures). When one fails, change rules.py to his new value, after reading what changed,
and never the other way round.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

from ngrunner.convert import rules

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(os.environ.get("NGR_FIXTURES", ROOT / ".local-fixtures"))
TOOLKIT = Path(os.environ.get("NGR_TOOLKIT", FIXTURES / "toolkit" / "20261031"))
SCRIPTS = TOOLKIT / "NG_Automation_scripts" / "scripts"
DOCS = TOOLKIT / "NG_Automation_docs"

pytestmark = pytest.mark.fixtures

# His tape expressions, as ast.unparse prints them, and the rule each one is in rules.TAPE.
_TAPE_EXPRESSIONS = {
    "0.0": "none",
    "2 * b / 12.0": "both_long_edges",
    "b / 12.0": "long_edge",
    "2 * (a + b) / 12.0": "all_four",
    "(b + 2 * a if is_wm(r['tokens']) else b) / 12.0": "vertical",
}


def _tree(name: str) -> ast.Module:
    path = SCRIPTS / name
    if not path.is_file():
        pytest.skip("no local toolkit: set NGR_TOOLKIT (docs/p2/build-spec.md, section 4)")
    return ast.parse(path.read_text(encoding="utf-8"), filename=name)


def _assigned(tree: ast.Module, name: str) -> ast.expr:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return node.value
    raise AssertionError(f"{name} is no longer assigned at the top of the file")


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    found = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)
    assert found is not None, f"{name}() is gone"
    return found


def _arithmetic(node: ast.expr) -> float:
    """A constant expression of numbers and + - * /, evaluated without eval()."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp):
        left, right = _arithmetic(node.left), _arithmetic(node.right)
        ops = {ast.Add: left + right, ast.Sub: left - right, ast.Mult: left * right}
        if isinstance(node.op, ast.Div):
            return left / right
        return ops[type(node.op)]
    raise AssertionError(f"not plain arithmetic: {ast.unparse(node)}")


def _regex(node: ast.expr) -> tuple[str, bool]:
    """(pattern, ignores case) of a re.compile(...) call."""
    assert isinstance(node, ast.Call) and ast.unparse(node.func) == "re.compile"
    pattern = node.args[0]
    assert isinstance(pattern, ast.Constant) and isinstance(pattern.value, str)
    flags = " ".join(ast.unparse(a) for a in node.args[1:])
    return pattern.value, "re.I" in flags


# ----- takeoff.py


def test_waste_is_his() -> None:
    assert _arithmetic(_assigned(_tree("takeoff.py"), "WASTE")) == rules.WASTE


def test_his_fallback_sheet_is_recorded_and_never_used() -> None:
    sqft = _arithmetic(_assigned(_tree("takeoff.py"), "SHEET_SQFT"))
    width, length = rules.TAKEOFF_PY_FALLBACK_SHEET
    assert sqft == width * length / 144.0
    assert rules.sheet_size("NV", 0.75) is None        # a colour with no row: no default (R4)


def test_disregard_is_his() -> None:
    pattern, ignores_case = _regex(_assigned(_tree("takeoff.py"), "DISREGARD"))
    assert (pattern, ignores_case) == (rules.DISREGARD.pattern, True)
    assert rules.DISREGARD.flags & re.IGNORECASE


def test_the_kind_list_is_his_in_his_order() -> None:
    pattern, _ = _regex(_assigned(_tree("takeoff.py"), "TYPE"))
    found = re.search(r"\(([^()]*)\)", pattern)
    assert found is not None, "TYPE no longer lists its kinds in one group"
    assert tuple(found[1].split("|")) == rules.TYPE_KINDS


def test_the_wall_mount_codes_are_his() -> None:
    strings = [n.value for n in ast.walk(_function(_tree("takeoff.py"), "is_wm"))
               if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert strings == [rules.WALL_MOUNT.pattern]


def test_every_tape_rule_is_his() -> None:
    body = _function(_tree("takeoff.py"), "eb_lf").body
    tape: dict[str, str] = {}
    fallback = None
    for node in body:
        if isinstance(node, ast.If):
            kinds = _kinds_tested(node.test)
            (ret,) = node.body
            assert isinstance(ret, ast.Return) and ret.value is not None
            expression = ast.unparse(ret.value)
            assert expression in _TAPE_EXPRESSIONS, f"a tape rule changed: {expression}"
            for kind in kinds:
                tape.setdefault(kind, _TAPE_EXPRESSIONS[expression])
        elif isinstance(node, ast.Return) and node.value is not None:
            fallback = ast.unparse(node.value)
    assert tape == rules.TAPE
    # His fallback gives a kind with no rule no tape; the converter places no such kind (HP, TR).
    assert fallback == "0.0"
    assert not (rules.BOARD_KINDS - set(rules.TAPE)) - {"HP"}


def _kinds_tested(test: ast.expr) -> list[str]:
    """The kinds an `if t in (...)` or `if t == '...'` names."""
    assert isinstance(test, ast.Compare) and ast.unparse(test.left) == "t", ast.unparse(test)
    (op,), (right,) = test.ops, test.comparators
    if isinstance(op, ast.Eq) and isinstance(right, ast.Constant):
        return [right.value]
    assert isinstance(op, ast.In) and isinstance(right, (ast.Tuple, ast.List)), ast.unparse(test)
    return [e.value for e in right.elts if isinstance(e, ast.Constant)]


def test_the_renames_are_his() -> None:
    renames = []
    for node in ast.walk(_function(_tree("takeoff.py"), "normalise")):
        if not (isinstance(node, ast.If) and len(node.body) == 1
                and isinstance(node.body[0], ast.Assign)
                and ast.unparse(node.body[0].targets[0]) == "t"):
            continue
        value = node.body[0].value
        assert isinstance(value, ast.Constant)
        tests = node.test.values if isinstance(node.test, ast.BoolOp) else [node.test]
        kind = word = ""
        for test in tests:
            assert isinstance(test, ast.Compare)
            right = test.comparators[0]
            if isinstance(test.ops[0], ast.Eq) and ast.unparse(test.left) == "t":
                kind = right.value if isinstance(right, ast.Constant) else ""
            elif isinstance(test.ops[0], ast.In) and isinstance(test.left, ast.Constant):
                word = test.left.value
        renames.append((kind, word, value.value))
    assert tuple(renames) == rules.RENAMES


def test_back_panels_under_half_an_inch_are_built_quarter_as_his() -> None:
    found = []
    for node in ast.walk(_function(_tree("takeoff.py"), "normalise")):
        if (isinstance(node, ast.If) and "'BP'" in ast.unparse(node.test)
                and "thk <" in ast.unparse(node.test)):
            limit = next(c.comparators[0] for c in ast.walk(node.test)
                         if isinstance(c, ast.Compare) and isinstance(c.ops[0], ast.Lt))
            built = node.body[0]
            assert isinstance(built, ast.Assign)
            found.append((_arithmetic(limit), _arithmetic(built.value)))
    assert found == [(rules.BACK_PANEL_UNDER, rules.BACK_PANEL_BUILT)]


def test_a_rail_cover_buckets_at_three_quarter_and_sheets_are_rounded_then_ceiled_as_his() -> None:
    source = ast.unparse(_function(_tree("takeoff.py"), "takeoff"))
    found = re.search(r"thk = ([\d.]+) if r\['type'\] == 'TR Rail Cover' else r\['thk_in'\]", source)
    assert found is not None and float(found[1]) == rules.RAIL_COVER_BUILT
    assert "b['sqft'] = round(b['sqft'], 2)" in source
    assert "b['sheets'] = math.ceil(b['sqft'] * WASTE / sq)" in source


# ----- material_lookup.py: which brand each IC colour code is


def test_a_colour_no_material_carries_is_none_of_his_colours() -> None:
    """Navy (IC MS) maps to no material in his material_lookup (question 3). The day he adds one, it is a
    colour of his and leaves rules.NO_MATERIAL."""
    aliases = ast.literal_eval(_assigned(_tree("material_lookup.py"), "ALIASES"))
    assert rules.NO_MATERIAL == {"MS": "Navy"}
    assert not set(rules.NO_MATERIAL) & {code for code, _texture, _brand in aliases.values()}
    assert "navy" not in aliases


def test_the_colour_codes_and_brands_are_his() -> None:
    aliases = ast.literal_eval(_assigned(_tree("material_lookup.py"), "ALIASES"))
    brands = {}
    for code, texture, brand in aliases.values():
        if brand == "Egger":
            brand = ("Egger Classic White" if "Classic White" in texture
                     else "Egger Black U999" if "U999" in texture else f"Egger {texture}")
        brands[code] = brand
    assert brands == rules.COLOUR_BRANDS


# ----- RULES section 2's sheet tables


def test_the_sheet_sizes_are_rules_tables() -> None:
    path = DOCS / "RULES.md"
    if not path.is_file():
        pytest.skip("no local toolkit docs: set NGR_TOOLKIT")
    text = path.read_text(encoding="utf-8")
    section = text[text.index("## Sheet size"):text.index("## Colours are named")]
    found: dict[tuple[str, float], tuple[float, float]] = {}
    columns: tuple[float, ...] = ()
    for row in section.splitlines():
        if not row.startswith("|") or set(row) <= {"|", "-", " "}:
            continue
        cells = [c.strip() for c in row.strip("|").split("|")]
        heads = [_column_thickness(c) for c in cells[1:]]
        if all(h is not None for h in heads):
            columns = tuple(h for h in heads if h is not None)       # a header row
            continue
        brand = _brand(cells[0])
        for thk, cell in zip(columns, cells[1:]):
            size = re.search(r"(\d+)\s*[×x]\s*(\d+)", cell)
            if brand and size:
                found[(brand, thk)] = (float(size[1]), float(size[2]))
    assert found == rules.RULES_SHEETS


def _column_thickness(cell: str) -> float | None:
    return {"3/4": 0.75, "1 in": 1.0, "1/4 back": 0.25}.get(cell)


def _brand(cell: str) -> str | None:
    for words, brand in (("Classic White", "Egger Classic White"), ("Black U999", "Egger Black U999"),
                         ("Tafisa", "Tafisa"), ("Arauco", "Arauco"), ("AMWH", "AMWH")):
        if words in cell.replace("*", ""):
            return brand
    return None
