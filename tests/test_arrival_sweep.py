"""A stop, or a message, made to arrive just before and just after every port call.

The chat process answers two messages, one run each. For each port call of
that run, a fresh process gets the arrival at that point, and the outcome
is checked against what the arrival point must lead to.
"""

import asyncio
from dataclasses import dataclass

from lamplighter.adapters import CountingIds, ScriptedChannel
from lamplighter.core import take
from lamplighter.ports import Inbound
from tests.chat_kit import Outcome, Stores, chat, first_and_second, outcome, process
from tests.port_counter import Counter, When
from tests.run_history import SPEC, inbound, stop
from tests.waiting_clock import JumpingClock

type Point = tuple[int, When]


def _order(point: Point) -> tuple[int, int]:
    number, when = point
    return number, 0 if when == "before" else 1


@dataclass
class Run:
    """One whole chat, with an arrival at `point`, or none."""

    names: list[str]
    result: Outcome


def chat_with(arrival: Inbound | None, point: Point | None) -> Run:
    async def scenario() -> Run:
        clock = JumpingClock()
        stores = Stores()
        channel = ScriptedChannel(first_and_second(), clock, one_at_a_time=True)
        counter = Counter()
        made = process(stores, channel, clock, CountingIds(), counter)

        async def arrive(number: int, when: When, name: str) -> None:
            del name
            if arrival is not None and (number, when) == point:
                # The arrival takes the stores directly, so it is not a call
                # of the process and does not shift the count.
                await take(
                    arrival, stores.inbox, channel, made.deps.running, made.wake.wake
                )

        counter.hook = arrive
        assert await chat(made, SPEC) is None
        return Run(counter.names, await outcome(stores, channel))

    return asyncio.run(scenario())


BASELINE = chat_with(None, None)


def _number_of(name: str, nth: int = 1) -> int:
    numbers = [n + 1 for n, called in enumerate(BASELINE.names) if called == name]
    return numbers[nth - 1]


def _points() -> list[Point]:
    return [
        (number, when)
        for number in range(1, len(BASELINE.names) + 1)
        for when in ("before", "after")
    ]


# The milestones the expectation is drawn from, found in the run with no
# arrival. A final reply is safe from a stop once its model call has
# begun, because this model gives no turn to the event loop while it
# streams, so the call is never cut.
FIRST_SAVED = (_number_of("inbox.accept m1"), "after")
FIRST_FINAL_CALL = (_number_of("model.stream", 3), "before")
SECOND_SAVED = (_number_of("inbox.accept m2"), "after")
SECOND_FINAL_CALL = (_number_of("model.stream", 5), "before")


def expected_ends(point: Point) -> list[str]:
    at = _order(point)
    if at < _order(FIRST_SAVED):
        return ["done", "done"]  # A stop with nothing to stop is dropped.
    if at < _order(FIRST_FINAL_CALL):
        return ["cancelled", "done"]
    if at < _order(SECOND_SAVED):
        return ["done", "done"]  # Too late for the first, before the second.
    if at < _order(SECOND_FINAL_CALL):
        return ["done", "cancelled"]
    return ["done", "done"]


def test_the_run_with_no_arrival_answers_both_messages() -> None:
    assert BASELINE.result.run_ends == ["done", "done"]
    assert len(BASELINE.names) > 60
    assert _order(FIRST_SAVED) < _order(FIRST_FINAL_CALL)
    assert _order(FIRST_FINAL_CALL) < _order(SECOND_SAVED)
    assert _order(SECOND_SAVED) < _order(SECOND_FINAL_CALL)


def test_a_stop_at_every_point_ends_exactly_the_run_it_should() -> None:
    wrong: list[str] = []
    counts = {"cancelled first": 0, "cancelled second": 0, "untouched": 0}
    for point in _points():
        result = chat_with(stop("s1"), point).result
        expected = expected_ends(point)
        if result.run_ends != expected:
            wrong.append(f"{point}: {result.run_ends}, expected {expected}")
        if result.pending or result.due:
            wrong.append(f"{point}: left {result.pending} {result.due}")
        sent = [out.text for out in result.sent]
        if len(sent) != 2 or len(result.texts_of("reply_ready")) != 2:
            wrong.append(f"{point}: sent {sent}")
        if expected[0] == "cancelled":
            counts["cancelled first"] += 1
        elif expected[1] == "cancelled":
            counts["cancelled second"] += 1
        else:
            counts["untouched"] += 1
    assert wrong == []
    assert all(count > 0 for count in counts.values()), counts


def test_a_message_at_every_point_is_answered_once_in_a_run_that_ends_done() -> None:
    wrong: list[str] = []
    for point in _points():
        result = chat_with(inbound("m3", "And one more."), point).result
        said = result.texts_of("user_message")
        if said.count("And one more.") != 1:
            wrong.append(f"{point}: said {said}")
        if set(result.run_ends) != {"done"}:
            wrong.append(f"{point}: {result.run_ends}")
        if result.pending or result.due:
            wrong.append(f"{point}: left {result.pending} {result.due}")
        if len(result.sent) != len(result.run_ends):
            wrong.append(f"{point}: {len(result.sent)} sent")
    assert wrong == []


def test_the_sweep_reaches_past_the_last_read_of_the_inbox() -> None:
    last_read = max(
        number
        for number, name in enumerate(BASELINE.names, start=1)
        if name == "inbox.pending"
    )
    result = chat_with(stop("s1"), (last_read, "after")).result
    assert result.pending == ()
