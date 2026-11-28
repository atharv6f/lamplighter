"""Whether one reply from a local model could be used as the loop would use it.

The bake-off asks each candidate the requests the loop really sent, and
judges each reply here. A reply is usable when it ended whole, the
adapter's end checks raised nothing, every call names a tool of its
request, parses and fits that tool's schema, and it acts as the reply
that was recorded did: a call where that reply made one, and text or a
call where it did not.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from lamplighter.core.arguments_check import problems
from lamplighter.core.reply import Reply
from lamplighter.core.reply_shape import has_visible_text, tool_calls
from lamplighter.ports import ErrorEvent, ToolCallPart, ToolSpec

type ReplyReason = Literal[
    "usable",
    "dropped_calls",
    "call_markup",
    "unparsed",
    "unknown_tool",
    "schema",
    "no_call",
    "empty",
    "cut",
    "too_large",
    "error",
]
type _CallReason = Literal["usable", "unparsed", "unknown_tool", "schema"]
# The adapter's end checks name what they found in the failure's detail.
_END_CHECKS: dict[object, ReplyReason] = {
    "call_markup": "call_markup",
    "dropped_calls": "dropped_calls",
}


@dataclass(frozen=True, slots=True)
class ReplyVerdict:
    """A reply's reason, `usable` or why not, and how many of its calls were good.

    A good call names a tool of its request, parsed, and fits the schema.
    """

    reason: ReplyReason
    calls: int
    good_calls: int


def judge_reply(
    reply: Reply, tools: Sequence[ToolSpec], recorded_call: bool
) -> ReplyVerdict:
    """Judge one reply to a request with these tools.

    `recorded_call` says whether the recorded reply to the same request
    made a tool call. When several things are wrong, the first of these
    is the reason: how the reply ended, a call that is not good, in the
    order of the calls, then a missing call, then nothing at all.
    """
    calls = tool_calls(reply)
    each: list[_CallReason] = [_call_reason(call, tools) for call in calls]
    good = each.count("usable")
    ended = _ending(reply)
    if ended is not None:
        return ReplyVerdict(ended, len(calls), good)
    bad: _CallReason | None = next((r for r in each if r != "usable"), None)
    if bad is not None:
        return ReplyVerdict(bad, len(calls), good)
    if recorded_call and not calls:
        return ReplyVerdict("no_call", 0, 0)
    if not calls and not any(has_visible_text(p) for p in reply.message.parts):
        return ReplyVerdict("empty", 0, 0)
    return ReplyVerdict("usable", len(calls), good)


def _ending(reply: Reply) -> ReplyReason | None:
    end = reply.end
    if isinstance(end, ErrorEvent):
        if end.failure.kind == "too_large":
            return "too_large"
        return _END_CHECKS.get(end.failure.detail.get("model_output"), "error")
    if end.reason not in ("end", "tool_calls"):
        return "cut"
    return None


def _call_reason(call: ToolCallPart, tools: Sequence[ToolSpec]) -> _CallReason:
    tool = next((t for t in tools if t.name == call.name), None)
    if tool is None:
        return "unknown_tool"
    if call.arguments is None:
        return "unparsed"
    if problems(tool.arguments_schema, call.arguments):
        return "schema"
    return "usable"
