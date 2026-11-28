"""Whether a reply from a local model is usable, and why not, on made-up replies."""

import dataclasses

import pytest

from lamplighter.core import Reply, ReplyReason, ReplyVerdict, judge_reply
from lamplighter.ports import (
    ErrorEvent,
    Failure,
    FailureKind,
    JsonObject,
    Message,
    Part,
    StopEvent,
    StopReason,
    TextPart,
    ToolCallPart,
)
from tests.model_samples import BROKEN_TOOL_CALL, REASONING, TEXT, TOOL, TOOL_CALL

TOOLS = (TOOL,)
WRONG_TYPE = dataclasses.replace(TOOL_CALL, id="call_3", arguments={"path": 7})
UNKNOWN = dataclasses.replace(TOOL_CALL, id="call_4", name="write_file")


def stopped(*parts: Part, reason: StopReason = "end") -> Reply:
    stop = StopEvent(reason=reason, provider_reason="", model_reported=None, skipped=0)
    return Reply(Message(role="assistant", parts=parts), None, stop)


def failed(kind: FailureKind, detail: JsonObject, *parts: Part) -> Reply:
    failure = Failure(
        kind=kind,
        effect="none",
        message="Made up.",
        source="openai_chat",
        detail=detail,
    )
    end = ErrorEvent(failure=failure, model_reported=None, skipped=0)
    return Reply(Message(role="assistant", parts=parts), None, end)


def judged(reply: Reply, recorded_call: bool = False) -> ReplyVerdict:
    return judge_reply(reply, TOOLS, recorded_call)


def test_text_where_the_recorded_reply_had_text_is_usable() -> None:
    assert judged(stopped(REASONING, TEXT)) == ReplyVerdict("usable", 0, 0)


def test_a_good_call_where_the_recorded_reply_had_text_is_usable() -> None:
    reply = stopped(TOOL_CALL, reason="tool_calls")
    assert judged(reply) == ReplyVerdict("usable", 1, 1)


def test_a_good_call_where_the_recorded_reply_had_one_is_usable() -> None:
    reply = stopped(TEXT, TOOL_CALL, reason="tool_calls")
    assert judged(reply, recorded_call=True) == ReplyVerdict("usable", 1, 1)


def test_only_text_where_the_recorded_reply_made_a_call_is_no_call() -> None:
    assert judged(stopped(TEXT), recorded_call=True) == ReplyVerdict("no_call", 0, 0)


@pytest.mark.parametrize(
    ("call", "reason"),
    [
        (BROKEN_TOOL_CALL, "unparsed"),
        (UNKNOWN, "unknown_tool"),
        (dataclasses.replace(UNKNOWN, arguments=None), "unknown_tool"),
        (WRONG_TYPE, "schema"),
        (dataclasses.replace(TOOL_CALL, arguments={}), "schema"),
    ],
)
def test_a_call_that_is_not_good_names_why(
    call: ToolCallPart, reason: ReplyReason
) -> None:
    reply = stopped(TOOL_CALL, call, reason="tool_calls")
    assert judged(reply, recorded_call=True) == ReplyVerdict(reason, 2, 1)


def test_the_first_call_that_is_not_good_gives_the_reason() -> None:
    reply = stopped(WRONG_TYPE, BROKEN_TOOL_CALL, reason="tool_calls")
    assert judged(reply) == ReplyVerdict("schema", 2, 0)


@pytest.mark.parametrize(
    "parts",
    [(), (TextPart(text=" \n\t"),), (REASONING,)],
)
def test_nothing_visible_and_no_call_is_empty(parts: tuple[Part, ...]) -> None:
    assert judged(stopped(*parts)) == ReplyVerdict("empty", 0, 0)


@pytest.mark.parametrize("reason", ["max_output", "refused", "other"])
def test_a_reply_that_did_not_end_whole_is_cut(reason: StopReason) -> None:
    reply = stopped(TEXT, TOOL_CALL, reason=reason)
    assert judged(reply) == ReplyVerdict("cut", 1, 1)


@pytest.mark.parametrize(
    ("kind", "detail", "reason"),
    [
        ("transient", {"model_output": "call_markup"}, "call_markup"),
        ("transient", {"model_output": "dropped_calls"}, "dropped_calls"),
        ("too_large", {"status": 400}, "too_large"),
        ("transient", {}, "error"),
        ("unavailable", {"model_output": "made_up"}, "error"),
    ],
)
def test_an_error_names_the_end_check_that_raised_it(
    kind: FailureKind, detail: JsonObject, reason: ReplyReason
) -> None:
    reply = failed(kind, detail, TEXT)
    assert judged(reply, recorded_call=True) == ReplyVerdict(reason, 0, 0)


def test_how_a_reply_ended_comes_before_its_calls() -> None:
    reply = failed("transient", {}, BROKEN_TOOL_CALL)
    assert judged(reply) == ReplyVerdict("error", 1, 0)
    cut = stopped(BROKEN_TOOL_CALL, reason="max_output")
    assert judged(cut) == ReplyVerdict("cut", 1, 0)


def test_a_bad_call_comes_before_a_missing_one() -> None:
    reply = stopped(BROKEN_TOOL_CALL, reason="tool_calls")
    assert judged(reply, recorded_call=True) == ReplyVerdict("unparsed", 1, 0)


def test_a_call_to_a_tool_the_request_does_not_have_is_unknown() -> None:
    reply = stopped(TOOL_CALL, reason="tool_calls")
    assert judge_reply(reply, (), False) == ReplyVerdict("unknown_tool", 1, 0)
