"""
Made-up nest files for the tests that read and wire them. No real .opt has been read on this PC
(docs/p1b/build-spec.md, 3.6), so every shape here is what Jim's words and his own parsers say one holds,
and is an assumption a real file could break:

    RULES 705-706, BUILD 565-568   UTF-16LE with a BOM on some, plain UTF-8 on others
    sheet_size_check               <OptimizeMaterial> is the header: Width, Length, DisplayName, in mm
    RULES 707, BUILD 576, 786      <OptimizeSheet>, each with a Quan to sum
    RULES 716-721                  Flipped="True" is the same board turned over
    RULES 722-726, cnc_job_sheet   <OptimizeGCodeRun Filename="..."> inside the sheet it cuts
    opt_readback, br_cam_check     <OptimizePart> holds its own <OperationHole>, X, Y, Diameter, FlipSideOp
    BUILD 696, 776-778             parts with no sheets and no locations: not nested
    BUILD 781-784                  RunId="-1" says nothing

The toolkit's names are of Jim's scripts, read at the copy of 2026-10-31 and never run. The root's name
is in neither, so nothing may rest on it. Every name here is synthetic.
"""
from __future__ import annotations

from collections.abc import Sequence

CLASSIC_MM = (1244.6, 2463.8)      # BUILD 554: 49 x 97
EGGER_MM = (1549.4, 2768.6)        # BUILD 555: 61 x 109
_RUNS_GO_HERE = "<!-- the runs -->"


def material(*, width: object = CLASSIC_MM[0], length: object = CLASSIC_MM[1],
             name: str = "3-4 Egger Classic White") -> str:
    """The header. None leaves that attribute out."""
    parts = [f'DisplayName="{name}"', 'WidthTrim="11.1125"']
    parts += [] if width is None else [f'Width="{width}"']
    parts += [] if length is None else [f'Length="{length}"']
    return f'<OptimizeMaterial {" ".join(parts)}/>'


def sheet(*, width: object = None, length: object = None, quan: object = 1,
          flipped: object = "False", pattern: int = 1) -> str:
    """One <OptimizeSheet>. None leaves that attribute out, and a block has no size of its own unless
    one is given: Jim's parsers take it from the header."""
    parts = [f'PatternNumber="{pattern}"', 'RunId="-1"']
    parts += [] if width is None else [f'Width="{width}"']
    parts += [] if length is None else [f'Length="{length}"']
    parts += [] if quan is None else [f'Quan="{quan}"']
    parts += [] if flipped is None else [f'Flipped="{flipped}"']
    return f'<OptimizeSheet {" ".join(parts)}>{_RUNS_GO_HERE}</OptimizeSheet>'


def part(number: int = 0, *, holes: int = 2) -> str:
    drills = "".join(f'<OperationHole X="{9.525 + n}" Y="96" Diameter="20" Depth="16" FlipSideOp="False"/>'
                     for n in range(holes))
    return (f'<OptimizePart ID="{number}" AssyNo="R1C{number}" Name="Shelf" Quan="1" Width="300"'
            f' Length="600" EdgeBand="A-1,0,0,0">{drills}</OptimizePart>')


def nest_text(*, sheets: Sequence[str] = (), parts: int = 3, placed: int | None = None,
              runs: Sequence[str] = ("TESS01.TAP",), holes: int = 2, header: str | None = None) -> str:
    """A nest as text. sheets: from sheet(); none means loaded in the Optimizer and never run, so it
    names no program. placed: how many parts have a location, none with no sheets (BUILD 696). Its runs
    go in the first sheet, as cnc_job_sheet finds them inside a sheet."""
    if placed is None:
        placed = parts if sheets else 0
    body = [material() if header is None else header]
    body += [part(n, holes=holes if n == 0 else 0) for n in range(parts)]
    named = "".join(f'<OptimizeGCodeRun PatternNumber="1" Filename="{name}"/>' for name in runs)
    located = "".join(f'<OptimizePartLocation PartID="{n}"/>' for n in range(placed))
    for number, block in enumerate(sheets):
        body.append(block.replace(_RUNS_GO_HERE, named + located if number == 0 else ""))
    return f'<?xml version="1.0"?><OptimizeJob RunId="-1">{"".join(body)}</OptimizeJob>'


