"""
A made-up toolkit for the delivery's tests: a scripts folder holding stand-ins for Jim's takeoff.py,
parse_pulled.py and classify.py, with the interfaces ng_convert calls.

None of his code is here (the repo is public): the takeoff stand-in is written from NG Runner's own
mirror of his rules (ngrunner/convert/rules.py), which tests/test_convert_ties.py holds to his file;
the parse_pulled stand-in hands back the rows a test gives it; classify buckets everything third_party.
The real toolkit is used only by the tests marked fixtures.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType

from ngrunner.convert import rules
from ngrunner.convert.summary_lines import pulled_lines, read_pages


def takeoff_source(*, waste: float = rules.WASTE, extra_part: bool = False) -> str:
    """A stand-in takeoff.py: NG Runner's mirrored rules, in its own words. extra_part makes its
    takeoff count one board part more than it was given (an agreement that fails)."""
    kinds = "|".join(rules.TYPE_KINDS)
    return f'''"""A made-up stand-in for the toolkit's takeoff.py (tests only)."""
import math, re
WASTE = {waste!r}
DISREGARD = re.compile({rules.DISREGARD.pattern!r}, re.I)
TYPE = re.compile(r"^\\s*({kinds})\\b")
_RULE = {dict(rules.TAPE)!r}
_EDGES = {{"none": lambda a, b, wm: 0.0, "long_edge": lambda a, b, wm: b,
           "both_long_edges": lambda a, b, wm: 2 * b, "all_four": lambda a, b, wm: 2 * (a + b),
           "vertical": lambda a, b, wm: b + 2 * a if wm else b}}


def is_wm(tokens):
    return bool(re.search(r"\\b(FN|RN|LN)\\b", tokens))


def eb_lf(r):
    rule = _RULE.get(r["type"])
    return 0.0 if rule is None else _EDGES[rule](r["a_in"], r["b_in"], is_wm(r["tokens"])) / 12.0


def normalise(conv):
    out = []
    for p in conv.get("parts", []):
        a, b = p["ic_in"]
        thk = p.get("thick_in", 0.75)
        if p["type"] == "BP" and thk < {rules.BACK_PANEL_UNDER!r}:
            thk = {rules.BACK_PANEL_BUILT!r}
        out.append(dict(type=p["type"], qty=int(p["qty"]), a_in=float(a), b_in=float(b), thk_in=round(float(thk), 4),
                        tokens=" ".join(str(p.get(k, "")) for k in ("desc", "flags", "tokens")), garage=False))
    return out


def takeoff(parts, sheet_sqft=None):
    buckets = {{}}
    for r in parts:
        key = "%g" % ({rules.RAIL_COVER_BUILT!r} if r["type"] == "TR Rail Cover" else r["thk_in"])
        b = buckets.setdefault(key, dict(parts=0, sqft=0.0))
        b["parts"] += r["qty"]
        b["sqft"] += r["qty"] * r["a_in"] * r["b_in"] / 144.0
    for b in buckets.values():
        b["sqft"] = round(b["sqft"], 2)
        b["sheets"] = math.ceil(b["sqft"] * WASTE / (49 * 97 / 144.0))
    vt = [r for r in parts if r["type"] == "VT"]
    return dict(board_parts=sum(r["qty"] for r in parts) + {1 if extra_part else 0},
                buckets=dict(sorted(buckets.items())),
                edgeband_lf=round(sum(r["qty"] * eb_lf(r) for r in parts), 1),
                closet_vt=sum(r["qty"] for r in vt), closet_vt_wm=sum(r["qty"] for r in vt if is_wm(r["tokens"])),
                closet_vt_rad=sum(r["qty"] for r in vt if re.search(r"\\bRAD\\b", r["tokens"])))
'''


PARSE_PULLED = '''"""A made-up stand-in for the toolkit's parse_pulled.py (tests only): the rows a test left beside it."""
import json, os


def parse_pdf(path):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "parse_pulled_rows.json"), encoding="utf-8") as f:
        return json.load(f)
'''

CLASSIFY = '''"""A made-up stand-in for the toolkit's classify.py (tests only)."""


def classify(row):
    return "third_party", "made-up family", "bought, not cut"
'''


def rows_of(data: bytes, *, drop: int | None = None) -> list[dict]:
    """The PULLED rows of a summary as parse_pulled gives them (page counted from 0); drop leaves one
    row out, so a second reading disagrees."""
    rows = [dict(page=line.page, col=line.column, top=0.0, section=line.section, qty=line.qty, item=line.item,
                 description=line.desc) for line in pulled_lines(read_pages(data))]
    if drop is not None:
        del rows[drop]
    return rows


def toolkit(root: Path, data: bytes, *, takeoff: str | None = None, rows: list[dict] | None = None,
            without: tuple[str, ...] = ()) -> Path:
    """<root>/_NG_Automation/scripts with the three stand-ins (less those named in without), its
    reference folder, and the second reading's rows for `data`. Returns the scripts folder."""
    scripts = root / "_NG_Automation" / "scripts"
    scripts.mkdir(parents=True)
    (root / "_NG_Automation" / "reference" / "regression").mkdir(parents=True)
    files = {"takeoff.py": takeoff or takeoff_source(), "parse_pulled.py": PARSE_PULLED, "classify.py": CLASSIFY}
    for name, text in files.items():
        if name not in without:
            (scripts / name).write_text(text, encoding="utf-8")
    (scripts / "parse_pulled_rows.json").write_text(json.dumps(rows if rows is not None else rows_of(data) if data else []),
                                                     encoding="utf-8")
    return scripts


def load(path: Path, name: str) -> ModuleType:
    """A stand-in loaded as a module, for the tests that call the gates directly."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(script_text: str, scripts: Path) -> Path:
    path = scripts / "ng_convert.py"
    path.write_text(script_text, encoding="utf-8")
    return path


def clear(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)
