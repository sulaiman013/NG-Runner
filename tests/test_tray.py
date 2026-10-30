"""The tray icon: its drawing, its menu and its thread, with a fake icon backend.

No real icon is ever shown: the Tray is given a fake backend that records what it was asked to
show and lets a test click menu items. The pystray adapter is checked by building its menu
only, which creates no window.
"""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field

import pytest
from PIL import Image

from ngrunner.board.views import TrayState, tray_tooltip
from ngrunner.tray import (
    COLOURS,
    MenuEntry,
    Tray,
    TrayActions,
    draw_icon,
    fit_tooltip,
    pystray_menu,
)

GREEN = TrayState("green", 0, "NG Runner is running")
AMBER = TrayState("amber", 2, "NG Runner: 2 items for you")


# ----- fakes


class FakeIcon:
    """Stands in for pystray.Icon: records what it shows, and runs menu clicks on its own
    thread, as the real one does, so an action that breaks the loop hangs the next click."""

    def __init__(self, image: Image.Image, tooltip: str, menu: tuple[MenuEntry, ...]) -> None:
        self.shown = [(image, tooltip, menu)]
        self.made_on = threading.current_thread()
        self.ran_on: threading.Thread | None = None
        self._clicks: queue.Queue = queue.Queue()
        self._stop = threading.Event()

    def run(self) -> None:
        self.ran_on = threading.current_thread()
        while not self._stop.is_set():
            try:
                action, done = self._clicks.get(timeout=0.02)
            except queue.Empty:
                continue
            action()
            done.set()

    def stop(self) -> None:
        self._stop.set()

    def show(self, image: Image.Image, tooltip: str, menu: tuple[MenuEntry, ...]) -> None:
        self.shown.append((image, tooltip, menu))

    @property
    def labels(self) -> list[str]:
        return [entry.label for entry in self.shown[-1][2]]

    def click(self, label: str) -> None:
        """Click a menu item and wait (up to 5 s) until the tray's thread has handled it."""
        entry = next(entry for entry in self.shown[-1][2] if entry.label == label)
        done = threading.Event()
        self._clicks.put((entry.action, done))
        assert done.wait(5), f"the tray's thread did not handle {label!r}"


@dataclass
class Calls:
    made: list[str] = field(default_factory=list)

    def actions(self, **override) -> TrayActions:
        def record(name: str):
            return lambda: self.made.append(name)

        names = ("open_board", "pause", "resume", "check_now", "send_support_bundle", "quit")
        values = {name: record(name) for name in names}
        values.update(override)
        return TrayActions(**values)


@pytest.fixture
def calls() -> Calls:
    return Calls()


@pytest.fixture
def icons() -> list[FakeIcon]:
    return []


def _backend(icons: list[FakeIcon]):
    def make(image, tooltip, menu) -> FakeIcon:
        icons.append(FakeIcon(image, tooltip, menu))
        return icons[-1]

    return make


@pytest.fixture
def tray(calls, icons):
    tray = Tray(calls.actions(), GREEN, backend=_backend(icons), confirm=lambda question: True)
    tray.start()
    yield tray
    tray.stop()


# ----- drawing


def _rgb(image: Image.Image, x: float, y: float) -> tuple[int, int, int]:
    return image.getpixel((int(x * image.width), int(y * image.height)))[:3]


@pytest.mark.parametrize("colour", ["green", "amber", "red", "grey"])
def test_the_icon_is_a_circle_in_the_states_colour_on_a_clear_background(colour):
    image = draw_icon(colour, 0)

    assert image.mode == "RGBA" and image.size == (64, 64)
    assert image.getpixel((0, 0))[3] == 0  # a corner is see-through
    fill = COLOURS[colour].fill
    near_top = _rgb(image, 0.5, 0.16)  # inside the circle, clear of the glyph in the middle
    assert all(abs(a - b) <= 12 for a, b in zip(near_top, fill))


def test_the_four_states_never_differ_by_colour_alone():
    """Each state has its own mark (tick, count, !, pause bars), for colour-blind eyes."""
    middles = {colour: draw_icon(colour, 2 if colour == "amber" else 0).crop((20, 20, 44, 44))
               .convert("L").point(lambda v: 255 if v > 128 else 0).tobytes()
               for colour in ("green", "amber", "red", "grey")}

    assert len(set(middles.values())) == 4


def test_the_count_is_drawn_and_a_new_count_makes_a_new_icon():
    assert draw_icon("amber", 3).tobytes() != draw_icon("amber", 4).tobytes()
    assert draw_icon("amber", 12).tobytes() != draw_icon("amber", 9).tobytes()


@pytest.mark.parametrize("size", [16, 32, 256])
def test_the_icon_can_be_drawn_at_any_tray_size(size):
    assert draw_icon("red", 0, size=size).size == (size, size)


def test_an_unknown_colour_is_refused():
    with pytest.raises(ValueError):
        draw_icon("purple", 0)


# ----- the menu and the thread


def test_the_tray_runs_its_icon_in_its_own_daemon_thread(tray, icons):
    icon = icons[0]

    assert tray.running
    assert icon.made_on is icon.ran_on
    assert icon.ran_on is not threading.current_thread() and icon.ran_on.daemon


