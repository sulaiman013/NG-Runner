r"""
Scores NG Runner's workbook against the one Jim's chat made from the same summary (dev only, read-only).

Why it exists: the workbook is proven on real data, never fitted to it. For each pair in
.local-fixtures\p2\pairs (tools\collect_p2_fixtures.py) it makes the conversion and its workbook from
the summary alone, reads the chat's workbook back with the standard library (xlsx.read), evaluates
its totals in Python (his files cache no values), and compares named cells only, never bytes: the
Parts total row, each part row, each bucket's block, the tape rows, the schedule block, the estimate
total, the walls and PULLED classified. A cell the chat's workbook doesn't hold, or holds in another
format, is "not scored", never a miss.

Usage:  python -m tools.workbook.score [--json <out file outside the repo>]

It prints pair indexes, IC colour codes, thicknesses, part kinds and numbers only: never a job's,
client's or design's name. The --json report holds the same.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.score_takeoff import _code  # noqa: E402 - the chat's material words -> IC colour code
from tools.workbook.conversion import ConversionRefused, conversion  # noqa: E402
from tools.workbook.sheets import PARTS, TAKEOFF, Book, Unevaluable, layout, named, value_of  # noqa: E402
from tools.workbook.xlsx import WorkbookUnreadable, read  # noqa: E402

PAIRS = Path(os.environ.get("NGR_FIXTURES", ROOT / ".local-fixtures")) / "p2" / "pairs"
SQFT, TAPE, MM = 0.05, 0.2, 0.15
FIELDS = ("board_parts", "sqft", "tape", "part_pieces", "built_mm", "buckets", "bucket_sqm",
          "bucket_calc_raw", "bucket_sheets", "edgeband", "schedule", "estimate", "walls", "pulled_pieces",
          "pulled_board", "material_names")


def his_format(book: Book) -> bool:
    """His shared writer's format: a Parts (mm) sheet headed as his, and the takeoff labels."""
    if not (book.has(PARTS) and book.has(TAKEOFF)):
        return False
    parts, takeoff = book.sheet(PARTS), book.sheet(TAKEOFF)
    headers = {parts.value(1, c) for c in range(1, parts.max_column + 1)}
    labels = {takeoff.value(r, 1) for r in range(1, takeoff.max_row + 1)}
    return {"Qty", "Type", "IC description", "IC a (in)", "IC b (in)"} <= headers and "  — SHEETS TO ORDER" in labels


def score_pair(i: int) -> dict[str, Any]:
    folder = PAIRS / str(i)
    data = (folder / "summary.pdf").read_bytes()
    try:
        conv = conversion(data, summary_name="summary.pdf")
    except ConversionRefused as exc:
        return {"index": i, "made": False, "why": _why(str(exc))}
    out: dict[str, Any] = {"index": i, "made": True, "complete": conv["ng_convert"]["complete"],
                           "could_not_verify": len(conv["unplaced"]) + sum(
                               b["sheets_with_waste"] is None for b in conv["buckets"])}
    chat_json = json.loads((folder / "conversion.json").read_bytes().decode("utf-8-sig"))
    try:
        theirs_book = read(folder / "conversion.xlsx")
    except (WorkbookUnreadable, FileNotFoundError):
        out["format"] = "none"
        return out
    out["format"] = "his" if his_format(theirs_book) else "other"
    if out["format"] != "his":
        older = older_board_parts(theirs_book)
        if older is not None:
            out["fields"] = {"board_parts": _same(float(conv["total_board_parts"]), older)}
        return out
    try:
        theirs = named(theirs_book)
    except Unevaluable:
        out["format"] = "unevaluable"
        return out
    ours = named(layout(conv))
    out["fields"] = compare(conv, ours, chat_json, theirs, theirs_book)
    return out


_TOTAL_WORDS = ("TOTAL BOARD PARTS", "TOTAL PIECES", "TOTAL BOARD")


