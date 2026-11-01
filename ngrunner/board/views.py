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
NEST_STATES = ("not_posted", "not_checked", "save_nest", "do_not_cut", "nest_checked")
CHECK_STATES = ("none", "asked", "running", "waiting", "passed", "failed", "could_not_verify", "stale",
                "not_run")
CHECK_ITEM_KINDS = ("fix", "mozaik", "could_not_verify", "advisory")
TAKEOFF_STATES = ("made", "chat", "unverified", "in_chat", "failed")
LOCK_BUTTON_STATES = ("ready", "waivable", "blocked", "asked")
BATCH_STATES = ("dropped", "archived", "first_ok", "confirmed", "doubt", "resent")
FIX_STATES = ("waiting", "applied", "undone", "held", "mozaik_undid", "you_changed", "could_not_verify")
LEASE_STATES = ("active", "lapsed", "released")
JOB_SHEET_STATES = ("matches", "mismatch", "could_not_verify")


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
class MaterialRow:
    """One material of a job, as the nest guard last read it. state: one of NEST_STATES. note: what
    it found, in plain words. at: when a real post landed, None for one from before NG Runner, which
    is what tells a NOT CHECKED worth saying from an old one. file: its folder, for Open. shown: its
    name as Jim reads it. trial: its verdict still says "(trial)"."""

    material: str
    state: str
    note: str = ""
    at: datetime | None = None
    file: Path | None = None
    shown: str = ""
    trial: bool = False

    def __post_init__(self) -> None:
        _one_of(self.state, NEST_STATES, "a material's state")
        _aware(self.at, "a material's time")


@dataclass(frozen=True)
class TakeoffView:
    """NG Runner's takeoff of a filed job, on its line. state: "made" (complete: lines are its
    sheets by colour and thickness, then its tape, and label the words every list carries),
    "chat" (lines it could not verify, and the chat's takeoff went as the order heads-up instead),
    "unverified" (lines it could not verify, which its row's notes count: no list), "in_chat" (a
    garage or mixed job) or "failed" (the chat's takeoff is used), each with its one line."""

    state: str
    lines: tuple[str, ...] = ()
    label: str = ""

    def __post_init__(self) -> None:
        _one_of(self.state, TAKEOFF_STATES, "a takeoff's state")


@dataclass(frozen=True)
class CheckItemView:
    """One thing a check found (P3). kind: "fix" (chat line given), "mozaik" (only Jim in Mozaik),
    "could_not_verify" or "advisory". text: NG Runner's words, parts by tag. chat: the line Jim pastes
    into his chat. settled: when and how Jim settled it, empty while open. can_settle: a button for it."""

    n: int
    kind: str
    text: str
    chat: str | None = None
    settled: str = ""
    can_settle: bool = False

    def __post_init__(self) -> None:
        _one_of(self.kind, CHECK_ITEM_KINDS, "a check item's kind")


@dataclass(frozen=True)
class PromptView:
    """A close prompt still waiting: its words, and its answers as buttons."""

    id: int
    text: str
    choices: tuple[str, ...]


@dataclass(frozen=True)
class CheckView:
    """A job's check (P3), with Check now always beside it. state: one of CHECK_STATES. line: its one
    line. run_id: the run its items are of. prompt: a close prompt waiting on Jim."""

    studio_id: str
    state: str
    line: str = ""
    run_id: int | None = None
    items: tuple[CheckItemView, ...] = ()
    prompt: PromptView | None = None

    def __post_init__(self) -> None:
        _one_of(self.state, CHECK_STATES, "a check's state")


@dataclass(frozen=True)
class LockButtonView:
    """One of Jim's lock buttons (P4): Lock design, Finish lock of one material, or Lock. state: ready,
    waivable (only the check is missing, so "lock without check" may go ahead), blocked, or asked (a click
    is being acted on). missing: exactly what stands in its way, in the gate's own words."""

    studio_id: str
    click: str
    material: str | None
    label: str
    state: str
    missing: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _one_of(self.state, LOCK_BUTTON_STATES, "a lock button's state")


