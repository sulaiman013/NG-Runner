"""
What the reader evaluation sends for one wall, and the checks that run before anything is sent.

Why a module of its own: Jim's consent (decision 10, 2026-10-31) covers elevation crops with no
text over them and the wall's parts-list text, never a page, a name or an address. So a request is
built here from those two things only, and assert_contained() runs on every request, in build()
and again in each provider immediately before it sends:

  images  each one's bytes hash to the sha256 it carries, and prep produced that crop in this
          process (prep.was_prepared): nothing else can be sent
  fixed   the instructions and the schema are exactly the reviewed constants below, nothing else
  text    the target's text, and every worked wall's text and answer, are scanned with the deny
          list of the summary each came from (prep.DenyList): one hit blocks the request, and the error names the kind of field and
          where, never the value. Words of our own fixed text (FIXED_WORDS) are skipped: they are in
          every payload anyway, and page 1's free text uses them ("all verticals where shelves
          are"), which would otherwise block whole jobs for nothing. The cost of that: a client
          whose name is one of those words (say "Box") would not be caught in a parts line
  held out  a worked wall never comes from the target's own job

What a request holds (design 3.6; decision 17): the instructions in Jim's terms, 3 to 5 worked
walls (crop, parts lines and the as-cut answer) from jobs outside the test set, the target's crop
and parts lines with the VT heights allowed, and one fixed schema (every property required,
"unclear" and "none of these" options, no reasoning fields). The allowed values are given in the
text and checked in code (scoring.py), so one schema serves every wall and caches.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from tools.reader_eval import prep
from tools.reader_eval.pairing import HEIGHT_TOL_IN, Pair

INSTRUCTIONS = """\
You read one wall's elevation drawing from an IC summary, the drawing a closet designer makes for \
the shop. The wall is drawn as a box. Up both sides runs a ruler in inches, 0" at the floor. Under \
the box, WIDTH gives each bay's inside width and DEPTH its depth.

Read the verticals (VT): the 3/4" upright panels the shelves hang between. Report every vertical \
drawn on this wall, left to right, with:
- x_in: inches from the inside of the box's left line to the vertical's left face. Each bay's \
WIDTH, plus 3/4" for each vertical before it, adds up to it.
- bottom_in and top_in: where the vertical starts and ends, read on the ruler.
- height: which VT height from "VT heights allowed" the vertical is; "none of these" if it is none \
of them; "unclear" if you can't tell.
- radius: "yes" for a VT RAD, whose front edge is drawn clipped at both ends so it looks shorter \
than its back edge (report it once, at its full length); "no" for a square one; "unclear" if you \
can't tell.
- unsure: each field above you are not sure of, and "exists" if you are not sure the vertical is \
there at all.

What the shop knows about these drawings:
- A vertical with no notch code (NN, ND or none) stands on the floor. One with a notch code (FN, \
LN or RN) hangs on the wall, its bottom above the floor.
- A stacked tower is a wall-hung vertical above a floor vertical at the same x: report both, the \
lower one first.
- The parts list gives the set of verticals on the wall, never their order: the drawing gives the \
order.
- A wall can have no verticals at all: then report none.

Give a number only when you read it off the drawing. Never guess: give null and list the field in \
unsure. Set count_unsure when you are not sure how many verticals there are, and list in \
unreadable anything that stops you reading the wall."""

RULES_BLOCK = """\
Rules the shop builds by, from its own build notes:
- The elevation assigns verticals to positions; the parts list does not. Walk the drawing left to \
right and match each drawn vertical to a parts line by its drawn length.
- Read x from the inside of the box's left line. A vertical tight to a wall has its outer face on \
the box line, so it reads one face short: give the x where its left face is drawn.
- A radius vertical's front edge is clipped by the corner radius at both ends, so a 47.4 reads about \
40 long: classify it by its longer back edge, and count it once.
- A stacked tower is an upper wall-hung run and a lower floor run at the same x, between two \
full-height verticals: report both.
- Floor verticals stand on 0. A vertical whose run starts well above the floor is wall-hung: read \
its drawn top and bottom.
- Do not infer what the shop decides when it builds the job: the room's top line (where wall-hung \
verticals hang) and where the run starts along the wall are set then. Give what is drawn, and list \
a value as unsure when the drawing does not show it plainly."""

TILES_BLOCK = """\
After the whole wall come enlarged tiles of the same drawing: first the WIDTH and DEPTH strip, then \
one tile around each vertical, left to right. They are parts of the one drawing, not other walls."""

BAYS_BLOCK = """\
Also give bays: the WIDTH values written under the box, left to right, in inches as printed (null \
for one you can't read)."""

NOTE_WORDS = ("verticals, left to right, at x; each x is every bay's WIDTH before it plus 3/4 for each "
              "vertical before it. The first vertical stands tight to the wall at x 0. Floor verticals "
              "stand on 0 (tops). Wall-hung verticals hang from to. The VT RAD is the one at x: its "
              "front edge is drawn clipped at both ends. A stacked tower at x: the lower vertical first. "
              "No verticals on this wall: report none. How it was read:")

