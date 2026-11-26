"""Checks the input of an inbox call, so every inbox refuses the same things."""

from lamplighter.ports.blob_check import check_handle
from lamplighter.ports.event_check import invalid
from lamplighter.ports.failure import Failure
from lamplighter.ports.inbound import Inbound
from lamplighter.ports.json_text import saved_size
from lamplighter.ports.value_rules import (
    is_channel,
    is_id,
    is_native_text,
    is_time_text,
)


def check_accept(
    inbound: Inbound, max_record_bytes: int, source: str
) -> Failure | None:
    """Give the Failure for a message no inbox may accept, or None."""
    problem = _inbound_problem(inbound) or _kind_problem(inbound)
    if problem is not None:
        return invalid(problem, source)
    for handle in inbound.attachments:
        refused = check_handle(handle, source)
        if refused is not None:
            return refused
    try:
        size = saved_size(inbound)
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


def check_mark_routed(session_id: str, seq: int, source: str) -> Failure | None:
    """Give the Failure for a routing no inbox may accept, or None."""
    if not is_id(session_id):
        return invalid("The session id breaks the id rule.", source)
    if seq < 1:
        return invalid("seq must be 1 or more.", source)
    return None


def unknown_inbox_row(inbox_id: int, source: str) -> Failure:
    """Build the failure for an inbox id the inbox never gave out."""
    return invalid(f"No inbox row has the id {inbox_id}.", source)


def _inbound_problem(inbound: Inbound) -> str | None:
    if not is_channel(inbound.channel):
        return "The channel breaks the channel rule."
    texts = (inbound.native_id, inbound.conversation, inbound.sender.id)
    if not all(is_native_text(text) for text in texts):
        return "native_id, conversation and the sender's id must be 1 to 256 long."
    if not (is_time_text(inbound.sent_at) and is_time_text(inbound.received_at)):
        return "sent_at and received_at must be times in the saved form."
    if inbound.v < 1:
        return "v must be 1 or more."
    return None


def _kind_problem(inbound: Inbound) -> str | None:
    is_command = inbound.kind == "command"
    if is_command != (inbound.command is not None):
        return "A command, and only a command, names its command."
    if inbound.command is not None and not is_native_text(inbound.command):
        return "A command's name must be 1 to 256 long."
    is_answer = inbound.kind == "approval_answer"
    has_both = inbound.approval_id is not None and inbound.answer is not None
    has_either = inbound.approval_id is not None or inbound.answer is not None
    if is_answer and not has_both:
        return "An approval answer has an approval id and an answer."
    if not is_answer and has_either:
        return "Only an approval answer has an approval id or an answer."
    if inbound.approval_id is not None and not is_native_text(inbound.approval_id):
        return "An approval id must be 1 to 256 long."
    return None
