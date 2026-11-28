"""The route step: when it asks for health, what it saves, and what it gives."""

import asyncio
import dataclasses
import time
from collections.abc import Mapping
from typing import Any

import pytest

from lamplighter.adapters.in_memory import InMemoryEventLog
from lamplighter.core import ROUTE_DECIDED, Deps
from lamplighter.core.model_choice import Choice, Keep, NoModel, Signal
from lamplighter.core.route_step import route_step
from lamplighter.core.run_spec import RunSpec
from lamplighter.core.run_view import RunView, view_of
from lamplighter.ports import Event, Failure, HealthCheck, NewEvent, TextPart, Usage
from tests.ladder_kit import (
    DEFAULT_ROUTE,
    DOWN,
    FRONTIER,
    HELD,
    LADDER,
    LOCAL,
    SteeredHealth,
    private,
)
from tests.loop_kit import MAX_RECORD_BYTES, in_memory
from tests.model_by_request import ModelByRequest
from tests.run_history import SESSION, History, call

NOW = "2026-11-26T07:35:12.345Z"


class Saved:
    """A session's events in a log, and the view of them, for one route step."""

    def __init__(self, history: History) -> None:
        self.log = InMemoryEventLog({}, MAX_RECORD_BYTES)
        for event in history.events:
            made = NewEvent(
                type=event.type,
                v=event.v,
                payload=event.payload,
                at=event.at,
                run_id=event.run_id,
                step=event.step,
                duration_ms=event.duration_ms,
                cause=event.cause,
                event_id=event.event_id if event.type == "user_message" else None,
            )
            added = asyncio.run(self.log.append(SESSION, (made,), event.seq - 1))
            assert not isinstance(added, Failure), added

    def view(self) -> RunView:
        made = view_of(self.events())
        assert isinstance(made, RunView), made
        return made

    def events(self) -> tuple[Event, ...]:
        read = asyncio.run(self.log.read(SESSION, 0))
        assert not isinstance(read, Failure), read
        return read

    def deps(self, health: Mapping[str, HealthCheck]) -> Deps:
        model = ModelByRequest(lambda request: pytest.fail("no model is called"))
        made = in_memory(model).deps
        return dataclasses.replace(
            made, log=self.log, models={LOCAL: model, FRONTIER: model}, health=health
        )

    def step(
        self,
        spec: RunSpec = LADDER,
        health: Mapping[str, HealthCheck] | None = None,
        given: Signal | None = None,
    ) -> Choice | Keep | NoModel | Failure:
        deps = self.deps(health or {})
        return asyncio.run(route_step(SESSION, self.view(), spec, deps, given))

    def choices(self) -> list[dict[str, Any]]:
        return [dict(e.payload) for e in self.events() if e.type == ROUTE_DECIDED]


def started(private_run: bool = False) -> History:
    return History().start(private=private_run)


def test_a_new_run_asks_each_model_and_saves_its_first_choice() -> None:
    saved = Saved(started())
    local = SteeredHealth()
    before = saved.view().last_seq
    answer = saved.step(health={LOCAL: local})
    assert answer == Choice("default", LOCAL, 0, "start")
    assert local.asked == 1
    events = saved.events()
    assert len(events) == before + 1
    last = events[-1]
    assert (last.type, last.v, last.run_id, last.step) == (
        ROUTE_DECIDED,
        1,
        "run-1",
        None,
    )
    assert last.payload == {
        "route_id": "default",
        "model": LOCAL,
        "position": 0,
        "reason": "start",
        "private": False,
        "size": None,
        "health": {LOCAL: "up", FRONTIER: "not_asked"},
    }


def test_a_model_that_is_down_is_skipped_and_its_answer_saved() -> None:
    saved = Saved(started())
    answer = saved.step(health={LOCAL: SteeredHealth(HELD)})
    assert answer == Choice("default", FRONTIER, 1, "skipped")
    assert saved.choices()[-1]["health"] == {LOCAL: "fatal", FRONTIER: "not_asked"}


def test_a_session_too_large_for_the_local_window_starts_above_it() -> None:
    history = started()
    history.request()
    usage = Usage(input_uncached=65_000, cache_read=0, cache_write=0, output=0)
    history.reply(TextPart(text="Long."), usage=usage)
    saved = Saved(history)
    assert saved.step() == Choice("default", FRONTIER, 1, "skipped")
    assert saved.choices()[-1]["size"] == 65_000


def test_with_a_choice_made_and_no_signal_nothing_is_asked_or_saved() -> None:
    history = started()
    history.chose(LOCAL, 0)
    saved = Saved(history)
    local = SteeredHealth()
    before = saved.events()
    assert saved.step(health={LOCAL: local}) == Keep()
    assert local.asked == 0
    assert saved.events() == before


@pytest.mark.parametrize(
    "signal",
    ["empty_reply", "refused", "model_failed", "too_large"],
)
def test_a_signal_of_the_move_moves_the_run_up_and_is_the_reason(
    signal: Signal,
) -> None:
    history = started()
    history.chose(LOCAL, 0)
    saved = Saved(history)
    local = SteeredHealth()
    answer = saved.step(health={LOCAL: local}, given=signal)
    assert answer == Choice("default", FRONTIER, 1, signal)
    assert local.asked == 1
    assert saved.choices()[-1]["reason"] == signal


