"""Shared pieces for the tests of the SQLite stores."""

import asyncio
import sqlite3
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from lamplighter.adapters.sqlite import open_database
from lamplighter.ports import Failure, NewEvent

AT = "2026-11-26T07:35:12.345Z"


def run[T](call: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(call)


def opened(path: Path, timeout_s: float = 0.2) -> sqlite3.Connection:
    connection = open_database(path, timeout_s)
    assert not isinstance(connection, Failure), connection
    return connection


def failure_of(result: object) -> Failure:
    assert isinstance(result, Failure), f"expected a failure, got {result!r}"
    return result


def note(text: str = "the kettle is on") -> NewEvent:
    return NewEvent(type="note", v=1, payload={"text": text}, at=AT)


def made_error(
    kind: type[sqlite3.Error], code: int, name: str, text: str
) -> sqlite3.Error:
    """Build a SQLite error as the library would raise it, with its code."""
    error = kind(text)
    error.sqlite_errorcode = code
    error.sqlite_errorname = name
    return error


class FailingConnection:
    """Passes every call to a real connection, but raises one error on one statement."""

    def __init__(
        self, inner: sqlite3.Connection, statement: str, error: sqlite3.Error
    ) -> None:
        self._inner = inner
        self._statement = statement
        self._error = error

    @property
    def in_transaction(self) -> bool:
        return self._inner.in_transaction

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        if sql == self._statement:
            raise self._error
        return self._inner.execute(sql, parameters)

    def executemany(self, sql: str, rows: Any) -> sqlite3.Cursor:
        return self._inner.executemany(sql, rows)
