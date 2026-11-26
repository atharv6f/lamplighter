from typing import Any

import pytest

from lamplighter.adapters import ScriptedBreak, ScriptedReply
from lamplighter.core import fold_reply
from lamplighter.ports import (
    ErrorEvent,
    Failure,
    ReasoningEvent,
    ReasoningPart,
    StopEvent,
    TextEvent,
    TextPart,
    ToolCallEvent,
    ToolCallPart,
    Usage,
    UsageEvent,
)
from tests.anthropic.wire_text import (
    MODEL,
    OWN_REASONING,
    PING,
    SIGNED,
    assemble,
    block_delta,
    block_start,
    block_stop,
    error,
    message_end,
    message_start,
    sse,
    stream_body,
)

USAGE = Usage(input_uncached=120, cache_read=3_000, cache_write=50, output=40)
LIST_CALL = ToolCallPart(id="toolu_a", name="read_file", arguments={"path": "list.txt"})
PLAN_CALL = ToolCallPart(id="toolu_b", name="read_file", arguments={"path": "plan.txt"})
REPLY = ScriptedReply(
    parts=(
        OWN_REASONING,
        TextPart(text="I will read both files now."),
        LIST_CALL,
        PLAN_CALL,
    ),
    reason="tool_calls",
    usage=USAGE,
)


def text_block(index: int, *pieces: str) -> bytes:
    deltas = [block_delta(index, {"type": "text_delta", "text": p}) for p in pieces]
    start = block_start(index, {"type": "text", "text": ""})
    return start + b"".join(deltas) + block_stop(index)


def tool_block(index: int, *pieces: str) -> bytes:
    deltas = [
        block_delta(index, {"type": "input_json_delta", "partial_json": p})
        for p in pieces
    ]
    opened: dict[str, Any] = {"type": "tool_use", "input": {}}
    opened.update(id="toolu_a", name="read_file")
    return block_start(index, opened) + b"".join(deltas) + block_stop(index)


def the_tool_call(body: bytes) -> ToolCallPart:
    calls = [e.part for e in assemble(body) if isinstance(e, ToolCallEvent)]
    assert len(calls) == 1
    return calls[0]


@pytest.mark.parametrize("piece_size", [1, 13, 100, 1_000_000])
def test_a_whole_reply_reads_the_same_however_the_body_is_cut(piece_size: int) -> None:
    reply = fold_reply(assemble(stream_body(REPLY), piece_size))
    assert reply.message.parts == REPLY.parts
    assert reply.usage == USAGE
    assert reply.end == StopEvent(
        reason="tool_calls", provider_reason="tool_use", model_reported=MODEL, skipped=0
    )


def test_text_arrives_piece_by_piece_under_one_index() -> None:
    events = assemble(message_start() + text_block(0, "The ket", "tle is on."))
    pieces = [e for e in events if isinstance(e, TextEvent)]
    assert pieces == [TextEvent(0, "The ket"), TextEvent(0, "tle is on.")]


def test_part_indexes_count_the_parts_given_and_not_the_blocks_on_the_wire() -> None:
    body = (
        message_start()
        + text_block(0)
        + block_start(1, {"type": "server_tool_use", "id": "x", "name": "y"})
        + block_stop(1)
        + text_block(2, "Hello.")
        + tool_block(3, "{}")
        + message_end("tool_use", 5)
    )
    events = assemble(body)
    assert [e.index for e in events if isinstance(e, (TextEvent, ToolCallEvent))] == [
        0,
        1,
    ]


def test_a_text_block_with_no_text_makes_no_part() -> None:
    body = message_start() + text_block(0, "", "") + message_end("end_turn", 1)
    assert fold_reply(assemble(body)).message.parts == ()


def test_tool_arguments_are_joined_from_their_fragments() -> None:
    call = the_tool_call(
        message_start() + tool_block(0, '{"pa', 'th": "li', 'st.txt"}')
    )
    assert call == LIST_CALL


def test_a_tool_call_with_no_argument_text_has_no_arguments() -> None:
    assert the_tool_call(message_start() + tool_block(0)).arguments == {}


def test_tool_arguments_cut_short_are_kept_as_text_with_the_reason() -> None:
    call = the_tool_call(message_start() + tool_block(0, '{"path": "li'))
    assert call.arguments is None
    assert call.raw_arguments == '{"path": "li'
    assert call.parse_error is not None
    assert call.parse_error.startswith("not JSON")
    assert (call.id, call.name) == ("toolu_a", "read_file")


def test_tool_arguments_that_are_not_an_object_are_kept_as_text() -> None:
    call = the_tool_call(message_start() + tool_block(0, "[1, 2]"))
    assert call.arguments is None
    assert call.raw_arguments == "[1, 2]"
    assert call.parse_error == "not a JSON object"


