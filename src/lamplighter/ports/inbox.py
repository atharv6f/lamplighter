"""The port that saves each arriving message once, before the channel is told."""

from dataclasses import dataclass
from typing import Protocol

from lamplighter.ports.failure import Failure
from lamplighter.ports.inbound import Inbound


@dataclass(frozen=True, slots=True)
class Accepted:
    """What `accept` gives: the message's row, and whether this call made it."""

    inbox_id: int
    is_new: bool


@dataclass(frozen=True, slots=True)
class InboxEntry:
    """One saved message that has not been routed to a session yet."""

    inbox_id: int
    inbound: Inbound


class Inbox(Protocol):
    """Every arriving message, saved once, until it is routed to a session.

    Callers can rely on this:

    - `accept` returns only after the message is forced to the drive. Only
      then may the channel be told that the message arrived.
    - A second `accept` with the same channel and native id writes nothing
      and gives the first row's id, not new. The first copy is the one kept.
    - `pending` gives every message not yet routed, in the order they arrived.
    - `mark_routed` with the values already saved is a no-op. With other
      values it is `conflict`. An inbox id never given out is `invalid_input`.
    - An inbound message over the size limit is `too_large`, and one that
      breaks a rule is `invalid_input`. Nothing is written for either.
    - An inbox id is never used twice. Ids may have gaps.
    """

    async def accept(self, inbound: Inbound) -> Accepted | Failure:
        """Save an arriving message, or find the copy already saved."""
        ...

    async def pending(self) -> tuple[InboxEntry, ...] | Failure:
        """Give every message not yet routed, oldest first."""
        ...

    async def mark_routed(
        self, inbox_id: int, session_id: str, seq: int
    ) -> None | Failure:
        """Save which session and seq a message was saved to as an event."""
        ...
