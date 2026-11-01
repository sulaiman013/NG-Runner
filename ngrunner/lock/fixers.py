r"""
Every fixer of Jim's that NG Runner plans with, by the name a plan and a written fix keep (design 3.5,
"Fixes"; docs/decisions.md, "P4's fixers").

Why a list of its own: the worker plans each one for the finding it fixes, and a fix written long ago is
undone and judged after a save by the fixer that wrote it, found here by its name. Only general fixers of
his that write a room and have a check that can confirm it are here; his one-offs for a single job
(fix_<client>_*, swap_rea_<job>, build_*) never are, nor a script whose write path he removed
(br_through.py), one that rebuilds elements and moves the line count (br_cams.py, which RULES section 6's
gate refuses), one driven by a hand-written list (apply_fix.py), or one with no check (eb_fix2.py).

    notch_face           notch_side.py, checked by notch_check.py      the hang notch's face
    top_line             top_line.py, checked by top_line.py --check   the room's top line
    wm_banding           wm_eb.enforce_room, checked by notch_check.py  WM panels taped top, front, bottom
    countertop_banding   eb_tops.py, checked by eb_tops.py --check      countertops taped on four edges
"""
from __future__ import annotations

from ngrunner.lock.banding import COUNTERTOP_BANDING, WM_BANDING
from ngrunner.lock.fixes import Fixer
from ngrunner.lock.notch import NOTCH_FACE
from ngrunner.lock.topline import TOP_LINE

FIXERS: tuple[Fixer, ...] = (NOTCH_FACE, TOP_LINE, WM_BANDING, COUNTERTOP_BANDING)


def fixer_named(name: str) -> Fixer | None:
    return next((fixer for fixer in FIXERS if fixer.name == name), None)


def fixer_for(finding: tuple[str, str]) -> Fixer | None:
    """The fixer for a P3 finding, (checker, what), or None when there is none."""
    return next((fixer for fixer in FIXERS if fixer.finding == finding), None)
