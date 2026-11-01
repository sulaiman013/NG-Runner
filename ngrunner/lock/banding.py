r"""
Two banding fixers of Jim's: the WM panels' banding (wm_eb.enforce_room, checked by notch_check.py) and
the countertops' four edges (eb_tops.py, its own check).

Why these two. Both are rules RULES says to fix on the live room, not to flag:
- "WM banding - top, front and bottom, LOCKED. Every family, every job ... Enforced in three places: the
  library, every build (wm_eb.enforce_room() + wm_eb.gate()), and every panel check on the live room
  (fix it, don't flag it)". BUILD 8c: "run wm_eb.enforce_room() on every staged Room#.des ... fixed in the
  file and reported by product and room". notch_check.py is "the gate on the two BUILD 8c fixes
  (notch_side.py, wm_eb.py)", and its banding line is the finding P3 raises (checks/findings.py,
  wm_banding). wm_eb has no command line, so it is called as BUILD calls it, through
  toolkit.run_room_call, from the adopted copy, on a staged copy.
- "Every COUNTERTOP / WS bands all FOUR edges (Jim, 2026-10-15) ... A part in a second colour bands in ITS
  OWN colour". eb_tops.py is "the two banding rules that Mozaik will not apply on its own"; crosscheck runs
  it with --check and the second colour it names (read here from crosscheck's own CHECKS by ast, never
  typed in), and P3 flags its "would change: N" itself (Jim's answer 26). Measured on every real room
  here, all it changes is countertops: so NG Runner plans those, and refuses a run of it that changes a
  part's banding or a banding slot's colour (its second rule), saying so, until such a room is measured.

Nothing of Jim's is copied here. What NG Runner reads for itself is what changed: each point of an
outline by its place (lock/des.py), and EBand and EBandLock on it. His checks name a panel by its name and
a countertop by its size, never by CabNo, so what they said is a Said, and a plan stands only when their
lines name exactly the panels or countertops the fix touched, and none after.

A banding change changes what a nest cuts, so a fix on a part a nest holds is "Fix and re-nest" (decision
7b). A countertop is named in a nest by its room alone (R3N0, on all 49 countertop parts in the real nests
here), so a countertop fix names every nest holding one of that room's countertops.
"""
from __future__ import annotations

import ast
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ngrunner.guards.facts import ToolRun
from ngrunner.lock.des import (
    CAB, IDTAG, attr, attributes, point_changes, point_span, products, room_words, staged, tops,
)
from ngrunner.lock.fixes import CabFixer, Element, FixRefused, Said, said_cleared, said_names, said_of
from ngrunner.lock.notch import read_notch_check
from ngrunner.messages import templates
from ngrunner.toolkit import Snapshot, run_room_call

_BANDS = frozenset({"EBand", "EBandLock"})
_WM_LINE = re.compile(r"   !! (.{24}) WM banding not taped \+ locked on top/front/bottom")
_COUNTERTOP = re.compile(r"   (Room\d+\.des) +COUNTERTOP (-?\d+\.\d{3})in x (-?\d+\.\d{3})in  four edges, slot (\d)")
_CORRECT = re.compile(r"   (Room\d+\.des) +already correct")
_TOTAL = re.compile(r"would change: (\d+) part\(s\)/slot\(s\)")
CROSSCHECK = "scripts/crosscheck.py"

Run = Callable[..., ToolRun]


# ----- the WM panels' banding


