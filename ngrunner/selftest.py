r"""
The post-install self-test: can this build, on this PC, do NG Runner's work safely?

Why: the launcher starts only a version whose self-test passed (spec 6.7), so a build with a
missing library, a migration that would damage the database, a chokepoint that lets a
forbidden write through, or a message that breaks Jim's format is caught at install, by
Sulaiman, instead of overnight on Jim's PC. The installer runs it as
NGRunner.exe --self-test --record; the exit code is 0 only when run_self_test() finds nothing.

It checks five things, each reported in plain words:
1. Every P1a and P1b module and library imports (in a build, a module PyInstaller missed fails here).
2. The database migration runs, twice, on a copy of the live database taken with SQLite's
   online backup (a new, empty database on a fresh install). The live database is only read.
3. The 20 scripted forbidden writes (the P1a exit test, design 3.2) are each refused by the
   real commit_write, in a sandbox of folders made for the purpose, and leave no file behind.
   Two allowed writes must succeed there first: a sandbox that refuses everything (a folder
   it can't reach, say) would refuse the 20 for the wrong reason and prove nothing.
4. Every message template passes the message lint.
5. Each guard's reader runs once on a small made-up file: a program, a shared read, a job sheet, a
   labels PDF and a tracker snapshot. A module imported by name at run time passes an import and
   fails only when used, as a real build showed: this is that use.

Everything it makes is inside NG Runner's own data folder (staging\selftest-<random>), and
removed again at the end, by remove_own_tree, which never follows a link.
"""
from __future__ import annotations

import hashlib
import importlib
import itertools
import secrets
import sys
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path, PureWindowsPath
from typing import Any

from ngrunner.backup import BackupError, DatabaseFacts, describe, remove_own_tree, snapshot
from ngrunner.config import AppPaths
from ngrunner.secrets import CredentialSecrets, Secrets, SecretsUnavailable
from ngrunner.writes import CommitResult, FileWrite, Roots, commit_write

# Every P1a module (spec section 5) and the libraries a build must carry.
P1A_MODULES: tuple[str, ...] = (
    "ngrunner.clock", "ngrunner.config", "ngrunner.signing", "ngrunner.writes", "ngrunner.match",
    "ngrunner.intake.summary_check", "ngrunner.store", "ngrunner.secrets",
    "ngrunner.intake.fetch", "ngrunner.intake.branch", "ngrunner.joblist", "ngrunner.fab",
    "ngrunner.messages.lint", "ngrunner.messages.templates", "ngrunner.messages.outbox",
    "ngrunner.messages.pushover", "ngrunner.cards", "ngrunner.digest", "ngrunner.heartbeat",
    "ngrunner.board", "ngrunner.tray", "ngrunner.toasts",
    "ngrunner.launcher", "ngrunner.single", "ngrunner.backup", "ngrunner.support",
    "ngrunner.selftest", "ngrunner.intake.pipeline", "ngrunner.interlock", "ngrunner.jobmodel",
    "ngrunner.messenger", "ngrunner.supervisor", "ngrunner.canary",
    "ngrunner.intake.studio_designs",
    "ngrunner.guards.bak", "ngrunner.guards.design", "ngrunner.guards.facts", "ngrunner.guards.headsup",
    "ngrunner.guards.kept", "ngrunner.guards.labels", "ngrunner.guards.lockcheck",
    "ngrunner.guards.nest_state",
    "ngrunner.guards.nestfiles", "ngrunner.guards.observer", "ngrunner.guards.opt",
    "ngrunner.guards.preflight", "ngrunner.guards.reads", "ngrunner.guards.scan",
    "ngrunner.guards.sheets", "ngrunner.guards.takeoffs", "ngrunner.guards.tap",
    "ngrunner.guards.tool_checks", "ngrunner.guards.tracker", "ngrunner.guards.watch",
    "ngrunner.toolkit",
    "ngrunner.checks", "ngrunner.checks.crosscheck", "ngrunner.checks.readers", "ngrunner.checks.findings",
    "ngrunner.checks.jobchecks",
    "flask", "waitress", "requests", "pystray", "PIL", "psutil", "win32api", "pywintypes",
    "pythoncom", "win32com", "win32com.shell", "win32com.propsys", "win32cred", "win32timezone",
    "win32job", "runpy", "pkgutil", "windows_toasts",
    "cryptography",
    "cryptography.hazmat.primitives.ciphers.aead", "pypdf", "tzdata", "sqlite3",
    "playwright.sync_api",
)

SANDBOX_CONTENT = b"NG Runner self-test: safe to delete\n"
# As Jim's docs give one (RULES 705-724), and made up: no real one has been read yet.
_SMALL_NEST = b"\xff\xfe" + (
    '<OptimizeJob><OptimizeMaterial Width="1244.6" Length="2463.8"/><OptimizePart/>'
    '<OptimizeSheet Quan="1" Flipped="False"><OptimizeGCodeRun Filename="TESS01.TAP"/></OptimizeSheet>'
    '</OptimizeJob>').encode("utf-16-le")
