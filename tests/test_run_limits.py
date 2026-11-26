"""The four limits of a run, a changed config, and the end batch, on fakes."""

import asyncio
from dataclasses import replace

from lamplighter.adapters import ScriptedReply, ScriptedTool, ScriptedTools
from lamplighter.core import TurnEnd, harness_texts, run_turn
from lamplighter.ports import (
    Failure,
    JsonObject,
    Message,
    ModelRequest,
    TextPart,
    ToolResult,
    ToolResultPart,
    Usage,
)
from tests.fake_clock import FakeClock
from tests.loop_kit import NOTE_SPEC, Loop, in_memory, in_order, note_tools
from tests.model_by_request import ModelByRequest, assistant_turns
from tests.run_history import SESSION, SPEC, inbound
from tests.test_run_rules import (
    DONE,
    QUESTION,
    USAGE,
    Ran,
    calls,
    ends,
    failing,
    note,
    run,
)
from tests.test_run_turn import FailingLog


def runaway(request: ModelRequest) -> ScriptedReply:
    """A model that never stops: each reply notes the next number."""
    turn = assistant_turns(request) + 1
    return calls(note(f"c{turn}", n=turn))


def run_away(usage: Usage = USAGE, **limits: int) -> Ran:
    async def scenario() -> Ran:
        clock = FakeClock()
        tools = note_tools(clock)
        model = ModelByRequest(
            lambda request: replace(runaway(request), usage=usage),
            clock,
            limits.pop("model_seconds", 0),
        )
        loop = in_memory(model, clock, tools)
        await loop.inbox.accept(inbound("n1", QUESTION))
        ended = await run_turn(SESSION, replace(SPEC, **limits), loop.deps)
        assert isinstance(ended, TurnEnd), ended
        return Ran(ended, await loop.events(SESSION), model, tools, loop)

    return asyncio.run(scenario())


def ending_types(ran: Ran) -> list[str]:
    return [event.type for event in ran.events[-3:]]


def test_the_step_limit_stops_a_runaway_after_its_last_tools_ran() -> None:
    ran = run_away(max_steps=5)
    assert (ran.model_calls, ran.tool_runs) == (5, 5)
    assert ran.of_type("limit_hit")[0].payload == {
        "limit": "steps",
        "used": 5,
        "allowed": 5,
    }
    ends(
        ran,
        "limit",
        "steps",
        "The run stopped at its limit of 5 steps. The work so far is saved. Send a"
        " message to go on.",
    )
    assert ending_types(ran) == ["limit_hit", "reply_ready", "run_end"]


def test_the_time_limit_stops_a_runaway() -> None:
    ran = run_away(max_seconds=500, model_seconds=100)
    assert (ran.model_calls, ran.tool_runs) == (5, 4)
    assert ran.of_type("limit_hit")[0].payload == {
        "limit": "seconds",
        "used": 500,
        "allowed": 500,
    }
    ends(
        ran,
        "limit",
        "seconds",
        "The run stopped at its limit of 500 seconds. The work so far is saved. Send"
        " a message to go on.",
    )
    # The fifth reply's call was open when time ran out, so it was not run.
    assert ran.results()[-1]["harness"] == "not_run"
    assert ending_types(ran) == ["tool_result", "reply_ready", "run_end"]


def test_the_time_tools_take_counts_toward_the_time_limit() -> None:
    async def scenario() -> Ran:
        clock = FakeClock()
        tools = note_tools(clock, seconds=100)
        model = ModelByRequest(runaway, clock)
        loop = in_memory(model, clock, tools)
        await loop.inbox.accept(inbound("n1", QUESTION))
        ended = await run_turn(SESSION, replace(SPEC, max_seconds=300), loop.deps)
        assert isinstance(ended, TurnEnd), ended
        return Ran(ended, await loop.events(SESSION), model, tools, loop)

    ran = asyncio.run(scenario())
    assert (ran.model_calls, ran.tool_runs) == (3, 3)
    assert ran.of_type("limit_hit")[0].payload == {
        "limit": "seconds",
        "used": 300,
        "allowed": 300,
    }
    assert [e.duration_ms for e in ran.of_type("tool_result")] == [100_000] * 3


