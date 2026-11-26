"""The port for the record of what happened, and the records that cross it."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from lamplighter.ports.failure import Failure
from lamplighter.ports.json_value import JsonObject

USER_MESSAGE = "user_message"
RUN_STARTED = "run_started"
RUN_END = "run_end"


@dataclass(frozen=True, slots=True)
class NewEvent:
    """One event as a caller hands it to `append`.

    `at` is the time text form, stamped by the caller. `step` counts the
    model requests of a run from 1. `event_id` is set only on a user message,
    from its channel and the channel's own id. The log works out every other
    id from the event's place.
    """

    type: str
    v: int
    payload: JsonObject
    at: str
    run_id: str | None = None
    step: int | None = None
    duration_ms: int | None = None
    cause: str | None = None
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class Event:
    """One saved event, as a reader gets it.

    `seq` is its place in its session, from 1 with no gaps. `pos` is its
    place across all sessions. It only grows and is never used twice.
    """

    session_id: str
    seq: int
    pos: int
    event_id: str
    type: str
    v: int
    payload: JsonObject
    at: str
    run_id: str | None
    step: int | None
    duration_ms: int | None
    cause: str | None


type SessionState = Literal["unfinished", "idle"]


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """One session in short.

    `state` is "unfinished" when the session has a run_started event with no
    run_end after it, and "idle" otherwise.
    """

    session_id: str
    last_seq: int
    last_at: str
    state: SessionState


# Turns the payload of one type at version n into that payload at n + 1.
type Upcaster = Callable[[JsonObject], JsonObject]

# The upcaster for each event type and version that has a newer form.
type Upcasters = Mapping[tuple[str, int], Upcaster]


class EventLog(Protocol):
    """The record of what happened, per session, saved once and never changed.

    Callers can rely on this:

    - `append` gives the batch the places `expected_seq + 1` onward, in the
      order given, and returns the new last place. It returns only after the
      batch is forced to the drive. After a kill at any moment the batch is
      wholly there or wholly absent. It is never left part way.
    - If the session's last place is not `expected_seq`, or an `event_id` in
      the batch is already in the log, `append` returns `conflict` and writes
      nothing. The caller reads the session again before it decides anything.
    - An empty batch, or any event that breaks a rule, is `invalid_input`. Any
      payload over the size limit is `too_large`. Either refuses the batch.
    - `read` returns whole batches only, in order, and checks that the places
      have no gap. A gap is `fatal`.
    - `tail` never skips and never repeats a position. Once it has returned
      position p, no event with a lower position appears later.
    - Both readers pass each event through the upcasters, so it arrives in
      the newest form this code knows. An event of a type the code does not
      know is passed on unchanged.
    - Every method returns a whole tuple and never a cursor that reads later.
    """

    async def append(
        self, session_id: str, events: Sequence[NewEvent], expected_seq: int
    ) -> int | Failure:
        """Save a batch at the end of a session, and give the new last place."""
        ...

    async def read(
        self, session_id: str, after_seq: int
    ) -> tuple[Event, ...] | Failure:
        """Give every event of a session after `after_seq`, in order."""
        ...

    async def sessions(
        self, state: SessionState | None
    ) -> tuple[SessionInfo, ...] | Failure:
        """Give one summary per session in that state, or all, by session id."""
        ...

    async def tail(self, after_pos: int, limit: int) -> tuple[Event, ...] | Failure:
        """Give up to `limit` events from all sessions after `after_pos`."""
        ...
