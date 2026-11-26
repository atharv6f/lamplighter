"""The loop's five rules for a model that misbehaves, run end to end on fakes."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

import pytest

from lamplighter.adapters import ScriptedBreak, ScriptedReply, ScriptedTools
from lamplighter.core import RunSpec, TurnEnd, harness_texts, run_turn
from lamplighter.ports import (
    Event,
    EventLog,
    Failure,
    Message,
    NewEvent,
    SessionInfo,
    SessionState,
    StopReason,
    TextPart,
    ToolCallPart,
    Usage,
)
from tests.loop_kit import Loop, in_memory, in_order, note_tools
from tests.model_by_request import ModelByRequest
from tests.run_history import SESSION, SPEC, inbound
from tests.waiting_clock import JumpingClock

QUESTION = "Note some numbers."
USAGE = Usage(input_uncached=100, cache_read=0, cache_write=0, output=20)
DONE = ScriptedReply((TextPart(text="All done."),), "end", USAGE)
EMPTY = ScriptedReply((), "end", USAGE)
WARNING = "\n\n" + harness_texts.REPEAT_WARNING


def note(call_id: str, n: int = 1) -> ToolCallPart:
    return ToolCallPart(id=call_id, name="note", arguments={"n": n})


def tick(call_id: str) -> ToolCallPart:
    return ToolCallPart(id=call_id, name="tick", arguments={})


def broken(call_id: str) -> ToolCallPart:
    return ToolCallPart(
        id=call_id,
        name="note",
        arguments=None,
        raw_arguments='{"n": ',
        parse_error="not JSON: Expecting value",
    )


def calls(*parts: ToolCallPart, reason: StopReason = "tool_calls") -> ScriptedReply:
    return ScriptedReply(parts, reason, USAGE)


def failing(kind: Any, *parts: TextPart, message: str = "It broke.") -> ScriptedBreak:
    return ScriptedBreak(
        parts, Failure(kind=kind, effect="none", message=message, source="model")
    )


@dataclass
class Ran:
    """What one call of the loop did: how it ended, its events, and what it called."""

    ended: TurnEnd
    events: tuple[Event, ...]
    model: ModelByRequest
    tools: ScriptedTools
    loop: Loop

    @property
    def model_calls(self) -> int:
        return len(self.model.requests)

    @property
    def tool_runs(self) -> int:
        return len(self.tools.started)

    def of_type(self, kind: str) -> list[Event]:
        return [event for event in self.events if event.type == kind]

    def results(self) -> list[dict[str, Any]]:
        return [dict(event.payload) for event in self.of_type("tool_result")]

    def texts(self) -> list[str]:
        return [str(result["text"]) for result in self.results()]

    @property
    def ready(self) -> dict[str, Any]:
        (ready,) = self.of_type("reply_ready")
        return dict(ready.payload)


def run(
    *turns: ScriptedReply | ScriptedBreak,
    spec: RunSpec = SPEC,
    model_seconds: float = 0.0,
) -> Ran:
    async def scenario() -> Ran:
        clock = JumpingClock()
        tools = note_tools(clock)
        model = ModelByRequest(in_order(*turns), clock, model_seconds)
        loop = in_memory(model, clock, tools)
        await loop.inbox.accept(inbound("n1", QUESTION))
        ended = await run_turn(SESSION, spec, loop.deps)
        assert isinstance(ended, TurnEnd), ended
        return Ran(ended, await loop.events(SESSION), model, tools, loop)

    return asyncio.run(scenario())


def ends(ran: Ran, reason: str, detail: str, text: str) -> None:
    assert (ran.ended.reason, ran.of_type("run_end")[0].payload) == (
        reason,
        {"reason": reason, "detail": detail},
    )
    assert ran.ready == {
        "text": text,
        "kind": "reply" if reason == "done" else "notice",
        "run_reason": reason,
        "run_detail": detail,
    }


# Rule 1: arguments that are not JSON.


def test_arguments_that_did_not_parse_are_answered_and_not_run() -> None:
    ran = run(calls(broken("c1")), DONE)
    assert ran.tool_runs == 0
    assert ran.of_type("tool_intent") == []
    assert ran.results() == [
        {
            "call_id": "c1",
            "text": "The arguments for note were not valid JSON: not JSON: Expecting"
            " value. Send the call again with valid JSON.",
            "is_error": True,
            "harness": "arguments_unparsed",
        }
    ]
    assert ran.model_calls == 2
    ends(ran, "done", "", "All done.")


# Rule 2: a tool that does not exist.


def test_a_tool_that_does_not_exist_is_answered_with_the_real_names() -> None:
    weather = ToolCallPart(id="c1", name="weather", arguments={})
    ran = run(calls(weather), DONE)
    assert ran.tool_runs == 0
    assert ran.results() == [
        {
            "call_id": "c1",
            "text": "There is no tool named weather. The tools are: note, tick.",
            "is_error": True,
            "harness": "unknown_tool",
        }
    ]
    assert ran.model_calls == 2
    ends(ran, "done", "", "All done.")


# Rule 3: the same call with the same result, again and again.


def test_one_repeat_per_reply_is_warned_on_the_third_and_ended_on_the_fourth() -> None:
    ran = run(*(calls(note(f"c{n}")) for n in range(1, 5)))
    assert ran.tool_runs == 3
    assert ran.texts() == [
        "Noted 1.",
        "Noted 1.",
        "Noted 1." + WARNING,
        harness_texts.not_run("note"),
    ]
    assert ran.results()[3]["harness"] == "not_run"
    assert ran.model_calls == 4
    ends(ran, "failed", "repeated_call", harness_texts.repeated_call_notice("note"))


def test_four_repeats_in_one_reply_all_run_and_the_next_reply_ends_the_run() -> None:
    ran = run(
        calls(note("c1"), note("c2"), note("c3"), note("c4")),
        calls(note("c5")),
    )
    assert ran.tool_runs == 4
    assert ran.texts() == [
        "Noted 1.",
        "Noted 1.",
        "Noted 1." + WARNING,
        "Noted 1." + WARNING,
        harness_texts.not_run("note"),
    ]
    assert ran.model_calls == 2
    ends(ran, "failed", "repeated_call", harness_texts.repeated_call_notice("note"))


def test_the_same_call_with_a_new_result_each_time_is_never_stopped() -> None:
    ran = run(*(calls(tick(f"c{n}")) for n in range(1, 7)), DONE)
    assert ran.texts() == [f"Tick {n}." for n in range(1, 7)]
    assert ran.model_calls == 7
    ends(ran, "done", "", "All done.")


def test_a_different_call_in_between_starts_the_count_again() -> None:
    ran = run(
        calls(note("c1")),
        calls(note("c2")),
        calls(note("c3", n=2)),
        calls(note("c4")),
        calls(note("c5")),
        DONE,
    )
    assert WARNING not in "".join(ran.texts())
    assert ran.tool_runs == 5
    ends(ran, "done", "", "All done.")


def test_a_different_call_after_three_repeats_is_run() -> None:
    ran = run(
        calls(note("c1")),
        calls(note("c2")),
        calls(note("c3")),
        calls(note("c4", n=2)),
        DONE,
    )
    assert ran.texts() == ["Noted 1.", "Noted 1.", "Noted 1." + WARNING, "Noted 2."]
    ends(ran, "done", "", "All done.")


def test_the_same_broken_call_is_warned_and_then_stopped() -> None:
    ran = run(*(calls(broken(f"c{n}")) for n in range(1, 5)))
    unparsed = harness_texts.arguments_unparsed("note", "not JSON: Expecting value")
    assert ran.texts() == [
        unparsed,
        unparsed,
        unparsed + WARNING,
        harness_texts.not_run("note"),
    ]
    assert ran.tool_runs == 0
    ends(ran, "failed", "repeated_call", harness_texts.repeated_call_notice("note"))


def test_the_same_cut_off_call_is_warned_and_then_stopped() -> None:
    ran = run(*(calls(note(f"c{n}"), reason="max_output") for n in range(1, 5)))
    cut = harness_texts.cut_off("note")
    assert ran.texts() == [cut, cut, cut + WARNING, harness_texts.not_run("note")]
    assert ran.tool_runs == 0
    ends(ran, "failed", "repeated_call", harness_texts.repeated_call_notice("note"))


def test_the_warning_is_left_out_when_results_are_compared() -> None:
    # The third result carries the warning, so the fourth must compare the
    # tool's own text, or the repeat would never be seen.
    ran = run(*(calls(note(f"c{n}")) for n in range(1, 6)))
    assert ran.model_calls == 4


# Rule 4: an empty reply.


def test_an_empty_reply_is_asked_again_with_a_note() -> None:
    ran = run(EMPTY, DONE)
    assert ran.model_calls == 2
    assert ran.model.requests[1].messages == (
        Message(
            role="user",
            parts=(
                TextPart(text=QUESTION),
                TextPart(text=harness_texts.EMPTY_REPLY_NOTE),
            ),
        ),
    )
    ends(ran, "done", "", "All done.")


def test_a_second_empty_reply_in_a_row_ends_the_run() -> None:
    ran = run(EMPTY, EMPTY)
    assert ran.model_calls == 2
    ends(ran, "failed", "empty_reply", harness_texts.EMPTY_REPLY_NOTICE)


def test_a_reply_of_blank_text_is_empty() -> None:
    blank = ScriptedReply((TextPart(text="  \n"),), "end", USAGE)
    ran = run(blank, blank)
    ends(ran, "failed", "empty_reply", harness_texts.EMPTY_REPLY_NOTICE)


def test_an_empty_reply_after_a_tool_step_gets_its_note_after_the_results() -> None:
    ran = run(calls(note("c1")), EMPTY, DONE)
    last = ran.model.requests[2].messages[-1]
    assert [part.kind for part in last.parts] == ["tool_result", "text"]
    assert last.parts[1] == TextPart(text=harness_texts.EMPTY_REPLY_NOTE)
    ends(ran, "done", "", "All done.")


def test_a_broken_reply_then_an_empty_one_ends_the_run() -> None:
    ran = run(failing("transient", TextPart(text="Half a")), EMPTY)
    assert ran.model_calls == 2
    ends(ran, "failed", "empty_reply", harness_texts.EMPTY_REPLY_NOTICE)


def test_a_refused_reply_ends_the_run() -> None:
    ran = run(ScriptedReply((), "refused", USAGE))
    assert ran.model_calls == 1
    ends(ran, "failed", "refused", harness_texts.REFUSED_NOTICE)


# Rule 5: a model call that fails.


def test_a_call_that_failed_after_part_of_the_reply_is_asked_once_more() -> None:
    ran = run(failing("transient", TextPart(text="Half a")), DONE)
    assert ran.model_calls == 2
    # The failed reply is never sent back.
    assert ran.model.requests[1].messages == ran.model.requests[0].messages
    ends(ran, "done", "", "All done.")


def test_a_second_failure_in_a_row_ends_the_run() -> None:
    ran = run(
        failing("transient", TextPart(text="Half a")),
        failing("transient", TextPart(text="Half a")),
    )
    assert ran.model_calls == 2
    ends(
        ran,
        "failed",
        "model_transient",
        "The run ended because the model call failed: It broke. Send a message to"
        " try again.",
    )


@pytest.mark.parametrize("kind", ["transient", "throttled", "timeout", "unavailable"])
def test_a_call_that_failed_with_nothing_arrived_ends_the_run_at_once(
    kind: str,
) -> None:
    ran = run(failing(kind))
    assert ran.model_calls == 1
    ends(
        ran,
        "failed",
        f"model_{kind}",
        "The run ended because the model call failed: It broke. Send a message to"
        " try again.",
    )


def test_a_failure_that_asking_again_cannot_fix_ends_the_run_even_with_a_part() -> None:
    ran = run(failing("denied", TextPart(text="Half a"), message="The key is refused."))
    assert ran.model_calls == 1
    ends(
        ran,
        "failed",
        "model_denied",
        "The run ended because the model call failed: The key is refused.",
    )


def test_a_refused_key_on_the_last_step_is_the_failure_and_not_the_limit() -> None:
    ran = run(
        calls(note("c1")),
        failing("denied", message="The key is refused."),
        spec=replace(SPEC, max_steps=2),
    )
    assert ran.model_calls == 2
    ends(
        ran,
        "failed",
        "model_denied",
        "The run ended because the model call failed: The key is refused.",
    )


def test_a_time_out_that_the_time_limit_caused_ends_at_the_limit() -> None:
    ran = run(
        failing("timeout"), spec=replace(SPEC, max_seconds=100), model_seconds=100
    )
    assert ran.model_calls == 1
    ends(ran, "limit", "seconds", harness_texts.limit_notice("seconds", 100))
    (hit,) = ran.of_type("limit_hit")
    assert hit.payload == {"limit": "seconds", "used": 100, "allowed": 100}


def test_a_time_out_with_time_left_is_the_failure() -> None:
    ran = run(
        failing("timeout"), spec=replace(SPEC, max_seconds=101), model_seconds=100
    )
    ends(
        ran,
        "failed",
        "model_timeout",
        "The run ended because the model call failed: It broke. Send a message to"
        " try again.",
    )


# A reply cut at its output limit.


def test_the_last_call_of_a_cut_reply_is_not_run_and_the_one_before_it_is() -> None:
    ran = run(calls(note("c1"), note("c2", n=2), reason="max_output"), DONE)
    assert [note.arguments for note in ran.tools.started] == [{"n": 1}]
    assert ran.texts() == ["Noted 1.", harness_texts.cut_off("note")]
    assert ran.results()[1]["harness"] == "cut_off"
    ends(ran, "done", "", "All done.")


def test_a_cut_final_reply_says_it_was_cut() -> None:
    cut = ScriptedReply((TextPart(text="The list is: one, two"),), "max_output", USAGE)
    ran = run(cut)
    ends(
        ran,
        "done",
        "",
        "The list is: one, two\n\n[The reply was cut off before the model finished.]",
    )


def test_a_final_reply_with_several_texts_joins_them_with_a_blank_line() -> None:
    reply = ScriptedReply(
        (TextPart(text="First."), TextPart(text=" "), TextPart(text="Second.")),
        "end",
        USAGE,
    )
    ran = run(reply)
    ends(ran, "done", "", "First.\n\nSecond.")


class LosingResults:
    """A log that loses every result a tool gave, as a kill after the tool would."""

    def __init__(self, inner: EventLog) -> None:
        self.inner = inner
        self.lost = 0

    async def append(
        self, session_id: str, events: Sequence[NewEvent], expected_seq: int
    ) -> int | Failure:
        if events[0].type == "tool_result" and "harness" not in events[0].payload:
            self.lost += 1
            return Failure(kind="fatal", effect="none", message="Lost.", source="t")
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


def test_a_call_interrupted_again_and_again_is_warned_before_it_is_stopped() -> None:
    async def scenario() -> None:
        clock = JumpingClock()
        tools = note_tools(clock)
        model = ModelByRequest(in_order(*(calls(note(f"c{n}")) for n in range(1, 5))))
        loop = in_memory(model, clock, tools)
        loop.log = LosingResults(loop.log)
        await loop.inbox.accept(inbound("n1", QUESTION))
        ended: object = None
        for _ in range(10):
            ended = await run_turn(SESSION, SPEC, loop.deps)
            if isinstance(ended, TurnEnd):
                break
        assert isinstance(ended, TurnEnd)
        events = await loop.events(SESSION)
        texts = [str(e.payload["text"]) for e in events if e.type == "tool_result"]
        interrupted = harness_texts.interrupted("note")
        assert texts == [
            interrupted,
            interrupted,
            interrupted + WARNING,
            harness_texts.not_run("note"),
        ]
        assert len(tools.started) == 3
        assert ended.reason == "failed"
        assert events[-1].payload == {"reason": "failed", "detail": "repeated_call"}

    asyncio.run(scenario())
