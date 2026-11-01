"""
NG Runner's converter on one IC summary, given back as the IC Conversion JSON in Jim's current shape.

Why it exists: Jim's chat reads each summary itself and types the parts into a per-job script (559
typed part lines in 15 of 24 of his ic_conversion scripts, docs/p2/build-spec.md 1.2). NG Runner's
converter (ngrunner/convert) reads the same summary through to a takeoff by code. This gives that
takeoff back in the shape his tools already read: his shared writer's v2 keys (wb_generic), BUDGET's
source block, and the unplaced list (ruling Q17b), so his workbook, his takeoff.py, panel_xcheck and
ic_code_check read it as they read the chat's.

What it says is NG Runner's takeoff, exactly. The takeoff is convert()'s, the very path NG Runner runs
on intake; the parts are the same rows that takeoff summed, each with the line it came from, and they
must add up to it bucket by bucket or nothing is given back. On top of the takeoff it adds only what
his shape holds and the takeoff doesn't: each part's millimetres, snapped as SCHEMA's "Snap
tolerances" says (VT depth and height and SH depth, nothing else); the walls the summary check read
from the WALL/AREA tables; every PULLED line in one bucket; and each bucket's tracker names by RULES
(convert/tracker_names.py), the names NG Runner's lock holds to the tracker's Pricing block, which this
can't read, so each says it must be checked there.

Never a guess (ruling R4). A line the takeoff could not verify is never a part: it is in "unplaced" and
"flags", and its PULLED line is bucketed "could not verify". A bucket with no sheet size has no sheet
count. A garage, mixed or unclear job, a summary that fails P1a's check, or one whose PROJECT NAME
isn't the job named is refused whole (ConversionRefused), in plain words.

What it never reads: page 1 beyond PROJECT NAME and the design list, and the Estimate page beyond its
Total (summary_lines). So the client, designer and address stay null, and the verticals and runs,
which come from the drawings, stay empty: "ng_convert.not_made" names them.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import pypdf

from ngrunner.convert import convert, rules, takeoff_branch
from ngrunner.convert.parts import BoardPart, Unplaced, board_part, settle_colours
from ngrunner.convert.summary_lines import (SummaryLine, SummaryUnreadable, design_lines, pulled_lines,
                                            read_pages)
from ngrunner.convert.takeoff import Bucket, Takeoff
from ngrunner.convert.tracker_names import Unnamed, bucket_names
from ngrunner.intake.summary_check import check_summary
from tools.workbook.sheets import COULD_NOT_VERIFY, unplaced_words

# ----- SCHEMA.md, "Snap tolerances (Takeoff)" (tests/test_workbook_ties.py holds these to his text)

SNAP_DEPTHS_MM = (254.0, 292.0, 302.0, 356.0, 406.5, 484.0, 558.8, 606.0, 762.0)
SNAP_GRID_MM, SNAP_GRID_OFFSET_MM = 32.0, 20.0      # heights: 32k + 20
SNAP_TOLERANCE_MM = 1.3
SNAPPED_DEPTH_KINDS = ("VT", "SH")                  # "Snap ONLY: VT depth, VT height, SH depth"
MM = 25.4
SQM_PER_SQFT = 0.09290304

TAPE_WIDTHS = {0.75: '15/16" / 1"', 1.0: '1-1/4" / 1-5/16"', 0.25: "none (BP)"}   # BUILD 104
THICKNESS_LABELS = {0.25: "1/4", 0.75: "3/4", 1.0: "1 in"}
TAPE_RULE = ("One long edge (IC's second dimension) on FM VT, SH, DK and TR Base; height + 2 x depth on"
             " a WM VT (FN, RN, LN); 2 x length on a TR Rail Cover; all four edges on DW, DR and WS;"
             " none on CK, FF Cleat and BP (takeoff.py's eb_lf).")
MATERIAL_RULE = ("Sheets by IC colour and built thickness from RULES' sheet table (NG Runner ruling R5:"
                 " the library is not read for sizes until Jim answers item 5). Arctic White (WH) is"
                 " Egger Classic White, or AMWH on the whole job when a white part is 1in or thicker"
                 " (RULES 214), its 1/4 backs then White 1/4 Vinyl at 49x97 (RULES 135).")
TRACKER_NOTE = "Each name must be in the tracker's Pricing exactly; NG Runner's lock checks it"
NOT_MADE = ("client", "designer", "address", "verticals", "runs", "each part's design and wall",
            "the estimate's lines other than its total")

Classify = Callable[[Mapping[str, Any]], tuple[str, str, str]]


class ConversionRefused(Exception):
    """Why nothing was converted, in plain words."""


_NOT_A_CLOSET = {
    "garage": "This IC summary is a garage job",
    "mixed": "This IC summary is a closet and garage job",
}


# ----- the conversion


def conversion(data: bytes, *, summary_name: str, job: str | None = None,
               classify: Classify | None = None) -> dict[str, Any]:
    """The IC Conversion JSON of one summary's bytes. job, when given, must be its PROJECT NAME.
    classify buckets the PULLED lines that aren't board (Jim's classify.classify, when it is there).
    Raises ConversionRefused, in plain words, whenever it converts nothing."""
    check = check_summary(data)
    if not check.ok:
        raise ConversionRefused("The IC summary did not pass its check: " + " ".join(check.missing)
                                + " Nothing was converted.")
    project = check.project_name
    if job is not None and job != project:
        raise ConversionRefused(f"The IC summary's PROJECT NAME is {project}, not {job}: nothing was"
                                " converted.")
    branch = takeoff_branch(check)
    if branch != "closet":
        start = _NOT_A_CLOSET.get(branch, "Closet or garage could not be verified from page 1's design"
                                          " categories")
        raise ConversionRefused(f"{start}: ng_convert converts closets only, until BUILD's garage rule"
                                " is ported (NG Runner ruling R4). Nothing was converted.")
    try:
        t = convert(data, branch=branch)
        pages = read_pages(data)
        lines = pulled_lines(pages)
        designs = design_lines(pages)
    except SummaryUnreadable as why:
        raise ConversionRefused(f"{why} Nothing was converted.") from why
    placed, unplaced, shares = _settled(lines, designs)
    _must_add_up(t, [p for p, _ in placed], unplaced)
    thick_white = rules.thick_white(p for p, _ in placed)
    buckets = [_bucket(b, [p for p, _ in placed], thick_white) for b in t.buckets]
    names = {(b["ic_colour"], b["thk_in"]): b["material"] for b in buckets}
    lost = [_unplaced(u) for u in unplaced]
    return {
        "schema_version": "2.0", "job": project, "client": None, "designer": None,
        "job_type": branch, "address": None, "designs": list(check.designs),
        "ic_color": ", ".join(sorted({p.colour for p, _ in placed})) or None,
        "material_rule": MATERIAL_RULE, "walls": _walls(check.details.get("designs", [])),
        "sheet_size_in": _sheet_size(buckets), "waste_factor": rules.WASTE,
        "total_board_parts": t.board_parts, "buckets": buckets,
        "edgeband": {"rows": [{"material": f"{b['material']} 1mm — {b['tape']}", "linear_feet": b["edgeband_lf"]}
                              for b in buckets], "rule": TAPE_RULE},
        "verticals": [], "runs": {},
        "estimate": {} if t.estimate_total is None else {"total": t.estimate_total},
        "flags": [f"{unplaced_words(u)}. Could not verify." for u in lost]
                 + [f"No sheet size for {b['thk_label']} {b['material']}: could not verify its sheets."
                    for b in buckets if b["sheets_with_waste"] is None],
        "parts": [_part(p, line, names[(p.colour, p.built_thk)]) for p, line in placed],
        "pulled_classified": _classified(lines, shares, classify),
        "source": {"summary_pdf": summary_name, "sha256_16": hashlib.sha256(data).hexdigest()[:16]},
        "unplaced": lost,
        "ng_convert": {"rules": rules.fingerprint(), "reader": f"pypdf {pypdf.__version__}",
                       "branch": branch, "complete": t.complete, "could_not_verify": len(t.could_not_verify),
                       "gate_pulled_equals_designs": t.gate_pulled_equals_designs,
                       "not_made": list(NOT_MADE)},
    }


def board_rows(data: bytes) -> Counter[tuple[int, str, tuple[Any, ...]]]:
    """Each board line of the PULLED list read from these bytes, as (qty, item, verdict): what a
    second reading of the same list must give."""
    return board_verdicts(pulled_lines(read_pages(data)))


def board_verdicts(lines: Iterable[SummaryLine]) -> Counter[tuple[int, str, tuple[Any, ...]]]:
    """(qty, item, verdict) for every line the rules take as board or could not verify. The verdict
    is the part's kind, sizes, thicknesses, colour, mount and codes, or could not verify and why."""
    out: Counter[tuple[int, str, tuple[Any, ...]]] = Counter()
    for line in lines:
        found = board_part(line)
        if isinstance(found, BoardPart):
            out[(line.qty, line.item, ("board", found.kind, found.a_in, found.b_in, found.printed_thk,
                                       found.built_thk, found.colour, found.wall_mount, found.codes))] += 1
        elif isinstance(found, Unplaced):
            out[(line.qty, line.item, (COULD_NOT_VERIFY, found.reason))] += 1
    return out


