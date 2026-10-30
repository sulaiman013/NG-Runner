"""The local board: its security rules first, then its pages, then opening files.

Everything runs through Flask's test client against create_board() with fake views and a fake
actions object, so no socket is opened and nothing outside tmp_path is touched. Names are
synthetic (Testerson, Fakeley, Mockford, Dummitt).
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ngrunner.board import create_board, set_port
from ngrunner.board.views import (
    BoardView,
    CardView,
    EventView,
    HealthView,
    JobRow,
    ListItem,
    PathCheck,
    Tick,
    TrayState,
    tray_tooltip,
)
from ngrunner.cards import new_card
from ngrunner.clock import FakeClock
from ngrunner.messages import templates

TOKEN = "t" * 43
PORT = 50123
BASE = f"http://127.0.0.1:{PORT}"
NOW = datetime(2026, 10, 30, 22, 0, tzinfo=timezone.utc)  # Fri 10/30 3:00 PM Pacific
SHADOW_TRAY = TrayState("green", 0, tray_tooltip("NG Runner is running", shadow=True))


# ----- fakes


def _ticks(*states: str) -> tuple[Tick, ...]:
    labels = ("Row", "Job", "Summary", "Filed", "Branch")
    return tuple(Tick(label.lower(), label, state) for label, state in zip(labels, states))


def _health(**changes) -> HealthView:
    values = dict(
        paths=(PathCheck("NG Jobs", Path(r"C:\fake\NG Jobs"), True, "Reachable"),),
        last_export=datetime(2026, 10, 30, 21, 58, tzinfo=timezone.utc),
        list_synced_at=datetime(2026, 10, 30, 21, 51, tzinfo=timezone.utc),
        heartbeat=datetime(2026, 10, 30, 21, 57, tzinfo=timezone.utc),
        toasts=True,
    )
    values.update(changes)
    return HealthView(**values)


def _view(**changes) -> BoardView:
    values = dict(
        jobs=(
            JobRow("900001", "Testerson_101426", date(2026, 11, 4),
                   _ticks("done", "done", "done", "done", "done"), "Filed 10/30 2:05 PM", None),
            JobRow("900002", "Fakeley_102726", date(2026, 11, 9),
                   _ticks("done", "done", "todo", "todo", "na"), "Second fetch at 3:15 PM",
                   "WAITING"),
        ),
        your_list=(ListItem("Fakeley_102726: closet or garage? Card FAK-1.", job="Fakeley_102726",
                            age_days=1, card_id="FAK-1"),),
        cards=(CardView("FAK-1", "Fakeley_102726", "Closet or garage?", ("Closet", "Garage"), 1),),
        health=_health(),
        tray=TrayState("amber", 1, "NG Runner: 1 item for you"),
        paused=False,
        shadow=False,
    )
    values.update(changes)
    return BoardView(**values)


@dataclass
class FakeViews:
    view: BoardView = field(default_factory=_view)
    events: tuple[EventView, ...] = ()

    def board(self) -> BoardView:
        return self.view

    def history(self) -> tuple[EventView, ...]:
        return self.events


@dataclass
class FakeActions:
    calls: list[tuple] = field(default_factory=list)
    reply: str = "Done."

    def _record(self, *call) -> str:
        self.calls.append(call)
        return self.reply

    def answer(self, card_id: str, choice: str) -> str:
        return self._record("answer", card_id, choice)

    def command(self, text: str) -> str:
        return self._record("command", text)

    def pause(self) -> str:
        return self._record("pause")

    def resume(self) -> str:
        return self._record("resume")

    def check_now(self) -> str:
        return self._record("check_now")

    def add_job(self, text: str) -> str:
        return self._record("add_job", text)

    def set_mode(self, mode: str) -> str:
        return self._record("set_mode", mode)


@pytest.fixture
def actions() -> FakeActions:
    return FakeActions()


@pytest.fixture
def views() -> FakeViews:
    return FakeViews()


@pytest.fixture
def app(views: FakeViews, actions: FakeActions):
    board = create_board(views, actions, token=TOKEN, clock=FakeClock(NOW))
    set_port(board, PORT)
    return board


@pytest.fixture
def anonymous(app):
    """A browser that has not opened the board's link yet."""
    return app.test_client()


@pytest.fixture
def raw(app):
    """A client that sends exactly the headers a test gives it (no cookie jar)."""
    return app.test_client(use_cookies=False)


@pytest.fixture
def browser(app):
    """A browser that opened the link from the tray, so it holds the token cookie."""
    client = app.test_client()
    client.get(f"/?t={TOKEN}", base_url=BASE)
    return client


