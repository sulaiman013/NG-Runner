"""The messenger: outbox files through the chokepoint, receipts, the red tray, shadow, quiet hours
and toasts (integration spec section 6; rulings 13-16, 22, 25). Roots are all under tmp_path, and
the test plays the Apps Script by writing signed receipts."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ngrunner.cards import Card
from ngrunner.clock import FakeClock
from ngrunner.config import AppPaths, settings_from
from ngrunner.messages import Message
from ngrunner.messages import templates as t
from ngrunner.messenger import (
    GIVE_UP, NO_RECEIPT, Messenger, in_quiet_hours, waits_for_morning,
)
from ngrunner.secrets import MemorySecrets
from ngrunner.signing import key_from_text, new_id, open_envelope, seal
from ngrunner.store import (
    get_outbox, last_sent_seq, list_dev, list_outbox, list_write_records, next_seq, open_store,
)
from ngrunner.toasts import ToastMessage, Toaster
from ngrunner.writes import Roots

KEY_TEXT = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
KEY = key_from_text(KEY_TEXT)
NOON = datetime(2026, 10, 30, 19, 0, tzinfo=timezone.utc)   # noon in Los Angeles
NIGHT = datetime(2026, 10, 31, 6, 0, tzinfo=timezone.utc)   # 11 PM in Los Angeles
FILED = t.filed("Testerson_101426", row="Testerson", designer="PE")
TOKEN, USER = "a" * 30, "b" * 30


class Rig:
    def __init__(self, tmp_path: Path, *, mode: str = "live", now: datetime = NOON,
                 secrets: dict[str, str] | None = None, toaster=None, http=None) -> None:
        for name in ("ng_jobs", "mozaik", "control", "app_data"):
            (tmp_path / name).mkdir(exist_ok=True)
        self.control = tmp_path / "control"
        self.roots = Roots(tmp_path / "ng_jobs", tmp_path / "mozaik", self.control,
                           tmp_path / "app_data")
        self.paths = AppPaths(tmp_path / "app_data")
        self.settings = settings_from({
            "ng_jobs": str(self.roots.ng_jobs), "mozaik_shared": str(self.roots.mozaik_shared),
            "control": str(self.control), "mode": mode})
        self.store = open_store(self.paths.db)
        self.clock = FakeClock(now)
        self.secrets = MemorySecrets({"signing_key": KEY_TEXT} if secrets is None else secrets)
        self.toaster, self.http = toaster, http
        self.messenger = self.new_messenger()

    def new_messenger(self) -> Messenger:
        """A new Messenger on the same store: what a restart makes."""
        return Messenger(self.store, self.settings, self.secrets, self.clock, self.roots,
                         self.paths, http=self.http, toaster=self.toaster)

    def outbox_files(self) -> list[Path]:
        folder = self.control / "outbox"
        return sorted(folder.iterdir()) if folder.exists() else []

    def payload(self, outbox_id: str) -> dict:
        data = (self.control / "outbox" / f"{outbox_id}.json").read_bytes()
        return open_envelope(data, key=KEY, kind="outbox").payload

    def receipt(self, outbox_id: str, *, ok: bool = True, error: str | None = None,
                seq: int = 5000, key: bytes = KEY) -> None:
        """The Apps Script's receipt for a message."""
        folder = self.control / "receipts"
        folder.mkdir(exist_ok=True)
        data = seal("receipt", {"outbox_id": outbox_id, "sent_at": self.clock.now().isoformat(),
                                "channel": "email", "ok": ok, "error": error},
                    key=key, seq=seq, issued_at=self.clock.now())
        (folder / f"{outbox_id}.json").write_bytes(data)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def _break_the_control_folder(rig: Rig) -> Path:
    """A control folder that is out of reach, as with Drive down: commits are refused."""
    moved = rig.control.with_name("control-away")
    rig.control.rename(moved)
    return moved


# ----- sending


