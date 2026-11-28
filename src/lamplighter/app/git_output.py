"""Runs git on one checkout, read-only, and gives what it printed."""

import os
import subprocess
from pathlib import Path

_GIT_SECONDS = 10


def git_output(root: Path, *args: str) -> bytes | None:
    """Give what `git ARGS` printed in `root`, or None when it failed or took too long.

    Nothing of the caller's that points git elsewhere, such as GIT_DIR, is
    passed on, so the answer is about the checkout at `root`. Optional locks
    are off, so reading the state never writes to the checkout.
    """
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=root,
            env=env,
            capture_output=True,
            timeout=_GIT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return done.stdout
