import asyncio
import dataclasses
from collections.abc import AsyncIterator

import httpx
import pytest

from lamplighter.adapters import ScriptedReply
from lamplighter.adapters.anthropic.request import (
    render_count_request,
    render_request,
)
from lamplighter.core import fold_reply
from lamplighter.ports import (
    CacheHint,
    ErrorEvent,
    Failure,
    Message,
    ModelEvent,
    ModelRequest,
    StopEvent,
    TextEvent,
    TextPart,
    Usage,
    UsageEvent,
)
from tests.anthropic.fake_server import (
    KEY,
    PROFILE,
    Body,
    FakeServer,
    client_for,
    counted,
    refusal,
    reply,
)
from tests.anthropic.wire_text import (
    MODEL,
    error,
    message_end,
    message_start,
    part_events,
    stream_body,
)
from tests.fake_clock import FakeClock
from tests.streams import collect, count

USAGE = Usage(input_uncached=120, cache_read=0, cache_write=0, output=40)
TEXT = TextPart(text="Milk, eggs and tea.")
TEXT_REPLY = ScriptedReply(parts=(TEXT,), reason="end", usage=USAGE)
GOOD = stream_body(TEXT_REPLY)
OVERLOADED_AT_ONCE = message_start(Usage(7, 0, 0, None)) + error("overloaded_error")


def ask(retries: int = 0, deadline_ms: int = 60_000) -> ModelRequest:
    return ModelRequest(
        system=("You are a careful helper.",),
        messages=(Message(role="user", parts=(TextPart(text="What is on my list?"),)),),
        tools=(),
        max_output=500,
        cache_hints=(),
        deadline_ms=deadline_ms,
        retries=retries,
    )


def the_error(events: list[ModelEvent]) -> ErrorEvent:
    end = events[-1]
    assert isinstance(end, ErrorEvent)
    return end


async def hang() -> httpx.Response:
    await asyncio.Event().wait()
    raise AssertionError("a wait with no end has ended")


def test_a_reply_is_read_from_one_call_to_the_messages_endpoint() -> None:
    server = FakeServer(reply(GOOD))
    reply_read = fold_reply(collect(client_for(server), ask()))
    assert reply_read.message.parts == TEXT_REPLY.parts
    assert reply_read.usage == USAGE
    assert isinstance(reply_read.end, StopEvent)
    assert reply_read.end.model_reported == MODEL

    assert len(server.requests) == 1
    sent = server.requests[0]
    assert sent.method == "POST"
    assert sent.url == "https://api.anthropic.com/v1/messages"
    assert sent.headers["x-api-key"] == KEY
    assert sent.headers["anthropic-version"] == "2026-10-21"
    assert sent.headers["content-type"] == "application/json"
    assert sent.content == render_request(MODEL, ask())


def test_the_deadline_is_the_only_time_limit_on_a_call() -> None:
    # The library's own limits are per read. A model may stay silent between
    # two reads for longer than any fixed limit, so they are all switched off.
    server = FakeServer(reply(GOOD))
    collect(client_for(server), ask())
    limits = server.requests[0].extensions["timeout"]
    assert limits == {"connect": None, "read": None, "write": None, "pool": None}


def test_a_request_the_profile_cannot_serve_is_never_sent() -> None:
    server = FakeServer()
    too_much = dataclasses.replace(ask(), max_output=PROFILE.max_output + 1)
    events = collect(client_for(server), too_much)
    assert len(events) == 1
    assert the_error(events).failure.kind == "invalid_input"
    assert server.requests == []


def test_a_profile_for_another_adapter_is_refused_when_the_client_is_built() -> None:
    other = dataclasses.replace(PROFILE, adapter="scripted")
    with pytest.raises(ValueError, match="scripted"):
        client_for(FakeServer(), profile=other)


