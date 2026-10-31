r"""
A nest's .opt: what it says, and what it names set against what is on disk (P1b build spec 5.11).

Why it is this strict: it was written from Jim's docs (RULES 143, 705-726; BUILD 554, 565-568, 576,
696, 774-787) and how his own scripts read one, before any real one was here, and then tried on the
20 real .opt of 10 real jobs (2026-10-31, tests/test_real_nests.py). All 20 failed at first: they are
UTF-8 with Mozaik's version on the line before the XML, which no doc said. Now all 20 read, with the
sheets and runs Jim's own check counts. More nests will differ in ways 20 can't show, so whatever isn't
as these are raises OptUnreadable: never a pass, never a crash, and never a DO NOT CUT from a doubt
about parsing. His docs name the worst way to get this wrong: "decoding UTF-16 as UTF-8
yields a zero count that looks like a finding and is not" (BUILD 567). So a zero is never read as a
fact: a file with no parts and no sheets is not a nest with nothing in it, it is a file not understood.

Where his scripts settle a point, this reads as they read: a block with no Quan is one sheet, a block
with no Flipped is a board, a run with no name names nothing. What the 20 settled says "Measured"; what
they couldn't is marked ASSUMPTION, with what to check.

What it doesn't do: judge. Whether the sheet is the right size for the colour is sheet_size_check's,
and doubled housings br_cam_check's (rule 4: Jim's rules come from his toolkit). This only reads.
It gives no part's data to anyone: counts and program names. The parts themselves are read by
guards/design.py, through parsed_opt, for the nest-to-design match.

A file that may be in use is read by read_opt_file: only once a listing has shown it unchanged for
OPT_SETTLES_AFTER (guards/scan.py sees to that), hashing the same twice, and parsing (design 5, Reads).
"""
from __future__ import annotations

import hashlib
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PureWindowsPath

from ngrunner.guards.facts import OptFacts, OptSheet
from ngrunner.guards.reads import Unreadable, read_shared
from ngrunner.guards.tap import Tap

OPT_SETTLES_AFTER = timedelta(seconds=120)      # design 5, Reads
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")        # RULES 705-706
_SMALLEST_SHEET_MM = 200.0                      # under 8in is no sheet: an inch figure, read as mm
# Mozaik's version, on the line before the XML: 8 on 20 of 20 real files (2026-10-31). A version not
# seen is a form not seen: add one here only after reading real files of it.
KNOWN_VERSIONS = frozenset({"8"})
_VERSION_LINE = re.compile(r"(\d+)\r?\n(?=\s*<)")


class OptUnreadable(Exception):
    """A .opt that couldn't be read as Jim's docs describe one. str() is plain words. retry: it may
    read next time, as with a file still changing; otherwise the same bytes will say the same."""

    def __init__(self, words: str, *, retry: bool = False) -> None:
        super().__init__(words)
        self.retry = retry


@dataclass(frozen=True)
class Reconcile:
    """missing: programs the nest names that aren't in the folder, a real finding (RULES 724). extra:
    programs in the folder it doesn't name, only ever a log line, as 65 of 551 are hand-named."""

    missing: tuple[str, ...]
    extra: tuple[str, ...]


def read_opt(data: bytes) -> OptFacts:
    """What one .opt's bytes say. Raises OptUnreadable for anything not as documented."""
    text, encoding, version = _decoded(data)
    root = _parsed(text)
    parts = placed = holes = 0
    header: ET.Element | None = None
    sheets: list[OptSheet] = []
    runs: dict[str, None] = {}
    for element in root.iter():          # in the file's order, so a sheet follows its header
        name = _local_name(element)
        if name == "OptimizeMaterial":
            header = element
        elif name == "OptimizePart":
            parts += 1
        elif name == "OptimizePartLocation":
            placed += 1
        elif name == "OperationHole":
            holes += 1
        elif name == "OptimizeSheet":
            sheets.append(_sheet(element, header))
        elif name == "OptimizeGCodeRun" and _run_name(element):
            runs[_run_name(element)] = None
    facts = OptFacts(sha256=hashlib.sha256(data).hexdigest(), parts=parts, sheets=tuple(sheets),
                     encoding=encoding, placed=placed, runs=tuple(runs), holes=holes, version=version)
    _refuse_what_doesnt_add_up(facts)
    return facts


