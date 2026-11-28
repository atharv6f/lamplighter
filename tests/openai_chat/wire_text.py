"""Writes made-up chat completions streams in the dialects of three local servers.

Each dialect writes what its server was seen to write: llama.cpp names its
reasoning field `reasoning_content`, MLX and vLLM name it `reasoning`; MLX
sends each tool call whole in one piece and its errors as one text; vLLM
ends a stream with `[DONE]` even after an error.
"""

import json
from typing import Any, Literal

from lamplighter.adapters import ScriptedBreak, ScriptedReply
from lamplighter.ports import ReasoningPart, TextPart, ToolCallPart, Usage

type Dialect = Literal["llama_cpp", "mlx_lm", "vllm"]
DIALECTS: tuple[Dialect, ...] = ("llama_cpp", "mlx_lm", "vllm")
MODEL = "made-up-local"
# The only name mlx_lm.server maps to the model it loaded.
MLX_MODEL = "default_model"
REASONING_FIELD: dict[Dialect, str] = {
    "llama_cpp": "reasoning_content",
    "mlx_lm": "reasoning",
    "vllm": "reasoning",
}
WIRE_REASON = {
    "end": "stop",
    "tool_calls": "tool_calls",
    "max_output": "length",
    "refused": "content_filter",
    "other": "abort",
}
WIRE_STATUS = {
    "transient": 500,
    "throttled": 429,
    "timeout": 504,
    "invalid_input": 400,
    "needs_user": 401,
    "too_large": 413,
    "fatal": 404,
    "unavailable": 503,
}


def model_of(dialect: Dialect) -> str:
    """Give the model name a server of this dialect is asked for."""
    return MLX_MODEL if dialect == "mlx_lm" else MODEL


def own_reasoning(dialect: Dialect) -> ReasoningPart:
    """Give a reasoning part as the adapter gives it for a server of this dialect."""
    return ReasoningPart(
        text="The list is in list.txt.",
        made_by="openai_chat",
        opaque={"field": REASONING_FIELD[dialect]},
    )


def chunk(
    delta: dict[str, Any] | None,
    finish: str | None = None,
    model: str = MODEL,
    **extra: Any,
) -> bytes:
    """Write one `data:` line of a stream, with one choice or none."""
    choices = (
        [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]
    )
    data = {
        "id": "chatcmpl-made-up",
        "object": "chat.completion.chunk",
        "model": model,
        "choices": choices,
        **extra,
    }
    return data_line(data)


def data_line(data: object) -> bytes:
    """Write one `data:` line holding this JSON value."""
    return f"data: {json.dumps(data)}\n\n".encode()


DONE = b"data: [DONE]\n\n"


def error_value(dialect: Dialect, status: int, error_type: str = "made_up") -> object:
    """Give what a server of this dialect puts under `error`."""
    if dialect == "mlx_lm":
        return "A made-up problem."
    return {"code": status, "message": "A made-up problem.", "type": error_type}


def error_body(dialect: Dialect, status: int, error_type: str = "made_up") -> bytes:
    """Write the body of an error response in this dialect."""
    return json.dumps({"error": error_value(dialect, status, error_type)}).encode()


def _cut(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def _part(
    dialect: Dialect, place: int, part: TextPart | ToolCallPart | ReasoningPart
) -> bytes:
    match part:
        case TextPart():
            return b"".join(chunk({"content": piece}) for piece in _cut(part.text, 9))
        case ReasoningPart():
            field = REASONING_FIELD[dialect]
            return b"".join(chunk({field: piece}) for piece in _cut(part.text, 5))
        case ToolCallPart():
            return _call(dialect, place, part)


def _call(dialect: Dialect, place: int, part: ToolCallPart) -> bytes:
    arguments = (
        part.raw_arguments if part.arguments is None else json.dumps(part.arguments)
    ) or ""
    head: dict[str, Any] = {
        "index": place,
        "id": part.id,
        "type": "function",
        "function": {"name": part.name, "arguments": ""},
    }
    if dialect == "mlx_lm":
        head["function"]["arguments"] = arguments
        return chunk({"role": "assistant", "tool_calls": [head]})
    body = chunk({"tool_calls": [head]})
    for piece in _cut(arguments, 7):
        more = {"index": place, "function": {"arguments": piece}}
        body += chunk({"tool_calls": [more]})
    return body


def usage_fields(usage: Usage | None) -> dict[str, Any]:
    """Give the `usage` a server sends for these counts, or nothing."""
    if usage is None or usage.input_uncached is None:
        return {}
    cached = usage.cache_read or 0
    counts: dict[str, Any] = {
        "prompt_tokens": usage.input_uncached + cached + (usage.cache_write or 0),
        "completion_tokens": usage.output,
        "prompt_tokens_details": {"cached_tokens": cached},
    }
    if usage.cache_write:
        counts["prompt_tokens_details"]["created_cache_tokens"] = usage.cache_write
    return {"usage": counts}


def stream_body(turn: ScriptedReply | ScriptedBreak, dialect: Dialect) -> bytes:
    """Write the whole body a server of this dialect would send for one turn."""
    body = b": keepalive\n\n" + chunk({"role": "assistant", "content": None})
    calls = 0
    for part in turn.parts:
        body += _part(dialect, calls, part)
        calls += isinstance(part, ToolCallPart)
    if isinstance(turn, ScriptedBreak):
        status = WIRE_STATUS[turn.failure.kind]
        body += data_line({"error": error_value(dialect, status)})
        return body + (DONE if dialect == "vllm" else b"")
    body += chunk({}, WIRE_REASON[turn.reason])
    usage = usage_fields(turn.usage)
    if usage:
        body += chunk(None, **usage)
    return body + DONE
