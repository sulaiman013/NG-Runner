"""
The tray icon: NG Runner's presence by the clock, and its menu (build-spec 6.6; design 4).

Green when all is well; amber with the count of Your list; red for DO NOT CUT, a posted nest
NOT CHECKED, or NG Runner unwell; grey when paused. The colours are not the only signal: each
state also has its own mark (a tick, the count, "!", pause bars), since red and green look
alike to many colour-blind people and status is never colour alone.

pystray runs in its own daemon thread, which creates the icon and runs its message loop, as
Windows wants one thread to own a window. Tray never decides a colour: the job model hands it
a TrayState (board/views.py). The icon backend is injectable, so tests never show a real icon.
"""
from __future__ import annotations

import ctypes
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple, Protocol

from PIL import Image, ImageDraw, ImageFont

from ngrunner.board.views import TrayState

QUIT_QUESTION = (
    "Quit NG Runner?\n\nWhile it is closed, new rows on the NG Fab Schedule are not filed."
)
TOOLTIP_LIMIT = 127  # Windows holds 128 UTF-16 units in a tray tooltip, the last one a NUL
_SUPERSAMPLE = 4
_log = logging.getLogger(__name__)


class Palette(NamedTuple):
    fill: tuple[int, int, int]
    ring: tuple[int, int, int]
    mark: tuple[int, int, int]


# Assembly Reports' status hues (DESIGN.md), made bright enough to read at 16 px on a light or
# a dark taskbar; the darker ring keeps the light amber visible on a light one.
_LIGHT_MARK, _DARK_MARK = (254, 252, 253), (34, 28, 31)
COLOURS = {
    "green": Palette((33, 138, 69), (0, 71, 19), _LIGHT_MARK),
    "amber": Palette((242, 176, 54), (161, 112, 3), _DARK_MARK),
    "red": Palette((204, 52, 45), (115, 0, 0), _LIGHT_MARK),
    "grey": Palette((128, 120, 124), (65, 59, 62), _LIGHT_MARK),
}


# ----- drawing the icon


def draw_icon(colour: str, count: int = 0, *, size: int = 64) -> Image.Image:
    """A coloured circle with its state's mark: green a tick, amber the count (9+ above
    nine), red "!", grey pause bars. Drawn large and scaled down, so its edges are smooth."""
    if colour not in COLOURS:
        raise ValueError(f"the tray colour must be one of {', '.join(COLOURS)}, not {colour!r}")
    palette = COLOURS[colour]
    big = size * _SUPERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    edge = big / 32
    draw.ellipse((edge, edge, big - edge, big - edge), fill=palette.ring)
    ring = big / 16
    draw.ellipse((edge + ring, edge + ring, big - edge - ring, big - edge - ring), fill=palette.fill)
    if colour == "green":
        _tick(draw, big, palette.mark)
    elif colour == "amber" and count > 0:
        _count(draw, big, count, palette.mark)
    elif colour == "grey":
        _pause(draw, big, palette.mark)
    else:
        _bang(draw, big, palette.mark)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def _tick(draw: ImageDraw.ImageDraw, big: int, ink: tuple[int, int, int]) -> None:
    points = [(big * 0.29, big * 0.52), (big * 0.44, big * 0.67), (big * 0.72, big * 0.37)]
    draw.line(points, fill=ink, width=int(big * 0.11), joint="curve")
    radius = big * 0.055
    for x, y in (points[0], points[-1]):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=ink)


def _bang(draw: ImageDraw.ImageDraw, big: int, ink: tuple[int, int, int]) -> None:
    half = big * 0.065
    draw.rounded_rectangle((big / 2 - half, big * 0.24, big / 2 + half, big * 0.60),
                           radius=half, fill=ink)
    draw.ellipse((big / 2 - half, big * 0.66, big / 2 + half, big * 0.66 + 2 * half), fill=ink)


def _pause(draw: ImageDraw.ImageDraw, big: int, ink: tuple[int, int, int]) -> None:
    width = big * 0.12
    for left in (big * 0.33, big * 0.55):
        draw.rounded_rectangle((left, big * 0.30, left + width, big * 0.70),
                               radius=width / 3, fill=ink)


def _count(draw: ImageDraw.ImageDraw, big: int, count: int, ink: tuple[int, int, int]) -> None:
    text = str(count) if count <= 9 else "9+"
    font = _font(int(big * (0.62 if len(text) == 1 else 0.46)))
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    x = (big - (right - left)) / 2 - left
    y = (big - (bottom - top)) / 2 - top
    draw.text((x, y), text, font=font, fill=ink)


