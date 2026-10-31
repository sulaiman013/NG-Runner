r"""
Scores the converter against what Jim's chat made from the same summary (dev only, read-only).

Why it exists: the converter is proven on real data, never fitted to it (docs/p2/build-spec.md,
sections 3.2 and 5.4). For each pair in .local-fixtures\p2\pairs (tools\collect_p2_fixtures.py),
it converts the summary and compares named fields only with the chat's IC Conversion JSON: board
parts, area by thickness, tape, the estimate total, the design count and the colour buckets. It is
also the P3 exit "takeoff equals the chat's on 10 jobs".

Usage:  python tools\score_takeoff.py [--json <out file outside the repo>]

The pairs are read from NGR_FIXTURES (AGENTS.md: Fixtures), .local-fixtures by default.

It prints pair indexes, codes and numbers only, never a job's or a client's name. A JSON's shape
is read as the heads-up reads it (list buckets, edgeband rows), plus the older dict and takeoff
shapes; a field it can't find is "not scored", never a miss.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ngrunner.convert import convert, takeoff_branch  # noqa: E402 - after the path is set
from ngrunner.convert.summary_lines import SummaryUnreadable, design_lines, read_pages  # noqa: E402
from ngrunner.convert.takeoff import Takeoff  # noqa: E402
from ngrunner.intake.summary_check import check_summary  # noqa: E402

PAIRS = Path(os.environ.get("NGR_FIXTURES", ROOT / ".local-fixtures")) / "p2" / "pairs"
SQFT_TOLERANCE = 0.05
TAPE_TOLERANCE = 0.2

# The chat's material words -> IC colour code (RULES 150-160 and material_lookup's aliases).
_MATERIAL_CODES = (
    (r"classic white|white 1/4 vinyl|am lam wh|amwh", "WH"), (r"apris ski|new natural", "AP"),
    (r"spring blossom|\bore\b", "LG"), (r"white choc|morning mist", "DA"),
    (r"black u999|charcoal", "CC"), (r"southwester|\bfort\b", "SW"),
    (r"sunday brunch|interlochen", "SU"), (r"casting at first light|warm sand|m2007", "MJ"),
    (r"moonlight|blue stone", "ML"), (r"prism|eggshell ivory", "IV"),
    (r"winter fun|houghton", "WF"), (r"river rock", "RR"), (r"weekend|marquette", "WG"),
)


@dataclass(frozen=True)
class Measure:
    ours: Any
    theirs: Any
    ok: bool | None          # None: the chat's file doesn't hold this field


@dataclass(frozen=True)
class Score:
    parts: Measure
    sqft_by_thk: dict[str, Measure]
    area: Measure
    tape: Measure
    estimate: Measure
    designs: Measure
    buckets: dict[str, Measure]


# ----- the chat's file, by shape


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            return float(value.replace("$", "").replace(",", "").strip())
        except ValueError:
            return None
    return None


def chat_parts(data: dict) -> float | None:
    total = number(data.get("total_board_parts"))
    if total is not None:
        return total
    takeoff = data.get("takeoff")
    if isinstance(takeoff, list) and takeoff:
        pieces = [number(t.get("pieces")) for t in takeoff if isinstance(t, dict)]
        if pieces and None not in pieces:
            return sum(pieces)                                    # type: ignore[arg-type]
    if isinstance(takeoff, dict) and number(takeoff.get("parts")) is not None:
        return number(takeoff.get("parts"))
    return None


def _thk_key(value: Any) -> str | None:
    found = number(value)
    if found is None and isinstance(value, str):
        pair = re.match(r"(\d+)[-/](\d+)", value)
        found = int(pair[1]) / int(pair[2]) if pair else None
    return None if found is None else f"{found:g}"


def chat_buckets(data: dict) -> list[dict] | None:
    """[{code, thk, parts, sqft, sheets, raw, tape}] from list buckets, dict buckets or takeoff."""
    found = data.get("buckets")
    rows: list[dict] = []
    if isinstance(found, list):
        for b in found:
            if isinstance(b, dict) and _thk_key(b.get("thk_in")) is not None:
                rows.append(dict(code=_code(b.get("material")), thk=_thk_key(b.get("thk_in")),
                                 parts=number(b.get("parts")), sqft=number(b.get("sqft")),
                                 sheets=number(b.get("sheets_with_waste")),
                                 raw=number(b.get("calc_sheets_raw")),
                                 tape=number(b.get("edgeband_lf"))))
    elif isinstance(found, dict):
        for key, b in found.items():
            thk = _thk_key(key) or (_thk_key(b.get("thk_in")) if isinstance(b, dict) else None)
            if thk is None and isinstance(key, str):
                thk = "1" if "1 in" in key else "0.25" if "1/4" in key else "0.75"
            if isinstance(b, dict):
                rows.append(dict(code=_code(key), thk=thk, parts=number(b.get("parts")),
                                 sqft=number(b.get("sqft")),
                                 sheets=number(b.get("sheets") or b.get("sheets_with_waste")),
                                 raw=None, tape=number(b.get("edgeband_lf"))))
            elif number(b) is not None:
                rows.append(dict(code=_code(key), thk=thk, parts=None, sqft=number(b),
                                 sheets=None, raw=None, tape=None))
    elif isinstance(data.get("takeoff"), dict):
        t = data["takeoff"]
        rows.append(dict(code=_code(t.get("material")), thk=_thk_key(t.get("thk_in")),
                         parts=number(t.get("parts")), sqft=number(t.get("sqft")),
                         sheets=number(t.get("sheets_with_waste")), raw=number(t.get("sheets_raw")),
                         tape=None))
    rows = [row for row in rows if row["thk"] is not None]
    return rows or None


def _code(material: Any) -> str | None:
    text = str(material or "").casefold()
    return next((code for pattern, code in _MATERIAL_CODES if re.search(pattern, text)), None)


def chat_tape(data: dict) -> float | None:
    found = data.get("edgeband")
    if isinstance(found, dict):
        rows = found.get("rows")
        if isinstance(rows, list) and rows:
            feet = [number(r.get("linear_feet")) for r in rows if isinstance(r, dict)]
            if feet and None not in feet:
                return sum(feet)                                  # type: ignore[arg-type]
        for key in ("linear_feet", "one_exposed_edge_lf", "lf", "recommended_lf"):
            if number(found.get(key)) is not None:
                return number(found[key])
    buckets = data.get("buckets")
    if isinstance(buckets, list):
        feet = [number(b.get("edgeband_lf")) for b in buckets if isinstance(b, dict)]
        if feet and None not in feet:
            return sum(feet)                                      # type: ignore[arg-type]
    for key in ("edgeband_lf", "edgeband_lf_estimate"):
        if number(data.get(key)) is not None:
            return number(data[key])
    return None


def chat_total(data: dict) -> float | None:
    estimate = data.get("estimate")
    if isinstance(estimate, dict):
        return number(estimate.get("total"))
    return number(data.get("estimate_total"))


# ----- scoring


def score(t: Takeoff, data: dict, *, designs: int | None = None) -> Score:
    """Our takeoff against the chat's file, on named fields only."""
    theirs_parts = chat_parts(data)
    parts = Measure(t.board_parts, theirs_parts,
                    None if theirs_parts is None else t.board_parts == theirs_parts)
    ours_thk: dict[str, float] = defaultdict(float)
    for b in t.buckets:
        ours_thk[f"{b.thk_in:g}"] += b.sqft
    theirs_rows = chat_buckets(data)
    theirs_thk: dict[str, float] = defaultdict(float)
    for row in theirs_rows or []:
        theirs_thk[row["thk"]] += row["sqft"] or 0.0
    by_thk = {k: Measure(round(ours_thk.get(k, 0.0), 2), round(theirs_thk.get(k, 0.0), 2),
                         abs(ours_thk.get(k, 0.0) - theirs_thk.get(k, 0.0)) <= SQFT_TOLERANCE)
              for k in sorted(set(ours_thk) | set(theirs_thk))} if theirs_rows else {}
    ours_area, theirs_area = sum(ours_thk.values()), sum(theirs_thk.values())
    area = Measure(round(ours_area, 2), round(theirs_area, 2) if theirs_rows else None,
                   abs(ours_area - theirs_area) <= SQFT_TOLERANCE if theirs_rows else None)
    theirs_tape = chat_tape(data)
    tape = Measure(t.tape_lf, theirs_tape,
                   None if theirs_tape is None else abs(t.tape_lf - theirs_tape) <= TAPE_TOLERANCE)
    theirs_total = chat_total(data)
    estimate = Measure(t.estimate_total, theirs_total,
                       None if theirs_total is None else t.estimate_total == theirs_total)
    listed = data.get("designs")
    theirs_designs = len(listed) if isinstance(listed, list) else None
    design_count = Measure(designs, theirs_designs,
                           None if theirs_designs is None or designs is None
                           else designs == theirs_designs)
    return Score(parts, by_thk, area, tape, estimate, design_count, _buckets(t, theirs_rows))


