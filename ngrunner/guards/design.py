r"""
The nest-to-design match: is a material's nest the saved design? (Design, "Nest guard and nest-to-design
match"; P1b build spec 5.3 and 5.11.)

Why it exists: on 10-20 two real nests were made from an unsaved state, and Jim's two nest checks can't
see that, as they read only the nest. A nest that isn't the saved design came from an unsaved state, or
the design changed after nesting, and either way it is "Not verified" (facts.NOT_VERIFIED), which
nest_state.decide makes a DO NOT CUT: red, to George, and in the way of Finish lock. Only "matches" is
ever part of a pass, and only with both of Jim's checks passing on the same files (decide, row 7).

Two readings of the saved design, which must agree:
- The cutlist is Jim's: des_cutlist, run through toolkit.run_tool from the adopted copy, on staged copies
  of the rooms (rulings R1 and R2). read_cutlist takes only what it really prints (measured on the 10 real
  jobs of 2026-10-31): "qty | colour | name | W | L" to 0.1 mm, then "TOTAL n". Anything else is
  CutlistUnreadable, and so could_not_verify.
- A STAND-IN, read_design, reads the rooms by bytes, as guards/bak.py does, for what des_cutlist doesn't
  print: where each part is (its room and cabinet, so it is named by tag), its operations, and the
  material or texture Jim set on it by hand. It takes the same parts by des_cutlist's own rules (room
  Quan x product Count x part Quan; no Metal; UsageType 0 only), and its count of each part must equal
  des_cutlist's, or nothing is concluded. It is a stand-in until Jim's toolkit prints these itself. It is
  not a model of how Mozaik lays parts out: it reads only what the design file holds.

What is compared, all of it measured on the 20 real nests of the 10 real jobs:
- Rooms: a nest names the rooms it was made from (RoomInfo). A saved room the nest lacks means the design
  changed after nesting (Case J's Room 4).
- Size and count: every part of every nest of the job against the saved design, counted across the
  job's nests, since one size may be split between two materials (Case B). A size within 0.05 mm is the
  same size: des_cutlist rounds to 0.1 mm, and panel_xcheck reports nothing of 0.05 or less.
- Place and operations: a nest part at its tag (R1C3; Room 0 by room alone, as a nest counts Room 0's
  products where the design numbers them), with every operation the design gives it there, value for
  value, numbers within the same 0.05. The equations beside them (X_Eq...) are left out: no nest carries
  one. A nest may hold more operations: Mozaik applies joint fastener templates at optimize
  (br_cam_check's own note), and doubled ones are br_cam_check's to find.
- Material, where the design carries one: a part's own material (MatORId) is its nest's (MaterialId), and
  its own texture (TextureORId) is its piece's (TextureID). Material that comes from the room's templates
  isn't read: that is how Mozaik lays parts out, and des_cutlist's colour is no stand-in for it. Measured:
  on Case I it names 15 of 22 parts by a texture none of them is nested on, from the first override in
  the room's block (it belongs to plastic parts).
- Counter tops, which des_cutlist reads from <Tops> and which carry no parts or operations in the design:
  size and count only.
A nest with no sheets holds parts not yet placed: it takes only what nested ones leave, and is never
itself judged a match. Its own verdict is SAVE_NEST.

Not compared: anything a nest holds outside its top-level <OptimizePart> (a flip pass's copy of them, a
<RemakePart> record), and a nest of the job not read by this pass changing on its own later (measured:
every job's nests come from one Optimizer run, so they change together, and each is judged on its own
change). The words name parts by tag only: names like "UEnd" are Mozaik's and the lint refuses them.

A room's identity (room_identity) is what a pass rests on, in place of the file's hash: the whole room
as it compares (numbers as numbers), less what a re-save of the same design changes. Measured on the 30
real .des/.bak pairs: 20 differ in bytes, des_cutlist prints the same cutlist from all 30, and the identity
is the same on 29. The 30th (Case G's Room 1) differs in groove Hide flags on 5 panels: the same cutlist,
a different design, and the nest's operations side with the .des.
"""
from __future__ import annotations

