"""
ng_convert's own code: an IC summary to its IC Conversion, the .json and the .xlsx, from Jim's toolkit.

Why it exists: Jim's chat types each job's parts into a per-job script, and RULES section 8 says takeoff
rules live in takeoff.py only and new conversions import it. This is the script his chat runs instead:
NG Runner's converter (tools/workbook) reads the summary, and before anything is written it is checked
against his own scripts, which sit beside it in _NG_Automation\\scripts, on every run:

  - his takeoff.py's rules must still be the ones NG Runner's converter mirrors (tie). When he changes
    takeoff.py, ng_convert stops and says so, until Sulaiman makes a new one: never two rule sets;
  - his parse_pulled.py, which reads the PDF with pdfplumber, must read every board line of the PULLED
    list as pypdf does, by quantity, item and what the rules make of it (second_reading). Measured
    2026-10-31: the same on all 52 real pairs;
  - his takeoff.py's own reading of the parts it writes must give the same parts, area and tape
    (agree). That reading is kept in the JSON, as ng_convert.takeoff_py.

His classify.py buckets the PULLED lines that aren't board. Then the JSON and the workbook are written
to temporary names, the workbook is read back and checked in Python (tools/workbook/xlsx.py), and only
then are both put in place. Anything that stops it writes nothing and says why in plain words.

Never a guess: a line the takeoff could not verify is listed, never filled in, and the exit code says
so (NOT_COMPLETE). It never writes inside the toolkit, and never over a file unless told (--replace).
Exit codes: 0 written and complete, 3 written with lines it could not verify, 1 nothing written, 2 a
wrong command line.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from ngrunner.convert import rules

VERSION = "repo"           # the delivered file sets its own, before main runs
WRITTEN, REFUSED, BAD_COMMAND, NOT_COMPLETE = 0, 1, 2, 3
JIMS = ("takeoff", "parse_pulled", "classify")
CHECKED = ["takeoff.py's rules", "parse_pulled.py's reading of every board line", "takeoff.py's reading of these parts"]
_NOT_A_FILE_NAME = re.compile(r'[\\/:*?"<>|]')


class Refused(Exception):
    """Why nothing was written, in plain words."""


# ----- the command line


def main(argv: Sequence[str] | None = None, *, scripts: Path | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ng_convert.py", description="An IC summary to its IC Conversion (.json and .xlsx), by code.")
    parser.add_argument("summary", help="the IC summary PDF")
    parser.add_argument("--job", help="the job it must be: refused when PROJECT NAME differs")
    parser.add_argument("--out", help="the folder to write in (default: the summary's folder)")
    parser.add_argument("--replace", action="store_true", help="write over the two files when they are there")
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return BAD_COMMAND if stop.code else WRITTEN
    scripts = scripts or Path(sys.argv[0]).resolve().parent
    # pypdf logs what it repairs as it reads ("Xref table not zero-indexed"): noise for the chat (RULES
    # section 0), and nothing it reads goes unchecked. Its errors still show.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    summary = Path(args.summary).resolve()
    out = Path(args.out).resolve() if args.out else summary.parent
    try:
        refuse_inside(out, scripts.parent)
        libraries()
        conv = convert_summary(summary, job=args.job, scripts=scripts)
        json_path, _ = write(conv, out, replace=args.replace)
    except Refused as why:
        print(why)
        return REFUSED
    for line in said(conv, json_path):
        print(line)
    return WRITTEN if conv["ng_convert"]["complete"] else NOT_COMPLETE


def said(conv: Mapping[str, Any], json_path: Path) -> list[str]:
    """What it prints: where it wrote, and the takeoff, or each line it could not verify."""
    lines = [f"{conv['job']}: IC Conversion written: {json_path.name} and .xlsx, in {json_path.parent}."]
    flags = list(conv.get("flags") or [])
    if conv["ng_convert"]["complete"]:
        from tools.workbook.sheets import schedule
        tape = sum(b["edgeband_lf"] for b in conv["buckets"])
        lines.append(f"Takeoff complete: {conv['total_board_parts']} board parts. Sheets: {schedule(conv['buckets'])}."
                     f" Tape: {tape:.1f} ft.")
    else:
        lost = len(conv.get("unplaced") or [])
        unsized = sum(1 for b in conv["buckets"] if b["sheets_with_waste"] is None)
        what = [f"{_count(lost, 'line')} could not verify"] if lost else []
        what += [f"{_count(unsized, 'bucket')} {'has' if unsized == 1 else 'have'} no sheet size"] if unsized else []
        it = "it is" if lost + unsized == 1 else "none is"
        lines.append(f"Takeoff not complete: {' and '.join(what)}, and {it} never filled in:")
        lines += [f"{i}. {flag}" for i, flag in enumerate(flags, 1)]
    lines.append("Checked against your takeoff.py and parse_pulled.py before writing.")
    return lines


# ----- what it needs


def libraries() -> None:
    missing = [name for name in ("pypdf", "openpyxl") if importlib.util.find_spec(name) is None]
    if missing:
        verb = "isn't" if len(missing) == 1 else "aren't"
        raise Refused(f"{' and '.join(missing)} {verb} installed in this Python, so nothing was converted.")


def jims(scripts: Path) -> tuple[ModuleType, ...]:
    """takeoff.py, parse_pulled.py and classify.py from the scripts folder ng_convert sits in, loaded
    by path, so no other module of the same name can stand in for one."""
    loaded = []
    for name in JIMS:
        path = scripts / f"{name}.py"
        if not path.is_file():
            raise Refused(f"{name}.py isn't beside ng_convert.py: it runs from the toolkit's scripts folder"
                          " only. Nothing was converted.")
        spec = importlib.util.spec_from_file_location(f"_jims_{name}", path)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except ImportError as missing:
            raise Refused(f"{name}.py needs {missing.name or 'a package'}, which isn't installed in this Python."
                          " Nothing was converted.") from missing
        except Exception as broke:  # noqa: BLE001 - his script, whatever it raises, isn't loaded
            raise Refused(f"{name}.py could not be loaded ({type(broke).__name__}). Nothing was"
                          " converted.") from broke
        loaded.append(module)
    return tuple(loaded)


# ----- the three checks against his scripts


def tie(takeoff: ModuleType) -> list[str]:
    """What moved in takeoff.py against the rules NG Runner's converter mirrors: its constants, the
    kinds it knows, its tape rule on a made-up part of each kind, its 1/4 back panel and its rail
    cover's bucket. [] when nothing did."""
    missing = [n for n in ("WASTE", "DISREGARD", "TYPE", "eb_lf", "normalise", "takeoff") if not hasattr(takeoff, n)]
    if missing:
        return [f"it has no {_listed(missing)}"]
    moved = []
    if takeoff.WASTE != rules.WASTE:
        moved.append(f"its WASTE is {takeoff.WASTE:g}, and ng_convert's is {rules.WASTE:g}")
    if takeoff.DISREGARD.pattern != rules.DISREGARD.pattern or not takeoff.DISREGARD.flags & re.IGNORECASE:
        moved.append("its DISREGARD, the lines it drops, isn't ng_convert's")
    kinds = re.search(r"\(([^()]*)\)", takeoff.TYPE.pattern)
    if kinds is None or tuple(kinds[1].split("|")) != rules.TYPE_KINDS:
        moved.append("its TYPE, the kinds it knows, isn't ng_convert's")
    try:
        for kind in rules.TAPE:
            for wall in (False, True):
                record = dict(type=kind, qty=1, a_in=14.0, b_in=30.0, thk_in=0.75, tokens="FN" if wall else "",
                              garage=False)
                if abs(takeoff.eb_lf(record) - rules.tape_ft(kind, 14.0, 30.0, wall)) > 1e-9:
                    moved.append(f"its eb_lf tapes a {kind} otherwise")
                    break
        back = takeoff.normalise({"parts": [{"type": "BP", "qty": 1, "ic_in": [14.0, 30.0], "thick_in": 0.375,
                                             "desc": "BP 14x30x3/8"}]})
        if [r["thk_in"] for r in back] != [rules.BACK_PANEL_BUILT]:
            moved.append("its normalise builds a 3/8 back otherwise")
        rail = takeoff.takeoff([dict(type="TR Rail Cover", qty=1, a_in=3.5, b_in=96.0, thk_in=0.4, tokens="",
                                     garage=False)])
        if list(rail["buckets"]) != [f"{rules.RAIL_COVER_BUILT:g}"]:
            moved.append("its takeoff buckets a rail cover otherwise")
    except Exception as broke:  # noqa: BLE001 - his functions on a made-up part: whatever breaks, it moved
        moved.append(f"its functions could not be run on a made-up part ({type(broke).__name__})")
    return moved


