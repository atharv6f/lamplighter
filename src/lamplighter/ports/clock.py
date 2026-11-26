"""The port for time and waiting, passed in so a test can control both."""

from typing import Protocol


class Clock(Protocol):
    """A clock that only moves forward, and a way to wait on it.

    Callers can rely on this: `monotonic` never goes backward, and its value
    only has meaning as a difference between two readings of the same clock.
    """

    def monotonic(self) -> float:
        """Give the current reading, in seconds."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Wait for the given number of seconds."""
        ...
