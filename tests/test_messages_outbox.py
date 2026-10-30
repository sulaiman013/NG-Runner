"""The outbox: messages leave as signed files for the Apps Script to send, and receipts come
back (build spec section 3). Nothing here writes: commit_write does, in wave 2."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ngrunner import heartbeat
from ngrunner.cards import new_card
from ngrunner.digest import DigestView, JobLine, compose
from ngrunner.match import Opportunity
from ngrunner.messages import Message
from ngrunner.messages import templates as t
from ngrunner.messages.lint import SUBJECT_LIMIT
from ngrunner.messages.outbox import (
    CHANNELS, OutboxError, Receipt, ReceiptError, outbox_file, read_receipt,
)
from ngrunner.signing import EnvelopeError, key_from_text, new_key, open_envelope, seal
from ngrunner.store import add_outbox, open_store

KEY = key_from_text("00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff")
NOW = datetime(2026, 10, 30, 22, 5, tzinfo=timezone.utc)
FILED = t.filed("Testerson_101426", row="Testerson", designer="PE")
LONG_ROW = "Testerson " + "closet and pantry " * 16   # about 300 characters
UNDATED = (Opportunity("Testerson", "201", "Pat Example", "Ready to Order"),)
LONG_CARD = t.card_message(new_card(t.which_job_card(LONG_ROW, candidates=UNDATED), created_at=NOW,
                                    allocate=lambda prefix: 2))


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


# ----- a queued row written again: the row is recorded first, so after a refused commit or a
# crash its file is rebuilt from the row, with no new id and no gap in the numbers


@pytest.mark.parametrize("message", [
    FILED, t.quiet_digest(date(2026, 10, 30)), t.which_job_email(LONG_ROW, candidates=UNDATED),
    LONG_CARD,
], ids=["a filed email", "a digest", "a long row with an undated candidate", "a long row's card"])
def test_a_queued_row_is_written_again_with_its_own_id_seq_and_ref(message, tmp_path):
    first = outbox_file(message, to="jim", channel="email", key=KEY, seq=7, now=NOW)
    row = add_outbox(open_store(tmp_path / "ngrunner.db"), id=first.id, seq=7, channel="email",
                     recipient="jim", subject=message.title, lines=message.lines,
                     urgent=message.urgent, created_at=NOW, ref=first.ref)
    again = outbox_file(Message("outbox", row.subject, row.lines, row.urgent), to=row.recipient,
                        channel=row.channel, key=KEY, seq=row.seq, now=NOW + timedelta(minutes=1),
                        ref=row.ref, id=row.id)
    assert again.path_parts == first.path_parts and again.id == first.id
    was, now = (open_envelope(made.data, key=KEY, kind="outbox") for made in (first, again))
    assert (now.id, now.seq, now.payload) == (was.id, was.seq, was.payload)


def test_a_file_says_the_ref_it_carries_for_the_stores_row():
    made = outbox_file(FILED, to="barbara", channel="email", key=KEY, seq=1, now=NOW)
    assert made.ref == "filed"
    named = outbox_file(FILED, to="barbara", channel="email", key=KEY, seq=2, now=NOW, ref="TES-2")
    assert named.ref == "TES-2"


@pytest.mark.parametrize("bad", ["", "abc", "AB" * 16, "../" + "a" * 29, 7])
def test_an_id_that_could_not_name_an_outbox_file_is_refused(bad):
    with pytest.raises(OutboxError):
        outbox_file(FILED, to="jim", channel="email", key=KEY, seq=1, now=NOW, id=bad)


# ----- a subject MailApp can't refuse, whatever a row holds


@pytest.mark.parametrize("message, start, end", [
    (t.which_job_email(LONG_ROW), 'Which job is Fab row "Testerson closet', '…"?'),
    (t.which_job_email("x" * 300), 'Which job is Fab row "xxx', '…"?'),
    (t.shadow(t.which_job_email(LONG_ROW)), 'SHADOW Which job is Fab row "Testerson', '…"?'),
    (LONG_CARD, 'TES-2 · Fab row "Testerson closet', '…" · Which job is this row?'),
    (t.shadow(LONG_CARD), 'SHADOW TES-2 · Fab row "Testerson', '…" · Which job is this row?'),
], ids=["which job email", "one long word", "shadow", "card", "shadow card"])
def test_every_subject_is_cut_whatever_the_row_holds(message, start, end):
    made = outbox_file(message, to="barbara", channel="email", key=KEY, seq=1, now=NOW)
    subject = open_envelope(made.data, key=KEY, kind="outbox").payload["subject"]
    assert len(subject) <= SUBJECT_LIMIT
    assert subject.startswith(start) and subject.endswith(end)


def test_a_title_with_no_row_in_it_is_cut_at_a_word_too():
    message = Message("filed", "Filed as Testerson_101426" + " pantry" * 40, ("It is filed.",))
    made = outbox_file(message, to="barbara", channel="email", key=KEY, seq=1, now=NOW)
    subject = open_envelope(made.data, key=KEY, kind="outbox").payload["subject"]
    assert len(subject) <= SUBJECT_LIMIT and subject.endswith("pantry…")


# ----- a digest's ref: how the Apps Script knows Jim's list went out


LATE = datetime(2026, 10, 31, 6, 0, tzinfo=timezone.utc)        # 11 PM Fri 10/30 in Pacific
DAY = date(2026, 10, 30)


@pytest.mark.parametrize("message", [
    t.digest(DAY), t.quiet_digest(DAY), t.fixed("digest", day=DAY),
    t.fixed("quiet_digest", day=DAY),
], ids=["digest", "quiet night", "fixed digest", "fixed quiet night"])
def test_a_digests_default_ref_names_its_pacific_day(message):
    _, data = outbox_file(message, to="jim", channel="email", key=KEY, seq=1, now=LATE)
    assert open_envelope(data, key=KEY).payload["ref"] == "digest:2026-10-30"


def test_a_ref_given_is_kept_on_a_digest_too():
    _, data = outbox_file(t.quiet_digest(DAY), to="jim", channel="email", key=KEY, seq=1, now=LATE,
                          ref="digest:2026-10-29")
    assert open_envelope(data, key=KEY).payload["ref"] == "digest:2026-10-29"


def test_a_redirected_digest_keeps_the_digest_ref():
    made = outbox_file(t.shadow(t.quiet_digest(DAY)), to="sulaiman", channel="email", key=KEY,
                       seq=1, now=LATE)
    assert made.ref == "digest:2026-10-30"
    assert open_envelope(made.data, key=KEY).payload["subject"] == "SHADOW NG Runner · Fri 10/30"


def _node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    default = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "node.exe"
    return str(default) if default.is_file() else None


# The real Outbox.gs and Watchdog.gs, on the files Python left in the control folder: the digest
# at 6:30 AM, then the 7:00 AM check that tells Jim when no list came.
_THE_MORNING = r"""
const fs = require('node:fs');
const { load } = require(process.argv[2]);
const files = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const h = load({ now: '2026-10-30T13:30:00Z' });
h.control.put(files.outbox.path, files.outbox.text);
h.control.put(files.heartbeat.path, files.heartbeat.text);
h.gs.processOutbox();
const day = h.gs.state_('digest.day');
h.clock.set('2026-10-30T14:00:00Z');
h.gs.runWatchdog();
console.log(JSON.stringify({ day, mail: h.mail.sent.map((m) => [m.to, m.subject]),
  push: h.fetch.requests.map((r) => r.params.payload.title) }));
