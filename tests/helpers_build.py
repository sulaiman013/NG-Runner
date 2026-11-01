"""
Made-up stand-ins for the files P5a's room writer reads at run time: the template job's Room1, Jim's
product templates and a reference room's joint. They are shaped like his (the same elements and the
attributes the writer and its gates read) but every value, name and library here is invented, so no byte of
his files is in the repo (AGENTS.md rule 5). The made-up job is Testerson's.
"""
from __future__ import annotations

from pathlib import Path

from ngrunner.build import products as pr
from ngrunner.build import schedule as sc
from ngrunner.build import writer as wr
from ngrunner.build.room import ICLine, Plan, ReadVertical, ReadWall, RoomModel, TableRow

LIB = "Testerson Library"
JOINT = ('<WallJoint Wall1="7" Wall2="8" IsInterior="False" Wall1Corner="1" Wall2Corner="0" Wall2Along="0" '
         'Wall2Front="False" MiterBack="False" />')
MATERIALS = ('<BaseMatTempSel CategoryId="3">\r\n      <MaterialTemplateSelection RootTemplateId="94" '
             'MissingTemplateName="Testerson cabinets" />\r\n    </BaseMatTempSel>\r\n    '
             '<BaseMatTempSel CategoryId="1">\r\n      <MaterialTemplateSelection RootTemplateId="12" '
             'MissingTemplateName="Testerson doors" />\r\n    </BaseMatTempSel>')
FRONT = "Testerson Drawer"
SETTINGS = wr.RoomSettings(MATERIALS, FRONT)


def point(i: int, x: float, y: float, side: str = "", band: str = "0", lock: str = "False") -> str:
    return (f'<ShapePoint ID="{i}" X="{x:g}" Y="{y:g}" PtType="0" EBand="{band}" EBandLock="{lock}" '
            f'SideName="{side}" />')


def groove(x: float, hide: str, depth_eq: str, flip: str, ang: str = "45", eq: bool = True) -> str:
    x_eq = 'X_Eq="PartL-191.9" ' if eq else 'X_Eq="" '
    return (f'<OperationGroove Width="6" Ang="{ang}" ID="2" X="{x:g}" Y="0" Depth="19" Hide="{hide}" {x_eq}'
            f'Depth_Eq="{depth_eq}" IsUserOp="True" FlipSideOp="{flip}">\n            <OpIdTag TypeCode="0" />\n'
            f'          </OperationGroove>')


def bore(y: float, flip: str) -> str:
    return (f'<OperationLineBore Quan="9" ID="1" X="10" Y="{y:g}" Depth="15" Hide="False" IsUserOp="False" '
            f'FlipSideOp="{flip}">\n            <OpIdTag TypeCode="2">\n              <OpIdTagReference Key="Side" '
            f'Value="4" />\n            </OpIdTag>\n          </OperationLineBore>')


def product(name: str, *, height: float = 1204.001, depth: float = 355.9988, width: float = 19.05,
            outline: list[str] | None = None, ops: list[str] | None = None, lib: str = "Old Library",
            prod_name: str | None = None) -> str:
    """A product shaped like Jim's (LF, as his templates are), every value made up."""
    h, d = height, depth
    outline = outline or [point(0, 0, 0), point(1, h, 0, "Top", "1", "True"), point(2, h, d, "Front", "1"),
                          point(3, 0, d, "Bottom", "0")]
    body = "\n          ".join(ops or [])
    return (f'<Product UniqueID="777" OrderID="0" ProdName="{prod_name or name}" IDTag="47" SourceLib="{lib}" '
            f'Width="{width:g}" Height="{h:g}" Depth="{d:g}" MaxH="3000" X="12.5" Elev="0" Rot="0" Outset="0" '
            f'Wall="0" CabNo="1">\n'
            f'  <CabProdParts>\n'
            f'    <CabProdPart Name=" Panel" Quan="1" W="{d:g}" L="{h:g}" Type="FEnd" X="0" Y="{d:g}" Z="0">\n'
            f'      <PartShapeXml Version="2">\n        ' + "\n        ".join(outline) + '\n      </PartShapeXml>\n'
            f'      <PartOpsXml Version="2">\n          {body}\n      </PartOpsXml>\n'
            f'    </CabProdPart>\n'
            f'  </CabProdParts>\n'
            f'  <TopShapeXml Version="2">\n    {point(0, 0, 0)}\n    {point(1, width, 0)}\n    {point(2, width, d)}\n'
            f'    {point(3, 0, d)}\n  </TopShapeXml>\n'
            f'</Product>')


def wm_square(side: str, radius: bool = False) -> str:
    """WM Panel 48 <side>[ Radius]: two hang notches, the active one full on Thru, skinned on Left and Right."""
    active = "FEnd.th" if side == "Thru" else "FEnd.th-HangNotchSkin"
    name = f"WM Panel 48 {side}" + (" Radius" if radius else "")
    ops = [groove(1012.101, "True", "FEnd.th", "True", "50"), groove(1012.101, "False", active, "False")]
    if side == "Thru":
        ops += [bore(319, "True"), bore(96, "True")]
    return product(name, ops=ops)


def rea(name: str, height: float, flip: str = "False") -> str:
    """An REA: its slanted outline, one notch, its own bores and a cam: every op a user op."""
    d = 356.0
    outline = [point(0, 0, 0), point(1, height, 0, "Top"), point(2, height, d - 54.38),
               point(3, height - 228.6, d, "Front", "1"), point(4, 0, d, "", "0")]
    hide = "False" if name.startswith("WM") else "True"
    ops = [groove(height - 191.9, hide, "FEnd.th-5", flip), bore(319, flip), bore(96, flip),
           '<OperationHole Diameter="8" X="37" Y="300" IsUserOp="True" FlipSideOp="False" />']
    return product(name, height=height, depth=d, width=19, outline=outline, ops=ops)


