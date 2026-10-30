r"""
Backups of NG Runner's database, encrypted to a key only ICLA holds and restorable on any
PC; and the pruning of the copies commit_write keeps of the files it replaced.

Why: the database is NG Runner's whole memory (jobs, cards with Jim's answers, what it wrote
where), and Jim's PC can fail. Each night a consistent copy is taken with SQLite's online
backup (a running NG Runner is never stopped, and a copy is never half old, half new), then
encrypted to an X25519 public key and handed back as bytes. The caller writes it into the
control folder's backups\ through commit_write (spec sections 3 and 6.7), where the Apps
Script keeps 30 days of them. Only the public key is ever on Jim's PC, so neither that PC nor
the Drive folder can read a backup: the private key stays in ICLA's password manager and is
typed in only to restore (the restore drill, or a new PC).

The file (format 1):
    "NGRBAK" | format (1 byte) | key id (8: sha256 of the public key) | one-off public key
    (32) | nonce (12) | AES-256-GCM ciphertext and its 16-byte tag.
The AES key is HKDF-SHA256 of the X25519 shared secret, salted with both public keys. The
header is authenticated with the data, so a changed byte anywhere, a wrong key or a file
cut short is refused with a plain reason and nothing is restored.

Pruning: commit_write copies every file it replaces into AppPaths.backup\<UTC time>\ and the
design keeps them 60 days (section 3.2). prune() removes such a folder once its name and
every file in it are older than that. It touches only folders named the way commit_write
names them, holding nothing but plain files and folders; anything else there, and any link
or junction, is left alone. Two clocks must agree before anything goes: a PC clock set wrong
when a copy was made can't make it look old.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ngrunner.clock import Clock, pacific_day
from ngrunner.config import AppPaths

FORMAT = 1
MAGIC = b"NGRBAK"
EXTENSION = ".ngrbak"
FOLDER = "backups"  # in the control folder
KEEP_DAYS = 60

_PRIVATE = "ngrbak-private-1-"
_PUBLIC = "ngrbak-public-1-"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_INFO = b"NG Runner backup, format 1"
_ID_BYTES, _KEY_BYTES, _NONCE_BYTES, _TAG_BYTES = 8, 32, 12, 16
_HEADER_BYTES = len(MAGIC) + 1 + _ID_BYTES + _KEY_BYTES + _NONCE_BYTES
_MAX_PLAIN = 2**31 - 1  # the most AES-GCM takes in one piece here
_SQLITE = b"SQLite format 3\x00"
_STAMP = re.compile(r"([0-9]{8}T[0-9]{12})Z(?:-[0-9]+)?")  # writes.py: %Y%m%dT%H%M%S%fZ[-n]


class BackupError(ValueError):
    """A backup can't be made or restored; the message says why, in plain words."""


@dataclass(frozen=True)
class Backup:
    """One night's backup: data goes to <control folder>\\<folder>\\<name> via commit_write."""

    folder: str
    name: str
    data: bytes


@dataclass(frozen=True)
class DatabaseFacts:
    """What a database holds: schema version, rows per table, and anything wrong with it."""

    schema_version: int | None
    tables: dict[str, int]
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class Pruned:
    """removed: the copies pruned. left: the copies due for pruning but left, and why."""

    removed: tuple[str, ...]
    left: tuple[str, ...]


# ----- keys


def new_keypair() -> tuple[str, str]:
    """A new backup key pair as text: (private, public). The private key goes to ICLA's
    password manager and never onto Jim's PC; NG Runner is given the public key."""
    private = X25519PrivateKey.generate()
    raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    return _PRIVATE + raw.hex(), _PUBLIC + _raw(private.public_key()).hex()


def key_id(key: str) -> str:
    """The short id (16 hex) that names a key pair, from either of its keys: written into
    every backup, so a backup says which private key opens it."""
    text = key.strip()
    if text.startswith(_PRIVATE):
        return _id(_raw(_private_key(text).public_key())).hex()
    return _id(_raw(_public_key(text))).hex()


def _public_key(text: str) -> X25519PublicKey:
    text = text.strip()
    if text.startswith(_PRIVATE) and _HEX64.fullmatch(text[len(_PRIVATE):]):
        raise BackupError("That is the private key; a backup is made with the public key"
                          f" ({_PUBLIC}...). Keep the private key off this PC.")
    return X25519PublicKey.from_public_bytes(_key_bytes(text, _PUBLIC))


