"""Checks the input of an outbox call, so every outbox refuses the same things."""

from lamplighter.ports.event_check import invalid
from lamplighter.ports.failure import Failure
from lamplighter.ports.json_text import saved_size
from lamplighter.ports.outbound import DeliveryReceipt, Outbound
from lamplighter.ports.value_rules import is_hash, is_id, is_time_text


def check_enqueue(out: Outbound, max_record_bytes: int, source: str) -> Failure | None:
    """Give the Failure for a message no outbox may take, or None."""
    problem = _outbound_problem(out)
    if problem is not None:
        return invalid(problem, source)
    try:
        size = saved_size(out)
    except ValueError:
        return invalid("The message cannot be saved as JSON.", source)
    if size > max_record_bytes:
        return Failure(
            kind="too_large",
            effect="none",
            message=f"The message is over {max_record_bytes} bytes.",
            source=source,
        )
    return None


def check_receipt(receipt: DeliveryReceipt, source: str) -> Failure | None:
    """Give the Failure for a receipt no outbox may save, or None."""
    if not is_time_text(receipt.at):
        return invalid("A receipt's time must be in the saved form.", source)
    return None


def unknown_outbound(out_id: str, source: str) -> Failure:
    """Build the failure for an out_id the outbox never saw."""
    return invalid(f"No outbox row has the id {out_id}.", source)


def _outbound_problem(out: Outbound) -> str | None:
    if not is_hash(out.out_id):
        return "out_id must be a SHA-256 in hex."
    if not is_id(out.session_id):
        return "The session id breaks the id rule."
    if out.kind not in ("reply", "notice"):
        return "kind must be reply or notice."
    if out.maybe_duplicate:
        # Only `due` sets it, from what the outbox has saved about the row.
        return "A message being enqueued cannot already be a possible repeat."
    if out.v < 1:
        return "v must be 1 or more."
    return None
