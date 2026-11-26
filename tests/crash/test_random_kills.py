"""Kills a process that writes to a real store at random moments.

After every kill the store must be whole: no damage, no gap in seq, no half
batch, and nothing lost that was reported saved. The short run is part of
the default test run. The long one is run by hand:

    uv run pytest -m crash -s
"""

from pathlib import Path

import pytest

from tests.crash.runner import run_random_kills


def test_ten_kills_at_random_moments_leave_the_store_whole(tmp_path: Path) -> None:
    result = run_random_kills(tmp_path, kills=10, seed=1)
    assert result.batches >= 20, f"the kills came too early to mean anything: {result}"


@pytest.mark.crash
def test_two_hundred_kills_at_random_moments_leave_the_store_whole(
    tmp_path: Path,
) -> None:
    result = run_random_kills(tmp_path, kills=200, seed=2)
    print(f"\n{result}")
    assert result.batches >= 1_000, f"too few batches were saved: {result}"
    assert result.kills_after_a_write >= 100, f"the kills came too early: {result}"
