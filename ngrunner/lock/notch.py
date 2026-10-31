r"""
The one real fixer: the hang notch's face (RULES section 3, "The notch", item 2), as Jim's own
notch_side.py makes it and his notch_check.py checks it.

Why this one. Of his fix family it is the one RULES describes exactly and the smallest: "Active groove
FlipSideOp == line-bore FlipSideOp ... FIX it, don't flag it (scripts\notch_side.py) ... Leave alone and
REPORT: bores on both faces, no bores cached, or anything other than 1 active + 1 hidden". Every WM
panel carries its notch twice, once per face, one of them hidden, so the fix swaps which of the two is
hidden: one attribute on two grooves, never a size, a position or a new element. notch_check.py is the
gate crosscheck runs for it. Its notes: "The edit invalidates any nest - say so" (decision 7b), and on
10-20 such a fix landed on parts Jim had already flipped by hand in the Optimizer, so the nest is read
for hand flips first (fixes.plan_fix).

Nothing of Jim's is copied here. His fixer runs from the adopted toolkit copy, inside a Job Object, on
a staged copy (toolkit.run_tool), and writes its output beside it; his check runs the same way. What
NG Runner reads for itself is only what changed and where: each product by its CabNo, each groove by
its ID, and the Hide values on them, so a fix is known by the elements it touched, not by offsets.

His check's lines are read failing closed: a room line, a "!!" or "--" line naming a panel, a banding
line, its clean line, or a blank. Anything else, a run that didn't finish, a word on stderr or an exit
code that doesn't fit what it printed means its verdict can't be read.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ngrunner.guards.facts import ToolRun
from ngrunner.lock.fixes import Element, FixRefused
from ngrunner.toolkit import Snapshot, ToolkitError, refuse_unless_local

_PRODUCT = re.compile(rb"<Product [^>]*>")
_CAB = re.compile(rb'\sCabNo="([^"]*)"')
_GROOVE = re.compile(rb"<OperationGroove [^>]*>")
_ID = re.compile(rb'\sID="([^"]*)"')
_HIDE = re.compile(rb'\sHide="([^"]*)"')
_FLIP = re.compile(rb'\sFlipSideOp="([^"]*)"')
_ROOM = re.compile(r"Room[0-9]+\.des")
_PANEL = re.compile(r"   (!!|--) C(\S+) +.* notch on face .*, line bores on .* - (.+)")
_BANDING = re.compile(r"   !! .+ WM banding not taped \+ locked on top/front/bottom")
_CLEAN = "   every notch is on its line-bore face; WM banding taped and locked"
_WRONG_FACE = "NOTCH ON THE WRONG FACE"

Run = Callable[..., ToolRun]


@dataclass(frozen=True)
class NotchFace:
    name: str = "notch_face"
    script: str = "scripts/notch_side.py"
    check: str = "scripts/notch_check.py"

    # -- Jim's fixer, on a staged copy

    def draft(self, snapshot: Snapshot, base: bytes, *, room_name: str, work: Path, run: Run,
              python: Path | None = None) -> bytes:
        """What Jim's fixer makes of base: his notch_side.py <room> <out>, run on a staged copy."""
        with _staged(work, room_name, base) as (stage, room):
            out = stage / "out" / room_name
            out.parent.mkdir()
            said = run(snapshot, self.script, [str(room), str(out)], python=python)
            if said.timed_out or said.exit_code != 0 or said.stderr.strip() or not out.is_file():
                raise FixRefused("Jim's notch fixer didn't finish cleanly, so no fix is planned.")
            return out.read_bytes()

    # -- what changed, and where

    def elements(self, base: bytes, result: bytes) -> tuple[Element, ...]:
        """Each groove whose Hide differs, by its product's CabNo and its own ID. The two files must
        hold the same products and grooves, and each panel touched must have swapped its two."""
        before, after = _grooves(base), _grooves(result)
        if [(c, [g[0] for g in gs]) for c, gs in before] != [(c, [g[0] for g in gs]) for c, gs in after]:
            raise FixRefused("Jim's notch fixer changed more than the notch's face, so no fix is planned.")
        found: list[Element] = []
        for (cab, old), (_, new) in zip(before, after):
            changed = [Element(cab, gid, "Hide", h0, h1) for (gid, h0), (_, h1) in zip(old, new) if h0 != h1]
            if changed and sorted((e.before, e.after) for e in changed) != [("False", "True"), ("True", "False")]:
                raise FixRefused("Jim's notch fixer didn't swap a panel's two notches, so no fix is planned.")
            found += changed
        return tuple(found)

    def locate(self, data: bytes, element: Element) -> tuple[int, int] | None:
        """Where element's value is in data: the Hide value of the groove with its ID, inside the one
        product with its CabNo. None when there isn't exactly one of each."""
        products = [(m, end) for m, end in _products(data) if _attr(_CAB, m.group(0)) == element.cab]
        if len(products) != 1:
            return None
        start, end = products[0][0].start(), products[0][1]
        grooves = [g for g in _GROOVE.finditer(data, start, end) if _attr(_ID, g.group(0)) == element.id]
        if len(grooves) != 1:
            return None
        hide = _HIDE.search(grooves[0].group(0))
        if hide is None:
            return None
        return grooves[0].start() + hide.start(1), grooves[0].start() + hide.end(1)

    # -- Jim's check, on a staged copy

    def flags(self, snapshot: Snapshot, data: bytes, *, room_name: str, work: Path, run: Run,
              python: Path | None = None) -> dict[str, str] | None:
        """What notch_check.py says of each panel it names: "wrong" (the notch on the wrong face) or
        "leave" (left alone, to report). A panel it doesn't name is fine. None: it can't be read."""
        with _staged(work, room_name, data) as (_, room):
            said = run(snapshot, self.check, [str(room)], python=python)
        if said.timed_out or said.exit_code not in (0, 1) or said.stderr.strip():
            return None
        found: dict[str, str] = {}
        rooms, faults, clean = 0, 0, 0
        for line in said.stdout.splitlines():
            panel = _PANEL.fullmatch(line)
            if line == room_name:
                rooms += 1
            elif panel:
                wrong = panel[3] == _WRONG_FACE
                if (panel[1] == "!!") != wrong:
                    return None
                found[panel[2]] = "wrong" if wrong else "leave"
                faults += wrong
            elif _BANDING.fullmatch(line):
                faults += 1
            elif line == _CLEAN:
                clean += 1
            elif line.strip():
                return None
        if rooms != 1 or said.exit_code != (1 if faults else 0) or (clean == 1) == bool(faults):
            return None
        return found

    # -- the card's words

    def words(self, room_name: str, tags: tuple[str, ...]) -> str:
        n = len(tags)
        return (f"{_room_words(room_name)}: the hang notch is cut from the wrong face on {n}"
                f" panel{'s' if n != 1 else ''} ({', '.join(tags)}); the fix makes the notch on the"
                " line-bore face the one that is cut")