# ----- the token and its cookie


def test_the_link_with_the_token_sets_a_strict_http_only_cookie_and_drops_the_token(anonymous):
    response = anonymous.get(f"/?t={TOKEN}", base_url=BASE)

    assert response.status_code == 303
    assert response.headers["Location"] == "/"
    cookie = anonymous.get_cookie("ngrunner_board", domain="127.0.0.1")
    assert cookie is not None and cookie.value == TOKEN
    assert cookie.http_only and cookie.same_site == "Strict"


def test_with_the_cookie_the_board_shows_the_jobs(browser):
    response = browser.get("/", base_url=BASE)

    assert response.status_code == 200
    assert "Testerson_101426" in response.get_data(as_text=True)


def test_without_the_cookie_the_board_says_how_to_open_it_and_shows_no_jobs(anonymous):
    response = anonymous.get("/", base_url=BASE)

    page = response.get_data(as_text=True)
    assert response.status_code == 403
    assert "Testerson" not in page
    assert "Open board" in page  # the way in: the tray's menu item


def test_a_wrong_token_in_the_link_sets_no_cookie(anonymous):
    response = anonymous.get("/?t=" + "x" * 43, base_url=BASE)

    assert response.status_code == 403
    assert anonymous.get_cookie("ngrunner_board", domain="127.0.0.1") is None


def _with_cookie(host: str) -> dict[str, str]:
    """Headers that carry the right cookie to another host name. A browser would not send
    it there, so this proves the Host check on its own, not the cookie check."""
    return {"Host": host, "Cookie": f"ngrunner_board={TOKEN}"}


def test_another_host_name_is_refused_even_with_the_cookie(raw):
    """DNS rebinding: a hostile name resolving to 127.0.0.1 reaches the port, but its Host
    header gives it away."""
    response = raw.get("/", base_url=BASE, headers=_with_cookie(f"rebind.example.com:{PORT}"))

    assert response.status_code == 403
    assert "Testerson" not in response.get_data(as_text=True)


def test_localhost_is_refused_too_since_only_the_exact_address_is_trusted(raw):
    response = raw.get("/", base_url=BASE, headers=_with_cookie(f"localhost:{PORT}"))

    assert response.status_code == 403


def test_the_right_cookie_on_the_right_host_is_let_in(raw):
    """The control for the two tests above: the same headers with the board's own address."""
    response = raw.get("/", base_url=BASE, headers=_with_cookie(f"127.0.0.1:{PORT}"))

    assert response.status_code == 200


def test_before_it_knows_its_port_the_board_refuses_everything(views, actions):
    unbound = create_board(views, actions, token=TOKEN, clock=FakeClock(NOW))
    client = unbound.test_client()

    assert client.get(f"/?t={TOKEN}", base_url=BASE).status_code == 403
    assert client.get_cookie("ngrunner_board", domain="127.0.0.1") is None


def test_a_short_token_is_refused_when_the_board_is_made(views, actions):
    with pytest.raises(ValueError):
        create_board(views, actions, token="short")


# ----- actions: POST only, with the token and the board's own Origin

ACTION_ROUTES = ("/pause", "/resume", "/check", "/command", "/add-job", "/cards/answer", "/open",
                 "/mode")


def _post(client, path: str, data: dict | None = None, *, token: str | None = TOKEN,
          origin: str | None = BASE, host: str | None = None, header_token: str | None = None):
    form = dict(data or {})
    if token is not None:
        form["token"] = token
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    if host is not None:
        headers["Host"] = host
    if header_token is not None:
        headers["X-NGR-Token"] = header_token
    return client.post(path, data=form, headers=headers, base_url=BASE)


def test_a_click_with_the_token_from_the_boards_page_runs_the_action(browser, actions):
    response = _post(browser, "/pause", {"back": "health"})

    assert actions.calls == [("pause",)]
    assert response.status_code == 303
    assert response.headers["Location"] == "/health"


def test_the_token_may_come_in_a_header_instead_of_the_form(browser, actions):
    _post(browser, "/check", token=None, header_token=TOKEN)

    assert actions.calls == [("check_now",)]


@pytest.mark.parametrize("change", [
    {"token": None},
    {"token": "x" * 43},
    {"origin": None},
    {"origin": "https://evil.example.com"},
    {"origin": f"http://localhost:{PORT}"},
    {"origin": f"http://127.0.0.1:{PORT + 1}"},
    {"host": f"rebind.example.com:{PORT}"},
], ids=["no token", "wrong token", "no origin", "other site", "localhost", "other port",
        "other host"])
