"""
A made-up toolkit whose crosscheck.py prints what Jim's prints, from the rooms it is handed, and a job
for it to check: for the P3 check tests and the day simulation. No code of Jim's is in it.

The fake crosscheck reads the staged rooms: a product room holding the bytes NOTCH_WRONG makes
notch_check fail on its C3, as Jim's would for a notch on the wrong face; any other room reads clean, in
the shapes test_checks.CLEAN gives. It writes <job>_crosscheck.txt into the folder it is given and prints
its verdict line and "written:", exactly as crosscheck.py does, so the real runner and readers read it.
Every run is noted in a file beside the copy, so a test can see what it ran on.

For P4 it also carries a made-up notch fixer and its check, in the shapes lock/notch.py reads Jim's
notch_side.py and notch_check.py by: the fixer swaps which of C3's two grooves is hidden, and the check
flags C3 while groove 2 is the one cut. A room made with grooves=True holds them.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from test_checks import CLEAN, PLAN
from ngrunner.clock import FakeClock
from ngrunner.store import Store, add_snapshot
from ngrunner.toolkit import Snapshot, hash_of_tree, take_snapshot

NOTCH_WRONG = b'Note="notch-wrong"'       # on a product, so it is part of the room's design
NOTCH_FAULT = ("Room1.des\n   !! C3   48 DH                notch on face True, line bores on False - NOTCH ON THE"
               " WRONG FACE")

FAKE_CROSSCHECK = r'''
import json, os, sys
job, ngjob, conv = sys.argv[1:4]
here = os.path.dirname(os.path.abspath(__file__))
plan = json.load(open(os.path.join(here, "plan.json"), encoding="utf-8"))
with open(plan["log"], "a", encoding="utf-8") as log:
    log.write(json.dumps([job, ngjob, conv]) + "\n")
name = os.path.basename(os.path.normpath(job))
rooms = sorted((f for f in os.listdir(job) if f.startswith("Room") and f.endswith(".des")),
               key=lambda n: int(n[4:-4]))
bad = any(plan["marker"].encode() in open(os.path.join(job, r), "rb").read() for r in rooms if r != "Room0.des")
sections = dict(plan["clean"])
flagged = []
if bad:
    sections["notch_check"] = plan["notch_fault"]
    flagged.append("notch_check")
verdict = "CROSSCHECK CLEAN" if not flagged else "CROSSCHECK FLAGGED: " + ", ".join(flagged)
out = [name + " - crosscheck 2026-10-31T09:00Z", "  rooms   " + ", ".join(rooms), "  against " + conv,
       "  " + verdict + "\n", ""]
for check, advisory in plan["checks"]:
    out += ["===== " + check + ("   [ADVISORY]" if advisory else ""), sections[check].rstrip(), ""]
path = os.path.join(ngjob, name + "_crosscheck.txt")
with open(path, "w", encoding="utf-8") as written:
    written.write("\n".join(out))
print(verdict)
print("written:", path)
sys.exit(1 if flagged else 0)
'''


FAKE_NOTCH_SIDE = r"""
import sys
data = open(sys.argv[1], "rb").read()
data = (data.replace(b'ID="2" Hide="False"', b'ID="2" Hide="@"').replace(b'ID="3" Hide="True"', b'ID="3" Hide="False"')
        .replace(b'ID="2" Hide="@"', b'ID="2" Hide="True"'))
open(sys.argv[2], "wb").write(data)
print("  fixed C3")
"""

FAKE_NOTCH_CHECK = r"""
import os, sys
data = open(sys.argv[1], "rb").read()
print(os.path.basename(sys.argv[1]))
if b'ID="2" Hide="False"' in data:
    print("   !! C3   48 DH                notch on face True, line bores on False - NOTCH ON THE WRONG FACE")
    sys.exit(1)