def test_hints_are_left_out_when_the_profile_honours_none() -> None:
    no_cache = dataclasses.replace(PROFILE, cache_hints=0)
    hints = (CacheHint(after="system", index=0), CacheHint(after="message", index=9))
    hinted = dataclasses.replace(ask(), cache_hints=hints)

    server = FakeServer(reply(GOOD), counted(14))
    client = client_for(server, profile=no_cache)
    assert isinstance(collect(client, hinted)[-1], StopEvent)
    assert count(client, hinted) == 14
    assert [b"cache_control" in r.content for r in server.requests] == [False, False]

    server = FakeServer(reply(GOOD), counted(14))
    client = client_for(server)
    well_hinted = dataclasses.replace(ask(), cache_hints=hints[:1])
    collect(client, well_hinted)
    count(client, well_hinted)
    assert [b"cache_control" in r.content for r in server.requests] == [True, True]


def test_a_call_with_no_time_at_all_is_a_timeout_and_is_never_sent() -> None:
    server = FakeServer(reply(GOOD))
    events = collect(client_for(server), ask(retries=3, deadline_ms=0))
    assert len(events) == 1
    assert the_error(events).failure.kind == "timeout"
    assert server.requests == []


def test_a_stream_with_bytes_that_are_not_text_ends_with_a_failure() -> None:
    not_text = b'event: content_block_start\ndata: {"index": \xff\xfe}\n\n'
    server = FakeServer(reply(message_start() + not_text), reply(GOOD))
    events = collect(client_for(server), ask())
    assert len(events) == 1
    assert the_error(events).failure.kind == "transient"
    assert "unreadable" in the_error(events).failure.detail


def test_a_count_the_profile_cannot_serve_is_never_sent() -> None:
    server = FakeServer(counted(14))
    nowhere = dataclasses.replace(
        ask(), cache_hints=(CacheHint(after="message", index=9),)
    )
    answer = the_failure(count(client_for(server), nowhere))
    assert answer.kind == "invalid_input"
    assert server.requests == []


def test_a_refused_call_ends_with_the_failure_for_its_status() -> None:
    server = FakeServer(refusal(401, "authentication_error"))
    events = collect(client_for(server), ask(retries=3))
    assert len(events) == 1
    end = the_error(events)
    assert end.failure.kind == "needs_user"
    assert end.failure.detail["status"] == 401
    assert end.failure.detail["request_id"] == "req_made_up"
    assert end.failure.detail["provider_message"] == "A made-up problem."
    assert (end.model_reported, end.skipped) == (None, 0)
    assert len(server.requests) == 1
    assert KEY not in repr(events)


def test_a_fault_that_may_clear_is_tried_again_with_the_same_bytes() -> None:
    server = FakeServer(refusal(529), refusal(500), reply(GOOD))
    clock = FakeClock()
    reply_read = fold_reply(collect(client_for(server, clock), ask(retries=2)))
    assert reply_read.message.parts == TEXT_REPLY.parts
    assert clock.slept == [0.5, 1.0]
    assert [r.content for r in server.requests] == [render_request(MODEL, ask())] * 3


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 504, 529])
def test_each_status_that_may_clear_is_tried_again(status: int) -> None:
    server = FakeServer(refusal(status), reply(GOOD))
    reply_read = fold_reply(collect(client_for(server), ask(retries=1)))
    assert isinstance(reply_read.end, StopEvent)
    assert len(server.requests) == 2


def test_when_attempts_run_out_the_stream_ends_with_the_last_failure() -> None:
    server = FakeServer(refusal(529), refusal(504), reply(GOOD))
    clock = FakeClock()
    events = collect(client_for(server, clock), ask(retries=1))
    assert len(events) == 1
    assert the_error(events).failure.kind == "timeout"
    assert clock.slept == [0.5]
    assert len(server.requests) == 2


def test_with_no_retries_allowed_one_attempt_is_made() -> None:
    server = FakeServer(refusal(529), reply(GOOD))
    clock = FakeClock()
    events = collect(client_for(server, clock), ask(retries=0))
    assert the_error(events).failure.kind == "transient"
    assert clock.slept == []
    assert len(server.requests) == 1


