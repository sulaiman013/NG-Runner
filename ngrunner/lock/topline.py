r"""
The room's top line, as Jim's own top_line.py sets it and checks it (RULES section 3, "The top line - one
T per ROOM, and the FM panels set it": "Every WM panel in a room hangs on that room's single top line T.
Elev = T - Height ... scripts\top_line.py is the implementation").

Why this one. It is a general fixer of his, written for a live room ("Surgical: rewrites ONLY the Elev
attribute of <Product ProdName="WM Panel *"> rows ... safe to run on a room Jim has already put sections
into"), and the same script is its own check: crosscheck runs it with --check, and P3 flags its "would
change: N" itself (checks/readers.py, Jim's answer 26). Elev is an input in RULES section 6 ("Inputs stick
... Elev, X, Height"), so a fix of it is one Mozaik keeps.

Nothing of Jim's is copied here: his script runs from the adopted copy on a staged copy (toolkit.run_tool)
and rewrites that copy, and how T is found is his alone. What NG Runner reads for itself is what changed:
each product by its CabNo, and its Elev before and after. His check names a panel by its name, height and
Elev rather than its CabNo, so what it said is a Said: each line's (name, height, Elev) against every
product's, and the plan stands only when the lines name exactly the panels the fix moved, and none after.

His check's lines are read failing closed: a panel line, his "already on its top line" line, a blank and
"would change: N WM panels", with N the panel lines and the room the one given; anything else, an exit
other than 0 or a word on stderr, and what it said can't be read.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ngrunner.guards.facts import ToolRun
from ngrunner.lock.des import CAB, attr, attributes, products, room_words, staged, the_product, value_span
from ngrunner.lock.fixes import CabFixer, Element, FixRefused, Said, said_cleared, said_names, said_of
from ngrunner.messages import templates
from ngrunner.toolkit import Snapshot

_HIT = re.compile(r"   (Room\d+\.des) +(\S.*?) +H=(\d+) +Elev +(-?\d+\.\d) -> +(-?\d+\.\d)"
                  r"  \(top (-?\d+) -> (-?\d+)\)")
_ALREADY = re.compile(r"   (Room\d+\.des) +already on its top line \((-?\d+)\)")
_TOTAL = re.compile(r"would change: (\d+) WM panels")

Run = Callable[..., ToolRun]


@dataclass(frozen=True)
class TopLine(CabFixer):
    name: str = "top_line"
    script: str = "scripts/top_line.py"
    finding: tuple[str, str] = ("top_line", "top_line")

    # -- Jim's fixer, on a staged copy

    def draft(self, snapshot: Snapshot, base: bytes, *, room_name: str, work: Path, run: Run,
              python: Path | None = None) -> bytes:
        """What Jim's top_line.py <room> makes of base. It rewrites the staged copy it is given."""
        with staged(work, room_name, base) as (_, room):
            said = run(snapshot, self.script, [str(room)], python=python)
            if said.timed_out or said.exit_code != 0 or said.stderr.strip():
                raise FixRefused("Jim's top line fixer didn't finish cleanly, so no fix is planned.")
            return room.read_bytes()

    # -- what changed, and where

    def elements(self, base: bytes, result: bytes) -> tuple[Element, ...]:
        """Each product whose Elev differs, by its CabNo. The two files must hold the same products in
        the same order, and nothing else on a product's tag may differ."""
        before, after = list(products(base)), list(products(result))
        if [attr(CAB, m.group(0)) for m, _ in before] != [attr(CAB, m.group(0)) for m, _ in after]:
            raise FixRefused("Jim's top line fixer changed more than the panels' Elev, so no fix is planned.")
        found = []
        for (old, _), (new, _) in zip(before, after):
            a, b = attributes(old.group(0)), attributes(new.group(0))
            moved = {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)}
            if not moved:
                continue
            if moved != {"Elev"} or a.keys() != b.keys():
                raise FixRefused("Jim's top line fixer changed more than the panels' Elev, so no fix is planned.")
            found.append(Element(a.get("CabNo", ""), "", "Elev", a["Elev"], b["Elev"]))
        return tuple(found)

    def locate(self, data: bytes, element: Element) -> tuple[int, int] | None:
        """The Elev of the one product with element's CabNo. None when there isn't exactly one."""
        found = the_product(data, element.cab)
        if found is None or element.attribute != "Elev":
            return None
        return value_span(data, found[0].start(), found[0].group(0), "Elev")

    # -- Jim's check, on a staged copy

    def flags(self, snapshot: Snapshot, data: bytes, *, room_name: str, work: Path, run: Run,
              python: Path | None = None) -> Said | None:
        """What top_line.py --check says of the room, each panel line by its name, height and Elev. None:
        it can't be read."""
        with staged(work, room_name, data) as (_, room):
            said = run(snapshot, self.script, ["--check", str(room)], python=python)
        if said.timed_out or said.exit_code != 0 or said.stderr.strip():
            return None
        hits: list[str] = []
        already, totals = 0, []
        for line in said.stdout.splitlines():
            hit, done, total = _HIT.fullmatch(line), _ALREADY.fullmatch(line), _TOTAL.fullmatch(line)
            if hit and hit[1] == room_name:
                hits.append(_identity(hit[2], hit[3], hit[4]))
            elif done and done[1] == room_name:
                already += 1
            elif total:
                totals.append(int(total[1]))
            elif line.strip():
                return None
        if totals != [len(hits)] or already != (0 if hits else 1):
            return None
        return said_of(hits, _keys(data))

    def cleared(self, before: Said, after: Said, elements: Sequence[Element]) -> bool:
        return said_cleared(before, after, {e.cab for e in elements})

    def names(self, said: Said, elements: Sequence[Element]) -> bool:
        return said_names(said, {e.cab for e in elements})

    # -- the card's words

    def words(self, room_name: str, tags: tuple[str, ...], elements: Sequence[Element] = (),
              data: bytes = b"") -> str:
        n = len(tags)
        said = (f"{room_words(room_name)}: {n} wall-hung panel{'s' if n != 1 else ''} ({', '.join(tags)})"
                f" {'don' if n != 1 else 'doesn'}'t hang on the room's top line; the fix moves"
                f" {'each one' if n != 1 else 'it'} up or down onto it")
        moves = [abs(float(e.after) - float(e.before)) for e in elements if _number(e.before) and _number(e.after)]
        tops = {round(float(e.after) + h, 1) for e in elements if (h := _height(data, e.cab)) is not None
                and _number(e.after)}
        if len(tops) == 1:
            said += f", the line at {_inches(next(iter(tops)))}"
        if moves:
            said += f", {'the furthest ' if n != 1 else ''}by {_inches(max(moves))}"
        return said


TOP_LINE = TopLine()


# ----- reading


def _identity(name: str, height: str, elev: str) -> str:
    return f"{name.rstrip()}|{height}|{elev}"


def _keys(data: bytes) -> dict[str, str]:
    """Each product by its CabNo, as top_line.py would name it: its name, height and Elev."""
    keys: dict[str, str] = {}
    for m, _ in products(data):
        a = attributes(m.group(0))
        cab = a.get("CabNo", "")
        try:
            said = _identity(a.get("ProdName", ""), f"{float(a['Height']):.0f}", f"{float(a['Elev']):.1f}")
        except (KeyError, ValueError):
            said = f"unread|{cab}"
        keys[cab] = said if cab not in keys else f"twice|{cab}"      # two products, one CabNo: never named
    return keys


def _height(data: bytes, cab: str) -> float | None:
    found = the_product(data, cab) if data else None
    if found is None:
        return None
    try:
        return float(attributes(found[0].group(0))["Height"])
    except (KeyError, ValueError):
        return None


def _number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _inches(mm: float) -> str:
    return templates._inches(round(mm, 1))
