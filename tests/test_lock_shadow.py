"""
A shadow batch against the chat's own rows (lock/shadow.py; docs/decisions.md, "P5's reader, and Phases 3
and 4 wired into the app"): nothing ingests NG Runner's test folder, so a shadow batch closes when the chat's
own rows for its job appear in the real tracker's snapshot, read only, as matched or differing, saying which
rows differ in plain words, or as not compared after 7 days with none. Those closings are P4's "5 locks match
the chat", counted by job, and only for batches made from NG Runner's own takeoff. Job names, materials and
numbers are made up.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ngrunner.guards.tracker import read_tracker
from ngrunner.lock import shadow
from ngrunner.lock.drop import IN_PROGRESS, LOCKED
from ngrunner.store.locks import BatchRecord
from ngrunner.tracker_inbox import HEADER
from test_lock_drop import BACKS, TAPE, WHITE, snapshot

T0 = datetime(2026, 10, 31, 13, 0, tzinfo=UTC)
JOB, OTHER = "Testerson_101426", "Sampleton_101526"
DESIGN = [list(HEADER),
          ["SHEETGOODS", JOB, "", WHITE, "24.3778", "262.40", "8.20", ""],
          ["SHEETGOODS", JOB, "", BACKS, "0.6652", "7.16", "0.22", ""],
          ["EDGEBAND", JOB, "", TAPE, "376.2", "", WHITE],
          ["JOBSUMMARY", JOB, "", IN_PROGRESS, ""]]
LOCK = [list(HEADER),
        ["SHEETGOODS", JOB, "", WHITE, "24.3778", "262.40", "8.20", "9"],
        ["EDGEBAND", JOB, "", TAPE, "376.2", "376.2", WHITE],
        ["JOBSUMMARY", JOB, "", LOCKED, ""]]


def batch(rows=DESIGN, *, kind="lock_design", folder=JOB, state="dropped", source="ng_runner",
          dropped_at=T0, shadow_=True, id="b1") -> BatchRecord:
    return BatchRecord(id, folder, kind, "append", None, [list(r) for r in rows], "x" * 64, f"tracker_inbox_{id}.csv",
                       state, dropped_at, None, None, None, None, None, None, shadow_, source, None)


def chat(tmp_path, *, minutes=5, sheets=(), tape=(), jobs=()):
    """The real tracker's snapshot, with the chat's own rows for the job."""
    return read_tracker(snapshot(tmp_path / f"real{minutes}", taken=T0 + timedelta(minutes=minutes),
                                 sheets=sheets, tape=tape, jobs=jobs))


THE_CHATS = dict(sheets=[(JOB, WHITE, "8.2", "", "40.00"), (JOB, BACKS, "0.22", "", "65.00")],
                 tape=[(JOB, TAPE, "376.2", "", "0.27")], jobs=[(JOB, IN_PROGRESS)])


# ----- when it is compared


def test_nothing_is_compared_on_a_snapshot_from_before_the_drop_or_before_the_chats_rows_come(tmp_path) -> None:
    assert shadow.compare(batch(), chat(tmp_path, minutes=-1, **THE_CHATS)) is None
    others = dict(sheets=[(OTHER, WHITE, "3.0", "", "40.00")], jobs=[(OTHER, IN_PROGRESS)])
    assert shadow.compare(batch(), chat(tmp_path, **others)) is None


def test_a_batch_with_nest_numbers_or_locked_waits_for_the_chats_own_lock(tmp_path) -> None:
    touch_one = dict(sheets=[(JOB, WHITE, "8.2", "", "40.00")], tape=[(JOB, TAPE, "376.2", "", "0.27")],
                     jobs=[(JOB, IN_PROGRESS)])
    assert shadow.compare(batch(LOCK, kind="lock"), chat(tmp_path, **touch_one)) is None

    locked = dict(sheets=[(JOB, WHITE, "8.2", "9", "40.00")], tape=[(JOB, TAPE, "376.2", "376.2", "0.27")],
                  jobs=[(JOB, LOCKED)])
    compared = shadow.compare(batch(LOCK, kind="lock"), chat(tmp_path, minutes=6, **locked))
    assert compared is not None and compared.state == shadow.MATCHED


# ----- matched, or differing in plain words


def test_the_chats_rows_as_the_batch_has_them_are_matched(tmp_path) -> None:
    compared = shadow.compare(batch(), chat(tmp_path, **THE_CHATS))

    assert compared == shadow.Compared(shadow.MATCHED, "Matches the chat's own rows on the tracker", ())


def test_a_lock_designs_in_progress_is_not_held_against_a_job_the_chat_has_locked_since(tmp_path) -> None:
    locked = {**THE_CHATS, "jobs": [(JOB, LOCKED)]}
    assert shadow.compare(batch(), chat(tmp_path, **locked)).state == shadow.MATCHED


def test_each_row_that_differs_is_named_with_both_numbers(tmp_path) -> None:
    theirs = dict(sheets=[(JOB, WHITE, "8.3", "", "40.00"), (JOB, "Am Lam Wh (3/4)", "1.0", "", "53.00")],
                  tape=[(JOB, TAPE, "376.2", "", "0.27")], jobs=[(JOB, IN_PROGRESS)])

    compared = shadow.compare(batch(), chat(tmp_path, **theirs))

    assert compared.state == shadow.DIFFERING
    assert compared.lines == (
        "Egger Classic White: Calc Sheets 8.3 on the chat's row, 8.2 on NG Runner's",
        "Egger Classic White (1/4) 5x9: the chat has no row by that name",
        "Am Lam Wh (3/4): the chat has a row NG Runner's batch has not")
    assert compared.words == "Differs from the chat's own rows: " + "; ".join(compared.lines)


def test_a_material_the_chat_has_twice_is_a_difference(tmp_path) -> None:
    twice = {**THE_CHATS, "sheets": [*THE_CHATS["sheets"], (JOB, WHITE, "8.2", "", "40.00")]}
    compared = shadow.compare(batch(), chat(tmp_path, **twice))
    assert compared.state == shadow.DIFFERING
    assert compared.lines == ("Egger Classic White: the chat has 2 rows for it",)


def test_a_finish_lock_compares_only_its_own_material(tmp_path) -> None:
    one = [list(HEADER), ["SHEETGOODS_SET", JOB, WHITE, "24.3778", "262.40", "8.20", "9"]]
    locked = dict(sheets=[(JOB, WHITE, "8.2", "9", "40.00"), (JOB, BACKS, "0.22", "1", "65.00")],
                  jobs=[(JOB, LOCKED)])
    assert shadow.compare(batch(one, kind="finish_lock"), chat(tmp_path, **locked)).state == shadow.MATCHED


def test_long_differences_are_cut_to_three_and_a_count(tmp_path) -> None:
    theirs = dict(sheets=[(JOB, f"Material {n}", "1.0", "", "40.00") for n in range(4)], jobs=[(JOB, IN_PROGRESS)])
    compared = shadow.compare(batch(), chat(tmp_path, **theirs))
    assert len(compared.lines) == 7 and compared.words.endswith("; and 4 more")


# ----- not compared after 7 days, and the tally


def test_a_shadow_batch_with_no_chat_rows_after_7_days_is_given_up(tmp_path) -> None:
    assert shadow.SHADOW_WAITS == timedelta(days=7)
    assert not shadow.gives_up(batch(), T0 + timedelta(days=7))
    assert shadow.gives_up(batch(), T0 + timedelta(days=7, seconds=1))
    assert not shadow.gives_up(batch(shadow_=False), T0 + timedelta(days=30))
    assert not shadow.gives_up(batch(state="matched"), T0 + timedelta(days=30))


def test_the_tally_counts_jobs_from_ng_runners_own_takeoff_against_five() -> None:
    batches = [batch(state="matched", id="a"), batch(LOCK, kind="lock", state="matched", id="b"),
               batch(folder="Sampleton_101526", state="differing", id="c"),
               batch(folder="Sampleton_101526", state="matched", id="d"),
               batch(folder="Exampleby_101626", state="not_compared", id="e"),
               batch(folder="Fakewell_101726", id="f"),
               batch(folder="Chatmade_101826", state="matched", source="chat", id="g"),
               batch(folder="Olden_101926", state="matched", source=None, id="h"),
               batch(folder="Livewell_102026", state="matched", shadow_=False, id="i"),
               batch(folder="Resendy_102126", kind="resend", state="matched", id="j")]

    tally = shadow.tally(batches)

    assert tally == shadow.Tally(matched=1, differing=1, not_compared=1, waiting=1)
    assert shadow.EXIT_LOCKS == 5
    assert shadow.tally_words(tally) == ("1 of 5 jobs' shadow locks match the chat; 1 differs, 1 not compared,"
                                         " 1 waiting for the chat's rows")
    assert shadow.tally_words(shadow.Tally()) == "0 of 5 jobs' shadow locks match the chat"
