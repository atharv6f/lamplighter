"""The loop on fakes, on its plain path and around it."""

import asyncio
import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from lamplighter.adapters import ScriptedReply, ScriptedTool, ScriptedTools
from lamplighter.adapters.sqlite import (
    SqliteEventLog,
    SqliteInbox,
    SqliteOutbox,
    open_database,
)
from lamplighter.core import Reply, RunSpec, TurnEnd, run_turn
from lamplighter.ports import (
    DeliveryReceipt,
    Event,
    EventLog,
    Failure,
    JsonObject,
    Message,
    ModelRequest,
    NewEvent,
    Outbound,
    Outbox,
    SessionInfo,
    SessionState,
    StopEvent,
    TextPart,
    ToolCallPart,
    ToolResult,
    ToolResultPart,
    ToolSpec,
    Usage,
    event_id_for,
    inbound_event_id,
    out_id_for,
    to_plain,
)
from tests.loop_kit import MAX_RECORD_BYTES, Loop, in_memory
from tests.model_by_request import ModelByRequest, assistant_turns
from tests.model_samples import PROFILE
from tests.run_history import CONFIG, SESSION, SPEC, inbound, stop
from tests.waiting_clock import JumpingClock

QUESTION = "What time is it, and can you wait two seconds?"
ANSWER = "It is 07:35 UTC, and I waited two seconds."
USAGE = Usage(input_uncached=100, cache_read=0, cache_write=0, output=20)
# 100 input tokens at $4 and 20 output tokens at $20 per million.
COST = 800
CURRENT_TIME = ToolCallPart(id="call_1", name="current_time", arguments={})
WAIT = ToolCallPart(id="call_2", name="wait", arguments={"seconds": 2})
WEATHER = ToolCallPart(id="call_3", name="weather", arguments={"city": "Lisbon"})
WAIT_SPEC = ToolSpec(name="wait", description="Wait.", arguments_schema={})
FIRST_AT = "2026-11-26T07:35:12.345Z"
AFTER_WAIT = "2026-11-26T07:35:14.345Z"


def three_steps(request: ModelRequest) -> ScriptedReply:
    """Two tools, then a tool that does not exist, then the answer."""
    match assistant_turns(request):
        case 0:
            return ScriptedReply((CURRENT_TIME, WAIT), "tool_calls", USAGE)
        case 1:
            return ScriptedReply((WEATHER,), "tool_calls", USAGE)
        case _:
            return ScriptedReply((TextPart(text=ANSWER),), "end", USAGE)


def reply_payload(*parts: TextPart | ToolCallPart, reason: Any) -> dict[str, Any]:
    reply = Reply(
        message=Message(role="assistant", parts=parts),
        usage=USAGE,
        end=StopEvent(
            reason=reason,
            provider_reason=reason,
            model_reported=PROFILE.model,
            skipped=0,
        ),
    )
    payload = cast(dict[str, Any], to_plain(reply))
    payload["cost_micro_dollars"] = COST
    return payload


def eid(seq: int) -> str:
    return event_id_for(SESSION, seq)


def shape(event: Event) -> tuple[object, ...]:
    return (
        event.seq,
        event.type,
        event.payload,
        event.run_id,
        event.step,
        event.cause,
        event.duration_ms,
        event.at,
    )


