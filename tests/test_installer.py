"""The installer: install.ps1 and uninstall.ps1, the task XML, the release manifest and the
PyInstaller spec.

The scripts run for real, but only into tmp_path and only with -NoRegister, so no scheduled
task, Run-key value or Start Menu item is ever made on this PC; tests check that by reading
(never changing) what Windows has before and after. The task XML is checked by parsing it,
with ElementTree and with Task Scheduler's own parser (a task definition filled in memory,
never registered). The app and the launcher are a small fake console program, compiled once
per run, that logs its arguments and exits with the code a test asks for
(NGR_FAKE_EXIT_<FIRST ARGUMENT>). Starting PowerShell takes seconds on a busy PC, so each
test runs its script calls one after another in a single PowerShell process.
"""
from __future__ import annotations

import _winapi
import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "installer"
TASK_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

_spec = importlib.util.spec_from_file_location("ngr_release_manifest", INSTALLER / "manifest.py")
manifest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(manifest)

FAKE_APP = r"""
using System;
using System.IO;
public static class FakeNGRunner {
    public static int Main(string[] args) {
        string exe = Path.GetFileName(System.Reflection.Assembly.GetExecutingAssembly().Location);
        string line = exe + " " + string.Join(" ", args);
        string log = Environment.GetEnvironmentVariable("NGR_FAKE_LOG");
        if (!string.IsNullOrEmpty(log)) { File.AppendAllText(log, line + Environment.NewLine); }
        Console.WriteLine("fake " + line);
        string key = args.Length > 0 ? args[0].TrimStart('-').Replace('-', '_').ToUpperInvariant() : "NONE";
        int code;
        return int.TryParse(Environment.GetEnvironmentVariable("NGR_FAKE_EXIT_" + key), out code) ? code : 0;
    }
}
"""


# ----- running PowerShell, and reading (never changing) what Windows has


def powershell(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=900)


@dataclass(frozen=True)
class Ran:
    code: int
    out: str


def run_scripts(*calls: tuple[str, list[str]], env: dict[str, str] | None = None) -> list[Ran]:
    """Run each (script, arguments) in turn in one PowerShell process; arguments that start
    with - are parameter names, the rest are values."""
    lines = []
    for n, (script, args) in enumerate(calls):
        words = " ".join(a if a.startswith("-") else _quoted(a) for a in args)
        lines.append(f"$global:LASTEXITCODE = -1; Write-Host '=== call {n}'; & {_quoted(script)} {words};"
                     " Write-Host ('=== exit ' + $LASTEXITCODE)")
    done = powershell("-Command", "; ".join(lines), env=env)
    found = re.findall(r"=== call (\d+)\r?\n(.*?)=== exit (-?\d+)", done.stdout, flags=re.S)
    assert len(found) == len(calls), done.stdout + done.stderr
    return [Ran(int(code), out) for _, out, code in found]


def _quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def registered() -> tuple[bool, bool, bool, bool]:
    """Whether this PC has NG Runner's two tasks, its Run-key value and its Start Menu item."""
    tasks = [subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True).returncode == 0
             for name in ("NGRunner-logon", "NGRunner-ensure")]
    run_key = subprocess.run(["reg", "query", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                              "/v", "NGRunner"], capture_output=True).returncode == 0
    start_menu = Path(os.environ["APPDATA"], "Microsoft", "Windows", "Start Menu", "Programs", "NG Runner.lnk")
    return tasks[0], tasks[1], run_key, start_menu.exists()


# ----- a fake release, and installing it


@pytest.fixture(scope="session")
def fake_exe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("fake-app")
    source, exe = folder / "FakeNGRunner.cs", folder / "FakeNGRunner.exe"
    source.write_text(FAKE_APP, encoding="utf-8")
    done = powershell("-Command", f"Add-Type -Path {_quoted(str(source))} -OutputAssembly {_quoted(str(exe))}"
                      " -OutputType ConsoleApplication")
    assert done.returncode == 0 and exe.is_file(), done.stderr
    return exe


@pytest.fixture(scope="session")
def before() -> tuple[bool, bool, bool, bool]:
    return registered()


