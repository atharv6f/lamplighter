"""Sums up the newest run of a session from its saved events. Pure.

It counts as the day report does, so a trial's numbers and the report's
numbers for the same run are the same.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from lamplighter.core.event_types import MODEL_REQUEST, MODEL_RESPONSE, TOOL_RESULT
from lamplighter.core.saved_facts import TokenCounts, saved_cost, token_counts
from lamplighter.ports import RUN_END, RUN_STARTED, Event


@dataclass(frozen=True, slots=True)
class RunSummary:
    """What one run did, and how it ended.

    `reason` and `detail` are those of its `run_end`, or None while it has
    not ended. A reply with a token count missing adds nothing to `tokens`.
    `micro_dollars` is None when a reply had no price.
    """

    run_id: str
    reason: str | None
    detail: str | None
    steps: int
    tool_calls: int
    tokens: TokenCounts
    micro_dollars: int | None


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
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None