RUN = "id-0001"
MESSAGE_ID = inbound_event_id("terminal", "n1")
UNKNOWN_TEXT = "There is no tool named weather. The tools are: current_time, wait."
EXPECTED: list[tuple[object, ...]] = [
    (1, "session_started", {"channel": "terminal", "conversation": SESSION},
     None, None, None, None, FIRST_AT),
    (2, "run_started", {"config_id": CONFIG, "trigger": "message"},
     RUN, None, MESSAGE_ID, None, FIRST_AT),
    (3, "user_message", {"text": QUESTION}, RUN, None, None, None, FIRST_AT),
    (4, "model_request", {"deadline_ms": 600_000}, RUN, 1, None, None, FIRST_AT),
    (5, "model_response",
     reply_payload(CURRENT_TIME, WAIT, reason="tool_calls"),
     RUN, 1, eid(4), 0, FIRST_AT),
    (6, "tool_intent", {"call_id": "call_1", "name": "current_time"},
     RUN, 1, eid(5), None, FIRST_AT),
    (7, "tool_result", {"call_id": "call_1", "text": FIRST_AT, "is_error": False},
     RUN, 1, eid(6), 0, FIRST_AT),
    (8, "tool_intent", {"call_id": "call_2", "name": "wait"},
     RUN, 1, eid(5), None, FIRST_AT),
    (9, "tool_result",
     {"call_id": "call_2", "text": "Waited 2 seconds.", "is_error": False},
     RUN, 1, eid(8), 2_000, AFTER_WAIT),
    (10, "model_request", {"deadline_ms": 600_000}, RUN, 2, None, None, AFTER_WAIT),
    (11, "model_response", reply_payload(WEATHER, reason="tool_calls"),
     RUN, 2, eid(10), 0, AFTER_WAIT),
    (12, "tool_result",
     {"call_id": "call_3", "text": UNKNOWN_TEXT, "is_error": True,
      "harness": "unknown_tool"},
     RUN, 2, eid(11), None, AFTER_WAIT),
    (13, "model_request", {"deadline_ms": 600_000}, RUN, 3, None, None, AFTER_WAIT),
    (14, "model_response", reply_payload(TextPart(text=ANSWER), reason="end"),
     RUN, 3, eid(13), 0, AFTER_WAIT),
    (15, "reply_ready",
     {"text": ANSWER, "kind": "reply", "run_reason": "done", "run_detail": ""},
     RUN, 3, None, None, AFTER_WAIT),
    (16, "run_end", {"reason": "done", "detail": ""},
     RUN, 3, None, None, AFTER_WAIT),
]  # fmt: skip


async def ask(loop: Loop, native_id: str = "n1", text: str = QUESTION) -> None:
    accepted = await loop.inbox.accept(inbound(native_id, text))
    assert not isinstance(accepted, Failure), accepted


def test_the_plain_run_saves_exactly_these_events() -> None:
    async def scenario() -> None:
        loop = in_memory(ModelByRequest(three_steps))
        await ask(loop)
        ended = await run_turn(SESSION, SPEC, loop.deps)
        assert ended == TurnEnd(run_id=RUN, last_seq=16, reason="done")
        events = await loop.events(SESSION)
        assert [shape(event) for event in events] == EXPECTED
        assert events[2].event_id == MESSAGE_ID
        assert await loop.inbox.pending() == ()
        assert await loop.outbox.due() == (
            Outbound(
                out_id=out_id_for(SESSION, 15),
                session_id=SESSION,
                kind="reply",
                text=ANSWER,
            ),
        )

    asyncio.run(scenario())


def test_the_model_sees_each_result_and_the_tool_list() -> None:
    async def scenario() -> None:
        model = ModelByRequest(three_steps)
        loop = in_memory(model)
        await ask(loop)
        await run_turn(SESSION, SPEC, loop.deps)
        assert len(model.requests) == 3
        first = model.requests[0]
        assert first.system == (SPEC.system,)
        assert first.tools == loop.tools.list_tools()
        assert first.max_output == SPEC.model_max_output
        assert first.retries == SPEC.model_retries
        assert first.cache_hints == ()
        assert first.messages == (
            Message(role="user", parts=(TextPart(text=QUESTION),)),
        )
        last = model.requests[2].messages
        assert [message.role for message in last] == [
            "user", "assistant", "user", "assistant", "user",
        ]  # fmt: skip
        assert [part.kind for part in last[2].parts] == ["tool_result", "tool_result"]
        assert model.closed == 3

    asyncio.run(scenario())


def test_the_deadline_is_the_time_the_run_has_left_when_that_is_less() -> None:
    async def scenario() -> None:
        clock = JumpingClock()
        model = ModelByRequest(three_steps, clock, seconds=100.5)
        loop = in_memory(model, clock)
        await ask(loop)
        await run_turn(SESSION, short_time(500), loop.deps)
        # 500 s allowed. The first call takes 100.5 s and the wait 2 s.
        assert [r.deadline_ms for r in model.requests] == [
            500_000,
            500_000 - 100_500 - 2_000,
            500_000 - 2 * 100_500 - 2_000,
        ]

    asyncio.run(scenario())


