"""The model client that calls Anthropic's Messages API over HTTP."""

import asyncio
import contextlib
import dataclasses
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable
from dataclasses import dataclass

import httpx

from lamplighter.adapters.anthropic.blocks import ADAPTER
from lamplighter.adapters.anthropic.errors import (
    failure,
    failure_for_response,
    may_retry,
)
from lamplighter.adapters.anthropic.request import (
    render_count_request,
    render_request,
)
from lamplighter.adapters.anthropic.sse import SseReader
from lamplighter.adapters.anthropic.stream import ReplyAssembler
from lamplighter.adapters.anthropic.wire import Unreadable, parse_object, whole_in
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

_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_COUNT_URL = "https://api.anthropic.com/v1/messages/count_tokens"
# The request writer and the stream reader are written for this version.
_API_VERSION = "2026-10-21"
# The waits Anthropic's own client libraries use between attempts, in seconds.
_FIRST_WAIT = 0.5
_LONGEST_WAIT = 8.0
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


class AnthropicModel:
    """One Claude model on Anthropic's API. Satisfies ModelClient."""

    def __init__(
        self,
        profile: ModelProfile,
        api_key: str,
        http: httpx.AsyncClient,
        clock: Clock,
    ) -> None:
        """Take the profile to report, the account's key, and what to call with."""
        if profile.adapter != ADAPTER:
            # This adapter puts its own name on reasoning parts and failures.
            # A profile under another name would disagree with them.
            raise ValueError(f"this profile is for the adapter {profile.adapter!r}")
        self._profile = profile
        self._headers = {
            "x-api-key": api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }
        self._http = http
        self._clock = clock

    @property
    def profile(self) -> ModelProfile:
        """The profile given when this client was built."""
        return self._profile

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelEvent]:
        """Send the request and yield the reply as events."""
        problem = check_request(self._profile, request)
        if problem is not None:
            yield ErrorEvent(failure=problem, model_reported=None, skipped=0)
            return
        deadline = self._clock.monotonic() + request.deadline_ms / 1000
        body = render_request(self._profile.model, self._as_sent(request))
        attempt = 0
        while True:
            refused = None
            # When the caller closes this stream part way, the attempt must be
            # closed with it. Left alone, its connection would stay open until
            # the garbage collector found it.
            async with contextlib.aclosing(self._attempt(body, deadline)) as events:
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
        """Ask the API to count the prompt. The count is the API's own estimate."""
        deadline = self._clock.monotonic() + request.deadline_ms / 1000
        body = render_count_request(self._profile.model, self._as_sent(request))
        attempt = 0
        while True:
            answer = await self._count_once(body, deadline)
            if isinstance(answer, int):
                return answer
            wait = self._wait_before_retry(answer, attempt, request.retries, deadline)
            if wait is None:
                return answer.error.failure
            await self._clock.sleep(wait)
            attempt += 1

    def _as_sent(self, request: ModelRequest) -> ModelRequest:
        """Give the request, with no cache hints if the profile honours none."""
        if self._profile.cache_hints > 0:
            return request
        # The check before sending did not look at these hints, so they may
        # point at nothing.
        return dataclasses.replace(request, cache_hints=())

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
        self, body: bytes, deadline: float
    ) -> AsyncGenerator[ModelEvent | _Refused]:
        """Make one HTTP call and yield its events, or one _Refused and nothing else."""
        response = await self._open(_MESSAGES_URL, body, deadline)
        if isinstance(response, _Refused):
            yield response
            return
        reader, assembler = SseReader(), ReplyAssembler()
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
                        retry = may_retry(event.failure, None)
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
        why = failure_for_response(response.status_code, response.headers, text)
        error = ErrorEvent(failure=why, model_reported=None, skipped=0)
        retry = may_retry(why, response.headers.get("x-should-retry"))
        return _Refused(usage=None, error=error, retry=retry)

    async def _count_once(self, body: bytes, deadline: float) -> int | _Refused:
        """Make one counting call and give the count."""
        response = await self._open(_COUNT_URL, body, deadline)
        if isinstance(response, _Refused):
            return response
        try:
            text = await self._in_time(response.aread(), deadline)
        except _NETWORK_PROBLEMS as problem:
            return _refused(_network_failure(problem))
        finally:
            await response.aclose()
        try:
            answer = parse_object(text.decode("utf-8", errors="replace"))
            return whole_in(answer, "input_tokens")
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
        # The time left is read from the clock that was passed in. That clock
        # keeps counting while the Mac sleeps, and a test can move it.
        return await asyncio.wait_for(step, deadline - self._clock.monotonic())


def _refused(why: Failure) -> _Refused:
    error = ErrorEvent(failure=why, model_reported=None, skipped=0)
    return _Refused(usage=None, error=error, retry=may_retry(why, None))


def _network_failure(problem: TimeoutError | httpx.RequestError) -> Failure:
    if isinstance(problem, TimeoutError):
        return failure("timeout", {"reason": "the deadline of the request passed"})
    detail: dict[str, JsonValue] = {"transport_message": str(problem)}
    if isinstance(problem, httpx.ConnectError):
        return failure("unavailable", detail)
    # The connection was made and then dropped.
    return failure("transient", detail)
