"""
The models the reader evaluation compares, behind one interface (decisions 16 and 17).

Why these: Sulaiman's first choice is OpenAI GPT-6 Luna, and it must pass against one stronger
reference on the same walls, one Claude model. Luna runs through OpenRouter (Sulaiman, 2026-10-31),
and OpenRouter is used for Luna only: OpenRouterLuna refuses any other model id. Claude runs direct
through Anthropic's API, never through OpenRouter. OpenAIResponses (Luna direct) is kept for when an
OpenAI key exists.

All are pinned and plain: one model by its exact id (a reply naming another model is an error,
never a silent swap), structured output against payload.SCHEMA, effort set explicitly, no
fallbacks, no tools, no sampling parameters, nothing stored by the provider where it can be turned
off (OpenAI's store=false). A refusal, a cut-off answer or JSON that doesn't parse declines the
wall, and an HTTP failure is plain words with no retry (design 3.6: an account problem is not
retried; an outage waits for a later run).

The facts each class rests on, checked 2026-10-31:
  GPT-6 Luna   developers.openai.com/api/docs/models/gpt-6-luna and /pricing: id gpt-6-luna, its
               only snapshot (no dated one); $0.10 per million input tokens, $0.01 cached, $0.50
               output; text and image input; structured outputs; reasoning effort none to max,
               default medium; Responses API. Image tokens: the vision guide gives 32 px patches
               times 1.2 for GPT-6 Astra and GPT-5.6 Luna but does not name GPT-6 Luna, so the
               worst case here counts 2.0 (ASSUMPTION; the bill uses the usage the reply reports).
               detail "original" keeps the crop's own pixels.
  Claude       the claude-api skill (its model table cached 2026-10-06) and the vision docs:
               claude-opus-5-5, $4 per million input, $20 output, $0.20 cache read, cache writes
               1.25 times input; thinking is always on (effort is the only control, default medium,
               so it is set); images cost ceil(w/28) x ceil(h/28) tokens up to 4,784 (prep keeps
               every crop within it). Raw HTTP through the pinned requests, so the evaluation adds no
               package.

A key reaches a provider only as read()'s argument, goes only into a request header, and is never
put in a Reading, an error or a log: a transport failure is reported by its type alone.
"""
from __future__ import annotations

import base64
import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from tools.reader_eval import payload
from tools.reader_eval.prep import visual_tokens
from tools.reader_eval.scoring import Reading

Transport = Callable[[str, Mapping[str, str], bytes, float], tuple[int, bytes]]
TIMEOUT_S = 300.0


@dataclass(frozen=True)
class Price:
    input_per_m: float
    cached_input_per_m: float
    cache_write_per_m: float
    output_per_m: float
    checked: str
    source: str


