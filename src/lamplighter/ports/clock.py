"""The port for time and waiting, passed in so a test can control both."""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """A clock that only moves forward, the time of day, and a way to wait.

    Callers can rely on this: `monotonic` never goes backward, and its value
    only has meaning as a difference between two readings of the same clock.
    `now` is the wall clock in UTC. It can jump when the machine's time is
    set, so it is for stamping records and never for ordering them.
    """

    def monotonic(self) -> float:
        """Give the current reading, in seconds."""
        ...

    def now(self) -> datetime:
        """Give the wall clock time, in UTC."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Wait for the given number of seconds."""
        ...


def utc_text(moment: datetime) -> str:
    """Give the one text form of a saved time, such as 2026-11-26T07:35:12.345Z.

    It is always 24 characters, so two times compare correctly as text. The
    milliseconds are cut, never rounded, so a time is never moved forward.
    """
    utc = moment.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"
