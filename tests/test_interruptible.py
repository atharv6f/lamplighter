"""The race between a call, a stop and its time, and the flags it waits on."""

import asyncio

import pytest

from lamplighter.core.interruptible import (
    CutShort,
    Finished,
    NotStarted,
    Raced,
    interruptible,
)
from lamplighter.core.running_sessions import RunningSessions
from tests.waiting_clock import WaitingClock, settle

SESSION = "sess-a"


class Work:
    """A call that takes `seconds`, and notes whether it began, ended or was cut."""

    def __init__(
        self, clock: WaitingClock, seconds: float, tidy_up: float | None = None
    ) -> None:
        self.clock = clock
        self.seconds = seconds
        self.tidy_up = tidy_up
        self.began = False
        self.cancelled = False

    async def run(self) -> str:
        self.began = True
        try:
            await self.clock.sleep(self.seconds)
        except asyncio.CancelledError:
            self.cancelled = True
            if self.tidy_up is None:
                raise
            await self.clock.sleep(self.tidy_up)
            return "tidied"
        return "done"


async def race(
    work: Work, running: RunningSessions, allowance: float = 100, bound: float = 0.8
) -> asyncio.Task[Raced[str]]:
    task = asyncio.ensure_future(
        interruptible(
            work.run(), running.signal_for(SESSION), work.clock, allowance, bound
        )
    )
    await settle()
    return task


def test_a_stop_already_known_means_the_call_never_begins() -> None:
    async def scenario() -> None:
        clock = WaitingClock()
        running = RunningSessions()
        running.ask_stop(SESSION)
        work = Work(clock, 5)
        task = await race(work, running)
        assert task.result() == NotStarted()
        assert not work.began
        assert clock.sleepers == 0

    asyncio.run(scenario())


def test_a_call_that_ends_first_gives_its_value_and_leaves_no_timer() -> None:
    async def scenario() -> None:
        clock = WaitingClock()
        work = Work(clock, 5)
        task = await race(work, RunningSessions())
        await clock.advance(5)
        assert task.result() == Finished("done")
        await settle()
        assert clock.sleepers == 0

    asyncio.run(scenario())


def test_a_stop_cuts_the_call_at_once() -> None:
    async def scenario() -> None:
        clock = WaitingClock()
        running = RunningSessions()
        work = Work(clock, 5)
        task = await race(work, running)
        await clock.advance(1)
        running.ask_stop(SESSION)
        await settle()
        assert task.result() == CutShort("stop", still_running=False)
        assert work.cancelled
        assert clock.seconds == 1
        assert clock.sleepers == 0

    asyncio.run(scenario())


def test_the_time_cuts_the_call_at_its_allowance() -> None:
    async def scenario() -> None:
        clock = WaitingClock()
        work = Work(clock, 500)
        task = await race(work, RunningSessions(), allowance=30)
        await clock.advance(29.9)
        assert not task.done()
        await clock.advance(0.1)
        assert task.result() == CutShort("time", still_running=False)

    asyncio.run(scenario())


def test_a_value_that_arrives_inside_the_bound_is_given_back() -> None:
    async def scenario() -> None:
        clock = WaitingClock()
        running = RunningSessions()
        work = Work(clock, 500, tidy_up=0.5)
        task = await race(work, running)
        running.ask_stop(SESSION)
        await clock.advance(0.5)
        assert task.result() == Finished("tidied")

    asyncio.run(scenario())


def test_a_value_after_the_bound_is_too_late() -> None:
    async def scenario() -> None:
        clock = WaitingClock()
        running = RunningSessions()
        work = Work(clock, 500, tidy_up=0.9)
        task = await race(work, running)
        running.ask_stop(SESSION)
        await clock.advance(0.8)
        assert task.result() == CutShort("stop", still_running=True)
        await clock.advance(0.1)

    asyncio.run(scenario())


def test_a_call_that_raises_is_a_bug_and_is_raised() -> None:
    async def broken() -> str:
        raise RuntimeError("a bug")

    async def scenario() -> None:
        clock = WaitingClock()
        signal = RunningSessions().signal_for(SESSION)
        with pytest.raises(RuntimeError, match="a bug"):
            await interruptible(broken(), signal, clock, 10, 0.8)
        await settle()
        assert clock.sleepers == 0

    asyncio.run(scenario())


def test_cancelling_the_race_cancels_the_call() -> None:
    async def scenario() -> None:
        clock = WaitingClock()
        work = Work(clock, 500)
        task = await race(work, RunningSessions())
        task.cancel()
        await settle()
        assert task.cancelled()
        assert work.cancelled
        assert clock.sleepers == 0

    asyncio.run(scenario())


def test_one_session_is_run_once_at_a_time() -> None:
    running = RunningSessions()
    assert running.begin(SESSION)
    assert not running.begin(SESSION)
    assert running.begin("sess-b")
    assert running.is_running(SESSION)
    running.end(SESSION)
    assert not running.is_running(SESSION)
    assert running.begin(SESSION)


def test_a_stop_flag_is_per_session_and_can_be_taken_down() -> None:
    async def scenario() -> None:
        running = RunningSessions()
        waiting = asyncio.ensure_future(running.wait_for_stop(SESSION))
        running.ask_stop("sess-b")
        await settle()
        assert not waiting.done()
        assert not running.stop_asked(SESSION)
        running.ask_stop(SESSION)
        await settle()
        assert waiting.done()
        assert running.stop_asked(SESSION)
        running.clear_stop(SESSION)
        assert not running.stop_asked(SESSION)
        assert running.stop_asked("sess-b")

    asyncio.run(scenario())
