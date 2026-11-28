"""How the local adapter reads a chat completions stream, on made-up servers."""

import dataclasses
import json
from typing import Any

import httpx
import pytest

from lamplighter.core import fold_reply
from lamplighter.ports import (
    ErrorEvent,
    Message,
    ModelEvent,
    ModelRequest,
    ReasoningEvent,
    StopEvent,
    TextEvent,
    TextPart,
    ToolCallEvent,
    ToolCallPart,
    Usage,
    UsageEvent,
)
from tests.model_samples import TOOL
from tests.openai_chat.fake_server import answering, client_for, profile_for
from tests.openai_chat.wire_text import (
    DIALECTS,
    DONE,
    REASONING_FIELD,
    Dialect,
    chunk,
    data_line,
    error_value,
)
from tests.streams import collect

pytestmark = pytest.mark.parametrize("dialect", DIALECTS)


def ask(tools: bool = True, retries: int = 0) -> ModelRequest:
    return ModelRequest(
        system=("You are a careful helper.",),
        messages=(Message(role="user", parts=(TextPart(text="What is on it?"),)),),
        tools=(TOOL,) if tools else (),
        max_output=500,
        cache_hints=(),
        deadline_ms=5_000,
        retries=retries,
    )


def stream_of(*lines: bytes) -> bytes:
    return b"".join(lines)


def call_piece(index: int | None, **fields: Any) -> bytes:
    piece: dict[str, Any] = dict(fields)
    if index is not None:
        piece["index"] = index
    return chunk({"tool_calls": [piece]})


def whole_call(index: int, call_id: object, arguments: str) -> bytes:
    function = {"name": "read_file", "arguments": arguments}
    return call_piece(index, id=call_id, type="function", function=function)


def ended(reason: str = "stop", usage: dict[str, Any] | None = None) -> bytes:
    tail = chunk({}, reason)
    if usage is not None:
        tail += chunk(None, usage=usage)
    return tail + DONE


USAGE = {
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "prompt_tokens_details": {"cached_tokens": 60},
}


def play(
    dialect: Dialect, body: bytes, request: ModelRequest | None = None
) -> list[ModelEvent]:
    serve, _ = answering(dialect, httpx.Response(200, content=body))
    return collect(client_for(serve, dialect), request or ask())


def calls_in(events: list[ModelEvent]) -> list[ToolCallPart]:
    return [e.part for e in events if isinstance(e, ToolCallEvent)]


def last(events: list[ModelEvent]) -> ModelEvent:
    return events[-1]


# What the adapter must not assume of a server.


def test_a_reply_with_no_usage_gives_no_usage_event(dialect: Dialect) -> None:
    events = play(dialect, stream_of(chunk({"content": "Tea."}), ended()))
    assert not any(isinstance(e, UsageEvent) for e in events)
    assert isinstance(last(events), StopEvent)


def test_a_call_with_no_id_gets_a_made_one(dialect: Dialect) -> None:
    function = {"name": "read_file", "arguments": '{"path": "a.txt"}'}
    body = stream_of(call_piece(0, function=function), ended("tool_calls"))
    [call] = calls_in(play(dialect, body))
    assert call.id.startswith("call_")
    assert call.id.endswith("_1")
    assert len(call.id) == len("call_") + 24 + len("_1")


def test_arguments_that_are_not_json_keep_their_text_and_say_why(
    dialect: Dialect,
) -> None:
    body = stream_of(whole_call(0, "c1", '{"path": "a.t'), ended("tool_calls"))
    [call] = calls_in(play(dialect, body))
    assert call.arguments is None
    assert call.raw_arguments == '{"path": "a.t'
    assert call.parse_error is not None and call.parse_error.startswith("not JSON")


def test_arguments_that_are_json_but_not_an_object_are_kept_as_text(
    dialect: Dialect,
) -> None:
    body = stream_of(whole_call(0, "c1", "[1, 2]"), ended("tool_calls"))
    [call] = calls_in(play(dialect, body))
    assert call.arguments is None
    assert call.raw_arguments == "[1, 2]"
    assert call.parse_error == "not a JSON object"


def test_no_argument_text_is_an_empty_object(dialect: Dialect) -> None:
    body = stream_of(whole_call(0, "c1", ""), ended("tool_calls"))
    [call] = calls_in(play(dialect, body))
    assert call.arguments == {}


def test_counts_with_no_cached_figure_are_unknown_never_zero(
    dialect: Dialect,
) -> None:
    usage = {"prompt_tokens": 100, "completion_tokens": 20}
    events = play(dialect, stream_of(chunk({"content": "Tea."}), ended("stop", usage)))
    [counted] = [e for e in events if isinstance(e, UsageEvent)]
    assert counted.usage == Usage(None, None, None, 20)


def test_cached_tokens_are_reads_and_the_rest_is_uncached(dialect: Dialect) -> None:
    events = play(dialect, stream_of(chunk({"content": "Tea."}), ended("stop", USAGE)))
    [counted] = [e for e in events if isinstance(e, UsageEvent)]
    assert counted.usage == Usage(40, 60, 0, 20)