import hashlib
import math
import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from ngrunner.guards.bak import DesignUnreadable, canonical, parse_room
from ngrunner.guards.facts import (
    COULD_NOT_VERIFY, DESIGN_MATCHES, NOT_VERIFIED, CheckedFiles, DesignMatch, ToolRun,
)
from ngrunner.guards.opt import OptUnreadable, parsed_opt, read_opt
from ngrunner.guards.reads import Unreadable, read_shared
from ngrunner.toolkit import Snapshot, ToolkitError, run_tool, stage_files

if TYPE_CHECKING:
    from ngrunner.guards.nestfiles import ToCheck

CUTLIST_SCRIPT = "scripts/des_cutlist.py"
COUNTER_TOP = "Counter Top"             # des_cutlist's name for a <Top>
SIZE_TOLERANCE_MM = 0.05                # des_cutlist rounds to 0.1 mm; panel_xcheck: 0.05 is the same
_SLACK = 1e-6                           # 758.85 - 758.8 is 0.0500000000000682 in floats
_TAGS_SHOWN = 8

COULD_NOT_COMPARE = "Could not compare the nest with the saved design"
NOT_THE_DESIGN = "Not verified: the nest is not the saved design"

# What a re-save of the same design changes in a room (measured on the real .des/.bak pairs): two of
# the room's own counters, and the numbers of Room 0's products, which Mozaik renumbers on save. Nothing
# compared here reads them. The annotations are left out by bak.canonical.
_BOOKKEEPING = ("RoomNosDirty", "IdTagCount")
_ROOM_FILE = re.compile(r"Room(\d+)\.des")        # des_cutlist's own pattern, which cares about case
_TAG = re.compile(r"R(\d+)([CN])(\d+)")
_SIZE = re.compile(r"\d+\.\d")                    # print(round(x, 1)) of a size in mm
_QTY = re.compile(r"[1-9]\d*")
_TOTAL = re.compile(r"TOTAL (\d+)")


class CutlistUnreadable(Exception):
    """What des_cutlist gave back isn't what it prints. str() is plain words."""


# ----- what each side holds


@dataclass(frozen=True)
class CutRow:
    """One line of des_cutlist: how many, the colour it names, the part's name, W and L in mm."""

    qty: int
    colour: str
    name: str
    w_mm: float
    l_mm: float


@dataclass(frozen=True)
class Cutlist:
    rows: tuple[CutRow, ...]

    @property
    def total(self) -> int:
        return sum(row.qty for row in self.rows)


@dataclass(frozen=True)
class Op:
    """One operation: its kind (OperationHole...) and its values, less the equations, sorted."""

    kind: str
    values: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Piece:
    """One piece of a part, the design's or a nest's. tag: R1C3 or R0N2 on a nest, R1C3 or "Room 0" in
    the design. cab: None in Room 0 and on an N tag, as a nest counts those where the design numbers
    them. material, texture: a design part's MatORId and TextureORId, None unless Jim set one; a nest
    piece's nest MaterialId and its own TextureID."""

    tag: str
    room: int
    cab: str | None
    name: str
    w_mm: float
    l_mm: float
    ops: tuple[Op, ...] = ()
    material: str | None = None
    texture: str | None = None


@dataclass(frozen=True)
class NestFile:
    """One nest of the job: its file's name less .opt, the rooms it was made from, every piece of its
    own parts, how many boards it is on, and its material's number in Mozaik."""

    name: str
    rooms: tuple[int, ...]
    pieces: tuple[Piece, ...]
    physical_sheets: int
    material_id: str | None = None


# ----- des_cutlist's output


def read_cutlist(run: ToolRun) -> Cutlist:
    """What des_cutlist printed, line by line. Raises CutlistUnreadable for a run that didn't finish
    cleanly or any line it doesn't print, so a cutlist is never half read."""
    if run.timed_out or run.exit_code != 0 or run.stderr.strip():
        raise CutlistUnreadable("The cutlist tool did not finish cleanly.")
    lines = run.stdout.splitlines()
    if not lines or (total := _TOTAL.fullmatch(lines[-1])) is None:
        raise CutlistUnreadable("The cutlist has no total line at its end.")
    rows = tuple(_row(line) for line in lines[:-1])
    if int(total[1]) != sum(row.qty for row in rows):
        raise CutlistUnreadable("The cutlist's total doesn't add up.")
    return Cutlist(rows)