NOTCH_FACE = NotchFace()


# ----- reading


def _products(data: bytes):
    for m in _PRODUCT.finditer(data):
        end = data.find(b"</Product>", m.end())
        yield m, (len(data) if end < 0 else end)


def _grooves(data: bytes) -> list[tuple[str, list[tuple[str, str]]]]:
    out = []
    for m, end in _products(data):
        cab = _attr(_CAB, m.group(0))
        grooves = [(_attr(_ID, g.group(0)), _attr(_HIDE, g.group(0))) for g in _GROOVE.finditer(data, m.end(), end)]
        out.append((cab, grooves))
    return out


def _attr(pattern: re.Pattern[bytes], tag: bytes) -> str:
    found = pattern.search(tag)
    return found.group(1).decode("utf-8", errors="replace") if found else ""


def _room_words(room_name: str) -> str:
    found = re.fullmatch(r"Room([0-9]+)\.des", room_name, re.IGNORECASE)
    return f"Room {int(found[1])}" if found else "A room"


class _staged:
    """A folder of its own under work, holding data as room_name, gone when the run ends."""

    def __init__(self, work: Path, room_name: str, data: bytes) -> None:
        if not _ROOM.fullmatch(room_name):
            raise FixRefused("Only a room's .des is fixed.")
        self.work, self.room_name, self.data = work, room_name, data

    def __enter__(self) -> tuple[Path, Path]:
        try:
            refuse_unless_local(self.work, "A fix's staged copy")
        except ToolkitError as exc:
            raise FixRefused(str(exc)) from exc
        self.work.mkdir(parents=True, exist_ok=True)
        self.stage = Path(tempfile.mkdtemp(prefix="fix-", dir=self.work))
        room = self.stage / "in" / self.room_name
        room.parent.mkdir()
        with open(room, "xb") as out:
            out.write(self.data)
        return self.stage, room

    def __exit__(self, *exc: object) -> None:
        shutil.rmtree(self.stage, ignore_errors=True)       # made by this run
