r"""
The write chokepoint. commit_write() is the only code that writes outside NG Runner's own
data folder (%LOCALAPPDATA%\NGRunner): into NG Jobs, the control folder and, in later
phases, Mozaik Shared. Design section 3.2; AGENTS.md rules 1 and 2.

Why one door: those folders hold work NG Runner must never damage (Mozaik jobs and
libraries, Jim's toolkit, chat outputs, g-code) and are shared with Jim's chat sessions,
other PCs and two sync clients. One small module enforces, and one test file proves, every
write rule at once. A commit guarantees:

- Where it writes. Each root is resolved once (a root may itself be a link), then known by
  identity (volume serial and file ID), never by spelling: a target is inside a root when a
  folder on its way up is that very folder, and the rules judge the names below it. Below a
  root no junction, symlink or mount point is ever followed, not even one into another root
  (roots never overlap, so a path that meets two went through a link); OneDrive's cloud
  placeholders are not links and stay allowed. Refused spellings: relative paths, the
  \\.\ device namespace, 8.3 short names, trailing dots and spaces, device names and streams.
- What it writes. Some places are refused in every phase; each phase then allows only its
  own list (P1a: NG Runner's own files in NG Jobs\<Job>\, NG Jobs\_NG Runner\ and the
  control folder). A file is overwritten only while it still holds what NG Runner last
  wrote there (owned()) and what the caller last read (expected_hash). Content is copied
  once, when the commit is planned, so a buffer the caller changes meanwhile changes nothing.
- When it writes. NG Runner's data folder and its record of its own files are checked
  first. The Mozaik interlock is asked next, when the write is planned, and again once the
  temp files are written. Every folder on the way is pinned by identity when planned and
  checked again before staging, after staging, after that last interlock call, and before
  and after each rename. Any change refuses the commit, or rolls it back.
- All or nothing. Replaced files are backed up first, to app_data\backup\<UTC time>\. Temp
  files (<name>.<random>.ngr-tmp) are written next to their targets, then all renamed in,
  then all read back. Each backup and temp file is created exclusively, never through a
  link at its name. A new file goes in with a rename that fails if a file appeared
  meanwhile; an existing one only while it still holds what was checked. A failure puts
  back what was already renamed in; so does Ctrl+C or an exit, which then goes on.
- No delete. The only things ever removed are this commit's own temp files and the empty
  folders it made (each checked by identity first) and, in a rollback, a file it renamed in
  moments before, while that is the very file and it still holds its bytes. Anything else
  is left alone and named in the reason.

Threat model: this guards against bugs in NG Runner and against accidents by the people and
programs that share these folders (a sync client or chat session moving a folder, a link
left behind, a file saved mid-commit). It does not stop a process of the same user that
races it on purpose in the microseconds between a final check and a rename: such a process
can write anywhere the user can, with or without NG Runner.
"""
from __future__ import annotations

import _winapi
import hashlib
import json
import msvcrt
import ntpath
import os
import re
import secrets
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

_TMP_SUFFIX = ".ngr-tmp"
_ROOT_NAMES = ("ng_jobs", "mozaik_shared", "control", "app_data")
_LABELS = {
    "ng_jobs": "NG Jobs",
    "mozaik_shared": "Mozaik Shared",
    "control": "the control folder",
    "app_data": "NG Runner's data folder",
}
_REGISTRY_NAME = "owned_files.json"
_BACKUP_FOLDER = "backup"
_LONG_PREFIX, _LONG_UNC_PREFIX = "\\\\?\\", "\\\\?\\UNC\\"
_DEVICE_PREFIX = "\\\\.\\"  # pathlib spells //./ this way too
_NAME_SURROGATE = 0x20000000  # reparse-tag bit of junctions, symlinks and mount points
# CreateFile values for a new file that never goes through a link at its own name. _winapi
# lacks the last three (its CREATE_NEW_CONSOLE is a process flag, not CREATE_NEW).
_GENERIC_WRITE = _winapi.GENERIC_WRITE
_CREATE_NEW = 1  # fail if anything at all is at the name
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000  # a link at the name is opened as itself
_FILE_ATTRIBUTE_NORMAL = 0x80
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_COMMIT_LOCK = threading.Lock()
_IN_COMMIT = threading.local()  # .active while this thread runs a commit and its callbacks

# Refused in every phase, wherever they appear below a root (compared case-folded).
_PROTECTED_FOLDERS = ("data", "product libraries", "_ng_automation", "regression")
_PROTECTED_WORD = "template"  # any folder named like it: templates\, template jobs
_INSTALLER_NAMES = ("index.html", "parts_catalog.csv")  # the Installer App serves any such file
_CHAT_SUFFIXES = ("_report.txt", "_actions.json", "_crosscheck.txt")
_MOZAIK_SUFFIXES = (".tap", ".lck", ".uns")

# P1a: NG Runner's own files in a job folder, its settled-list folder, and the control folder.
_SETTLED_FOLDER = "_NG Runner"
_LEASE_NAME = "_NG Runner is handling this job.txt"
_JOB_FILE_SUFFIXES = ("_summary.pdf", "_NGRunner.json", "_NGRunner_log.txt")
_IC_CONVERSION_SUFFIXES = ("_ic_conversion.json", "_ic_conversion.xlsx")  # allowed from P3
_JOB_DATE = re.compile(r"[0-9]{6}")  # a job folder carries its MMDDYY date (\d is any script)

