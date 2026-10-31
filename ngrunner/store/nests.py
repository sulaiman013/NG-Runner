"""
Nests: one row per material of a job, holding the nest guard's last verdict (build spec section 4;
design 5, "A pass").

Why it exists: the board, the digest and the heartbeat read the verdict without reading G:, and the
state a material was in is what tells a change worth telling someone from a poll that found nothing
new. guards.nest_state.decide() is what reaches a state: this only keeps it.

A pass is stored as the .opt's hash, the programs' hashes (manifest) and the rooms' hashes, and a
change to any of them puts the material back to NOT CHECKED. So a row in state "nest_checked" with
any of those missing is refused here: a pass that names nothing could never be seen to change.
studio_id may be None, since a folder with no Fab row still counts. posted_at stays None for a
material nothing was posted to since install, which is what tells a posted NOT CHECKED, which
alarms, from an old one, which never does.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from ngrunner.store.db import (
    KEEP, Db, Keep, dumps, iso, iso_or_none, key_text, loads, one_of, session, sha256_value,
    text_value, time_of, time_or_none,
)

# The values of guards.nest_state.NestState, kept here so the store needs nothing from the guards.
# tests/test_guards_nest_state.py fails if the two ever differ.
NEST_STATES = ("not_posted", "not_checked", "save_nest", "do_not_cut", "nest_checked")
NEST_BASES = ("programs", "nest")


@dataclass(frozen=True)
class NestRecord:
    """One material of one NG Jobs folder. basis: what the verdict rests on, "programs" or "nest".
    reasons: the verdict's notes, as the board shows them. manifest maps each program's name to its
    sha256, room_hashes each room file's. trial: its lines still say "(trial)"."""

    folder: str
    material: str
    studio_id: str | None
    state: str
    basis: str
    reasons: tuple[str, ...]
    opt_sha256: str | None
    manifest: dict[str, str]
    room_hashes: dict[str, str]
    posted_at: datetime | None
    checked_at: datetime | None
    trial: bool
    george_told_at: datetime | None
    updated_at: datetime


def add_nest(
    db: Db,
    *,
    folder: str,
    material: str,
    state: str,
    basis: str,
    updated_at: datetime,
    studio_id: str | None = None,
    reasons: Sequence[str] = (),
    opt_sha256: str | None = None,
    manifest: Mapping[str, str] | None = None,
    room_hashes: Mapping[str, str] | None = None,
    posted_at: datetime | None = None,
    checked_at: datetime | None = None,
    trial: bool = True,
    george_told_at: datetime | None = None,
) -> NestRecord:
    """Record a material seen for the first time. One already there is refused (StoreError): change
    it with update_nest. So is a job the store doesn't have (its foreign key). A pass must say what
    it passed on (ValueError)."""
    key = _key(folder, material)
    values = _checked({
        "studio_id": studio_id, "state": state, "basis": basis, "reasons": reasons,
        "opt_sha256": opt_sha256, "manifest": manifest, "room_hashes": room_hashes,
        "posted_at": posted_at, "checked_at": checked_at, "trial": trial,
        "george_told_at": george_told_at, "updated_at": updated_at,
    })
    _refuse_a_pass_that_names_nothing(values)
    with session(db, write=True) as conn:
        columns = ", ".join(values)   # names from this function, values as parameters
        marks = ", ".join("?" for _ in values)
        conn.execute(
            f"INSERT INTO nests (folder, material, {columns}) VALUES (?, ?, {marks})",
            (*key, *values.values()),
        )
        return _nest(conn, key)


def get_nest(db: Db, folder: str, material: str) -> NestRecord | None:
    with session(db, write=False) as conn:
        return _nest(conn, _key(folder, material))


