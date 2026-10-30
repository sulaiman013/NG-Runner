r"""
Pause NG Runner from anywhere: a signed pause file in the control folder (build spec section 3).

Why a file: pausing is always safe, so a file is enough, and it works when nobody can reach Jim's
PC. NG Runner reads control\pause\ every minute, and takes a file only if its signature verifies,
its kind is pause, its seq is above the last it accepted and its id is new. So the seq is the current
Unix second, as every stream's start is, and needs no memory here. There is no resume file: resume
happens only at the PC (build spec section 3).

    python tools\sign_pause.py --control <control folder> --reason "Why, in plain words"

The key is asked for, hidden, and never taken from the command line, printed or kept. The file goes
in through commit_write (AGENTS.md rule 1), with the control folder as its control root and a
throwaway data folder of its own. Exit code: 0 written, 1 refused, 2 wrong command line.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run as a script, from the repo

from ngrunner.clock import Clock, SystemClock  # noqa: E402
from ngrunner.signing import EnvelopeError, key_from_text, new_id, seal  # noqa: E402
from ngrunner.writes import FileWrite, Roots, commit_write  # noqa: E402

KIND = "pause"
FOLDER = "pause"            # in the control folder
REASON_LIMIT = 200


def main(argv: list[str] | None = None, *, out: TextIO | None = None,
         read_secret: Callable[[str], str] = getpass.getpass, clock: Clock | None = None) -> int:
    say = (out if out is not None else sys.stdout).write
    parser = argparse.ArgumentParser(prog="sign_pause", description="Pause NG Runner with a file.")
    parser.add_argument("--control", required=True, help="the control folder, as a full path")
    parser.add_argument("--reason", required=True, help="why, in one plain line")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    control, reason = Path(args.control), args.reason.strip()
    if not (reason and len(reason) <= REASON_LIMIT and reason.isprintable()):
        say(f"The reason must be one plain line of up to {REASON_LIMIT} characters.\n")
        return 1
    if not (control.is_absolute() and control.is_dir()):
        say("The control folder must be a full path to a folder that is there.\n")
        return 1
    try:
        key = key_from_text(read_secret("The control folder's signing key (it won't show): "))
    except (EnvelopeError, EOFError):
        say("That isn't the signing key: it is 64 characters, 0-9 and a-f. Nothing was written.\n")
        return 1
    now = (clock or SystemClock()).now()
    file_id = new_id()
    data = seal(KIND, {"reason": reason}, key=key, seq=int(now.timestamp()), issued_at=now,
                id=file_id)
    return _write(control / FOLDER / f"{file_id}.json", data, control, say)


def _write(path: Path, data: bytes, control: Path, say: Callable[[str], object]) -> int:
    with tempfile.TemporaryDirectory(prefix="ngr-pause-", ignore_cleanup_errors=True) as temp:
        local = Path(temp)
        (local / "app_data").mkdir()
        roots = Roots(local / "NG Jobs", local / "Mozaik Shared", control, local / "app_data")
        result = commit_write([FileWrite(path, data, None)], roots=roots, interlock=_clear)
    if not result.ok:
        say(f"The pause file was not written: {result.reason}\n")
        return 1
    say(f"Pause file written: {os.path.basename(path)}. NG Runner pauses within a minute of Drive"
        " bringing it to Jim's PC. Resume only at the PC.\n")
    return 0


def _clear(paths: list[Path]) -> None:
    """A pause file goes in whatever NG Runner is doing: no Mozaik job reads the control folder."""
    return None


if __name__ == "__main__":
    raise SystemExit(main())
