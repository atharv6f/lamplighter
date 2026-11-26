"""Builds each kind of model client the contract tests run against.

A test writes the turns it wants as scripted turns. Each kind here turns
that script into a client that will play it.
"""

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpx

from lamplighter.adapters import ScriptedBreak, ScriptedModel, ScriptedReply
from lamplighter.ports import ModelClient, ReasoningPart
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


KINDS = {
    "scripted": ClientKind(make=scripted, reasoning=REASONING),
    "anthropic": ClientKind(make=anthropic, reasoning=OWN_REASONING),
}
