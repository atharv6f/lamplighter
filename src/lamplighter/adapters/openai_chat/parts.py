"""Translates between message parts and the messages of the chat completions format."""

import hashlib
import json
import re
from typing import Any, cast

from lamplighter.ports import (
    JsonValue,
    Message,
    ModelRequest,
    ReasoningPart,
    TextPart,
    ToolCallPart,
    ToolResultPart,
)

ADAPTER = "openai_chat"
# The ids Anthropic's API accepts. A local model's calls may later be sent to
# Anthropic after an escalation, so every id this adapter gives must pass there.
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def render_request(model: str, request: ModelRequest) -> bytes:
    """Give the bytes of a streamed chat completions call. Same request, same bytes."""
    body = prompt(model, request)
    body["max_tokens"] = request.max_output
    body["stream"] = True
    body["stream_options"] = {"include_usage": True}
    return to_bytes(body)


def to_bytes(body: dict[str, JsonValue]) -> bytes:
    """Write a body with sorted keys and no spaces: the same body, the same bytes."""
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def prompt(model: str, request: ModelRequest) -> dict[str, JsonValue]:
    """Give the model, the messages and the tools of a request, as the wire has them."""
    messages: list[JsonValue] = []
    if request.system:
        # Many chat templates take one system message, and only first.
        messages.append({"role": "system", "content": "\n\n".join(request.system)})
    for message in request.messages:
        messages += _messages(message)
    body: dict[str, JsonValue] = {"model": model, "messages": messages}
    if request.tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.arguments_schema,
                },
            }
            for tool in request.tools
        ]
    return body


def _messages(message: Message) -> list[JsonValue]:
    if message.role == "user":
        return _user_messages(message)
    text = "".join(p.text for p in message.parts if isinstance(p, TextPart))
    out: dict[str, JsonValue] = {"role": "assistant", "content": text}
    calls: list[JsonValue] = [
        {
            "id": p.id,
            "type": "function",
            "function": {"name": p.name, "arguments": _arguments_text(p)},
        }
        for p in message.parts
        if isinstance(p, ToolCallPart)
    ]
    if calls:
        out["tool_calls"] = calls
    for part in message.parts:
        if isinstance(part, ReasoningPart) and part.made_by == ADAPTER:
            # Sent back in the field it came in, which is the one this server reads.
            out[str(part.opaque["field"])] = part.text
    return [out]


def _arguments_text(call: ToolCallPart) -> str:
    # Arguments that did not parse go back as an empty object. llama.cpp
    # refuses every request whose history holds arguments that are not JSON,
    # so sending the text back would break the session for good. The tool
    # result that follows already says they did not parse.
    if call.arguments is None:
        return "{}"
    return json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)


def _user_messages(message: Message) -> list[JsonValue]:
    out: list[JsonValue] = []
    texts: list[str] = []
    for part in message.parts:
        if isinstance(part, ToolResultPart):
            out.append(
                {"role": "tool", "tool_call_id": part.call_id, "content": part.text}
            )
        elif isinstance(part, TextPart):
            texts.append(part.text)
    if texts:
        out.append({"role": "user", "content": "\n\n".join(texts)})
    return out


def call_id(given: object, taken: set[str], body: bytes, place: int) -> str:
    """Give the server's id if any API would take it and it is new, else a made one.

    `taken` holds the ids of the reply's earlier calls. The made id is
    `call_`, the first 24 hex characters of the SHA-256 of the request's
    bytes, `_` and the call's place in the reply from 1, so the same request
    and reply give the same id.
    """
    if isinstance(given, str) and _SAFE_ID.fullmatch(given) and given not in taken:
        return given
    digest = hashlib.sha256(body).hexdigest()[:24]
    return f"call_{digest}_{place}"


def tool_call_part(call: str, name: str, arguments_text: str) -> ToolCallPart:
    """Build a tool call from the argument text, keeping text that is not an object."""
    try:
        arguments = json.loads(arguments_text or "{}")
    except json.JSONDecodeError as problem:
        why = f"not JSON: {problem.msg}"
        return ToolCallPart(call, name, None, arguments_text, why)
    if not isinstance(arguments, dict):
        return ToolCallPart(call, name, None, arguments_text, "not a JSON object")
    return ToolCallPart(call, name, cast(dict[str, Any], arguments))


def reasoning_part(text: str, field: str) -> ReasoningPart:
    """Wrap reasoning text, noting which field it came in."""
    return ReasoningPart(text=text, made_by=ADAPTER, opaque={"field": field})