def test_tokens_written_to_the_cache_are_counted_apart(dialect: Dialect) -> None:
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 60, "created_cache_tokens": 30},
    }
    events = play(dialect, stream_of(chunk({"content": "Tea."}), ended("stop", usage)))
    [counted] = [e for e in events if isinstance(e, UsageEvent)]
    assert counted.usage == Usage(10, 60, 30, 20)


def test_the_name_sent_is_the_profiles_and_the_name_given_back_is_kept(
    dialect: Dialect,
) -> None:
    named = "/made-up/weights/model.gguf"
    body = stream_of(
        chunk({"content": "Tea."}, model=named), chunk({}, "stop", model=named), DONE
    )
    serve, server = answering(dialect, httpx.Response(200, content=body))
    events = collect(client_for(serve, dialect), ask())
    sent = json.loads(server.requests[0].content)
    assert sent["model"] == profile_for(dialect).model
    end = last(events)
    assert isinstance(end, StopEvent)
    assert end.model_reported == named


def test_the_profile_says_how_many_requests_run_at_once(dialect: Dialect) -> None:
    serve, _ = answering(dialect)
    assert client_for(serve, dialect).profile.concurrent_requests == 2


# The checks at the end of a reply.


def test_a_reply_that_says_tool_calls_and_has_none_failed(dialect: Dialect) -> None:
    events = play(
        dialect, stream_of(chunk({"content": "Checking."}), ended("tool_calls"))
    )
    end = last(events)
    assert isinstance(end, ErrorEvent)
    assert end.failure.kind == "transient"
    assert end.failure.detail == {"model_output": "dropped_calls"}


def test_call_markup_in_the_text_of_a_reply_with_tools_failed(
    dialect: Dialect,
) -> None:
    text = 'Sure. <tool_call>{"name": "read_file"}</tool_call>'
    events = play(dialect, stream_of(chunk({"content": text}), ended()))
    end = last(events)
    assert isinstance(end, ErrorEvent)
    assert end.failure.kind == "transient"
    assert end.failure.detail == {"model_output": "call_markup"}


def test_a_marker_split_across_pieces_is_still_found(dialect: Dialect) -> None:
    body = stream_of(
        chunk({"content": "Sure. <tool_"}), chunk({"content": "call>{}"}), ended()
    )
    end = last(play(dialect, body))
    assert isinstance(end, ErrorEvent)
    assert end.failure.detail == {"model_output": "call_markup"}


def test_the_same_text_with_no_tools_offered_is_a_whole_reply(
    dialect: Dialect,
) -> None:
    text = "In this format a call opens with <tool_call>."
    events = play(dialect, stream_of(chunk({"content": text}), ended()), ask(False))
    assert isinstance(last(events), StopEvent)
    assert fold_reply(events).message.parts == (TextPart(text=text),)


def test_markup_in_reasoning_is_not_a_fault(dialect: Dialect) -> None:
    field = REASONING_FIELD[dialect]
    body = stream_of(
        chunk({field: "I could write <tool_call> here."}),
        chunk({"content": "Tea."}),
        ended(),
    )
    assert isinstance(last(play(dialect, body)), StopEvent)


def test_markup_comes_before_dropped_calls(dialect: Dialect) -> None:
    body = stream_of(chunk({"content": "<function=read_file>"}), ended("tool_calls"))
    end = last(play(dialect, body))
    assert isinstance(end, ErrorEvent)
    assert end.failure.detail == {"model_output": "call_markup"}


def test_a_fault_in_the_models_output_is_not_tried_again(dialect: Dialect) -> None:
    # Nothing was given before the fault, so only the rule on model output
    # stops a retry here.
    body = stream_of(ended("tool_calls"))
    serve, server = answering(
        dialect,
        httpx.Response(200, content=body),
        httpx.Response(200, content=body),
    )
    events = collect(client_for(serve, dialect), ask(retries=1))
    assert len(server.requests) == 1
    assert isinstance(last(events), ErrorEvent)


# The rules for tool call pieces.


def test_a_piece_for_a_closed_call_is_a_stream_fault(dialect: Dialect) -> None:
    body = stream_of(
        whole_call(0, "c1", "{"),
        chunk({"content": "and"}),
        call_piece(0, function={"arguments": "}"}),
        ended("tool_calls"),
    )
    end = last(play(dialect, body))
    assert isinstance(end, ErrorEvent)
    assert end.failure.kind == "transient"
    assert end.failure.detail == {
        "stream_fault": "a tool call came in pieces split by another part"
    }


def test_a_piece_with_no_index_is_a_stream_fault(dialect: Dialect) -> None:
    body = stream_of(
        call_piece(None, id="c1", function={"name": "read_file", "arguments": "{}"}),
        ended("tool_calls"),
    )
    end = last(play(dialect, body))
    assert isinstance(end, ErrorEvent)
    assert end.failure.detail == {"stream_fault": "a tool call piece had no index"}


