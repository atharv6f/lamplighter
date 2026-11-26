import pytest

from lamplighter.core import fold_reply
from lamplighter.ports import (
    ErrorEvent,
    ReasoningEvent,
    StopEvent,
    TextEvent,
    TextPart,
    ToolCallEvent,
    UsageEvent,
)
from tests.model_samples import REASONING, THROTTLED, TOOL_CALL, USAGE

STOP = StopEvent(
    reason="end", provider_reason="end", model_reported="made-up-1", skipped=0
)


def test_pieces_of_one_part_join_in_the_order_they_came() -> None:
    reply = fold_reply(
        [TextEvent(0, "The ket"), TextEvent(0, "tle is"), TextEvent(0, " on."), STOP]
    )
    assert reply.message.parts == (TextPart(text="The kettle is on."),)


def test_two_text_parts_stay_two_parts() -> None:
    reply = fold_reply([TextEvent(0, "One."), TextEvent(1, "Two."), STOP])
    assert reply.message.parts == (TextPart(text="One."), TextPart(text="Two."))


def test_parts_are_put_in_the_order_of_their_index() -> None:
    reply = fold_reply(
        [
            ReasoningEvent(index=0, part=REASONING),
            TextEvent(1, "I will read it."),
            ToolCallEvent(index=2, part=TOOL_CALL),
            UsageEvent(usage=USAGE),
            STOP,
        ]
    )
    assert reply.message.role == "assistant"
    assert reply.message.parts == (
        REASONING,
        TextPart(text="I will read it."),
        TOOL_CALL,
    )
    assert reply.usage == USAGE
    assert reply.end == STOP


def test_usage_is_none_when_the_stream_gave_none() -> None:
    assert fold_reply([STOP]).usage is None


def test_a_failed_stream_keeps_what_arrived_before_the_failure() -> None:
    error = ErrorEvent(failure=THROTTLED, model_reported=None, skipped=0)
    reply = fold_reply([TextEvent(0, "The ket"), error])
    assert reply.message.parts == (TextPart(text="The ket"),)
    assert reply.end == error


def test_events_with_no_last_event_are_not_a_reply() -> None:
    with pytest.raises(ValueError, match="no last event"):
        fold_reply([TextEvent(0, "The ket")])
