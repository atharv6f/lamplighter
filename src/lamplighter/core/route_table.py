"""The settings the router reads: each model by its name, and the table of routes."""

from dataclasses import dataclass
from typing import Literal

from lamplighter.core.cost import Prices
from lamplighter.ports import ModelProfile

type RouteWhen = Literal["private", "always"]


@dataclass(frozen=True, slots=True)
class NamedModel:
    """One model entry of the settings, as the core sees it.

    `name` is the entry's own name, which routes, the log and the bench use.
    `prices` is None for a model whose replies are not priced, such as one
    on this Mac. Such a reply is saved with no cost, never with a cost of 0.
    """

    name: str
    profile: ModelProfile
    prices: Prices | None


@dataclass(frozen=True, slots=True)
class Route:
    """One row of the rule table: when it holds, and its ladder of model names.

    `models` is the ladder, weakest first. A run starts as low on it as it
    can and only ever moves up.
    """

    id: str
    when: RouteWhen
    models: tuple[str, ...]
