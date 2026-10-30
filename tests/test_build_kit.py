r"""
tools\build_kit.py: one command from the repo to the test kit zip.

PyInstaller never runs here. A fake build step lays out a small release the way ngrunner.spec
does, with the real installer\manifest.py, and a fake NGRunner.exe answers --version and
--self-test. Everything is written under tmp_path, in a made-up repo; the real repo is only read.
"""
from __future__ import annotations

import _winapi
import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ngr_tools_build_kit", ROOT / "tools" / "build_kit.py")
build_kit = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = build_kit   # a dataclass looks its module up here
_spec.loader.exec_module(build_kit)
manifest = build_kit.MANIFEST

VERSION = "1.2.3"
ZIP_NAME = f"NGRunner-test-kit-{VERSION}.zip"
RELEASE_FILES = ("app/NGRunner.exe", "app/_internal/base_library.zip",
                 "launcher/NGRunnerLauncher.exe", "launcher/_internal/base_library.zip",
                 *(f"installer/{name}" for name in manifest.INSTALLER_FILES))


def fake_build(repo: Path, out: Path) -> None:
    dist = out / "dist"
    for name in RELEASE_FILES:
        path = dist / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode("utf-8"))
    manifest.write(dist, VERSION, built_at="2026-10-30T12:00:00+00:00", python="3.14.0",
                   pyinstaller="6.22.3")


class FakeExe:
    """NGRunner.exe's answers, and what it was asked."""

    def __init__(self, version=(0, f"NG Runner {VERSION}\n"),
                 self_test=(0, f"NG Runner {VERSION} self-test passed.\n")):
        self.answers = {"--version": version, "--self-test": self_test}
        self.calls: list[tuple[Path, list[str], dict[str, str]]] = []

    def __call__(self, exe, args, env, timeout_s):
        self.calls.append((Path(exe), list(args), dict(env)))
        answer = self.answers[args[0]]
        if isinstance(answer, Exception):
            raise answer
        return answer


class CountingBuild:
    def __init__(self):
        self.calls = 0

    def __call__(self, repo, out):
        self.calls += 1
        fake_build(repo, out)


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    files = {"pyproject.toml": f'[project]\nname = "ngrunner"\nversion = "{VERSION}"\n',
             "tools/setup_check.cmd": "@echo off\r\n",
             "tools/setup_check.ps1": "# the Setup Check\n",
             "docs/testing/jims-pc.md": "# Testing on Jim's PC\n",
             "installer/entry_app.py": "# build-time only: never in a release\n"}
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    return root


@pytest.fixture
def out(tmp_path) -> Path:
    return tmp_path / "builds" / "kit"


def listing(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))


def kit_run(repo: Path, out: Path | str, *, build=fake_build, exe=None) -> tuple[int, str]:
    said: list[str] = []
    code = build_kit.main(["--out", str(out)], repo=repo, build=build, run=exe or FakeExe(),
                          say=said.append)
    return code, "\n".join(said)


# ----- the kit


def test_the_kit_holds_the_build_the_setup_check_the_installer_and_the_test_guide(repo, out):
    code, _ = kit_run(repo, out)
    assert code == 0
    with zipfile.ZipFile(out / ZIP_NAME) as kit:
        assert set(kit.namelist()) == {*RELEASE_FILES, "manifest.json", *build_kit.KIT_FILES}
        assert kit.testzip() is None
        for name in build_kit.KIT_FILES:
            assert kit.read(name) == (repo / name).read_bytes()


def test_the_unzipped_kit_is_a_release_install_ps1_accepts(repo, out, tmp_path):
    # install.ps1 refuses a file its manifest doesn't list, so the repo's installer\ (with its
    # build-time scripts) must not be what goes in, and the kit's other files must not count.
    kit_run(repo, out)
    unpacked = tmp_path / "on Jim's PC"
    with zipfile.ZipFile(out / ZIP_NAME) as kit:
        kit.extractall(unpacked)
    assert manifest.verify(unpacked) == []
    assert not (unpacked / "installer" / "entry_app.py").exists()
    assert (unpacked / "tools" / "setup_check.cmd").read_bytes() == b"@echo off\r\n"


def test_it_prints_the_zips_path_size_and_sha256_and_the_hash_install_ps1_takes(repo, out):
    _, said = kit_run(repo, out)
    target = out / ZIP_NAME
    assert f"Test kit: {target.resolve()}" in said
    assert f"{target.stat().st_size:,} bytes" in said
    assert f"sha256: {hashlib.sha256(target.read_bytes()).hexdigest()}" in said
    pin = hashlib.sha256((out / "dist" / "manifest.json").read_bytes()).hexdigest()
    assert f"For install.ps1 -ManifestSha256: {pin}" in said


