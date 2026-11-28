"""Keeps the Mac awake for as long as this process runs, for the bake-off's soak."""

import os
import subprocess
from collections.abc import Callable


def keep_awake() -> Callable[[], None]:
    """Start `caffeinate` on this process, and give how to stop it.

    `-w` ends it when this process ends, even if the stop is never called,
    so the Mac stays awake for exactly as long as the soak runs.
    """
    awake = subprocess.Popen(
        ["/usr/bin/caffeinate", "-dims", "-w", str(os.getpid())],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def stop() -> None:
        awake.terminate()
        awake.wait()

    return stop