def list_nests(
    db: Db,
    *,
    folder: str | None = None,
    studio_id: str | None = None,
    state: str | None = None,
) -> list[NestRecord]:
    """Materials in order of folder and material; only those given."""
    clauses, params = [], []
    for column, value in (
        ("folder", None if folder is None else text_value(folder, "a folder name")),
        ("studio_id", None if studio_id is None else key_text(studio_id, "a Studio project ID")),
        ("state", None if state is None else one_of(state, NEST_STATES, "a material's state")),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")  # column names from this list, values as parameters
            params.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with session(db, write=False) as conn:
        rows = conn.execute(
            f"SELECT * FROM nests{where} ORDER BY folder, material", params
        ).fetchall()
    return [_record(r) for r in rows]


def update_nest(
    db: Db,
    folder: str,
    material: str,
    *,
    now: datetime,
    state: str | Keep = KEEP,
    basis: str | Keep = KEEP,
    reasons: Sequence[str] | Keep = KEEP,
    studio_id: str | None | Keep = KEEP,
    opt_sha256: str | None | Keep = KEEP,
    manifest: Mapping[str, str] | None | Keep = KEEP,
    room_hashes: Mapping[str, str] | None | Keep = KEEP,
    posted_at: datetime | None | Keep = KEEP,
    checked_at: datetime | None | Keep = KEEP,
    trial: bool | Keep = KEEP,
    george_told_at: datetime | None | Keep = KEEP,
) -> NestRecord:
    """Change only the fields given (None clears one; KEEP, the default, leaves it), stamp
    updated_at, and return the material as it now is. A pass must still say what it passed on
    (ValueError). No such material: LookupError."""
    key = _key(folder, material)
    given = {
        "state": state, "basis": basis, "reasons": reasons, "studio_id": studio_id,
        "opt_sha256": opt_sha256, "manifest": manifest, "room_hashes": room_hashes,
        "posted_at": posted_at, "checked_at": checked_at, "trial": trial,
        "george_told_at": george_told_at, "updated_at": now,
    }
    changes = _checked({field: value for field, value in given.items() if value is not KEEP})
    with session(db, write=True) as conn:
        current = conn.execute(
            "SELECT * FROM nests WHERE folder = ? AND material = ?", key
        ).fetchone()
        if current is None:
            raise LookupError("there is no such material in the store")
        _refuse_a_pass_that_names_nothing({**dict(current), **changes})
        # column names come from _checked (this code), values are parameters
        sets = ", ".join(f"{column} = ?" for column in changes)
        conn.execute(
            f"UPDATE nests SET {sets} WHERE folder = ? AND material = ?",
            (*changes.values(), *key),
        )
        return _nest(conn, key)


def _key(folder: object, material: object) -> tuple[str, str]:
    return text_value(folder, "a folder name"), text_value(material, "a material")


def _checked(fields: dict[str, object]) -> dict[str, object]:
    """The fields given, each refused (ValueError) when it breaks the contract, as stored."""
    checks = {
        "studio_id": lambda v: None if v is None else key_text(v, "a Studio project ID"),
        "state": lambda v: one_of(v, NEST_STATES, "a material's state"),
        "basis": lambda v: one_of(v, NEST_BASES, "a material's basis"),
        "reasons": _reasons,
        "opt_sha256": lambda v: None if v is None else sha256_value(v),
        "manifest": _hashes,
        "room_hashes": _hashes,
        "posted_at": lambda v: iso_or_none(v, "posted_at"),
        "checked_at": lambda v: iso_or_none(v, "checked_at"),
        "trial": _flag,
        "george_told_at": lambda v: iso_or_none(v, "george_told_at"),
        "updated_at": lambda v: iso(v, "updated_at"),
    }
    return {field: checks[field](value) for field, value in fields.items()}


def _reasons(value: object) -> str:
    if not isinstance(value, list | tuple) or not all(isinstance(r, str) for r in value):
        raise ValueError("a material's reasons must be a list of sentences")
    return dumps(list(value))


def _hashes(value: object) -> str:
    if value is None:
        return "{}"
    if not isinstance(value, Mapping):
        raise ValueError("hashes must map each file's name to its sha256")
    return dumps({text_value(k, "a file's name"): sha256_value(v) for k, v in value.items()})


def _flag(value: object) -> int:
    if not isinstance(value, bool):
        raise ValueError("trial must be True or False")
    return int(value)


def _refuse_a_pass_that_names_nothing(values: dict[str, object]) -> None:
    if values.get("state") != "nest_checked":
        return
    named = (values.get("opt_sha256"), values.get("checked_at"),
             values.get("manifest") not in (None, "{}"),
             values.get("room_hashes") not in (None, "{}"))
    if not all(named):
        raise ValueError(
            "a pass must name the nest's hash, the programs' hashes, the rooms' hashes and when"
        )


def _nest(conn: sqlite3.Connection, key: tuple[str, str]) -> NestRecord | None:
    row = conn.execute("SELECT * FROM nests WHERE folder = ? AND material = ?", key).fetchone()
    return None if row is None else _record(row)


def _record(row: sqlite3.Row) -> NestRecord:
    return NestRecord(
        folder=row["folder"],
        material=row["material"],
        studio_id=row["studio_id"],
        state=row["state"],
        basis=row["basis"],
        reasons=tuple(loads(row["reasons"])),
        opt_sha256=row["opt_sha256"],
        manifest=loads(row["manifest"]),
        room_hashes=loads(row["room_hashes"]),
        posted_at=time_or_none(row["posted_at"]),
        checked_at=time_or_none(row["checked_at"]),
        trial=bool(row["trial"]),
        george_told_at=time_or_none(row["george_told_at"]),
        updated_at=time_of(row["updated_at"]),
    )