def test_a_deadline_never_goes_above_the_setting() -> None:
    async def scenario() -> None:
        model = ModelByRequest(three_steps)
        loop = in_memory(model)
        await ask(loop)
        await run_turn(SESSION, short_time(100_000), loop.deps)
        assert {r.deadline_ms for r in model.requests} == {SPEC.model_deadline_ms}

    asyncio.run(scenario())


def short_time(seconds: int) -> RunSpec:
    return replace(SPEC, max_seconds=seconds)


def sqlite_loop(folder: Path) -> tuple[Loop, sqlite3.Connection]:
    clock = JumpingClock()
    connection = open_database(folder / "state.db", timeout_s=0.2)
    assert not isinstance(connection, Failure), connection
    loop = in_memory(ModelByRequest(three_steps), clock)
    loop.log = SqliteEventLog(connection, {}, MAX_RECORD_BYTES)
    loop.inbox = SqliteInbox(connection, MAX_RECORD_BYTES)
    loop.outbox = SqliteOutbox(connection, clock, MAX_RECORD_BYTES)
    return loop, connection


def saved_rows(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    return connection.execute("SELECT * FROM events ORDER BY pos").fetchall()


def test_the_same_run_twice_saves_the_same_bytes(tmp_path: Path) -> None:
    async def one_run(folder: Path) -> list[tuple[object, ...]]:
        folder.mkdir()
        loop, connection = sqlite_loop(folder)
        try:
            await ask(loop)
            ended = await run_turn(SESSION, SPEC, loop.deps)
            assert isinstance(ended, TurnEnd)
            return saved_rows(connection)
        finally:
            connection.close()

    first = asyncio.run(one_run(tmp_path / "first"))
    second = asyncio.run(one_run(tmp_path / "second"))
    assert len(first) == 16
    assert first == second


def test_the_plain_run_on_sqlite_reads_back_as_in_memory(tmp_path: Path) -> None:
    async def scenario() -> None:
        loop, connection = sqlite_loop(tmp_path)
        try:
            await ask(loop)
            await run_turn(SESSION, SPEC, loop.deps)
            events = await loop.events(SESSION)
            assert [shape(event) for event in events] == EXPECTED
        finally:
            connection.close()

    asyncio.run(scenario())


def test_with_nothing_to_do_it_returns_none_and_appends_nothing() -> None:
    async def scenario() -> None:
        model = ModelByRequest(three_steps)
        loop = in_memory(model)
        assert await run_turn(SESSION, SPEC, loop.deps) is None
        assert await loop.events(SESSION) == ()
        await ask(loop)
        await run_turn(SESSION, SPEC, loop.deps)
        before = await loop.events(SESSION)
        # A message for another session is not this session's work.
        await loop.inbox.accept(inbound("n2", "Hi.", session="sess-b"))
        assert await run_turn(SESSION, SPEC, loop.deps) is None
        assert await loop.events(SESSION) == before
        assert len(await loop.pending()) == 1
        assert len(model.requests) == 3

    asyncio.run(scenario())


class FailingLog:
    """A log whose append number `failing` returns a Failure and writes nothing."""

    def __init__(self, inner: EventLog, failing: int) -> None:
        self.inner = inner
        self.failing = failing
        self.appends = 0
        self.failure = Failure(
            kind="unavailable", effect="none", message="The drive is gone.", source="t"
        )

    async def append(
        self, session_id: str, events: Sequence[NewEvent], expected_seq: int
    ) -> int | Failure:
        self.appends += 1
        if self.appends == self.failing:
            return self.failure
        return await self.inner.append(session_id, events, expected_seq)

    async def read(
        self, session_id: str, after_seq: int
    ) -> tuple[Event, ...] | Failure:
        return await self.inner.read(session_id, after_seq)

    async def sessions(
        self, state: SessionState | None
    ) -> tuple[SessionInfo, ...] | Failure:
        return await self.inner.sessions(state)

    async def tail(self, after_pos: int, limit: int) -> tuple[Event, ...] | Failure:
        return await self.inner.tail(after_pos, limit)


def test_a_failing_append_is_returned_and_the_next_call_carries_on() -> None:
    async def scenario() -> None:
        model = ModelByRequest(three_steps)
        loop = in_memory(model)
        inner = loop.log
        failing = FailingLog(inner, failing=3)
        loop.log = failing
        await ask(loop)
        assert await run_turn(SESSION, SPEC, loop.deps) is failing.failure
        # The third append was the first reply. Its request is saved.
        kept = await loop.events(SESSION)
        assert [event.type for event in kept][-1] == "model_request"
        assert len(kept) == 4
        loop.log = inner
        ended = await run_turn(SESSION, SPEC, loop.deps)
        assert isinstance(ended, TurnEnd) and ended.reason == "done"
        types = [event.type for event in await loop.events(SESSION)]
        # The lost call counts as a step, and the model is asked again.
        assert types[3:6] == ["model_request", "model_request", "model_response"]
        assert len(model.requests) == 4
        assert types.count("run_end") == 1

    asyncio.run(scenario())


# The plain run makes 14 appends. A failure at each one is returned, and
# the next call ends the run with every call answered once.
@pytest.mark.parametrize("failing", range(1, 15))
def test_a_failure_at_any_append_is_carried_on_from(failing: int) -> None:
    async def scenario() -> None:
        loop = in_memory(ModelByRequest(three_steps))
        inner = loop.log
        loop.log = FailingLog(inner, failing)
        await ask(loop)
        first = await run_turn(SESSION, SPEC, loop.deps)
        assert isinstance(first, Failure)
        loop.log = inner
        ended = await run_turn(SESSION, SPEC, loop.deps)
        assert isinstance(ended, TurnEnd) and ended.reason == "done"
        events = await loop.events(SESSION)
        results = [str(e.payload["call_id"]) for e in events if e.type == "tool_result"]
        assert sorted(results) == ["call_1", "call_2", "call_3"]
        assert [e.type for e in events].count("run_end") == 1
        assert await loop.inbox.pending() == ()
        assert len(await loop.due()) == 1

    asyncio.run(scenario())


def test_a_stop_with_nothing_running_is_used_up_and_starts_nothing() -> None:
    async def scenario() -> None:
        model = ModelByRequest(three_steps)
        loop = in_memory(model)
        await loop.inbox.accept(stop("s1"))
        assert await run_turn(SESSION, SPEC, loop.deps) is None
        events = await loop.events(SESSION)
        assert [event.type for event in events] == ["session_started"]
        assert await loop.inbox.pending() == ()
        assert model.requests == []

    asyncio.run(scenario())


def test_a_stop_behind_a_message_waits_for_the_run_the_message_starts() -> None:
    async def scenario() -> None:
        loop = in_memory(ModelByRequest(three_steps))
        await ask(loop)
        await loop.inbox.accept(stop("s1"))
        ended = await run_turn(SESSION, SPEC, loop.deps)
        assert isinstance(ended, TurnEnd) and ended.reason == "cancelled"
        events = await loop.events(SESSION)
        assert [event.type for event in events] == [
            "session_started", "run_started", "user_message",
            "cancelled", "reply_ready", "run_end",
        ]  # fmt: skip
        # No model request was made, so no event has a step.
        assert {event.step for event in events} == {None}
        assert events[4].payload == {
            "text": "Stopped.",
            "kind": "notice",
            "run_reason": "cancelled",
            "run_detail": "",
        }
        assert await loop.inbox.pending() == ()

    asyncio.run(scenario())


LATE = "Also, what day is it?"


def test_a_message_during_a_run_joins_the_next_request_after_the_results() -> None:
    async def scenario() -> None:
        loop_box: list[Loop] = []

        async def wait_and_hear(arguments: JsonObject) -> ToolResult:
            del arguments
            await ask(loop_box[0], "n2", LATE)
            return ToolResult(text="Waited.", is_error=False)

        def one_wait(request: ModelRequest) -> ScriptedReply:
            if assistant_turns(request) == 0:
                return ScriptedReply((WAIT,), "tool_calls", USAGE)
            return ScriptedReply((TextPart(text=ANSWER),), "end", USAGE)

        tools = ScriptedTools([ScriptedTool(WAIT_SPEC, wait_and_hear)])
        model = ModelByRequest(one_wait)
        loop = in_memory(model, tools=tools)
        loop_box.append(loop)
        await ask(loop)
        ended = await run_turn(SESSION, SPEC, loop.deps)
        assert isinstance(ended, TurnEnd) and ended.reason == "done"
        last = model.requests[1].messages[-1]
        assert last == Message(
            role="user",
            parts=(
                ToolResultPart(call_id="call_2", text="Waited.", is_error=False),
                TextPart(text=LATE),
            ),
        )
        events = await loop.events(SESSION)
        joined = [e for e in events if e.type == "user_message"][1]
        assert (joined.run_id, joined.step) == (RUN, 1)
        assert joined.event_id == inbound_event_id("terminal", "n2")
        assert await loop.inbox.pending() == ()

    asyncio.run(scenario())


class FailingOutbox:
    """An outbox whose enqueue fails, wrapped around a working one."""

    def __init__(self, inner: Outbox) -> None:
        self.inner = inner
        self.failure = Failure(
            kind="unavailable", effect="none", message="The drive is gone.", source="t"
        )

    async def enqueue(self, out: Outbound) -> None | Failure:
        return self.failure

    async def due(self) -> tuple[Outbound, ...] | Failure:
        return await self.inner.due()

    async def start_send(self, out_id: str) -> None | Failure:
        return await self.inner.start_send(out_id)

    async def mark_sent(self, out_id: str, receipt: DeliveryReceipt) -> None | Failure:
        return await self.inner.mark_sent(out_id, receipt)

    async def mark_failed(self, out_id: str, failure: Failure) -> None | Failure:
        return await self.inner.mark_failed(out_id, failure)


def test_a_run_is_not_closed_before_its_reply_is_in_the_outbox() -> None:
    async def scenario() -> None:
        loop = in_memory(ModelByRequest(three_steps))
        inner = loop.outbox
        failing = FailingOutbox(inner)
        loop.outbox = failing
        await ask(loop)
        assert await run_turn(SESSION, SPEC, loop.deps) is failing.failure
        events = await loop.events(SESSION)
        assert events[-1].type == "reply_ready"
        loop.outbox = inner
        ended = await run_turn(SESSION, SPEC, loop.deps)
        assert ended == TurnEnd(run_id=RUN, last_seq=16, reason="done")
        assert [out.text for out in await loop.due()] == [ANSWER]

    asyncio.run(scenario())


def test_a_tool_that_fails_gives_the_model_an_error_result() -> None:
    async def scenario() -> None:
        async def broken(arguments: JsonObject) -> Failure:
            del arguments
            return Failure(
                kind="unavailable",
                effect="none",
                message="The timer is not there.",
                source="t",
            )

        def one_wait(request: ModelRequest) -> ScriptedReply:
            if assistant_turns(request) == 0:
                return ScriptedReply((WAIT,), "tool_calls", USAGE)
            return ScriptedReply((TextPart(text=ANSWER),), "end", USAGE)

        tools = ScriptedTools([ScriptedTool(WAIT_SPEC, broken)])
        loop = in_memory(ModelByRequest(one_wait), tools=tools)
        await ask(loop)
        ended = await run_turn(SESSION, SPEC, loop.deps)
        assert isinstance(ended, TurnEnd) and ended.reason == "done"
        result = [e for e in await loop.events(SESSION) if e.type == "tool_result"]
        assert [event.payload for event in result] == [
            {
                "call_id": "call_2",
                "text": "wait failed (unavailable): The timer is not there.",
                "is_error": True,
            }
        ]

    asyncio.run(scenario())
