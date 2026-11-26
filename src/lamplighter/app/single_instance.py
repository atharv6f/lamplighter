"""The lock that lets one process at a time use a store."""

import fcntl
import os
from pathlib import Path


def lock_store(folder: Path) -> int | None:
    """Make the store folder if it is missing, then lock it for this process.

    Gives the open lock file, which stays open and locked until the process
    ends, however it ends. Gives None when another process holds the lock.
    The folder is made first, with mode 0700, because the lock file lives
    in it.
    """
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    held = os.open(folder / "lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(held)
        return None
    return held
