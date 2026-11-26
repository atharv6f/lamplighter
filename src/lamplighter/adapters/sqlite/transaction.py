"""The one way a store reads or writes: one whole transaction per call.

The body of a transaction is a plain function with no `await` in it, so a
cancel can never land in the middle. Every exit that is not a commit sends
ROLLBACK as a statement, because with autocommit on, `rollback()` does
nothing, and a second Ctrl-C can raise inside a body at any line.
"""

import sqlite3
from collections.abc import Callable

from lamplighter.adapters.sqlite.errors import failure_for
from lamplighter.ports import Failure, FailureEffect

type Body[T] = Callable[[sqlite3.Connection], T | Failure]


def write[T](connection: sqlite3.Connection, source: str, body: Body[T]) -> T | Failure:
    """Run `body` in one write transaction, and commit unless it gives a Failure.

    BEGIN IMMEDIATE takes the write lock before the body reads anything, so
    what the body reads cannot change before its writes land.
    """
    try:
        connection.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as error:
        return _mapped(error, source, "none")
    result = _run(connection, source, body)
    if isinstance(result, Failure):
        return result
    try:
        connection.execute("COMMIT")
    except BaseException as error:
        _roll_back(connection)
        if isinstance(error, sqlite3.Error):
            return _mapped(error, source, "unknown")
        raise
    return result


def read[T](connection: sqlite3.Connection, source: str, body: Body[T]) -> T | Failure:
    """Run `body` in one read transaction, so every query in it sees one moment."""
    try:
        connection.execute("BEGIN")
    except sqlite3.Error as error:
        return _mapped(error, source, "none")
    result = _run(connection, source, body)
    _roll_back(connection)
    return result


def _run[T](connection: sqlite3.Connection, source: str, body: Body[T]) -> T | Failure:
    try:
        result = body(connection)
    except BaseException as error:
        _roll_back(connection)
        if isinstance(error, sqlite3.Error):
            return _mapped(error, source, "none")
        raise
    if isinstance(result, Failure):
        _roll_back(connection)
    return result


def _roll_back(connection: sqlite3.Connection) -> None:
    # SQLite ends a transaction by itself after some errors, such as a full drive.
    if connection.in_transaction:
        connection.execute("ROLLBACK")


def _mapped(error: sqlite3.Error, source: str, fatal_effect: FailureEffect) -> Failure:
    failure = failure_for(error, source, fatal_effect)
    if failure is None:
        raise error
    return failure
