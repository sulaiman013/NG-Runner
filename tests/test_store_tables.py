"""
Round trips for every table group of the store (build spec section 4), through ngrunner.store's
public functions only, on a database under tmp_path. Job names are synthetic.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType

import pytest
from helpers_pdf import summary_pdf

from ngrunner.clock import PACIFIC
from ngrunner.intake.summary_check import check_summary, content_fingerprint
from ngrunner.joblist import JobList
from ngrunner.match import FabRow, Opportunity, match_fab_row, match_file_to_job
from ngrunner.store import (
    AliasRecord, Db, ListState, OpportunityRecord, Store, StoreError, accept_envelope, accept_seq,
    add_alias, add_card, add_fab_row, add_outbox, add_pending, add_render, allocate_card_id,
    answer_card, clear_state, close_dev, due_pending, end_run, ensure_job, finish_pending,
    freeze_name, get_card, get_dev, get_fab_row, get_job, get_job_list, get_list_state,
    states_under, get_opportunity, get_outbox, get_pending, get_render, get_run, get_state, get_state_time,
    governing_render, jump_after_restore, last_accepted_seq, last_sent_seq, list_aliases,
    list_cards, list_dev,
    list_events, list_fab_rows, list_jobs, list_outbox, list_pending, list_renders, list_runs,
    list_write_records, log_event, mark_dev_told_jim, mark_outbox_fallback, mark_outbox_written,
    next_seq, open_dev, open_store, outbox_without_receipt, owned_sha, past_deadline_pending,
    path_key, prune_seen_ids, record_commit, record_owned, record_receipt, record_reminder,
    replace_opportunities,
    render_fingerprint, retry_pending, seen_id, set_governing, set_state, set_state_time, start_run,
    update_fab_row, update_job, withdraw_card,
)
from ngrunner.writes import CommitResult, FileWrite, Roots, commit_write

Z = timezone.utc
T0 = datetime(2026, 10, 30, 13, 30, tzinfo=Z)
T1, T2, T3 = (T0 + timedelta(minutes=n) for n in (10, 20, 30))
JOB, SID = "Testerson_101426", "101"


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return open_store(tmp_path / "ngrunner.db")


# ----- jobs and aliases


def test_a_job_is_stored_and_read_back_whole(store: Store) -> None:
    made = ensure_job(store, SID, JOB, now=T0, designer="AB", install=("Wednesday", " 7 /1"))

    job = get_job(store, SID)
    assert job == made
    assert (job.studio_id, job.name, job.mode, job.name_frozen) == (SID, JOB, "auto", False)
    assert (job.branch, job.folder, job.designer) == (None, None, "AB")
    assert job.install == ("Wednesday", " 7 /1")
    assert (job.created_at, job.updated_at, job.aliases) == (T0, T0, ())
    assert get_job(store, "99999") is None


def test_times_come_back_in_utc_whatever_zone_they_went_in(store: Store) -> None:
    six_thirty_pacific = datetime(2026, 10, 30, 6, 30, tzinfo=PACIFIC)

    job = ensure_job(store, SID, JOB, now=six_thirty_pacific)

    assert job.created_at == T0 and job.created_at.tzinfo == Z


def test_ensure_job_never_changes_a_job_that_is_there(store: Store) -> None:
    first = ensure_job(store, SID, JOB, now=T0)

    again = ensure_job(store, SID, "Fakeley_101426", now=T1, mode="held", designer="CD")

    assert again == first == get_job(store, SID)


def test_a_job_changes_only_the_fields_given(store: Store) -> None:
    ensure_job(store, SID, JOB, now=T0, designer="AB")

    job = update_job(store, SID, now=T1, branch="garage", folder=f"{JOB}_Gar", mode="held")
    assert (job.branch, job.folder, job.mode) == ("garage", f"{JOB}_Gar", "held")
    assert job.designer == "AB"
    assert (job.created_at, job.updated_at) == (T0, T1)

    job = update_job(store, SID, now=T2, designer=None, install=("7/15/26", "7/15/26"))
    assert (job.designer, job.install, job.branch) == (None, ("7/15/26", "7/15/26"), "garage")
    assert get_job(store, SID) == job


def test_jobs_are_listed_in_the_order_they_were_made(store: Store) -> None:
    ensure_job(store, "2", "Fakeley_101526", now=T1)
    ensure_job(store, "1", JOB, now=T0)
    ensure_job(store, "3", "Mockford_101626", now=T2)

    assert [j.studio_id for j in list_jobs(store)] == ["1", "2", "3"]


def test_a_frozen_name_stays_and_later_names_become_aliases(store: Store) -> None:
    ensure_job(store, SID, JOB, now=T0)
    update_job(store, SID, now=T0, name="Testerson_101426")  # before freezing, it may change
    freeze_name(store, SID, now=T1)

    with pytest.raises(ValueError, match="frozen"):
        update_job(store, SID, now=T2, name="Fakeley_101426")
    add_alias(store, SID, "Testerson-Fakeley_101426", seen_at=T2)
    add_alias(store, SID, "Testerson-Fakeley_101426", seen_at=T3)  # seen again: kept once
    add_alias(store, SID, "Testerson_Fakeley_101426", seen_at=T3)

    job = get_job(store, SID)
    assert (job.name, job.name_frozen, job.updated_at) == (JOB, True, T1)
    assert job.aliases == ("Testerson-Fakeley_101426", "Testerson_Fakeley_101426")
    assert list_aliases(store, SID) == [
        AliasRecord(SID, "Testerson-Fakeley_101426", T2),
        AliasRecord(SID, "Testerson_Fakeley_101426", T3),
    ]


def test_stored_jobs_are_what_the_matcher_takes(store: Store) -> None:
    ensure_job(store, SID, JOB, now=T0)
    add_alias(store, SID, "Fakeley_101426", seen_at=T1)

    result = match_file_to_job("Fakeley_101426_summary.pdf", list_jobs(store),
                               opportunity_names=["Fakeley_101426"])

    assert result.status == "matched" and result.job.studio_id == SID


@pytest.mark.parametrize("change", [
    {"mode": "paused"}, {"branch": "pantry"}, {"name": " "}, {"folder": ""},
    {"install": ("Wednesday",)},
])
def test_a_bad_value_is_refused_and_the_job_left_as_it_was(store: Store, change) -> None:
    before = ensure_job(store, SID, JOB, now=T0)

    with pytest.raises(ValueError):
        update_job(store, SID, now=T1, **change)

    assert get_job(store, SID) == before


@pytest.mark.parametrize("args", [
    {"studio_id": " ", "name": JOB}, {"studio_id": SID, "name": ""},
    {"studio_id": 101, "name": JOB}, {"studio_id": SID, "name": JOB, "mode": "paused"},
])
def test_a_new_job_with_a_bad_value_is_refused(store: Store, args) -> None:
    mode = args.pop("mode", "auto")

    with pytest.raises(ValueError):
        ensure_job(store, **args, now=T0, mode=mode)

    assert list_jobs(store) == []


def test_changing_a_job_that_is_not_there_is_refused(store: Store) -> None:
    with pytest.raises(LookupError):
        update_job(store, SID, now=T0, folder=JOB)
    with pytest.raises(LookupError):
        freeze_name(store, SID, now=T0)


def test_a_name_for_a_job_the_store_does_not_have_is_refused(store: Store) -> None:
    with pytest.raises(StoreError, match="breaks its rules"):  # a foreign key: they are on
        add_alias(store, SID, JOB, seen_at=T0)

    assert list_aliases(store, SID) == []


# ----- Fab rows


def test_a_fab_row_is_stored_with_its_first_sight_and_read_back(store: Store) -> None:
    row = add_fab_row(store, first_seen=T0, text="Testerson", designer="AB",
                      install=("Wednesday", " 7 /1"), fab_date="10/30", sheet_row=12)

    assert get_fab_row(store, row.id) == row
    assert (row.text, row.original_text) == ("Testerson", "Testerson")
    assert (row.designer, row.state) == ("AB", "new")
    assert (row.install, row.fab_date, row.sheet_row) == (("Wednesday", " 7 /1"), "10/30", 12)
    assert (row.first_seen, row.updated_at, row.studio_id, row.reason) == (T0, T0, None, None)
    assert row.to_match() == FabRow("Testerson", "AB", T0)
    assert get_fab_row(store, row.id + 1) is None


def test_an_edited_row_keeps_its_original_text_and_first_sight(store: Store) -> None:
    ensure_job(store, SID, JOB, now=T0)
    row = add_fab_row(store, first_seen=T0, text="Testersn", designer="AB", sheet_row=12)

    edited = update_fab_row(store, row.id, now=T1, text="Testerson", sheet_row=14)
    matched = update_fab_row(store, row.id, now=T2, state="matched", studio_id=SID,
                             reason="exact_name")

    assert (edited.text, edited.original_text, edited.sheet_row) == ("Testerson", "Testersn", 14)
    assert (matched.state, matched.studio_id, matched.reason) == ("matched", SID, "exact_name")
    assert (matched.first_seen, matched.updated_at, matched.text) == (T0, T2, "Testerson")
    assert get_fab_row(store, row.id) == matched


def test_a_row_is_measured_from_when_its_text_last_changed(store: Store) -> None:
    row = add_fab_row(store, first_seen=T0, text="Testersn", designer="AB", sheet_row=12)
    assert row.text_changed_at == T0
    assert row.to_match() == FabRow("Testersn", "AB", T0)

    corrected = update_fab_row(store, row.id, now=T1, text="Testerson")

    assert corrected.text_changed_at == T1
    assert corrected.to_match() == FabRow("Testerson", "AB", T1)
    assert (corrected.first_seen, corrected.original_text) == (T0, "Testersn")  # the record stays
    assert get_fab_row(store, row.id) == corrected


def test_only_a_change_of_its_text_moves_when_a_row_last_changed(store: Store) -> None:
    row = add_fab_row(store, first_seen=T0, text="Testerson", designer="AB")

    moved = update_fab_row(store, row.id, now=T1, sheet_row=14, designer="CD", state="asked",
                           install=("Wednesday", " 7 /1"), fab_date="10/30", reason="asked")
    same = update_fab_row(store, row.id, now=T2, text="Testerson")  # the same text read again

    assert (moved.text_changed_at, moved.updated_at) == (T0, T1)
    assert (same.text_changed_at, same.updated_at) == (T0, T2)
    assert same.to_match().first_seen == T0


def test_a_corrected_row_needs_a_job_list_read_after_the_correction(store: Store) -> None:
    sold = [Opportunity("Testerson_101426", SID, "Pat Example", "Ready to Order")]
    row = add_fab_row(store, first_seen=T0, text="Testersn", designer="PE")
    assert match_fab_row(row.to_match(), sold, [], T1).status != "stale_list"  # read after T0

    corrected = update_fab_row(store, row.id, now=T2, text="Testerson")

    assert match_fab_row(corrected.to_match(), sold, [], T1).status == "stale_list"
    assert match_fab_row(corrected.to_match(), sold, [], T3).status == "matched"


def test_fab_rows_are_listed_oldest_first_and_by_state(store: Store) -> None:
    a = add_fab_row(store, first_seen=T1, text="Fakeley", designer="CD", state="seen_at_install")
    b = add_fab_row(store, first_seen=T0, text="Testerson", designer="AB")
    c = add_fab_row(store, first_seen=T2, text="Mockford", designer="EF", state="waiting_list")

    assert [r.id for r in list_fab_rows(store)] == [b.id, a.id, c.id]
    assert [r.id for r in list_fab_rows(store, states=("new", "waiting_list"))] == [b.id, c.id]
    assert list_fab_rows(store, states=()) == []


@pytest.mark.parametrize("change", [
    {"state": "archived"}, {"text": " "}, {"sheet_row": 0}, {"designer": None},
    {"install": "Wednesday 7/1"},
])
def test_a_bad_fab_row_value_is_refused_and_the_row_left_as_it_was(store: Store, change) -> None:
    row = add_fab_row(store, first_seen=T0, text="Testerson", designer="AB")

    with pytest.raises(ValueError):
        update_fab_row(store, row.id, now=T1, **change)
    with pytest.raises(ValueError):
        add_fab_row(store, first_seen=T0, **{"text": "Fakeley", "designer": "CD", **change})

    assert list_fab_rows(store) == [row]


def test_a_fab_row_is_linked_only_to_a_job_the_store_has(store: Store) -> None:
    row = add_fab_row(store, first_seen=T0, text="Testerson", designer="AB")

    with pytest.raises(StoreError, match="breaks its rules"):
        update_fab_row(store, row.id, now=T1, state="matched", studio_id=SID)
    with pytest.raises(LookupError):
        update_fab_row(store, row.id + 1, now=T1, state="gone")

    assert get_fab_row(store, row.id) == row


# ----- the job list: opportunities and list_state


def test_the_job_list_is_stored_whole_with_its_state(store: Store) -> None:
    assert get_job_list(store) == (None, ()) and get_list_state(store) is None

    state = replace_opportunities(store, [
        OpportunityRecord("2", "Fakeley_101526", "Ready to Order", "CD", "Fakeley"),
        Opportunity("Testerson_101426", SID, "AB", "Ready to Install"),  # the matcher's own type
    ], synced_at=T0, fetched_at=T1)

    assert state == ListState(synced_at=T0, fetched_at=T1, job_count=2) == get_list_state(store)
    listed, opportunities = get_job_list(store)
    assert listed == state
    assert opportunities == (
        OpportunityRecord("2", "Fakeley_101526", "Ready to Order", "CD", "Fakeley"),
        OpportunityRecord(SID, "Testerson_101426", "Ready to Install", "AB", None),
    )
    assert get_opportunity(store, SID).to_match() == Opportunity(JOB, SID, "AB", "Ready to Install")
    assert get_opportunity(store, "3") is None


def test_each_fetch_replaces_the_whole_list(store: Store) -> None:
    replace_opportunities(store, [OpportunityRecord("1", JOB, "Sold", "AB", None),
                                  OpportunityRecord("2", "Fakeley_101526", "Sold", "CD", None)],
                          synced_at=T0, fetched_at=T0)

    mockford = OpportunityRecord("3", "Mockford_101626", "Sold", "EF", None)
    state = replace_opportunities(store, [mockford],
                                  synced_at=None, fetched_at=T2)  # Render had no sync time

    assert state == ListState(None, T2, 1)
    assert get_job_list(store) == (state, (mockford,))


def test_a_job_list_from_render_keeps_its_clients(store: Store) -> None:
    # match.Opportunity has no client: the list carries them beside it, by Studio project ID.
    job_list = JobList(
        synced_at=T0, fetched_at=T1,
        opportunities=(
            Opportunity(name=JOB, studio_id=SID, owner="AB", stage="Ready to Order"),
            Opportunity(name="Fakeley_101526", studio_id="2", owner="CD", stage="Ready to Install"),
            Opportunity(name="Mockford_101626", studio_id="3", owner="EF", stage="Ready to Order"),
        ),
        clients=MappingProxyType({SID: "Testerson Family", "2": ""}),
    )

    replace_opportunities(store, job_list.opportunities, synced_at=job_list.synced_at,
                          fetched_at=job_list.fetched_at, clients=job_list.clients)

    assert get_opportunity(store, SID) == OpportunityRecord(
        studio_id=SID, name=JOB, stage="Ready to Order", owner="AB", client="Testerson Family")
    assert get_opportunity(store, "2").client is None  # the list had none for it: one way to say so
    assert get_opportunity(store, "3").client is None  # nor is it in the clients at all


def test_clients_given_say_every_client_and_without_them_each_record_says_its_own(
    store: Store,
) -> None:
    record = OpportunityRecord(studio_id=SID, name=JOB, stage="Sold", owner="AB",
                               client="Testerson")

    replace_opportunities(store, [record], synced_at=T0, fetched_at=T0, clients={SID: "Fakeley"})
    assert get_opportunity(store, SID).client == "Fakeley"

    replace_opportunities(store, [record], synced_at=T1, fetched_at=T1, clients={})
    assert get_opportunity(store, SID).client is None

    replace_opportunities(store, [record], synced_at=T2, fetched_at=T2)
    assert get_opportunity(store, SID).client == "Testerson"


@pytest.mark.parametrize("clients", [["Testerson"], "Testerson", {SID: 7}])
def test_clients_that_are_not_names_by_studio_id_are_refused(store: Store, clients) -> None:
    old = OpportunityRecord(studio_id=SID, name=JOB, stage="Sold", owner="AB")
    replace_opportunities(store, [old], synced_at=T0, fetched_at=T0)

    with pytest.raises(ValueError):
        replace_opportunities(store, [old], synced_at=T1, fetched_at=T1, clients=clients)

    assert get_job_list(store) == (ListState(T0, T0, 1), (old,))


def test_a_list_that_can_not_be_stored_whole_leaves_the_old_one(store: Store) -> None:
    old = [OpportunityRecord("1", JOB, "Sold", "AB", None)]
    replace_opportunities(store, old, synced_at=T0, fetched_at=T0)
    twice = [OpportunityRecord("2", "Fakeley_101526", "Sold", "CD", None),
             OpportunityRecord("2", "Mockford_101626", "Sold", "EF", None)]  # one Studio ID twice

    with pytest.raises(StoreError, match="breaks its rules"):
        replace_opportunities(store, twice, synced_at=T1, fetched_at=T1)

    assert get_job_list(store) == (ListState(T0, T0, 1), tuple(old))


def test_a_failed_step_inside_a_transaction_undoes_only_itself(store: Store) -> None:
    old = [OpportunityRecord("1", JOB, "Sold", "AB", None)]
    replace_opportunities(store, old, synced_at=T0, fetched_at=T0)
    twice = [OpportunityRecord("2", "Fakeley_101526", "Sold", "CD", None)] * 2

    with store.transaction() as tx:
        set_state(tx, "list_push_asked_at", "2026-10-30")
        with pytest.raises(StoreError):
            replace_opportunities(tx, twice, synced_at=T1, fetched_at=T1)  # deleted, then failed
        ensure_job(tx, SID, JOB, now=T1)

    assert get_job_list(store) == (ListState(T0, T0, 1), tuple(old))  # the delete was undone too
    assert get_state(store, "list_push_asked_at") == "2026-10-30" and get_job(store, SID)


@pytest.mark.parametrize("bad", [
    OpportunityRecord(" ", JOB, "Sold", "AB", None), OpportunityRecord("1", "", "Sold", "AB", None),
    OpportunityRecord("1", JOB, None, "AB", None), OpportunityRecord("1", JOB, "Sold", "AB", 7),
])
def test_an_opportunity_with_a_bad_value_is_refused(store: Store, bad) -> None:
    with pytest.raises(ValueError):
        replace_opportunities(store, [bad], synced_at=T0, fetched_at=T0)

    assert get_job_list(store) == (None, ())


# ----- renders


SHA_A, SHA_B = "a" * 64, "0b" * 32


def render_of(store: Db, at: datetime, **fields):
    defaults = dict(studio_id=SID, fetched_at=at, check_ok=True, url="https://example.com/r/1/",
                    sha256=SHA_A, page_count=19, creation_date="D:20261030063000-07'00'",
                    fingerprint="f" * 64, layout="landscape", missing=(), project_name=JOB,
                    cache_path=Path(r"C:\data\renders") / SID / f"{SHA_A}.pdf")
    return add_render(store, **{**defaults, **fields})


def test_each_fetch_is_a_render_read_back_whole(store: Store) -> None:
    ensure_job(store, SID, JOB, now=T0)
    early = render_of(store, T0, check_ok=False, sha256=SHA_B, page_count=4,
                      missing=("Page 1 lists no designs.", "There is no PULLED list."))
    full = render_of(store, T1)

    assert list_renders(store, SID) == [early, full]
    assert get_render(store, early.id) == early
    assert (early.check_ok, early.missing, early.page_count) == (False, (
        "Page 1 lists no designs.", "There is no PULLED list."), 4)
    assert (full.check_ok, full.missing, full.governing) == (True, (), False)
    assert full.layout == "landscape"
    assert full.cache_path == Path(r"C:\data\renders") / SID / f"{SHA_A}.pdf"
    assert full.creation_date == "D:20261030063000-07'00'"  # the PDF's own text, verbatim
    assert get_render(store, full.id + 1) is None and list_renders(store, "2") == []


def test_one_render_governs_a_job_at_a_time(store: Store) -> None:
    ensure_job(store, SID, JOB, now=T0)
    ensure_job(store, "2", "Fakeley_101526", now=T0)
    first, second = render_of(store, T0), render_of(store, T1, sha256=SHA_B)
    other = render_of(store, T0, studio_id="2")
    assert governing_render(store, SID) is None

    assert set_governing(store, first.id).governing
    set_governing(store, other.id)
    now_governing = set_governing(store, second.id)

    assert governing_render(store, SID) == now_governing and now_governing.id == second.id
    assert [r.governing for r in list_renders(store, SID)] == [False, True]
    assert governing_render(store, "2").id == other.id  # another job's render is untouched
    with pytest.raises(LookupError):
        set_governing(store, other.id + 1)


NEW_PYPDF, OLD_PYPDF = "6.21.4", "5.0.0"
STALE, FRESH = "1" * 64, "2" * 64


def never_read(path: Path) -> str:
    raise AssertionError(f"{path} should not have been read")


def test_a_render_keeps_the_pypdf_version_that_read_its_fingerprint(store: Store) -> None:
    ensure_job(store, SID, JOB, now=T0)

    render = render_of(store, T0, pypdf=NEW_PYPDF)

    assert render.pypdf == NEW_PYPDF and get_render(store, render.id) == render
    assert render_of(store, T1).pypdf is None  # not said: not known


def test_a_fingerprint_read_by_the_same_pypdf_is_used_as_it_is(store: Store) -> None:
    ensure_job(store, SID, JOB, now=T0)
    render = render_of(store, T0, fingerprint=STALE, pypdf=NEW_PYPDF)

    assert render_fingerprint(store, render.id, pypdf=NEW_PYPDF, read=never_read) == STALE


@pytest.mark.parametrize("recorded", [OLD_PYPDF, None], ids=["another version", "not recorded"])
def test_a_fingerprint_read_by_another_pypdf_is_read_again_from_its_file(store: Store,
                                                                         recorded) -> None:
    ensure_job(store, SID, JOB, now=T0)
    render = render_of(store, T0, fingerprint=STALE, pypdf=recorded)
    asked: list[Path] = []

    def read(path: Path) -> str:
        asked.append(path)
        return FRESH

    assert render_fingerprint(store, render.id, pypdf=NEW_PYPDF, read=read) == FRESH

    assert asked == [render.cache_path]
    stored = get_render(store, render.id)
    assert (stored.fingerprint, stored.pypdf) == (FRESH, NEW_PYPDF)
    assert render_fingerprint(store, render.id, pypdf=NEW_PYPDF, read=never_read) == FRESH  # once


def test_a_fingerprint_is_read_again_by_the_real_reader(store: Store, tmp_path: Path) -> None:
    pdf = tmp_path / "render.pdf"
    pdf.write_bytes(summary_pdf(project_name=JOB))
    check = check_summary(pdf)
    ensure_job(store, SID, JOB, now=T0)
    render = render_of(store, T0, fingerprint=STALE, pypdf=OLD_PYPDF, cache_path=pdf)

    found = render_fingerprint(store, render.id, pypdf=check.details["pypdf"],
                               read=content_fingerprint)

    assert found == check.details["fingerprint"] == content_fingerprint(pdf)


def test_a_fingerprint_that_can_not_be_read_again_could_not_be_verified(store: Store,
                                                                        tmp_path: Path) -> None:
    ensure_job(store, SID, JOB, now=T0)
    damaged = tmp_path / "damaged.pdf"
    damaged.write_bytes(b"not a PDF")
    no_file = render_of(store, T0, fingerprint=STALE, pypdf=OLD_PYPDF, cache_path=None)
    gone = render_of(store, T0, fingerprint=STALE, pypdf=OLD_PYPDF,
                     cache_path=tmp_path / "gone.pdf")
    bad = render_of(store, T0, fingerprint=STALE, pypdf=OLD_PYPDF, cache_path=damaged)

    for render in (no_file, gone, bad):
        assert render_fingerprint(store, render.id, pypdf=NEW_PYPDF,
                                  read=content_fingerprint) is None
        assert get_render(store, render.id) == render  # never a stale value passed off as current


def test_a_render_with_no_fingerprint_has_none_to_compare(store: Store) -> None:
    ensure_job(store, SID, JOB, now=T0)
    refused = render_of(store, T0, check_ok=False, fingerprint=None, pypdf=NEW_PYPDF)

    assert render_fingerprint(store, refused.id, pypdf=NEW_PYPDF, read=never_read) is None


def test_a_fingerprint_is_never_read_again_inside_a_transaction(store: Store) -> None:
    ensure_job(store, SID, JOB, now=T0)
    render = render_of(store, T0, fingerprint=STALE, pypdf=OLD_PYPDF)

    with store.transaction() as tx:  # reading a PDF is slow, and this holds the write lock
        with pytest.raises(StoreError, match="transaction"):
            render_fingerprint(tx, render.id, pypdf=NEW_PYPDF, read=never_read)

    assert get_render(store, render.id) == render


@pytest.mark.parametrize("version", ["", " ", None, 6])
def test_a_fingerprint_needs_the_version_to_compare_with(store: Store, version) -> None:
    ensure_job(store, SID, JOB, now=T0)
    render = render_of(store, T0)

    with pytest.raises(ValueError):
        render_fingerprint(store, render.id, pypdf=version, read=never_read)


def test_the_fingerprint_of_a_render_that_is_not_there_is_refused(store: Store) -> None:
    with pytest.raises(LookupError):
        render_fingerprint(store, 1, pypdf=NEW_PYPDF, read=never_read)


def test_a_render_of_a_job_the_store_does_not_have_is_refused(store: Store) -> None:
    with pytest.raises(StoreError, match="breaks its rules"):
        render_of(store, T0)


@pytest.mark.parametrize("bad", [
    {"sha256": "not a hash"}, {"layout": "square"}, {"page_count": -1}, {"check_ok": None},
    {"missing": "Page 1 lists no designs."}, {"cache_path": Path("renders") / "x.pdf"},
    {"pypdf": ""}, {"pypdf": " "}, {"pypdf": 6},
])
def test_a_render_with_a_bad_value_is_refused(store: Store, bad) -> None:
    ensure_job(store, SID, JOB, now=T0)

    with pytest.raises(ValueError):
        render_of(store, T0, **bad)

    assert list_renders(store, SID) == []


# ----- pending operations


def test_pending_operations_come_due_in_time_order(store: Store) -> None:
    later = add_pending(store, "fetch_2", studio_id=SID, due_at=T2,
                        deadline_at=T0 + timedelta(hours=24))
    sooner = add_pending(store, "list_wait", fab_row_id=7, due_at=T1,
                         note="asked Render for a push")

    assert due_pending(store, T0) == []
    assert due_pending(store, T1) == [sooner]
    assert due_pending(store, T3) == [sooner, later]
    assert (later.kind, later.studio_id, later.fab_row_id, later.state, later.attempts) == (
        "fetch_2", SID, None, "open", 0)
    assert (later.due_at, later.deadline_at, sooner.note) == (
        T2, T0 + timedelta(hours=24), "asked Render for a push")
    assert get_pending(store, sooner.id) == sooner and get_pending(store, sooner.id + 1) is None


def test_a_time_with_a_fraction_of_a_second_is_compared_as_a_time(store: Store) -> None:
    half_past = T0 + timedelta(milliseconds=500)
    op = add_pending(store, "fetch_1", studio_id=SID, due_at=half_past)

    assert due_pending(store, T0) == []                         # 13:30:00 is before 13:30:00.5
    assert due_pending(store, half_past) == [op]
    assert due_pending(store, T0 + timedelta(seconds=1)) == [op]


def test_an_operation_is_queued_once_while_it_is_open(store: Store) -> None:
    first = add_pending(store, "fetch_2", studio_id=SID, due_at=T1)

    again = add_pending(store, "fetch_2", studio_id=SID, due_at=T3, note="again")  # a restart
    other_job = add_pending(store, "fetch_2", studio_id="2", due_at=T1)
    other_kind = add_pending(store, "fetch_1", studio_id=SID, due_at=T1)
    assert again == first and len({first.id, other_job.id, other_kind.id}) == 3

    assert finish_pending(store, first.id, "done")
    assert add_pending(store, "fetch_2", studio_id=SID, due_at=T3).id != first.id  # a new one


def test_a_retry_counts_and_moves_the_due_time_and_finishing_closes_it(store: Store) -> None:
    op = add_pending(store, "fetch_1", studio_id=SID, due_at=T0, deadline_at=T3)

    assert retry_pending(store, op.id, due_at=T2, note="Studio did not answer")
    retried = get_pending(store, op.id)
    assert (retried.attempts, retried.due_at, retried.note) == (1, T2, "Studio did not answer")
    assert due_pending(store, T1) == []

    assert finish_pending(store, op.id, "failed", note="past 24 hours")
    failed = get_pending(store, op.id)
    assert (failed.state, failed.note) == ("failed", "past 24 hours")
    assert due_pending(store, T3) == [] and past_deadline_pending(store, T3) == []
    assert not finish_pending(store, op.id, "done")      # already closed: nothing changes
    assert not retry_pending(store, op.id, due_at=T3)
    assert get_pending(store, op.id).state == "failed"
    with pytest.raises(LookupError):
        finish_pending(store, op.id + 1, "done")


def test_open_operations_past_their_deadline_are_listed(store: Store) -> None:
    late = add_pending(store, "fetch_1", studio_id=SID, due_at=T0, deadline_at=T1)
    add_pending(store, "fetch_1", studio_id="2", due_at=T0, deadline_at=T3)
    add_pending(store, "check", studio_id=SID, due_at=T0)  # no deadline

    assert past_deadline_pending(store, T0) == []
    assert past_deadline_pending(store, T2) == [late]


def test_pending_operations_are_listed_by_job_kind_and_state(store: Store) -> None:
    a = add_pending(store, "fetch_1", studio_id=SID, due_at=T1)
    b = add_pending(store, "list_wait", fab_row_id=7, due_at=T0)
    c = add_pending(store, "fetch_2", studio_id=SID, due_at=T2)
    finish_pending(store, c.id, "expired")

    assert list_pending(store) == [b, a]                         # open ones, soonest first
    assert list_pending(store, studio_id=SID) == [a]
    assert list_pending(store, fab_row_id=7, kind="list_wait") == [b]
    assert [p.id for p in list_pending(store, state="expired")] == [c.id]
    assert [p.id for p in list_pending(store, state=None)] == [b.id, a.id, c.id]


@pytest.mark.parametrize("bad", [
    {"kind": "Fetch 2"}, {"studio_id": " "}, {"fab_row_id": 0}, {"note": 5}, {"due_at": None},
])
def test_a_pending_operation_with_a_bad_value_is_refused(store: Store, bad) -> None:
    with pytest.raises(ValueError):
        add_pending(store, **{"kind": "fetch_1", "studio_id": SID, "due_at": T0, **bad})
    op = add_pending(store, "fetch_1", studio_id=SID, due_at=T0)
    with pytest.raises(ValueError):
        finish_pending(store, op.id, "open")  # open is not an end

    assert list_pending(store, state=None) == [op]


# ----- runs


def test_a_run_is_recorded_from_its_start_to_its_end(store: Store) -> None:
    run = start_run(store, stage="file", trigger="fab_row", started_at=T0, app_version="0.1.0",
                    studio_id=SID)
    assert get_run(store, run.id) == run
    assert (run.stage, run.trigger, run.studio_id) == ("file", "fab_row", SID)
    assert run.app_version == "0.1.0"
    assert (run.started_at, run.ended_at, run.outcome, run.reason) == (T0, None, None, None)
    assert (run.read_hashes, run.written_hashes) == ({}, {})

    summary = rf"C:\NG Jobs\{JOB}\{JOB}_summary.pdf"
    assert end_run(store, run.id, ended_at=T1, outcome="ok", read_hashes={"render": SHA_A},
                   written_hashes={summary: SHA_B})
    ended = get_run(store, run.id)
    assert (ended.ended_at, ended.outcome, ended.reason) == (T1, "ok", None)
    assert (ended.read_hashes, ended.written_hashes) == ({"render": SHA_A}, {summary: SHA_B})

    assert not end_run(store, run.id, ended_at=T2, outcome="failed", reason="again")  # ended once
    assert get_run(store, run.id) == ended
    with pytest.raises(LookupError):
        end_run(store, run.id + 1, ended_at=T2, outcome="ok")


def test_runs_are_listed_oldest_first_by_job_and_since_a_time(store: Store) -> None:
    a = start_run(store, stage="fetch", trigger="fab_row", started_at=T0, app_version="0.1.0",
                  studio_id=SID)
    b = start_run(store, stage="digest", trigger="schedule", started_at=T1, app_version="0.1.0")
    c = start_run(store, stage="fetch", trigger="night", started_at=T2, app_version="0.1.0",
                  studio_id=SID)

    assert [r.id for r in list_runs(store)] == [a.id, b.id, c.id]
    assert [r.id for r in list_runs(store, studio_id=SID)] == [a.id, c.id]
    assert [r.id for r in list_runs(store, since=T1)] == [b.id, c.id]


@pytest.mark.parametrize("bad", [
    {"stage": "File it"}, {"trigger": ""}, {"app_version": " "}, {"studio_id": " "},
])
def test_a_run_with_a_bad_value_is_refused(store: Store, bad) -> None:
    with pytest.raises(ValueError):
        start_run(store, **{"stage": "file", "trigger": "fab_row", "started_at": T0,
                            "app_version": "0.1.0", **bad})
    run = start_run(store, stage="file", trigger="fab_row", started_at=T0, app_version="0.1.0")
    for hashes in ({"render": "not a hash"}, {"": SHA_A}, ["render"]):
        with pytest.raises(ValueError):
            end_run(store, run.id, ended_at=T1, outcome="ok", read_hashes=hashes)

    assert list_runs(store) == [run]


# ----- cards and card numbers


WHICH = ("Testerson_101426", "Testerson_102026", "None of these")


def test_card_ids_are_the_prefix_and_the_next_number(store: Store) -> None:
    assert [allocate_card_id(store, "TES") for _ in range(2)] == ["TES-1", "TES-2"]
    assert allocate_card_id(store, "FAK") == "FAK-1"  # each prefix counts on its own

    card = add_card(store, prefix="TES", kind="which_job", text="Which job is this row?",
                    choices=WHICH, created_at=T0, fab_row_id=7)

    assert card.id == "TES-3"


def test_a_card_is_stored_and_read_back_whole(store: Store) -> None:
    ensure_job(store, SID, JOB, now=T0)
    card = add_card(store, prefix="TES", kind="closet_or_garage", text="Closet or garage?",
                    choices=("Closet", "Garage"), default_choice="Closet", created_at=T0,
                    studio_id=SID, fab_row_id=7)

    assert get_card(store, card.id) == card
    assert (card.id, card.kind, card.state) == ("TES-1", "closet_or_garage", "open")
    assert (card.text, card.choices) == ("Closet or garage?", ("Closet", "Garage"))
    assert (card.default_choice, card.created_at) == ("Closet", T0)
    assert (card.studio_id, card.fab_row_id, card.reminded_at) == (SID, 7, ())
    assert (card.answer, card.answer_words, card.answered_by) == (None, None, None)
    assert (card.answered_at, card.channel) == (None, None)
    assert get_card(store, "TES-2") is None


def test_a_card_keeps_which_job_each_choice_means(store: Store) -> None:
    # Two sales can share a name, and the job list is replaced whole before Jim may answer: the
    # candidates exist only in memory when the card is made.
    card = add_card(store, prefix="TES", kind="which_job", text="Which job is this row?",
                    choices=WHICH, choice_ids=["84213", "84377", None], created_at=T0,
                    fab_row_id=7)

    assert card.choice_ids == ("84213", "84377", None)
    assert get_card(store, card.id) == card
    assert list_cards(store) == [card]
    assert answer_card(store, card.id, answer=WHICH[1], answer_words="TES-1 2",
                       answered_by="jim", answered_at=T1, channel="command")
    answered = get_card(store, card.id)
    assert answered.choice_ids[answered.choices.index(answered.answer)] == "84377"


def test_a_card_keeps_its_jobs_through_a_restart_and_a_new_job_list(tmp_path: Path) -> None:
    path = tmp_path / "ngrunner.db"
    store = open_store(path)
    replace_opportunities(store, [OpportunityRecord("84213", "Testerson_101426", "Sold", "AB")],
                          synced_at=T0, fetched_at=T0)
    card = add_card(store, prefix="TES", kind="which_job", text="Which job is this row?",
                    choices=("Testerson_101426", "None of these"), choice_ids=("84213", None),
                    created_at=T0)

    restarted = open_store(path)
    replace_opportunities(restarted, [OpportunityRecord("90000", "Fakeley_101526", "Sold", "CD")],
                          synced_at=T1, fetched_at=T1)

    assert get_card(restarted, card.id).choice_ids == ("84213", None)


@pytest.mark.parametrize("no_ids", [{}, {"choice_ids": None}, {"choice_ids": ()},
                                    {"choice_ids": []}])
def test_a_card_whose_choices_name_no_job_says_so_for_each_choice(store: Store, no_ids) -> None:
    card = add_card(store, prefix="TES", kind="closet_or_garage", text="Closet or garage?",
                    choices=("Closet", "Garage"), created_at=T0, **no_ids)

    assert card.choice_ids == (None, None)
    assert get_card(store, card.id) == card


def test_an_answer_is_kept_verbatim_and_given_once(store: Store) -> None:
    card = add_card(store, prefix="TES", kind="which_job", text="Which job is this row?",
                    choices=WHICH, created_at=T0, fab_row_id=7)

    assert answer_card(store, card.id, answer="Testerson_102026", answer_words="TES-1 2",
                       answered_by="jim", answered_at=T1, channel="command")
    assert not answer_card(store, card.id, answer="None of these", answer_words="TES-1 3",
                           answered_by="jim", answered_at=T2, channel="board")  # a second click
    assert not withdraw_card(store, card.id)

    answered = get_card(store, card.id)
    assert (answered.state, answered.answer) == ("answered", "Testerson_102026")
    assert answered.answer_words == "TES-1 2"
    assert (answered.answered_by, answered.answered_at, answered.channel) == ("jim", T1, "command")


def test_a_withdrawn_card_takes_no_answer_and_its_number_is_never_reused(store: Store) -> None:
    card = add_card(store, prefix="TES", kind="which_job", text="Which job is this row?",
                    choices=WHICH, created_at=T0)
    allocate_card_id(store, "TES")  # handed out, never used: skipped for good as well

    assert withdraw_card(store, card.id)
    assert not answer_card(store, card.id, answer=WHICH[0], answer_words="TES-1 1",
                           answered_by="jim", answered_at=T1, channel="board")
    assert get_card(store, card.id).state == "withdrawn"
    assert add_card(store, prefix="TES", kind="which_job", text="Which job is this row?",
                    choices=WHICH, created_at=T1).id == "TES-3"
    with pytest.raises(LookupError):
        withdraw_card(store, "TES-9")


def test_an_answer_that_is_not_one_of_the_choices_is_refused(store: Store) -> None:
    card = add_card(store, prefix="TES", kind="which_job", text="Which job is this row?",
                    choices=WHICH, created_at=T0)

    with pytest.raises(ValueError, match="choices"):
        answer_card(store, card.id, answer="Fakeley_101526", answer_words="TES-1 Fakeley",
                    answered_by="jim", answered_at=T1, channel="command")

    assert get_card(store, card.id) == card


def test_reminders_are_kept_in_the_order_they_were_sent(store: Store) -> None:
    card = add_card(store, prefix="TES", kind="which_job", text="Which job is this row?",
                    choices=WHICH, created_at=T0)
    day = timedelta(days=1)

    record_reminder(store, card.id, at=T0 + day)
    record_reminder(store, card.id, at=T0 + 3 * day)

    assert get_card(store, card.id).reminded_at == (T0 + day, T0 + 3 * day)
    with pytest.raises(LookupError):
        record_reminder(store, "TES-9", at=T1)


def test_cards_are_listed_oldest_first_by_state_job_and_row(store: Store) -> None:
    ensure_job(store, SID, JOB, now=T0)
    a = add_card(store, prefix="TES", kind="closet_or_garage", text="Closet or garage?",
                 choices=("Closet", "Garage"), created_at=T1, studio_id=SID)
    b = add_card(store, prefix="FAK", kind="which_job", text="Which job is this row?",
                 choices=WHICH, created_at=T0, fab_row_id=8)
    c = add_card(store, prefix="MOC", kind="which_job", text="Which job is this row?",
                 choices=WHICH, created_at=T2, fab_row_id=9)
    withdraw_card(store, c.id)

    assert [x.id for x in list_cards(store)] == [b.id, a.id]          # open ones
    assert [x.id for x in list_cards(store, studio_id=SID)] == [a.id]
    assert [x.id for x in list_cards(store, fab_row_id=8)] == [b.id]
    assert [x.id for x in list_cards(store, state="withdrawn")] == [c.id]
    assert [x.id for x in list_cards(store, state=None)] == [b.id, a.id, c.id]


@pytest.mark.parametrize("bad", [
    {"prefix": "rod"}, {"prefix": "RO D"}, {"prefix": ""}, {"prefix": "R-1"}, {"prefix": "ROD1"},
    {"choices": ()}, {"choices": ("Closet", "Closet")}, {"choices": "Closet"},
    {"default_choice": "Pantry"}, {"kind": "Which job"}, {"text": " "},
    {"choice_ids": ("84213",)}, {"choice_ids": ("84213", None, None)}, {"choice_ids": "ab"},
    {"choice_ids": (84213, None)}, {"choice_ids": (" ", None)}, {"choice_ids": ("", None)},
    {"choice_ids": {"Closet": "84213", "Garage": None}},
])
def test_a_card_with_a_bad_value_is_refused(store: Store, bad) -> None:
    with pytest.raises(ValueError):
        add_card(store, **{"prefix": "TES", "kind": "closet_or_garage", "text": "Closet or garage?",
                           "choices": ("Closet", "Garage"), "created_at": T0, **bad})

    assert list_cards(store, state=None) == []
    assert allocate_card_id(store, "TES") == "TES-1"  # a refused card used no number


# ----- the events log and the DEV lane


def test_events_are_logged_and_read_back_in_time_order(store: Store) -> None:
    filed = log_event(store, at=T1, kind="filed", text=f"Filed the IC summary for {JOB}.",
                      lane="ACT", studio_id=SID)
    seen = log_event(store, at=T0, kind="row_seen", text="A new Fab row: Testerson.")
    odd = log_event(store, at=T2, kind="lint_failed", text="A message failed its lint.", lane="DEV")

    assert list_events(store) == [seen, filed, odd]
    assert (filed.at, filed.kind, filed.lane, filed.studio_id) == (T1, "filed", "ACT", SID)
    assert (seen.lane, seen.studio_id) == ("LOG", None)
    assert list_events(store, since=T1) == [filed, odd]
    assert list_events(store, since=T0, until=T1) == [seen]   # until is not included
    assert list_events(store, studio_id=SID) == [filed]
    assert list_events(store, lane="DEV") == [odd]


def test_a_dev_item_is_opened_once_while_it_stands(store: Store) -> None:
    stale = "The Fab export is over 30 minutes old."
    item = open_dev(store, at=T0, code="fab_export_stale", text=stale)

    again = open_dev(store, at=T1, code="fab_export_stale", text=stale)
    other = open_dev(store, at=T1, code="fab_export_stale", text="No Fab export at all.")

    assert again == item and other.id != item.id
    assert (item.at, item.code) == (T0, "fab_export_stale")
    assert (item.state, item.told_jim_at) == ("open", None)
    assert list_dev(store) == [item, other] and get_dev(store, item.id) == item

    assert close_dev(store, item.id)
    assert not close_dev(store, item.id)  # closed once
    assert list_dev(store) == [other]
    assert [d.id for d in list_dev(store, state="closed")] == [item.id]
    reopened = open_dev(store, at=T2, code="fab_export_stale", text=stale)
    assert reopened.id not in (item.id, other.id)  # it came back: a new item
    with pytest.raises(LookupError):
        close_dev(store, reopened.id + 1)


def test_a_dev_item_can_name_its_job_and_say_it_is_urgent(store: Store) -> None:
    stuck = open_dev(store, at=T0, code="too_big", text="The render was too big.", studio_id=SID,
                     urgent=True)
    plain = open_dev(store, at=T0, code="no_receipt", text="No receipt for 5 minutes.")

    assert (stuck.studio_id, stuck.urgent) == (SID, True)
    assert (plain.studio_id, plain.urgent) == (None, False)
    assert get_dev(store, stuck.id) == stuck and list_dev(store) == [stuck, plain]


def test_a_dev_item_is_opened_once_per_code_text_and_job(store: Store) -> None:
    text = "The render was too big."
    mine = open_dev(store, at=T0, code="too_big", text=text, studio_id=SID)
    other_job = open_dev(store, at=T0, code="too_big", text=text, studio_id="2")
    no_job = open_dev(store, at=T0, code="too_big", text=text)

    assert len({mine.id, other_job.id, no_job.id}) == 3
    assert open_dev(store, at=T1, code="too_big", text=text, studio_id=SID) == mine
    assert open_dev(store, at=T1, code="too_big", text=text, studio_id="2") == other_job
    assert open_dev(store, at=T1, code="too_big", text=text) == no_job
    assert list_dev(store) == [mine, other_job, no_job]


def test_a_standing_dev_item_raised_as_urgent_becomes_urgent_and_stays_so(store: Store) -> None:
    text = "No receipt for 5 minutes."
    item = open_dev(store, at=T0, code="no_receipt", text=text)

    raised = open_dev(store, at=T1, code="no_receipt", text=text, urgent=True)
    again = open_dev(store, at=T2, code="no_receipt", text=text)

    assert (raised.id, raised.at, raised.urgent) == (item.id, T0, True)  # one item, the tray red
    assert again == raised == get_dev(store, item.id)
    assert list_dev(store) == [raised]


@pytest.mark.parametrize("bad", [{"studio_id": " "}, {"studio_id": 101}, {"urgent": "yes"},
                                 {"urgent": 1}, {"urgent": None}])
def test_a_dev_item_with_a_bad_job_or_urgency_is_refused(store: Store, bad) -> None:
    with pytest.raises(ValueError):
        open_dev(store, at=T0, code="no_receipt", text="No receipt.", **bad)

    assert list_dev(store, state=None) == []


def test_telling_jim_about_a_dev_item_is_recorded_once(store: Store) -> None:
    item = open_dev(store, at=T0, code="no_receipt", text="No receipt for 5 minutes.")

    assert mark_dev_told_jim(store, item.id, at=T2)
    assert not mark_dev_told_jim(store, item.id, at=T3)  # the first time stays

    assert get_dev(store, item.id).told_jim_at == T2
    with pytest.raises(LookupError):
        mark_dev_told_jim(store, item.id + 1, at=T3)


@pytest.mark.parametrize("bad", [{"lane": "INFO"}, {"kind": "Filed it"}, {"text": ""},
                                 {"studio_id": " "}])
def test_an_event_with_a_bad_value_is_refused(store: Store, bad) -> None:
    with pytest.raises(ValueError):
        log_event(store, **{"at": T0, "kind": "filed", "text": "Filed.", **bad})
    with pytest.raises(ValueError):
        open_dev(store, at=T0, code="Bad Code", text="x")

    assert list_events(store) == [] and list_dev(store) == []


# ----- the write record: writes and owned


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def test_each_commit_is_recorded_with_its_hashes_before_and_after(store: Store) -> None:
    folder = Path(r"C:\NG Jobs") / JOB
    writes = [FileWrite(folder / f"{JOB}_summary.pdf", b"%PDF-1.7 new", None),
              FileWrite(folder / f"{JOB}_NGRunner.json", b"{}", SHA_B.upper())]  # any case
    backup = Path(r"C:\data\backup\20261030T133000Z\ng_jobs") / JOB / f"{JOB}_NGRunner.json"

    done = record_commit(store, writes, CommitResult(ok=True, written=tuple(w.path for w in writes),
                                                     backups=(backup,)), at=T0)
    refused = record_commit(store, writes[:1], CommitResult(ok=False, reason="Not written: paused"),
                            at=T1)

    assert list_write_records(store) == [done, refused]
    assert (done.at, done.ok, done.reason, done.backups) == (T0, True, None, (backup,))
    assert done.paths == (folder / f"{JOB}_summary.pdf", folder / f"{JOB}_NGRunner.json")
    assert done.before == (None, SHA_B) and done.after == (sha(b"%PDF-1.7 new"), sha(b"{}"))
    assert (refused.ok, refused.reason, refused.after) == (False, "Not written: paused",
                                                         (sha(b"%PDF-1.7 new"),))
    assert list_write_records(store, since=T1) == [refused]


def test_owned_files_are_keyed_as_writes_keys_its_registry(store: Store) -> None:
    summary = Path(r"C:\NG Jobs") / JOB / f"{JOB}_summary.pdf"
    assert path_key(summary) == rf"c:\ng jobs\{JOB.lower()}\{JOB.lower()}_summary.pdf"

    record_owned(store, {summary: SHA_A}, at=T0)
    assert owned_sha(store, summary) == SHA_A
    assert owned_sha(store, Path(str(summary).upper())) == SHA_A  # Windows names ignore case
    assert owned_sha(store, summary.with_name("other.pdf")) is None

    record_owned(store, {summary: SHA_B}, at=T1)  # NG Runner wrote it again
    assert owned_sha(store, summary) == SHA_B


def test_the_owned_table_answers_commit_writes_question_about_its_own_files(tmp_path: Path) -> None:
    roots = Roots(tmp_path / "NG Jobs", tmp_path / "Mozaik Shared", tmp_path / "control",
                  tmp_path / "appdata")
    for folder in (roots.ng_jobs, roots.mozaik_shared, roots.control, roots.app_data):
        folder.mkdir()
    store = open_store(roots.app_data / "ngrunner.db")

    def ours(path: Path) -> str | None:
        return owned_sha(store, path)

    target = roots.control / "status.json"
    first = commit_write([FileWrite(target, b"v1", None)], roots=roots,
                         interlock=lambda paths: None, owned=ours)
    assert first.ok, first.reason
    refused = commit_write([FileWrite(target, b"v2", sha(b"v1"))], roots=roots,
                           interlock=lambda paths: None, owned=ours)
    assert not refused.ok  # not recorded as NG Runner's yet, so not overwritten

    record_owned(store, {path: sha(b"v1") for path in first.written}, at=T0)
    second = commit_write([FileWrite(target, b"v2", sha(b"v1"))], roots=roots,
                          interlock=lambda paths: None, owned=ours)

    assert second.ok, second.reason
    assert target.read_bytes() == b"v2"


@pytest.mark.parametrize("bad", [
    {Path("relative.pdf"): SHA_A}, {Path(r"C:\x.pdf"): "not a hash"},
    {Path(r"C:\x.pdf"): SHA_A.upper()},
])
def test_an_owned_entry_with_a_bad_value_is_refused(store: Store, bad) -> None:
    with pytest.raises(ValueError):
        record_owned(store, bad, at=T0)

    assert owned_sha(store, Path(r"C:\x.pdf")) is None


# ----- the outbox


ID_1, ID_2, ID_3 = ("1" * 32, "2" * 32, "3" * 32)


def message(store: Db, outbox_id: str, seq: int, **fields):
    defaults = dict(channel="email", recipient="barbara", subject=f"Filed as {JOB}",
                    lines=(f"1. {JOB}: filed the IC summary.",), urgent=False, created_at=T0,
                    ref=SID)
    return add_outbox(store, id=outbox_id, seq=seq, **{**defaults, **fields})


def test_an_outbox_message_is_stored_and_read_back_whole(store: Store) -> None:
    sent = message(store, ID_1, 1)
    urgent = message(store, ID_2, 2, channel="pushover", recipient="jim", urgent=True,
                     subject="NG Runner can't send messages",
                     lines=("1. No receipt in 5 minutes.",),
                     ref=None)

    assert get_outbox(store, ID_1) == sent and list_outbox(store) == [sent, urgent]
    assert (sent.id, sent.seq, sent.channel) == (ID_1, 1, "email")
    assert (sent.recipient, sent.urgent) == ("barbara", False)
    assert (sent.subject, sent.lines, sent.ref, sent.state) == (
        f"Filed as {JOB}", (f"1. {JOB}: filed the IC summary.",), SID, "queued")
    assert (sent.created_at, sent.written_at) == (T0, None)
    assert (sent.receipt_at, sent.fallback_at) == (None, None)
    assert (urgent.urgent, urgent.ref) == (True, None) and get_outbox(store, ID_3) is None


def test_a_message_is_written_then_receipted_once(store: Store) -> None:
    message(store, ID_1, 1)
    message(store, ID_2, 2)

    assert mark_outbox_written(store, ID_1, at=T1) and not mark_outbox_written(store, ID_1, at=T2)
    assert record_receipt(store, ID_1, at=T2, ok=True)
    assert not record_receipt(store, ID_1, at=T3, ok=False)  # a second receipt changes nothing
    assert record_receipt(store, ID_2, at=T3, ok=False)      # its file was written, not yet marked

    first, second = get_outbox(store, ID_1), get_outbox(store, ID_2)
    assert (first.state, first.written_at, first.receipt_at) == ("sent", T1, T2)
    assert (second.state, second.written_at, second.receipt_at) == ("failed", None, T3)
    assert list_outbox(store, state="sent") == [first]
    with pytest.raises(LookupError):
        record_receipt(store, ID_3, at=T3, ok=True)


def test_messages_written_long_ago_without_a_receipt_are_listed(store: Store) -> None:
    for n, outbox_id in enumerate((ID_1, ID_2, ID_3), 1):
        message(store, outbox_id, n)
    mark_outbox_written(store, ID_1, at=T0)
    mark_outbox_written(store, ID_2, at=T0)
    record_receipt(store, ID_2, at=T1, ok=True)               # receipted: fine
    five_minutes = timedelta(minutes=5)                         # ID_3 is not written yet

    assert outbox_without_receipt(store, written_before=T0 - five_minutes) == []
    assert [m.id for m in outbox_without_receipt(store, written_before=T0 + five_minutes)] == [ID_1]


def test_the_direct_pushover_path_is_noted_once(store: Store) -> None:
    message(store, ID_1, 1, urgent=True)

    assert mark_outbox_fallback(store, ID_1, at=T1)
    assert not mark_outbox_fallback(store, ID_1, at=T2)

    assert get_outbox(store, ID_1).fallback_at == T1
    with pytest.raises(LookupError):
        mark_outbox_fallback(store, ID_2, at=T1)


def test_a_sequence_number_or_id_is_never_used_twice(store: Store) -> None:
    first = message(store, ID_1, 1)

    with pytest.raises(StoreError, match="breaks its rules"):
        message(store, ID_2, 1)
    with pytest.raises(StoreError, match="breaks its rules"):
        message(store, ID_1, 2)

    assert list_outbox(store) == [first]


@pytest.mark.parametrize("bad", [
    {"outbox_id": "x" * 32}, {"seq": 0}, {"channel": "sms"},
    {"recipient": "pat" + "@" + "example.com"},
    {"lines": "1. A line."}, {"lines": ()}, {"urgent": "yes"}, {"subject": ""},
])
def test_an_outbox_message_with_a_bad_value_is_refused(store: Store, bad) -> None:
    args = {"outbox_id": ID_1, "seq": 1, **bad}

    with pytest.raises(ValueError):
        message(store, args.pop("outbox_id"), args.pop("seq"), **args)

    assert list_outbox(store) == []


# ----- sequences and the replay guard


def test_each_outgoing_stream_counts_up_from_one(store: Store) -> None:
    assert last_sent_seq(store, "heartbeat") == 0

    assert [next_seq(store, "heartbeat") for _ in range(3)] == [1, 2, 3]
    assert next_seq(store, "outbox") == 1  # streams count on their own

    assert (last_sent_seq(store, "heartbeat"), last_sent_seq(store, "outbox")) == (3, 1)


def test_a_new_outgoing_stream_begins_at_its_start(store: Store) -> None:
    second = 1_793_000_000  # a Unix second, as the messenger and the heartbeat pass

    assert next_seq(store, "heartbeat", start=second) == second
    assert next_seq(store, "heartbeat", start=second + 300) == second + 1  # no gap: it carries on
    assert next_seq(store, "heartbeat") == second + 2
    assert next_seq(store, "outbox", start=second + 300) == second + 300   # each on its own

    assert last_sent_seq(store, "heartbeat") == second + 2


def test_a_new_database_on_a_used_control_folder_sends_above_what_the_old_one_sent(
    tmp_path: Path,
) -> None:
    second = 1_793_000_000
    old = open_store(tmp_path / "old.db")
    with old.transaction() as tx:
        for _ in range(288):  # a day of heartbeats
            seen_by_apps_script = next_seq(tx, "heartbeat", start=second)
    a_day_later = second + 86_400

    new = open_store(tmp_path / "new.db")  # a wiped data folder: no restore, nothing remembered

    assert next_seq(new, "heartbeat", start=a_day_later) > seen_by_apps_script


def test_a_seq_taken_in_a_transaction_that_fails_is_not_used_up(store: Store) -> None:
    with pytest.raises(RuntimeError):
        with store.transaction() as tx:
            assert next_seq(tx, "outbox", start=500) == 500
            raise RuntimeError("stopped half-way")

    assert last_sent_seq(store, "outbox") == 0
    assert next_seq(store, "outbox", start=900) == 900


def test_a_restore_jumps_every_outgoing_stream_and_every_card_number(store: Store) -> None:
    for _ in range(3):
        next_seq(store, "heartbeat")
    next_seq(store, "outbox", start=500)
    next_seq(store, "pause")
    allocate_card_id(store, "TES")
    allocate_card_id(store, "TES")
    allocate_card_id(store, "FAK")

    jump_after_restore(store)

    assert last_sent_seq(store, "heartbeat") == 1_000_003
    assert last_sent_seq(store, "outbox") == 1_000_500
    assert next_seq(store, "heartbeat") == 1_000_004
    assert allocate_card_id(store, "TES") == "TES-1003"
    assert allocate_card_id(store, "FAK") == "FAK-1002"


def test_a_restore_keeps_what_ng_runner_has_received_in_its_place(store: Store) -> None:
    assert accept_seq(store, "fab_export", 7)
    assert accept_seq(store, "pause", 3)
    next_seq(store, "pause")  # the same name used both ways: only what is sent jumps

    jump_after_restore(store)

    assert last_accepted_seq(store, "fab_export") == 7
    assert last_accepted_seq(store, "pause") == 3
    assert last_sent_seq(store, "pause") == 1_000_001
    assert accept_seq(store, "fab_export", 8)


def test_a_restore_jump_is_part_of_its_transaction(store: Store) -> None:
    next_seq(store, "heartbeat")
    allocate_card_id(store, "TES")

    with pytest.raises(RuntimeError):
        with store.transaction() as tx:
            jump_after_restore(tx)
            raise RuntimeError("stopped half-way")

    assert last_sent_seq(store, "heartbeat") == 1
    assert allocate_card_id(store, "TES") == "TES-2"


def test_an_incoming_seq_is_accepted_only_above_the_last_one(store: Store) -> None:
    assert last_accepted_seq(store, "fab_export") == 0

    assert accept_seq(store, "fab_export", 1)
    assert accept_seq(store, "fab_export", 2)
    assert not accept_seq(store, "fab_export", 2)   # the same file read again, or a replay
    assert not accept_seq(store, "fab_export", 1)   # an older one
    assert accept_seq(store, "fab_export", 7)       # gaps are fine
    assert not accept_seq(store, "fab_export", 6)

    assert last_accepted_seq(store, "fab_export") == 7
    assert accept_seq(store, "receipt", 1)          # another stream has its own last


def test_sent_and_accepted_sequences_never_mix_even_under_one_name(store: Store) -> None:
    for _ in range(5):
        next_seq(store, "pause")

    assert accept_seq(store, "pause", 1)            # nothing accepted yet on the incoming side
    assert next_seq(store, "pause") == 6            # and accepting moved nothing outgoing
    assert (last_sent_seq(store, "pause"), last_accepted_seq(store, "pause")) == (6, 1)


def test_an_id_is_new_once_then_seen(store: Store) -> None:
    assert seen_id(store, "pause", ID_1)
    assert not seen_id(store, "pause", ID_1)
    assert seen_id(store, "receipt", ID_1)          # per stream
    assert seen_id(store, "pause", ID_2)


def test_an_envelope_is_accepted_only_with_a_new_seq_and_a_new_id(store: Store) -> None:
    assert accept_envelope(store, "pause", seq=3, id=ID_1)
    assert not accept_envelope(store, "pause", seq=3, id=ID_1)   # a replayed file
    assert not accept_envelope(store, "pause", seq=2, id=ID_2)   # an old seq: its id is not kept
    assert not accept_envelope(store, "pause", seq=4, id=ID_1)   # a known id: the seq stays

    assert last_accepted_seq(store, "pause") == 3
    assert accept_envelope(store, "pause", seq=4, id=ID_2)       # ID_2 was never recorded
    assert last_accepted_seq(store, "pause") == 4


def test_only_the_newest_seen_ids_of_a_stream_are_kept(store: Store) -> None:
    # Ten ids seen, highest first: "newest" must mean last seen, whatever the id says.
    ids = [f"{n:032x}" for n in range(10, 0, -1)]
    for envelope_id in ids:
        assert seen_id(store, "receipt", envelope_id)

    assert prune_seen_ids(store, keep_last=3) == 7

    assert [seen_id(store, "receipt", i) for i in ids[-3:]] == [False, False, False]  # still known
    assert seen_id(store, "receipt", ids[0])                                # forgotten: looks new
    assert prune_seen_ids(store, keep_last=3) == 1                          # that one, just seen


def test_a_busy_stream_never_pushes_out_a_quiet_streams_ids(store: Store) -> None:
    assert seen_id(store, "pause", ID_1)
    for n in range(5):
        assert seen_id(store, "receipt", f"{n:032x}")

    assert prune_seen_ids(store, keep_last=2) == 3

    assert not seen_id(store, "pause", ID_1)
    assert seen_id(store, "pause", ID_2)


def test_a_replayed_envelope_stays_refused_after_its_id_is_pruned(store: Store) -> None:
    assert accept_envelope(store, "fab_export", seq=1, id=ID_1)
    assert accept_envelope(store, "fab_export", seq=2, id=ID_2)

    assert prune_seen_ids(store, keep_last=1) == 1

    assert not accept_envelope(store, "fab_export", seq=1, id=ID_1)  # the seq rule still holds
    assert last_accepted_seq(store, "fab_export") == 2


def test_pruning_seen_ids_keeps_5000_of_each_stream_by_default(store: Store) -> None:
    with store.transaction() as tx:
        for n in range(5003):
            seen_id(tx, "fab_export", f"{n:032x}")
        assert prune_seen_ids(tx) == 3

    assert prune_seen_ids(store) == 0
    assert seen_id(store, "fab_export", f"{2:032x}")      # the third seen is gone
    assert not seen_id(store, "fab_export", f"{3:032x}")  # the fourth is the oldest kept


@pytest.mark.parametrize("keep_last", [0, -1, True, "5", 1.5, None])
def test_a_bad_number_of_seen_ids_to_keep_is_refused(store: Store, keep_last) -> None:
    assert seen_id(store, "pause", ID_1)

    with pytest.raises(ValueError):
        prune_seen_ids(store, keep_last=keep_last)

    assert not seen_id(store, "pause", ID_1)


@pytest.mark.parametrize("call", [
    lambda s: next_seq(s, "Heart Beat"), lambda s: accept_seq(s, "fab_export", 0),
    lambda s: accept_seq(s, "fab_export", True), lambda s: seen_id(s, "pause", " "),
    lambda s: accept_envelope(s, "out:pause", seq=1, id=ID_1),
    lambda s: next_seq(s, "pause", start=0), lambda s: next_seq(s, "pause", start=-5),
    lambda s: next_seq(s, "pause", start=True), lambda s: next_seq(s, "pause", start="5"),
    lambda s: next_seq(s, "pause", start=1.5), lambda s: next_seq(s, "pause", start=None),
])
def test_a_bad_stream_seq_or_id_is_refused(store: Store, call) -> None:
    with pytest.raises(ValueError):
        call(store)

    assert last_accepted_seq(store, "fab_export") == 0 and last_accepted_seq(store, "pause") == 0
    assert last_sent_seq(store, "pause") == 0


# ----- the state table


def test_state_values_are_set_replaced_and_cleared(store: Store) -> None:
    assert get_state(store, "paused") is None and get_state(store, "paused", "no") == "no"

    set_state(store, "paused", "yes")
    set_state(store, "paused", "no")
    set_state(store, "toasts", "")       # an empty value is a value
    assert (get_state(store, "paused"), get_state(store, "toasts")) == ("no", "")

    clear_state(store, "paused")
    clear_state(store, "never_set")
    assert get_state(store, "paused") is None


def test_state_under_a_prefix_is_read_in_one_go(store: Store) -> None:
    set_state(store, "filed.4242", "one")
    set_state(store, "filed.4299", "two")
    set_state(store, "filedx", "not under it")
    set_state(store, "paused", "no")
    assert states_under(store, "filed.") == {"filed.4242": "one", "filed.4299": "two"}
    assert states_under(store, "nothing.") == {}


def test_state_times_are_stored_like_every_other_time(store: Store) -> None:
    set_state_time(store, "installed_at", datetime(2026, 10, 30, 6, 30, tzinfo=PACIFIC))

    assert get_state_time(store, "installed_at") == T0
    assert get_state(store, "installed_at") == "2026-10-30T13:30:00+00:00"   # to_iso text
    assert get_state_time(store, "last_digest_at") is None
    set_state(store, "paused", "yes")
    with pytest.raises(ValueError):
        get_state_time(store, "paused")  # not a time: refused, never guessed


@pytest.mark.parametrize("key", ["Paused", "paused now", "", "x" * 65, 7])
def test_a_bad_state_key_is_refused(store: Store, key) -> None:
    with pytest.raises(ValueError):
        set_state(store, key, "yes")
    with pytest.raises(ValueError):
        set_state(store, "paused", 1)


# ----- a naive time is refused everywhere, and nothing changes


NAIVE = datetime(2026, 10, 30, 6, 30)  # no zone: which 6:30?


@pytest.fixture
def seeded(store: Store) -> dict[str, object]:
    """One row in every table group, so a refused call has something to leave alone (one
    transaction: one commit instead of fourteen)."""
    ids: dict[str, object] = {"store": store}
    with store.transaction() as tx:
        ensure_job(tx, SID, JOB, now=T0)
        ids["row"] = add_fab_row(tx, first_seen=T0, text="Testerson", designer="AB").id
        replace_opportunities(tx, [Opportunity(JOB, SID, "AB", "Sold")], synced_at=T0,
                              fetched_at=T0)
        ids["render"] = render_of(tx, T0).id
        ids["pending"] = add_pending(tx, "fetch_1", studio_id=SID, due_at=T0).id
        ids["run"] = start_run(tx, stage="file", trigger="fab_row", started_at=T0,
                               app_version="0.1.0").id
        ids["card"] = add_card(tx, prefix="TES", kind="which_job", text="Which job?",
                               choices=WHICH, created_at=T0).id
        log_event(tx, at=T0, kind="filed", text="Filed.")
        ids["dev"] = open_dev(tx, at=T0, code="no_receipt", text="No receipt.").id
        record_commit(tx, [FileWrite(Path(r"C:\x.pdf"), b"x", None)], CommitResult(ok=True), at=T0)
        record_owned(tx, {Path(r"C:\x.pdf"): SHA_A}, at=T0)
        message(tx, ID_1, 1)
        set_state_time(tx, "installed_at", T0)
    return ids


def everything(store: Store) -> tuple[object, ...]:
    """All the store holds, read through its public functions."""
    return (
        list_jobs(store), list_aliases(store, SID), list_fab_rows(store), get_job_list(store),
        list_renders(store, SID), list_pending(store, state=None), list_runs(store),
        list_cards(store, state=None), list_events(store), list_dev(store, state=None),
        list_write_records(store), owned_sha(store, Path(r"C:\x.pdf")), list_outbox(store),
        get_state(store, "installed_at"),
    )


NAIVE_CALLS = {
    "ensure_job": lambda s, i: ensure_job(s, "2", "Fakeley_101526", now=NAIVE),
    "update_job": lambda s, i: update_job(s, SID, now=NAIVE, folder=JOB),
    "freeze_name": lambda s, i: freeze_name(s, SID, now=NAIVE),
    "add_alias": lambda s, i: add_alias(s, SID, "Fakeley_101526", seen_at=NAIVE),
    "add_fab_row": lambda s, i: add_fab_row(s, first_seen=NAIVE, text="Fakeley", designer="CD"),
    "update_fab_row": lambda s, i: update_fab_row(s, i["row"], now=NAIVE, state="gone"),
    "replace_opportunities synced_at": lambda s, i: replace_opportunities(
        s, [], synced_at=NAIVE, fetched_at=T1),
    "replace_opportunities fetched_at": lambda s, i: replace_opportunities(
        s, [], synced_at=T1, fetched_at=NAIVE),
    "add_render": lambda s, i: render_of(s, NAIVE),
    "add_pending due_at": lambda s, i: add_pending(s, "fetch_2", studio_id=SID, due_at=NAIVE),
    "add_pending deadline_at": lambda s, i: add_pending(s, "fetch_2", studio_id=SID, due_at=T1,
                                                        deadline_at=NAIVE),
    "due_pending": lambda s, i: due_pending(s, NAIVE),
    "past_deadline_pending": lambda s, i: past_deadline_pending(s, NAIVE),
    "retry_pending": lambda s, i: retry_pending(s, i["pending"], due_at=NAIVE),
    "start_run": lambda s, i: start_run(s, stage="file", trigger="jim", started_at=NAIVE,
                                        app_version="0.1.0"),
    "end_run": lambda s, i: end_run(s, i["run"], ended_at=NAIVE, outcome="ok"),
    "list_runs": lambda s, i: list_runs(s, since=NAIVE),
    "add_card": lambda s, i: add_card(s, prefix="FAK", kind="which_job", text="Which job?",
                                      choices=WHICH, created_at=NAIVE),
    "answer_card": lambda s, i: answer_card(s, i["card"], answer=WHICH[0], answer_words="1",
                                            answered_by="jim", answered_at=NAIVE, channel="board"),
    "record_reminder": lambda s, i: record_reminder(s, i["card"], at=NAIVE),
    "log_event": lambda s, i: log_event(s, at=NAIVE, kind="filed", text="Filed."),
    "list_events": lambda s, i: list_events(s, since=NAIVE),
    "open_dev": lambda s, i: open_dev(s, at=NAIVE, code="other", text="Other."),
    "mark_dev_told_jim": lambda s, i: mark_dev_told_jim(s, i["dev"], at=NAIVE),
    "record_commit": lambda s, i: record_commit(s, [], CommitResult(ok=False, reason="x"),
                                                at=NAIVE),
    "list_write_records": lambda s, i: list_write_records(s, since=NAIVE),
    "record_owned": lambda s, i: record_owned(s, {Path(r"C:\y.pdf"): SHA_B}, at=NAIVE),
    "add_outbox": lambda s, i: message(s, ID_2, 2, created_at=NAIVE),
    "mark_outbox_written": lambda s, i: mark_outbox_written(s, ID_1, at=NAIVE),
    "record_receipt": lambda s, i: record_receipt(s, ID_1, at=NAIVE, ok=True),
    "mark_outbox_fallback": lambda s, i: mark_outbox_fallback(s, ID_1, at=NAIVE),
    "outbox_without_receipt": lambda s, i: outbox_without_receipt(s, written_before=NAIVE),
    "set_state_time": lambda s, i: set_state_time(s, "installed_at", NAIVE),
}


@pytest.mark.parametrize("call", NAIVE_CALLS.values(), ids=NAIVE_CALLS.keys())
def test_a_naive_time_is_refused_and_nothing_changes(seeded: dict[str, object], call) -> None:
    store = seeded["store"]
    before = everything(store)

    with pytest.raises(ValueError, match="naive|timezone-aware"):
        call(store, seeded)

    assert everything(store) == before


def test_a_time_that_is_not_a_datetime_is_refused(store: Store) -> None:
    for not_a_time in ("2026-10-30T13:30:00+00:00", T0.date(), 1761831000):
        with pytest.raises(ValueError, match="timezone-aware"):
            ensure_job(store, SID, JOB, now=not_a_time)

    assert list_jobs(store) == []
