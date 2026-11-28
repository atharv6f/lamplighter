"""A made-up local chat completions server in three dialects, playing a script."""

import json
from collections.abc import Callable, Coroutine, Sequence
from typing import Literal

import httpx

from lamplighter.adapters import ScriptedBreak, ScriptedReply
from lamplighter.adapters.openai_chat import OpenAIChatModel
from lamplighter.ports import ModelProfile
from tests.anthropic.fake_server import Answer, FakeServer
from tests.fake_clock import FakeClock
from tests.openai_chat.wire_text import (
    WIRE_STATUS,
    Dialect,
    error_body,
    model_of,
    stream_body,
)

BASE_URL = "http://127.0.0.1:9931"
PORT = 9931
SERVER_DIR = "/made-up/lamplighter-models/server"
OWNER_LINE = f"{SERVER_DIR}/bin/made-up-server --port 9931 --alias made-up-local"
SERVER_KIND: dict[Dialect, Literal["llama.cpp", "mlx-lm", "vllm-metal"]] = {
    "llama_cpp": "llama.cpp",
    "mlx_lm": "mlx-lm",
    "vllm": "vllm-metal",
}
COUNT_KIND: dict[Dialect, Literal["llama_cpp", "vllm", "none"]] = {
    "llama_cpp": "llama_cpp",
    "mlx_lm": "none",
    "vllm": "vllm",
}
COUNT_PATH: dict[Dialect, str] = {
    "llama_cpp": "/v1/chat/completions/input_tokens",
    "vllm": "/tokenize",
}
MARKERS = ("<tool_call>", "<function=")
COUNTED = 12

type Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
type Turn = ScriptedReply | ScriptedBreak


def profile_for(dialect: Dialect, window: int = 65_536) -> ModelProfile:
    """Give the profile of the made-up model on a server of this dialect."""
    return ModelProfile(
        adapter="openai_chat",
        model=model_of(dialect),
        max_output=2_000,
        tool_calls=True,
        cache_hints=0,
        count_error_permille=0,
        window=window,
        concurrent_requests=2,
        stays_on_mac=True,
    )


def owned(port: int, seconds: float) -> str | None:
    """Say the made-up server's own program holds every port."""
    return OWNER_LINE


def client_for(
    server: Handler,
    dialect: Dialect,
    clock: FakeClock | None = None,
    profile: ModelProfile | None = None,
    port_owner: Callable[[int, float], str | None] = owned,
) -> OpenAIChatModel:
    """Give a local client whose calls all go to the made-up server."""
    return OpenAIChatModel(
        profile or profile_for(dialect),
        BASE_URL,
        SERVER_DIR,
        port_owner,
        clock or FakeClock(),
        server_kind=SERVER_KIND[dialect],
        count=COUNT_KIND[dialect],
        call_markers=MARKERS,
        transport=httpx.MockTransport(server),
    )


def counted(dialect: Dialect, tokens: int = COUNTED) -> httpx.Response:
    """Write a server's answer to a count, in its own field."""
    field = "input_tokens" if dialect == "llama_cpp" else "count"
    return httpx.Response(200, content=json.dumps({field: tokens}))


def refusal(
    dialect: Dialect, status: int, error_type: str = "made_up"
) -> httpx.Response:
    """Write an error response in the shape a server of this dialect sends."""
    return httpx.Response(status, content=error_body(dialect, status, error_type))


def answer_for(turn: Turn, dialect: Dialect) -> httpx.Response:
    """Write the response a server of this dialect gives for one scripted turn."""
    if isinstance(turn, ScriptedBreak) and not turn.parts:
        return refusal(dialect, WIRE_STATUS[turn.failure.kind])
    return httpx.Response(200, content=stream_body(turn, dialect))


def has_unparsed_arguments(request: httpx.Request) -> bool:
    """Say whether a request's history holds tool call arguments that are not JSON."""
    body = json.loads(request.content)
    for message in body.get("messages", []):
        for call in message.get("tool_calls", []):
            try:
                json.loads(call["function"]["arguments"])
            except json.JSONDecodeError:
                return True
    return False


def serving(dialect: Dialect, turns: Sequence[Turn]) -> tuple[Handler, FakeServer]:
    """Give a server that counts and plays the turns, and the record of its calls."""
    return answering(dialect, *[answer_for(turn, dialect) for turn in turns])


def answering(dialect: Dialect, *answers: Answer) -> tuple[Handler, FakeServer]:
    """Give a server that counts and gives these answers to chat calls in turn.

    The record keeps the chat calls only. The llama.cpp dialect answers 500
    to a request whose history holds arguments that are not JSON, as the
    real server does.
    """
    played = FakeServer(*answers)

    async def serve(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if dialect in COUNT_PATH and path == COUNT_PATH[dialect]:
            return counted(dialect)
        if path != "/v1/chat/completions":
            return refusal(dialect, 404)
        if dialect == "llama_cpp" and has_unparsed_arguments(request):
            played.requests.append(request)
            return refusal(dialect, 500, "server_error")
        return await played(request)

    return serve, played
