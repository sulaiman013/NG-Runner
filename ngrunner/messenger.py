r"""
The messenger: how a message leaves NG Runner, how its receipt comes back, and what happens when
none does (integration spec section 6; design section 4, "Silence and failure"; rulings 13-16, 22
and 25).

Why it is one class: every rule about a message has to hold for all of them. NG Runner never
sends mail itself. It leaves a signed file in control\outbox through the write chokepoint, the
Apps Script sends it and writes control\receipts\<id>.json back, and a message with no receipt
after 5 minutes turns the tray red (overdue()), whoever sent it and why.

The order of a send is fixed. In shadow mode the message is redirected to Sulaiman and marked
SHADOW first, so he sees what would have gone out. Then vetted(), with a DEV item if it replaced
the message. Then, in one transaction, next_seq, outbox_file and add_outbox. Then commit_recorded
and mark_outbox_written. A message whose write fails stays queued with its id and seq, and
check_receipts() writes it again, oldest first, so the Apps Script never sees a gap it would give
up on. For the same reason a message waiting out quiet hours has no row yet: it would hold a seq
that later messages pass, and the Apps Script refuses a number below the last it accepted. It
waits in the state table, in order, with the id send() returned, and gets its seq when it goes.

Nothing is ever sent twice. A receipt that says the send failed goes to DEV with its error, and
nothing is resent. The direct path (messages.pushover) is for urgent messages only, once its keys
exist, which in P1a they don't; without them the message is logged and an urgent DEV item opens.

Logs here name a message by its id and ref, never its title or lines (ruling 18).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ngrunner.clock import Clock, pacific, parse_wall, to_iso
from ngrunner.config import AppPaths, Settings
from ngrunner.messages import Message, pushover
from ngrunner.messages.outbox import (
    FOLDER, Receipt, ReceiptError, outbox_file, read_receipt,
)
from ngrunner.messages.templates import SHADOW, cant_send, card_message, shadow, vetted
from ngrunner.secrets import Secrets, SecretsError
from ngrunner.signing import EnvelopeError, key_from_text, new_id
from ngrunner.store import (
    OutboxRecord, Store, StoreError, add_outbox, close_dev, get_state, last_sent_seq, list_dev,
    list_outbox, mark_outbox_fallback, mark_outbox_written, next_seq, open_dev,
    outbox_without_receipt, record_receipt, seen_id, set_state,
)
from ngrunner.toasts import IMMEDIATE_KINDS, Choice, ToastMessage, ToastResult
from ngrunner.writes import CommitResult, FileWrite, Roots, commit_recorded

if TYPE_CHECKING:
    from ngrunner.cards import Card
    from ngrunner.toasts import Toaster

STREAM = "outbox"                     # NG Runner's outgoing sequence, and the Apps Script's name
RECEIPTS = "receipts"                 # in the control folder
RECEIPT_STREAM = "receipt"            # the seen-id guard: receipts are exempt from the seq rule
NO_RECEIPT = timedelta(minutes=5)     # then the tray turns red
UNREADABLE = timedelta(minutes=10)    # a half-synced file gets two export intervals, as Fab's do
GIVE_UP = timedelta(hours=24)         # then a lost message stops holding the tray red
PHONE_CHANNELS = ("pushover", "slack")
ROLE_BARBARA = "barbara"
ROLE_SULAIMAN = "sulaiman"
DO_NOT_CUT = "DO NOT CUT"             # the one exception to quiet hours (P1b on)
_HELD = "messenger.held"              # state key: messages waiting out quiet hours
# A stream counts a few a day, so only a restore's jump of 1,000,000 puts a queued row this far
# behind it, and the Apps Script would refuse its number with no receipt.
_RESTORE_GAP = 500_000
# outbox_file checks the role, channel and ref and settles the id and ref when the row is made;
# its bytes are thrown away, since a file is signed when it is written. So a message can queue
# with no key yet, and go when the key is there.
_NOT_SIGNED = bytes(32)
_ANY_WORDS = Message("outbox", "NG Runner", ("NG Runner has a message for you on the board.",))
_log = logging.getLogger(__name__)


# ----- quiet hours


def in_quiet_hours(now: datetime, settings: Settings) -> bool:
    """Whether Pacific wall time is inside settings' quiet hours (10 PM to 6 AM by default). The
    same start and end means no quiet hours."""
    wall = pacific(now).time()
    start, end = parse_wall(settings.quiet_from), parse_wall(settings.quiet_until)
    return start <= wall < end if start <= end else wall >= start or wall < end


def waits_for_morning(title: str, *, to: str, channel: str, now: datetime,
                      settings: Settings) -> bool:
    """Ruling 25: a message for a phone, or Barbara's email, waits out quiet hours, cant_send
    included. Only DO NOT CUT goes through. Jim's and Sulaiman's email never waits."""
    if title.removeprefix(f"{SHADOW} ").startswith(DO_NOT_CUT):
        return False
    if channel not in PHONE_CHANNELS and to != ROLE_BARBARA:
        return False
    return in_quiet_hours(now, settings)


# ----- the messenger


class Messenger:
    """Sends messages and reads their receipts. Safe to call from any thread; every step is keyed
    by store rows, so a new Messenger on the same store carries on where the last one stopped.

    settings is read at each send, so the supervisor assigns messenger.settings when the mode
    changes. http stands in for a requests session on the direct path; toaster is the supervisor's
    toasts.Toaster, which keeps the rate rule."""

    def __init__(self, store: Store, settings: Settings, secrets: Secrets, clock: Clock,
                 roots: Roots, paths: AppPaths, *, http: Any = None,
                 toaster: Toaster | None = None) -> None:
        self.settings = settings
        self._store, self._secrets, self._clock = store, secrets, clock
        self._roots, self._paths = roots, paths
        self._http, self._toaster = http, toaster
        self._writing = threading.Lock()              # one writer of queued rows, in seq order
        self._unreadable: dict[str, datetime] = {}    # receipt files that don't verify yet
        self._told: set[str] = set()                  # rows logged as silent (once each)
        self._cant_send_told = False                  # the direct "can't send", once per outage

    # ----- sending

    def send(self, message: Message, *, to: str, channel: str = "email",
             ref: str | None = None) -> str:
        """Send message to a role. Returns its outbox id, which its receipt will name. It is never
        lost: a write that fails stays queued for check_receipts(), and a message that must wait
        out quiet hours goes once they end. The row (get_outbox) exists from then on."""
        now = self._clock.now()
        message, to, channel = self._redirected(message, to, channel)
        if waits_for_morning(message.title, to=to, channel=channel, now=now,
                             settings=self.settings):
            return self._hold(message, to, channel, ref, now)
        outbox_id = self._add_row(message, to, channel, ref, now)
        self._write_queued_quietly(now)
        return outbox_id

    def _redirected(self, message: Message, to: str, channel: str) -> tuple[Message, str, str]:
        """In shadow mode, what would have gone to someone else goes to Sulaiman, marked. By
        email: it is the channel his role always has, and a phone channel would fail."""
        if self.settings.mode == "shadow" and to != ROLE_SULAIMAN:
            return shadow(message), ROLE_SULAIMAN, "email"
        return message, to, channel

    def _add_row(self, message: Message, to: str, channel: str, ref: str | None, now: datetime,
                 *, id: str | None = None) -> str:
        """Ruling 16's first two steps: vetted, then next_seq, outbox_file and add_outbox in one
        transaction. id: the id a held message was promised, and leaves the hold with."""
        checked = vetted(message, now=now)
        if checked.replaced:
            self._replaced(message.kind, checked.problems, now)
        with self._store.transaction() as tx:
            seq = next_seq(tx, STREAM, start=int(now.timestamp()))
            made = outbox_file(checked.message, to=to, channel=channel, key=_NOT_SIGNED, seq=seq,
                               now=now, ref=ref, id=id)
            add_outbox(tx, id=made.id, seq=seq, channel=channel, recipient=to,
                       subject=checked.message.title, lines=checked.message.lines,
                       urgent=checked.message.urgent, created_at=now, ref=made.ref)
            if id is not None:
                held = [entry for entry in _loads(get_state(tx, _HELD)) if entry["id"] != id]
                set_state(tx, _HELD, json.dumps(held))
        return made.id

    def _replaced(self, kind: str, problems: tuple[str, ...], now: datetime) -> None:
        open_dev(self._store, at=now, code="message_replaced",
                 text=f"A {kind} message failed the message check and went as its fixed template:"
                      f" {'; '.join(problems)}")

    # ----- quiet hours: held with no row, so its seq can't fall behind later messages

    def _hold(self, message: Message, to: str, channel: str, ref: str | None,
              now: datetime) -> str:
        # Its id now, and the refusals a row would get; its words are vetted when it goes.
        made = outbox_file(_ANY_WORDS, to=to, channel=channel, key=_NOT_SIGNED, seq=1, now=now,
                           ref=ref)
        entry = {"id": made.id, "kind": message.kind, "title": message.title,
                 "lines": list(message.lines), "urgent": message.urgent, "to": to,
                 "channel": channel, "ref": ref, "at": to_iso(now)}
        with self._store.transaction() as tx:
            set_state(tx, _HELD, json.dumps([*_loads(get_state(tx, _HELD)), entry]))
        return made.id

    def held(self) -> list[dict[str, Any]]:
        """The messages waiting out quiet hours, oldest first, each as its plain fields."""
        return _loads(get_state(self._store, _HELD))

    def _release_held(self, now: datetime) -> None:
        """A switch back to shadow during the night still keeps Barbara out of it."""
        for entry in self.held():
            if waits_for_morning(entry["title"], to=entry["to"], channel=entry["channel"], now=now,
                                 settings=self.settings):
                continue
            message, to, channel = self._redirected(
                Message(entry["kind"], entry["title"], tuple(entry["lines"]), entry["urgent"]),
                entry["to"], entry["channel"])
            self._add_row(message, to, channel, entry["ref"], now, id=entry["id"])

    # ----- writing: oldest first, stopping at the first that fails

    def _write_queued_quietly(self, now: datetime) -> None:
        """A row exists, so nothing is lost: a store too busy to write now retries next minute."""
        try:
            self._write_queued(now)
        except StoreError as exc:
            _log.warning("Queued messages could not be written yet: %s", exc)

    def _write_queued(self, now: datetime) -> None:
        with self._writing:
            for row in list_outbox(self._store, state="queued"):
                if os.path.lexists(self._file_of(row.id)):  # written before a crash or restart
                    mark_outbox_written(self._store, row.id, at=now)
                    continue
                key = self._key(now)
                if key is None or not self._write_row(self._renumbered(row, now), key, now):
                    return  # the later ones wait, or the Apps Script gives up on this one's seq

    def _renumbered(self, row: OutboxRecord, now: datetime) -> OutboxRecord:
        """After a restore every stream has jumped, so the Apps Script would refuse a row queued
        before it, with no receipt. It goes under a new id and seq; the old row is closed."""
        if last_sent_seq(self._store, STREAM) - row.seq < _RESTORE_GAP:
            return row
        with self._store.transaction() as tx:
            fresh = add_outbox(
                tx, id=new_id(), seq=next_seq(tx, STREAM, start=int(now.timestamp())),
                channel=row.channel, recipient=row.recipient, subject=row.subject,
                lines=row.lines, urgent=row.urgent, created_at=row.created_at, ref=row.ref)
            record_receipt(tx, row.id, at=now, ok=False)
        _log.info("Outbox %s was queued before a restore; it goes as %s.", row.id, fresh.id)
        return fresh

    def _write_row(self, row: OutboxRecord, key: bytes, now: datetime) -> bool:
        """Its own id and seq, signed now. The lint runs again in case this version's is
        stricter than the one that queued the row."""
        checked = vetted(_as_message(row), now=now)
        if checked.replaced:
            self._replaced("queued", checked.problems, now)
        made = outbox_file(checked.message, to=row.recipient, channel=row.channel, key=key,
                           seq=row.seq, now=row.created_at, ref=row.ref, id=row.id)
        result = self._commit(self._file_of(row.id), made.data)
        if result.ok:
            mark_outbox_written(self._store, row.id, at=self._clock.now())
            return True
        _log.warning("Outbox %s was not written, and stays queued: %s", row.id, result.reason)
        if now - row.created_at >= NO_RECEIPT:
            open_dev(self._store, at=now, code="outbox_write_failed",
                     text=f"Messages can't be written to the control folder: {result.reason}")
        return False

    def _commit(self, path: Path, data: bytes) -> CommitResult:
        return commit_recorded([FileWrite(path, data, None)], store=self._store, clock=self._clock,
                               roots=self._roots, interlock=self._only_the_outbox)

    def _only_the_outbox(self, paths: list[Path]) -> str | None:
        """The interlock for messages. A pause stops filing and never a message (ruling 12a), so
        the only refusal is a target that isn't directly in the control folder's outbox."""
        for path in paths:
            try:
                inside = path.parent.name == FOLDER and os.path.samefile(path.parent.parent,
                                                                         self._roots.control)
            except OSError:
                inside = False
            if not inside:
                return f"{path.name} is not in the control folder's outbox, so it was not written."
        return None

    def _file_of(self, outbox_id: str) -> Path:
        return Path(self._roots.control) / FOLDER / f"{outbox_id}.json"

    def _key(self, now: datetime) -> bytes | None:
        """The signing key, or None with a board item: never a crash, and never its value."""
        try:
            text = self._secrets.get("signing_key")
            if text:
                return key_from_text(text)
            problem = "NG Runner has no signing key yet, so messages wait. Run its setup."
        except (SecretsError, EnvelopeError) as exc:
            problem = f"The signing key can't be used, so messages wait: {exc}."
        open_dev(self._store, at=now, code="no_signing_key", text=problem)
        return None

    # ----- receipts

    def check_receipts(self) -> None:
        """Every minute (and at restart): messages whose quiet hours ended go, queued rows are
        written or marked written, each outstanding row's receipt is looked up, and silence is
        acted on."""
        now = self._clock.now()
        with self._writing:
            self._release_held(now)
        self._write_queued(now)
        waiting = list_outbox(self._store, state="written")
        key = self._key(now) if waiting else None
        if key is not None:
            for row in waiting:
                self._look_for_receipt(row, key, now)
        self._act_on_silence(now)

    def _look_for_receipt(self, row: OutboxRecord, key: bytes, now: datetime) -> None:
        """Ruling 14: receipts/<id>.json per outstanding row, never a listing, since Drive brings
        them in any order and a late one with a lower seq still counts."""
        try:
            data = (Path(self._roots.control) / RECEIPTS / f"{row.id}.json").read_bytes()
        except FileNotFoundError:
            return
        except OSError as exc:
            _log.info("The receipt for outbox %s could not be read yet: %s", row.id, exc)
            return
        try:
            receipt = read_receipt(data, key=key)
            if receipt.outbox_id != row.id:
                raise ReceiptError("the receipt names a different message")
        except EnvelopeError as exc:
            self._cant_read(row, exc, now)
            return
        self._unreadable.pop(row.id, None)
        self._take(row, receipt, now)

    def _cant_read(self, row: OutboxRecord, exc: EnvelopeError, now: datetime) -> None:
        """It may be half-synced, so it gets the next read; DEV only if it stays that way. It
        never counts as a receipt, so the row stays outstanding."""
        first = self._unreadable.setdefault(row.id, now)
        if now - first >= UNREADABLE:
            open_dev(self._store, at=now, code="receipt_unreadable",
                     text=f"A receipt was ignored, for a message about {row.ref}: {exc}.")

    def _take(self, row: OutboxRecord, receipt: Receipt, now: datetime) -> None:
        try:
            with self._store.transaction() as tx:
                if not seen_id(tx, RECEIPT_STREAM, receipt.id):
                    return
                record_receipt(tx, receipt.outbox_id, at=receipt.sent_at, ok=receipt.ok)
                if not receipt.ok:
                    open_dev(tx, at=now, code="send_failed",
                             text=f"A message to {row.recipient} about {row.ref} was not sent:"
                                  f" {receipt.error or 'no reason given'}.")
        except LookupError:  # a message the store doesn't hold, after a restore: ignored
            return
        self._told.discard(row.id)
        if row.urgent:
            self._close_urgent_item(row)

    # ----- silence

    def overdue(self) -> bool:
        """Whether the tray turns red: a message with no receipt 5 minutes after it was written,
        or one that still can't be written 5 minutes after it was made."""
        return bool(self._overdue_rows(self._clock.now()))

    def _overdue_rows(self, now: datetime) -> list[OutboxRecord]:
        late = outbox_without_receipt(self._store, written_before=now - NO_RECEIPT)
        stuck = [row for row in list_outbox(self._store, state="queued")
                 if row.created_at <= now - NO_RECEIPT]
        return sorted(late + stuck, key=lambda row: row.seq)

    def _act_on_silence(self, now: datetime) -> None:
        """Ruling 13: with the Pushover keys an urgent message goes straight there, outside quiet
        hours; without them it is logged and an urgent DEV item opens. The tray is red either way,
        through overdue()."""
        rows = []
        for row in self._overdue_rows(now):
            if row.written_at is not None and now - row.written_at >= GIVE_UP:
                self._give_up(row, now)
            else:
                rows.append(row)
        if not rows:
            self._cant_send_told = False
            return
        for row in rows:
            if row.id not in self._told:
                self._told.add(row.id)
                _log.warning("Outbox %s about %s has no receipt after 5 minutes.", row.id, row.ref)
        keys = self._pushover_keys()
        for row in rows:
            if row.urgent and row.fallback_at is None:
                self._send_direct(row, keys, now)
        if keys is not None:
            self._tell_cant_send(keys, rows, now)

    def _send_direct(self, row: OutboxRecord, keys: tuple[str, str] | None, now: datetime) -> None:
        if keys is None:
            self._raise_urgent(row, now)
        elif not self._phone_waits(row.subject, now):
            if self._ring(keys, row.subject, "\n".join(row.lines)):
                mark_outbox_fallback(self._store, row.id, at=now)
                self._close_urgent_item(row)
            else:
                self._raise_urgent(row, now)

    def _phone_waits(self, title: str, now: datetime) -> bool:
        return waits_for_morning(title, to="jim", channel="pushover", now=now,
                                 settings=self.settings)

    def _pushover_keys(self) -> tuple[str, str] | None:
        """Both keys, or None. In shadow mode always None: the direct path reaches Jim's phone,
        and in shadow he gets nothing."""
        if self.settings.mode == "shadow":
            return None
        try:
            token, user = self._secrets.get("pushover_token"), self._secrets.get("pushover_user")
        except SecretsError as exc:
            _log.warning("The Pushover keys could not be read: %s", exc)
            return None
        return (token, user) if token and user else None

    def _ring(self, keys: tuple[str, str], title: str, text: str) -> bool:
        if self._http is None:
            import requests  # here: the direct path runs only once its keys are added

            self._http = requests.Session()
        return pushover.send(keys[0], keys[1], title, text, urgent=True, http=self._http)

    def _tell_cant_send(self, keys: tuple[str, str], rows: list[OutboxRecord],
                        now: datetime) -> None:
        """The urgent "NG Runner can't send messages", once an outage, straight to the phone."""
        if self._cant_send_told or self._phone_waits("", now):
            return
        confirmed = [row.receipt_at for row in list_outbox(self._store, state="sent")
                     if row.receipt_at is not None]
        since = max(confirmed, default=rows[0].written_at or rows[0].created_at)
        checked = vetted(cant_send(since=since, now=now, waiting=len(rows)), now=now)
        self._cant_send_told = self._ring(keys, checked.message.title, checked.message.body)

    def _urgent_text(self, row: OutboxRecord) -> str:
        return (f"An urgent message about {row.ref} has no receipt after 5 minutes, and there is"
                " no other way to send it.")

    def _raise_urgent(self, row: OutboxRecord, now: datetime) -> None:
        open_dev(self._store, at=now, code="urgent_no_receipt", text=self._urgent_text(row),
                 urgent=True)

    def _close_urgent_item(self, row: OutboxRecord) -> None:
        """No screen closes a DEV item in P1a, so the one that made the tray red closes itself."""
        for item in list_dev(self._store):
            if item.code == "urgent_no_receipt" and item.text == self._urgent_text(row):
                close_dev(self._store, item.id)

    def _give_up(self, row: OutboxRecord, now: datetime) -> None:
        """A message the Apps Script lost, or refused as out of order, never gets a receipt, and
        no screen can clear it, so one lost message must not hold the tray red for good. After
        24 hours it counts as failed, in DEV. Nothing is resent. A real outage stays red through
        the next day's messages."""
        with self._store.transaction() as tx:
            record_receipt(tx, row.id, at=now, ok=False)
            open_dev(tx, at=now, code="receipt_lost",
                     text=f"A message to {row.recipient} about {row.ref} had no receipt in 24"
                          " hours, so it counts as not sent.")
        self._told.discard(row.id)
        if row.urgent:
            self._close_urgent_item(row)

    # ----- toasts

    def toast_card(self, card: Card) -> ToastResult | None:
        """A card reaches Jim by board, toast and digest, never the outbox (docs/decisions.md).
        Only the which-job and closet-or-garage kinds pop up (toasts.IMMEDIATE_KINDS); the others
        wait for the digest. Each button hands back "<card id> <choice>", the command box's form."""
        if card.kind not in IMMEDIATE_KINDS:
            return None
        now = self._clock.now()
        checked = vetted(card_message(card), now=now)
        if checked.replaced:
            self._replaced("card", checked.problems, now)
            return self._notify(ToastMessage(card.kind, checked.message.title,
                                             checked.message.lines))
        return self._notify(ToastMessage(
            card.kind, checked.message.title, (card.text,),
            tuple(Choice(label, f"{card.id} {label}") for label in card.choices)))

    def toast(self, message: Message) -> ToastResult | None:
        """Any other toast (the morning list): its words through the lint, then the rate rule."""
        now = self._clock.now()
        checked = vetted(message, now=now)
        if checked.replaced:
            self._replaced(message.kind, checked.problems, now)
        return self._notify(ToastMessage(message.kind, checked.message.title,
                                         checked.message.lines))

    def _notify(self, toast: ToastMessage) -> ToastResult | None:
        """Through toasts.Toaster, which keeps the rate rule: immediate kinds at once, others at
        most one per 10 minutes. None when nothing was asked of it: in shadow mode no toast shows
        on Jim's PC (ruling 22), and a start with no toaster has none to show."""
        if self.settings.mode == "shadow" or self._toaster is None:
            return None
        return self._toaster.notify(toast)


# ----- small pieces


def _loads(text: str | None) -> list[dict[str, Any]]:
    return json.loads(text) if text else []


def _as_message(row: OutboxRecord) -> Message:
    """A row as a message again (M3): its kind is gone, and its ref keeps what a kind decided."""
    return Message("outbox", row.subject, row.lines, row.urgent)