def utf16(text: str) -> bytes:
    """As on some nests: UTF-16LE with a BOM (RULES 705, BUILD 565)."""
    return b"\xff\xfe" + text.encode("utf-16-le")


def utf8(text: str) -> bytes:
    """As on others: plain UTF-8 (BUILD 565-566)."""
    return text.encode("utf-8")


NESTED = utf16(nest_text(sheets=(sheet(), sheet(quan=2))))
NOT_NESTED = utf16(nest_text(parts=19, sheets=()))              # BUILD 776-778


# ----- the form real nests have (measured on 20 real files, 2026-10-31)
#
# UTF-8 with no BOM, a first line holding Mozaik's version, 8, then an XML declaration. The root is the
# header, <OptimizeMaterial>, holding the parts and the sheets. Each <OptimizeSheet> has its own Width
# and Length, so a sheet added by hand is a block of its own size, and holds its runs and the parts'
# locations, each with a Flipped of its own. A flip pass is a second run of its sheet, holding a copy of
# the header with the flipped block in it. Every value here is made up.


def mozaik_nest(*, sheets: Sequence[tuple[tuple[float, float], Sequence[str]]] = ((CLASSIC_MM, ("TESS01.TAP",)),),
                parts: int = 3, header: tuple[float, float] = CLASSIC_MM, flip_on: int | None = None,
                holes: int = 2, version: str = "8") -> bytes:
    """A nest as Mozaik writes one. sheets: (size, its runs) per block; none is a nest not run.
    flip_on: the block whose last run is a flip pass, holding the flipped copy."""
    drills = "".join(f'<OperationHole ID="{n}" X="{9.525 + n}" Y="96" Diameter="20" Depth="16"'
                     f' FlipSideOp="False" IsUserOp="False"/>' for n in range(holes))
    body = [f'<OptimizePart PartID="{n}" AssyNo="R1C{n}" Name="Shelf" Quan="1" Width="300" Length="600"'
            f' EdgeBand="A-1,0,0,0"><Shape><Operations>{drills if n == 0 else ""}</Operations></Shape>'
            f'</OptimizePart>' for n in range(parts)]
    for number, ((width, length), runs) in enumerate(sheets, start=1):
        named = []
        for index, name in enumerate(runs):
            flipped = ""
            if flip_on == number - 1 and index == len(runs) - 1:
                flipped = (f'<OptimizeMaterial RunId="-1" Width="{header[0]}" Length="{header[1]}">'
                           f'<OptimizeSheet Width="{width}" Length="{length}" Quan="1" Flipped="True"'
                           f' PatternNumber="{number}"><OptimizePartLocation PartID="0" Flipped="True"/>'
                           f'</OptimizeSheet></OptimizeMaterial>')
            named.append(f'<OptimizeGCodeRun Filename="{name}" FullFilename="G:\\made-up\\{name}"'
                         f' PatternNumber="{number}">{flipped}</OptimizeGCodeRun>')
        body.append(f'<OptimizeSheet Width="{width}" Length="{length}" Rotation="0" Quan="1" Flipped="False"'
                    f' PatternNumber="{number}" PatternNumberSuffix="">{"".join(named)}'
                    f'<OptimizePartLocation PartID="0" Flipped="False" X="0" Y="0"/></OptimizeSheet>')
    xml = (f'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\r\n<OptimizeMaterial RunId="-1"'
           f' DisplayName="3-4 Egger Classic White" MaterialId="118" Width="{header[0]}"'
           f' Length="{header[1]}" WidthTrim="11.1125">{"".join(body)}</OptimizeMaterial>')
    return f"{version}\r\n{xml}".encode("utf-8")


MOZAIK_NESTED = mozaik_nest()
MOZAIK_NOT_NESTED = mozaik_nest(sheets=(), parts=21)          # Case I's form: parts and no sheets
