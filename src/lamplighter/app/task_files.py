"""Lists and reads the files of the bench's tasks and its split, as git lists them."""

import os
import stat
from pathlib import Path

from lamplighter.app.git_output import git_output
from lamplighter.bench import TaskFile
from lamplighter.ports import Failure

SOURCE = "task_files"
TASKS = "bench/tasks"
SPLIT = "bench/split.toml"


def task_files(root: Path) -> list[TaskFile] | Failure:
    """Give every file under `bench/tasks` and `bench/split.toml`, tracked or new.

    What `.gitignore` leaves out is not listed, nor is a tracked file that
    was deleted. Each path is from `root`, with forward slashes. A symbolic
    link is read as a link, with the text it points to, and never followed.
    """
    listed = _listed(root, "--cached", "--others", "--exclude-standard")
    if isinstance(listed, Failure):
        return listed
    files: list[TaskFile] = []
    try:
        for path in listed:
            # A tracked file deleted from the folder is listed by git, and
            # is not part of the task set any more.
            if os.path.lexists(root / path):
                files.append(_read(root, path))
    except OSError as error:
        return _unavailable(f"A task file could not be read: {error}.")
    return files


def ignored_task_files(root: Path) -> list[str] | Failure:
    """Give the files under `bench/tasks` that `.gitignore` leaves out.

    Such a file would drop out of the task set hash without a word, so any
    one of them is a fault of the task set.
    """
    return _listed(root, "--others", "--ignored", "--exclude-standard")


def _listed(root: Path, *which: str) -> list[str] | Failure:
    found = git_output(root, "ls-files", *which, "-z", "--", TASKS, SPLIT)
    if found is None:
        return _unavailable(f"git could not list the task files in {root}.")
    try:
        text = found.decode("utf-8")
    except UnicodeDecodeError:
        return _unavailable("A task file's name is not UTF-8.")
    # A file that is both tracked and new to the index is listed twice.
    return sorted({path for path in text.split("\0") if path})


def _read(root: Path, path: str) -> TaskFile:
    full = root / path
    mode = full.lstat().st_mode
    if stat.S_ISLNK(mode):
        target = os.readlink(full).encode("utf-8", errors="surrogateescape")
        return TaskFile(path=path, data=target, executable=False, link=True)
    executable = bool(mode & stat.S_IXUSR)
    return TaskFile(path, full.read_bytes(), executable, link=False)


def _unavailable(message: str) -> Failure:
    return Failure(kind="unavailable", effect="none", message=message, source=SOURCE)