def parsed_opt(data: bytes) -> ET.Element:
    """A nest's XML root, decoded and parsed as read_opt does it, for guards/design.py, which reads
    its parts. Raises OptUnreadable for anything read_opt would refuse in the bytes themselves."""
    text, _, _ = _decoded(data)
    return _parsed(text)


def read_opt_file(path: Path, *, read: Callable[[Path], bytes] = read_shared) -> OptFacts:
    """A .opt that may be in use, once its listing has settled: hashing the same twice, and parsing
    (design 5, Reads). What can pass by itself says retry; bytes that won't parse wait for a change."""
    try:
        first, second = read(path), read(path)
    except Unreadable as exc:
        raise OptUnreadable(str(exc), retry=True) from exc
    if hashlib.sha256(first).digest() != hashlib.sha256(second).digest():
        raise OptUnreadable(f"{path.name} changed while it was being read.", retry=True)
    return read_opt(second)


def reconcile(opt: OptFacts, taps: Sequence[Tap]) -> Reconcile:
    """What the nest names against what is in the folder, both ways (RULES 722-726). Names compare
    with no regard to case, since .TAP and .tap both occur on one disk (build spec 5.4). Jim's own
    --taps compares them exactly, so one that differs only in case fails there and passes here."""
    on_disk = {tap.name.casefold() for tap in taps}
    named = {_base_name(name).casefold() for name in opt.runs}
    return Reconcile(
        missing=tuple(_base_name(name) for name in opt.runs
                      if _base_name(name).casefold() not in on_disk),
        extra=tuple(tap.name for tap in taps if tap.name.casefold() not in named))


# ----- the bytes


def _decoded(data: bytes) -> tuple[str, str, str]:
    """(text, encoding, version). Sniff the BOM and never assume (RULES 705-706, BUILD 565-568), as all
    of Jim's scripts do: a stray byte in a UTF-8 one is replaced, as sheet_size_check does, and its hash
    stays the file's. UTF-16 with no BOM decodes here as text full of NULs, which the XML parser would
    read by guessing from its first bytes, so it is refused first: no NUL belongs in a nest.
    Measured on 20 of 20 real files (2026-10-31): UTF-8 with no BOM, and a first line holding Mozaik's
    version, 8, before the XML. RULES 705's UTF-16 is in none of them. One leading line of digits is
    set aside, and a version not seen is a form not seen, so it is unreadable: never a guess."""
    try:
        text, encoding = ((data.decode("utf-16"), "utf-16") if data[:2] in _UTF16_BOMS
                          else (data.decode("utf-8-sig", errors="replace"), "utf-8"))
    except UnicodeDecodeError as exc:
        raise OptUnreadable("It isn't text in the form a nest file should be.") from exc
    if "\0" in text:
        raise OptUnreadable("It isn't text in the form a nest file should be.")
    version, text = _version_line(text)
    return text, encoding, version


def _version_line(text: str) -> tuple[str, str]:
    """Mozaik's version, and the XML after it. A file with no such line is read as RULES has one."""
    found = _VERSION_LINE.match(text)
    if found is None:
        return "", text
    if found[1] not in KNOWN_VERSIONS:
        raise OptUnreadable(f"It is a form of nest file NG Runner hasn't seen (version {found[1]}).")
    return found[1], text[found.end():]


