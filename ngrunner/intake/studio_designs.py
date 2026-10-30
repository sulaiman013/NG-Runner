"""
Checking a report against a second list of its job's designs: the Proposal tab of Studio's
project page (AGENTS.md: Before filing goes live, item 8).

Why: the summary check and the two fetches show that a report is whole, stable and true to its
own page 1, but a design missing from page 1 can't be seen from the file. Measured on 59 real sold
jobs (2026-10-30), the designs on the Proposal tab equal the designs on the report's page 1, by
name, on all 59 (124 on each side), while those projects hold 202 designs in all and 27 jobs have
designs outside the proposal. So the Proposal tab, not the whole project, is the list to check.

How names compare: both sides are normalised (lowercase, every run of non a-z0-9 one space,
stripped), and a report design matches a card when its name is contained in the card's text. That
rule alone can't decide a job: Studio's names are often another design's name with more on the end
("Closet - Hanging" and "Closet - Hanging - Island"), so a report that lost the longer one would
still have a name for each card. So each design and each card pair off once, as many pairs as the
rule allows, and what is left over is what is missing. Two lists that really are equal always pair
off completely, so this can't refuse what the plain rule passed.

How Studio is reached: Studio's own form does not work for this login, so sign-in goes through the
community site, which needs a real browser (browser_sign_in). The cookies it leaves let plain
requests read project pages for 14 days, so the browser is rarely needed. A wrong password could
lock the account, so StudioSite never signs in twice within min_sign_in_gap. A browser can hang
where none of its own time limits reach, and a thread can't be killed, so a sign-in runs on a daemon
thread and past sign_in_limit it is given up on: it must not hold up the fetches behind it.

What leaves here: design names sit only in ProjectPage's fields, which no repr shows. A DesignCheck
holds counts, and every reason is fixed plain words, so no name, password or cookie reaches a
message or a DEV item (AGENTS.md rule 8, decision 10). Nothing is logged here. A "match" says the
report's designs are the proposal's and no more: a filed summary is "checked", never "complete".
"""
from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import urljoin

import requests

from ngrunner.clock import Clock
from ngrunner.intake.fetch import STUDIO_URL, USER_AGENT, FetchTarget, Http
from ngrunner.secrets import Secrets, SecretsError

DesignCode = Literal["match", "report_missing_designs", "proposal_missing_designs"]
UnavailableCode = Literal["no_login", "sign_in_failed", "network", "page_changed"]

COMMUNITY_LOGIN_URL = "https://www.inspiredclosetscommunity.com/login"
SESSION_COOKIES = ("sessionid", "csrftoken")  # all a plain request needs to read a project page
_TIMEOUT = (15, 60)  # seconds: connecting, then each wait for more of the page


class PageChanged(Exception):
    """Studio's project page is not shaped as measured, so it says nothing about the designs. The
    message is fixed words, with nothing from the page."""


