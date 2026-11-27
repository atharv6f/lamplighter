"""A chat child in a pty, which a test or a timing script types into and reads.

A pty is a pair of file descriptors that behaves like a terminal. The child
makes it its own terminal, so Ctrl-C typed into it reaches the child as a
signal, as in a real terminal.
"""

import os
import select
import subprocess
import sys
import time
from pathlib import Path

from tests.group_kill import kill_group

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
        end = self.find(text, after, SECONDS)
        assert end is not None, f"{text!r} never showed. The screen:\n{self.seen}"
        return end

    def find(self, text: str, after: int, seconds: float) -> int | None:
        """Read for up to `seconds` until `text` shows after `after`. Give its end."""
        deadline = time.monotonic() + seconds
        while (found := self.seen.find(text, after)) < 0:
            left = deadline - time.monotonic()
            if left <= 0 or not self._read(left):
                return None
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
        """Read until the child's side of the pty closes, then give its exit code."""
        # On macOS, what the child wrote and the test had not read yet is
        # dropped when the child's side closes. So the reading goes on while
        # the child ends, and not after it.
        deadline = time.monotonic() + SECONDS
        while (left := deadline - time.monotonic()) > 0 and self._read(left):
            pass
        return self.child.wait(SECONDS)

    def close(self) -> None:
        kill_group(self.child)
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
