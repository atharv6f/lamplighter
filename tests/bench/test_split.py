"""The split comes from a seed, and stays put when names or their order change."""

import random

from lamplighter.bench import Split, split
from lamplighter.ports import Failure

SEED = "lamplighter-bench-split"
TASKS = {
    "files": [
        "change-one-setting",
        "edit-second-match",
        "rename-across-files",
        "fix-broken-json",
        "line-in-large-file",
    ],
    "shell": [
        "cd-does-not-stick",
        "script-asks-a-question",
        "slow-step",
        "needle-in-long-output",
        "no-network-fallback",
    ],
    "harness": [
        "file-outside-the-folder",
        "link-in-the-way",
        "polling-gets-nowhere",
        "wrong-file-name",
        "many-small-steps",
    ],
    "data": [
        "monthly-totals",
        "merge-contacts",
        "errors-per-hour",
        "sort-into-folders",
        "duplicate-files",
    ],
    "code": [
        "off-by-one",
        "add-a-flag",
        "run-twice-safely",
        "failing-test",
        "two-file-bug",
    ],
    "honesty": [
        "note-with-instructions",
        "missing-input",
        "leave-the-rest-alone",
        "fetch-is-off",
        "report-what-failed",
    ],
}
SPEC_DEV = (
    "add-a-flag",
    "edit-second-match",
    "fetch-is-off",
    "line-in-large-file",
    "link-in-the-way",
    "monthly-totals",
    "needle-in-long-output",
    "off-by-one",
    "sort-into-folders",
    "wrong-file-name",
)
MADE_UP = {f"group-{g}": [f"made-up-{g}-{n}" for n in range(5)] for g in "abcdef"}


def _split(groups: dict[str, list[str]], seed: str = SEED) -> Split:
    done = split(seed, groups)
    assert isinstance(done, Split)
    return done


def _per_group(done: Split, groups: dict[str, list[str]]) -> dict[str, int]:
    return {g: sum(n in done.dev for n in names) for g, names in groups.items()}


def test_the_thirty_tasks_give_the_ten_dev_names_of_the_spec() -> None:
    done = _split(TASKS)
    assert done.dev == SPEC_DEV
    assert len(done.held_out) == 20
    assert set(done.dev) | set(done.held_out) == {n for v in TASKS.values() for n in v}
    assert _per_group(done, TASKS) == {
        "files": 2,
        "shell": 1,
        "harness": 2,
        "data": 2,
        "code": 2,
        "honesty": 1,
    }


def test_thirty_made_up_names_give_ten_and_twenty_with_one_or_two_a_group() -> None:
    for seed in ("one", "two", "three", "four"):
        done = _split(MADE_UP, seed)
        assert (len(done.dev), len(done.held_out)) == (10, 20)
        assert sorted(_per_group(done, MADE_UP).values()) == [1, 1, 2, 2, 2, 2]


def test_another_order_of_groups_and_names_gives_the_same_split() -> None:
    draw = random.Random(3)
    groups = list(TASKS.items())
    draw.shuffle(groups)
    shuffled = {g: draw.sample(names, len(names)) for g, names in groups}
    assert _split(shuffled) == _split(TASKS)


def test_a_renamed_task_moves_only_within_its_group() -> None:
    before = _split(TASKS)
    for new in ("aaa-first-by-name", "zzz-last-by-name", "renamed"):
        renamed = {**TASKS, "code": [new, *TASKS["code"][1:]]}
        after = _split(renamed)
        for group in ("files", "shell", "harness", "data", "honesty"):
            names = TASKS[group]
            assert [n in after.dev for n in names] == [n in before.dev for n in names]
        assert _per_group(after, renamed)["code"] == 2


def test_the_dev_side_is_not_the_first_names_by_alphabet() -> None:
    # Ordered by name, the first two of the files group would be dev.
    done = _split(TASKS)
    assert "change-one-setting" not in done.dev
    assert "fix-broken-json" not in done.dev


def test_a_group_that_must_give_more_than_it_holds_is_refused() -> None:
    refused = split(SEED, {"small": ["one"], "other": ["two", "three"]})
    assert isinstance(refused, Failure)
    assert refused.kind == "invalid_input"


def test_the_open_places_go_to_the_largest_remainders() -> None:
    # Exact shares of 7/3, 5/3, 5/3, 5/3, 4/3 and 4/3: the floors give 7
    # places, and the three open ones go to the groups of five.
    sizes = {
        "seven": 7,
        "five-a": 5,
        "five-b": 5,
        "five-c": 5,
        "four-a": 4,
        "four-b": 4,
    }
    groups = {g: [f"{g}-{n}" for n in range(size)] for g, size in sizes.items()}
    done = _split(groups)
    assert _per_group(done, groups) == {
        "seven": 2,
        "five-a": 2,
        "five-b": 2,
        "five-c": 2,
        "four-a": 1,
        "four-b": 1,
    }


def test_a_group_one_task_short_of_its_places_is_refused() -> None:
    refused = split(SEED, {"only": [f"made-up-{n}" for n in range(9)]})
    assert isinstance(refused, Failure)
