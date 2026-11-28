"""Sums up the newest run of a session from its saved events. Pure.

It counts as the day report does, so a trial's numbers and the report's
numbers for the same run are the same.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from lamplighter.core.event_types import (
    MODEL_REQUEST,
    MODEL_RESPONSE,
    ROUTE_DECIDED,
    TOOL_RESULT,
)
from lamplighter.core.model_choice import ChoiceReason
from lamplighter.core.saved_facts import TokenCounts, saved_cost, token_counts
from lamplighter.ports import RUN_END, RUN_STARTED, Event


@dataclass(frozen=True, slots=True)
class RunSummary:
    """What one run did, and how it ended.

    `reason` and `detail` are those of its `run_end`, or None while it has
    not ended. A reply with a token count missing adds nothing to `tokens`.
    `micro_dollars` is None when a reply had no price. `models_called` are
    the names of the models it asked, in the order it first asked each.
    `escalated` says it moved up its ladder on a signal and then asked the
    model it moved to. `output_fault` is what the adapter's end checks
    found in its last reply, such as `call_markup`, or None.
    """

    run_id: str
    reason: str | None
    detail: str | None
    steps: int
    tool_calls: int
    tokens: TokenCounts
    micro_dollars: int | None
    models_called: tuple[str, ...]
    escalated: bool
    output_fault: str | None


def run_summary(events: Iterable[Event]) -> RunSummary | None:
    """Give the summary of the newest run among the events, or None with no run."""
    newest: str | None = None
    by_run: dict[str, list[Event]] = {}
    for event in events:
        if event.run_id is None:
            continue
        if event.type == RUN_STARTED:
            newest = event.run_id
        by_run.setdefault(event.run_id, []).append(event)
    if newest is None:
        return None
    return _summed(newest, by_run[newest])


def _summed(run_id: str, events: list[Event]) -> RunSummary:
    steps = tool_calls = uncached = read = written = output = cost = 0
    priced = True
    reason: str | None = None
    detail: str | None = None
    for event in events:
        if event.type == MODEL_REQUEST:
            steps += 1
        elif event.type == TOOL_RESULT:
            tool_calls += 1
        elif event.type == RUN_END:
            reason = _text(event.payload.get("reason"))
            detail = _text(event.payload.get("detail"))
        elif event.type == MODEL_RESPONSE:
            counts = token_counts(event.payload)
            if counts is not None:
                uncached += counts.uncached
                read += counts.read
                written += counts.written
                output += counts.output
            replied = saved_cost(event.payload)
            priced = priced and replied is not None
            cost += replied or 0
    return RunSummary(
        run_id=run_id,
        reason=reason,
        detail=detail,
        steps=steps,
        tool_calls=tool_calls,
        tokens=TokenCounts(uncached, read, written, output),
        micro_dollars=cost if priced else None,
        models_called=_models_called(events),
        escalated=_escalated(events),
        output_fault=_output_fault(events),
    )


# The reasons of a choice that are not a signal to move up.
_NOT_SIGNALS: frozenset[ChoiceReason] = frozenset({"start", "skipped"})


def _models_called(events: list[Event]) -> tuple[str, ...]:
    names = [
        event.payload.get("model_name")
        for event in events
        if event.type == MODEL_REQUEST
    ]
    return tuple(dict.fromkeys(n for n in names if isinstance(n, str)))


def _escalated(events: list[Event]) -> bool:
    moved_to: str | None = None
    for event in events:
        if event.type == ROUTE_DECIDED:
            reason = event.payload.get("reason")
            model = _text(event.payload.get("model"))
            moved_to = None if reason in _NOT_SIGNALS else model
            continue
        asked = event.payload.get("model_name") if event.type == MODEL_REQUEST else None
        if moved_to is not None and asked == moved_to:
            return True
    return False


def _output_fault(events: list[Event]) -> str | None:
    replies = [e.payload for e in events if e.type == MODEL_RESPONSE]
    end = replies[-1].get("end") if replies else None
    failure = end.get("failure") if isinstance(end, dict) else None
    detail = failure.get("detail") if isinstance(failure, dict) else None
    return _text(detail.get("model_output")) if isinstance(detail, dict) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None