def _row(line: str) -> CutRow:
    fields = line.split(" | ")
    if len(fields) != 5:
        raise CutlistUnreadable("A cutlist line isn't in the form the tool prints.")
    qty, colour, name, w, l = fields
    if not _QTY.fullmatch(qty) or not name or name != name.strip() or not all(
            _SIZE.fullmatch(size) for size in (w, l)):
        raise CutlistUnreadable("A cutlist line isn't in the form the tool prints.")
    return CutRow(int(qty), colour, name, float(w), float(l))


# ----- the saved design, read by bytes: the stand-in


def room_number(name: str) -> int:
    """The room a file is, by des_cutlist's own pattern. Raises DesignUnreadable for any other name,
    as des_cutlist would leave that file out."""
    found = _ROOM_FILE.fullmatch(name)
    if found is None:
        raise DesignUnreadable("a room file isn't named as des_cutlist reads one")
    return int(found[1])


def room_numbers(rooms: Iterable[str]) -> tuple[int, ...]:
    return tuple(sorted(room_number(name) for name in rooms))


def read_design(rooms: Mapping[str, bytes]) -> tuple[Piece, ...]:
    """Every piece of every part des_cutlist counts, with its tag, operations and hand-set material.
    Raises DesignUnreadable for a room not as measured."""
    pieces: list[Piece] = []
    for name, data in sorted(rooms.items()):
        number = room_number(name)
        root = parse_room(data)
        room_quan = _whole(root.get("Quan", "1"))
        holder = root.find("Products")
        for product in (holder if holder is not None else ()):
            if product.tag == "Product":
                pieces += _product_pieces(number, room_quan, product)
    return tuple(pieces)


def _product_pieces(room: int, room_quan: int, product: ET.Element) -> list[Piece]:
    count = room_quan * _whole(product.get("Count", "1") or "1")
    cab = None if room == 0 else product.get("CabNo", "")
    tag = "Room 0" if room == 0 else f"R{room}C{cab}"
    pieces: list[Piece] = []
    for part in product.iter("CabProdPart"):
        if part.get("Type") == "Metal" or part.get("UsageType", "0") != "0":
            continue                        # hardware, and buyout drawer boxes: as des_cutlist does
        quan = count * _whole(part.get("Quan", "1") or "1")
        if quan <= 0:
            continue
        piece = Piece(tag=tag, room=room, cab=cab,
                      name=(part.get("ReportName") or part.get("Name") or "").strip(),
                      w_mm=_size(part.get("W"), DesignUnreadable),
                      l_mm=_size(part.get("L"), DesignUnreadable),
                      ops=_ops(part.find("PartOpsXml")), material=part.get("MatORId"),
                      texture=part.get("TextureORId"))
        pieces += [piece] * quan
    return pieces


def _whole(text: str) -> int:
    """A count as des_cutlist takes one: int(float(text))."""
    try:
        value = float(text)
    except ValueError:
        raise DesignUnreadable("a count in it isn't a number") from None
    if not math.isfinite(value):
        raise DesignUnreadable("a count in it isn't a number")
    return int(value)


def room_identity(name: str, data: bytes) -> str:
    """What a pass rests on for one room: the whole design as it compares, less what a re-save of the
    same design changes (module docstring). Raises DesignUnreadable."""
    number = room_number(name)
    root = parse_room(data)
    for flag in _BOOKKEEPING:
        root.attrib.pop(flag, None)
    if number == 0:
        holder = root.find("Products")
        for product in (holder if holder is not None else ()):
            product.attrib.pop("CabNo", None)
    return hashlib.sha256(repr(canonical(root)).encode("utf-8")).hexdigest()


# ----- a nest's own parts


def read_nest(name: str, data: bytes) -> NestFile:
    """A nest's top-level parts as pieces, the rooms it was made from and its boards. Raises
    OptUnreadable for a nest not as the 20 real ones are."""
    root = parsed_opt(data)
    rooms = tuple(sorted({_room_of(info) for info in root.findall("RoomInfo")}))
    if not rooms:
        raise OptUnreadable("It doesn't name the rooms it was made from.")
    material = root.get("MaterialId")
    pieces: list[Piece] = []
    for part in root.findall("OptimizePart"):        # never a flip pass's copy, which sits deeper
        pieces += _nest_pieces(part, material)
    if not pieces:
        raise OptUnreadable("It holds no parts, so it isn't read as a nest.")
    return NestFile(name=name, rooms=rooms, pieces=tuple(pieces),
                    physical_sheets=read_opt(data).physical_sheets, material_id=material)


