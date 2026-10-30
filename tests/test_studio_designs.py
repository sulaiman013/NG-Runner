"""
Studio's project page as a second list of a job's designs (AGENTS.md: Before filing goes live,
item 8). Pages are built here from the structure of a real page, with made-up names. StudioSite
talks over a real socket to a fake on 127.0.0.1, through a session that swaps Studio's address for
the fake's; sign-in is an injected callable, and browser_sign_in runs against a fake playwright.
Nothing here reaches Studio or the community site.
"""
from __future__ import annotations

import ast
import json
import sys
import threading
import time
import traceback
import types
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import requests
from helpers_studio import local_session

from ngrunner.clock import FakeClock
from ngrunner.intake import studio_designs
from ngrunner.intake.fetch import STUDIO_URL
from ngrunner.intake.studio_designs import (
    COMMUNITY_LOGIN_URL,
    DesignCheck,
    PageChanged,
    ProjectPage,
    StudioSite,
    StudioUnavailable,
    browser_sign_in,
    check_designs,
    normalise,
    parse_project_page,
)
from ngrunner.secrets import MemorySecrets, SecretsError

# Studio's real names often repeat another design's name with more on the end.
HANGING = "Testerson Closet - Hanging"
ISLAND = "Testerson Closet - Hanging - Island"
GARAGE = "Fakeley Garage"
BATH = "Mockford Bed & Bath #2"
PANTRY = "Dummitt Pantry"
DESIGNS = (HANGING, ISLAND, GARAGE, BATH, PANTRY)

STUDIO_ID = "1234567"
REPORT_PATH = f"/install_summary_landscape/testerson/testerson_101426/{STUDIO_ID}/"
USERNAME, PASSWORD = "testerson@example.com", "correct-horse-battery"
COOKIES = {"sessionid": "sess1cookie2value", "csrftoken": "csrf3cookie4value"}
LOGIN = {"studio_username": USERNAME, "studio_password": PASSWORD}
NEVER_SHOWN = (USERNAME, PASSWORD, *COOKIES.values(), *DESIGNS, *map(normalise, DESIGNS))


# ----- pages


def card(wrapper: str, name: str) -> str:
    """One design's form, as the real page has it: the form shares the card's class, its inputs
    have no closing slash, and a comment on it can name another design."""
    shown = escape(name)
    return f"""
<form class="design-details ui-state-default" data-design-id="x" id="{wrapper}">
 <div class="row"><div class="col-sm-12"><div class="design-details">
  <div class="design-title"><input id="id_name" type="hidden" value="{shown}">
   <a class="design-modify" href="/project/details/{STUDIO_ID}/ajax/design_edit_check/7/True">
    {shown}
    <span>(Reach-in Closet)</span> <span> <i class="fa fa-eye"></i> </span></a>
   <span>Design ID: IC123456789</span></div>
  <div class="design-area-of-home-select"> </div>
  <div class="include-in-proposal"> Included in proposal </div>
  <div class="include-in-price"> Included in price </div>
  <div class="design-price presentation_price show"><p class="estimate-design-price">$1,234.56</p>
  </div>
  <div class="design-options"><button class="btn" id="design_cancel_7">Cancel</button>
   <button class="design-save" id="design_save_7">Save<span class="loading-indicator"></span>
   </button>
   <div class="clickable-element"><a href="/preference/margin_simulator/design/IC1/"><i></i></a>
   </div>
  </div></div>
  <div class="design-styles"><div class="container-inline"><b>Material</b> Maple</div></div></div>
  <div class="col-sm-12"><div class="design-comments clearfix"><label>Proposal comment</label>
   <textarea class="form-control" id="id_proposal_comment">See {escape(PANTRY)} too</textarea>
   <br><div class="design-meta"><input id="id_id"></div></div></div>
 </div>
</form>"""


def cards(prefix: str, names: Iterable[str]) -> str:
    return "".join(card(f"{prefix}{number}", name) for number, name in enumerate(names, 7000))


def page_html(proposal: Iterable[str] = (), others: Iterable[str] = (),
              report_href: str | None = REPORT_PATH) -> str:
    """A whole project page. The Design tab lists the proposal's designs and the others, and so
    does its send-to-AR box, which is not a list of cards."""
    proposal = list(proposal)
    every = [*proposal, *others]
    link = f'<a class="btn" href="{report_href}">Install summary</a>' if report_href else ""
    box = "".join(f'<label><input class="send-ar-checkbox"> {escape(n)}</label>' for n in every)
    return f"""<!doctype html><html><head><title>Project</title>
</head><body>
<ul class="nav"><li><a href="#project-design" data-toggle="tab">Designs</a></li>
<li><a href="#project-proposal" data-toggle="tab">Proposal</a></li></ul><p>Unclosed paragraph {link}
<div class="tab-pane fade" id="project-design"><div id="design_container">
<script>var card = '<div class="design-details">not a card</div>';</script>
<h2 id="project-designs">Project designs</h2><div id="design-list-container">
<div class="design_list_sortable">{cards("design_details_", every)}</div>
<div class="modal fade" id="send-ar-designs"><form id="send-ar-project-form">{box}</form></div>
</div></div></div>
<div class="tab-pane fade" id="project-proposal"><div id="greeting_editable">
<form id="editable_proposal_greeting_form">
<a href="/proposal/testerson/testerson_101426/7/">View</a>
<textarea id="id_proposal_comments">Hello [[customer.first_name]]</textarea></form></div>
<div id="proposal_design_container"><h2 id="project-proposal-designs">Proposal designs</h2>
<div id="proposal-design-list-container"><div class="proposal_design_list_sortable">
{cards("design_proposal_details_", proposal)}</div></div></div></div></body></html>"""