def _requests_transport(url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
    import requests
    response = requests.post(url, headers=dict(headers), data=body, timeout=timeout)
    return response.status_code, response.content


def _http_failure(status: int) -> str:
    if status in (401, 402, 403):
        return f"account problem (HTTP {status}): check the key and the account's spend limit; not retried"
    if status == 429 or status >= 500:
        return f"provider unavailable (HTTP {status}); not retried in this run"
    return f"the provider refused the request (HTTP {status})"


class _Base:
    name = ""
    model = ""
    key_name = ""
    url = ""
    price: Price
    max_output_tokens = 16000

    def __init__(self, *, effort: str = "high", transport: Transport | None = None) -> None:
        self.effort = effort
        self._transport = transport or _requests_transport

    def __repr__(self) -> str:
        return f"{type(self).__name__}(model={self.model!r}, effort={self.effort!r})"

    def settings(self) -> str:
        p = self.price
        return (f"provider {self.name}, model {self.model} (pinned, no fallbacks), effort {self.effort}, "
                f"max output {self.max_output_tokens} tokens; price per million tokens: input ${p.input_per_m}, "
                f"cached ${p.cached_input_per_m}, output ${p.output_per_m} (checked {p.checked}, {p.source})")

    # --- what subclasses provide
    def body(self, req: payload.WallRequest) -> dict[str, Any]:
        raise NotImplementedError

    def _headers(self, key: str) -> dict[str, str]:
        raise NotImplementedError

    def _image_tokens(self, image: payload.Image) -> float:
        raise NotImplementedError

    def _parse(self, reply: Mapping[str, Any]) -> Reading:
        raise NotImplementedError

    # --- shared
    def worst_case_usd(self, req: payload.WallRequest) -> float:
        """The most this request can cost: every input token at the dearest input rate, one token per
        byte of text, and every output token max_output_tokens allows."""
        images = [req.target.image, *(e.turn.image for e in req.examples)]
        text = sum(len(t.encode("utf-8")) for t in [req.instructions, json.dumps(req.schema), req.target.text,
                                                     *(e.turn.text for e in req.examples),
                                                     *(e.answer for e in req.examples)])
        tokens_in = text + sum(self._image_tokens(i) for i in images) + 200 * len(images)
        dearest = max(self.price.input_per_m, self.price.cache_write_per_m)
        return (tokens_in * dearest + self.max_output_tokens * self.price.output_per_m) / 1e6

    def read(self, req: payload.WallRequest, key: str) -> Reading:
        """Send the request (containment is checked again first) and read the reply."""
        payload.assert_contained(req)
        data = json.dumps(self.body(req)).encode("utf-8")
        started = time.monotonic()
        try:
            status, raw = self._transport(self.url, self._headers(key), data, TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - only the type is kept: a message may carry headers
            return Reading("error", None, 0.0, f"the call failed ({type(exc).__name__})",
                           latency_s=time.monotonic() - started, network=True, cost_known=False)
        latency = time.monotonic() - started
        if status != 200:
            return Reading("error", None, 0.0, _http_failure(status), latency_s=latency, cost_known=False)
        try:
            reply = json.loads(raw)
        except ValueError:
            return Reading("error", None, 0.0, "the reply was not JSON", latency_s=latency, cost_known=False)
        if not isinstance(reply, Mapping):
            return Reading("error", None, 0.0, "the reply was not a JSON object", latency_s=latency,
                           cost_known=False)
        return replace(self._parse(reply), latency_s=latency)


def _answer(text: str, cost: float) -> Reading:
    try:
        return Reading("answered", json.loads(text), cost)
    except ValueError:
        return Reading("schema_failure", None, cost, "the answer was not JSON")


def _data_url(image: payload.Image) -> str:
    return "data:image/png;base64," + base64.b64encode(image.png).decode("ascii")


class OpenAIResponses(_Base):
    name = "openai"
    model = "gpt-6-luna"
    key_name = "openai_key"
    url = "https://api.openai.com/v1/responses"
    price = Price(0.10, 0.01, 0.10, 0.50, "2026-10-31",
                  "developers.openai.com/api/docs/models/gpt-6-luna and /pricing")
    image_multiplier = 2.0  # ASSUMPTION: an upper bound; the docs give 1.2 for its neighbours

    def _headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _image_tokens(self, image: payload.Image) -> float:
        w, h = image.pixels
        return math.ceil(w / 32) * math.ceil(h / 32) * self.image_multiplier

    def _user(self, turn: payload.Turn) -> dict[str, Any]:
        return {"role": "user", "content": [
            {"type": "input_image", "image_url": _data_url(turn.image), "detail": "original"},
            {"type": "input_text", "text": turn.text}]}

    def body(self, req: payload.WallRequest) -> dict[str, Any]:
        turns: list[dict[str, Any]] = [{"role": "developer", "content": [{"type": "input_text",
                                                                          "text": req.instructions}]}]
        for e in req.examples:
            turns += [self._user(e.turn), {"role": "assistant", "content": [{"type": "output_text", "text": e.answer}]}]
        turns.append(self._user(req.target))
        return {"model": self.model, "input": turns, "reasoning": {"effort": self.effort},
                "text": {"format": {"type": "json_schema", "name": "wall_reading", "strict": True,
                                    "schema": req.schema}},
                "max_output_tokens": self.max_output_tokens, "store": False}

    def _cost(self, usage: Mapping[str, Any]) -> float:
        total = float(usage.get("input_tokens") or 0)
        cached = float((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0)
        out = float(usage.get("output_tokens") or 0)
        p = self.price
        return ((total - cached) * p.input_per_m + cached * p.cached_input_per_m + out * p.output_per_m) / 1e6

    def _parse(self, reply: Mapping[str, Any]) -> Reading:
        cost = self._cost(reply.get("usage") or {})
        if not str(reply.get("model", "")).startswith(self.model):
            return Reading("error", None, cost, "the reply came from another model than the pinned one")
        if reply.get("status") == "incomplete":
            return Reading("truncated", None, cost, "the answer was cut off")
        if reply.get("status") != "completed":
            return Reading("error", None, cost, "the reply did not complete")
        parts = [c for item in reply.get("output") or [] if item.get("type") == "message"
                 for c in item.get("content") or []]
        if any(c.get("type") == "refusal" for c in parts):
            return Reading("refused", None, cost, "the model refused")
        text = "".join(c.get("text", "") for c in parts if c.get("type") == "output_text")
        return _answer(text, cost)


class AnthropicMessages(_Base):
    name = "anthropic"
    model = "claude-opus-5-5"
    key_name = "anthropic_key"
    url = "https://api.anthropic.com/v1/messages"
    version = "2026-10-21"
    price = Price(4.00, 0.20, 5.00, 20.00, "2026-10-31",
                  "the claude-api skill's model table and platform.claude.com vision docs")

    def _headers(self, key: str) -> dict[str, str]:
        return {"x-api-key": key, "anthropic-version": self.version, "content-type": "application/json"}

    def _image_tokens(self, image: payload.Image) -> float:
        return visual_tokens(*image.pixels)

    def _user(self, turn: payload.Turn) -> dict[str, Any]:
        return {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(turn.image.png).decode("ascii")}},
            {"type": "text", "text": turn.text}]}

    def body(self, req: payload.WallRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        for k, e in enumerate(req.examples):
            answer: dict[str, Any] = {"type": "text", "text": e.answer}
            if k == len(req.examples) - 1:
                answer["cache_control"] = {"type": "ephemeral"}  # the worked walls repeat for every wall
            messages += [self._user(e.turn), {"role": "assistant", "content": [answer]}]
        messages.append(self._user(req.target))
        return {"model": self.model, "max_tokens": self.max_output_tokens, "system": req.instructions,
                "messages": messages,
                "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": req.schema}}}

    def _cost(self, usage: Mapping[str, Any]) -> float:
        p = self.price
        return (float(usage.get("input_tokens") or 0) * p.input_per_m
                + float(usage.get("cache_creation_input_tokens") or 0) * p.cache_write_per_m
                + float(usage.get("cache_read_input_tokens") or 0) * p.cached_input_per_m
                + float(usage.get("output_tokens") or 0) * p.output_per_m) / 1e6

    def _parse(self, reply: Mapping[str, Any]) -> Reading:
        cost = self._cost(reply.get("usage") or {})
        if reply.get("model") != self.model:
            return Reading("error", None, cost, "the reply came from another model than the pinned one")
        stop = reply.get("stop_reason")
        if stop == "refusal":
            return Reading("refused", None, cost, "the model refused")
        if stop == "max_tokens":
            return Reading("truncated", None, cost, "the answer was cut off")
        if stop != "end_turn":
            return Reading("error", None, cost, f"the reply stopped for another reason ({stop})")
        text = "".join(b.get("text", "") for b in reply.get("content") or [] if b.get("type") == "text")
        return _answer(text, cost)


