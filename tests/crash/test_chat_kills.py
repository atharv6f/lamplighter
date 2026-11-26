"""Kills the real chat process with SIGKILL at random moments, then checks the store.

After the last kill the chat is let finish. Then every run has ended,
every call has one result, no tool ran more often than it was meant to,
every message is in the log once, and every reply was shown in order,
with any second copy labelled. The long run is run by hand:

    uv run pytest -m crash -s tests/crash/test_chat_kills.py
"""

from pathlib import Path

import pytest

from tests.crash.chat_runner import run_chat_kills


def test_ten_kills_of_the_chat_leave_its_work_whole(tmp_path: Path) -> None:
    result = run_chat_kills(tmp_path, kills=10, seed=1)
    assert result.kills == 10, result
    assert result.tool_starts >= 10, result


@pytest.mark.crash
def test_two_hundred_kills_of_the_chat_leave_its_work_whole(tmp_path: Path) -> None:
    result = run_chat_kills(tmp_path, kills=200, seed=2)
    print(f"\n{result}")
    assert result.kills == 200, result
    assert result.tool_starts >= 200, result
