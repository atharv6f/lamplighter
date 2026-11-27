"""Sends spans to a Phoenix on this Mac, then asks until Phoenix shows it holds them.

Phoenix answers the send before it has stored anything, so a 200 alone
says nothing. The sink asks for the batch's span ids until each is found,
and only then says the batch is stored.
"""

import asyncio
import itertools
import json
from collections.abc import Awaitable, Sequence
from typing import cast

import httpx

from lamplighter.adapters.phoenix.local_address import local_address
from lamplighter.adapters.phoenix.otlp import encoded
from lamplighter.ports import Clock, Failure, FailureEffect, FailureKind, Span

_SOURCE = "TraceSink/phoenix"
_HEADERS = {"Content-Type": "application/x-protobuf"}
# Phoenix's answers that say "busy or not there right now". It answers 503
# when its queue is full.
_TRY_AGAIN = frozenset({429, 502, 503, 504})
REQUEST_LIMIT_S = 10.0
CONFIRM_LIMIT_S = 30.0
# The waits between two rounds of questions, in seconds. The last repeats.
_WAITS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.0)
# Ids in one question, and rows asked for, so every answer is one page.
_IDS_PER_QUESTION = 100


class PhoenixSink:
    """A trace store that is a Phoenix on this Mac. Satisfies TraceSink.

    An address that is not exactly `http://127.0.0.1:` and a port is never
    sent to: every export then answers `denied`. The client must take no
    proxy from the environment and follow no redirect.
    """

    def __init__(
        self,
        setting: str,
        project: str,
        http: httpx.AsyncClient,
        clock: Clock,
        request_limit_s: float = REQUEST_LIMIT_S,
    ) -> None:
        self._address = local_address(setting)
        self._project = project
        self._http = http
        self._clock = clock
        self._request_limit_s = request_limit_s

    @property
    def leaves_this_mac(self) -> bool:
        """Whether the setting was anything but the one address on this Mac."""
        return self._address is None

    async def export(self, spans: Sequence[Span]) -> Failure | None:
        """Send the batch, then ask until Phoenix shows every span of it."""
        if not spans:
            return None
        if self._address is None:
            return _failure(
                "denied",
                "none",
                "The trace store's address is not on this Mac. Nothing was sent.",
            )
        sent = await self._send(self._address, encoded(spans, self._project))
        if sent is not None:
            return sent
        ids = list(dict.fromkeys(span.span_id for span in spans))
        return await self._confirm(self._address, ids)

    async def _send(self, address: str, body: bytes) -> Failure | None:
        answer = await self._request(
            address,
            self._http.post(
                f"{address}/v1/traces",
                content=body,
                headers=_HEADERS,
                timeout=None,
                follow_redirects=False,
            ),
        )
        if isinstance(answer, Failure):
            return answer
        status = answer.status_code
        if status == 200:
            return None
        message = f"The trace store answered {status}."
        if status in _TRY_AGAIN:
            return _failure("unavailable", "unknown", message)
        if 400 <= status <= 499:
            return _failure("invalid_input", "none", message)
        return _failure("fatal", "unknown", message)

    async def _confirm(self, address: str, ids: list[str]) -> Failure | None:
        """Ask for the ids until none is missing or the time to confirm is over."""
        started = self._clock.monotonic()
        missing = ids
        # Each round waits, so the time to confirm runs out and ends the loop.
        for wait in itertools.chain(_WAITS, itertools.repeat(_WAITS[-1])):
            found: set[str] = set()
            for start in range(0, len(missing), _IDS_PER_QUESTION):
                if self._clock.monotonic() - started >= CONFIRM_LIMIT_S:
                    return self._not_confirmed(len(missing) - len(found))
                chunk = missing[start : start + _IDS_PER_QUESTION]
                answer = await self._ask(address, chunk)
                if isinstance(answer, Failure):
                    return answer
                found |= answer
            missing = [span_id for span_id in missing if span_id not in found]
            if not missing:
                return None
            await self._clock.sleep(wait)
        raise AssertionError("the waits never end")

    async def _ask(self, address: str, ids: Sequence[str]) -> set[str] | Failure:
        """Give the ids of the question that Phoenix holds."""
        params = {"span_id": list(ids), "limit": _IDS_PER_QUESTION}
        answer = await self._request(
            address,
            self._http.get(
                f"{address}/v1/projects/{self._project}/spans",
                params=params,
                timeout=None,
                follow_redirects=False,
            ),
        )
        if isinstance(answer, Failure):
            return answer
        # Phoenix makes a project when it stores its first span.
        if answer.status_code == 404:
            return set()
        if answer.status_code != 200:
            message = f"The trace store answered {answer.status_code}."
            return _failure("unavailable", "unknown", message)
        return _ids_in(answer.content)

    async def _request(
        self, address: str, call: Awaitable[httpx.Response]
    ) -> httpx.Response | Failure:
        # The library's own limits are per read, so one limit for the whole
        # request is waited here instead.
        try:
            return await asyncio.wait_for(call, self._request_limit_s)
        except TimeoutError:
            return _failure(
                "timeout", "unknown", "The trace store did not answer in time."
            )
        except httpx.RequestError:
            return _failure(
                "unavailable",
                "unknown",
                f"The trace store cannot be reached at {address}.",
            )

    def _not_confirmed(self, count: int) -> Failure:
        return _failure(
            "timeout",
            "unknown",
            f"The trace store took the batch but does not show {count} of its"
            f" spans in project {self._project}. If the trace store was just"
            " started again, this passes by itself. If it stays, the spans are"
            " stored under another project name: delete that project in Phoenix.",
        )


def _ids_in(body: bytes) -> set[str] | Failure:
    """Give the span id of each row of an answer, or a failure if it is not a list."""
    unreadable = _failure(
        "fatal", "unknown", "The trace store's list of spans cannot be read."
    )
    try:
        data: object = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return unreadable
    rows = _field(data, "data")
    if not isinstance(rows, list):
        return unreadable
    found: set[str] = set()
    for row in cast(list[object], rows):
        span_id = _field(_field(row, "context"), "span_id")
        if not isinstance(span_id, str):
            return unreadable
        found.add(span_id)
    return found


def _field(value: object, key: str) -> object:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value).get(key)


def _failure(kind: FailureKind, effect: FailureEffect, message: str) -> Failure:
    return Failure(kind=kind, effect=effect, message=message, source=_SOURCE)
