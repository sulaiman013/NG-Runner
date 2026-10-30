r"""
NG Runner's command line: NGRunner.exe in a build, python -m ngrunner from the repo.

Why it is this thin: every command is a call into a module with its own tests; this file only
reads the arguments, prints the outcome in plain words and returns the exit code the
installer and the launcher rely on:

    0  done (for --self-test: every check passed)
    1  it ran and found a problem; the printed lines say what
    2  the command line was wrong, or NG Runner has no data folder
    3  that command is not built yet (wave 2 fills run, install-shortcut and release-all)

Commands:
    --version                          the build's version
    --self-test [--record]             the post-install self-test; --record (the installer)
                                       lets the launcher start this version
    run [--attempt ID] [--open-board]  NG Runner itself, the default (the launcher starts it)
    install-shortcut --target EXE      the Start Menu item, carrying NG Runner's AUMID
    release-all                        hand every job back to Jim's chat (uninstall)
    restore BACKUP [--install]         the restore drill; --install then puts the database in
                                       place on a new PC
    support-bundle                     a support bundle in the data folder, for Sulaiman
    keygen                             a new backup key pair

Modules are imported inside each command, so a module that fails to load breaks only the
commands that need it, and --self-test can still name it.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NoReturn, TextIO

from ngrunner.clock import Clock, SystemClock
from ngrunner.config import AppPaths, SettingsError, app_paths

NOT_BUILT = 3
_WAVE_2 = ("run", "install-shortcut", "release-all")


class _UsageError(Exception):
    pass


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
) -> int:
    say = _Say(out if out is not None else sys.stdout)
    try:
        args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
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
    try:
        paths = app_paths(os.environ if environ is None else environ)
    except SettingsError as exc:
        say(f"{exc}.")
        return 2
    if args.self_test:
        return _self_test(paths, args.record, self_test, clock, say)
    command = args.command or "run"
    if command in _WAVE_2:
        say(f"'{command}' is not built yet: wave 2 of P1a fills it.")
        return NOT_BUILT
    if command == "restore":
        return _restore(Path(args.backup), args.install, paths, read_secret, running, say)
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
    restore = commands.add_parser("restore", help="the restore drill (and a restore onto a new PC)")
    restore.add_argument("backup", help="a <YYYYMMDD>.ngrbak file from the control folder")
    restore.add_argument("--install", action="store_true",
                         help="then put the database in place (only where NG Runner has none)")
    commands.add_parser("support-bundle", help="write a support bundle into the data folder")
    commands.add_parser("keygen", help="print a new backup key pair")
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
        if self._out is not None:
            self._out.write(line + "\n")
            self._out.flush()


# ----- commands


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
    return _put_in_place(database, paths, running, say) if install else 0


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


def _keygen(say: _Say) -> int:
    from ngrunner import backup

    private, public = backup.new_keypair()
    say(f"New backup key pair, key {backup.key_id(public)}:")
    say(f"Private (ICLA's password manager only, never on Jim's PC): {private}")
    say(f"Public (NG Runner's settings on Jim's PC): {public}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
