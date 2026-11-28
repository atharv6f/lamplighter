"""The local adapter's health, its count before sending, and what it refuses."""

import asyncio
import dataclasses
import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from lamplighter.adapters.openai_chat import OpenAIChatModel, port_owner
from lamplighter.ports import ErrorEvent, Failure, Message, ModelRequest, TextPart
from tests.fake_clock import FakeClock
from tests.model_samples import TOOL
from tests.openai_chat.fake_server import (
    BASE_URL,
    COUNTED,
    MARKERS,
    OWNER_LINE,
    PORT,
    SERVER_DIR,
    answering,
    client_for,
    profile_for,
)
from tests.openai_chat.wire_text import DIALECTS, DONE, Dialect, chunk, model_of
from tests.streams import collect, count

REQUEST = ModelRequest(
    system=("Be brief.",),
    messages=(Message(role="user", parts=(TextPart(text="Hello."),)),),
    tools=(TOOL,),
    max_output=500,
    cache_hints=(),
    deadline_ms=5_000,
    retries=0,
)
REPLY = chunk({"content": "Hi."}) + chunk({}, "stop") + DONE


# The count before sending.


@pytest.mark.parametrize(
    ("dialect", "path", "field"),
    [
        ("llama_cpp", "/v1/chat/completions/input_tokens", "input_tokens"),
        ("vllm", "/tokenize", "count"),
    ],
)
def test_the_count_asks_the_servers_own_endpoint(
    dialect: Dialect, path: str, field: str
) -> None:
    seen: list[httpx.Request] = []

    async def serve(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=json.dumps({field: 31}))

    assert count(client_for(serve, dialect), REQUEST) == 31
    [asked] = seen
    assert asked.url.path == path
    sent = json.loads(asked.content)
    assert set(sent) == {"model", "messages", "tools"}
    assert sent["model"] == model_of(dialect)


def test_a_server_that_cannot_count_says_so_and_is_not_asked() -> None:
    serve, server = answering("mlx_lm")
    answer = count(client_for(serve, "mlx_lm"), REQUEST)
    assert isinstance(answer, Failure)
    assert answer.kind == "unavailable"
    assert server.requests == []


@pytest.mark.parametrize("dialect", ["llama_cpp", "vllm"])
def test_a_request_with_no_room_for_its_reply_is_not_sent(dialect: Dialect) -> None:
    serve, server = answering(dialect, httpx.Response(200, content=REPLY))
    tight = profile_for(dialect, window=COUNTED + REQUEST.max_output - 1)
    events = collect(client_for(serve, dialect, profile=tight), REQUEST)
    [end] = events
    assert isinstance(end, ErrorEvent)
    assert end.failure.kind == "too_large"
    assert end.failure.detail == {
        "counted": COUNTED,
        "max_output": REQUEST.max_output,
        "window": tight.window,
    }
    assert server.requests == []


@pytest.mark.parametrize("dialect", ["llama_cpp", "vllm"])
def test_a_request_that_just_fits_is_sent(dialect: Dialect) -> None:
    serve, server = answering(dialect, httpx.Response(200, content=REPLY))
    exact = profile_for(dialect, window=COUNTED + REQUEST.max_output)
    collect(client_for(serve, dialect, profile=exact), REQUEST)
    assert len(server.requests) == 1


def test_a_server_that_cannot_count_is_sent_the_request_whatever_its_size() -> None:
    serve, server = answering("mlx_lm", httpx.Response(200, content=REPLY))
    tight = profile_for("mlx_lm", window=REQUEST.max_output + 1)
    collect(client_for(serve, "mlx_lm", profile=tight), REQUEST)
    assert len(server.requests) == 1


