"""The clock: stored times are UTC, shown times are Pacific, and wall-time schedules survive DST."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from ngrunner.clock import (
    FakeClock, at_pacific, fmt_clock, fmt_date, fmt_day, from_iso, next_at, pacific_day,
    parse_wall, to_iso, utc,
)

Z = timezone.utc


def test_a_naive_time_is_refused_everywhere():
    naive = datetime(2026, 10, 30, 6, 30)
    for call in (lambda: utc(naive), lambda: to_iso(naive), lambda: FakeClock(naive)):
        with pytest.raises(ValueError):
            call()
    with pytest.raises(ValueError):
        from_iso("2026-10-30T06:30:00")          # stored text without an offset


def test_stored_times_round_trip_in_utc():
    when = datetime(2026, 10, 29, 21, 42, tzinfo=timezone(timedelta(hours=-7)))
    assert to_iso(when) == "2026-10-30T04:42:00+00:00"
    assert from_iso(to_iso(when)) == when


def test_the_fake_clock_moves_only_when_told():
    clock = FakeClock(datetime(2026, 10, 30, 13, 30, tzinfo=Z))
    assert clock.now() == clock.now()
    assert clock.advance(90) == datetime(2026, 10, 30, 13, 31, 30, tzinfo=Z)
    assert clock.advance(timedelta(hours=1)).hour == 14


def test_times_are_shown_as_jim_reads_them_in_pacific():
    evening = datetime(2026, 10, 30, 4, 42, tzinfo=Z)          # 9:42 PM PDT on 10/29
    assert fmt_clock(evening) == "9:42 PM"
    assert fmt_date(evening) == "10/29" and fmt_day(evening) == "Thu 10/29"
    assert fmt_clock(datetime(2026, 10, 30, 7, 5, tzinfo=Z)) == "12:05 AM"
    assert fmt_clock(datetime(2026, 10, 30, 19, 0, tzinfo=Z)) == "12:00 PM"
    assert fmt_day(date(2026, 11, 3)) == "Tue 11/3"
    assert pacific_day(evening) == date(2026, 10, 29)


def test_a_wall_time_is_placed_with_the_offset_of_its_day():
    assert at_pacific(date(2026, 10, 30), time(6, 30)) == datetime(2026, 10, 30, 13, 30, tzinfo=Z)
    assert at_pacific(date(2026, 11, 2), time(6, 30)) == datetime(2026, 11, 2, 14, 30, tzinfo=Z)


def test_the_repeated_autumn_hour_is_taken_once_at_its_first_occurrence():
    # 2026-11-01: 1:00-1:59 AM happens twice; the night run keyed to 1:30 AM runs once (PDT).
    assert at_pacific(date(2026, 11, 1), time(1, 30)) == datetime(2026, 11, 1, 8, 30, tzinfo=Z)


def test_a_time_skipped_in_spring_moves_forward_by_the_gap():
    assert at_pacific(date(2026, 3, 8), time(2, 30)) == datetime(2026, 3, 8, 10, 30, tzinfo=Z)


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 10, 30, 13, 29, tzinfo=Z), datetime(2026, 10, 30, 13, 30, tzinfo=Z)),
    (datetime(2026, 10, 30, 13, 30, tzinfo=Z), datetime(2026, 10, 31, 13, 30, tzinfo=Z)),
    (datetime(2026, 10, 31, 14, 0, tzinfo=Z), datetime(2026, 11, 1, 14, 30, tzinfo=Z)),  # DST ends
])
def test_next_at_finds_the_next_digest_time_across_dst(now, expected):
    assert next_at(now, time(6, 30)) == expected


@pytest.mark.parametrize("text, expected", [("06:30", time(6, 30)), ("6:30", time(6, 30)),
                                            ("22:00", time(22, 0))])
def test_wall_times_from_settings(text, expected):
    assert parse_wall(text) == expected


@pytest.mark.parametrize("text", ["24:00", "06:3", "6.30", "", "x", "06:30 AM"])
def test_anything_but_a_24_hour_wall_time_is_refused(text):
    with pytest.raises(ValueError):
        parse_wall(text)