def page_of(*proposal: str) -> ProjectPage:
    return parse_project_page(page_html(proposal))


# ----- parse_project_page


def test_each_design_is_one_card_on_its_tab_though_its_form_has_the_same_class() -> None:
    page = parse_project_page(page_html([HANGING, GARAGE], [PANTRY, BATH]))

    assert len(page.proposal) == 2 and len(page.all_designs) == 4
    assert normalise(HANGING) in page.proposal[0] and normalise(GARAGE) in page.proposal[1]
    assert [normalise(n) in text for n, text in zip(
        [HANGING, GARAGE, PANTRY, BATH], page.all_designs, strict=True)] == [True] * 4


def test_a_cards_text_is_normalised_and_leaves_out_what_people_typed_around_it() -> None:
    page = parse_project_page(page_html([BATH]))

    text = page.proposal[0]
    assert text.startswith("mockford bed bath 2 reach in closet design id ic123456789")
    assert text == normalise(text)
    assert "dummitt" not in text  # the comment on the card names another design
    assert "not a card" not in " ".join(page.all_designs)  # text in a script is no card

    inside = page_html([BATH]).replace(
        '<div class="design-area-of-home-select"> </div>',
        f"<textarea>See {PANTRY}</textarea><script>var name = '{PANTRY}';</script>",
    )
    assert "dummitt" not in parse_project_page(inside).proposal[0]  # nor in them inside a card


def test_the_report_address_comes_from_its_link_anywhere_on_the_page() -> None:
    assert page_of(HANGING).report_url == STUDIO_URL + REPORT_PATH
    absolute = f"https://elsewhere.example{REPORT_PATH}"
    assert parse_project_page(page_html([], report_href=absolute)).report_url == absolute
    assert parse_project_page(page_html([], report_href=None)).report_url is None


def test_tabs_with_no_cards_are_no_designs() -> None:
    page = parse_project_page(page_html())

    assert page.proposal == () and page.all_designs == ()


def _rename(old: str, new: str) -> Callable[[str], str]:
    return lambda html: html.replace(old, new)


@pytest.mark.parametrize("redesign", [
    _rename('id="project-proposal"', 'id="proposal-tab"'),  # its heading and link stay, near misses
    _rename('id="project-design"', 'id="design-tab"'),
    _rename('<div class="design-details">', '<section class="design-card">'),
    _rename('<div class="design-details">', '<div class="card">'),
    _rename("design_proposal_details_", "proposal_card_"),
    _rename("design_details_", "design_card_"),
    lambda html: html.replace('</div></body>', '</div></div></div>'
                              '<form id="design_proposal_details_1"><div class="design-details">'
                              'x</div></form></body>'),  # a card off its tab
    lambda html: html.replace('<form id="editable_proposal_greeting_form">',
                              '<form id="design_proposal_details_2"><div class="design-details">'
                              '</div><div class="design-details"></div></form><form>'),  # 2 in 1
    lambda html: "<html><body>We'll be back soon.</body></html>",
    lambda html: "",
], ids=["proposal tab gone", "design tab gone", "cards not divs", "cards renamed",
        "proposal wrappers renamed", "design wrappers renamed", "card off its tab",
        "two cards in a wrapper", "another page", "nothing"])
def test_a_redesigned_page_never_reads_as_no_designs(redesign: Callable[[str], str]) -> None:
    html = redesign(page_html([HANGING], [GARAGE]))

    with pytest.raises(PageChanged) as caught:
        parse_project_page(html)

    assert not any(text in all_text(caught.value) for text in NEVER_SHOWN)


def test_a_page_object_shows_no_design_or_address_in_its_repr() -> None:
    assert not any(text in repr(page_of(HANGING)) for text in (*NEVER_SHOWN, "testerson"))


# ----- check_designs


def test_a_report_with_the_proposals_designs_matches() -> None:
    check = check_designs([HANGING, GARAGE], page_of(GARAGE, HANGING))

    assert check == DesignCheck("match", 0, 0)


def test_names_compare_however_the_report_wraps_and_punctuates_them() -> None:
    check = check_designs(["TESTERSON  closet-hanging", "mockford bed & bath  (#2)"],
                          page_of(HANGING, BATH))

    assert check == DesignCheck("match", 0, 0)


def test_a_design_missing_from_the_report_is_counted() -> None:
    check = check_designs([HANGING], page_of(HANGING, GARAGE, PANTRY))

    assert check == DesignCheck("report_missing_designs", 2, 0)


def test_a_design_missing_from_the_proposal_is_counted() -> None:
    check = check_designs([HANGING, GARAGE], page_of(HANGING))

    assert check == DesignCheck("proposal_missing_designs", 0, 1)


def test_designs_outside_the_proposal_are_ignored() -> None:
    page = parse_project_page(page_html([HANGING], [GARAGE, PANTRY]))

    assert check_designs([HANGING], page) == DesignCheck("match", 0, 0)
    assert check_designs([HANGING, GARAGE], page) == DesignCheck("proposal_missing_designs", 0, 1)


def test_a_proposal_with_no_designs_is_missing_them_unless_the_report_has_none_either() -> None:
    assert check_designs([HANGING, GARAGE], page_of()) == DesignCheck(
        "proposal_missing_designs", 0, 2)
    assert check_designs([], page_of()) == DesignCheck("match", 0, 0)
    assert check_designs([], page_of(HANGING)) == DesignCheck("report_missing_designs", 1, 0)


