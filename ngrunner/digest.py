"""
The 6:30 AM digest: the one morning message that sums up everything touched overnight.

Jim reads it on his phone before anything else, so it is owner-first (design section 4,
"Timing"): what needs him (jobs installing soon, alarms) on one For you line, then one numbered
line per job touched since the last digest, then the open cards that come back today, at 1, 3
and 7 days, with their age, then Your list. A quiet night gets one line, never silence: the
digest's arrival is itself the sign that NG Runner ran. compose() only arranges; the words are
messages.templates', and the facts are wave 2's job model's, handed over as a DigestView.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ngrunner.cards import Card, age_days, reminder_due
from ngrunner.clock import pacific_day
from ngrunner.messages import Message
from ngrunner.messages import templates
from ngrunner.messages.lint import CARD_ID


@dataclass(frozen=True)
class JobLine:
    """One job touched since the last digest. text says what happened, in a few plain words
    that follow "<job>: " ("filed 10/30 3:05 PM", "closet or garage? Card MOC-1")."""

    job: str
    text: str


@dataclass(frozen=True)
class DigestView:
    """What the digest is made from.

    now: when it is composed (its Pacific day heads it).
    owner: owner items, each one plain sentence: jobs installing within 5 days with no summary
        filed (templates.install_soon), alarms (templates.ALARMS), stuck jobs.
    jobs: one line per job touched since the last digest.
    cards: every open card; compose() brings back those due at 1, 3 or 7 days.
    open_items: how many items are on Your list; oldest_item: when the oldest was raised.
    """

    now: datetime
    owner: tuple[str, ...] = ()
    jobs: tuple[JobLine, ...] = ()
    cards: tuple[Card, ...] = ()
    open_items: int = 0
    oldest_item: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("owner", "jobs", "cards"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.now.utcoffset() is None:
            raise ValueError("a digest's time must be timezone-aware")
        if self.open_items < 0:
            raise ValueError("Your list can't hold fewer than no items")


def compose(view: DigestView) -> Message:
    """The digest for view, or the quiet night's one line when nothing was touched, nothing
    needs Jim first and no card comes back today."""
    day = pacific_day(view.now)
    oldest = _days(view.oldest_item, view.now)
    shown = _cards_to_show(view)
    if not (view.owner or view.jobs or shown):
        return templates.quiet_digest(day, open_items=view.open_items, oldest_days=oldest)
    return templates.digest(
        day, for_you=view.owner, jobs=[(line.job, line.text) for line in view.jobs],
        cards=[(card, age_days(card, view.now)) for card in shown],
        open_items=view.open_items, oldest_days=oldest,
    )


def reminders(view: DigestView) -> tuple[Card, ...]:
    """The open cards this digest brings back (at 1, 3 or 7 days), including any whose job
    line already names them. Once the digest is sent, wave 2 adds view.now to each one's
    reminded_at, so it comes back once per step."""
    return tuple(card for card in view.cards if reminder_due(card, view.now) is not None)


def _cards_to_show(view: DigestView) -> list[Card]:
    """reminders(view), less those a job line already names (no saying it twice)."""
    named = {line.job: set(CARD_ID.findall(line.text)) for line in view.jobs}
    return [card for card in reminders(view) if card.id not in named.get(card.job or "", ())]


def _days(since: datetime | None, now: datetime) -> int | None:
    return None if since is None else max(0, (now - since) // timedelta(days=1))
