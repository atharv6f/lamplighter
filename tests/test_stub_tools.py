import asyncio

import pytest

from lamplighter.adapters import StubTools
from lamplighter.ports import JsonObject, ToolResult
from tests.fake_clock import FakeClock
from tests.waiting_clock import WaitingClock, settle


def test_current_time_gives_the_clock_time_in_the_saved_form() -> None:
    clock = WaitingClock()
    clock.seconds = 30
    result = asyncio.run(StubTools(clock).call("current_time", {}))
    assert result == ToolResult(
        text="2026-11-26T07:35:42.345Z", is_error=False, effect="none"
    )


def test_current_time_with_arguments_is_an_error_result() -> None:
    result = asyncio.run(StubTools(WaitingClock()).call("current_time", {"x": 1}))
    assert result == ToolResult(
        text="current_time takes no arguments.", is_error=True, effect="none"
    )


def test_wait_waits_on_the_clock_and_then_says_so() -> None:
    async def scene() -> None:
        clock = WaitingClock()
        task = asyncio.create_task(StubTools(clock).call("wait", {"seconds": 40}))
        await clock.advance(39)
        assert not task.done()
        await clock.advance(1)
        await settle()
        assert task.result() == ToolResult(
            text="Waited 40 seconds.", is_error=False, effect="none"
        )

    asyncio.run(scene())


def test_the_longest_wait_is_allowed_and_zero_returns_at_once() -> None:
    async def scene() -> None:
        clock = WaitingClock()
        tools = StubTools(clock)
        assert await tools.call("wait", {"seconds": 0}) == ToolResult(
            text="Waited 0 seconds.", is_error=False, effect="none"
        )
        task = asyncio.create_task(tools.call("wait", {"seconds": 600}))
        await clock.advance(600)
        assert task.result() == ToolResult(
            text="Waited 600 seconds.", is_error=False, effect="none"
        )

    asyncio.run(scene())


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"seconds": -1},
        {"seconds": 601},
        {"seconds": 1.5},
        {"seconds": True},
        {"seconds": "5"},
        {"seconds": 5, "and": "more"},
    ],
)
def test_wait_refuses_anything_but_one_whole_number_in_range(
    arguments: JsonObject,
) -> None:
    # A clock that returns from any sleep at once, so a bad value that slips
    # through gives a wrong answer and not a test that waits for ever.
    result = asyncio.run(StubTools(FakeClock()).call("wait", arguments))
    assert result == ToolResult(
        text="wait takes one argument, seconds, a whole number from 0 to 600.",
        is_error=True,
        effect="none",
    )


def test_the_specs_name_their_arguments() -> None:
    entries = StubTools(WaitingClock()).list_tools()
    assert [entry.spec.name for entry in entries] == ["current_time", "wait"]
    assert entries[1].spec.arguments_schema["required"] == ["seconds"]


def test_both_stubs_are_reads() -> None:
    entries = StubTools(WaitingClock()).list_tools()
    assert {entry.action_class for entry in entries} == {"read"}
