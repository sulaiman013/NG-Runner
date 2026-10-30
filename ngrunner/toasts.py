"""
Toasts: Windows pop-ups at Jim's desk (design 4: Channels, Timing, The close prompt).

What may interrupt him is rationed. Some kinds show at once: DO NOT CUT, a posted nest NOT
CHECKED, a fix Mozaik undid, lock results, echoes of his own answers, the close prompt, check
results he asked for, and the two questions that hold a job (which job; closet or garage).
Every other kind shows at most once per 10 minutes; one that comes sooner is held back, not
queued, because the board and the digest carry everything a toast does (the toast is never
the record).

Toasts go out under NG Runner's own app identity (AUMID ICLA.NGRunner), which the installer
registers with its Start Menu shortcut: without one Windows drops them, and button clicks only
come back to an app it recognises. toasts_enabled() reads, never writes, whether Windows lets
them show at all, so the board, the digest and the DEV lane can say so; when a setting can't
be read it answers None, "could not verify", never a guess. Do not disturb (Focus) can't be
read by a program, so "on" means only that nothing is switched off.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from ngrunner.clock import Clock

AUMID = "ICLA.NGRunner"
APP_NAME = "NG Runner"
GAP = timedelta(minutes=10)
OPEN_BOARD = "open-board"  # the argument of a click on the toast itself, not on a button
MAX_BUTTONS = 5  # Windows' limit for a toast's buttons and inputs together
IMMEDIATE_KINDS = frozenset({
    "do_not_cut", "not_checked", "mozaik_undid", "lock_result", "echo",
    "which_job", "closet_or_garage", "close_prompt", "check_result",
})
ToastResult = Literal["shown", "held", "failed"]
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Choice:
    """A toast button: its label, and the argument a click hands to on_choice."""

    label: str
    argument: str


@dataclass(frozen=True)
class ToastMessage:
    """One toast. kind decides the rate rule (IMMEDIATE_KINDS). title and lines are the words
    of a message that passed messages.lint. sticky: stays on screen until Jim acts (the first
    close prompt)."""

    kind: str
    title: str
    lines: tuple[str, ...] = ()
    choices: tuple[Choice, ...] = ()
    sticky: bool = False


class ToastBackend(Protocol):
    def show(self, message: ToastMessage, on_choice: Callable[[str], None]) -> None:
        """Show message now; call on_choice(argument) when Jim clicks it (from any thread)."""
        ...


# ----- the rate rule


class Toaster:
    """Shows toasts under the rate rule. Safe to call from any thread."""

    def __init__(
        self,
        *,
        clock: Clock,
        on_choice: Callable[[str], None] | None = None,
        backend: ToastBackend | None = None,
        gap: timedelta = GAP,
    ) -> None:
        self._clock = clock
        self._on_choice = on_choice or (lambda argument: None)
        self._backend = backend or WindowsToastBackend()
        self._gap = gap
        self._last_ordinary: datetime | None = None
        self._lock = threading.Lock()

    def notify(self, message: ToastMessage) -> ToastResult:
        """Show message now if the rate rule allows it: "shown", "held" (an ordinary toast
        within 10 minutes of the last one shown) or "failed" (Windows refused; logged)."""
        immediate = message.kind in IMMEDIATE_KINDS
        with self._lock:
            now = self._clock.now()
            if not immediate and self._last_ordinary and now - self._last_ordinary < self._gap:
                return "held"
            try:
                self._backend.show(message, self._clicked)
            except Exception:  # noqa: BLE001 - a toast is a courtesy; the board has it anyway
                _log.exception("Windows did not show the toast %r", message.title)
                return "failed"
            if not immediate:
                self._last_ordinary = now
            return "shown"

    def _clicked(self, argument: str) -> None:
        try:
            self._on_choice(argument)
        except Exception:  # noqa: BLE001 - runs on Windows' thread: nothing may escape into it
            _log.exception("handling a toast click failed")


# ----- windows-toasts


def build_toast(message: ToastMessage, on_choice: Callable[[str], None]):
    """The windows_toasts.Toast for message. A click on a button hands on_choice its
    argument; a click on the toast itself hands it OPEN_BOARD. More than five choices keep the
    first four and add "More on the board"."""
    from windows_toasts import (
        Toast,
        ToastButton,
        ToastScenario,
        ToastSystemButton,
        ToastSystemButtonAction,
    )

    fields = [message.title, "\n".join(message.lines)] if message.lines else [message.title]
    toast = Toast(text_fields=fields)
    choices = message.choices
    if len(choices) > MAX_BUTTONS:
        choices = choices[: MAX_BUTTONS - 1] + (Choice("More on the board", OPEN_BOARD),)
    for choice in choices:
        toast.AddAction(ToastButton(choice.label, choice.argument))
    if message.sticky:
        toast.scenario = ToastScenario.Reminder  # Windows keeps a reminder only with a button
        if not choices:
            toast.AddAction(ToastSystemButton(ToastSystemButtonAction.Dismiss))
    body = toast.tag  # windows-toasts makes the toast's own launch argument its tag

    def activated(event) -> None:
        argument = event.arguments
        on_choice(OPEN_BOARD if not argument or argument == body else argument)

    toast.on_activated = activated
    return toast


class WindowsToastBackend:
    """Shows toasts through windows-toasts under AUMID. The Windows notifier is made at the
    first toast, so importing this module touches nothing in Windows."""

    def __init__(self, toaster=None) -> None:
        self._toaster = toaster

    def show(self, message: ToastMessage, on_choice: Callable[[str], None]) -> None:
        if self._toaster is None:
            self._toaster = make_toaster()
        self._toaster.show_toast(build_toast(message, on_choice))


def make_toaster():
    """The windows-toasts toaster for NG Runner's identity (it shows nothing by itself)."""
    from windows_toasts import InteractableWindowsToaster

    return InteractableWindowsToaster(APP_NAME, notifierAUMID=AUMID)


