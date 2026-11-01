r"""
NG Runner's command line: NGRunner.exe in a build, python -m ngrunner from the repo.

Why it is this thin: every command is a call into a module with its own tests; this file only
reads the arguments, prints the outcome in plain words and returns the exit code the
installer and the launcher rely on:

    0  done (for --self-test and canary: every check passed)
    1  it ran and found a problem; the printed lines say what
    2  the command line was wrong, or NG Runner has no data folder; canary: a folder it refuses

Commands:
    --version                          the build's version
    --self-test [--record]             the post-install self-test; --record (the installer)
                                       lets the launcher start this version
    run [--attempt ID] [--open-board]  NG Runner itself, the default (the launcher starts it)
    install-shortcut --target EXE      the Start Menu item, carrying NG Runner's AUMID
    release-all                        hand every job back to Jim's chat (uninstall): every lease
                                       NG Runner holds is released (lock.leases.release_all)
    canary --folder PATH               prove a drive does what commit_write relies on
    setup                              settings.json, the two keys and Studio's login, asked for
                                       in plain words
    restore BACKUP [--install]         the restore drill; --install then puts the database in
                                       place on a new PC
    support-bundle                     a support bundle in the data folder, for Sulaiman
    keygen                             a new backup key pair
    dev-close [ID]                     the open DEV items, or close the one with this number, for
                                       Sulaiman: the runbook's way to close an item nothing else
                                       closes
    run-tool SCRIPT [ARGS...]          run a toolkit script in this process, as python SCRIPT ARGS
                                       would: the build has no Python (toolkit.py starts this, in
                                       its Job Object). Its exit code is the script's own, and it
                                       prints nothing of its own, so all of the output is the script's
    SCRIPT.py [ARGS...]                the same as run-tool (ruling R7): crosscheck.py starts each
                                       checker as sys.executable <checker>.py, and in a build that is
                                       NGRunner.exe
    run-call SCRIPT FUNCTION ROOM OUT  one room function of a toolkit script, given ROOM's text, its
                                       text written to OUT in binary (toolkit.run_room_call: a fixer
                                       of Jim's with no command line, as wm_eb.enforce_room)

Modules are imported inside each command, so a module that fails to load breaks only the
commands that need it, and --self-test can still name it.
"""
from __future__ import annotations

import argparse
import getpass
import itertools
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, TextIO

from ngrunner.clock import Clock, SystemClock, parse_wall
from ngrunner.config import (
    AppPaths, Settings, SettingsError, app_paths, load_settings, save_settings, settings_from,
    settings_to_json,
)

if TYPE_CHECKING:
    from ngrunner.secrets import Secrets

SHORTCUT = "NG Runner.lnk"


class _UsageError(Exception):
    pass


TRACKER_FOLDER_NAME = "NG Conversion program Claude"     # beside NG Jobs (P1b build spec 3.5)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError(f"{self.prog}: {message}")


def main(
    argv: list[str] | None = None,
    *,
    out: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
    self_test: Callable[[AppPaths], list[str]] | None = None,
    clock: Clock | None = None,
    read_secret: Callable[[str], str] = getpass.getpass,
    running: Callable[[], bool] | None = None,
    supervise: Callable[..., int] | None = None,
    read_line: Callable[[str], str] = input,
    secrets: Secrets | None = None,
    start_menu: Path | None = None,
) -> int:
    say = _Say(out if out is not None else sys.stdout)
    environ = os.environ if environ is None else environ
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0].casefold().endswith(".py"):     # ruling R7: python <script>.py, in a build
        return _run_tool(argv[0], list(argv[1:]))
    try:
        args = _parser().parse_args(argv)
        _check(args)
    except _UsageError as exc:
        say(str(exc))
        return 2
    except SystemExit as exc:  # --help has been printed
        return int(exc.code or 0)
    clock = clock or SystemClock()
    if args.version:
        from ngrunner.launcher import app_version

        say(f"NG Runner {app_version()}")
        return 0
    if args.command == "keygen":
        return _keygen(say)
    if args.command == "release-all":
        return _release_all(environ, clock, say)
    if args.command == "install-shortcut":
        return _install_shortcut(Path(args.target), start_menu, say)
    if args.command == "canary":
        return _canary(Path(args.folder), environ, say)
    if args.command == "run-tool":
        return _run_tool(args.script, args.args)
    if args.command == "run-call":
        return _run_call(args.script, args.function, args.room, args.out)
    try:
        paths = app_paths(environ)
    except SettingsError as exc:
        say(f"{exc}.")
        return 2
    if args.self_test:
        return _self_test(paths, args.record, self_test, clock, say)
    command = args.command or "run"
    if command == "run":
        return _run(paths, args, supervise, say)
    if command == "setup":
        return _setup(paths, read_line, read_secret, secrets, say)
    if command == "restore":
        return _restore(Path(args.backup), args.install, paths, read_secret, running, say)
    if command == "dev-close":
        return _dev_close(paths, args.id, clock, say)
    return _support_bundle(paths, clock, say)