# ----- the takeoff's own rows, each with its line


@dataclass
class _Share:
    """Part of one PULLED line: so many pieces, placed as board or not, and why."""

    qty: int
    state: str              # "board" | COULD_NOT_VERIFY
    note: str
    kind: str = ""


def _settled(lines: Sequence[SummaryLine], designs: Mapping[str, Sequence[SummaryLine]]
             ) -> tuple[list[tuple[BoardPart, SummaryLine]], list[Unplaced], dict[int, list[_Share]]]:
    """convert()'s own steps, keeping each part's line: its board parts in the takeoff's order, its
    Unplaced, and per PULLED line (by index) what became of its pieces."""
    judged = [(line, found) for line in lines if (found := board_part(line)) is not None]
    items = settle_colours(judged, designs)
    index = {id(line): i for i, line in enumerate(lines)}
    owner = {id(found): line for line, found in judged}
    pending: dict[str, deque[list[Any]]] = defaultdict(deque)     # desc -> [line, pieces left]
    kept = {id(item) for item in items}
    for line, found in judged:
        if isinstance(found, BoardPart) and id(found) not in kept:
            pending[line.desc.strip()].append([line, line.qty])
    placed: list[tuple[BoardPart, SummaryLine]] = []
    unplaced: list[Unplaced] = []
    shares: dict[int, list[_Share]] = defaultdict(list)
    for item in items:
        line = owner.get(id(item))
        is_board = isinstance(item, BoardPart)
        state = "board" if is_board else COULD_NOT_VERIFY
        note = _board_note(item) if isinstance(item, BoardPart) else item.reason
        kind = item.kind if isinstance(item, BoardPart) else ""
        if line is not None:                                     # the line's own verdict
            shares[index[id(line)]].append(_Share(line.qty, state, note, kind))
            if isinstance(item, BoardPart):
                placed.append((item, line))
            else:
                unplaced.append(item)
            continue
        desc = (item.line.desc if isinstance(item, Unplaced) else _desc_of(item, judged)).strip()
        for line, qty in _pour(pending[desc], item.qty if is_board else item.line.qty):
            shares[index[id(line)]].append(_Share(qty, state, note, kind))
            if isinstance(item, BoardPart):
                placed.append((_with_qty(item, qty), line))
            else:
                unplaced.append(Unplaced(_line_qty(line, qty), item.reason))
    return placed, unplaced, shares


