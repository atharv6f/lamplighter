"""Writes a model request as the body of an Anthropic Messages API call."""

import json

from lamplighter.adapters.anthropic.blocks import (
    Block,
    can_carry_cache_mark,
    to_block,
)
from lamplighter.ports import JsonValue, Message, ModelRequest, ToolSpec


def render_request(model: str, request: ModelRequest) -> bytes:
    """Give the bytes of the body of a call for a streamed reply.

    The same request always gives the same bytes: keys are sorted, nothing is
    left to dict order, and tools stay in the order the request gives them.
    The provider's cache matches on an exact prefix, so one moved key would
    make every later call pay full price.
    """
    body = _prompt(model, request)
    body["max_tokens"] = request.max_output
    body["stream"] = True
    return _to_bytes(body)


def render_count_request(model: str, request: ModelRequest) -> bytes:
    """Give the bytes of the body of a call that only counts the prompt's tokens."""
    return _to_bytes(_prompt(model, request))


def _prompt(model: str, request: ModelRequest) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {"model": model, "messages": _messages(request)}
    if request.system:
        body["system"] = _system(request)
    if request.tools:
        body["tools"] = [_tool(tool) for tool in request.tools]
    return body


def _to_bytes(body: dict[str, JsonValue]) -> bytes:
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def _system(request: ModelRequest) -> list[Block]:
    blocks: list[Block] = [{"type": "text", "text": text} for text in request.system]
    for hint in request.cache_hints:
        if hint.after == "system":
            _mark(blocks[hint.index])
    return blocks


def _messages(request: ModelRequest) -> list[JsonValue]:
    contents = [_content(message) for message in request.messages]
    for hint in request.cache_hints:
        if hint.after == "message":
            _mark_last(contents[hint.index])
    # A message can come out with no content, when all it held was reasoning
    # made by another adapter. The API refuses an empty message, so it is
    # left out.
    return [
        {"role": message.role, "content": content}
        for message, content in zip(request.messages, contents, strict=True)
        if content
    ]


def _content(message: Message) -> list[Block]:
    blocks = [to_block(part) for part in message.parts]
    return [block for block in blocks if block is not None]


def _tool(tool: ToolSpec) -> Block:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.arguments_schema,
    }


def _mark_last(blocks: list[Block]) -> None:
    for block in reversed(blocks):
        if can_carry_cache_mark(block):
            _mark(block)
            return


def _mark(block: Block) -> None:
    # "ephemeral" with no lifetime is the 5-minute cache.
    block["cache_control"] = {"type": "ephemeral"}
