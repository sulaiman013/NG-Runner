"""
The takeoff on intake on real summaries (local only, marked fixtures): every pair in
.local-fixtures/p2/pairs/, by index, goes through the pipeline's own takeoff worker, the store, the
heads-up, the notes block and the board's words, as a summary NG Runner filed would. Nothing names a
pair: a failure says its index. The client's details come from the pair's own conversion JSON
(its client fields, as guards/headsup.py reads them), and none of them may reach anything the
takeoff makes. The job's folder is a made-up name, so the job's own name can't hide a leak.

Measured 2026-10-31 on the 52 pairs: 34 complete closets, 12 closets with lines the rules don't
place (1 to 7 lines each), and 6 garage or mixed jobs that stay in chat. None failed. A count here
is a floor or a measured fact, never a target: when one moves, find why before changing it.
"""
from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ngrunner.board.views import TakeoffView
from ngrunner.clock import FakeClock
from ngrunner.config import Settings
from ngrunner.convert import rules
from ngrunner.guards import headsup
from ngrunner.guards import takeoffs as heads_up_source
from ngrunner.intake import pipeline
from ngrunner.messages import templates
from ngrunner.messages.lint import check_line, lint
from ngrunner.store import add_takeoff, ensure_job, open_store

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(os.environ.get("NGR_FIXTURES", ROOT / ".local-fixtures"))
PAIRS = FIXTURES / "p2" / "pairs"
FOLDER = "Testerson_101426"

pytestmark = pytest.mark.fixtures


@functools.cache
def _index() -> tuple[dict[str, Any], ...]:
    index = PAIRS / "index.json"
    if not index.is_file():
        pytest.skip("no local pairs: run tools/collect_p2_fixtures.py (AGENTS.md: Fixtures)")
    return tuple(json.loads(index.read_text(encoding="utf-8")))


@functools.cache
def _made(i: int) -> pipeline._Converted:
    path = PAIRS / str(i) / "summary.pdf"
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return pipeline._make_takeoff(i, "4242", None, sha, (path,))


def _client_values(i: int) -> list[str]:
    path = PAIRS / str(i) / "conversion.json"
    if not path.is_file():
        return []
    parsed = json.loads(path.read_bytes().decode("utf-8-sig"))
    return [value.casefold() for value in headsup._client_values(parsed)]


def test_every_real_summary_goes_through_the_takeoff_worker_and_none_fails():
    index = _index()
    states = [_made(i).state if _made(i).takeoff is None
              else ("complete" if _made(i).takeoff.complete else "incomplete")
              for i in range(len(index))]
    counts = {state: states.count(state) for state in set(states)}

    assert len(index) == 52
    assert counts == {"complete": 34, "incomplete": 12, "in_chat": 6}
    assert [i for i, s in enumerate(states) if s == "failed"] == []


def test_garage_and_mixed_pairs_stay_in_chat_and_closets_are_made():
    branches = [_made(i).branch for i in range(len(_index()))]
    assert {b: branches.count(b) for b in set(branches)} == {"closet": 46, "garage": 4, "mixed": 2}
    assert all(_made(i).state == "in_chat" for i, b in enumerate(branches) if b != "closet")


def test_no_real_takeoff_says_more_than_7_lines_could_not_be_verified():
    counts = [len(_made(i).takeoff.could_not_verify) for i in range(len(_index()))
              if _made(i).takeoff is not None and not _made(i).takeoff.complete]
    assert len(counts) == 12 and max(counts) <= 7 and min(counts) >= 1


def test_nothing_the_takeoff_makes_from_a_real_summary_carries_its_clients_details(tmp_path):
    store = open_store(tmp_path / "ngrunner.db")
    ensure_job(store, "4242", FOLDER, now=_now())
    settings = Settings(ng_jobs=tmp_path / "NG Jobs", mozaik_shared=tmp_path / "Mozaik Shared",
                        control=tmp_path / "control", mode="live", order_flags_on=True)
    ctx = SimpleNamespace(store=store, settings=settings, mode="live", clock=FakeClock(_now()))
    leaks, checked = [], 0
    for i in range(len(_index())):
        done = _made(i)
        takeoff = done.takeoff
        record = add_takeoff(
            store, studio_id="4242", render_sha256=hashlib.sha256(str(i).encode()).hexdigest(),
            rules_sha256=rules.fingerprint(), state=done.state, branch=done.branch,
            complete=takeoff is not None and takeoff.complete,
            could_not_verify=0 if takeoff is None else len(takeoff.could_not_verify),
            result=None if takeoff is None else pipeline._takeoff_result(takeoff), at=_now())
        made: list[str] = [repr(record), json.dumps(dataclasses.asdict(record), default=str),
                           json.dumps(pipeline._takeoff_block(record)),
                           pipeline._takeoff_words(record)]
        if record.state == "made":
            message = heads_up_source._own_message(ctx, FOLDER, record, revised=False)
            assert lint(message) == [], (i, message.text)
            made.append(message.text)
            view = _view(record)
            assert all(not check_line(line) for line in view.lines), i
            made += list(view.lines)
            made.append(templates.takeoff_digest(record.result["buckets"],
                                                 complete=record.complete,
                                                 could_not_verify=record.could_not_verify))
        said = " ".join(made).casefold()
        leaks += [i for value in _client_values(i) if value in said]
        checked += bool(_client_values(i))
    assert checked >= 40
    assert sorted(set(leaks)) == []


def test_a_real_takeoff_never_lists_a_partial_list(tmp_path):
    ctx = SimpleNamespace(settings=Settings(ng_jobs=tmp_path, mozaik_shared=tmp_path,
                                            control=tmp_path, mode="live", order_flags_on=True))
    for i in range(len(_index())):
        takeoff = _made(i).takeoff
        if takeoff is None or takeoff.complete:
            continue
        record = SimpleNamespace(complete=False, could_not_verify=len(takeoff.could_not_verify),
                                 result=pipeline._takeoff_result(takeoff))
        message = heads_up_source._own_message(ctx, FOLDER, record, revised=False)
        assert "sheet" not in message.text and "tape" not in message.text, i
        assert f"could not verify {templates._count(record.could_not_verify, 'line')}" in (
            message.text), i


def _view(record: Any) -> TakeoffView:
    from ngrunner import jobmodel

    return jobmodel._takeoff_view(SimpleNamespace(takeoff=record, chats_list=False))


def _now() -> Any:
    from datetime import datetime, timezone

    return datetime(2026, 10, 31, 17, 0, tzinfo=timezone.utc)