@pytest.mark.parametrize("path, data", [("/pause", {}), ("/mode", {"mode": "live"})],
                         ids=["pause", "going live"])
def test_a_post_that_fails_any_check_is_refused_and_does_nothing(browser, actions, change, path,
                                                                 data):
    response = _post(browser, path, data, **change)

    assert response.status_code == 403
    assert actions.calls == []


@pytest.mark.parametrize("path, data", [("/pause", {}), ("/mode", {"mode": "live"})],
                         ids=["pause", "going live"])
def test_a_post_without_the_cookie_is_refused_even_with_the_token(anonymous, actions, path, data):
    response = _post(anonymous, path, data)

    assert response.status_code == 403
    assert actions.calls == []


@pytest.mark.parametrize("host, status", [(f"rebind.example.com:{PORT}", 403),
                                          (f"127.0.0.1:{PORT}", 303)], ids=["other", "own"])
def test_a_post_with_cookie_token_and_origin_still_needs_the_boards_host(raw, actions, host,
                                                                        status):
    headers = _with_cookie(host) | {"Origin": BASE}

    response = raw.post("/pause", data={"token": TOKEN}, headers=headers, base_url=BASE)

    assert response.status_code == status
    assert len(actions.calls) == (status == 303)


@pytest.mark.parametrize("path", ACTION_ROUTES)
def test_nothing_changes_on_a_get(browser, actions, path):
    response = browser.get(f"{path}?text=pause&card=FAK-1&choice=Garage&path=C:/x.pdf&mode=live",
                           base_url=BASE)

    assert response.status_code == 405
    assert actions.calls == []


def test_after_a_post_the_board_goes_back_only_to_its_own_pages(browser):
    response = _post(browser, "/pause", {"back": "https://evil.example.com/"})

    assert response.headers["Location"] == "/"


# ----- the Content-Security-Policy and what the pages are made of

BOARD_DIR = Path(__file__).resolve().parent.parent / "ngrunner" / "board"
PAGES = ("/", "/list", "/cards", "/history", "/health")


def _csp(response) -> dict[str, str]:
    policy = response.headers["Content-Security-Policy"]
    return {part.split()[0]: part.strip() for part in policy.split(";") if part.strip()}


@pytest.mark.parametrize("path", PAGES)
def test_every_page_carries_a_strict_policy_whose_nonce_is_on_every_script_and_style(browser,
                                                                                     path):
    import re

    response = browser.get(path, base_url=BASE)
    csp = _csp(response)
    nonce = re.search(r"'nonce-([^']+)'", csp["script-src"]).group(1)
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert csp["default-src"] == "default-src 'self'"
    assert csp["object-src"] == "object-src 'none'"
    assert csp["frame-ancestors"] == "frame-ancestors 'none'"
    assert csp["base-uri"] == "base-uri 'none'"
    assert csp["form-action"] == "form-action 'self'"
    assert f"'nonce-{nonce}'" in csp["style-src"]
    assert "unsafe-inline" not in response.headers["Content-Security-Policy"]
    tags = re.findall(r"<(?:script|style|link)\b[^>]*>", page)
    assert tags and all(f'nonce="{nonce}"' in tag for tag in tags)


def test_each_response_gets_a_fresh_nonce(browser):
    first = _csp(browser.get("/", base_url=BASE))["script-src"]
    second = _csp(browser.get("/", base_url=BASE))["script-src"]

    assert first != second


def test_refusals_carry_the_policy_too(anonymous):
    response = anonymous.get("/", base_url=BASE)

    assert _csp(response)["default-src"] == "default-src 'self'"


def test_pages_are_never_stored_and_never_framed(browser):
    response = browser.get("/", base_url=BASE)

    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    # Not no-referrer: under it, browsers send "Origin: null" with the board's own form posts
    # (found with a real browser), and the Origin check then refuses every click.
    assert response.headers["Referrer-Policy"] == "same-origin"


def test_no_template_or_static_file_names_anything_outside_the_app():
    import re

    external = re.compile(r"(?i)(?:https?:|wss?:|ftp:)?//[a-z0-9\[]|\burl\(\s*['\"]?(?:https?:|//)"
                          r"|@import|javascript:")
    files = [p for p in BOARD_DIR.rglob("*") if p.is_file() and p.suffix in (".html", ".css", ".js")]

    found = {p.name: m.group(0) for p in files
             if (m := external.search(p.read_text(encoding="utf-8")))}

    assert len(files) >= 8 and found == {}