def test_send_leaves_a_signed_outbox_file_through_the_chokepoint(rig: Rig) -> None:
    outbox_id = rig.messenger.send(FILED, to="barbara")
    row = get_outbox(rig.store, outbox_id)
    assert row.state == "written" and row.written_at == NOON
    assert rig.outbox_files() == [rig.control / "outbox" / f"{outbox_id}.json"]
    envelope = open_envelope(rig.outbox_files()[0].read_bytes(), key=KEY, kind="outbox")
    assert envelope.id == outbox_id and envelope.seq == row.seq
    assert envelope.payload == {"channel": "email", "to": "barbara", "subject": FILED.title,
                                "lines": list(FILED.lines), "urgent": False, "ref": "filed"}
    written = list_write_records(rig.store)
    assert len(written) == 1 and written[0].ok  # commit_recorded: the writes table has its row


def test_a_new_stream_starts_at_the_unix_second_and_counts_up(rig: Rig) -> None:
    first = rig.messenger.send(FILED, to="barbara")
    second = rig.messenger.send(FILED, to="barbara", ref="TES-1")
    assert get_outbox(rig.store, first).seq == int(NOON.timestamp())
    assert get_outbox(rig.store, second).seq == int(NOON.timestamp()) + 1
    assert rig.payload(second)["ref"] == "TES-1"


def test_a_digests_ref_is_its_pacific_day_so_the_apps_script_knows_it(rig: Rig) -> None:
    quiet = t.quiet_digest(NOON.date())
    outbox_id = rig.messenger.send(quiet, to="jim")
    assert rig.payload(outbox_id)["ref"] == "digest:2026-10-30"


def test_a_message_that_fails_the_lint_goes_as_its_fixed_template_with_a_dev_item(rig: Rig) -> None:
    bad = Message("filed", "Filed as Testerson_101426", ("Call 310-555-0100 about it.",))
    outbox_id = rig.messenger.send(bad, to="barbara")
    assert rig.payload(outbox_id)["subject"] == t.FIXED["filed"][0]
    dev = list_dev(rig.store)
    assert [d.code for d in dev] == ["message_replaced"]
    assert "310-555-0100" not in dev[0].text


def test_a_pause_stops_no_message(rig: Rig) -> None:
    # Ruling 12a: the messenger's interlock refuses only a target outside the outbox.
    assert rig.messenger._only_the_outbox([rig.control / "outbox" / "a.json"]) is None
    assert "not in the control folder's outbox" in rig.messenger._only_the_outbox(
        [rig.roots.ng_jobs / "outbox" / "a.json"])
    assert "not in the control folder's outbox" in rig.messenger._only_the_outbox(
        [rig.control / "shadow" / "a.json"])


# ----- a failed write, and a restart


def test_a_failed_write_stays_queued_and_goes_later_with_the_same_id_and_seq(rig: Rig) -> None:
    moved = _break_the_control_folder(rig)
    outbox_id = rig.messenger.send(FILED, to="barbara")
    row = get_outbox(rig.store, outbox_id)
    assert row.state == "queued"
    moved.rename(rig.control)
    rig.clock.advance(60)
    rig.messenger.check_receipts()
    again = get_outbox(rig.store, outbox_id)
    assert (again.state, again.seq) == ("written", row.seq)
    envelope = open_envelope(rig.outbox_files()[0].read_bytes(), key=KEY)
    assert (envelope.id, envelope.seq) == (outbox_id, row.seq)


def test_queued_rows_go_oldest_first_and_a_later_one_never_passes_a_failed_one(rig: Rig) -> None:
    moved = _break_the_control_folder(rig)
    first = rig.messenger.send(FILED, to="barbara")
    second = rig.messenger.send(FILED, to="barbara")
    moved.rename(rig.control)
    calls = []
    real = rig.messenger._write_row

    def fail_the_first_time(row, key, now):
        calls.append(row.id)
        return False if len(calls) == 1 else real(row, key, now)

    rig.messenger._write_row = fail_the_first_time
    rig.messenger.check_receipts()
    assert calls == [first]  # it stopped: the second never went ahead of the first
    assert get_outbox(rig.store, second).state == "queued"
    rig.messenger.check_receipts()
    assert calls == [first, first, second]
    assert get_outbox(rig.store, second).state == "written"