def older_board_parts(book: Book) -> float | None:
    """The board-part total of a workbook from before his shared writer: a sheet named for parts, a
    Qty header in its first rows, and a row labelled TOTAL BOARD PARTS (or TOTAL PIECES) whose Qty
    cell is evaluated. None when any of these isn't there: not scored."""
    for sheet in book.sheets:
        if "parts" not in sheet.title.lower():
            continue
        qty = next(((r, c) for r in range(1, 4) for c in range(1, sheet.max_column + 1)
                    if sheet.value(r, c) == "Qty"), None)
        if qty is None:
            continue
        for r in range(qty[0] + 1, sheet.max_row + 1):
            words = [v for c in range(1, sheet.max_column + 1) if isinstance(v := sheet.value(r, c), str)]
            if any(w.upper().startswith(_TOTAL_WORDS) for w in words):
                try:
                    value = value_of(book, sheet.title, r, qty[1])
                except Unevaluable:
                    return None
                return float(value) if isinstance(value, (int, float)) else None
    return None


def _why(text: str) -> str:
    for key in ("garage", "did not pass its check", "PROJECT NAME", "could not be verified"):
        if key in text:
            return key
    return "other"


# ----- the comparison, field by field


def compare(conv: Mapping[str, Any], ours: Mapping[str, Any], chat: Mapping[str, Any],
            theirs: Mapping[str, Any], theirs_book: Book) -> dict[str, dict[str, Any]]:
    f: dict[str, dict[str, Any]] = {}
    f["board_parts"] = _same(ours.get("board_parts"), theirs.get("board_parts"))
    f["sqft"] = _near(ours.get("sqft"), theirs.get("sqft"), SQFT)
    f["tape"] = _near(ours.get("tape"), theirs.get("tape"), TAPE)
    mine_rows = Counter(_row_key(r) for r in ours.get("parts", []))
    their_rows = Counter(_row_key(r) for r in theirs.get("parts", []))
    f["part_rows"] = {"ok": mine_rows == their_rows, "ours": sum(mine_rows.values()),
                      "theirs": sum(their_rows.values())}                    # how they're grouped: not scored
    mine_pieces, their_pieces = _pieces(ours.get("parts", [])), _pieces(theirs.get("parts", []))
    f["part_pieces"] = {"ok": mine_pieces == their_pieces, "ours_only": _listed(mine_pieces - their_pieces),
                        "theirs_only": _listed(their_pieces - mine_pieces)}
    f["built_mm"] = _built(ours.get("parts", []), theirs.get("parts", []))
    f.update(_bucket_fields(conv, ours, chat, theirs))
    f["schedule"] = _same(_schedule(ours.get("schedule"), conv["buckets"]),
                          _schedule(theirs.get("schedule"), _their_buckets(chat)))
    f["estimate"] = _same(_money(ours.get("estimate_total")), _money(theirs.get("estimate_total")))
    f["walls"] = _same(_walls(layout(conv)), _walls(theirs_book))
    their_pulled = theirs.get("pulled")
    f["pulled_pieces"] = _same(ours["pulled"]["pieces"], their_pulled["pieces"]) if their_pulled else _none()
    f["pulled_board"] = _same(ours["pulled"]["board"], their_pulled["board"]) if their_pulled else _none()
    return f


def _same(a: Any, b: Any) -> dict[str, Any]:
    return {"ok": None if b is None else a == b, "ours": a, "theirs": b}


def _near(a: Any, b: Any, tolerance: float) -> dict[str, Any]:
    ok = None if b is None else (a is not None and abs(a - b) <= tolerance)
    return {"ok": ok, "ours": a, "theirs": b}


def _none() -> dict[str, Any]:
    return {"ok": None}


def _row_key(r: Mapping[str, Any]) -> tuple[Any, ...]:
    def n(v: Any) -> Any:
        return round(float(v), 2) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    return (r.get("type"), r.get("qty"), n(r.get("a_in")), n(r.get("b_in")))


def _pieces(rows: list[Mapping[str, Any]]) -> Counter:
    """Each part as so many pieces of a kind and IC size, however the rows group them."""
    out: Counter = Counter()
    for r in rows:
        out[(r.get("type"), _r(r.get("a_in")), _r(r.get("b_in")))] += int(r.get("qty") or 0)
    return out


