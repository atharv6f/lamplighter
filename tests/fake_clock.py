"""A clock for tests. It never really waits, and a test can move it."""

from datetime import UTC, datetime, timedelta

# A made-up moment, so every saved time in a test is known in advance.
START = datetime(2026, 11, 26, 7, 35, 12, 345_678, tzinfo=UTC)


class FakeClock:
    """Starts at zero and at START. A sleep moves both forward at once and is noted.

    A test sets `seconds` to move the monotonic clock and `wall` to move the
    time of day. The two are separate, as on a real machine.
    """

    def __init__(self) -> None:
        self.seconds = 0.0
        self.wall = START
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.seconds

    def now(self) -> datetime:
        return self.wall

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.seconds += seconds
        self.wall += timedelta(seconds=seconds)
