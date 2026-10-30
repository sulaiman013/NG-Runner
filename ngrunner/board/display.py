"""
How the board words and styles what it shows: Jim's formats and the status vocabulary.

Times are Pacific and read like 3:05 PM, dates like Fri 10/30 (design 4, Format rules), and
every state is shown as icon + label + colour, never colour alone (Assembly Reports'
DESIGN.md, which the board follows). One table per kind of state keeps those three in step.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import NamedTuple

from ngrunner.clock import fmt_clock, fmt_day, pacific_day


class Style(NamedTuple):
    """A state as the board shows it: colour role (ok, warn, bad, info, muted), icon, label."""

    tone: str
    icon: str
    label: str


TICKS = {
    "done": Style("ok", "check", "done"),
    "todo": Style("muted", "circle", "to do"),
    "blocked": Style("bad", "x", "blocked"),
    "na": Style("muted", "minus", "not built yet"),
}

OVERLAYS = {
    "WAITING": Style("info", "clock", "Waiting"),
    "BLOCKED": Style("bad", "x", "Blocked"),
    "NOT_RUN": Style("warn", "alert", "Not run"),
    "HELD": Style("warn", "lock", "Held"),
    "OBSERVED": Style("muted", "eye", "Observed"),
    "PAUSED": Style("muted", "pause", "Paused"),
}


def tray_style(colour: str, count: int) -> Style:
    """The tray's state in words, for the status beside the page's tabs."""
    if colour == "green":
        return Style("ok", "check", "Running")
    if colour == "amber":
        return Style("warn", "alert", f"{count} for you" if count else "Needs you")
    if colour == "red":
        return Style("bad", "alert", "Needs attention")
    return Style("muted", "pause", "Paused")


def toasts_style(enabled: bool | None) -> Style:
    if enabled is None:
        return Style("muted", "minus", "Could not verify")
    return Style("ok", "check", "On") if enabled else Style("bad", "x", "Off")


def path_style(ok: bool | None) -> Style:
    if ok is None:
        return Style("muted", "minus", "Could not check")
    return Style("ok", "check", "Reachable") if ok else Style("bad", "x", "Not reachable")


# ----- words for dates, times and ages


def install_text(install: date | str | None) -> str:
    """A date as Wed 11/4; the job model's own words when it gave words; None is not set."""
    if install is None:
        return "Not set"
    return fmt_day(install) if isinstance(install, date) else install


def age_text(days: int | None) -> str:
    if days is None:
        return ""
    if days <= 0:
        return "today"
    return "1 day" if days == 1 else f"{days} days"


def when_text(when: datetime, now: datetime) -> str:
    """3:05 PM today; Thu 10/29 3:05 PM on any other day."""
    if pacific_day(when) == pacific_day(now):
        return fmt_clock(when)
    return f"{fmt_day(when)} {fmt_clock(when)}"


def ago_text(when: datetime, now: datetime) -> str:
    seconds = (now - when).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return "1 hour ago" if hours == 1 else f"{hours} hours ago"
    days = int(seconds // 86400)
    return "1 day ago" if days == 1 else f"{days} days ago"


def stamp_text(when: datetime | None, now: datetime, never: str) -> str:
    """When something last happened, with its age: 3:05 PM, 2 min ago."""
    if when is None:
        return never
    return f"{when_text(when, now)}, {ago_text(when, now)}"
