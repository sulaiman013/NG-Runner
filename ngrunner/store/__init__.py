r"""
NG Runner's store: one SQLite database in its own data folder (AppPaths.db), with plain
functions grouped by table (build spec section 4). This package's docstrings are the contract
for wave 2.

    store = open_store(paths.db)       # creates or upgrades the tables; refuses what it can't use
    set_state(store, "paused", "no")   # each call opens, commits and closes its own connection
    with store.transaction() as tx:    # the few steps that change several tables at once:
        ...                            #   pass tx, not store; all of it commits, or none

Every table function takes the Store or a Transaction first (render_fingerprint only the Store,
as it reads a file; jump_after_restore also a plain connection). Times go in timezone-aware and
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
    cards          cards, card_numbers: Jim's questions; IDs (TES-2) are never reused
    events         events, dev: the history log and the DEV lane
    write_record   writes, owned: one row per commit_write, and NG Runner's own files
    write_journal  pending_writes: a commit's row before its first rename, the registry
                   commit_write takes, and the start-up settle
    pruning        nightly: old writes rows, closed journal rows, old renders and their files
    outbox         outbox: messages, from queued to receipted
    sequences      seqs, seen_ids: outgoing sequences and the replay guard for incoming ones
    state          state: NG Runner's own small facts (paused, installed_at...)
Phase 1b's guards:
    programs       programs: each CNC program seen in NG Jobs, by hash
    nests          nests: each material's last verdict; a pass names the hashes it passed on
    clearances     clearances: Jim's click on one program's sheet size, bound to its hash
    owner_log      owner_log: every UNS and LCK user string seen
    heads_ups      heads_ups: the takeoff each job's order heads-up last went out on
    snapshots      snapshots: each copy of Jim's toolkit, and what became of it
Phase 2's converter, wired in for P3's takeoff on intake:
    takeoffs       takeoffs: NG Runner's own takeoff of each summary it files, by rules version
P3's checks:
    checks         check_runs, settles, check_prompts: each run of Jim's crosscheck on a job, bound
                   to what it ran on; Jim's settles; the close prompts
Store.backup_to(path) copies the whole database with SQLite's online backup API, for the
supervisor's copy before an upgrade (backup.snapshot gives everything else its copy, as bytes).
"""
from __future__ import annotations

from pathlib import Path

