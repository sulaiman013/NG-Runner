"""
The tracker's names for a lock's rows (lock/names.py): what the batch calls each board and tape, taken from
the chat's own IC Conversion, which is what the chat types the tracker's rows from today, and never a
guess. A thickness that two colours share, a tape the conversion doesn't name for its board, or a
conversion that names none at all gives no name, and the lock says so. Job names and materials are made up.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from ngrunner.lock.names import NO_NAMES, nest_thickness, takeoff_facts, tracker_names, tracker_name_of_nest
from ngrunner.tracker_inbox import TrackerNames

WHITE, TAPE = "Egger Classic White", 'Egger Classic White 1mm — 15/16" / 1"'
BACKS = "White 1/4 Vinyl"


def conversion(buckets=((WHITE, 0.75), (BACKS, 0.25)), tapes=(TAPE,)) -> bytes:
    return json.dumps({
        "buckets": [{"material": m, "thk_in": t, "calc_sheets_raw": 1.0, "edgeband_lf": 1.0} for m, t in buckets],
        "edgeband": {"rows": [{"material": tape, "linear_feet": 10.0} for tape in tapes]}}).encode("utf-8")


def takeoff(*buckets) -> SimpleNamespace:
    return takeoff_facts({"buckets": [{"colour": c, "thk_in": t, "sqft": 10.0, "calc_sheets_32": 0.3,
                                       "tape_lf": lf} for c, t, lf in buckets], "unplaced": []})


TAKEOFF = takeoff(("WH", 0.75, 26.0), ("WH", 0.25, 0.0))


def test_each_board_takes_the_conversions_name_for_its_thickness_and_its_tape() -> None:
    names = tracker_names(conversion(), TAKEOFF)

    assert names("WH", 0.75) == TrackerNames(WHITE, TAPE, WHITE)
    assert names("WH", 0.25) == TrackerNames(BACKS, "", "")


def test_a_thickness_two_colours_share_has_no_name_on_either_side() -> None:
    two_here = takeoff(("WH", 0.75, 26.0), ("AP", 0.75, 4.0))
    assert tracker_names(conversion(), two_here)("WH", 0.75) is None
    two_there = conversion(buckets=((WHITE, 0.75), ("Tafisa Spring Blossom_Ore", 0.75)))
    assert tracker_names(two_there, TAKEOFF)("WH", 0.75) is None


def test_a_board_with_tape_and_no_tape_of_its_own_colour_in_the_conversion_has_no_name() -> None:
    names = tracker_names(conversion(tapes=('Tafisa 1mm — 15/16" / 1"',)), TAKEOFF)
    assert names("WH", 0.75) is None
    assert names("WH", 0.25) == TrackerNames(BACKS, "", "")          # it takes no tape


def test_no_conversion_or_one_that_names_no_board_names_nothing() -> None:
    for data in (None, b"not json", json.dumps({"buckets": None}).encode(), json.dumps([1]).encode()):
        assert tracker_names(data, TAKEOFF)("WH", 0.75) is None


def test_a_nests_thickness_comes_from_its_gauge_and_its_row_from_the_one_board_of_it() -> None:
    names = tracker_names(conversion(), TAKEOFF)

    assert nest_thickness("3-4 Egger Classic White") == 0.75
    assert nest_thickness("1-4 Egger Classic White") == 0.25
    assert nest_thickness("1 in Tafisa Southwester") == 1.0
    assert nest_thickness("Egger Classic White") is None
    assert tracker_name_of_nest("3-4 Egger Classic White", TAKEOFF, names) == WHITE
    assert tracker_name_of_nest("1-4 Egger Classic White", TAKEOFF, names) == BACKS
    assert tracker_name_of_nest("1 in Egger Classic White", TAKEOFF, names) is None


def test_the_takeoff_reads_as_the_inbox_reads_it() -> None:
    made = takeoff_facts({"buckets": [{"colour": "WH", "thk_in": 0.75, "sqft": 1.5, "calc_sheets_32": 0.05,
                                       "tape_lf": 3.0, "parts": 2}], "unplaced": [{"page": 4, "qty": 1}]})
    bucket, = made.buckets
    assert (bucket.colour, bucket.thk_in, bucket.sqft, bucket.calc_sheets_32, bucket.tape_lf) == (
        "WH", 0.75, 1.5, 0.05, 3.0)
    assert len(made.unplaced) == 1
    assert "IC Conversion" in NO_NAMES