# Windows names that the OS silently changes, or treats as devices or streams.
_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"{port}{n}" for port in ("com", "lpt") for n in "0123456789\u00b9\u00b2\u00b3"}
)
_BAD_CHARS = frozenset('<>:"/\\|?*') | frozenset(map(chr, range(32)))


# ----- public types


@dataclass(frozen=True)
class Roots:
    """The folders commit_write knows about. Each must be a full path with a drive."""

    ng_jobs: Path
    mozaik_shared: Path
    control: Path
    app_data: Path


@dataclass(frozen=True)
class FileWrite:
    """One file to write. expected_hash: None = must not exist yet; else sha256 hex of it now."""

    path: Path
    content: bytes
    expected_hash: str | None


@dataclass(frozen=True)
class CommitResult:
    """ok: every file written. written: files now holding the new content (after a refusal,
    only files a rollback had to leave changed). backups: copies of what was replaced.
    reason: why not, in plain words, when not ok."""

    ok: bool
    written: tuple[Path, ...] = ()
    backups: tuple[Path, ...] = ()
    reason: str | None = None


# ----- private types

_Rules = Callable[[str, tuple[str, ...]], str | None]  # (root, parts below it) -> why not
_Identity = tuple[int, int]  # (volume serial, file ID): what a file is, however it is spelled


@dataclass(frozen=True)
class _Root:
    """A root as this commit found it: resolved once, then known by identity."""

    name: str
    path: Path
    identity: _Identity | None  # None: not reachable now (a drive or sync client is down)


@dataclass(frozen=True)
class _Pin:
    """A folder as this commit checked it. The root is followed, since it may be a link; every
    folder below it must stay a real folder with the same identity."""

    path: Path
    identity: _Identity
    root: bool = False


@dataclass(frozen=True)
class _Temp:
    """A file this commit created, known by identity so that only it is ever removed."""

    path: Path
    identity: _Identity


@dataclass
class _Target:
    """One planned write: where it lands, the folders it goes through, and what it replaces."""

    write: FileWrite  # its content copied when planned: only this copy is used from then on
    path: Path  # the root as resolved, then the names below it: what was checked is written
    where: str  # which root it is in
    rel: tuple[str, ...]  # its names below that root
    pins: list[_Pin]  # the root, then each folder down to the target's own folder
    missing: list[Path]  # folders still to make, outermost first
    tmp: _Temp | None = None  # its staged temp file
    old: bytes | None = None  # content found on disk; None = a new file
    backup: Path | None = None
    attempted: bool = False  # its rename was started


# ----- the chokepoint


def commit_write(
    writes: list[FileWrite],
    *,
    roots: Roots,
    interlock: Callable[[list[Path]], str | None],
    phase: str = "P1a",
    owned: Callable[[Path], str | None] | None = None,
) -> CommitResult:
    """Write every file or none. Every problem comes back as ok=False with a reason in plain
    words; only Ctrl+C or an exit (KeyboardInterrupt, SystemExit) goes on through, and only
    once the files are back as they were.

    interlock (required, so forgetting it fails closed): interlock(paths) returns None when
    writing is safe, else why not. It is asked when the write is planned and again once the
    temp files are written, just before the renames. owned(path) returns the sha256 NG
    Runner last wrote at path (default: its registry in app_data); only such files are
    overwritten. A callback that starts a commit of its own is refused. A commit from
    another thread waits for this one to finish, so a callback must never wait for another
    thread's commit either (it would wait forever).
    """
    if getattr(_IN_COMMIT, "active", False):  # a callback of this thread's own commit
        return _refused(
            "A commit was started from inside another commit's safety check or ownership"
            " lookup; commits run one at a time, so it was refused and nothing was written."
        )
    _IN_COMMIT.active = True
    try:
        with _COMMIT_LOCK:  # one commit at a time in this process; other threads wait
            try:
                return _commit(list(writes), roots, phase, interlock, owned)
            except Exception as exc:  # noqa: BLE001 - last resort: no error escapes the chokepoint
                return _refused(f"Unexpected problem ({type(exc).__name__}); nothing was written.")
    finally:
        _IN_COMMIT.active = False


