"""The id of a saved output: the blob's hash in two halves, then its size.

A model may copy an id into a file that another session reads. The store's
export stops for a session whose text holds the bare hash of a blob it has
no handle for, so the id is never 64 hex characters in a row.
"""

import re

from lamplighter.ports.blob_store import BlobHandle

_ID = re.compile(r"([0-9a-f]{32})-([0-9a-f]{32})-(0|[1-9][0-9]{0,7})")


def output_id(handle: BlobHandle) -> str:
    """Give the id of the saved output a handle points at."""
    return f"{handle.sha256[:32]}-{handle.sha256[32:]}-{handle.size}"


def read_output_id(text: str) -> tuple[str, int] | None:
    """Give the hash and the size an id names, or None for text of another form."""
    match = _ID.fullmatch(text)
    if match is None:
        return None
    return match.group(1) + match.group(2), int(match.group(3))
