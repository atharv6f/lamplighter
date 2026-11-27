"""A store written at layout version 2 must read the same under this code.

It holds every event type the loop wrote when the file was made, and an
outbox row in each state. The loop has changed since: an intent now has a
class, and a read cut off by a kill is run once more. So the file is no
longer compared with what this code writes. It is read back row for row,
and its intent with no class is answered as it was then.
"""

import dataclasses
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from lamplighter.adapters import ScriptedChannel
from lamplighter.adapters.sqlite import SqliteEventLog, SqliteInbox, SqliteOutbox
from lamplighter.core import deliver_due, rebuild_messages, run_turn
from lamplighter.core.next_move import AnswerCall, RunCall, next_move
from lamplighter.core.run_view import RunView, view_of
from lamplighter.ports import Event, EventLog, Failure, Inbox, Outbox
from tests.fake_clock import FakeClock
from tests.kill_kit import answers
from tests.loop_kit import in_memory
from tests.model_by_request import ModelByRequest
from tests.run_history import SPEC, TOOLS
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


def rows_as_events(connection: sqlite3.Connection, session: str) -> list[Event]:
    """Build the events of a session from its rows, without the adapter."""
    rows = connection.execute(
        "SELECT session_id, seq, pos, event_id, type, v, payload, at, run_id,"
        " step, duration_ms, cause FROM events WHERE session_id = ? ORDER BY seq",
        (session,),
    ).fetchall()
    return [
        Event(
            session_id=row[0],
            seq=row[1],
            pos=row[2],
            event_id=row[3],
            type=row[4],
            v=row[5],
            payload=json.loads(row[6]),
            at=row[7],
            run_id=row[8],
            step=row[9],
            duration_ms=row[10],
            cause=row[11],
        )
        for row in rows
    ]


def test_the_frozen_version_2_store_reads_back_what_its_rows_hold(
    tmp_path: Path,
) -> None:
    connection = frozen(tmp_path)
    assert connection.execute("PRAGMA user_version").fetchone() == (2,)
    log, inbox, _ = stores(connection)
    every: list[Event] = []
    for session in ("sess-a", "sess-b"):
        read = run(log.read(session, 0))
        assert not isinstance(read, Failure), read
        assert list(read) == rows_as_events(connection, session)
        every.extend(read)
    assert run(log.tail(0, 100)) == tuple(sorted(every, key=lambda e: e.pos))
    sessions = run(log.sessions(None))
    assert not isinstance(sessions, Failure)
    assert [(info.session_id, info.state) for info in sessions] == [
        ("sess-a", "idle"),
        ("sess-b", "idle"),
    ]
    assert run(inbox.pending()) == ()


def test_its_intent_with_no_class_is_answered_interrupted_and_not_run_again(
    tmp_path: Path,
) -> None:
    log, _, _ = stores(frozen(tmp_path))
    events = run(log.read("sess-b", 0))
    assert not isinstance(events, Failure)
    (intent,) = [e for e in events if e.type == "tool_intent"]
    assert "action_class" not in intent.payload
    cut_off = view_of(events[: intent.seq])
    assert isinstance(cut_off, RunView)
    answered = next_move(cut_off, (), SPEC, TOOLS)
    assert isinstance(answered, AnswerCall)
    assert answered.harness == "interrupted"
    # The same intent with a class would be run once more: the class decides.
    classed = dataclasses.replace(
        intent, payload={**intent.payload, "action_class": "read"}
    )
    with_class = view_of((*events[: intent.seq - 1], classed))
    assert isinstance(with_class, RunView)
    assert isinstance(next_move(with_class, (), SPEC, TOOLS), RunCall)


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