def _room_of(info: ET.Element) -> int:
    text = info.get("Number", "")
    if not text.isdecimal():
        raise OptUnreadable("A room it names has no number.")
    return int(text)


def _nest_pieces(part: ET.Element, material: str | None) -> list[Piece]:
    found = _TAG.fullmatch(part.get("AssyNo", ""))
    name = part.get("Name", "").strip()
    quan = part.get("Quan", "1")
    if found is None or not name or not quan.isdecimal() or int(quan) < 1:
        raise OptUnreadable("A part in it isn't written as Mozaik writes one.")
    room = int(found[1])
    cab = found[3] if found[2] == "C" and room != 0 else None
    piece = Piece(tag=found[0], room=room, cab=cab, name=name,
                  w_mm=_size(part.get("Width"), OptUnreadable), l_mm=_size(part.get("Length"), OptUnreadable),
                  ops=_ops(part.find("Shape/Operations")), material=material,
                  texture=part.get("TextureID"))
    return [piece] * int(quan)


def _size(text: str | None, error: type[Exception]) -> float:
    try:
        value = float(text)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise error("A part in it has a size that isn't a number.") from None
    if not math.isfinite(value) or value < 0:
        raise error("A part in it has a size that isn't a number.")
    return value


def _ops(holder: ET.Element | None) -> tuple[Op, ...]:
    if holder is None:
        return ()
    return tuple(Op(child.tag, tuple(sorted((key, value) for key, value in child.attrib.items()
                                            if not key.lower().endswith("_eq"))))
                 for child in holder if child.tag.startswith("Operation"))


# ----- the match


def match(material: str, nests: Sequence[NestFile], cutlist: Cutlist, design: Sequence[Piece],
          saved_rooms: Iterable[int], *, checked: CheckedFiles | None) -> DesignMatch:
    """Whether the nest named material is the saved design, against every nest of its job. Only
    DESIGN_MATCHES passes. NOT_VERIFIED names every part that differs by tag. COULD_NOT_VERIFY: the
    nest isn't among those read or has no boards, or the two readings of the design disagree."""
    judged = next((nest for nest in nests if nest.name == material), None)
    pieces = [piece for piece in design if piece.name != COUNTER_TOP]
    if (judged is None or judged.physical_sheets <= 0
            or _counted(pieces) != _counted_rows(row for row in cutlist.rows if row.name != COUNTER_TOP)):
        return _could_not(checked)
    tops = [piece for row in cutlist.rows if row.name == COUNTER_TOP
            for piece in [Piece("", -1, None, row.name, row.w_mm, row.l_mm)] * row.qty]
    claims = _Claims(pieces, tops)
    ordered = [(nest.name, piece) for nest in sorted(nests, key=lambda nest: nest.physical_sheets <= 0)
               for piece in nest.pieces]                                       # nested ones first
    unfit = [(nest, piece) for nest, piece in ordered if not claims.fit(piece)]
    for nest, piece in unfit:
        claims.nearest(nest, piece)
    tags, tops_left = claims.left_over([nest for nest in nests if nest.physical_sheets > 0])
    tags |= claims.problems.get(judged.name, set())
    clauses = _room_clauses(set(saved_rooms), set(judged.rooms))
    if tops_left:
        clauses.append(f"{tops_left} counter {'top is' if tops_left == 1 else 'tops are'} in no nest")
    if not tags and not clauses:
        return DesignMatch(DESIGN_MATCHES, checked=checked)
    parts = tuple(sorted(tags, key=_tag_order))
    head = f"{NOT_THE_DESIGN} at {_listed(parts)}" if parts else NOT_THE_DESIGN
    return DesignMatch(NOT_VERIFIED, checked=checked, parts=parts, note="; ".join((head, *clauses)))