def test_a_count_that_fails_ends_the_reply_and_sends_nothing() -> None:
    sent: list[str] = []

    async def serve(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        return httpx.Response(503, content=b"")

    [end] = collect(client_for(serve, "llama_cpp"), REQUEST)
    assert isinstance(end, ErrorEvent)
    assert end.failure.kind == "unavailable"
    assert sent == ["/v1/chat/completions/input_tokens"]


# Health.


def health_server(
    health: int = 200,
    models: bytes | None = None,
    model: str = "made-up-local",
) -> tuple[Callable[[httpx.Request], Any], list[str]]:
    asked: list[str] = []
    listed = models or json.dumps({"object": "list", "data": [{"id": model}]}).encode()

    async def serve(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(health, content=b'{"status":"ok"}')
        if request.url.path == "/v1/models":
            return httpx.Response(200, content=listed)
        return httpx.Response(404)

    return serve, asked


def health_of(client: OpenAIChatModel) -> Failure | None:
    return asyncio.run(client.health())


@pytest.mark.parametrize("dialect", DIALECTS)
def test_a_server_that_is_up_and_serves_the_model_is_healthy(dialect: Dialect) -> None:
    serve, asked = health_server(model=model_of(dialect))
    assert health_of(client_for(serve, dialect)) is None
    if dialect == "mlx_lm":
        assert asked == ["/health"]
    else:
        assert asked == ["/health", "/v1/models"]


@pytest.mark.parametrize("status", [503, 500, 401, 302])
def test_any_other_status_is_unavailable(status: int) -> None:
    serve, _ = health_server(health=status)
    answer = health_of(client_for(serve, "llama_cpp"))
    assert isinstance(answer, Failure)
    assert answer.kind == "unavailable"


def test_a_refused_connection_is_unavailable() -> None:
    async def serve(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("made-up refusal")

    answer = health_of(client_for(serve, "vllm"))
    assert isinstance(answer, Failure)
    assert answer.kind == "unavailable"


@pytest.mark.parametrize("dialect", ["llama_cpp", "vllm"])
def test_a_server_that_lists_another_model_answers_as_another_one(
    dialect: Dialect,
) -> None:
    serve, _ = health_server(model="some-other-model")
    answer = health_of(client_for(serve, dialect))
    assert isinstance(answer, Failure)
    assert answer.kind == "fatal"


def test_a_list_that_cannot_be_read_is_fatal() -> None:
    serve, _ = health_server(models=b"<html>a proxy</html>")
    answer = health_of(client_for(serve, "llama_cpp"))
    assert isinstance(answer, Failure)
    assert answer.kind == "fatal"


def test_an_mlx_server_with_an_empty_list_is_healthy() -> None:
    serve, _ = health_server(models=b"")
    assert health_of(client_for(serve, "mlx_lm")) is None


@pytest.mark.parametrize(
    "owner",
    [
        "/usr/bin/ssh -L 9931:127.0.0.1:9931 far-away",
        f"{SERVER_DIR}-other/bin/server --port 9931",
        f"{SERVER_DIR}/../other/bin/server --port 9931",
        "bin/server --port 9931",
        SERVER_DIR,
    ],
)
def test_a_port_held_by_a_program_outside_the_folder_is_fatal(owner: str) -> None:
    serve, _ = health_server()
    client = client_for(serve, "llama_cpp", port_owner=lambda port, seconds: owner)
    answer = health_of(client)
    assert isinstance(answer, Failure)
    assert answer.kind == "fatal"


@pytest.mark.parametrize("owner", [None, "", "   "])
def test_a_port_with_no_owner_found_is_unavailable_or_fatal(owner: str | None) -> None:
    serve, _ = health_server()
    client = client_for(serve, "llama_cpp", port_owner=lambda port, seconds: owner)
    answer = health_of(client)
    assert isinstance(answer, Failure)
    assert answer.kind == ("unavailable" if owner is None else "fatal")


def test_the_owner_is_asked_for_the_port_of_the_address_within_the_bound() -> None:
    asked: list[tuple[int, float]] = []

    def owner(port: int, seconds: float) -> str:
        asked.append((port, seconds))
        return OWNER_LINE

    serve, _ = health_server()
    assert health_of(client_for(serve, "llama_cpp", port_owner=owner)) is None
    [(port, seconds)] = asked
    assert port == PORT
    assert 0 < seconds <= 2.0


def test_a_server_that_never_answers_is_unavailable_within_two_seconds() -> None:
    async def serve(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    began = time.monotonic()
    answer = health_of(client_for(serve, "llama_cpp"))
    took = time.monotonic() - began
    assert isinstance(answer, Failure)
    assert answer.kind == "unavailable"
    assert 1.9 <= took < 3.0


def test_an_owner_that_hangs_is_unavailable_within_two_seconds() -> None:
    def owner(port: int, seconds: float) -> str:
        time.sleep(min(seconds + 0.5, 3.0))
        return OWNER_LINE

    serve, _ = health_server()
    began = time.monotonic()
    answer = health_of(client_for(serve, "llama_cpp", port_owner=owner))
    assert isinstance(answer, Failure)
    assert answer.kind == "unavailable"
    assert time.monotonic() - began < 3.0


# The client the adapter builds for itself.


# Each answer ends its connection, so no request finds one the server closed.
CLOSE = b"connection: close\r\n"


@pytest.fixture
def plain_server() -> Iterator[int]:
    """Serve 200 to /health and a redirect to every other path, on a real port."""

    async def answer(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        line = await reader.readline()
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        if b" /health " in line:
            reply = b"HTTP/1.1 200 OK\r\n" + CLOSE + b"content-length: 2\r\n\r\nok"
        else:
            moved = b"location: /health\r\ncontent-length: 0\r\n\r\n"
            reply = b"HTTP/1.1 302 Found\r\n" + CLOSE + moved
        writer.write(reply)
        await writer.drain()
        writer.close()

    loop = asyncio.new_event_loop()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = loop.run_until_complete(asyncio.start_server(answer, sock=listener))
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        loop.call_soon_threadsafe(server.close)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)


def real_client(port: int, kind: str) -> OpenAIChatModel:
    return OpenAIChatModel(
        profile_for("mlx_lm"),
        f"http://127.0.0.1:{port}",
        SERVER_DIR,
        lambda port, seconds: OWNER_LINE,
        FakeClock(),
        server_kind="mlx-lm" if kind == "mlx-lm" else "llama.cpp",
        count="none",
        call_markers=MARKERS,
    )


def test_no_proxy_from_the_environment_carries_a_call(
    plain_server: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A proxy that is not there: a client that read it would fail to connect.
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    assert health_of(real_client(plain_server, "mlx-lm")) is None


def test_a_redirect_is_not_followed(plain_server: int) -> None:
    # The list of models answers with a redirect to /health, which a client
    # that followed it would read as a list.
    answer = health_of(real_client(plain_server, "llama.cpp"))
    assert isinstance(answer, Failure)
    assert answer.kind == "unavailable"
    assert answer.detail["status"] == 302


# What the adapter refuses to be built with.


@pytest.mark.parametrize(
    "address",
    [
        "http://localhost:9931",
        "http://0.0.0.0:9931",
        "https://127.0.0.1:9931",
        "http://127.0.0.1:09931",
        "http://127.0.0.1:9931/v1",
        "http://127.0.0.1",
    ],
)
def test_an_address_that_might_leave_the_mac_is_refused(address: str) -> None:
    with pytest.raises(ValueError, match="might not stay on this Mac"):
        OpenAIChatModel(
            profile_for("llama_cpp"),
            address,
            SERVER_DIR,
            lambda port, seconds: None,
            FakeClock(),
            server_kind="llama.cpp",
            count="llama_cpp",
            call_markers=(),
        )


def test_an_address_with_one_slash_at_its_end_is_taken() -> None:
    OpenAIChatModel(
        profile_for("llama_cpp"),
        BASE_URL + "/",
        SERVER_DIR,
        lambda port, seconds: None,
        FakeClock(),
        server_kind="llama.cpp",
        count="llama_cpp",
        call_markers=(),
    )


def test_a_profile_of_another_adapter_is_refused() -> None:
    other = dataclasses.replace(profile_for("llama_cpp"), adapter="anthropic")
    with pytest.raises(ValueError, match="adapter"):
        OpenAIChatModel(
            other,
            BASE_URL,
            SERVER_DIR,
            lambda port, seconds: None,
            FakeClock(),
            server_kind="llama.cpp",
            count="llama_cpp",
            call_markers=(),
        )


# Finding the program on a port.


class _Ran:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_the_owner_is_found_by_lsof_then_ps(monkeypatch: pytest.MonkeyPatch) -> None:
    runs: list[tuple[list[str], float]] = []
    answers = iter([_Ran(0, "4321\n4322\n"), _Ran(0, f"{OWNER_LINE}\n")])

    def run(argv: list[str], **options: Any) -> _Ran:
        runs.append((argv, options["timeout"]))
        return next(answers)

    monkeypatch.setattr("subprocess.run", run)
    assert port_owner(9931, 1.0) == OWNER_LINE
    assert runs == [
        (["/usr/sbin/lsof", "-nP", "-iTCP@127.0.0.1:9931", "-sTCP:LISTEN", "-t"], 0.5),
        (["/bin/ps", "-o", "args=", "-p", "4321"], 0.5),
    ]


@pytest.mark.parametrize(
    "answers",
    [
        [_Ran(1, "")],
        [_Ran(0, "")],
        [_Ran(0, "not-a-number\n")],
        [_Ran(0, "4321\n"), _Ran(1, "")],
        [_Ran(0, "4321\n"), _Ran(0, "  \n")],
    ],
)
def test_no_owner_found_is_none(
    monkeypatch: pytest.MonkeyPatch, answers: list[_Ran]
) -> None:
    given = iter(answers)

    def run(argv: list[str], **options: Any) -> _Ran:
        return next(given)

    monkeypatch.setattr("subprocess.run", run)
    assert port_owner(9931, 1.0) is None


def test_a_slow_lsof_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(argv: list[str], **options: Any) -> _Ran:
        raise subprocess.TimeoutExpired(argv, options["timeout"])

    monkeypatch.setattr("subprocess.run", run)
    assert port_owner(9931, 1.0) is None


@pytest.mark.mac
def test_the_owner_of_a_real_port_is_this_python() -> None:
    assert sys.platform == "darwin"
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    try:
        owner = port_owner(listener.getsockname()[1], 2.0)
    finally:
        listener.close()
    assert owner is not None
    assert "python" in owner.split()[0]
