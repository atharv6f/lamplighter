"""What the SQLite inbox does beyond the port's contract tests."""

from pathlib import Path

from lamplighter.adapters.sqlite import SqliteInbox
from lamplighter.ports import Accepted
from tests.sqlite.helpers import failure_of, opened, run
from tests.store_samples import inbound


def test_a_row_that_does_not_fit_is_reported_as_damage(tmp_path: Path) -> None:
    connection = opened(tmp_path / "state.db")
    inbox = SqliteInbox(connection, 2_000)
    run(inbox.accept(inbound()))
    connection.execute("UPDATE inbox SET inbound = json_remove(inbound, '$.sender')")
    assert failure_of(run(inbox.pending())).kind == "fatal"


def test_an_inbox_id_is_never_used_again_after_the_newest_row_is_deleted(
    tmp_path: Path,
) -> None:
    connection = opened(tmp_path / "state.db")
    inbox = SqliteInbox(connection, 2_000)
    first = run(inbox.accept(inbound("1")))
    connection.execute("DELETE FROM inbox")
    second = run(inbox.accept(inbound("2")))
    assert isinstance(first, Accepted)
    assert isinstance(second, Accepted)
    assert second.inbox_id > first.inbox_id


def test_the_channel_and_native_id_are_saved_beside_the_message(
    tmp_path: Path,
) -> None:
    connection = opened(tmp_path / "state.db")
    run(SqliteInbox(connection, 2_000).accept(inbound("8812")))
    assert connection.execute("SELECT channel, native_id FROM inbox").fetchall() == [
        ("telegram", "8812")
    ]
