"""Translates between message parts and the content blocks of Anthropic's API."""

import json
from typing import cast

from lamplighter.ports import (
    JsonObject,
    JsonValue,
    Part,
    ReasoningPart,
    TextPart,
    ToolCallPart,
    ToolResultPart,
)

ADAPTER = "anthropic"

type Block = dict[str, JsonValue]


def to_block(part: Part) -> Block | None:
    """Give the content block for a part, or None for a part that cannot be sent.

    Reasoning made by another adapter cannot be sent, because its opaque data
    means nothing to this API.
    """
    match part:
        case TextPart():
            return {"type": "text", "text": part.text}
        case ToolCallPart():
            # The API takes only an object here. Arguments that did not parse
            # have no object form, and the tool result that follows carries
            # the parse error back to the model.
            return {
                "type": "tool_use",
                "id": part.id,
                "name": part.name,
                "input": part.arguments or {},
            }
        case ToolResultPart():
            return {
                "type": "tool_result",
                "tool_use_id": part.call_id,
                "content": part.text,
                "is_error": part.is_error,
            }
        case ReasoningPart():
            return dict(part.opaque) if part.made_by == ADAPTER else None


def can_carry_cache_mark(block: Block) -> bool:
    """Say whether the API accepts a cache mark on this block."""
    return block["type"] not in ("thinking", "redacted_thinking")


def reasoning_part(block: JsonObject) -> ReasoningPart:
    """Wrap a thinking block as it arrived, so `to_block` can send it back unchanged."""
    text = block.get("thinking")
    return ReasoningPart(
        text=text if isinstance(text, str) else "", made_by=ADAPTER, opaque=block
    )


def tool_call_part(call_id: str, name: str, arguments_text: str) -> ToolCallPart:
    """Build a tool call from the argument text the API streamed.

    Text that is not a JSON object is kept as it came, with the reason, so the
    caller can tell the model what was wrong with it.
    """
    try:
        # A tool that takes no arguments streams no argument text at all.
        arguments = json.loads(arguments_text or "{}")
    except json.JSONDecodeError as problem:
        return _unparsed(call_id, name, arguments_text, f"not JSON: {problem.msg}")
    if not isinstance(arguments, dict):
        return _unparsed(call_id, name, arguments_text, "not a JSON object")
    return ToolCallPart(
        id=call_id, name=name, arguments=cast(dict[str, JsonValue], arguments)
    )


def _unparsed(call_id: str, name: str, raw: str, why: str) -> ToolCallPart:
    return ToolCallPart(
        id=call_id, name=name, arguments=None, raw_arguments=raw, parse_error=why
    )