def second_reading(path: Path, data: bytes, parse_pulled: ModuleType) -> int:
    """How many board lines parse_pulled.py (pdfplumber) reads otherwise than ng_convert (pypdf): each
    line by quantity and item, with what the rules make of it."""
    from ngrunner.convert.summary_lines import SummaryLine
    from tools.workbook.conversion import board_rows, board_verdicts

    try:
        rows = parse_pulled.parse_pdf(str(path))
        lines = [SummaryLine(str(r.get("section") or ""), str(r.get("item", "")), int(r.get("qty", 0)),
                             str(r.get("description", "")), int(r.get("page", 0) or 0), int(r.get("col", 0) or 0))
                 for r in rows]
    except Exception as broke:  # noqa: BLE001 - his reader: whatever it raises, there is no second reading
        raise Refused(f"Your parse_pulled.py could not read the PULLED list ({type(broke).__name__}), so"
                      " nothing was converted.") from broke
    ours, theirs = board_rows(data), board_verdicts(lines)
    return sum(((ours - theirs) + (theirs - ours)).values())


def agree(conv: Mapping[str, Any], takeoff: ModuleType) -> tuple[list[str], dict[str, Any]]:
    """takeoff.py's own reading of the parts the JSON holds, against ng_convert's: board parts, parts
    and area by thickness, tape and the vertical counts. Its sheets are left out: it sizes every
    bucket 49x97, where ng_convert takes RULES' table."""
    try:
        said_ = takeoff.takeoff(takeoff.normalise(conv))
    except Exception as broke:  # noqa: BLE001 - his takeoff on these parts: whatever breaks, no agreement
        return [f"it could not read these parts ({type(broke).__name__})"], {}
    problems = []
    total = conv["total_board_parts"]
    if said_.get("board_parts") != total:
        problems.append(f"board parts: takeoff.py says {said_.get('board_parts')}, ng_convert {total}")
    ours: dict[str, list[float]] = {}
    for b in conv["buckets"]:
        into = ours.setdefault(f"{b['thk_in']:g}", [0, 0.0, 0])
        into[0] += b["parts"]
        into[1] += b["sqft"]
        into[2] += 1
    his = said_.get("buckets") or {}
    for key in sorted(set(ours) | set(his)):
        mine, theirs = ours.get(key, [0, 0.0, 1]), his.get(key, {})
        if theirs.get("parts") != mine[0] or abs(float(theirs.get("sqft") or 0) - mine[1]) > 0.011 * mine[2]:
            problems.append(f"{key}in: takeoff.py says {theirs.get('parts')} parts and {theirs.get('sqft')} sqft,"
                            f" ng_convert {mine[0]} and {round(mine[1], 2)}")
    # Both round tape to 0.1 ft, takeoff.py once and ng_convert per bucket, so the same banding can
    # read 0.1 ft apart per bucket at a .x5 (measured: one real pair, 20.7 against 20.8).
    tape = sum(b["edgeband_lf"] for b in conv["buckets"])
    if abs(float(said_.get("edgeband_lf") or 0) - tape) > 0.1 * max(1, len(conv["buckets"])) + 1e-6:
        problems.append(f"tape: takeoff.py says {said_.get('edgeband_lf')} ft, ng_convert {round(tape, 1)}")
    vts = [p for p in conv["parts"] if p["type"] == "VT"]
    for key, count in (("closet_vt", sum(p["qty"] for p in vts)),
                       ("closet_vt_wm", sum(p["qty"] for p in vts if p["mount"] == "WM")),
                       ("closet_vt_rad", sum(p["qty"] for p in vts if p["radius"]))):
        if said_.get(key) != count:
            problems.append(f"{key}: takeoff.py says {said_.get(key)}, ng_convert {count}")
    return problems, said_