def test_at_restart_a_queued_row_whose_file_exists_is_marked_written_not_rewritten(
        rig: Rig) -> None:
    moved = _break_the_control_folder(rig)
    outbox_id = rig.messenger.send(FILED, to="barbara")
    moved.rename(rig.control)
    # the file got there, and the crash came before the row said so
    (rig.control / "outbox").mkdir()
    file = rig.control / "outbox" / f"{outbox_id}.json"
    file.write_bytes(b"the file from before the crash")
    rig.clock.advance(60)
    rig.new_messenger().check_receipts()
    assert get_outbox(rig.store, outbox_id).state == "written"
    assert file.read_bytes() == b"the file from before the crash"


def test_at_restart_a_queued_row_with_no_file_is_rewritten(rig: Rig) -> None:
    moved = _break_the_control_folder(rig)
    outbox_id = rig.messenger.send(FILED, to="barbara")
    moved.rename(rig.control)
    rig.new_messenger().check_receipts()
    assert get_outbox(rig.store, outbox_id).state == "written"
    assert rig.payload(outbox_id)["subject"] == FILED.title


def test_after_a_restore_a_queued_row_goes_under_a_new_number(rig: Rig) -> None:
    from ngrunner.store import jump_after_restore

    moved = _break_the_control_folder(rig)
    old_id = rig.messenger.send(FILED, to="barbara", ref="TES-1")
    moved.rename(rig.control)
    jump_after_restore(rig.store)
    rig.new_messenger().check_receipts()
    assert get_outbox(rig.store, old_id).state == "failed"
    fresh = list_outbox(rig.store, state="written")
    assert len(fresh) == 1 and fresh[0].ref == "TES-1"
    assert fresh[0].seq == last_sent_seq(rig.store, "outbox")
    assert open_envelope(rig.outbox_files()[0].read_bytes(), key=KEY).seq == fresh[0].seq


def test_with_no_signing_key_a_message_queues_with_a_board_item_and_goes_once_it_is_set(
        tmp_path: Path) -> None:
    rig = Rig(tmp_path, secrets={})
    outbox_id = rig.messenger.send(FILED, to="barbara")
    assert get_outbox(rig.store, outbox_id).state == "queued"
    assert [d.code for d in list_dev(rig.store)] == ["no_signing_key"]
    rig.secrets.put("signing_key", KEY_TEXT)
    rig.messenger.check_receipts()
    assert get_outbox(rig.store, outbox_id).state == "written"
    assert KEY_TEXT not in json.dumps([d.text for d in list_dev(rig.store)])


# ----- receipts


def test_a_receipt_marks_the_row_sent_and_the_tray_stays_clear(rig: Rig) -> None:
    outbox_id = rig.messenger.send(FILED, to="barbara")
    rig.clock.advance(120)
    rig.receipt(outbox_id)
    rig.messenger.check_receipts()
    row = get_outbox(rig.store, outbox_id)
    assert row.state == "sent" and row.receipt_at == rig.clock.now()
    rig.clock.advance(NO_RECEIPT)
    assert not rig.messenger.overdue()


def test_a_receipt_saying_the_send_failed_goes_to_dev_and_nothing_is_resent(rig: Rig) -> None:
    outbox_id = rig.messenger.send(FILED, to="barbara")
    rig.receipt(outbox_id, ok=False, error="the email could not be sent")
    rig.messenger.check_receipts()
    assert get_outbox(rig.store, outbox_id).state == "failed"
    dev = list_dev(rig.store)
    assert [d.code for d in dev] == ["send_failed"]
    assert "the email could not be sent" in dev[0].text
    rig.messenger.check_receipts()
    assert len(rig.outbox_files()) == 1 and len(list_outbox(rig.store)) == 1


def test_a_late_receipt_with_a_lower_seq_still_counts(rig: Rig) -> None:
    first = rig.messenger.send(FILED, to="barbara")
    second = rig.messenger.send(FILED, to="barbara")
    rig.receipt(second, seq=9000)
    rig.messenger.check_receipts()
    rig.receipt(first, seq=8000)  # lower, and later: Drive brings them in any order
    rig.messenger.check_receipts()
    assert get_outbox(rig.store, first).state == "sent"
    assert get_outbox(rig.store, second).state == "sent"


