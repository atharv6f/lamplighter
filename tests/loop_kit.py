"""Builds the loop's ports on fakes, so each test of the loop starts the same way."""

from dataclasses import dataclass

from lamplighter.adapters import CountingIds, StubTools
from lamplighter.adapters.in_memory import (
    InMemoryEventLog,
    InMemoryInbox,
    InMemoryOutbox,
)
from lamplighter.core import Deps
from lamplighter.ports import (
    Clock,
    Event,
    EventLog,
    Failure,
    Inbox,
    InboxEntry,
    ModelClient,
    Outbound,
    Outbox,
    ToolProvider,
)
from tests.fake_clock import FakeClock

MAX_RECORD_BYTES = 200_000


@dataclass
class Loop:
    """One set of ports, kept by their real types so a test can look inside."""

    log: EventLog
    inbox: Inbox
    outbox: Outbox
    model: ModelClient
    tools: ToolProvider
    clock: Clock
    ids: CountingIds

    @property
    def deps(self) -> Deps:
        return Deps(
            log=self.log,
            inbox=self.inbox,
            outbox=self.outbox,
            model=self.model,
            tools=self.tools,
            clock=self.clock,
            ids=self.ids,
        )

    async def events(self, session_id: str) -> tuple[Event, ...]:
        read = await self.log.read(session_id, 0)
        assert not isinstance(read, Failure), read
        return read

    async def pending(self) -> tuple[InboxEntry, ...]:
        rows = await self.inbox.pending()
        assert not isinstance(rows, Failure), rows
        return rows

    async def due(self) -> tuple[Outbound, ...]:
        rows = await self.outbox.due()
        assert not isinstance(rows, Failure), rows
        return rows


def in_memory(
    model: ModelClient,
    clock: Clock | None = None,
    tools: ToolProvider | None = None,
) -> Loop:
    """Give the loop's ports in memory, with the stub tools unless told otherwise."""
    clock = clock or FakeClock()
    return Loop(
        log=InMemoryEventLog({}, MAX_RECORD_BYTES),
        inbox=InMemoryInbox(MAX_RECORD_BYTES),
        outbox=InMemoryOutbox(MAX_RECORD_BYTES),
        model=model,
        tools=tools or StubTools(clock),
        clock=clock,
        ids=CountingIds(),
    )