def make_release(where: Path, fake_exe: Path, version: str = "0.0.1", payload: bytes = b"runtime") -> Path:
    release = where / f"NGRunner-{version}"
    for folder, exe in (("app", "NGRunner.exe"), ("launcher", "NGRunnerLauncher.exe")):
        (release / folder / "_internal").mkdir(parents=True)
        shutil.copyfile(fake_exe, release / folder / exe)
        (release / folder / "_internal" / "python314.dll").write_bytes(payload + version.encode("ascii"))
    manifest.copy_installer(INSTALLER, release)
    manifest.write(release, version, built_at="2026-10-29T20:00:00+00:00", python="3.14.0",
                   pyinstaller="6.22.3")
    return release


def install_call(root: Path, release: Path, *extra: str) -> tuple[str, list[str]]:
    return (str(release / "installer" / "install.ps1"),
            ["-Root", str(root), "-Build", str(release), "-NoRegister", *extra])


def uninstall_call(root: Path, *extra: str) -> tuple[str, list[str]]:
    return str(INSTALLER / "uninstall.ps1"), ["-Root", str(root), "-NoRegister", *extra]


def fake_env(log: Path, **exit_codes: str) -> dict[str, str]:
    return {**os.environ, "NGR_FAKE_LOG": str(log),
            **{f"NGR_FAKE_EXIT_{name}": code for name, code in exit_codes.items()}}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "R&D Programs" / "NGRunner"      # an & proves values are escaped in the XML


@dataclass(frozen=True)
class Installed:
    root: Path
    ran: Ran
    calls: list[str]


@pytest.fixture(scope="module")
def clean_install(tmp_path_factory: pytest.TempPathFactory, fake_exe: Path) -> Installed:
    where = tmp_path_factory.mktemp("clean")
    root = where / "R&D Programs" / "NGRunner"
    [ran] = run_scripts(install_call(root, make_release(where, fake_exe)), env=fake_env(where / "calls.txt"))
    return Installed(root, ran, (where / "calls.txt").read_text(encoding="utf-8").splitlines())


# ----- a clean install


def test_a_clean_install_puts_every_part_in_place_and_registers_nothing(clean_install, fake_exe, before):
    root = clean_install.root
    assert clean_install.ran.code == 0, clean_install.ran.out
    assert (root / "launcher" / "NGRunnerLauncher.exe").read_bytes() == fake_exe.read_bytes()
    assert (root / "app-0.0.1" / "_internal" / "python314.dll").read_bytes() == b"runtime0.0.1"
    assert (root / "installer" / "uninstall.ps1").is_file() and (root / "installer" / "common.ps1").is_file()
    assert sorted(p.name for p in (root / "tasks").iterdir()) == ["NGRunner-ensure.xml", "NGRunner-logon.xml"]
    assert sorted(p.name for p in root.iterdir()) == ["app-0.0.1", "installer", "launcher",
                                                      "ngrunner-install.json", "tasks"]
    assert clean_install.calls == ["NGRunner.exe --self-test --record"]   # no shortcut: -NoRegister
    assert "NG Runner 0.0.1 is installed" in clean_install.ran.out
    assert registered() == before


