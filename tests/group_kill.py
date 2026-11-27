"""Kill a child process that leads its own group, and wait for it."""

import contextlib
import os
import signal
import subprocess


def kill_group(child: subprocess.Popen[bytes]) -> None:
    """Kill the child's whole group with SIGKILL, if it still runs, then reap it."""
    if child.poll() is None:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except PermissionError:
            # macOS refuses to signal a group that holds only processes on
            # their way out. The child itself can still be killed.
            with contextlib.suppress(ProcessLookupError):
                child.kill()
    child.wait()
