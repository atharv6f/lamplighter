"""The loop around the projector's pass: when it passes, waits, tells and stops."""

import asyncio
from collections.abc import Sequence

import pytest

from lamplighter.core.projector_loop import pass_to_the_end, project_while_open
from lamplighter.core.trace_projector import PassDone
from lamplighter.ports import Failure, FailureKind
from tests.fake_clock import FakeClock
from tests.waiting_clock import WaitingClock, settle

BUSY = PassDone(events=200, spans=40, pos=200)
IDLE = PassDone(events=0, spans=0, pos=200)


def failed(kind: FailureKind) -> Failure:
    return Failure(kind=kind, effect="none", message=f"A made-up {kind}.", source="t")


FATAL = failed("fatal")
DOWN = failed("unavailable")


class Script:
    """Gives the planned result of each pass in turn, and notes what was called."""

    def __init__(self, results: Sequence[PassDone | Failure]) -> None:
        self.results = list(results)
        self.passes = 0
        self.first: list[Failure] = []
        self.ended: list[Failure] = []

    async def run_pass(self) -> PassDone | Failure:
        self.passes += 1
        return self.results.pop(0)


def loop(results: Sequence[PassDone | Failure]) -> tuple[Script, FakeClock]:
    script, clock = Script(results), FakeClock()
    asyncio.run(
        project_while_open(
            script.run_pass, clock, script.first.append, script.ended.append
        )
    )
    return script, clock


def test_a_pass_that_read_events_is_followed_at_once_and_an_idle_one_after_1_s() -> (
    None
):
    script, clock = loop([BUSY, BUSY, IDLE, BUSY, IDLE, FATAL])

    assert clock.slept == [1.0, 1.0]
    assert script.passes == 6


def test_failures_in_a_row_wait_twice_as_long_each_time_up_to_a_minute() -> None:
    script, clock = loop([DOWN] * 8 + [FATAL])

    assert clock.slept == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
    assert script.first == [DOWN]
    assert script.ended == [FATAL]


def test_a_good_pass_sets_the_wait_back_and_the_next_failure_is_told_again() -> None:
    script, clock = loop([DOWN, DOWN, BUSY, DOWN, IDLE, DOWN, FATAL])

    assert clock.slept == [1.0, 2.0, 1.0, 1.0, 1.0]
    assert script.first == [DOWN, DOWN, DOWN]


def test_two_failures_with_no_good_pass_between_are_told_once() -> None:
    script, _ = loop([DOWN, failed("timeout"), FATAL])

    assert script.first == [DOWN]


@pytest.mark.parametrize("kind", ["timeout", "throttled", "transient", "unavailable"])
def test_a_failure_that_may_pass_is_waited_on_and_tried_again(
    kind: FailureKind,
) -> None:
    script, clock = loop([failed(kind), BUSY, IDLE, FATAL])

    assert clock.slept == [1.0, 1.0]
    assert script.first == [failed(kind)]
    assert script.passes == 4


@pytest.mark.parametrize(
    "kind", ["invalid_input", "fatal", "denied", "too_large", "conflict"]
)
def test_any_other_failure_is_handed_on_once_and_ends_the_loop(
    kind: FailureKind,
) -> None:
    script, clock = loop([BUSY, failed(kind), BUSY])

    assert script.ended == [failed(kind)]
    assert script.first == []
    assert script.passes == 2
    assert clock.slept == []


def test_a_cancel_during_a_wait_ends_the_loop() -> None:
    async def cancelled() -> int:
        script, clock = Script([DOWN] * 3), WaitingClock()
        task = asyncio.create_task(
            project_while_open(
                script.run_pass, clock, script.first.append, script.ended.append
            )
        )
        await settle()
        assert clock.sleepers == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return script.passes

    assert asyncio.run(cancelled()) == 1


def test_a_cancel_during_a_pass_ends_the_loop() -> None:
    async def cancelled() -> None:
        hold = asyncio.Event()

        async def stuck() -> PassDone | Failure:
            await hold.wait()
            return IDLE

        task = asyncio.create_task(project_while_open(stuck, FakeClock(), print, print))
        await settle()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancelled())


def test_a_pass_that_never_waits_still_gives_the_chat_its_turn() -> None:
    async def beside() -> list[int]:
        turns: list[int] = []
        script = Script([BUSY] * 5 + [FATAL])

        async def chat() -> None:
            # The chat beside the loop, which wants a turn now and then.
            for _ in range(3):
                turns.append(script.passes)
                await asyncio.sleep(0)

        talking = asyncio.ensure_future(chat())
        await project_while_open(script.run_pass, FakeClock(), print, print)
        await talking
        return turns

    # Without a turn between busy passes, the chat would first run after
    # the loop had ended, at pass 6.
    assert asyncio.run(beside()) == [1, 2, 3]


def last_passes(results: Sequence[PassDone | Failure]) -> tuple[Script, FakeClock]:
    script, clock = Script(results), FakeClock()
    asyncio.run(pass_to_the_end(script.run_pass, clock))
    return script, clock


def test_the_last_passes_go_on_until_one_has_nothing_to_do() -> None:
    script, _ = last_passes([BUSY, BUSY, IDLE, BUSY])

    assert script.passes == 3


@pytest.mark.parametrize("failure", [DOWN, FATAL])
def test_the_last_passes_stop_at_the_first_failure(failure: Failure) -> None:
    script, _ = last_passes([BUSY, failure, BUSY, IDLE])

    assert script.passes == 2


def test_a_last_pass_still_under_way_after_2_seconds_is_cancelled() -> None:
    async def hung() -> list[str]:
        noted: list[str] = []
        clock = WaitingClock()

        async def stuck() -> PassDone | Failure:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                noted.append(f"cancelled at {clock.seconds}")
                raise
            return IDLE

        ending = asyncio.ensure_future(pass_to_the_end(stuck, clock))
        await clock.advance(1.99)
        noted.append(f"done at 1.99: {ending.done()}")
        await clock.advance(0.01)
        noted.append(f"done at 2: {ending.done()}")
        await ending
        return noted

    assert asyncio.run(hung()) == [
        "done at 1.99: False",
        "cancelled at 2.0",
        "done at 2: True",
    ]


def test_a_last_pass_that_raises_raises_to_the_caller() -> None:
    async def broken() -> PassDone | Failure:
        raise RuntimeError("a made-up bug")

    with pytest.raises(RuntimeError, match="a made-up bug"):
        asyncio.run(pass_to_the_end(broken, FakeClock()))
