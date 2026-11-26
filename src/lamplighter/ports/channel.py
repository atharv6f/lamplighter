"""The port for a place where you talk to the agent: messages in, messages out."""

from collections.abc import AsyncGenerator
from typing import Protocol

from lamplighter.ports.failure import Failure
from lamplighter.ports.inbound import Inbound
from lamplighter.ports.outbound import DeliveryReceipt, Outbound


class Channel(Protocol):
    """One way in and out, such as a terminal.

    Callers can rely on this:

    - `receive` yields each arriving message as an `Inbound` the inbox
      accepts, with `sender.verified` set by the adapter. It ends when the
      channel has no more input.
    - `ack` tells the channel a message is saved. A channel that keeps a
      place in a stream moves on only then.
    - `send` shows one message and returns a receipt. It is not idempotent:
      a second `send` shows the message a second time.
    - A `send` that fails returns a `Failure`. Its `effect` says whether the
      message may have been shown anyway.
    """

    def receive(self) -> AsyncGenerator[Inbound]:
        """Yield each message as it arrives, until the input ends."""
        ...

    async def ack(self, native_id: str) -> None:
        """Say that the message with this id is saved."""
        ...

    async def send(self, out: Outbound) -> DeliveryReceipt | Failure:
        """Show one message."""
        ...