def test_reasoning_is_given_whole_with_the_block_exactly_as_it_came() -> None:
    events = assemble(stream_body(REPLY))
    reasoning = [e for e in events if isinstance(e, ReasoningEvent)]
    assert reasoning == [ReasoningEvent(index=0, part=OWN_REASONING)]
    assert reasoning[0].part.opaque == SIGNED


def test_reasoning_text_is_kept_when_the_server_sends_it() -> None:
    body = (
        message_start()
        + block_start(0, {"type": "thinking", "thinking": "", "signature": ""})
        + block_delta(0, {"type": "thinking_delta", "thinking": "First, "})
        + block_delta(0, {"type": "thinking_delta", "thinking": "the list."})
        + block_delta(0, {"type": "signature_delta", "signature": "c2ln"})
        + block_stop(0)
    )
    part = assemble(body)[0]
    assert isinstance(part, ReasoningEvent)
    assert part.part == ReasoningPart(
        text="First, the list.",
        made_by="anthropic",
        opaque={
            "type": "thinking",
            "thinking": "First, the list.",
            "signature": "c2ln",
        },
    )


def test_hidden_reasoning_is_given_as_it_came() -> None:
    hidden = {"type": "redacted_thinking", "data": "aGlkZGVu"}
    events = assemble(message_start() + block_start(0, hidden) + block_stop(0))
    assert events[0] == ReasoningEvent(
        index=0, part=ReasoningPart(text="", made_by="anthropic", opaque=hidden)
    )


@pytest.mark.parametrize(
    ("wire_reason", "reason"),
    [
        ("end_turn", "end"),
        ("tool_use", "tool_calls"),
        ("max_tokens", "max_output"),
        ("refusal", "refused"),
        ("pause_turn", "other"),
        ("stop_sequence", "other"),
        ("model_context_window_exceeded", "other"),
        ("a_reason_added_later", "other"),
    ],
)
def test_stop_reasons_become_five_words_and_the_own_word_is_kept(
    wire_reason: str, reason: str
) -> None:
    end = assemble(message_start() + message_end(wire_reason, 3))[-1]
    assert isinstance(end, StopEvent)
    assert (end.reason, end.provider_reason) == (reason, wire_reason)


def test_input_counts_come_from_the_first_event_and_output_from_the_last() -> None:
    events = assemble(stream_body(REPLY))
    assert events[-2] == UsageEvent(usage=USAGE)


def test_a_later_count_replaces_an_earlier_one_and_is_not_added_to_it() -> None:
    def delta(usage: dict[str, int]) -> bytes:
        data = {"type": "message_delta", "delta": {"stop_reason": None}, "usage": usage}
        return sse("message_delta", data)

    body = (
        message_start(Usage(120, 0, 0, None))
        + delta({"output_tokens": 10})
        + delta(
            {"output_tokens": 25, "input_tokens": 130, "cache_read_input_tokens": 7}
        )
        + message_end("end_turn", 40)
    )
    usage = fold_reply(assemble(body)).usage
    assert usage == Usage(input_uncached=130, cache_read=7, cache_write=0, output=40)


def test_a_count_the_server_did_not_send_stays_empty() -> None:
    body = message_start(Usage(120, None, None, None)) + message_end("end_turn", 40)
    usage = fold_reply(assemble(body)).usage
    assert usage == Usage(
        input_uncached=120, cache_read=None, cache_write=None, output=40
    )


@pytest.mark.parametrize("not_a_count", [True, "40", 40.0])
def test_a_count_that_is_not_a_whole_number_stays_empty(not_a_count: object) -> None:
    usage = {"input_tokens": not_a_count, "output_tokens": not_a_count}
    data = {"type": "message_delta", "delta": {"stop_reason": None}, "usage": usage}
    body = message_start(Usage(None, 3, None, None)) + sse("message_delta", data)
    events = assemble(body + sse("message_stop", {"type": "message_stop"}))
    counts = events[-2]
    assert isinstance(counts, UsageEvent)
    assert counts.usage.cache_read == 3
    assert counts.usage.input_uncached is None
    assert counts.usage.output is None


def test_a_stream_with_no_counts_gives_no_usage_event() -> None:
    events = assemble(message_start() + message_end("end_turn", None))
    assert not [e for e in events if isinstance(e, UsageEvent)]


def test_an_error_event_ends_the_stream_and_the_input_counts_are_still_given() -> None:
    body = (
        message_start(Usage(120, 0, 0, None))
        + text_block(0, "The ket")
        + error("overloaded_error")
        + text_block(1, "never read")
        + message_end("end_turn", 40)
    )
    events = assemble(body)
    assert events[0] == TextEvent(0, "The ket")
    assert events[1] == UsageEvent(usage=Usage(120, 0, 0, None))
    end = events[2]
    assert isinstance(end, ErrorEvent)
    assert len(events) == 3
    assert end.failure.kind == "transient"
    assert end.failure.effect == "none"
    assert end.failure.source == "ModelClient/anthropic"
    assert end.failure.detail == {
        "error_type": "overloaded_error",
        "provider_message": "A made-up problem.",
    }
    assert "made-up" not in end.failure.message
    assert end.model_reported == MODEL


