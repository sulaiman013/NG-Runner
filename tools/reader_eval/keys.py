"""
The reader evaluation's provider keys, from Windows Credential Manager.

Why not ngrunner.secrets: its names are a closed list owned by the app, and the evaluation is a dev
tool whose keys the app must never hold. The rules are the same (AGENTS.md rule 6; design 6,
"Secrets"): a generic credential per key, NGRunner/openai_key and NGRunner/anthropic_key, stored as
UTF-16 text; a value is never printed, logged, put in a repr or in an error. Only these two names
can be read, so a typo is refused instead of reading as "not set".

To store one (Sulaiman, by hand, never in a script that echoes it): Credential Manager > Windows
Credentials > Add a generic credential, internet address NGRunner/openai_key, any user name, the
key as the password.
"""
from __future__ import annotations

from typing import Any

NAMES = ("openai_key", "anthropic_key")
TARGET_PREFIX = "NGRunner"
_NOT_FOUND = 1168  # ERROR_NOT_FOUND


class KeyProblem(Exception):
    """A key could not be read; the message names the key, never its value."""


def target(name: str) -> str:
    if name not in NAMES:
        raise KeyProblem(f"the reader evaluation reads only {', '.join(NAMES)}")
    return f"{TARGET_PREFIX}/{name}"


def read_key(name: str, *, api: Any = None) -> str | None:
    """The key's value, or None when Credential Manager has no NGRunner/<name>."""
    where = target(name)
    if api is None:
        try:
            import win32cred
        except ImportError:
            raise KeyProblem("Windows Credential Manager can't be reached (pywin32 is missing)") from None
        api = win32cred
    try:
        credential = api.CredRead(where, api.CRED_TYPE_GENERIC, 0)
    except Exception as exc:  # noqa: BLE001 - pywintypes.error and fakes; only the code is kept
        if getattr(exc, "winerror", None) == _NOT_FOUND:
            return None
        code = getattr(exc, "winerror", "?")
        raise KeyProblem(f"Windows Credential Manager could not read {where} (error {code})") from None
    blob = credential.get("CredentialBlob") if isinstance(credential, dict) else None
    text: str | None = None
    if isinstance(blob, bytes | bytearray) and blob and len(blob) % 2 == 0:
        try:
            text = bytes(blob).decode("utf-16-le")
        except UnicodeDecodeError:
            text = None
    if not text or not text.strip():
        raise KeyProblem(f"{where} in Windows Credential Manager is not readable text")
    return text.strip()