def test_the_task_xml_is_filled_in_for_this_user_and_this_install(clean_install):
    root = clean_install.root
    sid = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                         encoding="utf-8").stdout.strip().split(",")[1].strip('"')
    for name, argument in (("NGRunner-logon", "--logon"), ("NGRunner-ensure", "--ensure")):
        raw = (root / "tasks" / f"{name}.xml").read_bytes()
        assert raw.startswith(b"\xff\xfe")                                    # UTF-16, as Windows exports
        task = ET.fromstring(raw.decode("utf-16"))
        assert task.get("version") == "1.2"
        assert task.findtext("t:Principals/t:Principal/t:UserId", namespaces=TASK_NS) == sid
        assert task.findtext("t:Principals/t:Principal/t:LogonType", namespaces=TASK_NS) == "InteractiveToken"
        assert task.findtext("t:Principals/t:Principal/t:RunLevel", namespaces=TASK_NS) == "LeastPrivilege"
        assert task.findtext("t:Actions/t:Exec/t:Command", namespaces=TASK_NS) == str(
            root / "launcher" / "NGRunnerLauncher.exe")
        assert task.findtext("t:Actions/t:Exec/t:Arguments", namespaces=TASK_NS) == argument
        settings = {child.tag.split("}")[1]: child.text for child in task.find("t:Settings", TASK_NS)}
        assert settings["Priority"] == "4" and settings["ExecutionTimeLimit"] == "PT0S"
        assert settings["MultipleInstancesPolicy"] == "IgnoreNew"
        assert settings["DisallowStartIfOnBatteries"] == "false" == settings["StopIfGoingOnBatteries"]
    logon = ET.fromstring((root / "tasks" / "NGRunner-logon.xml").read_bytes().decode("utf-16"))
    assert logon.findtext("t:Triggers/t:LogonTrigger/t:UserId", namespaces=TASK_NS) == sid
    ensure = ET.fromstring((root / "tasks" / "NGRunner-ensure.xml").read_bytes().decode("utf-16"))
    assert ensure.findtext("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", namespaces=TASK_NS) == "PT10M"
    assert ensure.find("t:Triggers/t:TimeTrigger/t:Repetition/t:Duration", TASK_NS) is None  # for ever


def test_task_scheduler_itself_reads_both_tasks_without_registering_them(clean_install, before):
    reads = []
    for name in ("NGRunner-logon", "NGRunner-ensure"):
        path = _quoted(str(clean_install.root / "tasks" / f"{name}.xml"))
        reads.append(
            "$t = $s.NewTask(0);"
            f" $t.XmlText = [IO.File]::ReadAllText({path});"
            " '{0}|{1}|{2}|{3}|{4}|{5}|{6}' -f $t.Settings.Priority, $t.Settings.ExecutionTimeLimit,"
            " $t.Settings.MultipleInstances, $t.Settings.DisallowStartIfOnBatteries,"
            " $t.Settings.StopIfGoingOnBatteries, $t.Triggers.Item(1).Type, $t.Principal.LogonType")
    done = powershell("-Command", "$s = New-Object -ComObject Schedule.Service; $s.Connect(); " + "; ".join(reads))
    assert done.returncode == 0, done.stderr
    # 2: IgnoreNew. Trigger type 9 is at logon, 1 at a time. Logon type 3 is "only when signed in".
    assert done.stdout.split() == ["4|PT0S|2|False|False|9|3", "4|PT0S|2|False|False|1|3"]
    assert registered() == before


_WINDOWS_STAND_INS = r"""
$global:asked = New-Object System.Collections.Generic.List[string]
function Register-ScheduledTask { [CmdletBinding()] param([string]$TaskName, [string]$Xml, [switch]$Force)
    $global:asked.Add('register ' + $TaskName + ' force=' + $Force + ' launcher=' + ($Xml -like '*NGRunnerLauncher.exe*')) }
function New-ItemProperty { [CmdletBinding()] param([string]$Path, [string]$Name, [string]$Value, [string]$PropertyType, [switch]$Force)
    $global:asked.Add('set ' + $Path + ' ' + $Name + ' ' + $PropertyType + ' ' + $Value) }
function Get-ScheduledTask { [CmdletBinding()] param([string]$TaskName) 'a task' }
function Unregister-ScheduledTask { [CmdletBinding()] param([string]$TaskName, [switch]$Confirm)
    $global:asked.Add('unregister ' + $TaskName + ' confirm=' + $Confirm) }
function Get-ItemProperty { [CmdletBinding()] param([string]$Path, [string]$Name) 'a value' }
function Remove-ItemProperty { [CmdletBinding()] param([string]$Path, [string]$Name)
    $global:asked.Add('remove ' + $Path + ' ' + $Name) }
foreach ($name in 'Register-ScheduledTask', 'New-ItemProperty', 'Get-ScheduledTask', 'Unregister-ScheduledTask',
                  'Get-ItemProperty', 'Remove-ItemProperty') {
    if ((Get-Command $name).CommandType -ne 'Function') { throw "$name is not stood in for; stopping before Windows is touched" }
}
"""