# ----- the conversion, checked, and writing it


def convert_summary(path: Path, *, job: str | None, scripts: Path) -> dict[str, Any]:
    """The conversion of one summary, checked against the scripts beside ng_convert. Writes nothing."""
    from ngrunner.convert.summary_lines import SummaryUnreadable
    from tools.workbook.conversion import ConversionRefused, conversion

    takeoff, parse_pulled, classify = jims(scripts)
    moved = tie(takeoff)
    if moved:
        raise Refused("Your takeoff.py has changed since ng_convert was made (" + "; ".join(moved) + "), so nothing"
                      " was converted. Ask Sulaiman for a new ng_convert.")
    try:
        data = path.read_bytes()
    except OSError as why:
        raise Refused(f"The summary {path.name} could not be read, so nothing was converted.") from why
    try:
        conv = conversion(data, summary_name=path.name, job=job, classify=classify.classify)
    except ConversionRefused as why:
        raise Refused(str(why)) from why
    except SummaryUnreadable as why:
        raise Refused(f"{why} Nothing was converted.") from why
    differ = second_reading(path, data, parse_pulled)
    if differ:
        raise Refused("The two readings of the PULLED list, ng_convert's (pypdf) and your parse_pulled.py's"
                      f" (pdfplumber), differ on {_count(differ, 'board line')}, so nothing was converted.")
    problems, said_ = agree(conv, takeoff)
    if problems:
        raise Refused("Your takeoff.py reads these parts differently (" + "; ".join(problems) + "), so nothing was"
                      " converted.")
    conv["ng_convert"].update(version=VERSION, takeoff_py=said_, checked=list(CHECKED))
    return conv


