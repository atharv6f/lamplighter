"""Which model a run uses, from the rule table and the run's facts. Pure."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from lamplighter.core.route_table import Route
from lamplighter.ports import ModelProfile

type Signal = Literal[
    "invalid_calls",
    "repeated_call",
    "empty_reply",
    "refused",
    "model_failed",
    "too_large",
]
type ChoiceReason = Literal["start", "skipped"] | Signal


@dataclass(frozen=True, slots=True)
class ModelCall:
    """What the next call of a run needs from a model.

    `size` is the session's last known prompt size in tokens, a lower bound
    on the next prompt, or None in a new session. `max_output` is the most
    output the run asks for in one call.
    """

    private: bool
    size: int | None
    max_output: int


@dataclass(frozen=True, slots=True)
class RouteState:
    """Where a run is on its ladder.

    `position` is the place of the run's last choice on the ladder as the
    settings write it, or None before its first. `signal` is the one standing
    since that choice, or None. `health` says for each name whether it is up;
    a name with no answer counts as up.
    """

    position: int | None
    signal: Signal | None
    health: Mapping[str, bool]


@dataclass(frozen=True, slots=True)
class Choice:
    """Use this model from now on."""

    route_id: str
    model: str
    position: int
    reason: ChoiceReason


@dataclass(frozen=True, slots=True)
class Keep:
    """Use the model of the last choice, and write nothing."""


@dataclass(frozen=True, slots=True)
class NoModel:
    """No model the rules allow is up. `ladder` holds the names left after the veto."""

    route_id: str
    ladder: tuple[str, ...]


def route_for(routes: Sequence[Route], private: bool) -> Route:
    """Give the first route whose `when` holds. The last one always holds."""
    return next(
        r for r in routes if r.when == "always" or (private and r.when == "private")
    )


def allowed(
    route: Route, models: Mapping[str, ModelProfile], private: bool
) -> tuple[tuple[int, str], ...]:
    """Give the route's ladder after the veto, each name with its place as written.

    The veto: for a private call, every name whose calls leave this Mac is
    struck. It reads no setting but the profiles.
    """
    return tuple(
        (place, name)
        for place, name in enumerate(route.models)
        if not private or models[name].stays_on_mac
    )


def choose(
    routes: Sequence[Route],
    models: Mapping[str, ModelProfile],
    call: ModelCall,
    state: RouteState,
) -> Choice | Keep | NoModel:
    """Give the model a run uses next.

    A run starts on the first name that fits and moves only up, one choice
    at a time, and only when a signal stands.
    """
    route = route_for(routes, call.private)
    ladder = allowed(route, models, call.private)
    if state.position is None:
        for place, name in ladder:
            if _fits(models[name], name, call, state):
                reason = "start" if place == ladder[0][0] else "skipped"
                return Choice(route.id, name, place, reason)
        return NoModel(route.id, tuple(name for _, name in ladder))
    if state.signal is None:
        return Keep()
    current = models[route.models[state.position]].window
    for place, name in ladder:
        if place <= state.position or not _fits(models[name], name, call, state):
            continue
        if state.signal == "too_large" and models[name].window <= current:
            continue
        return Choice(route.id, name, place, state.signal)
    return Keep()


def has_model_above(
    routes: Sequence[Route],
    models: Mapping[str, ModelProfile],
    private: bool,
    position: int,
) -> bool:
    """Say whether the ladder, after the veto, has a name above this place.

    Health is not read, so the answer is the same each time it is asked.
    """
    route = route_for(routes, private)
    return any(place > position for place, _ in allowed(route, models, private))


def _fits(profile: ModelProfile, name: str, call: ModelCall, state: RouteState) -> bool:
    needed = (call.size or 0) + call.max_output
    return state.health.get(name, True) and profile.window >= needed