def test_the_exe_says_its_version_then_self_tests_with_its_data_folder_below_the_output(repo, out):
    exe = FakeExe()
    kit_run(repo, out, exe=exe)
    assert [args for _, args, _ in exe.calls] == [["--version"], ["--self-test"]]
    assert {path for path, _, _ in exe.calls} == {out / "dist" / "app" / "NGRunner.exe"}
    assert {env["NGR_APP_DATA"] for _, _, env in exe.calls} == {str(out / "selftest-data")}


def test_a_kit_changes_nothing_in_the_repo(repo, out):
    before = listing(repo)
    kit_run(repo, out)
    assert listing(repo) == before


# ----- where it may write


@pytest.mark.parametrize("inside", [".", "kit", "a/b/kit", ".venv/kit"])
def test_it_refuses_a_folder_inside_the_repo_before_building(repo, inside):
    build = CountingBuild()
    before = listing(repo)
    code, said = kit_run(repo, repo / inside, build=build)
    assert code == 1
    assert "inside the repo" in said
    assert build.calls == 0
    assert listing(repo) == before


def test_it_refuses_the_repo_however_it_is_spelled(repo):
    code, said = kit_run(repo, str(repo).upper() + "\\kit", build=CountingBuild())
    assert code == 1
    assert "inside the repo" in said
    assert not (repo / "kit").exists()


def test_it_refuses_a_junction_that_leads_into_the_repo(repo, tmp_path):
    link = tmp_path / "looks outside"
    _winapi.CreateJunction(str(repo), str(link))
    try:
        code, said = kit_run(repo, link / "kit", build=CountingBuild())
    finally:
        os.rmdir(link)   # the junction, never what it points at
    assert code == 1
    assert "inside the repo" in said
    assert not (repo / "kit").exists()


def test_it_refuses_a_relative_path(repo):
    build = CountingBuild()
    code, said = kit_run(repo, "kit", build=build)
    assert code == 1
    assert "not a full path" in said
    assert build.calls == 0


def test_it_refuses_a_folder_that_holds_anything_and_a_file(repo, tmp_path):
    full = tmp_path / "full"
    full.mkdir()
    (full / "old build.txt").write_text("keep me", encoding="utf-8")
    afile = tmp_path / "a file"
    afile.write_text("keep me", encoding="utf-8")
    build = CountingBuild()
    for out in (full, afile):
        code, said = kit_run(repo, out, build=build)
        assert code == 1
        assert "not a new, empty folder" in said
    assert build.calls == 0
    assert (full / "old build.txt").read_text(encoding="utf-8") == "keep me"
    assert afile.read_text(encoding="utf-8") == "keep me"


def test_an_empty_folder_is_fine(repo, out):
    out.mkdir(parents=True)
    assert kit_run(repo, out)[0] == 0


# ----- stopping with a plain message and no zip


def no_zip(out: Path) -> bool:
    return not list(out.glob("NGRunner-test-kit-*"))


def test_a_failed_self_test_stops_with_what_it_said(repo, out):
    failing = (1, f"NG Runner {VERSION} self-test found 1 problem:\n1. A module did not import.\n")
    code, said = kit_run(repo, out, exe=FakeExe(self_test=failing))
    assert code == 1
    assert "The self-test failed (exit code 1), so no test kit was made." in said
    assert "1. A module did not import." in said
    assert no_zip(out)


@pytest.mark.parametrize(("error", "words"), [
    (subprocess.TimeoutExpired("NGRunner.exe", 600), "did not finish in 10 minutes"),
    (OSError(5, "Access is denied"), "could not start (Access is denied)"),
])
def test_a_self_test_that_hangs_or_cannot_start_stops_plainly(repo, out, error, words):
    code, said = kit_run(repo, out, exe=FakeExe(self_test=error))
    assert code == 1
    assert words in said
    assert "no test kit was made" in said
    assert no_zip(out)


@pytest.mark.parametrize("answer", [(0, "NG Runner 9.9.9\n"), (1, f"NG Runner {VERSION}\n"), (0, "")])
def test_an_exe_that_is_not_this_version_stops_before_the_self_test(repo, out, answer):
    exe = FakeExe(version=answer)
    code, said = kit_run(repo, out, exe=exe)
    assert code == 1
    assert "no test kit was made" in said
    assert [args for _, args, _ in exe.calls] == [["--version"]]
    assert no_zip(out)


