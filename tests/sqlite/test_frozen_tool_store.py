"""Two frozen stores of tool calls, as the loop wrote them, must read the same now.

Each holds every event the loop writes for reads and changes, and an effect
row in each state the ledger can reach, as `tests/frozen_tool_samples.py`
made them. The first was made before a model request saved its model and
a failed tool its failure kind, so it is read back row for row. The second
was made after, and is compared with what this code writes today.
"""

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

from lamplighter.adapters.in_memory import (
    InMemoryBlobStore,
    InMemoryEffectLedger,
    InMemoryEventLog,
    InMemoryInbox,
    InMemoryOutbox,
)
from lamplighter.adapters.sqlite import (
    SqliteEffectLedger,
    SqliteEventLog,
    SqliteInbox,
    SqliteOutbox,
)
from lamplighter.ports import (
    Action,
    Begun,
    EffectLedger,
    Event,
    EventLog,
    Failure,
    Inbox,
    Outbox,
)
from tests.fake_clock import FakeClock
from tests.frozen_tool_samples import write_tool_samples
from tests.sqlite.helpers import opened, run

STORES = Path(__file__).parents[1] / "stores"
FIRST = STORES / "version_2_tools.sql"
WITH_MODEL = STORES / "version_2_tools_with_model.sql"
LIMIT = 4_194_304
STATES = {"intended", "done", "failed", "unknown", "resolved"}
SESSIONS = ("sess-a", "sess-b", "sess-c")


def frozen(tmp_path: Path, text: Path) -> sqlite3.Connection:
    path = tmp_path / "state.db"
    built = sqlite3.connect(path)
    built.executescript(text.read_text())
    built.close()
    return opened(path)


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


async def everything(
    log: EventLog,
    inbox: Inbox,
    outbox: Outbox,
    ledger: EffectLedger,
    actions: Sequence[Action],
) -> tuple[object, ...]:
    effects: list[object] = []
    for action in actions:
        found = await ledger.begin(action, None, None)
        assert isinstance(found, Begun), found
        assert not found.is_new
        effects.append(found.effect)
    reads = [await log.read(session, 0) for session in SESSIONS]
    return (
        *reads,
        await log.tail(0, 1_000),
        await log.sessions(None),
        await ledger.unresolved(),
        effects,
        await inbox.pending(),
        await outbox.due(),
    )


def fakes() -> tuple[tuple[object, ...], tuple[Action, ...]]:
    log = InMemoryEventLog({}, LIMIT)
    inbox = InMemoryInbox(LIMIT)
    outbox = InMemoryOutbox(LIMIT)
    ledger = InMemoryEffectLedger(FakeClock(), LIMIT)
    actions = run(
        write_tool_samples(log, inbox, outbox, ledger, InMemoryBlobStore(LIMIT * 16))
    )
    return run(everything(log, inbox, outbox, ledger, actions)), actions


def test_the_first_frozen_tool_store_reads_back_what_its_rows_hold(
    tmp_path: Path,
) -> None:
    connection = frozen(tmp_path, FIRST)
    log = SqliteEventLog(connection, {}, LIMIT)
    every: list[Event] = []
    for session in SESSIONS:
        read = run(log.read(session, 0))
        assert not isinstance(read, Failure), read
        assert list(read) == rows_as_events(connection, session)
        every.extend(read)
    assert run(log.tail(0, 1_000)) == tuple(sorted(every, key=lambda e: e.pos))
    # It was written before these fields were saved, so it is the store of
    # events that lack them.
    for event in every:
        for field in ("model", "provider", "failure_kind"):
            assert field not in event.payload, (event.seq, field)


def test_the_frozen_tool_store_with_the_model_reads_as_this_code_would_write_it(
    tmp_path: Path,
) -> None:
    connection = frozen(tmp_path, WITH_MODEL)
    expected, actions = fakes()
    from_file = run(
        everything(
            SqliteEventLog(connection, {}, LIMIT),
            SqliteInbox(connection, LIMIT),
            SqliteOutbox(connection, FakeClock(), LIMIT),
            SqliteEffectLedger(connection, FakeClock(), LIMIT),
            actions,
        )
    )
    assert not any(isinstance(part, Failure) for part in from_file), from_file
    assert from_file == expected


def test_the_second_store_saves_the_model_and_the_provider_on_each_request(
    tmp_path: Path,
) -> None:
    connection = frozen(tmp_path, WITH_MODEL)
    payloads = [
        json.loads(row[0])
        for row in connection.execute(
            "SELECT payload FROM events WHERE type = 'model_request'"
        )
    ]
    assert len(payloads) == 8
    for payload in payloads:
        assert payload["model"] == "made-up-1"
        assert payload["provider"] == "made-up"


@pytest.mark.parametrize("text", [FIRST, WITH_MODEL], ids=["first", "with_model"])
def test_it_holds_every_tool_payload_and_an_effect_in_each_state(
    tmp_path: Path, text: Path
) -> None:
    connection = frozen(tmp_path, text)
    states = {row[0] for row in connection.execute("SELECT state FROM effects")}
    assert states == STATES
    payloads = [
        row[0]
        for row in connection.execute(
            "SELECT payload FROM events WHERE type IN ('tool_intent', 'tool_result')"
        )
    ]
    for piece in (
        '"action_class":"read"',
        '"effect_id":',
        '"full_output":',
        '"tainted":true',
        '"harness":"arguments_invalid"',
        '"harness":"unconfirmed"',
        '"harness":"not_run"',
        '"harness":"interrupted"',
    ):
        assert any(piece in payload for payload in payloads), piece
    types = {row[0] for row in connection.execute("SELECT type FROM events")}
    assert {"limit_hit", "cancelled", "tool_intent", "tool_result"} <= types
