"""Projects a whole log in every order of trouble, and says what went wrong.

The spans the span function gives for the log are the truth. Whatever the
trouble, at the end the sink must hold exactly those, every copy that
arrived must equal its first, and the cursor must be at the log's end.
`quick_wrong` holds the checks that are cheap enough for every kill log,
`slow_wrong` the ones that run the projector once per export or position.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Sequence

from lamplighter.adapters.in_memory import InMemoryTraceCursor
from lamplighter.core.trace_projector import EVENTS_PER_PASS, PassDone, TraceProjector
from lamplighter.ports import CursorMark, Event, Failure
from tests.projector_kit import BUSY, ListedLog, TroubledSink
from tests.span_checks import log_spans

# More passes than any log of the tests needs. A drain that reaches it is
# a projector that never finishes.
_MOST_PASSES = 5_000


def _idle_at(pos: int) -> PassDone:
    return PassDone(events=0, spans=0, pos=pos)


class _Killed(Exception):
    """The projector's process died here. Nothing catches it but the check."""


class _Projection:
    """One log, the spans it promises, and a sink and a cursor to project into."""

    def __init__(self, events: Sequence[Event]) -> None:
        self.events = events
        whole = log_spans(events)
        self.truth = {made.span.span_id: made.span for made in whole}
        self.whole_at = {made.span.span_id: made.pos for made in whole}
        self.end = events[-1].pos if events else 0
        self.log = ListedLog(events)
        self.sink = TroubledSink()
        self.cursor = InMemoryTraceCursor()

    def projector(self, per_pass: int = EVENTS_PER_PASS) -> TraceProjector:
        return TraceProjector(self.log, self.sink, self.cursor, per_pass)

    async def wrong(self, label: str) -> list[str]:
        """Give each way the sink and the cursor differ from the truth."""
        held = {span.span_id: span for span in self.sink.store.held}
        wrong = [
            f"{label}: a copy of {span.name} differs from the truth"
            for span in self.sink.sent
            if self.truth.get(span.span_id) != span
        ]
        if held.keys() != self.truth.keys():
            missing = len(self.truth.keys() - held.keys())
            made_up = len(held.keys() - self.truth.keys())
            wrong.append(f"{label}: {missing} spans missing, {made_up} made up")
        mark = await self.cursor.read()
        if isinstance(mark, Failure) or mark.pos != self.end:
            wrong.append(f"{label}: the cursor ends at {mark}, the log at {self.end}")
        return wrong

    def twice(self, label: str) -> list[str]:
        ids = [span.span_id for span in self.sink.sent]
        return [f"{label}: a span arrived twice"] if len(ids) != len(set(ids)) else []


async def _drain(projector: TraceProjector) -> list[str]:
    """Pass until a pass has nothing to do. A failure is tried again."""
    for _ in range(_MOST_PASSES):
        done = await projector.run_pass()
        if not isinstance(done, Failure) and done.events == 0:
            return []
    return ["the projector never finished"]


def quick_wrong(events: Sequence[Event]) -> list[str]:
    """Give each rule broken by any batch size or by a pass after each event."""
    return asyncio.run(_quick(events))


def slow_wrong(events: Sequence[Event]) -> list[str]:
    """Give each rule broken by a kill, a failure, a second projector or a reset."""
    return asyncio.run(_slow(events))


async def _quick(events: Sequence[Event]) -> list[str]:
    wrong: list[str] = []
    for size in (1, 2, 3, 7, 500):
        made = _Projection(events)
        wrong += await _drain(made.projector(size))
        label = f"{size} events a pass"
        wrong += await made.wrong(label) + made.twice(label)
    return (
        wrong
        + await _pass_after_each_event(events, keep=False)
        + (await _pass_after_each_event(events, keep=True))
    )


