"""Runs passes for as long as a chat lives: when to pass, when to wait, when to stop."""

from collections.abc import Awaitable, Callable

from lamplighter.core.trace_projector import PassDone
from lamplighter.ports import Clock, Failure

# Kinds that say the same pass may work later: the store is down, slow or busy.
MAY_PASS = frozenset({"timeout", "unavailable", "throttled", "transient"})
IDLE_WAIT_S = 1.0
FIRST_WAIT_S = 1.0
LONGEST_WAIT_S = 60.0


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
