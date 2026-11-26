import pytest

from lamplighter.core import Reply
from lamplighter.core.reply_shape import (
    final_text,
    is_empty,
    is_refused,
    is_whole,
    tool_calls,
)
from lamplighter.ports import (
    ErrorEvent,
    Message,
    Part,
    StopEvent,
    StopReason,
    TextPart,
    ToolCallPart,
)
from tests.model_samples import REASONING, THROTTLED

CALL = ToolCallPart(id="call_a", name="wait", arguments={"seconds": 1})


def reply(*parts: Part, reason: StopReason = "end") -> Reply:
    end = StopEvent(
        reason=reason, provider_reason=reason, model_reported=None, skipped=0
    )
    return Reply(message=Message(role="assistant", parts=parts), usage=None, end=end)


def broken(*parts: Part) -> Reply:
    end = ErrorEvent(failure=THROTTLED, model_reported=None, skipped=0)
    return Reply(message=Message(role="assistant", parts=parts), usage=None, end=end)


@pytest.mark.parametrize(
    ("made", "whole", "empty", "refused"),
    [
        (reply(TextPart(text="Hi.")), True, False, False),
        (reply(CALL, reason="tool_calls"), True, False, False),
        (reply(TextPart(text="Hi."), reason="max_output"), True, False, False),
        (reply(), False, True, False),
        (reply(REASONING), False, True, False),
        (reply(TextPart(text=" \n")), False, True, False),
        (reply(TextPart(text="No."), reason="refused"), False, False, True),
        (broken(TextPart(text="Hel")), False, False, False),
        (broken(), False, False, False),
    ],
)
def test_each_reply_is_whole_empty_refused_or_none(
    made: Reply, whole: bool, empty: bool, refused: bool
) -> None:
    assert (is_whole(made), is_empty(made), is_refused(made)) == (whole, empty, refused)


def test_the_final_text_joins_visible_text_with_a_blank_line() -> None:
    made = reply(
        TextPart(text="One."),
        REASONING,
        TextPart(text="  "),
        CALL,
        TextPart(text="Two."),
    )
    assert final_text(made) == "One.\n\nTwo."
    assert tool_calls(made) == (CALL,)
