"""The command line (build spec 6.7; rulings 20 and 20a): every subcommand, its exit code, and what
it prints. Nothing here starts a real supervisor or touches the real Start Menu; the shortcut goes
in tmp_path, and the settings and keys in a test data folder and MemorySecrets."""
from __future__ import annotations

import io
import json
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from ngrunner import __main__ as cli
from ngrunner import canary
from ngrunner import launcher
from ngrunner.config import AppPaths, load_settings, save_settings, settings_from
from ngrunner.secrets import MemorySecrets, SecretsError

SIGNING = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
EXPORT = "an-export-key-of-any-shape"
PUBLIC = "ngrbak-public-1-" + "ab" * 32


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    data = tmp_path / "data"
    data.mkdir()
    return AppPaths(data)


def _main(paths: AppPaths, *argv: str, **kwargs) -> tuple[int, str]:
    out = io.StringIO()
    code = cli.main(list(argv), out=out, environ={"NGR_APP_DATA": str(paths.app_data)}, **kwargs)
    return code, out.getvalue()


# ----- run


def test_run_calls_the_supervisor_with_the_launchers_arguments(paths: AppPaths) -> None:
    calls = []

    def supervise(given, *, attempt=None, open_board=False):
        calls.append((given, attempt, open_board))
        return 7

    code, _ = _main(paths, "run", "--attempt", "a" * 32, "--open-board", supervise=supervise)
    assert code == 7
    assert calls == [(paths, "a" * 32, True)]


def test_no_command_means_run(paths: AppPaths) -> None:
    calls = []
    code, _ = _main(paths, supervise=lambda p, **kw: calls.append(kw) or 0)
    assert code == 0
    assert calls == [{"attempt": None, "open_board": False}]


def test_a_supervisor_that_cannot_load_is_a_plain_failed_start(
        paths: AppPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "ngrunner.supervisor", None)  # the import fails
    code, text = _main(paths, "run")
    assert code == 1 and "could not load" in text


# ----- --version, --self-test, keygen: still as they were


def test_version(paths: AppPaths) -> None:
    code, text = _main(paths, "--version")
    assert code == 0 and text == f"NG Runner {launcher.app_version()}\n"


def test_self_test_passes_and_fails_by_what_it_finds(paths: AppPaths) -> None:
    assert _main(paths, "--self-test", self_test=lambda p: [])[0] == 0
    code, text = _main(paths, "--self-test", self_test=lambda p: ["A problem."])
    assert code == 1 and "1. A problem." in text


def test_keygen_needs_no_data_folder() -> None:
    out = io.StringIO()
    assert cli.main(["keygen"], out=out, environ={}) == 0
    assert "ngrbak-public-1-" in out.getvalue()


def test_a_wrong_command_line_exits_2(paths: AppPaths) -> None:
    assert _main(paths, "no-such-command")[0] == 2
    assert _main(paths, "canary")[0] == 2  # --folder is required
    assert _main(paths, "install-shortcut")[0] == 2


def test_no_data_folder_exits_2() -> None:
    out = io.StringIO()
    assert cli.main(["run"], out=out, environ={}) == 2


# ----- release-all


def test_release_all_exits_0_and_says_there_are_no_leases(paths: AppPaths) -> None:
    code, text = _main(paths, "release-all")
    assert code == 0 and "no leases" in text


def test_release_all_works_with_no_data_folder() -> None:
    assert cli.main(["release-all"], out=io.StringIO(), environ={}) == 0


# ----- install-shortcut


def _aumid_of(link: Path) -> tuple[str, str]:
    import pythoncom
    from win32com.propsys import propsys, pscon
    from win32com.shell import shell

    shortcut = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None,
                                          pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
    shortcut.QueryInterface(pythoncom.IID_IPersistFile).Load(str(link))
    store = shortcut.QueryInterface(propsys.IID_IPropertyStore)
    target = shortcut.GetPath(shell.SLGP_RAWPATH)[0]
    return store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue(), target


