"""Signed envelopes: a stray, edited or foreign file in the control folder is never acted on."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ngrunner.signing import EnvelopeError, key_from_text, new_key, open_envelope, seal

KEY = key_from_text("00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff")
WHEN = datetime(2026, 10, 30, 13, 30, tzinfo=timezone.utc)
VECTORS = Path(__file__).parent / "vectors" / "envelope_v1.json"


def _sealed(**kw) -> bytes:
    args = {"kind": "heartbeat", "payload": {"note": "café 3/4\""}, "key": KEY, "seq": 7,
            "issued_at": WHEN, "id": "ab" * 16} | kw
    return seal(**args)


def test_a_sealed_file_opens_with_its_fields():
    env = open_envelope(_sealed(), key=KEY, kind="heartbeat")
    assert (env.kind, env.id, env.seq, env.issued_at) == ("heartbeat", "ab" * 16, 7, WHEN)
    assert env.payload == {"note": "café 3/4\""}


def test_the_shared_test_vector_is_reproduced_exactly():
    # The Apps Script side checks the same file, so both sides provably sign the same bytes.
    vector = json.loads(VECTORS.read_text(encoding="utf-8"))
    sealed = seal(vector["kind"], vector["payload"], key=bytes.fromhex(vector["key_hex"]),
                  seq=vector["seq"], issued_at=datetime.fromisoformat(vector["issued_at"]),
                  id=vector["id"])
    assert json.loads(sealed) == {"body": vector["body"], "sig": vector["sig"]}
    assert open_envelope(sealed, key=bytes.fromhex(vector["key_hex"])).payload == vector["payload"]


@pytest.mark.parametrize("change", [
    lambda d: d.replace(b'"seq\\":7', b'"seq\\":8'),        # an edited body
    lambda d: d.replace(b"heartbeat", b"pause_req"),          # another kind written in
])
def test_an_edited_file_is_refused(change):
    with pytest.raises(EnvelopeError, match="signature"):
        open_envelope(change(_sealed()), key=KEY)


def test_a_file_signed_with_another_key_is_refused():
    with pytest.raises(EnvelopeError, match="signature"):
        open_envelope(_sealed(), key=key_from_text(new_key()))


def test_a_file_of_another_kind_is_refused_when_one_is_expected():
    with pytest.raises(EnvelopeError, match="not a pause file"):
        open_envelope(_sealed(), key=KEY, kind="pause")


@pytest.mark.parametrize("data", [
    b"", b"not json", b"\xff\xfe", b"[]", b'{"body": "{}"}', b'{"body": 1, "sig": "00"}',
    b'{"body": "{}", "sig": "00", "extra": 1}',
])
def test_anything_that_is_not_an_envelope_is_refused(data):
    with pytest.raises(EnvelopeError):
        open_envelope(data, key=KEY)


def test_a_signed_body_with_bad_fields_is_still_refused():
    import hashlib
    import hmac

    def signed(body: dict) -> bytes:
        text = json.dumps(body)
        sig = hmac.new(KEY, text.encode(), hashlib.sha256).hexdigest()
        return json.dumps({"body": text, "sig": sig}).encode()

    good = {"v": 1, "kind": "heartbeat", "id": "ab" * 16, "seq": 1,
            "issued_at": "2026-10-30T13:30:00+00:00", "payload": {}}
    for bad in ({"seq": True}, {"seq": 0}, {"seq": "1"}, {"v": 2}, {"id": "x"},
                {"kind": "Heart beat"}, {"payload": []}, {"issued_at": "2026-10-30T13:30:00"}):
        with pytest.raises(EnvelopeError):
            open_envelope(signed(good | bad), key=KEY)
    assert open_envelope(signed(good), key=KEY).seq == 1


def test_an_oversized_file_is_refused_unread():
    with pytest.raises(EnvelopeError, match="too large"):
        open_envelope(b" " * (4 * 1024 * 1024 + 1), key=KEY)


@pytest.mark.parametrize("text", ["00" * 31, "zz" * 32, "", "00" * 33])
def test_a_key_that_is_not_64_hex_characters_is_refused(text):
    with pytest.raises(EnvelopeError):
        key_from_text(text)


def test_new_keys_are_random_and_well_formed():
    a, b = new_key(), new_key()
    assert a != b and len(key_from_text(a)) == 32
