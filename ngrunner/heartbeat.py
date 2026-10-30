"""
The heartbeat: a signed file every 5 minutes that says NG Runner is alive, and what is at risk.

Silence is never success (design section 1): the Apps Script's watchdog alerts Sulaiman when the
newest heartbeat is 45 minutes old during the hours the PC should be on, and when no digest has
gone by 7:00 AM it tells Jim there is no list today, listing the open risks the last heartbeat
named ("Not checked: ..."). So each risk is one line a person may read, held to the message
lint; one that fails is counted, never shown. The file goes to the control folder's
heartbeat/<Pacific YYYYMMDD>/<seq>.json through commit_write (wave 2).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from ngrunner.clock import pacific_day, to_iso
from ngrunner.messages.lint import check_line
from ngrunner.signing import new_id, seal

KIND = "heartbeat"
FOLDER = "heartbeat"     # in the control folder


def payload(*, app_version: str, paused: bool, open_risks: Sequence[str],
            last_fab_seq: int | None, list_synced_at: datetime | None, pending: int,
            dev_open: int) -> dict[str, Any]:
    """The heartbeat's payload (build spec 6.5). open_risks are plain lines (nests posted but
    unchecked, fixes not yet verified, a filing in flight); last_fab_seq is the last Fab export
    accepted, list_synced_at when Render read the job list, pending the open pending operations,
    dev_open the open DEV items. Unknown values are None, never guessed."""
    if not (isinstance(app_version, str) and app_version.strip()):
        raise ValueError("the app version is missing")
    if not isinstance(paused, bool):
        raise TypeError("paused is true or false")
    if last_fab_seq is not None:
        _count(last_fab_seq, "the last Fab export's number")
    if list_synced_at is not None and (not isinstance(list_synced_at, datetime)
                                       or list_synced_at.utcoffset() is None):
        raise ValueError("the job list's time must be timezone-aware")
    return {
        "app_version": app_version, "paused": paused, "open_risks": _risks(open_risks),
        "last_fab_seq": last_fab_seq,
        "list_synced_at": None if list_synced_at is None else to_iso(list_synced_at),
        "pending": _count(pending, "the pending count"),
        "dev_open": _count(dev_open, "the DEV count"),
    }


def heartbeat_file(beat: dict[str, Any], *, key: bytes, seq: int,
                   now: datetime) -> tuple[tuple[str, ...], bytes]:
    """The signed file for beat, and where it goes under the control folder:
    heartbeat/<YYYYMMDD of now in Pacific time, the Apps Script's own zone>/<seq>.json."""
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
        raise ValueError("a heartbeat's number must be a whole number from 1")
    data = seal(KIND, beat, key=key, seq=seq, issued_at=now, id=new_id())
    return (FOLDER, pacific_day(now).strftime("%Y%m%d"), f"{seq}.json"), data


def _risks(lines: Sequence[str]) -> list[str]:
    if isinstance(lines, str):
        raise TypeError("open risks are a list of lines, not one line")
    risks = list(lines)
    if not all(isinstance(risk, str) for risk in risks):
        raise TypeError("each open risk is a line of text")
    shown = [risk for risk in risks if not check_line(risk)]
    hidden = len(risks) - len(shown)
    if hidden:
        shown.append("1 more open risk is on the board." if hidden == 1 else
                     f"{hidden} more open risks are on the board.")
    return shown


def _count(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{what} must be a whole number from 0")
    return value
