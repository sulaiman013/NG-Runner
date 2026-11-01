r"""
Phase 4, lock and fixes (design section 7, P4; docs/p2/build-spec.md, section 2): leases, Lock design,
Finish lock and Lock, check-before-lock, the job sheet, and fixers as diffs with backup, inverse-patch
Undo and the survival check.

Why it is one package: every part of it ends in a write NG Runner has never made before, into the
tracker's inbox, a room's .des in Mozaik Shared or a job's folder, so all of it goes through
writes.commit_write() with P4's rules, and only as shadow writes until P4 goes live.

Shadow until go-live. P4_SHADOW is the phase every write here is made in: the chokepoint takes only
copies on this PC's own disk, refuses the tracker's own folder and the real Mozaik Shared by name, and
writes.py has no rules for P4_LIVE at all, so the real drop and the real fix are refused until they are
built and Sulaiman says so. The P4 interlock (interlock.p4_interlock) refuses anything below the folders
NG Runner is configured with, and any .des while Mozaik runs.

    leases      the lease file in a job's NG Jobs folder: taken, renewed, released, released all
    gate        check-before-lock: Nest checked for every material, and a crosscheck current for the rooms
    drop        a tracker batch: the checks before a drop, the drop, and what the snapshots say after it
    shadow      a shadow batch against the chat's own rows on the real tracker, and P4's "5 locks match"
    names       the tracker's names for a lock's rows: RULES' names held to the Pricing block
    totals      the snapshots' totals rows and BUILD's four equalities
    clicks      Lock design, Finish lock and Lock, made of the parts above
    job_sheet   Jim's cnc_job_sheet run on a staged copy, its counts read and set against the nests
    patch       byte patches on a .des: a diff, its inverse, applied only to the bytes it was made from
    fixes       a fixer's plan as a diff, applied after approval, Undo, and the survival check
    notch       the one real fixer: the hang notch's face (RULES section 3, Jim's notch_side.py)
"""
from __future__ import annotations

P4_SHADOW = "P4 shadow"     # the phase P4's build writes in: shadow copies only (writes.py)
P4_LIVE = "P4"              # the real drop and the real fix: no rules yet, so every write is refused