def _parser() -> _Parser:
    parser = _Parser(prog="NGRunner", description="NG Runner (see ngrunner/__main__.py).")
    parser.add_argument("--version", action="store_true", help="print the build's version")
    parser.add_argument("--self-test", action="store_true", help="run the post-install self-test")
    parser.add_argument("--record", action="store_true",
                        help="with --self-test: record the result for the launcher")
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser("run", help="NG Runner itself (the default)")
    run.add_argument("--attempt", help="the launcher's id for this start")
    run.add_argument("--open-board", action="store_true", help="open the board once started")
    shortcut = commands.add_parser("install-shortcut", help="the Start Menu item with the AUMID")
    shortcut.add_argument("--target", required=True, help="the launcher exe it starts")
    commands.add_parser("release-all", help="release every lease to Jim's chat")
    canary = commands.add_parser("canary", help="prove a drive does what commit_write relies on")
    canary.add_argument("--folder", required=True,
                        help="a test folder of your own, as a full path; never in NG Jobs")
    commands.add_parser("setup", help="settings.json, the keys and Studio's login, asked plainly")
    restore = commands.add_parser("restore", help="the restore drill (and a restore onto a new PC)")
    restore.add_argument("backup", help="a <YYYYMMDD>.ngrbak file from the control folder")
    restore.add_argument("--install", action="store_true",
                         help="then put the database in place (only where NG Runner has none)")
    commands.add_parser("support-bundle", help="write a support bundle into the data folder")
    commands.add_parser("keygen", help="print a new backup key pair")
    dev_close = commands.add_parser("dev-close", help="list the open DEV items, or close one")
    dev_close.add_argument("id", nargs="?", type=int, help="the number of the item to close")
    run_tool = commands.add_parser("run-tool", help="run a toolkit script (NG Runner starts this)")
    run_tool.add_argument("script", help="the script, as a full path")
    run_tool.add_argument("args", nargs=argparse.REMAINDER, help="what the script is given")
    run_call = commands.add_parser("run-call", help="run a toolkit room function (NG Runner starts this)")
    for name, said in (("script", "the script, as a full path"), ("function", "the function it holds"),
                       ("room", "the room it is given"), ("out", "where its text is written")):
        run_call.add_argument(name, help=said)
    return parser


def _check(args: argparse.Namespace) -> None:
    if args.record and not args.self_test:
        raise _UsageError("NGRunner: --record goes with --self-test")
    if (args.version or args.self_test) and args.command:
        raise _UsageError("NGRunner: --version and --self-test take no command")
    if args.version and args.self_test:
        raise _UsageError("NGRunner: give --version or --self-test, not both")


class _Say:
    """Prints a line; a build without a console (no stdout) prints nothing."""

    def __init__(self, out: TextIO | None) -> None:
        self._out = out

    def __call__(self, line: str) -> None:
        self.write(line + "\n")

    def write(self, text: str) -> None:
        if self._out is not None:
            self._out.write(text)
            self._out.flush()


# ----- commands


def _run(paths: AppPaths, args: argparse.Namespace, supervise: Callable[..., int] | None,
         say: _Say) -> int:
    """NG Runner itself. Exit 0 with the mutex held by another start means "already running" to
    the launcher (single.py); anything else that isn't a clean stop counts as a failed start."""
    if supervise is None:
        try:
            from ngrunner.supervisor import run as supervise
        except Exception as exc:  # noqa: BLE001 - a start that can't load is a failed start
            say(f"NG Runner could not load ({type(exc).__name__}: {exc}).")
            return 1
    return supervise(paths, attempt=getattr(args, "attempt", None),
                     open_board=getattr(args, "open_board", False))


