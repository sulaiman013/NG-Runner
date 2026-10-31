"""
The CNC job sheet (RULES section 7: cnc_job_sheet.py writes NG Jobs\\<Job>\\<Job> CNC Job Sheet.docx,
sheet counts from the nests; never over one Jim made): Jim's script run on a staged copy, what it
really wrote read back, and its counts set against NG Runner's own reading of the same nests. A
mismatch is an item, never fixed, and no sheet is written then.

Synthetic here: made-up nests, and a fake standing in for his script that writes a .docx of fixed
lines. tests/test_lock_job_sheet_real.py runs his script on the real jobs. Job names are made up.
"""
from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from ngrunner.clock import FakeClock
from ngrunner.guards.facts import ToolRun
from ngrunner.lock.job_sheet import JOB_SHEET_SCRIPT, make_job_sheet, nest_counts, write_job_sheet
from ngrunner.store import open_store
from ngrunner.toolkit import Snapshot
from ngrunner.writes import Roots

JOB = "Testerson_101426"
T0 = datetime(2026, 10, 31, 13, 0, tzinfo=UTC)


def opt(thickness: str, *quans: int, flipped: int = 0) -> bytes:
    sheets = "".join(f'<OptimizeSheet Quan="{q}" Flipped="False" Width="1244.6" Length="2463.8">'
                     f'<OptimizeGCodeRun Filename="P{i}.tap" /></OptimizeSheet>' for i, q in enumerate(quans))
    sheets += '<OptimizeSheet Quan="1" Flipped="True" Width="1244.6" Length="2463.8" />' * flipped
    return ('8\r\n<?xml version="1.0" encoding="utf-8"?>\r\n'
            f'<OptimizeMaterial Thickness="{thickness}" Width="1244.6" Length="2463.8">'
            '<OptimizePart PartID="1" Quan="1" Name="Shelf" AssyNo="R1C1" Width="300" Length="600" />'
            f'{sheets}</OptimizeMaterial>').encode("utf-8")


def docx(lines: list[str]) -> bytes:
    """A .docx holding one paragraph per line, as Jim's template fills them."""
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{escape(line)}</w:t></w:r></w:p>' for line in lines)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("word/document.xml", xml)
    return out.getvalue()


def sheet_lines(q1="7", c1="Egger Classic White", q2="", c2="", q4="1", c4="Egger Classic White", qi=""):
    return ["Job Name : made up", "Job Abbr : TES", "Install Date : Friday", "10/30",
            f"¾”: {q1}", f"Color: {c1}", f"¾”: {q2}", f"Color: {c2}", f"¼”: {q4}", f"Color {c4}",
            f"1”: {qi}", "Special Notes: "]


class FakeScript:
    """Jim's cnc_job_sheet.py, replayed: it writes the .docx given, and says what Python prints."""

    def __init__(self, lines: list[str] | None, *, exit_code: int = 0, stderr: str = "") -> None:
        self.lines, self.exit_code, self.stderr, self.calls = lines, exit_code, stderr, []

    def __call__(self, snapshot, script, args, *, timeout_s=120, python=None) -> ToolRun:
        self.calls.append((script, list(args)))
        if self.lines is not None:
            Path(args[4]).write_bytes(docx(self.lines))
        return ToolRun(self.exit_code, "({'NAME': '...'}, False)\n", self.stderr, False)


@pytest.fixture
def job(tmp_path: Path) -> Path:
    folder = tmp_path / "Mozaik" / "Jobs" / JOB
    folder.mkdir(parents=True)
    (folder / "JobDat.dat").write_bytes(b"<JobDat><Abbr>TES</Abbr></JobDat>")
    (folder / "3-4 Egger Classic White.opt").write_bytes(opt("19.304", 5, 2, flipped=1))
    (folder / "1-4 Egger Classic White.opt").write_bytes(opt("6.35", 1))
    (folder / "Room1.des").write_bytes(b"15\r\n<?xml version=\"1.0\"?><Room />")
    return folder


@pytest.fixture
def conversion(tmp_path: Path) -> Path:
    path = tmp_path / "ngjobs" / f"{JOB}_IC_Conversion.json"
    path.parent.mkdir()
    path.write_text('{"pulled_classified": []}', encoding="utf-8")
    return path


def make(job: Path, conversion: Path, tmp_path: Path, script: FakeScript):
    return make_job_sheet(Snapshot(tmp_path / "toolkit", "0" * 64, T0, 1), job, conversion,
                          work=tmp_path / "work", day="Friday", date="10/30", run=script)


# ----- NG Runner's own reading of the nests