# What des_cutlist prints (measured on its real output, 2026-10-31), and a room as Mozaik writes one.
_SMALL_CUTLIST = "1 | Made-up White | Top | 356.0 | 870.0\nTOTAL 1\n"
_SMALL_ROOM = (b'15\r\n<?xml version="1.0" encoding="utf-8"?>\r\n<Room Quan="1"><Products>'
               b'<Product CabNo="3" Count="1"><CabProdPart Name="Top" Quan="1" W="356" L="870"'
               b' UsageType="0" Type="Top"><PartOpsXml><OperationHole X="9.525" X_Eq="PartL-1" />'
               b'</PartOpsXml></CabProdPart></Product></Products></Room>')
_SMALL_TOOL = (
    "import sys\n"
    "sys.stdout.buffer.write((sys.argv[1] + ' utf8=' + str(sys.flags.utf8_mode) + ' caf\\u00e9')"
    ".encode(sys.stdout.encoding))\n"
    "sys.exit(3)\n")
_CREDENTIAL_PREFIX = "NGRunner-selftest"
_CREDENTIAL_NAME = "signing_key"

Migrate = Callable[[Path], object]
Commit = Callable[..., CommitResult]


# ----- the whole self-test


def run_self_test(
    paths: AppPaths,
    *,
    modules: Iterable[str] = P1A_MODULES,
    migrate: Migrate | None = None,
    samples: Callable[[], Iterable[object]] | None = None,
    lint: Callable[[object], list[str]] | None = None,
    credentials: Callable[[], Secrets] | None = None,
    tools: Callable[[Path], list[str]] | None = None,
) -> list[str]:
    """Every problem found, one plain sentence each; empty means this build may run. migrate
    and lint default to the store's and the lint module's own, samples to message_samples,
    credentials to Credential Manager under the self-test's own name, and tools to running a
    made-up toolkit for real (tests fake them)."""
    problems = import_problems(modules)
    if getattr(sys, "frozen", False) and not sys.flags.utf8_mode:
        problems.append("This build does not run in UTF-8 mode (ngrunner.spec must pass X utf8).")
    work = _new_work_folder(paths)
    try:
        problems += _migration(paths, work / "database", migrate)
        problems += forbidden_write_problems(work / "writes")
        problems += guard_reader_problems(work / "guards")
        problems += (tools or tool_run_problems)(work / "tools")
    finally:
        left = remove_own_tree(work, inside=paths.staging)
        if left:
            problems.append(f"The self-test could not clean up after itself: {left}")
    problems += _templates(samples, lint)
    problems += credential_problems(credentials)
    return problems


def _new_work_folder(paths: AppPaths) -> Path:
    paths.staging.mkdir(parents=True, exist_ok=True)
    work = paths.staging / f"selftest-{secrets.token_hex(4)}"
    work.mkdir()  # a new name: never a folder that was there before
    return work


# ----- 1. imports


def import_problems(modules: Iterable[str]) -> list[str]:
    problems = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - any failure to import is a failed self-test
            problems.append(f"{name} could not be imported ({type(exc).__name__}: {exc}).")
    return problems


# ----- Credential Manager, for real


def credential_problems(make: Callable[[], Secrets] | None = None) -> list[str]:
    """Store, read back and remove one throwaway credential under the self-test's own name, never
    NG Runner's keys. Importing win32cred proves little: reading a credential imports win32timezone
    by name at run time, and a build without it could read no key at all (found by the first real
    run of the built app, 2026-10-30). No Credential Manager here is not a broken build, and setup
    says so when it matters."""
    keeper = (make or _selftest_secrets)()
    stored = secrets.token_hex(16)
    try:
        keeper.put(_CREDENTIAL_NAME, stored)
        if keeper.get(_CREDENTIAL_NAME) != stored:
            return ["Windows Credential Manager gave back something other than what was stored."]
        return []
    except SecretsUnavailable:
        return []
    except Exception as exc:  # noqa: BLE001 - whatever stops a key being read fails this build
        return [f"A saved key could not be read from Windows Credential Manager ({_reason(exc)})."]
    finally:
        try:
            keeper.remove(_CREDENTIAL_NAME)
        except Exception:  # noqa: BLE001 - what stays is a random value under its own name
            pass


def _selftest_secrets() -> Secrets:
    return CredentialSecrets(target_prefix=_CREDENTIAL_PREFIX)


# ----- 2. the database migration, on a copy


def _migration(paths: AppPaths, work: Path, migrate: Migrate | None) -> list[str]:
    try:
        migrate = migrate or _store_migrate()
    except (ImportError, LookupError) as exc:
        return [f"The database migration could not be checked: {_reason(exc)}."]
    return migration_problems(paths, work, migrate)


def _store_migrate() -> Migrate:
    """The store's own way in: open_store(path) checks the file, then creates or upgrades it."""
    store = importlib.import_module("ngrunner.store")
    found = getattr(store, "open_store", None)
    if not callable(found):
        raise LookupError("ngrunner.store has no open_store(path)")
    return found


