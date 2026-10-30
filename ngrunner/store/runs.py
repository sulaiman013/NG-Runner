"""
Runs: one row per pipeline run, NG Runner's own pass over a job or a scheduled task (build spec
section 4; design 3.3, "Run"; GLOSSARY: Pipeline run).

Why: "Ready" exists only from a completed run record (design 4, "Silence and failure"), and a
run records what it read and wrote by hash, so a later check can tell whether the files are
still the ones it worked on. A run is started, then ended once with its outcome; one that never
ended (NG Runner stopped mid-way) stays visibly unfinished.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from ngrunner.store.db import (
    Db, code_value, dumps, iso, key_text, loads, optional_text, session, sha256_value,
    text_value, time_of, time_or_none, whole_number,
)


@dataclass(frozen=True)
class RunRecord:
    """One run. stage ("fetch", "file", "digest"...), trigger ("fab_row", "night", "jim"...) and
    outcome ("ok", "blocked", "not_run"...) are short lowercase codes; reason is plain words.
    read_hashes and written_hashes map what was read or written (a name or a path) to its
    sha256."""

    id: int
    studio_id: str | None
    stage: str
    trigger: str
    started_at: datetime
    ended_at: datetime | None
    outcome: str | None
    reason: str | None
    app_version: str
    read_hashes: dict[str, str]
    written_hashes: dict[str, str]


def start_run(
    db: Db,
    *,
    stage: str,
    trigger: str,
    started_at: datetime,
    app_version: str,
    studio_id: str | None = None,
) -> RunRecord:
    """Record that a run started; end it with end_run."""
    values = (
        None if studio_id is None else key_text(studio_id, "a Studio project ID"),
        code_value(stage, "a run's stage", "file"),
        code_value(trigger, "a run's trigger", "fab_row"),
        iso(started_at, "started_at"), key_text(app_version, "an app version"),
    )
    with session(db, write=True) as conn:
        cursor = conn.execute(
            "INSERT INTO runs (studio_id, stage, trigger, started_at, app_version)"
            " VALUES (?, ?, ?, ?, ?)",
            values,
        )
        return _run(conn, int(cursor.lastrowid))


def end_run(
    db: Db,
    run_id: int,
    *,
    ended_at: datetime,
    outcome: str,
    reason: str | None = None,
    read_hashes: Mapping[str, str] | None = None,
    written_hashes: Mapping[str, str] | None = None,
) -> bool:
    """End a run with its outcome. True when done; False when it had already ended (a run ends
    once: nothing changes). No such run: LookupError."""
    rid = whole_number(run_id, "a run id")
    values = (
        iso(ended_at, "ended_at"), code_value(outcome, "a run's outcome", "ok"),
        optional_text(reason, "a reason"), _hashes(read_hashes), _hashes(written_hashes),
    )
    with session(db, write=True) as conn:
        cursor = conn.execute(
            "UPDATE runs SET ended_at = ?, outcome = ?, reason = ?, read_hashes = ?,"
            " written_hashes = ? WHERE id = ? AND ended_at IS NULL",
            (*values, rid),
        )
        if cursor.rowcount == 0 and _run(conn, rid) is None:
            raise LookupError("there is no run with that id in the store")
        return cursor.rowcount == 1


def get_run(db: Db, run_id: int) -> RunRecord | None:
    with session(db, write=False) as conn:
        return _run(conn, whole_number(run_id, "a run id"))


def list_runs(
    db: Db, *, studio_id: str | None = None, since: datetime | None = None
) -> list[RunRecord]:
    """Runs in the order they started; only the job's, and only those started at or after
    since, when given."""
    clauses, params = [], []
    if studio_id is not None:
        clauses.append("studio_id = ?")
        params.append(key_text(studio_id, "a Studio project ID"))
    if since is not None:
        clauses.append("started_at >= ?")
        params.append(iso(since, "since"))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""  # fixed text; values as parameters
    with session(db, write=False) as conn:
        rows = conn.execute(f"SELECT * FROM runs{where} ORDER BY started_at, id", params).fetchall()
    return [_record(r) for r in rows]


def _hashes(value: Mapping[str, str] | None) -> str:
    if value is None:
        return "{}"
    if not isinstance(value, Mapping):
        raise ValueError("hashes must map each name or path to its sha256")
    return dumps({text_value(k, "a hashed name"): sha256_value(v) for k, v in value.items()})


def _run(conn: sqlite3.Connection, rid: int) -> RunRecord | None:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (rid,)).fetchone()
    return None if row is None else _record(row)


def _record(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        id=row["id"],
        studio_id=row["studio_id"],
        stage=row["stage"],
        trigger=row["trigger"],
        started_at=time_of(row["started_at"]),
        ended_at=time_or_none(row["ended_at"]),
        outcome=row["outcome"],
        reason=row["reason"],
        app_version=row["app_version"],
        read_hashes=loads(row["read_hashes"]),
        written_hashes=loads(row["written_hashes"]),
    )
