"""Runs the chat process on fakes: the intake and the driver, with every port counted.

The stores are made once and kept, so a test can build a second process on
the same stores after the first was killed, as a restart would.
"""

import asyncio
from dataclasses import dataclass, field

from lamplighter.adapters import CountingIds, ScriptedChannel, ScriptedReply
from lamplighter.adapters.in_memory import (
    InMemoryEventLog,
    InMemoryInbox,
    InMemoryOutbox,
)
from lamplighter.core import (
    Deps,
    Observers,
    RunningSessions,
    RunSpec,
    Wake,
    drive,
    run_intake,
)
from lamplighter.ports import (
    Event,
    EventLog,
    Failure,
    Inbound,
    Inbox,
    ModelRequest,
    Outbound,
    Outbox,
    TextPart,
    ToolCallPart,
    Usage,
)
from tests.loop_kit import note_tools
from tests.model_by_request import ModelByRequest, assistant_turns
from tests.port_counter import (
    CountedChannel,
    CountedInbox,
    CountedLog,
    CountedModel,
    CountedOutbox,
    CountedTools,
    Counter,
)
from tests.run_history import SESSION
from tests.waiting_clock import JumpingClock

MAX_RECORD_BYTES = 200_000
CHAT_SECONDS = 20.0
USAGE = Usage(input_uncached=100, cache_read=0, cache_write=0, output=20)


def _calls(*parts: ToolCallPart) -> ScriptedReply:
    return ScriptedReply(parts, "tool_calls", USAGE)


def _text(text: str) -> ScriptedReply:
    return ScriptedReply((TextPart(text=text),), "end", USAGE)


def two_runs(request: ModelRequest) -> ScriptedReply:
    """Three steps for the first message, two for the second.

    The reply depends only on how many replies the request holds, so a
    request made again after a kill gets the same reply.
    """
    match assistant_turns(request):
        case 0:
            return _calls(
                ToolCallPart(id="c1", name="note", arguments={"n": 1}),
                ToolCallPart(id="c2", name="tick", arguments={}),
            )
        case 1:
            return _calls(ToolCallPart(id="c3", name="weather", arguments={}))
        case 2:
            return _text("First answer.")
        case 3:
            return _calls(ToolCallPart(id="c4", name="note", arguments={"n": 4}))
        case _:
            return _text("Second answer.")


@dataclass
class Stores:
    """The stores that outlive a process."""

    log: EventLog = field(
        default_factory=lambda: InMemoryEventLog({}, MAX_RECORD_BYTES)
    )
    inbox: Inbox = field(default_factory=lambda: InMemoryInbox(MAX_RECORD_BYTES))
    outbox: Outbox = field(default_factory=lambda: InMemoryOutbox(MAX_RECORD_BYTES))


@dataclass
class Process:
    """One chat process on the stores, with each port counted."""

    deps: Deps
    channel: CountedChannel
    wake: Wake
    counter: Counter
    model: ModelByRequest


def process(
    stores: Stores,
    channel: ScriptedChannel,
    clock: JumpingClock,
    ids: CountingIds,
    counter: Counter,
) -> Process:
    model = ModelByRequest(two_runs)
    deps = Deps(
        log=CountedLog(stores.log, counter),
        inbox=CountedInbox(stores.inbox, counter),
        outbox=CountedOutbox(stores.outbox, counter),
        model=CountedModel(model, counter),
        tools=CountedTools(note_tools(clock), counter),
        clock=clock,
        ids=ids,
        observers=Observers(),
        running=RunningSessions(),
    )
    return Process(deps, CountedChannel(channel, counter), Wake(), counter, model)


async def chat(made: Process, spec: RunSpec) -> Failure | None:
    """Run the intake and the driver together, as `chat` does, until both end."""
    wake = made.wake
    intake = asyncio.ensure_future(
        run_intake(
            made.channel, made.deps.inbox, made.deps.running, wake.wake, wake.end_input
        )
    )
    try:
        # A driver that misses a wake waits for ever. The bound turns that
        # into a failed test. The clocks here are virtual, so this is far
        # longer than any chat takes.
        async with asyncio.timeout(CHAT_SECONDS):
            driven = await drive(
                SESSION, spec, made.deps, made.channel, wake, lambda: None
            )
    finally:
        if not intake.done():
            intake.cancel()
    taken = await asyncio.gather(intake, return_exceptions=True)
    if driven is not None:
        return driven
    return taken[0] if isinstance(taken[0], Failure) else None


@dataclass
class Outcome:
    """What the stores hold once the process has ended."""

    events: tuple[Event, ...]
    pending: tuple[object, ...]
    due: tuple[Outbound, ...]
    sent: list[Outbound]

    @property
    def run_ends(self) -> list[str]:
        return [str(e.payload["reason"]) for e in self.events if e.type == "run_end"]

    def texts_of(self, kind: str) -> list[str]:
        return [str(e.payload["text"]) for e in self.events if e.type == kind]


async def outcome(stores: Stores, channel: ScriptedChannel) -> Outcome:
    events = await stores.log.read(SESSION, 0)
    pending = await stores.inbox.pending()
    due = await stores.outbox.due()
    assert not isinstance(events, Failure), events
    assert not isinstance(pending, Failure), pending
    assert not isinstance(due, Failure), due
    return Outcome(events, pending, due, list(channel.sent))


def first_and_second(conversation: str = SESSION) -> list[Inbound]:
    from tests.run_history import inbound

    return [
        inbound("m1", "Note one and tick.", session=conversation),
        inbound("m2", "Now note four.", session=conversation),
    ]
