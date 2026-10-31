"""
Packaging NG Runner's converter as one script of Jim's toolkit, scripts/ng_convert.py, and offering it
only when his regression is clean on a staged copy with it added (tools/delivery/package.py).

Made-up toolkits only (tests/helpers_delivery.py); his real one is in test_delivery_real.py. The script
must be NG Runner's modules unchanged, need nothing but the standard library, pypdf and openpyxl, run on
Linux as on Windows, refuse to run when changed, and never be written into a toolkit or on Drive.
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from helpers_delivery import toolkit
from ngrunner.clock import FakeClock
from ngrunner.toolkit import Regression, hash_of_tree
from tools.delivery import package
from tools.delivery.package import PackagingRefused

ROOT = Path(__file__).resolve().parent.parent
MADE = "2026-10-31"
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
EMBEDDED = {
    "ngrunner", "ngrunner.match", "ngrunner.intake", "ngrunner.intake.summary_check", "ngrunner.intake.branch",
    "ngrunner.convert", "ngrunner.convert.rules", "ngrunner.convert.summary_lines", "ngrunner.convert.parts",
    "ngrunner.convert.takeoff", "tools", "tools.workbook", "tools.workbook.sheets", "tools.workbook.xlsx",
    "tools.workbook.conversion", "tools.delivery", "tools.delivery.front",
}


@pytest.fixture(scope="module")
def script() -> str:
    return package.build_script(made=MADE)


def run_script(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(path), *args], capture_output=True,
                          text=True, encoding="utf-8", timeout=120)


# ----- the script: NG Runner's modules, unchanged


def test_it_embeds_exactly_the_modules_the_converter_needs() -> None:
    assert {m.name for m in package.modules()} == EMBEDDED


def test_each_embedded_module_is_ngrunners_own_file_byte_for_byte(script: str) -> None:
    tree = ast.parse(script)
    sources = {node.targets[0].slice.value: node.value.value for node in ast.walk(tree)
               if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript)
               and getattr(node.targets[0].value, "id", "") == "_SOURCES"}
    for m in package.modules():
        on_disk = m.path.read_bytes().decode("utf-8").replace("\r\n", "\n") if m.path else ""   # as git holds it
        assert sources[m.name] == on_disk, m.name
    hashes = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                  and getattr(node.targets[0], "id", "") == "_SHA256")
    table = ast.literal_eval(hashes)
    assert table == {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in sources.items()}


def test_it_needs_only_the_standard_library_pypdf_and_openpyxl() -> None:
    assert package.third_party() == {"pypdf", "openpyxl"}


def test_it_holds_nothing_tied_to_windows_or_to_a_drive(script: str) -> None:
    imported = {alias.name.split(".")[0] for node in ast.walk(ast.parse(script)) for alias in getattr(node, "names", [])
                if isinstance(node, (ast.Import, ast.ImportFrom))}
    for m in package.modules():
        if m.path:
            tree = ast.parse(m.path.read_text(encoding="utf-8"))
            imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
            imported |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"winreg", "msvcrt", "win32api", "win32file", "win32job", "pywintypes", "ctypes",
                           "psutil", "_winapi", "nt"}
    assert not any(drive in script for drive in ("C:\\\\", "G:\\\\", "C:/", "G:/", "/mnt/", "/tmp/"))


def test_it_says_what_it_is_in_its_first_lines(script: str) -> None:
    head = script.splitlines()[:30]
    assert head[0].startswith('r"""ng_convert.py')
    assert any("python scripts/ng_convert.py" in line for line in head)
    assert any(f"version {package.version()}, made {MADE}" in line for line in head)


def test_the_version_names_its_sources(script: str) -> None:
    assert package.version() == package.version() and len(package.version()) == 12
    assert f'VERSION = "{package.version()}"' in script


def test_it_runs_and_checks_every_module_on_its_version_line(script: str, tmp_path: Path) -> None:
    path = tmp_path / "ng_convert.py"
    path.write_text(script, encoding="utf-8", newline="\n")
    done = run_script(path, "--version")
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.strip() == f"ng_convert {package.version()}: {len(EMBEDDED)} modules, each as made."


def test_a_module_changed_inside_it_refuses_to_run(script: str, tmp_path: Path) -> None:
    path = tmp_path / "ng_convert.py"
    path.write_text(script.replace("WASTE = 1.10", "WASTE = 1.15", 1), encoding="utf-8", newline="\n")
    done = run_script(path, "--version")
    assert done.returncode == 1
    assert done.stdout.strip() == ("ng_convert.py was changed after it was made (ngrunner.convert.rules), so nothing"
                                   " was converted. Ask Sulaiman for a new one.")


