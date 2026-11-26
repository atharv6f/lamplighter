"""Turns the events of one Anthropic stream into model events."""

from dataclasses import dataclass, field
from typing import Any

from lamplighter.adapters.anthropic.blocks import reasoning_part, tool_call_part
from lamplighter.adapters.anthropic.errors import failure, failure_in_stream
from lamplighter.adapters.anthropic.sse import SseEvent
from lamplighter.adapters.anthropic.wire import (
    Unreadable,
    as_object,
    object_or_empty,
    parse_object,
    text_in,
    whole_in,
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
    "end_turn": "end",
    "tool_use": "tool_calls",
    "max_tokens": "max_output",
    "refusal": "refused",
}

_REASONING_BLOCKS = ("thinking", "redacted_thinking")
_KNOWN_BLOCKS = ("text", "tool_use", *_REASONING_BLOCKS)

_INPUT_COUNTS = {
    "input_uncached": "input_tokens",
    "cache_read": "cache_read_input_tokens",
    "cache_write": "cache_creation_input_tokens",
}


@dataclass(slots=True)
class _OpenBlock:
    """A content block the stream has started and not yet closed."""

    kind: str
    block: dict[str, Any]
    arguments_text: str = ""
    part_index: int | None = None


@dataclass(slots=True)
class _Counts:
    """The latest token counts the stream has reported. None means not reported."""

    inputs: dict[str, int] = field(default_factory=dict[str, int])
    output: int | None = None


class ReplyAssembler:
    """Takes the events of one stream in order and gives the model events they make.

    Once it has given a StopEvent or an ErrorEvent it is finished, and gives
    nothing more whatever it is fed.
    """

    def __init__(self) -> None:
        """Start with nothing received."""
        self.finished = False
        self._model: str | None = None
        self._skipped = 0
        self._counts = _Counts()
        self._open: dict[int, _OpenBlock] = {}
        self._parts = 0
        self._stop_reason = ""

    def feed(self, event: SseEvent) -> list[ModelEvent]:
        """Take one event of the stream and give the model events it completes."""
        if self.finished:
            return []
        try:
            return self._handle(event.name, parse_object(event.data))
        except Unreadable as problem:
            return self._fail(failure("transient", {"unreadable": str(problem)}))

    def end_of_stream(self) -> list[ModelEvent]:
        """Say the body is over. A stream that never said it was done has failed."""
        if self.finished:
            return []
        detail = "the stream ended before it said the reply was complete"
        return self._fail(failure("transient", {"unreadable": detail}))

    def _handle(self, name: str, data: dict[str, Any]) -> list[ModelEvent]:
        match name:
            case "message_start":
                return self._on_message_start(data)
            case "content_block_start":
                return self._on_block_start(data)
            case "content_block_delta":
                return self._on_block_delta(data)
            case "content_block_stop":
                return self._on_block_stop(data)
            case "message_delta":
                return self._on_message_delta(data)
            case "message_stop":
                return self._stop()
            case "error":
                return self._fail(failure_in_stream(as_object(data.get("error"))))
            case "ping":
                return []
            case _:
                # The API may add events. An unknown one is passed over and counted.
                self._skipped += 1
                return []

    def _on_message_start(self, data: dict[str, Any]) -> list[ModelEvent]:
        message = as_object(data.get("message"))
        self._model = text_in(message, "model")
        self._read_input_counts(object_or_empty(message.get("usage")))
        return []

    def _on_block_start(self, data: dict[str, Any]) -> list[ModelEvent]:
        block = as_object(data.get("content_block"))
        kind = text_in(block, "type")
        if kind not in _KNOWN_BLOCKS:
            self._skipped += 1
        opened = _OpenBlock(kind=kind, block=block)
        self._open[whole_in(data, "index")] = opened
        first_text = block.get("text") if kind == "text" else ""
        return self._text(opened, first_text) if isinstance(first_text, str) else []

    def _on_block_delta(self, data: dict[str, Any]) -> list[ModelEvent]:
        opened = self._block_at(data)
        delta = as_object(data.get("delta"))
        match text_in(delta, "type"):
            case "text_delta":
                return self._text(opened, text_in(delta, "text"))
            case "input_json_delta":
                opened.arguments_text += text_in(delta, "partial_json")
            case "thinking_delta":
                so_far = opened.block.get("thinking", "")
                opened.block["thinking"] = so_far + text_in(delta, "thinking")
            case "signature_delta":
                so_far = opened.block.get("signature", "")
                opened.block["signature"] = so_far + text_in(delta, "signature")
            case _:
                if opened.kind in _KNOWN_BLOCKS:
                    self._skipped += 1
        return []

    def _on_block_stop(self, data: dict[str, Any]) -> list[ModelEvent]:
        opened = self._block_at(data)
        del self._open[whole_in(data, "index")]
        if opened.kind == "tool_use":
            part = tool_call_part(
                text_in(opened.block, "id"),
                text_in(opened.block, "name"),
                opened.arguments_text,
            )
            return [ToolCallEvent(index=self._next_part(), part=part)]
        if opened.kind in _REASONING_BLOCKS:
            part = reasoning_part(opened.block)
            return [ReasoningEvent(index=self._next_part(), part=part)]
        return []

    def _on_message_delta(self, data: dict[str, Any]) -> list[ModelEvent]:
        reason = as_object(data.get("delta")).get("stop_reason")
        if isinstance(reason, str):
            self._stop_reason = reason
        usage = object_or_empty(data.get("usage"))
        self._read_input_counts(usage)
        # Only this event carries the real output count. The first event of a
        # stream carries a starting value, which is not the answer.
        output = whole_or_none(usage, "output_tokens")
        if output is not None:
            self._counts.output = output
        return []

    def _read_input_counts(self, usage: dict[str, Any]) -> None:
        # Each report is a running total, so the last value replaces the one before.
        for name, wire_name in _INPUT_COUNTS.items():
            count = whole_or_none(usage, wire_name)
            if count is not None:
                self._counts.inputs[name] = count

    def _block_at(self, data: dict[str, Any]) -> _OpenBlock:
        opened = self._open.get(whole_in(data, "index"))
        if opened is None:
            raise Unreadable("an event names a block that is not open")
        return opened

    def _text(self, opened: _OpenBlock, text: str) -> list[ModelEvent]:
        # A text part with no text must not come to exist: the API refuses an
        # empty text block when the reply is sent back.
        if text == "":
            return []
        if opened.part_index is None:
            opened.part_index = self._next_part()
        return [TextEvent(index=opened.part_index, text=text)]

    def _next_part(self) -> int:
        self._parts += 1
        return self._parts - 1

    def _stop(self) -> list[ModelEvent]:
        self.finished = True
        stop = StopEvent(
            reason=_REASONS.get(self._stop_reason, "other"),
            provider_reason=self._stop_reason,
            model_reported=self._model,
            skipped=self._skipped,
        )
        return [*self._usage(), stop]

    def _fail(self, why: Failure) -> list[ModelEvent]:
        self.finished = True
        error = ErrorEvent(
            failure=why, model_reported=self._model, skipped=self._skipped
        )
        return [*self._usage(), error]

    def _usage(self) -> list[ModelEvent]:
        inputs, output = self._counts.inputs, self._counts.output
        if not inputs and output is None:
            return []
        usage = Usage(
            input_uncached=inputs.get("input_uncached"),
            cache_read=inputs.get("cache_read"),
            cache_write=inputs.get("cache_write"),
            output=output,
        )
        return [UsageEvent(usage=usage)]