def _commit(
    writes: list[FileWrite],
    roots: Roots,
    phase: str,
    interlock: Callable[[list[Path]], str | None],
    owned: Callable[[Path], str | None] | None,
) -> CommitResult:
    phase_rules = _PHASE_RULES.get(phase)
    if phase_rules is None:
        return _refused(f"Writes for phase {phase} are not built yet; only P1a is.")
    if not callable(interlock):
        return _refused("No Mozaik safety check was given, so nothing was written.")
    found = _load_roots(roots)
    if isinstance(found, str):
        return _refused(found)
    plan = _plan(writes, found, phase_rules)
    if isinstance(plan, str):
        return _refused(plan)
    app = found["app_data"]
    registry = _Registry(app)
    owned = owned or registry.owned
    reason = (
        _data_folder_problem(app, registry)
        or _duplicate(plan)
        or _ask(interlock, plan)
        or _first_problem(plan, owned)
    )
    if reason:
        return _refused(reason)
    created: list[_Pin] = []  # folders this commit made, outermost first
    try:
        reason = _stage_and_check_again(plan, created, interlock, owned) or _back_up(plan, app)
    except Exception as exc:  # noqa: BLE001 - nothing is renamed yet: clean up, then refuse
        reason = f"Unexpected problem ({type(exc).__name__}); nothing was written."
    except BaseException:  # Ctrl+C or an exit: the process is stopping, so clean up first
        _clean_up(plan, created)
        raise
    if reason:
        _clean_up(plan, created)
        return _refused(reason)
    backups = tuple(t.backup for t in plan if t.backup is not None)
    try:
        _rename_all(plan)
        _read_back_all(plan)
        _record(registry, plan)
    except _Failed as failed:
        return _roll_back(plan, created, str(failed), backups)
    except Exception as exc:  # noqa: BLE001 - after the first rename, any failure must roll back
        return _roll_back(plan, created, f"Unexpected problem ({type(exc).__name__})", backups)
    except BaseException:  # Ctrl+C or an exit: put the files back as they were, then stop
        _roll_back(plan, created, "NG Runner was stopped", backups)
        raise
    return CommitResult(ok=True, written=tuple(t.path for t in plan), backups=backups)


def _refused(reason: str) -> CommitResult:
    return CommitResult(ok=False, reason=reason)


def _plan(writes: list[FileWrite], roots: dict[str, _Root], rules: _Rules) -> list[_Target] | str:
    reason = _check_inputs(writes)
    if reason:
        return reason
    plan = []
    for w in writes:
        # Content is copied once, here: a buffer the caller changes mid-commit can't change
        # what is staged, read back, restored or recorded.
        placed = _place(FileWrite(w.path, bytes(w.content), w.expected_hash), roots, rules)
        if isinstance(placed, str):
            return placed
        plan.append(placed)
    return plan


def _check_inputs(writes: list[FileWrite]) -> str | None:
    if not writes:
        return "Nothing to write."
    for w in writes:
        name = Path(w.path).name
        if not isinstance(w.content, bytes | bytearray):
            return f"{name} has no content to write; NG Runner never deletes files."
        if w.expected_hash is not None and not _SHA256.fullmatch(str(w.expected_hash)):
            return f"{name}: the expected hash must be a sha256 (64 hex characters) or None."
    return None


def _duplicate(plan: list[_Target]) -> str | None:
    seen: set[str] = set()
    for t in plan:
        if _key(t.path) in seen:
            return f"{t.path.name} appears twice in one commit."
        seen.add(_key(t.path))
    return None


def _stage_and_check_again(
    plan: list[_Target],
    created: list[_Pin],
    interlock: Callable[[list[Path]], str | None],
    owned: Callable[[Path], str | None],
) -> str | None:
    """Write the temp files, then ask the interlock again and check every folder and file
    again: the interlock can take minutes."""
    return (
        _stage(plan, created)
        or _all_in_place(plan)
        or _ask(interlock, plan)
        or _all_in_place(plan)
        or _first_problem(plan, owned)
    )


def _ask(interlock: Callable[[list[Path]], str | None], plan: list[_Target]) -> str | None:
    try:
        answer = interlock([t.path for t in plan])
    except Exception as exc:  # noqa: BLE001 - a safety check that cannot run means wait
        return f"The safety check could not run ({_why(exc)}), so nothing was written."
    return None if answer is None else f"Not written: {answer}"


def _first_problem(plan: list[_Target], owned: Callable[[Path], str | None]) -> str | None:
    for t in plan:
        reason = _check_on_disk(t, owned)
        if reason:
            return reason
    return None


def _all_in_place(plan: list[_Target]) -> str | None:
    for t in plan:
        moved = _moved_folder(t.pins)
        if moved:
            return f"{moved}, so nothing was written."
    return None


def _why(exc: BaseException) -> str:
    return getattr(exc, "strerror", None) or str(exc) or type(exc).__name__


# ----- identities: what a folder or file is, whatever its path is spelled like


def _identity(st: os.stat_result) -> _Identity | None:
    """None when the drive gives no file ID, so nothing there can be told apart safely."""
    return (st.st_dev, st.st_ino) if st.st_dev and st.st_ino else None


def _identity_at(path: Path) -> _Identity | None:
    """The identity of what path leads to (links followed), or None if there is nothing."""
    try:
        return _identity(os.stat(path))
    except OSError:
        return None


def _is_link(st: os.stat_result) -> bool:
    """A junction, symlink or mount point: a name that leads somewhere else. OneDrive's
    cloud placeholders are reparse points too, but not links, so they stay allowed."""
    if stat.S_ISLNK(st.st_mode):
        return True
    if not getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    tag = getattr(st, "st_reparse_tag", 0)
    return not tag or bool(tag & _NAME_SURROGATE)  # a tag that can't be read counts as a link


def _is_plain_file(st: os.stat_result) -> bool:
    return stat.S_ISREG(st.st_mode) and not _is_link(st)


def _moved_folder(pins: list[_Pin]) -> str | None:
    """The first pinned folder that is no longer what was checked, in plain words."""
    for pin in pins:
        try:
            st = os.stat(pin.path) if pin.root else os.lstat(pin.path)
        except OSError:
            return f"{pin.path.name} was moved or removed while NG Runner was writing"
        if not pin.root and _is_link(st):
            return f"{pin.path.name} was replaced by a link or junction while NG Runner was writing"
        if _identity(st) != pin.identity:
            return f"{pin.path.name} was replaced by another folder while NG Runner was writing"
    return None


