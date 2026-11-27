"""A kill at every port call of a chat whose tools change the world, then a restart.

The method is the same as for the chats that only read. On top of those
rules, each world is checked for a change that happened twice, a row that
says something the world does not, and a row left open.
"""

from collections import Counter as Tally

import pytest

from tests.change_scenarios import (
    CHANGES,
    MANY_CHANGES,
    ONE_PUT,
    SETTINGS_CHANGE_WITH_CHANGES,
)
from tests.kill_kit import (
    Point,
    Scenario,
    World,
    ended,
    killed_once,
    killed_twice,
    new_world,
    points,
    run_process,
    what_is_wrong,
)
from tests.kill_scenarios import ALL
from tests.port_counter import STORES

ENDS = {
    MANY_CHANGES.name: ["done"],
    "a stop arrives while a put runs": ["cancelled"],
    "a stop arrives while a put that ignores a cancel runs": ["cancelled"],
    "a message arrives while a put runs": ["done"],
    "the time limit cuts a put": ["limit"],
    SETTINGS_CHANGE_WITH_CHANGES.name: ["done"],
    "a message, a stop and a message, with changes": ["cancelled", "done"],
    "an output over the cap, an outside text, and arguments that do not fit": ["done"],
    "the step limit, with changes": ["limit"],
    "a stop on the first of four calls": ["cancelled"],
    "a stop on a read between two changes": ["cancelled"],
    "the time limit on a put that ignores a cancel": ["limit"],
    "the same refused change, again and again": ["failed"],
    "the same change, again and again": ["failed"],
    "a message arrives while a put that ignores a cancel runs": ["done"],
    "the settings change at the restart, with a stop waiting": ["cancelled"],
    "the step limit, with two changes in each reply": ["limit"],
}


def unkilled(scenario: Scenario) -> tuple[World, int]:
    world = new_world(scenario)
    calls = run_process(world, scenario).calls
    return world, calls


def states(world: World) -> Tally[str]:
    return Tally(row.state for row in world.rows.values())


@pytest.mark.parametrize("scenario", CHANGES, ids=[s.name for s in CHANGES])
def test_the_chat_with_no_kill_ends_as_its_name_says(scenario: Scenario) -> None:
    world, calls = unkilled(scenario)
    assert what_is_wrong(world, scenario) == []
    assert ended(world).run_ends == ENDS[scenario.name]
    assert calls > 10


@pytest.mark.parametrize("scenario", CHANGES, ids=[s.name for s in CHANGES])
def test_a_kill_at_any_port_call_leaves_the_world_and_the_stores_whole(
    scenario: Scenario,
) -> None:
    _, calls = unkilled(scenario)
    wrong: list[str] = []
    for point in points(calls):
        world = killed_once(scenario, point)
        wrong += [f"{point}: {rule}" for rule in what_is_wrong(world, scenario)]
    assert wrong == []


def test_the_kills_leave_rows_in_every_state_but_undone() -> None:
    world, _ = unkilled(MANY_CHANGES)
    assert set(states(world)) == {"done", "resolved"}
    seen: Tally[str] = Tally()
    for scenario in (MANY_CHANGES, SETTINGS_CHANGE_WITH_CHANGES):
        _, calls = unkilled(scenario)
        for point in points(calls):
            seen += states(killed_once(scenario, point))
    assert set(seen) == {"done", "failed", "resolved", "unknown"}


def test_a_kill_after_an_intent_never_applies_the_change_again() -> None:
    _, calls = unkilled(MANY_CHANGES)
    settled = 0
    for point in points(calls):
        world = killed_once(MANY_CHANGES, point)
        starts = Tally(world.made.started)
        assert all(count == 1 for key, count in starts.items() if key[0] != "note")
        settled += any(
            row.result is not None and row.result.output.get("reason") == "interrupted"
            for row in world.rows.values()
        )
    assert settled > 0


def by_hand_after_a_kill(point: Point, text: str) -> tuple[World, int]:
    """Kill the chat at `point`, put `text` into tea by hand, then run it again.

    Also give how many changes the tools had made before the restart.
    """
    world = new_world(ONE_PUT)
    run_process(world, ONE_PUT, point)
    world.made.texts["tea"] = text
    before = len(world.made.changed)
    run_process(world, ONE_PUT)
    return world, before


def test_the_same_text_put_by_hand_leaves_no_row_open() -> None:
    _, calls = unkilled(ONE_PUT)
    refused_with_a_row = 0
    for point in points(calls):
        world, _ = by_hand_after_a_kill(point, "mint")
        assert all(row.state != "intended" for row in world.rows.values()), point
        refused_with_a_row += any(
            row.state == "failed"
            and row.result is not None
            and row.result.output.get("text") == "tea already holds this text."
            for row in world.rows.values()
        )
    # A kill after `begin` and before the intent leaves a row that the new
    # plan, which finds nothing to change, must close.
    assert refused_with_a_row > 0


def test_another_text_put_by_hand_is_never_written_over_unless_the_undo_holds_it() -> (
    None
):
    _, calls = unkilled(ONE_PUT)
    written_after = 0
    for point in points(calls):
        world, before = by_hand_after_a_kill(point, "black")
        after = world.made.changed[before:]
        if after:
            written_after += 1
            (row,) = world.rows.values()
            assert row.undo is not None and row.undo["text"] == "black", point
        else:
            assert world.made.texts["tea"] == "black", point
    assert written_after > 0


@pytest.mark.crash
@pytest.mark.parametrize("scenario", CHANGES, ids=[s.name for s in CHANGES])
def test_a_second_kill_during_the_recovery_leaves_the_world_whole(
    scenario: Scenario,
) -> None:
    _, calls = unkilled(scenario)
    wrong: list[str] = []
    for first in points(calls):
        world = new_world(scenario)
        run_process(world, scenario, first)
        recovery = run_process(world, scenario).calls
        for second in points(recovery):
            twice = killed_twice(scenario, first, second)
            wrong += [
                f"{first} then {second}: {rule}"
                for rule in what_is_wrong(twice, scenario)
            ]
    assert wrong == []


@pytest.mark.parametrize("scenario", CHANGES + ALL, ids=lambda s: s.name)
def test_a_store_failure_at_any_store_call_then_a_new_drive_keeps_the_rules(
    scenario: Scenario,
) -> None:
    counted = run_process(new_world(scenario), scenario)
    wrong: list[str] = []
    store_calls = [
        number
        for number, name in enumerate(counted.names, start=1)
        if name.startswith(STORES)
    ]
    for number in store_calls:
        world = new_world(scenario)
        run_process(world, scenario, fail_at=number)
        wrong += [f"{number}: {rule}" for rule in what_is_wrong(world, scenario)]
    assert len(store_calls) > 10
    assert wrong == []
