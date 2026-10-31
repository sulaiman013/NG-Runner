"""
The reader evaluation's run: the cost cap stops it before the call that could cross it, a cached
answer is reused at no cost, worked walls come only from the frozen jobs, the dry run reads no
key, and with no key nothing is sent. A synthetic world and a fake transport: no network.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from tools.reader_eval import budget, keys, pairing, prep, providers, run, truth
from tools.reader_eval import synthetic as s

ANSWER = {"verticals": [
    {"x_in": 0.0, "bottom_in": 36.0, "top_in": 96.0, "height": "60", "radius": "no", "unsure": []},
    {"x_in": 35.0, "bottom_in": 36.0, "top_in": 96.0, "height": "60", "radius": "yes", "unsure": []}],
    "count_unsure": False, "unreadable": []}


def _case(letter: str, size: tuple[int, int]) -> tuple[prep.SummaryPrep, pairing.Pairing]:
    walls = (s.DEFAULT_WALLS[0], s.WallSpec("2", '59 3/4"', image_size=size))
    result = prep.prepare(s.summary_pdf(walls=walls))
    return result, pairing.pair(letter, result.crops, {"Room1": truth.read_room(s.room_des())})


def _world(tests: str = "ABCD") -> run.World:
    preps, pairings = {}, {}
    for k, letter in enumerate(tests + "XYZ"):
        preps[letter], pairings[letter] = _case(letter, (300 + 7 * k, 400))
    return run.World(preps, pairings)


class Transport:
    def __init__(self, input_tokens: int = 20000) -> None:
        self.sent: list[bytes] = []
        self.input_tokens = input_tokens

    def __call__(self, url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
        self.sent.append(body)
        reply = {"model": "gpt-6-luna", "status": "completed",
                 "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(ANSWER)}]}],
                 "usage": {"input_tokens": self.input_tokens, "output_tokens": 1000}}
        return 200, json.dumps(reply).encode("utf-8")


class Dollar(providers.OpenAIResponses):
    """Each call's worst case is $1, whatever the request."""

    def worst_case_usd(self, req: Any) -> float:
        return 1.0


def _read(world: run.World, provider: providers._Base, tmp_path: Path, *, cap: float = 3.0,
          call: bool = True) -> tuple[run.RunResult, budget.CostGuard]:
    guard = budget.CostGuard(cap)
    result = run.read_walls(world, [p for p in run.test_walls(world) if p.wall.panels], run.worked_walls(world, 3),
                            provider, "sk-test-NOT-A-REAL-KEY", guard, answers=tmp_path, call=call, say=lambda _: None)
    return result, guard


def test_the_cap_stops_the_run_before_the_call_that_could_cross_it(tmp_path: Path) -> None:
    transport = Transport(input_tokens=9_000_000)  # each call really costs $0.90 + output
    result, guard = _read(_world(), Dollar(transport=transport), tmp_path, cap=2.5)
    assert len(transport.sent) == 2 and guard.calls == 2
    assert guard.spent <= 2.5 and result.stopped and result.not_run == 2


def test_a_cached_answer_is_reused_with_no_call_and_no_cost(tmp_path: Path) -> None:
    transport = Transport()
    world = _world()
    first, _ = _read(world, providers.OpenAIResponses(transport=transport), tmp_path)
    assert len(transport.sent) == 4 and len(first.scores) == 4
    again, guard = _read(world, providers.OpenAIResponses(transport=transport), tmp_path)
    assert len(transport.sent) == 4 and guard.calls == 0 and len(again.scores) == 4


def test_scoring_from_the_cache_alone_makes_no_call(tmp_path: Path) -> None:
    transport = Transport()
    result, guard = _read(_world(), providers.OpenAIResponses(transport=transport), tmp_path, call=False)
    assert transport.sent == [] and guard.calls == 0 and result.not_run == 4


def test_with_no_key_a_call_is_refused_before_it_is_made(tmp_path: Path) -> None:
    transport = Transport()
    world = _world()
    with pytest.raises(keys.KeyProblem):
        run.read_walls(world, run.test_walls(world), run.worked_walls(world, 3),
                       providers.OpenAIResponses(transport=transport), None, budget.CostGuard(1.0),
                       answers=tmp_path, call=True, say=lambda _: None)
    assert transport.sent == []


def test_worked_walls_come_only_from_the_frozen_jobs() -> None:
    worked = run.worked_walls(_world(), 3)
    assert {w.case for w in worked} == {"X", "Y", "Z"}
    with pytest.raises(ValueError):
        run.worked_walls(_world(), 6)


def _args(**over: Any) -> argparse.Namespace:
    base = dict(provider="openai", effort="high", worked=3, walls=2, limit=10, cap=1.0, tol_x=1.0, tol_y=1.5)
    base.update(over)
    return argparse.Namespace(**base)


