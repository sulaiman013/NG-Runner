"""
NG Runner's secrets (build spec section 6.1): Windows Credential Manager behind get, put and
remove, and MemorySecrets with the same rules. A secret's value never shows in an error or a
repr. Credential Manager is faked at its API for the failure cases; one real round trip uses a
throwaway target under NGRunner-test/ and removes it again.
"""
from __future__ import annotations

import secrets as random
import traceback

import pytest

import ngrunner.secrets as ngsecrets
from ngrunner.secrets import (
    NAMES, CredentialSecrets, MemorySecrets, SecretsError, SecretsUnavailable,
)

VALUE = "5f" * 32  # shaped like the signing key: 64 hex characters
NOT_FOUND, NO_LOGON_SESSION, ACCESS_DENIED = 1168, 1312, 5


def all_text(exc: BaseException) -> str:
    """Everything an error could show: its message, repr and any chained errors."""
    return "".join(traceback.format_exception(exc)) + repr(exc)


class FakeCredentialApi:
    """win32cred's three calls over a dict, raising pywintypes.error as Windows does."""

    CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE = 1, 2

    def __init__(self, fail_with: int | None = None) -> None:
        self.credentials: dict[str, dict] = {}
        self.fail_with = fail_with

    def _maybe_fail(self, call: str) -> None:
        if self.fail_with is not None:
            raise pywintypes.error(self.fail_with, call, "Access is denied.")

    def CredRead(self, target: str, kind: int, flags: int = 0) -> dict:
        self._maybe_fail("CredRead")
        if target not in self.credentials:
            raise pywintypes.error(NOT_FOUND, "CredRead", "Element not found.")
        return dict(self.credentials[target])

    def CredWrite(self, credential: dict, flags: int = 0) -> None:
        """As pywin32 does: the value goes in as text and is kept as UTF-16 bytes."""
        self._maybe_fail("CredWrite")
        if not isinstance(credential["CredentialBlob"], str):
            raise TypeError("Objects of type 'bytes' can not be converted to Unicode.")
        kept = {**credential, "CredentialBlob": credential["CredentialBlob"].encode("utf-16-le")}
        self.credentials[credential["TargetName"]] = kept

    def CredDelete(self, target: str, kind: int, flags: int = 0) -> None:
        self._maybe_fail("CredDelete")
        if self.credentials.pop(target, None) is None:
            raise pywintypes.error(NOT_FOUND, "CredDelete", "Element not found.")


pywintypes = pytest.importorskip("pywintypes")


# ----- one interface, two homes


@pytest.fixture(params=["memory", "credential manager"])
def store(request: pytest.FixtureRequest):
    if request.param == "memory":
        return MemorySecrets()
    return CredentialSecrets(api=FakeCredentialApi())


def test_a_secret_is_stored_read_and_removed(store) -> None:
    assert store.get("signing_key") is None

    store.put("signing_key", VALUE)
    store.put("pushover_user", "u" + "x" * 29)
    assert store.get("signing_key") == VALUE and store.get("pushover_user") == "u" + "x" * 29

    store.put("signing_key", "6a" * 32)  # replaced
    assert store.get("signing_key") == "6a" * 32
    store.remove("signing_key")
    store.remove("signing_key")  # removing what is not there is fine
    assert store.get("signing_key") is None and store.get("pushover_user") == "u" + "x" * 29


def test_only_ng_runners_own_secret_names_are_kept(store) -> None:
    assert NAMES == ("signing_key", "export_key", "pushover_token", "pushover_user")
    for call in (lambda: store.get("signing_kee"), lambda: store.put("Signing_Key", VALUE),
                 lambda: store.remove("anthropic_key")):
        with pytest.raises(SecretsError, match="signing_key, export_key"):
            call()