def migration_problems(paths: AppPaths, work: Path, migrate: Migrate) -> list[str]:
    """Run migrate twice on a copy of the live database (or on a new one) in work."""
    work.mkdir(parents=True)
    copy = work / "ngrunner-copy.db"
    before: DatabaseFacts | None = None
    if paths.db.is_file():
        try:
            image = snapshot(paths.db)
        except BackupError as exc:
            return [f"The database could not be copied for the migration check: {exc}"]
        before = describe(image)
        if not before.ok:
            return [f"The live database is damaged: {before.problems[0]}"]
        with open(copy, "xb") as out:
            out.write(image)
    try:
        migrate(copy)
        first = describe(snapshot(copy))
        migrate(copy)
        second = describe(snapshot(copy))
    except Exception as exc:  # noqa: BLE001 - a migration failing in any way fails the self-test
        return [f"The database migration failed on a copy: {_reason(exc)}."]
    return _migration_verdict(before, first, second)


def _migration_verdict(before: DatabaseFacts | None, first: DatabaseFacts,
                       second: DatabaseFacts) -> list[str]:
    if not first.ok:
        return [f"After the migration the copy is damaged: {first.problems[0]}"]
    if not first.schema_version:
        return ["The database migration left no schema version (PRAGMA user_version is 0)."]
    if before is not None and (before.schema_version or 0) > first.schema_version:
        return [f"The database migration lowered the schema version from {before.schema_version}"
                f" to {first.schema_version}."]
    if (second.schema_version, second.tables) != (first.schema_version, first.tables):
        return ["The database migration is not idempotent: running it again changed the copy."]
    return []


# ----- 3. the forbidden writes


@dataclass(frozen=True)
class ForbiddenWrite:
    """One scripted forbidden write: where it aims (a root and the names below it), what the
    Mozaik check answers, and what a file already there holds (written by someone else)."""

    label: str
    root: str  # ng_jobs, mozaik_shared, control, or outside (in no root at all)
    parts: tuple[str, ...]
    interlock: str | None = None
    existing: bytes | None = None


_JOB = "Testerson_101426"
FORBIDDEN_WRITES: tuple[ForbiddenWrite, ...] = (
    ForbiddenWrite("a Room file in a Mozaik job", "mozaik_shared", ("Jobs", _JOB, "Room1.des")),
    ForbiddenWrite("Mozaik's Data folder", "mozaik_shared", ("Data", "Materials.dat")),
    ForbiddenWrite("a product library", "mozaik_shared", ("Product Libraries", "Closets.dat")),
    ForbiddenWrite("Jim's toolkit", "mozaik_shared", ("_NG_Automation", "crosscheck.py")),
    ForbiddenWrite("the regression fixtures", "control", ("regression", "expected.json")),
    ForbiddenWrite("a template", "control", ("Templates", "digest.json")),
    ForbiddenWrite("the Installer App's page", "ng_jobs", (_JOB, "Index.html")),
    ForbiddenWrite("the Installer App's parts catalog", "ng_jobs",
                   ("_NG Runner", "parts_catalog.csv")),
    ForbiddenWrite("a chat report", "ng_jobs", (_JOB, f"{_JOB}_report.txt")),
    ForbiddenWrite("a chat crosscheck", "ng_jobs", (_JOB, f"{_JOB}_crosscheck.txt")),
    ForbiddenWrite("a CNC program", "ng_jobs", (_JOB, "TEST01.tap")),
    ForbiddenWrite("Mozaik's lock file", "ng_jobs", (_JOB, "MozaikJob.LCK")),
    ForbiddenWrite("a material or TAP subfolder", "ng_jobs",
                   (_JOB, "3-4 Classic White", f"{_JOB}_summary.pdf")),
    ForbiddenWrite("a file loose in NG Jobs", "ng_jobs", (f"{_JOB}_summary.pdf",)),
    ForbiddenWrite("a folder that is not a job", "ng_jobs", ("Archive", "Archive_summary.pdf")),
    ForbiddenWrite("a name NG Runner does not own", "ng_jobs", (_JOB, "notes.txt")),
    ForbiddenWrite("an IC Conversion before P3", "ng_jobs", (_JOB, f"{_JOB}_ic_conversion.json")),
    ForbiddenWrite("a summary someone else wrote", "ng_jobs",
                   ("Fakeley_102026", "Fakeley_102026_summary.pdf"),
                   existing=b"%PDF-1.7 Jim's own copy"),
    ForbiddenWrite("a file outside every allowed folder", "outside", (f"{_JOB}_summary.pdf",)),
    ForbiddenWrite("a write while Mozaik is running", "ng_jobs",
                   ("Mockford_103026", "Mockford_103026_summary.pdf"),
                   interlock="Mozaik is running on this PC"),
)

# Writes the sandbox must allow first (what each proves works), so that a refusal above
# comes from the rules and not from a sandbox that refuses everything.
_ALLOWED = (
    ("NG Jobs", "ng_jobs", ("Dummitt_110226", "Dummitt_110226_summary.pdf")),
    ("the control folder", "control", ("heartbeat", "20261029", "1.json")),
)
_SANDBOX_FOLDERS = {
    "ng_jobs": ((_JOB, "3-4 Classic White"), ("_NG Runner",), ("Archive",), ("Fakeley_102026",)),
    "mozaik_shared": (("Jobs", _JOB), ("Data",), ("Product Libraries",), ("_NG_Automation",)),
    "control": (("regression",), ("Templates",), ("heartbeat",)),
    "outside": ((),),
}
_SANDBOX_NAMES = {"ng_jobs": "NG Jobs", "mozaik_shared": "Mozaik Shared", "control": "control",
                  "app_data": "app_data", "outside": "elsewhere"}


