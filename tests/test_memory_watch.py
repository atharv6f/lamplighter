"""The memory watch keeps the peaks, paces its reads, and counts the reads that fail."""

import asyncio
import os
import socket
import sys

import pytest

from lamplighter.app.memory_watch import (
    MemoryPeaks,
    MemoryWatch,
    phys_footprint,
    pressure_level,
    server_footprint,
)
from lamplighter.ports import Failure
from tests.fake_clock import FakeClock

GB = 1_000_000_000


class Readings:
    """Gives the values of a list in turn, and the last one after that."""

    def __init__(self, *values: int | None) -> None:
        self.values = list(values)
        self.asked = 0

    def __call__(self) -> int | None:
        self.asked += 1
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def test_the_peaks_are_kept_and_a_failed_read_is_counted() -> None:
    clock = FakeClock()
    footprint = Readings(20 * GB, 23 * GB, None, 21 * GB)
    pressure = Readings(1, 2, 4, 1)
    watch = MemoryWatch(footprint, pressure, clock)
    for gap in (0.0, 1.0, 1.5, 1.0):
        clock.seconds += gap
        watch.read_once()
    # The third read lost the footprint, so its pressure is not kept either.
    assert watch.peaks() == MemoryPeaks(
        footprint_bytes=23 * GB,
        pressure_level=2,
        reads=4,
        unread=1,
        longest_gap_ms=1_500,
    )


def test_nothing_read_gives_zero_peaks() -> None:
    watch = MemoryWatch(Readings(None), Readings(1), FakeClock())
    assert watch.peaks() == MemoryPeaks(0, 0, 0, 0, 0)


class YieldingClock(FakeClock):
    """A fake clock whose sleep also lets other tasks run, as a real one does."""

    async def sleep(self, seconds: float) -> None:
        await super().sleep(seconds)
        await asyncio.sleep(0)


def test_the_watch_reads_once_a_second_until_cancelled() -> None:
    clock = YieldingClock()
    footprint = Readings(GB)

    class SlowPressure(Readings):
        def __call__(self) -> int | None:
            # Each read takes 0.3 seconds, so the watch waits only 0.7 more.
            clock.seconds += 0.3
            return super().__call__()

    watch = MemoryWatch(footprint, SlowPressure(1), clock)

    async def run_for_a_while() -> None:
        task = asyncio.create_task(watch.watch())
        for _ in range(20):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_for_a_while())
    assert footprint.asked >= 3
    assert clock.slept[:3] == pytest.approx([0.7, 0.7, 0.7])
    assert watch.peaks().longest_gap_ms == 1_000


@pytest.mark.mac
def test_this_process_s_footprint_and_the_pressure_can_be_read() -> None:
    assert sys.platform == "darwin"
    footprint = phys_footprint(os.getpid())()
    assert footprint is not None and footprint > 1_000_000
    assert pressure_level() in (1, 2, 4)


@pytest.mark.mac
def test_the_footprint_of_the_server_on_a_port_is_read() -> None:
    assert sys.platform == "darwin"
    with socket.socket() as listening:
        listening.bind(("127.0.0.1", 0))
        listening.listen()
        port = listening.getsockname()[1]
        reader = server_footprint(port)
        assert not isinstance(reader, Failure), reader
        footprint = reader()
        assert footprint is not None and footprint > 1_000_000
    nobody = server_footprint(port)
    assert isinstance(nobody, Failure)
    assert f"Nothing listens on 127.0.0.1:{port}." == nobody.message