def test_install_shortcut_makes_the_item_with_the_aumid(paths: AppPaths, tmp_path: Path) -> None:
    exe = tmp_path / "launcher" / "NGRunnerLauncher.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"not really a program")
    menu = tmp_path / "Start Menu"
    code, text = _main(paths, "install-shortcut", "--target", str(exe), start_menu=menu)
    assert code == 0, text
    assert (menu / "NG Runner.lnk").is_file()
    aumid, target = _aumid_of(menu / "NG Runner.lnk")
    assert aumid == "ICLA.NGRunner"
    assert Path(target) == exe


def test_the_real_start_menu_folder_is_only_read_here() -> None:
    assert cli._programs_folder().name == "Programs"


def test_install_shortcut_replaces_an_old_item(paths: AppPaths, tmp_path: Path) -> None:
    exe = tmp_path / "NGRunnerLauncher.exe"
    exe.write_bytes(b"x")
    menu = tmp_path / "Start Menu"
    assert _main(paths, "install-shortcut", "--target", str(exe), start_menu=menu)[0] == 0
    assert _main(paths, "install-shortcut", "--target", str(exe), start_menu=menu)[0] == 0


def test_install_shortcut_refuses_a_target_that_is_not_there(
        paths: AppPaths, tmp_path: Path) -> None:
    menu = tmp_path / "Start Menu"
    code, text = _main(paths, "install-shortcut", "--target", str(tmp_path / "no.exe"),
                       start_menu=menu)
    assert code == 1 and "was not made" in text
    assert not menu.exists()


def test_install_shortcut_says_when_windows_refuses(
        paths: AppPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = tmp_path / "NGRunnerLauncher.exe"
    exe.write_bytes(b"x")

    def refuses(*args) -> None:
        raise OSError("no")

    monkeypatch.setattr(cli, "_make_shortcut", refuses)
    code, text = _main(paths, "install-shortcut", "--target", str(exe), start_menu=tmp_path)
    assert code == 1 and "could not be made" in text


# ----- canary


@pytest.fixture
def no_waits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(canary, "WAITS", ())
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))


def test_canary_runs_on_a_folder_and_exits_0(paths: AppPaths, tmp_path: Path, no_waits) -> None:
    folder = tmp_path / "A test folder"
    folder.mkdir()
    code, text = _main(paths, "canary", "--folder", str(folder))
    assert code == 0, text
    assert "PASS  A forced failure rolls the whole commit back" in text
    assert list(folder.iterdir()) == []


def test_canary_refuses_ng_jobs_before_settings_exist(
        paths: AppPaths, tmp_path: Path, no_waits) -> None:
    folder = tmp_path / "NG Jobs"
    folder.mkdir()
    code, text = _main(paths, "canary", "--folder", str(folder))
    assert code == 2 and "never runs there" in text
    assert list(folder.iterdir()) == []


def test_canary_refuses_the_folders_the_settings_name(
        paths: AppPaths, tmp_path: Path, no_waits) -> None:
    filed, mozaik, control = tmp_path / "Filed work", tmp_path / "Shop files", tmp_path / "control"
    for folder in (filed, mozaik, control):
        folder.mkdir()
    save_settings(paths, settings_from({"ng_jobs": str(filed), "mozaik_shared": str(mozaik),
                                        "control": str(control)}))
    assert _main(paths, "canary", "--folder", str(filed))[0] == 2
    assert _main(paths, "canary", "--folder", str(mozaik))[0] == 2
    assert _main(paths, "canary", "--folder", str(control))[0] == 0


def test_canary_needs_no_data_folder(tmp_path: Path, no_waits) -> None:
    folder = tmp_path / "A test folder"
    folder.mkdir()
    out = io.StringIO()
    assert cli.main(["canary", "--folder", str(folder)], out=out, environ={}) == 0, out.getvalue()


# ----- setup