# ----- is Windows letting toasts show?

RegistryRead = Callable[[str, str, str], object]

_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"
_POLICY = r"Software\Policies\Microsoft\Windows\CurrentVersion\PushNotifications"
# (hive, key, value, the value that means off). The same switches the Setup Check reads
# (tools/setup_check.ps1, notifications), plus NG Runner's own per-app switch.
_SWITCHES = (
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\PushNotifications", "ToastEnabled", 0),
    ("HKCU", _SETTINGS, "NOC_GLOBAL_SETTING_TOASTS_ENABLED", 0),
    ("HKCU", _SETTINGS + "\\{aumid}", "Enabled", 0),
    ("HKCU", _POLICY, "NoToastApplicationNotification", 1),
    ("HKLM", _POLICY, "NoToastApplicationNotification", 1),
)


def toasts_enabled(*, aumid: str = AUMID, read: RegistryRead | None = None) -> bool | None:
    """False if any switch turns NG Runner's toasts off; None if none does but one could not
    be read (could not verify); True otherwise. A switch that is absent is at its default, on.
    read(hive, key, name) returns a value, None when absent, or raises OSError."""
    read = read or read_registry_value
    unsure = False
    for hive, key, name, off in _SWITCHES:
        try:
            value = read(hive, key.format(aumid=aumid), name)
        except OSError:
            unsure = True
            continue
        number = _number(value)
        if value is not None and number is None:
            unsure = True
        elif number == off:
            return False
    return None if unsure else True


def read_registry_value(hive: str, key: str, name: str) -> object | None:
    """A registry value, opened read-only; None when the key or the value is absent."""
    import winreg

    root = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}[hive]
    try:
        with winreg.OpenKey(root, key, 0, winreg.KEY_READ) as handle:
            value, _kind = winreg.QueryValueEx(handle, name)
    except FileNotFoundError:
        return None
    return value


def _number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None
