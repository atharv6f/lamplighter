"""Reads server-sent events out of the bytes of a streamed HTTP body."""

import codecs
import re
from dataclasses import dataclass

_LINE_END = re.compile(r"\r\n|\r|\n")


@dataclass(frozen=True, slots=True)
class SseEvent:
    """One event of a stream: its name and its data, which is usually JSON text."""

    name: str
    data: str


class SseReader:
    """Takes the body in whatever pieces the network gives and hands back whole events.

    A piece may end anywhere, even inside a character, so the reader keeps
    what is unfinished and waits for the rest.
    """

    def __init__(self) -> None:
        """Start with nothing read."""
        # A byte that is not UTF-8 becomes the replacement character. The
        # event it sits in then fails to parse or carries that character, and
        # either way the reader goes on. A strict decoder would raise.
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._unread = ""
        self._ended_with_cr = False
        self._name = ""
        self._data: list[str] = []

    def feed(self, piece: bytes) -> list[SseEvent]:
        """Take the next bytes of the body and give every event they complete."""
        text = self._decoder.decode(piece)
        # A line may end with "\r\n", and a piece may end between the two. The
        # "\r" has then already ended the line, so the "\n" must not end another.
        if text:
            if self._ended_with_cr and text.startswith("\n"):
                text = text[1:]
            self._ended_with_cr = text.endswith("\r")
        *lines, self._unread = _LINE_END.split(self._unread + text)
        events = [self._take(line) for line in lines]
        return [event for event in events if event is not None]

    def _take(self, line: str) -> SseEvent | None:
        if line == "":
            return self._close_event()
        # A comment line starts with a colon, so its field name is empty and it
        # is passed over like any other field this reader does not use.
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "event":
            self._name = value
        elif field == "data":
            self._data.append(value)
        return None

    def _close_event(self) -> SseEvent | None:
        name, data = self._name or "message", self._data
        self._name, self._data = "", []
        # The format says a block with no data line is not an event.
        return SseEvent(name=name, data="\n".join(data)) if data else None
