"""The book of experiments as one file of JSON lines, guarded by a lock beside it."""

import asyncio
import contextlib
import fcntl
import os
from collections.abc import Generator, Sequence
from pathlib import Path

from lamplighter.adapters.drive import flush
from lamplighter.ports import (
    ExperimentLedger,
    Failure,
    LedgerRow,
    read_row,
    read_saved_json,
    refused_row,
    row_text,
)

_SOURCE = "file_ledger"


class _NotARow(Exception):
    """A whole line of the file that is not a row of this book."""

    def __init__(self, number: int) -> None:
        """Keep the line's number, from 1."""
        super().__init__(number)
        self.number = number


class FileLedger(ExperimentLedger):
    """A book of experiments kept in one file.

    It reads no clock and makes no id: the caller fills both. Each append
    takes the lock, reads every row, checks the new one against them,
    writes it as one line and forces it to the drive. A last line with no
    newline is a row whole when it is JSON, and a torn row from a kill
    when it is not: a reader leaves a torn row out, and the next writer
    cuts it away.
    """

    def __init__(self, path: Path) -> None:
        """Keep the book at `path`, with its lock beside it."""
        self._path = path
        self._lock = path.with_name(f"{path.name}.lock")

    async def append(self, row: LedgerRow) -> Failure | None:
        """Add the row unless a rule refuses it. A wait for the lock blocks no task."""
        return await asyncio.to_thread(self._append, row)

    async def rows(self) -> Sequence[LedgerRow] | Failure:
        """Give every row, leaving a torn last line out and the file as it is."""
        return await asyncio.to_thread(self._rows)

    def _append(self, row: LedgerRow) -> Failure | None:
        try:
            with self._locked(fcntl.LOCK_EX):
                data = self._mended()
                refused = refused_row(_parsed(data), row)
                if refused is not None:
                    return refused
                self._write((row_text(row) + "\n").encode("utf-8"))
        except _NotARow as error:
            return self._not_a_row(error.number)
        except OSError as error:
            return self._unusable(error)
        return None

    def _rows(self) -> Sequence[LedgerRow] | Failure:
        try:
            with self._locked(fcntl.LOCK_SH):
                return _parsed(self._bytes())
        except _NotARow as error:
            return self._not_a_row(error.number)
        except OSError as error:
            return self._unusable(error)

    def _mended(self) -> bytes:
        # A whole row with no newline gets one. A row cut by a kill is cut
        # away, so that the next row does not join it.
        data = self._bytes()
        tail = data[data.rfind(b"\n") + 1 :]
        if tail and _is_json(tail):
            self._write(b"\n")
            return data + b"\n"
        if tail:
            os.truncate(self._path, len(data) - len(tail))
            descriptor = os.open(self._path, os.O_WRONLY)
            try:
                flush(descriptor)
            finally:
                os.close(descriptor)
            return data[: len(data) - len(tail)]
        return data

    def _bytes(self) -> bytes:
        try:
            return self._path.read_bytes()
        except FileNotFoundError:
            return b""

    def _write(self, data: bytes) -> None:
        descriptor = os.open(self._path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(descriptor, view) :]
            flush(descriptor)
        finally:
            os.close(descriptor)

    @contextlib.contextmanager
    def _locked(self, how: int) -> Generator[None]:
        descriptor = os.open(self._lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, how)
            yield
        finally:
            os.close(descriptor)

    def _not_a_row(self, number: int) -> Failure:
        return Failure(
            kind="unavailable",
            effect="none",
            message=f"Line {number} of {self._path} is not a row of this book.",
            source=_SOURCE,
        )

    def _unusable(self, error: OSError) -> Failure:
        return Failure(
            kind="unavailable",
            effect="none",
            message=f"The book {self._path} cannot be read, written or locked.",
            source=_SOURCE,
            detail={"os_error": error.strerror or str(error)},
        )


def _parsed(data: bytes) -> list[LedgerRow]:
    # Every whole line, and a last line with no newline when it is JSON.
    lines = data.split(b"\n")
    tail = lines.pop()
    if tail and _is_json(tail):
        lines.append(tail)
    rows: list[LedgerRow] = []
    for number, line in enumerate(lines, start=1):
        try:
            rows.append(read_row(line.decode("utf-8")))
        except ValueError as error:
            raise _NotARow(number) from error
    return rows


def _is_json(line: bytes) -> bool:
    # A row is one JSON object, and no front part of one is itself JSON, so
    # a row cut short never reads.
    try:
        read_saved_json(line.decode("utf-8"))
    except ValueError:
        return False
    return True
