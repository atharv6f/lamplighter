"""The four lists of observers: when each is called, and that they change nothing."""

import asyncio
import logging
from typing import Any

import pytest

from lamplighter.core import (
    ModelCallEnded,
    ModelCallStarted,
    Observers,
    ToolCallEnded,
    ToolCallStarted,
    TurnEnd,
    run_turn,
)
from lamplighter.core.observers import notify
from lamplighter.ports import Event, ToolResultPart
from tests.loop_kit import Loop, in_memory, stop_now
from tests.model_by_request import ModelByRequest
from tests.run_history import SESSION, SPEC
from tests.test_run_rules import DONE, calls, note
from tests.test_run_stops import StopDuringAppend, long_note, loop_with, start
from tests.test_run_turn import (
    CURRENT_TIME,
    WAIT,
    WEATHER,
    FailingLog,
    ask,
    shape,
    three_steps,
)
from tests.waiting_clock import JumpingClock, WaitingClock, settle

type Seen = list[tuple[str, Any]]


def recording(seen: Seen, name: str) -> Observers:
    async def before_model(record: ModelCallStarted) -> None:
        seen.append((f"{name}.before_model", record))

    async def after_model(record: ModelCallEnded) -> None:
        seen.append((f"{name}.after_model", record))

    async def before_tool(record: ToolCallStarted) -> None:
        seen.append((f"{name}.before_tool", record))

    async def after_tool(record: ToolCallEnded) -> None:
        seen.append((f"{name}.after_tool", record))

    return Observers((before_model,), (after_model,), (before_tool,), (after_tool,))


def both(first: Observers, second: Observers) -> Observers:
    return Observers(
        first.before_model + second.before_model,
        first.after_model + second.after_model,
        first.before_tool + second.before_tool,
        first.after_tool + second.after_tool,
    )


async def plain_run(observers: Observers) -> tuple[Loop, ModelByRequest]:
    model = ModelByRequest(three_steps)
    loop = in_memory(model, JumpingClock())
    loop.observers = observers
    await ask(loop)
    ended = await run_turn(SESSION, SPEC, loop.deps)
    assert isinstance(ended, TurnEnd) and ended.reason == "done"
    return loop, model


def test_each_list_is_called_in_order_with_its_record() -> None:
    async def scenario() -> None:
        seen: Seen = []
        observers = both(recording(seen, "a"), recording(seen, "b"))
        _, model = await plain_run(observers)
        names = [name for name, _ in seen]
        assert names == [
            "a.before_model", "b.before_model", "a.after_model", "b.after_model",
            "a.before_tool", "b.before_tool", "a.after_tool", "b.after_tool",
            "a.before_tool", "b.before_tool", "a.after_tool", "b.after_tool",
            "a.before_model", "b.before_model", "a.after_model", "b.after_model",
            "a.before_model", "b.before_model", "a.after_model", "b.after_model",
        ]  # fmt: skip
        records = [record for name, record in seen if name.startswith("a.")]
        assert records[0] == ModelCallStarted(SESSION, "id-0001", 1, model.requests[0])
        first_reply = records[1]
        assert isinstance(first_reply, ModelCallEnded)
        assert (first_reply.step, first_reply.duration_ms) == (1, 0)
        assert first_reply.reply.message.parts == (CURRENT_TIME, WAIT)
        assert records[2] == ToolCallStarted(SESSION, "id-0001", 1, CURRENT_TIME)
        assert records[5] == ToolCallEnded(
            SESSION,
            "id-0001",
            1,
            WAIT,
            ToolResultPart(call_id="call_2", text="Waited 2 seconds.", is_error=False),
            2_000,
        )
        # The call to a tool that does not exist got no intent, so no
        # tool observer saw it.
        tool_calls = [r.call for r in records if isinstance(r, ToolCallStarted)]
        assert WEATHER not in tool_calls
        assert [r.step for r in records if isinstance(r, ModelCallStarted)] == [1, 2, 3]

    asyncio.run(scenario())


def test_observers_of_one_list_are_awaited_one_at_a_time() -> None:
    async def scenario() -> None:
        order: list[str] = []
        clock = JumpingClock()

        async def slow(record: ModelCallStarted) -> None:
            order.append(f"slow began {record.step}")
            await clock.sleep(0.5)
            order.append(f"slow ended {record.step}")

        async def quick(record: ModelCallStarted) -> None:
            order.append(f"quick {record.step}")

        model = ModelByRequest(three_steps)
        loop = in_memory(model, clock)
        loop.observers = Observers(before_model=(slow, quick))
        await ask(loop)
        await run_turn(SESSION, SPEC, loop.deps)
        assert order[:3] == ["slow began 1", "slow ended 1", "quick 1"]

    asyncio.run(scenario())


def failing_everywhere() -> Observers:
    async def boom(record: object) -> None:
        raise RuntimeError(f"an observer's own fault: {record!r}"[:40])

    return Observers((boom,), (boom,), (boom,), (boom,))


def test_observers_that_raise_change_no_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> tuple[list[Event], list[Event]]:
        quiet, _ = await plain_run(Observers())
        noisy, _ = await plain_run(failing_everywhere())
        return list(await quiet.events(SESSION)), list(await noisy.events(SESSION))

    with caplog.at_level(logging.WARNING):
        quiet, noisy = asyncio.run(scenario())
    assert [shape(event) for event in noisy] == [shape(event) for event in quiet]
    assert caplog.text.count("An observer raised") == 10


