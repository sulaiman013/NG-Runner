"""
Messages people see: Jim's digest and cards, Barbara's emails, Sulaiman's DEV and "ran" lines.

Every one is a Message, so one lint (messages.lint) can hold all of them to Jim's format
(design section 4: numbered, one line per item, no intro or sign-off, no paths or hashes) before
anything leaves NG Runner. A Message is plain data: the outbox puts it in a signed file for the
Apps Script to send, and the Pushover client, once its keys are added, sends it directly when
receipts stop.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_KIND = re.compile(r"[a-z][a-z0-9_]{0,39}")


@dataclass(frozen=True)
class Message:
    """One message for a person.

    kind: which template made it ("digest", "filed", ...); a message that fails the lint at
        runtime is replaced by the fixed template for its kind (templates.vetted).
    title: its first line: an email subject, a phone notification's title, a card's header.
    lines: the body, one item per line.
    urgent: sent at once, and straight to Pushover when receipts stop and its keys exist.
    """

    kind: str
    title: str
    lines: tuple[str, ...]
    urgent: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not _KIND.fullmatch(self.kind):
            raise ValueError(f"{self.kind!r} is not a message kind (lowercase letters and _)")
        if not isinstance(self.title, str):
            raise TypeError("a message title is text")
        if isinstance(self.lines, str):
            raise TypeError("lines is a sequence of lines, not one string")
        lines = tuple(self.lines)
        if not all(isinstance(line, str) for line in lines):
            raise TypeError("every line of a message is text")
        if not isinstance(self.urgent, bool):
            raise TypeError("urgent is true or false")
        object.__setattr__(self, "lines", lines)

    @property
    def body(self) -> str:
        """The lines as one text, a line each."""
        return "\n".join(self.lines)

    @property
    def text(self) -> str:
        """Title and lines as one text, as a person reads it."""
        return "\n".join((self.title, *self.lines))
