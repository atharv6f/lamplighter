"""Measures what the agent left in its work folder, before it is copied for a check."""

import os
import stat
from pathlib import Path

from lamplighter.bench import BoundWord

# The copy for the check writes each file's stated size, so the stated size
# is added up and not the space it takes.
MOST_STATED_BYTES = 1 << 30
MOST_ENTRIES = 50_000
MOST_DEPTH = 100


def out_of_bounds(folder: Path) -> BoundWord | None:
    """Give the first bound that what is under `folder` passes, or None.

    It follows no link, keeps its own list of folders still to read, and
    stops at the first bound: the stated sizes of the regular files, the
    count of files, folders and links together, the depth of folders, or a
    folder that it may not list. `folder` itself is depth 0.
    """
    entries = 0
    stated = 0
    waiting = [(folder, 0)]
    while waiting:
        place, depth = waiting.pop()
        try:
            listed = list(os.scandir(place))
        except OSError:
            return "unreadable"
        for entry in listed:
            entries += 1
            if entries > MOST_ENTRIES:
                return "too_many_entries"
            found = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(found.st_mode):
                if depth + 1 > MOST_DEPTH:
                    return "too_deep"
                waiting.append((Path(entry.path), depth + 1))
            elif stat.S_ISREG(found.st_mode):
                stated += found.st_size
                if stated > MOST_STATED_BYTES:
                    return "too_large"
    return None