def _pour(queue: deque[list[Any]], qty: int) -> list[tuple[SummaryLine, int]]:
    """A settled share of rows printed alike, given back to the lines it came from, in order."""
    out = []
    while qty:
        if not queue:
            raise ConversionRefused("NG Runner's converter could not tie a part back to its line, so"
                                    " nothing was converted.")
        entry = queue[0]
        taken = min(qty, entry[1])
        out.append((entry[0], taken))
        entry[1] -= taken
        qty -= taken
        if not entry[1]:
            queue.popleft()
    return out


def _desc_of(part: BoardPart, judged: Sequence[tuple[SummaryLine, Any]]) -> str:
    for line, found in judged:
        if (isinstance(found, BoardPart) and (found.kind, found.a_in, found.b_in, found.printed_thk,
                                              found.codes, found.source, found.wall_mount)
                == (part.kind, part.a_in, part.b_in, part.printed_thk, part.codes, part.source, part.wall_mount)):
            return line.desc
    raise ConversionRefused("NG Runner's converter could not tie a part back to its line, so nothing"
                            " was converted.")


def _with_qty(part: BoardPart, qty: int) -> BoardPart:
    return replace(part, qty=qty)


def _line_qty(line: SummaryLine, qty: int) -> SummaryLine:
    return replace(line, qty=qty)


