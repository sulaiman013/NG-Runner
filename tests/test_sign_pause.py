"""tools/sign_pause.py: signed pause files with a Unix-second seq, pause only (build spec section
3). Everything stays under tmp_path; the tool's own temp folder too."""
from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ngrunner.clock import FakeClock
from ngrunner.signing import key_from_text, open_envelope
from ngrunner.store import accept_envelope, open_store

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ngr_tools_sign_pause",
                                               ROOT / "tools" / "sign_pause.py")
sign_pause = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sign_pause
_spec.loader.exec_module(sign_pause)

KEY_TEXT = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
NOW = datetime(2026, 10, 30, 19, 0, tzinfo=timezone.utc)


@pytest.fixture
def control(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    folder = tmp_path / "control"
    folder.mkdir()
    return folder


def _run(control: Path, *more: str, reason: str = "Testing the pause", key: str = KEY_TEXT,
         now: datetime = NOW) -> tuple[int, str]:
    out = io.StringIO()
    code = sign_pause.main(["--control", str(control), "--reason", reason, *more], out=out,
                           read_secret=lambda prompt: key, clock=FakeClock(now))
    return code, out.getvalue()


def test_it_makes_a_signed_pause_file_with_a_unix_second_seq(control: Path) -> None:
    code, text = _run(control)
    assert code == 0, text
    files = list((control / "pause").iterdir())
    assert len(files) == 1
    envelope = open_envelope(files[0].read_bytes(), key=key_from_text(KEY_TEXT), kind="pause")
    assert envelope.payload == {"reason": "Testing the pause"}
    assert envelope.seq == int(NOW.timestamp()) and envelope.issued_at == NOW
    assert files[0].name == f"{envelope.id}.json"
    assert "Resume only at the PC" in text


def test_ng_runner_accepts_it_and_a_later_one(control: Path, tmp_path: Path) -> None:
    store = open_store(tmp_path / "ngrunner.db")
    key = key_from_text(KEY_TEXT)
    _run(control)
    _run(control, now=datetime(2026, 10, 30, 19, 1, tzinfo=timezone.utc))
    envelopes = sorted((open_envelope(f.read_bytes(), key=key, kind="pause")
                        for f in (control / "pause").iterdir()), key=lambda e: e.seq)
    assert len(envelopes) == 2
    assert all(accept_envelope(store, "pause", seq=e.seq, id=e.id) for e in envelopes)


def test_the_key_is_never_printed_and_never_on_the_command_line(control: Path) -> None:
    prompts: list[str] = []
    out = io.StringIO()

    def read(prompt: str) -> str:
        prompts.append(prompt)
        return KEY_TEXT

    assert sign_pause.main(["--control", str(control), "--reason", "Testing"], out=out,
                           read_secret=read, clock=FakeClock(NOW)) == 0
    assert len(prompts) == 1 and KEY_TEXT not in out.getvalue()
    assert sign_pause.main(["--control", str(control), "--reason", "Testing", "--key", KEY_TEXT],
                           out=io.StringIO()) == 2


def test_a_wrong_key_writes_nothing_and_does_not_repeat_it(control: Path) -> None:
    code, text = _run(control, key="not a key")
    assert code == 1 and "not a key" not in text
    assert not (control / "pause").exists()


@pytest.mark.parametrize("reason", ["", "   ", "two\nlines", "x" * 201])
def test_a_reason_is_one_plain_line(control: Path, reason: str) -> None:
    code, _ = _run(control, reason=reason)
    assert code == 1 and not (control / "pause").exists()


def test_a_control_folder_that_is_not_there_is_refused(tmp_path: Path, control: Path) -> None:
    code, text = _run(tmp_path / "nowhere")
    assert code == 1 and "control folder" in text


def test_there_is_no_resume(control: Path) -> None:
    assert sign_pause.main(["--control", str(control), "--reason", "Testing", "--resume"],
                           out=io.StringIO()) == 2


def test_a_missing_argument_is_a_wrong_command_line(control: Path) -> None:
    assert sign_pause.main(["--control", str(control)], out=io.StringIO()) == 2


def test_its_throwaway_folder_is_gone_afterwards(control: Path, tmp_path: Path) -> None:
    _run(control)
    assert list((tmp_path / "temp").iterdir()) == []
