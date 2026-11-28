"""Settings with a ladder of two models, and health checks a test can steer."""

import asyncio
import dataclasses

from lamplighter.core import NamedModel, Route
from lamplighter.core.run_spec import RunSpec
from lamplighter.ports import Failure
from tests.model_samples import PROFILE
from tests.run_history import PRICES, SPEC

LOCAL = "local"
FRONTIER = "frontier"
# A model on this Mac with a smaller window than the frontier one.
LOCAL_PROFILE = dataclasses.replace(
    PROFILE,
    adapter="openai_chat",
    model="made-up-local",
    window=65_536,
    stays_on_mac=True,
)
FRONTIER_PROFILE = PROFILE
PRIVATE_ROUTE = Route("private", "private", (LOCAL,))
DEFAULT_ROUTE = Route("default", "always", (LOCAL, FRONTIER))
LADDER = dataclasses.replace(
    SPEC,
    models=(
        NamedModel(LOCAL, LOCAL_PROFILE, PRICES),
        NamedModel(FRONTIER, FRONTIER_PROFILE, PRICES),
    ),
    routes=(PRIVATE_ROUTE, DEFAULT_ROUTE),
)
DOWN = Failure(kind="unavailable", effect="none", message="Refused.", source="test")
HELD = Failure(kind="fatal", effect="none", message="Another program.", source="test")


def private(spec: RunSpec = LADDER) -> RunSpec:
    """Give the settings for a run started as private."""
    return dataclasses.replace(spec, private=True)


class SteeredHealth:
    """A health check whose answer the test sets. Satisfies HealthCheck.

    With `hang`, it does not answer until it is cancelled, and says so.
    """

    def __init__(self, answer: Failure | None = None, hang: bool = False) -> None:
        self.answer = answer
        self.hang = hang
        self.asked = 0
        self.cancelled = 0

    async def health(self) -> Failure | None:
        self.asked += 1
        if self.hang:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        return self.answer
