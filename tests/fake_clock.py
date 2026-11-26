"""A clock for tests. It never really waits, and a test can move it."""


class FakeClock:
    """Starts at zero. A sleep moves the time forward at once and is written down."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds
