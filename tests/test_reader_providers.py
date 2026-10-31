"""
The reader evaluation's providers (decisions 16 and 17): OpenAI GPT-6 Luna, direct, as first choice,
and one Claude model as the stronger reference, behind one interface. Pinned model, structured
output, no fallbacks; keys from Windows Credential Manager (NGRunner/openai_key,
NGRunner/anthropic_key), never printed, logged or put in an error. No network: a fake transport.
"""
from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from tools.reader_eval import keys, pairing, payload, prep, providers, truth
from tools.reader_eval import synthetic as s

KEY = "sk-test-NOT-A-REAL-KEY-0123456789"


def _request() -> payload.WallRequest:
    result = prep.prepare(s.summary_pdf())
    target = next(p for p in pairing.pair("T", result.crops, {"Room1": truth.read_room(s.room_des())}).pairs
                  if p.wall.panels)
    walls = (s.DEFAULT_WALLS[0], s.WallSpec("2", '59 3/4"', image_size=(320, 420)))
    other = prep.prepare(s.summary_pdf(layout="landscape", walls=walls))
    worked = next(p for p in pairing.pair("W", other.crops, {"Room1": truth.read_room(s.room_des())}).pairs
                  if p.wall.panels)
    return payload.build("T/4", target, [worked], result.deny | other.deny)


class Transport:
    def __init__(self, status: int = 200, body: Any = None, raises: Exception | None = None) -> None:
        self.status, self.body, self.raises = status, body, raises
        self.sent: list[tuple[str, dict[str, str], bytes]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
        self.sent.append((url, headers, body))
        if self.raises:
            raise self.raises
        return self.status, json.dumps(self.body).encode("utf-8")


ANSWER = {"verticals": [], "count_unsure": False, "unreadable": []}


def _openai_reply(**over: Any) -> dict[str, Any]:
    reply = {"model": "gpt-6-luna", "status": "completed",
             "output": [{"type": "reasoning"}, {"type": "message", "content": [
                 {"type": "output_text", "text": json.dumps(ANSWER)}]}],
             "usage": {"input_tokens": 20000, "input_tokens_details": {"cached_tokens": 15000},
                       "output_tokens": 3000}}
    reply.update(over)
    return reply


def _anthropic_reply(**over: Any) -> dict[str, Any]:
    reply = {"model": "claude-opus-5-5", "stop_reason": "end_turn",
             "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": json.dumps(ANSWER)}],
             "usage": {"input_tokens": 5000, "cache_creation_input_tokens": 0,
                       "cache_read_input_tokens": 20000, "output_tokens": 4000}}
    reply.update(over)
    return reply


# ----- OpenAI GPT-6 Luna, direct


def test_openai_pins_the_model_asks_for_the_schema_and_stores_nothing() -> None:
    body = providers.OpenAIResponses().body(_request())
    assert body["model"] == "gpt-6-luna"
    assert body["text"]["format"] == {"type": "json_schema", "name": "wall_reading", "strict": True,
                                      "schema": payload.SCHEMA}
    assert body["store"] is False and body["reasoning"] == {"effort": "high"}
    assert "fallbacks" not in json.dumps(body) and "models" not in body


def test_openai_sends_the_crops_at_native_pixels_and_the_worked_answers() -> None:
    req = _request()
    body = providers.OpenAIResponses().body(req)
    images = [c for turn in body["input"] for c in turn["content"] if c["type"] == "input_image"]
    assert [i["detail"] for i in images] == ["original", "original"]
    png = base64.b64decode(images[-1]["image_url"].removeprefix("data:image/png;base64,"))
    assert png == req.target.image.png
    assert body["input"][0]["role"] == "developer"
    assert body["input"][2] == {"role": "assistant", "content": [{"type": "output_text",
                                                                  "text": req.examples[0].answer}]}


def test_openai_reads_an_answer_and_prices_it_from_the_usage() -> None:
    reading = providers.OpenAIResponses(transport=Transport(body=_openai_reply())).read(_request(), KEY)
    assert reading.status == "answered" and reading.answer == ANSWER
    assert reading.cost_usd == pytest.approx((5000 * 0.10 + 15000 * 0.01 + 3000 * 0.50) / 1e6)