def test_a_standing_signal_moves_the_run_up_with_no_signal_given() -> None:
    history = started()
    history.chose(LOCAL, 0)
    for name in ("call_a", "call_b"):
        history.result(call(name), "Made-up answer.", harness="arguments_invalid")
    saved = Saved(history)
    assert saved.step() == Choice("default", FRONTIER, 1, "invalid_calls")


def test_with_a_signal_and_no_model_above_up_the_answer_is_keep_and_unsaved() -> None:
    history = started()
    history.chose(LOCAL, 0)
    saved = Saved(history)
    before = saved.events()
    frontier = SteeredHealth(DOWN)
    answer = saved.step(health={FRONTIER: frontier}, given="refused")
    assert answer == Keep()
    assert frontier.asked == 1
    assert saved.events() == before
    # Asked again on the next step, so the run moves once the model is back.
    frontier.answer = None
    assert saved.step(health={FRONTIER: frontier}, given="refused") == Choice(
        "default", FRONTIER, 1, "refused"
    )
    assert frontier.asked == 2


def test_a_private_run_never_asks_or_picks_a_model_that_leaves_this_mac() -> None:
    saved = Saved(started(private_run=True))
    frontier = SteeredHealth()
    answer = saved.step(
        spec=private(), health={LOCAL: SteeredHealth(), FRONTIER: frontier}
    )
    assert answer == Choice("private", LOCAL, 0, "start")
    assert frontier.asked == 0
    assert saved.choices()[-1]["private"] is True
    assert saved.choices()[-1]["health"] == {LOCAL: "up"}


def test_a_private_run_whose_local_model_is_down_has_no_model() -> None:
    saved = Saved(started(private_run=True))
    before = saved.events()
    answer = saved.step(spec=private(), health={LOCAL: SteeredHealth(DOWN)})
    assert answer == NoModel("private", (LOCAL,))
    assert saved.events() == before


def test_a_session_that_was_private_keeps_a_later_run_private() -> None:
    history = started(private_run=True)
    history.answered()
    history.start(native_id="m2", run_id="run-2", private=False)
    saved = Saved(history)
    answer = saved.step(health={LOCAL: SteeredHealth(DOWN)})
    assert answer == NoModel("private", (LOCAL,))


def test_a_check_that_does_not_answer_counts_as_unavailable_and_is_stopped() -> None:
    saved = Saved(started())
    hung = SteeredHealth(hang=True)
    began = time.monotonic()
    answer = saved.step(health={LOCAL: hung})
    took = time.monotonic() - began
    assert answer == Choice("default", FRONTIER, 1, "skipped")
    assert saved.choices()[-1]["health"][LOCAL] == "unavailable"
    assert hung.cancelled == 1
    assert 1.9 <= took < 3.0


def test_the_checks_are_asked_at_once_and_not_one_after_another() -> None:
    history = started()
    history.chose(LOCAL, 0)
    saved = Saved(history)
    first, second = SteeredHealth(hang=True), SteeredHealth(hang=True)
    began = time.monotonic()
    answer = saved.step(health={LOCAL: first, FRONTIER: second}, given="refused")
    took = time.monotonic() - began
    # Both hung, so both count as down, inside one bound and not two.
    assert answer == Keep()
    assert (first.cancelled, second.cancelled) == (1, 1)
    assert took < 3.0


def test_a_choice_the_log_refuses_is_given_as_the_failure() -> None:
    saved = Saved(started())
    view = saved.view()
    note = NewEvent(type="note", v=1, payload={"text": "Raced."}, at=NOW)
    asyncio.run(saved.log.append(SESSION, (note,), view.last_seq))
    deps = saved.deps({})
    answer = asyncio.run(route_step(SESSION, view, LADDER, deps, None))
    assert isinstance(answer, Failure)
    assert ROUTE_DECIDED not in {event.type for event in saved.events()}


def test_with_no_private_route_the_veto_strikes_the_frontier_from_the_default() -> None:
    spec = dataclasses.replace(private(), routes=(DEFAULT_ROUTE,))
    saved = Saved(started(private_run=True))
    frontier = SteeredHealth()
    answer = saved.step(
        spec=spec, health={LOCAL: SteeredHealth(DOWN), FRONTIER: frontier}
    )
    assert answer == NoModel("default", (LOCAL,))
    assert frontier.asked == 0


def test_a_choice_in_a_session_private_from_an_earlier_run_is_saved_as_private() -> (
    None
):
    history = started(private_run=True)
    history.answered()
    history.start(native_id="m2", run_id="run-2", private=False)
    saved = Saved(history)
    assert saved.step(health={LOCAL: SteeredHealth()}) == Choice(
        "private", LOCAL, 0, "start"
    )
    assert saved.choices()[-1]["private"] is True
