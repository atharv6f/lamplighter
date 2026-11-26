"""Writes made-up stream bodies in the format Anthropic's API sends."""

import json
from typing import Any

from lamplighter.adapters import ScriptedBreak, ScriptedReply
from lamplighter.adapters.anthropic.sse import SseReader
from lamplighter.adapters.anthropic.stream import ReplyAssembler
from lamplighter.ports import (
    ModelEvent,
    ReasoningPart,
    TextPart,
    ToolCallPart,
    Usage,
)

MODEL = "made-up-1"
SIGNED = {"type": "thinking", "thinking": "", "signature": "bWFkZS11cA=="}
OWN_REASONING = ReasoningPart(text="", made_by="anthropic", opaque=SIGNED)

WIRE_REASON = {
    "end": "end_turn",
    "tool_calls": "tool_use",
    "max_output": "max_tokens",
    "refused": "refusal",
    "other": "pause_turn",
}
WIRE_ERROR = {
    "transient": "overloaded_error",
    "throttled": "rate_limit_error",
    "timeout": "timeout_error",
    "invalid_input": "invalid_request_error",
    "needs_user": "authentication_error",
    "too_large": "request_too_large",
    "fatal": "not_found_error",
}


def sse(name: str, data: dict[str, Any]) -> bytes:
    """Write one event."""
    return f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()


def message_start(usage: Usage | None = None, model: str = MODEL) -> bytes:
    """Write the first event of a stream, with the input counts of `usage`."""
    counts: dict[str, int] = {"output_tokens": 1}
    names = {
        "input_tokens": usage and usage.input_uncached,
        "cache_creation_input_tokens": usage and usage.cache_write,
        "cache_read_input_tokens": usage and usage.cache_read,
    }
    counts.update({name: count for name, count in names.items() if count is not None})
    message: dict[str, Any] = {
        "id": "msg_made_up",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": counts,
    }
    return sse("message_start", {"type": "message_start", "message": message})


def block_start(index: int, block: dict[str, Any]) -> bytes:
    """Write the event that opens a content block."""
    data = {"type": "content_block_start", "index": index, "content_block": block}
    return sse("content_block_start", data)


def block_delta(index: int, delta: dict[str, Any]) -> bytes:
    """Write one delta of a content block."""
    data = {"type": "content_block_delta", "index": index, "delta": delta}
    return sse("content_block_delta", data)


def block_stop(index: int) -> bytes:
    """Write the event that closes a content block."""
    return sse("content_block_stop", {"type": "content_block_stop", "index": index})


def message_end(wire_reason: str, output: int | None) -> bytes:
    """Write the two events that end a reply."""
    usage = {} if output is None else {"output_tokens": output}
    delta = {"stop_reason": wire_reason, "stop_sequence": None}
    data = {"type": "message_delta", "delta": delta, "usage": usage}
    return sse("message_delta", data) + sse("message_stop", {"type": "message_stop"})


def error(error_type: str) -> bytes:
    """Write an error event, as the API sends one in the middle of a stream."""
    problem = {"type": error_type, "message": "A made-up problem."}
    return sse("error", {"type": "error", "error": problem})


PING = sse("ping", {"type": "ping"})


def _cut(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def part_events(index: int, part: TextPart | ToolCallPart | ReasoningPart) -> bytes:
    """Write the events of one content block for a part of a reply."""
    match part:
        case TextPart():
            start = block_start(index, {"type": "text", "text": ""})
            deltas = [
                block_delta(index, {"type": "text_delta", "text": piece})
                for piece in _cut(part.text, 9)
            ]
        case ToolCallPart():
            start = block_start(
                index,
                {"type": "tool_use", "id": part.id, "name": part.name, "input": {}},
            )
            arguments = part.raw_arguments or json.dumps(part.arguments)
            deltas = [
                block_delta(index, {"type": "input_json_delta", "partial_json": piece})
                for piece in _cut(arguments, 7)
            ]
        case ReasoningPart():
            opened = {"type": "thinking", "thinking": "", "signature": ""}
            start = block_start(index, opened)
            signature = {"type": "signature_delta", "signature": SIGNED["signature"]}
            deltas = [block_delta(index, signature)]
    return start + b"".join(deltas) + block_stop(index)


def stream_body(turn: ScriptedReply | ScriptedBreak) -> bytes:
    """Write the whole stream body the API would send for one scripted turn."""
    usage = turn.usage if isinstance(turn, ScriptedReply) else None
    body = message_start(usage) + PING
    for index, part in enumerate(turn.parts):
        body += part_events(index, part)
    if isinstance(turn, ScriptedBreak):
        return body + error(WIRE_ERROR[turn.failure.kind])
    return body + message_end(WIRE_REASON[turn.reason], usage and usage.output)


def assemble(body: bytes, piece_size: int = 1_000_000) -> list[ModelEvent]:
    """Read a stream body in pieces of the given size and give its model events."""
    reader, assembler = SseReader(), ReplyAssembler()
    events: list[ModelEvent] = []
    for start in range(0, len(body), piece_size):
        for event in reader.feed(body[start : start + piece_size]):
            events += assembler.feed(event)
    return events + assembler.end_of_stream()
