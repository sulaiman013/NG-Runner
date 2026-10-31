"""
Clearances: Jim's click saying a program past its sheet size is on a sheet he added on purpose
(build spec section 4 and 5.6; docs/decisions.md, Q3a).

Why it is so narrow: DO NOT CUT is never silenced, with this one exception, because otherwise Jim's
own 5x9 stays red for good. So a clearance is bound to that one program's sha256, on that one job
and material, and a re-post, having another hash, asks again. Only a sheet size can be cleared:
another kind is refused here, and cleared_programs() reads only this kind, whatever else a row may
say. And one comes only from Jim's answer: the card must be an answered sheet size card, answered
with its first choice, the one that clears, so "Re-nest it" can never write one by mistake.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ngrunner.store.db import Db, iso, key_text, session, sha256_value, text_value, time_of

SHEET_SIZE = "sheet_size"    # what can be cleared; the card kind of the same name asks for it


@dataclass(frozen=True)
class ClearanceRecord:
    """One click: the program (by hash) on one material of one folder, what it cleared and the card
    it answered."""

    id: int
    folder: str
    material: str
    program_sha256: str
    what: str
    card_id: str
    at: datetime


def add_clearance(
    db: Db,
    *,
    folder: str,
    material: str,
    program_sha256: str,
    card_id: str,
    at: datetime,
    what: str = SHEET_SIZE,
) -> ClearanceRecord:
    """Record Jim's answer to a sheet size card, for that program's hash: pass the transaction that
    acts on the answer. Anything but a sheet size is refused (ValueError), and so is a card that
    isn't an answered sheet size card answered with its first choice. No such card: LookupError."""
    if what != SHEET_SIZE:
        raise ValueError("only a sheet size can be cleared, never doubled holes or a failed check")
    values = (text_value(folder, "a folder name"), text_value(material, "a material"),
              sha256_value(program_sha256, "a program's sha256"), what,
              key_text(card_id, "a card ID"), iso(at, "at"))
    with session(db, write=True) as conn:
        _refuse_all_but_jims_answer(conn, values[4])
        cursor = conn.execute(
            "INSERT INTO clearances (folder, material, program_sha256, what, card_id, at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            values,
        )
        row = conn.execute("SELECT * FROM clearances WHERE id = ?",
                           (int(cursor.lastrowid),)).fetchone()
        return _record(row)


def list_clearances(
    db: Db, *, folder: str | None = None, material: str | None = None
) -> list[ClearanceRecord]:
    """Clearances in the order they were given; only the folder's and material's, when given."""
    clauses, params = [], []
    for column, value in (
        ("folder", None if folder is None else text_value(folder, "a folder name")),
        ("material", None if material is None else text_value(material, "a material")),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")  # column names from this list, values as parameters
            params.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with session(db, write=False) as conn:
        rows = conn.execute(f"SELECT * FROM clearances{where} ORDER BY id", params).fetchall()
    return [_record(r) for r in rows]


def cleared_programs(db: Db, folder: str, material: str) -> frozenset[str]:
    """The hashes of the programs whose sheet size Jim cleared on this material of this job: what
    guards.nest_state.MaterialEvidence takes as cleared."""
    key = (text_value(folder, "a folder name"), text_value(material, "a material"), SHEET_SIZE)
    with session(db, write=False) as conn:
        rows = conn.execute(
            "SELECT program_sha256 FROM clearances WHERE folder = ? AND material = ? AND what = ?",
            key,
        ).fetchall()
    return frozenset(row["program_sha256"] for row in rows)


def _refuse_all_but_jims_answer(conn: sqlite3.Connection, card_id: str) -> None:
    card = conn.execute(
        "SELECT kind, state, answer, json_extract(choices, '$[0]') AS clears FROM cards"
        " WHERE id = ?",
        (card_id,),
    ).fetchone()
    if card is None:
        raise LookupError("there is no card with that ID in the store")
    if card["kind"] != SHEET_SIZE:
        raise ValueError("a clearance comes only from a sheet size card")
    if card["state"] != "answered":
        raise ValueError("a clearance comes only from a card Jim has answered")
    if card["answer"] != card["clears"]:
        raise ValueError("a clearance comes only from the answer that says Jim added the sheet")


def _record(row: sqlite3.Row) -> ClearanceRecord:
    return ClearanceRecord(
        id=row["id"],
        folder=row["folder"],
        material=row["material"],
        program_sha256=row["program_sha256"],
        what=row["what"],
        card_id=row["card_id"],
        at=time_of(row["at"]),
    )
