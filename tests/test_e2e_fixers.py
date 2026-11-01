"""
The day simulation for a fixer beyond the notch (tests/test_e2e_day.py's world): Testerson built with a
wall-hung panel off its room's top line, checked from the command box, its fix card approved, the fix
written into NG Runner's own copy of the room and undone to the exact bytes, as scenario 84 does for the
notch. The made-up toolkit's crosscheck shows top_line's "would change" for that panel, which NG Runner
flags itself, and its made-up top_line.py moves the panel onto the line and checks it, in the shapes
lock/topline.py reads Jim's by. No code of Jim's is here, and nothing real is written.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fakes import tree
from helpers_checks import FAKE_CROSSCHECK, adopt, fake_toolkit, mozaik_job, room
from ngrunner.messages import templates
from test_e2e_day import (  # noqa: F401 - make_world is a fixture
    GUARD_JOB, make_world, nothing_of_p4_in_the_real_folders, p4_ready, quiet, shadow_of,
)

OFF_THE_LINE = '<Product CabNo="4" ProdName="WM Panel 48 Thru" Width="19" Height="1204" Elev="1127.1"></Product>'
TOP_FAULT = ("   Room1.des    WM Panel 48 Thru           H=1204    Elev  1127.1 ->   928.0  (top 2331 -> 2132)\n\n"
             "would change: 1 WM panels")

# The made-up crosscheck, which also shows top_line's "would change" for a product room holding the panel.
CROSSCHECK = FAKE_CROSSCHECK.replace(
    'verdict = "CROSSCHECK CLEAN"',
    "if any(b'Elev=\"1127.1\"' in open(os.path.join(job, r), 'rb').read() for r in rooms if r != 'Room0.des'):\n"
    '    sections["top_line"] = plan["top_fault"]\n'
    'verdict = "CROSSCHECK CLEAN"')

# The made-up top_line.py: --check prints the panel's line while it is off the line; without it, it moves it.
TOP_LINE = r'''
import os, sys
args = [a for a in sys.argv[1:] if a != "--check"]
path = args[0]
data = open(path, "rb").read()
off = b'Elev="1127.1"' in data
if "--check" in sys.argv:
    if off:
        print("   Room1.des    WM Panel 48 Thru           H=1204    Elev  1127.1 ->   928.0  (top 2331 -> 2132)")
    else:
        print("   " + os.path.basename(path).ljust(12) + " already on its top line (2132)")
    print("")
    print("would change: %d WM panels" % (1 if off else 0))
    sys.exit(0)
open(path, "wb").write(data.replace(b'Elev="1127.1"', b'Elev="928"'))
print("")
print("changed: 1 WM panels")
'''


def top_line_toolkit(into: Path, *, log: Path) -> Path:
    """The made-up toolkit of the day simulation, with a crosscheck that shows the top line and a top_line.py."""
    folder = fake_toolkit(into, log=log)
    scripts = folder / "scripts"
    made = (scripts / "crosscheck.py").read_text(encoding="utf-8")
    (scripts / "crosscheck.py").write_text(made.replace(FAKE_CROSSCHECK, CROSSCHECK), encoding="utf-8")
    (scripts / "top_line.py").write_text(TOP_LINE, encoding="utf-8")
    plan = json.loads((scripts / "plan.json").read_text(encoding="utf-8"))
    (scripts / "plan.json").write_text(json.dumps({**plan, "top_fault": TOP_FAULT}), encoding="utf-8")
    return folder


def test_scenario_97_a_top_line_fix_is_carded_written_into_ng_runners_copy_and_undone(make_world, monkeypatch):
    from ngrunner.store.locks import list_fixes

    world: Any = make_world(tracker=True)
    p4_ready(world, monkeypatch, build=False, toolkit=False)
    adopt(world.store, world.paths.toolkit, top_line_toolkit(world.tmp / "jims", log=world.tmp / "runs.jsonl"),
          at=world.clock.now())
    job = mozaik_job(world.mozaik, GUARD_JOB, room1=room(products=OFF_THE_LINE), closed=True)
    quiet(world, job)
    world.run(minutes=4)
    real = (job / "Room1.des").read_bytes()
    before = tree(world.mozaik)

    assert world.actions.command(f"check {GUARD_JOB}") == templates.check_asked(GUARD_JOB)
    world.run(minutes=3)
    card, = world.cards("fix")
    assert card.text.startswith("Room 1: 1 wall-hung panel (R1C4) doesn't hang on the room's top line; the fix"
                                " moves it up or down onto it")
    assert card.choices == templates.FIX_CHOICES

    world.actions.answer(card.id, "Fix it")
    world.run(minutes=6)
    fix, = list_fixes(world.store)
    copy = shadow_of(world).mozaik_shared / "Jobs" / GUARD_JOB / "Room1.des"
    assert fix.fixer == "top_line" and copy.read_bytes() == real.replace(b'Elev="1127.1"', b'Elev="928"')
    assert (job / "Room1.des").read_bytes() == real
    view, = world.board_row(GUARD_JOB).p4.fixes
    assert (view.state, view.can_undo) == ("applied", True)

    assert world.actions.undo_fix(str(fix.id)) == templates.undo_asked(GUARD_JOB)
    quiet(world, copy.parent)
    world.run(minutes=5)
    assert copy.read_bytes() == real
    assert list_fixes(world.store)[0].state == "undone"
    nothing_of_p4_in_the_real_folders(world, before)