def test_a_shorter_name_cant_stand_in_for_the_longer_design_it_starts_like() -> None:
    assert check_designs([HANGING], page_of(HANGING, ISLAND)) == DesignCheck(
        "report_missing_designs", 1, 0)  # the report lost the island; "hanging" is in both cards
    assert check_designs([HANGING, ISLAND], page_of(ISLAND)) == DesignCheck(
        "proposal_missing_designs", 0, 1)


@pytest.mark.parametrize("names", [[HANGING, ISLAND], [ISLAND, HANGING]])
@pytest.mark.parametrize("cards_", [[HANGING, ISLAND], [ISLAND, HANGING]])
def test_every_design_finds_its_own_card_whatever_the_order(names, cards_) -> None:
    assert check_designs(names, page_of(*cards_)) == DesignCheck("match", 0, 0)


def test_two_cards_of_one_name_need_two_designs_on_the_report() -> None:
    assert check_designs([PANTRY], page_of(PANTRY, PANTRY)) == DesignCheck(
        "report_missing_designs", 1, 0)
    assert check_designs([PANTRY, PANTRY], page_of(PANTRY, PANTRY)) == DesignCheck("match", 0, 0)


def test_a_report_name_with_nothing_to_compare_matches_nothing() -> None:
    assert check_designs(["---"], page_of(HANGING)) == DesignCheck("report_missing_designs", 1, 1)


def test_both_sides_lacking_a_design_reads_as_the_report_missing_one_with_both_counts() -> None:
    assert check_designs([HANGING, PANTRY], page_of(HANGING, GARAGE)) == DesignCheck(
        "report_missing_designs", 1, 1)


def test_a_check_holds_counts_only() -> None:
    assert not any(text in repr(check_designs([PANTRY], page_of(GARAGE))) for text in NEVER_SHOWN)


# ----- StudioSite: a real socket, a fake Studio


@dataclass
class FakeStudio:
    """Serves project pages to a live session; anyone else is sent to /login, as Studio does. A
    `reply` of (status, Location) answers every page instead. Logs everything it is asked."""

    html: str = ""
    live_session: str | None = None
    reply: tuple[int, str] | None = None
    asked: list[tuple[str, str, str]] = field(default_factory=list)  # (method, path, Cookie)
    http: Any = None


class _Handler(BaseHTTPRequestHandler):
    def _answer(self) -> None:
        studio: FakeStudio = self.server.studio  # type: ignore[attr-defined]
        cookie = self.headers.get("Cookie", "")
        studio.asked.append((self.command, self.path, cookie))
        status, location, body = 200, "", "<html>the login form</html>"
        if self.path.startswith("/project/details/"):
            if studio.reply is not None:
                status, location = studio.reply
                body = f"<html>an error page naming {HANGING}</html>"
            elif studio.live_session and f"sessionid={studio.live_session}" in cookie:
                body = studio.html
            else:
                status, location = 302, f"/login/?next={self.path}"
        data = body.encode("utf-8")
        self.send_response(status)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_HEAD = _answer  # noqa: N815 - the names http.server calls

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - http.server's name
        pass


class _Rerouted:
    """requests.Session.get with Studio's address swapped for the fake's, so a test can't reach the
    real Studio and StudioSite can't be told apart from its production self."""

    def __init__(self, session: requests.Session, base_url: str) -> None:
        self._session, self._base_url = session, base_url

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        assert url.startswith(STUDIO_URL + "/")
        return self._session.get(self._base_url + url[len(STUDIO_URL):], **kwargs)


@pytest.fixture
def studio() -> Iterator[FakeStudio]:
    fake = FakeStudio(html=page_html([HANGING, GARAGE], [PANTRY]))
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.studio = fake  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                              daemon=True)
    thread.start()
    try:
        with local_session() as session:
            fake.http = _Rerouted(session, f"http://127.0.0.1:{server.server_address[1]}")
            yield fake
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


class SignIn:
    """A sign_in that keeps its calls, and marks the fake's session live when it gives cookies."""

    def __init__(self, studio: FakeStudio | None, gives: Any = None) -> None:
        self.studio, self.gives, self.calls = studio, COOKIES if gives is None else gives, []

    def __call__(self, username: str, password: str) -> dict[str, str]:
        self.calls.append((username, password))
        if isinstance(self.gives, Exception):
            raise self.gives
        if self.studio is not None:
            self.studio.live_session = self.gives.get("sessionid")
        return dict(self.gives)


@dataclass
class Rig:
    site: StudioSite
    secrets: Any
    clock: FakeClock
    sign_in: SignIn


def rig(studio: FakeStudio, *, values: dict[str, str] | None = None, gives: Any = None,
        secrets: Any = None, http: Any = None) -> Rig:
    secrets = MemorySecrets(LOGIN if values is None else values) if secrets is None else secrets
    clock = FakeClock(datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc))
    sign_in = SignIn(studio, gives)
    site = StudioSite(secrets, clock, http=http or studio.http, sign_in=sign_in)
    return Rig(site, secrets, clock, sign_in)


def stored(values: dict[str, str] = COOKIES) -> dict[str, str]:
    return {**LOGIN, "studio_session": json.dumps(values)}


def all_text(exc: BaseException) -> str:
    """Everything an error could show: its message, repr and every error chained to it."""
    text = "".join(traceback.format_exception(exc)) + repr(exc)
    for linked in (exc.__cause__, exc.__context__):
        if linked is not None:
            text += all_text(linked)
    return text


