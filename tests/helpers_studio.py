"""
A fake Studio for tests: a server on 127.0.0.1 that answers only the addresses a test gives it
(404 for the rest, as Studio does for a wrong slug) and logs every address asked for.

Why a helper of its own: the fetch tests and the day simulation (docs/p1a/integration-spec.md,
section 8) both play Studio, each serving synthetic summaries from helpers_pdf.summary_pdf. Nothing
here reaches the network.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests


@dataclass(frozen=True)
class Reply:
    status: int = 200
    body: bytes = b""
    length: int | None = -1  # the Content-Length header: -1 for the body's own, None for none
    delay: float = 0.0  # seconds before the reply starts (Studio rendering)
    drip: float = 0.0  # seconds between pieces of the body
    piece: int = 64  # bytes per piece when it drips


@dataclass
class FakeStudio:
    base_url: str = ""
    replies: dict[str, Reply] = field(default_factory=dict)
    asked: list[str] = field(default_factory=list)  # every path asked for, in order
    agents: list[str] = field(default_factory=list)  # the User-Agent of each request

    def serve(self, path: str, body: bytes = b"", **reply: object) -> None:
        """Answer `path` with this from now on; call it again when Studio's answer changes."""
        self.replies[path] = Reply(body=body, **reply)  # type: ignore[arg-type]


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
        studio: FakeStudio = self.server.studio  # type: ignore[attr-defined]
        studio.asked.append(self.path)
        studio.agents.append(self.headers.get("User-Agent", ""))
        reply = studio.replies.get(self.path, Reply(status=404, body=b"<html>Not Found</html>"))
        try:
            time.sleep(reply.delay)
            self.send_response(reply.status)
            self.send_header("Content-Type", "application/pdf" if reply.body[:4] == b"%PDF" else "text/html")
            length = len(reply.body) if reply.length == -1 else reply.length
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.end_headers()
            step = reply.piece if reply.drip else max(len(reply.body), 1)
            for start in range(0, len(reply.body), step):
                self.wfile.write(reply.body[start:start + step])
                time.sleep(reply.drip)
        except OSError:
            pass  # the client gave up first (a timeout test)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - http.server's name
        pass


@contextmanager
def running_studio() -> Iterator[FakeStudio]:
    """A FakeStudio listening on a free port of 127.0.0.1 (its base_url) until the block ends."""
    fake = FakeStudio()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.studio = fake  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    fake.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@contextmanager
def local_session() -> Iterator[requests.Session]:
    """A real session for talking to a fake on 127.0.0.1, with no proxy from this PC's settings
    between the test and it."""
    session = requests.Session()
    session.trust_env = False
    try:
        yield session
    finally:
        session.close()
