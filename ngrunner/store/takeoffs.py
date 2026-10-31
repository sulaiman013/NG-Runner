"""
Takeoffs: NG Runner's own takeoff of each IC summary it files (docs/p2/build-spec.md, section 4;
P3's "takeoff on intake").

Why it exists: the order heads-up, the board, the digest and the job's notes file all read a job's
takeoff, and the converter takes a second or two, so it runs once, off the tick's thread, and its
result is kept here. One row per filed summary (studio_id and the render's sha256) and rules
version (rules.fingerprint()), so a takeoff always names the summary and the rules that made it.

state says what became of the summary:
    made     the converter ran on a closet. complete is False when any line could not be placed
             or any bucket has no sheet size, and could_not_verify counts those lines. result is
             the takeoff, codes and numbers only.
    in_chat  a garage, mixed or unclear job: it stays in chat until BUILD's garage rule is ported
             (ruling R4), so there is no takeoff.
    failed   the summary could not be read for a takeoff. There is no takeoff, and the chat's is
             used as before.
Nothing here holds a row's text: result is made by the pipeline from the converter's codes and
numbers, and this module refuses a result that isn't a mapping.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ngrunner.store.db import (
    Db, dumps, iso, key_text, loads, one_of, session, sha256_value, time_of, whole_number,
)

TAKEOFF_STATES = ("made", "in_chat", "failed")
# convert.takeoff.BRANCHES, and "" for a summary that could not be read far enough to say.
TAKEOFF_BRANCHES = ("closet", "garage", "mixed", "unclear", "")


@dataclass(frozen=True)
class TakeoffRecord:
    """A filed summary's takeoff. result is None unless state is "made"."""

    studio_id: str
    render_sha256: str
    rules_sha256: str
    state: str
    branch: str
    complete: bool
    could_not_verify: int
    result: Mapping[str, Any] | None
    at: datetime


def add_takeoff(
    db: Db,
    *,
    studio_id: str,
    render_sha256: str,
    rules_sha256: str,
    state: str,
    branch: str,
    complete: bool,
    could_not_verify: int,
    result: Mapping[str, Any] | None,
    at: datetime,
) -> TakeoffRecord:
    """Record the takeoff of one summary under one rules version, in place of any earlier one for
    the same pair. A row that breaks the contract above is refused (ValueError), nothing stored."""
    values = (
        key_text(studio_id, "a Studio project ID"),
        sha256_value(render_sha256, "a render's sha256"),
        sha256_value(rules_sha256, "the rules' fingerprint"),
        one_of(state, TAKEOFF_STATES, "a takeoff's state"),
        one_of(branch, TAKEOFF_BRANCHES, "a takeoff's branch"),
        _flag(complete),
        whole_number(could_not_verify, "a count of lines that could not be verified", at_least=0),
        _result(state, result, complete),
        iso(at, "at"),
    )
    if values[5] and values[6]:
        raise ValueError("a complete takeoff has no line it could not verify")
    with session(db, write=True) as conn:
        conn.execute(
            "INSERT INTO takeoffs (studio_id, render_sha256, rules_sha256, state, branch,"
            " complete, could_not_verify, result, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (studio_id, render_sha256, rules_sha256) DO UPDATE SET"
            " state = excluded.state, branch = excluded.branch, complete = excluded.complete,"
            " could_not_verify = excluded.could_not_verify, result = excluded.result,"
            " at = excluded.at",
            values,
        )
        row = conn.execute(
            "SELECT * FROM takeoffs WHERE studio_id = ? AND render_sha256 = ? AND rules_sha256 = ?",
            values[:3]).fetchone()
    return _record(row)


def get_takeoff(db: Db, studio_id: str, render_sha256: str) -> TakeoffRecord | None:
    """The newest takeoff of this summary, whichever rules version made it."""
    with session(db, write=False) as conn:
        row = conn.execute(
            "SELECT * FROM takeoffs WHERE studio_id = ? AND render_sha256 = ?"
            " ORDER BY at DESC, rowid DESC LIMIT 1",
            (key_text(studio_id, "a Studio project ID"),
             sha256_value(render_sha256, "a render's sha256"))).fetchone()
    return None if row is None else _record(row)


def list_takeoffs(db: Db, studio_id: str | None = None) -> list[TakeoffRecord]:
    """Every takeoff, or one job's, oldest first."""
    query, params = "SELECT * FROM takeoffs", ()
    if studio_id is not None:
        query = query + " WHERE studio_id = ?"
        params = (key_text(studio_id, "a Studio project ID"),)
    with session(db, write=False) as conn:
        rows = conn.execute(query + " ORDER BY at, rowid", params).fetchall()
    return [_record(r) for r in rows]


def _flag(value: object) -> int:
    if not isinstance(value, bool):
        raise ValueError("complete must be True or False")
    return int(value)


def _result(state: str, result: object, complete: bool) -> str | None:
    if state != "made":
        if result is not None or complete:
            raise ValueError("only a takeoff that was made holds a result or is complete")
        return None
    if not isinstance(result, Mapping):
        raise ValueError("a takeoff that was made holds its result, as a mapping")
    return dumps(dict(result))


def _record(row: sqlite3.Row) -> TakeoffRecord:
    return TakeoffRecord(
        studio_id=row["studio_id"], render_sha256=row["render_sha256"],
        rules_sha256=row["rules_sha256"], state=row["state"], branch=row["branch"],
        complete=bool(row["complete"]), could_not_verify=row["could_not_verify"],
        result=None if row["result"] is None else loads(row["result"]), at=time_of(row["at"]))