def test_no_page_asks_browsers_to_send_no_referrer():
    """A no-referrer policy makes browsers send "Origin: null" on the board's own posts."""
    found = [p.name for p in (BOARD_DIR / "templates").glob("*.html")
             if "no-referrer" in p.read_text(encoding="utf-8")]

    assert found == []


def test_no_template_has_an_inline_handler_or_style_attribute():
    import re

    inline = re.compile(r"(?i)<[^>]+\s(?:on[a-z]+|style)\s*=")
    found = [p.name for p in (BOARD_DIR / "templates").glob("*.html")
             if inline.search(p.read_text(encoding="utf-8"))]

    assert found == []


def test_the_stylesheet_loads_without_the_cookie_since_it_holds_no_data(anonymous):
    response = anonymous.get("/static/board.css", base_url=BASE)

    assert response.status_code == 200
    assert "--accent" in response.get_data(as_text=True)


# ----- the pages


def _html(client, path: str) -> str:
    response = client.get(path, base_url=BASE)
    assert response.status_code == 200
    return " ".join(response.get_data(as_text=True).split())  # one space between words


def _page(client, path: str) -> str:
    """The page with entities read back (I didn&#39;t becomes I didn't), for checking words."""
    return html.unescape(_html(client, path))


def _row(page: str, name: str) -> str:
    """The table row of one job, as text."""
    start = page.index(name)
    return page[start: page.index("</tr>", start)]


def test_the_board_shows_each_job_with_its_install_date_ticks_next_step_and_overlay(browser):
    page = _page(browser, "/")

    fakeley = _row(page, "Fakeley_102726")
    assert "Mon 11/9" in fakeley
    assert "Second fetch at 3:15 PM" in fakeley
    assert "Waiting" in fakeley
    assert "<span>Summary</span><span class=\"vh\">: to do</span>" in fakeley
    assert "<span>Branch</span><span class=\"vh\">: not built yet</span>" in fakeley
    testerson = _row(page, "Testerson_101426")
    assert "Wed 11/4" in testerson and "Filed 10/30 2:05 PM" in testerson
    assert testerson.count(": done</span>") == 5


def test_an_install_date_the_job_model_could_not_read_is_shown_in_its_words(browser, views):
    views.view = _view(jobs=(JobRow("900003", "Mockford_110226", "could not verify",
                                    _ticks("done", "todo", "todo", "todo", "na"),
                                    "Waiting for a fresh job list"),))

    assert "could not verify" in _row(_page(browser, "/"), "Mockford_110226")


def test_names_are_shown_as_text_never_as_markup(browser, views):
    views.view = _view(jobs=(JobRow("900004", "<script>alert(1)</script>", None, (), "x"),))

    page = _html(browser, "/")

    assert "<script>alert(1)" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_a_fab_row_with_no_job_yet_is_a_row_on_the_board(browser, views):
    views.view = _view(jobs=(JobRow(None, "Mockford closet", None,
                                    _ticks("done", "todo", "todo", "todo", "na"),
                                    "Card MOC-1: which job is this row?"),))

    row = _row(_page(browser, "/"), "Mockford closet")

    assert "Card MOC-1: which job is this row?" in row and "Not set" in row
    assert "None" not in row


@pytest.mark.parametrize("job", [None, 900001, ("Fakeley_102726",)],
                         ids=["no job", "an ID", "a tuple"])
def test_a_card_is_refused_when_made_with_anything_but_a_name_as_its_job(job):
    """Otherwise the Cards page shows the word None on a which-job card (the job model passes
    templates.card_subject(card), which is the job's name or the row as Barbara typed it)."""
    with pytest.raises(ValueError, match="a card's job"):
        CardView("MOC-1", job, "Which job is this row?", ("None of these",), 0)


def test_a_which_job_card_shows_the_row_it_asks_about_and_never_the_word_none(browser, views):
    """The join the job model makes: a card with no job yet stands in the Fab row it asks about."""
    card = new_card(templates.which_job_card("Mockford closet"), created_at=NOW,
                    allocate=lambda prefix: 1)
    assert card.job is None
    views.view = _view(cards=(CardView(card.id, templates.card_subject(card), card.text,
                                       card.choices, 0),))

    page = _page(browser, "/cards")

    assert '<span class="job-name">Fab row "Mockford closet"</span>' in page


def test_the_board_with_no_jobs_says_where_jobs_come_from(browser, views):
    views.view = _view(jobs=())

    assert "When Barbara adds a row to the NG Fab Schedule" in _page(browser, "/")


