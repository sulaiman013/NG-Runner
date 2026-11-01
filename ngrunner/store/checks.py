"""
Checks: each run of Jim's crosscheck on a job, Jim's settles of what one found, and the close prompts
that ask him whether a job's sections are done (P3: design 4, "The close prompt" and "How Jim answers";
docs/p2/build-spec.md, section 2).

Why a run holds so much: a result counts only while the files it ran on are the files on disk (design
3.5, "Results are bound to room hashes"). So a finished run keeps the sha256 of the very bytes of each
room it ran on, the IC Conversion's hash and the toolkit copy's, and a later save of any room makes it
stale (stale_at), which nothing undoes. It also keeps each room's design identity
(guards.design.room_identity), which is what a settle is bound to: a settle stands through Mozaik saving
the same design again, and any real change asks again.

A run moves one way: asked, then running, then done or not_run. A run waiting for Mozaik to close the
job goes back to asked. A job has at most one run open (asked or running) at a time, so asking twice
asks once. result holds NG Runner's own words, codes, tags and fingerprints only: never what a checker
printed, which names rooms and clients.

A settle names the run it was made on, the finding's fingerprint, and the identities and conversion
hash of that run; nothing here decides whether it still stands (ngrunner.checks does). A close prompt
is answered once, with Jim's words kept as he gave them.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ngrunner.store.db import (
    Db, dumps, iso, key_text, loads, one_of, optional_text, session, sha256_value, text_value,
    time_of, time_or_none,
)

CHECK_KINDS = ("check", "audit")
CHECK_TRIGGERS = ("check_now", "sections_done", "audit")
CHECK_STATES = ("asked", "running", "done", "not_run")
CHECK_CODES = ("passed", "failed", "could_not_verify")
CHECK_FEATURES = ("garage", "rea", "bridge")        # design 5, "Trust per feature", as rooms show them
SETTLE_HOWS = ("not_a_problem", "told_chat")
# A settle stands for this job (the very finding, design and IC Conversion it was made on), or for all jobs:
# then for every finding of its kind (its checker and what it found) on any job, under the condition Jim's
# words give (design 3.3, "Settled entry"; P4's second round).
SETTLE_SCOPES = ("job", "all_jobs")
_OPEN = ("asked", "running")


@dataclass(frozen=True)
class CheckRunRecord:
    """One run of the checks on one job. folder: its Mozaik Shared job folder's name. rooms: room
    name to the sha256 of the bytes it ran on; identities: room name to its design identity; stats:
    name (and "conversion") to [size, mtime in ns] when it ran, which the freshness pass compares
    first. result: NG Runner's verdicts and items, None unless done. words: why it did not run, or
    the run's own line."""

    id: int
    studio_id: str
    folder: str
    kind: str
    trigger: str
    state: str
    asked_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    code: str | None
    words: str | None
    snapshot_sha256: str | None
    conversion_sha256: str | None
    rooms: dict[str, str]
    identities: dict[str, str]
    stats: dict[str, list[int]]
    result: dict[str, Any] | None
    features: tuple[str, ...]
    trial: bool
    stale_at: datetime | None
    told_at: datetime | None


@dataclass(frozen=True)
class SettleRecord:
    """Jim saying a finding is not a problem for this job (how "not_a_problem"), or that he already
    told his chat (how "told_chat"), bound to the finding and to what its run ran on. scope "all_jobs":
    not a problem for any job, for every finding of its checker and what, under the condition his words
    give; checker and what are None for a settle of this job alone."""

    id: int
    studio_id: str
    run_id: int
    fingerprint: str
    identities: dict[str, str]
    conversion_sha256: str
    how: str
    words: str
    channel: str
    at: datetime
    scope: str = "job"
    checker: str | None = None
    what: str | None = None


@dataclass(frozen=True)
class PromptRecord:
    """One close of a job in Mozaik, asked about. first: the first close after the job appeared,
    which has three answers; a later close has two. answer is None while it is open."""

    id: int
    studio_id: str
    folder: str
    closed_at: datetime
    first: bool
    answer: str | None
    answer_words: str | None
    answered_at: datetime | None
    channel: str | None


# ----- runs


