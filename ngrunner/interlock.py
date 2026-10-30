r"""
The P1a interlock: what commit_write asks before it writes, and again just before the renames
(integration spec section 2; design section 5).

Why it is this small: in P1a NG Runner writes nothing a Mozaik job reads, so the Mozaik conditions
wait for P1b. What P1a must not do is file while paused, write into a folder that was there at
install (an OBSERVED job gets no writes: watch and check only), or write anywhere but NG Jobs and
the control folder. A pause stops filing and nothing else: heartbeats, the digest, messages and
backups all go to the control folder, and go on, or the 45-minute alarm would fire on a paused PC.
So paused applies only to NG Jobs and control\shadow\. HELD is not here: nothing can set it in P1a.
Shadow mode writes nothing in NG Jobs, whoever filed the job there: this is where no path can
forget it (docs/decisions.md).

It reads the store each time it is asked, since a pause or a new OBSERVED job can come while a
commit waits its turn. It takes anything with .store and .roots, so the messenger and the
supervisor can use it as well as the pipeline's Ctx.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ngrunner.store import Db, get_state, list_jobs

PAUSED = "paused"                    # state key: "yes" or "no" (the supervisor's)
INSTALL_FOLDERS = "install.folders"  # state key: the NG Jobs folders there at install, as JSON
SHADOW = "shadow"                    # control\shadow\: what shadow mode files
GARAGE_SUFFIX = "_Gar"

PAUSED_REASON = "NG Runner is paused, so nothing is filed until it is resumed at the PC."
SHADOW_REASON = "NG Runner is in shadow mode, so nothing is written in NG Jobs."


def p1a_interlock(ctx: Any) -> Callable[[list[Path]], str | None]:
    """The interlock for commit_write: given the target paths, a plain reason not to write them,
    or None when they may be written. ctx needs .store and .roots, and .mode when it can file. If
    the store can't be read this raises, and commit_write refuses: a safety check that cannot run
    means wait."""
    store, roots = ctx.store, ctx.roots

    def check(targets: list[Path]) -> str | None:
        watched: set[str] | None = None
        for target in targets:
            place = _place(Path(target), roots)
            if place is None:
                return (f"{Path(target).name} is outside NG Jobs and the control folder, so NG"
                        " Runner does not write it in this phase.")
            filing, folder = place
            if filing and is_paused(store):
                return PAUSED_REASON
            if getattr(ctx, "mode", None) == SHADOW and _below(Path(target), roots.ng_jobs):
                return SHADOW_REASON
            if folder is not None:
                watched = _observed_folders(store) if watched is None else watched
                if folder.casefold() in watched:
                    return (f"{folder} was there when NG Runner was installed, so NG Runner only"
                            " watches it and writes nothing into it.")
        return None

    return check


def is_paused(db: Db) -> bool:
    """Whether filing is paused. Anything but a plain "no" reads as paused: pausing is safe."""
    return (get_state(db, PAUSED) or "no") != "no"


def _place(target: Path, roots: Any) -> tuple[bool, str | None] | None:
    """(is this filing, the job folder it is in), or None for a target outside both roots."""
    below = _below(target, roots.ng_jobs)
    if below is not None:
        return True, below[0] if len(below) > 1 else None
    below = _below(target, roots.control)
    if below is None:
        return None
    if below and below[0].casefold() == SHADOW:
        return True, below[1] if len(below) > 2 else None
    return False, None


def _below(target: Path, root: Path) -> tuple[str, ...] | None:
    """The names of target below root, however the root is spelled (commit_write gives targets
    spelled from its resolved root); None when it is not below it."""
    parts = _folded(target)
    for spelling in (root, Path(os.path.realpath(root))):
        start = _folded(spelling)
        if parts[:len(start)] == start and len(parts) > len(start):
            return target.parts[len(start):]
    return None


def _folded(path: Path) -> tuple[str, ...]:
    return tuple(part.casefold() for part in Path(os.path.abspath(path)).parts)


def _observed_folders(db: Db) -> set[str]:
    """The folders that get no writes, case-folded: those there at install, an OBSERVED job's."""
    found = {name.casefold() for name in json.loads(get_state(db, INSTALL_FOLDERS) or "[]")}
    for job in list_jobs(db):
        if job.mode == "observed":
            names = (job.folder, job.name, job.name + GARAGE_SUFFIX)
            found |= {name.casefold() for name in names if name}
    return found
