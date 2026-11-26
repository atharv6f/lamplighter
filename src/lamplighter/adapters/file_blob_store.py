"""The blob store as one file per blob in a folder, named by the blob's hash."""

import contextlib
import fcntl
import hashlib
import os
from collections.abc import Generator, Set
from dataclasses import dataclass
from pathlib import Path

from lamplighter.adapters.drive import flush_folder, is_full, write_flushed
from lamplighter.ports import (
    BlobHandle,
    Clock,
    Failure,
    blob_damage,
    check_handle,
    check_put,
    is_hash,
)

_SOURCE = "BlobStore/file"

# A put that returned less than this long ago is never swept, because the
# event that points at its blob may not be saved yet.
SWEEP_GRACE_S = 3_600


@dataclass(frozen=True, slots=True)
class BlobTotals:
    """How many blobs the folder holds, and their bytes."""

    count: int
    total_bytes: int


class FileBlobStore:
    """Keeps each blob as a file in one folder. Satisfies BlobStore.

    The folder holds files named by 64 hex characters, temporary files whose
    names start with a dot, and a lock file. A put takes the lock shared, and
    the sweep takes it alone, so a sweep never looks at a blob mid-put.
    """

    def __init__(self, folder: Path, clock: Clock, max_blob_bytes: int) -> None:
        self._folder = folder
        self._clock = clock
        self._max_blob_bytes = max_blob_bytes

    async def put(self, data: bytes, media_type: str) -> BlobHandle | Failure:
        """Save the bytes and give the handle that points at them."""
        refused = check_put(data, self._max_blob_bytes, _SOURCE)
        if refused is not None:
            return refused
        sha256 = hashlib.sha256(data).hexdigest()
        try:
            with self._locked(fcntl.LOCK_SH):
                self._write(sha256, data)
        except OSError as error:
            return _os_failure(error)
        return BlobHandle(sha256=sha256, size=len(data), media_type=media_type)

    async def get(self, handle: BlobHandle) -> bytes | Failure:
        """Give the bytes a handle points at, checked against it."""
        refused = check_handle(handle, _SOURCE)
        if refused is not None:
            return refused
        try:
            data = (self._folder / handle.sha256).read_bytes()
        except FileNotFoundError:
            return blob_damage(handle, "missing", _SOURCE)
        except OSError as error:
            return _os_failure(error)
        if len(data) != handle.size:
            return blob_damage(handle, "size", _SOURCE)
        if hashlib.sha256(data).hexdigest() != handle.sha256:
            return blob_damage(handle, "hash", _SOURCE)
        return data

    async def exists(self, handle: BlobHandle) -> bool | Failure:
        """Say whether the store holds a blob under the handle's hash."""
        refused = check_handle(handle, _SOURCE)
        if refused is not None:
            return refused
        return (self._folder / handle.sha256).is_file()

    def remove_unused(self, in_use: Set[str]) -> int | Failure:
        """Remove every blob not in `in_use` and older than an hour. Give the count.

        Temporary files older than an hour are removed too: they are what a
        put leaves behind when it is killed before its rename.
        """
        cutoff = self._clock.now().timestamp() - SWEEP_GRACE_S
        removed = 0
        try:
            with self._locked(fcntl.LOCK_EX):
                for entry in os.scandir(self._folder):
                    if _is_unused(entry, in_use, cutoff):
                        os.unlink(entry.path)
                        removed += 1
        except OSError as error:
            return _os_failure(error)
        return removed

    def totals(self) -> BlobTotals | Failure:
        """Count the blobs in the folder and their bytes."""
        if not self._folder.is_dir():
            return BlobTotals(count=0, total_bytes=0)
        try:
            sizes = [
                entry.stat().st_size
                for entry in os.scandir(self._folder)
                if is_hash(entry.name)
            ]
        except OSError as error:
            return _os_failure(error)
        return BlobTotals(count=len(sizes), total_bytes=sum(sizes))

    def _write(self, sha256: str, data: bytes) -> None:
        # A temporary left by a killed put of the same bytes is written over.
        temporary = self._folder / f".{sha256}.{os.getpid()}.tmp"
        write_flushed(temporary, data, os.O_TRUNC)
        # The time is set from the clock that was passed in, so the sweep's
        # one hour rule reads the same clock a test controls.
        moment = self._clock.now().timestamp()
        os.utime(temporary, (moment, moment))
        os.replace(temporary, self._folder / sha256)
        flush_folder(self._folder)

    @contextlib.contextmanager
    def _locked(self, how: int) -> Generator[None]:
        self._folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self._folder / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, how)
            yield
        finally:
            os.close(descriptor)


def _is_unused(entry: os.DirEntry[str], in_use: Set[str], cutoff: float) -> bool:
    is_blob = is_hash(entry.name) and entry.name not in in_use
    is_leftover = entry.name.startswith(".") and entry.name.endswith(".tmp")
    return (is_blob or is_leftover) and entry.stat().st_mtime < cutoff


def _os_failure(error: OSError) -> Failure:
    full = is_full(error)
    return Failure(
        kind="unavailable" if full else "fatal",
        effect="none",
        message="The drive is full." if full else "The blob folder could not be used.",
        source=_SOURCE,
        detail={"os_error": error.strerror or str(error)},
    )
