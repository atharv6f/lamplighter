import asyncio

from tests.fake_clock import START
from tests.waiting_clock import WaitingClock, settle


def test_sleepers_wake_in_time_order_and_only_when_time_is_moved() -> None:
    async def scene() -> list[str]:
        clock = WaitingClock()
        woke: list[str] = []

        async def sleeper(name: str, seconds: float) -> None:
            await clock.sleep(seconds)
            woke.append(f"{name}@{clock.monotonic()}")

        tasks = [
            asyncio.create_task(sleeper("late", 5)),
            asyncio.create_task(sleeper("early", 2)),
        ]
        await settle()
        assert woke == []
        assert clock.sleepers == 2
        await clock.advance(1)
        assert woke == []
        await clock.advance(10)
        await asyncio.gather(*tasks)
        assert clock.monotonic() == 11
        return woke

    assert asyncio.run(scene()) == ["early@2.0", "late@5.0"]


def test_a_cancelled_sleeper_is_skipped_and_a_zero_sleep_returns() -> None:
    async def scene() -> None:
        clock = WaitingClock()
        task = asyncio.create_task(clock.sleep(3))
        await settle()
        task.cancel()
        await clock.advance(5)
        assert task.cancelled()
        await clock.sleep(0)
        assert clock.now() == START.replace(second=START.second + 5)

    asyncio.run(scene())