async def _pass_after_each_event(events: Sequence[Event], keep: bool) -> list[str]:
    made = _Projection(events)
    kept = made.projector()
    wrong: list[str] = []
    for shown in range(1, len(events) + 1):
        made.log.shown = shown
        wrong += await _drain(kept if keep else made.projector())
    label = "a pass after each event" + (" with memory" if keep else "")
    wrong += await made.wrong(label) + made.twice(label)
    if keep and made.log.reads:
        wrong.append(f"{label}: read a session {made.log.reads} times")
    return wrong


async def _slow(events: Sequence[Event]) -> list[str]:
    checks: list[Callable[[Sequence[Event]], Awaitable[list[str]]]] = [
        _killed_after_each_export,
        _each_export_fails_once,
        _two_projectors,
        _reset_between_passes,
        _reset_during_each_export,
        _reset_and_brought_back,
        _cursor_unreadable_once,
        _started_anywhere_with_the_sink_down,
        _sink_down_then_back,
    ]
    wrong: list[str] = []
    for check in checks:
        wrong += await check(events)
    return wrong


async def _exports(events: Sequence[Event], per_pass: int) -> int:
    """Give how many exports a clean projection makes at this batch size."""
    made = _Projection(events)
    await _drain(made.projector(per_pass))
    return made.sink.exports


async def _killed_after_each_export(events: Sequence[Event]) -> list[str]:
    wrong: list[str] = []
    for number in range(1, await _exports(events, 3) + 1):
        made = _Projection(events)
        made.sink.after = _kill_at(number)
        with contextlib.suppress(_Killed):
            await _drain(made.projector(3))
        wrong += await _drain(made.projector(3))
        wrong += await made.wrong(f"a kill after export {number}")
    return wrong


def _kill_at(number: int) -> Callable[[int], Awaitable[None]]:
    async def kill(export: int) -> None:
        if export == number:
            raise _Killed

    return kill


async def _each_export_fails_once(events: Sequence[Event]) -> list[str]:
    wrong: list[str] = []
    for number in range(1, await _exports(events, 3) + 1):
        made = _Projection(events)
        made.sink.fail_on = {number}
        wrong += await _drain(made.projector(3))
        label = f"export {number} failed once"
        wrong += await made.wrong(label) + made.twice(label)
    return wrong


async def _two_projectors(events: Sequence[Event]) -> list[str]:
    wrong: list[str] = []
    for first, second in ((1, 1), (2, 3), (3, 2), (200, 1)):
        for number in range(1, await _exports(events, first) + 1):
            made = _Projection(events)
            one, other = made.projector(first), made.projector(second)
            made.sink.before = _pass_of(other, number)
            wrong += await _drain(one) + await _drain(other) + await _drain(one)
            wrong += await made.wrong(f"{first} and {second}, inside export {number}")
    return wrong


def _pass_of(other: TraceProjector, number: int) -> Callable[[int], Awaitable[None]]:
    async def run_other(export: int) -> None:
        if export == number:
            await other.run_pass()

    return run_other


async def _reset_between_passes(events: Sequence[Event]) -> list[str]:
    wrong: list[str] = []
    for emptied in (False, True):
        for shown in range(len(events) + 1):
            made = _Projection(events)
            kept = made.projector()
            made.log.shown = shown
            wrong += await _drain(kept)
            await made.cursor.reset()
            if emptied:
                made.sink.store.empty()
            made.log.shown = len(events)
            wrong += await _drain(kept)
            wrong += await made.wrong(
                f"a reset after {shown} events, emptied {emptied}"
            )
    return wrong


async def _reset_during_each_export(events: Sequence[Event]) -> list[str]:
    wrong: list[str] = []
    for per_pass in (3, 200):
        for emptied in (False, True):
            for number in range(1, await _exports(events, per_pass) + 1):
                made = _Projection(events)
                made.sink.after = _reset_at(made, number, emptied)
                wrong += await _drain(made.projector(per_pass))
                label = f"a reset in export {number} at {per_pass}, emptied {emptied}"
                wrong += await made.wrong(label)
    return wrong


