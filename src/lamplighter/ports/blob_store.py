"""The port for large content, saved as files named by the hash of their bytes."""

from dataclasses import dataclass
from typing import Protocol

from lamplighter.ports.failure import Failure


@dataclass(frozen=True, slots=True)
class BlobHandle:
    """Points at one blob: the SHA-256 of its bytes in hex, its size, and its type.

    The store keeps the bytes only. Two handles with one hash and different
    media types point at the same bytes.
    """

    sha256: str
    size: int
    media_type: str


class BlobStore(Protocol):
    """Large content, kept once per distinct content.

    Callers can rely on this:

    - `put` returns only after the bytes are forced to the drive under their
      final name. A half-written blob is never visible under a blob's name.
      Putting the same bytes twice gives the same hash and keeps one copy,
      and always writes the file again, so it repairs a damaged copy.
    - Bytes over the size limit are `too_large`.
    - `get` checks the size and the hash of what it read. A blob that is
      missing or does not match is `fatal`, and the detail says which.
    - Put a blob before saving the event or effect that points at it, and
      save that within an hour. Only a retention command removes a blob, and
      it keeps any blob whose hash appears in a saved row, or that was put in
      the last hour.
    """

    async def put(self, data: bytes, media_type: str) -> BlobHandle | Failure:
        """Save the bytes and give the handle that points at them."""
        ...

    async def get(self, handle: BlobHandle) -> bytes | Failure:
        """Give the bytes a handle points at, checked against it."""
        ...

    async def exists(self, handle: BlobHandle) -> bool | Failure:
        """Say whether the store holds a blob under the handle's hash."""
        ...