def library_files(folder: Path) -> Path:
    """Every legal product but FM 84 as a made-up template file in folder."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in pr.LEGAL:
        if name == pr.FM_NAME:
            continue
        if name.startswith("WM Panel 48"):
            side = name.split()[3]
            text = wm_square(side, name.endswith("Radius"))
        elif name.startswith("WM 48 REA"):
            text = rea(name, 1204, flip="True" if " Right" in name or name.endswith("Thru") else "False")
        else:
            text = rea(name, 2132)
        (folder / pr.template_file(name)).write_text(text, encoding="utf-8", newline="\n")
    return folder


def fm84() -> str:
    return product("FM 84", prod_name="FM 84 ", height=2132, depth=356, width=19, lib=LIB,
                   outline=[point(1, 2132.012, 0, "Top"), point(2, 2132.012, 356, "Front", "1"), point(0, 0, 356),
                            point(0, 0, 0)],
                   ops=[bore(319, "False"), bore(96, "False")])


def template_room(*, walls: int = 1, version: str = "15", fm: bool = True, lib: str = LIB) -> bytes:
    """The template job's Room1, made up: one wall, an FM 84 and a drawer box of its own library."""
    wall = ('    <Wall IDTag="1" Len="2715.742" Height="2438.4" PosX="-20.5" PosY="10.25" Ang="0" Invisible="False" '
            'WallNumber="1" Thickness="152.4" ShapeType="0" CathedralHeight="0">\r\n      <LabelDimensionOverrideMap />'
            '\r\n    </Wall>')
    prods = [fm84()] if fm else []
    prods.append(f'<Product UniqueID="5" ProdName="Testerson Box" IDTag="5" SourceLib="{lib}" Width="400" '
                 'Height="1044" Depth="356" X="19" Elev="0" Wall="1_1" CabNo="2">\n  <CabProdParts />\n</Product>')
    text = (f'{version}\r\n<?xml version="1.0" encoding="utf-8" standalone="yes"?>\r\n'
            '<Room UniqueID="50905" RoomType="0" Name="Room 1" RoomNosDirty="True" IdTagCount="19" Quan="1">\r\n'
            '  <RoomParms SourceTemplateID="1" H_Walls="2438.4" H_Soffit="304.8" />\r\n'
            f'  <RoomSet Version="8" NonColumnTopDrawer="Slab" NonColumnMidDrawer="Slab" NonColumnBotDrawer="Slab" '
            f'AutofillLib="{LIB}" Note="a > b">\r\n'
            '    <BaseMatTempSel CategoryId="3">\r\n      <MaterialTemplateSelection RootTemplateId="88" '
            'MissingTemplateName="blank cabinets" />\r\n    </BaseMatTempSel>\r\n'
            '    <BaseMatTempSel CategoryId="1">\r\n      <MaterialTemplateSelection RootTemplateId="12" '
            'MissingTemplateName="blank doors" />\r\n    </BaseMatTempSel>\r\n'
            '  </RoomSet>\r\n'
            '  <Walls>\r\n' + "\r\n".join([wall] * walls) + '\r\n  </Walls>\r\n'
            '  <WallJoints />\r\n  <Appls />\r\n'
            '  <Products>\r\n' + "\r\n".join("    " + p.replace("\n", "\r\n") for p in prods) + '\r\n  </Products>\r\n'
            '  <Tops />\r\n  <RoomNotes />\r\n</Room>')
    return text.encode("utf-8")


def reference_room() -> bytes:
    return ("15\r\n<?xml version=\"1.0\"?>\r\n<Room>\r\n  <WallJoints>\r\n    " + JOINT +
            "\r\n  </WallJoints>\r\n</Room>").encode("utf-8")


def template() -> wr.RoomTemplate:
    return wr.load_template(template_room(), [reference_room()])


def library(tmp: Path) -> pr.Library:
    return pr.load_library(library_files(tmp / "templates"), template().fm84)


# ----- a made-up room: Testerson's primary, three walls, two floor panels and a hung one on wall 2


def line(qty: int | None, desc: str) -> ICLine:
    return ICLine(qty, desc)


def v(x: float, bottom: float, top: float, height: str, radius: str = "no") -> ReadVertical:
    return ReadVertical(x, bottom, top, height, radius)


TABLE = (TableRow("1", 24.0, 98.0), TableRow("2", 60.75, 98.0), TableRow("3", 24.0, 98.0))
PLAN = Plan(("1", "2", "3"), (90.0, 0.0, 270.0))
PLAIN = ReadWall("2", (line(2, "VT 14x83.9x3/4 FD WH"), line(1, "VT 14x47.4x3/4 FD FN WH"),
                       line(2, "SH 14x29.6x3/4 FIX WH"), line(2, "SH 14x28.9x3/4 FIX WH")),
                 (v(0.0, 0.0, 83.94, "83.9"), v(30.375, 36.5, 83.94, "47.4"), v(60.0, 0.0, 83.94, "83.9")))
SIDES = (ReadWall("1", ()), ReadWall("3", ()))
WALLS = (SIDES[0], PLAIN, SIDES[1])


def model(*walls: ReadWall, plan: Plan | None = PLAN, table: tuple[TableRow, ...] = TABLE,
          name: str = "Testerson - Primary") -> RoomModel:
    return sc.build_room(name, table, walls or WALLS, plan)