def _private_key(text: str) -> X25519PrivateKey:
    text = text.strip()
    if text.startswith(_PUBLIC) and _HEX64.fullmatch(text[len(_PUBLIC):]):
        raise BackupError(
            f"That is the public key; restoring needs the private key ({_PRIVATE}...).")
    return X25519PrivateKey.from_private_bytes(_key_bytes(text, _PRIVATE))


def _key_bytes(text: str, prefix: str) -> bytes:
    if not (text.startswith(prefix) and _HEX64.fullmatch(text[len(prefix):])):
        raise BackupError(f"That is not an NG Runner backup key (it starts with {prefix}).")
    return bytes.fromhex(text[len(prefix):])


def _raw(key: X25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _id(public: bytes) -> bytes:
    return hashlib.sha256(public).digest()[:_ID_BYTES]


def _aes_key(shared: bytes, one_off: bytes, recipient: bytes) -> bytes:
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=one_off + recipient, info=_INFO)
    return hkdf.derive(shared)


# ----- encrypting and restoring


def encrypt(data: bytes, public_key: str) -> bytes:
    """data locked to public_key, in the format above."""
    recipient = _public_key(public_key)
    if len(data) > _MAX_PLAIN:
        raise BackupError("The database is too large to back up in one file.")
    one_off = X25519PrivateKey.generate()
    one_off_raw, recipient_raw = _raw(one_off.public_key()), _raw(recipient)
    nonce = os.urandom(_NONCE_BYTES)
    header = MAGIC + bytes([FORMAT]) + _id(recipient_raw) + one_off_raw + nonce
    key = _aes_key(one_off.exchange(recipient), one_off_raw, recipient_raw)
    return header + AESGCM(key).encrypt(nonce, bytes(data), header)


def restore(data: bytes, private_key: str) -> bytes:
    """The database a backup holds, after every check passed (spec 6.7: the restore drill)."""
    own = _private_key(private_key)
    data = bytes(data)
    if len(data) >= len(MAGIC) and not data.startswith(MAGIC):
        raise BackupError("This file is not an NG Runner backup.")
    if len(data) < _HEADER_BYTES + _TAG_BYTES:
        raise BackupError("This backup is cut short, so it was not restored.")
    _check_header(data, _raw(own.public_key()))
    header = data[:_HEADER_BYTES]
    one_off = header[-_NONCE_BYTES - _KEY_BYTES:-_NONCE_BYTES]
    nonce = header[-_NONCE_BYTES:]
    try:
        shared = own.exchange(X25519PublicKey.from_public_bytes(one_off))
        plain = AESGCM(_aes_key(shared, one_off, _raw(own.public_key()))).decrypt(
            nonce, data[_HEADER_BYTES:], header)
    except (InvalidTag, ValueError) as exc:
        raise BackupError("This backup is damaged or was changed after it was made,"
                          " so it was not restored.") from exc
    if not plain.startswith(_SQLITE):
        raise BackupError("This backup does not hold an NG Runner database.")
    return plain


def _check_header(data: bytes, own_public: bytes) -> None:
    form = data[len(MAGIC)]
    if form > FORMAT:
        raise BackupError(f"This backup was made by a newer NG Runner (format {form});"
                          " restore it with that version.")
    if form != FORMAT:
        raise BackupError(f"This backup's format ({form}) is not one NG Runner knows,"
                          " so it was not restored.")
    found = data[len(MAGIC) + 1:len(MAGIC) + 1 + _ID_BYTES]
    if found != _id(own_public):
        raise BackupError(f"This backup was locked with a different backup key (key {found.hex()}),"
                          f" not the key given (key {_id(own_public).hex()}). If the key is right,"
                          " the file is damaged.")


# ----- the database


