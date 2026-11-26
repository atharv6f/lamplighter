"""The promises of the effect ledger port, checked on every ledger."""

import asyncio
import dataclasses
import math
from collections.abc import Coroutine
from datetime import timedelta
from typing import Any, cast

import pytest

from lamplighter.ports import (
    Action,
    Begun,
    Effect,
    EffectLedger,
    EffectResult,
    Failure,
    JsonObject,
    Outcome,
)
from tests.contract.stores import MAX_RECORD_BYTES, Stores
from tests.fake_clock import FakeClock
from tests.store_samples import (
    DID_NOT_HAPPEN,
    DONE,
    DONE_WITH_UNDO,
    FAILED,
    HAPPENED,
    PROBE,
    TIMED_OUT,
    UNDO_AFTER,
    UNDO_BEFORE,
    action,
    other_args,
)


def run[T](call: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(call)


def begun(result: Begun | Failure) -> Begun:
    assert isinstance(result, Begun), result
    return result


def failure_of(result: object) -> Failure:
    assert isinstance(result, Failure), f"expected a failure, got {result!r}"
    return result


def effects(ledger: EffectLedger) -> tuple[Effect, ...]:
    listed = run(ledger.unresolved())
    assert not isinstance(listed, Failure), listed
    return listed


def only(ledger: EffectLedger) -> Effect:
    (effect,) = effects(ledger)
    return effect


@pytest.fixture
def ledger(stores: Stores) -> EffectLedger:
    return stores.ledger()


def test_a_new_intent_is_saved_with_its_recipes_and_its_time(
    ledger: EffectLedger,
) -> None:
    first = begun(run(ledger.begin(action(), PROBE, UNDO_BEFORE)))
    assert first.is_new
    effect = first.effect
    assert effect.effect_id >= 1
    assert effect.effect_key == action().effect_key
    assert effect.action == action()
    assert effect.state == "intended"
    assert (effect.probe, effect.undo) == (PROBE, UNDO_BEFORE)
    assert (effect.result, effect.resolution) == (None, None)
    assert effect.begun_at == "2026-11-26T07:35:12.345Z"
    assert (effect.finished_at, effect.resolved_at) == (None, None)
    assert only(ledger) == effect


def test_a_second_begin_of_one_action_finds_the_first_row(ledger: EffectLedger) -> None:
    first = begun(run(ledger.begin(action(), PROBE, UNDO_BEFORE)))
    second = begun(run(ledger.begin(action(), None, UNDO_AFTER)))
    assert not second.is_new
    assert second.effect == first.effect
    assert second.effect.undo == UNDO_BEFORE
    assert len(effects(ledger)) == 1


def test_a_second_begin_finds_the_first_row_when_the_args_hold_a_tuple(
    ledger: EffectLedger,
) -> None:
    # A tuple is saved as a list, so the comparison must be with the action as
    # it was saved, or a repeat of the same action would read as a conflict.
    args = cast(JsonObject, {"lines": ("milk", "tea")})
    with_tuple = dataclasses.replace(action(), args=args)
    first = begun(run(ledger.begin(with_tuple, None, None)))
    second = begun(run(ledger.begin(with_tuple, None, None)))
    assert not second.is_new
    assert second.effect == first.effect
    assert second.effect.action.args == {"lines": ["milk", "tea"]}


def test_a_begin_after_finish_gives_the_row_as_it_now_stands(
    ledger: EffectLedger,
) -> None:
    first = begun(run(ledger.begin(action(), None, None)))
    run(ledger.finish(first.effect.effect_id, DONE))
    again = begun(run(ledger.begin(action(), None, None)))
    assert not again.is_new
    assert again.effect.state == "done"
    assert again.effect.result == DONE


def test_a_different_action_under_a_saved_key_is_a_conflict(
    ledger: EffectLedger,
) -> None:
    first = begun(run(ledger.begin(action(), PROBE, None)))
    failure = failure_of(run(ledger.begin(other_args(action()), PROBE, None)))
    assert (failure.kind, failure.effect) == ("conflict", "none")
    assert only(ledger) == first.effect


def test_each_attempt_of_one_call_has_its_own_row(ledger: EffectLedger) -> None:
    first = begun(run(ledger.begin(action(attempt=0), None, None)))
    second = begun(run(ledger.begin(action(attempt=1), None, None)))
    assert second.is_new
    assert second.effect.effect_id != first.effect.effect_id
    assert [e.action.attempt for e in effects(ledger)] == [0, 1]


@pytest.mark.parametrize("result", [DONE, FAILED, TIMED_OUT], ids=lambda r: r.outcome)
def test_finish_moves_an_intent_to_the_outcome_of_its_result(
    ledger: EffectLedger, clock: FakeClock, result: EffectResult
) -> None:
    effect_id = begun(run(ledger.begin(action(), None, None))).effect.effect_id
    clock.wall += timedelta(seconds=2)
    assert run(ledger.finish(effect_id, result)) is None
    effect = begun(run(ledger.begin(action(), None, None))).effect
    assert effect.state == result.outcome
    assert effect.result == result
    assert effect.finished_at == "2026-11-26T07:35:14.345Z"


def test_an_undo_recipe_from_begin_stays_when_finish_brings_none(
    ledger: EffectLedger,
) -> None:
    effect_id = begun(run(ledger.begin(action(), None, UNDO_BEFORE))).effect.effect_id
    run(ledger.finish(effect_id, DONE))
    assert begun(run(ledger.begin(action(), None, None))).effect.undo == UNDO_BEFORE


def test_an_undo_recipe_from_finish_replaces_the_one_from_begin(
    ledger: EffectLedger,
) -> None:
    effect_id = begun(run(ledger.begin(action(), None, UNDO_BEFORE))).effect.effect_id
    run(ledger.finish(effect_id, DONE_WITH_UNDO))
    effect = begun(run(ledger.begin(action(), None, None))).effect
    assert effect.undo == UNDO_AFTER
    assert effect.result == DONE_WITH_UNDO


def test_a_second_identical_finish_changes_nothing(
    ledger: EffectLedger, clock: FakeClock
) -> None:
    effect_id = begun(run(ledger.begin(action(), None, None))).effect.effect_id
    run(ledger.finish(effect_id, DONE))
    before = begun(run(ledger.begin(action(), None, None))).effect
    clock.wall += timedelta(minutes=1)
    assert run(ledger.finish(effect_id, DONE)) is None
    assert begun(run(ledger.begin(action(), None, None))).effect == before


def test_a_finish_with_another_result_is_a_conflict(ledger: EffectLedger) -> None:
    effect_id = begun(run(ledger.begin(action(), None, None))).effect.effect_id
    run(ledger.finish(effect_id, DONE))
    assert failure_of(run(ledger.finish(effect_id, FAILED))).kind == "conflict"
    assert begun(run(ledger.begin(action(), None, None))).effect.result == DONE


def test_resolve_settles_an_unknown_effect(
    ledger: EffectLedger, clock: FakeClock
) -> None:
    effect_id = begun(run(ledger.begin(action(), None, None))).effect.effect_id
    run(ledger.finish(effect_id, TIMED_OUT))
    clock.wall += timedelta(hours=1)
    assert run(ledger.resolve(effect_id, HAPPENED)) is None
    effect = begun(run(ledger.begin(action(), None, None))).effect
    assert effect.state == "resolved"
    assert effect.resolution == HAPPENED
    assert effect.resolved_at == "2026-11-26T08:35:12.345Z"
    assert effect.result == TIMED_OUT
    assert effects(ledger) == ()


def test_a_second_identical_resolve_changes_nothing_and_another_is_a_conflict(
    ledger: EffectLedger,
) -> None:
    effect_id = begun(run(ledger.begin(action(), None, None))).effect.effect_id
    run(ledger.finish(effect_id, TIMED_OUT))
    run(ledger.resolve(effect_id, HAPPENED))
    assert run(ledger.resolve(effect_id, HAPPENED)) is None
    assert failure_of(run(ledger.resolve(effect_id, DID_NOT_HAPPEN))).kind == (
        "conflict"
    )
    resolution = begun(run(ledger.begin(action(), None, None))).effect.resolution
    assert resolution == HAPPENED


def test_a_finish_after_resolve_with_the_saved_result_changes_nothing(
    ledger: EffectLedger,
) -> None:
    effect_id = begun(run(ledger.begin(action(), None, None))).effect.effect_id
    run(ledger.finish(effect_id, TIMED_OUT))
    run(ledger.resolve(effect_id, HAPPENED))
    assert run(ledger.finish(effect_id, TIMED_OUT)) is None
    assert failure_of(run(ledger.finish(effect_id, DONE))).kind == "conflict"


@pytest.mark.parametrize("finished_with", [None, DONE, FAILED])
def test_only_an_unknown_effect_can_be_resolved(
    ledger: EffectLedger, finished_with: EffectResult | None
) -> None:
    effect_id = begun(run(ledger.begin(action(), None, None))).effect.effect_id
    if finished_with is not None:
        run(ledger.finish(effect_id, finished_with))
    failure = failure_of(run(ledger.resolve(effect_id, HAPPENED)))
    assert (failure.kind, failure.effect) == ("conflict", "none")


@pytest.mark.parametrize("effect_id", [0, -1, 99])
def test_an_effect_id_never_given_out_is_refused(
    ledger: EffectLedger, effect_id: int
) -> None:
    run(ledger.begin(action(), None, None))
    assert failure_of(run(ledger.finish(effect_id, DONE))).kind == "invalid_input"
    assert failure_of(run(ledger.resolve(effect_id, HAPPENED))).kind == (
        "invalid_input"
    )


def test_unresolved_lists_intended_and_unknown_effects_oldest_first(
    ledger: EffectLedger,
) -> None:
    ids = [
        begun(run(ledger.begin(action(call_index=n), None, None))).effect.effect_id
        for n in range(5)
    ]
    run(ledger.finish(ids[0], DONE))
    run(ledger.finish(ids[1], TIMED_OUT))
    run(ledger.finish(ids[3], FAILED))
    run(ledger.finish(ids[4], TIMED_OUT))
    run(ledger.resolve(ids[4], DID_NOT_HAPPEN))
    listed = effects(ledger)
    assert [e.effect_id for e in listed] == [ids[1], ids[2]]
    assert [e.state for e in listed] == ["unknown", "intended"]


def test_an_empty_ledger_has_nothing_unresolved(ledger: EffectLedger) -> None:
    assert effects(ledger) == ()


BROKEN_ACTIONS = {
    "a session id that breaks the rule": dataclasses.replace(
        action(), session_id="-sess"
    ),
    "a negative reply seq": dataclasses.replace(action(), reply_seq=-1),
    "a negative call index": dataclasses.replace(action(), call_index=-1),
    "a negative attempt": dataclasses.replace(action(), attempt=-1),
    "a key that is no hash": dataclasses.replace(action(), effect_key="key-1"),
    "an action hash that is no hash": dataclasses.replace(
        action(), action_hash="A" * 64
    ),
    "v zero": dataclasses.replace(action(), v=0),
    "arguments that are no JSON": dataclasses.replace(
        action(), args={"ratio": math.nan}
    ),
}


@pytest.mark.parametrize("broken", BROKEN_ACTIONS.values(), ids=BROKEN_ACTIONS.keys())
def test_an_action_that_breaks_a_rule_is_refused(
    ledger: EffectLedger, broken: Action
) -> None:
    failure = failure_of(run(ledger.begin(broken, None, None)))
    assert (failure.kind, failure.effect) == ("invalid_input", "none")
    assert effects(ledger) == ()


BIG = {"kind": "blob", "v": 1, "text": "x" * MAX_RECORD_BYTES}


def test_a_value_over_the_size_limit_is_too_large_and_saves_nothing(
    ledger: EffectLedger,
) -> None:
    big_action = dataclasses.replace(action(), args=BIG)
    for probe, undo, made in (
        (BIG, None, action()),
        (None, BIG, action()),
        (None, None, big_action),
    ):
        failure = failure_of(run(ledger.begin(made, probe, undo)))
        assert (failure.kind, failure.effect) == ("too_large", "none")
    assert effects(ledger) == ()
    effect_id = begun(run(ledger.begin(action(), None, None))).effect.effect_id
    big_result = EffectResult(outcome="unknown", output=BIG)
    assert failure_of(run(ledger.finish(effect_id, big_result))).kind == "too_large"
    run(ledger.finish(effect_id, TIMED_OUT))
    big_outcome = Outcome(happened=True, settled_by="check", evidence=BIG)
    assert failure_of(run(ledger.resolve(effect_id, big_outcome))).kind == ("too_large")
    assert only(ledger).state == "unknown"
