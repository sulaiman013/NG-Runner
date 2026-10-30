r"""
The post-install self-test: can this build, on this PC, do NG Runner's work safely?

Why: the launcher starts only a version whose self-test passed (spec 6.7), so a build with a
missing library, a migration that would damage the database, a chokepoint that lets a
forbidden write through, or a message that breaks Jim's format is caught at install, by
Sulaiman, instead of overnight on Jim's PC. The installer runs it as
NGRunner.exe --self-test --record; the exit code is 0 only when run_self_test() finds nothing.

It checks four things, each reported in plain words:
1. Every P1a module and library imports (in a build, a module PyInstaller missed fails here).
2. The database migration runs, twice, on a copy of the live database taken with SQLite's
   online backup (a new, empty database on a fresh install). The live database is only read.
3. The 20 scripted forbidden writes (the P1a exit test, design 3.2) are each refused by the
   real commit_write, in a sandbox of folders made for the purpose, and leave no file behind.
   Two allowed writes must succeed there first: a sandbox that refuses everything (a folder
   it can't reach, say) would refuse the 20 for the wrong reason and prove nothing.
4. Every message template passes the message lint.

Everything it makes is inside NG Runner's own data folder (staging\selftest-<random>), and
removed again at the end, by remove_own_tree, which never follows a link.
"""
from __future__ import annotations

import hashlib
import importlib
import itertools
import secrets
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PureWindowsPath

from ngrunner.backup import BackupError, DatabaseFacts, describe, remove_own_tree, snapshot
from ngrunner.config import AppPaths
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
    "ngrunner.selftest",
    "flask", "waitress", "requests", "pystray", "PIL", "psutil", "win32api", "pywintypes",
    "windows_toasts", "cryptography", "cryptography.hazmat.primitives.ciphers.aead", "pypdf",
    "tzdata", "sqlite3",
)

SANDBOX_CONTENT = b"NG Runner self-test: safe to delete\n"

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
) -> list[str]:
    """Every problem found, one plain sentence each; empty means this build may run. migrate
    and lint default to the store's and the lint module's own, samples to message_samples (tests
    fake them)."""
    problems = import_problems(modules)
    if getattr(sys, "frozen", False) and not sys.flags.utf8_mode:
        problems.append("This build does not run in UTF-8 mode (ngrunner.spec must pass X utf8).")
    work = _new_work_folder(paths)
    try:
        problems += _migration(paths, work / "database", migrate)
        problems += forbidden_write_problems(work / "writes")
    finally:
        left = remove_own_tree(work, inside=paths.staging)
        if left:
            problems.append(f"The self-test could not clean up after itself: {left}")
    problems += _templates(samples, lint)
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
    )]
    echoes = [t.echo(parse_command(typed, cards)) for typed in (
        f"{cards[2].id} 2", "pause", "resume", f"add job {job}", "add job 84213")]
    return [
        t.digest(day, for_you=(t.install_soon(job, install), t.stuck(other), *t.ALARMS.values()),
                 jobs=((other, "filed 10/29 3:05 PM"),), cards=[(card, 3) for card in cards],
                 open_items=len(cards), oldest_days=3),
        t.quiet_digest(day),
        t.quiet_digest(day, open_items=2, oldest_days=3),
        t.filed(job, row="Testerson", designer="PE", install=install),
        t.filed(job, row="Testerson garage", garage=True),
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


# ----- wording


def _reason(exc: BaseException) -> str:
    text = str(exc).strip().rstrip(".")
    return text or type(exc).__name__


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."