def test_a_slow_observer_is_cut_at_one_second_and_its_time_is_not_counted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> tuple[list[Event], list[Event], float]:
        clock = JumpingClock()
        cut: list[bool] = []

        async def stuck(record: ToolCallStarted) -> None:
            del record
            try:
                await clock.sleep(30)
            except asyncio.CancelledError:
                cut.append(True)
                raise

        async def stuck_model(record: ModelCallStarted) -> None:
            del record
            await clock.sleep(30)

        quiet, _ = await plain_run(Observers())
        model = ModelByRequest(three_steps)
        slow = in_memory(model, clock)
        slow.observers = Observers(before_model=(stuck_model,), before_tool=(stuck,))
        await ask(slow)
        await run_turn(SESSION, SPEC, slow.deps)
        assert cut == [True, True]
        return (
            list(await quiet.events(SESSION)),
            list(await slow.events(SESSION)),
            (clock.seconds),
        )

    with caplog.at_level(logging.WARNING):
        quiet, slow, seconds = asyncio.run(scenario())
    # Three model calls and two tool calls, each with a second of observer,
    # and the two second wait.
    assert seconds == 7
    durations = [(e.type, e.duration_ms) for e in slow]
    assert durations == [(e.type, e.duration_ms) for e in quiet]
    assert caplog.text.count("took more than a second") == 5


def test_after_model_is_not_called_for_a_call_a_stop_cut_short() -> None:
    async def scenario() -> None:
        seen: Seen = []
        loop, _, _ = loop_with(long_note(1), DONE, model_seconds=100)
        loop.observers = recording(seen, "a")
        clock = loop.clock
        assert isinstance(clock, WaitingClock)
        task = await start(loop)
        await clock.advance(5)
        await stop_now(loop, "s1")
        await settle()
        assert isinstance(task.result(), TurnEnd)
        assert [name for name, _ in seen] == ["a.before_model"]

    asyncio.run(scenario())


def test_after_tool_is_not_called_for_a_call_that_was_not_started() -> None:
    async def scenario() -> None:
        seen: Seen = []
        loop, _, _ = loop_with(long_note(1), calls(note("c1")))
        loop.observers = recording(seen, "a")
        loop.log = StopDuringAppend(loop.log, loop, "tool_intent")
        ended = await (await start(loop))
        assert isinstance(ended, TurnEnd) and ended.reason == "cancelled"
        assert [name for name, _ in seen] == [
            "a.before_model",
            "a.after_model",
            "a.before_tool",
        ]

    asyncio.run(scenario())


def test_after_tool_is_called_for_a_call_cut_short_with_its_harness_result() -> None:
    async def scenario() -> None:
        seen: Seen = []
        loop, _, _ = loop_with(long_note(600), calls(note("c1")))
        loop.observers = recording(seen, "a")
        clock = loop.clock
        assert isinstance(clock, WaitingClock)
        task = await start(loop)
        await clock.advance(10)
        await stop_now(loop, "s1")
        await settle()
        assert isinstance(task.result(), TurnEnd)
        name, record = seen[-1]
        assert name == "a.after_tool"
        assert isinstance(record, ToolCallEnded)
        assert record.duration_ms == 10_000
        assert record.result.is_error

    asyncio.run(scenario())


def test_no_tool_observer_sees_a_read_answered_interrupted_after_two_starts() -> None:
    async def scenario() -> None:
        loop, _, _ = loop_with(
            long_note(1), calls(note("c1")), DONE, clock=JumpingClock()
        )
        inner = loop.log
        # The sixth append is the result of c1, so c1 is left started.
        loop.log = FailingLog(inner, failing=6)
        await ask(loop)
        assert not isinstance(await run_turn(SESSION, SPEC, loop.deps), TurnEnd)
        # The read is run once more, and its result is lost again: the
        # second intent is the first append, the result the second.
        again: Seen = []
        loop.log = FailingLog(inner, failing=2)
        loop.observers = recording(again, "a")
        assert not isinstance(await run_turn(SESSION, SPEC, loop.deps), TurnEnd)
        assert [name for name, _ in again] == ["a.before_tool"]
        seen: Seen = []
        loop.log = inner
        loop.observers = recording(seen, "a")
        ended = await run_turn(SESSION, SPEC, loop.deps)
        assert isinstance(ended, TurnEnd) and ended.reason == "done"
        assert [name for name, _ in seen] == ["a.before_model", "a.after_model"]

    asyncio.run(scenario())


def test_notify_with_no_observers_does_nothing() -> None:
    async def scenario() -> None:
        await notify((), object(), WaitingClock())

    asyncio.run(scenario())


def test_each_after_list_is_called_once_its_event_is_saved() -> None:
    async def scenario() -> None:
        loops: list[Loop] = []
        saved_when_seen: list[str] = []

        async def after_model(record: ModelCallEnded) -> None:
            del record
            events = await loops[0].events(SESSION)
            saved_when_seen.append(events[-1].type)

        async def after_tool(record: ToolCallEnded) -> None:
            events = await loops[0].events(SESSION)
            assert events[-1].payload["call_id"] == record.call.id
            saved_when_seen.append(events[-1].type)

        model = ModelByRequest(three_steps)
        loop = in_memory(model, JumpingClock())
        loops.append(loop)
        loop.observers = Observers(after_model=(after_model,), after_tool=(after_tool,))
        await ask(loop)
        await run_turn(SESSION, SPEC, loop.deps)
        assert saved_when_seen == [
            "model_response",
            "tool_result",
            "tool_result",
            "model_response",
            "model_response",
        ]

    asyncio.run(scenario())