def ask_check(db: Db, *, studio_id: str, folder: str, kind: str, trigger: str,
              at: datetime) -> CheckRunRecord:
    """A check asked for. The job's open run, unchanged, when it has one: asking twice asks once."""
    values = (key_text(studio_id, "a Studio project ID"), text_value(folder, "a job's folder"),
              one_of(kind, CHECK_KINDS, "a check's kind"), one_of(trigger, CHECK_TRIGGERS, "a check's trigger"),
              iso(at, "at"))
    with session(db, write=True) as conn:
        row = conn.execute("SELECT * FROM check_runs WHERE studio_id = ? AND state IN ('asked', 'running')"
                           " ORDER BY id LIMIT 1", (values[0],)).fetchone()
        if row is None:
            cursor = conn.execute(
                "INSERT INTO check_runs (studio_id, folder, kind, trigger, state, asked_at)"
                " VALUES (?, ?, ?, ?, 'asked', ?)", values)
            row = conn.execute("SELECT * FROM check_runs WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _run(row)


def start_check(db: Db, run_id: int, *, at: datetime) -> bool:
    """asked to running. False when it wasn't asked."""
    return _move(db, run_id, "UPDATE check_runs SET state = 'running', started_at = ?"
                             " WHERE id = ? AND state = 'asked'", (iso(at, "at"), run_id))


def back_to_asked(db: Db, run_id: int, *, words: str | None = None) -> bool:
    """running to asked: it waits, as Mozaik has the job open, and words say so. False when it wasn't
    running."""
    said = None if words is None else text_value(words, "why a check waits")
    return _move(db, run_id, "UPDATE check_runs SET state = 'asked', words = ? WHERE id = ? AND state = 'running'",
                 (said, run_id))


def finish_check(db: Db, run_id: int, *, at: datetime, code: str, words: str, snapshot_sha256: str,
                 conversion_sha256: str, rooms: Mapping[str, str], identities: Mapping[str, str],
                 stats: Mapping[str, Sequence[int]], result: Mapping[str, Any],
                 features: Iterable[str], trial: bool) -> bool:
    """running to done, with what it ran on and what it found. Refused (ValueError) when it names no
    room, its identities aren't of the same rooms, or anything else breaks the contract above."""
    rooms_json = _hashes(rooms, "a room's sha256")
    identities_json = _hashes(identities, "a room's design identity")
    if set(rooms) != set(identities) or not rooms:
        raise ValueError("a finished check names the rooms it ran on, each with its design identity")
    if not isinstance(result, Mapping):
        raise ValueError("a finished check holds its result, as a mapping")
    if not isinstance(trial, bool):
        raise ValueError("trial must be True or False")
    values = (iso(at, "at"), one_of(code, CHECK_CODES, "a check's code"), text_value(words, "a check's words"),
              sha256_value(snapshot_sha256, "the toolkit copy's sha256"),
              sha256_value(conversion_sha256, "the IC Conversion's sha256"), rooms_json, identities_json,
              dumps(_stats(stats)), dumps(dict(result)), dumps(_features(features)), int(trial), run_id)
    return _move(db, run_id,
                 "UPDATE check_runs SET state = 'done', finished_at = ?, code = ?, words = ?,"
                 " snapshot_sha256 = ?, conversion_sha256 = ?, rooms = ?, identities = ?, stats = ?,"
                 " result = ?, features = ?, trial = ? WHERE id = ? AND state = 'running'", values)


def not_run_check(db: Db, run_id: int, *, at: datetime, words: str) -> bool:
    """asked or running to not_run, saying why in plain words."""
    return _move(db, run_id, "UPDATE check_runs SET state = 'not_run', finished_at = ?, words = ?"
                             " WHERE id = ? AND state IN ('asked', 'running')",
                 (iso(at, "at"), text_value(words, "why a check did not run"), run_id))


def mark_stale(db: Db, run_id: int, *, at: datetime) -> bool:
    """A finished run whose files changed: it never counts again. The first time stands."""
    return _move(db, run_id, "UPDATE check_runs SET stale_at = ? WHERE id = ? AND state = 'done'"
                             " AND stale_at IS NULL", (iso(at, "at"), run_id))


def mark_told(db: Db, run_id: int, *, at: datetime) -> bool:
    """Its result was told (a toast, or Sulaiman in shadow mode): once."""
    return _move(db, run_id, "UPDATE check_runs SET told_at = ? WHERE id = ? AND told_at IS NULL",
                 (iso(at, "at"), run_id))


def get_check_run(db: Db, run_id: int) -> CheckRunRecord | None:
    with session(db, write=False) as conn:
        row = conn.execute("SELECT * FROM check_runs WHERE id = ?", (run_id,)).fetchone()
    return None if row is None else _run(row)


def list_check_runs(db: Db, studio_id: str | None = None, *,
                    states: Sequence[str] | None = None) -> list[CheckRunRecord]:
    """Every run, or one job's, or those in states, oldest first."""
    clauses, params = [], []
    if studio_id is not None:
        clauses.append("studio_id = ?")
        params.append(key_text(studio_id, "a Studio project ID"))
    if states is not None:
        for state in states:
            one_of(state, CHECK_STATES, "a check's state")
        clauses.append(f"state IN ({', '.join('?' * len(states))})" if states else "0")
        params += list(states)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with session(db, write=False) as conn:
        rows = conn.execute(f"SELECT * FROM check_runs{where} ORDER BY id", params).fetchall()
    return [_run(row) for row in rows]


# ----- settles


def add_settle(db: Db, *, studio_id: str, run_id: int, fingerprint: str, identities: Mapping[str, str],
               conversion_sha256: str, how: str, words: str, channel: str, at: datetime, scope: str = "job",
               checker: str | None = None, what: str | None = None) -> SettleRecord:
    """Record Jim's settle. The run must be one of this job's. scope "all_jobs" needs the checker and
    what of the finding, which is how it is known on any job; a settle of this job alone takes neither."""
    if not identities:
        raise ValueError("a settle is bound to the rooms its run ran on")
    one_of(scope, SETTLE_SCOPES, "a settle's scope")
    if (scope == "all_jobs") != bool(checker and what) or (scope == "job" and (checker or what)):
        raise ValueError("a settle for all jobs names its checker and what it found, and only it does")
    values = (key_text(studio_id, "a Studio project ID"), _run_id(run_id),
              sha256_value(fingerprint, "a finding's fingerprint"), _hashes(identities, "a room's design identity"),
              sha256_value(conversion_sha256, "the IC Conversion's sha256"), one_of(how, SETTLE_HOWS, "a settle"),
              text_value(words, "Jim's words"), text_value(channel, "a channel"), iso(at, "at"), scope,
              optional_text(checker, "a checker"), optional_text(what, "what a checker found"))
    with session(db, write=True) as conn:
        owner = conn.execute("SELECT studio_id FROM check_runs WHERE id = ?", (values[1],)).fetchone()
        if owner is None or owner["studio_id"] != values[0]:
            raise ValueError("a settle names a check run of its own job")
        cursor = conn.execute(
            "INSERT INTO settles (studio_id, run_id, fingerprint, identities, conversion_sha256, how, words,"
            " channel, at, scope, checker, what) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
        row = conn.execute("SELECT * FROM settles WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _settle(row)


def list_settles(db: Db, studio_id: str | None = None) -> list[SettleRecord]:
    query, params = "SELECT * FROM settles", ()
    if studio_id is not None:
        query, params = query + " WHERE studio_id = ?", (key_text(studio_id, "a Studio project ID"),)
    with session(db, write=False) as conn:
        rows = conn.execute(query + " ORDER BY id", params).fetchall()
    return [_settle(row) for row in rows]


# ----- close prompts


def add_prompt(db: Db, *, studio_id: str, folder: str, closed_at: datetime, first: bool) -> PromptRecord:
    if not isinstance(first, bool):
        raise ValueError("first must be True or False")
    values = (key_text(studio_id, "a Studio project ID"), text_value(folder, "a job's folder"),
              iso(closed_at, "closed_at"), int(first))
    with session(db, write=True) as conn:
        cursor = conn.execute("INSERT INTO check_prompts (studio_id, folder, closed_at, first)"
                              " VALUES (?, ?, ?, ?)", values)
        row = conn.execute("SELECT * FROM check_prompts WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _prompt(row)


def answer_prompt(db: Db, prompt_id: int, *, answer: str, answer_words: str, answered_at: datetime,
                  channel: str) -> bool:
    """Once: False when it was answered already, changing nothing."""
    return _move(db, prompt_id,
                 "UPDATE check_prompts SET answer = ?, answer_words = ?, answered_at = ?, channel = ?"
                 " WHERE id = ? AND answer IS NULL",
                 (text_value(answer, "an answer"), text_value(answer_words, "Jim's words"),
                  iso(answered_at, "answered_at"), text_value(channel, "a channel"), prompt_id))


def list_prompts(db: Db, studio_id: str | None = None, *, open_only: bool = False) -> list[PromptRecord]:
    clauses, params = [], []
    if studio_id is not None:
        clauses.append("studio_id = ?")
        params.append(key_text(studio_id, "a Studio project ID"))
    if open_only:
        clauses.append("answer IS NULL")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with session(db, write=False) as conn:
        rows = conn.execute(f"SELECT * FROM check_prompts{where} ORDER BY id", params).fetchall()
    return [_prompt(row) for row in rows]


# ----- small pieces


def _move(db: Db, row_id: object, sql: str, params: Sequence[object]) -> bool:
    if isinstance(row_id, bool) or not isinstance(row_id, int):
        raise ValueError("a row's id is a whole number")
    with session(db, write=True) as conn:
        return conn.execute(sql, params).rowcount == 1


def _run_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("a check run's id is a whole number from 1")
    return value


def _hashes(values: Mapping[str, str], what: str) -> str:
    if not isinstance(values, Mapping):
        raise ValueError(f"{what}s are a mapping of room names")
    return dumps({text_value(name, "a room's name"): sha256_value(sha, what) for name, sha in values.items()})


def _stats(stats: Mapping[str, Sequence[int]]) -> dict[str, list[int]]:
    if not isinstance(stats, Mapping):
        raise ValueError("a check's file facts are a mapping")
    out = {}
    for name, pair in stats.items():
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not all(isinstance(n, int) and not isinstance(n, bool) for n in pair)):
            raise ValueError("a file's facts are its size and its time, as whole numbers")
        out[text_value(name, "a file's name")] = list(pair)
    return out


def _features(features: Iterable[str]) -> list[str]:
    if isinstance(features, str):
        raise ValueError("features are a list")
    return sorted({one_of(feature, CHECK_FEATURES, "a feature") for feature in features})


def _run(row: sqlite3.Row) -> CheckRunRecord:
    return CheckRunRecord(
        id=row["id"], studio_id=row["studio_id"], folder=row["folder"], kind=row["kind"],
        trigger=row["trigger"], state=row["state"], asked_at=time_of(row["asked_at"]),
        started_at=time_or_none(row["started_at"]), finished_at=time_or_none(row["finished_at"]),
        code=row["code"], words=row["words"], snapshot_sha256=row["snapshot_sha256"],
        conversion_sha256=row["conversion_sha256"], rooms=loads(row["rooms"]),
        identities=loads(row["identities"]), stats=loads(row["stats"]),
        result=None if row["result"] is None else loads(row["result"]),
        features=tuple(loads(row["features"])), trial=bool(row["trial"]),
        stale_at=time_or_none(row["stale_at"]), told_at=time_or_none(row["told_at"]))


def _settle(row: sqlite3.Row) -> SettleRecord:
    return SettleRecord(
        id=row["id"], studio_id=row["studio_id"], run_id=row["run_id"], fingerprint=row["fingerprint"],
        identities=loads(row["identities"]), conversion_sha256=row["conversion_sha256"], how=row["how"],
        words=row["words"], channel=row["channel"], at=time_of(row["at"]), scope=row["scope"],
        checker=row["checker"], what=row["what"])


def _prompt(row: sqlite3.Row) -> PromptRecord:
    return PromptRecord(
        id=row["id"], studio_id=row["studio_id"], folder=row["folder"], closed_at=time_of(row["closed_at"]),
        first=bool(row["first"]), answer=row["answer"], answer_words=row["answer_words"],
        answered_at=time_or_none(row["answered_at"]), channel=row["channel"])


__all__ = [
    "CHECK_CODES", "CHECK_FEATURES", "CHECK_KINDS", "CHECK_STATES", "CHECK_TRIGGERS", "SETTLE_HOWS", "SETTLE_SCOPES",
    "CheckRunRecord", "PromptRecord", "SettleRecord", "add_prompt", "add_settle", "answer_prompt",
    "ask_check", "back_to_asked", "finish_check", "get_check_run", "list_check_runs", "list_prompts",
    "list_settles", "mark_stale", "mark_told", "not_run_check", "start_check",
]