class OpenRouterLuna(_Base):
    """
    GPT-6 Luna through OpenRouter, and nothing else (Sulaiman, 2026-10-31): any other model id is
    refused when the provider is made, and again just before anything is sent.

    The host is pinned to OpenRouter's azure/us endpoint (checked 2026-10-31 at
    openrouter.ai/api/v1/models/openai/gpt-6-luna/endpoints and /api/v1/endpoints/zdr): of Luna's
    hosts only Azure's three endpoints are on OpenRouter's zero-data-retention list, none with safety
    retention, and azure/us is the US one (design decision 16: a host that neither keeps nor trains
    on prompts, US preferred). OpenAI's own endpoint is not on that list. Its price there is $0.11 per
    million input tokens and $0.55 output. The routing: that one host, no fallbacks, data collection
    denied, zero retention required, every parameter required of the host (so structured output is
    honoured, and max_completion_tokens is sent because Azure lists it, not max_tokens), and a price
    ceiling at the pinned host's, so a dearer endpoint can never serve it. A reply from another host
    or another model is an error. The cost is what OpenRouter reports in usage.cost.
    """
    name = "openrouter"
    ALLOWED = frozenset({"openai/gpt-6-luna"})
    SERVED_AS = frozenset({"openai/gpt-6-luna", "openai/gpt-6-luna-20260922"})
    key_name = "openrouter_key"
    url = "https://openrouter.ai/api/v1/chat/completions"
    host, host_name = "azure/us", "Azure"
    price = Price(0.11, 0.011, 0.1375, 0.55, "2026-10-31",
                  "openrouter.ai/api/v1/models/openai/gpt-6-luna/endpoints, endpoint azure/us")
    image_multiplier = 2.0  # ASSUMPTION, as for OpenAI direct: an upper bound for the guard only

    def __init__(self, *, model: str = "openai/gpt-6-luna", effort: str = "high",
                 transport: Transport | None = None) -> None:
        self._check(model)
        super().__init__(effort=effort, transport=transport)
        self.model = model

    @classmethod
    def _check(cls, model: str) -> None:
        if model not in cls.ALLOWED:
            raise ValueError("OpenRouter is used for openai/gpt-6-luna only")

    def settings(self) -> str:
        return super().settings() + f"; via OpenRouter, host {self.host} only, zero data retention"

    def _headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _image_tokens(self, image: payload.Image) -> float:
        w, h = image.pixels
        return math.ceil(w / 32) * math.ceil(h / 32) * self.image_multiplier

    def _user(self, turn: payload.Turn) -> dict[str, Any]:
        return {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": _data_url(turn.image), "detail": "original"}},
            {"type": "text", "text": turn.text}]}

    def body(self, req: payload.WallRequest) -> dict[str, Any]:
        self._check(self.model)
        messages: list[dict[str, Any]] = [{"role": "system", "content": req.instructions}]
        for e in req.examples:
            messages += [self._user(e.turn), {"role": "assistant", "content": e.answer}]
        messages.append(self._user(req.target))
        return {"model": self.model, "messages": messages,
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "wall_reading", "strict": True, "schema": req.schema}},
                "reasoning": {"effort": self.effort}, "max_completion_tokens": self.max_output_tokens,
                "provider": {"order": [self.host], "only": [self.host], "allow_fallbacks": False,
                             "require_parameters": True, "data_collection": "deny", "zdr": True,
                             "max_price": {"prompt": self.price.input_per_m, "completion": self.price.output_per_m}},
                "usage": {"include": True}}

    def read(self, req: payload.WallRequest, key: str) -> Reading:
        self._check(self.model)
        return super().read(req, key)

    def _cost(self, usage: Mapping[str, Any]) -> float:
        reported = usage.get("cost")
        if isinstance(reported, int | float) and math.isfinite(reported) and reported >= 0:
            return float(reported)
        cached = float((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        prompt = float(usage.get("prompt_tokens") or 0)
        p = self.price
        return ((prompt - cached) * p.input_per_m + cached * p.cached_input_per_m
                + float(usage.get("completion_tokens") or 0) * p.output_per_m) / 1e6

    def _parse(self, reply: Mapping[str, Any]) -> Reading:
        usage = reply.get("usage") or {}
        cost = self._cost(usage)
        known = bool(usage)
        if reply.get("error"):
            return Reading("error", None, cost, "OpenRouter returned an error in the reply", cost_known=known)
        if reply.get("model") not in self.SERVED_AS:
            return Reading("error", None, cost, "the reply came from another model than the pinned one",
                           cost_known=known)
        if reply.get("provider") != self.host_name:
            return Reading("error", None, cost, "the reply came from another host than the pinned one",
                           cost_known=known)
        choices = reply.get("choices") or []
        if not choices:
            return Reading("error", None, cost, "the reply held no answer", cost_known=known)
        choice = choices[0]
        message = choice.get("message") or {}
        finish = choice.get("finish_reason")
        if message.get("refusal") or finish == "content_filter":
            return Reading("refused", None, cost, "the model refused", cost_known=known)
        if finish == "length":
            return Reading("truncated", None, cost, "the answer was cut off", cost_known=known)
        if finish != "stop":
            return Reading("error", None, cost, f"the reply stopped for another reason ({finish})", cost_known=known)
        return replace(_answer(message.get("content") or "", cost), cost_known=known)


PROVIDERS: dict[str, type[_Base]] = {"openai": OpenAIResponses, "anthropic": AnthropicMessages,
                                     "openrouter": OpenRouterLuna}
