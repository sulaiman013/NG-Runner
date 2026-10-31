"""
Made-up rooms, nests and cutlists for the nest-to-design match (guards/design.py). Every name and number is
synthetic. The shapes are the ones measured on the 10 real jobs of 2026-10-31, read-only:

    Room#.des      a line "15", CRLF, then the XML: <Room Quan ...> with <Products>, <Product CabNo Count>,
                   <CabProdPart Name ReportName Quan W L Type UsageType>, its <PartOpsXml> holding
                   <Operation...> with equations (X_Eq ...) beside the values, and <Annotations>
    <Material>.opt a line "8", then the XML: the root <OptimizeMaterial MaterialId TextureId>, its top-level
                   <OptimizePart AssyNo Name Width Length Quan TextureID> with <Shape><Operations> and no
                   equations, <RoomInfo Number>, and <OptimizeSheet> blocks, a flip pass holding a copy of
                   the header with parts of its own
    des_cutlist    "qty | colour | name | W | L", one per line, W and L to 0.1 mm, then "TOTAL n"
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence

from ngrunner.guards.facts import ToolRun

HOLE = {"Diameter": "20", "ID": "1", "X": "9.525", "Y": "37", "Depth": "16", "Hide": "False",
        "IsUserOp": "False", "Noneditable": "False", "Anchor": "", "FlipSideOp": "False"}
GROOVE = {"Width": "6.35", "Len": "20.6375", "Ang": "49.99975", "ID": "2", "X": "1012.1", "Y": "0",
          "Depth": "19.304", "Hide": "True", "IsUserOp": "True", "Noneditable": "False", "Anchor": "",
          "FlipSideOp": "True"}


def op(kind: str = "OperationHole", **values: str) -> tuple[str, dict[str, str]]:
    """One operation, a hole unless told otherwise, with values changed as given."""
    base = HOLE if kind == "OperationHole" else GROOVE
    return kind, {**base, **values}


def _attrs(values: dict[str, str]) -> str:
    return " ".join(f'{name}="{value}"' for name, value in values.items())


def _design_op(kind: str, values: dict[str, str]) -> str:
    """As the design writes one: an equation beside each value it can compute."""
    equations = {f"{name}_Eq": "PartL-1" for name in ("X", "Y", "Depth")}
    return f"<{kind} {_attrs({**values, **equations})}><OpIdTag TypeCode=\"0\" /></{kind}>"


def _nest_op(kind: str, values: dict[str, str]) -> str:
    """As a nest writes one: the values, and no equation (measured on all 20 real nests)."""
    return f"<{kind} {_attrs(values)} />"


def part(name: str = "Top", w: object = 356, l: object = 870, *, quan: int = 1,
         ops: Sequence[tuple[str, dict[str, str]]] = (), report: str | None = None,
         kind: str = "Top", usage: int = 0, mat: str | None = None, tex: str | None = None) -> str:
    extra = {}
    if mat is not None:
        extra |= {"MatORId": mat, "MissingMatORName": "3-4 Made-up"}
    if tex is not None:
        extra |= {"TextureORId": tex, "MissingTextureORName": "Made-up White"}
    values = {"Name": name, "ReportName": name if report is None else report, "UsageType": str(usage),
              "Quan": str(quan), "W": str(w), "L": str(l), "Color": "None", "Type": kind, **extra}
    body = "".join(_design_op(k, v) for k, v in ops)
    return (f"<CabProdPart {_attrs(values)}><PartShapeXml Version=\"2\" />"
            f"<PartOpsXml Version=\"2\">{body}</PartOpsXml></CabProdPart>")


def product(cab: int, *parts: str, count: int = 1, name: str = "Made-up 84", uid: int | None = None) -> str:
    """A product. Its UniqueID stays when Mozaik renumbers it, so give uid to renumber one."""
    uid = 9000 + cab if uid is None else uid
    return (f'<Product UniqueID="{uid}" ProdName="{name}" CabNo="{cab}" Count="{count}">'
            f'<CabProdParts>{"".join(parts)}</CabProdParts><JointFastenerCounts /></Product>')


def room(*products: str, quan: int = 1, dirty: str = "False", tag_count: int = 4,
         annotation: str = "", name: str = "Testerson - Closet", tops: str = "") -> bytes:
    xml = (f'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\r\n'
           f'<Room UniqueID="50001" Name="{name}" RoomNosDirty="{dirty}" IdTagCount="{tag_count}"'
           f' Quan="{quan}">\r\n  <RoomParms H_Walls="2438.4" />\r\n'
           f'  <Products>{"".join(products)}</Products>\r\n  <Tops>{tops}</Tops>\r\n'
           f'  <Annotations>{annotation}</Annotations>\r\n</Room>')
    return ("15\r\n" + xml).encode("utf-8")


def nest_part(tag: str, name: str = "Top", w: object = 356, l: object = 870, *, quan: int = 1,
              ops: Sequence[tuple[str, dict[str, str]]] = (), texture: str = "3505",
              space: bool = True) -> str:
    """A part as a nest writes it. A nest's names often start with a space, as real ones do."""
    shown = f" {name}" if space else name
    body = "".join(_nest_op(k, v) for k, v in ops)
    return (f'<OptimizePart PartID="1" Quan="{quan}" Name="{shown}" Width="{w}" Length="{l}"'
            f' AssyNo="{tag}" UserAdded="False" TextureID="{texture}">'
            f'<Shape Version="2"><ShapePoint ID="0" X="0" Y="0" /><Operations Version="2">{body}'
            f'</Operations></Shape></OptimizePart>')


