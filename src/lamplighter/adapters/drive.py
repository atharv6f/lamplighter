"""Forces a file or a folder to the drive, with the Mac's full flush where it exists.

Shared by the adapters that write files of their own. It implements no port.
"""

import errno
import fcntl
import os
from pathlib import Path

# macOS only. A plain fsync there hands the data to the drive and does not
# make the drive write it down. Python's os.fsync is the plain one.
_FULL_FLUSH = getattr(fcntl, "F_FULLFSYNC", None)


def flush(descriptor: int) -> None:
    """Force an open file's data to the drive."""
    if _FULL_FLUSH is not None:
        try:
            fcntl.fcntl(descriptor, _FULL_FLUSH)
            return
        except OSError:
            # Some file systems refuse it, and Apple does not say whether a
            # folder takes it. SQLite falls back to a plain sync in the same
            # case, and so does this.
            pass
    os.fsync(descriptor)


def flush_folder(folder: Path) -> None:
    """Force a folder's names to the drive, so a new name survives a power cut."""
    descriptor = os.open(folder, os.O_RDONLY)
    try:
        flush(descriptor)
    finally:
        os.close(descriptor)


def write_flushed(path: Path, data: bytes, flags: int = os.O_EXCL) -> None:
    """Write a new file with mode 0600 and force it to the drive before returning.

    `flags` is added to the open flags. The default refuses a file that is
    already there. Pass os.O_TRUNC to replace one.
    """
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(descriptor, view) :]
        flush(descriptor)
    finally:
        os.close(descriptor)


def is_full(error: OSError) -> bool:
    """Say whether an error means the drive, or this user's share of it, is full."""
    return error.errno in (errno.ENOSPC, errno.EDQUOT)
