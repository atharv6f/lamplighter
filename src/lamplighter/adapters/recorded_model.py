"""Saves the calls made to a model client, and plays a saved file back.

A recording is a text file with one JSON value on each line. The first line
holds the format version and the profile of the client that was recorded.
Each line after it holds one call: the request, and what came back.
"""

import contextlib
import json
from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from lamplighter.ports import (
    ErrorEvent,
    Failure,
    JsonValue,
    ModelClient,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    from_plain,
    to_plain,
)

_VERSION = 2


@dataclass(frozen=True, slots=True)
class _Header:
    v: int
    profile: ModelProfile


@dataclass(frozen=True, slots=True)
class _SavedStream:
    request: ModelRequest
    events: tuple[ModelEvent, ...]
    kind: Literal["stream"] = "stream"


@dataclass(frozen=True, slots=True)
class _SavedCount:
    request: ModelRequest
    tokens: int | None
    failure: Failure | None
    kind: Literal["count"] = "count"


type _Saved = _SavedStream | _SavedCount


class RecordingModel:
    """Passes every call to the client it wraps and saves it. Satisfies ModelClient.

    A recording of a real model holds real messages. It must be written to a
    folder that git ignores.
    """

    def __init__(self, inner: ModelClient, path: Path) -> None:
        """Start a new recording at `path`. A file already there is replaced."""
        self._inner = inner
        self._path = path
        header = _Header(v=_VERSION, profile=inner.profile)
        path.write_text(json.dumps(to_plain(header)) + "\n")

    @property
    def profile(self) -> ModelProfile:
        """The profile of the client being recorded."""
        return self._inner.profile

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelEvent]:
        """Yield what the wrapped client yields, and save it when the call ends."""
        events: list[ModelEvent] = []
        try:
            # Closing this stream part way has to close the wrapped one too.
            async with contextlib.aclosing(self._inner.stream(request)) as inner:
                async for event in inner:
                    events.append(event)
                    yield event
        finally:
            # Also reached when the caller stops early, so the file holds
            # exactly what the caller was given.
            self._save(_SavedStream(request=request, events=tuple(events)))

    async def count_tokens(self, request: ModelRequest) -> int | Failure:
        """Give the wrapped client's answer, and save it."""
        answer = await self._inner.count_tokens(request)
        if isinstance(answer, Failure):
            self._save(_SavedCount(request=request, tokens=None, failure=answer))
        else:
            self._save(_SavedCount(request=request, tokens=answer, failure=None))
        return answer

    def _save(self, call: _Saved) -> None:
        # One line for each call, added as the call ends. A run that is killed
        # between two calls leaves every earlier call in the file.
        with self._path.open("a") as file:
            file.write(json.dumps(to_plain(call)) + "\n")


class ReplayedModel:
    """Answers each call from a recording, in the order saved. Satisfies ModelClient.

    A call that is not the next one in the recording ends with a failure of
    kind "fatal". The code under test has then changed what it asks, and no
    saved answer is true for it.
    """

    def __init__(self, path: Path) -> None:
        """Read the recording at `path`.

        Raises ValueError if any line is not as a recording saves it. A run
        killed in the middle of writing a line leaves such a line.
        """
        first, *rest = path.read_text().splitlines()
        plain: JsonValue = json.loads(first)
        # The version is read before the rest, because another version's
        # profile may have other fields.
        version = plain.get("v") if isinstance(plain, dict) else None
        if version != _VERSION:
            raise ValueError(f"this recording has version {version}, not {_VERSION}")
        header = from_plain(_Header, plain)
        self._profile = header.profile
        self._calls = iter([_read_call(line) for line in rest])

    @property
    def profile(self) -> ModelProfile:
        """The profile of the client that was recorded."""
        return self._profile

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelEvent]:
        """Yield the saved events of the next call, if it is this request."""
        saved = next(self._calls, None)
        if not isinstance(saved, _SavedStream) or not _same(saved.request, request):
            yield ErrorEvent(failure=_not_next(), model_reported=None, skipped=0)
            return
        for event in saved.events:
            yield event

    async def count_tokens(self, request: ModelRequest) -> int | Failure:
        """Give the saved answer of the next call, if it is this request."""
        saved = next(self._calls, None)
        if not isinstance(saved, _SavedCount) or not _same(saved.request, request):
            return _not_next()
        if saved.tokens is None:
            return saved.failure or _not_next()
        return saved.tokens


def _read_call(line: str) -> _Saved:
    data: JsonValue = json.loads(line)
    kind = data.get("kind") if isinstance(data, Mapping) else None
    if kind == "stream":
        return from_plain(_SavedStream, data)
    if kind == "count":
        return from_plain(_SavedCount, data)
    raise ValueError("a line of this recording is not a saved call")


def _same(saved: ModelRequest, asked: ModelRequest) -> bool:
    # Compared in their JSON form. A request built in code may hold a tuple
    # where the saved one, read back from JSON, holds a list.
    return to_plain(saved) == to_plain(asked)


def _not_next() -> Failure:
    return Failure(
        kind="fatal",
        effect="none",
        message="This call is not the next one in the recording.",
        source="ModelClient/replay",
    )
