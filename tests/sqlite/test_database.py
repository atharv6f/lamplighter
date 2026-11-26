import os
import sqlite3
import stat
import threading
from pathlib import Path
from typing import Any

import pytest

from lamplighter.adapters.sqlite import database, open_database
from lamplighter.adapters.sqlite.database import APPLICATION_ID, LAYOUT
from tests.sqlite.helpers import failure_of, opened


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_new_store_is_made_with_its_layout_and_settings(tmp_path: Path) -> None:
    connection = opened(tmp_path / "store" / "state.db")
    tables = {
        name
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table'"
        )
    }
    assert {"events", "effects", "inbox", "outbox"} <= tables
    assert connection.execute("PRAGMA application_id").fetchone() == (APPLICATION_ID,)
    assert connection.execute("PRAGMA user_version").fetchone() == (2,)
    assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert connection.execute("PRAGMA synchronous").fetchone() == (2,)
    assert connection.execute("PRAGMA fullfsync").fetchone() == (1,)
    assert connection.execute("PRAGMA secure_delete").fetchone() == (1,)
    assert connection.autocommit is True


def test_the_store_and_its_side_files_can_be_read_only_by_their_owner(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "store"
    connection = opened(folder / "state.db")
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "INSERT INTO inbox (channel, native_id, inbound) VALUES ('a', 'b', '{}')"
    )
    connection.execute("COMMIT")
    assert mode(folder) == 0o700
    for name in ("state.db", "state.db-wal", "state.db-shm"):
        assert mode(folder / name) == 0o600, name


def test_a_second_open_finds_the_store_and_sets_the_connection_settings_again(
    tmp_path: Path,
) -> None:
    opened(tmp_path / "state.db").close()
    again = opened(tmp_path / "state.db")
    assert again.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert again.execute("PRAGMA fullfsync").fetchone() == (1,)
    assert again.execute("PRAGMA synchronous").fetchone() == (2,)
    assert again.execute("PRAGMA secure_delete").fetchone() == (1,)


def test_a_store_written_by_newer_code_is_refused(tmp_path: Path) -> None:
    connection = opened(tmp_path / "state.db")
    connection.execute("PRAGMA user_version = 3")
    connection.close()
    failure = failure_of(open_database(tmp_path / "state.db", 0.2))
    assert failure.kind == "needs_user"
    assert "newer" in failure.message


def test_an_sqlite_older_than_the_floor_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 51, 2))
    failure = failure_of(open_database(tmp_path / "state.db", 0.2))
    assert failure.kind == "needs_user"
    assert not (tmp_path / "state.db").exists()


