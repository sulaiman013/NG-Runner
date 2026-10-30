"""Toasts: the rate rule, clicks, the windows-toasts adapter and the Windows setting check.

No toast is ever shown: the Toaster gets a fake backend, and the windows-toasts adapter is
checked on the Toast object it builds. The registry is only read; the one test that writes
creates its own key under HKCU\\Software\\NGRunner-test-<random> and removes it again.
"""
from __future__ import annotations

import secrets
import winreg
from datetime import datetime, timedelta, timezone

import pytest
from windows_toasts import ToastActivatedEventArgs, ToastScenario, ToastSystemButton

from ngrunner.clock import FakeClock
from ngrunner.toasts import (
    AUMID,
    IMMEDIATE_KINDS,
    OPEN_BOARD,
    Choice,
    Toaster,
    ToastMessage,
    WindowsToastBackend,
    build_toast,
    make_toaster,
    read_registry_value,
    toasts_enabled,
)

T0 = datetime(2026, 10, 30, 22, 0, tzinfo=timezone.utc)
FILED = ToastMessage("filed", "Filed as Testerson_101426", ("Summary checked on two fetches.",))
DO_NOT_CUT = ToastMessage("do_not_cut", "DO NOT CUT · Fakeley_102726 · 3/4 Classic White",
                          ("The nest does not match the saved design.",))


class FakeBackend:
    def __init__(self, fail: bool = False) -> None:
        self.shown: list[ToastMessage] = []
        self.on_choice = None
        self.fail = fail

    def show(self, message: ToastMessage, on_choice) -> None:
        if self.fail:
            raise OSError("the notification platform is not available")
        self.shown.append(message)
        self.on_choice = on_choice


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def toaster(clock, backend) -> Toaster:
    return Toaster(clock=clock, backend=backend)


# ----- the rate rule


def test_aumid_is_ngrunners_own():
    assert AUMID == "ICLA.NGRunner"


def test_immediate_kinds_show_at_once_even_back_to_back(toaster, backend):
    assert "do_not_cut" in IMMEDIATE_KINDS

    assert toaster.notify(DO_NOT_CUT) == "shown"
    assert toaster.notify(DO_NOT_CUT) == "shown"
    assert len(backend.shown) == 2


def test_other_kinds_show_at_most_one_per_ten_minutes(toaster, backend, clock):
    assert toaster.notify(FILED) == "shown"
    clock.advance(timedelta(minutes=9, seconds=59))
    assert toaster.notify(FILED) == "held"
    clock.advance(timedelta(seconds=1))
    assert toaster.notify(FILED) == "shown"
    assert len(backend.shown) == 2


def test_immediate_toasts_neither_wait_for_nor_use_up_the_ten_minutes(toaster, clock):
    assert toaster.notify(FILED) == "shown"
    clock.advance(60)
    assert toaster.notify(DO_NOT_CUT) == "shown"   # not held by the ordinary one
    clock.advance(timedelta(minutes=9))
    assert toaster.notify(FILED) == "shown"        # 10 min after the last ordinary one


def test_a_toast_windows_refused_does_not_count_and_does_not_raise(clock):
    backend = FakeBackend(fail=True)
    toaster = Toaster(clock=clock, backend=backend)

    assert toaster.notify(FILED) == "failed"
    backend.fail = False
    clock.advance(60)
    assert toaster.notify(FILED) == "shown"


def test_a_click_on_a_toast_button_passes_its_argument_on(clock, backend):
    clicked: list[str] = []
    toaster = Toaster(clock=clock, backend=backend, on_choice=clicked.append)
    toaster.notify(ToastMessage("closet_or_garage", "Closet or garage? Fakeley_102726",
                                choices=(Choice("Closet", "FAK-1 Closet"),
                                         Choice("Garage", "FAK-1 Garage"))))

    backend.on_choice("FAK-1 Garage")

    assert clicked == ["FAK-1 Garage"]


def test_a_click_handler_that_fails_is_logged_not_raised(clock, backend, caplog):
    def broken(argument: str) -> None:
        raise RuntimeError("the board is not up yet")

    toaster = Toaster(clock=clock, backend=backend, on_choice=broken)
    toaster.notify(DO_NOT_CUT)

    backend.on_choice(OPEN_BOARD)  # Windows calls this on its own thread: nothing may escape

    assert "toast click" in caplog.text


# ----- the windows-toasts adapter (builds Toast objects; never shows one)


def test_the_toast_carries_the_title_and_the_lines():
    toast = build_toast(FILED, lambda argument: None)

    assert toast.text_fields == ["Filed as Testerson_101426", "Summary checked on two fetches."]
    assert toast.actions == [] and toast.scenario == ToastScenario.Default


def test_choices_become_buttons_and_a_click_passes_the_choice_argument():
    clicked: list[str] = []
    message = ToastMessage("closet_or_garage", "Closet or garage? Fakeley_102726",
                           choices=(Choice("Closet", "FAK-1 Closet"),
                                    Choice("Garage", "FAK-1 Garage")))

    toast = build_toast(message, clicked.append)
    toast.on_activated(ToastActivatedEventArgs(arguments="FAK-1 Garage"))
    toast.on_activated(ToastActivatedEventArgs(arguments=toast.tag))  # a click on the toast

    assert [(b.content, b.arguments) for b in toast.actions] == [
        ("Closet", "FAK-1 Closet"), ("Garage", "FAK-1 Garage")]
    assert clicked == ["FAK-1 Garage", OPEN_BOARD]


