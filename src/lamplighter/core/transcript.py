"""Rebuilds the conversation the model sees from the saved events of a session."""

from collections.abc import Sequence

from lamplighter.core.event_types import (
    MODEL_RESPONSE,
    READ_VERSION,
    TOOL_RESULT,
)
from lamplighter.core.harness_texts import EMPTY_REPLY_NOTE, run_end_note
from lamplighter.core.reply import Reply
from lamplighter.core.reply_shape import as_sent_back, is_empty, is_whole
from lamplighter.ports import (
    RUN_END,
    USER_MESSAGE,
    Event,
    Failure,
    Message,
    Part,
    TextPart,
    ToolResultPart,
    from_stored,
)

_SOURCE = "transcript"


def rebuild_messages(events: Sequence[Event]) -> tuple[Message, ...] | Failure:
    """Give the transcript of one session's events, oldest first.

    A user message, a tool result, the note after an empty reply and the
    note after a run that did not end `done` each add a part on the user
    side. The user side parts between two replies become one message, tool
    results first and texts after, each in event order. A whole reply is
    added as one assistant message, without its blank text parts. A reply
    that failed or was refused is left out, and no missing tool result is
    ever made up. Every other event adds nothing.

    Gives `needs_user` for a type or version this code cannot read, and
    `fatal` for a payload that does not fit its type.
    """
    messages: list[Message] = []
    results: list[Part] = []
    texts: list[Part] = []
    for event in events:
        if READ_VERSION.get(event.type) != event.v:
            return Failure(
                kind="needs_user",
                effect="none",
                message=f"This code cannot read a {event.type} event at version"
                f" {event.v}, at seq {event.seq}.",
                source=_SOURCE,
            )
        try:
            added = _read(event)
        except ValueError:
            return Failure(
                kind="fatal",
                effect="none",
                message=f"The {event.type} event at seq {event.seq} does not fit.",
                source=_SOURCE,
            )
        if isinstance(added, ToolResultPart):
            results.append(added)
        elif isinstance(added, TextPart):
            texts.append(added)
        elif isinstance(added, Message):
            _close_user_side(messages, results, texts)
            messages.append(added)
    _close_user_side(messages, results, texts)
    return tuple(messages)


def _read(event: Event) -> TextPart | ToolResultPart | Message | None:
    if event.type == USER_MESSAGE:
        return from_stored(TextPart, event.payload)
    if event.type == TOOL_RESULT:
        return from_stored(ToolResultPart, event.payload)
    if event.type == MODEL_RESPONSE:
        return _from_reply(from_stored(Reply, event.payload))
    if event.type == RUN_END:
        reason = event.payload.get("reason")
        if not isinstance(reason, str):
            raise ValueError("a run_end names its reason")
        note = run_end_note(reason)
        return None if note is None else TextPart(text=note)
    return None


def _from_reply(reply: Reply) -> TextPart | Message | None:
    if is_whole(reply):
        return as_sent_back(reply.message)
    if is_empty(reply):
        # The empty reply is never sent back, so its note joins the user
        # message that the empty reply answered.
        return TextPart(text=EMPTY_REPLY_NOTE)
    return None


def _close_user_side(
    messages: list[Message], results: list[Part], texts: list[Part]
) -> None:
    if results or texts:
        messages.append(Message(role="user", parts=(*results, *texts)))
        results.clear()
        texts.clear()