def test_registering_asks_windows_for_exactly_the_two_tasks_the_run_key_and_the_start_menu_item(
        clean_install, tmp_path, before):
    # Windows' own cmdlets are replaced by stand-ins that only record what they are asked, so
    # this PC is never changed; each stand-in is checked to be in place before anything runs.
    root = clean_install.root
    start_menu = tmp_path / "Start Menu"
    start_menu.mkdir()
    (start_menu / "NG Runner.lnk").write_bytes(b"made by install-shortcut")
    script = (f". {_quoted(str(INSTALLER / 'common.ps1'))}\n{_WINDOWS_STAND_INS}\n"
              f"$missing = Register-NGRunner {_quoted(str(root))} {_quoted(str(root / 'app-0.0.1' / 'NGRunner.exe'))}\n"
              f"Unregister-NGRunner {_quoted(str(start_menu))}\n"
              "$global:asked | ForEach-Object { 'ASKED ' + $_ }\n'MISSING=' + $missing\n")
    done = powershell("-Command", script, env=fake_env(tmp_path / "calls.txt"))
    assert done.returncode == 0, done.stdout + done.stderr
    run_key = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    launcher_exe = root / "launcher" / "NGRunnerLauncher.exe"
    assert [line[6:] for line in done.stdout.splitlines() if line.startswith("ASKED ")] == [
        "register NGRunner-logon force=True launcher=True",
        "register NGRunner-ensure force=True launcher=True",
        f'set {run_key} NGRunner String "{launcher_exe}" --logon',
        "unregister NGRunner-logon confirm=False",
        "unregister NGRunner-ensure confirm=False",
        f"remove {run_key} NGRunner",
    ]
    assert "MISSING=\n" in done.stdout or done.stdout.rstrip().endswith("MISSING=")
    assert (tmp_path / "calls.txt").read_text(encoding="utf-8").splitlines() == [
        f"NGRunner.exe install-shortcut --target {launcher_exe}"]
    assert not (start_menu / "NG Runner.lnk").exists()
    assert registered() == before


# ----- what is refused


def test_a_release_that_does_not_match_its_manifest_is_never_installed(tmp_path, fake_exe):
    releases = {case: make_release(tmp_path / case, fake_exe) for case in ("changed", "extra", "missing", "none")}
    dll = releases["changed"] / "app" / "_internal" / "python314.dll"
    dll.write_bytes(b"x" + dll.read_bytes()[1:])
    (releases["extra"] / "launcher" / "_internal" / "unexpected.dll").write_bytes(b"?")
    (releases["missing"] / "app" / "_internal" / "python314.dll").unlink()
    (releases["none"] / "manifest.json").unlink()
    roots = {case: tmp_path / case / "root" for case in releases}
    ran = run_scripts(*(install_call(roots[case], releases[case]) for case in releases),
                      env=fake_env(tmp_path / "calls.txt"))
    expected = {"changed": "app/_internal/python314.dll has changed",
                "extra": "launcher/_internal/unexpected.dll is not in the manifest",
                "missing": "app/_internal/python314.dll is missing",
                "none": "has no manifest.json"}
    for case, result in zip(releases, ran):
        assert result.code == 1 and expected[case] in result.out, (case, result.out)
        assert not roots[case].exists()
    assert not (tmp_path / "calls.txt").exists()                   # nothing was even self-tested


def test_the_manifest_itself_can_be_pinned_by_its_hash(root, tmp_path, fake_exe):
    release = make_release(tmp_path, fake_exe)
    wrong, right = run_scripts(
        install_call(root, release, "-ManifestSha256", "0" * 64),
        install_call(root, release, "-ManifestSha256", manifest.sha256(release / "manifest.json").upper()),
        env=fake_env(tmp_path / "calls.txt"))
    assert wrong.code == 1 and "manifest.json is not the one expected" in wrong.out
    assert right.code == 0, right.out