# ----- roots: resolved once, then known by identity


def _load_roots(roots: Roots) -> dict[str, _Root] | str:
    found: dict[str, _Root] = {}
    for name in _ROOT_NAMES:
        raw = str(Path(getattr(roots, name)))
        if not _is_full_path(raw):
            return (
                f"The setting for {_LABELS[name]} is not a full path with a drive ({raw}),"
                " so nothing was written. Check NG Runner's folder settings."
            )
        root = _load_root(name, _resolve(raw))
        if isinstance(root, str):
            return root
        found[name] = root
    reason = _overlap(found)
    return reason if reason else found


def _load_root(name: str, path: Path) -> _Root | str:
    try:
        st = os.stat(path)
    except OSError:
        return _Root(name, path, None)  # writes into it are refused as "not reachable"
    if not stat.S_ISDIR(st.st_mode):
        return f"{_LABELS[name]} is set to a file, not a folder. Check NG Runner's folder settings."
    if _identity(st) is None:
        return (
            f"NG Runner can't tell folders apart in {_LABELS[name]} (its drive gives them no"
            " IDs), so nothing was written."
        )
    return _Root(name, path, _identity(st))


def _resolve(path: Path | str) -> Path:
    """Absolute, with links, junctions and '..' followed, and no long-path prefix."""
    return Path(_strip_long_prefix(os.path.realpath(_strip_long_prefix(str(Path(path))))))


def _strip_long_prefix(raw: str) -> str:
    if raw[: len(_LONG_UNC_PREFIX)].upper() == _LONG_UNC_PREFIX:
        return "\\\\" + raw[len(_LONG_UNC_PREFIX) :]
    return raw.removeprefix(_LONG_PREFIX)


def _is_full_path(raw: str) -> bool:
    """A drive (or a UNC share) and a root, never the \\\\.\\ device namespace."""
    if raw.startswith(_DEVICE_PREFIX):
        return False
    drive, root, _ = ntpath.splitroot(_strip_long_prefix(raw))
    return bool(drive and root)


def _overlap(roots: dict[str, _Root]) -> str | None:
    names = list(roots)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            if _within(roots[a], roots[b]) or _within(roots[b], roots[a]):
                return (
                    f"{_LABELS[a]} and {_LABELS[b]} overlap, so NG Runner can't tell which"
                    " rules apply. Check its folder settings."
                )
    return None


def _within(inner: _Root, outer: _Root) -> bool:
    """inner is outer or inside it: by name (which also catches roots not there now) or by
    identity (which catches other spellings of one folder). Names only ever add refusals."""
    a, b = _folded(outer.path), _folded(inner.path)
    if b[: len(a)] == a:
        return True
    if inner.identity is None or outer.identity is None:
        return False
    return any(_identity_at(p) == outer.identity for p in (inner.path, *inner.path.parents))


def _folded(path: Path) -> tuple[str, ...]:
    return tuple(part.casefold() for part in path.parts)


# ----- where a write lands


def _place(w: FileWrite, roots: dict[str, _Root], phase_rules: _Rules) -> _Target | str:
    """Find the target's root by identity and check every rule, or say why it can't be written."""
    raw = _strip_long_prefix(str(Path(w.path)))
    if not _is_full_path(raw):
        return f"{Path(raw).name}: only full paths (with a drive) are accepted."
    reason = _names_problem(Path(raw).parts[1:])  # before Windows drops a trailing dot
    if reason:
        return reason
    path = Path(os.path.abspath(raw))  # '..' and '.' resolved the way Windows opens the path
    found = _find_root(path, roots)
    if found is None:
        return _outside(path, roots)
    if isinstance(found, str):
        return found
    root, rel = found
    if not rel:
        return f"{path.name} is one of NG Runner's own root folders, not a file."
    if root.name == "app_data":
        return f"{path.name} is in NG Runner's own data folder, which it writes directly."
    reason = _refused_always(root.name, rel) or phase_rules(root.name, rel)
    return reason if reason else _pin_target(w, root, rel)


def _find_root(path: Path, roots: dict[str, _Root]) -> tuple[_Root, tuple[str, ...]] | str | None:
    """The folder on path's way up that is a root by identity, and the names below it (None:
    no root). Roots never overlap, so a second root higher up means the first was reached
    through a link below that one, which is refused: no link below a root is followed."""
    by_identity = {r.identity: r for r in roots.values() if r.identity}
    found: tuple[_Root, tuple[str, ...]] | None = None
    for depth, folder in enumerate((path, *path.parents)):
        root = by_identity.get(_identity_at(folder))
        if root and found:
            return _through_a_link(path, outer=root, inner=found[0])
        if root:
            found = root, path.parts[len(path.parts) - depth :]
    return found


def _through_a_link(path: Path, outer: _Root, inner: _Root) -> str:
    leads = "back into it" if inner.name == outer.name else f"to {_LABELS[inner.name]}"
    return (
        f"{path.name} goes through a link or junction inside {_LABELS[outer.name]} that leads"
        f" {leads}, so it was not written."
    )


