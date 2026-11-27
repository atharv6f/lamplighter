"""When the chat leaves at once, the commands it started do not live on.

A command runs in a session of its own, so the terminal closing or a kill
of the chat would not reach it. The chat kills each one itself first.
"""

import contextlib
import io
import os
import signal
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from lamplighter.adapters import CountingIds, FakeExecutor, TerminalChannel
from lamplighter.app.chat import LEAVE_CODE, LEAVING, leave_now
from lamplighter.ports import ExecResult
from tests.contract.test_executor import is_alive
from tests.fake_clock import FakeClock
from tests.group_kill import kill_group
from tests.pty_terminal import SECONDS, piped


@pytest.fixture
def chat(tmp_path: Path) -> Iterator[subprocess.Popen[bytes]]:
    """A chat running the long command, and nothing of it left after the test."""
    child = piped(tmp_path / "store", "long_command", b"Run a long command.\n")
    try:
        yield child
    finally:
        kill_group(child)
        written = tmp_path / "store-work" / "pids.txt"
        if written.exists():
            # The shell leads the command's own process group.
            shell, _ = written.read_text().split()
            # A group already gone, or on macOS one whose processes are all
            # on their way out, refuses the signal, and either is fine here.
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(int(shell), signal.SIGKILL)


def _pids(work: Path) -> tuple[int, int]:
    """Wait for the long command to write its own id and its child's."""
    written = work / "pids.txt"
    deadline = time.monotonic() + SECONDS
    while time.monotonic() < deadline:
        if written.exists() and written.read_text().endswith("\n"):
            shell, child = written.read_text().split()
            return int(shell), int(child)
        time.sleep(0.05)
    raise AssertionError("the long command never started")


@pytest.mark.parametrize(
    "ending", [signal.SIGTERM, signal.SIGHUP], ids=["kill", "closed terminal"]
)
def test_a_kill_or_a_closed_terminal_kills_the_running_command_first(
    tmp_path: Path, chat: subprocess.Popen[bytes], ending: signal.Signals
) -> None:
    shell, child = _pids(tmp_path / "store-work")
    assert is_alive(shell) and is_alive(child)
    os.kill(chat.pid, ending)
    _, errors = chat.communicate(timeout=SECONDS)
    assert chat.returncode == 128 + ending, errors
    assert not is_alive(shell)
    assert not is_alive(child)


def test_a_command_outlives_a_kill_the_chat_cannot_answer(
    tmp_path: Path, chat: subprocess.Popen[bytes]
) -> None:
    # This is why the chat kills its commands itself: nothing else would.
    shell, child = _pids(tmp_path / "store-work")
    os.killpg(chat.pid, signal.SIGKILL)
    chat.wait()
    assert is_alive(shell) and is_alive(child)


class _Left(Exception):
    """Stands in for leaving the process, so the test can look after it."""


def test_leaving_at_once_kills_every_command_then_leaves_with_its_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def left(code: int) -> None:
        raise _Left(code)

    ended = ExecResult(0, None, b"", False, False, False)
    executor = FakeExecutor(tmp_path, lambda _: ended)
    screen = io.StringIO()
    terminal = TerminalChannel("sess-a", FakeClock(), CountingIds(), 0, screen)
    monkeypatch.setattr(os, "_exit", left)
    with pytest.raises(_Left) as leaving:
        leave_now(terminal, executor)
    assert leaving.value.args == (LEAVE_CODE,)
    assert executor.killed_all == 1
    assert LEAVING in screen.getvalue()
