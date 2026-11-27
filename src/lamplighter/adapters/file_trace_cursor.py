"""A trace cursor kept in a small file beside the store, one entry per target."""

import contextlib
import fcntl
import json
import os
from collections.abc import Generator
from pathlib import Path
from typing import cast

from lamplighter.ports import CursorMark, Failure, canonical_json

_SOURCE = "TraceCursor/file"
_FILE = "trace_cursor.json"
_LOCK = "trace_cursor.lock"
_VERSION = 1
# The largest position the log can hold: SQLite's largest whole number.
_LARGEST = 9_223_372_036_854_775_807

type _Cursors = dict[str, CursorMark]


class _NotACursor(Exception):
    """The file is there and is not one this code wrote."""


class FileTraceCursor:
    """Keeps the mark of one target in `trace_cursor.json`. Satisfies TraceCursor.

    The file holds a mark for every target that has had one. A move or a
    reset takes a lock on a second file, reads the whole file, and writes it
    anew under another name that is then renamed over it, so a kill leaves
    the old file or the new one. A missing or empty file is a new cursor. A
    file in any other shape is never guessed at and never written over.
    """

    def __init__(self, folder: Path, target: str) -> None:
        self._folder = folder
        self._target = target

    async def read(self) -> CursorMark | Failure:
        """Give this target's mark, or position 0 with 0 resets if it has none."""
        try:
            return self._load().get(self._target, CursorMark(pos=0, resets=0))
        except _NotACursor:
            return self._not_a_cursor()
        except OSError as error:
            return self._unusable(error)

    async def move(self, seen: CursorMark, to: int) -> bool | Failure:
        """Move to `to` if this target's mark is still `seen`, and say if it did."""
        try:
            with self._locked():
                cursors = self._load()
                if cursors.get(self._target, CursorMark(0, 0)) != seen:
                    return False
                cursors[self._target] = CursorMark(pos=to, resets=seen.resets)
                self._write(cursors)
        except _NotACursor:
            return self._not_a_cursor()
        except OSError as error:
            return self._unusable(error)
        return True

    async def reset(self) -> Failure | None:
        """Set this target's position to 0 and count one more reset."""
        # With no store there is no cursor to reset, and nothing is made.
        if not self._folder.is_dir():
            return None
        try:
            with self._locked():
                cursors = self._load()
                resets = cursors.get(self._target, CursorMark(0, 0)).resets
                cursors[self._target] = CursorMark(pos=0, resets=resets + 1)
                self._write(cursors)
        except _NotACursor:
            return self._not_a_cursor()
        except OSError as error:
            return self._unusable(error)
        return None

    def _load(self) -> _Cursors:
        try:
            text = (self._folder / _FILE).read_bytes()
        except FileNotFoundError:
            return {}
        # A power cut can leave an empty file, and it means a new cursor.
        if not text:
            return {}
        try:
            data = json.loads(text, object_pairs_hook=_unique_keys)
        # ValueError also covers a number too long to convert and a key twice;
        # RecursionError is what deep nesting raises.
        except (ValueError, RecursionError) as error:
            raise _NotACursor from error
        return _cursors_of(data)

    def _write(self, cursors: _Cursors) -> None:
        data = {
            "cursors": {
                target: {"pos": mark.pos, "resets": mark.resets}
                for target, mark in cursors.items()
            },
            "v": _VERSION,
        }
        new = self._folder / f"{_FILE}.new"
        descriptor = os.open(new, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            # A leftover from a killed write may have been made another way.
            os.fchmod(descriptor, 0o600)
            left = canonical_json(data).encode()
            # A write to a nearly full disk may take only part of the bytes.
            while left:
                left = left[os.write(descriptor, left) :]
        finally:
            os.close(descriptor)
        os.replace(new, self._folder / _FILE)

    @contextlib.contextmanager
    def _locked(self) -> Generator[None]:
        descriptor = os.open(self._folder / _LOCK, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def _not_a_cursor(self) -> Failure:
        return Failure(
            kind="unavailable",
            effect="none",
            message=(
                f"The file {_FILE} in {self._folder} is not one this program"
                " wrote. Stop every chat, delete the file and start again."
                " Every span is then sent again, and the copies change nothing."
            ),
            source=_SOURCE,
        )

    def _unusable(self, error: OSError) -> Failure:
        return Failure(
            kind="unavailable",
            effect="none",
            message=f"The trace cursor in {self._folder} cannot be read or written.",
            source=_SOURCE,
            detail={"os_error": error.strerror or str(error)},
        )


def _cursors_of(data: object) -> _Cursors:
    """Give the marks the file holds, or raise when it is not in this shape."""
    if not isinstance(data, dict):
        raise _NotACursor
    top = cast(dict[str, object], data)
    version, cursors = top.get("v"), top.get("cursors")
    if top.keys() != {"cursors", "v"} or not _whole(version) or version != _VERSION:
        raise _NotACursor
    if not isinstance(cursors, dict):
        raise _NotACursor
    entries = cast(dict[str, object], cursors)
    return {target: _mark_of(entry) for target, entry in entries.items()}


def _mark_of(entry: object) -> CursorMark:
    if not isinstance(entry, dict):
        raise _NotACursor
    fields = cast(dict[str, object], entry)
    if fields.keys() != {"pos", "resets"}:
        raise _NotACursor
    pos, resets = fields["pos"], fields["resets"]
    if not _whole(pos) or not _whole(resets):
        raise _NotACursor
    return CursorMark(pos=cast(int, pos), resets=cast(int, resets))


def _whole(value: object) -> bool:
    # True and False are whole numbers to Python, and never a position here.
    return type(value) is int and 0 <= value <= _LARGEST


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Make an object of `pairs`, refusing a key given twice."""
    made = dict(pairs)
    if len(made) != len(pairs):
        raise ValueError("a key is given twice")
    return made