def _buckets(t: Takeoff, theirs: list[dict] | None) -> dict[str, Measure]:
    if not theirs or any(row["code"] is None for row in theirs):
        return {}
    merged: dict[str, dict] = {}
    for row in theirs:
        key = f"{row['code']} {row['thk']}"
        into = merged.setdefault(key, dict(parts=0.0, sqft=0.0, sheets=0.0))
        for field in ("parts", "sqft", "sheets"):
            into[field] = None if into[field] is None or row[field] is None \
                else into[field] + row[field]
    ours = {f"{b.colour} {b.thk_in:g}": dict(parts=b.parts, sqft=b.sqft,
                                             sheets=b.sheets_with_waste) for b in t.buckets}
    out = {}
    for key in sorted(set(merged) | set(ours)):
        mine, chat = ours.get(key), merged.get(key)
        same = bool(mine and chat and abs(mine["sqft"] - (chat["sqft"] or 0)) <= SQFT_TOLERANCE
                    and (chat["sheets"] is None or mine["sheets"] == chat["sheets"]))
        out[key] = Measure(mine, chat, same)
    return out


# ----- running it


def score_pair(index: int) -> dict[str, Any]:
    folder = PAIRS / str(index)
    data = (folder / "summary.pdf").read_bytes()
    check = check_summary(data)
    branch = takeoff_branch(check)
    t = convert(data, branch=branch)
    chat = json.loads((folder / "conversion.json").read_bytes().decode("utf-8-sig"))
    s = score(t, chat, designs=len(design_lines(read_pages(data))))
    return dict(index=index, branch=branch, complete=t.complete,
                unplaced_qty=sum(u.line.qty for u in t.unplaced),
                gate=t.gate_pulled_equals_designs, unplaced=[u.reason for u in t.unplaced],
                buckets=[asdict(b) for b in t.buckets], score=asdict(s))