def forbidden_write_problems(work: Path, *, commit: Commit = commit_write) -> list[str]:
    """Run FORBIDDEN_WRITES in a new sandbox in work; each must be refused and change nothing."""
    folders = _make_sandbox(work)
    roots = Roots(folders["ng_jobs"], folders["mozaik_shared"], folders["control"],
                  folders["app_data"])
    problems = _allowed_writes(roots, folders, commit)
    if problems:
        return problems
    before = _contents(work)
    for number, row in enumerate(FORBIDDEN_WRITES, start=1):
        target = folders[row.root].joinpath(*row.parts)
        expected = hashlib.sha256(row.existing).hexdigest() if row.existing is not None else None
        result = _commit(commit, roots, target, expected, row.interlock)
        if result.ok:
            problems.append(f"Forbidden write {number} ({row.label}) was not refused.")
            before = _contents(work)
            continue
        after = _contents(work)
        if after != before:
            changed = sorted(name for name in set(before) | set(after)
                             if before.get(name) != after.get(name))
            problems.append(f"Forbidden write {number} ({row.label}) was refused, but files"
                            " changed:"
                            f" {', '.join(changed)}.")
            before = after
    return problems


def _make_sandbox(work: Path) -> dict[str, Path]:
    work.mkdir(parents=True)
    folders = {key: work / name for key, name in _SANDBOX_NAMES.items()}
    folders["app_data"].mkdir()
    for key, subfolders in _SANDBOX_FOLDERS.items():
        for names in subfolders:
            folders[key].joinpath(*names).mkdir(parents=True, exist_ok=True)
    for row in FORBIDDEN_WRITES:
        if row.existing is not None:
            folders[row.root].joinpath(*row.parts).write_bytes(row.existing)
    return folders


def _allowed_writes(roots: Roots, folders: dict[str, Path], commit: Commit) -> list[str]:
    for where, root, parts in _ALLOWED:
        target = folders[root].joinpath(*parts)
        result = _commit(commit, roots, target, None, None)
        if not result.ok or not (target.is_file() and target.read_bytes() == SANDBOX_CONTENT):
            why = (result.reason if not result.ok
                   else "it reported success, but the file is not there")
            return [f"The self-test's own allowed write to {where} was refused ({why}),"
                    " so the forbidden-writes check proves nothing."]
    return []


def _commit(commit: Commit, roots: Roots, target: Path, expected: str | None,
            answer: str | None) -> CommitResult:
    try:
        return commit([FileWrite(target, SANDBOX_CONTENT, expected)], roots=roots,
                      interlock=lambda planned: answer, owned=lambda path: None)
    except Exception as exc:  # noqa: BLE001 - a chokepoint that raises has refused, and says why
        return CommitResult(ok=False, reason=f"{type(exc).__name__}: {exc}")


def _contents(work: Path) -> dict[str, str]:
    """Every file under work, by its path below work, with its sha256."""
    return {str(PureWindowsPath(path.relative_to(work))):
            hashlib.sha256(path.read_bytes()).hexdigest()
            for path in work.rglob("*") if path.is_file()}


# ----- 4. message templates


def _templates(samples: Callable[[], Iterable[object]] | None,
               lint: Callable[[object], list[str]] | None) -> list[str]:
    try:
        lint = lint or _function("ngrunner.messages.lint", "lint")
    except (ImportError, LookupError) as exc:
        return [f"The message templates could not be checked: {_reason(exc)}."]
    return template_problems(samples or message_samples, lint)


