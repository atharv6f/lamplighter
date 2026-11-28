"""The bake-off's timed requests, against a made-up server and a clock that ticks."""

import asyncio
import json
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

import httpx

from lamplighter.app.bakeoff_wire import Timed, Wire
from lamplighter.bench import FirstToken
from lamplighter.ports import Failure, Verdict

BASE_URL = "http://127.0.0.1:9931/"
TICK = 0.25


class TickingClock:
    """A clock whose monotonic time moves on a quarter of a second at each read."""

    def __init__(self) -> None:
        self.reads = 0

    def monotonic(self) -> float:
        self.reads += 1
        return self.reads * TICK

    def now(self) -> datetime:
        return datetime(2026, 11, 28, tzinfo=UTC)

    async def sleep(self, seconds: float) -> None:
        pass


def sse(*events: object) -> bytes:
    lines = [f"data: {json.dumps(event)}\n\n" for event in events]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


def delta(**fields: str) -> dict[str, object]:
    return {"choices": [{"index": 0, "delta": fields}]}


USAGE: dict[str, object] = {
    "usage": {"prompt_tokens": 8_000, "completion_tokens": 40},
    "choices": [],
}


type Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


def wire(handler: Handler, clock: TickingClock | None = None) -> Wire:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Wire(http, BASE_URL, "made-up-local", clock or TickingClock())


def body_of(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


def streaming(*events: object) -> Handler:
    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse(*events))

    return handle


def test_the_times_are_of_the_first_and_last_event_with_a_delta() -> None:
    handler = streaming(
        {"choices": [{"index": 0, "delta": {"role": "assistant"}}]},
        delta(reasoning_content="Thinking."),
        delta(content="The lamp"),
        delta(content=" is lit."),
        {**USAGE, "timings": {"predicted_per_second": 31.5}},
    )
    found = asyncio.run(
        wire(handler).streamed([{"role": "user", "content": "Hi."}], 64)
    )
    # Read 1 starts the clock, and each event reads it once: the role event
    # at read 2 has no delta, and the three deltas are reads 3 to 5.
    assert found == Timed(
        first_ms=2 * TICK * 1000,
        last_ms=4 * TICK * 1000,
        prompt_tokens=8_000,
        completion_tokens=40,
        timings={"predicted_per_second": 31.5},
    )


def test_a_tool_call_delta_counts_as_a_delta() -> None:
    call = {"tool_calls": [{"index": 0, "function": {"name": "wait"}}]}
    handler = streaming({"choices": [{"index": 0, "delta": call}]}, USAGE)
    found = asyncio.run(wire(handler).streamed([], 64))
    assert isinstance(found, Timed)
    assert found.first_ms == found.last_ms == TICK * 1000


