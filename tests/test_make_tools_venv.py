"""
tools/make_tools_venv.py's pinned programs (tools/tools-bin.txt): poppler for gate 6's drawing check, fetched from
one tagged release, refused unless its SHA-256 is the pinned one, and unpacked only in its own folder. No test
here touches the network: every download is a fake that writes made-up bytes.
"""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from tools import make_tools_venv as mtv

URL = "https://github.com/testerson/poppler-made-up/releases/download/v1.2.3-0/Release-1.2.3-0.zip"


def a_zip(members: dict[str, bytes]) -> bytes:
    held = io.BytesIO()
    with zipfile.ZipFile(held, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return held.getvalue()


GOOD = a_zip({"poppler-1.2.3/Library/bin/pdftoppm.exe": b"MZ made up", "poppler-1.2.3/share/x.txt": b"x"})


def program(data: bytes = GOOD) -> mtv.Program:
    return mtv.Program("poppler", "1.2.3-0", URL, hashlib.sha256(data).hexdigest(), "poppler-1.2.3/Library/bin")


def fake(data: bytes, asked: list[str] | None = None):
    def get(url: str, to: Path) -> None:
        if asked is not None:
            asked.append(url)
        to.write_bytes(data)
    return get


def test_a_download_whose_sha256_isnt_the_pinned_one_is_refused_and_nothing_is_unpacked(tmp_path: Path) -> None:
    pinned = program()
    with pytest.raises(mtv.Refused) as refused:
        mtv.fetch(pinned, tmp_path / "tools", get=fake(GOOD + b"one byte more"))
    words = str(refused.value)
    assert "SHA-256" in words and pinned.sha256 in words and "was not unpacked" in words
    assert list((tmp_path / "tools").iterdir()) == []  # not its folder, and not the download


def test_main_says_so_in_plain_words_and_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    pin = tmp_path / "tools-bin.txt"
    pin.write_text(f"# made up\npoppler 1.2.3-0 {URL} sha256={'0' * 64} bin=poppler-1.2.3/Library/bin\n",
                   encoding="utf-8")
    monkeypatch.setattr(mtv, "PROGRAMS", pin)
    assert mtv.make_programs(tmp_path / "tools", get=fake(GOOD)) == 1
    said = capsys.readouterr().out
    assert said.startswith("The poppler 1.2.3-0 download's SHA-256 is ") and "Traceback" not in said
    assert not (tmp_path / "tools" / "poppler-1.2.3-0").exists()


def test_the_pinned_download_is_unpacked_in_its_own_folder_and_its_bin_folder_named(tmp_path: Path) -> None:
    asked: list[str] = []
    found = mtv.fetch(program(), tmp_path / "tools", get=fake(GOOD, asked))
    assert asked == [URL]
    assert found == tmp_path / "tools" / "poppler-1.2.3-0" / "poppler-1.2.3" / "Library" / "bin"
    assert (found / "pdftoppm.exe").read_bytes() == b"MZ made up"
    assert sorted(p.name for p in (tmp_path / "tools").iterdir()) == ["poppler-1.2.3-0"]  # the download is gone


@pytest.mark.parametrize("bad", ["../escape.exe", "/abs/pdftoppm.exe", "C:/abs/pdftoppm.exe",
                                 "poppler-1.2.3/../../escape.exe"])
def test_a_zip_naming_a_path_outside_its_folder_is_never_unpacked(tmp_path: Path, bad: str) -> None:
    data = a_zip({"poppler-1.2.3/Library/bin/pdftoppm.exe": b"MZ", bad: b"x"})
    with pytest.raises(mtv.Refused, match="outside its own folder"):
        mtv.fetch(program(data), tmp_path / "tools", get=fake(data))
    assert list((tmp_path / "tools").iterdir()) == []


def test_a_folder_already_there_is_never_changed(tmp_path: Path) -> None:
    there = tmp_path / "tools" / "poppler-1.2.3-0"
    there.mkdir(parents=True)
    with pytest.raises(mtv.Refused, match="is there already"):
        mtv.fetch(program(), tmp_path / "tools", get=fake(GOOD))
    assert list(there.iterdir()) == []


def test_a_zip_without_its_bin_folder_is_not_kept(tmp_path: Path) -> None:
    data = a_zip({"poppler-1.2.3/share/x.txt": b"x"})
    with pytest.raises(mtv.Refused, match="holds no"):
        mtv.fetch(program(data), tmp_path / "tools", get=fake(data))
    assert list((tmp_path / "tools").iterdir()) == []


@pytest.mark.parametrize("line, words", [
    ("poppler 1.2.3-0 https://github.com/a/b/releases/latest/download/Release.zip sha256=" + "0" * 64 + " bin=b",
     "never from latest"),
    ("poppler 1.2.3-0 https://github.com/a/b/releases/download/v1.2.4-0/Release.zip sha256=" + "0" * 64 + " bin=b",
     "never from latest"),
    ("poppler 1.2.3-0 https://example.com/a/b/releases/download/v1.2.3-0/R.zip sha256=" + "0" * 64 + " bin=b",
     "never from latest"),
    ("poppler 1.2.3-0 " + URL + " sha256=abc bin=b", "64 hex digits"),
    ("poppler 1.2.3-0 " + URL + " sha256=" + "0" * 64 + " bin=../b", "inside its zip"),
    ("poppler 1.2.3-0 " + URL, "isn't name, version"),
])
def test_a_pin_that_isnt_one_tagged_release_with_a_full_hash_is_refused(line: str, words: str) -> None:
    with pytest.raises(mtv.Refused, match=words):
        mtv.read_programs(line)


def test_the_committed_pin_is_one_tagged_poppler_windows_release_with_its_sha256() -> None:
    (poppler,) = mtv.read_programs(mtv.PROGRAMS.read_text(encoding="utf-8"))
    assert poppler.name == "poppler" and poppler.version == "26.09.0-0"
    assert poppler.url == ("https://github.com/oschwartz10612/poppler-windows/releases/download/v26.09.0-0/"
                           "Release-26.09.0-0.zip")
    assert poppler.sha256 == "7a6f256a0ddf7536182246a5733331bf4677cbcc34f4663774947ad34556c8d0"
    assert poppler.bin == "poppler-26.09.0/Library/bin"