def _outside(path: Path, roots: dict[str, _Root]) -> str:
    """Why a path in no root is refused. A root that is not there gets its own words, chosen
    by name: there is no identity to go by, and the words only pick which refusal to give."""
    for root in roots.values():
        if root.identity is None and _folded(path)[: len(_folded(root.path))] == _folded(root.path):
            return f"{path.name} was not written: {_LABELS[root.name]} is not reachable right now."
    return f"{path.name} is outside the folders NG Runner may write to."


def _pin_target(w: FileWrite, root: _Root, rel: tuple[str, ...]) -> _Target | str:
    """Pin the root and each existing folder below it. No link may sit below the root, every
    identity must be readable, and names must be spelled in full."""
    pins = [_Pin(root.path, root.identity, root=True)]
    here, existing = root.path, 0
    for i, part in enumerate(rel):
        here = here / part
        try:
            st = os.lstat(here)
        except FileNotFoundError:
            break  # this part and everything below it are new
        except OSError as exc:
            return f"{rel[-1]}: {part} could not be checked ({_why(exc)}), so nothing was written."
        is_target = i == len(rel) - 1
        reason = _entry_problem(here, st, rel[-1], is_target)
        if reason:
            return reason
        if not is_target:
            pins.append(_Pin(here, _identity(st)))
        existing = i + 1
    reason = _short_name(root.path, rel[:existing])
    if reason:
        return reason
    path = root.path.joinpath(*rel)
    missing = [root.path.joinpath(*rel[:n]) for n in range(existing + 1, len(rel))]
    return _Target(w, path, root.name, rel, pins, missing)


def _entry_problem(path: Path, st: os.stat_result, name: str, is_target: bool) -> str | None:
    if _is_link(st):
        if is_target:
            return f"{name} is a link or junction, so NG Runner will not write through it."
        return f"{name} goes through a link or junction ({path.name}), so it was not written."
    if _identity(st) is None:
        return (
            f"NG Runner can't tell where {path.name} is (its drive gives no ID),"
            f" so {name} was not written."
        )
    if not is_target and not stat.S_ISDIR(st.st_mode):
        return f"{path.name} is a file, so {name} can't be written inside it."
    return None


def _short_name(folder: Path, names: tuple[str, ...]) -> str | None:
    """Refuse 8.3 short names (PRODUC~1): the rules must judge the names on disk. Google
    Drive has none and gives names back as typed, so case variants pass there."""
    if not names:
        return None
    on_disk = Path(os.path.realpath(folder.joinpath(*names))).parts[-len(names) :]
    if len(on_disk) != len(names):
        return f"{names[-1]} could not be checked, so nothing was written."
    for typed, real in zip(names, on_disk):
        if typed.casefold() != real.casefold():
            return f"{typed} is a short name for {real}; NG Runner only writes full names."
    return None


def _names_problem(parts: tuple[str, ...]) -> str | None:
    for part in parts:
        if part in (".", ".."):
            continue  # navigation; where it leads is checked after resolving
        if part[-1] in ". ":
            problem = "ends in a dot or a space"
        elif not _BAD_CHARS.isdisjoint(part):
            problem = "has a character Windows does not allow in names"
        elif part.split(".")[0].rstrip(" ").casefold() in _DEVICE_NAMES:
            problem = "is a reserved Windows device name"
        else:
            continue
        return f"'{part}' {problem}, so NG Runner will not write it."
    return None


# ----- the path rules


def _refused_always(where: str, rel: tuple[str, ...]) -> str | None:
    """Refusals that no phase lifts."""
    name, folded = rel[-1], [part.casefold() for part in rel]
    for folder, low in zip(rel[:-1], folded[:-1]):
        if low in _PROTECTED_FOLDERS or _PROTECTED_WORD in low:
            return (
                f"{name} is inside {folder}, which NG Runner never writes (Mozaik libraries,"
                " templates, Jim's toolkit and its regression fixtures)."
            )
    low = folded[-1]
    if low in _INSTALLER_NAMES:
        return f"{name}: the Installer App serves any file so named, so NG Runner never makes one."
    if low.endswith(_CHAT_SUFFIXES):
        return f"{name} is named like a chat output; those belong to Jim's chat sessions."
    if low.endswith(_MOZAIK_SUFFIXES):
        return f"{name} is one of Mozaik's own program or lock files; NG Runner never writes those."
    if low.endswith(_TMP_SUFFIX):
        return f"{name}: names ending in {_TMP_SUFFIX} are kept for NG Runner's temporary files."
    if where == "ng_jobs" and len(rel) > 2 and folded[0] != _SETTLED_FOLDER.casefold():
        return (
            f"{name} is in a subfolder of {rel[0]} (material and TAP folders);"
            " NG Runner never writes there."
        )
    return None


