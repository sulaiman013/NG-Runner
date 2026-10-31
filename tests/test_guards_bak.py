"""
The .bak reader and diff (docs/p2/build-spec.md, sections 2 and 3.5; RULES 81; Q19).

Synthetic rooms here are small designs in Mozaik's measured form: a version line 15 and CRLF, then
the XML, all CRLF. Real rooms are read only by the tests marked fixtures, from the ten job folders
of Jim's toolkit (cases A to J), named by case and room number, counted and never printed.

The real tests rest on sizes and contents, never on file times: the zip did not keep Mozaik's
times (every file there carries the time it was unpacked, spec section 10), so "newer" can't be
measured on them. The synthetic tests set times to show newer is read.
"""
from __future__ import annotations

import ast
import os
from dataclasses import fields
from pathlib import Path

import pytest

from ngrunner.guards import bak
from ngrunner.guards.bak import (
    BakFinding,
    DesignUnreadable,
    PartChange,
    ProductChange,
    RoomDiff,
    check_job,
    check_room,
    diff_designs,
)
from ngrunner.messages.lint import check_line
from conftest import FIXTURES
from test_tracker_inbox import writes_while


# ----- a small design in Mozaik's form

def groove(op_id: int, hide: str = "False", x: str = "0") -> str:
    return (f'<OperationGroove Width="6.35" Len="350.52" ID="{op_id}" X="{x}" Y="15.875"'
            f' Hide="{hide}" FlipSideOp="False"><OpIdTag Tag="1" /></OperationGroove>')


def part(name: str = "Fin End (L)", kind: str = "FEnd", w: str = "356", ops: str = "") -> str:
    ops = ops or groove(1) + groove(2, "True")
    return (f'<CabProdPart Name="{name}" Type="{kind}" Quan="1" W="{w}" L="2356" X="0">'
            f'<PartShapeXml Version="2"><ShapePoint X="0" Y="0" /></PartShapeXml>'
            f'<PartOpsXml Version="2">{ops}</PartOpsXml></CabProdPart>')


def product(uid: str, cab: str = "1", parts: str = "", x: str = "0", parms: str = "8") -> str:
    parts = parts or part()
    return (f'<Product UniqueID="{uid}" ProdName="FM 84 " CabNo="{cab}" X="{x}" Width="19">'
            f'<CabProdParts>{parts}</CabProdParts>'
            f'<CabProdParms><CabProdParm Name="SysHoles" Value="{parms}" /></CabProdParms>'
            f'</Product>')


PRODUCTS = product("50001001") + product("50001002", "2")
WALLS = '<Wall IDTag="1" Len="2438.4" />'
NOTES = '<AnnotationSet Id="1"><AnnotationLocator X="10" /></AnnotationSet>'


def design(products: str = PRODUCTS, notes: str = NOTES, walls: str = WALLS,
           version: bytes = b"15", line_end: str = "\r\n") -> bytes:
    xml = "\n".join([
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
        '<Room UniqueID="50001" Name="Testerson_101426 reach-in" RoomNosDirty="False">',
        '  <RoomParms H_Walls="2438.4" />',
        f"  <Walls>{walls}</Walls>",
        f"  <Products>{products}</Products>",
        f"  <Annotations>{notes}</Annotations>",
        "</Room>", ""])
    return version + b"\r\n" + xml.replace("\n", line_end).encode("utf-8")


def room(folder: Path, name: str, des: bytes, backup: bytes | None, *,
         des_time: float = 1_700_000_000, bak_time: float = 1_700_000_000) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.des").write_bytes(des)
    os.utime(folder / f"{name}.des", (des_time, des_time))
    if backup is not None:
        (folder / f"{name}.bak").write_bytes(backup)
        os.utime(folder / f"{name}.bak", (bak_time, bak_time))
    return folder


# ----- the diff


def test_identical_files_are_the_same_bytes() -> None:
    diff = diff_designs(design(), design())
    assert diff.same_bytes
    assert diff.matches


def test_numbers_are_compared_as_numbers_so_formatting_alone_matches() -> None:
    diff = diff_designs(design(), design(products=product("50001001", x="0.00")
                                         + product("50001002", "2")))
    assert not diff.same_bytes
    assert diff.matches
    assert diff == RoomDiff(False, (), (), (), (), (), False)


def test_annotations_alone_are_found_as_annotations() -> None:
    diff = diff_designs(design(), design(notes=NOTES + NOTES))
    assert diff.annotations
    assert diff.only_annotations
    assert (diff.parts, diff.products, diff.sections) == ((), (), ())