@dataclass(frozen=True)
class WmBanding(CabFixer):
    name: str = "wm_banding"
    module: str = "scripts/wm_eb.py"
    function: str = "enforce_room"
    finding: tuple[str, str] = ("notch_check", "wm_banding")
    call: Callable[..., ToolRun] = field(default=run_room_call, compare=False, repr=False)

    def draft(self, snapshot: Snapshot, base: bytes, *, room_name: str, work: Path, run: Run,
              python: Path | None = None) -> bytes:
        """What wm_eb.enforce_room makes of base, called on a staged copy and written beside it."""
        with staged(work, room_name, base) as (stage, room):
            out = stage / "out" / room_name
            out.parent.mkdir()
            said = self.call(snapshot, self.module, self.function, room, out, python=python)
            if said.timed_out or said.exit_code != 0 or said.stderr.strip() or not out.is_file():
                raise FixRefused("Jim's WM banding fixer didn't finish cleanly, so no fix is planned.")
            return out.read_bytes()

    def elements(self, base: bytes, result: bytes) -> tuple[Element, ...]:
        """Each EBand and EBandLock that differs on a point of a part's outline, by the part's product and
        place. A countertop's or any other point changed is more than this fixer does."""
        found = [Element(*c) for c in point_changes(base, result, allowed=_BANDS, who="Jim's WM banding fixer")]
        if any(not e.cab for e in found):
            raise FixRefused("Jim's WM banding fixer changed more than the panels' banding, so no fix is planned.")
        return tuple(found)

    def locate(self, data: bytes, element: Element) -> tuple[int, int] | None:
        return point_span(data, element.cab, element.id, element.attribute)

    def flags(self, snapshot: Snapshot, data: bytes, *, room_name: str, work: Path, run: Run,
              python: Path | None = None) -> Said | None:
        """notch_check.py's banding lines, each by the panel name it prints. None: it can't be read."""
        said = read_notch_check(snapshot, data, room_name=room_name, work=work, run=run, python=python)
        if said is None:
            return None
        names = [found[1].rstrip() for line in said[1] if (found := _WM_LINE.fullmatch(line))]
        if len(names) != len(said[1]):
            return None
        return said_of(names, _panel_keys(data))

    def cleared(self, before: Said, after: Said, elements: Sequence[Element]) -> bool:
        return said_cleared(before, after, {e.cab for e in elements})

    def names(self, said: Said, elements: Sequence[Element]) -> bool:
        return said_names(said, {e.cab for e in elements})

    def words(self, room_name: str, tags: tuple[str, ...], elements: Sequence[Element] = (),
              data: bytes = b"") -> str:
        n = len(tags)
        said = (f"{room_words(room_name)}: {n} wall-hung panel{'s' if n != 1 else ''} ({', '.join(tags)})"
                f" {'aren' if n != 1 else 'isn'}'t taped and locked on top, front and bottom; the fix tapes"
                " and locks those edges")
        bare = sum(1 for e in elements if e.attribute == "EBand" and e.after == "0")
        if bare:
            said += f" and takes the tape off {bare} edge{'s' if bare != 1 else ''} against the wall"
        return said


# ----- the countertops' four edges


@dataclass(frozen=True)
class CountertopBanding:
    name: str = "countertop_banding"
    script: str = "scripts/eb_tops.py"
    finding: tuple[str, str] = ("eb_tops", "banding")

    def draft(self, snapshot: Snapshot, base: bytes, *, room_name: str, work: Path, run: Run,
              python: Path | None = None) -> bytes:
        """What eb_tops.py <second colour> <room> makes of base, with the second colour crosscheck checks
        with. It rewrites the staged copy it is given."""
        args = _second_colour(snapshot)
        with staged(work, room_name, base) as (_, room):
            said = run(snapshot, self.script, [*args, str(room)], python=python)
            if said.timed_out or said.exit_code != 0 or said.stderr.strip():
                raise FixRefused("Jim's countertop banding fixer didn't finish cleanly, so no fix is planned.")
            return room.read_bytes()

    def elements(self, base: bytes, result: bytes) -> tuple[Element, ...]:
        """Each EBand and EBandLock that differs on a countertop's outline, by its IDTag and place. A part's
        banding changed is eb_tops' second rule, which NG Runner doesn't plan yet."""
        found = [Element(*c) for c in point_changes(base, result, allowed=_BANDS,
                                                    who="Jim's countertop banding fixer")]
        if any(e.cab for e in found):
            raise FixRefused("Jim's countertop banding fixer changed a part's banding as well, which NG Runner"
                             " doesn't plan yet, so no fix is planned.")
        return tuple(found)

    def locate(self, data: bytes, element: Element) -> tuple[int, int] | None:
        return point_span(data, element.cab, element.id, element.attribute)

    def flags(self, snapshot: Snapshot, data: bytes, *, room_name: str, work: Path, run: Run,
              python: Path | None = None) -> Said | None:
        """What eb_tops.py --check says of the room's countertops, each by its size. A line about a part or
        a banding slot is one NG Runner doesn't read yet. None: it can't be read."""
        try:
            args = _second_colour(snapshot)
        except FixRefused:
            return None
        with staged(work, room_name, data) as (_, room):
            said = run(snapshot, self.script, ["--check", *args, str(room)], python=python)
        if said.timed_out or said.exit_code != 0 or said.stderr.strip():
            return None
        lines: list[str] = []
        correct, totals = 0, []
        for line in said.stdout.splitlines():
            top, done, total = _COUNTERTOP.fullmatch(line), _CORRECT.fullmatch(line), _TOTAL.fullmatch(line)
            if top and top[1] == room_name:
                lines.append(_top_identity(top[2], top[3]))
            elif done and done[1] == room_name:
                correct += 1
            elif total:
                totals.append(int(total[1]))
            elif line.strip():
                return None
        if totals != [len(lines)] or correct != (0 if lines else 1):
            return None
        return said_of(lines, _top_keys(data))

    def cleared(self, before: Said, after: Said, elements: Sequence[Element]) -> bool:
        return said_cleared(before, after, {_top_key(e) for e in elements})

    def names(self, said: Said, elements: Sequence[Element]) -> bool:
        return said_names(said, {_top_key(e) for e in elements})

    def tag(self, number: int, element: Element) -> str:
        """As the nests name a countertop: by its room (R3N0)."""
        return f"R{number}C{element.cab}" if element.cab else f"R{number}N0"

    def short(self, element: Element) -> str:
        return f"C{element.cab}" if element.cab else "the countertops"

    def words(self, room_name: str, tags: tuple[str, ...], elements: Sequence[Element] = (),
              data: bytes = b"") -> str:
        keys = list(dict.fromkeys(_top_key(e) for e in elements))
        sizes = [size for key in keys if (size := _top_size(data, key))]
        n = len(keys)
        said = (f"{room_words(room_name)}: {n} countertop{'s' if n != 1 else ''}"
                f"{' (' + '; '.join(sizes) + ')' if sizes else ''} {'aren' if n != 1 else 'isn'}'t taped and"
                " locked on all four edges")
        slots = sorted({e.after for e in elements if e.attribute == "EBand"})
        said += f"; the fix tapes and locks every edge in banding {_slots(slots)}" if slots else \
            "; the fix locks every edge's tape"
        moved = sorted({e.before for e in elements if e.attribute == "EBand" and e.before not in ("0", e.after)})
        if moved:
            said += f", moving tape that was in {_slots(moved)}"
        return said


