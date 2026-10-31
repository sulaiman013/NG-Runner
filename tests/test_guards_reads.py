r"""
Reading a file Mozaik or Drive might be using, without getting in its way.

Why: Python's plain open() lets nobody rename the file while it is open, and Mozaik saves a nest by
renaming, so a guard that only reads could still make Mozaik's save fail on Jim's PC. And a file
that exists only in the cloud can be opened but not read: on this PC, Windows error 362 from every
placeholder behind the stopped OneDrive account. Both must come back in plain words. The test
marked `fixtures` reads one real placeholder, skips when there is none, and prints no path.
"""
from __future__ import annotations

import _winapi
import os
import stat
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from ngrunner.guards import reads
from ngrunner.guards.reads import CloudOnly, Unreadable, read_shared

_RECALL_ON_DATA_ACCESS = 0x00400000
_NO_SHARING = 0
_OPEN_EXISTING = 3


def _file(tmp_path: Path, content: bytes = b"made-up content") -> Path:
    path = tmp_path / "Testerson_101426.opt"
    path.write_bytes(content)
    return path


def _during_read(monkeypatch: pytest.MonkeyPatch, action) -> list[object]:
    """Run action while a real read is under way, between its open and its first bytes."""
    original = reads._read_chunk
    results: list[object] = []

    def chunk(handle: int, size: int) -> bytes:
        if not results:
            results.append(action())
        return original(handle, size)

    monkeypatch.setattr(reads, "_read_chunk", chunk)
    return results


# ----- reading


def test_it_reads_the_whole_file_byte_for_byte(tmp_path):
    content = bytes(range(256)) * 20000 + b"\r\n\x00end"   # past one chunk, in binary
    assert read_shared(_file(tmp_path, content)) == content


def test_an_empty_file_is_empty(tmp_path):
    assert read_shared(_file(tmp_path, b"")) == b""