def _font(pixels: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Segoe UI Bold, as Windows draws its own badges; Pillow's own font if it is missing."""
    try:
        return ImageFont.truetype("segoeuib.ttf", pixels)
    except OSError:
        return ImageFont.load_default(pixels)


def fit_tooltip(text: str) -> str:
    """text cut to what a tray tooltip holds, ending with an ellipsis when cut. Only the end
    goes, so the SHADOW that starts it in shadow mode (views.tray_tooltip) always shows."""
    if _utf16_units(text) <= TOOLTIP_LIMIT:
        return text
    cut = text
    while _utf16_units(cut) > TOOLTIP_LIMIT - 1:
        cut = cut[:-1]
    return cut.rstrip() + "…"


def _utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


# ----- the menu and the icon's thread


@dataclass(frozen=True)
class TrayActions:
    """What the menu's items do; wave 2's supervisor provides them. Each runs on the tray's
    thread, so it should return quickly (hand long work to another thread)."""

    open_board: Callable[[], None]
    pause: Callable[[], None]
    resume: Callable[[], None]
    check_now: Callable[[], None]
    send_support_bundle: Callable[[], None]
    quit: Callable[[], None]


@dataclass(frozen=True)
class MenuEntry:
    """One menu item. default: the item a click on the icon runs (Open board)."""

    label: str
    action: Callable[[], None]
    default: bool = False


class TrayBackend(Protocol):
    """A tray icon: made, run and stopped on the tray's thread; show() may come from any."""

    def run(self) -> None:
        """Show the icon and handle its clicks until stop()."""
        ...

    def stop(self) -> None: ...

    def show(self, image: Image.Image, tooltip: str, menu: tuple[MenuEntry, ...]) -> None:
        """Replace the picture, the tooltip and the menu."""
        ...


BackendFactory = Callable[[Image.Image, str, tuple[MenuEntry, ...]], TrayBackend]


class Tray:
    """NG Runner's tray icon. start() once; update() whenever the job model's TrayState or the
    pause changes; stop() at shutdown (safe from any thread, the tray's own included)."""

    def __init__(
        self,
        actions: TrayActions,
        state: TrayState,
        *,
        paused: bool = False,
        confirm: Callable[[str], bool] | None = None,
        backend: BackendFactory | None = None,
    ) -> None:
        self._actions = actions
        self._state = state
        self._paused = paused
        self._confirm = confirm or ask_yes_no
        self._make_backend = backend or PystrayBackend
        self._lock = threading.Lock()
        self._icon: TrayBackend | None = None
        self._thread: threading.Thread | None = None
        self._made = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("this tray was already started")
        self._thread = threading.Thread(target=self._run, name="ngrunner-tray", daemon=True)
        self._thread.start()
        self._made.wait(5)

    def update(self, state: TrayState, *, paused: bool) -> None:
        with self._lock:
            self._state, self._paused = state, paused
            if self._icon is not None:
                self._icon.show(*self._picture())

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            icon, thread = self._icon, self._thread
        if icon is not None:
            icon.stop()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def menu(self) -> tuple[MenuEntry, ...]:
        """The menu as it is now: Pause turns into Resume while paused."""
        a = self._actions
        pause = ("Resume", a.resume) if self._paused else ("Pause", a.pause)
        items = (("Open board", a.open_board), pause, ("Check for new rows now", a.check_now),
                 ("Send support bundle", a.send_support_bundle), ("Quit", self._quit))
        return tuple(MenuEntry(label, self._guarded(label, action), default=(i == 0))
                     for i, (label, action) in enumerate(items))

    def _run(self) -> None:
        try:
            with self._lock:
                self._icon = self._make_backend(*self._picture())
            self._made.set()
            self._icon.run()
        except Exception:  # noqa: BLE001 - logged; NG Runner runs on without its icon
            _log.exception("the tray icon stopped with an error")
        finally:
            self._made.set()

    def _picture(self) -> tuple[Image.Image, str, tuple[MenuEntry, ...]]:
        return (draw_icon(self._state.colour, self._state.count), fit_tooltip(self._state.tooltip),
                self.menu())

    def _quit(self) -> None:
        if self._confirm(QUIT_QUESTION):
            self._actions.quit()

    @staticmethod
    def _guarded(label: str, action: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                action()
            except Exception:  # noqa: BLE001 - a failing item must not take the tray down
                _log.exception("the tray's %r item failed", label)

        return run


# ----- Windows: pystray and the question box


class PystrayBackend:
    """The real icon, through pystray (Windows' notification area). Made on the tray thread."""

    def __init__(self, image: Image.Image, tooltip: str, menu: tuple[MenuEntry, ...]) -> None:
        import pystray

        self._ready = threading.Event()
        self._icon = pystray.Icon("NGRunner", icon=image, title=tooltip, menu=pystray_menu(menu))

    def run(self) -> None:
        self._icon.run(setup=self._setup)

    def stop(self) -> None:
        self._ready.wait(5)  # pystray ignores a stop() that comes before its loop is ready
        self._icon.stop()

    def show(self, image: Image.Image, tooltip: str, menu: tuple[MenuEntry, ...]) -> None:
        self._icon.icon = image
        self._icon.title = tooltip
        self._icon.menu = pystray_menu(menu)

    def _setup(self, icon) -> None:
        icon.visible = True
        self._ready.set()


def pystray_menu(entries: tuple[MenuEntry, ...]):
    """entries as a pystray.Menu (pure Python: building it shows nothing)."""
    import pystray

    return pystray.Menu(*(pystray.MenuItem(e.label, _no_args(e.action), default=e.default)
                          for e in entries))


def _no_args(action: Callable[[], None]) -> Callable[[], None]:
    return lambda: action()  # pystray calls an action with as many arguments as it takes


def ask_yes_no(question: str) -> bool:
    """Ask in a Windows message box, No preselected so a stray Enter keeps NG Runner open."""
    yes_no, question_icon, no_first, foreground, topmost = 0x4, 0x20, 0x100, 0x10000, 0x40000
    flags = yes_no | question_icon | no_first | foreground | topmost
    return ctypes.windll.user32.MessageBoxW(None, question, "NG Runner", flags) == 6  # IDYES