def test_the_wait_doubles_from_half_a_second_and_stops_at_eight() -> None:
    server = FakeServer(*[refusal(529) for _ in range(8)])
    clock = FakeClock()
    collect(client_for(server, clock), ask(retries=7))
    assert clock.slept == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0, 8.0]


def test_the_wait_the_server_asks_for_is_the_wait_taken() -> None:
    slow_down = refusal(429, "rate_limit_error", {"retry-after": "3"})
    server = FakeServer(slow_down, reply(GOOD))
    clock = FakeClock()
    reply_read = fold_reply(collect(client_for(server, clock), ask(retries=1)))
    assert isinstance(reply_read.end, StopEvent)
    assert clock.slept == [3.0]


def test_a_wait_that_ends_at_or_after_the_deadline_is_not_taken() -> None:
    slow_down = refusal(429, "rate_limit_error", {"retry-after": "5"})
    server = FakeServer(slow_down, reply(GOOD))
    clock = FakeClock()
    events = collect(client_for(server, clock), ask(retries=1, deadline_ms=5_000))
    end = the_error(events)
    assert (end.failure.kind, end.failure.retry_after_ms) == ("throttled", 5_000)
    assert clock.slept == []
    assert len(server.requests) == 1


def test_a_wait_that_ends_just_inside_the_deadline_is_taken() -> None:
    slow_down = refusal(429, "rate_limit_error", {"retry-after": "5"})
    server = FakeServer(slow_down, reply(GOOD))
    clock = FakeClock()
    collect(client_for(server, clock), ask(retries=1, deadline_ms=5_001))
    assert clock.slept == [5.0]


def test_the_deadline_is_counted_from_the_start_of_the_call() -> None:
    server = FakeServer(refusal(529), reply(GOOD))
    clock = FakeClock()
    clock.seconds = 1_000.0
    collect(client_for(server, clock), ask(retries=1, deadline_ms=2_000))
    assert clock.slept == [0.5]


@pytest.mark.parametrize("status", [400, 401, 404, 413])
def test_a_failure_that_will_not_clear_is_not_tried_again(status: int) -> None:
    server = FakeServer(refusal(status), reply(GOOD))
    clock = FakeClock()
    events = collect(client_for(server, clock), ask(retries=3))
    assert isinstance(events[-1], ErrorEvent)
    assert clock.slept == []
    assert len(server.requests) == 1


def test_the_servers_word_stops_a_retry_the_kind_would_allow() -> None:
    server = FakeServer(refusal(529, headers={"x-should-retry": "false"}), reply(GOOD))
    events = collect(client_for(server), ask(retries=3))
    assert the_error(events).failure.kind == "transient"
    assert len(server.requests) == 1


def test_the_servers_word_allows_a_retry_the_kind_would_stop() -> None:
    server = FakeServer(refusal(400, headers={"x-should-retry": "true"}), reply(GOOD))
    reply_read = fold_reply(collect(client_for(server), ask(retries=3)))
    assert isinstance(reply_read.end, StopEvent)
    assert len(server.requests) == 2


def test_a_server_that_cannot_be_reached_is_unavailable_and_tried_again() -> None:
    server = FakeServer(httpx.ConnectError("made-up: no route"), reply(GOOD))
    clock = FakeClock()
    events = collect(client_for(server, clock), ask(retries=0))
    end = the_error(events)
    assert end.failure.kind == "unavailable"
    assert end.failure.detail == {"transport_message": "made-up: no route"}

    server = FakeServer(httpx.ConnectError("made-up: no route"), reply(GOOD))
    reply_read = fold_reply(collect(client_for(server, clock), ask(retries=1)))
    assert isinstance(reply_read.end, StopEvent)


def test_a_connection_that_drops_before_the_answer_is_transient() -> None:
    server = FakeServer(httpx.ReadError("made-up: reset"))
    events = collect(client_for(server), ask())
    assert len(events) == 1
    assert the_error(events).failure.kind == "transient"


