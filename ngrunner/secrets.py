"""
NG Runner's secrets: the control folder's signing key, the Render export key and the Pushover
keys (build spec section 6.1; design section 6, "Secrets"; AGENTS.md rule 6).

Why Windows Credential Manager: it is per user and encrypted with the user's logon, so a copy
of NG Runner's data folder, its database or a support bundle holds no key. Each secret is a
generic credential named NGRunner/<name>, kept on this PC only (not roaming), its value stored
as UTF-16 text, as Windows and cmdkey store theirs. MemorySecrets keeps the same rules in
memory, for tests.

A value is never logged, printed, or put in an exception or a repr: errors name the secret,
never its value. Names are a closed list (NAMES, extended phase by phase), so a typo is
refused instead of reading as "not set", and a value passed where a name belongs is refused
without being echoed.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

NAMES = ("signing_key", "export_key", "pushover_token", "pushover_user")
TARGET_PREFIX = "NGRunner"
MAX_BYTES = 2560  # Credential Manager's limit for one value (CRED_MAX_CREDENTIAL_BLOB_SIZE)
_NOT_FOUND = 1168  # ERROR_NOT_FOUND
_NO_LOGON_SESSION = 1312  # ERROR_NO_SUCH_LOGON_SESSION: this session has no Credential Manager
_PREFIX_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_./")


class SecretsError(Exception):
    """A secret could not be read, kept or removed; the message names it, never its value."""


class SecretsUnavailable(SecretsError):
    """Windows Credential Manager can't be used here (no pywin32, or no logon session)."""


class Secrets(Protocol):
    """What wave 2 is handed: CredentialSecrets on Jim's PC, MemorySecrets in tests."""

    def get(self, name: str) -> str | None: ...

    def put(self, name: str, value: str) -> None: ...

    def remove(self, name: str) -> None: ...


# ----- Windows Credential Manager


class CredentialSecrets:
    """Secrets in Windows Credential Manager, as generic credentials <target_prefix>/<name> of
    the current user. api stands in for win32cred in tests; by default it is win32cred."""

    def __init__(self, *, target_prefix: str = TARGET_PREFIX, api: Any = None) -> None:
        if not (isinstance(target_prefix, str) and target_prefix
                and set(target_prefix) <= _PREFIX_CHARS and not target_prefix.endswith("/")):
            raise SecretsError("a target prefix is letters, digits, - _ . and /, like NGRunner")
        self._prefix = target_prefix
        self._api = api

    def __repr__(self) -> str:
        return f"CredentialSecrets(target_prefix={self._prefix!r})"

    def get(self, name: str) -> str | None:
        """The secret's value, or None when it is not set."""
        target = self._target(name)
        api, error = self._win32()
        try:
            credential = api.CredRead(target, api.CRED_TYPE_GENERIC, 0)
        except error as exc:
            if exc.winerror == _NOT_FOUND:
                return None
            raise _failed("read", name, exc) from None
        return _decode(credential.get("CredentialBlob"), name)

    def put(self, name: str, value: str) -> None:
        """Set the secret, replacing any value it had."""
        target = self._target(name)
        _encode(value, name)  # refused here, with its reason, before Windows sees it
        api, error = self._win32()
        credential = {
            "Type": api.CRED_TYPE_GENERIC,
            "TargetName": target,
            "UserName": name,
            "CredentialBlob": value,  # pywin32 stores text as UTF-16; CredRead gives the bytes
            "Persist": api.CRED_PERSIST_LOCAL_MACHINE,
            "Comment": "NG Runner",
        }
        try:
            api.CredWrite(credential, 0)
        except error as exc:
            raise _failed("store", name, exc) from None

    def remove(self, name: str) -> None:
        """Remove the secret; nothing happens when it is not set."""
        target = self._target(name)
        api, error = self._win32()
        try:
            api.CredDelete(target, api.CRED_TYPE_GENERIC, 0)
        except error as exc:
            if exc.winerror != _NOT_FOUND:
                raise _failed("remove", name, exc) from None

    def _target(self, name: str) -> str:
        return f"{self._prefix}/{_name(name)}"

    def _win32(self) -> tuple[Any, type[BaseException]]:
        """The Credential Manager API and the error it raises; SecretsUnavailable without
        pywin32."""
        try:
            import pywintypes
            import win32cred
        except ImportError:  # holds no value, so raising inside it shows none
            raise SecretsUnavailable(
                "Windows Credential Manager can't be reached (pywin32 is missing)"
            ) from None
        return (win32cred if self._api is None else self._api), pywintypes.error


def _failed(verb: str, name: str, exc: Any) -> SecretsError:
    """The plain reason for a Credential Manager failure: Windows' own words and code, the
    secret's name, never its value."""
    reason = getattr(exc, "strerror", "") or "no reason given"
    detail = f"{reason}, error {getattr(exc, 'winerror', '?')}"
    if getattr(exc, "winerror", None) == _NO_LOGON_SESSION:
        return SecretsUnavailable(
            f"Windows Credential Manager is not available in this session ({detail})"
        )
    return SecretsError(
        f"Windows Credential Manager could not {verb} the {name} secret ({detail})"
    )


# ----- in memory, for tests


class MemorySecrets:
    """Secrets kept in memory, under the same rules as CredentialSecrets: for tests."""

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self._values: dict[str, str] = {}
        for name, value in (values or {}).items():
            self.put(name, value)

    def __repr__(self) -> str:
        return f"MemorySecrets(names={sorted(self._values)!r})"

    def get(self, name: str) -> str | None:
        return self._values.get(_name(name))

    def put(self, name: str, value: str) -> None:
        checked = _name(name)
        _encode(value, checked)
        self._values[checked] = value

    def remove(self, name: str) -> None:
        self._values.pop(_name(name), None)


# ----- the module's own: NG Runner's secrets on this PC


_DEFAULT: Secrets = CredentialSecrets()


def get(name: str) -> str | None:
    """The value of NG Runner's secret name (Credential Manager: NGRunner/<name>), or None."""
    return _DEFAULT.get(name)


def put(name: str, value: str) -> None:
    """Set NG Runner's secret name to value."""
    _DEFAULT.put(name, value)


def remove(name: str) -> None:
    """Remove NG Runner's secret name; nothing happens when it is not set."""
    _DEFAULT.remove(name)


# ----- checks that never show a value


def _name(name: object) -> str:
    if name not in NAMES:  # never echoed: it may be a value passed by mistake
        raise SecretsError(f"unknown secret name; NG Runner keeps only: {', '.join(NAMES)}")
    return str(name)


def _encode(value: object, name: str) -> bytes:
    """The value as Credential Manager keeps it (UTF-16 text). Refusals name the secret only,
    and are raised outside any except block, so no error carries the value along."""
    blob: bytes | None = None
    if isinstance(value, str) and value:
        try:
            blob = value.encode("utf-16-le")
        except UnicodeEncodeError:
            blob = None
    if blob is None:
        raise SecretsError(f"the {name} secret must be non-empty text that Windows can store")
    if len(blob) > MAX_BYTES:
        raise SecretsError(f"the {name} secret is too long for Windows Credential Manager")
    return blob


def _decode(blob: object, name: str) -> str | None:
    if not blob:
        return None
    text: str | None = None
    if isinstance(blob, bytes | bytearray) and len(blob) % 2 == 0:
        try:
            text = bytes(blob).decode("utf-16-le")
        except UnicodeDecodeError:
            text = None
    if text is None:
        raise SecretsError(f"the {name} secret in Windows Credential Manager is not readable text")
    return text
