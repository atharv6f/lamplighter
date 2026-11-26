"""A read cut off by a kill runs once more, and arguments that do not fit never run."""

import asyncio

from lamplighter.adapters import ScriptedReply, ScriptedTool, ScriptedTools
from lamplighter.core import TurnEnd, harness_texts, run_turn
from lamplighter.ports import (
    Event,
    JsonObject,
    TextPart,
    ToolCallPart,
    ToolResult,
    ToolSpec,
    Usage,
)
from tests.kill_kit import Killed
from tests.loop_kit import Loop, in_memory, in_order
from tests.model_by_request import ModelByRequest
from tests.run_history import SESSION, SPEC, inbound
from tests.waiting_clock import JumpingClock

USAGE = Usage(input_uncached=100, cache_read=0, cache_write=0, output=20)
LOOK = ToolSpec(
    name="look",
    description="Look at a named thing.",
    arguments_schema={
        "type": "object",
        "properties": {"name": {"type": "string", "maxLength": 8}},
        "required": ["name"],
        "additionalProperties": False,
    },
)


class Looker:
    """The tool `look`, which kills the process the first `deaths` times it runs."""

    def __init__(self, deaths: int) -> None:
        self.deaths = deaths
        self.started = 0

    def tools(self) -> ScriptedTools:
        async def look(arguments: JsonObject) -> ToolResult:
            self.started += 1
            if self.started <= self.deaths:
                raise Killed
            return ToolResult(f"Saw {arguments['name']}.", False, "none")

        return ScriptedTools([ScriptedTool(LOOK, look)])


def loop_calling(looker: Looker, arguments: JsonObject) -> Loop:
    model = ModelByRequest(
        in_order(
            ScriptedReply(
                (ToolCallPart(id="c1", name="look", arguments=arguments),),
                "tool_calls",
                USAGE,
            ),
            ScriptedReply((TextPart(text="Looked."),), "end", USAGE),
        )
    )
    return in_memory(model, JumpingClock(), looker.tools())


def run_until_done(loop: Loop, processes: int) -> tuple[Event, ...]:
    """Run the loop in up to `processes` processes, each ended by a kill."""

    async def scene() -> tuple[Event, ...]:
        await loop.inbox.accept(inbound("m1", "Look at it."))
        for _ in range(processes):
            try:
                ended = await run_turn(SESSION, SPEC, loop.deps)
            except Killed:
                loop.running.end(SESSION)
                continue
            assert isinstance(ended, TurnEnd), ended
            assert ended.reason == "done"
            return await loop.events(SESSION)
        raise AssertionError("the run did not end")

    return asyncio.run(scene())


def tool_events(events: tuple[Event, ...]) -> list[tuple[str, JsonObject]]:
    return [
        (e.type, e.payload) for e in events if e.type in ("tool_intent", "tool_result")
    ]


INTENT = {"call_id": "c1", "name": "look", "action_class": "read"}


def test_a_read_cut_off_by_a_kill_runs_once_more_behind_a_second_intent() -> None:
    looker = Looker(deaths=1)
    events = run_until_done(loop_calling(looker, {"name": "tea"}), processes=2)
    assert tool_events(events) == [
        ("tool_intent", INTENT),
        ("tool_intent", INTENT),
        ("tool_result", {"call_id": "c1", "text": "Saw tea.", "is_error": False}),
    ]
    assert looker.started == 2


def test_a_read_that_kills_twice_is_answered_interrupted_and_the_run_goes_on() -> None:
    looker = Looker(deaths=2)
    events = run_until_done(loop_calling(looker, {"name": "tea"}), processes=3)
    assert tool_events(events) == [
        ("tool_intent", INTENT),
        ("tool_intent", INTENT),
        (
            "tool_result",
            {
                "call_id": "c1",
                "text": harness_texts.interrupted("look"),
                "is_error": True,
                "harness": "interrupted",
            },
        ),
    ]
    assert looker.started == 2


def test_arguments_that_do_not_fit_are_answered_and_the_tool_is_not_called() -> None:
    looker = Looker(deaths=0)
    loop = loop_calling(looker, {"name": "a long name", "colour": "red"})
    events = run_until_done(loop, processes=1)
    # The arguments are read back from the saved reply, whose keys are
    # sorted, so the problems come in the order of the names.
    text = (
        "The arguments for look do not fit its schema: colour: is not an argument"
        " of this tool; name: must have at most 8 characters. Send the call again"
        " with arguments that fit."
    )
    assert tool_events(events) == [
        (
            "tool_result",
            {
                "call_id": "c1",
                "text": text,
                "is_error": True,
                "harness": "arguments_invalid",
            },
        ),
    ]
    assert looker.started == 0