print("   every notch is on its line-bore face; WM banding taped and locked")
"""


def fake_toolkit(into: Path, *, log: Path) -> Path:
    """A made-up _NG_Automation in into, whose crosscheck reads the rooms it is given. log: where each
    run notes what it was handed."""
    scripts = into / "_NG_Automation" / "scripts"
    scripts.mkdir(parents=True)
    checks = "".join(f"    ({p.name!r}, lambda j, c: [j], {p.advisory}),\n" for p in PLAN)
    (scripts / "crosscheck.py").write_text(FAKE_CROSSCHECK + "CHECKS = [\n" + checks + "]\n", encoding="utf-8")
    library = into / "Products"
    library.mkdir()
    (scripts / "wm_variant_check.py").write_text(f"LIB = r\"{library}\"\n", encoding="utf-8")
    (scripts / "notch_side.py").write_text(FAKE_NOTCH_SIDE, encoding="utf-8")
    (scripts / "notch_check.py").write_text(FAKE_NOTCH_CHECK, encoding="utf-8")
    (scripts / "plan.json").write_text(json.dumps({
        "clean": CLEAN, "notch_fault": NOTCH_FAULT, "marker": NOTCH_WRONG.decode(), "log": str(log),
        "checks": [[p.name, p.advisory] for p in PLAN]}), encoding="utf-8")
    return scripts.parent


def adopt(store: Store, toolkit_folder: Path, source: Path, *, at: datetime) -> Snapshot:
    """source copied into NG Runner's toolkit folder and recorded as adopted, as try_adoption leaves one."""
    sha = hash_of_tree(source)
    copy = take_snapshot(source, toolkit_folder / sha, clock=FakeClock(at))
    add_snapshot(store, taken_at=at, sha256=copy.sha256, adopted=True, result="Adopted for a test.")
    return copy


def room(*, width: int = 356, note: str = "", fault: bool = False, products: str = "",
         grooves: bool = False, fixed: bool = False) -> bytes:
    """A room as Mozaik writes one: version line 15, CRLF, then the XML. note: an annotation, which a
    re-save of the same design may change (guards/design.room_identity). width: a real change. grooves:
    C3's notch drawn twice, one hidden, as a WM panel carries it; fixed: the line-bore face's one is cut."""
    marker = " " + NOTCH_WRONG.decode() if fault else ""
    cut, hidden = ("True", "False") if fixed else ("False", "True")
    notch = (f'<OperationGroove ID="2" Hide="{cut}" FlipSideOp="False" />'
             f'<OperationGroove ID="3" Hide="{hidden}" FlipSideOp="True" />') if grooves else ""
    return ('15\r\n<?xml version="1.0" encoding="utf-8"?>\r\n<Room Quan="1" RoomNosDirty="False"><Products>'
            f'<Product CabNo="3" Count="1" ProdName="48 DH"{marker}><CabProdPart Name="Top" Quan="1" W="{width}"'
            f' L="870" UsageType="0" Type="Top" />{notch}</Product>{products}</Products><Annotations>{note}</Annotations>'
            '</Room>').encode("utf-8")


def mozaik_job(mozaik_shared: Path, folder: str, *, room1: bytes | None = None, closed: bool = False) -> Path:
    """A built job in Mozaik Shared\\Jobs: Room0 and Room1."""
    place = mozaik_shared / "Jobs" / folder
    place.mkdir(parents=True, exist_ok=True)
    (place / "Room0.des").write_bytes(room())
    (place / "Room1.des").write_bytes(room1 if room1 is not None else room())
    if closed:                  # its MozaikJob.UNS, as Mozaik leaves a job it closed (P4 decision 7e)
        (place / "MozaikJob.UNS").write_bytes(b"JDELA" + bytes([13, 10]))
    return place


def conversion(ng_jobs: Path, folder: str, text: str = '{"schema_version": "2.0"}') -> Path:
    """The chat's IC Conversion in the job's NG Jobs folder."""
    path = ng_jobs / folder / f"{folder}_IC_Conversion.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def runs_of(log: Path) -> list[list[str]]:
    """What each run of the fake crosscheck was handed: [job, ngjob, conversion]."""
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
