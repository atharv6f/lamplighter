"""The hash that names a task set and its split, from the files' plain values."""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from lamplighter.ports import canonical_json


@dataclass(frozen=True, slots=True)
class TaskFile:
    """One file of the task set.

    `path` has forward slashes. For a symbolic link, `data` is the text of
    its target and `link` is true: a link is never followed.
    """

    path: str
    data: bytes
    executable: bool
    link: bool


def task_set_hash(files: Iterable[TaskFile]) -> str:
    """Give the SHA-256 of the canonical JSON of one entry per file, by path.

    A changed byte, a changed executable bit, a new file and a link with
    another target each give a new hash. A link is never executable.
    """
    entries = [
        {
            "path": f.path,
            "sha256": hashlib.sha256(f.data).hexdigest(),
            "executable": f.executable and not f.link,
            "link": f.link,
        }
        for f in sorted(files, key=lambda f: f.path)
    ]
    return hashlib.sha256(canonical_json(entries).encode("utf-8")).hexdigest()
