"""The clock of the machine the agent runs on."""

import asyncio
import time


class SystemClock:
    """Reads the machine's own clock and waits in real time. Satisfies Clock."""

    def monotonic(self) -> float:
        """Give the seconds on a clock that keeps counting while a Mac sleeps."""
        # time.monotonic() stops while a Mac sleeps, so a deadline measured
        # with it would still look far away after the lid was shut for an hour.
        return time.clock_gettime(time.CLOCK_MONOTONIC_RAW)

    async def sleep(self, seconds: float) -> None:
        """Wait for the given number of seconds."""
        await asyncio.sleep(seconds)
