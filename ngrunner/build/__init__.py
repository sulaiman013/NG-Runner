"""
P5's builder: a closet's walls and verticals, from the drawing a reader gives and Jim's rules.

Why its own package: P5a builds the rooms Jim's chat builds by hand today (design section 7, P5;
docs/p2/build-spec.md). A model reads the elevation drawings (docs/decisions.md, "P5's reader"), and
code places every part. placement.py holds the build choices a drawing doesn't show, each a pure
function citing the line of Jim's docs it ports. room.py is the room model, walls.py its walls from the
WALL/AREA table, and schedule.py each wall's panel schedule and the room (docs/decisions.md, "P5a
builder, slice 1"). writer.py turns a room with nothing left to verify into Room#.des in memory, from the
template job's Room1 and Jim's own products (products.py, desxml.py); gates.py reads the bytes back
against the model and his rules; and commit.py writes a room only when every gate passed, through
commit_write, into NG Runner's own shadow copy until P5 goes live (docs/decisions.md, "P5a builder,
slice 2").
"""
from __future__ import annotations
