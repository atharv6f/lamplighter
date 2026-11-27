"""The runner's own rules: the check that comes first, and how a kill sets the flags."""

import asyncio
import contextlib
import os
import signal
from datetime import datetime
from pathlib import Path

from lamplighter.adapters import SystemClock
from lamplighter.adapters.command_run import run_command
from lamplighter.ports import ExecResult, Failure
from tests.fake_clock import START

PATH = {"PATH": "/usr/bin:/bin"}


async def nothing_to_check() -> Failure | None:
    return None


def test_a_failed_first_check_is_given_back_and_nothing_starts(tmp_path: Path) -> None:
    refusal = Failure("unavailable", "none", "made-up: no wall", "test")

    async def refuse() -> Failure | None:
        return refusal

    marker = tmp_path / "marker"
    result = asyncio.run(
        run_command(
            ("/usr/bin/touch", str(marker)),
            str(tmp_path),
            PATH,
            10_000,
            1_000,
            SystemClock(),
            refuse,
        )
    )
    assert result is refusal
    assert not marker.exists()


def test_a_cancel_during_the_first_check_does_not_reach_it_and_kills_at_once(
    tmp_path: Path,
) -> None:
    release = asyncio.Event()
    checked: list[str] = []

    async def slow_check() -> Failure | None:
        try:
            await release.wait()
        except asyncio.CancelledError:
            checked.append("cancelled")
            raise
        checked.append("ended")
        return None

    async def scene() -> ExecResult | Failure:
        call = asyncio.ensure_future(
            run_command(
                ("/bin/sleep", "30"),
                str(tmp_path),
                PATH,
                10_000,
                1_000,
                SystemClock(),
                slow_check,
            )
        )
        await asyncio.sleep(0.05)
        call.cancel()
        await asyncio.sleep(0.05)
        call.cancel()
        release.set()
        return await asyncio.wait_for(call, 5)

    result = asyncio.run(scene())
    assert checked == ["ended"]
    assert isinstance(result, ExecResult)
    assert result.cancelled and result.signal == signal.SIGKILL


class DeathAtTheLimit:
    """A clock whose time limit passes only once another signal has ended the command.

    The sleep holds the event loop while it signals and waits, so the loop
    has not yet seen the command end when the limit passes. That is the
    instant between a limit and the kill.
    """

    def __init__(self, pid_file: Path) -> None:
        self.pid_file = pid_file

    def monotonic(self) -> float:
        return 0.0

    def now(self) -> datetime:
        return START

    async def sleep(self, seconds: float) -> None:
        while not self.pid_file.exists() or not self.pid_file.read_text():
            await asyncio.sleep(0.01)
        pid = int(self.pid_file.read_text())
        os.kill(pid, signal.SIGTERM)
        # Wait for the end without reaping, so the loop still has it to see.
        # Where a thread of asyncio's reaps at once, it is already gone.
        with contextlib.suppress(ChildProcessError):
            os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)


def test_a_command_that_a_limit_finds_dead_keeps_how_it_ended(tmp_path: Path) -> None:
    script = "echo $$ > pid; exec sleep 30"
    clock = DeathAtTheLimit(tmp_path / "pid")
    result = asyncio.run(
        run_command(
            ("/bin/sh", "-c", script),
            str(tmp_path),
            {"PATH": "/usr/bin:/bin"},
            10_000,
            1_000,
            clock,
            nothing_to_check,
        )
    )
    assert isinstance(result, ExecResult)
    assert (result.exit_code, result.signal) == (None, signal.SIGTERM)
    assert not result.timed_out and not result.cancelled
