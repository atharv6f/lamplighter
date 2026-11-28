"""A model whose reply is a function of the request, and that checks every request.

After a kill the same request is made again, and it must get the same reply,
so a script played in order will not do. The check on each request is the
one the API makes: every tool call has its result in the next message, no
result is without its call, results come before texts, and roles take turns.
"""

import random
from collections.abc import AsyncGenerator, Callable

from lamplighter.adapters import ScriptedBreak, ScriptedModel, ScriptedReply
from lamplighter.app.config import Config
from lamplighter.app.model_set import ModelSet
from lamplighter.ports import (
    Clock,
    Failure,
    Message,
    ModelClient,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    ToolCallPart,
    ToolResultPart,
)
from tests.model_samples import PROFILE

type Choose = Callable[[ModelRequest], ScriptedReply | ScriptedBreak]


class ModelByRequest:
    """Plays the reply that `choose` picks for each request. Satisfies ModelClient.

    With a clock and `seconds`, each call first waits that long on the clock,
    so a call takes time a test can see.
    """

    def __init__(
        self, choose: Choose, clock: Clock | None = None, seconds: float = 0.0
    ) -> None:
        self._choose = choose
        self._clock = clock
        self._seconds = seconds
        self.requests: list[ModelRequest] = []
        self.closed = 0

    @property
    def profile(self) -> ModelProfile:
        return PROFILE

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelEvent]:
        check_request(request)
        self.requests.append(request)
        try:
            if self._clock is not None and self._seconds:
                await self._clock.sleep(self._seconds)
            # A fresh generator each time, so the cuts in the text are the same
            # for the same reply, whatever came before it.
            played = ScriptedModel(PROFILE, [self._choose(request)], random.Random(0))
            async for event in played.stream(request):
                yield event
        finally:
            self.closed += 1

    async def count_tokens(self, request: ModelRequest) -> int | Failure:
        raise AssertionError("the loop never counts tokens")


def assistant_turns(request: ModelRequest) -> int:
    """Count the replies already in the request, which is how far the run is."""
    return sum(1 for message in request.messages if message.role == "assistant")


def check_request(request: ModelRequest) -> None:
    """Raise AssertionError for a request the real API would refuse."""
    messages = request.messages
    assert messages, "a request has at least one message"
    assert messages[0].role == "user", "the first message is the user's"
    assert messages[-1].role == "user", "the last message is the user's"
    for before, after in zip(messages, messages[1:], strict=False):
        assert before.role != after.role, "roles take turns"
    for place, message in enumerate(messages):
        if message.role == "user":
            _check_user_side(message)
            calls = _calls(messages[place - 1]) if place else ()
            results = [
                p.call_id for p in message.parts if isinstance(p, ToolResultPart)
            ]
            assert results == [call.id for call in calls], (
                f"message {place}: results {results} do not match the calls before"
            )


def _check_user_side(message: Message) -> None:
    seen_text = False
    for part in message.parts:
        if isinstance(part, ToolResultPart):
            assert not seen_text, "tool results come before texts"
        else:
            seen_text = True


def _calls(message: Message) -> tuple[ToolCallPart, ...]:
    return tuple(part for part in message.parts if isinstance(part, ToolCallPart))


def model_set(config: Config, client: ModelClient) -> ModelSet:
    """Give every model entry of the settings the one client, with no health checks."""

    async def close() -> None:
        pass

    return ModelSet({entry.name: client for entry in config.models}, {}, close)
