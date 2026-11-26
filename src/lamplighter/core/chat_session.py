"""The driver: carries one session on, and sends its replies, until input ends."""

import asyncio
from collections.abc import Callable

from lamplighter.core.delivery import deliver_due
from lamplighter.core.run_deps import Deps
from lamplighter.core.run_spec import RunSpec
from lamplighter.core.run_turn import run_turn
from lamplighter.ports import Channel, Failure


class Wake:
    """The driver's wake flag, and whether the input has ended."""

    def __init__(self) -> None:
        """Start with the flag down and the input open."""
        self._flag = asyncio.Event()
        self._ended = False

    def wake(self) -> None:
        """Raise the flag: something was saved for the driver to look at."""
        self._flag.set()

    def end_input(self) -> None:
        """Note that no more input will come, and raise the flag."""
        self._ended = True
        self._flag.set()

    def clear(self) -> None:
        """Take the flag down, before the driver looks at what is saved."""
        self._flag.clear()

    @property
    def raised(self) -> bool:
        """Whether the flag is up."""
        return self._flag.is_set()

    @property
    def input_ended(self) -> bool:
        """Whether the input has ended."""
        return self._ended

    async def wait(self) -> None:
        """Return once the flag is up."""
        await self._flag.wait()


async def drive(
    session_id: str,
    spec: RunSpec,
    deps: Deps,
    channel: Channel,
    wake: Wake,
    show_prompt: Callable[[], None],
) -> Failure | None:
    """Carry the session on and send its replies, until it is idle and input ended.

    Each trip round clears the flag, sends what is due, and calls
    `run_turn`. A run that ended sends the driver round again, so its reply
    goes out at the start of the next trip. When `run_turn` finds nothing
    to do, the prompt is shown and the driver waits for the flag. The flag
    is cleared before `run_turn` and not after, so a message saved during
    its last pass sends the driver round once more. The first trip sends
    any reply a kill left unsent before it carries on a run that was cut
    off. A `Failure` ends it.
    """
    while True:
        wake.clear()
        failed = await deliver_due(session_id, deps.outbox, channel)
        if failed is not None:
            return failed
        turned = await run_turn(session_id, spec, deps)
        if isinstance(turned, Failure):
            return turned
        if turned is not None or wake.raised:
            continue
        if wake.input_ended:
            return None
        show_prompt()
        await wake.wait()