def main(argv: list[str]) -> int:
    index = json.loads((PAIRS / "index.json").read_text(encoding="utf-8"))
    rows = []
    for i, entry in enumerate(index):
        try:
            row = score_pair(i)
        except SummaryUnreadable as exc:
            print(f"{i:2} {entry['place']:3} unreadable: {exc}")
            continue
        row["place"] = entry["place"]
        rows.append(row)
        s = row["score"]
        flags = " ".join(f"{name}={_mark(s[name]['ok'])}" for name in
                         ("parts", "area", "tape", "estimate", "designs"))
        print(f"{i:2} {row['place']:3} {row['branch']:7} {flags} parts {s['parts']['ours']}"
              f"/{s['parts']['theirs']} area {s['area']['ours']}/{s['area']['theirs']} tape"
              f" {s['tape']['ours']}/{s['tape']['theirs']} unplaced {len(row['unplaced'])}")
    for place in ("top", "old"):
        mine = [r for r in rows if r["place"] == place]
        for name in ("parts", "area", "tape", "estimate", "designs"):
            held = [r for r in mine if r["score"][name]["ok"] is not None]
            print(f"{place} {name}: {sum(r['score'][name]['ok'] for r in held)} of {len(held)}")
    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps(rows, indent=1),
                                                        encoding="utf-8")
    return 0


def _mark(ok: bool | None) -> str:
    return "-" if ok is None else "Y" if ok else "n"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