def refuse_inside(out: Path, toolkit: Path) -> None:
    """ng_convert writes in a job's folder, never inside the toolkit it runs from."""
    def key(p: Path) -> str:
        return os.path.normcase(str(p.resolve()))
    inside, kit = key(out), key(toolkit)
    if inside == kit or inside.startswith(kit.rstrip(os.sep) + os.sep):
        raise Refused("ng_convert never writes inside the toolkit (_NG_Automation): give --out a job's folder."
                      " Nothing was written.")


def write(conv: Mapping[str, Any], out: Path, *, replace: bool) -> tuple[Path, Path]:
    """<Job>_IC_Conversion.json and .xlsx in out, both or neither: written to temporary names, the
    workbook read back and checked against the JSON, then put in place."""
    job = str(conv["job"])
    if not job or _NOT_A_FILE_NAME.search(job):
        raise Refused("The summary's PROJECT NAME can't be a file name, so nothing was written.")
    targets = (out / f"{job}_IC_Conversion.json", out / f"{job}_IC_Conversion.xlsx")
    there = [t for t in targets if t.exists()]
    if there and not replace:
        raise Refused(f"{there[0].name} is there already, so nothing was written. Run it again with --replace to"
                      " write over it.")
    from tools.workbook import sheets, xlsx

    out.mkdir(parents=True, exist_ok=True)
    temps = tuple(out / f".{t.name}.{os.getpid()}.part" for t in targets)
    try:
        with open(temps[0], "x", encoding="utf-8", newline="\n") as f:
            json.dump(conv, f, indent=1, ensure_ascii=False)
        xlsx.save(sheets.layout(conv), temps[1])
        problems = xlsx.check(temps[1], conv)
        if problems:
            raise Refused("The workbook didn't check against its JSON (" + " ".join(problems) + "), so nothing was"
                          " written.")
        for temp, target in zip(temps, targets):
            os.replace(temp, target)
    except OSError as why:
        raise Refused(f"The IC Conversion could not be written in {out} ({why.strerror or why}).") from why
    finally:
        for temp in temps:
            if temp.exists():
                temp.unlink()
    return targets


def _count(n: int, thing: str) -> str:
    return f"{n} {thing}{'s' * (n != 1)}"


def _listed(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"