def _money(value: Any) -> float | None:
    """A figure, or the same figure written as text ($1,916.26)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 2)
    if isinstance(value, str):
        try:
            return round(float(value.replace("$", "").replace(",", "").strip()), 2)
        except ValueError:
            return None
    return None


def _listed(counter: Counter) -> list[list[Any]]:
    return [[*key, count] for key, count in sorted(counter.items(), key=str)]


def _built(mine: list[Mapping[str, Any]], theirs: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Each of our rows against the chat's row of the same kind and IC size: the built mm."""
    pool = [dict(r) for r in theirs]
    differ, compared = [], 0
    for r in mine:
        match = next((t for t in pool if (t.get("type"), _r(t.get("a_in")), _r(t.get("b_in")))
                      == (r.get("type"), _r(r.get("a_in")), _r(r.get("b_in")))), None)
        if match is None:
            continue
        pool.remove(match)
        compared += 1
        a, b = (r.get("built_a"), r.get("built_b")), (match.get("built_a"), match.get("built_b"))
        if not all(isinstance(x, (int, float)) and isinstance(y, (int, float)) and abs(x - y) <= MM
                   for x, y in zip(a, b)):
            differ.append([r.get("type"), _r(r.get("a_in")), _r(r.get("b_in")), list(a), list(b),
                           bool(match.get("review"))])
    return {"ok": not differ if compared else None, "compared": compared, "differ": differ}


def _r(v: Any) -> Any:
    return round(float(v), 2) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


def _their_buckets(chat: Mapping[str, Any]) -> list[dict[str, Any]]:
    found = chat.get("buckets")
    return [b for b in found if isinstance(b, dict)] if isinstance(found, list) else []