def test_the_dry_run_reads_no_key_and_sends_nothing(monkeypatch: pytest.MonkeyPatch,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    def no_key(*_: Any, **__: Any) -> None:
        raise AssertionError("the dry run read a key")
    monkeypatch.setattr(run, "load", _world)
    monkeypatch.setattr(keys, "read_key", no_key)
    monkeypatch.setattr(run.cases, "job_names", lambda *a, **k: {"A": "Testerson_101426"})
    sent: list[bytes] = []
    monkeypatch.setitem(providers.PROVIDERS, "openai",
                        lambda **kw: providers.OpenAIResponses(transport=lambda *a: sent.append(a[2])))
    assert run.cmd_dry_run(_args()) == 0
    out = capsys.readouterr().out
    assert sent == [] and "DRY RUN A/" in out and "nothing is sent" in out
    assert "no job name or surname found" in out


def test_with_no_key_eval_says_so_and_sends_nothing(monkeypatch: pytest.MonkeyPatch,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    sent: list[bytes] = []
    monkeypatch.setattr(run, "load", _world)
    monkeypatch.setattr(keys, "read_key", lambda name, **_: None)
    monkeypatch.setitem(providers.PROVIDERS, "openai",
                        lambda **kw: providers.OpenAIResponses(transport=lambda *a: sent.append(a[2])))
    assert run.main(["eval", "--provider", "openai"]) == 2
    assert sent == [] and "NGRunner/openai_key" in capsys.readouterr().out


# ----- nothing is sent twice: one retry on a network error, every outcome cached


class Flaky(Transport):
    def __init__(self, failures: int, status: int = 200) -> None:
        super().__init__()
        self.failures, self.status = failures, status

    def __call__(self, url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
        if self.failures > 0:
            self.failures -= 1
            self.sent.append(body)
            raise ConnectionError("connection reset")
        if self.status != 200:
            self.sent.append(body)
            return self.status, b'{"error": {"message": "busy"}}'
        return super().__call__(url, headers, body, timeout)


def _one(world: run.World, transport: Transport, tmp_path: Path, ledger: budget.SpendLedger | None = None
         ) -> tuple[run.RunResult, budget.CostGuard]:
    guard = budget.CostGuard(3.0, ledger=ledger)
    walls = [p for p in run.test_walls(world) if p.wall.panels][:1]
    result = run.read_walls(world, walls, run.worked_walls(world, 3), providers.OpenAIResponses(transport=transport),
                            "sk-test-NOT-A-REAL-KEY", guard, answers=tmp_path, call=True, say=lambda _: None)
    return result, guard


def test_a_network_error_is_retried_once(tmp_path: Path) -> None:
    transport = Flaky(failures=1)
    result, guard = _one(_world("A"), transport, tmp_path)
    assert len(transport.sent) == 2 and result.scores[0].decline is None and guard.calls == 2


def test_a_second_network_error_is_not_retried_and_is_kept(tmp_path: Path) -> None:
    transport = Flaky(failures=5)
    world = _world("A")
    result, _ = _one(world, transport, tmp_path)
    assert len(transport.sent) == 2 and result.scores[0].decline == "error"
    _one(world, transport, tmp_path)
    assert len(transport.sent) == 2  # the error is cached: the crop is never sent again


def test_an_http_error_is_never_retried_or_resent(tmp_path: Path) -> None:
    transport = Flaky(failures=0, status=503)
    world = _world("A")
    _one(world, transport, tmp_path)
    _one(world, transport, tmp_path)
    assert len(transport.sent) == 1


def test_a_network_failure_is_charged_its_worst_case_to_the_ledger(tmp_path: Path) -> None:
    ledger_path = tmp_path / "spend.json"
    transport = Flaky(failures=5)
    _, guard = _one(_world("A"), transport, tmp_path / "answers", budget.SpendLedger(ledger_path, 50.0))
    assert budget.SpendLedger(ledger_path, 50.0).spent == pytest.approx(guard.spent) and guard.spent > 0


def test_the_latency_of_each_call_is_kept_with_its_answer(tmp_path: Path) -> None:
    _one(_world("A"), Transport(), tmp_path)
    saved = [json.loads(p.read_text(encoding="utf-8")) for p in tmp_path.rglob("*.json")]
    assert len(saved) == 1 and saved[0]["latency_s"] >= 0


def test_no_key_reaches_a_cached_answer_or_a_report(tmp_path: Path) -> None:
    from tools.reader_eval import scoring
    result, _ = _one(_world("A"), Transport(), tmp_path)
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert b"NOT-A-REAL-KEY" not in p.read_bytes()
    assert "NOT-A-REAL-KEY" not in scoring.summarize(result.scores, required={}).to_json()
