"""A frozen store of tool calls, as the loop wrote it, must read the same now.

It holds every event the loop writes for reads and changes, and an effect
row in each state the ledger can reach, as `tests/frozen_tool_samples.py`
made them.
"""

import sqlite3
from collections.abc import Sequence
from pathlib import Path

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
    EventLog,
    Failure,
    Inbox,
    Outbox,
)
from tests.fake_clock import FakeClock
from tests.frozen_tool_samples import write_tool_samples
from tests.sqlite.helpers import opened, run

FROZEN = Path(__file__).parents[1] / "stores" / "version_2_tools.sql"
LIMIT = 4_194_304
STATES = {"intended", "done", "failed", "unknown", "resolved"}
SESSIONS = ("sess-a", "sess-b", "sess-c")


def frozen(tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "state.db"
    built = sqlite3.connect(path)
    built.executescript(FROZEN.read_text())
    built.close()
    return opened(path)


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


def test_the_frozen_tool_store_reads_as_this_code_would_write_it(
    tmp_path: Path,
) -> None:
    connection = frozen(tmp_path)
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


def test_it_holds_every_tool_payload_and_an_effect_in_each_state(
    tmp_path: Path,
) -> None:
    connection = frozen(tmp_path)
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
