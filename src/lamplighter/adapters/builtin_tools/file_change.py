"""How a file tool changes a file: its plan, the replace and the check.

A replace writes a new file beside the target and renames it over the
target, so a kill leaves the old file or the new one, never a mix.
"""

import contextlib
import hashlib
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass

from lamplighter.adapters.builtin_tools.work_paths import (
    MOST_BYTES,
    Refused,
    WorkFolder,
    make_folders,
    open_existing,
    open_file,
    path_parts,
    read_up_to,
    target_of,
)
from lamplighter.ports import BlobStore, Failure, JsonObject, Plan, ToolResult

_SOURCE = "ToolProvider/builtin"
# The name of an unfinished write, beside its target.
NEW_SUFFIX = ".lamplighter-new"
# A file name on macOS is at most 255 bytes, and the suffix takes 16.
MOST_NAME_BYTES = 255 - len(NEW_SUFFIX)
# Creating with "exclusive" also refuses a link under the name: POSIX says
# the open then fails as if a file were there.
_NEW_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC

type Content = Callable[[bytes | None], bytes]


@dataclass(frozen=True, slots=True)
class Seen:
    """A file a change will replace: its target, its bytes and its size.

    `old` is None when there is no file yet, or no folder above it. It holds
    at most one byte past the most any tool reads, and `size` is the size
    the file had when it was opened.
    """

    target: str
    old: bytes | None
    size: int


def look_up(work: WorkFolder, path: str) -> Seen | Refused:
    """Find the file a change to `path` would replace. It blocks.

    It refuses a path the file tools do not use and a name they do not write.
    """
    parts = path_parts(path, work.root)
    if isinstance(parts, Refused):
        return parts
    refused = name_refusal(parts, path)
    if refused is not None:
        return refused
    found = open_existing(work, parts, path)
    if isinstance(found, Refused):
        return found
    if found is None:
        return Seen(target_of(parts), None, 0)
    size = os.fstat(found).st_size
    return Seen(target_of(parts), read_up_to(found, MOST_BYTES), size)


def check_hash(data: bytes | None) -> str | None:
    """Give the check hash of a file's bytes, or None when there is no file.

    Five bytes go in front, so the hash is never the name the blob store
    gives the same bytes.
    """
    if data is None:
        return None
    return hashlib.sha256(b"file\x00" + data).hexdigest()


def name_refusal(parts: tuple[str, ...], path: str) -> Refused | None:
    """Refuse a file name that a write could not put its unfinished file beside."""
    if not parts:
        return Refused(f"{path} is a folder, not a file.")
    name = parts[-1]
    if name.lower().endswith(NEW_SUFFIX):
        return Refused(
            f"{path} ends in {NEW_SUFFIX}, a name this tool keeps for its own"
            " unfinished writes. Choose another name."
        )
    size = len(name.encode("utf-8"))
    if size > MOST_NAME_BYTES:
        return Refused(
            f"The file name in {path} is {size} bytes long. This tool writes"
            f" names of up to {MOST_NAME_BYTES} bytes."
        )
    return None


async def file_plan(
    work: WorkFolder, blobs: BlobStore, target: str, old: bytes | None, new: bytes
) -> Plan | Failure:
    """Save the old bytes, and give the plan that turns `old` into `new`."""
    probe: JsonObject = {
        "kind": "file_hash",
        "v": 1,
        "root": work.root,
        "path": target,
        "before": check_hash(old),
        "after": check_hash(new),
    }
    if old is None:
        undo: JsonObject = {
            "kind": "remove_file",
            "v": 1,
            "root": work.root,
            "path": target,
        }
        return Plan(target=target, probe=probe, undo=undo)
    saved = await blobs.put(old, "application/octet-stream")
    if isinstance(saved, Failure):
        return saved
    blob: JsonObject = {
        "sha256": saved.sha256,
        "size": saved.size,
        "media_type": saved.media_type,
    }
    undo = {
        "kind": "restore_file",
        "v": 1,
        "root": work.root,
        "path": target,
        "blob": blob,
    }
    return Plan(target=target, probe=probe, undo=undo)


