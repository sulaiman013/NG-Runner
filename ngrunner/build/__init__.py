"""
P5's builder: a closet's walls and verticals, from the drawing a reader gives and Jim's rules.

Why its own package: P5a builds the rooms Jim's chat builds by hand today (design section 7, P5;
docs/p2/build-spec.md). A model reads the elevation drawings (docs/decisions.md, "P5's reader"), and
code places every part. placement.py holds the build choices a drawing doesn't show, each a pure
function citing the line of Jim's docs it ports. Nothing here writes a file yet.
"""
from __future__ import annotations
