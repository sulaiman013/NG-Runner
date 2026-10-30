"""The Pushover client: the direct path to Jim's phone when receipts stop (design section 4).
No network: an injected fake, and one real requests session against a server on 127.0.0.1."""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest
import requests

from ngrunner.messages.pushover import API_URL, send

TOKEN = "a" * 10 + "B1" * 10                 # 30 letters and digits, as Pushover's are
USER = "u" * 20 + "9Z" * 5


class Reply:
    def __init__(self, status_code=200, body=None):
        self.status_code, self._body = status_code, body

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


class FakeHttp:
    """Records each post and answers with the replies given, or raises one."""

    def __init__(self, *replies):
        self.replies, self.posts = list(replies), []

    def post(self, url, *, data, timeout):
        self.posts.append((url, dict(data), timeout))
        reply = self.replies.pop(0) if self.replies else Reply(200, {"status": 1})
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_an_urgent_message_goes_out_at_high_priority():
    http = FakeHttp(Reply(200, {"status": 1, "request": "x"}))
    assert send(TOKEN, USER, "NG Runner can't send messages", "No receipt since 2:58 PM.",
                urgent=True, http=http) is True
    url, form, timeout = http.posts[0]
    assert url == API_URL and timeout > 0
    assert form == {"token": TOKEN, "user": USER, "title": "NG Runner can't send messages",
                    "message": "No receipt since 2:58 PM.", "priority": "1"}


def test_an_ordinary_message_goes_out_at_normal_priority():
    http = FakeHttp()
    assert send(TOKEN, USER, "NG Runner · Fri 10/30", "Nothing new.", urgent=False, http=http)
    assert http.posts[0][1]["priority"] == "0"


@pytest.mark.parametrize("reply", [
    Reply(400, {"status": 0, "errors": ["user identifier is invalid"]}),
    Reply(429, {"status": 0}),                  # over the monthly limit
    Reply(500, None),
    Reply(200, {"status": 0}),
    Reply(200, None),                           # not JSON
    Reply(200, ["status", 1]),                  # JSON, but not Pushover's
    requests.ConnectionError("offline"),
    requests.Timeout("slow"),
    RuntimeError("anything at all"),
])
def test_anything_but_pushovers_yes_is_false_and_nothing_is_raised(reply):
    assert send(TOKEN, USER, "Title", "Text", urgent=True, http=FakeHttp(reply)) is False


@pytest.mark.parametrize("token, user", [
    ("", USER), (TOKEN, ""), (None, USER), ("short", USER), (TOKEN, USER + "x"),
    ("a" * 29 + "!", USER),
])
def test_without_a_well_formed_token_and_user_nothing_is_sent(token, user):
    http = FakeHttp()
    assert send(token, user, "Title", "Text", urgent=True, http=http) is False
    assert http.posts == []


def test_an_empty_message_is_not_sent():
    http = FakeHttp()
    assert send(TOKEN, USER, "Title", "   ", urgent=True, http=http) is False
    assert http.posts == []


def test_a_long_message_keeps_whole_lines_and_says_the_rest_is_on_the_board():
    lines = [f"{n}. Testerson_101426: line {n} of the digest, written out at length." for n in
             range(1, 41)]
    http = FakeHttp()
    assert send(TOKEN, USER, "T" * 300, "\n".join(lines), urgent=False, http=http)
    form = http.posts[0][1]
    assert len(form["title"]) == 250 and len(form["message"]) <= 1024
    kept = form["message"].split("\n")
    assert kept[:-1] == lines[:len(kept) - 1]
    assert kept[-1] == f"{40 - (len(kept) - 1)} more lines are on the board."


def test_the_token_and_user_key_are_never_logged(caplog):
    caplog.set_level(logging.DEBUG)
    for reply in (Reply(200, {"status": 1}), RuntimeError(f"boom {TOKEN} {USER}")):
        send(TOKEN, USER, "Title", "Text", urgent=True, http=FakeHttp(reply))
    assert TOKEN not in caplog.text and USER not in caplog.text


# ----- the real wire format, against a fake Pushover on 127.0.0.1


class _FakePushover(BaseHTTPRequestHandler):
    seen: list[tuple[str, str]] = []

    def do_POST(self):  # noqa: N802 - the http.server name
        length = int(self.headers.get("Content-Length", 0))
        _FakePushover.seen.append((self.path, self.rfile.read(length).decode("utf-8")))
        body = json.dumps({"status": 1, "request": "fake"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the test output quiet
        pass


@pytest.fixture
def fake_pushover():
    _FakePushover.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakePushover)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/1/messages.json"
    server.shutdown()
    server.server_close()


def test_a_real_session_posts_a_form_with_the_token_in_the_body_not_the_address(
        fake_pushover, caplog):
    caplog.set_level(logging.DEBUG)
    session = requests.Session()
    session.trust_env = False                         # no proxy between us and 127.0.0.1
    assert send(TOKEN, USER, "NG Runner · Fri 10/30", "1. Testerson_101426: filed.",
                urgent=False, http=session, url=fake_pushover) is True
    path, body = _FakePushover.seen[0]
    assert path == "/1/messages.json" and TOKEN not in path
    assert parse_qs(body) == {"token": [TOKEN], "user": [USER],
                              "title": ["NG Runner · Fri 10/30"],
                              "message": ["1. Testerson_101426: filed."], "priority": ["0"]}
    assert TOKEN not in caplog.text and USER not in caplog.text
