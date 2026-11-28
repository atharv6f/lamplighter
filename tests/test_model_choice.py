"""The model choice: the worked cases, and its laws over random tables and runs."""

import random
from collections.abc import Mapping

import pytest

from lamplighter.core import Route
from lamplighter.core.model_choice import (
    Choice,
    Keep,
    ModelCall,
    NoModel,
    RouteState,
    Signal,
    allowed,
    choose,
    has_model_above,
    route_for,
)
from lamplighter.ports import ModelProfile

SIGNALS: tuple[Signal, ...] = (
    "invalid_calls",
    "repeated_call",
    "empty_reply",
    "refused",
    "model_failed",
    "too_large",
)


def profile(window: int, stays_on_mac: bool) -> ModelProfile:
    return ModelProfile(
        adapter="openai_chat" if stays_on_mac else "anthropic",
        model="made-up",
        max_output=16_000,
        tool_calls=True,
        cache_hints=0,
        count_error_permille=0,
        window=window,
        concurrent_requests=1,
        stays_on_mac=stays_on_mac,
    )


MODELS = {
    "local": profile(65_536, True),
    "frontier": profile(1_000_000, False),
}
DEFAULT = Route("default", "always", ("local", "frontier"))
PRIVATE = Route("private", "private", ("local", "frontier"))
BACKWARDS = Route("private", "private", ("frontier", "local"))
UP = {"local": True, "frontier": True}


def call(private: bool = False, size: int | None = None) -> ModelCall:
    return ModelCall(private=private, size=size, max_output=16_000)


def state(
    position: int | None = None,
    signal: Signal | None = None,
    health: Mapping[str, bool] = UP,
) -> RouteState:
    return RouteState(position=position, signal=signal, health=health)


# The worked cases of the design, one row each.

WORKED = [
    ((DEFAULT,), call(), state(), Choice("default", "local", 0, "start")),
    (
        (DEFAULT,),
        call(),
        state(health={"local": False, "frontier": True}),
        Choice("default", "frontier", 1, "skipped"),
    ),
    (
        (DEFAULT,),
        call(size=50_000),
        state(),
        Choice("default", "frontier", 1, "skipped"),
    ),
    ((DEFAULT,), call(), state(0), Keep()),
    (
        (DEFAULT,),
        call(),
        state(0, "invalid_calls"),
        Choice("default", "frontier", 1, "invalid_calls"),
    ),
    ((DEFAULT,), call(), state(1, "refused"), Keep()),
    ((PRIVATE, DEFAULT), call(True), state(), Choice("private", "local", 0, "start")),
    ((PRIVATE, DEFAULT), call(True), state(0, "model_failed"), Keep()),
    (
        (PRIVATE, DEFAULT),
        call(True),
        state(health={"local": False, "frontier": True}),
        NoModel("private", ("local",)),
    ),
    (
        (Route("default", "always", ("frontier",)),),
        call(True),
        state(),
        NoModel("default", ()),
    ),
    ((BACKWARDS, DEFAULT), call(True), state(), Choice("private", "local", 1, "start")),
]


@pytest.mark.parametrize(("routes", "asked", "where", "answer"), WORKED)
def test_each_worked_case_gives_its_answer(
    routes: tuple[Route, ...], asked: ModelCall, where: RouteState, answer: object
) -> None:
    assert choose(routes, MODELS, asked, where) == answer


def test_a_name_fits_only_with_room_for_the_whole_reply() -> None:
    just = call(size=65_536 - 16_000)
    assert choose((DEFAULT,), MODELS, just, state()) == Choice(
        "default", "local", 0, "start"
    )
    over = call(size=65_536 - 16_000 + 1)
    assert choose((DEFAULT,), MODELS, over, state()) == Choice(
        "default", "frontier", 1, "skipped"
    )


def test_too_large_moves_only_to_a_larger_window() -> None:
    models = {**MODELS, "other": profile(65_536, True)}
    ladder = Route("default", "always", ("local", "other", "frontier"))
    answer = choose((ladder,), models, call(), state(0, "too_large"))
    assert answer == Choice("default", "frontier", 2, "too_large")
    answer = choose((ladder,), models, call(), state(0, "model_failed"))
    assert answer == Choice("default", "other", 1, "model_failed")