def _reset_at(
    made: _Projection, number: int, emptied: bool
) -> Callable[[int], Awaitable[None]]:
    async def reset(export: int) -> None:
        if export == number:
            await made.cursor.reset()
            if emptied:
                made.sink.store.empty()

    return reset


async def _reset_and_brought_back(events: Sequence[Event]) -> list[str]:
    wrong: list[str] = []
    for per_pass in (3, 5):
        for number in range(1, await _exports(events, per_pass) + 1):
            made = _Projection(events)
            made.sink.after = _brought_back_at(made, number, per_pass)
            wrong += await _drain(made.projector(per_pass))
            label = f"brought back in export {number} at {per_pass}"
            wrong += await made.wrong(label)
    return wrong


def _brought_back_at(
    made: _Projection, number: int, per_pass: int
) -> Callable[[int], Awaitable[None]]:
    async def bring_back(export: int) -> None:
        if export != number:
            return
        began = await made.cursor.read()
        assert isinstance(began, CursorMark)
        made.sink.store.empty()
        await made.cursor.reset()
        second = made.projector(per_pass)
        # The second projector stops where the first pass began, and not a
        # pass later: only there does the position alone match the old mark.
        for _ in range(_MOST_PASSES):
            mark = await made.cursor.read()
            assert isinstance(mark, CursorMark)
            if mark.pos >= began.pos:
                return
            done = await second.run_pass()
            if isinstance(done, Failure) or done.events == 0:
                return

    return bring_back


async def _cursor_unreadable_once(events: Sequence[Event]) -> list[str]:
    clean = _Projection(events)
    projector = clean.projector(3)
    passes = 1
    while (done := await projector.run_pass()) != _idle_at(clean.end):
        assert not isinstance(done, Failure)
        passes += 1
    wrong: list[str] = []
    for number in range(1, passes + 1):
        made = _Projection(events)
        kept = made.projector(3)
        for _ in range(number - 1):
            await kept.run_pass()
        exports = made.sink.exports
        made.cursor.fail_next(BUSY)
        if await kept.run_pass() != BUSY or made.sink.exports != exports:
            wrong.append(f"pass {number} went on with no cursor")
        wrong += await _drain(kept)
        label = f"no cursor at pass {number}"
        wrong += await made.wrong(label) + made.twice(label)
    return wrong


async def _started_anywhere_with_the_sink_down(events: Sequence[Event]) -> list[str]:
    wrong: list[str] = []
    for at in [0, *(event.pos for event in events)]:
        made = _Projection(events)
        if at:
            await made.cursor.move(CursorMark(pos=0, resets=0), at)
        made.sink.down = True
        kept = made.projector()
        for number in (1, 2, 3):
            reads = made.log.reads
            await kept.run_pass()
            if number > 1 and made.log.reads != reads:
                wrong.append(f"started at {at}: pass {number} read a session")
    return wrong


async def _sink_down_then_back(events: Sequence[Event]) -> list[str]:
    wrong: list[str] = []
    for number in range(1, await _exports(events, 3) + 1):
        made = _Projection(events)
        made.sink.fail_on = set(range(number, number + 3))
        kept = made.projector(3)
        for _ in range(number + 2):
            await kept.run_pass()
            wrong += await _below_what_is_missing(made, number)
        made.sink.fail_on = set()
        wrong += await _drain(kept)
        wrong += await made.wrong(f"the sink down from export {number}")
    return wrong


async def _below_what_is_missing(made: _Projection, number: int) -> list[str]:
    mark = await made.cursor.read()
    assert isinstance(mark, CursorMark)
    held = {span.span_id for span in made.sink.store.held}
    passed = [
        span_id
        for span_id, pos in made.whole_at.items()
        if span_id not in held and pos <= mark.pos
    ]
    if passed:
        return [f"down from export {number}: the cursor passed a span not stored"]
    return []