class StudioUnavailable(Exception):
    """
    Studio's project page could not be read, so the designs could not be verified (AGENTS.md rule
    8: report it as that, never as a result).

    code    no_login (no Studio login on this PC), sign_in_failed, network (Studio out of reach or
            failing, so try later) or page_changed (Studio did not give a page NG Runner can read).
    reason  one plain sentence: never a password, a cookie, a design name or an address.
    """

    def __init__(self, code: UnavailableCode, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


# ----- reading the page


@dataclass(frozen=True)
class ProjectPage:
    """
    What a project page says about its designs.

    proposal     the normalised text of each card on the Proposal tab, in page order.
    all_designs  the normalised text of each card on the Design tab: every design on the project.
    report_url   the job's exact report address on Studio, from the page's link; None without one.

    Hidden from repr, so a stray log or message can't quote a design or a client's slug.
    """

    proposal: tuple[str, ...] = field(repr=False)
    all_designs: tuple[str, ...] = field(repr=False)
    report_url: str | None = field(repr=False)


def normalise(text: str) -> str:
    """Lowercase, every run of non a-z0-9 one space, stripped: as real jobs were compared."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


_PROPOSAL_TAB, _DESIGN_TAB = "project-proposal", "project-design"
# The element around one card, and the tab it belongs on. Fullmatch: the tabs also hold headings
# called project-proposal-designs and project-designs, and buttons like design_cancel_<number>.
_WRAPPERS = ((re.compile(r"design_proposal_details_\d+"), _PROPOSAL_TAB),
             (re.compile(r"design_details_\d+"), _DESIGN_TAB))
_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                   "param", "source", "track", "wbr"})  # never closed, so never kept as open
_NOT_TEXT = frozenset({"script", "style", "textarea"})  # code, and what people typed as comments


@dataclass(eq=False)
class _Element:
    tag: str
    element_id: str
    tab: str | None = None  # on a card's wrapper: the tab it belongs on
    cards: int = 0  # on a card's wrapper: how many cards it holds
    text: list[str] = field(default_factory=list)  # on a card: its text so far


class _PageReader(HTMLParser):
    """
    Each tab's cards and the report link. A card is a div.design-details inside its wrapper; the
    wrapper is usually a form that has the class design-details too, so class alone would count
    each design twice.

    An end tag closes back to its nearest open match and a stray one is ignored, so an unclosed <p>
    can't shift the rest. Anything else not shaped as measured sets `odd`: a card with no wrapper,
    a wrapper off its tab, a wrapper without exactly one card. Otherwise a small redesign, or one
    extra </div>, would quietly read as fewer designs.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tabs_seen: set[str] = set()
        self.cards: dict[str, list[str]] = {_PROPOSAL_TAB: [], _DESIGN_TAB: []}
        self.report_href: str | None = None
        self.odd = False
        self._open: list[_Element] = []
        self._card: _Element | None = None
        self._card_tab = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        given = dict(attrs)
        href = given.get("href") or ""
        if tag == "a" and self.report_href is None and "install_summary" in href:
            self.report_href = href
        if tag in _VOID:
            return
        element = _Element(tag, given.get("id") or "")
        if element.element_id in self.cards:
            self.tabs_seen.add(element.element_id)
        for pattern, tab in _WRAPPERS:
            if pattern.fullmatch(element.element_id):
                element.tab = tab
                self.odd = self.odd or not any(o.element_id == tab for o in self._open)
        if tag == "div" and "design-details" in (given.get("class") or "").split():
            self._start_card(element)
        self._open.append(element)

    def _start_card(self, element: _Element) -> None:
        wrapper = next((o for o in reversed(self._open) if o.tab), None)
        if wrapper is None:
            # Off the tabs it is none of this page's lists; on a tab it is a card out of place.
            self.odd = self.odd or any(o.element_id in self.cards for o in self._open)
            return
        wrapper.cards += 1
        if self._card is None:
            self._card, self._card_tab = element, wrapper.tab or ""

    def handle_endtag(self, tag: str) -> None:
        for depth in range(len(self._open) - 1, -1, -1):
            if self._open[depth].tag == tag:
                self._close_down_to(depth)
                return

    def handle_data(self, data: str) -> None:
        if self._card is not None and self._open[-1].tag not in _NOT_TEXT:
            self._card.text.append(data)

    def close(self) -> None:
        super().close()
        self._close_down_to(0)

    def _close_down_to(self, depth: int) -> None:
        for element in reversed(self._open[depth:]):
            if element.tab is not None and element.cards != 1:
                self.odd = True
            if element is self._card:
                self.cards[self._card_tab].append(normalise(" ".join(element.text)))
                self._card = None
        del self._open[depth:]


def parse_project_page(html: str) -> ProjectPage:
    """
    The designs and report link on a project page's HTML.

    Raises PageChanged when #project-proposal or #project-design is absent, or their cards are not
    shaped as measured, so a redesign of the page can never read as "no designs".
    """
    reader = _PageReader()
    readable = True
    try:
        reader.feed(html)
        reader.close()
    except Exception:  # noqa: BLE001 - html.parser gives up on few pages; each one means the same
        readable = False
    if not readable or not reader.tabs_seen >= set(reader.cards):
        raise PageChanged("Studio's project page no longer has its Proposal and Design tabs.")
    if reader.odd:
        raise PageChanged("Studio's project page no longer lists its designs in the usual way.")
    href = reader.report_href
    return ProjectPage(tuple(reader.cards[_PROPOSAL_TAB]), tuple(reader.cards[_DESIGN_TAB]),
                       None if href is None else urljoin(STUDIO_URL, href.strip()))


# ----- checking a report's designs


@dataclass(frozen=True)
class DesignCheck:
    """
    A report's designs against the proposal's. It holds counts, never names, so it can go anywhere.

    code                 match; report_missing_designs (a proposal design has no design on the
                         report, the mistake this check exists for: it wins when both sides lack
                         one); or proposal_missing_designs (a report design has no card, which
                         includes a proposal with no designs).
    missing_from_report  proposal cards left without a report design.
    not_in_proposal      report designs left without a proposal card.
    """

    code: DesignCode
    missing_from_report: int
    not_in_proposal: int


def check_designs(report_names: Iterable[str], page: ProjectPage) -> DesignCheck:
    """
    report_names (SummaryCheck.designs) against the page's Proposal tab. Designs on the project but
    outside the proposal don't count: they are not meant to be on the report. Two empty lists are a
    match; a blank report name matches nothing.
    """
    names = [normalise(name) for name in report_names]
    cards = [normalise(card) for card in page.proposal]
    paired = _pairs(names, cards)
    missing, extra = len(cards) - paired, len(names) - paired
    code: DesignCode = "match"
    if missing:
        code = "report_missing_designs"
    elif extra:
        code = "proposal_missing_designs"
    return DesignCheck(code, missing, extra)


def _pairs(names: Sequence[str], cards: Sequence[str]) -> int:
    """The most (name, card) pairs with each used once and the name contained in the card. Taking
    the first fit isn't enough: a short name can take the card a longer one needs, so a name may
    move an earlier name on to another card that fits it too."""
    owner: dict[int, int] = {}  # card -> the name holding it

    def place(name: int, tried: set[int]) -> bool:
        for card, text in enumerate(cards):
            if names[name] and names[name] in text and card not in tried:
                tried.add(card)
                if card not in owner or place(owner[card], tried):
                    owner[card] = name
                    return True
        return False

    return sum(place(name, set()) for name in range(len(names)))


# ----- signing in, in a browser


_EMAIL_BOXES = ("input[type=email]", "input[type=text]")
_PASSWORD_BOX = "input[type=password]"
_SIGN_IN_BUTTONS = ("button:has-text('Log in')", "button:has-text('Login')",
                    "button:has-text('Sign')", "input[type=submit]", "button[type=submit]")
_STUDIO_LINK = "a[href*='/s/studio']"
_TICK_MS = 1_000
_TICKS_TO_SIGN_IN = 90  # the community site takes a while, and its frames detach as it moves
_TICKS_FOR_A_PAGE = 60
_BROWSER_FAILED = "The browser could not finish signing in to Studio."
_TOOK_TOO_LONG = "Signing in to Studio took too long."


def browser_sign_in(username: str, password: str) -> dict[str, str]:
    """
    Sign in as a person does, in headless Microsoft Edge, and return Studio's two session cookies.

    The community site's login page gives a session; its Studio link then opens a new tab (through
    window.open, so only a click does it) at Studio's /gateway/, which lands on /dashboard/ with the
    cookies set. Raises StudioUnavailable("sign_in_failed", reason). A browser's own errors can
    quote what was typed, so they stop here, with nothing chained.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sync_playwright = None
    if sync_playwright is None:
        raise StudioUnavailable(
            "sign_in_failed", "The browser tool for signing in is not installed."
        )
    cookies, reason = None, _BROWSER_FAILED
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            try:
                cookies = _walk_sign_in(browser.new_context(), username, password)
            finally:
                browser.close()
    except StudioUnavailable as exc:
        reason = exc.reason
    except Exception:  # noqa: BLE001 - see the docstring: whatever it was, only fixed words leave
        pass
    if cookies is None:
        raise StudioUnavailable("sign_in_failed", reason)
    return cookies


def _walk_sign_in(context: Any, username: str, password: str) -> dict[str, str]:
    page = context.new_page()
    page.goto(COMMUNITY_LOGIN_URL, wait_until="domcontentloaded",
              timeout=_TICKS_FOR_A_PAGE * _TICK_MS)
    form = _poll(page, _TICKS_FOR_A_PAGE, lambda: _login_form(page))
    if form is None:
        raise StudioUnavailable("sign_in_failed", "The community site did not show its login page.")
    email_box, password_box, button = form
    email_box.fill(username)
    password_box.fill(password)
    button.click()
    if not _poll(page, _TICKS_TO_SIGN_IN, lambda: _left_login(page)):
        raise StudioUnavailable(
            "sign_in_failed", "The community site did not accept the login; it may have changed."
        )
    link = _poll(page, _TICKS_FOR_A_PAGE, lambda: _studio_link(page))
    if link is None:
        raise StudioUnavailable(
            "sign_in_failed", "The community site did not show its Studio link."
        )
    with context.expect_page(timeout=_TICKS_FOR_A_PAGE * _TICK_MS) as opened:
        link.click()
    tab = opened.value
    cookies = _poll(tab, _TICKS_FOR_A_PAGE, lambda: _studio_cookies(context, tab))
    if cookies is None:
        raise StudioUnavailable("sign_in_failed", "Studio did not open after signing in.")
    return cookies


def _poll(page: Any, ticks: int, look: Callable[[], Any]) -> Any:
    """look()'s first truthy answer, asked once a tick; None if none came. A look that raises means
    the page is still moving, since its frames detach as it does, so it only means: not yet."""
    for _ in range(ticks):
        try:
            found = look()
        except Exception:  # noqa: BLE001 - a detached frame; a real failure runs out the ticks
            found = None
        if found:
            return found
        page.wait_for_timeout(_TICK_MS)
    return None


def _first_visible(frame: Any, selectors: Iterable[str]) -> Any:
    """The first visible element of the first selector that has one, so the order is a preference:
    a "Sign up" button must not win over "Log in" by standing first on the page."""
    for selector in selectors:
        for element in frame.query_selector_all(selector):
            if element.is_visible():
                return element
    return None


def _login_form(page: Any) -> tuple[Any, Any, Any] | None:
    """The email box, password box and button of whichever frame holds them all."""
    for frame in list(page.frames):
        email_box = _first_visible(frame, _EMAIL_BOXES)
        password_box = _first_visible(frame, (_PASSWORD_BOX,))
        button = _first_visible(frame, _SIGN_IN_BUTTONS)
        if email_box and password_box and button:
            return email_box, password_box, button
    return None


def _left_login(page: Any) -> bool:
    return ("login" not in page.url.lower()
            and not any(frame.query_selector(_PASSWORD_BOX) for frame in list(page.frames)))


def _studio_link(page: Any) -> Any:
    for frame in list(page.frames):
        link = _first_visible(frame, (_STUDIO_LINK,))
        if link:
            return link
    return None


def _studio_cookies(context: Any, tab: Any) -> dict[str, str] | None:
    """The two cookies, once the tab has reached the dashboard and both are set."""
    if "/dashboard" not in tab.url:
        return None
    found = {cookie["name"]: cookie["value"] for cookie in context.cookies(STUDIO_URL)}
    return _session_cookies(found)


def _session_cookies(found: object) -> dict[str, str] | None:
    """Just the two cookies of a session, or None unless both are there. Never shows a value."""
    if not isinstance(found, Mapping):
        return None
    kept = {name: found.get(name) for name in SESSION_COOKIES}
    return kept if all(isinstance(value, str) and value for value in kept.values()) else None


# ----- reading Studio


class StudioSite:
    """
    Reads Studio's project pages, signing in only when it must. It only ever GETs /project/details/.

    secrets  holds studio_username, studio_password and studio_session (JSON of the two cookies).
    http     a requests.Session or anything with its get(), whose answers have status_code, headers,
             url and text; None makes one per read, which holds no cookies between reads. Don't pass
             a session shared with other calls: Studio's Set-Cookie answers would stay in it.
    sign_in  (username, password) -> cookies. Raising means it failed; only a StudioUnavailable's
             reason is passed on, since any other error's text could quote the password.
    min_sign_in_gap  sign-ins, good or bad, are at least this far apart. recall and remember keep
             the last one's time where a restart can read it, so a crash loop can't retry a wrong
             password; without them this object alone remembers it.
    sign_in_limit    real time a sign-in may take before it counts as failed. A read waiting behind
             one gives up after half as long again, which covers the sign-in and its two reads.
    """

    def __init__(self, secrets: Secrets, clock: Clock, *, http: Http | None = None,
                 sign_in: Callable[[str, str], Mapping[str, str]] = browser_sign_in,
                 min_sign_in_gap: timedelta = timedelta(hours=1),
                 sign_in_limit: timedelta = timedelta(minutes=7),
                 recall: Callable[[], datetime | None] = lambda: None,
                 remember: Callable[[datetime], None] = lambda when: None) -> None:
        self._secrets, self._clock, self._http = secrets, clock, http
        self._sign_in, self._gap, self._limit = sign_in, min_sign_in_gap, sign_in_limit
        self._recall, self._remember = recall, remember
        self._last_sign_in: datetime | None = None
        self._lock = threading.Lock()  # one at a time, so a sign-in in progress is waited for

    def project_page(self, studio_id: str) -> ProjectPage:
        """
        The job's project page. It uses the stored session; when that is missing or sent to /login
        it signs in once and reads once more. A report_url that FetchTarget would refuse for this
        job reads as None. Raises StudioUnavailable, and ValueError for an ID that isn't digits.
        """
        if not (isinstance(studio_id, str) and studio_id.isascii() and studio_id.isdigit()):
            raise ValueError("studio_id must be the Studio project ID, digits only")
        if not self._lock.acquire(timeout=self._limit.total_seconds() * 1.5):
            raise StudioUnavailable("sign_in_failed", _TOOK_TOO_LONG)
        try:
            session = self._stored_session()
            html = None if session is None else self._read(studio_id, session)
            if html is None:
                html = self._read(studio_id, self._new_session())
            if html is None:
                raise StudioUnavailable(
                    "sign_in_failed", "Studio asked for its login again right after signing in."
                )
        finally:
            self._lock.release()
        return _for_job(_parsed(html), studio_id)

    def _stored_session(self) -> dict[str, str] | None:
        """The stored cookies; None when there are none or they can't be read as cookies."""
        try:
            text = self._secrets.get("studio_session")
            return _session_cookies(json.loads(text)) if text else None
        except (SecretsError, ValueError):
            return None

    def _new_session(self) -> dict[str, str]:
        """Sign in and store the cookies, unless the last sign-in was too recent."""
        username, password = self._login()
        now = self._clock.now()
        last = self._last_sign_in or self._recall()
        # a time ahead of the clock is a clock that moved, and must not stop sign-ins for good
        if last is not None and timedelta(0) <= now - last < self._gap:
            raise StudioUnavailable(
                "sign_in_failed",
                "NG Runner tried Studio's login too recently to try again, so that a wrong password"
                " can't lock the account.",
            )
        self._last_sign_in = now  # before trying: a sign-in that crashes counts too
        self._remember(now)
        got, reason = None, _BROWSER_FAILED
        try:
            got = self._within_limit(username, password)
        except StudioUnavailable as exc:
            reason = exc.reason
        except Exception:  # noqa: BLE001 - its text could quote the password, so it stops here
            pass
        session = _session_cookies(got)
        if session is None:
            raise StudioUnavailable("sign_in_failed", reason)
        kept = True
        try:
            self._secrets.put("studio_session", json.dumps(session))
        except SecretsError:
            kept = False
        if not kept:
            raise StudioUnavailable(
                "sign_in_failed", "Windows Credential Manager could not keep Studio's session."
            )
        return session

    def _within_limit(self, username: str, password: str) -> Mapping[str, str]:
        """The sign-in, on its own daemon thread, waited for only so long. One that never returns
        stays parked there and what it brings back goes nowhere: only fixed words leave here."""
        box: dict[str, Any] = {}

        def run() -> None:
            try:
                box["got"] = self._sign_in(username, password)
            except Exception as exc:  # noqa: BLE001 - raised on the waiting side, which sorts it
                box["error"] = exc

        thread = threading.Thread(target=run, daemon=True, name="ngrunner-sign-in")
        thread.start()
        thread.join(self._limit.total_seconds())
        if thread.is_alive():
            raise StudioUnavailable("sign_in_failed", _TOOK_TOO_LONG)
        if "error" in box:
            raise box["error"]
        return box["got"]

    def _login(self) -> tuple[str, str]:
        login: tuple[str | None, str | None] | None = None
        try:
            login = self._secrets.get("studio_username"), self._secrets.get("studio_password")
        except SecretsError:
            pass
        if login is None:
            raise StudioUnavailable(
                "no_login", "Windows Credential Manager could not give Studio's login."
            )
        username, password = login
        if not (username and password):
            raise StudioUnavailable("no_login", "Studio's login is not saved on this PC.")
        return username, password

    def _read(self, studio_id: str, session: Mapping[str, str]) -> str | None:
        """
        The project page's HTML; None when Studio sends this session to sign in.

        A redirect is read, never followed: following one would ask for something other than
        /project/details/, and requests would carry these cookies to wherever it pointed.
        """
        url = f"{STUDIO_URL}/project/details/{studio_id}/"
        options: dict[str, Any] = {"cookies": dict(session), "allow_redirects": False,
                                   "headers": {"User-Agent": USER_AGENT}, "timeout": _TIMEOUT}
        response = None
        try:
            if self._http is not None:
                response = self._http.get(url, **options)
            else:
                with requests.Session() as own:
                    response = own.get(url, **options)
        except (OSError, ValueError):  # requests' errors are OSErrors; their text can quote cookies
            pass
        if response is None:
            raise StudioUnavailable("network", "Studio could not be reached.")
        status = response.status_code
        redirected_to = response.headers.get("Location", "") if 300 <= status < 400 else ""
        if "/login" in redirected_to or "/login" in (getattr(response, "url", "") or ""):
            return None
        if status == 200:
            return response.text
        if status == 429 or status >= 500:
            raise StudioUnavailable("network", "Studio is answering with an error; it may be down.")
        raise StudioUnavailable(
            "page_changed", "Studio did not give the project page at its usual address."
        )


def _parsed(html: str) -> ProjectPage:
    try:
        return parse_project_page(html)
    except PageChanged as exc:  # its message is fixed words, with nothing from the page
        raise StudioUnavailable("page_changed", str(exc)) from None


def _for_job(page: ProjectPage, studio_id: str) -> ProjectPage:
    """The page, with its report link only if FetchTarget would take it as this job's known_url."""
    if page.report_url is None:
        return page
    try:
        FetchTarget(studio_id, "x", None, "landscape", known_url=page.report_url)
    except ValueError:
        return replace(page, report_url=None)
    return page