def test_a_value_passed_where_the_name_belongs_is_never_echoed(store) -> None:
    with pytest.raises(SecretsError) as caught:
        store.put(VALUE, "signing_key")  # the arguments swapped by mistake

    assert VALUE not in all_text(caught.value)


@pytest.mark.parametrize("bad", ["", None, b"bytes", "x" * 1281, "\ud800" + VALUE])
def test_a_value_that_cannot_be_kept_is_refused_without_showing_it(store, bad) -> None:
    with pytest.raises(SecretsError) as caught:
        store.put("export_key", bad)

    assert "export_key" in str(caught.value)
    if isinstance(bad, str) and bad:
        assert bad not in all_text(caught.value)
    assert store.get("export_key") is None


def test_a_repr_names_no_value() -> None:
    memory = MemorySecrets({"signing_key": VALUE})

    assert VALUE not in repr(memory) and "signing_key" in repr(memory)
    assert VALUE not in repr(CredentialSecrets(api=FakeCredentialApi()))
    assert memory.get("signing_key") == VALUE


# ----- Windows Credential Manager, through a fake of its API


def test_each_secret_is_a_generic_credential_named_ngrunner_slash_name() -> None:
    api = FakeCredentialApi()

    CredentialSecrets(api=api).put("signing_key", VALUE)

    stored = api.credentials["NGRunner/signing_key"]
    assert stored["Type"] == api.CRED_TYPE_GENERIC
    assert stored["Persist"] == api.CRED_PERSIST_LOCAL_MACHINE  # this PC only, not roaming
    assert stored["CredentialBlob"] == VALUE.encode("utf-16-le")  # as cmdkey and Windows keep them


def test_the_module_functions_use_credential_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeCredentialApi()
    monkeypatch.setattr(ngsecrets, "_DEFAULT", CredentialSecrets(api=api))

    ngsecrets.put("pushover_token", VALUE)
    assert ngsecrets.get("pushover_token") == VALUE and "NGRunner/pushover_token" in api.credentials
    ngsecrets.remove("pushover_token")
    assert ngsecrets.get("pushover_token") is None


@pytest.mark.parametrize("call", [
    lambda s: s.get("signing_key"), lambda s: s.put("signing_key", VALUE),
    lambda s: s.remove("signing_key"),
], ids=["get", "put", "remove"])
def test_a_credential_manager_failure_names_the_secret_never_its_value(call) -> None:
    with pytest.raises(SecretsError) as caught:
        call(CredentialSecrets(api=FakeCredentialApi(fail_with=ACCESS_DENIED)))

    assert "signing_key" in str(caught.value) and "Access is denied" in str(caught.value)
    assert VALUE not in all_text(caught.value)
    assert not isinstance(caught.value, SecretsUnavailable)


def test_no_logon_session_means_credential_manager_is_unavailable() -> None:
    with pytest.raises(SecretsUnavailable):
        CredentialSecrets(api=FakeCredentialApi(fail_with=NO_LOGON_SESSION)).get("export_key")


def test_a_stored_value_that_is_not_text_is_refused_without_showing_it() -> None:
    api = FakeCredentialApi()
    api.credentials["NGRunner/export_key"] = {"CredentialBlob": b"\x00\xd8abc"}  # odd, not UTF-16

    with pytest.raises(SecretsError, match="export_key") as caught:
        CredentialSecrets(api=api).get("export_key")

    assert "abc" not in all_text(caught.value)


# ----- the real Credential Manager, once, under a throwaway target


def test_a_real_round_trip_through_windows_credential_manager() -> None:
    prefix = f"NGRunner-test/{random.token_hex(8)}"
    real = CredentialSecrets(target_prefix=prefix)
    value = random.token_hex(32)
    try:
        real.put("signing_key", value)
    except SecretsUnavailable as exc:
        pytest.skip(f"Windows Credential Manager is not available here: {exc}")
    try:
        assert real.get("signing_key") == value
    finally:
        real.remove("signing_key")
    assert real.get("signing_key") is None