def failure(call: Callable[[], Any], code: str) -> StudioUnavailable:
    with pytest.raises(StudioUnavailable) as caught:
        call()
    assert caught.value.code == code
    assert caught.value.reason == str(caught.value) and caught.value.reason
    return caught.value


def test_a_live_session_reads_the_page_without_signing_in(studio: FakeStudio) -> None:
    studio.live_session = COOKIES["sessionid"]
    r = rig(studio, values=stored())

    page = r.site.project_page(STUDIO_ID)

    assert len(page.proposal) == 2 and len(page.all_designs) == 3
    assert page.report_url == STUDIO_URL + REPORT_PATH
    assert r.sign_in.calls == []
    [(method, path, cookie)] = studio.asked
    assert (method, path) == ("GET", f"/project/details/{STUDIO_ID}/")
    assert f"sessionid={COOKIES['sessionid']}" in cookie
    assert f"csrftoken={COOKIES['csrftoken']}" in cookie


def test_with_no_http_it_makes_a_session_of_its_own_and_closes_it(
        monkeypatch: pytest.MonkeyPatch, studio: FakeStudio) -> None:
    class Own:
        made = closed = 0

        def __enter__(self) -> Own:
            Own.made += 1
            return self

        def __exit__(self, *exc: object) -> None:
            Own.closed += 1

        def get(self, url: str, **kwargs: Any) -> requests.Response:
            return studio.http.get(url, **kwargs)  # to the fake, never the real Studio

    studio.live_session = COOKIES["sessionid"]
    monkeypatch.setattr(studio_designs.requests, "Session", Own)
    clock = FakeClock(datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc))

    page = StudioSite(MemorySecrets(stored()), clock, sign_in=SignIn(None)).project_page(STUDIO_ID)

    assert len(page.proposal) == 2 and Own.made == Own.closed == 1


def test_with_no_session_it_signs_in_once_and_stores_only_the_two_cookies(
        studio: FakeStudio) -> None:
    r = rig(studio, gives={**COOKIES, "community": "not-a-studio-cookie"})

    page = r.site.project_page(STUDIO_ID)

    assert len(page.proposal) == 2
    assert r.sign_in.calls == [(USERNAME, PASSWORD)]
    assert json.loads(r.secrets.get("studio_session")) == COOKIES
    [(_, _, cookie)] = studio.asked
    assert "community" not in cookie

    r.site.project_page(STUDIO_ID)  # the next read uses what was stored
    assert len(r.sign_in.calls) == 1


def test_an_expired_session_is_renewed_and_the_page_read(studio: FakeStudio) -> None:
    old = {"sessionid": "expired5session", "csrftoken": "expired6csrf"}
    studio.live_session = "someone-elses"
    r = rig(studio, values=stored(old))

    page = r.site.project_page(STUDIO_ID)

    assert len(page.proposal) == 2 and len(r.sign_in.calls) == 1
    assert json.loads(r.secrets.get("studio_session")) == COOKIES
    assert ["expired5session" in c for _, _, c in studio.asked] == [True, False]


def test_it_only_ever_gets_project_pages_and_follows_no_redirect(studio: FakeStudio) -> None:
    r = rig(studio, values=stored())  # sent to /login, signs in, reads again

    r.site.project_page(STUDIO_ID)
    studio.reply = (302, "https://elsewhere.example/steal")
    failure(lambda: r.site.project_page(STUDIO_ID), "page_changed")
    studio.reply = (302, "/login/")
    failure(lambda: r.site.project_page(STUDIO_ID), "sign_in_failed")

    assert {method for method, _, _ in studio.asked} == {"GET"}
    assert {path for _, path, _ in studio.asked} == {f"/project/details/{STUDIO_ID}/"}


def test_a_session_turned_away_after_signing_in_is_not_tried_a_third_time(
        studio: FakeStudio) -> None:
    studio.reply = (302, "/login/?next=x")
    r = rig(studio, values=stored())

    error = failure(lambda: r.site.project_page(STUDIO_ID), "sign_in_failed")

    assert "again" in error.reason
    assert len(studio.asked) == 2 and len(r.sign_in.calls) == 1


def test_a_sign_in_that_never_returns_fails_at_its_limit_and_says_so(studio: FakeStudio) -> None:
    release = threading.Event()

    def hangs(username: str, password: str) -> dict[str, str]:
        release.wait(30)
        return dict(COOKIES)

    clock = FakeClock(datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc))
    site = StudioSite(MemorySecrets(LOGIN), clock, http=studio.http, sign_in=hangs,
                      sign_in_limit=timedelta(seconds=0.05))
    try:
        error = failure(lambda: site.project_page(STUDIO_ID), "sign_in_failed")
    finally:
        release.set()
    assert "too long" in error.reason


def test_a_restart_cannot_retry_a_sign_in_within_the_hour(studio: FakeStudio) -> None:
    kept: list[datetime] = []
    clock = FakeClock(datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc))

    def new_site() -> tuple[StudioSite, SignIn]:
        sign_in = SignIn(studio, RuntimeError("wrong password"))
        return StudioSite(MemorySecrets(LOGIN), clock, http=studio.http, sign_in=sign_in,
                          recall=lambda: kept[-1] if kept else None, remember=kept.append), sign_in

    site, sign_in = new_site()
    failure(lambda: site.project_page(STUDIO_ID), "sign_in_failed")
    assert len(sign_in.calls) == 1 and kept == [clock.now()]

    site, sign_in = new_site()                                    # a crash, then a restart
    failure(lambda: site.project_page(STUDIO_ID), "sign_in_failed")
    assert sign_in.calls == []

    clock.advance(timedelta(hours=1))
    failure(lambda: site.project_page(STUDIO_ID), "sign_in_failed")
    assert len(sign_in.calls) == 1