def test_the_tabs_count_what_waits_and_the_status_names_the_tray_state(browser):
    page = _page(browser, "/")

    assert 'Your list<span class="count">1</span>' in page
    assert 'Cards<span class="count">1</span>' in page
    assert "1 for you" in page and "NG Runner: 1 item for you" in page


def test_while_running_the_board_offers_pause_and_while_paused_resume(browser, views):
    running = _page(browser, "/")
    views.view = _view(paused=True, tray=TrayState("grey", 0, "NG Runner is paused"))
    paused = _page(browser, "/")

    assert 'action="/pause"' in running and 'action="/resume"' not in running
    assert 'action="/resume"' in paused and 'action="/pause"' not in paused
    assert "Paused." in paused


# ----- shadow or live


@pytest.mark.parametrize("path", PAGES)
def test_in_shadow_mode_every_page_says_so_in_a_banner(browser, views, path):
    views.view = _view(shadow=True, tray=SHADOW_TRAY)

    page = _page(browser, path)

    assert '<div class="banner banner--shadow"' in page
    assert "<strong>SHADOW.</strong>" in page
    assert "nothing goes into NG Jobs" in page and "every message goes to Sulaiman only" in page


@pytest.mark.parametrize("path", PAGES)
def test_when_live_no_page_says_shadow(browser, path):
    page = _page(browser, path)

    assert "SHADOW" not in page and "banner--shadow" not in page


def test_shadow_and_paused_each_keep_their_own_banner(browser, views):
    views.view = _view(shadow=True, paused=True,
                       tray=TrayState("grey", 0, tray_tooltip("NG Runner is paused", shadow=True)))

    page = _page(browser, "/")

    assert "<strong>SHADOW.</strong>" in page and "<strong>Paused.</strong>" in page
    assert 'action="/resume"' in page


def test_in_shadow_mode_the_trays_tooltip_starts_shadow():
    assert tray_tooltip("NG Runner: 1 item for you", shadow=True).startswith("SHADOW")
    assert tray_tooltip("NG Runner: 1 item for you", shadow=False) == "NG Runner: 1 item for you"


def test_the_status_beside_the_tabs_carries_the_trays_shadow_tooltip(browser, views):
    views.view = _view(shadow=True, tray=SHADOW_TRAY)

    assert tray_tooltip("NG Runner is running", shadow=True) in _page(browser, "/")


@pytest.mark.parametrize("change", [
    {"shadow": True},
    {"shadow": False, "tray": SHADOW_TRAY},
], ids=["shadow with a plain tooltip", "live with a SHADOW tooltip"])
def test_a_view_whose_tray_disagrees_about_the_mode_fails_where_it_is_made(change):
    with pytest.raises(ValueError, match="SHADOW"):
        _view(**change)


@pytest.mark.parametrize("shadow", ["shadow", "live", None, 1], ids=str)
def test_a_view_given_anything_but_true_or_false_for_shadow_fails_where_it_is_made(shadow):
    """settings.mode passed straight in would show the banner on a live board too."""
    with pytest.raises(ValueError, match="shadow must be"):
        _view(shadow=shadow)


def test_a_view_can_not_be_made_without_saying_which_mode_it_is_in():
    values = dict(jobs=(), your_list=(), cards=(), health=_health(),
                  tray=TrayState("green", 0, "NG Runner is running"), paused=False)

    with pytest.raises(TypeError, match="shadow"):
        BoardView(**values)


def _mode_section(page: str) -> str:
    start = page.index('<h2 class="facts-title">Mode</h2>')
    return page[start: page.index("</section>", start)]


def test_health_in_shadow_mode_says_so_and_offers_going_live_behind_a_second_step(browser, views):
    views.view = _view(shadow=True, tray=SHADOW_TRAY)

    section = _mode_section(_page(browser, "/health"))

    assert "<span>SHADOW</span>" in section and "control folder" in section
    assert '<input type="hidden" name="mode" value="live">' in section
    assert 'value="shadow"' not in section
    assert section.index("<summary") < section.index("<form") < section.index("Yes, go live")
    assert section.index('action="/mode"') < section.index("</details>")


def test_health_when_live_says_so_and_offers_going_back_behind_a_second_step(browser):
    section = _mode_section(_page(browser, "/health"))

    assert "<span>Live</span>" in section and "SHADOW" not in section
    assert '<input type="hidden" name="mode" value="shadow">' in section
    assert 'value="live"' not in section
    assert section.index("<summary") < section.index("<form") < section.index("Yes, back to shadow")
    assert "Nothing it has filed is removed" in section


