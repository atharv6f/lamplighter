"""Chooses the run's model when it has none or a signal stands, and saves the choice."""

import asyncio
from collections.abc import Mapping, Sequence

from lamplighter.core.event_types import ROUTE_DECIDED
from lamplighter.core.model_choice import (
    Choice,
    Keep,
    ModelCall,
    NoModel,
    RouteState,
    Signal,
    allowed,
    choose,
    route_for,
)
from lamplighter.core.run_deps import Deps
from lamplighter.core.run_events import in_run
from lamplighter.core.run_spec import RunSpec
from lamplighter.core.run_view import RunView
from lamplighter.ports import Failure, HealthCheck, JsonObject, ModelProfile, utc_text

# The loop's own bound on all the health checks of one step. Each check
# promises an answer within two seconds; this is the backstop behind that.
_HEALTH_SECONDS = 2.0
_LATE = Failure(
    kind="unavailable",
    effect="none",
    message="The health check did not answer within two seconds.",
    source="route_step",
)


def is_private(view: RunView, spec: RunSpec) -> bool:
    """Say whether the run's calls must stay on this Mac."""
    return spec.private or view.session_private


def profiles(spec: RunSpec) -> Mapping[str, ModelProfile]:
    """Give each model's profile by its name."""
    return {entry.name: entry.profile for entry in spec.models}


async def route_step(
    session_id: str,
    view: RunView,
    spec: RunSpec,
    deps: Deps,
    given: Signal | None,
) -> Choice | Keep | NoModel | Failure:
    """Give the model the run calls next, and save a new choice as `route_decided`.

    `given` is the signal of the move, from how the last reply would end
    the run. With a choice already made and no signal, the answer is Keep
    and nothing is asked. Otherwise the health of each model left on the
    ladder is asked, all at once, and a Choice is saved before it is given.
    """
    signal = given or view.signal
    if view.route is not None and signal is None:
        return Keep()
    private = is_private(view, spec)
    route = route_for(spec.routes, private)
    known = profiles(spec)
    asked = [name for _, name in allowed(route, known, private) if name in deps.health]
    answers = {
        name: "up" if answer is None else answer.kind
        for name, answer in (await ask_health(deps.health, asked)).items()
    }
    call = ModelCall(private, view.session_size, spec.model_max_output)
    position = None if view.route is None else view.route.position
    up = {name: answer == "up" for name, answer in answers.items()}
    answer = choose(spec.routes, known, call, RouteState(position, signal, up))
    if not isinstance(answer, Choice):
        return answer
    payload: JsonObject = {
        "route_id": answer.route_id,
        "model": answer.model,
        "position": answer.position,
        "reason": answer.reason,
        "private": private,
        "size": call.size,
        "health": {name: answers.get(name, "not_asked") for name in route.models},
    }
    event = in_run(view, utc_text(deps.clock.now()), ROUTE_DECIDED, payload)
    saved = await deps.log.append(session_id, (event,), view.last_seq)
    return saved if isinstance(saved, Failure) else answer


async def ask_health(
    checks: Mapping[str, HealthCheck], names: Sequence[str]
) -> dict[str, Failure | None]:
    """Ask each named check at once, and give each one's answer.

    A check that has not answered within two seconds is stopped, and its
    answer is `unavailable`. No check is left running after this returns.
    """
    tasks = {name: asyncio.create_task(checks[name].health()) for name in names}
    if tasks:
        await asyncio.wait(tasks.values(), timeout=_HEALTH_SECONDS)
    late = [task for task in tasks.values() if not task.done()]
    for task in late:
        task.cancel()
    await asyncio.gather(*late, return_exceptions=True)
    return {
        name: _LATE if task in late else task.result() for name, task in tasks.items()
    }