def snapshot(db: Path) -> bytes:
    """A consistent copy of the database, taken with SQLite's online backup while NG Runner
    may be using it, as the bytes of a database file. It never creates a database. The nightly
    backup, the self-test and the support bundle use this, as it leaves no temp file;
    store.Store.backup_to, which makes a file, has one caller: the supervisor's copy before an
    upgrade."""
    if not db.is_file():
        raise BackupError("There is no database to back up yet.")
    try:
        with closing(sqlite3.connect(f"{db.as_uri()}?mode=rw", uri=True, timeout=5.0)) as source, \
                closing(sqlite3.connect(":memory:")) as copy:
            source.backup(copy)
            return copy.serialize()
    except sqlite3.Error as exc:
        raise BackupError(f"The database could not be copied ({exc}).") from exc


def describe(db: bytes) -> DatabaseFacts:
    """Check a database held as bytes (from snapshot or restore): its integrity, schema
    version and rows per table. It reads a copy in memory, so nothing on disk changes."""
    if not db.startswith(_SQLITE):
        return DatabaseFacts(None, {}, ("It is not an SQLite database.",))
    try:
        with closing(sqlite3.connect(":memory:")) as con:
            con.deserialize(_openable(db))
            checks = [row[0] for row in con.execute("PRAGMA integrity_check")]
            version = con.execute("PRAGMA user_version").fetchone()[0]
            names = [row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
                " AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name")]
            tables = {name: con.execute(f"SELECT count(*) FROM {_quoted(name)}").fetchone()[0]
                      for name in names}
    except sqlite3.Error as exc:
        return DatabaseFacts(None, {}, (f"The database is damaged ({exc}).",))
    problems = () if checks == ["ok"] else tuple(f"Integrity check: {c}" for c in checks[:10])
    return DatabaseFacts(version, tables, problems)


def _quoted(name: str) -> str:
    """A table name from the database's own catalogue, quoted as SQL needs (names can't be
    passed as parameters)."""
    return '"' + name.replace('"', '""') + '"'


def _openable(db: bytes) -> bytes:
    """A database's bytes as SQLite can open them in memory: WAL marks can't be, and no row
    depends on them."""
    image = bytearray(db)
    if len(image) > 19:
        image[18] = image[19] = 1
    return bytes(image)


def for_a_new_pc(db: bytes) -> bytes:
    """A restored database made ready to run on a new PC: store.jump_after_restore moves its
    outgoing sequence numbers and its card numbers far ahead. The backup is older than the last
    files NG Runner sent, and the Apps Script ignores any number it has seen (spec section 3), so
    a restored NG Runner would go unheard until the numbers caught up; and a card ID given since
    the backup would be given again. What it has received keeps its place. restore() itself
    changes nothing, so the drill checks the backup exactly as it was made."""
    # Imported here, so the self-test and the support bundle, which use the rest of this module
    # to report a broken build, still load when the store does not.
    from ngrunner.store import jump_after_restore
    try:
        with closing(sqlite3.connect(":memory:")) as con:
            con.deserialize(_openable(db))
            if con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'seqs'"
                           ).fetchone() is None:
                return db
            jump_after_restore(con)
            con.commit()
            return con.serialize()
    except sqlite3.Error as exc:
        raise BackupError(f"The restored database could not be made ready ({exc}).") from exc


def nightly_backup(paths: AppPaths, public_key: str, *, clock: Clock) -> Backup:
    """Tonight's backup, named by the Pacific date: backups\\<YYYYMMDD>.ngrbak."""
    _public_key(public_key)  # a wrong key is refused before the database is read
    data = encrypt(snapshot(paths.db), public_key)
    return Backup(FOLDER, f"{pacific_day(clock.now()):%Y%m%d}{EXTENSION}", data)


# ----- pruning commit_write's copies


def prune(paths: AppPaths, *, clock: Clock, keep_days: int = KEEP_DAYS) -> Pruned:
    """Remove the copies in AppPaths.backup older than keep_days (see the module docstring)."""
    base = paths.backup
    try:
        st = os.lstat(base)
    except FileNotFoundError:
        return Pruned((), ())
    except OSError as exc:
        return Pruned((), (f"The backup folder could not be checked ({_why(exc)}).",))
    if _is_link(st) or not stat.S_ISDIR(st.st_mode):
        return Pruned((), ("The backup folder is a link or not a folder, so nothing in it was"
                           " pruned.",))
    cutoff = clock.now() - timedelta(days=keep_days)
    try:
        names = sorted(os.listdir(base))
    except OSError as exc:
        return Pruned((), (f"The backup folder could not be listed ({_why(exc)}).",))
    removed, left = [], []
    for name in names:
        made = _stamp_time(name)
        if made is None or made >= cutoff:
            continue  # not one of commit_write's copies, or not old enough
        found = _scan(base / name)
        if isinstance(found, str):
            left.append(found)
            continue
        if max(st.st_mtime for _, st in found) >= cutoff.timestamp():
            continue  # something in it changed recently: the clocks disagree, so keep it
        reason = remove_own_tree(base / name, inside=base)
        if reason:
            left.append(reason)
        else:
            removed.append(name)
    return Pruned(tuple(removed), tuple(left))