def test_the_switch_carries_what_every_post_needs_and_comes_back_to_health(browser):
    form = _mode_section(_page(browser, "/health"))

    assert f'<input type="hidden" name="token" value="{TOKEN}">' in form
    assert '<input type="hidden" name="back" value="health">' in form


@pytest.mark.parametrize("path", [p for p in PAGES if p != "/health"])
def test_only_health_offers_the_switch(browser, path):
    assert 'action="/mode"' not in _page(browser, path)


def test_a_switch_from_the_boards_own_page_runs_the_action_with_that_mode(browser, actions):
    actions.reply = "NG Runner is live."

    response = _post(browser, "/mode", {"mode": "live", "back": "health"})

    assert actions.calls == [("set_mode", "live")]
    assert response.status_code == 303 and response.headers["Location"] == "/health"
    assert "NG Runner is live." in _page(browser, "/health")


@pytest.mark.parametrize("data", [
    {"mode": "LIVE"}, {"mode": "Live"}, {"mode": " live"}, {"mode": "live "}, {"mode": ""},
    {"mode": "dry run"}, {},
], ids=["capitals", "title case", "space before", "space after", "blank", "other", "none"])
def test_a_mode_the_board_does_not_know_changes_nothing_and_says_so(browser, actions, data):
    response = _post(browser, "/mode", data | {"back": "health"})

    assert actions.calls == []
    assert response.status_code == 303
    assert "Nothing changed" in _page(browser, "/health")


def test_a_mode_in_the_address_instead_of_the_form_changes_nothing(browser, actions):
    _post(browser, "/mode?mode=live", {"back": "health"})

    assert actions.calls == []


def test_a_switch_that_fails_shows_plain_words_and_never_the_error(browser, actions):
    def broken(mode: str) -> str:
        raise OSError(f"could not save C:\\secret\\settings.json for {mode}")

    actions.set_mode = broken

    _post(browser, "/mode", {"mode": "live", "back": "health"})
    page = _page(browser, "/health")

    assert "That didn't go through" in page
    assert "settings.json" not in page and "OSError" not in page


# ----- Your list


def test_your_list_numbers_each_item_with_its_answer_and_open_buttons(browser, views, tmp_path):
    summary = tmp_path / "Dummitt_103026_summary.pdf"
    views.view = _view(your_list=(
        ListItem("Fakeley_102726: closet or garage? Card FAK-1.", job="Fakeley_102726",
                 age_days=2, card_id="FAK-1"),
        ListItem("Dummitt_103026: IC changed, which render governs?", job="Dummitt_103026",
                 file=summary),
    ))

    page = _page(browser, "/list")

    assert '<span class="row-num">1.</span>' in page and '<span class="row-num">2.</span>' in page
    assert 'href="/cards#card-FAK-1"' in page and "2 days old" in page
    assert f'name="path" value="{summary}"' in page


def test_an_empty_list_says_what_would_be_there(browser, views):
    views.view = _view(your_list=())

    assert "Nothing waits for you" in _page(browser, "/list")


def test_cards_show_each_question_with_a_button_per_choice_and_the_command_box(browser):
    page = _page(browser, "/cards")

    assert 'id="card-FAK-1"' in page and "Closet or garage?" in page
    assert '<input type="hidden" name="choice" value="Closet">' in page
    assert '<input type="hidden" name="choice" value="Garage">' in page
    assert 'action="/command"' in page
    assert "<code>FAK-1 Closet</code>" in page  # the example names a real open card


def test_a_card_button_passes_the_card_and_the_choice_exactly(browser, actions):
    actions.reply = "Saved: FAK-1, Garage"

    response = _post(browser, "/cards/answer", {"card": "FAK-1", "choice": "Garage",
                                                "back": "cards"})

    assert actions.calls == [("answer", "FAK-1", "Garage")]
    assert response.headers["Location"] == "/cards"
    assert "Saved: FAK-1, Garage" in _page(browser, "/cards")


def test_the_command_box_passes_the_text_through_exactly_as_typed(browser, actions):
    actions.reply = "I didn't understand. Try: FAK-1 Garage"

    _post(browser, "/command", {"text": "  fak-1   garage please ", "back": "cards"})

    assert actions.calls == [("command", "  fak-1   garage please ")]
    assert "I didn't understand. Try: FAK-1 Garage" in _page(browser, "/cards")


def test_add_job_passes_the_typed_name_through(browser, actions):
    _post(browser, "/add-job", {"text": "Mockford_110226"})

    assert actions.calls == [("add_job", "Mockford_110226")]


