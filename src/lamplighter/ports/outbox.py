"""The port that keeps each message to you until a channel has shown it."""

from typing import Protocol

from lamplighter.ports.failure import Failure
from lamplighter.ports.outbound import DeliveryReceipt, Outbound


class Outbox(Protocol):
    """Every message to you, saved before it is sent and marked once it is.

    A row is "pending", "sending" or "sent". Callers can rely on this:

    - `enqueue` returns only after the row is forced to the drive. A second
      `enqueue` with the same `out_id` and the same content writes nothing.
      The same `out_id` with other content is `conflict`.
    - `due` gives every row that is not sent, in the order they were
      enqueued. A row that is "sending", or that carries the mark below,
      comes back with `maybe_duplicate` set.
    - `start_send` moves a row to "sending" and is forced to the drive
      before it returns. The caller calls it before the send. If the row is
      already "sending", a send was cut off by a kill, so the row also gets
      a lasting mark that it may have gone out.
    - `mark_sent` moves a "pending" or "sending" row to "sent" and saves the
      receipt. A second call with the same receipt is a no-op.
    - `mark_failed` moves a row back to "pending" and saves the failure. If
      the failure's effect is not "none", the row gets the same lasting mark.
    - The mark is never taken off. Once a row may have gone out, every later
      copy says so.
    - `start_send` or `mark_failed` on a sent row is `conflict`. So is
      `mark_sent` on a sent row with a different receipt.
    - An `out_id` the outbox never saw is `invalid_input`. An Outbound whose
      saved JSON is over the size limit is `too_large`.
    """

    async def enqueue(self, out: Outbound) -> None | Failure:
        """Save a message to send, or find the same one already saved."""
        ...

    async def due(self) -> tuple[Outbound, ...] | Failure:
        """Give every message not yet sent, oldest first."""
        ...

    async def start_send(self, out_id: str) -> None | Failure:
        """Save that a send of this row is about to begin."""
        ...

    async def mark_sent(self, out_id: str, receipt: DeliveryReceipt) -> None | Failure:
        """Save that the row was sent, with the channel's receipt."""
        ...

    async def mark_failed(self, out_id: str, failure: Failure) -> None | Failure:
        """Save that a send failed, so the row is tried again later."""
        ...
