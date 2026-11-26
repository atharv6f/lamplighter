"""A blob store kept in memory, for tests. It refuses what the real one does."""

import hashlib

from lamplighter.ports import (
    BlobHandle,
    Failure,
    blob_damage,
    check_handle,
    check_put,
)

_SOURCE = "BlobStore/in_memory"


class InMemoryBlobStore:
    """Keeps every blob in a dict by its hash. Satisfies BlobStore."""

    def __init__(self, max_blob_bytes: int) -> None:
        self._max_blob_bytes = max_blob_bytes
        self.by_hash: dict[str, bytes] = {}

    async def put(self, data: bytes, media_type: str) -> BlobHandle | Failure:
        """Save the bytes and give the handle that points at them."""
        refused = check_put(data, self._max_blob_bytes, _SOURCE)
        if refused is not None:
            return refused
        sha256 = hashlib.sha256(data).hexdigest()
        self.by_hash[sha256] = bytes(data)
        return BlobHandle(sha256=sha256, size=len(data), media_type=media_type)

    async def get(self, handle: BlobHandle) -> bytes | Failure:
        """Give the bytes a handle points at, checked against it."""
        refused = check_handle(handle, _SOURCE)
        if refused is not None:
            return refused
        data = self.by_hash.get(handle.sha256)
        if data is None:
            return blob_damage(handle, "missing", _SOURCE)
        if len(data) != handle.size:
            return blob_damage(handle, "size", _SOURCE)
        return data

    async def exists(self, handle: BlobHandle) -> bool | Failure:
        """Say whether the store holds a blob under the handle's hash."""
        refused = check_handle(handle, _SOURCE)
        if refused is not None:
            return refused
        return handle.sha256 in self.by_hash
