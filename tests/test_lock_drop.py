"""
A tracker batch at Lock (design 5, "Tracker integrity"; BUILD 817-837 and its three traps): the checks
before a drop, the drop itself into a folder the test passes, and what the snapshots say after it.

Nothing is written to the real tracker inbox: the drop folder is a test folder under tmp_path, written
through commit_write with P4's rules. Snapshots are synthetic, in the shape guards/tracker.py reads, with
totals that agree as BUILD's four equalities need. Job names, materials and prices are made up.
"""
from __future__ import annotations

import csv
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ngrunner.clock import FakeClock
from ngrunner.guards.tracker import read_tracker
from ngrunner.lock.drop import (
    CONFIRM_AFTER, IN_PROGRESS, LOCKED, SNAPSHOT_MAX_AGE, after_drop, before_drop, drop_batch,
    resend_missing,
)
from ngrunner.lock.totals import four_equalities, read_totals
from ngrunner.store import open_store
from ngrunner.store.locks import get_batch, list_batches
from ngrunner.tracker_inbox import HEADER
from ngrunner.writes import Roots
from test_guards_tracker import JOB_HEADER, SHEET_HEADER, TAPE_HEADER

JOB, OTHER = "Testerson_101426", "Sampleton_101526"
WHITE, TAPE = "Egger Classic White", 'Egger Classic White 1mm — 15/16" / 1"'
BACKS = "Egger Classic White (1/4) 5x9"
T0 = datetime(2026, 10, 31, 13, 0, tzinfo=UTC)
APPEND = [list(HEADER),
          ["SHEETGOODS", JOB, "", WHITE, "24.3778", "262.40", "8.20", ""],
          ["SHEETGOODS", JOB, "", BACKS, "0.6652", "7.16", "0.22", ""],
          ["EDGEBAND", JOB, "", TAPE, "376.2", "", WHITE],
          ["JOBSUMMARY", JOB, "", IN_PROGRESS, ""]]
SET_WHITE = [list(HEADER), ["SHEETGOODS_SET", JOB, WHITE, "24.3778", "262.40", "8.20", "9"]]


# ----- a snapshot


