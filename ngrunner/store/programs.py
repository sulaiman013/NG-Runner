"""
Programs: one row per CNC program (.TAP) seen in a material folder of NG Jobs (build spec
section 4).

Why it exists: what the nest guard knows about a program has to outlast the poll that read it, so
a restart neither re-alarms nor forgets, and a program only counts as new once it differs from what
the first scan saw. folder is the NG Jobs folder, not a job, so a folder with no Fab row still
counts.

States: "seen_at_install" (there at the first scan and unchanged, so it raises no alarm), "new"
(listed, not read yet) and "read" (read once two polls agreed). A row may hold only what the
listing gives, so the first scan needn't read every program on G:; a row marked read must hold what
reading it gives, since a verdict rests on it.

Its text never leaves the program reader: near_pairs here is a count, and the pairs themselves stay
in guards.tap.Tap. mtime is the file's own time, kept as the store keeps every time.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ngrunner.store.db import (
    KEEP, Db, Keep, iso, one_of, session, sha256_value, text_value, time_of, whole_number,
)

PROGRAM_STATES = ("seen_at_install", "new", "read")
FAMILIES = ("sheet", "long", "merged", "remake", "hand")


@dataclass(frozen=True)
class ProgramRecord:
    """One program in one material folder of one NG Jobs folder. sha256, family, reach, holes and
    near_pairs come from reading it, so they are None until then; reach is also None for a program
    with no cutting moves. near_pairs counts the pairs of holes too close together."""

    folder: str
    material: str
    name: str
    sha256: str | None
    size: int
    mtime: datetime
    family: str | None
    reach: tuple[float, float] | None
    holes: int | None
    near_pairs: int | None
    seen_at: datetime
    state: str


def add_program(
    db: Db,
    *,
    folder: str,
    material: str,
    name: str,
    size: int,
    mtime: datetime,
    seen_at: datetime,
    state: str = "new",
    sha256: str | None = None,
    family: str | None = None,
    reach: tuple[float, float] | None = None,
    holes: int | None = None,
    near_pairs: int | None = None,
) -> ProgramRecord:
    """Record a program seen for the first time. One already there is refused (StoreError):
    change it with update_program. Marked read, it must hold what reading it gives (ValueError)."""
    key = _key(folder, material, name)
    values = _checked({
        "size": size, "mtime": mtime, "seen_at": seen_at, "state": state, "sha256": sha256,
        "family": family, "reach": reach, "holes": holes, "near_pairs": near_pairs,
    })
    _refuse_a_read_program_without_its_facts(values)
    with session(db, write=True) as conn:
        conn.execute(
            "INSERT INTO programs (folder, material, name, sha256, size, mtime, family, reach_x,"
            " reach_y, holes, near_pairs, seen_at, state)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*key, values["sha256"], values["size"], values["mtime"], values["family"],
             values["reach_x"], values["reach_y"], values["holes"], values["near_pairs"],
             values["seen_at"], values["state"]),
        )
        return _program(conn, key)


def get_program(db: Db, folder: str, material: str, name: str) -> ProgramRecord | None:
    with session(db, write=False) as conn:
        return _program(conn, _key(folder, material, name))


def list_programs(
    db: Db,
    *,
    folder: str | None = None,
    material: str | None = None,
    state: str | None = None,
) -> list[ProgramRecord]:
    """Programs in order of folder, material and name; only those given."""
    clauses, params = [], []
    for column, value in (
        ("folder", None if folder is None else text_value(folder, "a folder name")),
        ("material", None if material is None else text_value(material, "a material")),
        ("state", None if state is None else one_of(state, PROGRAM_STATES, "a program's state")),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")  # column names from this list, values as parameters
            params.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with session(db, write=False) as conn:
        rows = conn.execute(
            f"SELECT * FROM programs{where} ORDER BY folder, material, name", params
        ).fetchall()
    return [_record(r) for r in rows]


def update_program(
    db: Db,
    folder: str,
    material: str,
    name: str,
    *,
    state: str | Keep = KEEP,
    sha256: str | None | Keep = KEEP,
    size: int | Keep = KEEP,
    mtime: datetime | Keep = KEEP,
    family: str | None | Keep = KEEP,
    reach: tuple[float, float] | None | Keep = KEEP,
    holes: int | None | Keep = KEEP,
    near_pairs: int | None | Keep = KEEP,
    seen_at: datetime | Keep = KEEP,
) -> ProgramRecord:
    """Change only the fields given (None clears one; KEEP, the default, leaves it) and return the
    program as it now is. A program marked read must still hold what reading it gives (ValueError).
    No such program: LookupError."""
    key = _key(folder, material, name)
    given = {
        "state": state, "sha256": sha256, "size": size, "mtime": mtime, "family": family,
        "reach": reach, "holes": holes, "near_pairs": near_pairs, "seen_at": seen_at,
    }
    changes = _checked({field: value for field, value in given.items() if value is not KEEP})
    with session(db, write=True) as conn:
        current = conn.execute(
            "SELECT * FROM programs WHERE folder = ? AND material = ? AND name = ?", key
        ).fetchone()
        if current is None:
            raise LookupError("there is no such program in the store")
        _refuse_a_read_program_without_its_facts({**dict(current), **changes})
        if changes:
            # column names come from _checked (this code), values are parameters
            sets = ", ".join(f"{column} = ?" for column in changes)
            conn.execute(
                f"UPDATE programs SET {sets} WHERE folder = ? AND material = ? AND name = ?",
                (*changes.values(), *key),
            )
        return _program(conn, key)


def remove_program(db: Db, folder: str, material: str, name: str) -> bool:
    """Forget a program no longer in its folder, so a verdict doesn't rest on a file that is gone.
    True when one was removed, False when there was none."""
    key = _key(folder, material, name)
    with session(db, write=True) as conn:
        cursor = conn.execute(
            "DELETE FROM programs WHERE folder = ? AND material = ? AND name = ?", key
        )
        return cursor.rowcount == 1


def _key(folder: object, material: object, name: object) -> tuple[str, str, str]:
    return (text_value(folder, "a folder name"), text_value(material, "a material"),
            text_value(name, "a program's name"))


def _checked(fields: dict[str, object]) -> dict[str, object]:
    """The fields given, each refused (ValueError) when it breaks the contract, as stored."""
    checks = {
        "size": lambda v: whole_number(v, "a program's size", at_least=0),
        "mtime": lambda v: iso(v, "a program's mtime"),
        "seen_at": lambda v: iso(v, "seen_at"),
        "state": lambda v: one_of(v, PROGRAM_STATES, "a program's state"),
        "sha256": lambda v: None if v is None else sha256_value(v),
        "family": lambda v: None if v is None else one_of(v, FAMILIES, "a program's family"),
        "holes": lambda v: None if v is None else whole_number(v, "a hole count", at_least=0),
        "near_pairs": lambda v: None if v is None else whole_number(v, "a near pair count",
                                                                    at_least=0),
    }
    values: dict[str, object] = {}
    for field, value in fields.items():
        if field == "reach":
            values["reach_x"], values["reach_y"] = _reach(value)
        else:
            values[field] = checks[field](value)
    return values


def _reach(value: object) -> tuple[float | None, float | None]:
    if value is None:
        return None, None
    if not isinstance(value, tuple) or len(value) != 2 or not all(_is_number(v) for v in value):
        raise ValueError("a program's reach must be its largest X and Y, as two numbers")
    return float(value[0]), float(value[1])


def _is_number(value: object) -> bool:
    return (isinstance(value, int | float) and not isinstance(value, bool)
            and math.isfinite(value))


def _refuse_a_read_program_without_its_facts(values: dict[str, object]) -> None:
    if values.get("state") == "read" and any(
        values.get(field) is None for field in ("sha256", "family", "holes", "near_pairs")
    ):
        raise ValueError("a program marked read must hold its sha256, family, holes and near pairs")


def _program(conn: sqlite3.Connection, key: tuple[str, str, str]) -> ProgramRecord | None:
    row = conn.execute(
        "SELECT * FROM programs WHERE folder = ? AND material = ? AND name = ?", key
    ).fetchone()
    return None if row is None else _record(row)


def _record(row: sqlite3.Row) -> ProgramRecord:
    reach = None if row["reach_x"] is None else (row["reach_x"], row["reach_y"])
    return ProgramRecord(
        folder=row["folder"],
        material=row["material"],
        name=row["name"],
        sha256=row["sha256"],
        size=row["size"],
        mtime=time_of(row["mtime"]),
        family=row["family"],
        reach=reach,
        holes=row["holes"],
        near_pairs=row["near_pairs"],
        seen_at=time_of(row["seen_at"]),
        state=row["state"],
    )