def test_a_remembered_time_in_the_future_does_not_stop_sign_ins(studio: FakeStudio) -> None:
    clock = FakeClock(datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc))
    sign_in = SignIn(studio)
    site = StudioSite(MemorySecrets(LOGIN), clock, http=studio.http, sign_in=sign_in,
                      recall=lambda: clock.now() + timedelta(days=30))
    site.project_page(STUDIO_ID)
    assert len(sign_in.calls) == 1


def test_a_failed_sign_in_is_not_retried_within_the_hour(studio: FakeStudio) -> None:
    r = rig(studio, gives=RuntimeError("wrong password"))

    for _ in range(3):
        failure(lambda: r.site.project_page(STUDIO_ID), "sign_in_failed")
    r.clock.advance(timedelta(minutes=59, seconds=59))
    failure(lambda: r.site.project_page(STUDIO_ID), "sign_in_failed")
    assert len(r.sign_in.calls) == 1
    assert studio.asked == []  # with no session, nothing was asked of Studio either

    r.clock.advance(timedelta(seconds=1))
    r.sign_in.gives = COOKIES
    assert len(r.site.project_page(STUDIO_ID).proposal) == 2
    assert len(r.sign_in.calls) == 2


def test_a_good_sign_in_counts_towards_the_gap_too(studio: FakeStudio) -> None:
    r = rig(studio)
    r.site.project_page(STUDIO_ID)

    studio.live_session = None  # the session dies at once
    failure(lambda: r.site.project_page(STUDIO_ID), "sign_in_failed")

    assert len(r.sign_in.calls) == 1


def test_the_gap_can_be_set(studio: FakeStudio) -> None:
    sign_in = SignIn(studio, RuntimeError("no"))
    clock = FakeClock(datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc))
    site = StudioSite(MemorySecrets(LOGIN), clock, http=studio.http, sign_in=sign_in,
                      min_sign_in_gap=timedelta(minutes=5))

    failure(lambda: site.project_page(STUDIO_ID), "sign_in_failed")
    clock.advance(timedelta(minutes=5))
    failure(lambda: site.project_page(STUDIO_ID), "sign_in_failed")

    assert len(sign_in.calls) == 2


def test_two_readers_at_once_sign_in_once(studio: FakeStudio) -> None:
    def slow_sign_in(username: str, password: str) -> dict[str, str]:
        time.sleep(0.2)  # the other reader arrives while this one is signing in
        return signed(username, password)

    signed = SignIn(studio)
    site = StudioSite(MemorySecrets(LOGIN), FakeClock(datetime(2026, 10, 30, tzinfo=timezone.utc)),
                      http=studio.http, sign_in=slow_sign_in)
    pages: list[ProjectPage] = []
    readers = [threading.Thread(target=lambda: pages.append(site.project_page(STUDIO_ID)))
               for _ in range(2)]

    for reader in readers:
        reader.start()
    for reader in readers:
        reader.join(10)

    assert len(pages) == 2 and len(signed.calls) == 1


@pytest.mark.parametrize("values", [
    {}, {"studio_username": USERNAME}, {"studio_password": PASSWORD},
], ids=["nothing saved", "no password", "no username"])
def test_with_no_login_nothing_is_tried(studio: FakeStudio, values: dict[str, str]) -> None:
    r = rig(studio, values=values)

    failure(lambda: r.site.project_page(STUDIO_ID), "no_login")

    assert r.sign_in.calls == [] and studio.asked == []


def test_a_login_later_saved_is_used_at_once(studio: FakeStudio) -> None:
    r = rig(studio, values={})
    failure(lambda: r.site.project_page(STUDIO_ID), "no_login")

    r.secrets.put("studio_username", USERNAME)
    r.secrets.put("studio_password", PASSWORD)

    assert len(r.site.project_page(STUDIO_ID).proposal) == 2


