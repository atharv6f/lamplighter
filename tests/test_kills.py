"""A kill just before and just after every port call of a chat, then a restart.

For each scenario the run with no kill is counted first. Then, for each
point, a first process is killed there, a second runs to the end on the
same stores, and every rule about the stores is checked. With the `crash`
marker, the second process is also killed at each of its points.
"""

import pytest

from tests.kill_kit import (
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
from tests.kill_scenarios import ALL, SETTINGS_CHANGE, STOP_FIRST

ENDS = {
    "three steps with two tools and a tool that does not exist": ["done"],
    "a message, a stop and another message, all waiting at the start": [
        "cancelled",
        "done",
    ],
    "a stop that arrives before any message": ["done"],
    "a message arrives while a tool runs": ["done"],
    "a stop arrives while a tool runs": ["cancelled"],
    "the step limit": ["limit"],
    "the time limit cuts a tool": ["limit"],
    "the same call, repeated": ["failed"],
    "the settings change at the restart": ["done"],
    "two bad replies": ["failed"],
    "an empty reply, then text": ["done"],
    # Both stops are waiting when the first run closes, so it uses up both.
    "message, message, stop, message, stop": ["cancelled", "done"],
}


def unkilled(scenario: Scenario) -> tuple[World, int]:
    world = new_world(scenario)
    calls = run_process(world, scenario).calls
    return world, calls


def run_ends(world: World) -> list[str]:
    return ended(world).run_ends


@pytest.mark.parametrize("scenario", ALL, ids=[s.name for s in ALL])
def test_the_chat_with_no_kill_ends_as_its_name_says(scenario: Scenario) -> None:
    world, calls = unkilled(scenario)
    assert what_is_wrong(world, scenario) == []
    assert run_ends(world) == ENDS[scenario.name]
    assert calls > 10


@pytest.mark.parametrize("scenario", ALL, ids=[s.name for s in ALL])
def test_a_kill_at_any_port_call_leaves_the_stores_whole(scenario: Scenario) -> None:
    _, calls = unkilled(scenario)
    wrong: list[str] = []
    for point in points(calls):
        world = killed_once(scenario, point)
        wrong += [f"{point}: {rule}" for rule in what_is_wrong(world, scenario)]
    assert wrong == []


def test_a_run_open_under_other_settings_after_a_kill_is_ended() -> None:
    _, calls = unkilled(SETTINGS_CHANGE)
    changed = 0
    for point in points(calls):
        ends = run_ends(killed_once(SETTINGS_CHANGE, point))
        if ends == ["failed"]:
            changed += 1
    assert changed > 0


@pytest.mark.crash
@pytest.mark.parametrize(
    "scenario",
    [s for s in ALL if s not in (SETTINGS_CHANGE, STOP_FIRST)],
    ids=lambda s: s.name,
)
def test_a_second_kill_during_the_recovery_leaves_the_stores_whole(
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
