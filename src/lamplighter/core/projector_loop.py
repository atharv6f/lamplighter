"""Runs passes for as long as a chat lives: when to pass, when to wait, when to stop."""

import asyncio
from collections.abc import Awaitable, Callable

from lamplighter.core.trace_projector import PassDone
from lamplighter.ports import Clock, Failure

# Kinds that say the same pass may work later: the store is down, slow or busy.
MAY_PASS = frozenset({"timeout", "unavailable", "throttled", "transient"})
IDLE_WAIT_S = 1.0
FIRST_WAIT_S = 1.0
LONGEST_WAIT_S = 60.0
# Leaving a chat must not hang on a trace store that is down.
LAST_PASSES_S = 2.0


async def project_while_open(
    run_pass: Callable[[], Awaitable[PassDone | Failure]],
    clock: Clock,
    first_failure: Callable[[Failure], None],
    ended: Callable[[Failure], None],
) -> None:
    """Call `run_pass` again and again, until a failure that will not pass.

    After a pass that read events the next follows at once, and after an idle
    one the loop waits a second. A failure that may pass is waited on, one
    second after the first in a row and twice as long after each next one,
    up to a minute. `first_failure` is called for the first of each row.
    Any other failure is handed to `ended`, and the loop returns. A cancel
    ends it at once, wherever it waits.
    """
    wait = FIRST_WAIT_S
    told = False
    while True:
        done = await run_pass()
        if isinstance(done, Failure):
            if done.kind not in MAY_PASS:
                ended(done)
                return
            if not told:
                first_failure(done)
                told = True
            await clock.sleep(wait)
            wait = min(wait * 2, LONGEST_WAIT_S)
            continue
        wait, told = FIRST_WAIT_S, False
        if done.events == 0:
            await clock.sleep(IDLE_WAIT_S)
        else:
            # A pass may never truly wait, as one with no span to send does,
            # so the chat beside the loop is given its turn here.
            await asyncio.sleep(0)


async def pass_to_the_end(
    run_pass: Callable[[], Awaitable[PassDone | Failure]], clock: Clock
) -> None:
    """Pass until nothing is left or a pass fails, for at most two seconds.

    A pass still under way when the time is over is cancelled, and waited
    for until it has stopped. A pass that raises raises here.
    """
    passes = asyncio.ensure_future(_until_idle(run_pass))
    timer = asyncio.ensure_future(clock.sleep(LAST_PASSES_S))
    try:
        await asyncio.wait({passes, timer}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        timer.cancel()
        passes.cancel()
    await asyncio.wait({passes})
    if not passes.cancelled():
        passes.result()


async def _until_idle(run_pass: Callable[[], Awaitable[PassDone | Failure]]) -> None:
    while True:
        done = await run_pass()
        if isinstance(done, Failure) or done.events == 0:
            return
