"""Wraps every port the chat process uses, so a test can act at any one call.

Each call is numbered from 1 across all ports. A hook runs just before the
call and just after it has taken effect. The kill tests raise there, and
the arrival sweeps make a message or a stop arrive there.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from lamplighter.ports import (
    Accepted,
    Channel,
    DeliveryReceipt,
    Event,
    EventLog,
    Failure,
    Inbound,
    Inbox,
    InboxEntry,
    JsonObject,
    ModelClient,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    NewEvent,
    Outbound,
    Outbox,
    Plan,
    SessionInfo,
    SessionState,
    ToolEntry,
    ToolProvider,
    ToolResult,
)

type When = Literal["before", "after"]
type Hook = Callable[[int, When, str], Awaitable[None]]


async def no_hook(number: int, when: When, name: str) -> None:
    del number, when, name


@dataclass
class Counter:
    """Numbers every call and runs the hook around it."""

    hook: Hook = no_hook
    names: list[str] = field(default_factory=list[str])

    @property
    def calls(self) -> int:
        return len(self.names)

    async def around[T](self, name: str, call: Callable[[], Awaitable[T]]) -> T:
        self.names.append(name)
        number = len(self.names)
        await self.hook(number, "before", name)
        result = await call()
        await self.hook(number, "after", name)
        return result


class CountedLog:
    def __init__(self, inner: EventLog, counter: Counter) -> None:
        self.inner, self.counter = inner, counter

    async def append(
        self, session_id: str, events: Sequence[NewEvent], expected_seq: int
    ) -> int | Failure:
        name = "log.append " + ",".join(event.type for event in events)
        return await self.counter.around(
            name, lambda: self.inner.append(session_id, events, expected_seq)
        )

    async def read(
        self, session_id: str, after_seq: int
    ) -> tuple[Event, ...] | Failure:
        return await self.counter.around(
            "log.read", lambda: self.inner.read(session_id, after_seq)
        )

    async def sessions(
        self, state: SessionState | None
    ) -> tuple[SessionInfo, ...] | Failure:
        return await self.counter.around(
            "log.sessions", lambda: self.inner.sessions(state)
        )

    async def tail(self, after_pos: int, limit: int) -> tuple[Event, ...] | Failure:
        return await self.counter.around(
            "log.tail", lambda: self.inner.tail(after_pos, limit)
        )


class CountedInbox:
    def __init__(self, inner: Inbox, counter: Counter) -> None:
        self.inner, self.counter = inner, counter

    async def accept(self, inbound: Inbound) -> Accepted | Failure:
        return await self.counter.around(
            f"inbox.accept {inbound.native_id}", lambda: self.inner.accept(inbound)
        )

    async def pending(self) -> tuple[InboxEntry, ...] | Failure:
        return await self.counter.around("inbox.pending", self.inner.pending)

    async def mark_routed(
        self, inbox_id: int, session_id: str, seq: int
    ) -> None | Failure:
        return await self.counter.around(
            "inbox.mark_routed",
            lambda: self.inner.mark_routed(inbox_id, session_id, seq),
        )


class CountedOutbox:
    def __init__(self, inner: Outbox, counter: Counter) -> None:
        self.inner, self.counter = inner, counter

    async def enqueue(self, out: Outbound) -> None | Failure:
        return await self.counter.around(
            "outbox.enqueue", lambda: self.inner.enqueue(out)
        )

    async def due(self) -> tuple[Outbound, ...] | Failure:
        return await self.counter.around("outbox.due", self.inner.due)

    async def start_send(self, out_id: str) -> None | Failure:
        return await self.counter.around(
            "outbox.start_send", lambda: self.inner.start_send(out_id)
        )

    async def mark_sent(self, out_id: str, receipt: DeliveryReceipt) -> None | Failure:
        return await self.counter.around(
            "outbox.mark_sent", lambda: self.inner.mark_sent(out_id, receipt)
        )

    async def mark_failed(self, out_id: str, failure: Failure) -> None | Failure:
        return await self.counter.around(
            "outbox.mark_failed", lambda: self.inner.mark_failed(out_id, failure)
        )


class CountedModel:
    """Counts one call per stream: before its first event, after its last."""

    def __init__(self, inner: ModelClient, counter: Counter) -> None:
        self.inner, self.counter = inner, counter

    @property
    def profile(self) -> ModelProfile:
        return self.inner.profile

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelEvent]:
        self.counter.names.append("model.stream")
        number = self.counter.calls
        await self.counter.hook(number, "before", "model.stream")
        async for event in self.inner.stream(request):
            yield event
        await self.counter.hook(number, "after", "model.stream")

    async def count_tokens(self, request: ModelRequest) -> int | Failure:
        return await self.inner.count_tokens(request)


class CountedTools:
    def __init__(self, inner: ToolProvider, counter: Counter) -> None:
        self.inner, self.counter = inner, counter

    def list_tools(self) -> tuple[ToolEntry, ...]:
        return self.inner.list_tools()

    async def call(self, name: str, arguments: JsonObject) -> ToolResult | Failure:
        return await self.counter.around(
            f"tools.call {name}", lambda: self.inner.call(name, arguments)
        )

    async def plan(
        self, name: str, arguments: JsonObject
    ) -> Plan | ToolResult | Failure:
        return await self.counter.around(
            f"tools.plan {name}", lambda: self.inner.plan(name, arguments)
        )

    async def apply(
        self, name: str, arguments: JsonObject, plan: Plan
    ) -> ToolResult | Failure:
        return await self.counter.around(
            f"tools.apply {name}", lambda: self.inner.apply(name, arguments, plan)
        )

    async def check(self, probe: JsonObject) -> bool | None:
        return await self.counter.around("tools.check", lambda: self.inner.check(probe))


class CountedChannel:
    """Counts `ack` and `send`. What arrives is not a call the process makes."""

    def __init__(self, inner: Channel, counter: Counter) -> None:
        self.inner, self.counter = inner, counter

    def receive(self) -> AsyncGenerator[Inbound]:
        return self.inner.receive()

    async def ack(self, native_id: str) -> None:
        await self.counter.around("channel.ack", lambda: self.inner.ack(native_id))

    async def send(self, out: Outbound) -> DeliveryReceipt | Failure:
        return await self.counter.around("channel.send", lambda: self.inner.send(out))