@dataclass(frozen=True)
class BatchView:
    """A tracker batch NG Runner dropped (into its own test folder until P4 goes live), as the after-drop
    step last judged it. can_resend: [Re-send only the missing row] may be offered."""

    id: str
    label: str
    state: str
    words: str
    can_resend: bool = False

    def __post_init__(self) -> None:
        _one_of(self.state, BATCH_STATES, "a batch's state")


@dataclass(frozen=True)
class FixView:
    """A fix: waiting to be written, written, undone, or what Jim's next save showed. id: the fix's, for
    Undo; None while it waits."""

    id: int | None
    state: str
    words: str
    can_undo: bool = False

    def __post_init__(self) -> None:
        _one_of(self.state, FIX_STATES, "a fix's state")


@dataclass(frozen=True)
class LeaseView:
    state: str
    words: str

    def __post_init__(self) -> None:
        _one_of(self.state, LEASE_STATES, "a lease's state")


@dataclass(frozen=True)
class JobSheetView:
    state: str
    words: str
    items: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _one_of(self.state, JOB_SHEET_STATES, "a job sheet's state")


@dataclass(frozen=True)
class LockPanelView:
    """A job's P4 panel: its lock buttons, its lease, its batches, its fixes and its job sheet, and the
    note that P4 writes only NG Runner's own copies until it goes live."""

    buttons: tuple[LockButtonView, ...] = ()
    lease: LeaseView | None = None
    batches: tuple[BatchView, ...] = ()
    fixes: tuple[FixView, ...] = ()
    sheet: JobSheetView | None = None
    note: str = ""


@dataclass(frozen=True)
class JobRow:
    """One row per job, and one for a Fab row with no job yet: its studio_id is None and its
    name is the row as Barbara typed it. A folder in NG Jobs with no Fab row gets one too, once the
    guard has something to say about it. install: a date, the text to show when it isn't one
    (the job model's words, such as "could not verify"), or None when the Fab row has no
    install date. materials: the nest guard's lines. notes: anything else a guard can't verify. lock:
    what the pre-flight would have him look at before he locks. takeoff: NG Runner's takeoff of
    the summary it filed, once it has one. check: its check (P3), for a row with a job. p4: its lock
    panel (P4), for a filed job or one with something of P4's."""

    studio_id: str | None
    name: str
    install: date | str | None
    ticks: tuple[Tick, ...]
    next_step: str
    overlay: str | None = None
    materials: tuple[MaterialRow, ...] = ()
    notes: tuple[str, ...] = ()
    lock: tuple[str, ...] = ()
    takeoff: TakeoffView | None = None
    check: CheckView | None = None
    p4: LockPanelView | None = None

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
    mozaik: str = ""                     # what the observer last found, in words; "" not looked yet

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

    def check_job(self, studio_id: str) -> str:
        """Check now: Jim's crosscheck on this job, as it is (P3)."""
        ...

    def settle_item(self, studio_id: str, run_id: str, n: str, how: str) -> str:
        """Settle item n of a check run for this job: how is "not_a_problem" or "told_chat", as the
        board refuses anything else. The ids are as the form sent them."""
        ...

    def answer_prompt(self, prompt_id: str, choice: str) -> str:
        """A close prompt's button: choice is its label, exactly as shown."""
        ...

    def lock_click(self, studio_id: str, click: str, material: str, without_check: bool) -> str:
        """Lock design, Finish lock (of material) or Lock (P4): refused with what is missing, or asked.
        without_check: Jim confirmed "lock without check", which waives only the check."""
        ...

    def resend_row(self, batch_id: str) -> str:
        """[Re-send only the missing row] of a batch in doubt, when the board offers it."""
        ...

    def undo_fix(self, fix_id: str) -> str:
        """Undo a fix NG Runner wrote."""
        ...