def _count_closes(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    closes: list[int] = []
    real_close = _winapi.CloseHandle

    def counted(handle: int) -> None:
        closes.append(handle)
        real_close(handle)

    monkeypatch.setattr(reads._winapi, "CloseHandle", counted)
    return closes


def test_it_closes_the_file_when_done(tmp_path, monkeypatch):
    path = _file(tmp_path)
    closes = _count_closes(monkeypatch)
    read_shared(path)
    assert len(closes) == 1


def test_it_closes_the_file_when_the_read_fails(tmp_path, monkeypatch):
    path = _file(tmp_path)
    closes = _count_closes(monkeypatch)

    def fails(handle: int, size: int) -> bytes:
        raise _windows_error(5)

    monkeypatch.setattr(reads, "_read_chunk", fails)
    with pytest.raises(Unreadable):
        read_shared(path)
    assert len(closes) == 1


# ----- others can carry on while it reads


def test_mozaik_can_rename_the_file_during_the_read(tmp_path, monkeypatch):
    path = _file(tmp_path)
    moved = tmp_path / "moved.opt"
    results = _during_read(monkeypatch, lambda: os.replace(path, moved))
    assert read_shared(path) == b"made-up content"
    assert results == [None]
    assert moved.read_bytes() == b"made-up content"
    assert not path.exists()


def test_a_replace_over_the_file_is_refused_during_the_read_whatever_the_sharing(tmp_path,
                                                                                 monkeypatch):
    """A limit of Windows, kept here as a fact: moving the file away works, moving another onto its
    name does not, while any read holds it. So a read must be short, and how Mozaik really saves is
    for the lab (build spec section 8) to show. It reads whole, and the file stands afterwards."""
    path = _file(tmp_path)
    newer = tmp_path / "newer.opt"
    newer.write_bytes(b"new")

    def replace() -> bool:
        with pytest.raises(PermissionError):
            os.replace(newer, path)
        return True

    results = _during_read(monkeypatch, replace)
    assert read_shared(path) == b"made-up content"
    assert results == [True]
    os.replace(newer, path)                            # free again as soon as the read has finished
    assert path.read_bytes() == b"new"


def test_something_can_write_to_the_file_during_the_read(tmp_path, monkeypatch):
    path = _file(tmp_path)

    def write() -> None:
        with open(path, "r+b") as other:
            other.write(b"M")

    _during_read(monkeypatch, write)
    assert read_shared(path) == b"Made-up content"


def test_something_can_read_the_file_during_the_read(tmp_path, monkeypatch):
    path = _file(tmp_path)
    results = _during_read(monkeypatch, path.read_bytes)
    read_shared(path)
    assert results == [b"made-up content"]


def test_something_can_delete_the_file_during_the_read(tmp_path, monkeypatch):
    path = _file(tmp_path)
    _during_read(monkeypatch, lambda: os.unlink(path))
    assert read_shared(path) == b"made-up content"
    assert not path.exists()


def test_a_plain_open_would_block_that_rename(tmp_path):
    """Why this module exists: the reason to read any other way."""
    path = _file(tmp_path)
    with open(path, "rb"):
        with pytest.raises(PermissionError):
            os.replace(path, tmp_path / "moved.opt")


# ----- what comes back when it can't read


def _windows_error(code: int) -> OSError:
    return OSError(None, "some Windows wording", None, code)


@pytest.mark.parametrize("code", [362, 395])
def test_the_cloud_errors_during_a_read_are_cloud_only(tmp_path, monkeypatch, code):
    def fails(handle: int, size: int) -> bytes:
        raise _windows_error(code)

    monkeypatch.setattr(reads, "_read_chunk", fails)
    with pytest.raises(CloudOnly) as caught:
        read_shared(_file(tmp_path))
    assert caught.value.winerror == code


@pytest.mark.parametrize("code", [362, 395])
def test_the_cloud_errors_on_opening_are_cloud_only(tmp_path, monkeypatch, code):
    def fails(_: Path) -> int:
        raise _windows_error(code)

    monkeypatch.setattr(reads, "_open_shared", fails)
    with pytest.raises(CloudOnly):
        read_shared(_file(tmp_path))


@pytest.mark.parametrize("code", [5, 32, 21, 361, 363, 394, 396, 1392])
def test_any_other_error_is_unreadable_and_not_cloud_only(tmp_path, monkeypatch, code):
    def fails(handle: int, size: int) -> bytes:
        raise _windows_error(code)

    monkeypatch.setattr(reads, "_read_chunk", fails)
    with pytest.raises(Unreadable) as caught:
        read_shared(_file(tmp_path))
    assert not isinstance(caught.value, CloudOnly)
    assert caught.value.winerror == code


def test_a_missing_file_is_unreadable(tmp_path):
    with pytest.raises(Unreadable) as caught:
        read_shared(tmp_path / "gone.opt")
    assert not isinstance(caught.value, CloudOnly)
    assert caught.value.winerror == 2


def test_a_folder_is_unreadable(tmp_path):
    with pytest.raises(Unreadable):
        read_shared(tmp_path)


def test_a_file_held_with_no_sharing_is_unreadable(tmp_path):
    path = _file(tmp_path)
    held = _winapi.CreateFile(str(path), _winapi.GENERIC_READ, _NO_SHARING, _winapi.NULL,
                              _OPEN_EXISTING, 0, _winapi.NULL)
    try:
        with pytest.raises(Unreadable) as caught:
            read_shared(path)
    finally:
        _winapi.CloseHandle(held)
    assert caught.value.winerror == 32


def test_cloud_only_is_a_kind_of_unreadable():
    """Most callers only care that it can't be read; a board note can tell them apart."""
    assert issubclass(CloudOnly, Unreadable)


@pytest.mark.parametrize("code, kind", [(362, CloudOnly), (395, CloudOnly), (5, Unreadable),
                                        (32, Unreadable), (2, Unreadable)])
def test_the_words_are_plain(tmp_path, monkeypatch, code, kind):
    def fails(handle: int, size: int) -> bytes:
        raise _windows_error(code)

    monkeypatch.setattr(reads, "_read_chunk", fails)
    with pytest.raises(kind) as caught:
        read_shared(_file(tmp_path))
    text = str(caught.value)
    assert "Testerson_101426.opt" in text
    assert str(tmp_path) not in text                    # the file's name, not where it lives
    rest = text.replace("Testerson_101426.opt", "")
    for word in ("WinError", "Errno", "errno", "Traceback", "OSError", "some Windows wording",
                 "CreateFile", str(code), "Exception", "\\"):
        assert word not in rest
    assert text == text.strip() and text.endswith(".")


def test_the_cloud_words_say_what_to_do_about_it(tmp_path, monkeypatch):
    def fails(handle: int, size: int) -> bytes:
        raise _windows_error(362)

    monkeypatch.setattr(reads, "_read_chunk", fails)
    with pytest.raises(CloudOnly) as caught:
        read_shared(_file(tmp_path))
    assert "only in the cloud" in str(caught.value)


def test_the_real_cause_stays_for_the_developer_but_out_of_the_words(tmp_path):
    with pytest.raises(Unreadable) as caught:
        read_shared(tmp_path / "gone.opt")
    assert isinstance(caught.value.__cause__, OSError)


def test_reading_from_several_threads_at_once_is_safe(tmp_path):
    path = _file(tmp_path, b"x" * 2_000_000)
    results: list[bytes] = []
    threads = [threading.Thread(target=lambda: results.append(read_shared(path))) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [b"x" * 2_000_000] * 4


# ----- a real placeholder on this PC


def _placeholders() -> Iterator[Path]:
    """Cloud-only files behind this PC's OneDrive accounts, by what Windows says about them."""
    for account in sorted(Path.home().glob("OneDrive*")):
        for jobs in (account / "Mozaik Shared" / "Jobs",
                     account / "Documents" / "JD" / "Mozaik Move" / "Mozaik" / "Jobs"):
            for folder, _, files in os.walk(jobs):
                for file in sorted(files):
                    path = Path(folder) / file
                    if path.suffix.casefold() in (".opt", ".des"):
                        if os.stat(path).st_file_attributes & _RECALL_ON_DATA_ACCESS:
                            yield path


@pytest.mark.fixtures
def test_a_real_cloud_only_file_raises_cloud_only():
    path = next(_placeholders(), None)
    if path is None:
        pytest.skip("no cloud-only placeholder on this PC")
    holds_something = os.stat(path).st_size > 0 and stat.S_ISREG(os.stat(path).st_mode)
    assert holds_something                    # Windows says it has a size, though no bytes are here
    try:
        data = read_shared(path)
    except CloudOnly as exc:
        named = path.name in str(exc)         # tested apart so a failure shows no path
        assert exc.winerror == 362
        assert named
        return
    pytest.skip(f"OneDrive is running here, so it read {len(data)} bytes: nothing cloud-only left")
