"""Says whether a saved reply is whole, empty or neither, by the loop's rules.

A whole reply ended in a stop, was not refused, and has a tool call or
visible text. Only a whole reply is ever sent back to the model. An empty
reply ended in a stop, was not refused, and has neither. Text made only of
spaces and line breaks is not visible text.
"""

from lamplighter.core.reply import Reply
from lamplighter.ports import Message, Part, StopEvent, TextPart, ToolCallPart


def has_visible_text(part: Part) -> bool:
    """Say whether a part is text with something left after `str.strip()`."""
    return isinstance(part, TextPart) and part.text.strip() != ""


def is_whole(reply: Reply) -> bool:
    """Say whether the reply may be sent back to the model."""
    return _stopped_and_not_refused(reply) and any(
        isinstance(part, ToolCallPart) or has_visible_text(part)
        for part in reply.message.parts
    )


def is_empty(reply: Reply) -> bool:
    """Say whether the reply ended normally with nothing in it to act on."""
    return _stopped_and_not_refused(reply) and not is_whole(reply)


def is_refused(reply: Reply) -> bool:
    """Say whether the model declined to answer."""
    return isinstance(reply.end, StopEvent) and reply.end.reason == "refused"


def as_sent_back(message: Message) -> Message:
    """Give a whole reply as it goes into the next request.

    A text part with no visible text is left out, because the API refuses an
    empty text block, and then every later request of the session would
    fail. Nothing else is changed.
    """
    kept = tuple(
        part
        for part in message.parts
        if not isinstance(part, TextPart) or has_visible_text(part)
    )
    return Message(role=message.role, parts=kept)


def tool_calls(reply: Reply) -> tuple[ToolCallPart, ...]:
    """Give the reply's tool calls, in the model's order."""
    return tuple(part for part in reply.message.parts if isinstance(part, ToolCallPart))


def final_text(reply: Reply) -> str:
    """Give the reply's visible text parts joined with a blank line."""
    return "\n\n".join(
        part.text
        for part in reply.message.parts
        if isinstance(part, TextPart) and has_visible_text(part)
    )


def _stopped_and_not_refused(reply: Reply) -> bool:
    return isinstance(reply.end, StopEvent) and reply.end.reason != "refused"
