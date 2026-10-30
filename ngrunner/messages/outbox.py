"""
The outbox: how a message leaves NG Runner, and how its receipt comes back.

NG Runner never sends email or Slack itself. It leaves a signed file in the control folder's
outbox/ (build spec section 3), the Apps Script sends it from ICLA's own account and writes a
signed receipt; no receipt within 5 minutes turns the tray red and, once Pushover's keys are
added, sends urgent items there directly (design section 4, "Silence and failure"). A file
names a role (jim, barbara...), never an address: the Apps Script turns roles into addresses from
its own settings, so no address is ever written into a file. This module only builds the bytes
and reads receipts; the write itself goes through commit_write (wave 2), and the replay rules
(seq above the last accepted, id never seen) are the store's.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NamedTuple

from ngrunner.clock import from_iso, pacific_day
from ngrunner.config import ROLES
from ngrunner.messages import Message
from ngrunner.messages.lint import lint, redact, subject_line
from ngrunner.signing import EnvelopeError, new_id, open_envelope, seal

CHANNELS = ("email", "slack", "pushover")
OUTBOX_KIND = "outbox"
RECEIPT_KIND = "receipt"
FOLDER = "outbox"            # in the control folder
REF_LIMIT = 120
_DIGEST_KINDS = frozenset({"digest", "quiet_digest"})
_ENVELOPE_ID = re.compile(r"[0-9a-f]{32}")
_RECEIPT_FIELDS = frozenset({"outbox_id", "sent_at", "channel", "ok", "error"})


class OutboxError(ValueError):
    """A message can't become an outbox file; the reason is in plain words."""


class ReceiptError(EnvelopeError):
    """A signed receipt that doesn't read as one; an EnvelopeError, so one except catches
    both."""


# ----- outbox files


class OutboxFile(NamedTuple):
    """Where the file goes, under the control folder, and its bytes. Unpacks as the pair."""

    path_parts: tuple[str, ...]
    data: bytes

    @property
    def id(self) -> str:
        """The file's envelope id, which its receipt names."""
        return self.path_parts[-1].removesuffix(".json")

    @property
    def ref(self) -> str:
        """The ref its payload carries, for the store's row: writing the row again needs it."""
        return json.loads(json.loads(self.data)["body"])["payload"]["ref"]


def outbox_file(message: Message, *, to: str, channel: str, key: bytes, seq: int,
                now: datetime, ref: str | None = None, id: str | None = None) -> OutboxFile:
    """One signed outbox file for message. to is a role; channel is email, slack or pushover;
    seq is the outbox stream's next number (the store's); ref says what the message is about
    (a card ID, a job name), for the Apps Script's log and threading. By default it is the
    message's kind, or for a digest digest:<Pacific YYYY-MM-DD of now>, which is how the Apps
    Script knows Jim's list went out, so leave it to the default on a digest. id is the envelope
    id, new by default. A queued row is written again with its own: rebuild it as
    Message("outbox", row.subject, row.lines, row.urgent) and pass id=row.id, seq=row.seq and
    ref=row.ref, so the numbers have no gap. A message that fails the lint is refused: send
    templates.vetted(message) instead. Its title goes as the subject, cut to lint.SUBJECT_LIMIT,
    so no row can make one that MailApp refuses."""
    if to not in ROLES:
        raise OutboxError("the recipient must be one of NG Runner's roles, never an address")
    if channel not in CHANNELS:
        raise OutboxError("the channel must be email, slack or pushover")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
        raise OutboxError("the outbox number must be a whole number from 1")
    if id is not None and not (isinstance(id, str) and _ENVELOPE_ID.fullmatch(id)):
        raise OutboxError("an outbox id is 32 lowercase hex characters")
    ref = _default_ref(message.kind, now) if ref is None else ref
    _check_ref(ref)
    problems = lint(message)
    if problems:
        raise OutboxError(f"the message failed the lint ({problems[0]}); nothing was built")
    payload: dict[str, Any] = {
        "channel": channel, "to": to, "subject": subject_line(message.title),
        "lines": list(message.lines), "urgent": message.urgent, "ref": ref,
    }
    file_id = new_id() if id is None else id
    data = seal(OUTBOX_KIND, payload, key=key, seq=seq, issued_at=now, id=file_id)
    return OutboxFile((FOLDER, f"{file_id}.json"), data)


def _default_ref(kind: str, now: datetime) -> str:
    return f"digest:{pacific_day(now).isoformat()}" if kind in _DIGEST_KINDS else kind


def _check_ref(ref: object) -> None:
    if not (isinstance(ref, str) and 0 < len(ref) <= REF_LIMIT and ref == ref.strip()
            and "@" not in ref
            and not any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in ref)):
        raise OutboxError(f"a ref is one short line of up to {REF_LIMIT} characters, with no"
                          " address in it")


# ----- receipts


@dataclass(frozen=True)
class Receipt:
    """The Apps Script's word on one outbox file.

    outbox_id: the file it is for. sent_at: when it was sent, or tried. ok: whether it went.
    error: the sender's reason when not, with addresses, links and codes taken out (redact).
    id, seq, issued_at: the receipt's own envelope, for the store's replay guard.
    """

    outbox_id: str
    sent_at: datetime
    channel: str
    ok: bool
    error: str | None
    id: str
    seq: int
    issued_at: datetime


def read_receipt(data: bytes, *, key: bytes) -> Receipt:
    """A receipt file read and checked: signature, kind, and every field. Anything else raises
    EnvelopeError (ReceiptError for a well-signed receipt with a wrong field)."""
    envelope = open_envelope(data, key=key, kind=RECEIPT_KIND)
    fields = envelope.payload
    if set(fields) != _RECEIPT_FIELDS:
        raise ReceiptError("the receipt does not have exactly the five things a receipt holds")
    outbox_id, channel, ok, error = (fields["outbox_id"], fields["channel"], fields["ok"],
                                     fields["error"])
    if not (isinstance(outbox_id, str) and _ENVELOPE_ID.fullmatch(outbox_id)):
        raise ReceiptError("the receipt does not name the message it is for")
    if channel not in CHANNELS:
        raise ReceiptError("the receipt's channel is not email, slack or pushover")
    if not isinstance(ok, bool):
        raise ReceiptError("the receipt does not say whether the send worked")
    if error is not None and not isinstance(error, str):
        raise ReceiptError("the receipt's reason is not text")
    return Receipt(outbox_id, _sent_at(fields["sent_at"]), channel, ok,
                   None if error is None else redact(error), envelope.id, envelope.seq,
                   envelope.issued_at)


def _sent_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise ReceiptError("the receipt's send time is missing")
    try:
        return from_iso(value)
    except ValueError as exc:
        raise ReceiptError("the receipt's send time is not a full time with an offset") from exc