def _write(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as out:
        csv.writer(out).writerows(rows)


def snapshot(folder: Path, *, taken: datetime, sheets=(), tape=(), jobs=(), prices=None,
             tape_prices=None, extra_totals: bool = False, total_off: float = 0.0) -> Path:
    """sheets: (job, material, calc, optimizer, price); tape: (job, material, calc, optimizer, price);
    jobs: (job, status). Costs and totals are worked out the way the tracker does."""
    folder.mkdir(parents=True, exist_ok=True)
    sheet_rows, sheet_cost, labor = [], 0.0, 0.0
    for job, material, calc, optimizer, price in sheets:
        cost = float(optimizer or 0) * float(price or 0)
        sheet_cost += cost
        labor += 10.0 if optimizer else 0.0
        sheet_rows.append([job, "Fakecustomer", material, "1.0", "10.0", calc, optimizer,
                           f"${price}" if price else "", f"${cost:,.2f}", "", "", "", f"${10.0 if optimizer else 0:,.2f}",
                           "", "Ready", "Ready", ""])
    tape_rows, tape_cost = [], 0.0
    for job, material, calc, optimizer, price in tape:
        cost = float(optimizer or 0) * float(price or 0)
        tape_cost += cost
        tape_rows.append([job, "Fakecustomer", material, calc, optimizer, f"${price}", f"${cost:,.2f}",
                          "", "", "Ready", WHITE])
    job_rows = [[job, "Fakecustomer", "", "", "", "", status, "a note"] for job, status in jobs]
    totals = [["TOTALS", "", "", "", "", "", "", "", f"${sheet_cost:,.2f}", "", "", "", f"${labor:,.2f}"]]
    _write(folder / "tracker_snapshot_SheetGoods.csv",
           [["TITLE"], SHEET_HEADER, *sheet_rows, *(totals * (2 if extra_totals else 1))])
    _write(folder / "tracker_snapshot_Edgeband.csv",
           [["TITLE"], TAPE_HEADER, *tape_rows, ["TOTALS", "", "", "", "", "", f"${tape_cost:,.2f}"]])
    grand = sheet_cost + tape_cost + labor + total_off
    _write(folder / "tracker_snapshot_JobSummary.csv",
           [["TITLE"], JOB_HEADER, *job_rows,
            ["GRAND TOTAL", "", f"${sheet_cost:,.2f}", f"${tape_cost:,.2f}", f"${labor:,.2f}",
             f"${grand:,.2f}", "", ""]])
    sheet_keys = [[WHITE, "$40.00"], [BACKS, "$65.00"]] if prices is None else prices
    tape_keys = [[TAPE, "$0.27"]] if tape_prices is None else tape_prices
    _write(folder / "tracker_snapshot_Pricing.csv",
           [["PRICING"], ["SHEET GOODS — price per sheet"], ["Material (this exact text)", "$/Sheet"],
            *sheet_keys, ["▲ add sheet-good materials anywhere above this line"],
            ["EDGEBAND — price per linear foot"], ["Material + gauge + tape width", "$/LF"], *tape_keys,
            ["▲ add edgeband anywhere above this line"]])
    _write(folder / "tracker_snapshot_meta.csv",
           [["key", "value"], ["snapshot_taken", taken.strftime("%Y-%m-%dT%H:%M:%SZ")]])
    return folder


def landed_rows(job: str = JOB, *, optimizer: str = "", status: str = IN_PROGRESS):
    return dict(sheets=[(job, WHITE, "8.2", optimizer, "40.00"), (job, BACKS, "0.22", "", "65.00")],
                tape=[(job, TAPE, "376.2", "", "0.27")], jobs=[(job, status)])


@pytest.fixture
def env(tmp_path: Path):
    roots = Roots(tmp_path / "NG Jobs", tmp_path / "Mozaik copy", tmp_path / "control", tmp_path / "data",
                  tracker=tmp_path / "tracker test")
    for folder in (roots.ng_jobs, roots.mozaik_shared, roots.control, roots.app_data, roots.tracker):
        folder.mkdir(parents=True)
    store = open_store(roots.app_data / "ngrunner.db")
    clock = FakeClock(T0)
    return dict(store=store, roots=roots, interlock=lambda paths: None, clock=clock, tmp=tmp_path)


def folder_of(env, name: str, *, minutes: float = 0, **kw) -> Path:
    taken = env["clock"].now() + timedelta(minutes=minutes)
    return snapshot(env["tmp"] / name, taken=taken, **kw)


def snap_of(env, name: str, *, minutes: float = 0, **kw):
    return read_tracker(folder_of(env, name, minutes=minutes, **kw))


def drop(env, rows=APPEND, *, kind="lock_design", verb="append", snap=None, material=None):
    snap = snap if snap is not None else snap_of(env, "before", minutes=-5)
    return drop_batch(JOB, rows, kind=kind, verb=verb, material=material, snap=snap, store=env["store"],
                      roots=env["roots"], interlock=env["interlock"], clock=env["clock"])


def archive(env, batch) -> Path:
    """What the idempotent ingest does: the batch moves to ingested_<its name>."""
    folder = env["roots"].tracker
    target = folder / f"ingested_{batch.file}"
    os.replace(folder / batch.file, target)
    stamp = env["clock"].now().timestamp()
    os.utime(target, (stamp, stamp))          # archived now, by the test's clock
    return target


# ----- before a drop


def test_a_clean_append_batch_has_nothing_against_it(env) -> None:
    assert before_drop(JOB, APPEND, verb="append", snap=snap_of(env, "s", minutes=-5), now=T0,
                       open_batches=0, waiting_files=()) == ()


def problems(env, rows=APPEND, *, verb="append", minutes=-5, open_batches=0, waiting=(), **kw):
    snap = snap_of(env, f"s{len(list(env['tmp'].iterdir()))}", minutes=minutes, **kw)
    return [p.code for p in before_drop(JOB, rows, verb=verb, snap=snap, now=env["clock"].now(),
                                        open_batches=open_batches, waiting_files=waiting)]


def test_no_drop_while_a_batch_is_in_flight(env) -> None:
    assert problems(env, open_batches=1) == ["in_flight"]
    assert problems(env, waiting=("tracker_inbox_someone.csv",)) == ["in_flight"]


def test_no_drop_on_a_snapshot_20_minutes_old(env) -> None:
    assert problems(env, minutes=-20.5) == ["snapshot_old"]
    assert problems(env, minutes=-19.5) == []
    assert SNAPSHOT_MAX_AGE == timedelta(minutes=20)


def test_each_material_must_match_pricing_exactly_and_have_a_price(env) -> None:
    assert problems(env, prices=[[WHITE + " ", "$40.00"], [BACKS, "$65.00"]]) == ["not_in_pricing"]
    assert problems(env, prices=[[WHITE, ""], [BACKS, "$65.00"]]) == ["no_price"]
    assert problems(env, prices=[[WHITE, "$40.00"], [WHITE, "$41.00"], [BACKS, "$65.00"]]) == ["priced_twice"]
    assert problems(env, tape_prices=[["Tafisa 1mm — 15/16\" / 1\"", "$0.27"]]) == ["not_in_pricing"]


def test_an_append_needs_no_row_yet_and_a_set_exactly_one(env) -> None:
    assert problems(env, sheets=[(JOB, WHITE, "8.2", "", "40.00")]) == ["row_there"]
    assert problems(env, jobs=[(JOB, IN_PROGRESS)]) == ["row_there"]
    assert problems(env, SET_WHITE, verb="set") == ["no_row"]
    assert problems(env, SET_WHITE, verb="set", sheets=[(JOB, WHITE, "8.2", "", "40.00")] * 2) == ["rows_twice"]
    assert problems(env, SET_WHITE, verb="set", sheets=[(JOB, WHITE, "8.2", "", "40.00")]) == []


def test_status_strings_are_exactly_jims_two(env) -> None:
    assert (IN_PROGRESS, LOCKED) == ("🔲 In progress", "🔒 LOCKED")
    for status in ("In progress", "✅ Complete", "🔲  In progress", "LOCKED"):
        rows = [*APPEND[:-1], ["JOBSUMMARY", JOB, "", status, ""]]
        assert problems(env, rows) == ["status_not_jims"], status


def test_a_batch_for_another_job_or_out_of_shape_is_refused(env) -> None:
    assert problems(env, [*APPEND, ["JOBSUMMARY", OTHER, "", IN_PROGRESS, ""]]) == ["other_job"]
    assert problems(env, APPEND[1:]) == ["not_a_batch"]


# ----- the drop


def test_a_drop_writes_one_new_batch_file_in_the_folder_given_and_records_it(env) -> None:
    result = drop(env)

    assert result.ok, result.reason
    files = list(env["roots"].tracker.iterdir())
    assert [f.name for f in files] == [result.batch.file]
    assert files[0].read_bytes().startswith(b"TAB,JOB,FIELD1")
    assert get_batch(env["store"], result.batch.id).state == "dropped"


def test_a_drop_with_a_problem_writes_nothing(env) -> None:
    result = drop(env, snap=snap_of(env, "old", minutes=-30))

    assert not result.ok and [p.code for p in result.problems] == ["snapshot_old"]
    assert list(env["roots"].tracker.iterdir()) == []


def test_the_same_rows_are_never_dropped_twice(env) -> None:
    first = drop(env)
    archive(env, first.batch)
    env["clock"].advance(timedelta(hours=1))

    again = drop(env, snap=snap_of(env, "later", minutes=-1))

    assert not again.ok and "never drops" in again.reason
    assert len(list_batches(env["store"])) == 1


def test_a_second_batch_waits_while_the_first_is_in_flight(env) -> None:
    drop(env)
    other = [list(HEADER), ["JOBSUMMARY", JOB, "", IN_PROGRESS, "x"]]

    result = drop(env, other)

    assert not result.ok and [p.code for p in result.problems] == ["in_flight"]


# ----- after a drop


def after(env, batch, folder: Path):
    return after_drop(get_batch(env["store"], batch.id), store=env["store"], roots=env["roots"],
                      snapshots=folder, now=env["clock"].now())


def test_it_waits_for_the_archive_then_for_a_snapshot_after_it(env) -> None:
    batch = drop(env).batch
    assert after(env, batch, folder_of(env, "a", **landed_rows())).state == "dropped"

    archive(env, batch)
    early = folder_of(env, "b", minutes=-1, **landed_rows())
    assert after(env, batch, early).state == "archived"


def test_confirmed_only_when_a_second_snapshot_10_minutes_later_agrees(env) -> None:
    batch = drop(env).batch
    archive(env, batch)
    env["clock"].advance(timedelta(minutes=2))

    first = after(env, batch, folder_of(env, "a", **landed_rows()))
    assert first.state == "first_ok"
    env["clock"].advance(CONFIRM_AFTER - timedelta(minutes=1))
    too_soon = after(env, batch, folder_of(env, "b", **landed_rows()))
    assert too_soon.state == "first_ok"
    env["clock"].advance(timedelta(minutes=1))
    confirmed = after(env, batch, folder_of(env, "c", **landed_rows()))
    assert confirmed.state == "confirmed"
    assert get_batch(env["store"], batch.id).confirmed_at == env["clock"].now()


def test_a_missing_row_is_doubt_and_never_dropped_again_by_itself(env) -> None:
    batch = drop(env).batch
    archive(env, batch)
    env["clock"].advance(timedelta(minutes=2))
    rows = landed_rows()
    rows["sheets"] = rows["sheets"][:1]                     # the 1/4 backs never landed

    result = after(env, batch, folder_of(env, "a", **rows))

    assert result.state == "doubt" and result.missing == ("Egger Classic White (1/4) 5x9",)
    assert result.can_resend
    assert [f.name for f in env["roots"].tracker.iterdir()] == [f"ingested_{batch.file}"]


def test_a_row_that_differs_is_doubt_with_no_resend(env) -> None:
    batch = drop(env).batch
    archive(env, batch)
    env["clock"].advance(timedelta(minutes=2))
    rows = landed_rows()
    rows["sheets"][0] = (JOB, WHITE, "9.9", "", "40.00")

    result = after(env, batch, folder_of(env, "a", **rows))

    assert result.state == "doubt" and not result.can_resend


def test_two_totals_rows_or_a_broken_equality_is_doubt(env) -> None:
    for name, broken in (("a", dict(extra_totals=True)), ("b", dict(total_off=5.0))):
        batch = drop(env, [*APPEND[:-1], ["JOBSUMMARY", JOB, "", IN_PROGRESS, name]]).batch
        archive(env, batch)
        env["clock"].advance(timedelta(minutes=2))
        result = after(env, batch, folder_of(env, name, **landed_rows(), **broken))
        assert result.state == "doubt", name


def test_a_nested_row_at_0_dollars_is_doubt(env) -> None:
    batch = drop(env, SET_WHITE, kind="finish_lock", verb="set", material=WHITE,
                 snap=snap_of(env, "pre", minutes=-5, sheets=[(JOB, WHITE, "8.2", "", "40.00")])).batch
    archive(env, batch)
    env["clock"].advance(timedelta(minutes=2))

    result = after(env, batch, folder_of(env, "a", sheets=[(JOB, WHITE, "8.2", "9", "0")],
                                       prices=[[WHITE, "$0.00"], [BACKS, "$65.00"]]))

    assert result.state == "doubt"


def test_a_batch_the_ingest_set_aside_is_doubt(env) -> None:
    batch = drop(env).batch
    folder = env["roots"].tracker
    os.replace(folder / batch.file, folder / f"DUPLICATE_{batch.file}")

    assert after(env, batch, folder_of(env, "a", **landed_rows())).state == "doubt"


# ----- re-send only the missing row


def test_only_the_missing_row_is_resent_after_a_fresh_snapshot_proves_it_absent(env) -> None:
    batch = drop(env).batch
    archive(env, batch)
    env["clock"].advance(timedelta(minutes=2))
    rows = landed_rows()
    rows["sheets"] = rows["sheets"][:1]
    after(env, batch, folder_of(env, "a", **rows))
    pressed = env["clock"].advance(timedelta(minutes=5))

    stale = resend_missing(get_batch(env["store"], batch.id), fresh=snap_of(env, "b", minutes=-1, **rows),
                           pressed_at=pressed, store=env["store"], roots=env["roots"],
                           interlock=env["interlock"], clock=env["clock"])
    assert not stale.ok and "fresh snapshot" in stale.reason

    env["clock"].advance(timedelta(minutes=1))
    result = resend_missing(get_batch(env["store"], batch.id), fresh=snap_of(env, "c", **rows),
                            pressed_at=pressed, store=env["store"], roots=env["roots"],
                            interlock=env["interlock"], clock=env["clock"])

    assert result.ok, result.reason
    assert result.batch.rows == [list(HEADER), ["SHEETGOODS", JOB, "", BACKS, "0.6652", "7.16", "0.22", ""]]
    assert get_batch(env["store"], batch.id).state == "resent"


def test_nothing_is_resent_when_the_fresh_snapshot_shows_the_row(env) -> None:
    batch = drop(env).batch
    archive(env, batch)
    env["clock"].advance(timedelta(minutes=2))
    rows = landed_rows()
    rows["sheets"] = rows["sheets"][:1]
    after(env, batch, folder_of(env, "a", **rows))
    pressed = env["clock"].advance(timedelta(minutes=5))
    env["clock"].advance(timedelta(minutes=1))

    result = resend_missing(get_batch(env["store"], batch.id), fresh=snap_of(env, "b", **landed_rows()),
                            pressed_at=pressed, store=env["store"], roots=env["roots"],
                            interlock=env["interlock"], clock=env["clock"])

    assert not result.ok and "nothing is missing" in result.reason
    assert len(list_batches(env["store"])) == 1


# ----- the totals


def test_the_four_equalities_hold_on_agreeing_totals_and_fail_on_one_off(env) -> None:
    good = read_totals(snapshot(env["tmp"] / "t1", taken=T0, **landed_rows(optimizer="9")))
    off = read_totals(snapshot(env["tmp"] / "t2", taken=T0, **landed_rows(optimizer="9"), total_off=0.02))
    twice = read_totals(snapshot(env["tmp"] / "t3", taken=T0, **landed_rows(), extra_totals=True))

    assert four_equalities(good) == ()
    assert four_equalities(off) != ()
    assert four_equalities(twice) != ()


@pytest.mark.fixtures
def test_the_real_snapshot_reads_and_its_four_equalities_hold() -> None:
    from conftest import FIXTURES
    folder = FIXTURES / "p2" / "tracker" / "snapshot"
    if not (folder / "tracker_snapshot_SheetGoods.csv").is_file():
        pytest.skip("the real tracker snapshot isn't on this PC (.local-fixtures/p2/tracker)")

    assert four_equalities(read_totals(folder)) == ()


@pytest.mark.fixtures
def test_before_a_drop_on_the_real_snapshot_its_pricing_is_read_exactly() -> None:
    """The real Pricing, with a made-up job: a key spelled twice (one with a trailing space, at two
    prices) and a key with no price both stop a drop; exact keys with prices don't."""
    from conftest import FIXTURES
    folder = FIXTURES / "p2" / "tracker" / "snapshot"
    if not (folder / "tracker_snapshot_SheetGoods.csv").is_file():
        pytest.skip("the real tracker snapshot isn't on this PC (.local-fixtures/p2/tracker)")
    snap = read_tracker(folder)
    now = snap.taken + timedelta(minutes=5)

    def codes(material: str) -> list[str]:
        rows = [list(HEADER), ["SHEETGOODS", JOB, "", material, "1.0", "10.0", "0.31", ""],
                ["EDGEBAND", JOB, "", TAPE, "12.0", "", WHITE], ["JOBSUMMARY", JOB, "", IN_PROGRESS, ""]]
        return [p.code for p in before_drop(JOB, rows, verb="append", snap=snap, now=now, open_batches=0,
                                            waiting_files=())]

    assert codes(WHITE) == []
    assert codes(BACKS) == ["priced_twice"]
    assert codes("Tafisa Apris Ski (1/4) 4x8") == ["no_price"]
    assert codes("Egger Classic White (3/4)") == ["not_in_pricing"]


def test_every_problem_before_a_drop_reads_plainly_for_the_board(env) -> None:
    from ngrunner.messages.lint import check_line
    snaps = [snap_of(env, "w1", minutes=-5, tape_prices=[["Tafisa 1mm — 15/16\" / 1\"", "$0.27"]]),
             snap_of(env, "w2", minutes=-5, prices=[[WHITE, ""], [BACKS, "$65.00"], [BACKS, "$60.00"]],
                     tape_prices=[[TAPE, ""]]),
             snap_of(env, "w3", minutes=-5, **landed_rows()),
             snap_of(env, "w4", minutes=-30)]
    found = [p for snap in snaps for rows, verb in ((APPEND, "append"), (SET_WHITE, "set"))
             for p in before_drop(JOB, rows, verb=verb, snap=snap, now=env["clock"].now(), open_batches=1,
                                  waiting_files=())]
    assert {p.code for p in found} >= {"in_flight", "not_in_pricing", "no_price", "priced_twice", "row_there",
                                       "no_row", "snapshot_old"}
    for problem in found:
        assert check_line(problem.words) == [], problem.words


def test_rows_left_in_doubt_are_never_dropped_again_even_with_nothing_in_flight(env) -> None:
    batch = drop(env).batch
    archive(env, batch)
    env["clock"].advance(timedelta(minutes=2))
    rows = landed_rows()
    rows["sheets"][0] = (JOB, WHITE, "9.9", "", "40.00")
    assert after(env, batch, folder_of(env, "a", **rows)).state == "doubt"
    assert list_batches(env["store"], open_only=True) == []          # nothing in flight now
    env["clock"].advance(timedelta(hours=1))

    again = drop(env, snap=snap_of(env, "later", minutes=-1))

    assert not again.ok and "never drops" in again.reason
    assert [f.name for f in env["roots"].tracker.iterdir()] == [f"ingested_{batch.file}"]
