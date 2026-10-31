r"""
Reading a file that Mozaik or a cloud drive might be using, without getting in its way.

Why it exists: Python's open() doesn't share delete access, so while it holds a file nobody can
rename it, and Mozaik saves a nest by renaming (design 5, Reads). Windows' CreateFile with all three
share flags lets the read and the save both carry on. Every .opt, .des, .UNS and .LCK read goes
through here (docs/p1b/build-spec.md, section 5.10).

A file that lives only in the cloud can be opened and then can't be read: Windows error 362, "the
cloud file provider is not running", on every placeholder behind this PC's stopped OneDrive account,
or 395 when access is denied. That needs a different answer from a file that is just gone, so it has
its own exception. Both say what happened in plain words, naming the file and not where it lives.
The Windows error stays on .winerror, and the OSError behind it on __cause__, for a developer.
"""
from __future__ import annotations

import _winapi
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_CLOUD_ERRORS = frozenset({362, 395})     # ERROR_CLOUD_FILE_PROVIDER_NOT_RUNNING, _ACCESS_DENIED
_SHARE_READ, _SHARE_WRITE, _SHARE_DELETE = 0x1, 0x2, 0x4   # _winapi has none of these by name
_OPEN_EXISTING = 3
_ATTRIBUTE_NORMAL = 0x80
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Listed:
    """A file as a listing gives it: enough to tell it changed, and two polls apart agreeing."""

    size: int
    mtime: datetime


def listed_of(entry: os.DirEntry[str]) -> Listed:
    stat = entry.stat(follow_symlinks=False)
    # Whole microseconds: as a stored time keeps them, so two polls compare equal after a restart.
    try:
        return Listed(stat.st_size, _EPOCH + timedelta(microseconds=stat.st_mtime_ns // 1000))
    except OverflowError:       # a time past what a date can hold: still the same file next poll
        return Listed(stat.st_size, _EPOCH)


def stat_words(listed: Listed) -> list[Any]:
    """A listing as the state table keeps it."""
    return [listed.size, listed.mtime.isoformat()]


class Unreadable(Exception):
    """The file couldn't be read. str() is plain words; .winerror is the Windows error, if any."""

    def __init__(self, words: str, winerror: int | None = None) -> None:
        super().__init__(words)
        self.winerror = winerror


class CloudOnly(Unreadable):
    """The file is on this PC by name only, and its contents can't be fetched from the cloud."""


def read_shared(path: Path) -> bytes:
    """A file's whole contents, leaving others free to read, write, rename or delete it meanwhile.
    Raises CloudOnly when its contents are only in the cloud, and Unreadable for anything else."""
    try:
        handle = _open_shared(path)
        try:
            return _read_all(handle)
        finally:
            _winapi.CloseHandle(handle)
    except OSError as exc:
        raise _in_plain_words(path, exc) from exc


def _open_shared(path: Path) -> int:
    return _winapi.CreateFile(
        str(path), _winapi.GENERIC_READ, _SHARE_READ | _SHARE_WRITE | _SHARE_DELETE, _winapi.NULL,
        _OPEN_EXISTING, _ATTRIBUTE_NORMAL, _winapi.NULL)


def _read_all(handle: int) -> bytes:
    chunks = []
    while chunk := _read_chunk(handle, _CHUNK):
        chunks.append(chunk)
    return b"".join(chunks)


def _read_chunk(handle: int, size: int) -> bytes:
    data, _ = _winapi.ReadFile(handle, size)
    return data


def _in_plain_words(path: Path, exc: OSError) -> Unreadable:
    if exc.winerror in _CLOUD_ERRORS:
        return CloudOnly(f"{path.name} is only in the cloud, and the cloud drive isn't giving it "
                         "to this PC.", exc.winerror)
    return Unreadable(f"{path.name} could not be read.", exc.winerror)