def _p1a_rules(where: str, rel: tuple[str, ...]) -> str | None:
    """P1a: new files only in NG Runner's own places (overwrites are limited by owned())."""
    name = rel[-1]
    if name.casefold().endswith(_IC_CONVERSION_SUFFIXES):
        return f"{name}: NG Runner writes IC Conversions only from P3."
    if where == "mozaik_shared":
        return f"{name} is in Mozaik Shared, which NG Runner does not write in P1a."
    if where == "control" or (len(rel) > 1 and rel[0].casefold() == _SETTLED_FOLDER.casefold()):
        return None
    if len(rel) == 1:
        return (
            f"{name} is directly in NG Jobs; NG Runner writes only inside a job folder"
            f" or {_SETTLED_FOLDER}."
        )
    job = rel[0]
    if not _JOB_DATE.search(job):
        return f"{job} does not look like a job folder (no MMDDYY date in its name)."
    own_names = [job + suffix for suffix in _JOB_FILE_SUFFIXES] + [_LEASE_NAME]
    if name.casefold() not in {n.casefold() for n in own_names}:
        return (
            f"{name}: in a job folder NG Runner only writes <Job>_summary.pdf,"
            " <Job>_NGRunner.json, <Job>_NGRunner_log.txt and the lease file."
        )
    return None


_PHASE_RULES: dict[str, _Rules] = {"P1a": _p1a_rules}


# ----- what the target holds now


def _check_on_disk(t: _Target, owned: Callable[[Path], str | None]) -> str | None:
    """Expected hash first (the caller's view must be current), then ownership."""
    name = t.path.name
    reason = _read_current(t)
    if reason:
        return reason
    expected = t.write.expected_hash
    if t.old is None:
        return None if expected is None else f"{name} is gone; the write expected to replace it."
    current = _sha(t.old)
    if expected is None:
        return f"{name} already exists, but the write expected to create it."
    if current != expected.lower():
        return f"{name} has changed since it was read, so it was not overwritten."
    try:
        ours = owned(t.path)
    except Exception as exc:  # noqa: BLE001 - a lookup that fails means "not known to be ours"
        return f"NG Runner could not check who wrote {name} ({_why(exc)}), so it was not changed."
    if ours != current:
        return (
            f"{name} is not NG Runner's own file (or someone changed it after NG Runner"
            " wrote it), so it was not overwritten."
        )
    return None


def _read_current(t: _Target) -> str | None:
    """Read what the target holds now into t.old (None: no file), never through a link."""
    name = t.path.name
    try:
        st = os.lstat(t.path)
    except FileNotFoundError:
        t.old = None
        return None
    except OSError as exc:
        return f"{name} could not be checked ({_why(exc)}), so it was not written."
    if _is_link(st):
        return f"{name} is a link or junction, so NG Runner will not write through it."
    if stat.S_ISDIR(st.st_mode):
        return f"{name} is a folder, not a file."
    try:
        t.old = t.path.read_bytes()
    except OSError as exc:
        return f"{name} could not be read ({_why(exc)}), so it was not overwritten."
    return None


# ----- staging: folders and temp files


class _NotSafe(OSError):
    """A folder or file is not what it must be (a link, a file in a folder's place, no ID)."""


def _stage(plan: list[_Target], created: list[_Pin]) -> str | None:
    """Make missing folders below the root and write every temp file next to its target,
    each only once its folders are checked again."""
    for t in plan:
        moved = _moved_folder(t.pins)
        if moved:
            return f"{moved}, so nothing was written."
        try:
            for folder in t.missing:
                t.pins.append(_make_folder(folder, created))
            t.tmp = _write_temp(t.path, t.write.content)
        except OSError as exc:
            return f"{t.path.name} could not be prepared ({_why(exc)}), so nothing was written."
    return None


def _make_folder(folder: Path, created: list[_Pin]) -> _Pin:
    """Make one folder (never through exist_ok) and pin it. One that appeared since planning,
    or that this commit made for another file, is used only if it is a real folder too."""
    try:
        os.mkdir(folder)
        made = True
    except FileExistsError:
        made = False
    pin = _pin_folder(folder)
    if made:
        created.append(pin)
    return pin


def _pin_folder(folder: Path) -> _Pin:
    """Pin a folder that must be real: not a link, not a file, with an identity."""
    st = os.lstat(folder)
    if _is_link(st):
        raise _NotSafe(f"{folder.name} is a link or junction")
    if not stat.S_ISDIR(st.st_mode):
        raise _NotSafe(f"{folder.name} is a file, not a folder")
    identity = _identity(st)
    if identity is None:
        raise _NotSafe(f"{folder.name} has no ID on its drive")
    return _Pin(folder, identity)


def _write_temp(next_to: Path, content: bytes) -> _Temp:
    """Write content to a new temp file next to next_to, under a name no one else uses
    (<name>.<random>.ngr-tmp, created exclusively, so nothing already there is ever opened,
    followed or removed). If the write fails, that file is removed again."""
    for _ in range(3):  # a taken name is someone's leftover: pick another, never touch it
        path = next_to.with_name(f"{next_to.name}.{secrets.token_hex(4)}{_TMP_SUFFIX}")
        try:
            return _write_new(path, content)
        except FileExistsError:
            continue
    raise _NotSafe(f"no free temp name next to {next_to.name}")


def _write_new(path: Path, content: bytes) -> _Temp:
    """Write content to a new file at path and flush it to disk. Its name must show the very
    file just made (a plain file with the ID of the open one) before anything is written
    into it. If it doesn't, or the write fails, the file is removed again while it is
    provably that file, and otherwise left alone."""
    with _create_new(path) as f:
        identity = _identity(os.fstat(f.fileno()))
        if identity is None:
            raise _NotSafe(f"{path.name}'s drive gives files no ID")
        new = _Temp(path, identity)
        try:
            if not _is_own_file(new):
                raise _NotSafe(f"NG Runner could not confirm that {path.name} is the file it made")
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:  # a failed write, or Ctrl+C: the caller never learns of this file
            f.close()
            _remove_own_file(new)
            raise
    return new


