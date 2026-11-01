"""
The tracker's names for a lock's rows (lock/names.py). NG Runner's own complete takeoff is the first source,
each bucket named by RULES' names; only when it isn't complete do the rows come from the chat's IC
Conversion, whole, under its own names. Never a mix. Whichever source, every name must be in the tracker's
Pricing block exactly, once, with a price, or the lock is refused saying which name. Job names are made up;
materials and prices are in the tracker's own forms.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from ngrunner.guards.tracker import PriceKey, TrackerSnapshot
from ngrunner.lock.names import (
    CHATS, NO_TAKEOFF, OURS, LockNames, lock_names, nest_thickness, takeoff_facts, tracker_name_of_nest,
)
from ngrunner.tracker_inbox import TrackerNames

T0 = datetime(2026, 10, 31, 13, 0, tzinfo=UTC)
WHITE, TAPE = "Egger Classic White", 'Egger Classic White 1mm — 15/16" / 1"'
BACKS = "Egger Classic White (1/4) 5x9"
ORE, TAFISA_TAPE = "Tafisa Spring Blossom_Ore (3/4)", 'Tafisa 1mm — 15/16" / 1"'
SHEET_PRICES = {WHITE: 40.0, BACKS: 65.0, ORE: 67.0, "Am Lam Wh (3/4)": 53.0, "Am Lam Wh (1 in)": 66.0,
                "White 1/4 Vinyl": 46.0}
TAPE_PRICES = {TAPE: 0.27, TAFISA_TAPE: 0.27, 'Am Lam Wh 1mm — 15/16" / 1"': 0.27,
               'Am Lam Wh 1mm — 1-1/4" / 1-5/16"': 0.39}


def snap(sheets=None, tape=None) -> TrackerSnapshot:
    sheets = SHEET_PRICES if sheets is None else sheets
    tape = TAPE_PRICES if tape is None else tape
    return TrackerSnapshot(T0, None, (), (), (), tuple(PriceKey(m, p) for m, p in sheets.items()),
                           tuple(PriceKey(m, p) for m, p in tape.items()))


def record(*buckets, complete=True, state="made", unplaced=()) -> SimpleNamespace:
    """NG Runner's takeoff as the store keeps it."""
    result = {"buckets": [{"colour": c, "thk_in": t, "sqft": 10.0, "calc_sheets_32": 0.3, "tape_lf": lf}
                          for c, t, lf in buckets], "unplaced": list(unplaced)}
    return SimpleNamespace(state=state, complete=complete, result=result)


def conversion(buckets=((WHITE, 0.75, 26.0), (BACKS, 0.25, 0.0)), tapes=(TAPE,)) -> bytes:
    return json.dumps({
        "buckets": [{"material": m, "thk_in": t, "sqft": 10.0, "calc_sheets_raw": 0.3125, "edgeband_lf": lf}
                    for m, t, lf in buckets],
        "edgeband": {"rows": [{"material": tape, "linear_feet": 26.0} for tape in tapes]}}).encode("utf-8")


CLASSIC = record(("WH", 0.75, 26.0), ("WH", 0.25, 0.0))


# ----- NG Runner's own takeoff, by RULES' names


def test_a_complete_takeoff_of_ng_runners_is_named_by_rules_and_held_to_pricing() -> None:
    made = lock_names(CLASSIC, None, snap())

    assert isinstance(made, LockNames) and made.source == OURS
    assert made.names("WH", 0.75) == TrackerNames(WHITE, TAPE, WHITE)
    assert made.names("WH", 0.25) == TrackerNames(BACKS, "", "")
    assert made.names("LG", 0.75) is None
    assert [b.colour for b in made.takeoff.buckets] == ["WH", "WH"]


def test_a_tafisa_colour_takes_its_tafisa_name_and_the_one_tafisa_tape() -> None:
    made = lock_names(record(("LG", 0.75, 40.0)), None, snap())
    assert made.names("LG", 0.75) == TrackerNames(ORE, TAFISA_TAPE, "Spring Blossom (Ore)")


def test_a_job_with_thick_white_is_all_amwh() -> None:
    made = lock_names(record(("WH", 0.75, 30.0), ("WH", 1.0, 12.0), ("WH", 0.25, 0.0)), None, snap())
    assert [made.names("WH", t).sheet for t in (0.75, 1.0, 0.25)] == ["Am Lam Wh (3/4)", "Am Lam Wh (1 in)",
                                                                       "White 1/4 Vinyl"]


def test_a_name_not_in_pricing_exactly_refuses_the_lock_and_says_which() -> None:
    assert lock_names(CLASSIC, None, snap(sheets={WHITE + " ": 40.0, BACKS: 65.0})) == (
        "Egger Classic White isn't in the tracker's Pricing exactly as NG Runner names it, so the rows can't be"
        " made")
    assert lock_names(record(("LG", 0.75, 4.0)), None, snap(tape={TAPE: 0.27})) == (
        "Tafisa tape 15/16\" isn't in the tracker's Pricing exactly as NG Runner names it, so the rows can't be"
        " made")
    assert lock_names(CLASSIC, None, snap(sheets={WHITE: None, BACKS: 65.0})) == (
        "Egger Classic White has no price in the tracker's Pricing, so the rows can't be made")


