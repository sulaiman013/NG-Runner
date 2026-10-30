"""
The state table: NG Runner's own small facts that belong to no job or row (build spec
section 4), such as whether it is paused, when it was installed, when the last digest went out
and what the Windows toast setting read last.

Values are text, set and read whole. Times go through set_state_time and get_state_time, so they
are stored as to_iso() text like every other time in the store. Keys are short lowercase names
("paused", "installed_at", "last_digest_day"), so a typo is refused instead of reading as unset.
"""
from __future__ import annotations

import re
from datetime import datetime

from ngrunner.store.db import Db, iso, session, text_value, time_of

_KEY = re.compile(r"[a-z][a-z0-9_.]{0,63}")


def get_state(db: Db, key: str, default: str | None = None) -> str | None:
    """The value stored under key, or default when there is none."""
    with session(db, write=False) as conn:
        row = conn.execute("SELECT value FROM state WHERE key = ?", (_key(key),)).fetchone()
    return default if row is None else row["value"]


def set_state(db: Db, key: str, value: str) -> None:
    """Store value under key, replacing what was there."""
    value = text_value(value, "a state value", empty=True)
    with session(db, write=True) as conn:
        conn.execute(
            "INSERT INTO state (key, value) VALUES (?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (_key(key), value),
        )


def clear_state(db: Db, key: str) -> None:
    """Forget key (nothing happens when it is not set)."""
    with session(db, write=True) as conn:
        conn.execute("DELETE FROM state WHERE key = ?", (_key(key),))


def get_state_time(db: Db, key: str) -> datetime | None:
    """The time stored under key (UTC), or None. A value there that is not a stored time is
    refused (ValueError), never guessed."""
    value = get_state(db, key)
    return None if value is None else time_of(value)


def set_state_time(db: Db, key: str, when: datetime) -> None:
    """Store a time under key, as to_iso() text; a naive time is refused (ValueError)."""
    set_state(db, key, iso(when, key))


def _key(key: object) -> str:
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValueError("a state key is a short lowercase name, like paused or installed_at")
    return key