from ngrunner.store.cards import (
    CARD_STATES, CardRecord, add_card, allocate_card_id, answer_card, get_card, list_cards,
    record_reminder, withdraw_card,
)
from ngrunner.store.checks import (
    CHECK_CODES, CHECK_FEATURES, CHECK_KINDS, CHECK_STATES, CHECK_TRIGGERS, SETTLE_HOWS, SETTLE_SCOPES,
    CheckRunRecord, PromptRecord, SettleRecord, add_prompt, add_settle, answer_prompt, ask_check,
    back_to_asked, finish_check, get_check_run, list_check_runs, list_prompts, list_settles,
    mark_stale, mark_told, not_run_check, start_check,
)
from ngrunner.store.clearances import (
    SHEET_SIZE, ClearanceRecord, add_clearance, cleared_programs, list_clearances,
)
from ngrunner.store.db import KEEP, Db, Keep, Store, StoreError, Transaction
from ngrunner.store.events import (
    DEV_STATES, LANES, DevRecord, EventRecord, close_dev, get_dev, list_dev, list_events,
    log_event, mark_dev_told_jim, open_dev,
)
from ngrunner.store.fab_rows import (
    FAB_ROW_STATES, FabRowRecord, add_fab_row, get_fab_row, list_fab_rows, update_fab_row,
)
from ngrunner.store.heads_ups import (
    HeadsUpRecord, get_heads_up, list_heads_ups, record_heads_up,
)
from ngrunner.store.jobs import (
    BRANCHES, MODES, AliasRecord, JobRecord, add_alias, ensure_job, freeze_name, get_job,
    list_aliases, list_jobs, update_job,
)
from ngrunner.store.nests import (
    NEST_BASES, NEST_STATES, NestRecord, add_nest, get_nest, list_nests, update_nest,
)
from ngrunner.store.opportunities import (
    ListState, OpportunityLike, OpportunityRecord, get_job_list, get_list_state, get_opportunity,
    replace_opportunities,
)
from ngrunner.store.outbox import (
    CHANNELS, OUTBOX_STATES, OutboxRecord, add_outbox, get_outbox, list_outbox,
    mark_outbox_fallback, mark_outbox_written, outbox_without_receipt, record_receipt,
)
from ngrunner.store.owner_log import OwnerRecord, first_owner, list_owner_log, log_owner
from ngrunner.store.pending import (
    PENDING_STATES, PendingRecord, add_pending, due_pending, finish_pending, get_pending,
    list_pending, past_deadline_pending, retry_pending,
)
from ngrunner.store.programs import (
    FAMILIES, PROGRAM_STATES, ProgramRecord, add_program, get_program, list_programs,
    remove_program, update_program,
)
from ngrunner.store.pruning import PrunedRenders, prune_renders, prune_write_records
from ngrunner.store.renders import (
    RenderRecord, add_render, get_render, governing_render, list_renders, render_fingerprint,
    set_governing,
)
from ngrunner.store.runs import RunRecord, end_run, get_run, list_runs, start_run
from ngrunner.store.schema import LATEST, MIGRATIONS, check_readable, migrate
from ngrunner.store.sequences import (
    accept_envelope, accept_seq, jump_after_restore, last_accepted_seq, last_sent_seq, next_seq,
    prune_seen_ids, seen_id,
)
from ngrunner.store.snapshots import (
    SnapshotRecord, add_snapshot, get_snapshot, list_snapshots, update_snapshot,
)
from ngrunner.store.takeoffs import (
    TAKEOFF_BRANCHES, TAKEOFF_STATES, TakeoffRecord, add_takeoff, get_takeoff, list_takeoffs,
)
from ngrunner.store.state import (
    clear_state, get_state, get_state_time, set_state, set_state_time, states_under,
)
from ngrunner.store.write_journal import (
    JOURNAL_STATES, PendingWrite, Settled, StoreRegistry, list_pending_writes,
    settle_pending_writes,
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
    "render_fingerprint", "set_governing",
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
    # pending_writes
    "JOURNAL_STATES", "PendingWrite", "Settled", "StoreRegistry", "list_pending_writes",
    "settle_pending_writes",
    # pruning
    "PrunedRenders", "prune_renders", "prune_write_records",
    # outbox
    "CHANNELS", "OUTBOX_STATES", "OutboxRecord", "add_outbox", "get_outbox", "list_outbox",
    "mark_outbox_fallback", "mark_outbox_written", "outbox_without_receipt", "record_receipt",
    # seqs, seen_ids
    "accept_envelope", "accept_seq", "jump_after_restore", "last_accepted_seq", "last_sent_seq",
    "next_seq", "prune_seen_ids", "seen_id",
    # state
    "clear_state", "get_state", "get_state_time", "set_state", "set_state_time", "states_under",
    # programs
    "FAMILIES", "PROGRAM_STATES", "ProgramRecord", "add_program", "get_program", "list_programs",
    "remove_program", "update_program",
    # nests
    "NEST_BASES", "NEST_STATES", "NestRecord", "add_nest", "get_nest", "list_nests", "update_nest",
    # clearances
    "SHEET_SIZE", "ClearanceRecord", "add_clearance", "cleared_programs", "list_clearances",
    # owner_log
    "OwnerRecord", "first_owner", "list_owner_log", "log_owner",
    # heads_ups
    "HeadsUpRecord", "get_heads_up", "list_heads_ups", "record_heads_up",
    # snapshots
    "SnapshotRecord", "add_snapshot", "get_snapshot", "list_snapshots", "update_snapshot",
    # takeoffs
    "TAKEOFF_BRANCHES", "TAKEOFF_STATES", "TakeoffRecord", "add_takeoff", "get_takeoff",
    "list_takeoffs",
    # check_runs, settles, check_prompts
    "CHECK_CODES", "CHECK_FEATURES", "CHECK_KINDS", "CHECK_STATES", "CHECK_TRIGGERS", "SETTLE_HOWS", "SETTLE_SCOPES",
    "CheckRunRecord", "PromptRecord", "SettleRecord", "add_prompt", "add_settle", "answer_prompt",
    "ask_check", "back_to_asked", "finish_check", "get_check_run", "list_check_runs", "list_prompts",
    "list_settles", "mark_stale", "mark_told", "not_run_check", "start_check",
]
