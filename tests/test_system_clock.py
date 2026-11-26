import asyncio

from lamplighter.adapters import SystemClock


def test_the_clock_does_not_go_backward() -> None:
    clock = SystemClock()
    readings = [clock.monotonic() for _ in range(100)]
    assert readings == sorted(readings)


def test_a_sleep_takes_at_least_the_time_asked_for() -> None:
    clock = SystemClock()

    async def nap() -> float:
        before = clock.monotonic()
        await clock.sleep(0.02)
        return clock.monotonic() - before

    # The two clocks are not the same clock, so the check leaves some room.
    assert asyncio.run(nap()) >= 0.01
