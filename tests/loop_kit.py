"""Builds the loop's ports on fakes, so each test of the loop starts the same way."""

from dataclasses import dataclass

from lamplighter.adapters import (
    CountingIds,
    ScriptedBreak,
    ScriptedReply,
    ScriptedTool,
    ScriptedTools,
    StubTools,
)
from lamplighter.adapters.in_memory import (
    InMemoryEventLog,
    InMemoryInbox,
    InMemoryOutbox,
)
from lamplighter.core import Deps, RunningSessions
from lamplighter.ports import (
    Clock,
    Event,
    EventLog,
    Failure,
    Inbox,
    InboxEntry,
    JsonObject,
    ModelClient,
    ModelRequest,
    Outbound,
    Outbox,
    ToolProvider,
    ToolResult,
    ToolSpec,
)
from tests.model_by_request import Choose
from tests.run_history import SESSION, stop
from tests.waiting_clock import JumpingClock

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
    running: RunningSessions

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
            running=self.running,
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
    clock = clock or JumpingClock()
    return Loop(
        log=InMemoryEventLog({}, MAX_RECORD_BYTES),
        inbox=InMemoryInbox(MAX_RECORD_BYTES),
        outbox=InMemoryOutbox(MAX_RECORD_BYTES),
        model=model,
        tools=tools or StubTools(clock),
        clock=clock,
        ids=CountingIds(),
        running=RunningSessions(),
    )


def in_order(*turns: ScriptedReply | ScriptedBreak) -> Choose:
    """Play the turns one per call, whatever the request, for tests with no kill."""
    played = iter(turns)

    def choose(request: ModelRequest) -> ScriptedReply | ScriptedBreak:
        del request
        return next(played)

    return choose


NOTE_SPEC = ToolSpec(
    name="note",
    description="Note a number.",
    arguments_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
)
TICK_SPEC = ToolSpec(
    name="tick",
    description="Count one more.",
    arguments_schema={"type": "object", "properties": {}},
)


def note_tools(clock: Clock, seconds: float = 0.0) -> ScriptedTools:
    """Give `note`, whose result depends only on its argument, and `tick`, whose
    result is new each time. With `seconds`, `note` takes that long on the clock.
    """
    ticks = [0]

    async def note(arguments: JsonObject) -> ToolResult:
        if seconds:
            await clock.sleep(seconds)
        return ToolResult(text=f"Noted {arguments.get('n')}.", is_error=False)

    async def tick(arguments: JsonObject) -> ToolResult:
        del arguments
        ticks[0] += 1
        return ToolResult(text=f"Tick {ticks[0]}.", is_error=False)

    return ScriptedTools([ScriptedTool(NOTE_SPEC, note), ScriptedTool(TICK_SPEC, tick)])


async def stop_now(loop: Loop, native_id: str, session_id: str = SESSION) -> None:
    """Save a stop and raise its flag, in the order the intake does."""
    accepted = await loop.inbox.accept(stop(native_id, session_id))
    assert not isinstance(accepted, Failure), accepted
    loop.running.ask_stop(session_id)