def _slots(numbers: Sequence[str]) -> str:
    """slot 1, slots 2 and 3."""
    if len(numbers) == 1:
        return f"slot {numbers[0]}"
    return f"slots {', '.join(numbers[:-1])} and {numbers[-1]}"


WM_BANDING = WmBanding()
COUNTERTOP_BANDING = CountertopBanding()


# ----- reading


def _panel_keys(data: bytes) -> dict[str, str]:
    """Each product by its CabNo, as notch_check.py's banding line names it: its name's first 24 letters."""
    keys: dict[str, str] = {}
    for m, _ in products(data):
        a = attributes(m.group(0))
        cab = a.get("CabNo", "")
        keys[cab] = a.get("ProdName", "")[:24].rstrip() if cab not in keys else f"twice|{cab}"
    return keys


def _top_key(element: Element) -> str:
    return element.cab or element.id.rpartition(".")[0]


def _top_identity(length: str, depth: str) -> str:
    return f"countertop|{length}|{depth}"


def _top_keys(data: bytes) -> dict[str, str]:
    """Each countertop by T<IDTag>, as eb_tops.py names it: its length and depth in inches, to 3 places."""
    keys: dict[str, str] = {}
    for m, _ in tops(data):
        key = "T" + attr(IDTAG, m.group(0))
        a = attributes(m.group(0))
        try:
            said = _top_identity(f"{float(a['Length']) / 25.4:.3f}", f"{float(a['D']) / 25.4:.3f}")
        except (KeyError, ValueError):
            said = f"unread|{key}"
        keys[key] = said if key not in keys else f"twice|{key}"
    return keys


def _top_size(data: bytes, key: str) -> str:
    for m, _ in tops(data):
        if "T" + attr(IDTAG, m.group(0)) == key:
            a = attributes(m.group(0))
            try:
                return f"{templates._inches(float(a['Length']))} by {templates._inches(float(a['D']))}"
            except (KeyError, ValueError):
                return ""
    return ""


def _second_colour(snapshot: Snapshot) -> list[str]:
    """What crosscheck gives eb_tops besides --check and the rooms, read from its CHECKS by ast: the second
    colour, as crosscheck checks with it. Raises FixRefused when it isn't there as a list of words."""
    refused = FixRefused("The second colour Jim's crosscheck checks countertops with can't be read, so no fix"
                         " is planned.")
    try:
        tree = ast.parse((snapshot.path / CROSSCHECK).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        raise refused from exc
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and [getattr(t, "id", None) for t in node.targets] == ["CHECKS"]
                and isinstance(node.value, ast.List)):
            continue
        for item in node.value.elts:
            words = _eb_tops_words(item)
            if words is not None:
                if words[:1] != ["--check"] or len(words) > 2 or not all(w.isdecimal() for w in words[1:]):
                    raise refused
                return words[1:]
    raise refused


def _eb_tops_words(item: Any) -> list[str] | None:
    if not (isinstance(item, ast.Tuple) and len(item.elts) == 3 and isinstance(item.elts[0], ast.Constant)
            and item.elts[0].value == "eb_tops"):
        return None
    body = getattr(item.elts[1], "body", None)
    if not (isinstance(item.elts[1], ast.Lambda) and isinstance(body, ast.BinOp) and isinstance(body.op, ast.Add)
            and isinstance(body.left, ast.List)):
        return []
    words = [e.value for e in body.left.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return words if len(words) == len(body.left.elts) else []
