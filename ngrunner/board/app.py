"""
The board: one local page that is the record of NG Runner's work (design 4, Channels).

Why it is guarded so hard although it only listens on 127.0.0.1: any web page Jim opens can
make his browser send requests to 127.0.0.1, and a page on a hostile name that resolves to
127.0.0.1 (DNS rebinding) can even read the answers. So, per build-spec 6.6:

- A per-launch token. The tray opens the board with ?t=<token>; that first GET swaps the token
  for an HttpOnly, SameSite=Strict cookie and redirects to the same page without it. Every
  request after that needs the cookie, except the stylesheet and script, which hold no data.
- Host must be exactly 127.0.0.1:<port> on every request; that is what defeats rebinding. A
  POST also needs the token (form field or X-NGR-Token header) and Origin exactly
  http://127.0.0.1:<port>, so no other page can make Jim's browser act for it.
- Nothing changes on a GET: every action is a POST route, and GET on it is refused (405).
- A strict Content-Security-Policy with a fresh nonce per response: no external resources,
  no inline handlers, nothing framed.

The board computes nothing about jobs; it shows what views.board() returns and passes clicks
and typed text to actions (views.py). "Open" opens a file or folder only inside the roots it
was given (files.py).
"""
from __future__ import annotations

import hmac
import logging
import ntpath
import os
import re
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from flask import (
    Flask,
    Response,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from ngrunner.board import display
from ngrunner.board.files import open_inside
from ngrunner.board.views import BoardActions, BoardView, BoardViews, EventView
from ngrunner.clock import Clock, SystemClock, fmt_clock, fmt_day, pacific_day
from ngrunner.config import MODES
from ngrunner.messages.templates import LOCK_LABELS, LOCK_WITHOUT_CHECK, NOT_A_LOCK, RESEND, SETTLE_LABELS

COOKIE = "ngrunner_board"
TOKEN_FIELD = "token"
TOKEN_HEADER = "X-NGR-Token"
HOST = "127.0.0.1"
PAGES = ("board", "your_list", "cards", "history", "health")
_EXTENSION = "ngrunner_board"
_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]{32,}")
_READS = ("GET", "HEAD")
_FAILED = "That didn't go through: NG Runner hit a problem. Try again, or tell Sulaiman."
_NO_SUCH_MODE = "That didn't go through: NG Runner runs either in shadow or live. Nothing changed."
_NO_SUCH_SETTLE = "That didn't go through: an item is settled as not a problem, or as told to chat. Nothing changed."
_log = logging.getLogger(__name__)


@dataclass
class _Guard:
    """The board's token and its address, held by the app (one per launch)."""

    token: str
    port: int | None = None

    @property
    def host(self) -> str | None:
        return f"{HOST}:{self.port}" if self.port else None

    def matches(self, offered: str | None) -> bool:
        """offered is the token, compared in constant time."""
        return offered is not None and hmac.compare_digest(offered.encode(), self.token.encode())


# ----- making the board


def new_token() -> str:
    """A fresh per-launch token (build-spec 6.6)."""
    return secrets.token_urlsafe(32)


