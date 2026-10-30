"""The board served for real: waitress on 127.0.0.1 at a port the OS picks, in a daemon thread.

One start and one stop, with a real browser-like client (urllib with a cookie jar) in between,
so the token link, the cookie, a POST and the shutdown are all proven over a socket. Nothing
leaves 127.0.0.1.
"""
from __future__ import annotations

import http.cookiejar
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pytest

from ngrunner.board import BoardServer, create_board, new_token
from ngrunner.board.views import BoardView, HealthView, JobRow, Tick, TrayState


class _Views:
    def board(self) -> BoardView:
        when = datetime(2026, 10, 30, 22, 0, tzinfo=timezone.utc)
        return BoardView(
            jobs=(JobRow("900001", "Testerson_101426", None, (Tick("row", "Row", "done"),),
                         "Filed 10/30 2:05 PM"),),
            your_list=(), cards=(),
            health=HealthView((), when, when, when, None),
            tray=TrayState("green", 0, "NG Runner is running"),
            paused=False,
        )

    def history(self) -> tuple:
        return ()


class _Actions:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.threads: list[str] = []

    def _record(self, *call) -> str:
        self.calls.append(call)
        self.threads.append(threading.current_thread().name)
        return "Paused."

    def pause(self) -> str:
        return self._record("pause")

    def resume(self) -> str:
        return self._record("resume")

    def check_now(self) -> str:
        return self._record("check_now")

    def answer(self, card_id: str, choice: str) -> str:
        return self._record("answer", card_id, choice)

    def command(self, text: str) -> str:
        return self._record("command", text)

    def add_job(self, text: str) -> str:
        return self._record("add_job", text)


def test_the_board_serves_on_127_0_0_1_at_an_os_chosen_port_and_stops():
    token = new_token()
    actions = _Actions()
    server = BoardServer(create_board(_Views(), actions, token=token))

    url = server.start()
    try:
        parts = urllib.parse.urlsplit(url)
        assert parts.hostname == "127.0.0.1" and parts.port and parts.port > 0
        assert urllib.parse.parse_qs(parts.query) == {"t": [token]}
        assert server.url == url

        browser = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with browser.open(url, timeout=10) as page:  # the token link: 303, cookie, the board
            assert page.geturl() == f"http://127.0.0.1:{parts.port}/"
            assert "Testerson_101426" in page.read().decode("utf-8")

        post = urllib.request.Request(
            f"http://127.0.0.1:{parts.port}/pause",
            data=urllib.parse.urlencode({"token": token}).encode(),
            headers={"Origin": f"http://127.0.0.1:{parts.port}"},
        )
        with browser.open(post, timeout=10) as page:
            assert "Paused." in page.read().decode("utf-8")
        assert actions.calls == [("pause",)]

        squatter = socket.socket()
        squatter.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with squatter, pytest.raises(OSError):  # the port is held exclusively
            squatter.bind(("127.0.0.1", parts.port))
    finally:
        server.stop()

    assert not server.running
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(f"http://127.0.0.1:{parts.port}/", timeout=2)


def test_a_server_starts_once():
    server = BoardServer(create_board(_Views(), _Actions(), token=new_token()))
    server.start()
    try:
        with pytest.raises(RuntimeError):
            server.start()
    finally:
        server.stop()
    server.stop()  # a second stop is harmless
