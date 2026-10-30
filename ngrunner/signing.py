"""
Signed envelopes for the control folder.

Everything NG Runner and its Apps Script partner leave for each other in the control folder
(heartbeats, outbox messages, receipts, the Fab export, pause requests) is signed and carries an
ID and a sequence number (design section 3.1), so a stray, edited or replayed file is never acted
on. The signature covers one exact string, the body, so Python and Apps Script never have to
agree on how to serialise JSON: the signer writes the body once, and the verifier checks those
very bytes before it parses them.

A file is {"body": "<JSON text>", "sig": "<64 hex: HMAC-SHA256 of the body's UTF-8 bytes>"}.
The body holds v, kind, id, seq, issued_at and payload. The key is 32 random bytes, kept as 64
hex characters in Windows Credential Manager on Jim's PC and in the Apps Script's properties.
Replay is the receiver's job: it accepts a seq only above the last one it accepted from that
stream (store), and acts on an id only once.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ngrunner.clock import from_iso, to_iso

VERSION = 1
KEY_BYTES = 32
_HEX = re.compile(r"[0-9a-f]+")
_ID = re.compile(r"[0-9a-f]{32}")
_KIND = re.compile(r"[a-z][a-z0-9_]{0,39}")
_BODY_KEYS = frozenset({"v", "kind", "id", "seq", "issued_at", "payload"})
MAX_BYTES = 4 * 1024 * 1024  # a control-folder file larger than this is refused unread


class EnvelopeError(ValueError):
    """The file is not a valid signed envelope; the message says why, in plain words."""


@dataclass(frozen=True)
class Envelope:
    kind: str
    id: str
    seq: int
    issued_at: datetime
    payload: dict[str, Any]


# ----- keys


def new_key() -> str:
    """A new signing key as 64 hex characters (for setup; never logged)."""
    return secrets.token_hex(KEY_BYTES)


def key_from_text(text: str) -> bytes:
    """The key from its stored form. A key that is not 64 hex characters is refused."""
    text = text.strip().lower()
    if len(text) != 2 * KEY_BYTES or not _HEX.fullmatch(text):
        raise EnvelopeError("the signing key is not 64 hex characters")
    return bytes.fromhex(text)


# ----- sealing and opening


def new_id() -> str:
    return secrets.token_hex(16)


def seal(
    kind: str,
    payload: dict[str, Any],
    *,
    key: bytes,
    seq: int,
    issued_at: datetime,
    id: str | None = None,
) -> bytes:
    """The bytes of one signed file."""
    _check_fields(kind, id or "0" * 32, seq)
    body = json.dumps(
        {"v": VERSION, "kind": kind, "id": id or new_id(), "seq": seq,
         "issued_at": to_iso(issued_at), "payload": payload},
        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )
    return json.dumps({"body": body, "sig": _mac(key, body)}, ensure_ascii=False).encode("utf-8")


def open_envelope(data: bytes, *, key: bytes, kind: str | None = None) -> Envelope:
    """Check the signature, then read the body. Raises EnvelopeError on anything wrong."""
    if len(data) > MAX_BYTES:
        raise EnvelopeError("the file is too large to be one of NG Runner's")
    outer = _json(data, "the file")
    if not (isinstance(outer, dict) and set(outer) == {"body", "sig"}
            and isinstance(outer["body"], str) and isinstance(outer["sig"], str)):
        raise EnvelopeError("the file is not a signed envelope")
    if not hmac.compare_digest(_mac(key, outer["body"]), outer["sig"].lower()):
        raise EnvelopeError("the signature does not match")
    return _read_body(_json(outer["body"].encode("utf-8"), "the body"), kind)


def _read_body(body: object, kind: str | None) -> Envelope:
    if not isinstance(body, dict) or set(body) != _BODY_KEYS or body["v"] != VERSION:
        raise EnvelopeError("the body is not version 1 of NG Runner's envelope")
    seq = body["seq"]
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise EnvelopeError("its sequence number is not a whole number")
    if not isinstance(body["kind"], str) or not isinstance(body["id"], str):
        raise EnvelopeError("its kind or id is not text")
    _check_fields(body["kind"], body["id"], seq)
    if kind is not None and body["kind"] != kind:
        raise EnvelopeError(f"it is a {body['kind']} file, not a {kind} file")
    if not isinstance(body["payload"], dict):
        raise EnvelopeError("its payload is not an object")
    try:
        issued_at = from_iso(str(body["issued_at"]))
    except ValueError as exc:
        raise EnvelopeError("its issue time is not a full time with an offset") from exc
    return Envelope(body["kind"], body["id"], seq, issued_at, body["payload"])


def _check_fields(kind: str, id: str, seq: int) -> None:
    if not _KIND.fullmatch(kind):
        raise EnvelopeError(f"{kind!r} is not a valid kind")
    if not _ID.fullmatch(id):
        raise EnvelopeError("its id is not 32 hex characters")
    if seq < 1:
        raise EnvelopeError("its sequence number is below 1")


def _mac(key: bytes, body: str) -> str:
    if len(key) != KEY_BYTES:
        raise EnvelopeError("the signing key is not 32 bytes")
    return hmac.new(key, body.encode("utf-8"), hashlib.sha256).hexdigest()


def _json(data: bytes, what: str) -> object:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvelopeError(f"{what} is not readable JSON") from exc