class _Claims:
    """Who took which piece of the design. First every nest piece that fits one exactly takes it (same
    name and size, at its own place, with its operations and material), so a wrong piece can never take
    a right one's place, whichever nest is read first. Then each piece left takes the nearest of its
    name and size, and is a problem of its nest, named by tag, as is one that finds none."""

    def __init__(self, pieces: list[Piece], tops: list[Piece]) -> None:
        self.pieces, self.tops = pieces, tops
        self.used, self.tops_used = [False] * len(pieces), [False] * len(tops)
        self.problems: dict[str, set[str]] = {}

    def fit(self, piece: Piece) -> bool:
        """Take the piece of the design this one is, exactly; False when there is none left."""
        if piece.name == COUNTER_TOP:
            free = self._same(self.tops, self.tops_used, piece)
            if free:
                self.tops_used[min(free, key=lambda i: _apart(self.tops[i], piece))] = True
            return bool(free)
        exact = next((i for i in self._same(self.pieces, self.used, piece)
                      if _same_place(self.pieces[i], piece) and _ops_covered(self.pieces[i].ops, piece.ops)
                      and _material_fits(self.pieces[i], piece)), None)
        if exact is not None:
            self.used[exact] = True
        return exact is not None

    def nearest(self, nest: str, piece: Piece) -> None:
        """A piece that fits nothing: it takes the nearest of its name and size, if any is left, and
        is a problem either way: not in the saved design, one too many, or at another place, with other
        operations or another material."""
        self._problem(nest, piece.tag)
        same = [] if piece.name == COUNTER_TOP else self._same(self.pieces, self.used, piece)
        if not same:
            return
        here = [i for i in same if _same_place(self.pieces[i], piece)]
        pick = min(here or same, key=lambda i: _apart(self.pieces[i], piece))
        self.used[pick] = True
        if not here:
            self._problem(nest, self.pieces[pick].tag)

    @staticmethod
    def _same(pool: list[Piece], used: list[bool], piece: Piece) -> list[int]:
        return [i for i, d in enumerate(pool)
                if not used[i] and d.name == piece.name and _same_size(d, piece)]

    def left_over(self, nested: Sequence[NestFile]) -> tuple[set[str], int]:
        """The design's pieces no nest took. One of a size some nest holds is that nest's problem: it
        holds fewer than the design. One in no nest at all is every material's: it can't be placed."""
        everyone: set[str] = set()
        for i, piece in enumerate(self.pieces):
            if self.used[i]:
                continue
            holders = [nest.name for nest in nested
                       if any(p.name == piece.name and _same_size(p, piece) for p in nest.pieces)]
            for holder in holders:
                self._problem(holder, piece.tag)
            if not holders:
                everyone.add(piece.tag)
        return everyone, self.tops_used.count(False)

    def _problem(self, nest: str, tag: str) -> None:
        self.problems.setdefault(nest, set()).add(tag)


def _counted(pieces: Iterable[Piece]) -> Counter[tuple[str, float, float]]:
    return Counter((piece.name, round(piece.w_mm, 1), round(piece.l_mm, 1)) for piece in pieces)


def _counted_rows(rows: Iterable[CutRow]) -> Counter[tuple[str, float, float]]:
    counted: Counter[tuple[str, float, float]] = Counter()
    for row in rows:
        counted[(row.name, row.w_mm, row.l_mm)] += row.qty
    return counted


def _same_size(a: Piece, b: Piece) -> bool:
    return _apart(a, b) <= SIZE_TOLERANCE_MM + _SLACK


def _same_place(design: Piece, nest: Piece) -> bool:
    return (design.room, design.cab) == (nest.room, nest.cab)


def _apart(a: Piece, b: Piece) -> float:
    return max(abs(a.w_mm - b.w_mm), abs(a.l_mm - b.l_mm))


def _ops_covered(design: Sequence[Op], nest: Sequence[Op]) -> bool:
    """Every operation of the design's piece is one of the nest piece's, each used once."""
    left = list(nest)
    for wanted in design:
        found = next((i for i, op in enumerate(left) if op.kind == wanted.kind
                      and _same_values(wanted.values, op.values)), None)
        if found is None:
            return False
        del left[found]
    return True


def _same_values(a: Sequence[tuple[str, str]], b: Sequence[tuple[str, str]]) -> bool:
    one, other = dict(a), dict(b)
    return one.keys() == other.keys() and all(_same_value(one[key], other[key]) for key in one)


