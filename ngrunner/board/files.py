r"""
"Open" on the board: show Jim a file or folder NG Runner names, and nothing else.

The path comes back from the page in a form field, so the board treats it as untrusted and
opens it only when all of these hold (build-spec 6.6: "Open file" opens only paths inside the
configured roots):

- it is a full path with a drive, with no \\.\ device form and no ":" stream after the drive;
- it exists, and with every link, junction and ".." followed it is still inside one of the
  roots (each root followed the same way), so a link in NG Jobs can't lead out of it; a
  whole drive (C:\) is never taken as a root;
- it is a folder, a PDF or a text file. NG Jobs is a shared Drive folder anyone in the shop can
  drop files into, and Windows "opens" a program or a shortcut by running it.

What was checked is what is opened: the resolved path, never the text that came in.
"""
from __future__ import annotations

import ntpath
import os
from collections.abc import Callable, Sequence
from pathlib import Path

OPENABLE_SUFFIXES = (".pdf", ".txt")
_LONG_PREFIX, _LONG_UNC_PREFIX = "\\\\?\\", "\\\\?\\UNC\\"
_DEVICE_PREFIX = "\\\\.\\"


def open_inside(text: str, roots: Sequence[Path], opener: Callable[[str], None]) -> str | None:
    """Open text's file or folder with opener when the rules above allow it; otherwise, or if
    Windows can't open it, return why not in plain words. None means it was opened."""
    raw = _strip_long_prefix(text.strip())
    if not _is_full_path(raw) or ":" in ntpath.splitroot(raw)[2]:
        return "NG Runner can only open a file it names itself."
    try:
        resolved = _resolve(raw, strict=True)
    except OSError:
        return f"{ntpath.basename(raw) or raw} is not there any more."
    if not any(_inside(resolved, _resolve(str(root), strict=False)) for root in roots):
        return f"{resolved.name or resolved} is outside the folders NG Runner works in, so it was not opened."
    if not (resolved.is_dir() or (resolved.is_file()
                                  and resolved.suffix.casefold() in OPENABLE_SUFFIXES)):
        return f"{resolved.name} was not opened: NG Runner only opens folders, PDFs and text files."
    try:
        opener(str(resolved))
    except OSError:
        return f"Windows could not open {resolved.name or resolved}."
    return None


def _resolve(raw: str, *, strict: bool) -> Path:
    """Absolute, with links, junctions and '..' followed, and no long-path prefix."""
    return Path(_strip_long_prefix(os.path.realpath(raw, strict=strict)))


def _inside(path: Path, root: Path) -> bool:
    """path is root or below it, compared by name, case-folded (both already resolved)."""
    a = tuple(part.casefold() for part in root.parts)
    b = tuple(part.casefold() for part in path.parts)
    return len(a) > 1 and b[: len(a)] == a


def _strip_long_prefix(raw: str) -> str:
    if raw[: len(_LONG_UNC_PREFIX)].upper() == _LONG_UNC_PREFIX:
        return "\\\\" + raw[len(_LONG_UNC_PREFIX):]
    return raw.removeprefix(_LONG_PREFIX)


def _is_full_path(raw: str) -> bool:
    if raw.startswith(_DEVICE_PREFIX):
        return False
    drive, root, _ = ntpath.splitroot(raw)
    return bool(drive and root)