def test_an_error_before_any_content_is_tried_again() -> None:
    server = FakeServer(reply(OVERLOADED_AT_ONCE), reply(GOOD))
    clock = FakeClock()
    events = collect(client_for(server, clock), ask(retries=1))
    reply_read = fold_reply(events)
    assert reply_read.message.parts == TEXT_REPLY.parts
    assert clock.slept == [0.5]
    # The counts of the attempt that was given up are not reported.
    assert [e for e in events if isinstance(e, UsageEvent)] == [UsageEvent(USAGE)]


def test_the_last_failed_attempt_still_reports_its_counts() -> None:
    server = FakeServer(reply(OVERLOADED_AT_ONCE))
    events = collect(client_for(server), ask())
    assert events[0] == UsageEvent(Usage(7, 0, 0, None))
    assert the_error(events).failure.kind == "transient"
    assert the_error(events).model_reported == MODEL
    assert len(events) == 2


def test_an_error_before_content_that_will_not_clear_is_not_tried_again() -> None:
    rejected = message_start() + error("invalid_request_error")
    server = FakeServer(reply(rejected), reply(GOOD))
    events = collect(client_for(server), ask(retries=3))
    assert the_error(events).failure.kind == "invalid_input"
    assert len(server.requests) == 1


def test_once_content_has_been_given_nothing_is_tried_again() -> None:
    part_way = (
        message_start(Usage(7, 0, 0, None))
        + part_events(0, TextPart(text="Milk, "))
        + error("overloaded_error")
    )
    server = FakeServer(reply(part_way), reply(GOOD))
    clock = FakeClock()
    events = collect(client_for(server, clock), ask(retries=3))
    assert events == [
        TextEvent(0, "Milk, "),
        UsageEvent(Usage(7, 0, 0, None)),
        the_error(events),
    ]
    assert the_error(events).failure.kind == "transient"
    assert clock.slept == []
    assert len(server.requests) == 1


def test_a_connection_that_drops_part_way_keeps_what_came_and_is_closed() -> None:
    async def pieces() -> AsyncIterator[bytes]:
        yield message_start() + part_events(0, TextPart(text="Milk, "))
        raise httpx.ReadError("made-up: reset")

    body = Body(pieces())
    server = FakeServer(httpx.Response(200, stream=body), reply(GOOD))
    events = collect(client_for(server), ask(retries=3))
    assert events[0] == TextEvent(0, "Milk, ")
    end = the_error(events)
    assert end.failure.kind == "transient"
    assert end.failure.detail == {"transport_message": "made-up: reset"}
    assert end.model_reported == MODEL
    assert body.closed
    assert len(server.requests) == 1


def test_a_connection_that_drops_before_content_is_tried_again() -> None:
    async def pieces() -> AsyncIterator[bytes]:
        yield message_start()
        raise httpx.ReadError("made-up: reset")

    body = Body(pieces())
    server = FakeServer(httpx.Response(200, stream=body), reply(GOOD))
    reply_read = fold_reply(collect(client_for(server), ask(retries=1)))
    assert reply_read.message.parts == TEXT_REPLY.parts
    assert body.closed


def test_a_body_that_ends_early_is_a_failure_and_is_tried_again() -> None:
    cut_short = message_start() + part_events(0, TEXT)
    events = collect(client_for(FakeServer(reply(cut_short))), ask())
    assert the_error(events).failure.kind == "transient"

    server = FakeServer(reply(message_start()), reply(GOOD))
    reply_read = fold_reply(collect(client_for(server), ask(retries=1)))
    assert isinstance(reply_read.end, StopEvent)


def test_reading_stops_at_the_last_event_and_the_connection_is_closed() -> None:
    async def pieces() -> AsyncIterator[bytes]:
        yield GOOD
        raise AssertionError("the body was read past the end of the reply")

    body = Body(pieces())
    events = collect(client_for(FakeServer(httpx.Response(200, stream=body))), ask())
    assert isinstance(events[-1], StopEvent)
    assert body.closed


