"""One pass of the projector, on logs made up by hand."""

import asyncio
from collections.abc import Coroutine
from typing import Any

import pytest

from lamplighter.adapters.in_memory import InMemoryTraceCursor
from lamplighter.core.run_spans import spans_of_run
from lamplighter.core.trace_projector import PassDone, TraceProjector
from lamplighter.ports import CursorMark, Failure, Span
from tests.projector_kit import BUSY, DOWN, ListedLog, TroubledSink
from tests.span_kit import MadeUpRun, plain_run


def run[T](call: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(call)


class RecordedCursor:
    """The in-memory cursor, with each call written down."""

    def __init__(self, calls: list[str]) -> None:
        self.inner = InMemoryTraceCursor()
        self.calls = calls

    async def read(self) -> CursorMark | Failure:
        self.calls.append("cursor.read")
        return await self.inner.read()

    async def move(self, seen: CursorMark, to: int) -> bool | Failure:
        self.calls.append(f"cursor.move {seen.pos} {seen.resets} {to}")
        return await self.inner.move(seen, to)

    async def reset(self) -> Failure | None:
        self.calls.append("cursor.reset")
        return await self.inner.reset()


class Setup:
    """A log, a recording sink and cursor, and a projector on them."""

    def __init__(self, run_of: MadeUpRun, per_pass: int = 200) -> None:
        self.calls: list[str] = []
        self.events = run_of.log.events
        self.log = ListedLog(self.events, self.calls)
        self.sink = TroubledSink(self.calls)
        self.cursor = RecordedCursor(self.calls)
        self.projector = TraceProjector(self.log, self.sink, self.cursor, per_pass)

    def run_pass(self) -> PassDone | Failure:
        return run(self.projector.run_pass())

    def mark(self) -> CursorMark:
        found = run(self.cursor.inner.read())
        assert isinstance(found, CursorMark)
        return found

    def truth(self) -> dict[str, Span]:
        return {m.span.span_id: m.span for m in spans_of_run(self.events)}

    def held(self) -> dict[str, Span]:
        return {span.span_id: span for span in self.sink.store.held}


def open_run() -> MadeUpRun:
    """A run whose first step is done and whose second is under way."""
    run_of = plain_run()
    log = run_of.log
    other = log.run(session="sess-b")
    other.start()
    other.answered()
    other.end()
    return run_of


def test_a_pass_reads_the_cursor_the_log_exports_once_and_moves() -> None:
    made = Setup(plain_run())

    assert made.run_pass() == PassDone(events=11, spans=6, pos=11)
    assert made.calls == [
        "cursor.read",
        "log.tail 0 200",
        "sink.export 6",
        "cursor.move 0 0 11",
    ]


def test_a_pass_with_nothing_to_do_reads_the_cursor_and_the_log_only() -> None:
    made = Setup(plain_run())
    made.run_pass()
    made.calls.clear()

    assert made.run_pass() == PassDone(events=0, spans=0, pos=11)
    assert made.calls == ["cursor.read", "log.tail 11 200"]


def test_a_pass_with_events_and_no_whole_span_exports_nothing_and_moves() -> None:
    made = Setup(plain_run(), per_pass=3)

    assert made.run_pass() == PassDone(events=3, spans=0, pos=3)
    assert made.calls == ["cursor.read", "log.tail 0 3", "cursor.move 0 0 3"]


def test_an_event_with_no_run_is_read_and_counted_and_gives_no_span() -> None:
    made = Setup(plain_run(), per_pass=1)

    assert made.run_pass() == PassDone(events=1, spans=0, pos=1)
    assert made.events[0].run_id is None


def fail_cursor_read(made: Setup) -> None:
    made.cursor.inner.fail_next(BUSY)


def fail_tail(made: Setup) -> None:
    made.log.fail_next_tail(BUSY)


def fail_log_read(made: Setup) -> None:
    made.log.fail_next_read(BUSY)


def fail_export(made: Setup) -> None:
    made.sink.fail_on = {made.sink.exports + 1}


def fail_move(made: Setup) -> None:
    async def fail(export: int) -> None:
        made.cursor.inner.fail_next(BUSY)
        made.sink.after = None

    made.sink.after = fail


@pytest.mark.parametrize(
    "trouble",
    [fail_cursor_read, fail_tail, fail_log_read, fail_export, fail_move],
    ids=["cursor read", "tail", "log read", "export", "cursor move"],
)
def test_a_failure_ends_the_pass_and_the_next_pass_gives_the_right_spans(
    trouble: Any,
) -> None:
    made = Setup(plain_run(), per_pass=5)
    # Start inside the run, so the pass has a session to read.
    run(made.cursor.inner.move(CursorMark(0, 0), 4))
    trouble(made)

    failed = made.run_pass()
    assert failed in (BUSY, DOWN)
    assert made.mark() == CursorMark(4, 0)

    assert made.run_pass() == PassDone(events=5, spans=4, pos=9)
    assert made.run_pass() == PassDone(events=2, spans=2, pos=11)
    assert made.held() == made.truth()
    # Only a failed move sends a batch again, and the copy is the same.
    assert all(made.truth()[span.span_id] == span for span in made.sink.sent)


def test_a_move_that_says_no_leaves_the_memory_and_the_next_pass_empties_it() -> None:
    made = Setup(plain_run(), per_pass=5)
    run(made.cursor.inner.move(CursorMark(0, 0), 4))

    async def reset_inside(export: int) -> None:
        if export == 1:
            await made.cursor.inner.reset()

    made.sink.after = reset_inside
    assert made.run_pass() == PassDone(events=5, spans=4, pos=4)
    assert made.log.reads == 1
    assert made.mark() == CursorMark(0, 1)

    made.sink.after = None
    while (done := made.run_pass()) != PassDone(0, 0, 11):
        assert not isinstance(done, Failure)
    assert made.held() == made.truth()
    assert made.log.reads == 1


def test_a_cancel_inside_export_leaves_the_cursor_and_the_next_pass_goes_on() -> None:
    made = Setup(plain_run(), per_pass=5)
    run(made.cursor.inner.move(CursorMark(0, 0), 4))

    async def cancelled_pass() -> None:
        hold = asyncio.Event()

        async def stuck(export: int) -> None:
            await hold.wait()

        made.sink.before = stuck
        task = asyncio.create_task(made.projector.run_pass())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(cancelled_pass())
    made.sink.before = None
    assert made.mark() == CursorMark(4, 0)
    assert made.run_pass() == PassDone(events=5, spans=4, pos=9)
    assert made.log.reads == 1


def test_a_process_that_starts_inside_a_run_reads_its_session_once() -> None:
    made = Setup(open_run(), per_pass=2)
    run(made.cursor.inner.move(CursorMark(0, 0), 4))
    made.sink.down = True
    for _ in range(5):
        assert made.run_pass() == DOWN
    made.sink.down = False
    while (done := made.run_pass()) != PassDone(0, 0, len(made.events)):
        assert not isinstance(done, Failure)

    assert [call for call in made.calls if call.startswith("log.read")] == [
        "log.read sess-a 0"
    ]


def test_a_run_that_began_before_the_cursor_has_bounds_that_hold_every_child() -> None:
    made = Setup(plain_run(), per_pass=3)
    run(made.cursor.inner.move(CursorMark(0, 0), 6))
    while (done := made.run_pass()) != PassDone(0, 0, 11):
        assert not isinstance(done, Failure)
    whole_at = {m.span.span_id: m.pos for m in spans_of_run(made.events)}

    # Step 1 and the run hold the model call of step 1, which was whole
    # before the cursor and so never sent by this projector.
    assert made.held() == {
        key: span for key, span in made.truth().items() if whole_at[key] > 6
    }


def test_a_pass_gives_back_last_after_a_move_and_after_otherwise() -> None:
    made = Setup(plain_run(), per_pass=4)

    assert made.run_pass() == PassDone(events=4, spans=0, pos=4)
    assert made.run_pass() == PassDone(events=4, spans=3, pos=8)
    run(made.cursor.inner.reset())
    assert made.run_pass() == PassDone(events=4, spans=0, pos=4)

    async def reset_inside(export: int) -> None:
        await made.cursor.inner.reset()

    made.sink.after = reset_inside
    assert made.run_pass() == PassDone(events=4, spans=3, pos=4)