def test_a_badly_signed_receipt_never_counts_and_reaches_dev_only_if_it_stays(rig: Rig) -> None:
    outbox_id = rig.messenger.send(FILED, to="barbara")
    rig.receipt(outbox_id, key=b"\x01" * 32)
    rig.messenger.check_receipts()
    assert get_outbox(rig.store, outbox_id).state == "written"
    assert list_dev(rig.store) == []
    rig.clock.advance(timedelta(minutes=10))
    rig.messenger.check_receipts()
    assert [d.code for d in list_dev(rig.store)] == ["receipt_unreadable"]
    assert rig.messenger.overdue()


def test_a_half_synced_receipt_is_read_again_next_minute(rig: Rig) -> None:
    outbox_id = rig.messenger.send(FILED, to="barbara")
    (rig.control / "receipts").mkdir()
    (rig.control / "receipts" / f"{outbox_id}.json").write_bytes(b'{"body": "half')
    rig.messenger.check_receipts()
    assert get_outbox(rig.store, outbox_id).state == "written"
    rig.clock.advance(60)
    rig.receipt(outbox_id)
    rig.messenger.check_receipts()
    assert get_outbox(rig.store, outbox_id).state == "sent"
    assert list_dev(rig.store) == []


def test_a_receipt_filed_under_another_messages_name_is_refused(rig: Rig) -> None:
    first = rig.messenger.send(FILED, to="barbara")
    second = rig.messenger.send(FILED, to="barbara")
    rig.receipt(first)
    (rig.control / "receipts" / f"{second}.json").write_bytes(
        (rig.control / "receipts" / f"{first}.json").read_bytes())
    rig.messenger.check_receipts()
    assert get_outbox(rig.store, first).state == "sent"
    assert get_outbox(rig.store, second).state == "written"


def test_a_receipt_is_taken_once(rig: Rig) -> None:
    outbox_id = rig.messenger.send(FILED, to="barbara")
    rig.receipt(outbox_id)
    rig.messenger.check_receipts()
    first_at = get_outbox(rig.store, outbox_id).receipt_at
    rig.clock.advance(300)
    rig.messenger.check_receipts()
    assert get_outbox(rig.store, outbox_id).receipt_at == first_at


# ----- overdue: the red tray


def test_no_receipt_in_five_minutes_turns_the_tray_red(rig: Rig) -> None:
    outbox_id = rig.messenger.send(FILED, to="barbara")
    rig.clock.advance(NO_RECEIPT - timedelta(seconds=1))
    assert not rig.messenger.overdue()
    rig.clock.advance(1)
    assert rig.messenger.overdue()
    rig.receipt(outbox_id)
    rig.messenger.check_receipts()
    assert not rig.messenger.overdue()


def test_a_message_that_cannot_be_written_for_five_minutes_turns_the_tray_red(rig: Rig) -> None:
    _break_the_control_folder(rig)
    rig.messenger.send(FILED, to="barbara")
    assert not rig.messenger.overdue()
    rig.clock.advance(NO_RECEIPT)
    assert rig.messenger.overdue()
    rig.messenger.check_receipts()
    assert [d.code for d in list_dev(rig.store)] == ["outbox_write_failed"]


def test_a_lost_message_stops_holding_the_tray_red_after_a_day_and_is_not_resent(rig: Rig) -> None:
    outbox_id = rig.messenger.send(FILED, to="barbara")
    rig.clock.advance(GIVE_UP)
    assert rig.messenger.overdue()
    rig.messenger.check_receipts()
    assert get_outbox(rig.store, outbox_id).state == "failed"
    assert not rig.messenger.overdue()
    assert [d.code for d in list_dev(rig.store)] == ["receipt_lost"]
    assert len(rig.outbox_files()) == 1
    rig.receipt(outbox_id)  # a very late one changes nothing
    rig.messenger.check_receipts()
    assert get_outbox(rig.store, outbox_id).state == "failed"


# ----- urgent messages with no receipt (ruling 13, scenario 9)


