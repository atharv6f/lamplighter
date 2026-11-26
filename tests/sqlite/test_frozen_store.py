"""A store written at layout version 1 must read the same under this code."""

import sqlite3
from pathlib import Path

from lamplighter.adapters.in_memory import (
    InMemoryEffectLedger,
    InMemoryEventLog,
    InMemoryInbox,
)
from lamplighter.adapters.sqlite import (
    SqliteEffectLedger,
    SqliteEventLog,
    SqliteInbox,
    SqliteOutbox,
)
from lamplighter.core import rebuild_messages
from lamplighter.ports import (
    Begun,
    EffectLedger,
    EventLog,
    Failure,
    Inbox,
    Message,
    TextPart,
)
from tests.fake_clock import FakeClock
from tests.frozen_samples import (
    DONE_ACTION,
    INTENDED_ACTION,
    REPLY,
    RESULT,
    write_samples,
)
from tests.sqlite.helpers import opened, run

FROZEN = Path(__file__).parents[1] / "stores" / "version_1.sql"
LIMIT = 4_194_304


async def everything(
    log: EventLog, ledger: EffectLedger, inbox: Inbox
) -> tuple[object, ...]:
    effects: list[object] = []
    for action in (DONE_ACTION, INTENDED_ACTION):
        found = await ledger.begin(action, None, None)
        assert isinstance(found, Begun), found
        assert not found.is_new
        effects.append(found.effect)
    return (
        await log.read("sess-a", 0),
        await log.read("sess-b", 0),
        await log.tail(0, 100),
        await log.sessions(None),
        await ledger.unresolved(),
        effects,
        await inbox.pending(),
    )


def test_the_frozen_version_1_store_reads_as_this_code_would_write_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    built = sqlite3.connect(path)
    built.executescript(FROZEN.read_text())
    built.close()
    connection = opened(path)
    from_file = run(
        everything(
            SqliteEventLog(connection, {}, LIMIT),
            SqliteEffectLedger(connection, FakeClock(), LIMIT),
            SqliteInbox(connection, LIMIT),
        )
    )
    fakes = (
        InMemoryEventLog({}, LIMIT),
        InMemoryEffectLedger(FakeClock(), LIMIT),
        InMemoryInbox(LIMIT),
    )
    run(write_samples(*fakes))
    expected = run(everything(*fakes))
    assert not any(isinstance(part, Failure) for part in from_file), from_file
    assert from_file == expected


def test_the_frozen_session_rebuilds_into_its_transcript(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    built = sqlite3.connect(path)
    built.executescript(FROZEN.read_text())
    built.close()
    events = run(SqliteEventLog(opened(path), {}, LIMIT).read("sess-a", 0))
    assert not isinstance(events, Failure), events
    assert rebuild_messages(events) == (
        Message(role="user", parts=(TextPart(text="What is on my list?"),)),
        REPLY.message,
        Message(role="user", parts=(RESULT,)),
    )


def test_the_frozen_version_1_store_becomes_version_2_with_an_empty_outbox(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    built = sqlite3.connect(path)
    built.executescript(FROZEN.read_text())
    built.close()
    connection = opened(path)
    assert connection.execute("PRAGMA user_version").fetchone() == (2,)
    assert run(SqliteOutbox(connection, FakeClock(), LIMIT).due()) == ()
    assert connection.execute("SELECT count(*) FROM outbox").fetchone() == (0,)