def test_a_build_with_no_exe_stops(repo, out):
    def build(repo, out):
        (out / "dist").mkdir()

    code, said = kit_run(repo, out, build=build)
    assert code == 1
    assert "without app/NGRunner.exe" in said


def test_a_build_that_does_not_match_its_manifest_stops(repo, out):
    def build(repo, out):
        fake_build(repo, out)
        (out / "dist" / "installer" / "entry_app.py").write_text("extra", encoding="utf-8")

    code, said = kit_run(repo, out, build=build)
    assert code == 1
    assert "install.ps1 would refuse it" in said
    assert "installer/entry_app.py is not in the manifest" in said
    assert no_zip(out)


def test_a_missing_kit_file_stops_before_the_build(repo, out):
    (repo / "tools" / "setup_check.ps1").unlink()
    build = CountingBuild()
    code, said = kit_run(repo, out, build=build)
    assert code == 1
    assert "tools/setup_check.ps1" in said
    assert build.calls == 0


def test_a_zip_that_fails_half_way_leaves_nothing_named_a_zip(repo, out):
    def build(repo, out):
        fake_build(repo, out)
        (repo / "docs" / "testing" / "jims-pc.md").unlink()   # gone once the build has finished

    code, said = kit_run(repo, out, build=build)
    assert code == 1
    assert "The test kit could not be made" in said
    assert "Traceback" not in said
    assert no_zip(out)


def test_an_unreadable_version_stops(repo, out):
    (repo / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    code, said = kit_run(repo, out, build=CountingBuild())
    assert code == 1
    assert "version could not be read" in said


def test_a_wrong_command_line_exits_2(capsys):
    with pytest.raises(SystemExit) as stopped:
        build_kit.main([])
    assert stopped.value.code == 2
    assert "--out" in capsys.readouterr().err


# ----- the real build step and the real exe step


def test_the_build_step_is_the_runbooks_command_from_the_repo_root(repo, out, monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen.update(kwargs, command=command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(build_kit.subprocess, "run", fake_run)
    build_kit.pyinstaller_build(repo, out)
    assert seen["command"] == [str(repo / ".venv" / "Scripts" / "python.exe"), "-m", "PyInstaller",
                               "ngrunner.spec", "--noconfirm", "--distpath", str(out / "dist"),
                               "--workpath", str(out / "build")]
    assert seen["cwd"] == repo
    assert seen["env"]["PYINSTALLER_CONFIG_DIR"] == str(out / "pyinstaller-config")


@pytest.mark.parametrize(("outcome", "words"), [
    (subprocess.CompletedProcess([], 1), "The build failed (exit code 1)"),
    (subprocess.TimeoutExpired("python", 1800), "did not finish in 30 minutes"),
    (FileNotFoundError(2, "The system cannot find the file specified"), "could not start"),
])
def test_a_build_step_that_fails_says_so_plainly(repo, out, monkeypatch, outcome, words):
    def fake_run(command, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(build_kit.subprocess, "run", fake_run)
    with pytest.raises(build_kit.KitError, match=re.escape(words)):
        build_kit.pyinstaller_build(repo, out)


def test_the_exe_step_returns_the_exit_code_and_what_was_printed():
    code, text = build_kit.run_exe(Path(sys.executable),
                                   ["-c", "import sys; print('said'); sys.exit(3)"], os.environ, 60)
    assert (code, text.strip()) == (3, "said")


# ----- the real repo (read only)


def test_the_real_repo_has_what_the_kit_takes():
    for name in (*build_kit.KIT_FILES, "ngrunner.spec"):
        assert (ROOT / name).is_file(), name
    assert re.fullmatch(r"[0-9]+(\.[0-9]+)*", build_kit.read_version(ROOT))


def test_the_setup_guide_names_every_file_and_script_property():
    google = ROOT / "google"
    guide = (ROOT / "docs" / "testing" / "apps-script-setup.md").read_text(encoding="utf-8")
    needed = {p.name for p in google.iterdir() if p.suffix in (".gs", ".json")}
    table = (google / "README.md").read_text(encoding="utf-8").split("## Script Properties")[1]
    for row in table.split("\n## ")[0].splitlines():
        if row.startswith("| `"):
            needed.update(re.findall(r"`([^`]+)`", row.split("|")[1]))
    code = "\n".join(p.read_text(encoding="utf-8") for p in google.glob("*.gs"))
    needed.update(re.findall(r"\b(?:setting_|requiredSetting_|flagSetting_)\('([A-Z_]+)'", code))
    needed.update(("installTriggers", "checkSetup", "everything NG Runner needs is set"))
    assert not sorted(name for name in needed if name not in guide)