_NUMBER_OR_NULL = {"anyOf": [{"type": "number"}, {"type": "null"}]}
FIELDS = ("x_in", "bottom_in", "top_in", "height", "radius")
UNREADABLE = ("more than one wall", "too small to read", "verticals hidden", "no drawing", "other")
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verticals": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "x_in": _NUMBER_OR_NULL, "bottom_in": _NUMBER_OR_NULL, "top_in": _NUMBER_OR_NULL,
                "height": {"type": "string"},
                "radius": {"type": "string", "enum": ["yes", "no", "unclear"]},
                "unsure": {"type": "array", "items": {"type": "string", "enum": [*FIELDS, "exists"]}},
            },
            "required": [*FIELDS, "unsure"],
        }},
        "count_unsure": {"type": "boolean"},
        "unreadable": {"type": "array", "items": {"type": "string", "enum": list(UNREADABLE)}},
    },
    "required": ["verticals", "count_unsure", "unreadable"],
}


_SCAFFOLD = ("Wall width: unknown. in. Wall height: Parts list for this wall (quantity | description): "
             "VT heights allowed: none Verticals on the parts list: of them VT RAD:")
# Our own fixed words. They are in every payload whatever the summary says, so a page-1 word that is
# also one of them carries nothing about the client, and the deny scan skips it (prep.DenyList).
FIXED_WORDS = frozenset(w.casefold() for w in prep._WORD.findall(
    " ".join([INSTRUCTIONS, json.dumps(SCHEMA), _SCAFFOLD, *UNREADABLE, *FIELDS,
              RULES_BLOCK, TILES_BLOCK, BAYS_BLOCK, NOTE_WORDS, "bays"])))


SCHEMA_BAYS: dict[str, Any] = {**SCHEMA, "properties": {**SCHEMA["properties"],
                                                       "bays": {"type": "array", "items": _NUMBER_OR_NULL}},
                                "required": [*SCHEMA["required"], "bays"]}


def instructions(*, rules: bool = False, tiles: bool = False, bays: bool = False) -> str:
    """The reviewed instructions, with the blocks a tuning variant switches on (tuning.py)."""
    return "\n\n".join([INSTRUCTIONS, *(b for b, on in ((RULES_BLOCK, rules), (TILES_BLOCK, tiles),
                                                        (BAYS_BLOCK, bays)) if on)])


REVIEWED_INSTRUCTIONS = frozenset(instructions(rules=r, tiles=t, bays=b)
                                  for r in (False, True) for t in (False, True) for b in (False, True))
REVIEWED_SCHEMAS = frozenset(json.dumps(x, sort_keys=True) for x in (SCHEMA, SCHEMA_BAYS))


class ContainmentError(Exception):
    """A request carries something that may not leave the PC; it names where, never the value."""


@dataclass(frozen=True)
class Image:
    png: bytes = field(repr=False)
    sha256: str
    pixels: tuple[int, int]


@dataclass(frozen=True)
class Turn:
    image: Image
    text: str
    tiles: tuple[Image, ...] = ()  # enlarged parts of the same crop (prep.tiles), sent after it


@dataclass(frozen=True)
class Example:
    case: str
    turn: Turn
    answer: str  # the as-cut answer, as JSON in the schema's shape
    deny: prep.DenyList = field(default_factory=prep.DenyList, repr=False, compare=False)  # its own job's


@dataclass(frozen=True)
class WallRequest:
    wall_id: str  # case letter and page, e.g. "F/4": never a name
    instructions: str
    examples: tuple[Example, ...]
    target: Turn
    schema: Mapping[str, Any]
    deny: prep.DenyList = field(repr=False, compare=False)


def _fmt(value: float) -> str:
    return f"{value:g}"


def wall_text(crop: prep.WallCrop) -> str:
    """The wall's text: its size, its parts lines, and the VT heights allowed."""
    size = "Wall width: unknown." if crop.width_in is None else f"Wall width: {_fmt(crop.width_in)} in."
    if crop.height_in is not None:
        size += f" Wall height: {_fmt(crop.height_in)} in."
    lines = [size, "Parts list for this wall (quantity | description):",
             *(p.as_text() for p in crop.parts),
             f"VT heights allowed: {', '.join(crop.vt_heights) if crop.vt_heights else 'none'}",
             f"Verticals on the parts list: {crop.vt_count}, of them VT RAD: {crop.vt_rad_count}."]
    return "\n".join(lines)


def _height_label(height_in: float, allowed: Sequence[str]) -> str:
    near = [a for a in allowed if abs(float(a) - height_in) <= HEIGHT_TOL_IN]
    return min(near, key=lambda a: abs(float(a) - height_in)) if near else "none of these"


def answer_of(pair: Pair) -> dict[str, Any]:
    """The as-cut answer for a paired wall, in the schema's shape: a worked wall's answer."""
    allowed = pair.crop.vt_heights
    return {"verticals": [{"x_in": round(p.x_in, 2), "bottom_in": round(p.bottom_in, 2),
                           "top_in": round(p.top_in, 2), "height": _height_label(p.height_in, allowed),
                           "radius": "yes" if p.radius else "no", "unsure": []} for p in pair.wall.panels],
            "count_unsure": False, "unreadable": []}


def _image(crop: prep.WallCrop) -> Image:
    return Image(crop.png, crop.sha256, crop.pixels)


def _turn(crop: prep.WallCrop, text: str, tiles: bool) -> Turn:
    return Turn(_image(crop), text,
                tuple(Image(t.png, t.sha256, t.pixels) for t in prep.tiles(crop)) if tiles else ())


def build(wall_id: str, target: Pair, worked: Sequence[Pair], deny: prep.DenyList,
          worked_deny: Mapping[str, prep.DenyList] | None = None, *, instructions: str = INSTRUCTIONS,
          schema: Mapping[str, Any] = SCHEMA, notes: Mapping[tuple[str, int], str] | None = None,
          tiles: bool = False, worked_tiles: bool = True) -> WallRequest:
    """
    The request for target, with worked walls from other jobs; raises ContainmentError. deny is the
    target job's deny list; worked_deny gives each worked wall's job its own (deny when absent), as
    a text can only carry details of the summary it came from.
    """
    for w in worked:
        if w.case == target.case or w.crop.sha256 == target.crop.sha256:
            raise ContainmentError(f"{wall_id}: a worked wall must be held out of the target's job")
    def text(w: Pair) -> str:
        note = (notes or {}).get((w.case, w.crop.page))
        return wall_text(w.crop) + (f"\nHow it was read: {note}" if note else "")
    examples = tuple(Example(w.case, _turn(w.crop, text(w), tiles and worked_tiles),
                             json.dumps(answer_of(w), separators=(",", ":")),
                             (worked_deny or {}).get(w.case, deny)) for w in worked)
    req = WallRequest(wall_id, instructions, examples, _turn(target.crop, wall_text(target.crop), tiles),
                      schema, deny)
    assert_contained(req)
    return req


def assert_contained(req: WallRequest) -> None:
    """Raise ContainmentError unless every image is a prepared crop and no text hits the deny list."""
    images = [("target", req.target.image), *(("target tile", t) for t in req.target.tiles)]
    for k, e in enumerate(req.examples):
        images += [(f"worked wall {k + 1}", e.turn.image), *((f"worked wall {k + 1} tile", t) for t in e.turn.tiles)]
    for where, image in images:
        if hashlib.sha256(image.png).hexdigest() != image.sha256:
            raise ContainmentError(f"{req.wall_id}: the {where} image's bytes do not match its hash")
        if not prep.was_prepared(image.sha256):
            raise ContainmentError(f"{req.wall_id}: the {where} image was not prepared and checked here")
    if req.instructions not in REVIEWED_INSTRUCTIONS or json.dumps(req.schema, sort_keys=True) not in REVIEWED_SCHEMAS:
        raise ContainmentError(f"{req.wall_id}: the instructions or the schema are not the reviewed text")
    texts = [("target text", req.target.text, req.deny)]
    for k, e in enumerate(req.examples):
        texts += [(f"worked wall {k + 1} text", e.turn.text, e.deny),
                  (f"worked wall {k + 1} answer", e.answer, e.deny)]
    for where, text, deny in texts:
        if kinds := deny.hits(text, ignore=FIXED_WORDS):
            raise ContainmentError(f"{req.wall_id}: the {where} repeats a word from: {', '.join(kinds)}")


def dry_run(req: WallRequest, settings: str = "") -> str:
    """Exactly what would be sent, with each image as its size and hash; nothing is called."""
    out = [f"=== DRY RUN {req.wall_id}: nothing is sent ===", settings] if settings else [
        f"=== DRY RUN {req.wall_id}: nothing is sent ==="]
    out += ["--- instructions ---", req.instructions]
    for k, e in enumerate(req.examples, 1):
        img = e.turn.image
        out += [f"--- worked wall {k} (job {e.case}) ---",
                f"[image: {img.pixels[0]} x {img.pixels[1]} px PNG, {len(img.png)} bytes, sha256 {img.sha256}]",
                *(f"[tile: {t.pixels[0]} x {t.pixels[1]} px PNG, sha256 {t.sha256}]" for t in e.turn.tiles),
                e.turn.text, "--- its answer ---", e.answer]
    img = req.target.image
    out += ["--- the wall to read ---",
            f"[image: {img.pixels[0]} x {img.pixels[1]} px PNG, {len(img.png)} bytes, sha256 {img.sha256}]",
            *(f"[tile: {t.pixels[0]} x {t.pixels[1]} px PNG, sha256 {t.sha256}]" for t in req.target.tiles),
            req.target.text, "--- schema ---", json.dumps(req.schema, indent=1)]
    return "\n".join(out)
