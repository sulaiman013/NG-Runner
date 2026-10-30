"""
The job list: the Salesforce Opportunities NG Runner last fetched from Render, and the state of
that list (build spec section 4; design 5, "Matching").

Why wholesale: the list is a mirror, so each fetch replaces it completely, in one transaction
with list_state; nothing ever sees half of one list and half of another. The freshness rule
depends on it ("never match against a job list older than the row's first appearance"), so
get_job_list() reads the list and its state at one moment: a list paired with a newer list's
time could match a row against Opportunities that were already out of date.

synced_at is Render's (when it read the list from Salesforce, None when it gave none);
fetched_at is when NG Runner fetched it from Render.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ngrunner.match import Opportunity
from ngrunner.store.db import (
    Db, iso, iso_or_none, key_text, optional_text, session, text_value, time_of, time_or_none,
)


class OpportunityLike(Protocol):
    """What replace_opportunities reads: match.Opportunity, or a record with a client too."""

    @property
    def studio_id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def stage(self) -> str: ...

    @property
    def owner(self) -> str: ...


@dataclass(frozen=True)
class OpportunityRecord:
    """One Opportunity of the stored list. owner is its designer; client is the client's
    name (Studio's report links are built from it), None when the list gave none."""

    studio_id: str
    name: str
    stage: str
    owner: str
    client: str | None = None

    def to_match(self) -> Opportunity:
        """The Opportunity as the matcher takes it (match_fab_row). By keyword: its fields are
        in another order than this record's."""
        return Opportunity(name=self.name, studio_id=self.studio_id, owner=self.owner,
                           stage=self.stage)


@dataclass(frozen=True)
class ListState:
    synced_at: datetime | None
    fetched_at: datetime
    job_count: int


def replace_opportunities(
    db: Db,
    opportunities: Iterable[OpportunityLike],
    *,
    synced_at: datetime | None,
    fetched_at: datetime,
    clients: Mapping[str, str | None] | None = None,
) -> ListState:
    """Replace the whole stored list with these Opportunities, and its state with these times,
    in one transaction; returns the new state. Each needs a Studio project ID and a name (stage
    and owner may be blank text). clients is the job list's client names by Studio project ID
    (JobList.clients), since match.Opportunity has no field for one: when given it says every
    client, and an ID it lacks has none; left out, each Opportunity's own client is read, when
    it has one. A blank client is stored as None. A list that can't be stored whole (a bad value:
    ValueError; one Studio ID twice: StoreError) leaves the old list and state exactly as they
    were."""
    if clients is not None and not isinstance(clients, Mapping):
        raise ValueError("clients must map each Studio project ID to its client's name")
    rows = [_row_values(o, clients) for o in opportunities]
    state = (iso_or_none(synced_at, "synced_at"), iso(fetched_at, "fetched_at"), len(rows))
    with session(db, write=True) as conn:
        conn.execute("DELETE FROM opportunities")
        conn.executemany(
            "INSERT INTO opportunities (studio_id, name, stage, owner, client)"
            " VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute("DELETE FROM list_state")
        conn.execute(
            "INSERT INTO list_state (synced_at, fetched_at, job_count) VALUES (?, ?, ?)", state
        )
        return _state(conn)


def get_list_state(db: Db) -> ListState | None:
    """The stored list's times and size; None before the first fetch."""
    with session(db, write=False) as conn:
        return _state(conn)


def get_job_list(db: Db) -> tuple[ListState | None, tuple[OpportunityRecord, ...]]:
    """The stored list and its state, read at one moment, ordered by name; (None, ()) before
    the first fetch."""
    with session(db, write=False) as conn:
        state = _state(conn)
        rows = conn.execute("SELECT * FROM opportunities ORDER BY name, studio_id").fetchall()
    return state, tuple(_record(r) for r in rows)


def get_opportunity(db: Db, studio_id: str) -> OpportunityRecord | None:
    with session(db, write=False) as conn:
        row = conn.execute(
            "SELECT * FROM opportunities WHERE studio_id = ?",
            (key_text(studio_id, "a Studio project ID"),),
        ).fetchone()
    return None if row is None else _record(row)


def _row_values(
    o: OpportunityLike, clients: Mapping[str, str | None] | None
) -> tuple[str, str, str, str, str | None]:
    studio_id = key_text(getattr(o, "studio_id", None), "an Opportunity's Studio project ID")
    client = getattr(o, "client", None) if clients is None else clients.get(studio_id)
    return (
        studio_id,
        text_value(getattr(o, "name", None), "an Opportunity's name"),
        text_value(getattr(o, "stage", None), "an Opportunity's stage", empty=True),
        text_value(getattr(o, "owner", None), "an Opportunity's owner", empty=True),
        optional_text(client, "an Opportunity's client") or None,
    )


def _state(conn: sqlite3.Connection) -> ListState | None:
    row = conn.execute("SELECT * FROM list_state").fetchone()
    if row is None:
        return None
    return ListState(time_or_none(row["synced_at"]), time_of(row["fetched_at"]), row["job_count"])


def _record(row: sqlite3.Row) -> OpportunityRecord:
    return OpportunityRecord(
        studio_id=row["studio_id"], name=row["name"], stage=row["stage"], owner=row["owner"],
        client=row["client"],
    )
