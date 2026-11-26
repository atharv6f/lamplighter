"""Checks the input of an event log call, so every log refuses the same things."""

from collections.abc import Sequence

from lamplighter.ports.event_log import USER_MESSAGE, NewEvent
from lamplighter.ports.failure import Failure
from lamplighter.ports.json_text import saved_size
from lamplighter.ports.value_rules import is_event_type, is_hash, is_id, is_time_text


def check_append(
    session_id: str,
    events: Sequence[NewEvent],
    expected_seq: int,
    max_record_bytes: int,
    source: str,
) -> Failure | None:
    """Give the Failure for an append no log may accept, or None.

    The rules come first and the sizes after, for the whole batch, so a batch
    that is both badly formed and too large is always `invalid_input`.
    """
    problem = _batch_problem(session_id, events, expected_seq)
    if problem is not None:
        return invalid(problem, source)
    for place, event in enumerate(events):
        if saved_size(event.payload) > max_record_bytes:
            return Failure(
                kind="too_large",
                effect="none",
                message=f"Event {place} of the batch is over {max_record_bytes} bytes.",
                source=source,
            )
    return None


def check_read(session_id: str, after_seq: int, source: str) -> Failure | None:
    """Give the Failure for a read no log may accept, or None."""
    if not is_id(session_id):
        return invalid("The session id breaks the id rule.", source)
    if after_seq < 0:
        return invalid("after_seq must be 0 or more.", source)
    return None


def check_tail(after_pos: int, limit: int, source: str) -> Failure | None:
    """Give the Failure for a tail no log may accept, or None."""
    if after_pos < 0:
        return invalid("after_pos must be 0 or more.", source)
    if limit < 1:
        return invalid("limit must be 1 or more.", source)
    return None


def invalid(message: str, source: str) -> Failure:
    """Build the failure for a value that breaks a rule. Nothing was written."""
    return Failure(kind="invalid_input", effect="none", message=message, source=source)


def _batch_problem(
    session_id: str, events: Sequence[NewEvent], expected_seq: int
) -> str | None:
    if not is_id(session_id):
        return "The session id breaks the id rule."
    if expected_seq < 0:
        return "expected_seq must be 0 or more."
    if not events:
        return "The batch is empty."
    for place, event in enumerate(events):
        problem = _event_problem(event)
        if problem is not None:
            return f"Event {place} of the batch: {problem}"
    return None


def _event_problem(event: NewEvent) -> str | None:
    if not is_event_type(event.type):
        return "the type breaks the type rule."
    if event.v < 1:
        return "v must be 1 or more."
    if not is_time_text(event.at):
        return "at is not a time in the saved form."
    if event.run_id is not None and not is_id(event.run_id):
        return "the run id breaks the id rule."
    if event.step is not None and event.step < 1:
        return "step must be 1 or more."
    if event.duration_ms is not None and event.duration_ms < 0:
        return "duration_ms must be 0 or more."
    if event.cause is not None and not is_hash(event.cause):
        return "cause is not a hash."
    return _id_problem(event) or _payload_problem(event)


def _id_problem(event: NewEvent) -> str | None:
    if event.type == USER_MESSAGE:
        if event.event_id is None or not is_hash(event.event_id):
            return "a user message must carry the id made from its channel."
        return None
    if event.event_id is not None:
        return "only a user message carries its own id."
    return None


def _payload_problem(event: NewEvent) -> str | None:
    try:
        saved_size(event.payload)
    except ValueError:
        return "the payload cannot be saved as JSON."
    return None