def test_the_token_limit_stops_a_runaway() -> None:
    usage = Usage(input_uncached=200, cache_read=30, cache_write=10, output=10)
    ran = run_away(usage, max_tokens=1_000)
    assert (ran.model_calls, ran.tool_runs) == (4, 4)
    assert ran.of_type("limit_hit")[0].payload == {
        "limit": "tokens",
        "used": 1_000,
        "allowed": 1_000,
    }
    ends(
        ran,
        "limit",
        "tokens",
        "The run stopped at its limit of 1000 tokens. The work so far is saved. Send"
        " a message to go on.",
    )


def test_the_money_limit_stops_a_runaway() -> None:
    # 500 tokens in at $4 and 25 out at $20 per million is 2,500 micro-dollars.
    usage = Usage(input_uncached=500, cache_read=0, cache_write=0, output=25)
    ran = run_away(usage, max_micro_dollars=10_000)
    assert (ran.model_calls, ran.tool_runs) == (4, 4)
    assert ran.of_type("limit_hit")[0].payload == {
        "limit": "micro_dollars",
        "used": 10_000,
        "allowed": 10_000,
    }
    ends(
        ran,
        "limit",
        "micro_dollars",
        "The run stopped at its limit of $0.01. The work so far is saved. Send a"
        " message to go on.",
    )


def test_a_reply_with_no_known_cost_spends_nothing() -> None:
    usage = Usage(input_uncached=500, cache_read=None, cache_write=0, output=25)
    ran = run_away(usage, max_micro_dollars=1, max_steps=3)
    assert ran.of_type("limit_hit")[0].payload["limit"] == "steps"
    assert all(
        "cost_micro_dollars" not in e.payload for e in ran.of_type("model_response")
    )


def test_the_first_limit_in_order_is_named_when_two_are_reached() -> None:
    ran = run_away(max_steps=2, max_tokens=240)
    assert ran.of_type("limit_hit")[0].payload == {
        "limit": "steps",
        "used": 2,
        "allowed": 2,
    }


def test_a_final_reply_over_a_limit_ends_done() -> None:
    usage = Usage(input_uncached=5_000, cache_read=0, cache_write=0, output=5_000)
    final = ScriptedReply((TextPart(text="All done."),), "end", usage)
    ran = run(final, spec=replace(SPEC, max_tokens=1_000, max_steps=1))
    ends(ran, "done", "", "All done.")
    assert ran.of_type("limit_hit") == []


def test_time_up_with_calls_open_answers_each_not_run() -> None:
    ran = run(
        calls(note("c1"), note("c2", n=2)),
        spec=replace(SPEC, max_seconds=500),
        model_seconds=500,
    )
    assert ran.tool_runs == 0
    assert [event.type for event in ran.events[-5:]] == [
        "limit_hit",
        "tool_result",
        "tool_result",
        "reply_ready",
        "run_end",
    ]
    assert ran.results() == [
        {
            "call_id": call_id,
            "text": harness_texts.not_run("note"),
            "is_error": True,
            "harness": "not_run",
        }
        for call_id in ("c1", "c2")
    ]
    reply_id = ran.of_type("model_response")[0].event_id
    assert {event.cause for event in ran.of_type("tool_result")} == {reply_id}
    assert {event.duration_ms for event in ran.of_type("tool_result")} == {None}


def test_a_lost_model_call_counts_as_a_step_and_spends_nothing() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        tools = note_tools(clock)
        model = ModelByRequest(runaway, clock, 100)
        loop = in_memory(model, clock, tools)
        inner = loop.log
        loop.log = FailingLog(inner, failing=3)
        await loop.inbox.accept(inbound("n1", QUESTION))
        spec = replace(SPEC, max_steps=3, max_seconds=250)
        assert isinstance(await run_turn(SESSION, spec, loop.deps), Failure)
        loop.log = inner
        ended = await run_turn(SESSION, spec, loop.deps)
        assert isinstance(ended, TurnEnd) and ended.reason == "limit"
        events = await loop.events(SESSION)
        requests = [e for e in events if e.type == "model_request"]
        assert [e.step for e in requests] == [1, 2, 3]
        # The lost call took 100 s, and none of it is counted: only the
        # second call's 100 s is gone when the third is made.
        assert [e.payload["deadline_ms"] for e in requests] == [
            250_000,
            250_000,
            150_000,
        ]
        (hit,) = [e for e in events if e.type == "limit_hit"]
        assert hit.payload == {"limit": "steps", "used": 3, "allowed": 3}
        assert len(model.requests) == 3

    asyncio.run(scenario())