def message_samples() -> list[object]:
    """One rendered example of every P1a message, from the templates this build carries: each
    template, every kind of card, every alarm and every fixed template, with made-up jobs.
    tests\\test_selftest.py fails when a template has no example here. Imported here, not at the
    top, so a template module that won't load is reported as a problem like any other."""
    from ngrunner.cards import new_card, parse_command
    from ngrunner.match import Opportunity
    from ngrunner.messages import Message, templates as t

    day, install = date(2026, 10, 30), date(2026, 11, 6)
    at = datetime(2026, 10, 30, 22, 5, tzinfo=timezone.utc)
    job, other, row = "Testerson_101426", "Fakeley_102026", "Dummitt"
    candidates = (Opportunity("Dummitt_101426", "201", "Pat Example", "Ready to Install"),
                  Opportunity("Dummitt_102226", "202", "Pat Example", "Ready to Order"))
    numbers = itertools.count(1)
    cards = [new_card(spec, created_at=at, allocate=lambda prefix: next(numbers)) for spec in (
        t.which_job_card(row, designer="PE", install=install, candidates=candidates),
        t.which_job_card(row),
        t.closet_or_garage_card(job, reason="the Fab row says garage, but no design is a garage"),
        t.fetch_failed_card(job, since=at, reason="Studio did not answer within 60 seconds"),
        t.ic_changed_card(job, filed_at=at, filed_pages=19, new_at=at, new_pages=20),
        t.row_changed_card(job, was="Testerson", now_reads=row, candidates=candidates),
        t.remake_card(job, row="Testerson"),
        t.design_mismatch_card(job, report=3, proposal=4),
        t.design_mismatch_card(other, report=3, proposal=3),
        t.sheet_size_card(job, material="3-4 Classic White", programs=(("TESS07.TAP", "61x109"),),
                          expected="49x97"),
        t.sheet_size_card(job, material="3-4 Classic White", expected="49x97", programs=(
            ("TESS07.TAP", "61x109"), ("TESS08.TAP", "61x109"), ("TESS09.TAP", None))),
    )]
    echoes = [t.echo(parse_command(typed, cards)) for typed in (
        f"{cards[2].id} 2", "pause", "resume", f"add job {job}", "add job 84213")]
    return [
        t.digest(day, for_you=(t.install_soon(job, install), t.stuck(other), t.fetch_stuck(other),
                               t.missed_digest(due=at, sent=at), *t.ALARMS.values()),
                 jobs=((other, "filed 10/29 3:05 PM"),), cards=[(card, 3) for card in cards],
                 open_items=len(cards), oldest_days=3),
        t.quiet_digest(day),
        t.quiet_digest(day, open_items=2, oldest_days=3),
        t.filed(job, row="Testerson", designer="PE", install=install),
        t.filed(job, row="Testerson garage", garage=True),
        t.shadow(t.filed(job, row="Testerson 10/14/26")),
        t.which_job_email(row, designer="PE", install=install, candidates=candidates),
        t.which_job_email(row),
        *(t.card_message(card) for card in cards),
        t.dev((t.DevLine(at, "Studio did not answer on the 3rd try", job),
               t.DevLine(at, "the Fab export is 42 minutes old")), day=day),
        t.dev((), day=day),
        t.ran(day, version="0.0.1", new_rows=2, filed=1, cards_opened=1, cards_answered=0,
              writes_refused=0, dev_open=1),
        t.cant_send(since=at, now=at, waiting=2),
        *(t.fixed(kind, day=day) for kind in (*t.FIXED, "any_other_kind")),
        *(Message("echo", "NG Runner", (line,)) for line in echoes),
        *_nest_samples(t, job),
        *_takeoff_samples(t, job),
        *_check_samples(t, job),
    ]


def _nest_samples(t: Any, job: str) -> list[object]:
    """The nest guard's messages and board notes (P1b build spec 5.3 and 5.6): the shapes the spec
    gives, word for word, so a template that drifts from them fails here."""
    from ngrunner.messages import Message

    classic = "3/4 Classic White"
    posted = datetime(2026, 10, 31, 6, 40, tzinfo=timezone.utc)      # 11:40 PM Pacific
    checked = datetime(2026, 10, 31, 4, 42, tzinfo=timezone.utc)     # 9:42 PM Pacific
    doubled, six = ("TESS02.TAP", "TESS03.TAP"), tuple(f"TESS0{n}.TAP" for n in range(1, 7))
    skis = "Tafisa Apres Skis"
    notes = (t.POSTED_BEFORE, t.SAVE_THE_NEST, t.NO_PROGRAMS_YET,
             t.nest_checked_note(checked, six),
             t.NEST_UNREADABLE, t.NEST_SETTLING, t.NEST_CHANGED, t.nest_names_missing(doubled),
             t.lock_held(False, "JDELA", timedelta(days=2)), t.lock_held(True, None, timedelta(minutes=5)),
             t.open_save_nest(job, classic), t.nest_on_wrong_sheet("61x109", "49x97"),
             t.nest_holes_doubled(8), t.nest_holes_doubled(1),
             t.programs_read_note(at=checked, sheet="49x97"),
             t.programs_read_note(at=checked, sheet=None, merged=1),
             t.programs_read_note(at=None, sheet="49x97", cleared=1),
             t.programs_unread_note(doubled))
    return [
        t.do_not_cut(job, classic, trial=True, reasons=(t.past_its_size(1, "61x109", "49x97"),)),
        t.do_not_cut(job, classic, trial=False, reasons=(t.doubled_holes(8, doubled),)),
        t.do_not_cut(job, classic, trial=False, reasons=(t.past_its_size(2, None, "49x97"),)),
        t.not_checked(job, classic, posted=posted, now=posted),
        t.nest_checked(job, classic, at=checked, programs=six, trial=False),
        t.nest_checked(job, classic, at=checked, programs=six, trial=True, open_items=2),
        t.sheet_cleared(job, classic, programs=(("TESS07.TAP", "61x109"),), at=checked),
        t.sheet_cleared(job, classic, programs=(("TESS07.TAP", "61x109"), ("TESS08.TAP", None)),
                        at=checked),
        t.fault_gone(job, classic, at=checked),
        t.pass_gone(job, classic, at=checked),
        *(Message("nest_risk", "NG Runner", (line,)) for line in (
            t.open_do_not_cut(job, classic, trial=True, reasons=(t.doubled_holes(8, doubled),
                                                                 t.past_its_size(1, "61x109",
                                                                                 "49x97"))),
            t.open_not_checked(job, classic, posted=posted, now=posted),
            t.more_not_checked(4), t.material_words("3-4 Classic White"))),
        t.order_heads_up(job, sheets=((12, f"3/4 {skis}"), (4, f"1/4 {skis}")),
                         tape=((240, '15/16"', skis),)),
        t.order_heads_up(job, sheets=((12, f"3/4 {skis}"),), tape=(), revised=True,
                         flags=("IC Arctic White, built as Egger Classic White",)),
        t.before_you_lock(job, items=("2 sheets of 3/4 Spring Blossom have no price in the"
                                      " tracker",)),
        *_heads_up_samples(job),
        *(Message("nest_note", "NG Runner", (note,)) for note in notes),
    ]