@pytest.mark.parametrize(
    ("error_type", "kind"),
    [
        ("overloaded_error", "transient"),
        ("api_error", "transient"),
        ("rate_limit_error", "throttled"),
        ("timeout_error", "timeout"),
        ("invalid_request_error", "invalid_input"),
        ("authentication_error", "needs_user"),
        ("billing_error", "needs_user"),
        ("permission_error", "needs_user"),
        ("request_too_large", "too_large"),
        ("not_found_error", "fatal"),
        ("an_error_added_later", "transient"),
    ],
)
def test_each_error_type_in_a_stream_maps_to_one_kind(
    error_type: str, kind: str
) -> None:
    end = assemble(message_start() + error(error_type))[-1]
    assert isinstance(end, ErrorEvent)
    assert end.failure.kind == kind
    assert end.failure.retry_after_ms is None


def test_a_body_that_ends_before_the_reply_is_complete_is_a_failure() -> None:
    events = assemble(message_start(Usage(120, 0, 0, None)) + text_block(0, "The ket"))
    end = events[-1]
    assert isinstance(end, ErrorEvent)
    assert end.failure.kind == "transient"
    assert events[-2] == UsageEvent(usage=Usage(120, 0, 0, None))


def test_an_empty_body_is_a_failure() -> None:
    events = assemble(b"")
    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)
    assert events[0].model_reported is None


@pytest.mark.parametrize(
    "bad_event",
    [
        b"event: content_block_delta\ndata: {not json\n\n",
        b"event: content_block_delta\ndata: [1, 2]\n\n",
        sse("content_block_delta", {"type": "content_block_delta", "delta": {}}),
        block_delta(5, {"type": "text_delta", "text": "no such block"}),
        block_delta(0, {"type": "text_delta"}),
        block_stop(5),
        block_stop(0) + block_delta(0, {"type": "text_delta", "text": "too late"}),
        sse("message_start", {"type": "message_start"}),
        sse("error", {"type": "error"}),
    ],
)
def test_data_that_is_not_as_documented_ends_the_stream_with_a_failure(
    bad_event: bytes,
) -> None:
    body = message_start() + block_start(0, {"type": "text", "text": ""}) + bad_event
    events = assemble(body + text_block(1, "never read") + message_end("end_turn", 1))
    assert len(events) == 1
    end = events[0]
    assert isinstance(end, ErrorEvent)
    assert end.failure.kind == "transient"
    assert "unreadable" in end.failure.detail


def test_an_unknown_event_is_passed_over_and_counted() -> None:
    body = (
        message_start()
        + sse("something_new", {"type": "something_new"})
        + text_block(0, "Hello.")
        + sse("another_new_thing", {"type": "another_new_thing", "index": 0})
        + message_end("end_turn", 2)
    )
    reply = fold_reply(assemble(body))
    assert reply.message.parts == (TextPart(text="Hello."),)
    assert isinstance(reply.end, StopEvent)
    assert reply.end.skipped == 2


def test_a_ping_is_not_counted_as_unknown() -> None:
    end = assemble(message_start() + PING + PING + message_end("end_turn", 2))[-1]
    assert isinstance(end, StopEvent)
    assert end.skipped == 0


def test_an_unknown_block_is_counted_once_and_its_deltas_are_passed_over() -> None:
    body = (
        message_start()
        + block_start(0, {"type": "hologram"})
        + block_delta(0, {"type": "hologram_delta", "light": "x"})
        + block_delta(0, {"type": "hologram_delta", "light": "y"})
        + block_stop(0)
        + text_block(1, "Hello.")
        + message_end("end_turn", 2)
    )
    reply = fold_reply(assemble(body))
    assert reply.message.parts == (TextPart(text="Hello."),)
    assert isinstance(reply.end, StopEvent)
    assert reply.end.skipped == 1


def test_an_unknown_delta_in_a_known_block_is_counted() -> None:
    body = (
        message_start()
        + block_start(0, {"type": "text", "text": ""})
        + block_delta(0, {"type": "footnote_delta", "footnote": {}})
        + block_delta(0, {"type": "text_delta", "text": "Hello."})
        + block_stop(0)
        + message_end("end_turn", 2)
    )
    reply = fold_reply(assemble(body))
    assert reply.message.parts == (TextPart(text="Hello."),)
    assert isinstance(reply.end, StopEvent)
    assert reply.end.skipped == 1


def test_a_scripted_failure_part_way_reads_as_text_then_an_error() -> None:
    overloaded = Failure("transient", "none", "made up", "ModelClient/made-up")
    turn = ScriptedBreak(
        parts=(TextPart(text="The list starts with"),), failure=overloaded
    )
    reply = fold_reply(assemble(stream_body(turn)))
    assert reply.message.parts == turn.parts
    assert isinstance(reply.end, ErrorEvent)
    assert reply.end.failure.kind == "transient"