def _install_shortcut(target: Path, start_menu: Path | None, say: _Say) -> int:
    """The Start Menu item: without the AUMID on it, Windows drops NG Runner's toasts. The
    installer reads only the exit code, and shows these lines."""
    if not (target.is_absolute() and target.is_file()):
        say(f"There is no program at {target}, so the Start Menu item was not made.")
        return 1
    try:
        from ngrunner.toasts import AUMID

        folder = _programs_folder() if start_menu is None else start_menu
        folder.mkdir(parents=True, exist_ok=True)
        _make_shortcut(folder / SHORTCUT, target, AUMID)
    except Exception as exc:  # noqa: BLE001 - COM raises its own errors; each is this one failure
        say(f"The Start Menu item could not be made ({type(exc).__name__}: {exc}).")
        return 1
    say(f"Start Menu item made: {folder / SHORTCUT}")
    return 0


def _programs_folder() -> Path:
    from win32com.shell import shell, shellcon

    return Path(shell.SHGetFolderPath(0, shellcon.CSIDL_PROGRAMS, None, 0))


def _make_shortcut(path: Path, target: Path, aumid: str) -> None:
    import pythoncom
    from win32com.propsys import propsys, pscon
    from win32com.shell import shell

    link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
                                      shell.IID_IShellLink)
    link.SetPath(str(target))
    link.SetWorkingDirectory(str(target.parent))
    link.SetDescription("NG Runner")
    properties = link.QueryInterface(propsys.IID_IPropertyStore)
    properties.SetValue(pscon.PKEY_AppUserModel_ID,
                        propsys.PROPVARIANTType(aumid, pythoncom.VT_LPWSTR))
    properties.Commit()
    link.QueryInterface(pythoncom.IID_IPersistFile).Save(str(path), True)


def _release_all(environ: Mapping[str, str], clock: Clock, say: _Say) -> int:
    """Uninstall's hand-back: every lease NG Runner holds, released through lock.leases.release_all. With
    no data folder, no database or no lease, there is nothing to release, which is no failure."""
    nothing = ("There is nothing to release: NG Runner holds no lease on any job, so Jim's chat is free to"
               " work them all.")
    lapse = "Leases end by themselves within 2 and a half hours; until then, chat leaves those jobs alone."
    try:
        paths = app_paths(environ)
    except SettingsError:
        say(nothing)
        return 0
    if not paths.db.is_file():
        say(nothing)
        return 0
    from ngrunner.config import roots, shadow_roots
    from ngrunner.lock import leases, work
    from ngrunner.store import StoreError, open_store
    from ngrunner.store.locks import list_leases

    try:
        store = open_store(paths.db)
        held = list_leases(store, state=leases.ACTIVE)
    except StoreError:
        say(f"NG Runner's database could not be read, so no lease was released. {lapse}")
        return 1
    if not held:
        say(nothing)
        return 0
    try:
        settings = load_settings(paths)
    except SettingsError as exc:
        say(f"{exc}, so no lease was released. {lapse}")
        return 1
    results = leases.release_all(store=store, roots=shadow_roots(paths), clock=clock, holder=work.pc_name(),
                                 interlock=work.release_interlock(store, roots(settings, paths), clock,
                                                                  settings.tracker_folder))
    failed = [result for result in results if not result.ok]
    done = len(results) - len(failed)
    say(f"Released {done} job{'' if done == 1 else 's'} to chat.")
    for result in failed:
        say(f"{result.folder} was not released: {result.reason} {lapse}")
    return 1 if failed else 0


def _canary(folder: Path, environ: Mapping[str, str], say: _Say) -> int:
    from ngrunner.canary import run_canary

    try:
        settings = load_settings(app_paths(environ))
        refuse = (settings.ng_jobs, settings.mozaik_shared)
    except SettingsError:  # it runs before settings exist, so it also goes by the folders' names
        refuse = ()
    return run_canary(folder, out=say, refuse=refuse)


