import asyncio

from lamplighter.adapters import ScriptedTool, ScriptedTools, ToolCallNote
from lamplighter.ports import JsonObject, ToolResult, ToolSpec
from tests.waiting_clock import WaitingClock, settle

SPEC = ToolSpec(name="nap", description="Sleep a minute.", arguments_schema={})


def napping(clock: WaitingClock, ignores_cancel: bool) -> ScriptedTools:
    async def nap(arguments: JsonObject) -> ToolResult:
        del arguments
        await clock.sleep(60)
        return ToolResult(text="rested", is_error=False, effect="none")

    return ScriptedTools([ScriptedTool(SPEC, nap, ignores_cancel=ignores_cancel)])


def test_a_finished_call_is_noted_as_started_and_finished() -> None:
    async def scene() -> ScriptedTools:
        clock = WaitingClock()
        tools = napping(clock, ignores_cancel=False)
        task = asyncio.create_task(tools.call("nap", {"for": 1}))
        await settle()
        assert tools.started == (ToolCallNote("nap", {"for": 1}),)
        assert tools.finished == ()
        await clock.advance(60)
        assert task.result() == ToolResult(text="rested", is_error=False, effect="none")
        return tools

    tools = asyncio.run(scene())
    assert tools.finished == (ToolCallNote("nap", {"for": 1}),)


def test_a_cancelled_call_is_noted_as_started_only() -> None:
    async def scene() -> ScriptedTools:
        clock = WaitingClock()
        tools = napping(clock, ignores_cancel=False)
        task = asyncio.create_task(tools.call("nap", {}))
        await settle()
        task.cancel()
        await settle()
        assert task.cancelled()
        return tools

    tools = asyncio.run(scene())
    assert len(tools.started) == 1
    assert tools.finished == ()


def test_a_tool_that_ignores_the_cancel_runs_on_to_its_end() -> None:
    async def scene() -> None:
        clock = WaitingClock()
        tools = napping(clock, ignores_cancel=True)
        task = asyncio.create_task(tools.call("nap", {}))
        await settle()
        task.cancel()
        await settle()
        assert not task.done()
        await clock.advance(60)
        assert task.result() == ToolResult(text="rested", is_error=False, effect="none")
        assert len(tools.finished) == 1

    asyncio.run(scene())
