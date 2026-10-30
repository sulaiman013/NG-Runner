"""The outbox: messages leave as signed files for the Apps Script to send, and receipts come
back (build spec section 3). Nothing here writes: commit_write does, in wave 2."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from ngrunner.messages import Message
from ngrunner.messages import templates as t
from ngrunner.messages.outbox import (
    CHANNELS, OutboxError, Receipt, ReceiptError, outbox_file, read_receipt,
)
from ngrunner.signing import EnvelopeError, key_from_text, new_key, open_envelope, seal

KEY = key_from_text("00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff")
NOW = datetime(2026, 10, 30, 22, 5, tzinfo=timezone.utc)
FILED = t.filed("Testerson_101426", row="Testerson", designer="PE")


# ----- outbox files


def test_an_outbox_file_is_a_signed_envelope_the_apps_script_can_send():
    parts, data = outbox_file(FILED, to="barbara", channel="email", key=KEY, seq=7, now=NOW)
    envelope = open_envelope(data, key=KEY, kind="outbox")
    assert parts == ("outbox", f"{envelope.id}.json")
    assert (envelope.seq, envelope.issued_at) == (7, NOW)
    assert envelope.payload == {
        "channel": "email", "to": "barbara", "subject": "Filed as Testerson_101426",
        "lines": ['Fab row "Testerson" (designer PE) is filed as Testerson_101426.'],
        "urgent": False, "ref": "filed",
    }


def test_the_file_knows_its_id_for_matching_the_receipt():
    made = outbox_file(FILED, to="barbara", channel="email", key=KEY, seq=7, now=NOW)
    assert made.id == open_envelope(made.data, key=KEY).id and len(made.id) == 32
    other = outbox_file(FILED, to="barbara", channel="email", key=KEY, seq=8, now=NOW)
    assert other.id != made.id


def test_ref_says_what_the_message_is_about_and_urgency_is_kept():
    urgent = t.cant_send(since=NOW - timedelta(minutes=7), now=NOW, waiting=2)
    _, data = outbox_file(urgent, to="jim", channel="pushover", key=KEY, seq=1, now=NOW,
                          ref="receipts 10/30")
    payload = open_envelope(data, key=KEY).payload
    assert (payload["ref"], payload["urgent"], payload["channel"]) == (
        "receipts 10/30", True, "pushover")


@pytest.mark.parametrize("to", ["barbara@example.com", "Barbara", "", "the boss"])
def test_the_recipient_is_a_role_never_an_address(to):
    with pytest.raises(OutboxError):
        outbox_file(FILED, to=to, channel="email", key=KEY, seq=1, now=NOW)


@pytest.mark.parametrize("channel", ["sms", "", "Email"])
def test_the_channel_is_one_the_apps_script_knows(channel):
    with pytest.raises(OutboxError):
        outbox_file(FILED, to="jim", channel=channel, key=KEY, seq=1, now=NOW)
    assert CHANNELS == ("email", "slack", "pushover")


@pytest.mark.parametrize("ref", ["", "two\nlines", "x" * 121, "barbara@example.com"])
def test_a_ref_that_could_carry_more_than_a_label_is_refused(ref):
    with pytest.raises(OutboxError):
        outbox_file(FILED, to="jim", channel="email", key=KEY, seq=1, now=NOW, ref=ref)


def test_a_message_that_fails_the_lint_never_becomes_a_file():
    bad = Message("filed", "Filed as Testerson_101426", ("Saved in G:\\NG Jobs.",))
    with pytest.raises(OutboxError, match="lint"):
        outbox_file(bad, to="barbara", channel="email", key=KEY, seq=1, now=NOW)


@pytest.mark.parametrize("seq, now", [
    (0, NOW), (True, NOW), ("2", NOW), (1, NOW.replace(tzinfo=None)),
])
def test_a_bad_sequence_or_a_naive_time_is_refused(seq, now):
    with pytest.raises((EnvelopeError, ValueError)):
        outbox_file(FILED, to="jim", channel="email", key=KEY, seq=seq, now=now)


def test_no_address_is_ever_written_into_a_file():
    _, data = outbox_file(FILED, to="barbara", channel="email", key=KEY, seq=1, now=NOW)
    assert b"@" not in data


# ----- receipts

OUTBOX_ID = "0123456789abcdef0123456789abcdef"


def receipt(seq=3, key=KEY, kind="receipt", **change) -> bytes:
    payload = {"outbox_id": OUTBOX_ID, "sent_at": "2026-10-30T22:06:00+00:00",
               "channel": "email", "ok": True, "error": None} | change
    return seal(kind, payload, key=key, seq=seq, issued_at=NOW, id="cd" * 16)


def test_a_receipt_says_which_message_went_out_and_when():
    assert read_receipt(receipt(), key=KEY) == Receipt(
        outbox_id=OUTBOX_ID, sent_at=datetime(2026, 10, 30, 22, 6, tzinfo=timezone.utc),
        channel="email", ok=True, error=None, id="cd" * 16, seq=3, issued_at=NOW)


def test_a_failed_send_keeps_its_reason_without_addresses_or_links():
    got = read_receipt(receipt(ok=False, error="Invalid email: barbara@example.com, see"
                                               " https://script.example.com/x?token=abc"),
                       key=KEY)
    assert not got.ok and got.error == "Invalid email: [address], see [web address]"


@pytest.mark.parametrize("change", [
    {"outbox_id": "not-an-id"}, {"outbox_id": OUTBOX_ID.upper()}, {"sent_at": "2026-10-30T22:06"},
    {"sent_at": None}, {"ok": "yes"}, {"ok": 1}, {"channel": "fax"}, {"error": 42},
])
def test_a_receipt_with_a_wrong_field_is_refused(change):
    with pytest.raises(ReceiptError):
        read_receipt(receipt(**change), key=KEY)


def test_a_receipt_with_a_missing_or_an_extra_field_is_refused():
    payload = {"outbox_id": OUTBOX_ID, "sent_at": "2026-10-30T22:06:00+00:00",
               "channel": "email", "ok": True}
    for broken in (payload, payload | {"error": None, "extra": 1}):
        with pytest.raises(ReceiptError):
            read_receipt(seal("receipt", broken, key=KEY, seq=1, issued_at=NOW), key=KEY)


def test_anything_but_a_receipt_signed_with_our_key_is_refused():
    with pytest.raises(EnvelopeError):
        read_receipt(receipt(key=key_from_text(new_key())), key=KEY)
    with pytest.raises(EnvelopeError):
        read_receipt(receipt(kind="outbox"), key=KEY)
    with pytest.raises(EnvelopeError):
        read_receipt(b"not json", key=KEY)


def test_receipt_errors_are_envelope_errors_so_one_except_catches_both():
    assert issubclass(ReceiptError, EnvelopeError)
    with pytest.raises(EnvelopeError):
        read_receipt(receipt(ok="yes"), key=KEY)


def test_the_outbox_payload_is_plain_json_for_the_apps_script():
    _, data = outbox_file(FILED, to="barbara", channel="email", key=KEY, seq=2, now=NOW)
    body = json.loads(json.loads(data)["body"])
    assert set(body["payload"]) == {"channel", "to", "subject", "lines", "urgent", "ref"}


def test_an_outbox_file_is_one_commit_write_allows_into_the_control_folder(tmp_path):
    # Wave 2 writes it; this proves the path and bytes fit the chokepoint's P1a rules.
    from ngrunner.writes import FileWrite, Roots, commit_write

    roots = Roots(tmp_path / "NG Jobs", tmp_path / "Mozaik Shared", tmp_path / "control",
                  tmp_path / "appdata")
    for folder in (roots.ng_jobs, roots.mozaik_shared, roots.control, roots.app_data):
        folder.mkdir()
    made = outbox_file(FILED, to="barbara", channel="email", key=KEY, seq=1, now=NOW)
    target = roots.control.joinpath(*made.path_parts)
    result = commit_write([FileWrite(target, made.data, None)], roots=roots,
                          interlock=lambda paths: None)
    assert result.ok, result.reason
    assert open_envelope(target.read_bytes(), key=KEY, kind="outbox").id == made.id