def test_a_model_above_that_is_down_keeps_the_run_where_it_is() -> None:
    down = {"local": True, "frontier": False}
    assert choose((DEFAULT,), MODELS, call(), state(0, "refused", down)) == Keep()


def test_a_name_with_no_health_answer_counts_as_up() -> None:
    assert choose((DEFAULT,), MODELS, call(), state(health={})) == Choice(
        "default", "local", 0, "start"
    )


def test_a_private_call_takes_the_private_route_and_others_the_default() -> None:
    assert route_for((PRIVATE, DEFAULT), True) == PRIVATE
    assert route_for((PRIVATE, DEFAULT), False) == DEFAULT


def test_the_veto_keeps_places_as_written() -> None:
    assert allowed(BACKWARDS, MODELS, True) == ((1, "local"),)
    assert allowed(BACKWARDS, MODELS, False) == ((0, "frontier"), (1, "local"))


def test_a_model_above_is_read_after_the_veto() -> None:
    assert has_model_above((DEFAULT,), MODELS, False, 0)
    assert not has_model_above((DEFAULT,), MODELS, False, 1)
    assert not has_model_above((PRIVATE, DEFAULT), MODELS, True, 0)


# The laws, over random tables and random runs with a fixed seed.

NAMES = ("tiny", "local", "big-local", "frontier", "frontier-two")


def random_models(rng: random.Random) -> dict[str, ModelProfile]:
    return {
        name: profile(
            rng.choice((8_192, 32_768, 65_536, 131_072, 1_000_000)), rng.random() < 0.5
        )
        for name in NAMES
    }


def random_table(rng: random.Random) -> tuple[Route, ...]:
    def ladder() -> tuple[str, ...]:
        return tuple(rng.sample(NAMES, rng.randint(1, len(NAMES))))

    routes: list[Route] = []
    if rng.random() < 0.7:
        routes.append(Route("private", "private", ladder()))
    routes.append(Route("default", "always", ladder()))
    return tuple(routes)


def test_the_laws_hold_over_random_tables_and_runs() -> None:
    rng = random.Random(7)
    checked = 0
    for _ in range(3_000):
        models = random_models(rng)
        routes = random_table(rng)
        private = rng.random() < 0.5
        ladder = allowed(route_for(routes, private), models, private)
        position: int | None = None
        moves = 0
        for _ in range(rng.randint(1, 30)):
            signal = rng.choice((None, *SIGNALS)) if position is not None else None
            health = {n: rng.random() < 0.8 for n in NAMES}
            asked = ModelCall(
                private, rng.choice((None, 1_000, 20_000, 60_000, 500_000)), 16_000
            )
            where = RouteState(position, signal, health)
            answer = choose(routes, models, asked, where)
            # 1. The same inputs give the same answer.
            assert answer == choose(routes, models, asked, where)
            if isinstance(answer, NoModel):
                # 6. Only before a run's first choice.
                assert position is None
                break
            if isinstance(answer, Keep):
                continue
            # 2. A private call never names a model that leaves this Mac.
            if private:
                assert models[answer.model].stays_on_mac
            # 3. A later choice is higher, so it names another model.
            if position is not None:
                assert answer.position > position
                assert answer.model != route_for(routes, private).models[position]
                moves += 1
            # 5. A route of the settings, and a model that is up.
            assert answer.route_id in {r.id for r in routes}
            assert health[answer.model]
            position = answer.position
            checked += 1
        # 4. At most one move fewer than the names left on the ladder.
        assert moves <= max(len(ladder) - 1, 0)
    assert checked > 3_000


def test_the_veto_holds_whatever_the_private_route_lists() -> None:
    only_frontier = Route("private", "private", ("frontier",))
    for where in (state(), state(health={"local": True, "frontier": True})):
        answer = choose((only_frontier, DEFAULT), MODELS, call(True), where)
        assert answer == NoModel("private", ())


def test_a_choice_at_the_top_after_the_veto_is_kept() -> None:
    answer = choose((PRIVATE, DEFAULT), MODELS, call(True), state(0, "too_large"))
    assert answer == Keep()