def test_a_name_in_pricing_twice_refuses_the_lock() -> None:
    twice = TrackerSnapshot(T0, None, (), (), (), (PriceKey(WHITE, 40.0), PriceKey(BACKS, 65.0), PriceKey(BACKS + " ", 60.0)),
                            (PriceKey(TAPE, 0.27),))
    assert lock_names(CLASSIC, None, twice) == (
        "Egger Classic White (1/4) 5x9 is in the tracker's Pricing twice, under two spellings, so the rows can't"
        " be made")


def test_an_empty_pricing_block_names_nothing() -> None:
    assert "isn't in the tracker's Pricing" in lock_names(CLASSIC, None, snap(sheets={}, tape={}))


def test_a_colour_rules_gives_no_tafisa_name_refuses_and_never_falls_back_to_the_chats_names() -> None:
    said = lock_names(record(("MO", 0.75, 10.0)), conversion(), snap())
    assert said == "RULES gives IC colour Mocha no Tafisa name, so it has no tracker name, so the rows can't be made"


# ----- the chat's IC Conversion, only when NG Runner's own takeoff isn't complete


def test_an_incomplete_takeoff_takes_the_chats_conversion_whole_under_its_own_names() -> None:
    for incomplete in (record(("WH", 0.75, 26.0), complete=False, unplaced=[{"page": 4, "qty": 1}]),
                       record(state="in_chat", complete=False), None):
        made = lock_names(incomplete, conversion(), snap())
        assert isinstance(made, LockNames) and made.source == CHATS
        assert made.names(WHITE, 0.75) == TrackerNames(WHITE, TAPE, WHITE)
        assert made.names(BACKS, 0.25) == TrackerNames(BACKS, "", "")
        bucket = made.takeoff.buckets[0]
        assert (bucket.colour, bucket.thk_in, bucket.sqft, bucket.calc_sheets_32, bucket.tape_lf) == (
            WHITE, 0.75, 10.0, 0.3125, 26.0)
        assert made.takeoff.unplaced == ()


def test_the_chats_names_are_held_to_pricing_too() -> None:
    chats = conversion(buckets=(("Tafisa Spring Blossom_Ore", 0.75, 40.0),),
                       tapes=('Tafisa Spring Blossom_Ore 1mm — 15/16" / 1"',))
    assert lock_names(None, chats, snap()) == (
        "Tafisa Spring Blossom Ore isn't in the tracker's Pricing exactly as the chat's IC Conversion names it, so"
        " the rows can't be made")


def test_a_chat_conversion_that_is_no_whole_list_or_names_no_tape_of_its_own_gives_nothing() -> None:
    for data in (None, b"not json", json.dumps({"buckets": None}).encode(), json.dumps([1]).encode(),
                 json.dumps({"buckets": []}).encode(), json.dumps({"buckets": [{"material": WHITE}]}).encode()):
        assert lock_names(None, data, snap()) == NO_TAKEOFF
    assert "no one tape of its own for Egger Classic White" in lock_names(None, conversion(tapes=(TAFISA_TAPE,)), snap())
    assert "IC Conversion" in NO_TAKEOFF


# ----- a nest's row


def test_a_nests_thickness_comes_from_its_gauge_and_its_row_from_the_one_board_of_it() -> None:
    made = lock_names(CLASSIC, None, snap())

    assert nest_thickness("3-4 Egger Classic White") == 0.75
    assert nest_thickness("1-4 Egger Classic White") == 0.25
    assert nest_thickness("1 in Tafisa Southwester") == 1.0
    assert nest_thickness("Egger Classic White") is None
    assert tracker_name_of_nest("3-4 Egger Classic White", made.takeoff, made.names) == WHITE
    assert tracker_name_of_nest("1-4 Egger Classic White", made.takeoff, made.names) == BACKS
    assert tracker_name_of_nest("1 in Egger Classic White", made.takeoff, made.names) is None


def test_the_takeoff_reads_as_the_inbox_reads_it() -> None:
    made = takeoff_facts({"buckets": [{"colour": "WH", "thk_in": 0.75, "sqft": 1.5, "calc_sheets_32": 0.05,
                                       "tape_lf": 3.0, "parts": 2}], "unplaced": [{"page": 4, "qty": 1}]})
    bucket, = made.buckets
    assert (bucket.colour, bucket.thk_in, bucket.sqft, bucket.calc_sheets_32, bucket.tape_lf) == (
        "WH", 0.75, 1.5, 0.05, 3.0)
    assert len(made.unplaced) == 1
