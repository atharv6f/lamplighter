"""A made-up Anthropic server that answers from a script, with no network."""

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine

import httpx

from lamplighter.adapters.anthropic import AnthropicModel
from lamplighter.ports import ModelProfile
from tests.anthropic.wire_text import MODEL
from tests.fake_clock import FakeClock

KEY = "made-up-key-0000"
PROFILE = ModelProfile(
    adapter="anthropic",
    model=MODEL,
    max_output=2_000,
    tool_calls=True,
    cache_hints=4,
    count_error_permille=0,
)

type Answer = httpx.Response | Exception | Callable[[], Awaitable[httpx.Response]]
type Handler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


class Body(httpx.AsyncByteStream):
    """A response body that gives its pieces one by one and notes when it is closed."""

    def __init__(self, pieces: AsyncIterator[bytes]) -> None:
        self._pieces = pieces
        self.given = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for piece in self._pieces:
            self.given += 1
            yield piece

    async def aclose(self) -> None:
        self.closed = True


class FakeServer:
    """Gives the next scripted answer to each request and keeps every request."""

    def __init__(self, *answers: Answer) -> None:
        self._answers = iter(answers)
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = next(self._answers)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return await answer()


def client_for(
    server: Handler, clock: FakeClock | None = None, profile: ModelProfile = PROFILE
) -> AnthropicModel:
    """Give an Anthropic client whose calls all go to the made-up server."""
    http = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return AnthropicModel(profile, KEY, http, clock or FakeClock())


def reply(body: bytes) -> httpx.Response:
    """Write a 200 response with a stream body."""
    return httpx.Response(200, content=body)


def counted(tokens: int) -> httpx.Response:
    """Write the answer of the counting endpoint."""
    return httpx.Response(200, content=json.dumps({"input_tokens": tokens}))


def refusal(
    status: int,
    error_type: str = "made_up_error",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Write an error response in the shape the API documents."""
    problem = {"type": error_type, "message": "A made-up problem."}
    body = {"type": "error", "error": problem, "request_id": "req_made_up"}
    all_headers = {"request-id": "req_made_up", **(headers or {})}
    return httpx.Response(status, headers=all_headers, content=json.dumps(body))
