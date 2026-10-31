"""
OpenRouter for the reader evaluation: GPT-6 Luna only (Sulaiman, 2026-10-31). Any other model id is
refused in code, so nothing can go to another model through OpenRouter by mistake. The request is
pinned: one model, one zero-retention US host, no fallbacks, data collection denied, every parameter
required of the host. The key is read from NGRunner/openrouter_key and appears in no log, request
dump, cached answer or report. No network: a fake transport.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from tools.reader_eval import pairing, payload, prep, providers, truth
from tools.reader_eval import synthetic as s

KEY = "sk-or-test-NOT-A-REAL-KEY-0123456789"
ANSWER = {"verticals": [], "count_unsure": False, "unreadable": []}


def _request() -> payload.WallRequest:
    result = prep.prepare(s.summary_pdf())
    target = next(p for p in pairing.pair("T", result.crops, {"Room1": truth.read_room(s.room_des())}).pairs
                  if p.wall.panels)
    walls = (s.DEFAULT_WALLS[0], s.WallSpec("2", '59 3/4"', image_size=(320, 420)))
    other = prep.prepare(s.summary_pdf(layout="landscape", walls=walls))
    worked = next(p for p in pairing.pair("W", other.crops, {"Room1": truth.read_room(s.room_des())}).pairs
                  if p.wall.panels)
    return payload.build("T/4", target, [worked], result.deny, {"W": other.deny})


def _reply(**over: Any) -> dict[str, Any]:
    reply = {"id": "gen-1", "model": "openai/gpt-6-luna", "provider": "Azure",
             "choices": [{"finish_reason": "stop", "native_finish_reason": "stop",
                          "message": {"role": "assistant", "content": json.dumps(ANSWER), "refusal": None}}],
             "usage": {"prompt_tokens": 20000, "completion_tokens": 3000, "cost": 0.0038,
                       "prompt_tokens_details": {"cached_tokens": 0}}}
    reply.update(over)
    return reply


class Transport:
    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status, self.body = status, body if body is not None else _reply()
        self.sent: list[tuple[str, dict[str, str], bytes]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
        self.sent.append((url, headers, body))
        return self.status, json.dumps(self.body).encode("utf-8")


# ----- only GPT-6 Luna, ever


@pytest.mark.parametrize("model", ["anthropic/claude-opus-5.5", "moonshotai/kimi-k3",
                                   "deepseek/deepseek-v4.1-flash-1017", "openai/gpt-6-luna-pro",
                                   "~openai/gpt-6-luna", "openai/gpt-6-luna:latest", "openai/gpt-6"])
def test_openrouter_refuses_every_model_but_gpt_6_luna(model: str) -> None:
    with pytest.raises(ValueError, match="gpt-6-luna only"):
        providers.OpenRouterLuna(model=model)


def test_a_model_changed_after_construction_is_refused_before_sending() -> None:
    transport = Transport()
    provider = providers.OpenRouterLuna(transport=transport)
    provider.model = "anthropic/claude-opus-5.5"
    with pytest.raises(ValueError, match="gpt-6-luna only"):
        provider.read(_request(), KEY)
    assert transport.sent == []


def test_claude_never_goes_through_openrouter() -> None:
    assert providers.AnthropicMessages.url.startswith("https://api.anthropic.com/")
    assert all(cls.url.startswith("https://openrouter.ai/") is (cls is providers.OpenRouterLuna)
               for cls in providers.PROVIDERS.values())


# ----- the request


def test_the_request_pins_model_host_privacy_and_structured_output() -> None:
    body = providers.OpenRouterLuna().body(_request())
    assert body["model"] == "openai/gpt-6-luna"
    assert body["provider"] == {"order": ["azure/us"], "only": ["azure/us"], "allow_fallbacks": False,
                                "require_parameters": True, "data_collection": "deny", "zdr": True,
                                "max_price": {"prompt": 0.11, "completion": 0.55}}
    assert body["response_format"] == {"type": "json_schema", "json_schema": {
        "name": "wall_reading", "strict": True, "schema": payload.SCHEMA}}
    assert body["reasoning"] == {"effort": "high"} and body["max_completion_tokens"] == 16000
    assert "models" not in body and "route" not in body and "max_tokens" not in body


def test_the_request_carries_the_reviewed_text_and_the_crops_at_native_pixels() -> None:
    req = _request()
    messages = providers.OpenRouterLuna().body(req)["messages"]
    assert messages[0] == {"role": "system", "content": payload.INSTRUCTIONS}
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    image, text = messages[-1]["content"]
    assert image["type"] == "image_url" and image["image_url"]["detail"] == "original"
    assert image["image_url"]["url"].startswith("data:image/png;base64,")
    assert text == {"type": "text", "text": req.target.text}
    assert messages[2] == {"role": "assistant", "content": req.examples[0].answer}


# ----- the reply


def test_an_answer_is_read_and_priced_from_openrouters_reported_cost() -> None:
    reading = providers.OpenRouterLuna(transport=Transport()).read(_request(), KEY)
    assert reading.status == "answered" and reading.answer == ANSWER
    assert reading.cost_usd == pytest.approx(0.0038) and reading.latency_s is not None


def test_without_a_reported_cost_it_is_priced_from_the_tokens_at_the_pinned_hosts_rates() -> None:
    reply = _reply(usage={"prompt_tokens": 20000, "completion_tokens": 3000})
    reading = providers.OpenRouterLuna(transport=Transport(body=reply)).read(_request(), KEY)
    assert reading.cost_usd == pytest.approx((20000 * 0.11 + 3000 * 0.55) / 1e6)


@pytest.mark.parametrize(("reply", "status"), [
    (_reply(provider="OpenAI"), "error"),
    (_reply(model="openai/gpt-6-luna-pro"), "error"),
    (_reply(model="anthropic/claude-opus-5.5"), "error"),
    (_reply(choices=[{"finish_reason": "length", "message": {"content": "{", "refusal": None}}]), "truncated"),
    (_reply(choices=[{"finish_reason": "content_filter", "message": {"content": None, "refusal": None}}]),
     "refused"),
    (_reply(choices=[{"finish_reason": "stop", "message": {"content": None, "refusal": "no"}}]), "refused"),
    (_reply(choices=[{"finish_reason": "stop", "message": {"content": "not json", "refusal": None}}]),
     "schema_failure"),
    (_reply(error={"code": 502, "message": "Provider returned error"}), "error"),
])
def test_another_host_another_model_a_cut_off_a_refusal_or_bad_json_never_counts(reply: dict, status: str) -> None:
    assert providers.OpenRouterLuna(transport=Transport(body=reply)).read(_request(), KEY).status == status


def test_the_dated_version_openrouter_serves_is_accepted() -> None:
    reply = _reply(model="openai/gpt-6-luna-20260922")
    assert providers.OpenRouterLuna(transport=Transport(body=reply)).read(_request(), KEY).status == "answered"


@pytest.mark.parametrize(("status", "words"), [(401, "account"), (402, "account"), (429, "unavailable"),
                                                (502, "unavailable"), (404, "refused")])
def test_http_failures_are_plain_words_and_never_carry_the_key(status: int, words: str) -> None:
    transport = Transport(status=status, body={"error": {"code": status, "message": f"bad key {KEY}"}})
    reading = providers.OpenRouterLuna(transport=transport).read(_request(), KEY)
    assert reading.status == "error" and words in reading.detail and KEY not in reading.detail
    assert len(transport.sent) == 1


# ----- the key, nowhere but the header


def test_the_key_goes_in_the_header_only_never_the_body_or_the_dry_run() -> None:
    transport = Transport()
    provider = providers.OpenRouterLuna(transport=transport)
    req = _request()
    provider.read(req, KEY)
    url, headers, body = transport.sent[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers["Authorization"] == f"Bearer {KEY}" and KEY.encode() not in body
    assert KEY not in payload.dry_run(req, provider.settings()) and KEY not in repr(provider)


def test_the_key_never_reaches_a_reading_or_a_report() -> None:
    from tools.reader_eval import scoring
    reading = providers.OpenRouterLuna(transport=Transport()).read(_request(), KEY)
    assert KEY not in repr(reading) and KEY not in json.dumps(reading.answer)
    report = scoring.summarize([], required={})
    assert KEY not in report.to_json() and KEY not in report.text()


def test_the_worst_case_counts_every_output_token_at_the_pinned_hosts_rate() -> None:
    provider = providers.OpenRouterLuna()
    worst = provider.worst_case_usd(_request())
    assert worst >= 16000 * 0.55 / 1e6 and worst > 0.0038
