"""Rebuilds the conversation the model sees from the saved events of a session."""

from collections.abc import Sequence

from lamplighter.core.reply import Reply
from lamplighter.ports import (
    RUN_END,
    RUN_STARTED,
    USER_MESSAGE,
    Event,
    Failure,
    Message,
    Part,
    StopEvent,
    TextPart,
    ToolResultPart,
    from_stored,
)

_SOURCE = "transcript"

TOOL_RESULT = "tool_result"
MODEL_RESPONSE = "model_response"

# The payload version of each type this function reads. A newer version
# was written by newer code, and old code never guesses at its meaning.
_READ_VERSION = {USER_MESSAGE: 1, TOOL_RESULT: 1, MODEL_RESPONSE: 1}


def rebuild_messages(events: Sequence[Event]) -> tuple[Message, ...] | Failure:
    """Give the transcript of one session's events, oldest first.

    A user message and a tool result each add a part on the user side, and
    the user side parts between two replies become one message, with tool
    results first. A reply that ended in a stop and has parts is added as
    one assistant message. A reply that was cut off is left out, and no
    missing tool result is ever made up. Run marks add nothing.

    Gives `needs_user` for a type or version this code cannot read, and
    `fatal` for a payload that does not fit its type.
    """
    messages: list[Message] = []
    results: list[Part] = []
    texts: list[Part] = []
    for event in events:
        if event.type in (RUN_STARTED, RUN_END):
            continue
        if _READ_VERSION.get(event.type) != event.v:
            return Failure(
                kind="needs_user",
                effect="none",
                message=f"This code cannot read a {event.type} event at version"
                f" {event.v}, at seq {event.seq}.",
                source=_SOURCE,
            )
        try:
            part_or_reply = _read(event)
        except ValueError:
            return Failure(
                kind="fatal",
                effect="none",
                message=f"The {event.type} event at seq {event.seq} does not fit.",
                source=_SOURCE,
            )
        if isinstance(part_or_reply, ToolResultPart):
            results.append(part_or_reply)
        elif isinstance(part_or_reply, TextPart):
            texts.append(part_or_reply)
        elif _is_whole(part_or_reply):
            _close_user_side(messages, results, texts)
            parts = part_or_reply.message.parts
            messages.append(Message(role="assistant", parts=parts))
    _close_user_side(messages, results, texts)
    return tuple(messages)


def _read(event: Event) -> TextPart | ToolResultPart | Reply:
    if event.type == USER_MESSAGE:
        return from_stored(TextPart, event.payload)
    if event.type == TOOL_RESULT:
        return from_stored(ToolResultPart, event.payload)
    return from_stored(Reply, event.payload)


def _is_whole(reply: Reply) -> bool:
    # A reply that ended in an error was cut off, and the model must not see
    # half of it as if it had said it.
    return isinstance(reply.end, StopEvent) and bool(reply.message.parts)


def _close_user_side(
    messages: list[Message], results: list[Part], texts: list[Part]
) -> None:
    if results or texts:
        messages.append(Message(role="user", parts=(*results, *texts)))
        results.clear()
        texts.clear()