def test_the_menu_offers_the_five_items_with_open_board_as_the_click(tray, icons):
    menu = icons[0].shown[-1][2]

    assert [entry.label for entry in menu] == [
        "Open board", "Pause", "Check for new rows now", "Send support bundle", "Quit"]
    assert [entry.label for entry in menu if entry.default] == ["Open board"]


@pytest.mark.parametrize("label, action", [("Open board", "open_board"), ("Pause", "pause"),
                                           ("Check for new rows now", "check_now"),
                                           ("Send support bundle", "send_support_bundle")])
def test_each_item_runs_its_action(tray, icons, calls, label, action):
    icons[0].click(label)

    assert calls.made == [action]


def test_while_paused_the_menu_offers_resume(tray, icons, calls):
    tray.update(TrayState("grey", 0, "NG Runner is paused"), paused=True)

    icons[0].click("Resume")

    assert "Pause" not in icons[0].labels and calls.made == ["resume"]


def test_quit_asks_first_and_quits_only_on_yes(calls, icons):
    questions: list[str] = []
    answers = iter([False, True])

    def confirm(question: str) -> bool:
        questions.append(question)
        return next(answers)

    tray = Tray(calls.actions(), GREEN, backend=_backend(icons), confirm=confirm)
    tray.start()
    try:
        icons[0].click("Quit")
        assert calls.made == []
        icons[0].click("Quit")
        assert calls.made == ["quit"]
    finally:
        tray.stop()
    assert "Quit NG Runner?" in questions[0]


def test_update_redraws_the_icon_and_its_tooltip(tray, icons):
    tray.update(AMBER, paused=False)

    image, tooltip, _ = icons[0].shown[-1]
    assert tooltip == "NG Runner: 2 items for you"
    assert image.tobytes() == draw_icon("amber", 2).tobytes()


def test_a_state_given_before_start_is_the_one_shown_at_start(calls, icons):
    tray = Tray(calls.actions(), GREEN, backend=_backend(icons))
    tray.update(AMBER, paused=False)
    tray.start()
    try:
        assert icons[0].shown[-1][1] == "NG Runner: 2 items for you"
    finally:
        tray.stop()


def test_a_long_tooltip_is_cut_to_what_windows_holds():
    text = "NG Runner: " + "Testerson_101426 waits for you; " * 10

    fitted = fit_tooltip(text)

    assert len(fitted.encode("utf-16-le")) // 2 <= 127
    assert fitted.endswith("…") and fitted.startswith("NG Runner: Testerson_101426")
    assert fit_tooltip("NG Runner is running") == "NG Runner is running"


def test_in_shadow_mode_the_tray_says_so_first_so_a_long_tooltip_is_never_cut_to_lose_it(calls,
                                                                                         icons):
    text = tray_tooltip("NG Runner: " + "Testerson_101426 waits for you; " * 10, shadow=True)
    tray = Tray(calls.actions(), TrayState("amber", 9, text), backend=_backend(icons))
    tray.start()
    try:
        shown = icons[0].shown[-1][1]
    finally:
        tray.stop()

    assert shown.startswith("SHADOW") and shown.endswith("…")
    assert fit_tooltip(tray_tooltip("NG Runner is running", shadow=True)).startswith("SHADOW")


def test_an_action_that_fails_leaves_the_tray_working(calls, icons, caplog):
    def broken() -> None:
        raise RuntimeError("the board is not up yet")

    tray = Tray(calls.actions(open_board=broken), GREEN, backend=_backend(icons))
    tray.start()
    try:
        icons[0].click("Open board")
        icons[0].click("Pause")
    finally:
        tray.stop()

    assert calls.made == ["pause"]
    assert "Open board" in caplog.text


def test_quitting_from_the_trays_own_thread_can_stop_the_tray(calls, icons):
    """Quit runs on the tray's thread, and wave 2's quit stops the tray: that must not try
    to wait for its own thread."""
    holder: dict[str, Tray] = {}

    def quit_now() -> None:
        holder["tray"].stop()
        calls.made.append("quit")

    tray = Tray(calls.actions(quit=quit_now), GREEN, backend=_backend(icons),
                confirm=lambda question: True)
    holder["tray"] = tray
    tray.start()

    icons[0].click("Quit")
    tray.stop()

    assert calls.made == ["quit"] and not tray.running


def test_an_icon_that_cannot_be_made_is_logged_and_the_tray_says_it_is_not_running(calls,
                                                                                   caplog):
    def no_icon(image, tooltip, menu):
        raise OSError("the notification area is not available")

    tray = Tray(calls.actions(), GREEN, backend=no_icon)
    tray.start()
    tray.stop()

    assert not tray.running
    assert "tray icon stopped" in caplog.text


def test_stop_ends_the_thread(calls, icons):
    tray = Tray(calls.actions(), GREEN, backend=_backend(icons))
    tray.start()

    tray.stop()

    assert not tray.running


# ----- the pystray adapter (menu only: no window, no icon)


def test_the_pystray_menu_keeps_labels_order_and_the_default_and_runs_the_actions(calls):
    clicked: list[str] = []
    entries = (MenuEntry("Open board", lambda: clicked.append("open"), default=True),
               MenuEntry("Quit", lambda: clicked.append("quit")))

    menu = pystray_menu(entries)
    items = list(menu.items)

    assert [item.text for item in items] == ["Open board", "Quit"]
    assert [item.default for item in items] == [True, False]
    items[1](None)
    assert clicked == ["quit"]
