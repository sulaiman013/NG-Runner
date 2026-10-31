"""
Made-up CNC programs for the tests that wire the guards: shaped as the real ones are (build spec 3.1),
so the real reader reads them. Every name in them is synthetic.
"""
from __future__ import annotations

START = ["G00 G17 G20 G40", "G49 G80", "G90 G61", "G28 Z0. W0.", "G90 G54"]
TOOL6 = ["M06 T6", "G43 H6 Z1.5100", "S12000 M03"]
END = ["G0 Z3.0000", "M09", "M05", "G28 Z0.0 W0.0", "G90", "G00 X0.0000 Y108.0000", "M200",
       "M30", "%"]


def _hole(x: float, y: float) -> list[str]:
    return [f"G0 X{x:.4f} Y{y:.4f} Z1.5100", f"G0 X{x:.4f} Y{y:.4f} Z1.0100",
            "G1 Z0.1300 F150.0", f"G0 X{x:.4f} Y{y:.4f} Z1.5100"]


def _cut_to(x: float, y: float) -> list[str]:
    return ["G0 X0.0000 Y0.0000 Z1.5100", "G1 Z0.5000 F100.0", f"G1 X{x:.4f} Y{y:.4f} F738.0",
            "G0 Z1.5100"]


def program(*, reach: tuple[float, float] = (48.75, 96.75), doubled: int = 0, mark: int = 0,
            whole: bool = True) -> bytes:
    """A program. reach: how far it cuts. doubled: how many cam holes it drills twice, 0.001in apart.
    mark: makes its bytes differ, so a re-post has another hash. whole: False cuts it off part-way,
    as a file half-synced is."""
    body = [line for n in range(doubled) for line in (*_hole(5.0 + 4 * n, 5.0),
                                                      *_hole(5.001 + 4 * n, 5.0))]
    lines = [*START, *TOOL6, *_hole(30.0, 40.0 + mark / 100), *body, *_cut_to(*reach)]
    return "\r\n".join([*lines, *(END if whole else ())]).encode("ascii")


CLEAN = program()
DOUBLED = program(doubled=8)
ON_61 = program(reach=(60.75, 108.75))
BOTH = program(reach=(60.75, 108.75), doubled=2)
HALF = program(whole=False)

