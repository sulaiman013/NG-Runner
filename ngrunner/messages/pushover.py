"""
The Pushover client: the one path to Jim's phone that doesn't go through the Apps Script.

Normally every message leaves through the outbox and the Apps Script. When receipts stop (none
within 5 minutes) and the keys are in Credential Manager, urgent items come here instead (design
section 4, "Silence and failure"), so a Google outage can't silence them. send() never raises
and never logs: a failure is just False, for the caller to retry or alarm on, and the app token
and user key travel only in the request body, never in an address, a log line or an exception.
"""
from __future__ import annotations

import re
from typing import Any, Protocol

API_URL = "https://api.pushover.net/1/messages.json"
TITLE_LIMIT = 250      # Pushover's limits, in characters
TEXT_LIMIT = 1024
TIMEOUT_S = 15.0
_CREDENTIAL = re.compile(r"[A-Za-z0-9]{30}")   # app tokens and user keys are 30 of these


class Http(Protocol):
    """What send() needs from requests.Session."""

    def post(self, url: str, *, data: dict[str, str], timeout: float) -> Any: ...


def send(token: str, user: str, title: str, text: str, *, urgent: bool, http: Http,
         url: str = API_URL, timeout: float = TIMEOUT_S) -> bool:
    """True only when Pushover took the message. urgent sends at high priority, which shows
    through the phone's own quiet hours; NG Runner's quiet hours are the caller's to keep. A
    text over Pushover's limit keeps its first whole lines and says how many more are on the
    board. url is for tests (a fake server on 127.0.0.1)."""
    if not (isinstance(token, str) and _CREDENTIAL.fullmatch(token)
            and isinstance(user, str) and _CREDENTIAL.fullmatch(user)):
        return False
    message = _fit(text, TEXT_LIMIT)
    if not message.strip():
        return False
    form = {"token": token, "user": user, "title": title[:TITLE_LIMIT], "message": message,
            "priority": "1" if urgent else "0"}
    try:
        reply = http.post(url, data=form, timeout=timeout)
        answer = reply.json()
        return reply.status_code == 200 and isinstance(answer, dict) and answer.get("status") == 1
    except Exception:  # noqa: BLE001 - any failure is "not sent"; the token goes no further
        return False


def _fit(text: str, limit: int) -> str:
    """text within limit: whole lines from the top, then how many more are on the board."""
    if len(text) <= limit:
        return text
    lines = text.split("\n")
    for keep in range(len(lines) - 1, 0, -1):
        left = len(lines) - keep
        note = f"{left} more line is on the board." if left == 1 else (
            f"{left} more lines are on the board.")
        fitted = "\n".join([*lines[:keep], note])
        if len(fitted) <= limit:
            return fitted
    return text[: limit - 1] + "…"
