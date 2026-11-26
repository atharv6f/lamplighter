"""A store written at layout version 2 must read the same under this code.

It holds every event type the loop writes and an outbox row in each
state, as `tests/frozen_v2_samples.py` made them.
"""

import sqlite3
from dataclasses import replace
from pathlib import Path

from lamplighter.adapters import ScriptedChannel
from lamplighter.adapters.in_memory import (
    InMemoryEventLog,
    InMemoryInbox,
    InMemoryOutbox,
)
from lamplighter.adapters.sqlite import SqliteEventLog, SqliteInbox, SqliteOutbox
from lamplighter.core import deliver_due, rebuild_messages, run_turn
from lamplighter.ports import EventLog, Failure, Inbox, Outbox
from tests.fake_clock import FakeClock
from tests.frozen_v2_samples import write_v2_samples
from tests.kill_kit import answers
from tests.loop_kit import in_memory
from tests.model_by_request import ModelByRequest
from tests.run_history import SPEC
from tests.sqlite.helpers import opened, run

FROZEN = Path(__file__).parents[1] / "stores" / "version_2.sql"
LIMIT = 4_194_304
EVERY_TYPE = {
    "session_started",
    "run_started",
    "user_message",
    "model_request",
    "model_response",
    "tool_intent",
    "tool_result",
    "limit_hit",
    "cancelled",
    "reply_ready",
    "run_end",
}


def frozen(tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "state.db"
    built = sqlite3.connect(path)
    built.executescript(FROZEN.read_text())
    built.close()
    return opened(path)


def stores(connection: sqlite3.Connection) -> tuple[EventLog, Inbox, Outbox]:
    return (
        SqliteEventLog(connection, {}, LIMIT),
        SqliteInbox(connection, LIMIT),
        SqliteOutbox(connection, FakeClock(), LIMIT),
    )


async def everything(log: EventLog, inbox: Inbox, outbox: Outbox) -> tuple[object, ...]:
    return (
        await log.read("sess-a", 0),
        await log.read("sess-b", 0),
        await log.tail(0, 100),
        await log.sessions(None),
        await inbox.pending(),
        await outbox.due(),
    )


def test_the_frozen_version_2_store_reads_as_this_code_would_write_it(
    tmp_path: Path,
) -> None:
    connection = frozen(tmp_path)
    assert connection.execute("PRAGMA user_version").fetchone() == (2,)
    from_file = run(everything(*stores(connection)))
    fakes = (
        InMemoryEventLog({}, LIMIT),
        InMemoryInbox(LIMIT),
        InMemoryOutbox(LIMIT),
    )
    run(write_v2_samples(*fakes))
    assert not any(isinstance(part, Failure) for part in from_file), from_file
    assert from_file == run(everything(*fakes))


def test_it_holds_every_event_type_and_every_outbox_state(tmp_path: Path) -> None:
    connection = frozen(tmp_path)
    types = {row[0] for row in connection.execute("SELECT type FROM events")}
    assert types == EVERY_TYPE
    rows = connection.execute(
        "SELECT state, maybe_duplicate FROM outbox ORDER BY outbox_id"
    ).fetchall()
    assert rows == [("sent", 0), ("sending", 0), ("pending", 1), ("pending", 0)]


def test_each_frozen_session_rebuilds_and_has_nothing_left_to_run(
    tmp_path: Path,
) -> None:
    connection = frozen(tmp_path)
    log, inbox, outbox = stores(connection)
    # The model is never called: every run in the store has ended.
    model = ModelByRequest(answers({}))
    deps = replace(in_memory(model).deps, log=log, inbox=inbox, outbox=outbox)
    before = run(log.tail(0, 1_000))
    for session in ("sess-a", "sess-b"):
        events = run(log.read(session, 0))
        assert not isinstance(events, Failure)
        assert not isinstance(rebuild_messages(events), Failure)
        assert run(run_turn(session, SPEC, deps)) is None
    assert run(log.tail(0, 1_000)) == before
    assert model.requests == []


def test_the_frozen_outbox_sends_its_rows_and_labels_the_possible_repeats(
    tmp_path: Path,
) -> None:
    _, _, outbox = stores(frozen(tmp_path))
    channel = ScriptedChannel([], FakeClock())
    for session in ("sess-a", "sess-b"):
        assert run(deliver_due(session, outbox, channel)) is None
    assert [out.maybe_duplicate for out in channel.sent] == [True, True, False]
    assert run(outbox.due()) == ()