def create_board(
    views: BoardViews,
    actions: BoardActions,
    *,
    token: str,
    roots: Sequence[Path] = (),
    clock: Clock | None = None,
    opener: Callable[[str], None] | None = None,
) -> Flask:
    """The board as a Flask app. roots: the folders whose files and subfolders "Open" may open
    (none by default, so it opens nothing). opener opens a checked path (os.startfile).

    It refuses every request until set_port() tells it the port it is served on (BoardServer
    does that), since Host and Origin are checked exactly."""
    if not _TOKEN_SHAPE.fullmatch(token):
        raise ValueError("the board's token must be at least 32 URL-safe characters (new_token())")
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.extensions[_EXTENSION] = _Guard(token)
    app.config.update(
        SECRET_KEY=secrets.token_bytes(32),  # signs the one-line notice after an action
        SESSION_COOKIE_NAME="ngrunner_notice",  # cookies are shared by every port on 127.0.0.1
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    app.before_request(_new_nonce)
    app.before_request(_check_request)
    app.after_request(_headers)
    app.context_processor(_template_values)
    _pages(app, views, clock or SystemClock())
    _actions(app, actions, tuple(roots), opener or os.startfile)
    app.register_error_handler(404, _missing_page)
    app.register_error_handler(500, _failed_page)
    return app


def set_port(app: Flask, port: int) -> None:
    """Tell the board the port it is served on; until then it refuses every request."""
    if not 0 < port < 65536:
        raise ValueError(f"{port} is not a TCP port")
    _guard(app).port = port


def open_url(app: Flask) -> str:
    """The address that opens the board in a browser, carrying the token the first GET swaps
    for its cookie. Only known once set_port() has run. It is a secret: never log it."""
    guard = _guard(app)
    if guard.host is None:
        raise RuntimeError("the board has no port yet: set_port() first")
    return f"http://{guard.host}/?{urlencode({'t': guard.token})}"


def _guard(app: Flask) -> _Guard:
    return app.extensions[_EXTENSION]


# ----- every request


def _new_nonce() -> None:
    g.csp_nonce = secrets.token_urlsafe(16)


def _check_request() -> Response | None:
    """Refuse anything that is not Jim's browser on the board's own page (see the module
    docstring); None lets the request through."""
    guard = _guard(current_app)
    if guard.host is None or request.headers.get("Host") != guard.host:
        return _refused("the Host header is not the board's address")
    offered = request.args.get("t")
    if request.method in _READS and offered is not None:
        return _swap_token(guard, offered)
    if request.method in _READS and request.endpoint == "static":
        return None  # the stylesheet and the script hold no data
    if not guard.matches(request.cookies.get(COOKIE)):
        return _refused("no board cookie, or an old one")
    if request.method in _READS:
        return None
    if request.headers.get("Origin") != f"http://{guard.host}":
        return _refused("the request did not come from the board's own page")
    if not guard.matches(request.form.get(TOKEN_FIELD) or request.headers.get(TOKEN_HEADER)):
        return _refused("no token, or a wrong one, with the request")
    return None


def _swap_token(guard: _Guard, offered: str) -> Response:
    if not guard.matches(offered):
        return _refused("the link's token is not this launch's")
    response = redirect(request.path, code=303)
    response.set_cookie(COOKIE, guard.token, httponly=True, samesite="Strict", path="/")
    return response


def _refused(why: str) -> Response:
    """The same plain page for every refusal; the reason goes to the log only, so a probing
    page learns nothing about which check it failed."""
    _log.info("board refused %s %s: %s", request.method, request.path, why)
    return Response(render_template("refused.html"), status=403)


def _headers(response: Response) -> Response:
    nonce = getattr(g, "csp_nonce", "")
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        f"style-src 'self' 'nonce-{nonce}'; "
        "img-src 'self'; font-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # same-origin, not no-referrer: under no-referrer a browser sends "Origin: null" with the
    # board's own form posts, and the exact Origin check would refuse every click.
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"  # pages name clients: keep them off disk
    return response


def _template_values() -> dict[str, object]:
    return {
        "csp_nonce": getattr(g, "csp_nonce", ""),
        "token": _guard(current_app).token,
        "page": request.endpoint,
        "tabs": _tabs,
        "tray_style": display.tray_style,
        "tick_style": display.TICKS.__getitem__,
        "overlay_style": display.OVERLAYS.__getitem__,
        "nest_style": display.nest_style,
        "takeoff_style": display.TAKEOFFS.__getitem__,
        "check_style": display.CHECKS.__getitem__,
        "check_item_style": display.check_item_style,
        "settle_labels": SETTLE_LABELS,
        "lock_button_style": display.LOCK_BUTTONS.__getitem__,
        "batch_style": display.BATCHES.__getitem__,
        "fix_style": display.FIXES.__getitem__,
        "lease_style": display.LEASES.__getitem__,
        "sheet_style": display.JOB_SHEETS.__getitem__,
        "lock_without_check": LOCK_WITHOUT_CHECK,
        "resend_label": RESEND,
        "toasts_style": display.toasts_style,
        "path_style": display.path_style,
        "mode_style": display.mode_style,
        "install_text": display.install_text,
        "age_text": display.age_text,
        "count_text": _count_text,
    }


def _tabs(view: BoardView) -> list[tuple[str, str, int]]:
    return [
        ("board", "Board", 0),
        ("your_list", "Your list", len(view.your_list)),
        ("cards", "Cards", len(view.cards)),
        ("history", "History", 0),
        ("health", "Health", 0),
    ]


def _count_text(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _missing_page(_error: Exception) -> tuple[str, int]:
    return render_template("missing.html"), 404


def _failed_page(_error: Exception) -> tuple[str, int]:
    return render_template("failed.html"), 500


# ----- pages (GET only: nothing here changes anything)


def _pages(app: Flask, views: BoardViews, clock: Clock) -> None:
    @app.get("/")
    def board() -> str:
        return render_template("board.html", view=views.board())

    @app.get("/list")
    def your_list() -> str:
        return render_template("list.html", view=views.board())

    @app.get("/cards")
    def cards() -> str:
        view = views.board()
        return render_template("cards.html", view=view, example=_example(view))

    @app.get("/history")
    def history() -> str:
        now = clock.now()
        days = _by_day(views.history(), now)
        return render_template("history.html", view=views.board(), days=days)

    @app.get("/health")
    def health() -> str:
        now = clock.now()
        return render_template("health.html", view=views.board(),
                               stamp=lambda when, never: display.stamp_text(when, now, never))


def _example(view: BoardView) -> str:
    """A command Jim could type now: a real open card when there is one (build-spec 6.5)."""
    for card in view.cards:
        if card.choices:
            return f"{card.id} {card.choices[0]}"
    return "pause"


def _by_day(events: Sequence[EventView],
            now: datetime) -> list[tuple[str, list[tuple[str, EventView]]]]:
    """Jim's events grouped by Pacific day, newest first. DEV lines are Sulaiman's and are
    never shown here (design 2: a DEV lane Jim never sees)."""
    days: list[tuple[str, list[tuple[str, EventView]]]] = []
    current = None
    for event in sorted((e for e in events if e.lane != "DEV"), key=lambda e: e.at, reverse=True):
        day = pacific_day(event.at)
        if day != current:
            label = "Today" if day == pacific_day(now) else fmt_day(day)
            days.append((label, []))
            current = day
        days[-1][1].append((fmt_clock(event.at), event))
    return days


# ----- actions (POST only, after every check in _check_request)


def _actions(app: Flask, actions: BoardActions, roots: tuple[Path, ...],
             opener: Callable[[str], None]) -> None:
    @app.post("/cards/answer")
    def answer() -> Response:
        return _run(lambda: actions.answer(request.form.get("card", ""),
                                           request.form.get("choice", "")))

    @app.post("/command")
    def command() -> Response:
        return _run(lambda: actions.command(request.form.get("text", "")))

    @app.post("/pause")
    def pause() -> Response:
        return _run(actions.pause)

    @app.post("/resume")
    def resume() -> Response:
        return _run(actions.resume)

    @app.post("/check")
    def check_now() -> Response:
        return _run(actions.check_now)

    @app.post("/add-job")
    def add_job() -> Response:
        return _run(lambda: actions.add_job(request.form.get("text", "")))

    @app.post("/mode")
    def set_mode() -> Response:
        mode = request.form.get("mode", "")
        if mode not in MODES:  # going live is never a guess: only an exact mode gets through
            flash(_NO_SUCH_MODE, "bad")
            return _back()
        return _run(lambda: actions.set_mode(mode))

    @app.post("/checks/check")
    def check_job() -> Response:
        return _run(lambda: actions.check_job(request.form.get("studio_id", "")))

    @app.post("/checks/settle")
    def settle_item() -> Response:
        how = request.form.get("how", "")
        if how not in SETTLE_LABELS:     # a settle is never a guess: only an exact kind gets through
            flash(_NO_SUCH_SETTLE, "bad")
            return _back()
        return _run(lambda: actions.settle_item(request.form.get("studio_id", ""), request.form.get("run_id", ""),
                                                request.form.get("n", ""), how))

    @app.post("/checks/prompt")
    def answer_prompt() -> Response:
        return _run(lambda: actions.answer_prompt(request.form.get("prompt_id", ""),
                                                  request.form.get("choice", "")))

    @app.post("/lock/click")
    def lock_click() -> Response:
        click = request.form.get("click", "")
        if click not in LOCK_LABELS:     # a lock is never a guess: only one of Jim's three gets through
            flash(NOT_A_LOCK, "bad")
            return _back()
        return _run(lambda: actions.lock_click(request.form.get("studio_id", ""), click,
                                               request.form.get("material", ""),
                                               request.form.get("without_check", "") == "yes"))

    @app.post("/lock/resend")
    def resend_row() -> Response:
        return _run(lambda: actions.resend_row(request.form.get("batch", "")))

    @app.post("/lock/undo")
    def undo_fix() -> Response:
        return _run(lambda: actions.undo_fix(request.form.get("fix", "")))

    @app.post("/open")
    def open_file() -> Response:
        text = request.form.get("path", "")
        reason = open_inside(text, roots, opener)
        if reason:
            flash(reason, "bad")
        else:  # the window can take a moment: say it is coming, so one click is enough
            name = ntpath.basename(text.strip().rstrip("\\/"))
            flash(f"Opening {name}.", "ok")
        return _back()


def _run(action: Callable[[], str]) -> Response:
    """Do one action and show its reply. Any failure shows plain words, never the error."""
    try:
        reply = action()
    except Exception:  # noqa: BLE001 - an action's error must never reach Jim's page as text
        _log.exception("a board action failed")
        flash(_FAILED, "bad")
    else:
        if reply:
            flash(reply, "ok")
    return _back()


def _back() -> Response:
    """Back to the page the form was on (only one of the board's own pages, never a URL)."""
    page = request.form.get("back", "")
    return redirect(url_for(page if page in PAGES else "board"), code=303)