class Answers:
    """Plays the person at the console: prompts are kept, and answers come in order."""

    def __init__(self, *lines: str, secrets: tuple[str, ...] = ()) -> None:
        self._lines, self._secrets = list(lines), list(secrets)
        self.prompts: list[str] = []

    def line(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self._lines:
            raise EOFError
        return self._lines.pop(0)

    def secret(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self._secrets:
            raise EOFError
        return self._secrets.pop(0)


@pytest.fixture
def folders(tmp_path: Path) -> dict[str, Path]:
    made = {"ng_jobs": tmp_path / "NG Jobs", "mozaik_shared": tmp_path / "Mozaik Shared",
            "control": tmp_path / "Control"}
    for folder in made.values():
        folder.mkdir()
    return made


def _setup(paths: AppPaths, answers: Answers, secrets: MemorySecrets) -> tuple[int, str]:
    return _main(paths, "setup", read_line=answers.line, read_secret=answers.secret,
                 secrets=secrets)


def _first_run(folders: dict[str, Path], *, public: str = PUBLIC,
               login: tuple[str, ...] = ("",)) -> Answers:
    """Enter at Studio's username leaves the login for later, unless a test types one."""
    return Answers(str(folders["ng_jobs"]), str(folders["mozaik_shared"]), str(folders["control"]),
                   "y", "", "", secrets=(public, SIGNING, EXPORT, *login))


def test_setup_writes_the_settings_and_puts_the_keys_in_credential_manager(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    secrets = MemorySecrets()
    code, text = _setup(paths, _first_run(folders), secrets)
    assert code == 0, text
    settings = load_settings(paths)
    assert settings.ng_jobs == folders["ng_jobs"] and settings.control == folders["control"]
    assert settings.mozaik_shared == folders["mozaik_shared"]
    assert settings.mode == "shadow" and not settings.pc_sleeps_at_night
    assert settings.backup_public_key == PUBLIC
    assert secrets.get("signing_key") == SIGNING and secrets.get("export_key") == EXPORT
    assert "Start Menu" in text


def test_setup_shows_no_secret_and_writes_none_in_the_settings_file(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    code, text = _setup(paths, _first_run(folders), MemorySecrets())
    assert code == 0
    stored = paths.settings.read_text(encoding="utf-8")
    for secret in (SIGNING, EXPORT):
        assert secret not in text and secret not in stored


def test_setup_asks_for_the_keys_hidden(paths: AppPaths, folders: dict[str, Path]) -> None:
    answers = _first_run(folders)
    _setup(paths, answers, MemorySecrets())
    hidden = answers.prompts[-4:]
    assert "backup public key" in hidden[0] and "signing key" in hidden[1]
    assert "export key" in hidden[2] and "Studio's login" in hidden[3]


def test_setup_says_when_a_pc_sleeps_at_night_and_takes_the_hours(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    answers = Answers(str(folders["ng_jobs"]), str(folders["mozaik_shared"]),
                      str(folders["control"]), "n", "07:00", "21:30",
                      secrets=("", SIGNING, EXPORT, ""))
    assert _setup(paths, answers, MemorySecrets())[0] == 0
    settings = load_settings(paths)
    assert settings.pc_sleeps_at_night
    assert (settings.expected_on_from, settings.expected_on_until) == ("07:00", "21:30")
    assert settings.backup_public_key == ""


def test_setup_asks_again_for_a_folder_that_is_not_there_without_repeating_it(
        paths: AppPaths, folders: dict[str, Path], tmp_path: Path) -> None:
    pasted = str(tmp_path / SIGNING)
    answers = Answers(pasted, "relative\\path", str(folders["ng_jobs"]),
                      str(folders["mozaik_shared"]), str(folders["control"]), "", "", "",
                      secrets=("", SIGNING, EXPORT, ""))
    code, text = _setup(paths, answers, MemorySecrets())
    assert code == 0, text
    assert "There is no folder there" in text and "full path" in text
    assert SIGNING not in text


def test_setup_asks_about_a_folder_not_called_ng_jobs(
        paths: AppPaths, folders: dict[str, Path], tmp_path: Path) -> None:
    other = tmp_path / "Somewhere else"
    other.mkdir()
    answers = Answers(str(other), "n", str(other), "y", str(folders["mozaik_shared"]),
                      str(folders["control"]), "", "", "", secrets=("", SIGNING, EXPORT, ""))
    assert _setup(paths, answers, MemorySecrets())[0] == 0
    assert load_settings(paths).ng_jobs == other


def test_setup_refuses_folders_that_overlap_and_saves_nothing(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    inside = folders["ng_jobs"] / "Control"
    inside.mkdir()
    secrets = MemorySecrets()
    answers = Answers(str(folders["ng_jobs"]), str(folders["mozaik_shared"]), str(inside), "", "",
                      "", secrets=("", SIGNING, EXPORT, ""))
    code, text = _setup(paths, answers, secrets)
    assert code == 1 and "overlap" in text and "Nothing was saved" in text
    assert not paths.settings.exists() and secrets.get("signing_key") is None


def test_setup_asks_again_for_a_bad_signing_key_and_never_repeats_it(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    secrets = MemorySecrets()
    answers = Answers(str(folders["ng_jobs"]), str(folders["mozaik_shared"]),
                      str(folders["control"]), "", "", "",
                      secrets=("", "too short", "", SIGNING, "", EXPORT, ""))
    code, text = _setup(paths, answers, secrets)
    assert code == 0, text
    assert "too short" not in text and "isn't the signing key" in text
    assert "can't work without this key" in text
    assert secrets.get("signing_key") == SIGNING


def test_setup_asks_again_for_a_bad_time_and_a_bad_backup_key(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    private = "ngrbak-private-1-" + "cd" * 32
    answers = Answers(str(folders["ng_jobs"]), str(folders["mozaik_shared"]),
                      str(folders["control"]), "maybe", "y", "seven", "07:00", "",
                      secrets=(private, PUBLIC, SIGNING, EXPORT, ""))
    code, text = _setup(paths, answers, MemorySecrets())
    assert code == 0, text
    assert "answer y or n" in text and "a time like 06:30" in text
    assert private not in text
    assert load_settings(paths).expected_on_from == "07:00"


def test_setting_up_again_keeps_what_enter_keeps(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    secrets = MemorySecrets()
    assert _setup(paths, _first_run(folders), secrets)[0] == 0
    raw = json.loads(paths.settings.read_text(encoding="utf-8"))
    raw["a_setting_from_a_newer_version"] = "kept"
    paths.settings.write_text(json.dumps(raw), encoding="utf-8")
    answers = Answers("", "", "", "", "", "", secrets=("", "", "", ""))
    code, text = _setup(paths, answers, secrets)
    assert code == 0, text
    settings = load_settings(paths)
    assert settings.ng_jobs == folders["ng_jobs"] and settings.backup_public_key == PUBLIC
    assert secrets.get("signing_key") == SIGNING and secrets.get("export_key") == EXPORT
    kept = json.loads(paths.settings.read_text(encoding="utf-8"))
    assert kept["a_setting_from_a_newer_version"] == "kept"  # for the version that knows it


def test_setup_keeps_a_mode_already_switched_to_live(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    secrets = MemorySecrets()
    assert _setup(paths, _first_run(folders), secrets)[0] == 0
    raw = json.loads(paths.settings.read_text(encoding="utf-8"))
    raw["mode"] = "live"
    paths.settings.write_text(json.dumps(raw), encoding="utf-8")
    answers = Answers("", "", "", "", "", "", secrets=("", "", "", ""))
    assert _setup(paths, answers, secrets)[0] == 0
    assert load_settings(paths).mode == "live"


def test_stopping_part_way_saves_nothing(paths: AppPaths, folders: dict[str, Path]) -> None:
    secrets = MemorySecrets()
    answers = Answers(str(folders["ng_jobs"]))  # then the console closes
    code, text = _setup(paths, answers, secrets)
    assert code == 1 and "Nothing was saved" in text
    assert not paths.settings.exists()
    assert secrets.get("signing_key") is None


# ----- setup: Studio's login, asked for and never shown


USERNAME, PASSWORD = "testerson@example.com", "a studio password"
LOGIN = (USERNAME, PASSWORD)
_SESSION = '{"sessionid": "a", "csrftoken": "b"}'


def _shows_neither(answers: Answers, text: str, paths: AppPaths) -> None:
    for shown in (text, paths.settings.read_text(encoding="utf-8"), *answers.prompts):
        assert USERNAME not in shown and PASSWORD not in shown


def _run_again(*login: str) -> Answers:
    return Answers("", "", "", "", "", "", secrets=("", "", "", *login))


def test_setup_saves_studios_login_and_shows_neither_part(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    secrets, answers = MemorySecrets(), _first_run(folders, login=LOGIN)
    code, text = _setup(paths, answers, secrets)
    assert code == 0, text
    assert secrets.get("studio_username") == USERNAME and secrets.get("studio_password") == PASSWORD
    _shows_neither(answers, text, paths)
    assert "No Studio login" not in text
    assert "username" in answers.prompts[-2] and "password" in answers.prompts[-1]
    assert all("it won't show" in prompt for prompt in answers.prompts[-2:])


def test_setup_asks_for_studios_login_hidden_never_on_the_open_line(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    open_lines: list[str] = []
    answers = _first_run(folders, login=LOGIN)

    def line(prompt: str) -> str:
        open_lines.append(prompt)
        return answers.line(prompt)

    code, _ = _main(paths, "setup", read_line=line, read_secret=answers.secret,
                    secrets=MemorySecrets())
    assert code == 0
    assert not [prompt for prompt in open_lines if "Studio" in prompt]


def test_enter_leaves_studios_login_for_later_and_setup_says_what_that_means(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    secrets, answers = MemorySecrets(), _first_run(folders)
    code, text = _setup(paths, answers, secrets)
    assert code == 0, text
    assert "for later" in answers.prompts[-1]
    assert not [prompt for prompt in answers.prompts if "password" in prompt]
    assert secrets.get("studio_username") is None and secrets.get("studio_password") is None
    assert "No Studio login yet" in text and "not cross-checked" in text


def test_setting_up_again_keeps_studios_login_with_enter_and_never_shows_it(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    secrets = MemorySecrets()
    assert _setup(paths, _first_run(folders, login=LOGIN), secrets)[0] == 0
    secrets.put("studio_session", _SESSION)
    answers = _run_again("", "")
    code, text = _setup(paths, answers, secrets)
    assert code == 0, text
    assert secrets.get("studio_username") == USERNAME and secrets.get("studio_password") == PASSWORD
    assert secrets.get("studio_session") == _SESSION    # nothing changed, so it stays good
    _shows_neither(answers, text, paths)
    assert all("keeps the one saved" in prompt for prompt in answers.prompts[-2:])


def test_a_new_password_replaces_the_old_and_drops_the_session_it_got(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    secrets = MemorySecrets()
    assert _setup(paths, _first_run(folders, login=LOGIN), secrets)[0] == 0
    secrets.put("studio_session", _SESSION)
    code, text = _setup(paths, _run_again("", "a newer password"), secrets)
    assert code == 0, text
    assert secrets.get("studio_username") == USERNAME
    assert secrets.get("studio_password") == "a newer password"
    assert secrets.get("studio_session") is None
    assert "a newer password" not in text


def test_a_new_username_alone_keeps_the_password_and_drops_the_session(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    secrets = MemorySecrets()
    assert _setup(paths, _first_run(folders, login=LOGIN), secrets)[0] == 0
    secrets.put("studio_session", _SESSION)
    code, _ = _setup(paths, _run_again("fakeley@example.com", ""), secrets)
    assert code == 0
    assert secrets.get("studio_username") == "fakeley@example.com"
    assert secrets.get("studio_password") == PASSWORD and secrets.get("studio_session") is None


def test_a_username_is_no_login_without_its_password_so_it_is_asked_again(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    secrets = MemorySecrets()
    code, text = _setup(paths, _first_run(folders, login=(USERNAME, "", " ", PASSWORD)), secrets)
    assert code == 0, text
    assert "needs its password too" in text and secrets.get("studio_password") == PASSWORD
    assert USERNAME not in text


def test_a_password_is_kept_exactly_as_typed(paths: AppPaths, folders: dict[str, Path]) -> None:
    secrets = MemorySecrets()
    answers = _first_run(folders, login=(f"  {USERNAME} ", "  spaces count "))
    assert _setup(paths, answers, secrets)[0] == 0
    assert secrets.get("studio_username") == USERNAME
    assert secrets.get("studio_password") == "  spaces count "


def test_stopping_at_studios_password_saves_nothing(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    secrets = MemorySecrets()
    code, text = _setup(paths, _first_run(folders, login=(USERNAME,)), secrets)
    assert code == 1 and "Nothing was saved" in text and USERNAME not in text
    assert not paths.settings.exists()
    assert secrets.get("signing_key") is None and secrets.get("studio_username") is None


class NoStudioSecrets(MemorySecrets):
    """Credential Manager refusing the login, its error naming the secret, as CredentialSecrets'
    does."""

    def put(self, name: str, value: str) -> None:
        if name.startswith("studio_"):
            raise SecretsError(f"Windows Credential Manager could not store the {name} secret")
        super().put(name, value)


def test_a_login_that_cannot_be_stored_leaves_no_settings_and_shows_neither_part(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    code, text = _setup(paths, _first_run(folders, login=LOGIN), NoStudioSecrets())
    assert code == 1 and "could not finish" in text
    assert USERNAME not in text and PASSWORD not in text
    assert not paths.settings.exists()


class BrokenSecrets(MemorySecrets):
    def put(self, name: str, value: str) -> None:
        raise SecretsError(f"Windows Credential Manager could not store the {name} secret")


def test_a_key_that_cannot_be_stored_leaves_no_settings_behind(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    code, text = _setup(paths, _first_run(folders), BrokenSecrets())
    assert code == 1 and "could not finish" in text
    assert SIGNING not in text and not paths.settings.exists()


def test_settings_that_cannot_be_read_are_not_a_dead_end(
        paths: AppPaths, folders: dict[str, Path]) -> None:
    paths.settings.write_text("not json", encoding="utf-8")
    code, text = _setup(paths, _first_run(folders), MemorySecrets())
    assert code == 0 and "starts afresh" in text


# ----- dev-close


def test_dev_close_lists_what_is_open_and_closes_one_by_its_number(paths: AppPaths) -> None:
    from datetime import UTC, datetime

    from ngrunner.store import list_dev, list_events, open_dev, open_store

    store = open_store(paths.db)
    now = datetime(2026, 10, 30, 12, tzinfo=UTC)
    first = open_dev(store, at=now, code="too_big", text="A report was too big.")
    second = open_dev(store, at=now, code="layouts_differ", text="They differ.")
    code, text = _main(paths, "dev-close")
    assert code == 0 and "too_big" in text and "A report was too big." in text
    assert f"{first.id} " in text and f"{second.id} " in text
    code, text = _main(paths, "dev-close", str(first.id))
    assert code == 0 and "closed" in text.casefold()
    assert [item.id for item in list_dev(store)] == [second.id]
    assert [e.lane for e in list_events(store) if e.kind == "dev_closed"] == ["DEV"]
    code, text = _main(paths, "dev-close", str(first.id))
    assert code == 0 and "already" in text
    assert _main(paths, "dev-close", str(second.id))[0] == 0
    assert "No DEV items" in _main(paths, "dev-close")[1]


def test_dev_close_says_when_there_is_no_such_item_or_no_database(paths: AppPaths) -> None:
    from ngrunner.store import open_store

    code, text = _main(paths, "dev-close", "7")
    assert code == 1 and "database" in text and not paths.db.exists()
    open_store(paths.db)
    code, text = _main(paths, "dev-close", "7")
    assert code == 1 and "no DEV item 7" in text


def test_dev_close_wants_a_number(paths: AppPaths) -> None:
    assert _main(paths, "dev-close", "seven")[0] == 2


# ----- setup and the tracker's folder


def test_setup_finds_the_trackers_folder_beside_ng_jobs(paths: AppPaths, folders: dict[str, Path]) -> None:
    tracker = folders["ng_jobs"].parent / "NG Conversion program Claude"
    tracker.mkdir()
    code, text = _setup(paths, _first_run(folders), MemorySecrets())
    assert code == 0 and f"Tracker snapshots: {tracker}" in text
    assert load_settings(paths).tracker_folder == tracker


def test_setup_with_no_trackers_folder_leaves_it_unset(paths: AppPaths, folders: dict[str, Path]) -> None:
    code, text = _setup(paths, _first_run(folders), MemorySecrets())
    assert code == 0 and "Tracker snapshots" not in text
    assert load_settings(paths).tracker_folder is None


def test_setup_keeps_a_trackers_folder_already_set(paths: AppPaths, folders: dict[str, Path],
                                                   tmp_path: Path) -> None:
    secrets = MemorySecrets()
    _setup(paths, _first_run(folders), secrets)
    kept = replace(load_settings(paths), tracker_folder=tmp_path / "elsewhere")
    save_settings(paths, kept)
    answers = Answers("", "", "", "", "", "", secrets=("", "", "", ""))
    assert _setup(paths, answers, secrets)[0] == 0
    assert load_settings(paths).tracker_folder == tmp_path / "elsewhere"
