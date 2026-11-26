"""The promises of the tool port, checked on the scripted tools and the stub tools."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from lamplighter.adapters import ScriptedTool, ScriptedTools, StubTools
from lamplighter.ports import (
    Failure,
    JsonObject,
    ToolProvider,
    ToolResult,
    ToolSpec,
)
from tests.waiting_clock import WaitingClock, settle


@dataclass(frozen=True)
class ToolKind:
    """A provider, and the calls the suite makes on it."""

    make: Callable[[WaitingClock], ToolProvider]
    good: tuple[str, JsonObject]
    bad: tuple[str, JsonObject]
    slow: tuple[str, JsonObject]


async def _echo(arguments: JsonObject) -> ToolResult:
    text = arguments.get("text")
    if not isinstance(text, str):
        return ToolResult(text="echo needs text.", is_error=True)
    return ToolResult(text=text, is_error=False)


def _scripted(clock: WaitingClock) -> ToolProvider:
    async def nap(arguments: JsonObject) -> ToolResult:
        del arguments
        await clock.sleep(60)
        return ToolResult(text="rested", is_error=False)

    def spec(name: str) -> ToolSpec:
        return ToolSpec(name=name, description=f"The {name} tool.", arguments_schema={})

    return ScriptedTools(
        [ScriptedTool(spec("nap"), nap), ScriptedTool(spec("echo"), _echo)]
    )


KINDS = {
    "scripted": ToolKind(
        make=_scripted,
        good=("echo", {"text": "tea"}),
        bad=("echo", {"text": 7}),
        slow=("nap", {}),
    ),
    "stub": ToolKind(
        make=StubTools,
        good=("current_time", {}),
        bad=("wait", {"seconds": "soon"}),
        slow=("wait", {"seconds": 600}),
    ),
}


@pytest.fixture(params=list(KINDS.values()), ids=list(KINDS))
def kind(request: pytest.FixtureRequest) -> ToolKind:
    return request.param


def test_the_list_is_the_same_twice_and_sorted_by_name(kind: ToolKind) -> None:
    tools = kind.make(WaitingClock())
    first = tools.list_tools()
    assert first == tools.list_tools()
    names = [spec.name for spec in first]
    assert names == sorted(names)
    assert len(set(names)) == len(names)


def test_a_listed_tool_gives_a_result(kind: ToolKind) -> None:
    tools = kind.make(WaitingClock())
    name, arguments = kind.good
    assert name in {spec.name for spec in tools.list_tools()}
    result = asyncio.run(tools.call(name, arguments))
    assert isinstance(result, ToolResult), result
    assert not result.is_error


def test_bad_arguments_give_an_error_result_and_do_not_raise(kind: ToolKind) -> None:
    tools = kind.make(WaitingClock())
    result = asyncio.run(tools.call(*kind.bad))
    assert isinstance(result, ToolResult), result
    assert result.is_error


def test_a_call_can_be_cancelled_while_it_runs(kind: ToolKind) -> None:
    async def scene() -> None:
        clock = WaitingClock()
        tools = kind.make(clock)
        task = asyncio.create_task(tools.call(*kind.slow))
        await settle()
        assert not task.done()
        task.cancel()
        await settle()
        assert task.cancelled()

    asyncio.run(scene())


def test_an_unlisted_name_is_a_failure_and_not_a_raise(kind: ToolKind) -> None:
    result = asyncio.run(kind.make(WaitingClock()).call("no_such_tool", {}))
    assert isinstance(result, Failure)
    assert result.kind == "invalid_input"
