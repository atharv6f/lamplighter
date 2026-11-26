import sqlite3
from pathlib import Path
from typing import cast

import pytest

from lamplighter.adapters.sqlite.transaction import read, write
from lamplighter.ports import Failure
from tests.sqlite.helpers import FailingConnection, made_error, opened

SOURCE = "EventLog/sqlite"
INSERT = "INSERT INTO inbox (channel, native_id, inbound) VALUES (?, ?, '{}')"


def rows(connection: sqlite3.Connection) -> int:
    (count,) = connection.execute("SELECT count(*) FROM inbox").fetchone()
    return count


def test_a_body_that_returns_commits(tmp_path: Path) -> None:
    connection = opened(tmp_path / "state.db")
    result = write(connection, SOURCE, lambda c: c.execute(INSERT, ("a", "1")).rowcount)
    assert result == 1
    assert rows(opened(tmp_path / "state.db")) == 1


def test_a_body_that_gives_a_failure_saves_nothing(tmp_path: Path) -> None:
    connection = opened(tmp_path / "state.db")
    refused = Failure(kind="conflict", effect="none", message="No.", source=SOURCE)

    def body(c: sqlite3.Connection) -> Failure:
        c.execute(INSERT, ("a", "1"))
        return refused

    assert write(connection, SOURCE, body) is refused
    assert not connection.in_transaction
    assert rows(connection) == 0


def test_an_interrupt_inside_a_write_saves_nothing_and_leaves_no_transaction(
    tmp_path: Path,
) -> None:
    connection = opened(tmp_path / "state.db")

    def body(c: sqlite3.Connection) -> int:
        c.execute(INSERT, ("a", "1"))
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        write(connection, SOURCE, body)
    assert not connection.in_transaction
    assert rows(connection) == 0


def test_an_interrupt_inside_a_read_leaves_no_transaction(tmp_path: Path) -> None:
    connection = opened(tmp_path / "state.db")

    def body(c: sqlite3.Connection) -> int:
        c.execute("SELECT 1")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        read(connection, SOURCE, body)
    assert not connection.in_transaction


def test_a_bug_in_a_body_is_raised_and_the_write_is_rolled_back(
    tmp_path: Path,
) -> None:
    connection = opened(tmp_path / "state.db")

    def body(c: sqlite3.Connection) -> int:
        c.execute(INSERT, ("a", "1"))
        c.execute("SELECT * FROM no_such_table")
        return 0

    with pytest.raises(sqlite3.OperationalError):
        write(connection, SOURCE, body)
    assert not connection.in_transaction
    assert rows(connection) == 0


def test_a_full_drive_is_unavailable_and_saves_nothing(tmp_path: Path) -> None:
    connection = opened(tmp_path / "state.db")
    (pages,) = connection.execute("PRAGMA page_count").fetchone()
    connection.execute(f"PRAGMA max_page_count = {pages + 1}")

    def body(c: sqlite3.Connection) -> int:
        for n in range(200):
            c.execute(INSERT.replace("'{}'", "?"), ("a", str(n), "x" * 4_000))
        return 0

    failure = write(connection, SOURCE, body)
    assert isinstance(failure, Failure)
    assert (failure.kind, failure.effect) == ("unavailable", "none")
    assert not connection.in_transaction
    assert rows(connection) == 0


def test_a_drive_error_on_commit_may_have_saved_the_change(tmp_path: Path) -> None:
    inner = opened(tmp_path / "state.db")
    error = made_error(
        sqlite3.OperationalError,
        sqlite3.SQLITE_IOERR_FSYNC,
        "SQLITE_IOERR_FSYNC",
        "disk I/O error",
    )
    connection = cast(sqlite3.Connection, FailingConnection(inner, "COMMIT", error))
    failure = write(
        connection, SOURCE, lambda c: c.execute(INSERT, ("a", "1")).rowcount
    )
    assert isinstance(failure, Failure)
    assert (failure.kind, failure.effect) == ("fatal", "unknown")
    assert not inner.in_transaction


def test_a_drive_error_before_the_commit_saved_nothing(tmp_path: Path) -> None:
    inner = opened(tmp_path / "state.db")
    error = made_error(
        sqlite3.OperationalError,
        sqlite3.SQLITE_IOERR_READ,
        "SQLITE_IOERR_READ",
        "disk I/O error",
    )
    connection = cast(sqlite3.Connection, FailingConnection(inner, "SELECT 1", error))
    failure = write(connection, SOURCE, lambda c: c.execute("SELECT 1").fetchone())
    assert isinstance(failure, Failure)
    assert (failure.kind, failure.effect) == ("fatal", "none")


def test_a_lock_held_too_long_by_another_process_is_unavailable(
    tmp_path: Path,
) -> None:
    holder = opened(tmp_path / "state.db")
    waiter = opened(tmp_path / "state.db", timeout_s=0.05)
    holder.execute("BEGIN IMMEDIATE")
    failure = write(waiter, SOURCE, lambda c: c.execute(INSERT, ("a", "1")).rowcount)
    assert isinstance(failure, Failure)
    assert (failure.kind, failure.effect) == ("unavailable", "none")
    holder.execute("ROLLBACK")


def test_a_reader_never_sees_a_write_that_is_not_committed(tmp_path: Path) -> None:
    writer = opened(tmp_path / "state.db")
    reader = opened(tmp_path / "state.db")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(INSERT, ("a", "1"))
    assert read(reader, SOURCE, rows) == 0
    writer.execute("COMMIT")
    assert read(reader, SOURCE, rows) == 1
