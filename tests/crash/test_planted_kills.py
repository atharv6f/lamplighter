"""Kills a process that writes to a real store at one named point each.

The child plants the kill with a trace callback that fires as a statement
starts, so the code under test has no hook in it.
"""

import asyncio
import os
import sqlite3
from pathlib import Path

from lamplighter.adapters import FileBlobStore, SystemClock
from lamplighter.adapters.sqlite import (
    SqliteEffectLedger,
    SqliteEventLog,
    SqliteInbox,
    open_database,
)
from lamplighter.ports import Accepted, Failure
from tests.crash.child import MAX_RECORD_BYTES, batch, crash_action, crash_inbound
from tests.crash.runner import run_planted


def opened(folder: Path) -> sqlite3.Connection:
    connection = open_database(folder / "state.db", 5)
    assert not isinstance(connection, Failure), connection
    return connection


def made(folder: Path) -> None:
    opened(folder).close()


def log_of(folder: Path) -> SqliteEventLog:
    return SqliteEventLog(opened(folder), {}, MAX_RECORD_BYTES)


def event_count(folder: Path) -> int:
    events = asyncio.run(log_of(folder).read("crash-a", 0))
    assert not isinstance(events, Failure), events
    return len(events)


def test_a_kill_before_the_commit_of_an_append_leaves_no_batch(tmp_path: Path) -> None:
    made(tmp_path)
    run_planted("append_before_commit", tmp_path)
    assert event_count(tmp_path) == 0
    assert asyncio.run(log_of(tmp_path).append("crash-a", batch("again", 3), 0)) == 3


def test_a_kill_before_the_commit_of_a_large_append_leaves_no_batch(
    tmp_path: Path,
) -> None:
    made(tmp_path)
    run_planted("large_append_before_commit", tmp_path)
    # Five MiB is more than SQLite's page cache holds, so it had already
    # spilled pages to the log file when the kill came, with no commit frame
    # after them. The read below must skip those frames.
    assert os.path.getsize(tmp_path / "state.db-wal") > 1024 * 1024
    assert event_count(tmp_path) == 0
    assert asyncio.run(log_of(tmp_path).append("crash-a", batch("again", 1), 0)) == 1


def test_a_kill_after_an_append_returned_keeps_the_batch(tmp_path: Path) -> None:
    made(tmp_path)
    assert '"append"' in run_planted("append_after_return", tmp_path)
    assert event_count(tmp_path) == 3
    again = asyncio.run(log_of(tmp_path).append("crash-a", batch("again", 3), 0))
    assert isinstance(again, Failure)
    assert again.kind == "conflict"


def test_a_kill_between_begin_and_finish_leaves_the_intent_listed(
    tmp_path: Path,
) -> None:
    made(tmp_path)
    assert '"begin"' in run_planted("between_begin_and_finish", tmp_path)
    ledger = SqliteEffectLedger(opened(tmp_path), SystemClock(), MAX_RECORD_BYTES)
    listed = asyncio.run(ledger.unresolved())
    assert not isinstance(listed, Failure), listed
    assert [(e.action, e.state) for e in listed] == [
        (crash_action("crash-a", 7), "intended")
    ]


def test_a_kill_before_the_commit_of_a_finish_leaves_the_effect_intended(
    tmp_path: Path,
) -> None:
    made(tmp_path)
    run_planted("finish_before_commit", tmp_path)
    ledger = SqliteEffectLedger(opened(tmp_path), SystemClock(), MAX_RECORD_BYTES)
    listed = asyncio.run(ledger.unresolved())
    assert not isinstance(listed, Failure), listed
    assert [e.state for e in listed] == ["intended"]
    assert listed[0].result is None


def test_a_kill_before_the_commit_of_an_accept_leaves_the_message_new(
    tmp_path: Path,
) -> None:
    made(tmp_path)
    run_planted("accept_before_commit", tmp_path)
    inbox = SqliteInbox(opened(tmp_path), MAX_RECORD_BYTES)
    again = asyncio.run(inbox.accept(crash_inbound("planted")))
    assert isinstance(again, Accepted), again
    assert again.is_new


def test_a_kill_before_a_blob_is_renamed_leaves_no_blob_under_its_name(
    tmp_path: Path,
) -> None:
    made(tmp_path)
    run_planted("put_before_rename", tmp_path)
    names = [p.name for p in (tmp_path / "blobs").iterdir()]
    assert [n for n in names if not n.startswith(".")] == []
    assert any(n.endswith(".tmp") for n in names)
    blobs = FileBlobStore(tmp_path / "blobs", SystemClock(), 1_000)
    data = b"a blob that never gets its name"
    handle = asyncio.run(blobs.put(data, "text/plain"))
    assert not isinstance(handle, Failure), handle
    assert asyncio.run(blobs.get(handle)) == data


def idle_session(folder: Path) -> None:
    log = log_of(folder)
    asyncio.run(log.append("crash-a", batch("kept", 3), 0))


def test_a_kill_before_the_commit_of_a_delete_leaves_the_session_whole(
    tmp_path: Path,
) -> None:
    idle_session(tmp_path)
    run_planted("delete_before_commit", tmp_path)
    assert (tmp_path / "export" / "manifest.json").exists()
    assert event_count(tmp_path) == 3


def test_a_kill_after_the_commit_of_a_delete_leaves_the_session_gone(
    tmp_path: Path,
) -> None:
    idle_session(tmp_path)
    run_planted("delete_after_commit", tmp_path)
    assert event_count(tmp_path) == 0


def test_a_kill_before_the_first_open_commits_leaves_a_file_the_next_open_makes(
    tmp_path: Path,
) -> None:
    run_planted("first_open_before_commit", tmp_path)
    raw = sqlite3.connect(tmp_path / "state.db")
    assert raw.execute("SELECT count(*) FROM sqlite_schema").fetchone() == (0,)
    raw.close()
    connection = opened(tmp_path)
    tables = connection.execute(
        "SELECT name FROM sqlite_schema WHERE type = 'table' AND name = 'events'"
    ).fetchall()
    assert tables == [("events",)]
