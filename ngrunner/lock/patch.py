r"""
Byte patches on a room's .des (RULES section 6, "Editing a .des safely"; design 3.3, "Write").

Why bytes: a .des is pure CRLF with a version line before its XML, and a text-mode read or a
re-serialised tree changes every line. A fix is therefore kept as the bytes it changed: each edit is an
offset in the file it was made from, the bytes there before and the bytes after. A patch applies only to
the very bytes it was made from (their sha256) and must give the very bytes it was made to give, so a
patch can never land on a room that has moved on. Its inverse, offsets moved into the fixed file's
place, gives back the original bytes exactly: that is Undo while nothing else changed.

des_problem is RULES section 6's commit gate, the part that is about bytes: UTF-8, the same version
line, pure CRLF, the same line count, and XML that parses. What a fix may change is its fixer's to say.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any
from xml.etree import ElementTree


class PatchRefused(Exception):
    """A patch that can't be applied to the bytes given. Its text is plain."""


@dataclass(frozen=True)
class Edit:
    offset: int
    old: bytes
    new: bytes


@dataclass(frozen=True)
class Patch:
    base_sha256: str
    result_sha256: str
    edits: tuple[Edit, ...]

    def as_json(self) -> list[dict[str, Any]]:
        return [{"offset": e.offset, "old": e.old.hex(), "new": e.new.hex()} for e in self.edits]

    @staticmethod
    def from_json(base_sha256: str, result_sha256: str, items: Sequence[Mapping[str, Any]]) -> Patch:
        return Patch(base_sha256, result_sha256,
                     tuple(Edit(int(i["offset"]), bytes.fromhex(i["old"]), bytes.fromhex(i["new"]))
                           for i in items))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def diff(base: bytes, result: bytes) -> Patch:
    """The edits that make result of base, each trimmed to the bytes that differ. Lines are compared
    pairwise when the line count holds, which is the case RULES allows; otherwise matched up."""
    a, b = base.splitlines(keepends=True), result.splitlines(keepends=True)
    starts, at = [], 0
    for line in a:
        starts.append(at)
        at += len(line)
    starts.append(at)
    edits: list[Edit] = []
    if len(a) == len(b):
        spans = [(i, i + 1, i, i + 1) for i, (x, y) in enumerate(zip(a, b)) if x != y]
    else:
        spans = [(i1, i2, j1, j2) for tag, i1, i2, j1, j2 in
                 SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if tag != "equal"]
    for i1, i2, j1, j2 in spans:
        old, new = b"".join(a[i1:i2]), b"".join(b[j1:j2])
        head = _common_head(old, new)
        tail = _common_head(old[head:][::-1], new[head:][::-1])
        edits.append(Edit(starts[i1] + head, old[head:len(old) - tail], new[head:len(new) - tail]))
    return Patch(sha256(base), sha256(result), tuple(edits))


def apply_patch(patch: Patch, data: bytes) -> bytes:
    """patch applied to data, which must be the very bytes it was made from."""
    if sha256(data) != patch.base_sha256:
        raise PatchRefused("The file isn't the one the patch was made from.")
    out, at = [], 0
    for edit in sorted(patch.edits, key=lambda e: e.offset):
        if edit.offset < at or data[edit.offset:edit.offset + len(edit.old)] != edit.old:
            raise PatchRefused("The patch doesn't fit the file.")
        out += [data[at:edit.offset], edit.new]
        at = edit.offset + len(edit.old)
    result = b"".join([*out, data[at:]])
    if sha256(result) != patch.result_sha256:
        raise PatchRefused("The patch didn't give the bytes it was made to give.")
    return result


def inverse(patch: Patch) -> Patch:
    """The patch that takes the result back to the base, byte for byte."""
    edits, shift = [], 0
    for edit in sorted(patch.edits, key=lambda e: e.offset):
        edits.append(Edit(edit.offset + shift, edit.new, edit.old))
        shift += len(edit.new) - len(edit.old)
    return Patch(patch.result_sha256, patch.base_sha256, tuple(edits))


def des_problem(base: bytes, result: bytes) -> str | None:
    """Why result can't stand in for base as a room's .des, or None."""
    try:
        result.decode("utf-8")
    except UnicodeDecodeError:
        return "isn't UTF-8 any more"
    if base.split(b"\r\n", 1)[0] != result.split(b"\r\n", 1)[0]:
        return "has another version line"
    if result.count(b"\r\n") != result.count(b"\n"):
        return "has a line that doesn't end in CRLF"
    if result.count(b"\n") != base.count(b"\n"):
        return "has another number of lines"
    start = result.find(b"<?xml")
    if start < 0:
        return "has no XML in it"
    try:
        ElementTree.fromstring(result[start:])
    except ElementTree.ParseError:
        return "isn't XML that reads"
    return None


def _common_head(a: bytes, b: bytes) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n
