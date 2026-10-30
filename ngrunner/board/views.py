"""
What the board and the tray show, as plain values (build-spec 6.6, "View models").

The board computes nothing about jobs: a job has no single state (design 3.4), and its row,
next step and tray colour are worked out by the job model from facts, clicks and pending work.
That model hands the board these frozen values, fresh for every page, and the board only
formats them. Keeping the boundary this thin is what lets the board be built and tested before
the job model exists: tests fill the same values with synthetic jobs. Each value checks itself
when made, so a wrong colour or state fails where it was made, not on Jim's screen.

The board asks two objects for everything: BoardViews (what to show) and BoardActions (what to
do when Jim clicks or types). Both are protocols, so the job model and the supervisor can be
anything with these methods.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Protocol, get_args

TickState = Literal["done", "todo", "blocked", "na"]
TrayColour = Literal["green", "amber", "red", "grey"]
Lane = Literal["ACT", "LOG", "DEV"]
# A job's overlays (design 3.4). The reason, the fix or the retry goes in its next step line.
OVERLAYS = ("WAITING", "BLOCKED", "NOT_RUN", "HELD", "OBSERVED", "PAUSED")
SHADOW = "SHADOW"  # what the board and the tray say in shadow mode (integration spec 7)


def _one_of(value: object, allowed: Sequence[object], what: str) -> None:
    if value not in allowed:
        raise ValueError(f"{what} must be one of {', '.join(map(str, allowed))}, not {value!r}")


def _aware(when: datetime | None, what: str) -> None:
    if when is not None and (when.tzinfo is None or when.utcoffset() is None):
        raise ValueError(f"{what} must be a timezone-aware time")


# ----- the board's rows


@dataclass(frozen=True)
class Tick:
    """One step of a job, shown as icon + label + colour. "na": not built yet in this phase."""

    key: str
    label: str
    state: TickState

    def __post_init__(self) -> None:
        _one_of(self.state, get_args(TickState), "a tick's state")


@dataclass(frozen=True)
class JobRow:
    """One row per job, and one for a Fab row with no job yet: its studio_id is None and its
    name is the row as Barbara typed it. install: a date, the text to show when it isn't one
    (the job model's words, such as "could not verify"), or None when the Fab row has no
    install date."""

    studio_id: str | None
    name: str
    install: date | str | None
    ticks: tuple[Tick, ...]
    next_step: str
    overlay: str | None = None

    def __post_init__(self) -> None:
        if self.overlay is not None:
            _one_of(self.overlay, OVERLAYS, "a job's overlay")


@dataclass(frozen=True)
class CardView:
    """A question for Jim with fixed choices (GLOSSARY: Card). id is like TES-2. job is the
    line that says what it is about, templates.card_subject(card), so a card with no job yet
    (which job is this row?) shows its row and never the word None."""

    id: str
    job: str
    text: str
    choices: tuple[str, ...]
    age_days: int

    def __post_init__(self) -> None:
        if not isinstance(self.job, str):
            raise ValueError(f"a card's job must be text, not {self.job!r}")


@dataclass(frozen=True)
class ListItem:
    """One item on Your list. card_id links it to its card; file is what its Open button opens
    (only inside the board's roots)."""

    text: str
    job: str | None = None
    age_days: int | None = None
    card_id: str | None = None
    file: Path | None = None


@dataclass(frozen=True)
class TrayState:
    """The tray icon: green; amber with a count; red when something is urgent or NG Runner is
    unwell; grey when paused (design 4, Channels). tooltip is the plain sentence beside it, made
    with tray_tooltip, so that in shadow mode it starts SHADOW."""

    colour: TrayColour
    count: int
    tooltip: str

    def __post_init__(self) -> None:
        _one_of(self.colour, get_args(TrayColour), "the tray colour")
        if self.count < 0:
            raise ValueError("the tray count can't be negative")


def tray_tooltip(text: str, *, shadow: bool) -> str:
    """The sentence beside the tray icon. In shadow mode it starts SHADOW: a long tooltip loses
    only its end (tray.fit_tooltip), so the word always shows."""
    return f"{SHADOW}: {text}" if shadow else text


# ----- health and history


@dataclass(frozen=True)
class PathCheck:
    """A folder NG Runner depends on. ok None: it could not be checked (say why in note)."""

    label: str
    path: Path
    ok: bool | None
    note: str = ""


@dataclass(frozen=True)
class HealthView:
    """The Health page. Times are when each thing last happened (None: never); the board shows
    them in Pacific time with their age. toasts is toasts_enabled(): None = could not verify.
    leftover_temps lists *.ngr-tmp files found, for Sulaiman (AGENTS.md, Before filing goes
    live, 7)."""

    paths: tuple[PathCheck, ...]
    last_export: datetime | None
    list_synced_at: datetime | None
    heartbeat: datetime | None
    toasts: bool | None
    alarms: tuple[str, ...] = ()
    leftover_temps: tuple[Path, ...] = ()
    app_version: str = ""

    def __post_init__(self) -> None:
        for name in ("last_export", "list_synced_at", "heartbeat"):
            _aware(getattr(self, name), name)


@dataclass(frozen=True)
class EventView:
    """One line of History. The DEV lane is Sulaiman's: the board never shows it to Jim."""

    at: datetime
    job: str | None
    text: str
    lane: Lane = "ACT"

    def __post_init__(self) -> None:
        _aware(self.at, "an event's time")
        _one_of(self.lane, get_args(Lane), "an event's lane")


@dataclass(frozen=True)
class BoardView:
    """Everything the board shows apart from History, read once per page. shadow: Settings.mode
    is shadow, so every page says so; it has no default, so no view can forget it and look live,
    and the tray's tooltip must agree with it."""

    jobs: tuple[JobRow, ...]
    your_list: tuple[ListItem, ...]
    cards: tuple[CardView, ...]
    health: HealthView
    tray: TrayState
    paused: bool
    shadow: bool

    def __post_init__(self) -> None:
        if not isinstance(self.shadow, bool):
            raise ValueError(f"shadow must be true or false, not {self.shadow!r}")
        if self.tray.tooltip.startswith(SHADOW) != self.shadow:
            raise ValueError("the tray's tooltip must start SHADOW in shadow mode, and only then")


# ----- what the board asks of the rest of NG Runner


class BoardViews(Protocol):
    """What the board shows. Called on every page, from the board server's threads."""

    def board(self) -> BoardView:
        """The current view."""
        ...

    def history(self) -> Sequence[EventView]:
        """Recent events, newest first; the provider decides how far back."""
        ...


class BoardActions(Protocol):
    """What the board does when Jim clicks or types. Each returns the line to show him at once
    (the echo, such as "Saved: TES-2, 3rd from left"), in plain words; "" shows nothing. Called
    from the board server's threads, only ever for a POST that passed every check."""

    def answer(self, card_id: str, choice: str) -> str:
        """A card's button: choice is the button's label, exactly as shown."""
        ...

    def command(self, text: str) -> str:
        """The command box, exactly as typed (cards.parse_command reads it, never the board)."""
        ...

    def pause(self) -> str: ...

    def resume(self) -> str: ...

    def check_now(self) -> str:
        """Check for new Fab rows now."""
        ...

    def add_job(self, text: str) -> str:
        """Add a job by its name or Studio project ID, exactly as typed."""
        ...

    def set_mode(self, mode: str) -> str:
        """Switch to shadow or live, the deliberate settings change on Health, logged as an
        event (integration spec 7). mode is one of config.MODES, as the board refuses anything
        else; it may be the mode NG Runner is already in, from a page left open."""
        ...