def test_a_flipped_groove_is_a_part_that_differs() -> None:
    # Job G's room 1 in miniature: which groove is hidden swapped between the two files.
    flipped = product("50001001", parts=part(ops=groove(1, "True") + groove(2, "False")))
    diff = diff_designs(design(), design(products=flipped + product("50001002", "2")))
    assert diff.parts == (PartChange("50001001", "Fin End (L) (CabProdPart 1)", "differs",
                                     ("OperationGroove 1 Hide", "OperationGroove 2 Hide")),)
    assert diff.products == ()
    assert not diff.matches


def test_a_part_size_is_a_part_field() -> None:
    wider = product("50001002", "2", parts=part(w="406"))
    diff = diff_designs(design(), design(products=product("50001001") + wider))
    assert diff.parts == (PartChange("50001002", "Fin End (L) (CabProdPart 1)", "differs",
                                     ("W",)),)


def test_parts_and_operations_only_in_one_file_are_named_by_side() -> None:
    more = product("50001001", parts=part() + part("Shelf", "Shelf"))
    fewer_ops = product("50001002", "2", parts=part(ops=groove(1)))
    diff = diff_designs(design(products=more + fewer_ops), design())
    assert PartChange("50001001", "Shelf (CabProdPart 1)", "only in the design", ()) in diff.parts
    assert PartChange("50001002", "Fin End (L) (CabProdPart 1)", "differs",
                      ("OperationGroove 2 only in the backup",)) in diff.parts


def test_a_product_only_in_one_file_is_named_by_side() -> None:
    diff = diff_designs(design(products=product("50001001")), design())
    assert diff.products == (ProductChange("50001002", "only in the backup", ()),)
    assert diff.parts == ()


def test_a_products_own_fields_and_settings_are_product_changes() -> None:
    moved = product("50001001", x="25.4", parms="7") + product("50001002", "2")
    diff = diff_designs(design(), design(products=moved))
    assert diff.products == (ProductChange("50001001", "differs", ("X", "CabProdParms")),)


def test_room_sections_other_than_products_are_named() -> None:
    diff = diff_designs(design(), design(walls='<Wall IDTag="1" Len="3048" />'))
    assert diff.sections == ("Walls",)


@pytest.mark.parametrize("data, why", [
    (design()[4:], "version line"),                            # no version line
    (design(version=b"14"), "version"),                        # a version not measured
    (b"15\r\n<Room><Products></Room>", "not a design"),        # broken XML
    (b"15\r\n<Job />", "not a design"),                         # not a room
    (b"", "version line"),
])
def test_a_file_that_is_not_a_measured_design_is_unreadable_in_plain_words(
        data: bytes, why: str) -> None:
    with pytest.raises(DesignUnreadable) as caught:
        diff_designs(design(), data)
    assert why in str(caught.value)
    assert check_line(str(caught.value)) == []


# ----- one room on disk


def test_a_larger_backup_is_reported_with_rules_81s_condition(tmp_path: Path) -> None:
    room(tmp_path, "Room1", design(), design(notes=NOTES + NOTES))
    finding = check_room(tmp_path, "Room1")
    assert finding.backup_larger
    assert finding.backup_newer is False
    assert finding.newer_or_larger
    assert finding.words == "Room 1's backup is larger than its design; only the annotations" \
                            " differ."


def test_a_newer_smaller_backup_is_reported_newer(tmp_path: Path) -> None:
    # The 10-20 case Q19 quotes: a newer, smaller .bak held the real recalculation.
    flipped = product("50001001", parts=part(ops=groove(1, "True") + groove(2, "False")))
    room(tmp_path, "Room2", design(), design(products=flipped + product("50001002", "2"),
                                             notes=""),
         bak_time=1_700_000_600)
    finding = check_room(tmp_path, "Room2")
    assert (finding.backup_larger, finding.backup_newer, finding.newer_or_larger) == (
        False, True, True)
    assert finding.words == ("Room 2's backup is smaller than its design and newer; 1 part in"
                             " 1 product differs; the annotations differ.")


def test_identical_files_read_as_identical(tmp_path: Path) -> None:
    room(tmp_path, "Room0", design(), design())
    finding = check_room(tmp_path, "Room0")
    assert finding.newer_or_larger is False
    assert finding.words == "Room 0's backup is identical to its design."


def test_cabinet_numbers_alone_are_said_plainly(tmp_path: Path) -> None:
    room(tmp_path, "Room0", design(), design(products=product("50001001", "3")
                                             + product("50001002", "4")))
    assert check_room(tmp_path, "Room0").words == (
        "Room 0's backup is the same size as its design; cabinet numbers differ on 2 products.")


