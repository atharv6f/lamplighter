"""Folds the events of one model stream back into one reply."""

from collections.abc import Iterable
from dataclasses import dataclass

from lamplighter.ports import (
    ErrorEvent,
    Message,
    ModelEvent,
    Part,
    ReasoningEvent,
    StopEvent,
    TextEvent,
    TextPart,
    ToolCallEvent,
    Usage,
    UsageEvent,
)


@dataclass(frozen=True, slots=True)
class Reply:
    """One whole reply: its parts in order, its token counts, and how it ended.

    `message` can go into the next request as it is. `usage` is None when the
    stream gave no counts. After an ErrorEvent the message holds whatever
    arrived before the failure.
    """

    message: Message
    usage: Usage | None
    end: StopEvent | ErrorEvent


def fold_reply(events: Iterable[ModelEvent]) -> Reply:
    """Rebuild the reply from every event of one finished stream.

    Raises ValueError when the events have no last event, because a stream
    that was closed early is not a reply.
    """
    pieces: dict[int, list[str]] = {}
    parts: dict[int, Part] = {}
    usage: Usage | None = None
    end: StopEvent | ErrorEvent | None = None
    for event in events:
        match event:
            case TextEvent():
                pieces.setdefault(event.index, []).append(event.text)
            case ReasoningEvent() | ToolCallEvent():
                parts[event.index] = event.part
            case UsageEvent():
                usage = event.usage
            case StopEvent() | ErrorEvent():
                end = event
    if end is None:
        raise ValueError("the stream has no last event")
    for index, texts in pieces.items():
        parts[index] = TextPart(text="".join(texts))
    in_order = tuple(parts[index] for index in sorted(parts))
    return Reply(
        message=Message(role="assistant", parts=in_order), usage=usage, end=end
    )
