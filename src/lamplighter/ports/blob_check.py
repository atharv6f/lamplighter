"""Checks the input of a blob store call, so every blob store refuses the same."""

from lamplighter.ports.blob_store import BlobHandle
from lamplighter.ports.event_check import invalid
from lamplighter.ports.failure import Failure
from lamplighter.ports.value_rules import is_hash


def check_put(data: bytes, max_blob_bytes: int, source: str) -> Failure | None:
    """Give the Failure for bytes no blob store may accept, or None."""
    if len(data) > max_blob_bytes:
        return Failure(
            kind="too_large",
            effect="none",
            message=f"The blob is over {max_blob_bytes} bytes.",
            source=source,
        )
    return None


def check_handle(handle: BlobHandle, source: str) -> Failure | None:
    """Give the Failure for a handle that cannot point at any blob, or None."""
    if not is_hash(handle.sha256):
        return invalid("The handle's sha256 is not a hash.", source)
    if handle.size < 0:
        return invalid("The handle's size must be 0 or more.", source)
    return None


def blob_damage(handle: BlobHandle, problem: str, source: str) -> Failure:
    """Build the failure for a blob that is missing or does not match its handle.

    `problem` is "missing", "size" or "hash".
    """
    return Failure(
        kind="fatal",
        effect="none",
        message=f"The blob {handle.sha256} is {_WORDS[problem]}.",
        source=source,
        detail={"problem": problem, "sha256": handle.sha256},
    )


_WORDS = {
    "missing": "missing",
    "size": "not the size its handle says",
    "hash": "not the content its hash says",
}
