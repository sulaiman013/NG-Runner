"""
Time for NG Runner: one injectable clock, times stored in UTC, shown in Pacific time.

Jim's messages are in Pacific time (design section 4), and the night run, the 6:30 AM digest and
the repeated 1 AM hour on the first Sunday of November all depend on reading wall time right.
Code never calls datetime.now() itself: it asks a Clock, so a test can run a whole day in a
second. A naive time is refused, never guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")  # Windows has no IANA data: this needs tzdata


# ----- clocks


class Clock(Protocol):
    def now(self) -> datetime:
        """The current time, timezone-aware, in UTC."""
        ...


class SystemClock:
    """The real clock."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass
class FakeClock:
    """A clock for tests: it moves only when told to."""

    current: datetime

    def __post_init__(self) -> None:
        self.current = utc(self.current)

    def now(self) -> datetime:
        return self.current

    def advance(self, by: timedelta | float) -> datetime:
        """Move forward by a timedelta or a number of seconds; returns the new time."""
        self.current += by if isinstance(by, timedelta) else timedelta(seconds=by)
        return self.current

    def set(self, when: datetime) -> None:
        self.current = utc(when)


# ----- storing times


def utc(when: datetime) -> datetime:
    """when in UTC. A naive time is refused: there is no way to know which zone it meant."""
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("a naive time can't be placed; pass a timezone-aware datetime")
    return when.astimezone(timezone.utc)


def to_iso(when: datetime) -> str:
    """How every time is stored: ISO 8601 in UTC with its offset (2026-10-30T13:30:00+00:00)."""
    return utc(when).isoformat()


def from_iso(text: str) -> datetime:
    """A stored time back, in UTC. Text without an offset is refused, as a naive time is."""
    return utc(datetime.fromisoformat(text))


# ----- showing times to people (always Pacific)


def pacific(when: datetime) -> datetime:
    return utc(when).astimezone(PACIFIC)


def pacific_day(when: datetime) -> date:
    """The calendar day in Pacific time."""
    return pacific(when).date()


def fmt_clock(when: datetime) -> str:
    """Jim's form of a time of day: 9:42 PM."""
    p = pacific(when)
    return f"{p.hour % 12 or 12}:{p.minute:02d} {'AM' if p.hour < 12 else 'PM'}"


def fmt_date(when: datetime | date) -> str:
    """Month/day with no leading zeros: 10/30, 11/3."""
    d = pacific_day(when) if isinstance(when, datetime) else when
    return f"{d.month}/{d.day}"


def fmt_day(when: datetime | date) -> str:
    """Weekday and date: Fri 10/30."""
    d = pacific_day(when) if isinstance(when, datetime) else when
    return f"{d.strftime('%a')} {fmt_date(d)}"


# ----- wall-clock schedules (DST-safe)


def at_pacific(day: date, wall: time) -> datetime:
    """The UTC instant at which Pacific wall time reads wall on day.

    An hour the autumn change repeats (1:00-1:59 AM on the first Sunday of November) is taken
    at its first occurrence, so a run keyed to that time happens once. A time the spring change
    skips (2:00-2:59 AM in March) is moved forward by the gap, as zoneinfo does: 2:30 becomes
    3:30 PDT. NG Runner's own times (1:25, 1:30, 6:30 and 7:00 AM) never fall in that gap.
    """
    return datetime.combine(day, wall, tzinfo=PACIFIC).replace(fold=0).astimezone(timezone.utc)


def next_at(now: datetime, wall: time) -> datetime:
    """The first instant after now at which Pacific wall time reads wall."""
    today = pacific_day(now)
    for offset in range(0, 3):
        candidate = at_pacific(today + timedelta(days=offset), wall)
        if candidate > utc(now):
            return candidate
    raise AssertionError("unreachable: a wall time recurs within two days")


def parse_wall(text: str) -> time:
    """A wall time from settings, "HH:MM" on a 24-hour clock; anything else is refused."""
    hours, sep, minutes = text.partition(":")
    if not (sep and hours.isdigit() and minutes.isdigit() and len(minutes) == 2):
        raise ValueError(f"{text!r} is not a time like 06:30")
    return time(int(hours), int(minutes))