def test_the_nests_are_counted_by_their_own_thickness_and_sheets_not_flips(job: Path) -> None:
    counts = nest_counts({p.stem: p.read_bytes() for p in job.glob("*.opt")})
    assert sorted((c.thickness, c.stem, c.sheets) for c in counts) == [
        ("1/4", "1-4 Egger Classic White", 1), ("3/4", "3-4 Egger Classic White", 7)]


# ----- Jim's sheet, read and compared


def test_a_sheet_whose_counts_match_the_nests_is_ready_to_file(job, conversion, tmp_path) -> None:
    script = FakeScript(sheet_lines())

    result = make(job, conversion, tmp_path, script)

    assert result.code == "matches" and result.items == ()
    assert result.docx is not None and zipfile.is_zipfile(io.BytesIO(result.docx))
    (name, args), = script.calls
    assert name == JOB_SHEET_SCRIPT and args[2:4] == ["Friday", "10/30"]
    assert Path(args[0]).name == JOB and Path(args[0]) != job          # a staged copy, never in place
    assert not any((tmp_path / "work").rglob("*.opt"))                 # and gone after


def test_a_count_that_differs_is_an_item_and_never_fixed(job, conversion, tmp_path) -> None:
    result = make(job, conversion, tmp_path, FakeScript(sheet_lines(q1="8")))

    assert result.code == "mismatch"
    item, = result.items
    assert (item.thickness, item.sheet_count, item.nest_count) == ("3/4", 8, 7)
    assert "8" in item.words and "7" in item.words


def test_a_nest_missing_from_the_sheet_is_an_item(job, conversion, tmp_path) -> None:
    result = make(job, conversion, tmp_path, FakeScript(sheet_lines(q4="", c4="")))
    assert result.code == "mismatch" and [i.thickness for i in result.items] == ["1/4"]


def test_a_third_three_quarter_colour_the_template_has_no_room_for_is_an_item(job, conversion, tmp_path) -> None:
    (job / "3-4 Tafisa Ore.opt").write_bytes(opt("19.304", 2))
    (job / "3-4 Am Lam Wh.opt").write_bytes(opt("19.304", 3))

    result = make(job, conversion, tmp_path, FakeScript(sheet_lines(q2="2", c2="Tafisa Ore")))

    assert result.code == "mismatch" and [i.colour for i in result.items] == ["Am Lam Wh"]


def test_a_one_inch_line_is_read_from_its_count_and_colour(job, conversion, tmp_path) -> None:
    (job / "1 in Am Lam Wh.opt").write_bytes(opt("25.6", 5))
    assert make(job, conversion, tmp_path, FakeScript(sheet_lines(qi="5 Am Lam Wh"))).code == "matches"
    assert make(job, conversion, tmp_path, FakeScript(sheet_lines(qi="4 Am Lam Wh"))).code == "mismatch"


@pytest.mark.parametrize("script", [FakeScript(None), FakeScript(sheet_lines(), exit_code=1),
                                    FakeScript(sheet_lines(), stderr="Traceback ..."),
                                    FakeScript(["not a job sheet"])])
def test_a_run_that_didnt_give_a_readable_sheet_could_not_verify(job, conversion, tmp_path, script) -> None:
    result = make(job, conversion, tmp_path, script)
    assert result.code == "could_not_verify" and result.docx is None


def test_a_nest_that_cant_be_read_could_not_verify(job, conversion, tmp_path) -> None:
    (job / "3-4 Egger Classic White.opt").write_bytes(b"not a nest")
    assert make(job, conversion, tmp_path, FakeScript(sheet_lines())).code == "could_not_verify"


# ----- filing it, when there is none


def test_a_matching_sheet_is_filed_once_and_a_mismatch_never(job, conversion, tmp_path) -> None:
    roots = Roots(tmp_path / "NG Jobs shadow", tmp_path / "Mozaik copy", tmp_path / "control",
                  tmp_path / "data")
    for folder in (roots.ng_jobs / JOB, roots.mozaik_shared, roots.control, roots.app_data):
        folder.mkdir(parents=True)
    args = dict(store=open_store(roots.app_data / "n.db"), roots=roots, interlock=lambda p: None,
                clock=FakeClock(T0))
    good = make(job, conversion, tmp_path, FakeScript(sheet_lines()))
    bad = make(job, conversion, tmp_path, FakeScript(sheet_lines(q1="8")))
    target = roots.ng_jobs / JOB / f"{JOB} CNC Job Sheet.docx"

    refused = write_job_sheet(JOB, bad, **args)
    assert not refused.ok and not target.exists()
    filed = write_job_sheet(JOB, good, **args)
    assert filed.ok, filed.reason
    assert target.read_bytes() == good.docx
    again = write_job_sheet(JOB, good, **args)
    assert not again.ok and target.read_bytes() == good.docx
