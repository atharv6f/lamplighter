"""The promises of the model port, checked on every client that implements it."""

import dataclasses

import pytest

from lamplighter.adapters import ScriptedBreak, ScriptedReply
from lamplighter.core import fold_reply
from lamplighter.ports import (
    ErrorEvent,
    Failure,
    Message,
    ModelEvent,
    ModelRequest,
    StopEvent,
    TextEvent,
    TextPart,
    ToolCallEvent,
    ToolCallPart,
    ToolResultPart,
    Usage,
    UsageEvent,
)
from tests.contract.model_clients import ClientKind, Turn
from tests.model_samples import TOOL
from tests.streams import collect, collect_first, count

USAGE = Usage(input_uncached=120, cache_read=0, cache_write=0, output=40)
LIST_CALL = ToolCallPart(id="call_a", name="read_file", arguments={"path": "list.txt"})
PLAN_CALL = ToolCallPart(id="call_b", name="read_file", arguments={"path": "plan.txt"})
OVERLOADED = Failure(
    kind="transient",
    effect="none",
    message="The model server is overloaded.",
    source="ModelClient/made-up",
)
REJECTED = Failure(
    kind="invalid_input",
    effect="none",
    message="The model server rejected the request.",
    source="ModelClient/made-up",
)

TEXT_REPLY = ScriptedReply(
    parts=(TextPart(text="Milk, eggs and tea. Nothing else is on the list."),),
    reason="end",
    usage=USAGE,
)


def tool_call_reply(kind: ClientKind) -> ScriptedReply:
    return ScriptedReply(
        parts=(kind.reasoning, TextPart(text="I will read the list."), LIST_CALL),
        reason="tool_calls",
        usage=USAGE,
    )


TWO_TOOL_CALLS_REPLY = ScriptedReply(
    parts=(LIST_CALL, PLAN_CALL), reason="tool_calls", usage=USAGE
)
CUT_FOR_LENGTH_REPLY = ScriptedReply(
    parts=(TextPart(text="The list starts with milk and"),),
    reason="max_output",
    usage=USAGE,
)
FAILS_AT_ONCE = ScriptedBreak(parts=(), failure=REJECTED)
FAILS_PART_WAY = ScriptedBreak(
    parts=(TextPart(text="The list starts with"),), failure=OVERLOADED
)

EVERY_TURN = [
    "text reply",
    "tool call",
    "two tool calls",
    "cut for length",
    "fails at once",
    "fails part way",
]


def play_named(kind: ClientKind, name: str) -> list[ModelEvent]:
    turns: dict[str, Turn] = {
        "text reply": TEXT_REPLY,
        "tool call": tool_call_reply(kind),
        "two tool calls": TWO_TOOL_CALLS_REPLY,
        "cut for length": CUT_FOR_LENGTH_REPLY,
        "fails at once": FAILS_AT_ONCE,
        "fails part way": FAILS_PART_WAY,
    }
    return play(kind, turns[name])


def ask(text: str = "What is on my list?") -> ModelRequest:
    return ModelRequest(
        system=("You are a careful helper.",),
        messages=(Message(role="user", parts=(TextPart(text=text),)),),
        tools=(TOOL,),
        max_output=500,
        cache_hints=(),
        deadline_ms=5_000,
        retries=0,
    )


def play(kind: ClientKind, turn: Turn) -> list[ModelEvent]:
    return collect(kind.make([turn]), ask())


@pytest.mark.parametrize("turn", EVERY_TURN)
def test_a_stream_ends_with_exactly_one_last_event(kind: ClientKind, turn: str) -> None:
    events = play_named(kind, turn)
    last_events = [e for e in events if isinstance(e, (StopEvent, ErrorEvent))]
    assert len(last_events) == 1
    assert events[-1] is last_events[0]


@pytest.mark.parametrize("turn", EVERY_TURN)
def test_usage_comes_at_most_once_and_just_before_the_last_event(
    kind: ClientKind, turn: str
) -> None:
    events = play_named(kind, turn)
    places = [i for i, e in enumerate(events) if isinstance(e, UsageEvent)]
    assert places in ([], [len(events) - 2])


