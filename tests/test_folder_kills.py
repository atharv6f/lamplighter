"""A kill at every port call of a chat on the built-in tools in a real folder.

The method is the one for the made-up world. On top of its rules, the
folder must hold what the ledger says happened, nothing outside it may
change, and no unfinished file may be left.
"""

import hashlib
from collections import Counter as Tally

import pytest

from tests.folder_scenarios import IN_A_FOLDER, MANY_CHANGES, write
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
from tests.kill_scenarios import calls, text
from tests.port_counter import STORES
from tests.run_history import inbound

ENDS = {
    MANY_CHANGES.name: ["done"],
    "a stop arrives while a write runs": ["cancelled"],
    "a stop arrives while a long command runs": ["cancelled"],
    "a message arrives while a write runs": ["done"],
    "the time limit cuts a long command": ["limit"],
    "the settings change at the restart, with file changes open": ["done"],
    "a message, a stop and a message, with file changes": ["cancelled", "done"],
    "a command's output over the cap, a page, and arguments that do not fit": ["done"],
    "the step limit, with file changes": ["limit"],
}

ONE_WRITE = Scenario(
    "one write over a file that is there",
    waiting=(inbound("m1", "Cool the pot."),),
    tools="folder",
    clock="still",
    script={"Cool the pot.": (calls(write("c1", "pot.txt", "cold\n")), text("Done."))},
)


def unkilled(scenario: Scenario) -> tuple[World, int]:
    world = new_world(scenario)
    calls = run_process(world, scenario).calls
    return world, calls


def states(world: World) -> Tally[str]:
    return Tally(row.state for row in world.rows.values())


@pytest.mark.parametrize("scenario", IN_A_FOLDER, ids=lambda s: s.name)
def test_the_chat_with_no_kill_ends_as_its_name_says(scenario: Scenario) -> None:
    world, calls = unkilled(scenario)
    assert what_is_wrong(world, scenario) == []
    assert ended(world).run_ends == ENDS[scenario.name]
    assert world.changes
    assert calls > 10


@pytest.mark.parametrize("scenario", IN_A_FOLDER, ids=lambda s: s.name)
def test_a_kill_at_any_port_call_leaves_the_folder_and_the_stores_whole(
    scenario: Scenario,
) -> None:
    _, calls = unkilled(scenario)
    wrong: list[str] = []
    for point in points(calls):
        world = killed_once(scenario, point)
        wrong += [f"{point}: {rule}" for rule in what_is_wrong(world, scenario)]
    assert wrong == []


def test_the_kills_leave_rows_in_every_state_but_undone() -> None:
    seen: Tally[str] = Tally()
    unknown: Tally[str] = Tally()
    for scenario in IN_A_FOLDER:
        _, calls = unkilled(scenario)
        for point in points(calls):
            world = killed_once(scenario, point)
            seen += states(world)
            unknown += Tally(
                row.action.tool for row in world.rows.values() if row.state == "unknown"
            )
    assert set(seen) == {"done", "failed", "resolved", "unknown"}
    # A file change can always be checked, so only a command stays unknown.
    assert set(unknown) == {"shell"}


def test_a_kill_after_an_intent_never_applies_a_change_again() -> None:
    _, calls = unkilled(MANY_CHANGES)
    settled = 0
    for point in points(calls):
        world = killed_once(MANY_CHANGES, point)
        assert world.folder is not None
        starts = Tally(world.folder.started)
        assert all(
            count == 1 for key, count in starts.items() if key[0] != "read_file"
        ), point
        settled += any(row.state == "resolved" for row in world.rows.values())
    assert settled > 0


def by_hand_after_a_kill(point: Point, text: str) -> tuple[World, int]:
    """Kill the chat at `point`, write `text` into the pot by hand, run it again.

    Also give how many changes the tools had made before the restart.
    """
    world = new_world(ONE_WRITE)
    assert world.folder is not None
    run_process(world, ONE_WRITE, point)
    (world.folder.work / "pot.txt").write_text(text)
    before = len(world.folder.changed)
    run_process(world, ONE_WRITE)
    return world, before


def test_a_file_changed_by_hand_is_never_written_over_unless_the_undo_holds_it() -> (
    None
):
    _, calls = unkilled(ONE_WRITE)
    written_after = 0
    for point in points(calls):
        world, before = by_hand_after_a_kill(point, "warm\n")
        assert world.folder is not None
        if world.folder.changed[before:]:
            written_after += 1
            (row,) = world.rows.values()
            assert row.undo is not None, point
            blob = row.undo.get("blob")
            assert isinstance(blob, dict), point
            assert blob["sha256"] == hashlib.sha256(b"warm\n").hexdigest(), point
        else:
            assert (world.folder.work / "pot.txt").read_text() == "warm\n", point
        assert all(row.state != "intended" for row in world.rows.values()), point
    assert written_after > 0


@pytest.mark.parametrize("scenario", IN_A_FOLDER, ids=lambda s: s.name)
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


@pytest.mark.crash
@pytest.mark.parametrize("scenario", IN_A_FOLDER, ids=lambda s: s.name)
def test_a_second_kill_during_the_recovery_leaves_the_folder_whole(
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
