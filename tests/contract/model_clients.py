"""Builds each kind of model client the contract tests run against.

A test writes the turns it wants as scripted turns. Each kind here turns
that script into a client that will play it.
"""

import random
import tempfile
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from lamplighter.adapters import (
    RecordingModel,
    ReplayedModel,
    ScriptedBreak,
    ScriptedModel,
    ScriptedReply,
)
from lamplighter.ports import (
    Failure,
    ModelClient,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    ReasoningPart,
)
from tests.anthropic.fake_server import FakeServer, client_for, counted, refusal, reply
from tests.anthropic.wire_text import OWN_REASONING, stream_body
from tests.model_samples import PROFILE, REASONING

type Turn = ScriptedReply | ScriptedBreak


@dataclass(frozen=True)
class ClientKind:
    """One kind of model client.

    `reasoning` is a reasoning part as this kind gives it. Each kind seals its
    reasoning in its own way, so a script cannot use one part for all of them.
    """

    make: Callable[[Sequence[Turn]], ModelClient]
    reasoning: ReasoningPart


def scripted(turns: Sequence[Turn]) -> ModelClient:
    """Give the scripted model itself, with cuts that differ on every run."""
    return ScriptedModel(PROFILE, turns, random.Random())


# The status a made-up Anthropic server gives for a call that fails at once.
_STATUS = {"invalid_input": 400, "needs_user": 401, "throttled": 429, "transient": 529}


def anthropic(turns: Sequence[Turn]) -> ModelClient:
    """Give the Anthropic client, talking to a made-up server that plays the turns."""
    played = FakeServer(*[_answer(turn) for turn in turns])

    async def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/count_tokens"):
            return counted(12)
        return await played(request)

    return client_for(serve)


def _answer(turn: Turn) -> httpx.Response:
    if isinstance(turn, ScriptedBreak) and not turn.parts:
        return refusal(_STATUS[turn.failure.kind])
    return reply(stream_body(turn))


class _ThroughARecording:
    """Makes each call on a client, saves it, and answers from the saved file.

    A test cannot record ahead of time, because it has not made its calls
    yet. So each call is recorded to its own file and then played back, and
    what the test sees has been through the file and out again.
    """

    def __init__(self, inner: ModelClient) -> None:
        self._inner = inner
        self._folder = tempfile.TemporaryDirectory()
        self._files = 0

    @property
    def profile(self) -> ModelProfile:
        return self._inner.profile

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelEvent]:
        path = self._next_path()
        async for _ in RecordingModel(self._inner, path).stream(request):
            pass
        async for event in ReplayedModel(path).stream(request):
            yield event

    async def count_tokens(self, request: ModelRequest) -> int | Failure:
        path = self._next_path()
        await RecordingModel(self._inner, path).count_tokens(request)
        return await ReplayedModel(path).count_tokens(request)

    def _next_path(self) -> Path:
        self._files += 1
        return Path(self._folder.name) / f"call-{self._files}.jsonl"


def replayed(turns: Sequence[Turn]) -> ModelClient:
    """Give a client that answers from recordings of the scripted model."""
    return _ThroughARecording(scripted(turns))


KINDS = {
    "scripted": ClientKind(make=scripted, reasoning=REASONING),
    "anthropic": ClientKind(make=anthropic, reasoning=OWN_REASONING),
    "replayed": ClientKind(make=replayed, reasoning=REASONING),
}
