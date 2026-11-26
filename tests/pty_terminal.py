"""A chat child in a pty, which a test or a timing script types into and reads.

A pty is a pair of file descriptors that behaves like a terminal. The child
makes it its own terminal, so Ctrl-C typed into it reaches the child as a
signal, as in a real terminal.
"""

import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]
CTRL_C = b"\x03"
CTRL_D = b"\x04"
# Far above what any of these takes, and far below the 60 seconds a tool or
# a model call here would take if a stop did not cut it.
SECONDS = 30.0


def _command(folder: Path, behaviour: str, session: str | None) -> list[str]:
    command = [sys.executable, "-m", "tests.chat_child", str(folder), behaviour]
    return command + ([session] if session else [])


def _environment() -> dict[str, str]:
    return {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


class Terminal:
    """A chat child whose input and output are a pty the test holds."""

    def __init__(
        self, folder: Path, behaviour: str, session: str | None = None
    ) -> None:
        self._screen, child_side = os.openpty()
        self.child = subprocess.Popen(
            _command(folder, behaviour, session),
            cwd=ROOT,
            env=_environment(),
            stdin=child_side,
            stdout=child_side,
            stderr=child_side,
            start_new_session=True,
        )
        os.close(child_side)
        self.seen = ""

    def type(self, data: bytes) -> None:
        os.write(self._screen, data)

    def wait_for(self, text: str, after: int = 0) -> int:
        """Read until `text` shows after position `after`. Give where it ends."""
        deadline = time.monotonic() + SECONDS
        while (found := self.seen.find(text, after)) < 0:
            left = deadline - time.monotonic()
            assert left > 0, f"{text!r} never showed. The screen:\n{self.seen}"
            if not self._read(left):
                raise AssertionError(f"{text!r} never showed. The screen:\n{self.seen}")
        return found + len(text)

    def _read(self, seconds: float) -> bool:
        """Read what has come within `seconds`. False once the child's side closed."""
        ready, _, _ = select.select([self._screen], [], [], seconds)
        if not ready:
            return True
        try:
            data = os.read(self._screen, 4096)
        except OSError:
            # The child has gone and closed its side.
            return False
        self.seen += data.decode("utf-8", errors="replace").replace("\r\n", "\n")
        return bool(data)

    def exit_code(self) -> int:
        """Wait for the child to end, read what is left, and give its code."""
        code = self.child.wait(SECONDS)
        while select.select([self._screen], [], [], 0.1)[0] and self._read(0):
            pass
        return code

    def close(self) -> None:
        if self.child.poll() is None:
            os.killpg(self.child.pid, signal.SIGKILL)
            self.child.wait()
        os.close(self._screen)

    def session(self) -> str:
        end = self.wait_for("\n", self.wait_for("Session "))
        return self.seen[: end - 1].rsplit("Session ", 1)[1]


def piped(folder: Path, behaviour: str, typed: bytes) -> subprocess.Popen[bytes]:
    """Start a chat child whose input is a pipe holding `typed`, then closed."""
    child = subprocess.Popen(
        _command(folder, behaviour, None),
        cwd=ROOT,
        env=_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert child.stdin is not None
    child.stdin.write(typed)
    child.stdin.close()
    return child