def _heads_up_samples(job: str) -> list[object]:
    """What really goes out to Barbara and Will: guards/headsup.py makes it, over a made-up takeoff."""
    from ngrunner.guards.headsup import Takeoff, TapeLine, heads_up, order_flags
    from ngrunner.guards.headsup import SheetLine as HeadsUpSheet

    takeoff = Takeoff((HeadsUpSheet("Egger Classic White", 0.75, 12, 11.2),),
                      (TapeLine("Egger Classic White", '15/16"', 240.0),), 26000.0,
                      "Arctic White (WH)", 10)
    flags = order_flags(takeoff, total_line=25000)
    return [heads_up(job, takeoff, flags, revised=False), heads_up(job, takeoff, (), revised=True)]


def _takeoff_samples(t: Any, job: str) -> list[object]:
    """NG Runner's own takeoff (P3's takeoff on intake): its heads-up, the one that replaces a
    partial list, the chat's list sent in its place, and its lines on the board, in the digest and
    in the history."""
    from ngrunner.messages import Message

    white = {"colour": "WH", "thk_in": 0.75, "parts": 8, "sqft": 30.33, "sheet_in": [49.0, 97.0],
             "sheets_with_waste": 2, "calc_sheets_32": 0.9478, "tape_lf": 26.0}
    inch = {**white, "thk_in": 1.0, "sheets_with_waste": 1, "tape_lf": 4.0}
    tafisa = {**white, "colour": "AP", "parts": 4, "sheets_with_waste": 1, "tape_lf": 12.4}
    chats = _heads_up_samples(job)[0]
    return [
        t.takeoff_heads_up(job, buckets=[white, tafisa], waste_percent=10, revised=False,
                           flags=(f"Not all Classic White: {t.takeoff_material('AP')}",)),
        t.takeoff_heads_up(job, buckets=[white, inch], flags=(), waste_percent=10, revised=True),
        t.takeoff_unverified(job, lines=3, revised=False),
        t.takeoff_unverified(job, lines=1, revised=True),
        t.from_the_chats_takeoff(chats),
        t.from_the_chats_takeoff(chats, could_not_verify=2),
        *(Message("takeoff_line", "NG Runner", (f"1. {job}: {line}",)) for line in (
            t.takeoff_made_words(state="made", complete=True, board_parts=8, buckets=1,
                                 tape_lf=26.0),
            t.takeoff_made_words(state="made", complete=False, could_not_verify=2),
            t.takeoff_made_words(state="in_chat", branch="garage"),
            t.takeoff_made_words(state="in_chat", branch="mixed"),
            t.takeoff_made_words(state="failed"),
            t.takeoff_digest([white], complete=True, could_not_verify=0),
            t.takeoff_digest([white], complete=False, could_not_verify=2),
            t.takeoff_digest([white], complete=False, could_not_verify=2, chat=True),
            t.could_not_verify_lines(3), t.CHATS_TAKEOFF,
            *t.takeoff_lines([white, inch, tafisa]))),
    ]


