"""The only way a tool reaches a file: the path rule, and the guarded open.

The rule is text work alone, so nothing a command planted on the drive can
change its answer. The open walks from a handle on the work folder, one
part at a time, and follows no symbolic link at any depth.
"""

import contextlib
import errno
import os
import stat
from dataclasses import dataclass

# A file bigger than this is not read whole by any tool.
MOST_BYTES = 8_388_608
_FOLDER_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# No blocking, so a named pipe is opened at once and then refused.
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
# Linux says "not a folder" for a link opened as a folder, macOS says "loop".
_MAYBE_A_LINK = (errno.ELOOP, errno.ENOTDIR)


@dataclass(frozen=True, slots=True)
class Refused:
    """A path a file tool does not use, with the text for the model."""

    text: str


@dataclass(frozen=True, slots=True)
class WorkFolder:
    """The work folder: its resolved path, and a handle opened on it once."""

    root: str
    fd: int


def open_work_folder(root: str) -> WorkFolder:
    """Open the handle on the work folder that every walk starts from."""
    return WorkFolder(root=root, fd=os.open(root, _FOLDER_FLAGS))


def path_parts(path: str, root: str) -> tuple[str, ...] | Refused:
    """Turn the model's path into the parts below the work folder, or refuse it.

    A relative path is joined to the work folder. `.`, empty and `..` parts
    are resolved in the text alone.
    """
    if "\0" in path or not _is_utf8(path):
        return Refused("That is not a valid path.")
    joined = path if path.startswith("/") else f"{root}/{path}"
    parts: list[str] = []
    for part in joined.split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    base = [part for part in root.split("/") if part]
    if parts[: len(base)] != base:
        return Refused(
            f"The path {path} leaves the work folder. File tools work only inside it."
        )
    return tuple(parts[len(base) :])


def target_of(parts: tuple[str, ...]) -> str:
    """Give the path below the work folder, with `/` between its parts."""
    return "/".join(parts)


def open_folder(
    work: WorkFolder, parts: tuple[str, ...], path: str
) -> int | None | Refused:
    """Open the folder that `parts` name, from the work folder down.

    None means a part is missing. The caller closes the handle it gets.
    """
    fd = os.dup(work.fd)
    for part in parts:
        try:
            inner = os.open(part, _FOLDER_FLAGS, dir_fd=fd)
        except OSError as error:
            refused = _not_opened(error, fd, part, path)
            os.close(fd)
            return None if error.errno == errno.ENOENT else refused
        os.close(fd)
        fd = inner
    return fd


def make_folders(work: WorkFolder, parts: tuple[str, ...], path: str) -> int | Refused:
    """Open the folder that `parts` name, making each missing one with mode 0755."""
    fd = os.dup(work.fd)
    for part in parts:
        try:
            inner = _open_or_make(fd, part)
        except OSError as error:
            refused = _not_opened(error, fd, part, path)
            os.close(fd)
            return refused
        os.close(fd)
        fd = inner
    return fd


def open_file(folder: int, name: str, path: str) -> int | None | Refused:
    """Open the regular file `name` in an open folder. None means there is none."""
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=folder)
    except OSError as error:
        if error.errno == errno.ENOENT:
            return None
        return _not_opened(error, folder, name, path)
    mode = os.fstat(fd).st_mode
    if stat.S_ISREG(mode):
        return fd
    os.close(fd)
    if stat.S_ISDIR(mode):
        return Refused(f"{path} is a folder, not a file.")
    return Refused(f"{path} is not a regular file.")


def open_existing(
    work: WorkFolder, parts: tuple[str, ...], path: str
) -> int | None | Refused:
    """Open the regular file that `parts` name. None means there is none."""
    if not parts:
        return Refused(f"{path} is a folder, not a file.")
    folder = open_folder(work, parts[:-1], path)
    if not isinstance(folder, int):
        return folder
    try:
        return open_file(folder, parts[-1], path)
    finally:
        os.close(folder)


def read_up_to(fd: int, most: int) -> bytes:
    """Read an open file, at most one byte past `most`, and close it.

    A result longer than `most` says the file is too large, even if it grew
    after its size was asked.
    """
    chunks: list[bytes] = []
    left = most + 1
    try:
        while left > 0 and (chunk := os.read(fd, min(left, 1_048_576))):
            chunks.append(chunk)
            left -= len(chunk)
    finally:
        os.close(fd)
    return b"".join(chunks)


def _open_or_make(fd: int, part: str) -> int:
    try:
        return os.open(part, _FOLDER_FLAGS, dir_fd=fd)
    except FileNotFoundError:
        # Another writer may make it first, which is as good.
        with contextlib.suppress(FileExistsError):
            os.mkdir(part, 0o755, dir_fd=fd)
        return os.open(part, _FOLDER_FLAGS, dir_fd=fd)


def _not_opened(error: OSError, folder: int, part: str, path: str) -> Refused:
    if error.errno in _MAYBE_A_LINK and _is_link(folder, part):
        return Refused(
            f"The path {path} goes through a symbolic link, which this tool"
            " does not follow."
        )
    return Refused(f"{path} could not be opened: {error.strerror}.")


def _is_link(folder: int, part: str) -> bool:
    # Only for the text: the open has already refused it.
    try:
        return stat.S_ISLNK(os.stat(part, dir_fd=folder, follow_symlinks=False).st_mode)
    except OSError:
        return False


def _is_utf8(path: str) -> bool:
    try:
        path.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
