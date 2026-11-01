"""
The delivered script's own gates (tools/delivery/front.py), called directly on a made-up toolkit
(tests/helpers_delivery.py) and made-up summaries: its rules held to the toolkit's takeoff.py, the PULLED
list read a second time through its parse_pulled.py, its takeoff.py's own reading agreeing, and never
writing inside the toolkit. What needs openpyxl or pdfplumber is in test_delivery_script.py.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from helpers_delivery import load, rows_of, takeoff_source, toolkit
from helpers_pdf import summary_pdf
from test_workbook_conversion import PARTS
from tools.delivery import front
from tools.delivery.front import Refused
from tools.workbook.conversion import conversion

JOB = "Testerson_101426"


@pytest.fixture
def summary(tmp_path: Path) -> Path:
    path = tmp_path / "NG Jobs" / JOB / f"{JOB}_summary.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(summary_pdf(parts=PARTS))
    return path


def his(tmp_path: Path, data: bytes, **kw) -> Path:
    return toolkit(tmp_path / "kit", data, **kw)


# ----- the rules, held to takeoff.py on every run


def test_the_rules_hold_against_a_takeoff_py_with_ng_runners_mirrored_rules(tmp_path: Path) -> None:
    scripts = his(tmp_path, b"")
    assert front.tie(load(scripts / "takeoff.py", "takeoff")) == []


def test_a_takeoff_py_whose_rules_moved_is_named(tmp_path: Path) -> None:
    scripts = his(tmp_path, b"", takeoff=takeoff_source(waste=1.15))
    assert front.tie(load(scripts / "takeoff.py", "takeoff")) == ["its WASTE is 1.15, and ng_convert's is 1.1"]


def test_a_takeoff_py_missing_what_ng_convert_checks_is_named(tmp_path: Path) -> None:
    scripts = his(tmp_path, b"", takeoff="WASTE = 1.1\n")
    assert front.tie(load(scripts / "takeoff.py", "takeoff")) == ["it has no DISREGARD, TYPE, eb_lf, normalise or takeoff"]


# ----- the second reading: every board line read the same by parse_pulled


def test_the_second_reading_agrees_line_for_line(summary: Path, tmp_path: Path) -> None:
    data = summary.read_bytes()
    scripts = his(tmp_path, data)
    assert front.second_reading(summary, data, load(scripts / "parse_pulled.py", "parse_pulled")) == 0


def test_a_board_line_the_second_reading_misses_is_counted_and_a_pin_isnt(summary: Path, tmp_path: Path) -> None:
    data = summary.read_bytes()
    board = his(tmp_path / "a", data, rows=rows_of(data, drop=0))
    pin = his(tmp_path / "b", data, rows=rows_of(data, drop=len(PARTS) - 1))
    assert front.second_reading(summary, data, load(board / "parse_pulled.py", "parse_pulled")) == 1
    assert front.second_reading(summary, data, load(pin / "parse_pulled.py", "parse_pulled")) == 0


# ----- takeoff.py's own reading of the parts


def test_takeoff_pys_reading_of_the_parts_agrees(summary: Path, tmp_path: Path) -> None:
    conv = conversion(summary.read_bytes(), summary_name=summary.name)
    problems, said = front.agree(conv, load(his(tmp_path, b"") / "takeoff.py", "takeoff"))
    assert problems == [] and said["board_parts"] == conv["total_board_parts"]


def test_a_takeoff_py_that_counts_otherwise_is_named(summary: Path, tmp_path: Path) -> None:
    conv = conversion(summary.read_bytes(), summary_name=summary.name)
    scripts = his(tmp_path, b"", takeoff=takeoff_source(extra_part=True))
    problems, _ = front.agree(conv, load(scripts / "takeoff.py", "takeoff"))
    assert problems == ["board parts: takeoff.py says 12, ng_convert 11"]


# ----- the whole conversion, short of writing


def test_a_conversion_carries_what_checked_it(summary: Path, tmp_path: Path) -> None:
    data = summary.read_bytes()
    conv = front.convert_summary(summary, job=None, scripts=his(tmp_path, data))
    block = conv["ng_convert"]
    assert block["version"] == front.VERSION and block["takeoff_py"]["board_parts"] == 11
    assert block["checked"] == ["takeoff.py's rules", "parse_pulled.py's reading of every board line",
                                "takeoff.py's reading of these parts"]
    assert conv["pulled_classified"][-1]["bucket"] == "third_party"          # its classify, for the rest
    assert {k: v for k, v in conv.items() if k != "ng_convert"} == {
        k: v for k, v in conversion(data, summary_name=summary.name, classify=lambda r: (
            "third_party", "made-up family", "bought, not cut")).items() if k != "ng_convert"}


@pytest.mark.parametrize("kw, words", [
    ({"without": ("takeoff.py",)}, "takeoff.py isn't beside ng_convert.py"),
    ({"without": ("parse_pulled.py",)}, "parse_pulled.py isn't beside ng_convert.py"),
    ({"without": ("classify.py",)}, "classify.py isn't beside ng_convert.py"),
    ({"takeoff": takeoff_source(waste=1.15)}, "takeoff.py has changed since ng_convert was made"),
    ({"takeoff": takeoff_source(extra_part=True)}, "takeoff.py reads these parts differently"),
])
def test_it_refuses_when_his_scripts_are_missing_moved_or_disagree(summary: Path, tmp_path: Path, kw, words) -> None:
    with pytest.raises(Refused, match=words):
        front.convert_summary(summary, job=None, scripts=his(tmp_path, summary.read_bytes(), **kw))


def test_it_refuses_when_the_second_reading_differs_on_a_board_line(summary: Path, tmp_path: Path) -> None:
    data = summary.read_bytes()
    with pytest.raises(Refused, match="differ on 1 board line"):
        front.convert_summary(summary, job=None, scripts=his(tmp_path, data, rows=rows_of(data, drop=0)))


def test_it_refuses_a_garage_job_in_plain_words(tmp_path: Path) -> None:
    path = tmp_path / "s.pdf"
    path.write_bytes(summary_pdf(parts=PARTS, designs=(("Testerson - Garage", "Garage"),)))
    with pytest.raises(Refused, match="garage job"):
        front.convert_summary(path, job=None, scripts=his(tmp_path, path.read_bytes()))


# ----- never inside the toolkit, never over a file unless told


def test_it_never_writes_inside_its_own_toolkit(summary: Path, tmp_path: Path) -> None:
    scripts = his(tmp_path, summary.read_bytes())
    for inside in (scripts.parent, scripts, scripts.parent / "reference" / "out", scripts.parent / "new" / "deep"):
        with pytest.raises(Refused, match="never writes inside the toolkit"):
            front.refuse_inside(inside, scripts.parent)
    front.refuse_inside(summary.parent, scripts.parent)


def test_the_command_line_refuses_an_out_folder_in_the_toolkit_and_makes_nothing(summary: Path, tmp_path: Path,
                                                                              capsys) -> None:
    scripts = his(tmp_path, summary.read_bytes())
    before = sorted(p for p in scripts.parent.rglob("*"))
    code = front.main([str(summary), "--out", str(scripts.parent / "out")], scripts=scripts)
    assert code == front.REFUSED and "never writes inside the toolkit" in capsys.readouterr().out
    assert sorted(p for p in scripts.parent.rglob("*")) == before


def test_a_wrong_command_line_is_exit_2(capsys) -> None:
    assert front.main([]) == front.BAD_COMMAND
    assert front.main(["a.pdf", "--frobnicate"]) == front.BAD_COMMAND


def test_files_already_there_are_never_written_over_without_replace(summary: Path, tmp_path: Path) -> None:
    conv = front.convert_summary(summary, job=None, scripts=his(tmp_path, summary.read_bytes()))
    there = summary.parent / f"{JOB}_IC_Conversion.json"
    there.write_text("the chat's own", encoding="utf-8")
    with pytest.raises(Refused, match="is there already"):
        front.write(conv, summary.parent, replace=False)
    assert there.read_text(encoding="utf-8") == "the chat's own"
    assert not list(summary.parent.glob("*.part"))


@pytest.mark.skipif(importlib.util.find_spec("openpyxl") is not None, reason="this Python has openpyxl")
def test_without_openpyxl_it_says_so_and_writes_nothing(summary: Path, tmp_path: Path, capsys) -> None:
    scripts = his(tmp_path, summary.read_bytes())
    code = front.main([str(summary)], scripts=scripts)
    assert code == front.REFUSED and "openpyxl isn't installed" in capsys.readouterr().out
    assert sorted(p.name for p in summary.parent.iterdir()) == [summary.name]


def test_the_lines_it_prints_say_what_it_could_not_verify(summary: Path, tmp_path: Path) -> None:
    conv = front.convert_summary(summary, job=None, scripts=his(tmp_path, summary.read_bytes()))
    lines = front.said(conv, summary.parent / f"{JOB}_IC_Conversion.json")
    assert lines[0] == f"{JOB}: IC Conversion written: {JOB}_IC_Conversion.json and .xlsx, in {summary.parent}."
    assert lines[1] == "Takeoff not complete: 1 line could not verify, and it is never filled in:"
    assert lines[2].startswith("1. Page 4: 2 of item CK-WH")
    assert json.dumps(lines)


def test_a_bucket_with_no_sheet_size_is_said_as_a_bucket_not_a_line(tmp_path: Path) -> None:
    path = tmp_path / "s.pdf"
    path.write_bytes(summary_pdf(parts=[("SH-CC", 2, "SH 14x24x3/4 FIX CC"), ("BP-CC", 1, "BP 14x96x3/8 CC")]))
    conv = front.convert_summary(path, job=None, scripts=his(tmp_path, path.read_bytes()))
    lines = front.said(conv, tmp_path / f"{JOB}_IC_Conversion.json")
    assert lines[1] == "Takeoff not complete: 1 bucket has no sheet size, and it is never filled in:"
    assert lines[2] == "1. No sheet size for 1/4 Egger Black U999: could not verify its sheets."