def test_a_version_whose_self_test_fails_is_removed_again(root, tmp_path, fake_exe):
    [ran] = run_scripts(install_call(root, make_release(tmp_path, fake_exe)),
                        env=fake_env(tmp_path / "calls.txt", SELF_TEST="1"))
    assert ran.code == 1 and "0.0.1 failed its self-test" in ran.out
    assert not (root / "app-0.0.1").exists()
    record = json.loads((root / "ngrunner-install.json").read_text(encoding="utf-8-sig"))
    assert record["versions"] == []


def test_the_same_release_again_changes_nothing_and_another_build_of_that_version_is_refused(
        root, tmp_path, fake_exe):
    first = make_release(tmp_path / "a", fake_exe)
    rebuilt = make_release(tmp_path / "b", fake_exe, payload=b"rebuilt")
    installed, again, other = run_scripts(install_call(root, first), install_call(root, first),
                                          install_call(root, rebuilt), env=fake_env(tmp_path / "calls.txt"))
    assert installed.code == 0 and again.code == 0 and "0.0.1 is already installed" in again.out
    assert other.code == 1 and "give the new build a new version" in other.out
    assert (root / "app-0.0.1" / "_internal" / "python314.dll").read_bytes() == b"runtime0.0.1"


def test_a_new_version_goes_in_beside_the_old_one(root, tmp_path, fake_exe):
    ran = run_scripts(install_call(root, make_release(tmp_path / "a", fake_exe, "0.0.1")),
                      install_call(root, make_release(tmp_path / "b", fake_exe, "0.0.2")),
                      env=fake_env(tmp_path / "calls.txt"))
    assert [r.code for r in ran] == [0, 0], ran
    assert (root / "app-0.0.1" / "NGRunner.exe").is_file() and (root / "app-0.0.2" / "NGRunner.exe").is_file()
    record = json.loads((root / "ngrunner-install.json").read_text(encoding="utf-8-sig"))
    assert record["app"] == "NGRunner" and record["versions"] == ["0.0.1", "0.0.2"]


def test_a_folder_that_holds_other_things_is_never_installed_into(root, tmp_path, fake_exe):
    root.mkdir(parents=True)
    (root / "someone else's.txt").write_bytes(b"keep")
    [ran] = run_scripts(install_call(root, make_release(tmp_path, fake_exe)), env=fake_env(tmp_path / "calls.txt"))
    assert ran.code == 1 and "is not an NG Runner install" in ran.out
    assert [p.name for p in root.iterdir()] == ["someone else's.txt"]


def test_a_root_that_is_not_a_full_folder_path_is_refused(tmp_path, fake_exe):
    release = make_release(tmp_path, fake_exe)
    bad = ["Programs\\NGRunner", "C:\\", "\\\\?\\C:\\NGRunner", "C:\\NGRunner\\..\\Windows"]
    ran = run_scripts(*(install_call(Path(r), release) for r in bad), env=fake_env(tmp_path / "calls.txt"))
    for result in ran:
        assert result.code == 2 and "-Root must be the full path of a folder" in result.out, result.out


# ----- uninstalling


def test_uninstall_removes_what_the_installer_made_and_nothing_else(root, tmp_path, fake_exe, before):
    neighbour = root.parent / "NGRunner data stays"
    neighbour.mkdir(parents=True)
    installed_copy = str(root / "installer" / "uninstall.ps1")       # as the runbook runs it: it
    installed, removed = run_scripts(                                 # removes its own folder too
        install_call(root, make_release(tmp_path, fake_exe)), (installed_copy, ["-Root", str(root), "-NoRegister"]),
        env=fake_env(tmp_path / "calls.txt"))
    assert installed.code == 0 and removed.code == 0, removed.out
    assert not root.exists() and neighbour.is_dir()
    assert "NG Runner's data folder was kept" in removed.out
    assert registered() == before


def test_uninstall_refuses_a_folder_the_installer_did_not_make(root):
    (root / "app-0.0.1").mkdir(parents=True)
    (root / "app-0.0.1" / "keep.txt").write_bytes(b"keep")
    [ran] = run_scripts(uninstall_call(root))
    assert ran.code == 1 and "is not an NG Runner install" in ran.out
    assert (root / "app-0.0.1" / "keep.txt").is_file()