def _bucket_fields(conv: Mapping[str, Any], ours: Mapping[str, Any], chat: Mapping[str, Any],
                   theirs: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Buckets keyed by IC colour code and thickness. Ours carry their code; the chat's blocks are
    in its JSON's bucket order (his writer writes them so), which gives each its material and
    thickness."""
    mine = {}
    for b in conv["buckets"]:
        block = ours["buckets"].get(f"{b['material']} {b['thk_label']}", {})
        tape = next((v for k, v in ours["edgeband"].items() if k.startswith(f"{b['material']} 1mm")), None)
        mine[(b["ic_colour"], float(b["thk_in"]))] = {**block, "tape": tape, "name": b["material"]}
    blocks = list(theirs.get("buckets", {}).values())
    tapes = list(theirs.get("edgeband", {}).values())
    chat_buckets = _their_buckets(chat)
    theirs_by: dict[tuple[str, float], dict[str, Any]] = {}
    if len(blocks) == len(chat_buckets):
        for k, (b, block) in enumerate(zip(chat_buckets, blocks)):
            key = (_code(b.get("material")), float(b.get("thk_in") or 0.0))
            into = theirs_by.setdefault(key, {"parts": 0, "sqft": 0.0, "sqm": 0.0, "calc_sheets_raw": 0.0,
                                              "sheets": 0, "tape": 0.0, "name": b.get("material")})
            for field in ("parts", "sqft", "sqm", "calc_sheets_raw", "sheets"):
                v = block.get(field)
                into[field] = into[field] + v if isinstance(v, (int, float)) and isinstance(into[field], (int, float)) else None
            into["tape"] = (into["tape"] or 0) + (tapes[k] if k < len(tapes) and isinstance(tapes[k], (int, float)) else 0)
    keys = sorted(set(mine) | set(theirs_by), key=str)
    fields: dict[str, dict[str, Any]] = {}
    if not theirs_by:
        return {name: _none() for name in ("buckets", "bucket_sqm", "bucket_calc_raw", "bucket_sheets",
                                           "edgeband", "material_names")}

    def per(field: str, tolerance: float | None) -> dict[str, Any]:
        rows, ok = [], True
        for key in keys:
            a, b = mine.get(key, {}).get(field), theirs_by.get(key, {}).get(field)
            same = (a == b) if tolerance is None else (isinstance(a, (int, float)) and isinstance(b, (int, float))
                                                       and abs(a - b) <= tolerance)
            ok &= bool(same)
            rows.append([key[0], key[1], a, b, bool(same)])
        return {"ok": ok, "rows": rows}

    fields["buckets"] = per("parts", None)
    sqft = per("sqft", SQFT)
    fields["buckets"]["ok"] = fields["buckets"]["ok"] and sqft["ok"]
    fields["buckets"]["sqft"] = sqft["rows"]
    fields["bucket_sqm"] = per("sqm", 0.005)
    fields["bucket_calc_raw"] = per("calc_sheets_raw", 0.0015)
    fields["bucket_sheets"] = per("sheets", None)
    mine_tape = sum(v for v in ours.get("edgeband", {}).values() if isinstance(v, (int, float)))
    their_tape = sum(v for v in theirs.get("edgeband", {}).values() if isinstance(v, (int, float)))
    fields["edgeband"] = _near(round(mine_tape, 1), round(their_tape, 1), TAPE)
    fields["material_names"] = per("name", None)
    return fields


_SCHEDULE = re.compile(r"^\s*(?P<n>\d+|could not verify) - (?P<thk>1/4|3/4|1 in|1)\b")


def _schedule(text: Any, buckets: list[Mapping[str, Any]]) -> list[list[Any]] | None:
    """The paste block as (sheets, thickness) in order, the names left out (they differ by design)."""
    if not isinstance(text, str):
        return None
    out = []
    for piece in text.split("|"):
        found = _SCHEDULE.match(piece)
        out.append([found["n"], found["thk"].replace("1 in", "1")] if found else ["?", piece.strip()[:6]])
    return sorted(out, key=str)


_WALL = re.compile(r'W(?P<n>[^ ]+) (?P<len>[\d.]+)" x (?P<h>[\d.]+)"')


def _walls(book: Book) -> list[list[Any]] | None:
    sheet = book.sheet(TAKEOFF)
    out, inside = [], False
    for r in range(1, sheet.max_row + 1):
        label, value = sheet.value(r, 1), sheet.value(r, 2)
        if label == "WALLS (IC WALL/AREA tables)":
            inside = True
            continue
        if inside and (not isinstance(label, str) or not label.startswith("  ")):
            break
        if inside and isinstance(value, str):
            out += [[m["n"], float(m["len"]), float(m["h"])] for m in _WALL.finditer(value)]
    return sorted(out, key=str) if out else None


# ----- running it


def main(argv: list[str]) -> int:
    index = json.loads((PAIRS / "index.json").read_text(encoding="utf-8"))
    rows = []
    for i, entry in enumerate(index):
        row = score_pair(i)
        row["place"] = entry["place"]
        rows.append(row)
        if not row["made"]:
            print(f"{i:2} {row['place']:3} not made: {row['why']}")
            continue
        state = "complete" if row["complete"] else f"incomplete ({row['could_not_verify']})"
        if row.get("format") != "his":
            older = row.get("fields", {}).get("board_parts")
            said = f"board parts {older['ours']:g}/{older['theirs']:g}" if older else "not scored"
            print(f"{i:2} {row['place']:3} {state:16} chat's workbook: {row['format']} format, {said}")
            continue
        marks = " ".join(f"{name}={_mark(row['fields'][name].get('ok'))}" for name in FIELDS)
        print(f"{i:2} {row['place']:3} {state:16} {marks}")
    scored = [r for r in rows if r.get("made") and r.get("complete") and r.get("format") == "his"]
    older = [r for r in rows if r.get("made") and r.get("complete") and r.get("format") == "other"]
    held = [r for r in older if r.get("fields", {}).get("board_parts", {}).get("ok") is not None]
    print(f"\ncomplete closets in an older format: {len(older)}; board parts equal on"
          f" {sum(bool(r['fields']['board_parts']['ok']) for r in held)} of the {len(held)} whose total"
          " row could be read")
    print(f"complete closets scored in his format: {len(scored)}")
    for name in FIELDS:
        held = [r for r in scored if r["fields"][name].get("ok") is not None]
        print(f"  {name}: {sum(bool(r['fields'][name]['ok']) for r in held)} of {len(held)}")
    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
    return 0


def _mark(ok: bool | None) -> str:
    return "-" if ok is None else "Y" if ok else "n"


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