URGENT = Message("filed", "Filed as Testerson_101426",
                 ("Testerson is filed as Testerson_101426.",), urgent=True)


class FakeReply:
    status_code = 200

    def json(self) -> dict:
        return {"status": 1}


class FakeHttp:
    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.fail = False

    def post(self, url, *, data, timeout):
        self.posts.append(data)
        if self.fail:
            raise OSError("no network")
        return FakeReply()


def test_without_pushover_keys_an_urgent_message_is_logged_and_opens_an_urgent_dev_item(
        rig: Rig, caplog: pytest.LogCaptureFixture) -> None:
    outbox_id = rig.messenger.send(URGENT, to="jim")
    rig.clock.advance(NO_RECEIPT)
    with caplog.at_level(logging.WARNING, logger="ngrunner.messenger"):
        rig.messenger.check_receipts()
    assert outbox_id in caplog.text
    assert "Testerson is filed" not in caplog.text  # never a message body (ruling 18)
    dev = list_dev(rig.store)
    assert [(d.code, d.urgent) for d in dev] == [("urgent_no_receipt", True)]
    assert rig.messenger.overdue()
    assert get_outbox(rig.store, outbox_id).fallback_at is None


def test_a_late_receipt_closes_the_urgent_item_that_made_the_tray_red(rig: Rig) -> None:
    outbox_id = rig.messenger.send(URGENT, to="jim")
    rig.clock.advance(NO_RECEIPT)
    rig.messenger.check_receipts()
    rig.receipt(outbox_id)
    rig.messenger.check_receipts()
    assert list_dev(rig.store) == [] and not rig.messenger.overdue()


def test_with_pushover_keys_an_urgent_message_goes_straight_there_once(tmp_path: Path) -> None:
    http = FakeHttp()
    rig = Rig(tmp_path, http=http, secrets={"signing_key": KEY_TEXT, "pushover_token": TOKEN,
                                            "pushover_user": USER})
    outbox_id = rig.messenger.send(URGENT, to="jim")
    rig.clock.advance(NO_RECEIPT)
    rig.messenger.check_receipts()
    assert rig.messenger.overdue()  # red either way
    assert get_outbox(rig.store, outbox_id).fallback_at == rig.clock.now()
    assert list_dev(rig.store) == []
    titles = [post["title"] for post in http.posts]
    assert titles == [URGENT.title, "NG Runner can't send messages"]
    rig.clock.advance(60)
    rig.messenger.check_receipts()
    assert len(http.posts) == 2  # each once, and cant_send once an outage


def test_a_direct_send_that_fails_opens_the_urgent_item_and_tries_again(tmp_path: Path) -> None:
    http = FakeHttp()
    http.fail = True
    rig = Rig(tmp_path, http=http, secrets={"signing_key": KEY_TEXT, "pushover_token": TOKEN,
                                            "pushover_user": USER})
    outbox_id = rig.messenger.send(URGENT, to="jim")
    rig.clock.advance(NO_RECEIPT)
    rig.messenger.check_receipts()
    assert [d.code for d in list_dev(rig.store)] == ["urgent_no_receipt"]
    http.fail = False
    rig.clock.advance(60)
    rig.messenger.check_receipts()
    assert get_outbox(rig.store, outbox_id).fallback_at == rig.clock.now()
    assert list_dev(rig.store) == []


def test_the_direct_path_keeps_quiet_hours_except_do_not_cut(tmp_path: Path) -> None:
    http = FakeHttp()
    rig = Rig(tmp_path, http=http, now=NIGHT, secrets={"signing_key": KEY_TEXT,
                                                       "pushover_token": TOKEN,
                                                       "pushover_user": USER})
    urgent = rig.messenger.send(URGENT, to="jim")
    rig.clock.advance(NO_RECEIPT)
    rig.messenger.check_receipts()
    assert http.posts == []  # cant_send included
    rig.clock.set(datetime(2026, 10, 31, 13, 0, tzinfo=timezone.utc))  # 6 AM
    rig.messenger.check_receipts()
    assert [post["title"] for post in http.posts][0] == URGENT.title
    assert get_outbox(rig.store, urgent).fallback_at is not None
    assert not rig.messenger._phone_waits("DO NOT CUT Testerson_101426", NIGHT)
    assert not rig.messenger._phone_waits("SHADOW DO NOT CUT Testerson_101426", NIGHT)


