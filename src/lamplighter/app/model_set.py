"""The model clients of one settings file, by name, and how to close them."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from lamplighter.ports import HealthCheck, ModelClient


@dataclass(frozen=True)
class ModelSet:
    """A client for each model entry, and the health checks of those on this Mac.

    `close` closes every HTTP client the set holds open.
    """

    clients: Mapping[str, ModelClient]
    health: Mapping[str, HealthCheck]
    close: Callable[[], Awaitable[None]]
