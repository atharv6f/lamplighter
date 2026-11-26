import dataclasses
from typing import Any, cast

import pytest

from lamplighter.core import Reply, rebuild_messages
from lamplighter.ports import (
    ErrorEvent,
    Event,
    Failure,
    JsonObject,
    Message,
    StopEvent,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    Usage,
    event_id_for,
    to_plain,
)
from tests.model_samples import REASONING, THROTTLED

AT = "2026-11-26T07:35:12.345Z"
CALL_A = ToolCallPart(id="call_a", name="read_file", arguments={"path": "a.txt"})
CALL_B = ToolCallPart(id="call_b", name="read_file", arguments={"path": "b.txt"})
USAGE = Usage(input_uncached=10, cache_read=0, cache_write=0, output=5)
STOP = StopEvent(
    reason="tool_calls", provider_reason="tool_use", model_reported=None, skipped=0
)


def event(seq: int, kind: str, payload: Any, v: int = 1) -> Event:
    return Event(
        session_id="sess-a",
        seq=seq,
        pos=seq,
        event_id=event_id_for("sess-a", seq),
        type=kind,
        v=v,
        payload=cast(JsonObject, payload),
        at=AT,
        run_id=None,
        step=None,
        duration_ms=None,
        cause=None,
    )


def said(seq: int, text: str) -> Event:
    return event(seq, "user_message", {"text": text})


def result(seq: int, call_id: str, text: str) -> Event:
    return event(
        seq, "tool_result", {"call_id": call_id, "text": text, "is_error": False}
    )


def replied(seq: int, *parts: Any, end: StopEvent | ErrorEvent = STOP) -> Event:
    reply = Reply(message=Message(role="assistant", parts=parts), usage=USAGE, end=end)
    return event(seq, "model_response", to_plain(reply))


def mark(seq: int, kind: str) -> Event:
    return event(seq, kind, {})


def messages(events: list[Event]) -> tuple[Message, ...]:
    rebuilt = rebuild_messages(events)
    assert not isinstance(rebuilt, Failure), rebuilt
    return rebuilt


def failure_of(events: list[Event]) -> Failure:
    rebuilt = rebuild_messages(events)
    assert isinstance(rebuilt, Failure), rebuilt
    return rebuilt


def test_a_whole_exchange_is_rebuilt_in_order_with_the_reply_unchanged() -> None:
    rebuilt = messages(
        [
            said(1, "What is on my list?"),
            mark(2, "run_started"),
            replied(3, REASONING, TextPart(text="I will look."), CALL_A),
            result(4, "call_a", "milk"),
            replied(
                5, TextPart(text="Milk."), end=dataclasses.replace(STOP, reason="end")
            ),
            mark(6, "run_end"),
        ]
    )
    assert rebuilt == (
        Message(role="user", parts=(TextPart(text="What is on my list?"),)),
        Message(
            role="assistant", parts=(REASONING, TextPart(text="I will look."), CALL_A)
        ),
        Message(
            role="user",
            parts=(ToolResultPart(call_id="call_a", text="milk", is_error=False),),
        ),
        Message(role="assistant", parts=(TextPart(text="Milk."),)),
    )


def test_user_side_parts_between_two_replies_join_with_tool_results_first() -> None:
    rebuilt = messages(
        [
            replied(1, CALL_A, CALL_B),
            said(2, "Also check the plan."),
            result(3, "call_b", "b"),
            said(4, "And hurry."),
            result(5, "call_a", "a"),
        ]
    )
    assert rebuilt[1] == Message(
        role="user",
        parts=(
            ToolResultPart(call_id="call_b", text="b", is_error=False),
            ToolResultPart(call_id="call_a", text="a", is_error=False),
            TextPart(text="Also check the plan."),
            TextPart(text="And hurry."),
        ),
    )


def test_two_replies_in_a_row_are_never_joined() -> None:
    rebuilt = messages(
        [replied(1, TextPart(text="one")), replied(2, TextPart(text="two"))]
    )
    assert [m.role for m in rebuilt] == ["assistant", "assistant"]


def test_a_reply_that_was_cut_off_is_left_out() -> None:
    cut = ErrorEvent(failure=THROTTLED, model_reported=None, skipped=0)
    rebuilt = messages(
        [
            said(1, "Hello."),
            replied(2, TextPart(text="Hel"), end=cut),
            said(3, "Again?"),
        ]
    )
    assert rebuilt == (
        Message(role="user", parts=(TextPart(text="Hello."), TextPart(text="Again?"))),
    )


def test_a_reply_with_no_parts_is_left_out() -> None:
    assert messages([said(1, "Hello."), replied(2)]) == (
        Message(role="user", parts=(TextPart(text="Hello."),)),
    )


def test_a_tool_call_with_no_result_is_left_as_it_is() -> None:
    rebuilt = messages([said(1, "Read a."), replied(2, CALL_A)])
    assert rebuilt[-1] == Message(role="assistant", parts=(CALL_A,))


def test_no_events_give_no_messages() -> None:
    assert messages([]) == ()


def test_a_saved_payload_with_a_field_this_code_does_not_know_still_reads() -> None:
    newer = event(1, "user_message", {"text": "Hello.", "language": "en"})
    assert messages([newer]) == (
        Message(role="user", parts=(TextPart(text="Hello."),)),
    )


@pytest.mark.parametrize(
    "unreadable",
    [mark(2, "tool_intent"), event(2, "user_message", {"text": "x"}, v=2)],
    ids=["a type this code does not read", "a version newer than this code"],
)
def test_an_event_this_code_cannot_read_stops_the_rebuild(unreadable: Event) -> None:
    failure = failure_of([said(1, "Hello."), unreadable])
    assert (failure.kind, failure.source) == ("needs_user", "transcript")
    assert "seq 2" in failure.message
    assert unreadable.type in failure.message


@pytest.mark.parametrize(
    "damaged",
    [
        event(2, "user_message", {}),
        event(2, "user_message", {"text": 3}),
        event(2, "tool_result", {"call_id": "call_a", "text": "a"}),
        event(2, "model_response", {"message": {}, "usage": None, "end": {}}),
    ],
)
def test_a_payload_that_does_not_fit_its_type_is_damage(damaged: Event) -> None:
    failure = failure_of([said(1, "Hello."), damaged])
    assert failure.kind == "fatal"
    assert "seq 2" in failure.message