# ----- shadow mode


def test_in_shadow_the_direct_path_never_rings_jims_phone(tmp_path: Path) -> None:
    http = FakeHttp()
    rig = Rig(tmp_path, mode="shadow", http=http, secrets={
        "signing_key": KEY_TEXT, "pushover_token": TOKEN, "pushover_user": USER})
    rig.messenger.send(URGENT, to="jim")
    rig.clock.advance(NO_RECEIPT)
    rig.messenger.check_receipts()
    assert http.posts == []
    assert [(d.code, d.urgent) for d in list_dev(rig.store)] == [("urgent_no_receipt", True)]
    assert rig.messenger.overdue()


def test_shadow_redirects_to_sulaiman_marked_and_jim_and_barbara_get_nothing(
        tmp_path: Path) -> None:
    rig = Rig(tmp_path, mode="shadow")
    outbox_id = rig.messenger.send(FILED, to="barbara", ref="TES-1")
    payload = rig.payload(outbox_id)
    assert payload["to"] == "sulaiman" and payload["channel"] == "email"
    assert payload["subject"] == "SHADOW Filed as Testerson_101426"
    assert payload["lines"] == list(FILED.lines) and payload["ref"] == "TES-1"
    row = get_outbox(rig.store, outbox_id)
    assert row.recipient == "sulaiman" and row.subject.startswith("SHADOW ")


def test_a_shadow_digest_keeps_its_ref_so_the_apps_script_counts_it(tmp_path: Path) -> None:
    rig = Rig(tmp_path, mode="shadow")
    outbox_id = rig.messenger.send(t.quiet_digest(NOON.date()), to="jim")
    payload = rig.payload(outbox_id)
    assert payload["to"] == "sulaiman" and payload["ref"] == "digest:2026-10-30"


def test_a_message_already_for_sulaiman_is_not_marked(tmp_path: Path) -> None:
    rig = Rig(tmp_path, mode="shadow")
    outbox_id = rig.messenger.send(FILED, to="sulaiman")
    assert rig.payload(outbox_id)["subject"] == FILED.title


def test_the_redirection_comes_before_the_lint_and_the_replacement_stays_marked(
        tmp_path: Path) -> None:
    rig = Rig(tmp_path, mode="shadow")
    bad = Message("filed", "Filed as Testerson_101426", ("Call 310-555-0100 about it.",))
    outbox_id = rig.messenger.send(bad, to="barbara")
    assert rig.payload(outbox_id)["subject"] == f"SHADOW {t.FIXED['filed'][0]}"


def test_a_redirected_message_goes_at_once_even_at_night(tmp_path: Path) -> None:
    rig = Rig(tmp_path, mode="shadow", now=NIGHT)
    outbox_id = rig.messenger.send(FILED, to="barbara")
    assert get_outbox(rig.store, outbox_id).state == "written"


def test_switching_to_live_takes_effect_at_the_next_send(tmp_path: Path) -> None:
    from dataclasses import replace

    rig = Rig(tmp_path, mode="shadow")
    rig.messenger.settings = replace(rig.settings, mode="live")
    outbox_id = rig.messenger.send(FILED, to="barbara")
    assert rig.payload(outbox_id)["to"] == "barbara"


# ----- quiet hours


@pytest.mark.parametrize(("hour", "minute", "quiet"), [
    (21, 59, False), (22, 0, True), (23, 30, True), (0, 0, True), (5, 59, True), (6, 0, False),
    (12, 0, False),
])
def test_quiet_hours_are_10_pm_to_6_am_pacific(tmp_path: Path, hour: int, minute: int,
                                               quiet: bool) -> None:
    rig = Rig(tmp_path)
    from ngrunner.clock import PACIFIC

    wall = datetime(2026, 10, 30, hour, minute, tzinfo=PACIFIC)
    assert in_quiet_hours(wall, rig.settings) is quiet


