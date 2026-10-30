r"""
NG Runner's store: one SQLite database in its own data folder (AppPaths.db), with plain
functions grouped by table (build spec section 4). This package's docstrings are the contract
for wave 2.

    store = open_store(paths.db)       # creates or upgrades the tables; refuses what it can't use
    set_state(store, "paused", "no")   # each call opens, commits and closes its own connection
    with store.transaction() as tx:    # the few steps that change several tables at once:
        ...                            #   pass tx, not store; all of it commits, or none

Every table function takes the Store or a Transaction first. Times go in timezone-aware and
come back in UTC (stored as clock.to_iso() text; a naive time is refused with ValueError).
Records come back as frozen dataclasses. A value that breaks a function's contract is refused with
ValueError and nothing is stored; a change to a row that doesn't exist raises LookupError; a
change that only applies in one state (answering an open card, ending an open operation)
returns True when made and False when the row had moved on, changing nothing; a database
failure, or a reference to a job the store doesn't have, raises StoreError in plain words.
Updates take only the fields given: KEEP (the default) leaves a field as it is, None clears it.

Table groups, one module each:
    jobs           jobs, aliases: identity by Studio project ID, the name frozen at intake
    fab_rows       fab_rows: each Fab Schedule row as first seen, and its state
    opportunities  opportunities, list_state: the job list, replaced whole on each fetch
    renders        renders: one row per fetch of an IC summary; one governs each job
    pending        pending: work due later, with time limits (due_pending(now))
    runs           runs: pipeline runs, with the hashes they read and wrote
    cards          cards, card_numbers: Jim's questions; IDs (ROD-2) are never reused
    events         events, dev: the history log and the DEV lane
    write_record   writes, owned: one row per commit_write, and NG Runner's own files
    outbox         outbox: messages, from queued to receipted
    sequences      seqs, seen_ids: outgoing sequences and the replay guard for incoming ones
    state          state: NG Runner's own small facts (paused, installed_at...)
Store.backup_to(path) copies the whole database with SQLite's online backup API.
"""
from __future__ import annotations

from pathlib import Path

from ngrunner.store.cards import (
    CARD_STATES, CardRecord, add_card, allocate_card_id, answer_card, get_card, list_cards,
    record_reminder, withdraw_card,
)
from ngrunner.store.db import KEEP, Db, Keep, Store, StoreError, Transaction
from ngrunner.store.events import (
    DEV_STATES, LANES, DevRecord, EventRecord, close_dev, get_dev, list_dev, list_events,
    log_event, mark_dev_told_jim, open_dev,
)
from ngrunner.store.fab_rows import (
    FAB_ROW_STATES, FabRowRecord, add_fab_row, get_fab_row, list_fab_rows, update_fab_row,
)
from ngrunner.store.jobs import (
    BRANCHES, MODES, AliasRecord, JobRecord, add_alias, ensure_job, freeze_name, get_job,
    list_aliases, list_jobs, update_job,
)
from ngrunner.store.opportunities import (
    ListState, OpportunityLike, OpportunityRecord, get_job_list, get_list_state, get_opportunity,
    replace_opportunities,
)
from ngrunner.store.outbox import (
    CHANNELS, OUTBOX_STATES, OutboxRecord, add_outbox, get_outbox, list_outbox,
    mark_outbox_fallback, mark_outbox_written, outbox_without_receipt, record_receipt,
)
from ngrunner.store.pending import (
    PENDING_STATES, PendingRecord, add_pending, due_pending, finish_pending, get_pending,
    list_pending, past_deadline_pending, retry_pending,
)
from ngrunner.store.renders import (
    RenderRecord, add_render, get_render, governing_render, list_renders, set_governing,
)
from ngrunner.store.runs import RunRecord, end_run, get_run, list_runs, start_run
from ngrunner.store.schema import LATEST, MIGRATIONS, check_readable, migrate
from ngrunner.store.sequences import (
    accept_envelope, accept_seq, last_accepted_seq, last_sent_seq, next_seq, seen_id,
)
from ngrunner.store.state import (
    clear_state, get_state, get_state_time, set_state, set_state_time,
)
from ngrunner.store.write_record import (
    CommitResultLike, FileWriteLike, WriteRecord, list_write_records, owned_sha, path_key,
    record_commit, record_owned,
)


def open_store(path: Path) -> Store:
    """The store at path (AppPaths.db), created when missing or empty and upgraded to LATEST.
    Refused with StoreError, and left exactly as it was: a relative path, a file that is not
    a database or is damaged, and a database made by a newer NG Runner. The folder must exist
    (it is NG Runner's data folder, made at install)."""
    store = Store(path)
    check_readable(store)
    migrate(store)
    return store


__all__ = [
    # the database
    "KEEP", "LATEST", "MIGRATIONS", "Db", "Keep", "Store", "StoreError", "Transaction",
    "check_readable", "migrate", "open_store",
    # jobs, aliases
    "BRANCHES", "MODES", "AliasRecord", "JobRecord", "add_alias", "ensure_job", "freeze_name",
    "get_job", "list_aliases", "list_jobs", "update_job",
    # fab_rows
    "FAB_ROW_STATES", "FabRowRecord", "add_fab_row", "get_fab_row", "list_fab_rows",
    "update_fab_row",
    # opportunities, list_state
    "ListState", "OpportunityLike", "OpportunityRecord", "get_job_list", "get_list_state",
    "get_opportunity", "replace_opportunities",
    # renders
    "RenderRecord", "add_render", "get_render", "governing_render", "list_renders",
    "set_governing",
    # pending
    "PENDING_STATES", "PendingRecord", "add_pending", "due_pending", "finish_pending",
    "get_pending", "list_pending", "past_deadline_pending", "retry_pending",
    # runs
    "RunRecord", "end_run", "get_run", "list_runs", "start_run",
    # cards, card_numbers
    "CARD_STATES", "CardRecord", "add_card", "allocate_card_id", "answer_card", "get_card",
    "list_cards", "record_reminder", "withdraw_card",
    # events, dev
    "DEV_STATES", "LANES", "DevRecord", "EventRecord", "close_dev", "get_dev", "list_dev",
    "list_events", "log_event", "mark_dev_told_jim", "open_dev",
    # writes, owned
    "CommitResultLike", "FileWriteLike", "WriteRecord", "list_write_records", "owned_sha",
    "path_key", "record_commit", "record_owned",
    # outbox
    "CHANNELS", "OUTBOX_STATES", "OutboxRecord", "add_outbox", "get_outbox", "list_outbox",
    "mark_outbox_fallback", "mark_outbox_written", "outbox_without_receipt", "record_receipt",
    # seqs, seen_ids
    "accept_envelope", "accept_seq", "last_accepted_seq", "last_sent_seq", "next_seq",
    "seen_id",
    # state
    "clear_state", "get_state", "get_state_time", "set_state", "set_state_time",
]