class BrokenSecrets:
    """Credential Manager failing: for every name, or only when a value is stored."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values

    def get(self, name: str) -> str | None:
        if self.values is None:
            raise SecretsError(f"could not read the {name} secret")
        return self.values.get(name)

    def put(self, name: str, value: str) -> None:
        raise SecretsError(f"could not store the {name} secret")

    def remove(self, name: str) -> None:
        raise SecretsError(f"could not remove the {name} secret")


def test_credential_manager_failing_reads_as_no_login(studio: FakeStudio) -> None:
    r = rig(studio, secrets=BrokenSecrets())

    failure(lambda: r.site.project_page(STUDIO_ID), "no_login")

    assert r.sign_in.calls == []


def test_a_session_that_cant_be_kept_is_a_failed_sign_in(studio: FakeStudio) -> None:
    r = rig(studio, secrets=BrokenSecrets(LOGIN))

    failure(lambda: r.site.project_page(STUDIO_ID), "sign_in_failed")


@pytest.mark.parametrize("bad", ["{not json", "[]", '{"sessionid": "x"}',
                                 '{"sessionid": 5, "csrftoken": "y"}',
                                 '{"sessionid": "", "csrftoken": "y"}'])
def test_a_stored_session_that_isnt_two_cookies_counts_as_missing(studio: FakeStudio,
                                                                  bad: str) -> None:
    r = rig(studio, values={**LOGIN, "studio_session": bad})

    r.site.project_page(STUDIO_ID)

    assert len(r.sign_in.calls) == 1 and json.loads(r.secrets.get("studio_session")) == COOKIES


@pytest.mark.parametrize("gives", [{"sessionid": "only-this"}, {"csrftoken": "only-this"}, {},
                                   {"sessionid": "", "csrftoken": "y"}])
def test_a_sign_in_without_both_cookies_failed(studio: FakeStudio, gives: dict[str, str]) -> None:
    r = rig(studio, gives=gives)

    failure(lambda: r.site.project_page(STUDIO_ID), "sign_in_failed")

    assert r.secrets.get("studio_session") is None and studio.asked == []


class _NoNetwork:
    def __init__(self, error: Exception) -> None:
        self.error, self.calls = error, 0

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        self.calls += 1
        raise self.error


@pytest.mark.parametrize("error", [requests.ConnectionError("no route"), requests.Timeout("slow"),
                                   requests.exceptions.ChunkedEncodingError("cut off"),
                                   OSError("socket"), ValueError("Invalid header value")],
                         ids=["connection", "timeout", "cut off", "socket", "bad header"])
def test_a_network_failure_never_signs_in(studio: FakeStudio, error: Exception) -> None:
    down = _NoNetwork(error)
    r = rig(studio, values=stored(), http=down)

    failure(lambda: r.site.project_page(STUDIO_ID), "network")

    assert r.sign_in.calls == [] and down.calls == 1


def test_a_network_failure_right_after_signing_in_keeps_the_session(studio: FakeStudio) -> None:
    r = rig(studio, http=_NoNetwork(requests.ConnectionError("no route")))

    failure(lambda: r.site.project_page(STUDIO_ID), "network")

    assert len(r.sign_in.calls) == 1 and json.loads(r.secrets.get("studio_session")) == COOKIES


def test_a_refused_port_is_a_network_failure() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    server.server_close()  # nothing listens there now
    with local_session() as session:
        r = rig(FakeStudio(), values=stored(), http=_Rerouted(session, f"http://127.0.0.1:{port}"))

        failure(lambda: r.site.project_page(STUDIO_ID), "network")


@pytest.mark.parametrize(("status", "code"), [
    (500, "network"), (503, "network"), (429, "network"),
    (404, "page_changed"), (403, "page_changed"), (204, "page_changed"),
])
def test_an_error_answer_is_named_without_signing_in(studio: FakeStudio, status: int,
                                                     code: str) -> None:
    studio.reply = (status, "")
    r = rig(studio, values=stored())

    failure(lambda: r.site.project_page(STUDIO_ID), code)

    assert r.sign_in.calls == []


def test_a_redesigned_page_reads_as_page_changed(studio: FakeStudio) -> None:
    studio.live_session = COOKIES["sessionid"]
    studio.html = f'<html><div class="design-details">{HANGING}</div></html>'
    r = rig(studio, values=stored())

    failure(lambda: r.site.project_page(STUDIO_ID), "page_changed")

    assert r.sign_in.calls == []


def test_an_http_that_follows_redirects_itself_is_still_seen_going_to_login(
        studio: FakeStudio) -> None:
    class Followed:
        status_code, headers, text = 200, {}, "<html>the login form</html>"
        url = f"{STUDIO_URL}/login/?next=/project/details/{STUDIO_ID}/"

    class Following:
        def __init__(self) -> None:
            self.answers: list[Any] = [Followed()]

        def get(self, url: str, **kwargs: Any) -> Any:
            return self.answers.pop() if self.answers else studio.http.get(url, **kwargs)

    r = rig(studio, values=stored(), http=Following())

    assert len(r.site.project_page(STUDIO_ID).proposal) == 2
    assert len(r.sign_in.calls) == 1


@pytest.mark.parametrize("href", [
    "/install_summary_landscape/fakeley/fakeley_101426/7654321/",  # another job's
    "/install_summary_pdf/testerson/testerson_101426/1234567/",  # not a report address
], ids=["another job", "not a report address"])
def test_a_report_link_fetching_could_not_use_reads_as_none(studio: FakeStudio, href: str) -> None:
    studio.live_session = COOKIES["sessionid"]
    studio.html = page_html([HANGING], report_href=href)
    r = rig(studio, values=stored())

    page = r.site.project_page(STUDIO_ID)

    assert page.report_url is None and len(page.proposal) == 1


@pytest.mark.parametrize("bad", ["", "12a", "../../logout", "123/../456", "٣٤٥", None, 1234567])
def test_an_id_that_isnt_digits_is_refused_before_anything_is_asked(studio: FakeStudio,
                                                                    bad: Any) -> None:
    r = rig(studio, values=stored())

    with pytest.raises(ValueError):
        r.site.project_page(bad)

    assert studio.asked == [] and r.sign_in.calls == []


def test_no_secret_or_design_name_reaches_any_reason(studio: FakeStudio) -> None:
    """Every way project_page can fail, with its causes quoting all they can."""
    quoting = f"{USERNAME} {PASSWORD} {COOKIES['sessionid']} {COOKIES['csrftoken']} {HANGING}"
    errors = [failure(lambda: rig(studio, values={}).site.project_page(STUDIO_ID), "no_login")]

    r = rig(studio, gives=RuntimeError(quoting))  # a sign-in that quotes everything
    errors.append(failure(lambda: r.site.project_page(STUDIO_ID), "sign_in_failed"))
    errors.append(failure(lambda: r.site.project_page(STUDIO_ID), "sign_in_failed"))  # the gap

    r = rig(studio, values=stored(), http=_NoNetwork(requests.ConnectionError(quoting)))
    errors.append(failure(lambda: r.site.project_page(STUDIO_ID), "network"))
    r = rig(studio, values=stored(), http=_NoNetwork(ValueError(quoting)))
    errors.append(failure(lambda: r.site.project_page(STUDIO_ID), "network"))

    for status, code in ((503, "network"), (404, "page_changed"), (302, "page_changed")):
        studio.reply = (status, "/elsewhere/")
        r = rig(studio, values=stored())
        errors.append(failure(lambda: r.site.project_page(STUDIO_ID), code))
    studio.reply = (302, "/login/")
    r = rig(studio, values=stored())
    errors.append(failure(lambda: r.site.project_page(STUDIO_ID), "sign_in_failed"))
    r = rig(studio, secrets=BrokenSecrets(LOGIN))
    errors.append(failure(lambda: r.site.project_page(STUDIO_ID), "sign_in_failed"))

    studio.reply = None
    studio.live_session = COOKIES["sessionid"]
    studio.html = f'<div class="design-details">{quoting}</div>'
    r = rig(studio, values=stored())
    errors.append(failure(lambda: r.site.project_page(STUDIO_ID), "page_changed"))

    assert {error.code for error in errors} == {"no_login", "sign_in_failed", "network",
                                                "page_changed"}
    for error in errors:
        assert not any(text in all_text(error) for text in NEVER_SHOWN), error.code
        assert "127.0.0.1" not in error.reason and STUDIO_ID not in error.reason


def test_nothing_is_logged() -> None:
    source = Path(studio_designs.__file__).read_text(encoding="utf-8")

    assert "logging" not in source and "print(" not in source


# ----- browser_sign_in, against a fake playwright


class FakeElement:
    def __init__(self, name: str, *, visible: bool = True,
                 on_click: Callable[[], None] | None = None) -> None:
        self.name, self.visible, self.on_click = name, visible, on_click
        self.filled: str | None = None
        self.clicks = 0

    def is_visible(self) -> bool:
        return self.visible

    def fill(self, value: str) -> None:
        self.filled = value

    def click(self) -> None:
        self.clicks += 1
        if self.on_click:
            self.on_click()


class FakeFrame:
    def __init__(self, elements: dict[str, list[FakeElement]] | None = None,
                 *, detached: bool = False) -> None:
        self.elements, self.detached = elements or {}, detached

    def query_selector_all(self, selector: str) -> list[FakeElement]:
        if self.detached:
            raise RuntimeError(f"Frame was detached while filling {PASSWORD}")
        return self.elements.get(selector, [])

    def query_selector(self, selector: str) -> FakeElement | None:
        found = self.query_selector_all(selector)
        return found[0] if found else None


class FakePage:
    """A page whose wait_for_timeout runs the next step of its script, if any, at once."""

    def __init__(self, url: str = "about:blank", frames: Iterable[FakeFrame] = ()) -> None:
        self.url, self.frames, self.waits = url, list(frames), 0
        self.script: list[Callable[[], None]] = []
        self.went_to: list[str] = []

    def goto(self, url: str, **kwargs: Any) -> None:
        self.url = url
        self.went_to.append(url)

    def wait_for_timeout(self, ms: float) -> None:
        self.waits += 1
        if self.script:
            self.script.pop(0)()


class FakeBrowserWorld:
    """Playwright's sync API, with the community site's login and a Studio tab opened by a click.
    A frame with a search box comes before the login frame, which has a Sign up button too."""

    def __init__(self, *, accepts: bool = True, fail_launch: bool = False) -> None:
        self.accepts, self.fail_launch = accepts, fail_launch
        self.launches: list[dict[str, Any]] = []
        self.closed = self.stopped = False
        self.cookie_urls: list[str] = []
        self.cookie_jar: list[dict[str, str]] = [
            {"name": "sessionid", "value": COOKIES["sessionid"]},
            {"name": "csrftoken", "value": COOKIES["csrftoken"]},
            {"name": "other", "value": "not-kept"},
        ]
        self.email = FakeElement("email")
        self.password = FakeElement("password")
        self.hidden_email = FakeElement("hidden email", visible=False)
        self.search = FakeElement("search")
        self.sign_up = FakeElement("sign up")
        self.log_in = FakeElement("log in", on_click=self._submitted)
        login_frame = FakeFrame({
            "input[type=email]": [self.hidden_email, self.email],
            "input[type=password]": [self.password],
            "button:has-text('Log in')": [self.log_in],
            "button:has-text('Sign')": [self.sign_up],
        })
        self.page = FakePage(frames=[FakeFrame({"input[type=text]": [self.search]})])
        # The form takes a moment to appear.
        self.page.script.append(lambda: self.page.frames.append(login_frame))
        self.studio_link = FakeElement("studio link", on_click=self._open_studio)
        self.tab = FakePage("https://studio.inspiredclosets.com/gateway/")
        self.tab.script.append(lambda: setattr(
            self.tab, "url", "https://studio.inspiredclosets.com/dashboard/"))
        self.opened: FakePage | None = None

    def _submitted(self) -> None:
        if not self.accepts:
            return
        def moved_on() -> None:
            self.page.url = "https://www.inspiredclosetscommunity.com/s/"
            self.page.frames = [FakeFrame({"a[href*='/s/studio']": [self.studio_link]})]
        # The page moves: first the login frame detaches, then the new page settles.
        self.page.url = "https://www.inspiredclosetscommunity.com/s/"
        self.page.frames = [FakeFrame(detached=True)]
        self.page.script.append(moved_on)

    def _open_studio(self) -> None:
        self.opened = self.tab

    # the parts of the API browser_sign_in touches

    def sync_playwright(self) -> FakeBrowserWorld:
        return self

    def __enter__(self) -> FakeBrowserWorld:
        return self

    def __exit__(self, *exc: object) -> bool:
        self.stopped = True
        return False

    @property
    def chromium(self) -> FakeBrowserWorld:
        return self

    def launch(self, **kwargs: Any) -> FakeBrowserWorld:
        self.launches.append(kwargs)
        if self.fail_launch:
            raise RuntimeError(f"Edge is missing; was typing {PASSWORD}")
        return self

    def new_context(self) -> FakeBrowserWorld:
        return self

    def new_page(self) -> FakePage:
        return self.page

    def close(self) -> None:
        self.closed = True

    def expect_page(self, **kwargs: Any) -> Any:
        world = self

        class Opening:
            def __enter__(self) -> Opening:
                return self

            def __exit__(self, *exc: object) -> bool:
                self.value = world.opened
                return False

        return Opening()

    def cookies(self, url: str) -> list[dict[str, str]]:
        self.cookie_urls.append(url)
        return self.cookie_jar


def install(monkeypatch: pytest.MonkeyPatch, world: FakeBrowserWorld) -> None:
    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = world.sync_playwright  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)


def test_playwright_is_imported_only_inside_the_real_sign_in() -> None:
    tree = ast.parse(Path(studio_designs.__file__).read_text(encoding="utf-8"))

    at_module_level = [node for node in tree.body if isinstance(node, ast.Import | ast.ImportFrom)]
    names = [alias.name for node in at_module_level for alias in node.names]
    froms = [node.module or "" for node in at_module_level if isinstance(node, ast.ImportFrom)]
    assert not any("playwright" in name for name in [*names, *froms])


def test_browser_sign_in_walks_the_community_site_to_studios_cookies(
        monkeypatch: pytest.MonkeyPatch) -> None:
    world = FakeBrowserWorld()
    install(monkeypatch, world)

    cookies = browser_sign_in(USERNAME, PASSWORD)

    assert cookies == COOKIES  # the two, and no other
    assert world.launches == [{"channel": "msedge", "headless": True}]
    assert world.page.went_to == [COMMUNITY_LOGIN_URL]
    # In the frame holding the password box, the visible email box, not the other frame's search.
    assert world.email.filled == USERNAME and world.password.filled == PASSWORD
    assert world.search.filled is None and world.hidden_email.filled is None
    assert world.log_in.clicks == 1 and world.sign_up.clicks == 0  # Log in is preferred
    assert world.studio_link.clicks == 1
    assert world.cookie_urls and set(world.cookie_urls) == {STUDIO_URL}
    assert world.closed and world.stopped


def test_browser_sign_in_waits_for_the_tab_to_reach_the_dashboard(
        monkeypatch: pytest.MonkeyPatch) -> None:
    world = FakeBrowserWorld()
    install(monkeypatch, world)

    browser_sign_in(USERNAME, PASSWORD)

    assert world.tab.url.endswith("/dashboard/") and world.tab.waits == 1


def test_a_wrong_password_waits_90_seconds_then_fails_in_plain_words(
        monkeypatch: pytest.MonkeyPatch) -> None:
    world = FakeBrowserWorld(accepts=False)
    install(monkeypatch, world)

    error = failure(lambda: browser_sign_in(USERNAME, PASSWORD), "sign_in_failed")

    assert world.page.waits == 1 + 90  # one for the form to appear, then the 90 one-second polls
    assert world.studio_link.clicks == 0 and world.closed and world.stopped
    assert not any(text in all_text(error) for text in NEVER_SHOWN)


def test_cookies_that_never_arrive_fail_the_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    world = FakeBrowserWorld()
    world.cookie_jar = [{"name": "sessionid", "value": COOKIES["sessionid"]}]
    install(monkeypatch, world)

    error = failure(lambda: browser_sign_in(USERNAME, PASSWORD), "sign_in_failed")

    assert COOKIES["sessionid"] not in all_text(error) and world.closed


def test_a_browser_error_is_replaced_by_fixed_words_with_nothing_chained(
        monkeypatch: pytest.MonkeyPatch) -> None:
    world = FakeBrowserWorld(fail_launch=True)
    install(monkeypatch, world)

    error = failure(lambda: browser_sign_in(USERNAME, PASSWORD), "sign_in_failed")

    assert PASSWORD not in all_text(error) and error.__context__ is None


def test_without_playwright_the_sign_in_fails_in_plain_words(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)  # import raises ImportError

    error = failure(lambda: browser_sign_in(USERNAME, PASSWORD), "sign_in_failed")

    assert "installed" in error.reason


def test_a_site_using_browser_sign_in_keeps_its_cookies(monkeypatch: pytest.MonkeyPatch,
                                                        studio: FakeStudio) -> None:
    world = FakeBrowserWorld()
    install(monkeypatch, world)
    studio.live_session = COOKIES["sessionid"]
    secrets = MemorySecrets(LOGIN)
    clock = FakeClock(datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc))

    page = StudioSite(secrets, clock, http=studio.http).project_page(STUDIO_ID)  # default sign_in

    assert len(page.proposal) == 2 and json.loads(secrets.get("studio_session")) == COOKIES