def replace_file(
    work: WorkFolder, path: str, probe: JsonObject, content: Content, done: str
) -> ToolResult | Failure:
    """Carry out a plan: make the folders, compare, write beside, rename.

    `content` makes the new bytes from the file as it is, which the
    comparison has shown is the file the plan saw. `done` is the text when
    the file was replaced. It blocks, so it runs in a worker thread.
    """
    if probe.get("root") != work.root:
        return _error(
            f"The plan for {path} was made in another work folder. Nothing was"
            " written. Read the file again and repeat the call."
        )
    parts = path_parts(path, work.root)
    if isinstance(parts, Refused):
        return _error(parts.text)
    folder = make_folders(work, parts[:-1], path)
    if isinstance(folder, Refused):
        return _error(folder.text)
    try:
        return _replace_in(folder, parts[-1], path, probe, content, done)
    finally:
        os.close(folder)


def file_check(work: WorkFolder, probe: JsonObject) -> bool | None:
    """Say whether the file holds the plan's new bytes, its old ones, or neither.

    It blocks, so it runs in a worker thread.
    """
    if probe.get("kind") != "file_hash" or probe.get("v") != 1:
        return None
    path = probe.get("path")
    if probe.get("root") != work.root or not isinstance(path, str):
        return None
    parts = path_parts(path, work.root)
    if isinstance(parts, Refused):
        return None
    found = open_existing(work, parts, path)
    if isinstance(found, Refused):
        return None
    now = None if found is None else read_up_to(found, MOST_BYTES)
    return _compared(check_hash(now), probe)


def _replace_in(
    folder: int, name: str, path: str, probe: JsonObject, content: Content, done: str
) -> ToolResult | Failure:
    found = open_file(folder, name, path)
    if isinstance(found, Refused):
        return _error(found.text)
    old, mode = None, 0o644
    if found is not None:
        mode = stat.S_IMODE(os.fstat(found).st_mode)
        old = read_up_to(found, MOST_BYTES)
    if check_hash(old) != probe.get("before"):
        return _error(
            f"The file {path} changed since the plan was made. Nothing was"
            " written. Read it again and repeat the call."
        )
    try:
        _write_beside(folder, name, content(old), mode)
    except OSError as error:
        return _not_written(path, error)
    try:
        os.rename(name + NEW_SUFFIX, name, src_dir_fd=folder, dst_dir_fd=folder)
        os.fsync(folder)
    except OSError as error:
        return _after_the_rename_failed(folder, name, path, probe, error, done)
    return ToolResult(done, False, "done")


def _write_beside(folder: int, name: str, data: bytes, mode: int) -> None:
    beside = name + NEW_SUFFIX
    # The name is removed, never a file that a link under it points at.
    with contextlib.suppress(FileNotFoundError):
        os.unlink(beside, dir_fd=folder)
    # A new file, so nothing planted under the name is written through.
    fd = os.open(beside, _NEW_FLAGS, 0o600, dir_fd=folder)
    try:
        os.fchmod(fd, mode)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    except OSError:
        # The file is this write's own, made above, and the write failed.
        _remove_beside(folder, name)
        raise
    finally:
        os.close(fd)


def _remove_beside(folder: int, name: str) -> None:
    with contextlib.suppress(OSError):
        os.unlink(name + NEW_SUFFIX, dir_fd=folder)


def _after_the_rename_failed(
    folder: int, name: str, path: str, probe: JsonObject, error: OSError, done: str
) -> ToolResult | Failure:
    # The one place the tool may not know: the rename may have happened.
    found = open_file(folder, name, path)
    now = check_hash(read_up_to(found, MOST_BYTES)) if isinstance(found, int) else None
    seen = _compared(now, probe) if not isinstance(found, Refused) else None
    if seen is True:
        return ToolResult(done, False, "done")
    if seen is False:
        # The rename did not happen, so the new file is still this write's.
        _remove_beside(folder, name)
        return _not_written(path, error)
    return Failure(
        kind="unavailable",
        effect="unknown",
        message=f"Writing {path} failed at the rename, and the file now matches neither"
        " the old content nor the new.",
        source=_SOURCE,
    )


def _compared(now: str | None, probe: JsonObject) -> bool | None:
    if now == probe.get("after"):
        return True
    if now == probe.get("before"):
        return False
    return None


def _not_written(path: str, error: OSError) -> ToolResult:
    return _error(f"{path} could not be written: {error.strerror}.")


def _error(text: str) -> ToolResult:
    return ToolResult(text, True, "none")
