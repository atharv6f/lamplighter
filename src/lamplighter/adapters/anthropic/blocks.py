"""Translates message parts to the content blocks of Anthropic's Messages API."""

from lamplighter.ports import (
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
