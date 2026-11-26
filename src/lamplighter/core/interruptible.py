"""The race between a call, a stop and the time the call is allowed."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal

from lamplighter.ports import Clock

_log = logging.getLogger(__name__)

# A call that did not stop when it was cancelled is kept here until it
# ends, so it is not lost to the garbage collector half way.
_left_behind: set[asyncio.Task[Any]] = set()


@dataclass(frozen=True, slots=True)
class NotStarted:
    """A stop was already known, so the call was never begun."""


@dataclass(frozen=True, slots=True)
class Finished[T]:
    """The call ended with this value, maybe just after it was cut short."""

    value: T


@dataclass(frozen=True, slots=True)
class CutShort:
    """The call was cut short by a stop or by the time, and gave no value.

    `still_running` says the call did not stop inside the bound and was
    left behind.
    """

    reason: Literal["stop", "time"]
    still_running: bool


type Raced[T] = NotStarted | Finished[T] | CutShort


@dataclass(frozen=True, slots=True)
class StopSignal:
    """How the race learns of a stop: is one already known, and a wait for one."""

    known: Callable[[], bool]
    wait: Callable[[], Awaitable[object]]


async def interruptible[T](
    call: Coroutine[Any, Any, T],
    stop: StopSignal,
    clock: Clock,
    allowance_s: float,
    bound_s: float,
) -> Raced[T]:
    """Run `call` until it ends, a stop is asked for, or `allowance_s` passes.

    A call that is cut short is cancelled and given `bound_s` to stop. If
    it ends with a value inside that time, the value is given back. Every
    wait is on `clock`, so a test moves time by hand. Exceptions from the
    call are bugs and are raised.
    """
    if stop.known():
        call.close()
        return NotStarted()
    work = asyncio.ensure_future(call)
    stop_task = asyncio.ensure_future(stop.wait())
    timer = asyncio.ensure_future(clock.sleep(allowance_s))
    try:
        await asyncio.wait(
            {work, stop_task, timer}, return_when=asyncio.FIRST_COMPLETED
        )
        if work.done():
            return Finished(work.result())
        reason: Literal["stop", "time"] = "stop" if stop_task.done() else "time"
        work.cancel()
        await _wait_up_to(work, clock, bound_s)
        if work.done() and not work.cancelled():
            return Finished(work.result())
        if not work.done():
            _log.warning(
                "A call did not stop when it was cancelled. It is left running."
            )
        return CutShort(reason, still_running=not work.done())
    finally:
        stop_task.cancel()
        timer.cancel()
        _keep_until_done(work)


async def _wait_up_to(work: asyncio.Future[Any], clock: Clock, bound_s: float) -> None:
    bound = asyncio.ensure_future(clock.sleep(bound_s))
    try:
        await asyncio.wait({work, bound}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        bound.cancel()


def _keep_until_done(work: asyncio.Task[Any]) -> None:
    if work.done():
        return
    # A cancel of the race itself must reach the call too.
    work.cancel()
    _left_behind.add(work)
    work.add_done_callback(_left_behind.discard)