def test_matching_contents_are_said_plainly(tmp_path: Path) -> None:
    room(tmp_path, "Room3", design(), design(products=product("50001001", x="0.00")
                                             + product("50001002", "2")))
    assert check_room(tmp_path, "Room3").words == (
        "Room 3's backup is larger than its design; their contents match part for part.")


def test_line_ends_mozaik_does_not_write_are_reported(tmp_path: Path) -> None:
    room(tmp_path, "Room1", design(), design(line_end="\n"))
    finding = check_room(tmp_path, "Room1")
    assert finding.diff is not None and finding.diff.matches
    assert finding.words.endswith("; the backup has line ends Mozaik does not write.")


def test_a_room_with_no_backup_says_so(tmp_path: Path) -> None:
    room(tmp_path, "Room1", design(), None)
    finding = check_room(tmp_path, "Room1")
    assert finding.diff is None
    assert finding.newer_or_larger is None
    assert finding.words == "Room 1 has no backup."


@pytest.mark.parametrize("make", [
    lambda d: None,                                                    # no folder at all
    lambda d: room(d, "Room1", b"", design()),                         # an empty design
    lambda d: room(d, "Room1", design(), b"15\r\n<Room"),              # a broken backup
    lambda d: (d / "Room1.des").mkdir(parents=True),                   # a folder, not a file
])
def test_a_room_that_cannot_be_compared_never_raises(tmp_path: Path, make) -> None:
    make(tmp_path / "job")
    finding = check_room(tmp_path / "job", "Room1")
    assert finding.diff is None
    assert finding.words.startswith("Room 1")
    assert "could not" in finding.words or "has no" in finding.words
    assert check_line(finding.words) == []
    assert str(tmp_path) not in finding.words


def test_a_job_is_every_room_with_a_design_in_number_order(tmp_path: Path) -> None:
    for name in ("Room10", "Room2", "Room0"):
        room(tmp_path, name, design(), design())
    (tmp_path / "Room4.bak").write_bytes(design())        # a backup alone is not a room
    (tmp_path / "Notes.des").write_bytes(design())
    assert [f.room for f in check_job(tmp_path)] == ["Room0", "Room2", "Room10"]


def test_a_job_folder_that_cannot_be_read_gives_one_plain_finding(tmp_path: Path) -> None:
    (finding,) = check_job(tmp_path / "nowhere")
    assert finding.diff is None
    assert check_line(finding.words) == []


# ----- it reads and reports only: never a verdict, never a write

_VERDICT_WORDS = ("stale", "unsaved", "must", "do not", "don't", "stop", "block", "never",
                  "cut", "not checked", "lost", "wrong", "fix")


def _rooms(tmp_path: Path) -> Path:
    flipped = product("50001001", parts=part(ops=groove(1, "True") + groove(2, "False")))
    room(tmp_path, "Room0", design(), design(products=product("50001001", "3")
                                             + product("50001002", "4")))
    room(tmp_path, "Room1", design(), design(notes=NOTES + NOTES), bak_time=1_800_000_000)
    room(tmp_path, "Room2", design(), design(products=flipped, walls=""))
    room(tmp_path, "Room3", design(), b"15\r\n<Room")
    room(tmp_path, "Room4", design(), None)
    room(tmp_path, "Room5", design(), design())
    return tmp_path


def test_every_finding_reads_plainly_and_judges_nothing(tmp_path: Path) -> None:
    findings = check_job(_rooms(tmp_path))
    assert len(findings) == 6
    for finding in findings:
        assert check_line(finding.words) == [], finding.words
        assert not any(word in finding.words.lower() for word in _VERDICT_WORDS), finding.words


def test_a_finding_has_no_verdict_to_act_on() -> None:
    # RULES 81 and Q19 wait for P3: a finding is facts and words, and nothing a caller could
    # gate a check, a write or a card on.
    names = {f.name for kind in (BakFinding, RoomDiff, PartChange, ProductChange)
             for f in fields(kind)}
    for word in ("block", "verdict", "gate", "stale", "stop", "card", "state", "ok", "safe",
                 "pass", "fail", "level", "severity", "urgent"):
        assert not any(word in name.lower() for name in names), word


def test_reading_a_job_writes_nothing(tmp_path: Path) -> None:
    _rooms(tmp_path)
    made = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert writes_while(lambda: check_job(tmp_path)) == []
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == made