def _must_add_up(t: Takeoff, parts: Sequence[BoardPart], unplaced: Sequence[Unplaced]) -> None:
    """The rows given back must be the takeoff's, bucket by bucket, or nothing is."""
    grouped: dict[tuple[str, float], list[BoardPart]] = defaultdict(list)
    for p in parts:
        grouped[(p.colour, p.built_thk)].append(p)
    same = (sum(p.qty for p in parts) == t.board_parts and len(unplaced) == len(t.unplaced)
            and sorted(grouped) == [(b.colour, b.thk_in) for b in t.buckets]
            and all(sum(p.qty for p in grouped[(b.colour, b.thk_in)]) == b.parts
                    and abs(sum(p.sqft for p in grouped[(b.colour, b.thk_in)]) - b.sqft) <= 0.006
                    and abs(sum(p.tape_ft for p in grouped[(b.colour, b.thk_in)]) - b.tape_lf) <= 0.06
                    for b in t.buckets))
    if not same:
        raise ConversionRefused("NG Runner's converter gave two different answers for this summary, so"
                                " nothing was converted.")


# ----- one part, as his v2 parts hold it


def _part(p: BoardPart, line: SummaryLine, material: str) -> dict[str, Any]:
    built, review = snap(p.kind, p.a_in, p.b_in)
    desc = line.desc.strip()
    if p.source == "pulled_hardware":
        desc = f"{desc} (3/4 at 3.5x96, RULES 224)"
    return {"design": "", "wall": "", "type": p.kind, "qty": p.qty, "desc": desc,
            "tokens": " ".join(p.codes), "mount": ("WM" if p.wall_mount else "FM") if p.kind == "VT" else "",
            "radius": "RAD" in p.codes, "grain": "", "ic_in": [p.a_in, p.b_in],
            "ic_mm": [_mm(p.a_in), _mm(p.b_in)], "build_mm": built, "thick_in": p.built_thk,
            "material": material, "sqft": round(p.sqft, 3), "eb_lf": round(p.tape_ft, 2), "review": review}


def snap(kind: str, a_in: float, b_in: float) -> tuple[list[float], str]:
    """The built millimetres of one part and its Review words, as SCHEMA's snap tolerances say: a VT's
    depth and a SH's depth to the nearest standard depth, a VT's height to the nearest 32k + 20, each
    only within 1.3 mm; beyond it kept IC-exact and flagged Review. Everything else stays exact."""
    a, b = _mm(a_in), _mm(b_in)
    review = []
    if kind in SNAPPED_DEPTH_KINDS:
        nearest = min(SNAP_DEPTHS_MM, key=lambda d: abs(d - a))
        if abs(nearest - a) <= SNAP_TOLERANCE_MM:
            a = _whole(nearest)
        else:
            review.append(f"depth {a:g}mm is {abs(nearest - a):.1f}mm from the nearest standard depth")
    if kind == "VT":
        k = round((b - SNAP_GRID_OFFSET_MM) / SNAP_GRID_MM)
        grid = SNAP_GRID_MM * k + SNAP_GRID_OFFSET_MM
        if abs(grid - b) <= SNAP_TOLERANCE_MM:
            b = _whole(grid)
        else:
            review.append(f"height {b:g}mm is {abs(grid - b):.1f}mm off the 32mm grid")
    return [a, b], "; ".join(review) + (": kept IC-exact, Review" if review else "")


def _mm(inches: float) -> float:
    return round(inches * MM, 2)


def _whole(value: float) -> float:
    return int(value) if float(value).is_integer() else value


def _board_note(p: BoardPart) -> str:
    if p.source == "pulled_hardware":
        return "fabricated from the room's 3/4 at 3.5x96 (RULES 224)"
    if p.printed_thk and p.built_thk != p.printed_thk:
        return f"cut from sheet, built {THICKNESS_LABELS.get(p.built_thk, p.built_thk)}"
    return "cut from sheet"


# ----- the buckets


def material_name(colour: str, *, thick_white: bool) -> str:
    """NG Runner's name for a bucket's material (messages.templates.takeoff_material, which the tests
    hold this to): the brand when one IC colour maps to it, else the brand and the IC code."""
    brand = rules.job_brand(colour, amwh=thick_white)
    if brand is None:
        return f"IC colour {colour}"
    if brand == "AMWH":
        return f"AMWH (IC {colour})"
    many = sum(1 for other in rules.COLOUR_BRANDS.values() if other == brand) > 1
    return f"{brand} (IC {colour})" if many else brand