@pytest.mark.parametrize("turn", EVERY_TURN)
def test_part_indexes_start_at_zero_and_never_skip_or_go_back(
    kind: ClientKind, turn: str
) -> None:
    events = play_named(kind, turn)
    indexes = [
        e.index
        for e in events
        if not isinstance(e, (UsageEvent, StopEvent, ErrorEvent))
    ]
    assert indexes == sorted(indexes)
    assert sorted(set(indexes)) == list(range(len(set(indexes))))


def test_a_text_reply_folds_back_to_the_text_that_was_sent(
    kind: ClientKind,
) -> None:
    events = play(kind, TEXT_REPLY)
    reply = fold_reply(events)
    assert reply.message.parts == TEXT_REPLY.parts
    assert isinstance(reply.end, StopEvent)
    assert reply.end.reason == "end"
    assert reply.end.model_reported is not None
    assert reply.usage == USAGE


def test_a_tool_call_arrives_whole_and_once(kind: ClientKind) -> None:
    events = play(kind, tool_call_reply(kind))
    calls = [e for e in events if isinstance(e, ToolCallEvent)]
    assert [call.part for call in calls] == [LIST_CALL]
    end = events[-1]
    assert isinstance(end, StopEvent)
    assert end.reason == "tool_calls"


def test_two_tool_calls_arrive_in_order_with_different_ids(
    kind: ClientKind,
) -> None:
    events = play(kind, TWO_TOOL_CALLS_REPLY)
    calls = [e.part for e in events if isinstance(e, ToolCallEvent)]
    assert calls == [LIST_CALL, PLAN_CALL]
    assert len({call.id for call in calls}) == 2


def test_a_reply_cut_for_length_says_so(kind: ClientKind) -> None:
    end = play(kind, CUT_FOR_LENGTH_REPLY)[-1]
    assert isinstance(end, StopEvent)
    assert end.reason == "max_output"


def test_a_failure_before_any_content_is_the_only_event(
    kind: ClientKind,
) -> None:
    events = play(kind, FAILS_AT_ONCE)
    assert len(events) == 1
    end = events[0]
    assert isinstance(end, ErrorEvent)
    assert end.failure.kind == "invalid_input"
    assert end.failure.effect == "none"


def test_a_failure_part_way_keeps_what_came_before_it(
    kind: ClientKind,
) -> None:
    events = play(kind, FAILS_PART_WAY)
    reply = fold_reply(events)
    assert isinstance(reply.end, ErrorEvent)
    assert reply.end.failure.kind == "transient"
    assert reply.end.failure.effect == "none"
    assert reply.message.parts == FAILS_PART_WAY.parts


def test_the_parts_of_a_reply_come_back_unchanged_and_are_accepted_again(
    kind: ClientKind,
) -> None:
    client = kind.make([tool_call_reply(kind), TEXT_REPLY])
    first = fold_reply(collect(client, ask()))
    assert first.message.parts == tool_call_reply(kind).parts

    result = ToolResultPart(
        call_id=LIST_CALL.id, text="milk\neggs\ntea", is_error=False
    )
    follow_up = dataclasses.replace(
        ask(),
        messages=(
            *ask().messages,
            first.message,
            Message(role="user", parts=(result,)),
        ),
    )
    second = fold_reply(collect(client, follow_up))
    assert isinstance(second.end, StopEvent)
    assert second.message.parts == TEXT_REPLY.parts


def test_a_request_over_the_output_limit_fails_and_uses_up_no_turn(
    kind: ClientKind,
) -> None:
    client = kind.make([TEXT_REPLY])
    too_much = dataclasses.replace(ask(), max_output=client.profile.max_output + 1)

    refused = collect(client, too_much)
    assert len(refused) == 1
    assert isinstance(refused[0], ErrorEvent)
    assert refused[0].failure.kind == "invalid_input"

    played = fold_reply(collect(client, ask()))
    assert played.message.parts == TEXT_REPLY.parts


def test_closing_a_stream_early_yields_nothing_more(kind: ClientKind) -> None:
    events = collect_first(kind.make([TEXT_REPLY]), ask(), count=1)
    assert len(events) == 1
    assert isinstance(events[0], TextEvent)


def test_a_token_count_is_a_whole_number_and_not_negative(
    kind: ClientKind,
) -> None:
    size = count(kind.make([]), ask())
    assert isinstance(size, int)
    assert size >= 0
