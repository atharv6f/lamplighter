"""The port the projector hands finished spans to, and the span record."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from lamplighter.ports.failure import Failure
from lamplighter.ports.json_value import JsonObject

# Which of the four boxes of a run's tree a span is.
type SpanKind = Literal["run", "step", "model", "tool"]


@dataclass(frozen=True, slots=True)
class Span:
    """One timed box of a trace: a run, a step, a model call or a tool call.

    `trace_id` is 32 lowercase hex characters, the same for every span of
    one run, and `span_id` is 16. `parent_id` is None for the run's span.
    Times are whole milliseconds since 1970 in UTC, and `duration_ms` is
    never negative. `error` is None, or one word from a fixed list of this
    repo. An attribute value is a string, a whole number, true or false, or
    a list of strings. A fact that is not known is left out, never sent as
    null, zero or an empty string.
    """

    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    kind: SpanKind
    start_ms: int
    duration_ms: int
    error: str | None
    attributes: JsonObject


class TraceSink(Protocol):
    """Where finished spans go to be kept and shown.

    Callers can rely on this:

    1. `export` returns None only when the trace store holds every span of
       the batch, in a form that outlives a kill of the store's process.
    2. A span whose `span_id` the store already holds changes nothing, so a
       batch may be sent again, in whole or in part, any number of times.
    3. Spans may be sent in any order. A child may arrive before its
       parent, and a parent may never arrive.
    4. An empty batch returns None and reaches nobody.
    5. A failure is a value. `timeout`, `unavailable`, `throttled` and
       `transient` mean the same batch may be tried again later. Any other
       kind means this batch will fail again, so stop. `effect` is
       `unknown` whenever the store may have taken some of the batch.
    6. `export` tries once. It does not retry, and does not wait beyond its
       own time limits. Waiting and trying again are the caller's.
    7. A cancel may land at any moment. The store then holds all of the
       batch, some of it or none.
    8. `leaves_this_mac` is set when the sink is built and never changes. It
       is false only when everything the sink sends goes to a loopback
       address, one that reaches only this Mac.

    A sink calls nothing else in this repo: it never reads the log and
    never moves the cursor.
    """

    @property
    def leaves_this_mac(self) -> bool:
        """Whether anything this sink sends can reach another machine."""
        ...

    async def export(self, spans: Sequence[Span]) -> Failure | None:
        """Hand the store a batch of spans, and say whether it holds them all."""
        ...