def test_who_waits_for_morning(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    def waits(title: str, to: str, channel: str) -> bool:
        return waits_for_morning(title, to=to, channel=channel, now=NIGHT, settings=rig.settings)

    assert waits("Filed as Testerson_101426", "barbara", "email")
    assert waits("NG Runner can't send messages", "jim", "pushover")
    assert waits("Anything", "jim", "slack")
    assert not waits("DO NOT CUT Testerson_101426", "jim", "pushover")
    assert not waits("SHADOW DO NOT CUT Testerson_101426", "jim", "pushover")
    assert not waits("Your list", "jim", "email")
    assert not waits("A DEV line", "sulaiman", "email")
    assert waits("Order heads-up", "will", "email")
    assert waits("Nest checked", "george", "email")
    assert not waits("DO NOT CUT Testerson_101426", "george", "email")
    assert not waits("SHADOW DO NOT CUT Testerson_101426", "george", "email")
    assert not waits("Cleared by Jim · Testerson_101426", "george", "email")
    assert not waits("No longer DO NOT CUT · Testerson_101426", "george", "email")
    assert waits("Cleared by Jim · Testerson_101426", "jim", "pushover")


def test_barbaras_email_waits_out_the_night_with_no_row_and_keeps_its_id(tmp_path: Path) -> None:
    rig = Rig(tmp_path, now=NIGHT)
    outbox_id = rig.messenger.send(FILED, to="barbara", ref="TES-1")
    assert get_outbox(rig.store, outbox_id) is None and rig.outbox_files() == []
    assert not rig.messenger.overdue()
    rig.clock.advance(timedelta(hours=1))
    rig.messenger.check_receipts()
    assert rig.outbox_files() == [] and not rig.messenger.overdue()
    rig.clock.set(datetime(2026, 10, 31, 13, 0, tzinfo=timezone.utc))  # 6 AM
    rig.messenger.check_receipts()
    row = get_outbox(rig.store, outbox_id)
    assert row.state == "written" and row.ref == "TES-1" and rig.messenger.held() == []
    assert rig.payload(outbox_id)["to"] == "barbara"


def test_a_message_sent_during_the_night_takes_no_number_a_held_one_would_fall_behind(
        tmp_path: Path) -> None:
    rig = Rig(tmp_path, now=NIGHT)
    held = rig.messenger.send(FILED, to="barbara")
    night_line = rig.messenger.send(FILED, to="sulaiman")
    assert get_outbox(rig.store, night_line).state == "written"
    rig.clock.set(datetime(2026, 10, 31, 13, 0, tzinfo=timezone.utc))
    rig.messenger.check_receipts()
    # The Apps Script refuses a seq below the last it accepted, so the held one must be above it.
    assert get_outbox(rig.store, held).seq > get_outbox(rig.store, night_line).seq


def test_a_held_message_survives_a_restart(tmp_path: Path) -> None:
    rig = Rig(tmp_path, now=NIGHT)
    outbox_id = rig.messenger.send(FILED, to="barbara")
    rig.clock.set(datetime(2026, 10, 31, 13, 0, tzinfo=timezone.utc))
    rig.new_messenger().check_receipts()
    assert get_outbox(rig.store, outbox_id).state == "written"


def test_a_switch_back_to_shadow_in_the_night_still_keeps_a_held_message_from_barbara(
        tmp_path: Path) -> None:
    from dataclasses import replace

    rig = Rig(tmp_path, now=NIGHT)
    outbox_id = rig.messenger.send(FILED, to="barbara")
    rig.messenger.settings = replace(rig.settings, mode="shadow")
    rig.clock.set(datetime(2026, 10, 31, 13, 0, tzinfo=timezone.utc))
    rig.messenger.check_receipts()
    payload = rig.payload(outbox_id)
    assert payload["to"] == "sulaiman" and payload["subject"].startswith("SHADOW ")


def test_jims_email_and_a_do_not_cut_go_through_the_night(tmp_path: Path) -> None:
    rig = Rig(tmp_path, now=NIGHT)
    jims = rig.messenger.send(t.quiet_digest(NOON.date()), to="jim")
    assert get_outbox(rig.store, jims).state == "written"
    urgent = Message("do_not_cut", "DO NOT CUT Testerson_101426",
                     ("Testerson_101426 changed after it was checked.",), urgent=True)
    assert get_outbox(rig.store, rig.messenger.send(urgent, to="jim", channel="pushover"))


# ----- toasts


class FakeBackend:
    def __init__(self) -> None:
        self.shown: list[ToastMessage] = []

    def show(self, message: ToastMessage, on_choice) -> None:
        self.shown.append(message)


def _card(kind: str = "which_job") -> Card:
    return Card(id="TES-1", kind=kind, job=None, row="Testerson", text="Two jobs could be this row.",
                choices=("Testerson_101426", "None of these"), created_at=NOON,
                choice_ids=("123", None))


def _toasting(tmp_path: Path, mode: str = "live") -> tuple[Rig, FakeBackend]:
    backend = FakeBackend()
    rig = Rig(tmp_path, mode=mode)
    rig.messenger._toaster = Toaster(clock=rig.clock, backend=backend)
    return rig, backend


def test_a_which_job_card_toasts_at_once_with_its_choices_as_buttons(tmp_path: Path) -> None:
    rig, backend = _toasting(tmp_path)
    assert rig.messenger.toast_card(_card()) == "shown"
    assert rig.messenger.toast_card(_card()) == "shown"  # immediate: no rate rule
    toast = backend.shown[0]
    assert toast.kind == "which_job" and toast.lines == ("Two jobs could be this row.",)
    assert [c.argument for c in toast.choices] == ["TES-1 Testerson_101426", "TES-1 None of these"]
    assert rig.outbox_files() == []  # a card never goes through the outbox to Jim


def test_other_card_kinds_wait_for_the_digest(tmp_path: Path) -> None:
    rig, backend = _toasting(tmp_path)
    assert rig.messenger.toast_card(_card("fetch_failed")) is None
    assert backend.shown == []


def test_ordinary_toasts_keep_the_rate_rule(tmp_path: Path) -> None:
    rig, backend = _toasting(tmp_path)
    morning = Message("morning_list", "Your list", ("Nothing is waiting on you.",))
    assert rig.messenger.toast(morning) == "shown"
    rig.clock.advance(timedelta(minutes=9))
    assert rig.messenger.toast(morning) == "held"
    rig.clock.advance(timedelta(minutes=1))
    assert rig.messenger.toast(morning) == "shown"
    assert len(backend.shown) == 2


def test_no_toast_shows_in_shadow_mode(tmp_path: Path) -> None:
    rig, backend = _toasting(tmp_path, mode="shadow")
    assert rig.messenger.toast_card(_card()) is None
    assert rig.messenger.toast(Message("morning_list", "Your list", ("Nothing waits.",))) is None
    assert backend.shown == []


def test_a_toast_that_fails_the_lint_shows_the_fixed_words_with_a_dev_item(tmp_path: Path) -> None:
    rig, backend = _toasting(tmp_path)
    bad = Card(id="TES-1", kind="which_job", job=None, row="Testerson",
               text="Call 310-555-0100 about it.", choices=("None of these",), created_at=NOON,
               choice_ids=(None,))
    assert rig.messenger.toast_card(bad) == "shown"
    assert backend.shown[0].title == t.FIXED["card"][0] and backend.shown[0].choices == ()
    assert [d.code for d in list_dev(rig.store)] == ["message_replaced"]


def test_with_no_toaster_nothing_is_asked(rig: Rig) -> None:
    assert rig.messenger.toast_card(_card()) is None


# ----- odds and ends


def test_a_used_stream_carries_on_whatever_the_clock_says(rig: Rig) -> None:
    last = next_seq(rig.store, "outbox", start=7)
    outbox_id = rig.messenger.send(FILED, to="barbara")
    assert get_outbox(rig.store, outbox_id).seq == last + 1


def test_a_new_id_is_never_a_receipt_for_a_message_the_store_does_not_hold(rig: Rig) -> None:
    # Ruling 14: only outstanding rows are looked up, so a stray file is never read.
    rig.receipt(new_id())
    rig.messenger.check_receipts()
    assert list_dev(rig.store) == []