def test_it_refuses_to_run_inside_another_copy_of_ng_runner(script: str, tmp_path: Path) -> None:
    path = tmp_path / "ng_convert.py"
    path.write_text(script, encoding="utf-8", newline="\n")
    code = f"import sys; sys.path.insert(0, {str(ROOT)!r}); import ngrunner, runpy; runpy.run_path({str(path)!r}, run_name='__main__')"
    done = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert done.returncode == 1 and "must run as its own script" in done.stdout


# ----- where the delivery may be written


@pytest.mark.parametrize("where", ["toolkit", "scripts", "deep in a toolkit", "named _NG_Automation"])
def test_it_is_never_written_into_a_toolkit(tmp_path: Path, where: str) -> None:
    scripts = toolkit(tmp_path, b"")
    (scripts / "regression.py").write_text("# made-up\n", encoding="utf-8")
    out = {"toolkit": scripts.parent, "scripts": scripts, "deep in a toolkit": scripts.parent / "reference" / "x",
           "named _NG_Automation": tmp_path / "other" / "_NG_Automation" / "x"}[where]
    with pytest.raises(PackagingRefused, match="never written into a toolkit"):
        package.refuse_out(out)


@pytest.mark.parametrize("out", [r"G:\My Drive\delivery", r"\\server\share\delivery", r"Z:\nowhere"])
def test_it_is_never_written_on_drive_or_off_this_pc(out: str) -> None:
    with pytest.raises(PackagingRefused, match="local disk"):
        package.refuse_out(Path(out))


@pytest.mark.parametrize("name", ["Mozaik Shared", "NG Jobs", "NG Conversion program Claude"])
def test_it_is_never_written_where_jims_work_lives(tmp_path: Path, name: str) -> None:
    with pytest.raises(PackagingRefused, match=name):
        package.refuse_out(tmp_path / name / "delivery")


def test_in_the_repo_it_goes_only_into_the_gitignored_fixtures(tmp_path: Path) -> None:
    with pytest.raises(PackagingRefused, match="git"):
        package.refuse_out(ROOT / "docs" / "delivery")
    package.refuse_out(ROOT / ".local-fixtures" / "delivery")
    package.refuse_out(tmp_path / "delivery")


# ----- offered only when his regression is clean with it added


def regress(clean: bool):
    def run(snapshot) -> Regression:
        assert (snapshot.path / "scripts" / package.SCRIPT).is_file()        # regressed WITH the script in
        words = "Its regression is clean." if clean else "Its regression moved 1 thing on Jim's frozen jobs."
        return Regression(snapshot.sha256, clean, words, 0 if clean else 1)
    return run


def test_nothing_is_offered_when_his_regression_isnt_clean(tmp_path: Path) -> None:
    kit = toolkit(tmp_path / "jims", b"").parent
    out = tmp_path / "delivery"

    offered = package.offer(toolkit=kit, out=out, work=tmp_path / "work", clock=FakeClock(NOW),
                            regress=regress(False), made=MADE)

    assert not offered.offered and offered.words == "Not offered: Its regression moved 1 thing on Jim's frozen jobs."
    assert not out.exists() or not any(out.rglob("*"))


def test_a_clean_regression_offers_the_script_its_note_and_its_manifest(tmp_path: Path) -> None:
    kit = toolkit(tmp_path / "jims", b"").parent
    before = hash_of_tree(kit)
    out = tmp_path / "delivery"

    offered = package.offer(toolkit=kit, out=out, work=tmp_path / "work", clock=FakeClock(NOW),
                            regress=regress(True), made=MADE)

    assert offered.offered
    folder = out / f"ng_convert-{package.version()}"
    assert sorted(p.name for p in folder.iterdir()) == ["MANIFEST.json", "NOTE-for-Jim.md", "ng_convert.py"]
    assert (folder / "ng_convert.py").read_text(encoding="utf-8") == package.build_script(made=MADE)
    manifest = json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["version"] == package.version() and manifest["regression"]["clean"] is True
    assert manifest["script_sha256"] == hashlib.sha256((folder / "ng_convert.py").read_bytes()).hexdigest()
    assert set(manifest["modules"]) == EMBEDDED
    assert hash_of_tree(kit) == before                                   # his toolkit: read, never written


def test_the_note_for_jim_is_one_plain_page_with_the_rules_line(tmp_path: Path) -> None:
    note = package.note_for_jim(version="abc123def456", made=MADE, regression="clean", frozen=[], score=None)
    lines = note.splitlines()
    assert len(lines) <= 60 and lines[0] == "# ng_convert: a note for Jim"
    assert "Never write a per-job script" in note and "never filled in" in note
    assert "python scripts/ng_convert.py" in note
    for word in ("pypdf", "openpyxl", "ast", "sha256", "meta_path"):
        assert word not in note, word
