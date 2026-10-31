"""
What the guards keep in the state table: its keys, and what the rest of NG Runner reads from them.

Why it is a module of its own: the pipeline acts on an answer, the job model shows what a pass found,
and watch.py, which imports the pipeline, writes it. This imports nothing from any of them.

The main thing here is what a sheet size card asked. The cards table holds a question's words and
choices, and has nowhere for what an answer must act on: the folder, the material and each program's
hash (P1b build spec 5.6). The design check keeps its facts the same way. An answer clears exactly
the programs its own card named, so a program posted after it was asked is never cleared by a click
that couldn't have meant it.

A sheet size card can be about a folder with no Fab row, so it can have no Studio ID and no row. This
is then the only thing that says what the card is about, and folder_of_card is how the board and the
command box name it. Nothing here reads a file.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ngrunner.guards.labels import LabelsMatch
from ngrunner.store import Db, clear_state, get_state, list_nests, set_state, states_under

_CARD = "guards.card."
_SIZES = "guards.sizes."
_REDO = "guards.redo"
TAKEOFFS = "guards.takeoffs"            # JSON: per folder, its takeoff as last seen and last read
LOCK_AT = "guards.lock_at"              # when the pre-flight last read the tracker
LOCK_PREFIX = "guards.lock."            # the pre-flight per folder, for the board and the notes
LABELS_PREFIX = "guards.labels."        # what its labels showed, and which files showed it
NEST_FILES = "guards.nest_files"        # JSON: per job, what its nests, rooms and locks last showed
MOZAIK_RUNNING = "guards.mozaik_running"  # JSON: true, false or null, as the observer last found it


@dataclass(frozen=True)
class AskedProgram:
    """One program a card asked about. on: the sheet it reads as, or None for one nothing fits."""

    name: str
    sha256: str
    on: str | None


@dataclass(frozen=True)
class SizeQuestion:
    """What one sheet size card asked. expected: the sheet this material's rule says."""

    folder: str
    material: str
    expected: str
    programs: tuple[AskedProgram, ...]

    def named(self) -> tuple[tuple[str, str | None], ...]:
        """The programs as the templates take them: (name, sheet)."""
        return tuple((program.name, program.on) for program in self.programs)

    def hashes(self) -> frozenset[str]:
        return frozenset(program.sha256 for program in self.programs)


def _card_key(card_id: str) -> str:
    return f"{_CARD}{card_id.lower().replace('-', '.')}"


def place_key(prefix: str, folder: str, material: str = "") -> str:
    """A state key for a folder or a material: their names can hold anything, and a key can't."""
    digest = hashlib.sha256(f"{folder}|{material}".encode()).hexdigest()[:16]
    return f"{prefix}{digest}"


def keep_question(db: Db, card_id: str, question: SizeQuestion) -> None:
    set_state(db, _card_key(card_id), json.dumps({
        "folder": question.folder, "material": question.material, "expected": question.expected,
        "programs": [[p.name, p.sha256, p.on] for p in question.programs]}, ensure_ascii=False))


def question_of(db: Db, card_id: str) -> SizeQuestion | None:
    text = get_state(db, _card_key(card_id))
    if not text:
        return None
    said = json.loads(text)
    return SizeQuestion(said["folder"], said["material"], said["expected"],
                        tuple(AskedProgram(*program) for program in said["programs"]))


def forget_question(db: Db, card_id: str) -> None:
    clear_state(db, _card_key(card_id))


def folder_of_card(db: Db, card_id: str) -> str | None:
    """The folder a card is about, for one with no job and no row to name it by."""
    question = question_of(db, card_id)
    return None if question is None else question.folder


# ----- what Jim said no to, and what is waiting to be judged again


def declined_hashes(db: Db, folder: str, material: str) -> frozenset[str]:
    """Programs he answered "Re-nest it" about: they stay DO NOT CUT, and aren't asked about again.
    A re-post has another hash, so it asks again."""
    return frozenset(json.loads(get_state(db, place_key(_SIZES, folder, material)) or "[]"))


def note_declined(db: Db, question: SizeQuestion) -> None:
    key = place_key(_SIZES, question.folder, question.material)
    held = set(json.loads(get_state(db, key) or "[]")) | question.hashes()
    set_state(db, key, json.dumps(sorted(held)))


def redo_places(db: Db) -> frozenset[tuple[str, str]]:
    """The materials to read and judge again at the next pass: an answer changed what counts. The
    store keeps only a count of a program's near pairs, so a verdict is made from the files."""
    return frozenset((place[0], place[1]) for place in json.loads(get_state(db, _REDO) or "[]"))


def ask_for_redo(db: Db, folder: str, material: str) -> None:
    places = {*redo_places(db), (folder, material)}
    set_state(db, _REDO, json.dumps(sorted(places), ensure_ascii=False))


def done_redo(db: Db, folder: str, material: str) -> None:
    places = redo_places(db) - {(folder, material)}
    set_state(db, _REDO, json.dumps(sorted(places), ensure_ascii=False))


# ----- what the labels showed, and the pre-flight


def labels_of(db: Db, folder: str) -> LabelsMatch | None:
    text = get_state(db, place_key(LABELS_PREFIX, folder))
    if not text:
        return None
    said = json.loads(text)
    return LabelsMatch(said["code"], tuple(said.get("missing", ())), said.get("compared", 0),
                       said.get("not_compared", 0))


def labels_seen(db: Db) -> dict[str, str]:
    """Each folder's labels as last read, so an unchanged file isn't read again."""
    said = [json.loads(text) for text in states_under(db, LABELS_PREFIX).values()]
    return {record["folder"]: record["stamp"] for record in said}


def ask_again(db: Db, folder: str) -> None:
    """A folder's labels now read otherwise, so its materials' notes are out of date."""
    for nest in list_nests(db, folder=folder):
        if nest.posted_at is not None:
            ask_for_redo(db, folder, nest.material)


def saved_preflights(db: Db) -> dict[str, dict[str, Any]]:
    """Each folder's last pre-flight, as the board and the notes file read it, by folder."""
    said = [json.loads(text) for text in states_under(db, LOCK_PREFIX).values()]
    return {record["folder"]: record for record in said}


def takeoffs_said(db: Db) -> dict[str, dict[str, Any]]:
    return json.loads(get_state(db, TAKEOFFS) or "{}")


# ----- the nests, rooms and lock files in Mozaik Shared (guards/nestfiles.py)


def nest_files_said(db: Db) -> dict[str, dict[str, Any]] | None:
    """Each job's files as listings last showed them; None until the first listing, which is what
    marks all of them as from before install."""
    text = get_state(db, NEST_FILES)
    return None if text is None else json.loads(text)


def mozaik_said(db: Db) -> tuple[bool, bool | None]:
    """Whether the observer has looked yet, and what it found: True, False or None for can't tell."""
    text = get_state(db, MOZAIK_RUNNING)
    return (False, None) if text is None else (True, json.loads(text))