def test_the_module_uses_no_part_of_the_app_that_writes_or_sends() -> None:
    tree = ast.parse(Path(bak.__file__).read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {a.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                 for a in node.names}
    assert not {name for name in imported if name and name.startswith("ngrunner")}
    calls = {getattr(node.func, "attr", None) or getattr(node.func, "id", "")
             for node in ast.walk(tree) if isinstance(node, ast.Call)}
    assert not calls & {"open", "write_bytes", "write_text", "unlink", "rename", "replace",
                        "mkdir", "rmdir", "remove", "utime", "commit_write"}


# ----- real files

TOOLKIT = Path(os.environ.get("NGR_TOOLKIT", FIXTURES / "toolkit" / "20261031"))


def _case(letter: str) -> Path:
    holder = TOOLKIT / "jobs" / letter
    if not holder.is_dir():
        pytest.skip("no local copy of Jim's job folders: set NGR_TOOLKIT (AGENTS.md: Fixtures)")
    (folder,) = [p for p in holder.iterdir() if p.is_dir()]
    return folder


def _real() -> dict[str, BakFinding]:
    """Every real room as "<case><room number>", e.g. "G1"."""
    return {f"{case}{f.room.removeprefix('Room')}": f
            for case in "ABCDEFGHIJ" for f in check_job(_case(case))}


@pytest.fixture(scope="module")
def real() -> dict[str, BakFinding]:
    return _real()


@pytest.mark.fixtures
def test_the_real_rooms_all_read_and_compare(real: dict[str, BakFinding]) -> None:
    assert len(real) == 30
    assert all(f.diff is not None for f in real.values())
    assert all(check_line(f.words) == [] for f in real.values())


@pytest.mark.fixtures
def test_every_real_design_and_backup_starts_15_crlf_and_is_crlf_throughout() -> None:
    for case in "ABCDEFGHIJ":
        for path in _case(case).iterdir():
            if path.suffix.lower() in (".des", ".bak"):
                data = path.read_bytes()
                assert data.startswith(b"15\r\n<?xml")
                assert data.count(b"\n") == data.count(b"\r\n")


@pytest.mark.fixtures
def test_real_backups_by_size(real: dict[str, BakFinding]) -> None:
    larger = sorted(k for k, f in real.items() if f.backup_larger)
    smaller = sorted(k for k, f in real.items()
                     if f.backup_bytes is not None and f.design_bytes is not None
                     and f.backup_bytes < f.design_bytes)
    assert larger == ["B1", "C1", "C2", "C4", "E1", "E2", "E3"]
    assert smaller == ["B0", "B2", "D1", "F1", "G1"]


@pytest.mark.fixtures
def test_what_the_real_backups_hold(real: dict[str, BakFinding]) -> None:
    identical = sorted(k for k, f in real.items() if f.diff.same_bytes)
    assert identical == ["A1", "C3", "D0", "H1", "H2", "I1", "J1", "J2", "J3", "J4"]
    annotations = sorted(k for k, f in real.items() if f.diff.only_annotations)
    assert annotations == MEASURED_ANNOTATIONS_ONLY
    numbering = sorted(k for k, f in real.items() if f.diff.products and all(
        c.fields == ("CabNo",) for c in f.diff.products))
    assert numbering == MEASURED_NUMBERING
    parts = {k: (len(f.diff.parts), len({c.product for c in f.diff.parts}))
             for k, f in real.items() if f.diff.parts}
    assert parts == {"G1": (5, 5)}
    # Job G's room 1: which of two grooves is hidden swapped on 5 wall-mounted panels
    assert {field for c in real["G1"].diff.parts for field in c.fields} == {
        "OperationGroove 2 Hide", "OperationGroove 3 Hide"}


@pytest.mark.fixtures
def test_on_the_real_rooms_rules_81_by_size_fires_only_where_no_part_differs(
        real: dict[str, BakFinding]) -> None:
    # The evidence for Q19: the 7 larger backups differ in annotations and numbering only, and the
    # one room whose parts differ has a smaller backup. Times are not measured (module docstring).
    fired = [f for f in real.values() if f.backup_larger]
    assert len(fired) == 7
    assert not any(f.diff.parts or f.diff.products for f in fired)
    assert not real["G1"].backup_larger


@pytest.mark.fixtures
def test_the_older_real_backups_read_and_compare_too() -> None:
    compared = 0
    for case in "ABCDEFGHIJ":
        folder = _case(case)
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() in (".bak2", ".bak3", ".bak4"):
                design_file = folder / (path.stem + ".des")
                diff_designs(design_file.read_bytes(), path.read_bytes())
                compared += 1
    assert compared == 18


# Measured 2026-10-31. B1 and B2 also differ in the room's own renumbering flag, so they are
# not "only annotations". Each Room0 that differs does so in its cabinet numbers alone (B0 in
# that flag too), and D0 is identical.
MEASURED_ANNOTATIONS_ONLY = ["C1", "C2", "C4", "D1", "E1", "E2", "E3", "F1"]
MEASURED_NUMBERING = ["A0", "B0", "C0", "E0", "F0", "G0", "H0", "I0", "J0"]
