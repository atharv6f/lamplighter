"""The one way a store reads or writes: one whole transaction per call.

The body of a transaction is a plain function with no `await` in it, so a
cancel can never land in the middle. Every exit that is not a commit sends
ROLLBACK as a statement, from a `finally` that covers everything from BEGIN
to COMMIT, because with autocommit on, `rollback()` does nothing, and a
second Ctrl-C can raise at any line, even just after BEGIN returns.
"""

import sqlite3
from collections.abc import Callable

from lamplighter.adapters.sqlite.errors import failure_for
from lamplighter.ports import Failure, FailureEffect

type Body[T] = Callable[[sqlite3.Connection], T | Failure]


def write[T](connection: sqlite3.Connection, source: str, body: Body[T]) -> T | Failure:
    """Run `body` in one write transaction, and commit unless it gives a Failure.

    BEGIN IMMEDIATE takes the write lock before the body reads anything, so
    what the body reads cannot change before its writes land. An interrupt
    that lands as COMMIT returns leaves the change saved, so a caller that
    gets one does not know whether its write happened.
    """
    # Only an error from COMMIT can come after the drive kept the change.
    effect: FailureEffect = "none"
    try:
        connection.execute("BEGIN IMMEDIATE")
        result = body(connection)
        if not isinstance(result, Failure):
            effect = "unknown"
            connection.execute("COMMIT")
        return result
    except sqlite3.Error as error:
        return _mapped(error, source, effect)
    finally:
        _roll_back(connection)


def read[T](connection: sqlite3.Connection, source: str, body: Body[T]) -> T | Failure:
    """Run `body` in one read transaction, so every query in it sees one moment."""
    try:
        connection.execute("BEGIN")
        return body(connection)
    except sqlite3.Error as error:
        return _mapped(error, source, "none")
    finally:
        _roll_back(connection)


def _roll_back(connection: sqlite3.Connection) -> None:
    # Nothing is open after a commit, and SQLite ends a transaction by itself
    # after some errors, such as a full drive.
    if connection.in_transaction:
        connection.execute("ROLLBACK")


def _mapped(error: sqlite3.Error, source: str, fatal_effect: FailureEffect) -> Failure:
    failure = failure_for(error, source, fatal_effect)
    if failure is None:
        raise error
    return failure