def test_an_answer_that_comes_after_the_deadline_is_a_timeout() -> None:
    clock = FakeClock()

    async def slow() -> httpx.Response:
        clock.seconds += 10
        return reply(GOOD)

    server = FakeServer(slow, reply(GOOD))
    events = collect(client_for(server, clock), ask(retries=3, deadline_ms=10_000))
    assert len(events) == 1
    end = the_error(events)
    assert end.failure.kind == "timeout"
    assert end.failure.retry_after_ms is None
    assert clock.slept == []
    assert len(server.requests) == 1


def test_a_deadline_that_passes_part_way_ends_the_reply_and_closes_it() -> None:
    clock = FakeClock()

    async def pieces() -> AsyncIterator[bytes]:
        yield message_start(Usage(7, 0, 0, None)) + part_events(0, TEXT)
        clock.seconds += 10
        # These bytes were already on their way, so they are still read.
        yield part_events(1, TextPart(text="Late."))
        yield message_end("end_turn", 40)

    body = Body(pieces())
    server = FakeServer(httpx.Response(200, stream=body))
    events = collect(client_for(server, clock), ask(deadline_ms=10_000))
    assert fold_reply(events).message.parts == (TEXT, TextPart(text="Late."))
    assert events[-2] == UsageEvent(Usage(7, 0, 0, None))
    assert the_error(events).failure.kind == "timeout"
    assert body.given == 2
    assert body.closed


def test_a_server_that_never_answers_is_cut_off_at_the_deadline() -> None:
    server = FakeServer(hang, reply(GOOD))
    events = collect(client_for(server), ask(retries=3, deadline_ms=30))
    assert len(events) == 1
    assert the_error(events).failure.kind == "timeout"
    assert len(server.requests) == 1


def test_a_body_that_stops_coming_is_cut_off_at_the_deadline() -> None:
    async def pieces() -> AsyncIterator[bytes]:
        yield message_start() + part_events(0, TEXT)
        await asyncio.Event().wait()

    body = Body(pieces())
    server = FakeServer(httpx.Response(200, stream=body))
    events = collect(client_for(server), ask(deadline_ms=30))
    assert fold_reply(events).message.parts == (TEXT,)
    assert the_error(events).failure.kind == "timeout"
    assert body.closed


def test_an_error_body_that_never_comes_still_gives_the_failure_of_its_status() -> None:
    async def pieces() -> AsyncIterator[bytes]:
        await asyncio.Event().wait()
        yield b""

    body = Body(pieces())
    server = FakeServer(httpx.Response(503, stream=body))
    events = collect(client_for(server), ask(deadline_ms=30))
    end = the_error(events)
    assert end.failure.kind == "transient"
    assert end.failure.detail["status"] == 503
    assert body.closed


def test_closing_the_stream_early_closes_the_connection_before_it_returns() -> None:
    async def pieces() -> AsyncIterator[bytes]:
        yield message_start() + part_events(0, TextPart(text="Milk, "))
        yield part_events(1, TextPart(text="eggs and tea.")) + message_end(
            "end_turn", 4
        )

    body = Body(pieces())
    client = client_for(FakeServer(httpx.Response(200, stream=body)))

    async def read_one_then_close() -> tuple[ModelEvent, bool]:
        stream = client.stream(ask())
        first = await anext(stream)
        await stream.aclose()
        # Read here, before the event loop gets a chance to tidy up by itself.
        return first, body.closed

    first, closed_at_once = asyncio.run(read_one_then_close())
    assert first == TextEvent(0, "Milk, ")
    assert closed_at_once
    assert body.given == 1


def test_cancelling_the_reader_closes_the_connection() -> None:
    reached_the_wait = asyncio.Event()

    async def pieces() -> AsyncIterator[bytes]:
        yield message_start() + part_events(0, TextPart(text="Milk, "))
        reached_the_wait.set()
        await asyncio.Event().wait()

    body = Body(pieces())
    client = client_for(FakeServer(httpx.Response(200, stream=body)))
    events: list[ModelEvent] = []

    async def read() -> None:
        async for event in client.stream(ask()):
            events.append(event)

    async def read_then_cancel() -> bool:
        reading = asyncio.ensure_future(read())
        await reached_the_wait.wait()
        reading.cancel()
        await asyncio.wait([reading])
        return reading.cancelled()

    assert asyncio.run(read_then_cancel())
    assert events == [TextEvent(0, "Milk, ")]
    assert body.closed