def _check_samples(t: Any, job: str) -> list[object]:
    """P3's checks: what a check found, item by item in every kind there is, the check's message, its
    lines on the board, Your list and in the digest, the close prompt and every echo."""
    from ngrunner.checks.findings import Item
    from ngrunner.messages import Message

    at = datetime(2026, 10, 31, 4, 42, tzinfo=timezone.utc)          # 9:42 PM Pacific
    fixes = [Item(n, "mozaik" if what in ("stale_rows", "bore_off_panel") else "fix", "notch_check", what,
                  ("R1C3", "R2C5"), settles=True) for n, what in enumerate(t._CHECK_FIXES, start=1)]
    fixes += [Item(0, "fix", "parm_check", "parameters", count=2),
              Item(0, "fix", "panel_xcheck", "missing_part", sizes=((157.5, 447.0, 1), (304.8, 2438.4, 2))),
              Item(0, "fix", "panel_xcheck", "extra_part", sizes=((158.5, 447.8, 1),)),
              Item(0, "fix", "section_span", "other", ("R1C4",)), Item(0, "fix", "op_schema_check", "op_schema")]
    unverified = [Item(0, "could_not_verify", "outset_check", what, ("R1C7",), count=1)
                  for what in t._CHECK_UNVERIFIED]
    unverified += [Item(0, "could_not_verify", "wm_variant_check", "checker",
                        why="reads its product library from a path this PC doesn't have"),
                   Item(0, "could_not_verify", "", "not_read", why="Jim's crosscheck stopped with an error."),
                   Item(0, "advisory", "ic_code_check", "ic_code", count=3)]
    items = [*fixes, *unverified]
    title = f"{job} checked 9:42 PM (trial)"
    states = [t.check_state_words(state=state, at=at, now=at, trial=True, to_fix=2, unverified=1, settled=1,
                                  words=t.NO_TOOLKIT)
              for state in ("asked", "running", "waiting", "passed", "failed", "could_not_verify", "stale",
                            "not_run")]
    states += [t.check_state_words(state="passed", at=at, now=at), t.check_state_words(state="not_run", words="x y"),
               t.check_state_words(state="stale", at=at, now=at, audit=True)]
    lines = [t.check_item_words(item) for item in items] + [line for line in (
        t.check_chat_line(job, item) for item in items) if line]
    return [
        t.check_result(job, at=at, trial=True, audit=False, items=items, passed=False),
        t.check_result(job, at=at, trial=False, audit=True, items=(), passed=True),
        t.check_result(job, at=at, trial=False, audit=False, items=(), passed=False),
        t.close_prompt(job, closed_at=at, first=True),
        Message("check_lines", title, tuple(f"{n}. {t.check_item_line(job, item)}"
                                            for n, item in enumerate(items, start=1))),
        Message("check_lines", title, tuple(f"{n}. {line}" for n, line in enumerate((*lines, *states), start=1))),
        *(Message("check_line", "NG Runner", (f"1. {line}",)) for line in (
            t.check_your_list(job, to_fix=2, at=at, trial=True, now=at),
            f"{job}: {t.check_digest(at=at, trial=True, audit=False, to_fix=2, unverified=1, passed=False)}",
            f"{job}: {t.check_digest(at=at, trial=False, audit=True, to_fix=0, unverified=0, passed=True)}",
            t.close_unanswered(job, closed_at=at, now=at),
            f"{job}: {t.settled_line(at=at, words=t.SETTLE_LABELS['told_chat'], now=at)}")),
        *(Message("echo", "NG Runner", (line,)) for line in (
            t.check_asked(job), t.check_already_asked(job), t.settled_echo(job, n=1, how="told_chat"),
            t.settled_echo(job, n=2, how="not_a_problem"), t.mozaik_error_echo(job), t.not_yet_echo(job),
            f"{t.NO_TOOLKIT}.", t.NOT_A_JOB, t.SETTLE_GONE, t.SETTLE_STALE, t.SETTLE_NEVER, t.PROMPT_ANSWERED,
            t.PROMPT_NOT_A_CHOICE)),
    ]


def _function(module: str, name: str) -> Callable[..., object]:
    found = getattr(importlib.import_module(module), name, None)
    if not callable(found):
        raise LookupError(f"{module} has no {name}()")
    return found


def template_problems(samples: Callable[[], Iterable[object]],
                      lint: Callable[[object], list[str]]) -> list[str]:
    """Lint one rendered example of every template; each problem names the template's kind."""
    try:
        messages = list(samples())
    except Exception as exc:  # noqa: BLE001 - a template that can't render fails the self-test
        return [f"The message templates could not be rendered ({type(exc).__name__}: {exc})."]
    if not messages:
        return ["No message templates were found to check."]
    problems = []
    for message in messages:
        kind = getattr(message, "kind", type(message).__name__)
        try:
            found = lint(message)
        except Exception as exc:  # noqa: BLE001 - the lint failing on a template is a failure
            problems.append(f"Message template {kind} could not be linted"
                            f" ({type(exc).__name__}: {exc}).")
            continue
        problems += [f"Message template {kind}: {_sentence(text)}" for text in found]
    return problems


# ----- 5. the guards' readers, for real


_SMALL_PROGRAM = (b"G00 G17 G20 G40\r\nM06 T6\r\nG0 X1.0000 Y1.0000 Z1.5100\r\nG1 Z0.1300 F150.0\r\n"
                  b"G0 X1.0000 Y1.0000 Z1.5100\r\nG1 X2.0000 Y3.0000 F738.0\r\nM30\r\n%\r\n")
_SMALL_SHEET = (b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                b"<w:body><w:p><w:r><w:t>Job Name: Testerson_101426</w:t></w:r></w:p></w:body>"
                b"</w:document>")
_SMALL_SNAPSHOT = {
    "meta": "key,value\nsnapshot_taken,2026-10-30T23:51:34Z\n",
    "SheetGoods": 'Job,Material,Calc Sheets,Optimizer Sheets,$/Sheet,Material Cost ($),Sheet Status\n'
                  "TOTALS\n",
    "Edgeband": "Job,Material,Calc Lin Ft,Optimizer Lin Ft,$/LF,EB Cost ($),Status\nTOTALS\n",
    "JobSummary": "Job,Status\nTOTALS\n",
    "Pricing": "SHEET GOODS\nMaterial\n\u25b2 end\nEDGEBAND\nMaterial\n\u25b2 end\n",
}


