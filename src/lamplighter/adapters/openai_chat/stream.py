"""Turns the events of one chat completions stream into model events."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from lamplighter.adapters.openai_chat.errors import failure, failure_for_error
from lamplighter.adapters.openai_chat.parts import (
    call_id,
    reasoning_part,
    tool_call_part,
)
from lamplighter.adapters.sse import SseEvent
from lamplighter.adapters.wire_json import (
    Unreadable,
    as_object,
    list_or_empty,
    object_or_empty,
    parse_object,
    whole_or_none,
)
from lamplighter.ports import (
    ErrorEvent,
    Failure,
    ModelEvent,
    ReasoningEvent,
    StopEvent,
    StopReason,
    TextEvent,
    ToolCallEvent,
    Usage,
    UsageEvent,
)

_REASONS: dict[str, StopReason] = {
    "stop": "end",
    "tool_calls": "tool_calls",
    "length": "max_output",
    "content_filter": "refused",
}
# llama.cpp names the field one way, MLX and vLLM the other. Both are read
# before `content` and `content` before `tool_calls`, the order the servers
# write them in one chunk.
_REASONING_FIELDS = ("reasoning_content", "reasoning")
# A stream error comes with no status of its own.
_STREAM_ERROR_STATUS = 500

type _Kind = Literal["text", "reasoning", "tool_call"]


@dataclass(slots=True)
class _Open:
    """The one part the stream is adding to: text, reasoning or one tool call.

    `wire_index` is the call's `index` on the wire, and -1 for the others.
    """

    kind: _Kind
    index: int
    wire_index: int = -1
    text: str = ""
    call_id: object = None
    name: str = ""
    field: str = ""


@dataclass(slots=True)
class _Reply:
    """What the reply has given so far, for the checks at its end."""

    text: str = ""
    calls: int = 0
    closed_calls: set[int] = field(default_factory=set[int])
    ids: set[str] = field(default_factory=set[str])


class ReplyAssembler:
    """Takes the events of one stream in order and gives the model events they make."""

    def __init__(
        self, body: bytes, has_tools: bool, call_markers: Sequence[str]
    ) -> None:
        """Start with nothing received.

        `body` is the request's bytes, for made ids. `has_tools` says
        whether the request offered tools, and `call_markers` are texts only
        a call in the model's own markup holds.
        """
        self.finished = False
        self._body = body
        self._has_tools = has_tools
        self._markers = tuple(call_markers)
        self._model: str | None = None
        self._open: _Open | None = None
        self._parts = 0
        self._reply = _Reply()
        self._reason: str | None = None
        self._usage: Usage | None = None

    def feed(self, event: SseEvent) -> list[ModelEvent]:
        """Take one event of the stream and give the model events it completes."""
        if self.finished:
            return []
        if event.data == "[DONE]":
            return self._end()
        try:
            return self._handle(parse_object(event.data))
        except Unreadable as problem:
            return self.fail(failure("transient", {"unreadable": str(problem)}))
        except _StreamFault as fault:
            return self.fail(failure("transient", {"stream_fault": str(fault)}))

    def end_of_stream(self) -> list[ModelEvent]:
        """Say the body is over. One that never sent [DONE] has failed."""
        detail = "the stream ended before it said the reply was complete"
        return self.fail(failure("transient", {"unreadable": detail}))

    def fail(self, why: Failure) -> list[ModelEvent]:
        """End the reply with a failure. Counts already received still come first."""
        if self.finished:
            return []
        self.finished = True
        usage = [UsageEvent(self._usage)] if self._usage else []
        return [*usage, ErrorEvent(failure=why, model_reported=self._model, skipped=0)]

    def _handle(self, data: dict[str, Any]) -> list[ModelEvent]:
        if "error" in data:
            return self.fail(failure_for_error(_STREAM_ERROR_STATUS, data["error"]))
        model = data.get("model")
        if isinstance(model, str):
            self._model = model
        usage = data.get("usage")
        if usage is not None:
            self._usage = _usage(as_object(usage))
        choices = list_or_empty(data.get("choices"))
        if not choices:
            return []
        # Only the first choice is read. This adapter never asks for more.
        choice = as_object(choices[0])
        events = self._delta(object_or_empty(choice.get("delta")))
        reason = choice.get("finish_reason")
        if isinstance(reason, str):
            self._reason = reason
            events += self._close()
        return events

    def _delta(self, delta: dict[str, Any]) -> list[ModelEvent]:
        events: list[ModelEvent] = []
        for name in _REASONING_FIELDS:
            text = delta.get(name)
            if isinstance(text, str) and text:
                opened, closed = self._into("reasoning", -1)
                events += closed
                opened.text += text
                opened.field = name
        text = delta.get("content")
        if isinstance(text, str) and text:
            opened, closed = self._into("text", -1)
            events += closed
            self._reply.text += text
            events.append(TextEvent(index=opened.index, text=text))
        for call in list_or_empty(delta.get("tool_calls")):
            events += self._call_piece(as_object(call))
        return events

    def _call_piece(self, call: dict[str, Any]) -> list[ModelEvent]:
        wire_index = whole_or_none(call, "index")
        if wire_index is None:
            raise _StreamFault("a tool call piece had no index")
        if wire_index in self._reply.closed_calls:
            raise _StreamFault("a tool call came in pieces split by another part")
        opened, events = self._into("tool_call", wire_index)
        # The id and the name are set, never joined: some servers send them
        # again on every piece.
        if call.get("id") is not None:
            opened.call_id = call["id"]
        function = object_or_empty(call.get("function"))
        name = function.get("name")
        if isinstance(name, str) and name:
            opened.name = name
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            opened.text += arguments
        return events

    def _into(self, kind: _Kind, wire_index: int) -> tuple[_Open, list[ModelEvent]]:
        """Give the open part of this kind, closing the one before if it differs."""
        opened = self._open
        if opened and opened.kind == kind and opened.wire_index == wire_index:
            return opened, []
        events = self._close()
        # The index is given when the part opens, so indexes come in order
        # with no gaps whatever closes when.
        self._open = _Open(kind=kind, index=self._parts, wire_index=wire_index)
        self._parts += 1
        return self._open, events

    def _close(self) -> list[ModelEvent]:
        opened, self._open = self._open, None
        if opened is None or opened.kind == "text":
            # A text part's pieces went out as they came.
            return []
        if opened.kind == "reasoning":
            part = reasoning_part(opened.text, opened.field)
            return [ReasoningEvent(index=opened.index, part=part)]
        reply = self._reply
        reply.calls += 1
        reply.closed_calls.add(opened.wire_index)
        made = call_id(opened.call_id, reply.ids, self._body, reply.calls)
        reply.ids.add(made)
        part = tool_call_part(made, opened.name, opened.text)
        return [ToolCallEvent(index=opened.index, part=part)]

    def _end(self) -> list[ModelEvent]:
        closed = self._close()
        if self._has_tools and any(m in self._reply.text for m in self._markers):
            # A call the server could not parse, passed on as text. Shown as
            # the answer, it would be markup the person cannot use.
            return self.fail(failure("transient", {"model_output": "call_markup"}))
        if self._reason == "tool_calls" and self._reply.calls == 0:
            # MLX drops a call it cannot parse and still says it made calls.
            return self.fail(failure("transient", {"model_output": "dropped_calls"}))
        self.finished = True
        reason = self._reason or ""
        usage = [UsageEvent(self._usage)] if self._usage else []
        stop = StopEvent(
            reason=_REASONS.get(reason, "other"),
            provider_reason=reason,
            model_reported=self._model,
            skipped=0,
        )
        return [*closed, *usage, stop]


class _StreamFault(Exception):
    """Pieces of a reply that cannot be put together. Caught in `feed`."""


def _usage(usage: dict[str, Any]) -> Usage:
    prompt = whole_or_none(usage, "prompt_tokens")
    details = object_or_empty(usage.get("prompt_tokens_details"))
    read = whole_or_none(details, "cached_tokens")
    output = whole_or_none(usage, "completion_tokens")
    if prompt is None or read is None:
        # The split is not known, so no part of it is given.
        return Usage(None, None, None, output)
    # vLLM counts what it wrote to its cache. llama.cpp reads every prompt
    # token from its cache or not, and has no write count.
    write = whole_or_none(details, "created_cache_tokens") or 0
    return Usage(prompt - read - write, read, write, output)
