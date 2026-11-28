"""The model client that calls a server on this Mac in the chat completions format."""

import asyncio
import contextlib
import os
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

import httpx

from lamplighter.adapters.loopback_address import local_address
from lamplighter.adapters.openai_chat.errors import (
    failure,
    failure_for_response,
    may_retry,
)
from lamplighter.adapters.openai_chat.parts import (
    ADAPTER,
    prompt,
    render_request,
    to_bytes,
)
from lamplighter.adapters.openai_chat.stream import ReplyAssembler
from lamplighter.adapters.sse import SseReader
from lamplighter.adapters.wire_json import (
    Unreadable,
    as_object,
    list_or_empty,
    parse_object,
    whole_in,
)
from lamplighter.ports import (
    Clock,
    ErrorEvent,
    Failure,
    JsonValue,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    StopEvent,
    UsageEvent,
    check_request,
)

type ServerKind = Literal["llama.cpp", "vllm-metal", "mlx-lm"]
type CountKind = Literal["llama_cpp", "vllm", "none"]
# Gives the command line of the program listening on 127.0.0.1 at a port,
# or None, within the seconds given.
type PortOwner = Callable[[int, float], str | None]

_MESSAGES_PATH = "/v1/chat/completions"
# Each server's exact count of a chat request, with its template applied,
# and the field the count comes back in.
_COUNT_CALLS: dict[str, tuple[str, str]] = {
    "llama_cpp": ("/v1/chat/completions/input_tokens", "input_tokens"),
    "vllm": ("/tokenize", "count"),
}
# The waits Anthropic's own client libraries use between attempts, in seconds.
_FIRST_WAIT = 0.5
_LONGEST_WAIT = 8.0
# The HealthCheck port's bound on one answer.
_HEALTH_SECONDS = 2.0
# What can go wrong on the network: the deadline comes, or the library reports
# a fault. Both are expected, so both become failure values.
_NETWORK_PROBLEMS = (TimeoutError, httpx.RequestError)


@dataclass(frozen=True, slots=True)
class _Refused:
    """An attempt that failed before it gave any content.

    Nothing of it has reached the caller, so another attempt may follow. If
    none does, `usage` and `error` end the stream.
    """

    usage: UsageEvent | None
    error: ErrorEvent
    retry: bool


