"""
The local board, Jim's record of NG Runner's work (build-spec 6.6; design 4, Channels).

create_board() makes the Flask app from two objects: views (what to show, views.BoardViews)
and actions (what Jim's clicks and typed commands do, views.BoardActions). BoardServer serves
it with waitress on 127.0.0.1 at a port the OS picks, in a daemon thread, and start() returns
the address to open it with. Nothing here decides anything about a job.
"""
from __future__ import annotations

from ngrunner.board.app import create_board, new_token, open_url, set_port
from ngrunner.board.server import BoardServer

__all__ = ["BoardServer", "create_board", "new_token", "open_url", "set_port"]