def test_a_failing_action_shows_plain_words_and_never_the_error(browser, actions):
    def broken() -> str:
        raise RuntimeError("database is locked at C:\\secret\\ngrunner.db")

    actions.pause = broken

    _post(browser, "/pause")
    page = _page(browser, "/")

    assert "That didn't go through" in page
    assert "database is locked" not in page and "RuntimeError" not in page


def test_when_the_view_cannot_be_read_the_page_says_so_plainly(browser, views):
    def broken() -> BoardView:
        raise RuntimeError("no such table: jobs")

    views.board = broken

    response = browser.get("/", base_url=BASE)
    page = response.get_data(as_text=True)

    assert response.status_code == 500
    assert "can't be shown right now" in page and "no such table" not in page


def test_a_page_that_does_not_exist_says_so_and_links_back(browser):
    response = browser.get("/settings", base_url=BASE)

    assert response.status_code == 404
    assert "There is no such page on the board" in response.get_data(as_text=True)


def test_history_shows_jims_events_by_day_in_pacific_time_and_never_the_dev_lane(browser, views):
    views.events = (
        EventView(datetime(2026, 10, 30, 21, 5, tzinfo=timezone.utc), "Testerson_101426",
                  "Filed the IC summary, checked on two fetches 10 minutes apart."),
        EventView(datetime(2026, 10, 30, 20, 40, tzinfo=timezone.utc), "Testerson_101426",
                  "Second fetch agreed with the first.", "LOG"),
        EventView(datetime(2026, 10, 30, 20, 30, tzinfo=timezone.utc), None,
                  "Studio answered 503 on the first try.", "DEV"),
        EventView(datetime(2026, 10, 29, 16, 12, tzinfo=timezone.utc), "Fakeley_102726",
                  "Asked closet or garage (card FAK-1)."),
    )

    page = _page(browser, "/history")

    assert page.index("Today") < page.index("2:05 PM") < page.index("1:40 PM")
    assert page.index("1:40 PM") < page.index("Thu 10/29") < page.index("9:12 AM")
    assert "Filed the IC summary" in page and "Second fetch agreed" in page
    assert "Studio answered 503" not in page


def test_health_shows_when_things_last_worked_and_whether_pop_ups_are_on(browser):
    page = _page(browser, "/health")

    assert "2:58 PM, 2 min ago" in page   # the Fab export
    assert "2:51 PM, 9 min ago" in page   # the job list
    assert "2:57 PM, 3 min ago" in page   # the heartbeat
    assert "NG Jobs" in page and "C:\\fake\\NG Jobs" in page and "Reachable" in page
    assert "No alarms" in page


@pytest.mark.parametrize("toasts, words", [(True, "On"), (False, "Turn them on"),
                                           (None, "Could not verify")])
def test_health_says_on_off_or_could_not_verify_for_pop_ups(browser, views, toasts, words):
    views.view = _view(health=_health(toasts=toasts))

    assert words in _page(browser, "/health")


def test_health_lists_alarms_and_left_over_temp_files(browser, views):
    views.view = _view(health=_health(
        alarms=("Fab export is over 30 minutes old",),
        leftover_temps=(Path(r"C:\fake\NG Jobs\Dummitt_103026\x.pdf.ab12.ngr-tmp"),),
        last_export=None,
    ))

    page = _page(browser, "/health")

    assert "Fab export is over 30 minutes old" in page
    assert "x.pdf.ab12.ngr-tmp" in page
    assert "Not read yet" in page


# ----- Open: only folders, PDFs and text files inside the roots


@pytest.fixture
def ng_jobs(tmp_path: Path) -> Path:
    root = tmp_path / "NG Jobs"
    (root / "Dummitt_103026").mkdir(parents=True)
    (root / "Dummitt_103026" / "Dummitt_103026_summary.pdf").write_bytes(b"%PDF-1.7\n%%EOF\n")
    (root / "Dummitt_103026" / "Dummitt_103026_NGRunner_log.txt").write_text("10/30 3:05 PM Filed.",
                                                                            encoding="utf-8")
    (root / "imager.exe").write_bytes(b"MZ")
    (root / "shortcut.lnk").write_bytes(b"L\0\0\0")
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "private.pdf").write_bytes(b"%PDF-1.7\n%%EOF\n")
    return root


@pytest.fixture
def opened() -> list[str]:
    return []


