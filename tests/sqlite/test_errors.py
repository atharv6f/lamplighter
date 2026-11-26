import sqlite3

import pytest

from lamplighter.adapters.sqlite.errors import failure_for
from tests.sqlite.helpers import made_error

# Extended codes, as SQLite raises them, and the kind each must become.
KINDS = [
    (sqlite3.SQLITE_BUSY, "unavailable"),
    (sqlite3.SQLITE_BUSY_SNAPSHOT, "unavailable"),
    (sqlite3.SQLITE_LOCKED, "unavailable"),
    (sqlite3.SQLITE_FULL, "unavailable"),
    (sqlite3.SQLITE_IOERR_FSYNC, "fatal"),
    (sqlite3.SQLITE_CORRUPT, "fatal"),
    (sqlite3.SQLITE_NOTADB, "fatal"),
    (sqlite3.SQLITE_CANTOPEN, "fatal"),
    (sqlite3.SQLITE_READONLY, "fatal"),
    (sqlite3.SQLITE_CONSTRAINT_UNIQUE, "conflict"),
    (sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY, "conflict"),
]


def error(code: int) -> sqlite3.Error:
    return made_error(sqlite3.OperationalError, code, "SQLITE_MADE_UP", "made up")


@pytest.mark.parametrize(("code", "kind"), KINDS)
def test_each_error_a_caller_can_meet_has_its_kind(code: int, kind: str) -> None:
    failure = failure_for(error(code), "test", "unknown")
    assert failure is not None
    assert failure.kind == kind
    # Only a fatal error can have kept a change, and only after a commit.
    assert failure.effect == ("unknown" if kind == "fatal" else "none")
    assert failure.source == "test"


def test_a_fatal_error_outside_a_commit_wrote_nothing() -> None:
    failure = failure_for(error(sqlite3.SQLITE_CORRUPT), "test", "none")
    assert failure is not None
    assert (failure.kind, failure.effect) == ("fatal", "none")


@pytest.mark.parametrize(
    "code",
    [sqlite3.SQLITE_CONSTRAINT_CHECK, sqlite3.SQLITE_CONSTRAINT_NOTNULL, 1],
    ids=["check", "not_null", "generic"],
)
def test_an_error_only_a_bug_can_cause_has_no_failure(code: int) -> None:
    assert failure_for(error(code), "test", "none") is None