def _run_tool(script: str, args: list[str]) -> int:
    """A toolkit script in this process, as Python itself would run it: it finds what lies beside it,
    and its exit code and output are what comes back (build spec 5.13)."""
    import runpy
    import traceback

    sys.argv = [script, *args]
    sys.path.insert(0, str(Path(script).parent))
    sys.dont_write_bytecode = True      # a build may read no PYTHONDONTWRITEBYTECODE: no .pyc in a copy
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as exc:
        if exc.code is None or isinstance(exc.code, int):
            return exc.code or 0
        print(exc.code, file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - a script that breaks fails its own run, as under Python
        traceback.print_exc()
        return 1
    return 0


def _run_call(script: str, function: str, room: str, out: str) -> int:
    """A toolkit room function in this process, as run_tool runs a script: what breaks is on stderr and
    exit code 1, and nothing is written."""
    import traceback

    from ngrunner.toolkit import call_room_function

    sys.dont_write_bytecode = True
    try:
        call_room_function(script, function, room, out)
    except SystemExit as exc:
        print(exc.code, file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - a function that breaks fails its own run, as under Python
        traceback.print_exc()
        return 1
    return 0


def _self_test(paths: AppPaths, record: bool, self_test: Callable[[AppPaths], list[str]] | None,
               clock: Clock, say: _Say) -> int:
    from ngrunner import launcher

    version = launcher.app_version()
    if self_test is None:
        try:
            from ngrunner.selftest import run_self_test as self_test
        except Exception as exc:  # noqa: BLE001 - a self-test that can't load is a failed one
            self_test = _cannot_load(exc)
    problems = list(self_test(paths))
    if record:
        try:
            launcher.record_self_test(paths, version, problems, clock=clock)
        except (launcher.LauncherStateError, OSError) as exc:
            problems.append(f"The result could not be recorded for the launcher: {exc}.")
    if not problems:
        say(f"NG Runner {version} self-test passed.")
        return 0
    plural = "" if len(problems) == 1 else "s"
    say(f"NG Runner {version} self-test found {len(problems)} problem{plural}:")
    for number, problem in enumerate(problems, start=1):
        say(f"{number}. {problem}")
    return 1


def _cannot_load(exc: Exception) -> Callable[[AppPaths], list[str]]:
    reason = f"The self-test itself could not be loaded ({type(exc).__name__}: {exc})."
    return lambda paths: [reason]


def _restore(source: Path, install: bool, paths: AppPaths, read_secret: Callable[[str], str],
             running: Callable[[], bool] | None, say: _Say) -> int:
    from ngrunner import backup

    try:
        data = source.read_bytes()
    except OSError as exc:
        say(f"The backup {source.name} could not be read ({exc.strerror or exc}).")
        return 1
    try:
        database = backup.restore(data, read_secret("Private backup key: "))
    except backup.BackupError as exc:
        say(str(exc))
        return 1
    facts = backup.describe(database)
    if not facts.ok:
        say(f"The backup opened, but its database is damaged: {facts.problems[0]}")
        return 1
    say(f"Restore drill passed: schema version {facts.schema_version}, {len(facts.tables)} tables,"
        f" {sum(facts.tables.values())} rows.")
    if not install:
        return 0
    try:
        ready = backup.for_a_new_pc(database)
    except backup.BackupError as exc:
        say(str(exc))
        return 1
    return _put_in_place(ready, paths, running, say)


def _put_in_place(database: bytes, paths: AppPaths, running: Callable[[], bool] | None,
                  say: _Say) -> int:
    """A restored database becomes NG Runner's only where it has none: a new PC."""
    if running is None:
        from ngrunner.single import is_running as running
    if running():
        say("NG Runner is running. Quit it from the tray first, then restore again.")
        return 1
    there = [p.name for p in (paths.db, paths.db.with_name(paths.db.name + "-wal"),
                              paths.db.with_name(paths.db.name + "-shm")) if p.exists()]
    if there:
        say(f"NG Runner already has a database here ({', '.join(there)}), so the backup was not"
            " put in place. Restore only onto a new install.")
        return 1
    paths.app_data.mkdir(parents=True, exist_ok=True)
    temp = paths.db.with_name(paths.db.name + ".restoring")
    try:
        with open(temp, "xb") as out:  # a new file: one left by an earlier attempt stays as it is
            out.write(database)
            out.flush()
            os.fsync(out.fileno())
    except OSError as exc:
        say(f"The restored database could not be written ({exc.strerror or exc}); nothing was put"
            f" in place. If {temp.name} is left from an earlier restore, delete it and try again.")
        return 1
    try:
        os.rename(temp, paths.db)  # never over a database that appeared meanwhile
    except OSError as exc:
        temp.unlink(missing_ok=True)  # made just now, by this command
        say(f"The restored database could not be put in place ({exc.strerror or exc}).")
        return 1
    say(f"The database is in place in {paths.app_data}. Start NG Runner from the Start Menu.")
    return 0


def _dev_close(paths: AppPaths, dev_id: int | None, clock: Clock, say: _Say) -> int:
    """For Sulaiman, once the cause is fixed: most DEV items close by themselves, and one that
    can't (no later event says it cleared) would stand for good and hide the next. With NG Runner
    running too: the database takes a second reader and writer."""
    from ngrunner.clock import fmt_clock, fmt_date
    from ngrunner.store import (
        StoreError, close_dev, get_dev, list_dev, log_event, open_store,
    )

    if not paths.db.exists():
        say("NG Runner has no database here yet, so it has no DEV items.")
        return 1
    try:
        store = open_store(paths.db)
        if dev_id is None:
            items = list_dev(store)
            for item in items:
                when = f"{fmt_date(item.at)} {fmt_clock(item.at)}"
                say(f"{item.id}  {when}  {item.code}  {item.text}")
            if not items:
                say("No DEV items are open.")
            return 0
        item = get_dev(store, dev_id)
        if item is None:
            say(f"There is no DEV item {dev_id}.")
            return 1
        if not close_dev(store, dev_id):
            say(f"DEV item {dev_id} was already closed.")
            return 0
        log_event(store, at=clock.now(), kind="dev_closed", lane="DEV",
                  text=f"DEV item {dev_id} ({item.code}) was closed by hand.")
    except StoreError as exc:
        say(f"{exc}.")
        return 1
    say(f"DEV item {dev_id} closed.")
    return 0


def _support_bundle(paths: AppPaths, clock: Clock, say: _Say) -> int:
    from ngrunner import support

    data = support.make_bundle(paths, clock=clock)
    folder = paths.app_data / "support"
    folder.mkdir(parents=True, exist_ok=True)
    name = support.bundle_name(clock.now())
    for n in range(1, 100):
        target = folder / (name if n == 1 else name.replace(".zip", f"-{n}.zip"))
        try:
            with open(target, "xb") as out:
                out.write(data)
        except FileExistsError:
            continue
        say(f"Support bundle written: {target}")
        return 0
    say("No free name for the support bundle; remove old ones from the support folder.")
    return 1


# ----- setup: settings.json, the keys and Studio's login, asked for in plain words


def _setup(paths: AppPaths, read_line: Callable[[str], str], read_secret: Callable[[str], str],
           secrets: Secrets | None, say: _Say) -> int:
    """Test window 2's step 4. Nothing is saved until every answer is in and checked. It shows
    no secret, and never repeats what was typed: a key pasted at the wrong prompt stays unprinted
    beyond what the console itself echoed."""
    from ngrunner.secrets import CredentialSecrets, SecretsError

    secrets = CredentialSecrets() if secrets is None else secrets
    before = _settings_there(paths, say)
    say("NG Runner's setup. Press Enter to keep what is in [brackets].")
    try:
        answers = _ask_settings(before, read_line, read_secret, say)
        keys = {name: _ask_key(name, secrets, read_secret, say) for name in _KEYS}
        login = _ask_studio_login(secrets, read_secret, say)
    except (EOFError, KeyboardInterrupt):
        say("Setup stopped. Nothing was saved.")
        return 1
    except SecretsError as exc:
        say(f"{exc}. Nothing was saved.")
        return 1
    problem = _overlap(answers, paths)
    if problem:
        say(f"{problem} Nothing was saved.")
        return 1
    raw = {} if before is None else settings_to_json(before)
    try:
        settings = settings_from({**raw, **answers})
    except SettingsError as exc:
        say(f"{exc}. Nothing was saved.")
        return 1
    if before is not None:
        settings = replace(settings, set_aside=before.set_aside)
    try:
        for name, value in {**keys, **login}.items():
            if value is not None:
                secrets.put(name, value)
        if any(login.values()):  # the cookies of the login it replaces
            secrets.remove("studio_session")
        has_login = all(secrets.get(name) for name in login)
        save_settings(paths, settings)
    except (SecretsError, OSError) as exc:
        say(f"Setup could not finish ({exc}). Run it again.")
        return 1
    _say_saved(settings, has_login, say)
    return 0


def _settings_there(paths: AppPaths, say: _Say) -> Settings | None:
    if not paths.settings.exists():
        return None
    try:
        return load_settings(paths)
    except SettingsError as exc:
        say(f"The settings already there can't be used ({exc}), so setup starts afresh.")
        return None


def _ask_settings(before: Settings | None, read_line: Callable[[str], str],
                  read_secret: Callable[[str], str], say: _Say) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    for name, label, called in (("ng_jobs", "NG Jobs", "NG Jobs"),
                                ("mozaik_shared", "Mozaik Shared", "Mozaik Shared"),
                                ("control", "control", None)):
        current = None if before is None else str(getattr(before, name))
        answers[name] = _ask_folder(label, called, current, read_line, say)
    answers["tracker_folder"] = _tracker_folder(before, answers["ng_jobs"], say)
    stays_on = True if before is None else not before.pc_sleeps_at_night
    answers["pc_sleeps_at_night"] = not _ask_yes_no("Does this PC stay on overnight?", stays_on,
                                                    read_line, say)
    for name, label in (("expected_on_from", "The PC is expected on from"),
                        ("expected_on_until", "The PC is expected on until")):
        current = getattr(Settings, name) if before is None else getattr(before, name)
        answers[name] = _ask_time(label, current, read_line, say)
    answers["backup_public_key"] = _ask_backup_key(before, read_secret, say)
    return answers


def _tracker_folder(before: Settings | None, ng_jobs: str, say: _Say) -> str | None:
    """Where the tracker's snapshots are, with no question: Jim's chat drops them beside NG Jobs, in
    a folder of this name, on every PC with the Drive. It is only ever read, and with none the lock
    pre-flight says so on Health. A folder set before stays."""
    if before is not None and before.tracker_folder is not None:
        return str(before.tracker_folder)
    found = Path(ng_jobs).parent / TRACKER_FOLDER_NAME
    if not found.is_dir():
        return None
    say(f"Tracker snapshots: {found}")
    return str(found)


def _ask_folder(label: str, called: str | None, current: str | None,
                read_line: Callable[[str], str], say: _Say) -> str:
    """A folder that is there. A name that isn't what the folder is normally called is asked
    about, since a wrong folder here would file a summary where Jim doesn't look."""
    hint = f" [{current}]" if current else ""
    while True:
        text = read_line(f"The {label} folder, as a full path{hint}: ").strip().strip('"')
        text = text or current or ""
        if not Path(text).is_absolute():
            say("That needs to be a full path, starting with its drive letter.")
        elif not os.path.isdir(text):
            say("There is no folder there. Check the path and try again.")
        elif (called is None or Path(text).name.casefold() == called.casefold()
              or _ask_yes_no(f"That folder is not called {called}. Use it anyway?", False,
                             read_line, say)):
            return text


def _ask_yes_no(question: str, default: bool, read_line: Callable[[str], str], say: _Say) -> bool:
    while True:
        text = read_line(f"{question} (y/n) [{'y' if default else 'n'}]: ").strip().casefold()
        if not text:
            return default
        if text in ("y", "yes"):
            return True
        if text in ("n", "no"):
            return False
        say("Please answer y or n.")


def _ask_time(label: str, current: str, read_line: Callable[[str], str], say: _Say) -> str:
    while True:
        text = read_line(f"{label}, on a 24-hour clock [{current}]: ").strip() or current
        try:
            parse_wall(text)
        except ValueError:
            say("That needs to be a time like 06:30.")
        else:
            return text


def _ask_backup_key(before: Settings | None, read_secret: Callable[[str], str], say: _Say) -> str:
    """Hidden though it is public: the private key printed beside it is an easy slip to paste."""
    current = "" if before is None else before.backup_public_key
    hint = "Enter keeps the one saved" if current else "Enter to leave it for later"
    while True:
        text = read_secret(f"The backup public key, the line keygen printed ({hint}): ").strip()
        text = text or current
        try:
            settings_from({"ng_jobs": "C:\\x", "mozaik_shared": "C:\\y", "control": "C:\\z",
                           "backup_public_key": text})
        except SettingsError as exc:
            say(f"{exc}.")
        else:
            return text


_KEYS = {
    "signing_key": "The control folder's signing key, as in the Apps Script's Script Properties",
    "export_key": "The Render job-list export key",
}


def _ask_key(name: str, secrets: Secrets, read_secret: Callable[[str], str],
             say: _Say) -> str | None:
    """The new value, or None to keep the one saved. Input is hidden, and nothing repeats it."""
    from ngrunner.signing import EnvelopeError, key_from_text

    saved = secrets.get(name)
    hint = "it won't show; Enter keeps the one saved" if saved else "it won't show"
    while True:
        text = read_secret(f"{_KEYS[name]} ({hint}): ").strip()
        if not text:
            if saved:
                return None
            say("NG Runner can't work without this key. Press Ctrl+C to stop for now.")
        elif name != "signing_key":
            return text
        else:
            try:
                key_from_text(text)
                return text
            except EnvelopeError:
                say("That isn't the signing key: it is 64 characters, 0-9 and a-f.")


def _ask_studio_login(secrets: Secrets, read_secret: Callable[[str], str],
                      say: _Say) -> dict[str, str | None]:
    """Studio's login: each secret's new value, or None to keep what is saved. Both are hidden, and
    nothing repeats them. NG Runner files without it, marking each report "designs not
    cross-checked", so Enter leaves it for later; but a username is no login without its password.
    A password is kept as typed, since a space can be part of one."""
    saved_username, saved_password = secrets.get("studio_username"), secrets.get("studio_password")
    keeps = "it won't show; Enter keeps the one saved"
    hint = keeps if saved_username else "it won't show; Enter to leave it for later"
    username = read_secret(f"Studio's login, its username ({hint}): ").strip() or None
    if not (username or saved_username):
        return {"studio_username": None, "studio_password": None}
    hint = keeps if saved_password else "it won't show"
    while True:
        password = read_secret(f"Studio's login, its password ({hint}): ")
        if password.strip():
            return {"studio_username": username, "studio_password": password}
        if saved_password:
            return {"studio_username": username, "studio_password": None}
        say("A Studio login needs its password too. Press Ctrl+C to stop for now.")


def _overlap(answers: Mapping[str, Any], paths: AppPaths) -> str | None:
    folders = {name: Path(os.path.realpath(answers[name]))
               for name in ("ng_jobs", "mozaik_shared", "control")}
    folders["NG Runner's data"] = Path(os.path.realpath(paths.app_data))
    for (a, here), (b, there) in itertools.combinations(folders.items(), 2):
        if here == there or here in there.parents or there in here.parents:
            return f"The {a} and {b} folders overlap, and they must be separate."
    return None


def _say_saved(settings: Settings, has_login: bool, say: _Say) -> None:
    say("Saved. The folders are there, and the keys are in Windows Credential Manager.")
    say(f"NG Jobs: {settings.ng_jobs}")
    say(f"Mozaik Shared: {settings.mozaik_shared}")
    say(f"Control folder: {settings.control}")
    say(f"Hours the PC is expected on: {settings.expected_on_from} to {settings.expected_on_until},"
        f" and {'off' if settings.pc_sleeps_at_night else 'on'} overnight.")
    if not settings.backup_public_key:
        say("No backup key yet: the board will say the nightly backup is waiting for one.")
    if not has_login:
        say("No Studio login yet: reports are filed marked designs not cross-checked, and the board"
            " says so.")
    say(f"Mode: {settings.mode}.")
    say("Quit NG Runner from the tray and start it from the Start Menu, so it reads them.")


def _keygen(say: _Say) -> int:
    from ngrunner import backup

    private, public = backup.new_keypair()
    say(f"New backup key pair, key {backup.key_id(public)}:")
    say(f"Private (ICLA's password manager only, never on Jim's PC): {private}")
    say(f"Public (NG Runner's settings on Jim's PC): {public}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
