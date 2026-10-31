"""
The models the reader evaluation compares, behind one interface (decisions 16 and 17).

Why these two: Sulaiman's first choice is OpenAI GPT-6 Luna, called direct (one processor fewer
than OpenRouter), and it must pass against one stronger reference on the same walls; the reference
is one Claude model. OpenRouter is not built: it is optional, and adding it is one more class here.

Both are pinned and plain: one model by its exact id (a reply naming another model is an error,
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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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
        try:
            status, raw = self._transport(self.url, self._headers(key), data, TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - only the type is kept: a message may carry headers
            return Reading("error", None, 0.0, f"the call failed ({type(exc).__name__}); not retried")
        if status != 200:
            return Reading("error", None, 0.0, _http_failure(status))
        try:
            reply = json.loads(raw)
        except ValueError:
            return Reading("error", None, 0.0, "the reply was not JSON")
        if not isinstance(reply, Mapping):
            return Reading("error", None, 0.0, "the reply was not a JSON object")
        return self._parse(reply)


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


PROVIDERS: dict[str, type[_Base]] = {"openai": OpenAIResponses, "anthropic": AnthropicMessages}