@pytest.fixture
def opening(views, actions, ng_jobs, opened):
    """A browser on a board whose Open may open things in NG Jobs, recording what it opens."""
    board = create_board(views, actions, token=TOKEN, roots=(ng_jobs,), opener=opened.append,
                         clock=FakeClock(NOW))
    set_port(board, PORT)
    client = board.test_client()
    client.get(f"/?t={TOKEN}", base_url=BASE)
    return client


def _open(client, path) -> str:
    """Ask the board to open path; returns the notice it shows afterwards ("" if none)."""
    _post(client, "/open", {"path": str(path), "back": "list"})
    page = _page(client, "/list")
    found = re.search(r'<p class="notice notice--bad">.*?<span>(.*?)</span></p>', page)
    return found.group(1) if found else ""


def test_open_opens_a_summary_inside_ng_jobs_and_says_it_is_coming(opening, opened, ng_jobs):
    summary = ng_jobs / "Dummitt_103026" / "Dummitt_103026_summary.pdf"

    _post(opening, "/open", {"path": str(summary), "back": "list"})

    assert [Path(p) for p in opened] == [summary]
    assert "Opening Dummitt_103026_summary.pdf." in _page(opening, "/list")


@pytest.mark.parametrize("name", ["Dummitt_103026", "Dummitt_103026/Dummitt_103026_NGRunner_log.txt"])
def test_open_opens_a_job_folder_or_its_log(opening, opened, ng_jobs, name):
    assert _open(opening, ng_jobs / name) == ""
    assert [Path(p) for p in opened] == [ng_jobs / name]


@pytest.mark.parametrize("name", ["imager.exe", "shortcut.lnk"])
def test_open_never_runs_a_program_or_a_shortcut_even_inside_ng_jobs(opening, opened, ng_jobs,
                                                                     name):
    reason = _open(opening, ng_jobs / name)

    assert opened == []
    assert "only opens folders, PDFs and text files" in reason


def test_open_refuses_a_file_outside_the_roots(opening, opened, tmp_path):
    reason = _open(opening, tmp_path / "elsewhere" / "private.pdf")

    assert opened == []
    assert "outside the folders NG Runner works in" in reason


def test_open_refuses_a_climb_out_of_a_root(opening, opened, ng_jobs):
    _open(opening, f"{ng_jobs}\\Dummitt_103026\\..\\..\\elsewhere\\private.pdf")

    assert opened == []


def test_open_refuses_a_junction_inside_a_root_that_leads_out_of_it(opening, opened, ng_jobs,
                                                                    tmp_path):
    import _winapi

    _winapi.CreateJunction(str(tmp_path / "elsewhere"), str(ng_jobs / "Looks_103026"))

    reason = _open(opening, ng_jobs / "Looks_103026" / "private.pdf")

    assert opened == []
    assert "outside the folders" in reason


@pytest.mark.parametrize("text", ["Dummitt_103026\\Dummitt_103026_summary.pdf",
                                  "\\\\.\\C:\\Windows\\notepad.exe", ""],
                         ids=["relative", "device path", "empty"])
def test_open_refuses_anything_but_a_full_path(opening, opened, text):
    assert "can only open a file it names itself" in _open(opening, text)
    assert opened == []


def test_open_refuses_a_stream_of_a_file(opening, opened, ng_jobs):
    stream = f"{ng_jobs}\\Dummitt_103026\\Dummitt_103026_log.txt:hidden.pdf"  # an NTFS stream
    with open(stream, "wb") as f:
        f.write(b"%PDF-1.7\n%%EOF\n")

    assert "can only open a file it names itself" in _open(opening, stream)
    assert opened == []


def test_open_says_when_the_file_is_gone(opening, opened, ng_jobs):
    reason = _open(opening, ng_jobs / "Dummitt_103026" / "Dummitt_103026_old.pdf")

    assert reason == "Dummitt_103026_old.pdf is not there any more."


def test_a_board_given_no_roots_opens_nothing(browser, ng_jobs):
    reason = _open(browser, ng_jobs / "Dummitt_103026" / "Dummitt_103026_summary.pdf")

    assert "outside the folders" in reason


def test_when_windows_cannot_open_the_file_the_board_says_so(views, actions, ng_jobs):
    def refuse(path: str) -> None:
        raise OSError("no application is associated")

    board = create_board(views, actions, token=TOKEN, roots=(ng_jobs,), opener=refuse)
    set_port(board, PORT)
    client = board.test_client()
    client.get(f"/?t={TOKEN}", base_url=BASE)

    reason = _open(client, ng_jobs / "Dummitt_103026" / "Dummitt_103026_summary.pdf")

    assert reason == "Windows could not open Dummitt_103026_summary.pdf."
