"""The Mac is kept awake by `caffeinate` on this process, until it is told to stop."""

import os
import subprocess
import sys

import pytest

from lamplighter.app.keep_awake import keep_awake


def caffeinated() -> list[str]:
    found = subprocess.run(
        ["/usr/bin/pgrep", "-f", f"caffeinate -dims -w {os.getpid()}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return found.stdout.split()


@pytest.mark.mac
def test_caffeinate_runs_on_this_process_until_stopped() -> None:
    assert sys.platform == "darwin"
    assert caffeinated() == []
    stop = keep_awake()
    try:
        assert len(caffeinated()) == 1
    finally:
        stop()
    assert caffeinated() == []
