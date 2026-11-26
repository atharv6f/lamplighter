"""Builds the events of an open run, stamped the one way the loop stamps them."""

import math

from lamplighter.core.event_types import READ_VERSION
from lamplighter.core.run_view import RunView
from lamplighter.ports import JsonObject, NewEvent


def in_run(
    view: RunView,
    at: str,
    kind: str,
    payload: JsonObject,
    cause: str | None = None,
    duration_ms: int | None = None,
    event_id: str | None = None,
) -> NewEvent:
    """Give one event of the view's open run, at the step of its newest request.

    Before the run's first model request the step is empty. `at` is taken
    once for a whole batch, so every event of a batch has the same time.
    """
    return NewEvent(
        type=kind,
        v=READ_VERSION[kind],
        payload=payload,
        at=at,
        run_id=view.run_id,
        step=view.step or None,
        duration_ms=duration_ms,
        cause=cause,
        event_id=event_id,
    )


def elapsed_ms(start: float, end: float) -> int:
    """Give the time between two monotonic readings, rounded up to a whole ms.

    The difference is first rounded to a microsecond, so a float that is a
    hair over a whole millisecond is not counted as one more.
    """
    return math.ceil(round((end - start) * 1000, 3))


def open_run_id(view: RunView) -> str:
    """Give the id of the view's open run. Only an open run takes a step."""
    if view.run_id is None:
        raise ValueError("this step is only taken in an open run")
    return view.run_id