def _create_new(path: Path) -> BinaryIO:
    """Open a new file at path for writing, never through a link at that name. os.open with
    O_CREAT | O_EXCL follows a symlink that leads nowhere and creates what it leads to, so
    CreateFile is asked directly: CREATE_NEW, with a link at the name opened as itself.
    Anything already there is refused, a file or file link with FileExistsError, a folder,
    junction or folder link with PermissionError (both checked on Windows 11)."""
    handle = _winapi.CreateFile(
        str(path), _GENERIC_WRITE, 0, _winapi.NULL, _CREATE_NEW,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_ATTRIBUTE_NORMAL, _winapi.NULL,
    )
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
    except BaseException:
        _winapi.CloseHandle(handle)
        raise
    return open(fd, "wb")


def _replace_with(path: Path, content: bytes) -> None:
    """Replace path with content through a temp file of its own, removed if that fails."""
    temp = _write_temp(path, content)
    try:
        os.replace(temp.path, path)
    finally:
        _remove_own_file(temp)  # still there only if the replace failed


def _is_own_file(temp: _Temp) -> bool:
    """temp is still the very file this commit created: a plain file with its identity."""
    try:
        st = os.lstat(temp.path)
    except OSError:
        return False
    return _is_plain_file(st) and _identity(st) == temp.identity


def _remove_own_file(temp: _Temp) -> None:
    """Remove a file this commit created, only while that very file is still there."""
    try:
        if _is_own_file(temp):
            os.unlink(temp.path)
    except OSError:
        pass  # in use, or gone already: either way nothing that is not ours is touched


def _remove_own_folder(pin: _Pin) -> None:
    """Remove a folder this commit made, only while it is still that folder and empty."""
    try:
        st = os.lstat(pin.path)
        if not _is_link(st) and _identity(st) == pin.identity:
            os.rmdir(pin.path)
    except OSError:
        pass  # gone, or not empty any more, so it is not only ours: leave it


def _clean_up(plan: list[_Target], created: list[_Pin]) -> None:
    """Remove this commit's own temp files and the (still empty) folders it made."""
    for t in plan:
        if t.tmp is not None:
            _remove_own_file(t.tmp)
    for pin in reversed(created):
        _remove_own_folder(pin)


# ----- renames, read-back and the record of what was written


class _Failed(Exception):
    """A step after the first rename failed; the message says which file and why."""


def _rename_all(plan: list[_Target]) -> None:
    """Rename each temp file into place, checking its folders just before and just after.
    A new file goes in with os.rename, which fails if a file appeared there meanwhile; an
    existing one is replaced only while it still holds what was checked."""
    for t in plan:
        problem = _moved_folder(t.pins) or _temp_changed(t) or _changed_since_checked(t)
        if problem:
            raise _Failed(problem)
        t.attempted = True
        try:
            (os.rename if t.old is None else os.replace)(t.tmp.path, t.path)
        except FileExistsError as exc:
            raise _Failed(f"{t.path.name} appeared while NG Runner was writing it") from exc
        except OSError as exc:
            raise _Failed(f"{t.path.name} could not be replaced ({_why(exc)})") from exc
        moved = _moved_folder(t.pins)
        if moved:
            raise _Failed(moved)


def _temp_changed(t: _Target) -> str | None:
    if t.tmp is None or not _is_own_file(t.tmp):
        return f"the temp file for {t.path.name} was changed by something else"
    return None


def _changed_since_checked(t: _Target) -> str | None:
    """An existing file is replaced only while it still holds what the last check read."""
    if t.old is None:
        return None
    try:
        same = _is_plain_file(os.lstat(t.path)) and _sha_file(t.path) == _sha(t.old)
    except OSError:
        same = False
    return None if same else f"{t.path.name} changed just before NG Runner replaced it"


def _read_back_all(plan: list[_Target]) -> None:
    for t in plan:
        try:
            same = _sha_file(t.path) == _sha(t.write.content)
        except OSError:
            same = False
        if not same:
            raise _Failed(f"{t.path.name} did not read back as written")


def _record(registry: _Registry, plan: list[_Target]) -> None:
    try:
        registry.record({t.path: _sha(t.write.content) for t in plan})
    except (OSError, ValueError) as exc:
        raise _Failed(f"NG Runner could not record what it wrote ({_why(exc)})") from exc


# ----- rollback


def _roll_back(
    plan: list[_Target], created: list[_Pin], failure: str, backups: tuple[Path, ...]
) -> CommitResult:
    """Undo every rename this commit started, newest first, then clean up. Never raises."""
    notes: list[str] = []
    still_changed: list[Path] = []
    attempted = [t for t in plan if t.attempted]
    for t in reversed(attempted):
        try:
            note, changed = _undo(t)
        except Exception as exc:  # noqa: BLE001 - a rollback must finish and say what is left
            note, changed = f"{t.path.name} could not be restored ({_why(exc)}).", True
        if changed:
            still_changed.append(t.path)
        if note:
            notes.append(note + _backup_note(t))
    _clean_up(plan, created)
    outcome = "the commit was rolled back" if attempted else "nothing was written"
    reason = " ".join([f"{failure}, so {outcome}.", *notes])
    return CommitResult(ok=False, written=tuple(still_changed), backups=backups, reason=reason)