def _stamp_time(name: str) -> datetime | None:
    match = _STAMP.fullmatch(name)
    if not match:
        return None
    try:
        return datetime.strptime(match[1], "%Y%m%dT%H%M%S%f").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def remove_own_tree(folder: Path, *, inside: Path) -> str | None:
    """Remove folder and all it holds, only when it is below inside (each folder on the way a
    real folder, never a link) and holds nothing but plain files and folders. Returns None
    once it is gone, else why it was left. Everything is checked before the first removal,
    so anything odd leaves it all in place; only a change made while it is being removed
    (or a file in use) can stop it part-way, and the reason then says so."""
    base, target = Path(os.path.abspath(inside)), Path(os.path.abspath(folder))
    below = _names_below(base, target)
    if not below:
        return f"{target} is not inside {base}, so it was left alone."
    here = base
    for name in ("", *below[:-1]):
        here = here / name if name else here
        try:
            st = os.lstat(here)
        except OSError as exc:
            return (f"{here.name} could not be checked ({_why(exc)}), so {target.name} was left"
                    " alone.")
        if _is_link(st) or not stat.S_ISDIR(st.st_mode):
            return f"{here.name} is a link or not a folder, so {target.name} was left alone."
    found = _scan(target)
    if isinstance(found, str):
        return found
    for path, st in reversed(found):  # deepest first: a folder is removed after what it holds
        try:
            now = os.lstat(path)
            if _is_link(now) or (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):
                return (f"{path.name} changed while {target.name} was being removed; the rest is"
                        " left.")
            (os.rmdir if stat.S_ISDIR(st.st_mode) else os.unlink)(path)
        except OSError as exc:
            return (f"{path.name} could not be removed ({_why(exc)}); the rest of {target.name}"
                    " is left.")
    return None


def _names_below(base: Path, target: Path) -> tuple[str, ...]:
    """The names of target below base (compared as Windows does, ignoring case); empty when
    target is not strictly below base."""
    outer = [os.path.normcase(p) for p in base.parts]
    inner = [os.path.normcase(p) for p in target.parts]
    if len(inner) <= len(outer) or inner[: len(outer)] != outer:
        return ()
    return target.parts[len(outer):]


def _scan(top: Path) -> list[tuple[Path, os.stat_result]] | str:
    """Everything in top, top first, each before what it holds; or why top must be left: a
    link, a junction or anything but a plain file or folder anywhere in it."""
    found: list[tuple[Path, os.stat_result]] = []
    todo = [top]
    while todo:
        path = todo.pop()
        try:
            st = os.lstat(path)
        except OSError as exc:
            return f"{path.name} could not be checked ({_why(exc)}), so {top.name} was left alone."
        if _is_link(st):
            return f"{top.name} holds a link or junction ({path.name}), so it was left alone."
        if stat.S_ISDIR(st.st_mode):
            try:
                names = sorted(os.listdir(path))
            except OSError as exc:
                return (f"{path.name} could not be listed ({_why(exc)}), so {top.name} was left"
                        " alone.")
            todo.extend(path / name for name in reversed(names))
        elif not stat.S_ISREG(st.st_mode):
            return (f"{top.name} holds something that is not a file or a folder, so it was left"
                    " alone.")
        found.append((path, st))
    return found


def _is_link(st: os.stat_result) -> bool:
    """Any reparse point (junction, symlink, mount point) counts: nothing in NG Runner's own
    data folder should be one, so none is ever followed or removed."""
    return stat.S_ISLNK(st.st_mode) or bool(
        getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _why(exc: BaseException) -> str:
    return getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
