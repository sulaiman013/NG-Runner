"""
A room's colour as the writer needs it: its <BaseMatTempSel> run, lifted whole from a known-good room
already built in that colour and found at run time among the real Mozaik jobs, and its drawer front, from
the IC's GV code.

Why: slice 2 writes every value of a room but its colour, so every real room waited on it (docs/decisions.md,
"P5a builder, slice 2"). Jim's chat sets it by SCHEMA 46-85 and RULES 211-233, and his builders lift the run
from a job he built (build_jobs_0914.py 39-50; P04's own build script: "lifted WHOLESALE ... never
hand-author texture overrides"). Ported from his toolkit of 2026-10-31, never copied:

  the colour    the one IC colour code every board row of the design prints after its size
                (parts.printed_colour). None printed, two codes (a part in a second colour bands in its own,
                RULES 190, on slot 6 with its own Tops texture, which NG Runner doesn't write yet), Navy or a
                code the rules don't know (RULES 146): could not verify
  the family    SCHEMA's template set for it (SCHEMA 52-59, 79-85; RULES 210-216), by the brand material_lookup
                gives the colour (RULES 210-213): Egger for Classic White and Charcoal, Tafisa for the rest,
                AMWH for white on a job holding a white part built 1in or thicker (RULES 214, rules.job_brand).
                SCHEMA names no Arauco set (RULES 215: "Exact IDs in SCHEMA.md"), and a garage room is SCHEMA's
                garage column on the garage template, which NG Runner doesn't write: both could not verify
  the run       lifted whole, never authored, from the newest known-good job (KnownGood). A job is a Mozaik
                job folder (its JobDat.dat's ModifiedDate orders them, newest first), not open in Mozaik, never
                the one being built, passing Jim's standing material gate (RULES 274-279, BUILD 156-160,
                frozen job Z's build script: Room0's material categories are Room1's, and MatORId in no room
                file), whose rooms in that colour hold products and carry one run: SCHEMA's ids for the family
                (Jim's own verify scripts of P24 and P08 check the same), every texture override the colour's
                library texture (material_lookup.ALIASES), no MatORId. No such job: could not verify, and the
                room isn't written
  drawer front  NG H Drawer with pilot; NG VG Drawer with pilot when every drawer front (DW) IC prints for
                the room is GV. A mixed room takes the default and its GV fronts are flagged for review
                (RULES 230-233, SCHEMA 59): a real job built six GV fronts and two plain ones on H

The writer refuses a run that isn't Lifted, or whose room no longer holds it byte for byte (still_holds),
and gates.py's colour gate holds the written bytes to the lifted run again. Which job a room's colour came
from is NG Runner's own data (record_sources, the state table), never git's: it names a client.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ngrunner.build.desxml import XmlError, attrs, elements, one, start_tags
from ngrunner.build.room import Fact, given, review, unverified
from ngrunner.convert import rules
from ngrunner.convert.parts import BoardPart, board_part, printed_colour
from ngrunner.convert.summary_lines import SummaryLine
from ngrunner.convert.tracker_names import IC_WORDS
from ngrunner.guards.observer import LOCK_FILE, OPTIMIZER_LOCK

if TYPE_CHECKING:
    from ngrunner.store import Store

Family = Literal["Egger", "Tafisa", "AMWH"]
MATERIAL = "BaseMatTempSel"
H_FRONT = "NG H Drawer with pilot"  # RULES 230, SCHEMA 59: every room's default
VG_FRONT = "NG VG Drawer with pilot"  # RULES 231: a room IC marks GV
FRONTS = (H_FRONT, VG_FRONT)
DRAWER_FRONT = "DW"  # the IC item code of a drawer front
GRAIN_VERTICAL = "GV"  # SCHEMA 225
JOB_DATA = "JobDat.dat"
_ROOM_FILE = re.compile(r"Room(\d+)\.des", re.IGNORECASE)
_MODIFIED = re.compile(rb"<ModifiedDate>\s*([0-9]+(?:\.[0-9]+)?)\s*</ModifiedDate>")
SOURCES_KEY = "p5.colour_sources"  # the state table: which job each committed room's colour came from

# SCHEMA 52-58 and 85: each family's closet template ids by CategoryId (3 Cabinet, 1 Doors, 2 Drawers, 4 Tops,
# 5 Banding). The closet cabinet is 94, never the blank template's 88 (SCHEMA 82-84, RULES 216); Drawers 41 is
# the one shared category (SCHEMA 79-81); Tops is the family's own, 89, 53 or 82 (SCHEMA 61-68, 79-81, RULES 214-215).
# AMWH in a closet is SCHEMA 85's cabinet 91 and banding 86 with the AMWH column's doors, drawers and tops.
FAMILY_IDS: dict[Family, dict[str, str]] = {
    "Egger": {"3": "94", "1": "87", "2": "41", "4": "89", "5": "90"},
    "Tafisa": {"3": "76", "1": "75", "2": "41", "4": "82", "5": "77"},
    "AMWH": {"3": "91", "1": "65", "2": "41", "4": "53", "5": "86"},
}
# rules.job_brand's brand rows as SCHEMA's families. Arauco has none: SCHEMA names no Arauco set.
BRAND_FAMILY: dict[str, Family] = {"Egger Classic White": "Egger", "Egger Black U999": "Egger", "Tafisa": "Tafisa",
                                   "AMWH": "AMWH"}
# Each IC colour code's library texture, the MissingTextureName every override of its run carries. Mirrored
# from material_lookup.ALIASES (its code and texture columns; tests/test_build_colour_real.py ties them).
TEXTURES: dict[str, str] = {
    "SU": "Sunday Brunch-Inter__M2005", "WF": "Winter Fun_Houghton_Tafisa_M2004_Y", "RR": "River Rock TFS L538",
    "SW": "Southwestern-Fort_L535", "LG": "Spring Blossom_L543", "DA": "White Choc MMist", "WG": "Weekend Get Marq",
    "AP": "Apris Ski_M2015", "MJ": "Casting at First Light_M2007", "MO": "Chocolate Pear Tree-Mocha-_L444",
    "ML": "Moonlight_L761", "CC": "Black U999 ST19 Woodgrain", "WH": "Classic White",
    "IV": "Prism-SF210-Antique-White-Suede",
}
GARAGE = ("a garage room takes SCHEMA's garage set and is seeded from the garage template (BUILD 144), which "
          "NG Runner doesn't write yet")
UNCLEAR = "closet or garage could not be verified for this design, so its template set isn't known"
UNTOLD = "which parts lines are this design's can't be told from the summary, so its colour isn't read"
NO_MATERIALS = ("the room's colour sets its material templates (SCHEMA 46-85), lifted from a known-good job in "
                "that colour, and none was looked for")
NO_DRAWER_FRONT = ("the drawer front is NG H or NG VG Drawer with pilot by the IC's grain code (SCHEMA 59), and it "
                   "wasn't read")
HANDED_IN = ("the material run was handed in, not lifted from a known-good room: a room's run is never "
             "hand-authored (SCHEMA 75-76)")


# ----- the design's own lines


def lines_by_design(found: Mapping[str, Sequence[SummaryLine]],
                    names: Sequence[str]) -> dict[str, tuple[SummaryLine, ...] | None]:
    """Each page-1 design's parts list, from summary_lines.design_lines' lists (names: page 1's designs, as
    the summary check reads them). A parts page whose "Design:" line wraps gives its list under the name's
    start: it is the design's whose page-1 name alone starts so. A list that starts two names, or none, leaves
    every design it could be None: whose lines it holds can't be told, so its colour isn't read."""
    full = {" ".join(n.split()): n for n in names}
    held: dict[str, list[SummaryLine]] = {n: [] for n in names}
    unsure: set[str] = set()
    for key, lines in found.items():
        k = " ".join(key.split())
        if k in full:
            held[full[k]].extend(lines)
            continue
        starts = [n for nk, n in full.items() if nk.startswith(k)]
        if len(starts) == 1:
            held[starts[0]].extend(lines)
        else:
            unsure.update(starts or names)
    return {n: None if n in unsure else tuple(lines) for n, lines in held.items()}


# ----- the colour


@dataclass(frozen=True)
class Shade:
    """A room's colour as SCHEMA builds it: the IC code, its family and the library texture every override of
    its run carries (None for AMWH, whose textures are its own template's)."""
    code: str
    family: Family
    texture: str | None

    @property
    def ids(self) -> dict[str, str]:
        return FAMILY_IDS[self.family]

    @property
    def name(self) -> str:
        word = IC_WORDS.get(self.code, "a colour")
        return f"{word} ({self.code}) in AMWH" if self.family == "AMWH" else f"{word} ({self.code})"

    @property
    def key(self) -> str:
        return self.code.lower() + ("_amwh" if self.family == "AMWH" else "")


def _word(code: str) -> str:
    """A colour code in words, never echoed when the rules don't know it: two capitals may be anything."""
    if code in rules.NO_MATERIAL:
        return rules.NO_MATERIAL[code]
    if code in rules.COLOUR_BRANDS:
        return IC_WORDS.get(code, code)
    return "a code the rules don't know"


def shade_of(code: str, *, amwh: bool) -> Shade | str:
    """The shade an IC colour code is built in, or why it can't be said. amwh: the job holds a white part
    built 1in or thicker (rules.thick_white)."""
    if code in rules.NO_MATERIAL:
        name = rules.NO_MATERIAL[code]
        return f"{name} can't be placed: no material in the library carries it (RULES 146)"
    brand = rules.job_brand(code, amwh=amwh)
    if brand is None:
        return "its colour is a code the rules don't know"
    family = BRAND_FAMILY.get(brand)
    if family is None:
        return f"{_word(code)} is {brand}, and SCHEMA names no {brand} template set (RULES 215)"
    texture = None if family == "AMWH" else TEXTURES.get(code)
    if family != "AMWH" and texture is None:
        return f"{_word(code)} has no library texture in material_lookup's ALIASES"
    return Shade(code, family, texture)


def room_shade(design: Sequence[SummaryLine], job: Sequence[SummaryLine], *, branch: str) -> Fact[Shade]:
    """The room's shade from its design's own parts list. job: the summary's PULLED lines, for a white part
    built 1in or thicker anywhere on the job (RULES 214). branch: the design's, from P1a's branch_of."""
    if branch != "closet":
        return unverified(GARAGE if branch == "garage" else UNCLEAR)
    codes: Counter[str] = Counter()
    for line in design:
        if board_part(line) is not None and (code := printed_colour(line.desc)):
            codes[code] += line.qty
    if not codes:
        return unverified("no board row of the design prints a colour")
    if len(codes) > 1:
        words = sorted({_word(c) for c in codes})
        return unverified(f"its board rows print {len(codes)} colours ({', '.join(words)}): a part in a second "
                          "colour bands in its own on slot 6 with its own Tops (RULES 190), which NG Runner "
                          "doesn't write yet")
    (code,) = codes
    amwh = rules.thick_white([p for line in job if isinstance(p := board_part(line), BoardPart)])
    found = shade_of(code, amwh=amwh)
    if isinstance(found, str):
        return unverified(found)
    return given(found, "IC line")


def drawer_front(design: Sequence[SummaryLine]) -> Fact[str]:
    """The room's drawer front from its drawer fronts' grain codes (RULES 230-233, SCHEMA 59)."""
    fronts = [(line.qty, GRAIN_VERTICAL in line.desc.split()) for line in design if _is_front(line)]
    vertical = sum(qty for qty, gv in fronts if gv)
    if not vertical:
        return given(H_FRONT, "rule", "no drawer front IC prints is GV: the default (RULES 230)")
    if vertical == sum(qty for qty, _ in fronts):
        return given(VG_FRONT, "IC line", "every drawer front IC prints is GV (RULES 231)")
    return review(H_FRONT, "IC line", f"{vertical} of the room's {sum(q for q, _ in fronts)} drawer fronts are "
                                      "GV: a mixed room takes the default, its GV fronts flagged (RULES 233)")


def _is_front(line: SummaryLine) -> bool:
    found = board_part(line)
    return line.desc.split()[:1] == [DRAWER_FRONT] or (isinstance(found, BoardPart) and found.kind == DRAWER_FRONT)


# ----- a run, as it stands in a room


def material_run(text: str) -> str | None:
    """The room's <BaseMatTempSel> run as it stands, from the first to the end of the last, inside its
    RoomSet (build_jobs_0914.py 39-41); None when it has none or can't be read."""
    try:
        room_set = one(text, "RoomSet")
        found = elements(text, MATERIAL, within=(room_set.head_end, room_set.end))
    except XmlError:
        return None
    return text[found[0].start:found[-1].end] if found else None


def categories(run: str) -> list[tuple[str, str]]:
    """(CategoryId, RootTemplateId) of each <BaseMatTempSel> in order: what Jim's checks compare."""
    out = []
    try:
        for e in elements(run, MATERIAL):
            sel = start_tags(e.of(run), "MaterialTemplateSelection")
            root = attrs(sel[0]).get("RootTemplateId", "") if sel else ""
            out.append((attrs(e.head(run)).get("CategoryId", ""), root))
    except XmlError:
        return []
    return out


def textures(run: str) -> set[str]:
    """Every texture an override of the run names."""
    return {attrs(t).get("MissingTextureName", "") for t in start_tags(run, "TextureIdOverrideByPartType")}


def run_problem(run: str, shade: Shade) -> str | None:
    """Why a run isn't the shade's (module notes, "the run"); None when it is."""
    if "MatORId=" in run:
        return "it carries a per-part material override (MatORId)"
    cats = categories(run)
    ids = dict(cats)
    if not cats or len(ids) != len(cats) or ids != shade.ids:
        shown = ", ".join(f"{c}:{i}" for c, i in cats) or "none"
        want = ", ".join(f"{c}:{i}" for c, i in shade.ids.items())
        return f"its template ids are {shown}, not SCHEMA's {shade.family} set {want}"
    names = textures(run)
    if shade.texture is not None and names - {shade.texture}:
        return f"it carries {len(names - {shade.texture})} texture(s) other than {shade.texture}: a second colour"
    if shade.texture is None and names & set(TEXTURES.values()):
        return "it carries a colour's texture, which an AMWH run doesn't"
    return None


def sha256(run: str) -> str:
    return hashlib.sha256(run.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Lifted:
    """A material run exactly as it stands in a real room, with where it stands. job is local provenance: it
    names a client, so it is never printed by the tools or committed."""
    run: str
    shade: Shade
    job: Path
    room: str
    modified: float  # the job's ModifiedDate (JobDat.dat), Unix seconds
    sha256: str


def still_holds(lifted: object) -> str | None:
    """None when lifted is a Lifted whose run is still, byte for byte, its room's, and the shade's; else why not."""
    if not isinstance(lifted, Lifted):
        return HANDED_IN
    if sha256(lifted.run) != lifted.sha256:
        return "the material run isn't the one lifted: its hash differs (SCHEMA 75-76)"
    try:
        text = (lifted.job / lifted.room).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return "the room the material run was lifted from can't be read now"
    if material_run(text) != lifted.run:
        return "the room the material run was lifted from no longer holds it byte for byte"
    return run_problem(lifted.run, lifted.shade)


# ----- the real jobs


def job_modified(folder: Path) -> float | None:
    """The job's ModifiedDate from its JobDat.dat, the one value read from that file (it holds the client's
    name and address); None when it has none: then the folder is no Mozaik job."""
    try:
        found = _MODIFIED.search((folder / JOB_DATA).read_bytes())
    except OSError:
        return None
    return float(found[1]) if found else None


def room_files(folder: Path) -> dict[str, str] | None:
    """The job's Room<n>.des files by name, decoded; None when one can't be read."""
    out = {}
    try:
        for path in sorted(folder.iterdir()):
            if path.is_file() and _ROOM_FILE.fullmatch(path.name):
                out[path.name] = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return out


def room_number(name: str) -> int:
    found = _ROOM_FILE.fullmatch(name)
    return int(found[1]) if found else -1


def standing_gate(rooms: Mapping[str, str]) -> str | None:
    """Jim's standing material gate on a job (RULES 279, BUILD 156-160; frozen job Z's build script): Room0's material
    categories are Room1's, and MatORId appears in no room file. None when it passes."""
    if any("MatORId=" in text for text in rooms.values()):
        return "a room file carries a per-part material override (MatORId)"
    room0 = next((t for n, t in rooms.items() if room_number(n) == 0), None)
    room1 = next((t for n, t in rooms.items() if room_number(n) == 1), None)
    if room0 is None or room1 is None:
        return "it has no Room0 or no Room1"
    if categories(material_run(room0) or "") != categories(material_run(room1) or ""):
        return "Room0's material categories aren't Room1's"
    return None


def _has_products(text: str) -> bool:
    return re.search(r"<Product\s", text) is not None


class KnownGood:
    """The real Mozaik jobs a room's colour is lifted from, newest first (module notes, "the run"). jobs: the
    folders to look in (Mozaik Shared\\Jobs's, job_folders); exclude: the job being built, by folder name. Each
    shade is looked for once."""

    def __init__(self, jobs: Iterable[Path], *, exclude: Iterable[str] = ()) -> None:
        self._jobs = list(jobs)
        self._exclude = {name.casefold() for name in exclude}
        self._order: list[tuple[float, Path]] | None = None
        self._found: dict[str, Fact[Lifted]] = {}

    def newest_first(self) -> list[tuple[float, Path]]:
        if self._order is None:
            dated = []
            for folder in self._jobs:
                if folder.name.casefold() in self._exclude or not folder.is_dir():
                    continue
                modified = job_modified(folder)
                if modified is not None:
                    dated.append((modified, folder))
            self._order = sorted(dated, key=lambda d: (-d[0], d[1].name.casefold()))
        return self._order

    def find(self, shade: Shade) -> Fact[Lifted]:
        if shade.key not in self._found:
            self._found[shade.key] = self._look(shade)
        return self._found[shade.key]

    def _look(self, shade: Shade) -> Fact[Lifted]:
        why: Counter[str] = Counter()
        for modified, folder in self.newest_first():
            if any((folder / lock).exists() for lock in (LOCK_FILE, OPTIMIZER_LOCK)):
                why["open in Mozaik"] += 1
                continue
            rooms = room_files(folder)
            if rooms is None:
                why["unreadable"] += 1
                continue
            runs = {name: run for name, text in rooms.items()
                    if room_number(name) >= 1 and _has_products(text) and (run := material_run(text)) is not None
                    and run_problem(run, shade) is None}
            if not runs:
                continue
            if standing_gate(rooms) is not None:
                why["standing gate"] += 1
                continue
            if len(set(runs.values())) > 1:
                why["two runs"] += 1
                continue
            room = min(runs, key=room_number)
            return given(Lifted(runs[room], shade, folder, room, modified, sha256(runs[room])), "rule",
                         f"lifted whole from the newest known-good job built in {shade.name} (SCHEMA 75-76)")
        return unverified(_none_found(shade, why))


def _none_found(shade: Shade, why: Counter[str]) -> str:
    out = f"no known-good job is built in {shade.name} (SCHEMA 75-76)"
    held = why["standing gate"] + why["two runs"]
    if held:
        out += (f": {held} job{'s carry' if held != 1 else ' carries'} its run, and none passes Jim's standing "
                "material gate with one run (Room0's categories are Room1's, no MatORId in any room: RULES 279)")
    if why["open in Mozaik"] or why["unreadable"]:
        out += f"; {why['open in Mozaik'] + why['unreadable']} job(s) open in Mozaik or unreadable were passed over"
    return out


def job_folders(jobs_root: Path) -> list[Path]:
    """Every folder in Mozaik Shared\\Jobs: the jobs KnownGood looks in."""
    try:
        return sorted(p for p in jobs_root.iterdir() if p.is_dir())
    except OSError:
        return []


# ----- what the writer is given


@dataclass(frozen=True)
class RoomSettings:
    """The room's colour as the writer needs it: its material run, lifted (SCHEMA 75-78), and its drawer front
    (SCHEMA 59). Unsettled, the room waits, with the reason."""
    materials: Fact[Lifted] = unverified(NO_MATERIALS)
    drawer_front: Fact[str] = unverified(NO_DRAWER_FRONT)

    def reviews(self) -> tuple[str, ...]:
        """What the colour writes that Jim's rules flag: a mixed room's GV fronts (RULES 233)."""
        front = self.drawer_front
        return (f"room: drawer front: {front.why}",) if isinstance(front, Fact) and front.status == "review" else ()


def settle(design: Sequence[SummaryLine] | None, job: Sequence[SummaryLine], *, branch: str,
           sources: KnownGood) -> RoomSettings:
    """The room's colour: its shade from its design, the run from the newest known-good job in it, the drawer
    front from its drawer fronts. design: the design's parts list (lines_by_design; None: it can't be told);
    job: the summary's PULLED lines; branch: the design's (closet, garage or unclear)."""
    if design is None:
        return RoomSettings(unverified(UNTOLD), unverified(UNTOLD))
    shade = room_shade(design, job, branch=branch)
    materials: Fact[Lifted] = unverified(shade.why) if shade.value is None else sources.find(shade.value)
    return RoomSettings(materials, drawer_front(design))


# ----- where each committed room's colour came from: NG Runner's own data


def record_sources(store: Store, job: str, rooms: Iterable[tuple[str, RoomSettings]], *, at: datetime) -> None:
    """Which job each committed room's run was lifted from, in the state table (SOURCES_KEY), by the room's
    place in NG Runner's copy. Local only: it names clients, so it never reaches git or a message."""
    from ngrunner.store import get_state, set_state
    from ngrunner.store.db import iso

    held = json.loads(get_state(store, SOURCES_KEY) or "{}")
    for name, settings in rooms:
        lifted = settings.materials.value
        if isinstance(lifted, Lifted):
            held[f"{job}\\{name}"] = {"colour": lifted.shade.code, "family": lifted.shade.family,
                                       "from_job": lifted.job.name, "from_room": lifted.room,
                                       "run_sha256": lifted.sha256, "at": iso(at, "at")}
    set_state(store, SOURCES_KEY, json.dumps(held, ensure_ascii=False, sort_keys=True))


def recorded_sources(store: Store) -> dict[str, dict[str, str]]:
    from ngrunner.store import get_state

    return json.loads(get_state(store, SOURCES_KEY) or "{}")