def _parsed(text: str) -> ET.Element:
    """Measured: after its version line, each of the 20 is one well-formed XML document, its root the
    header, <OptimizeMaterial>. A half-written file is what this catches."""
    try:
        return ET.fromstring(text)
    except (ET.ParseError, ValueError) as exc:
        raise OptUnreadable("It is cut off, or isn't laid out as a nest file should be.") from exc


def _local_name(element: ET.Element) -> str:
    return str(element.tag).rsplit("}", 1)[-1]


# ----- the elements


def _sheet(element: ET.Element, header: ET.Element | None) -> OptSheet:
    """One <OptimizeSheet>, with its Quan and Flipped (RULES 707, 716). Its size is <OptimizeMaterial>'s
    Width and Length, in mm, which is what RULES 143 calls the header and sheet_size_check reads.
    Measured: every real block carries its own Width and Length, and a sheet added by hand is a block
    of its own size (Case B: 6 on 49x97, 3 on 61x109; Case H: 11 on 49x97, 6 on 49x120). The header's
    size is only a fallback. Jim's check reads the first header only, so it doesn't see those. A flip
    pass is a copy of the header holding the flipped block, inside its run (Cases C and D)."""
    return OptSheet(width_mm=_size(element, header, "Width"), length_mm=_size(element, header, "Length"),
                    quan=_quan(element), flipped=_flipped(element))


def _size(element: ET.Element, header: ET.Element | None, name: str) -> float:
    text = element.get(name, None if header is None else header.get(name))
    try:
        value = float(text)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise OptUnreadable(f"A sheet in it has no readable {name}.") from exc
    if not math.isfinite(value) or value < _SMALLEST_SHEET_MM:
        raise OptUnreadable(f"A sheet in it has a {name} that isn't a sheet's in mm.")
    return value


def _quan(element: ET.Element) -> int:
    """A block with no Quan is one sheet, as sheet_size_check counts it. Measured: all 141 real blocks
    say Quan="1". One that says something other than a whole number from 1 is unreadable."""
    text = element.get("Quan")
    if text is None:
        return 1
    if not text.isdecimal() or int(text) < 1:
        raise OptUnreadable("A sheet in it has no readable Quan.")
    return int(text)


def _flipped(element: ET.Element) -> bool:
    """Only Flipped="True" is a flip, and a block with none is a board, as sheet_size_check counts
    them. Measured: all 141 real blocks say "True" or "False". Read on sheet blocks only, as a part's
    location has a Flipped of its own. His check would read "true" as a board, so here it is
    unreadable."""
    text = element.get("Flipped", "False")
    if text not in ("True", "False"):
        raise OptUnreadable("A sheet in it doesn't say whether it is flipped.")
    return text == "True"


def _run_name(element: ET.Element) -> str:
    """What this run's Filename names; "" for none, as sheet_size_check only takes a run with one. A
    nest may be saved before it is posted. Measured: all 19 real nests with sheets name their programs,
    by file name with no path, with FullFilename beside it. One that names none can never pass."""
    return element.get("Filename", "").strip()


def _refuse_what_doesnt_add_up(facts: OptFacts) -> None:
    """A nest is parts, on sheets or not yet: <OptimizeSheet> is the only test of which (BUILD 784).
    Anything else is a file not understood, so it is never counted as zero. <OptimizePartLocation> is
    only counted: no script of Jim's reads one, so nothing rests on it.
    Measured: all 20 real nests hold <OptimizePart>, Case I's 21 with no sheet at all."""
    if facts.parts == 0:
        raise OptUnreadable("It holds no parts, so it isn't read as a nest.")
    if facts.sheets and facts.physical_sheets == 0:
        raise OptUnreadable("It has flip passes and no board.")


# ----- names


def _base_name(name: str) -> str:
    """The file's name, as sheet_size_check takes it from Filename, which may hold a path. Either slash
    works in PureWindowsPath. Whole, with its extension, since his check compares it with what is in
    the folder."""
    return PureWindowsPath(name).name
