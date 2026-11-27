"""Counts the events of one day in UTC, and gives the lines of its report. Pure.

The events are handed over one at a time, in log order, from the start of
the log, and none is kept. What is kept across days is small: the sums of
each run that has not ended, since a run's line covers all of its replies,
and the model each unanswered request named, since a reply can come on the
day after its request.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from lamplighter.core.event_types import MODEL_REQUEST, MODEL_RESPONSE, TOOL_RESULT
from lamplighter.core.run_spans import model_name
from lamplighter.core.saved_facts import (
    reply_failure,
    saved_cost,
    token_counts,
    tool_error,
)
from lamplighter.ports import RUN_END, RUN_STARTED, Event, JsonObject

NO_REPLY = (
    "Model calls with no saved reply: {n}. The provider may have billed them."
    " They are in no sum above. A call that is running right now is counted"
    " here too."
)
COUNT_MISSING = (
    "Replies with a token count missing: {n}. They are in no token sum above."
)
_UNKNOWN_MODEL = "unknown"
_MICROS_PER_DOLLAR = 1_000_000

type _RunKey = tuple[str, str]


@dataclass(slots=True)
class _Sums:
    """The replies of a day, a model or a run, added up."""

    calls: int = 0
    ms: int = 0
    uncached: int = 0
    read: int = 0
    written: int = 0
    output: int = 0
    cost: int = 0
    unpriced: int = 0

    def add(self, reply: Event) -> None:
        self.calls += 1
        self.ms += reply.duration_ms or 0
        counts = token_counts(reply.payload)
        if counts is not None:
            self.uncached += counts.uncached
            self.read += counts.read
            self.written += counts.written
            self.output += counts.output
        cost = saved_cost(reply.payload)
        if cost is None:
            self.unpriced += 1
        else:
            self.cost += cost

    @property
    def input(self) -> int:
        return self.uncached + self.read + self.written


@dataclass(slots=True)
class _Run:
    """What one run has done so far, over all its days."""

    steps: int = 0
    tool_calls: int = 0
    replies: _Sums = field(default_factory=_Sums)


class DayReport:
    """The counts of one day, built up one event at a time."""

    def __init__(self, day: str) -> None:
        """Count the day `day`, written as YYYY-MM-DD."""
        self._day = day
        self._ended: Counter[str] = Counter()
        self._steps = 0
        self._tool_calls = 0
        self._replies = _Sums()
        self._by_model: dict[str, _Sums] = {}
        self._requested: dict[str, str | None] = {}
        self._unanswered: set[str] = set()
        self._count_missing = 0
        self._errors: Counter[str] = Counter()
        self._runs: dict[_RunKey, _Run] = {}
        self._run_lines: list[str] = []

    def count(self, event: Event) -> None:
        """Take the next event of the log into the counts."""
        today = event.at.startswith(self._day)
        run = self._run_of(event)
        if event.type == MODEL_REQUEST:
            self._request(event, today, run)
        elif event.type == MODEL_RESPONSE:
            self._reply(event, today, run)
        elif event.type == TOOL_RESULT:
            self._result(event, today, run)
        elif event.type == RUN_END:
            self._run_end(event, today)

    def lines(self) -> list[str]:
        """Give the report of the day, as counted so far, one line each."""
        sums = self._replies
        lines = [
            f"Day {self._day}, 00:00 to 24:00 UTC",
            _runs_ended(self._ended),
            f"Steps: {self._steps}",
            f"Tool calls: {self._tool_calls}",
            f"Tokens in: {sums.input} ({sums.uncached} not cached, {sums.read} read"
            f" from the cache, {sums.written} written to the cache)",
            f"Tokens out: {sums.output}",
            f"Cache hit rate: {_rate(sums)}",
            f"Cost: {_cost(sums)}",
            *_section(
                "By model", [_model_row(*row) for row in sorted(self._by_model.items())]
            ),
        ]
        if self._unanswered:
            lines.append(NO_REPLY.format(n=len(self._unanswered)))
        if self._count_missing:
            lines.append(COUNT_MISSING.format(n=self._count_missing))
        errors = [f"{kind}: {n}" for kind, n in sorted(self._errors.items())]
        lines += _section("Errors by kind", errors)
        lines += _section("Runs", self._run_lines)
        return lines

    def _run_of(self, event: Event) -> _Run | None:
        if event.run_id is None:
            return None
        key = (event.session_id, event.run_id)
        if event.type == RUN_STARTED:
            self._runs[key] = _Run()
        return self._runs.get(key)

    def _request(self, request: Event, today: bool, run: _Run | None) -> None:
        model = request.payload.get("model")
        self._requested[request.event_id] = model if isinstance(model, str) else None
        if run is not None:
            run.steps += 1
        if today:
            self._steps += 1
            self._unanswered.add(request.event_id)

    def _reply(self, reply: Event, today: bool, run: _Run | None) -> None:
        cause = reply.cause or ""
        self._unanswered.discard(cause)
        requested = self._requested.pop(cause, None)
        if run is not None:
            run.replies.add(reply)
        if not today:
            return
        self._replies.add(reply)
        model = requested or _reported_model(reply) or _UNKNOWN_MODEL
        self._by_model.setdefault(model, _Sums()).add(reply)
        if token_counts(reply.payload) is None:
            self._count_missing += 1
        failure = reply_failure(reply.payload)
        if failure is not None:
            self._errors[f"model {failure}"] += 1

    def _result(self, result: Event, today: bool, run: _Run | None) -> None:
        if run is not None:
            run.tool_calls += 1
        if not today:
            return
        self._tool_calls += 1
        word = tool_error(result.payload)
        if word is not None:
            self._errors[f"tool {word}"] += 1

    def _run_end(self, ended: Event, today: bool) -> None:
        run = self._runs.pop((ended.session_id, ended.run_id or ""), None) or _Run()
        if not today:
            return
        reason = _text(ended.payload.get("reason")) or "unknown"
        detail = _text(ended.payload.get("detail"))
        how = reason if detail is None else f"{reason}: {detail}"
        self._ended[reason] += 1
        if reason in ("failed", "limit"):
            self._errors[f"run {how}"] += 1
        # A run whose calls were all cut off may still have been billed.
        cost = _cost(run.replies) if run.replies.calls else "no price"
        self._run_lines.append(
            f"{ended.at[11:19]} {how}, {_counted(run.steps, 'step')},"
            f" {_counted(run.tool_calls, 'tool call')}, cache {_rate(run.replies)},"
            f" {cost}, session {ended.session_id} run {ended.run_id}"
        )


def _reported_model(reply: Event) -> str | None:
    end = reply.payload.get("end")
    if not isinstance(end, Mapping):
        return None
    return model_name(cast(JsonObject, end).get("model_reported"))


def _runs_ended(ended: Counter[str]) -> str:
    total = sum(ended.values())
    if not total:
        return "Runs ended: 0"
    parts = ", ".join(f"{n} {reason}" for reason, n in sorted(ended.items()))
    return f"Runs ended: {total} ({parts})"


def _model_row(model: str, sums: _Sums) -> str:
    return (
        f"{model}: {_counted(sums.calls, 'call')}, {sums.ms / 1000:.1f} s,"
        f" in {sums.uncached} + {sums.read} read + {sums.written} written,"
        f" out {sums.output}, {_cost(sums)}"
    )


def _section(title: str, rows: list[str]) -> list[str]:
    if not rows:
        return [f"{title}: none"]
    return [f"{title}:", *(f"  {row}" for row in rows)]


def _rate(sums: _Sums) -> str:
    """Give the share of input tokens read from the cache, or none with no input."""
    if not sums.input:
        return "none"
    return f"{100 * sums.read / sums.input:.1f}%"


def _cost(sums: _Sums) -> str:
    """Give the saved costs in dollars, never counting a call with no price as 0."""
    if sums.calls and sums.unpriced == sums.calls:
        return "no price"
    dollars = f"${sums.cost // _MICROS_PER_DOLLAR}.{sums.cost % _MICROS_PER_DOLLAR:06d}"
    if not sums.unpriced:
        return dollars
    return f"{dollars} ({_counted(sums.unpriced, 'call')} with no price)"


def _counted(n: int, thing: str) -> str:
    return f"{n} {thing}" if n == 1 else f"{n} {thing}s"


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