def the_failure(answer: int | Failure) -> Failure:
    assert isinstance(answer, Failure)
    return answer


def test_a_count_is_read_from_one_call_to_the_counting_endpoint() -> None:
    server = FakeServer(counted(14))
    assert count(client_for(server), ask()) == 14
    assert len(server.requests) == 1
    sent = server.requests[0]
    assert sent.method == "POST"
    assert sent.url == "https://api.anthropic.com/v1/messages/count_tokens"
    assert sent.headers["x-api-key"] == KEY
    assert sent.headers["anthropic-version"] == "2026-10-21"
    assert sent.content == render_count_request(MODEL, ask())


def test_a_refused_count_gives_the_failure_for_its_status() -> None:
    server = FakeServer(refusal(400, "invalid_request_error"), counted(14))
    failure = the_failure(count(client_for(server), ask(retries=3)))
    assert failure.kind == "invalid_input"
    assert failure.detail["status"] == 400
    assert len(server.requests) == 1


def test_a_count_is_tried_again_by_the_same_rules_as_a_reply() -> None:
    slow_down = refusal(429, "rate_limit_error", {"retry-after": "2"})
    server = FakeServer(refusal(529), slow_down, counted(14))
    clock = FakeClock()
    assert count(client_for(server, clock), ask(retries=2)) == 14
    assert clock.slept == [0.5, 2.0]


def test_the_deadline_of_a_count_is_counted_from_the_start_of_the_call() -> None:
    server = FakeServer(refusal(529), counted(14))
    clock = FakeClock()
    clock.seconds = 1_000.0
    assert count(client_for(server, clock), ask(retries=1, deadline_ms=2_000)) == 14
    assert clock.slept == [0.5]


def test_a_count_gives_the_last_failure_when_attempts_run_out() -> None:
    server = FakeServer(refusal(529), httpx.ConnectError("made-up"), counted(14))
    clock = FakeClock()
    failure = the_failure(count(client_for(server, clock), ask(retries=1)))
    assert failure.kind == "unavailable"
    assert clock.slept == [0.5]


@pytest.mark.parametrize(
    "answer", [b"not json", b"[14]", b'{"tokens": 14}', b'{"input_tokens": "14"}']
)
def test_a_count_that_is_not_as_documented_is_a_failure(answer: bytes) -> None:
    server = FakeServer(httpx.Response(200, content=answer), counted(14))
    failure = the_failure(count(client_for(server), ask()))
    assert failure.kind == "transient"
    assert "unreadable" in failure.detail

    server = FakeServer(httpx.Response(200, content=answer), counted(14))
    assert count(client_for(server), ask(retries=1)) == 14


def test_a_count_whose_answer_drops_part_way_is_a_failure_and_is_closed() -> None:
    async def pieces() -> AsyncIterator[bytes]:
        yield b'{"input_'
        raise httpx.ReadError("made-up: reset")

    body = Body(pieces())
    server = FakeServer(httpx.Response(200, stream=body))
    assert the_failure(count(client_for(server), ask())).kind == "transient"
    assert body.closed


def test_a_count_that_never_comes_is_cut_off_at_the_deadline() -> None:
    server = FakeServer(hang, counted(14))
    failure = the_failure(count(client_for(server), ask(retries=3, deadline_ms=30)))
    assert failure.kind == "timeout"
    assert len(server.requests) == 1

    async def pieces() -> AsyncIterator[bytes]:
        await asyncio.Event().wait()
        yield b""

    body = Body(pieces())
    server = FakeServer(httpx.Response(200, stream=body))
    failure = the_failure(count(client_for(server), ask(deadline_ms=30)))
    assert failure.kind == "timeout"
    assert body.closed
