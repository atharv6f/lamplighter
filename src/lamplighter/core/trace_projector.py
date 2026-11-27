"""Turns the log into stored spans, one batch of events per pass.

A pass reads the cursor, reads the events after it, builds the spans that
became whole in them, hands those to the sink, and moves the cursor only
after the sink said they are stored. So the cursor never passes a span the
trace store does not hold, and a kill or a failure anywhere costs at most
one batch sent twice, which the store drops.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from lamplighter.core.run_spans import WholeSpan, spans_of_run
from lamplighter.ports import (
    RUN_END,
    RUN_STARTED,
    CursorMark,
    Event,
    EventLog,
    Failure,
    TraceCursor,
    TraceSink,
)

# How many events one pass reads. A pass sends the spans of one read in one
# export, so this also bounds the size of a batch.
EVENTS_PER_PASS = 200

type _RunKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class PassDone:
    """What one pass did: events read, spans sent, and the cursor's position.

    The position is where the pass moved the cursor, or where it found it
    when there was nothing to do or the move did not go through.
    """

    events: int
    spans: int
    pos: int


class TraceProjector:
    """Sends the spans of the log to a sink, and remembers how far it got.

    It keeps the events of each run that has not ended, up to the mark it
    names, so a pass reads only new events. A run with no entry is read
    from the log, so an empty memory is always right.
    """

    def __init__(
        self,
        log: EventLog,
        sink: TraceSink,
        cursor: TraceCursor,
        events_per_pass: int = EVENTS_PER_PASS,
    ) -> None:
        self._log = log
        self._sink = sink
        self._cursor = cursor
        self._events_per_pass = events_per_pass
        self._runs: dict[_RunKey, tuple[Event, ...]] = {}
        self._right_at: CursorMark | None = None

    async def run_pass(self) -> PassDone | Failure:
        """Send the spans that became whole in the next batch, then move the cursor."""
        seen = await self._cursor.read()
        if isinstance(seen, Failure):
            return seen
        if seen != self._right_at:
            # Someone else moved or reset the cursor, or this is the first
            # pass: the lists may end at another position, so start empty.
            self._runs, self._right_at = {}, seen
        events = await self._log.tail(seen.pos, self._events_per_pass)
        if isinstance(events, Failure):
            return events
        if not events:
            return PassDone(events=0, spans=0, pos=seen.pos)
        last = events[-1].pos
        runs = await self._extended(events, seen.pos)
        if isinstance(runs, Failure):
            return runs
        whole = _whole_in(runs.values(), seen.pos, last)
        if whole:
            failed = await self._sink.export([made.span for made in whole])
            if failed is not None:
                return failed
        moved = await self._cursor.move(seen, last)
        if isinstance(moved, Failure):
            return moved
        if not moved:
            return PassDone(events=len(events), spans=len(whole), pos=seen.pos)
        self._remember(runs, CursorMark(pos=last, resets=seen.resets))
        return PassDone(events=len(events), spans=len(whole), pos=last)

    async def _extended(
        self, events: Sequence[Event], after: int
    ) -> dict[_RunKey, list[Event]] | Failure:
        """Give each touched run's events up to the batch's end, as new lists."""
        runs: dict[_RunKey, list[Event]] = {}
        for event in events:
            if event.run_id is None:
                continue
            key = (event.session_id, event.run_id)
            if key not in runs:
                earlier = await self._earlier(key, event, after)
                if isinstance(earlier, Failure):
                    return earlier
                runs[key] = list(earlier)
            runs[key].append(event)
        return runs

    async def _earlier(
        self, key: _RunKey, event: Event, after: int
    ) -> tuple[Event, ...] | Failure:
        """Give the run's events up to `after`, from memory or from the log."""
        if key in self._runs:
            return self._runs[key]
        if event.type == RUN_STARTED:
            return ()
        read = await self._log.read(key[0], 0)
        if isinstance(read, Failure):
            return read
        earlier = tuple(e for e in read if e.run_id == key[1] and e.pos <= after)
        # The list is the run up to the mark the memory is right at, so it
        # may be kept now. A pass that fails later then never reads it again.
        self._runs[key] = earlier
        return earlier

    def _remember(self, runs: dict[_RunKey, list[Event]], at: CursorMark) -> None:
        # No await here, so a cancel never leaves the memory half replaced.
        kept = dict(self._runs)
        for key, run in runs.items():
            if any(event.type == RUN_END for event in run):
                kept.pop(key, None)
            else:
                kept[key] = tuple(run)
        self._runs, self._right_at = kept, at


def _whole_in(
    runs: Iterable[Sequence[Event]], after: int, last: int
) -> list[WholeSpan]:
    """Give the spans that became whole above `after` and at most at `last`."""
    whole = [
        made for run in runs for made in spans_of_run(run) if after < made.pos <= last
    ]
    return sorted(whole, key=lambda made: made.pos)