def nest(*parts: str, rooms: Iterable[int] = (0, 1), sheets: int = 1, material_id: str = "118",
         texture_id: str = "3505", flip_copy: str = "") -> bytes:
    """A nest. flip_copy: parts in a flip pass's copy of the header, which are no parts of their own."""
    infos = "".join(f'<RoomInfo Number="{n}" Name="Room {n}" />' for n in rooms)
    blocks = "".join(
        f'<OptimizeSheet Width="1244.6" Length="2463.8" Quan="1" Flipped="False" PatternNumber="{n}">'
        f'<OptimizeGCodeRun Filename="TESS0{n}.TAP" PatternNumber="{n}">'
        + (f'<OptimizeMaterial RunId="-1" Width="1244.6" Length="2463.8">{flip_copy}'
           f'<OptimizeSheet Width="1244.6" Length="2463.8" Quan="1" Flipped="True" /></OptimizeMaterial>'
           if flip_copy and n == 1 else "")
        + '</OptimizeGCodeRun></OptimizeSheet>' for n in range(1, sheets + 1))
    xml = (f'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\r\n'
           f'<OptimizeMaterial RunId="-1" DisplayName="3-4 Made-up White" MaterialId="{material_id}"'
           f' TextureId="{texture_id}" Width="1244.6" Length="2463.8" CustomerName="A Made-up Client">'
           f'{"".join(parts)}{infos}{blocks}</OptimizeMaterial>')
    return ("8\r\n" + xml).encode("utf-8")


def cutlist_text(rows: Iterable[tuple[int, str, str, float, float]], *, total: int | None = None) -> str:
    """What des_cutlist prints: print(qty, colour, name, W, L, sep=' | '), then TOTAL."""
    rows = list(rows)
    lines = [" | ".join(str(value) for value in (qty, colour, name, float(w), float(l)))
             for qty, colour, name, w, l in rows]
    lines.append(f"TOTAL {sum(row[0] for row in rows) if total is None else total}")
    return "\n".join(lines) + "\n"


def printed(text: str, *, exit_code: int | None = 0, stderr: str = "", timed_out: bool = False) -> ToolRun:
    return ToolRun(exit_code, text, stderr, timed_out)
