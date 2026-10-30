"""
Serving the board: waitress on 127.0.0.1, at a port the OS picks, in a daemon thread.

Why these choices (build-spec 6.6; design 6, Stack): 127.0.0.1 only, so nothing on the shop's
network can reach it; a port picked per launch, so nothing can be told in advance where the
board is; the listening socket bound with SO_EXCLUSIVEADDRUSE, because Windows otherwise lets
another program bind the same port with SO_REUSEADDR and take some of the board's connections;
a daemon thread, so the board never keeps NG Runner from exiting. waitress is the server
Assembly Reports already runs in production.
"""
from __future__ import annotations

import logging
import socket
import threading

from flask import Flask
from waitress import wasyncore
from waitress.server import create_server

from ngrunner.board.app import HOST, open_url, set_port

_log = logging.getLogger(__name__)


class BoardServer:
    """The board's server. start() once, stop() any number of times."""

    def __init__(self, app: Flask, *, threads: int = 4) -> None:
        self._app = app
        self._threads = threads
        self._map: dict = {}  # waitress's socket map: owned here, so stop() can close it all
        self._server = None
        self._thread: threading.Thread | None = None
        self.url: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> str:
        """Start serving; returns the address that opens the board. It carries the token, so
        hand it only to the browser (webbrowser.open) and never log it."""
        if self._server is not None:
            raise RuntimeError("this board server was already started")
        sock = _listening_socket()
        self._server = create_server(
            self._app, map=self._map, sockets=[sock], threads=self._threads,
            channel_timeout=30, connection_limit=50, ident="NG Runner",
        )
        set_port(self._app, sock.getsockname()[1])
        self._thread = threading.Thread(target=self._serve, name="ngrunner-board", daemon=True)
        self._thread.start()
        self.url = open_url(self._app)
        return self.url

    def stop(self, timeout: float = 5.0) -> None:
        """Close every connection and the listening socket, then wait for the thread."""
        server, thread = self._server, self._thread
        if server is None or thread is None or not thread.is_alive():
            return
        # Closing sockets from another thread races the loop; the trigger runs the close
        # inside the loop's own thread, and the loop ends once its map is empty.
        server.trigger.pull_trigger(lambda: wasyncore.close_all(self._map))
        thread.join(timeout)
        server.task_dispatcher.shutdown(timeout=timeout)
        if thread.is_alive():
            _log.warning("the board's server did not stop within %s seconds", timeout)

    def _serve(self) -> None:
        try:
            self._server.run()
        except Exception:  # noqa: BLE001 - logged; the board is down, the rest of NG Runner runs on
            _log.exception("the board's server stopped with an error")


def _listening_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)  # Windows only
    if exclusive is not None:
        sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
    sock.bind((HOST, 0))
    return sock
