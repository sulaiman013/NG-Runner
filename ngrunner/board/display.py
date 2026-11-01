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


NESTS = {
    "do_not_cut": Style("bad", "x", "DO NOT CUT"),
    "not_checked": Style("warn", "alert", "NOT CHECKED"),
    "save_nest": Style("warn", "file", "Save the nest"),
    "nest_checked": Style("ok", "check", "Nest checked"),
    "not_posted": Style("muted", "minus", "Not posted"),
}
_FROM_BEFORE = Style("muted", "minus", "Not checked")

# NG Runner's takeoff of a filed job (views.TakeoffView). An estimate, never a pass: info, not ok.
TAKEOFFS = {
    "made": Style("info", "file", "NG Runner's takeoff"),
    "chat": Style("info", "file", "Chat's takeoff"),
    "unverified": Style("warn", "alert", "Could not verify"),
    "in_chat": Style("muted", "minus", "Takeoff in chat"),
    "failed": Style("muted", "minus", "No takeoff"),
}

# A job's check (views.CheckView, P3). Never "OK": a pass is "Checked", and only while it stands.
CHECKS = {
    "none": Style("muted", "minus", "Not checked yet"),
    "asked": Style("info", "clock", "Check asked"),
    "running": Style("info", "clock", "Checking"),
    "waiting": Style("info", "clock", "Waiting for Mozaik"),
    "passed": Style("ok", "check", "Checked"),
    "failed": Style("bad", "x", "To fix"),
    "could_not_verify": Style("warn", "alert", "Could not verify"),
    "stale": Style("warn", "alert", "Changed since checked"),
    "not_run": Style("muted", "minus", "Not checked"),
}
CHECK_ITEMS = {
    "fix": Style("bad", "x", "To fix"),
    "mozaik": Style("bad", "x", "Fix in Mozaik"),
    "could_not_verify": Style("warn", "alert", "Could not verify"),
    "advisory": Style("info", "info", "Advisory"),
}
_SETTLED = Style("muted", "check", "Settled")

# P4's lock panel (views.LockPanelView). Never "Locked" for a batch the snapshots haven't confirmed.
LOCK_BUTTONS = {
    "ready": Style("ok", "lock", "Ready"),
    "waivable": Style("warn", "alert", "Needs its check"),
    "blocked": Style("bad", "x", "Not yet"),
    "asked": Style("info", "clock", "Asked"),
}
BATCHES = {
    "dropped": Style("info", "clock", "Dropped"),
    "archived": Style("info", "clock", "Taken by the tracker"),
    "first_ok": Style("info", "clock", "Landed, confirming"),
    "confirmed": Style("ok", "check", "Confirmed"),
    "doubt": Style("bad", "alert", "In doubt"),
    "resent": Style("muted", "refresh", "Re-sent"),
}
FIXES = {
    "waiting": Style("info", "clock", "Waiting to write"),
    "applied": Style("ok", "check", "Fix written"),
    "undone": Style("muted", "minus", "Undone"),
    "held": Style("ok", "check", "Held after the save"),
    "mozaik_undid": Style("bad", "x", "Mozaik undid"),
    "you_changed": Style("muted", "info", "You changed it"),
    "could_not_verify": Style("warn", "alert", "Could not verify"),
}
LEASES = {
    "active": Style("info", "lock", "Lease held"),
    "lapsed": Style("muted", "minus", "Lease lapsed"),
    "released": Style("muted", "minus", "Lease released"),
}
JOB_SHEETS = {
    "matches": Style("ok", "check", "Job sheet matches"),
    "mismatch": Style("bad", "x", "Job sheet differs"),
    "could_not_verify": Style("warn", "alert", "Job sheet not verified"),
}


def check_item_style(kind: str, settled: str) -> Style:
    """An item of a check: settled ones are no longer to fix."""
    return _SETTLED if settled else CHECK_ITEMS[kind]


def nest_style(state: str, posted: datetime | None) -> Style:
    """A material's state. NOT CHECKED on what was there before NG Runner is no warning: nothing
    new was posted, and today every old job reads so."""
    return _FROM_BEFORE if state == "not_checked" and posted is None else NESTS[state]


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


def mode_style(shadow: bool) -> Style:
    return Style("warn", "eye", "SHADOW") if shadow else Style("ok", "check", "Live")


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
