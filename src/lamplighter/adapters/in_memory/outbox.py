"""An outbox kept in memory, for tests. It refuses what the real one does."""

import dataclasses
from dataclasses import dataclass
from typing import Literal

from lamplighter.ports import (
    DeliveryReceipt,
    Failure,
    Outbound,
    as_saved,
    check_enqueue,
    check_receipt,
    unknown_outbound,
)

_SOURCE = "Outbox/in_memory"


@dataclass
class _Row:
    outbound: Outbound
    state: Literal["pending", "sending", "sent"] = "pending"
    may_have_gone: bool = False
    receipt: DeliveryReceipt | None = None
    last_failure: Failure | None = None


class InMemoryOutbox:
    """Keeps every row in a dict, in the order enqueued. Satisfies Outbox."""

    def __init__(self, max_record_bytes: int) -> None:
        self._max_record_bytes = max_record_bytes
        self._rows: dict[str, _Row] = {}

    async def enqueue(self, out: Outbound) -> None | Failure:
        """Save a message to send, or find the same one already saved."""
        refused = check_enqueue(out, self._max_record_bytes, _SOURCE)
        if refused is not None:
            return refused
        saved = as_saved(Outbound, out)
        row = self._rows.get(out.out_id)
        if row is None:
            self._rows[out.out_id] = _Row(outbound=saved)
            return None
        if row.outbound == saved:
            return None
        return _conflict("Another message is saved under this out_id.")

    async def due(self) -> tuple[Outbound, ...] | Failure:
        """Give every message not yet sent, oldest first."""
        return tuple(
            dataclasses.replace(
                row.outbound,
                maybe_duplicate=row.state == "sending" or row.may_have_gone,
            )
            for row in self._rows.values()
            if row.state != "sent"
        )

    async def start_send(self, out_id: str) -> None | Failure:
        """Save that a send of this row is about to begin."""
        row = self._rows.get(out_id)
        if row is None:
            return unknown_outbound(out_id, _SOURCE)
        if row.state == "sent":
            return _conflict("The message was already sent.")
        if row.state == "sending":
            row.may_have_gone = True
        row.state = "sending"
        return None

    async def mark_sent(self, out_id: str, receipt: DeliveryReceipt) -> None | Failure:
        """Save that the row was sent, with the channel's receipt."""
        refused = check_receipt(receipt, _SOURCE)
        if refused is not None:
            return refused
        row = self._rows.get(out_id)
        if row is None:
            return unknown_outbound(out_id, _SOURCE)
        saved = as_saved(DeliveryReceipt, receipt)
        if row.state == "sent":
            if row.receipt == saved:
                return None
            return _conflict("The message was already sent with another receipt.")
        row.state = "sent"
        row.receipt = saved
        return None

    async def mark_failed(self, out_id: str, failure: Failure) -> None | Failure:
        """Save that a send failed, so the row is tried again later."""
        row = self._rows.get(out_id)
        if row is None:
            return unknown_outbound(out_id, _SOURCE)
        if row.state == "sent":
            return _conflict("The message was already sent.")
        row.state = "pending"
        row.last_failure = failure
        if failure.effect != "none":
            row.may_have_gone = True
        return None


def _conflict(message: str) -> Failure:
    return Failure(kind="conflict", effect="none", message=message, source=_SOURCE)
