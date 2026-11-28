"""The bake-off's timed requests, sent and read at the wire, on the client's clock.

The adapter holds reasoning back until its part closes, so its events
cannot time the first token. Here the server-sent events are read as they
come, and the first and last are those that carry any delta, reasoning
included. A time starts just before the request is sent: on 127.0.0.1 the
body is written in well under a millisecond.
"""

import json
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from lamplighter.adapters.sse import SseReader
from lamplighter.adapters.wire_json import (
    Unreadable,
    as_object,
    list_or_empty,
    object_or_empty,
    parse_object,
    text_in,
    whole_in,
)
from lamplighter.bench import FirstToken, Speeds
from lamplighter.core import problems
from lamplighter.ports import Clock, Failure, JsonObject, JsonValue, Verdict

_PATH = "/v1/chat/completions"
_DELTAS = ("content", "reasoning_content", "reasoning", "tool_calls")
# A made-up paragraph. Each copy is numbered, so no two lines are the same.
_PARAGRAPH = (
    "Line {n}: the lamp on the made-up street is lit at dusk and put out at"
    " dawn, and the keeper notes the hour, the weather and the oil left."
)
_WARM_TOKENS = 8_000
_SPEED_SIZES = {"8k": 8_000, "32k": 32_000}
_TIMES = 5
_SPEED_OUTPUT = 512
_SCHEMA: JsonObject = {
    "type": "object",
    "properties": {"street": {"type": "string"}, "lamps": {"type": "integer"}},
    "required": ["street", "lamps"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class Timed:
    """One streamed request at the wire.

    The two times are from the start to the first and to the last event
    that carried a delta. `timings` is what `llama-server` says of its own
    speed, or empty.
    """

    first_ms: float
    last_ms: float
    prompt_tokens: int
    completion_tokens: int
    timings: JsonObject


class Wire:
    """Sends the bake-off's own requests to one server.

    The client it is given reads no proxy setting, so every byte goes to
    the server on this Mac.
    """

    def __init__(
        self, http: httpx.AsyncClient, base_url: str, model: str, clock: Clock
    ) -> None:
        """Keep the client, where the server is, the model it serves and the clock."""
        self._http = http
        self._url = base_url.rstrip("/") + _PATH
        self._model = model
        self._clock = clock

    async def first_token(self) -> FirstToken | Failure:
        """Fill the cache with one request, then time five with another last message."""
        text = await self.sized_text(_WARM_TOKENS)
        if isinstance(text, Failure):
            return text
        times: list[float] = []
        for n in range(_TIMES + 1):
            asked = await self.streamed(_warm(text, n), 16)
            if isinstance(asked, Failure):
                return asked
            if n:
                times.append(asked.first_ms)
        each = tuple(round(t) for t in times)
        return FirstToken(median_ms=statistics.median_low(each), each_ms=each)

    async def speeds(self) -> Speeds | Failure:
        """Time five requests at each size, each with its own first line.

        No two share a first line, so none is answered from the cache.
        """
        found: dict[str, tuple[int, int]] = {}
        timings: dict[str, JsonValue] = {}
        for label, size in _SPEED_SIZES.items():
            text = await self.sized_text(size)
            if isinstance(text, Failure):
                return text
            prompt: list[int] = []
            generation: list[int] = []
            kept: list[JsonValue] = []
            for n in range(_TIMES):
                asked = await self.streamed(_speed(text, n), _SPEED_OUTPUT)
                if isinstance(asked, Failure):
                    return asked
                prompt.append(_per_second(asked.prompt_tokens, asked.first_ms))
                generating = asked.last_ms - asked.first_ms
                generation.append(_per_second(asked.completion_tokens, generating))
                kept.append(asked.timings)
            found[label] = (
                statistics.median_low(prompt),
                statistics.median_low(generation),
            )
            timings[label] = kept
        return Speeds(
            prompt_8k_milli=found["8k"][0],
            generation_8k_milli=found["8k"][1],
            prompt_32k_milli=found["32k"][0],
            generation_32k_milli=found["32k"][1],
            server_timings=timings,
        )

    async def forced_schema(self) -> Verdict | Failure:
        """Ask once for a reply that must fit a JSON schema, with no tools."""
        body: dict[str, JsonValue] = {
            "model": self._model,
            "messages": [_user("Describe one made-up street with lamps, as JSON.")],
            "max_tokens": 2_048,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "street", "schema": _SCHEMA, "strict": True},
            },
        }
        answer = await self.posted(body)
        if isinstance(answer, Failure):
            return answer
        content = _content(answer)
        try:
            parsed = parse_object(content)
        except Unreadable as error:
            return Verdict(
                "forced_schema", False, f"The reply is not an object: {error}"
            )
        found = problems(_SCHEMA, parsed)
        if found:
            return Verdict("forced_schema", False, f"The reply breaks it: {found[0]}")
        return Verdict("forced_schema", True, f"The reply fits: {content[:200]}")

    async def sized_text(self, tokens: int) -> str | Failure:
        """Give a made-up text whose request the server counts at about `tokens`.

        The size of one copy of the paragraph is measured from two requests
        the server counts, so it works on a server with no count endpoint.
        """
        none = await self.prompt_tokens(_text(0))
        some = await self.prompt_tokens(_text(100))
        if isinstance(none, Failure):
            return none
        if isinstance(some, Failure):
            return some
        each = (some - none) / 100
        if each <= 0:
            return _failed("The server counted a longer text as no longer.")
        return _text(max(0, int((tokens - none) / each)))

    async def prompt_tokens(self, text: str) -> int | Failure:
        """Give the server's own count of a request's prompt. It asks for one token."""
        body: dict[str, JsonValue] = {
            "model": self._model,
            "messages": [_user(text)],
            "max_tokens": 1,
        }
        answer = await self.posted(body)
        if isinstance(answer, Failure):
            return answer
        try:
            return whole_in(object_or_empty(answer.get("usage")), "prompt_tokens")
        except Unreadable:
            return _failed("The server gave no prompt_tokens.")

    async def posted(self, body: dict[str, JsonValue]) -> dict[str, Any] | Failure:
        """Send one request that is not streamed, and give its answer."""
        try:
            response = await self._http.post(self._url, content=json.dumps(body))
        except httpx.HTTPError as error:
            return _failed(f"The request did not complete: {error!r}.")
        if response.status_code != 200:
            said = response.text[:300]
            return _failed(f"The server answered {response.status_code}: {said}")
        try:
            return parse_object(response.text)
        except Unreadable as error:
            return _failed(f"The server's answer is not a JSON object: {error}.")

    async def streamed(
        self, messages: Sequence[JsonValue], max_tokens: int
    ) -> Timed | Failure:
        """Send one streamed request, and time its first and last delta."""
        body: dict[str, JsonValue] = {
            "model": self._model,
            "messages": list(messages),
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        reader = SseReader()
        seen = _Seen()
        start = self._clock.monotonic()
        try:
            async with self._http.stream(
                "POST", self._url, content=json.dumps(body)
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    return _failed(f"The server answered {response.status_code}.")
                async for piece in response.aiter_bytes():
                    for event in reader.feed(piece):
                        seen.take(event.data, self._clock.monotonic())
        except httpx.HTTPError as error:
            return _failed(f"The request did not complete: {error!r}.")
        return seen.timed(start)


class _Seen:
    # What a stream has shown so far: the times of its first and last delta,
    # and the last counts and timings it gave.

    def __init__(self) -> None:
        self.first: float | None = None
        self.last: float | None = None
        self.usage: dict[str, Any] = {}
        self.timings: dict[str, Any] = {}

    def take(self, text: str, now: float) -> None:
        try:
            data = parse_object(text)
            delta = _has_delta(data)
        except Unreadable:
            # "[DONE]", or an event of another shape, which holds no delta.
            return
        if delta:
            self.last = now
            self.first = now if self.first is None else self.first
        if isinstance(data.get("usage"), dict):
            self.usage = data["usage"]
        if isinstance(data.get("timings"), dict):
            self.timings = data["timings"]

    def timed(self, start: float) -> Timed | Failure:
        prompt = self.usage.get("prompt_tokens")
        completion = self.usage.get("completion_tokens")
        if self.first is None or self.last is None:
            return _failed("The stream carried no delta.")
        if type(prompt) is not int or type(completion) is not int:
            return _failed("The stream gave no token counts.")
        return Timed(
            first_ms=(self.first - start) * 1000,
            last_ms=(self.last - start) * 1000,
            prompt_tokens=prompt,
            completion_tokens=completion,
            timings=self.timings,
        )


def _per_second(tokens: int, ms: float) -> int:
    # In thousandths of a token a second.
    return round(tokens * 1_000_000 / ms) if ms > 0 else 0


def _has_delta(data: dict[str, Any]) -> bool:
    choices = list_or_empty(data.get("choices"))
    if not choices:
        return False
    delta = object_or_empty(as_object(choices[0]).get("delta"))
    return any(delta.get(key) for key in _DELTAS)


def _content(answer: dict[str, Any]) -> str:
    # A reply with any other shape has no content to judge.
    try:
        choices = list_or_empty(answer.get("choices"))
        message = object_or_empty(as_object(choices[0]).get("message"))
        return text_in(message, "content")
    except (Unreadable, IndexError):
        return ""


def _text(copies: int) -> str:
    return "\n".join(_PARAGRAPH.format(n=n) for n in range(1, copies + 1))


def _user(text: str) -> JsonValue:
    return {"role": "user", "content": text}


def _warm(text: str, n: int) -> list[JsonValue]:
    return [
        _user(f"Read these notes.\n{text}"),
        {"role": "assistant", "content": "I have read them."},
        _user(f"In one word: what is noted on line {n + 1}?"),
    ]


def _speed(text: str, n: int) -> list[JsonValue]:
    asked = f"Request {n + 1} of {_TIMES}. Read these notes.\n{text}\n"
    return [_user(asked + "Now write a long story about the lamp keeper.")]


def _failed(message: str) -> Failure:
    return Failure(
        kind="unavailable", effect="none", message=message, source="bakeoff_wire"
    )