def test_a_file_that_is_not_sqlite_is_refused_and_left_untouched(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    path.write_bytes(b"a made-up note that is not a database\n" * 200)
    before = path.read_bytes()
    failure = failure_of(open_database(path, 0.2))
    assert failure.kind == "fatal"
    assert path.read_bytes() == before


def test_another_programs_database_is_refused_and_left_untouched(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    other = sqlite3.connect(path)
    other.execute("CREATE TABLE recipes (name TEXT)")
    other.commit()
    other.close()
    before = path.read_bytes()
    failure = failure_of(open_database(path, 0.2))
    assert failure.kind == "fatal"
    assert "not a Lamplighter store" in failure.message
    assert path.read_bytes() == before
    assert not (tmp_path / "state.db-wal").exists()


def test_two_opens_of_one_new_file_at_once_both_succeed_and_make_one_layout(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    start = threading.Barrier(2)
    results: list[object] = []

    def open_it() -> None:
        start.wait()
        # An error raised in a thread would otherwise only be a warning.
        try:
            connection = open_database(path, 5)
        except BaseException as error:
            results.append(error)
            return
        results.append(connection)
        if isinstance(connection, sqlite3.Connection):
            connection.close()

    threads = [threading.Thread(target=open_it) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 2
    assert all(isinstance(result, sqlite3.Connection) for result in results), results
    check = opened(path)
    (events_tables,) = check.execute(
        "SELECT count(*) FROM sqlite_schema WHERE name = 'events'"
    ).fetchone()
    assert events_tables == 1


def test_a_store_in_a_folder_that_cannot_be_made_is_fatal(tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder")
    failure = failure_of(open_database(blocker / "state.db", 0.2))
    assert failure.kind == "fatal"


def test_an_open_clears_the_write_ahead_log(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    first = opened(path)
    first.execute("BEGIN IMMEDIATE")
    first.execute(
        "INSERT INTO inbox (channel, native_id, inbound) VALUES ('a', 'b', '{}')"
    )
    first.execute("COMMIT")
    assert os.path.getsize(tmp_path / "state.db-wal") > 0
    # A second connection's open folds the log back into the file and empties it.
    opened(path)
    assert os.path.getsize(tmp_path / "state.db-wal") == 0


def test_opening_a_store_while_another_process_writes_takes_no_write_lock(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    holder = opened(path)
    holder.execute("BEGIN IMMEDIATE")
    try:
        second = open_database(path, 0.05)
        assert isinstance(second, sqlite3.Connection), second
        second.close()
    finally:
        holder.execute("ROLLBACK")


def test_a_setting_that_does_not_read_back_is_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No real build refuses these values, so the test asks for a read back
    # that cannot happen.
    monkeypatch.setattr(database, "_SETTINGS", (("secure_delete", "ON", 2),))
    failure = failure_of(open_database(tmp_path / "state.db", 0.2))
    assert failure.kind == "fatal"
    assert "secure_delete" in failure.message


class _NoWal:
    """A connection whose switch to WAL answers with another mode."""

    def __init__(self, inner: sqlite3.Connection) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        if sql == "PRAGMA journal_mode = WAL":
            return self._inner.execute("SELECT 'delete'")
        return self._inner.execute(sql, parameters)


def test_a_file_that_will_not_switch_to_wal_is_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_connect = sqlite3.connect

    def connect(*args: Any, **kwargs: Any) -> Any:
        return _NoWal(real_connect(*args, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", connect)
    failure = failure_of(open_database(tmp_path / "state.db", 0.2))
    assert failure.kind == "fatal"
    assert "journal_mode" in failure.message


def test_the_switch_to_wal_waits_while_another_open_holds_the_new_file(
    tmp_path: Path,
) -> None:
    # Two first opens at once: the other one holds a lock on the new file,
    # and SQLite answers "busy" to the switch at once, without the wait it
    # gives a write, so the switch must be tried again.
    path = tmp_path / "state.db"
    path.touch(mode=0o600)
    other = sqlite3.connect(path, autocommit=True, check_same_thread=False)
    other.execute("BEGIN IMMEDIATE")
    release = threading.Timer(0.2, lambda: other.execute("ROLLBACK"))
    release.start()
    try:
        connection = open_database(path, 5)
        assert isinstance(connection, sqlite3.Connection), connection
        connection.close()
    finally:
        release.join()
        other.close()


def version_1_store(path: Path) -> None:
    """Make a store as the code of layout version 1 made it: no outbox."""
    connection = sqlite3.connect(path)
    for statement in LAYOUT:
        connection.execute(statement)
    connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()


def outbox_tables(connection: sqlite3.Connection) -> int:
    (count,) = connection.execute(
        "SELECT count(*) FROM sqlite_schema WHERE name IN ('outbox', 'outbox_due')"
    ).fetchone()
    return count


def test_a_version_1_store_gains_the_outbox_and_becomes_version_2(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    version_1_store(path)
    connection = opened(path)
    assert connection.execute("PRAGMA user_version").fetchone() == (2,)
    assert outbox_tables(connection) == 2
    assert connection.execute("SELECT count(*) FROM outbox").fetchone() == (0,)
    connection.close()
    again = opened(path)
    assert again.execute("PRAGMA user_version").fetchone() == (2,)
    assert outbox_tables(again) == 2


def test_two_opens_of_one_version_1_file_at_once_both_end_at_version_2(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    version_1_store(path)
    start = threading.Barrier(2)
    results: list[object] = []

    def open_it() -> None:
        start.wait()
        try:
            connection = open_database(path, 5)
        except BaseException as error:
            results.append(error)
            return
        results.append(connection)
        if isinstance(connection, sqlite3.Connection):
            connection.close()

    threads = [threading.Thread(target=open_it) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(isinstance(result, sqlite3.Connection) for result in results), results
    check = opened(path)
    assert check.execute("PRAGMA user_version").fetchone() == (2,)
    assert outbox_tables(check) == 2


def test_the_step_from_version_1_reads_the_version_again_under_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This open read version 1, and then another process made the step
    # before this one took the write lock. Read again, the version is 2.
    path = tmp_path / "state.db"
    opened(path).close()
    real_identity = database._identity  # pyright: ignore[reportPrivateUsage]

    def as_seen_before(connection: sqlite3.Connection) -> tuple[int, int, int]:
        application_id, _, tables = real_identity(connection)
        return application_id, 1, tables

    monkeypatch.setattr(database, "_identity", as_seen_before)
    connection = opened(path)
    assert connection.execute("PRAGMA user_version").fetchone() == (2,)
    assert outbox_tables(connection) == 2
