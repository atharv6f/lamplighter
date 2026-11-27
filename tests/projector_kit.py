"""A log, a sink and a cursor for tests of the projector, each a test can trouble."""

from collections.abc import Awaitable, Callable, Sequence

from lamplighter.adapters.in_memory import InMemoryTraceSink
from lamplighter.ports import (
    Event,
    Failure,
    NewEvent,
    SessionInfo,
    SessionState,
    Span,
)

DOWN = Failure(
    kind="unavailable", effect="none", message="The trace store is down.", source="t"
)
BUSY = Failure(
    kind="unavailable", effect="none", message="The store is busy.", source="t"
)

type Hook = Callable[[int], Awaitable[None]]


class ListedLog:
    """Serves a list of saved events as a log that is only read.

    `shown` is how many of the events a reader sees, so a test can let the
    log grow one event at a time. It counts every read of a session.
    """

    def __init__(self, events: Sequence[Event], calls: list[str] | None = None) -> None:
        self.events = list(events)
        self.shown = len(self.events)
        self.reads = 0
        self.calls: list[str] = [] if calls is None else calls
        self._fail_read: Failure | None = None
        self._fail_tail: Failure | None = None

    def fail_next_read(self, failure: Failure) -> None:
        self._fail_read = failure

    def fail_next_tail(self, failure: Failure) -> None:
        self._fail_tail = failure

    async def read(
        self, session_id: str, after_seq: int
    ) -> tuple[Event, ...] | Failure:
        self.calls.append(f"log.read {session_id} {after_seq}")
        failed, self._fail_read = self._fail_read, None
        if failed is not None:
            return failed
        self.reads += 1
        return tuple(
            event
            for event in self.events[: self.shown]
            if event.session_id == session_id and event.seq > after_seq
        )

    async def tail(self, after_pos: int, limit: int) -> tuple[Event, ...] | Failure:
        self.calls.append(f"log.tail {after_pos} {limit}")
        failed, self._fail_tail = self._fail_tail, None
        if failed is not None:
            return failed
        later = [event for event in self.events[: self.shown] if event.pos > after_pos]
        return tuple(later[:limit])

    async def append(
        self, session_id: str, events: Sequence[NewEvent], expected_seq: int
    ) -> int | Failure:
        raise AssertionError("the projector never writes the log")

    async def sessions(
        self, state: SessionState | None
    ) -> tuple[SessionInfo, ...] | Failure:
        raise AssertionError("the projector never lists the sessions")


class TroubledSink:
    """A sink in memory that records every copy, and that a test can trouble.

    `down` fails every export. `fail_on` fails the exports with those
    numbers, counted from 1 over the exports that hold spans. `before` runs
    at the start of each such export and `after` once it has stored the
    batch, each given the export's number.
    """

    def __init__(self, calls: list[str] | None = None) -> None:
        self.store = InMemoryTraceSink()
        self.sent: list[Span] = []
        self.exports = 0
        self.down = False
        self.fail_on: set[int] = set()
        self.before: Hook | None = None
        self.after: Hook | None = None
        self.calls: list[str] = [] if calls is None else calls

    @property
    def leaves_this_mac(self) -> bool:
        return False

    async def export(self, spans: Sequence[Span]) -> Failure | None:
        self.calls.append(f"sink.export {len(spans)}")
        if not spans:
            return None
        self.exports += 1
        number = self.exports
        if self.before is not None:
            await self.before(number)
        if self.down or number in self.fail_on:
            return DOWN
        self.sent += spans
        stored = await self.store.export(spans)
        if self.after is not None:
            await self.after(number)
        return stored