def guard_reader_problems(work: Path) -> list[str]:
    """Each guard's reader once, on made-up files in work. Each must give what it should."""
    from ngrunner.guards import design
    from ngrunner.guards.facts import ToolRun
    from ngrunner.guards.labels import read_catalog, read_labels
    from ngrunner.guards.observer import mozaik_running, read_locks
    from ngrunner.guards.opt import read_opt
    from ngrunner.guards.preflight import read_job_sheet
    from ngrunner.guards.reads import read_shared
    from ngrunner.guards.tap import read_tap
    from ngrunner.guards.tracker import read_tracker

    work.mkdir(parents=True)
    checks: list[tuple[str, Callable[[], bool]]] = [
        ("a program", lambda: read_tap("TESS01.TAP", _SMALL_PROGRAM).reach == (2.0, 3.0)),
        ("a shared read", lambda: _shared_read(work, read_shared)),
        ("a job sheet", lambda: _job_sheet_name(work, read_job_sheet) == "Testerson_101426"),
        ("a labels file", lambda: read_labels(_small_pdf()) == ()),
        ("the parts catalog", lambda: read_catalog(b"Job,Part,Pieces,JobModified\n") == ()),
        ("a tracker snapshot", lambda: _snapshot_rows(work, read_tracker) == 0),
        ("a nest file", lambda: read_opt(_SMALL_NEST).physical_sheets == 1),
        ("a cutlist", lambda: design.read_cutlist(ToolRun(0, _SMALL_CUTLIST, "", False)).total == 1),
        ("a room's design", lambda: (
            [(p.tag, len(p.ops)) for p in design.read_design({"Room1.des": _SMALL_ROOM})] == [("R1C3", 1)]
            and bool(design.room_identity("Room1.des", _SMALL_ROOM)))),
        ("a lock file", lambda: _lock_owner(work, read_locks) == "TESTER"),
        ("the list of running programs", lambda: mozaik_running() is not None),
    ]
    problems = []
    for what, check in checks:
        try:
            if not check():
                problems.append(f"The guards read {what} wrongly in this build.")
        except Exception as exc:  # noqa: BLE001 - whatever stops a reader fails this build
            problems.append(f"The guards can't read {what} in this build ({_reason(exc)}).")
    return problems


def tool_run_problems(work: Path) -> list[str]:
    """A made-up toolkit copied and run for real, by this build itself. Only this shows what a real
    run depends on and importing can't: NGRunner.exe's run-tool, runpy, UTF-8 mode, and the Job Object
    and its kill. Each ran first on a real .exe here, which is where a module imported by name shows
    itself missing (the first real run, 2026-10-30)."""
    from ngrunner.clock import SystemClock
    from ngrunner.toolkit import run_tool, take_snapshot

    try:
        source = work / "source"
        source.mkdir(parents=True)
        (source / "say.py").write_text(_SMALL_TOOL, encoding="utf-8")
        (source / "hang.py").write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
        copy = take_snapshot(source, work / "copy", clock=SystemClock())
        said = run_tool(copy, "say.py", ["--a-flag"])
        hung = run_tool(copy, "hang.py", [], timeout_s=1)
    except Exception as exc:  # noqa: BLE001 - whatever stops a run fails this build
        return [f"A toolkit script can't be run in this build ({_reason(exc)})."]
    problems = []
    if said.exit_code != 3 or said.stdout.strip() != "--a-flag utf8=1 caf\u00e9":
        problems.append("A toolkit script ran wrongly in this build, so no check of it could be believed.")
    if not hung.timed_out:
        problems.append("A toolkit script that hung was not stopped in this build.")
    return problems


def _shared_read(work: Path, read_shared: Callable[[Path], bytes]) -> bool:
    path = work / "shared.txt"
    path.write_bytes(SANDBOX_CONTENT)
    return read_shared(path) == SANDBOX_CONTENT


def _job_sheet_name(work: Path, read_job_sheet: Callable[[Path], Any]) -> str:
    path = work / "sheet.docx"
    with zipfile.ZipFile(path, "w") as sheet:
        sheet.writestr("word/document.xml", _SMALL_SHEET)
    return read_job_sheet(path).name


def _lock_owner(work: Path, read_locks: Callable[..., Any]) -> str | None:
    folder = work / "job"
    folder.mkdir()
    (folder / "MozaikJob.LCK").write_bytes(b"TESTER\r\n")
    return read_locks(folder, now=datetime.now(timezone.utc)).lck_owner


def _small_pdf() -> bytes:
    """A real one-page PDF with no label on it, so pypdf's text extraction runs to its end."""
    from pypdf import PdfWriter

    out = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(out)
    return out.getvalue()


def _snapshot_rows(work: Path, read_tracker: Callable[[Path], Any]) -> int:
    folder = work / "tracker"
    folder.mkdir()
    for tab, text in _SMALL_SNAPSHOT.items():
        (folder / f"tracker_snapshot_{tab}.csv").write_text(text, encoding="utf-8")
    return len(read_tracker(folder).sheets)


# ----- wording


def _reason(exc: BaseException) -> str:
    text = str(exc).strip().rstrip(".")
    return text or type(exc).__name__


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."