def test_an_id_and_a_name_sent_on_every_piece_are_not_doubled(
    dialect: Dialect,
) -> None:
    body = stream_of(
        call_piece(0, id="c1", function={"name": "read_file", "arguments": '{"pa'}),
        call_piece(
            0, id="c1", function={"name": "read_file", "arguments": 'th": "a"}'}
        ),
        ended("tool_calls"),
    )
    [call] = calls_in(play(dialect, body))
    assert call == ToolCallPart(id="c1", name="read_file", arguments={"path": "a"})


def test_two_calls_by_index_come_in_order(dialect: Dialect) -> None:
    body = stream_of(
        whole_call(0, "c1", '{"path": "a"}'),
        whole_call(1, "c2", '{"path": "b"}'),
        ended("tool_calls"),
    )
    events = play(dialect, body)
    assert [c.id for c in calls_in(events)] == ["c1", "c2"]
    assert [e.index for e in events if isinstance(e, ToolCallEvent)] == [0, 1]


def test_a_second_choice_is_passed_over(dialect: Dialect) -> None:
    data = {
        "model": "made-up-local",
        "choices": [
            {"index": 0, "delta": {"content": "Tea."}, "finish_reason": None},
            {"index": 1, "delta": {"content": "Milk."}, "finish_reason": None},
        ],
    }
    events = play(dialect, stream_of(data_line(data), ended()))
    assert fold_reply(events).message.parts == (TextPart(text="Tea."),)


# Ids.


@pytest.mark.parametrize("given", ["call.1", "c" * 65, "", 7])
def test_an_id_another_api_would_refuse_is_replaced(
    dialect: Dialect, given: object
) -> None:
    body = stream_of(whole_call(0, given, "{}"), ended("tool_calls"))
    [call] = calls_in(play(dialect, body))
    assert call.id.startswith("call_") and call.id.endswith("_1")


def test_an_id_used_twice_in_a_reply_is_replaced_the_second_time(
    dialect: Dialect,
) -> None:
    body = stream_of(
        whole_call(0, "same", "{}"), whole_call(1, "same", "{}"), ended("tool_calls")
    )
    first, second = calls_in(play(dialect, body))
    assert first.id == "same"
    assert second.id.startswith("call_") and second.id.endswith("_2")


def test_made_ids_are_the_same_for_the_same_request_and_reply(
    dialect: Dialect,
) -> None:
    body = stream_of(whole_call(0, "bad.id", "{}"), ended("tool_calls"))
    once = calls_in(play(dialect, body))
    again = calls_in(play(dialect, body))
    assert once == again
    other = dataclasses.replace(ask(), max_output=400)
    assert calls_in(play(dialect, body, other))[0].id != once[0].id


# Reasoning, stop reasons and the end of the body.


def test_reasoning_comes_whole_and_notes_its_field(dialect: Dialect) -> None:
    field = REASONING_FIELD[dialect]
    body = stream_of(
        chunk({field: "The list "}),
        chunk({field: "is short."}),
        chunk({"content": "Tea."}),
        ended(),
    )
    events = play(dialect, body)
    [thought] = [e for e in events if isinstance(e, ReasoningEvent)]
    assert thought.index == 0
    assert thought.part.text == "The list is short."
    assert thought.part.opaque == {"field": field}
    assert [e.index for e in events if isinstance(e, TextEvent)] == [1]


@pytest.mark.parametrize(
    ("wire", "reason"),
    [
        ("stop", "end"),
        ("tool_calls", "tool_calls"),
        ("length", "max_output"),
        ("content_filter", "refused"),
        ("abort", "other"),
    ],
)
def test_each_finish_reason_maps_to_one_stop_reason(
    dialect: Dialect, wire: str, reason: str
) -> None:
    body = stream_of(whole_call(0, "c1", "{}"), ended(wire))
    end = last(play(dialect, body))
    assert isinstance(end, StopEvent)
    assert end.reason == reason
    assert end.provider_reason == wire


def test_a_stream_that_ends_without_done_failed(dialect: Dialect) -> None:
    events = play(dialect, stream_of(chunk({"content": "Tea."}), chunk({}, "stop")))
    end = last(events)
    assert isinstance(end, ErrorEvent)
    assert end.failure.kind == "transient"


def test_counts_received_before_a_failure_still_come_first(dialect: Dialect) -> None:
    body = stream_of(
        chunk({"content": "Tea."}),
        chunk(None, usage=USAGE),
        data_line({"error": error_value(dialect, 500)}),
    )
    events = play(dialect, body)
    assert isinstance(events[-2], UsageEvent)
    assert isinstance(events[-1], ErrorEvent)


def test_a_call_cut_for_length_keeps_its_text(dialect: Dialect) -> None:
    body = stream_of(whole_call(0, "c1", '{"path": "a'), ended("length"))
    events = play(dialect, body)
    [call] = calls_in(events)
    assert call.raw_arguments == '{"path": "a'
    end = last(events)
    assert isinstance(end, StopEvent) and end.reason == "max_output"
