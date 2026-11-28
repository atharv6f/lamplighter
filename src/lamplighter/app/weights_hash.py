"""The hash that names a model's exact weights: of one file, or of a folder of files."""

import hashlib
import os
from pathlib import Path

from lamplighter.ports import Failure

_SOURCE = "weights_hash"
_CHUNK = 1 << 20


def weights_hash(path: Path) -> str | Failure:
    """Give the SHA-256 of the weights at `path`, as 64 hex characters.

    For a file it is the file's own SHA-256. For a folder it is the SHA-256
    of one line `<SHA-256 of the file>  <path from the folder>` and a newline
    for every file under it, sorted by path as bytes, so a changed chat
    template or tokenizer gives a new hash. A link inside the folder, or
    anything there that is neither a file nor a folder, is refused, because
    what it points at could change without the folder changing.
    """
    if path.is_file():
        return _file_hash(path)
    if not path.is_dir():
        return _refused(f"There is no file or folder at {path}.")
    listed = _files_under(path)
    if isinstance(listed, Failure):
        return listed
    lines = hashlib.sha256()
    for relative in sorted(listed, key=os.fsencode):
        one = _file_hash(path / relative)
        if isinstance(one, Failure):
            return one
        lines.update(os.fsencode(f"{one}  {relative}\n"))
    return lines.hexdigest()


def _files_under(folder: Path) -> list[str] | Failure:
    found: list[str] = []
    # A folder the walk cannot open is skipped by default, which would leave
    # its files out of the hash.
    unread: list[OSError] = []
    for root, folders, files in os.walk(folder, onerror=unread.append):
        here = Path(root)
        for name in (*folders, *files):
            place = here / name
            if place.is_symlink():
                return _refused(f"The weights hold a link, at {place}.")
            if not place.is_dir() and not place.is_file():
                return _refused(
                    f"The weights hold something that is not a file, at {place}."
                )
        found.extend((here / name).relative_to(folder).as_posix() for name in files)
    if unread:
        return _refused(f"The weights could not be read: {unread[0]}.")
    return found


def _file_hash(path: Path) -> str | Failure:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            while chunk := file.read(_CHUNK):
                digest.update(chunk)
    except OSError as error:
        return _refused(f"The weights could not be read at {path}: {error.strerror}.")
    return digest.hexdigest()


def _refused(message: str) -> Failure:
    return Failure(kind="invalid_input", effect="none", message=message, source=_SOURCE)
