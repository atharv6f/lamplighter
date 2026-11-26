"""A clock for tests of races. Sleepers really wait, until the test moves time."""

import asyncio
import heapq
from datetime import datetime, timedelta

from tests.fake_clock import START

# How many turns the event loop gets after each wake. A wake can start a
# chain of tasks that each wait their turn, and every store here answers
# without giving a turn, so a few dozen is plenty.
TURNS = 50


async def settle(turns: int = TURNS) -> None:
    """Give the event loop that many turns, so every task that can run, runs."""
    for _ in range(turns):
        await asyncio.sleep(0)


class WaitingClock:
    """Starts at zero and at START. `sleep` waits until `advance` passes its time.

    Sleepers wake one at a time, the earliest first, with the event loop
    given turns between each, so two sleepers wake in time order as they
    would on a real clock. A sleep of zero or less returns after one turn.
    """

    def __init__(self) -> None:
        self.seconds = 0.0
        self._sleepers: list[tuple[float, int, asyncio.Future[None]]] = []
        self._made = 0

    def monotonic(self) -> float:
        return self.seconds

    def now(self) -> datetime:
        return START + timedelta(seconds=self.seconds)

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        woken = asyncio.get_running_loop().create_future()
        self._made += 1
        heapq.heappush(self._sleepers, (self.seconds + seconds, self._made, woken))
        await woken

    @property
    def sleepers(self) -> int:
        """How many sleeps are waiting now."""
        return sum(1 for _, _, woken in self._sleepers if not woken.done())

    async def advance(self, seconds: float) -> None:
        """Move time on, waking each sleeper whose time has come, in order."""
        target = self.seconds + seconds
        await settle()
        while self._sleepers and self._sleepers[0][0] <= target:
            wake_at, _, woken = heapq.heappop(self._sleepers)
            if woken.done():
                continue
            self.seconds = max(self.seconds, wake_at)
            woken.set_result(None)
            await settle()
        self.seconds = target
        await settle()
