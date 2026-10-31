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
    " ".join([INSTRUCTIONS, json.dumps(SCHEMA), _SCAFFOLD, *UNREADABLE, *FIELDS])))


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


def build(wall_id: str, target: Pair, worked: Sequence[Pair], deny: prep.DenyList,
          worked_deny: Mapping[str, prep.DenyList] | None = None) -> WallRequest:
    """
    The request for target, with worked walls from other jobs; raises ContainmentError. deny is the
    target job's deny list; worked_deny gives each worked wall's job its own (deny when absent), as
    a text can only carry details of the summary it came from.
    """
    for w in worked:
        if w.case == target.case or w.crop.sha256 == target.crop.sha256:
            raise ContainmentError(f"{wall_id}: a worked wall must be held out of the target's job")
    examples = tuple(Example(w.case, Turn(_image(w.crop), wall_text(w.crop)),
                             json.dumps(answer_of(w), separators=(",", ":")),
                             (worked_deny or {}).get(w.case, deny)) for w in worked)
    req = WallRequest(wall_id, INSTRUCTIONS, examples, Turn(_image(target.crop), wall_text(target.crop)),
                      SCHEMA, deny)
    assert_contained(req)
    return req


def assert_contained(req: WallRequest) -> None:
    """Raise ContainmentError unless every image is a prepared crop and no text hits the deny list."""
    images = [("target", req.target.image)] + [(f"worked wall {k + 1}", e.turn.image)
                                                for k, e in enumerate(req.examples)]
    for where, image in images:
        if hashlib.sha256(image.png).hexdigest() != image.sha256:
            raise ContainmentError(f"{req.wall_id}: the {where} image's bytes do not match its hash")
        if not prep.was_prepared(image.sha256):
            raise ContainmentError(f"{req.wall_id}: the {where} image was not prepared and checked here")
    if req.instructions != INSTRUCTIONS or json.dumps(req.schema, sort_keys=True) != json.dumps(SCHEMA, sort_keys=True):
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
                e.turn.text, "--- its answer ---", e.answer]
    img = req.target.image
    out += ["--- the wall to read ---",
            f"[image: {img.pixels[0]} x {img.pixels[1]} px PNG, {len(img.png)} bytes, sha256 {img.sha256}]",
            req.target.text, "--- schema ---", json.dumps(req.schema, indent=1)]
    return "\n".join(out)
