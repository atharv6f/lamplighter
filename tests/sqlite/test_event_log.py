"""What the SQLite event log does beyond the port's contract tests."""

import sqlite3
from pathlib import Path

from lamplighter.adapters.sqlite import SqliteEventLog
from lamplighter.ports import Event, Failure
from tests.sqlite.helpers import failure_of, note, opened, run


def log_on(connection: sqlite3.Connection) -> SqliteEventLog:
    return SqliteEventLog(connection, {}, 2_000)


def events_of(result: tuple[Event, ...] | Failure) -> tuple[Event, ...]:
    assert not isinstance(result, Failure), result
    return result


def test_a_payload_that_does_not_fit_is_reported_as_damage(tmp_path: Path) -> None:
    connection = opened(tmp_path / "state.db")
    log = log_on(connection)
    run(log.append("sess-a", [note(), note()], 0))
    for planted in ("[1, 2]", "not json"):
        connection.execute("UPDATE events SET payload = ? WHERE seq = 2", (planted,))
        for answer in (run(log.read("sess-a", 0)), run(log.tail(0, 10))):
            failure = failure_of(answer)
            assert (failure.kind, failure.effect) == ("fatal", "none")


def test_a_gap_in_seq_is_reported_as_damage(tmp_path: Path) -> None:
    connection = opened(tmp_path / "state.db")
    log = log_on(connection)
    run(log.append("sess-a", [note("one"), note("two"), note("three")], 0))
    connection.execute("DELETE FROM events WHERE seq = 2")
    failure = failure_of(run(log.read("sess-a", 0)))
    assert failure.kind == "fatal"
    assert "gap" in failure.message
    assert failure_of(run(log.read("sess-a", 1))).kind == "fatal"
    assert [e.seq for e in events_of(run(log.read("sess-a", 2)))] == [3]


def test_a_position_is_never_used_again_after_the_newest_rows_are_deleted(
    tmp_path: Path,
) -> None:
    connection = opened(tmp_path / "state.db")
    log = log_on(connection)
    run(log.append("sess-a", [note()], 0))
    run(log.append("sess-b", [note(), note()], 0))
    highest = max(e.pos for e in events_of(run(log.tail(0, 10))))
    connection.execute("DELETE FROM events WHERE session_id = 'sess-b'")
    run(log.append("sess-c", [note()], 0))
    newest = events_of(run(log.read("sess-c", 0)))[0]
    assert newest.pos > highest


def test_append_takes_the_write_lock_before_it_reads(tmp_path: Path) -> None:
    holder = opened(tmp_path / "state.db")
    connection = opened(tmp_path / "state.db", timeout_s=0.05)
    statements: list[str] = []
    connection.set_trace_callback(statements.append)
    holder.execute("BEGIN IMMEDIATE")
    failure = failure_of(run(log_on(connection).append("sess-a", [note()], 0)))
    holder.execute("ROLLBACK")
    assert (failure.kind, failure.effect) == ("unavailable", "none")
    # With a plain BEGIN the read would run first and the write fail later.
    assert not any(s.startswith("SELECT") for s in statements), statements


def test_two_connections_with_one_expected_seq_save_exactly_one_batch(
    tmp_path: Path,
) -> None:
    first = log_on(opened(tmp_path / "state.db"))
    second = log_on(opened(tmp_path / "state.db"))
    assert run(first.append("sess-a", [note("mine")], 0)) == 1
    assert failure_of(run(second.append("sess-a", [note("theirs")], 0))).kind == (
        "conflict"
    )
    events = events_of(run(second.read("sess-a", 0)))
    assert [e.payload["text"] for e in events] == ["mine"]


def test_every_payload_is_saved_as_its_saved_json(tmp_path: Path) -> None:
    connection = opened(tmp_path / "state.db")
    run(log_on(connection).append("sess-a", [note("café")], 0))
    (text,) = connection.execute("SELECT payload FROM events").fetchone()
    assert text == '{"text":"café"}'