def test_uninstall_never_follows_a_junction_out_of_the_install(root, tmp_path, fake_exe):
    outside = tmp_path / "Mozaik Shared"
    outside.mkdir()
    (outside / "Room1.des").write_bytes(b"Jim's design")
    [installed] = run_scripts(install_call(root, make_release(tmp_path, fake_exe)), env=fake_env(tmp_path / "calls.txt"))
    assert installed.code == 0
    _winapi.CreateJunction(str(outside), str(root / "app-0.0.1" / "linked"))
    [removed] = run_scripts(uninstall_call(root))
    assert removed.code == 1 and "link or junction" in removed.out
    assert (outside / "Room1.des").read_bytes() == b"Jim's design"
    assert (root / "launcher" / "NGRunnerLauncher.exe").is_file()          # nothing at all was removed


def test_one_version_can_be_removed_by_hand_but_never_the_only_one(root, tmp_path, fake_exe):
    ran = run_scripts(install_call(root, make_release(tmp_path / "a", fake_exe, "0.0.1")),
                      install_call(root, make_release(tmp_path / "b", fake_exe, "0.0.2")),
                      uninstall_call(root, "-Version", "0.0.2"),
                      uninstall_call(root, "-Version", "0.0.1"),
                      env=fake_env(tmp_path / "calls.txt"))
    assert [r.code for r in ran] == [0, 0, 0, 1], ran
    assert "only version installed" in ran[3].out
    assert sorted(p.name for p in root.iterdir() if p.name.startswith("app-")) == ["app-0.0.1"]
    assert (root / "launcher" / "NGRunnerLauncher.exe").is_file()
    record = json.loads((root / "ngrunner-install.json").read_text(encoding="utf-8-sig"))
    assert record["versions"] == ["0.0.1"]


# ----- the release manifest


def test_the_manifest_lists_every_file_and_catches_any_change(tmp_path, fake_exe):
    release = make_release(tmp_path, fake_exe)
    assert manifest.verify(release) == []
    listed = json.loads((release / "manifest.json").read_text(encoding="utf-8"))["files"]
    assert "app/NGRunner.exe" in listed and "installer/tasks/NGRunner-ensure.xml" in listed
    (release / "installer" / "install.ps1").write_bytes(b"# changed\n")
    assert manifest.verify(release) == ["installer/install.ps1 has changed"]


def test_a_version_the_launcher_could_not_read_is_never_written_into_a_manifest(tmp_path, fake_exe):
    with pytest.raises(ValueError, match="not a version"):
        make_release(tmp_path, fake_exe, version="0.1.0rc1")


# ----- the task XML templates and the PyInstaller spec, as written in the repo


def test_the_task_templates_are_task_scheduler_1_2_xml_with_the_designs_settings():
    for name in ("NGRunner-logon.xml", "NGRunner-ensure.xml"):
        task = ET.parse(INSTALLER / "tasks" / name).getroot()
        assert task.tag == "{http://schemas.microsoft.com/windows/2004/02/mit/task}Task"
        assert task.get("version") == "1.2"
        assert task.findtext("t:Settings/t:Priority", namespaces=TASK_NS) == "4"
        assert task.findtext("t:Settings/t:IdleSettings/t:StopOnIdleEnd", namespaces=TASK_NS) == "false"


def test_the_spec_builds_folders_without_upx_in_utf_8_mode():
    tree = ast.parse((ROOT / "ngrunner.spec").read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    exes = [c for c in calls if c.func.id == "EXE"]
    collects = [c for c in calls if c.func.id == "COLLECT"]
    assert len(exes) == 2 and len(collects) == 2                         # the app and the launcher

    def keyword(call: ast.Call, name: str) -> object:
        return next(ast.literal_eval(k.value) for k in call.keywords if k.arg == name)

    assert all(keyword(c, "upx") is False for c in exes + collects)
    assert all(keyword(c, "exclude_binaries") is True for c in exes)     # a folder build, not one file
    assert sorted(keyword(c, "name") for c in exes) == ["NGRunner", "NGRunnerLauncher"]
    assert sorted(keyword(c, "console") for c in exes) == [False, True]
    assert "('X utf8', None, 'OPTION')" in ast.unparse(tree).replace('"', "'")