class OpenAIChatModel:
    """One model on a chat completions server on this Mac.

    Satisfies ModelClient and HealthCheck. It builds its own HTTP client,
    which reads no proxy from the environment and follows no redirect, so no
    call can be carried off this Mac.
    """

    def __init__(
        self,
        profile: ModelProfile,
        base_url: str,
        server_dir: str,
        port_owner: PortOwner,
        clock: Clock,
        *,
        server_kind: ServerKind,
        count: CountKind,
        call_markers: tuple[str, ...],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Take the profile, the server's address and folder, and how to find its owner.

        `server_dir` is the folder the server's program is installed in.
        `count` names the server's endpoint for an exact count of a request.
        `call_markers` are texts only a tool call in the model's own markup
        holds. `transport` is for tests only, where it is a made-up server.
        Raises ValueError for a profile of another adapter and for an
        address that might leave this Mac.
        """
        if profile.adapter != ADAPTER:
            # This adapter puts its own name on reasoning parts and failures.
            # A profile under another name would disagree with them.
            raise ValueError(f"this profile is for the adapter {profile.adapter!r}")
        base = local_address(base_url)
        if base is None:
            raise ValueError(f"the address {base_url!r} might not stay on this Mac")
        self._profile = profile
        self._base = base
        self._port = int(base.rsplit(":", 1)[1])
        self._server_dir = os.path.normpath(os.path.expanduser(server_dir))
        self._port_owner = port_owner
        self._clock = clock
        self._server_kind = server_kind
        self._count = count
        self._markers = call_markers
        self._headers = {"content-type": "application/json"}
        self._http = httpx.AsyncClient(
            trust_env=False, follow_redirects=False, transport=transport
        )

    @property
    def profile(self) -> ModelProfile:
        """The profile given when this client was built."""
        return self._profile

    async def aclose(self) -> None:
        """Close the HTTP client this adapter built."""
        await self._http.aclose()

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelEvent]:
        """Send the request and yield the reply as events.

        A request whose count plus `max_output` is above the window is not
        sent: the reply is one `too_large` error.
        """
        problem = check_request(self._profile, request)
        if problem is not None:
            yield ErrorEvent(failure=problem, model_reported=None, skipped=0)
            return
        deadline = self._clock.monotonic() + request.deadline_ms / 1000
        # Cache hints have no field in this format, so they are not sent.
        if self._count != "none":
            room = await self._room(request, request.retries, deadline)
            if room is not None:
                yield ErrorEvent(failure=room, model_reported=None, skipped=0)
                return
        body = render_request(self._profile.model, request)
        has_tools = bool(request.tools)
        attempt = 0
        while True:
            refused = None
            # When the caller closes this stream part way, the attempt must be
            # closed with it. Left alone, its connection would stay open until
            # the garbage collector found it.
            attempt_events = self._attempt(body, has_tools, deadline)
            async with contextlib.aclosing(attempt_events) as events:
                async for event in events:
                    if isinstance(event, _Refused):
                        refused = event
                    else:
                        yield event
            if refused is None:
                return
            wait = self._wait_before_retry(refused, attempt, request.retries, deadline)
            if wait is None:
                if refused.usage is not None:
                    yield refused.usage
                yield refused.error
                return
            await self._clock.sleep(wait)
            attempt += 1

    async def count_tokens(self, request: ModelRequest) -> int | Failure:
        """Ask the server for its exact count of the prompt.

        A server with no count endpoint gives `unavailable`.
        """
        problem = check_request(self._profile, request)
        if problem is not None:
            return problem
        if self._count == "none":
            return failure("unavailable", {"count": "this server cannot count"})
        deadline = self._clock.monotonic() + request.deadline_ms / 1000
        return await self._counted(request, request.retries, deadline)

    async def health(self) -> Failure | None:
        """Say whether the server is up, serves this model, and runs from its folder."""
        try:
            return await asyncio.wait_for(self._health_checks(), _HEALTH_SECONDS)
        except TimeoutError:
            return failure("unavailable", {"health": "no answer within two seconds"})

    async def _room(
        self, request: ModelRequest, retries: int, deadline: float
    ) -> Failure | None:
        """Give a Failure when the request leaves no room for its reply, or None."""
        counted = await self._counted(request, retries, deadline)
        if isinstance(counted, Failure):
            return counted
        if counted + request.max_output <= self._profile.window:
            return None
        detail: dict[str, JsonValue] = {
            "counted": counted,
            "max_output": request.max_output,
            "window": self._profile.window,
        }
        return failure("too_large", detail)

    async def _counted(
        self, request: ModelRequest, retries: int, deadline: float
    ) -> int | Failure:
        """Count the request by the server's endpoint, with the usual retries."""
        path, field = _COUNT_CALLS[self._count]
        body = to_bytes(prompt(self._profile.model, request))
        attempt = 0
        while True:
            answer = await self._count_once(path, field, body, deadline)
            if isinstance(answer, int):
                return answer
            wait = self._wait_before_retry(answer, attempt, retries, deadline)
            if wait is None:
                return answer.error.failure
            await self._clock.sleep(wait)
            attempt += 1

    async def _health_checks(self) -> Failure | None:
        started = self._clock.monotonic()
        answer = await self._get("/health")
        if isinstance(answer, Failure):
            return answer
        if self._server_kind != "mlx-lm":
            # mlx_lm.server lists a folder and not the name it serves, and
            # sends an empty body when it has no Hugging Face cache folder.
            listed = await self._get("/v1/models")
            if isinstance(listed, Failure):
                return listed
            wrong = self._wrong_list(listed)
            if wrong is not None:
                return wrong
        left = _HEALTH_SECONDS - (self._clock.monotonic() - started)
        owner = await asyncio.to_thread(self._port_owner, self._port, max(left, 0.0))
        return self._wrong_owner(owner)

    async def _get(self, path: str) -> bytes | Failure:
        """Give the body of a GET that answered 200, or `unavailable`."""
        try:
            response = await self._http.get(self._base + path, timeout=None)
        except httpx.RequestError as problem:
            detail: dict[str, JsonValue] = {"transport_message": str(problem)}
            return failure("unavailable", {"path": path, **detail})
        if response.status_code != 200:
            status = response.status_code
            return failure("unavailable", {"path": path, "status": status})
        return response.content

    def _wrong_list(self, body: bytes) -> Failure | None:
        """Give `fatal` when the server's list of models lacks this model."""
        try:
            answer = parse_object(body.decode("utf-8", "replace"))
            names = {
                as_object(entry).get("id") for entry in list_or_empty(answer["data"])
            }
        except (Unreadable, KeyError) as problem:
            detail = f"the list is unreadable: {problem}"
            return failure("fatal", {"models": detail})
        if self._profile.model in names:
            return None
        detail = f"the server lists no {self._profile.model}"
        return failure("fatal", {"models": detail})

    def _wrong_owner(self, owner: str | None) -> Failure | None:
        """Give a Failure unless the program on the port runs from the server folder."""
        if owner is None:
            detail = "no program could be found listening on the port"
            return failure("unavailable", {"port_owner": detail})
        program = os.path.normpath(owner.split(maxsplit=1)[0]) if owner.strip() else ""
        if os.path.isabs(program) and program.startswith(self._server_dir + os.sep):
            return None
        # A tunnel or a proxy on the port would answer as the server does.
        return failure("fatal", {"port_owner": f"the port is held by {program!r}"})

    def _wait_before_retry(
        self, refused: _Refused, attempt: int, retries: int, deadline: float
    ) -> float | None:
        """Give the seconds to wait before the next attempt, or None for no attempt."""
        if not refused.retry or attempt >= retries:
            return None
        asked = refused.error.failure.retry_after_ms
        if asked is None:
            wait = min(_FIRST_WAIT * 2**attempt, _LONGEST_WAIT)
        else:
            wait = asked / 1000
        # An attempt that would start at or after the deadline is not made.
        # This is also why a call that ran out of time is never tried again.
        return wait if self._clock.monotonic() + wait < deadline else None

    async def _attempt(
        self, body: bytes, has_tools: bool, deadline: float
    ) -> AsyncGenerator[ModelEvent | _Refused]:
        """Make one HTTP call and yield its events, or one _Refused and nothing else."""
        response = await self._open(self._base + _MESSAGES_PATH, body, deadline)
        if isinstance(response, _Refused):
            yield response
            return
        reader = SseReader()
        assembler = ReplyAssembler(body, has_tools, self._markers)
        chunks = response.aiter_bytes()
        usage: UsageEvent | None = None
        gave_content = False
        try:
            while not assembler.finished:
                for event in await self._read(chunks, reader, assembler, deadline):
                    if isinstance(event, UsageEvent):
                        # Held back until the last event shows who gets it.
                        usage = event
                    elif isinstance(event, ErrorEvent) and not gave_content:
                        retry = may_retry(event.failure)
                        yield _Refused(usage=usage, error=event, retry=retry)
                    elif isinstance(event, (StopEvent, ErrorEvent)):
                        if usage is not None:
                            yield usage
                        yield event
                    else:
                        gave_content = True
                        yield event
        finally:
            await response.aclose()

    async def _open(
        self, url: str, body: bytes, deadline: float
    ) -> httpx.Response | _Refused:
        """Send one request. Give the response if its status is 200, still open."""
        # The deadline is the only time limit. The library's own limits are per
        # read, and a model may think for a long time between two reads.
        request = self._http.build_request(
            "POST", url, content=body, headers=self._headers, timeout=None
        )
        try:
            response = await self._in_time(
                self._http.send(request, stream=True), deadline
            )
        except _NETWORK_PROBLEMS as problem:
            return _refused(_network_failure(problem))
        if response.status_code == 200:
            return response
        try:
            text = await self._in_time(response.aread(), deadline)
        except _NETWORK_PROBLEMS:
            # The status alone still says what kind of failure this is.
            text = b""
        finally:
            await response.aclose()
        return _refused(failure_for_response(response.status_code, text))

    async def _count_once(
        self, path: str, field: str, body: bytes, deadline: float
    ) -> int | _Refused:
        """Make one counting call and give the count."""
        response = await self._open(self._base + path, body, deadline)
        if isinstance(response, _Refused):
            return response
        try:
            text = await self._in_time(response.aread(), deadline)
        except _NETWORK_PROBLEMS as problem:
            return _refused(_network_failure(problem))
        finally:
            await response.aclose()
        try:
            return whole_in(parse_object(text.decode("utf-8", errors="replace")), field)
        except Unreadable as problem:
            return _refused(failure("transient", {"unreadable": str(problem)}))

    async def _read(
        self,
        chunks: AsyncIterator[bytes],
        reader: SseReader,
        assembler: ReplyAssembler,
        deadline: float,
    ) -> list[ModelEvent]:
        """Read the next bytes of the body and give the model events they complete."""
        try:
            chunk = await self._in_time(anext(chunks, None), deadline)
        except _NETWORK_PROBLEMS as problem:
            return assembler.fail(_network_failure(problem))
        if chunk is None:
            return assembler.end_of_stream()
        return [
            event
            for wire_event in reader.feed(chunk)
            for event in assembler.feed(wire_event)
        ]

    async def _in_time[T](self, step: Awaitable[T], deadline: float) -> T:
        """Wait for one network step. Raise TimeoutError once the deadline has come."""
        # The time left is read from the clock that was passed in, which keeps
        # counting while the Mac sleeps. The wait itself is the event loop's,
        # and its timer stops while the Mac sleeps. So a step that is waiting
        # when the lid is shut can overrun by the time it had left. The step
        # after it reads the clock again and stops at once.
        return await asyncio.wait_for(step, deadline - self._clock.monotonic())


def _refused(why: Failure) -> _Refused:
    error = ErrorEvent(failure=why, model_reported=None, skipped=0)
    return _Refused(usage=None, error=error, retry=may_retry(why))


def _network_failure(problem: TimeoutError | httpx.RequestError) -> Failure:
    if isinstance(problem, TimeoutError):
        return failure("timeout", {"reason": "the deadline of the request passed"})
    detail: dict[str, JsonValue] = {"transport_message": str(problem)}
    if isinstance(problem, httpx.ConnectError):
        return failure("unavailable", detail)
    # The connection was made and then dropped.
    return failure("transient", detail)
