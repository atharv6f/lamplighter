"""The projector on the log of every chat the kill tests run, in every trouble.

Each log is projected with a kill after each export, a failure of each
export, a second projector inside each export, a reset at each position
and inside each export, a cursor that cannot be read at each pass, a start
at each position with the sink down, and a sink that is down and back.
The cheaper checks run on every kill log through the kill kit itself.
"""

import pytest

from tests.change_scenarios import CHANGES
from tests.folder_scenarios import IN_A_FOLDER
from tests.kill_kit import Scenario, ended, killed_once, new_world, points, run_process
from tests.kill_scenarios import ALL
from tests.projection_checks import slow_wrong

CHATS = (*ALL, *CHANGES, *IN_A_FOLDER)


@pytest.mark.parametrize("scenario", CHATS, ids=[s.name for s in CHATS])
def test_the_log_of_a_chat_with_no_kill_projects_whole_in_every_trouble(
    scenario: Scenario,
) -> None:
    world = new_world(scenario)
    run_process(world, scenario)

    assert slow_wrong(ended(world).events) == []


@pytest.mark.crash
@pytest.mark.parametrize("scenario", CHATS, ids=[s.name for s in CHATS])
def test_the_log_of_each_kill_projects_whole_in_every_trouble(
    scenario: Scenario,
) -> None:
    world = new_world(scenario)
    calls = run_process(world, scenario).calls
    wrong: list[str] = []
    for point in points(calls):
        events = ended(killed_once(scenario, point)).events
        wrong += [f"{point}: {rule}" for rule in slow_wrong(events)]

    assert wrong == []
