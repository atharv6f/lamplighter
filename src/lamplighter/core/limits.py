"""The four limits of a run, checked against what its saved events say it used."""

from dataclasses import dataclass

from lamplighter.core.harness_texts import LimitName
from lamplighter.core.run_spec import RunSpec
from lamplighter.core.run_view import RunView


@dataclass(frozen=True, slots=True)
class LimitHit:
    """A limit that is reached, with what was used and what was allowed.

    Both are in the limit's own unit: steps, whole seconds, tokens or
    micro-dollars. Used seconds are the milliseconds rounded down.
    """

    limit: LimitName
    used: int
    allowed: int


def time_is_up(view: RunView, spec: RunSpec) -> bool:
    """Say whether the run has used all of its seconds, compared in milliseconds."""
    return view.used_ms >= spec.max_seconds * 1000


def time_left_ms(view: RunView, spec: RunSpec) -> int:
    """Give the milliseconds the run has left, which is 0 once time is up."""
    return max(0, spec.max_seconds * 1000 - view.used_ms)


def seconds_hit(view: RunView, spec: RunSpec) -> LimitHit:
    """Give the record of the time limit, for a run whose time is up."""
    return LimitHit("seconds", view.used_ms // 1000, spec.max_seconds)


def first_limit_reached(view: RunView, spec: RunSpec) -> LimitHit | None:
    """Give the first limit the run has reached, in the order steps, seconds,
    tokens, micro-dollars, or None.

    A limit is reached when what was used is equal to it or above.
    """
    if view.step >= spec.max_steps:
        return LimitHit("steps", view.step, spec.max_steps)
    if time_is_up(view, spec):
        return seconds_hit(view, spec)
    if view.used_tokens >= spec.max_tokens:
        return LimitHit("tokens", view.used_tokens, spec.max_tokens)
    if view.used_micro_dollars >= spec.max_micro_dollars:
        return LimitHit(
            "micro_dollars", view.used_micro_dollars, spec.max_micro_dollars
        )
    return None