@pytest.mark.parametrize(("reply", "status"), [
    (_openai_reply(output=[{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]), "refused"),
    (_openai_reply(status="incomplete", incomplete_details={"reason": "max_output_tokens"}), "truncated"),
    (_openai_reply(output=[{"type": "message", "content": [{"type": "output_text", "text": "{oops"}]}]),
     "schema_failure"),
    (_openai_reply(model="gpt-6-sol"), "error"),
])
def test_openai_never_takes_a_refusal_a_cut_off_a_bad_json_or_another_model(reply: dict, status: str) -> None:
    assert providers.OpenAIResponses(transport=Transport(body=reply)).read(_request(), KEY).status == status


def test_openai_sends_the_key_in_the_header_only() -> None:
    transport = Transport(body=_openai_reply())
    providers.OpenAIResponses(transport=transport).read(_request(), KEY)
    url, headers, body = transport.sent[0]
    assert url == "https://api.openai.com/v1/responses" and headers["Authorization"] == f"Bearer {KEY}"
    assert KEY.encode() not in body


# ----- Claude, the stronger reference


def test_claude_pins_the_model_and_effort_with_the_schema_and_no_fallbacks() -> None:
    body = providers.AnthropicMessages().body(_request())
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": payload.SCHEMA}}
    assert "fallbacks" not in body and "thinking" not in body and "temperature" not in body
    assert body["max_tokens"] == 16000 and body["system"] == payload.INSTRUCTIONS


def test_claude_caches_the_worked_walls_and_ends_on_the_target() -> None:
    req = _request()
    messages = providers.AnthropicMessages().body(req)["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    image = messages[-1]["content"][0]
    assert image["source"]["media_type"] == "image/png"
    assert base64.b64decode(image["source"]["data"]) == req.target.image.png


def test_claude_reads_an_answer_and_prices_it_from_the_usage() -> None:
    reading = providers.AnthropicMessages(transport=Transport(body=_anthropic_reply())).read(_request(), KEY)
    assert reading.status == "answered" and reading.answer == ANSWER
    assert reading.cost_usd == pytest.approx((5000 * 4.00 + 20000 * 0.20 + 4000 * 20.00) / 1e6)


@pytest.mark.parametrize(("reply", "status"), [
    (_anthropic_reply(stop_reason="refusal"), "refused"),
    (_anthropic_reply(stop_reason="max_tokens"), "truncated"),
    (_anthropic_reply(content=[{"type": "text", "text": "not json"}]), "schema_failure"),
    (_anthropic_reply(model="claude-opus-5"), "error"),
])
def test_claude_never_takes_a_refusal_a_cut_off_a_bad_json_or_another_model(reply: dict, status: str) -> None:
    assert providers.AnthropicMessages(transport=Transport(body=reply)).read(_request(), KEY).status == status


def test_claude_sends_the_key_in_the_header_only() -> None:
    transport = Transport(body=_anthropic_reply())
    providers.AnthropicMessages(transport=transport).read(_request(), KEY)
    url, headers, body = transport.sent[0]
    assert url == "https://api.anthropic.com/v1/messages" and headers["x-api-key"] == KEY
    assert "anthropic-beta" not in headers and KEY.encode() not in body


# ----- both


@pytest.mark.parametrize("make", [providers.OpenAIResponses, providers.AnthropicMessages])
def test_the_worst_case_covers_every_output_token_and_the_full_input(make: type) -> None:
    provider = make()
    worst = provider.worst_case_usd(_request())
    assert worst >= provider.price.output_per_m * provider.max_output_tokens / 1e6
    reply = _openai_reply() if make is providers.OpenAIResponses else _anthropic_reply()
    assert make(transport=Transport(body=reply)).read(_request(), KEY).cost_usd < worst


@pytest.mark.parametrize("make", [providers.OpenAIResponses, providers.AnthropicMessages])
def test_a_request_that_fails_containment_never_reaches_the_transport(make: type) -> None:
    req = _request()
    bad = payload.WallRequest(req.wall_id, req.instructions + f" {s.CLIENT}", req.examples, req.target,
                              req.schema, req.deny)
    transport = Transport(body={})
    with pytest.raises(payload.ContainmentError):
        make(transport=transport).read(bad, KEY)
    assert transport.sent == []


@pytest.mark.parametrize("make", [providers.OpenAIResponses, providers.AnthropicMessages])
@pytest.mark.parametrize(("status", "words"), [(401, "account"), (403, "account"), (429, "unavailable"),
                                                (529, "unavailable"), (500, "unavailable")])
def test_http_failures_are_plain_words_with_no_retry(make: type, status: int, words: str) -> None:
    transport = Transport(status=status, body={"error": {"message": f"bad key {KEY}"}})
    reading = make(transport=transport).read(_request(), KEY)
    assert reading.status == "error" and words in reading.detail and len(transport.sent) == 1
    assert KEY not in reading.detail


@pytest.mark.parametrize("make", [providers.OpenAIResponses, providers.AnthropicMessages])
def test_a_transport_failure_never_carries_the_key_out(make: type) -> None:
    transport = Transport(raises=RuntimeError(f"connection reset, headers had Bearer {KEY}"))
    reading = make(transport=transport).read(_request(), KEY)
    assert reading.status == "error" and KEY not in reading.detail and KEY not in repr(reading)


@pytest.mark.parametrize("make", [providers.OpenAIResponses, providers.AnthropicMessages])
def test_a_provider_holds_no_key(make: type) -> None:
    provider = make()
    assert KEY not in repr(provider) and not any(KEY in str(v) for v in vars(provider).values())


def test_the_dry_run_settings_name_the_pinned_model_and_its_price() -> None:
    assert "gpt-6-luna" in providers.OpenAIResponses().settings()
    assert "claude-opus-5-5" in providers.AnthropicMessages().settings()


# ----- keys, from Windows Credential Manager


class FakeCred:
    CRED_TYPE_GENERIC = 1

    class Missing(Exception):
        winerror = 1168

    def __init__(self, values: dict[str, str]) -> None:
        self.values, self.asked = values, []

    def CredRead(self, target: str, kind: int, flags: int) -> dict[str, Any]:  # noqa: N802 (win32cred's name)
        self.asked.append(target)
        if target not in self.values:
            raise self.Missing("Element not found.")
        return {"CredentialBlob": self.values[target].encode("utf-16-le")}


def test_keys_are_read_under_their_ngrunner_names() -> None:
    api = FakeCred({"NGRunner/openai_key": KEY})
    assert keys.read_key("openai_key", api=api) == KEY
    assert keys.read_key("anthropic_key", api=api) is None
    assert api.asked == ["NGRunner/openai_key", "NGRunner/anthropic_key"]


def test_only_the_two_reader_keys_can_be_read() -> None:
    with pytest.raises(keys.KeyProblem) as caught:
        keys.read_key("export_key", api=FakeCred({}))
    assert "export_key" not in str(caught.value) or "only" in str(caught.value)


def test_a_key_that_cannot_be_read_names_the_secret_never_the_value() -> None:
    class Broken(FakeCred):
        def CredRead(self, target: str, kind: int, flags: int) -> dict[str, Any]:  # noqa: N802
            return {"CredentialBlob": b"\x00"}
    with pytest.raises(keys.KeyProblem) as caught:
        keys.read_key("openai_key", api=Broken({}))
    assert "openai_key" in str(caught.value)


def test_each_provider_names_its_key() -> None:
    assert providers.OpenAIResponses().key_name == "openai_key"
    assert providers.AnthropicMessages().key_name == "anthropic_key"


def test_the_openrouter_key_is_read_and_a_trailing_nul_dropped() -> None:
    api = FakeCred({"NGRunner/openrouter_key": KEY + "\0"})
    assert keys.read_key("openrouter_key", api=api) == KEY
    assert providers.OpenRouterLuna().key_name == "openrouter_key"