def _undo(t: _Target) -> tuple[str | None, bool]:
    """Take back what this commit put at t.path, only while that is provably its own file:
    the very file its temp file became (same identity), still holding the bytes it wrote.
    Returns a note for the reason, and whether the file still holds the new content."""
    if t.tmp is None or _is_own_file(t.tmp):
        return None, False  # its temp file is still there: the rename never happened
    try:
        holds_ours = _sha_file(t.path) == _sha(t.write.content)
    except FileNotFoundError:
        return f"{t.path.name} was moved or removed after NG Runner wrote it.", False
    if not holds_ours:
        return f"{t.path.name} was left as found: it no longer holds what NG Runner wrote.", False
    if not _is_own_file(_Temp(t.path, t.tmp.identity)):
        return (
            f"{t.path.name} was left in place: it holds what NG Runner wrote, but NG Runner"
            " could not confirm it is the very file it put there.",
            True,
        )
    if t.old is None:
        os.unlink(t.path)  # this commit's own file, moments old, still exactly its bytes
    else:
        _replace_with(t.path, t.old)
    return None, False


def _backup_note(t: _Target) -> str:
    return f" Its earlier content is in {t.backup}." if t.backup else ""


# ----- NG Runner's data folder: the ownership registry and backups


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha(path.read_bytes())


class _Registry:
    """The sha256 NG Runner last wrote at each path, kept in app_data. It is read and written
    only while it is a plain file there: a link at its name could forge it or lead elsewhere."""

    def __init__(self, app: _Root) -> None:
        self._app = app
        self._file = app.path / _REGISTRY_NAME

    def owned(self, path: Path) -> str | None:
        return self._load().get(_key(path))

    def record(self, written: dict[Path, str]) -> None:
        data = self._load()
        data.update({_key(path): digest for path, digest in written.items()})
        _replace_with(self._file, json.dumps(data, indent=1, sort_keys=True).encode("utf-8"))

    def problem(self) -> str | None:
        """Why the record can't be used now, in plain words, or None."""
        try:
            self._load()
        except (_NotSafe, ValueError) as exc:
            return str(exc)
        except OSError as exc:
            return f"NG Runner's record of its own files could not be read ({_why(exc)})"
        return None

    def _load(self) -> dict[str, str]:
        problem = _data_entry_problem(self._app, _REGISTRY_NAME, folder=False)
        if problem:
            raise _NotSafe(problem)
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except ValueError:  # not UTF-8, or not JSON
            data = None
        if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
            raise ValueError("NG Runner's record of its own files is damaged")
        return data


def _key(path: Path) -> str:
    return str(path).casefold()


def _data_folder_problem(app: _Root, registry: _Registry) -> str | None:
    """Checked before the interlock is first asked and anything is written, so a bad data
    folder or a damaged record of NG Runner's own files never forces a rollback."""
    if app.identity is None:
        return (
            "NG Runner's data folder is not there, so it could not record what it writes;"
            " nothing was written."
        )
    problem = registry.problem() or _data_entry_problem(app, _BACKUP_FOLDER, folder=True)
    return f"{problem}, so nothing was written." if problem else None


def _data_entry_problem(app: _Root, name: str, folder: bool) -> str | None:
    """A name directly in app_data is missing, or a real folder (or plain file) of its own;
    never a link, which would lead out of the data folder."""
    if app.identity is None:
        return "NG Runner's data folder is not there"
    moved = _moved_folder([_Pin(app.path, app.identity, root=True)])
    if moved:
        return moved
    try:
        st = os.lstat(app.path / name)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"{name} in NG Runner's data folder could not be checked ({_why(exc)})"
    if _is_link(st):
        return f"{name} in NG Runner's data folder is a link or junction"
    if stat.S_ISDIR(st.st_mode) != folder:
        return f"{name} in NG Runner's data folder is not a {'folder' if folder else 'file'}"
    return None


def _back_up(plan: list[_Target], app: _Root) -> str | None:
    try:
        _back_up_all(plan, app)
    except OSError as exc:
        return f"A backup could not be made ({_why(exc)}), so nothing was written."
    return None


def _back_up_all(plan: list[_Target], app: _Root) -> None:
    r"""Copy every file the commit replaces into one new folder, backup\<UTC time>\<root>\...
    Each folder on the way is made or checked like a target's: inside app_data, no links."""
    replaced = [t for t in plan if t.old is not None]
    if not replaced:
        return
    problem = _data_entry_problem(app, _BACKUP_FOLDER, folder=True)
    if problem:
        raise _NotSafe(problem)
    folder = _new_backup_folder(_make_folder(app.path / _BACKUP_FOLDER, []).path)
    for t in replaced:
        here = folder
        for name in (t.where, *t.rel[:-1]):
            here = _make_folder(here / name, []).path
        t.backup = here / t.rel[-1]
        _write_new(t.backup, t.old)


def _new_backup_folder(base: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    n = 1
    while True:  # the clock can repeat (it may be set back); never reuse a folder
        folder = base / (stamp if n == 1 else f"{stamp}-{n}")
        try:
            os.mkdir(folder)
        except FileExistsError:
            n += 1
            continue
        return _pin_folder(folder).path