"""


@pytest.mark.parametrize("view", [
    DigestView(now=datetime(2026, 10, 30, 13, 30, tzinfo=timezone.utc)),
    DigestView(now=datetime(2026, 10, 30, 13, 30, tzinfo=timezone.utc),
               jobs=(JobLine("Testerson_101426", "filed 10/29 3:05 PM"),)),
], ids=["a quiet night", "a busy night"])
def test_the_real_apps_script_counts_the_composed_digest_so_no_false_no_list(view, tmp_path):
    node = _node()
    if node is None:
        pytest.skip("node is not installed, so the Apps Script can't run here")
    at = view.now
    first_seq = int(at.timestamp())                             # as a new stream starts (store)
    made = outbox_file(compose(view), to="jim", channel="email", key=KEY, seq=first_seq, now=at)
    beat = heartbeat.payload(app_version="0.1.0", paused=False, open_risks=(), last_fab_seq=None,
                             list_synced_at=None, pending=0, dev_open=0)
    parts, data = heartbeat.heartbeat_file(beat, key=KEY, seq=first_seq, now=at)
    files = tmp_path / "files.json"
    files.write_text(json.dumps({
        "outbox": {"path": "/".join(made.path_parts), "text": made.data.decode("utf-8")},
        "heartbeat": {"path": "/".join(parts), "text": data.decode("utf-8")},
    }), encoding="utf-8")
    script = tmp_path / "the_morning.js"
    script.write_text(_THE_MORNING, encoding="utf-8")
    harness = Path(__file__).resolve().parent.parent / "google" / "test" / "harness.js"
    done = subprocess.run([node, str(script), str(harness), str(files)], capture_output=True,
                          encoding="utf-8", errors="replace", timeout=120, check=False)
    assert done.returncode == 0, done.stderr[-4000:]
    got = json.loads(done.stdout)
    assert got["day"] == "20261030"                             # the Apps Script's digest.day
    assert got["mail"] == [["jim@example.com", "NG Runner · Fri 10/30"]]    # and nothing else:
    assert got["push"] == []                                    # no "No list today" for Jim


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