def test_more_than_five_choices_keep_four_and_send_the_rest_to_the_board():
    choices = tuple(Choice(f"Job {n}", f"WHI-1 {n}") for n in range(1, 8))

    toast = build_toast(ToastMessage("which_job", "Which job is this row?", choices=choices),
                        lambda argument: None)

    assert [b.content for b in toast.actions] == ["Job 1", "Job 2", "Job 3", "Job 4",
                                                 "More on the board"]
    assert toast.actions[-1].arguments == OPEN_BOARD


def test_a_sticky_toast_is_a_reminder_that_stays_until_dismissed():
    toast = build_toast(ToastMessage("close_prompt", "Testerson_101426 closed. Sections done?",
                                     sticky=True), lambda argument: None)

    assert toast.scenario == ToastScenario.Reminder
    assert any(isinstance(action, ToastSystemButton) for action in toast.actions)


def test_the_windows_backend_hands_the_built_toast_to_its_toaster():
    class FakeToaster:
        def __init__(self) -> None:
            self.toasts = []

        def show_toast(self, toast) -> None:
            self.toasts.append(toast)

    fake = FakeToaster()

    WindowsToastBackend(toaster=fake).show(FILED, lambda argument: None)

    assert [t.text_fields[0] for t in fake.toasts] == ["Filed as Testerson_101426"]


def test_the_real_toaster_is_made_with_ngrunners_aumid():
    """Makes the Windows notifier object only; nothing is shown."""
    assert make_toaster().notifierAUMID == AUMID


# ----- toasts_enabled(): the Windows setting, read only

_PUSH = r"Software\Microsoft\Windows\CurrentVersion\PushNotifications"
_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"
_POLICY = r"Software\Policies\Microsoft\Windows\CurrentVersion\PushNotifications"


def _reader(values: dict[tuple[str, str, str], object], unreadable: set = frozenset()):
    def read(hive: str, key: str, name: str) -> object | None:
        if (hive, key.casefold(), name) in unreadable:
            raise PermissionError("access denied")
        return values.get((hive, key.casefold(), name))

    return read


def _k(hive: str, key: str, name: str) -> tuple[str, str, str]:
    return hive, key.casefold(), name


def test_with_nothing_switched_off_toasts_are_on():
    assert toasts_enabled(read=_reader({})) is True
    assert toasts_enabled(read=_reader({_k("HKCU", _PUSH, "ToastEnabled"): 1})) is True


@pytest.mark.parametrize("key, value", [
    (_k("HKCU", _PUSH, "ToastEnabled"), 0),
    (_k("HKCU", _SETTINGS, "NOC_GLOBAL_SETTING_TOASTS_ENABLED"), 0),
    (_k("HKCU", _SETTINGS + "\\" + AUMID, "Enabled"), 0),
    (_k("HKCU", _POLICY, "NoToastApplicationNotification"), 1),
    (_k("HKLM", _POLICY, "NoToastApplicationNotification"), 1),
], ids=["all apps off", "notifications off", "NG Runner off", "user policy", "machine policy"])
def test_any_switch_off_means_off(key, value):
    assert toasts_enabled(read=_reader({key: value})) is False


def test_a_setting_that_cannot_be_read_means_could_not_verify():
    unreadable = {_k("HKCU", _PUSH, "ToastEnabled")}

    assert toasts_enabled(read=_reader({}, unreadable)) is None


def test_off_wins_over_could_not_verify():
    unreadable = {_k("HKCU", _PUSH, "ToastEnabled")}
    values = {_k("HKCU", _SETTINGS + "\\" + AUMID, "Enabled"): 0}

    assert toasts_enabled(read=_reader(values, unreadable)) is False


def test_a_value_of_an_unexpected_kind_means_could_not_verify():
    assert toasts_enabled(read=_reader({_k("HKCU", _PUSH, "ToastEnabled"): b"\x00"})) is None


def test_the_registry_reader_reads_a_test_key_and_the_check_works_through_it():
    """The real winreg path, on a key this test makes and removes again."""
    base = rf"Software\NGRunner-test-{secrets.token_hex(6)}"
    made: list[str] = []

    def make(key: str, name: str, kind: int, value: object) -> None:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_WRITE) as handle:
            winreg.SetValueEx(handle, name, 0, kind, value)
        made.append(key)

    try:
        make(rf"{base}\HKCU\{_PUSH}", "ToastEnabled", winreg.REG_DWORD, 1)
        make(rf"{base}\HKCU\{_SETTINGS}\{AUMID}", "Enabled", winreg.REG_DWORD, 0)
        make(rf"{base}\text", "Note", winreg.REG_SZ, "1")

        assert read_registry_value("HKCU", rf"{base}\HKCU\{_PUSH}", "ToastEnabled") == 1
        assert read_registry_value("HKCU", rf"{base}\text", "Note") == "1"
        assert read_registry_value("HKCU", rf"{base}\text", "Missing") is None
        assert read_registry_value("HKCU", rf"{base}\no such key", "Note") is None

        def inside_test_key(hive: str, key: str, name: str) -> object | None:
            return read_registry_value("HKCU", rf"{base}\{hive}\{key}", name)

        assert toasts_enabled(read=inside_test_key) is False  # NG Runner's own switch is off
    finally:
        _delete_tree(winreg.HKEY_CURRENT_USER, base)

    with pytest.raises(FileNotFoundError):
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, base)


def _delete_tree(root, key: str) -> None:
    with winreg.OpenKey(root, key, 0, winreg.KEY_READ | winreg.KEY_WRITE) as handle:
        while True:
            try:
                child = winreg.EnumKey(handle, 0)
            except OSError:
                break
            _delete_tree(root, rf"{key}\{child}")
    winreg.DeleteKey(root, key)


def test_on_this_pc_the_check_answers_on_off_or_could_not_verify():
    """Reads this PC's real settings (read only)."""
    assert toasts_enabled() in (True, False, None)