def _bucket(b: Bucket, parts: Sequence[BoardPart], thick_white: bool) -> dict[str, Any]:
    area = sum(p.sqft for p in parts if (p.colour, p.built_thk) == (b.colour, b.thk_in))
    sheet = list(b.sheet_in) if b.sheet_in else None
    if b.sheet_in and b.sheets_with_waste is not None:
        sheet_sqft = b.sheet_in[0] * b.sheet_in[1] / 144.0
        math_words = (f"ceil({b.sqft} x 1.10 / {sheet_sqft:.4f}) = ceil({b.sqft * rules.WASTE / sheet_sqft:.3f})"
                      f" = {b.sheets_with_waste}")
    else:
        math_words = f"{COULD_NOT_VERIFY}: RULES' sheet table gives no size for it here (ruling R5)"
    return {"material": material_name(b.colour, thick_white=thick_white), "ic_colour": b.colour,
            "thk_in": b.thk_in, "thk_label": THICKNESS_LABELS.get(b.thk_in, f"{b.thk_in:g}"),
            "parts": b.parts, "sqft": b.sqft, "sqm": round(area * SQM_PER_SQFT, 4),
            "calc_sheets_raw": b.calc_sheets_32, "sheets_with_waste": b.sheets_with_waste,
            "sheet_in": sheet, "sheet_math": math_words, "edgeband_lf": b.tape_lf,
            "tape": TAPE_WIDTHS.get(b.thk_in, "could not verify"), "tracker": _tracker(b, thick_white)}


def _tracker(b: Bucket, thick_white: bool) -> dict[str, Any]:
    """The bucket's tracker names by RULES (convert/tracker_names.py), the very names NG Runner's lock holds
    to the tracker's Pricing block, which this can't read; or why it has none."""
    named = bucket_names(b.colour, b.thk_in, amwh=thick_white, tape=b.tape_lf > 0)
    if isinstance(named, Unnamed):
        return {"sheet": None, "tape": None, "tape_colour": None, "could_not_verify": named.why, "note": TRACKER_NOTE}
    return {"sheet": named.sheet, "tape": named.tape or None, "tape_colour": named.tape_colour or None,
            "could_not_verify": None, "note": TRACKER_NOTE}


def _sheet_size(buckets: Sequence[Mapping[str, Any]]) -> str:
    sizes = {tuple(b["sheet_in"]) if b["sheet_in"] else None for b in buckets}
    return "49 x 97" if sizes == {(49.0, 97.0)} else "by bucket"


def _unplaced(u: Unplaced) -> dict[str, Any]:
    return {"page": u.line.page + 1, "qty": u.line.qty, "item": u.line.item,
            "description": u.line.desc.strip(), "reason": u.reason}


# ----- every PULLED line in one bucket


def _classified(lines: Sequence[SummaryLine], shares: Mapping[int, Sequence[_Share]],
                classify: Classify | None) -> list[dict[str, Any]]:
    """One row per PULLED line, or one per share of a line placed in part. Board and could-not-verify
    lines are the converter's verdict; the rest are Jim's classify's buckets, except that a line it
    calls board and the converter doesn't count is an open question (his "unclassified")."""
    out = []
    for i, line in enumerate(lines):
        base = {"section": line.section, "item": line.item, "description": line.desc.strip(),
                "price_key": f"{line.item} — {line.desc.strip()}", "page": line.page + 1, "col": line.column}
        if i in shares:
            for share in shares[i]:
                family = share.kind if share.state == "board" else ""
                out.append({**base, "qty": share.qty, "bucket": share.state, "family": family,
                            "class_note": share.note})
            continue
        if classify is None:
            bucket, family, note = "", "", "not classified: classify.py wasn't given"
        else:
            bucket, family, note = classify({"section": line.section, "qty": line.qty, "item": line.item,
                                             "description": line.desc.strip()})
            if bucket == "board":
                bucket, note = "unclassified", ("classify.py calls it board, and ng_convert doesn't count"
                                                " it as board: an open question")
        out.append({**base, "qty": line.qty, "bucket": bucket, "family": family, "class_note": note})
    return out


# ----- the walls the summary check read


_INCHES = re.compile(r'^(?P<whole>\d+(?:\.\d+)?)(?: (?P<num>\d+)/(?P<den>\d+))?"$')


def _walls(designs: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {d["name"]: [{"wall": w["name"], "len_in": _inches(w["width"]), "height_in": _inches(w["height"]),
                         "ang": None} for w in d.get("walls", [])] for d in designs}


def _inches(text: str) -> float | None:
    found = _INCHES.match(text.strip())
    if found is None:
        return None
    value = float(found["whole"])
    if found["num"]:
        value += int(found["num"]) / int(found["den"])
    return value