def _same_value(a: str, b: str) -> bool:
    x, y = _number(a), _number(b)
    if x is not None and y is not None:
        return abs(x - y) <= SIZE_TOLERANCE_MM + _SLACK
    return a == b


def _number(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _material_fits(design: Piece, nest: Piece) -> bool:
    """Only where the design carries one: a material or texture Jim set on the part by hand."""
    return all(wanted is None or got is None or wanted == got
               for wanted, got in ((design.material, nest.material), (design.texture, nest.texture)))


def _could_not(checked: CheckedFiles | None) -> DesignMatch:
    return DesignMatch(COULD_NOT_VERIFY, checked=checked, note=COULD_NOT_COMPARE)


# ----- words


def _room_clauses(saved: set[int], made: set[int]) -> list[str]:
    return ([f"Room {n} is in the saved design and not in the nest" for n in sorted(saved - made)]
            + [f"Room {n} is in the nest and not in the saved design" for n in sorted(made - saved)])


def _tag_order(tag: str) -> tuple:
    return tuple(int(bit) if bit.isdigit() else bit for bit in re.split(r"(\d+)", tag))


def _listed(tags: Sequence[str]) -> str:
    shown = list(tags[:_TAGS_SHOWN])
    if len(tags) > _TAGS_SHOWN:
        return f"{', '.join(shown)} and {len(tags) - _TAGS_SHOWN} more"
    return shown[0] if len(shown) == 1 else f"{', '.join(shown[:-1])} and {shown[-1]}"


# ----- running it: des_cutlist from the adopted toolkit copy, on staged copies


def design_checks_from(snapshot: Snapshot, work: Path, *,
                       read: Callable[[Path], bytes] = read_shared) -> Callable[[ToCheck], DesignMatch]:
    """The match for one material, as watch.nest_checks runs it beside Jim's two checks: his
    des_cutlist on staged copies of the rooms the pass is to rest on, in a folder of its own under work.
    A staged room that isn't what was read, a nest of the job that won't read, or a tool that fails,
    is could_not_verify, never a match."""

    def run(job: ToCheck) -> DesignMatch:
        try:
            work.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix="design-", dir=work))
        except OSError:
            return _could_not(job.files)
        try:
            return _checked(snapshot, job, stage, read)
        except (ToolkitError, CutlistUnreadable, DesignUnreadable, OptUnreadable, Unreadable, OSError):
            return _could_not(job.files)
        finally:
            shutil.rmtree(stage, ignore_errors=True)        # made by this call

    return run


def _checked(snapshot: Snapshot, job: ToCheck, stage: Path, read: Callable[[Path], bytes]) -> DesignMatch:
    rooms_dir = stage / "rooms"
    names = [name for name, _ in job.files.room_hashes]
    if not names:
        raise DesignUnreadable("no room was read")
    staged = stage_files([job.job_dir / name for name in names], rooms_dir, read=read)
    rooms = {path.name: path.read_bytes() for path in staged}
    if tuple(sorted((name, room_identity(name, data)) for name, data in rooms.items())) != (
            job.files.room_hashes):
        raise DesignUnreadable("a room changed since it was read")
    nests = _job_nests(job, read)
    cutlist = read_cutlist(run_tool(snapshot, CUTLIST_SCRIPT, [str(rooms_dir)]))
    return match(job.opt_path.stem, nests, cutlist, read_design(rooms), room_numbers(rooms),
                 checked=job.files)


def _job_nests(job: ToCheck, read: Callable[[Path], bytes]) -> list[NestFile]:
    """Every nest in the job's folder, each read twice to the same bytes, the judged one the very
    bytes the pass is to rest on."""
    nests = []
    for path in sorted(job.job_dir.iterdir()):
        if not (path.is_file() and path.suffix.casefold() == ".opt"):
            continue
        data = read(path)
        if hashlib.sha256(read(path)).digest() != hashlib.sha256(data).digest():
            raise OptUnreadable(f"{path.name} changed while it was being read.")
        if path.name.casefold() == job.opt_path.name.casefold() and (
                hashlib.sha256(data).hexdigest() != job.files.opt_sha256):
            raise OptUnreadable("The nest changed since it was read.")
        nests.append(read_nest(path.stem, data))
    return nests
