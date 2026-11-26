"""Stopping a run, and the race between a call, a stop and the time left."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Sequence
from dataclasses import replace
from typing import Any

import pytest

from lamplighter.adapters import ScriptedReply, ScriptedTool, ScriptedTools
from lamplighter.core import TurnEnd, harness_texts, run_turn
from lamplighter.ports import (
    Accepted,
    Event,
    EventLog,
    Failure,
    Inbound,
    InboxEntry,
    JsonObject,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    NewEvent,
    SessionInfo,
    SessionState,
    StopEvent,
    TextEvent,
    ToolResult,
    Usage,
)
from tests.loop_kit import NOTE_SPEC, Loop, in_memory, in_order, stop_now
from tests.model_by_request import ModelByRequest
from tests.model_samples import PROFILE
from tests.run_history import SESSION, SPEC, inbound
from tests.test_run_rules import DONE, QUESTION, calls, note
from tests.waiting_clock import JumpingClock, WaitingClock, settle

USAGE = Usage(input_uncached=100, cache_read=0, cache_write=0, output=20)
STOPPED_TAIL = ["cancelled", "reply_ready", "run_end"]
STOPPED_READY = {
    "text": "Stopped.",
    "kind": "notice",
    "run_reason": "cancelled",
    "run_detail": "",
}


def long_note(seconds: float, after_cancel: float | None = None) -> ScriptedTool:
    """`note` that takes `seconds`. With `after_cancel`, a cancel makes it
    finish that much later with a real result, as a tool that tidies up would.
    """

    async def run(arguments: JsonObject) -> ToolResult:
        clock = CLOCKS[-1]
        try:
            await clock.sleep(seconds)
        except asyncio.CancelledError:
            if after_cancel is None:
                raise
            await clock.sleep(after_cancel)
            return ToolResult(
                text="Noted, just in time.", is_error=False, effect="none"
            )
        return ToolResult(
            text=f"Noted {arguments.get('n')}.", is_error=False, effect="none"
        )

    return ScriptedTool(NOTE_SPEC, run)


CLOCKS: list[WaitingClock] = []


def loop_with(
    tool: ScriptedTool,
    *turns: ScriptedReply,
    clock: WaitingClock | None = None,
    model_seconds: float = 0.0,
) -> tuple[Loop, ScriptedTools, ModelByRequest]:
    clock = clock or WaitingClock()
    CLOCKS.append(clock)
    tools = ScriptedTools([tool])
    model = ModelByRequest(in_order(*turns), clock, model_seconds)
    return in_memory(model, clock, tools), tools, model


async def start(loop: Loop) -> asyncio.Task[TurnEnd | Failure | None]:
    await loop.inbox.accept(inbound("n1", QUESTION))
    task = asyncio.ensure_future(run_turn(SESSION, SPEC, loop.deps))
    await settle()
    return task


def types(events: Sequence[Event]) -> list[str]:
    return [event.type for event in events]


def results(events: Sequence[Event]) -> list[dict[str, Any]]:
    return [dict(event.payload) for event in events if event.type == "tool_result"]


def test_a_stop_during_a_tool_ends_the_run_at_that_moment() -> None:
    async def scenario() -> None:
        loop, tools, _ = loop_with(long_note(600), calls(note("c1")))
        clock = CLOCKS[-1]
        task = await start(loop)
        await clock.advance(10)
        await stop_now(loop, "s1")
        await settle()
        assert task.done()
        ended = task.result()
        assert isinstance(ended, TurnEnd) and ended.reason == "cancelled"
        assert clock.seconds == 10
        events = await loop.events(SESSION)
        assert types(events)[-5:] == ["tool_intent", "tool_result", *STOPPED_TAIL]
        assert results(events) == [
            {
                "call_id": "c1",
                "text": harness_texts.stopped("note"),
                "is_error": True,
                "harness": "stopped",
            }
        ]
        assert events[-4].duration_ms == 10_000
        assert events[-2].payload == STOPPED_READY
        assert tools.finished == ()
        assert await loop.pending() == ()

    asyncio.run(scenario())


def test_a_stop_during_a_model_call_closes_its_stream_and_saves_no_reply() -> None:
    async def scenario() -> None:
        loop, _, model = loop_with(long_note(1), DONE, model_seconds=100)
        clock = CLOCKS[-1]
        task = await start(loop)
        await clock.advance(5)
        assert not task.done()
        await stop_now(loop, "s1")
        await settle()
        ended = task.result()
        assert isinstance(ended, TurnEnd) and ended.reason == "cancelled"
        assert model.closed == 1
        events = await loop.events(SESSION)
        assert types(events)[-4:] == ["model_request", *STOPPED_TAIL]
        assert "model_response" not in types(events)

    asyncio.run(scenario())


def test_a_tool_that_ends_with_a_result_inside_the_bound_has_it_saved() -> None:
    async def scenario() -> None:
        loop, tools, _ = loop_with(long_note(600, after_cancel=0.1), calls(note("c1")))
        clock = CLOCKS[-1]
        task = await start(loop)
        await clock.advance(10)
        await stop_now(loop, "s1")
        await clock.advance(0.1)
        ended = task.result()
        assert isinstance(ended, TurnEnd) and ended.reason == "cancelled"
        events = await loop.events(SESSION)
        assert results(events) == [
            {"call_id": "c1", "text": "Noted, just in time.", "is_error": False}
        ]
        assert events[-4].duration_ms == 10_100
        assert len(tools.started) == 1

    asyncio.run(scenario())


def test_a_tool_that_ignores_the_cancel_is_left_after_the_bound(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        stubborn = replace(long_note(600), ignores_cancel=True)
        loop, tools, _ = loop_with(stubborn, calls(note("c1")))
        clock = CLOCKS[-1]
        task = await start(loop)
        try:
            await clock.advance(10)
            await stop_now(loop, "s1")
            await clock.advance(0.79)
            assert not task.done()
            await clock.advance(0.02)
            ended = task.result()
            assert isinstance(ended, TurnEnd) and ended.reason == "cancelled"
            events = await loop.events(SESSION)
            assert results(events)[0]["text"] == (
                "note was cut short because the user stopped the run. Its outcome"
                " is unknown. It did not stop when asked and may still be running."
            )
            assert events[-4].duration_ms == 10_800
            assert tools.finished == ()
        finally:
            # The tool is still running, and ends on its own time. A tool
            # that swallows every cancel would otherwise hold up the test's
            # own shutdown.
            await clock.advance(600)
        assert len(tools.finished) == 1

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())
    assert "did not stop when it was cancelled" in caplog.text


class StopDuringAppend:
    """A log that lets a stop in while it saves an event of the given type.

    A store that gives the event loop a turn could let a stop in there.
    """

    def __init__(self, inner: EventLog, loop: Loop, kind: str) -> None:
        self.inner = inner
        self.loop = loop
        self.kind = kind
        self.done = False

    async def append(
        self, session_id: str, events: Sequence[NewEvent], expected_seq: int
    ) -> int | Failure:
        saved = await self.inner.append(session_id, events, expected_seq)
        if not self.done and events[0].type == self.kind:
            self.done = True
            await stop_now(self.loop, "s1")
        return saved

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


def test_a_tool_is_not_started_once_a_stop_is_known() -> None:
    async def scenario() -> None:
        loop, tools, _ = loop_with(long_note(1), calls(note("c1"), note("c2", n=2)))
        loop.log = StopDuringAppend(loop.log, loop, "tool_intent")
        task = await start(loop)
        ended = await task
        assert isinstance(ended, TurnEnd) and ended.reason == "cancelled"
        events = await loop.events(SESSION)
        assert tools.started == ()
        assert types(events)[-6:] == [
            "tool_intent",
            "tool_result",
            "cancelled",
            "tool_result",
            "reply_ready",
            "run_end",
        ]
        assert [r["harness"] for r in results(events)] == ["not_run", "not_run"]
        assert events[-5].duration_ms is None
        assert events[-5].cause == events[-6].event_id

    asyncio.run(scenario())


def test_a_final_reply_then_a_stop_ends_done_and_uses_the_stop_up() -> None:
    async def scenario() -> None:
        loop, _, _ = loop_with(long_note(1), DONE)
        loop.log = StopDuringAppend(loop.log, loop, "model_response")
        ended = await (await start(loop))
        assert isinstance(ended, TurnEnd) and ended.reason == "done"
        assert [out.text for out in await loop.due()] == ["All done."]
        assert await loop.pending() == ()

    asyncio.run(scenario())


class StopAfterRead:
    """An inbox that lets a stop in just after its read number `on`."""

    def __init__(self, loop: Loop, on: int) -> None:
        self.inner = loop.inbox
        self.loop = loop
        self.on = on
        self.reads = 0

    async def accept(self, inbound: Inbound) -> Accepted | Failure:
        return await self.inner.accept(inbound)

    async def pending(self) -> tuple[InboxEntry, ...] | Failure:
        rows = await self.inner.pending()
        self.reads += 1
        if self.reads == self.on:
            self.loop.inbox = self.inner
            await stop_now(self.loop, "s1")
            self.loop.inbox = self
        return rows

    async def mark_routed(
        self, inbox_id: int, session_id: str, seq: int
    ) -> None | Failure:
        return await self.inner.mark_routed(inbox_id, session_id, seq)


def test_a_stop_saved_just_after_the_inbox_read_keeps_the_model_from_starting() -> None:
    async def scenario() -> None:
        loop, _, model = loop_with(long_note(1), DONE)
        # The third read is the pass that calls the model: the first starts
        # the run and the second marks the message.
        loop.inbox = StopAfterRead(loop, on=3)
        ended = await (await start(loop))
        assert isinstance(ended, TurnEnd) and ended.reason == "cancelled"
        assert model.requests == []
        events = await loop.events(SESSION)
        assert types(events)[-4:] == ["model_request", *STOPPED_TAIL]

    asyncio.run(scenario())


def test_a_flag_set_after_its_stop_was_used_does_not_cut_the_next_run() -> None:
    async def scenario() -> None:
        loop, tools, _ = loop_with(long_note(1), DONE, calls(note("c1")), DONE)
        clock = CLOCKS[-1]
        task = await start(loop)
        await task
        # The intake was slow: the flag goes up after the run that used
        # its stop has closed.
        loop.running.ask_stop(SESSION)
        await loop.inbox.accept(inbound("n2", "Note one."))
        second = asyncio.ensure_future(run_turn(SESSION, SPEC, loop.deps))
        await clock.advance(1)
        ended = await second
        assert isinstance(ended, TurnEnd) and ended.reason == "done"
        assert len(tools.finished) == 1

    asyncio.run(scenario())


def test_every_stop_pending_at_the_close_is_used_up() -> None:
    async def scenario() -> None:
        loop, _, _ = loop_with(long_note(600), calls(note("c1")))
        clock = CLOCKS[-1]
        task = await start(loop)
        await clock.advance(10)
        await loop.inbox.accept(inbound("n0", "x", session="sess-b"))
        await stop_now(loop, "s1")
        await stop_now(loop, "s2")
        await settle()
        ended = task.result()
        assert isinstance(ended, TurnEnd) and ended.reason == "cancelled"
        reply_ready = (await loop.events(SESSION))[-2]
        assert [row.inbound.native_id for row in await loop.pending()] == ["n0"]
        assert reply_ready.type == "reply_ready"

    asyncio.run(scenario())


def test_a_second_call_for_a_running_session_is_refused_and_writes_nothing() -> None:
    async def scenario() -> None:
        loop, _, _ = loop_with(long_note(600), calls(note("c1")))
        task = await start(loop)
        before = await loop.events(SESSION)
        second = await run_turn(SESSION, SPEC, loop.deps)
        assert isinstance(second, Failure) and second.kind == "conflict"
        assert await loop.events(SESSION) == before
        assert not task.done()
        # Another session is not held up by this one.
        assert await run_turn("sess-b", SPEC, loop.deps) is None
        await stop_now(loop, "s1")
        await settle()
        assert isinstance(task.result(), TurnEnd)
        assert not loop.running.is_running(SESSION)

    asyncio.run(scenario())


def test_the_time_limit_cuts_a_running_tool() -> None:
    async def scenario() -> None:
        clock = JumpingClock()
        loop, tools, _ = loop_with(long_note(5_000), calls(note("c1")), clock=clock)
        await loop.inbox.accept(inbound("n1", QUESTION))
        ended = await run_turn(SESSION, SPEC, loop.deps)
        assert isinstance(ended, TurnEnd) and ended.reason == "limit"
        assert clock.seconds == 1_200
        events = await loop.events(SESSION)
        assert results(events) == [
            {
                "call_id": "c1",
                "text": harness_texts.out_of_time("note"),
                "is_error": True,
                "harness": "out_of_time",
            }
        ]
        assert [e for e in events if e.type == "tool_result"][0].duration_ms == (
            1_200_000
        )
        assert [e.payload for e in events if e.type == "limit_hit"] == [
            {"limit": "seconds", "used": 1_200, "allowed": 1_200}
        ]
        assert tools.finished == ()

    asyncio.run(scenario())


def test_a_model_that_ignores_its_deadline_is_cut_one_bound_after_it() -> None:
    async def scenario() -> None:
        clock = JumpingClock()
        loop, _, model = loop_with(long_note(1), DONE, clock=clock, model_seconds=5_000)
        await loop.inbox.accept(inbound("n1", QUESTION))
        spec = replace(SPEC, max_seconds=100)
        ended = await run_turn(SESSION, spec, loop.deps)
        assert isinstance(ended, TurnEnd) and ended.reason == "limit"
        assert clock.seconds == pytest.approx(100.8)
        assert model.closed == 1
        events = await loop.events(SESSION)
        (reply,) = [e for e in events if e.type == "model_response"]
        assert reply.duration_ms == 100_800
        assert reply.payload["end"] == {
            "failure": {
                "cause": None,
                "detail": {},
                "effect": "none",
                "kind": "timeout",
                "message": harness_texts.MODEL_OUT_OF_TIME,
                "retry_after_ms": None,
                "source": "run_turn",
                "v": 1,
            },
            "kind": "error",
            "model_reported": None,
            "skipped": 0,
        }
        assert [e.payload for e in events if e.type == "limit_hit"] == [
            {"limit": "seconds", "used": 100, "allowed": 100}
        ]

    asyncio.run(scenario())


def test_a_model_past_its_own_deadline_with_run_time_left_is_a_failed_call() -> None:
    async def scenario() -> None:
        clock = JumpingClock()
        loop, _, _ = loop_with(long_note(1), DONE, clock=clock, model_seconds=5_000)
        await loop.inbox.accept(inbound("n1", QUESTION))
        spec = replace(SPEC, model_deadline_ms=100_000)
        ended = await run_turn(SESSION, spec, loop.deps)
        assert isinstance(ended, TurnEnd) and ended.reason == "failed"
        events = await loop.events(SESSION)
        assert events[-2].payload == {
            "text": "The run ended because the model call failed: The model did"
            " not answer in the time it was given. Send a message to try again.",
            "kind": "notice",
            "run_reason": "failed",
            "run_detail": "model_timeout",
        }
        assert clock.seconds == pytest.approx(100.8)

    asyncio.run(scenario())


class HalfThenHang:
    """A model whose first reply sends a little text and then never ends."""

    def __init__(self, clock: WaitingClock) -> None:
        self.clock = clock
        self.requests: list[ModelRequest] = []

    @property
    def profile(self) -> ModelProfile:
        return PROFILE

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelEvent]:
        self.requests.append(request)
        if len(self.requests) == 1:
            yield TextEvent(index=0, text="Half a")
            await self.clock.sleep(5_000)
        yield TextEvent(index=0, text="All done.")
        yield StopEvent(
            reason="end", provider_reason="end", model_reported=None, skipped=0
        )

    async def count_tokens(self, request: ModelRequest) -> int | Failure:
        raise AssertionError("the loop never counts tokens")


def test_a_reply_cut_at_its_deadline_keeps_what_arrived_and_is_asked_again() -> None:
    async def scenario() -> None:
        clock = JumpingClock()
        model = HalfThenHang(clock)
        loop = in_memory(model, clock, ScriptedTools([long_note(1)]))
        await loop.inbox.accept(inbound("n1", QUESTION))
        spec = replace(SPEC, model_deadline_ms=100_000)
        ended = await run_turn(SESSION, spec, loop.deps)
        assert isinstance(ended, TurnEnd) and ended.reason == "done"
        events = await loop.events(SESSION)
        first = [e for e in events if e.type == "model_response"][0]
        assert first.payload["message"] == {
            "parts": [{"kind": "text", "text": "Half a"}],
            "role": "assistant",
        }
        assert isinstance(first.payload["end"], dict)
        assert first.payload["end"]["kind"] == "error"
        assert len(model.requests) == 2
        assert model.requests[1].messages == model.requests[0].messages
        assert model.requests[1].deadline_ms == 100_000

    asyncio.run(scenario())