def test_the_streamed_body_asks_for_counts_and_names_the_model() -> None:
    seen: list[dict[str, Any]] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        seen.append(body_of(request))
        assert request.url == "http://127.0.0.1:9931/v1/chat/completions"
        return httpx.Response(200, content=sse(delta(content="x"), USAGE))

    asyncio.run(wire(handle).streamed([{"role": "user", "content": "Hi."}], 512))
    assert seen == [
        {
            "model": "made-up-local",
            "messages": [{"role": "user", "content": "Hi."}],
            "max_tokens": 512,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
    ]


def test_a_stream_with_no_delta_or_no_counts_is_a_failure() -> None:
    no_delta = asyncio.run(wire(streaming(USAGE)).streamed([], 64))
    assert isinstance(no_delta, Failure)
    assert no_delta.message == "The stream carried no delta."
    no_counts = asyncio.run(wire(streaming(delta(content="x"))).streamed([], 64))
    assert isinstance(no_counts, Failure)
    assert no_counts.message == "The stream gave no token counts."


def test_a_refused_or_lost_request_is_a_failure() -> None:
    async def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, content=b'{"error": "too long"}')

    async def lose(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("made up", request=request)

    for handler, said in ((refuse, "answered 400"), (lose, "did not complete")):
        found = asyncio.run(wire(handler).streamed([], 64))
        assert isinstance(found, Failure)
        assert found.kind == "unavailable"
        assert said in found.message
        posted = asyncio.run(wire(handler).prompt_tokens("Hi."))
        assert isinstance(posted, Failure)


def counting(per_line: int = 20, base: int = 30) -> Handler:
    """A server that counts each made-up line at `per_line` tokens."""

    async def handle(request: httpx.Request) -> httpx.Response:
        body = body_of(request)
        text = "".join(m["content"] for m in body["messages"])
        tokens = base + per_line * text.count("Line ")
        if body.get("stream"):
            usage = {"usage": {"prompt_tokens": tokens, "completion_tokens": 512}}
            deltas = (delta(content="The lamp"), delta(content=" is lit."))
            return httpx.Response(200, content=sse(*deltas, usage))
        usage = {"prompt_tokens": tokens, "completion_tokens": 1}
        return httpx.Response(200, content=json.dumps({"usage": usage}).encode())

    return handle


def test_a_text_is_sized_by_the_server_s_own_count() -> None:
    made = wire(counting())
    text = asyncio.run(made.sized_text(8_000))
    assert isinstance(text, str)
    assert text.count("Line ") == (8_000 - 30) // 20
    assert asyncio.run(made.prompt_tokens(text)) == 7_990
    assert len(set(text.splitlines())) == text.count("Line ")


def test_a_server_that_counts_nothing_more_for_more_text_is_a_failure() -> None:
    found = asyncio.run(wire(counting(per_line=0)).sized_text(8_000))
    assert isinstance(found, Failure)


def test_the_first_token_is_the_median_of_five_after_one_to_fill_the_cache() -> None:
    asked: list[list[dict[str, str]]] = []
    count = counting()

    async def handle(request: httpx.Request) -> httpx.Response:
        body = body_of(request)
        if body.get("stream"):
            asked.append(body["messages"])
        return await count(request)

    found = asyncio.run(wire(handle).first_token())
    assert isinstance(found, FirstToken)
    # Each stream reads the clock at its start, then once for each event.
    assert found == FirstToken(median_ms=250, each_ms=(250,) * 5)
    assert len(asked) == 6
    assert len({json.dumps(m[:2]) for m in asked}) == 1
    assert len({m[2]["content"] for m in asked}) == 6


def test_speeds_are_medians_in_thousandths_of_a_token_a_second() -> None:
    asked: list[str] = []
    count = counting()

    async def handle(request: httpx.Request) -> httpx.Response:
        body = body_of(request)
        if body.get("stream"):
            asked.append(body["messages"][0]["content"].splitlines()[0])
            assert body["max_tokens"] == 512
        return await count(request)

    found = asyncio.run(wire(handle).speeds())
    assert not isinstance(found, Failure), found
    # The prompt took one tick, 250 ms, and so did the 512 tokens after it.
    assert found.prompt_8k_milli == round(7_990 * 1_000_000 / 250)
    assert found.prompt_32k_milli == round(31_990 * 1_000_000 / 250)
    assert found.generation_8k_milli == found.generation_32k_milli == 2_048_000
    assert len(asked) == 10 and len(set(asked)) == 5


def answering_json(content: str) -> Handler:
    async def handle(request: httpx.Request) -> httpx.Response:
        body = body_of(request)
        assert "tools" not in body
        assert body["response_format"]["type"] == "json_schema"
        message = {"role": "assistant", "content": content}
        answer = {"choices": [{"index": 0, "message": message}]}
        return httpx.Response(200, content=json.dumps(answer).encode())

    return handle


def test_a_forced_schema_passes_only_when_the_reply_fits() -> None:
    def verdict(content: str) -> Verdict | Failure:
        return asyncio.run(wire(answering_json(content)).forced_schema())

    fits = verdict('{"street": "Made-up Lane", "lamps": 12}')
    assert isinstance(fits, Verdict) and fits.passed
    for broken in ('{"street": "Lane"}', '{"street": "Lane", "lamps": "12"}', "Lamps."):
        found = verdict(broken)
        assert isinstance(found, Verdict) and not found.passed, broken