def test_a_message_waiting_at_a_limit_starts_the_next_run() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        model = ModelByRequest(in_order(calls(note("c1")), DONE), clock)
        loops: list[Loop] = []

        async def note_and_hear(arguments: JsonObject) -> ToolResult:
            await loops[0].inbox.accept(inbound("n2", "And one more."))
            return ToolResult(text=f"Noted {arguments['n']}.", is_error=False)

        tools = ScriptedTools([ScriptedTool(NOTE_SPEC, note_and_hear)])
        loop = in_memory(model, clock, tools)
        loops.append(loop)
        await loop.inbox.accept(inbound("n1", QUESTION))
        spec = replace(SPEC, max_steps=1)
        first = await run_turn(SESSION, spec, loop.deps)
        assert isinstance(first, TurnEnd) and first.reason == "limit"
        assert [row.inbound.native_id for row in await loop.pending()] == ["n2"]
        second = await run_turn(SESSION, spec, loop.deps)
        assert isinstance(second, TurnEnd) and second.reason == "done"
        assert second.run_id != first.run_id
        last = model.requests[1].messages[-1]
        assert last == Message(
            role="user",
            parts=(
                ToolResultPart(call_id="c1", text="Noted 1.", is_error=False),
                TextPart(text=harness_texts.run_end_note("limit") or ""),
                TextPart(text="And one more."),
            ),
        )

    asyncio.run(scenario())


def test_after_a_failed_run_the_next_request_carries_the_note() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        model = ModelByRequest(in_order(failing("denied"), DONE), clock)
        loop = in_memory(model, clock, note_tools(clock))
        await loop.inbox.accept(inbound("n1", QUESTION))
        first = await run_turn(SESSION, SPEC, loop.deps)
        assert isinstance(first, TurnEnd) and first.reason == "failed"
        await loop.inbox.accept(inbound("n2", "Try again."))
        second = await run_turn(SESSION, SPEC, loop.deps)
        assert isinstance(second, TurnEnd) and second.reason == "done"
        assert model.requests[1].messages == (
            Message(
                role="user",
                parts=(
                    TextPart(text=QUESTION),
                    TextPart(text=harness_texts.run_end_note("failed") or ""),
                    TextPart(text="Try again."),
                ),
            ),
        )

    asyncio.run(scenario())


def test_a_run_open_under_other_settings_ends_after_its_started_call_is_answered() -> (
    None
):
    async def scenario() -> None:
        clock = FakeClock()
        tools = note_tools(clock)
        model = ModelByRequest(in_order(calls(note("c1"), note("c2", n=2))), clock)
        loop = in_memory(model, clock, tools)
        inner = loop.log
        # The fifth append is c1's result. It fails as a kill would, after
        # the tool ran, so the call has an intent and no result.
        loop.log = FailingLog(inner, failing=5)
        await loop.inbox.accept(inbound("n1", QUESTION))
        old = replace(SPEC, config_id="d" * 64)
        assert isinstance(await run_turn(SESSION, old, loop.deps), Failure)
        loop.log = inner
        ended = await run_turn(SESSION, SPEC, loop.deps)
        assert isinstance(ended, TurnEnd) and ended.reason == "failed"
        events = await loop.events(SESSION)
        assert [e.type for e in events[-5:]] == [
            "tool_intent",
            "tool_result",
            "tool_result",
            "reply_ready",
            "run_end",
        ]
        results = [e for e in events if e.type == "tool_result"]
        assert [r.payload["harness"] for r in results] == ["interrupted", "not_run"]
        assert results[0].cause == events[-5].event_id
        assert results[0].payload["text"] == harness_texts.interrupted("note")
        assert events[-2].payload == {
            "text": harness_texts.CONFIG_CHANGED_NOTICE,
            "kind": "notice",
            "run_reason": "failed",
            "run_detail": "config_changed",
        }
        assert len(tools.started) == 1

    asyncio.run(scenario())
