"""The task set hash changes with every byte, mode, file and link target."""

import dataclasses
import hashlib
import json

import pytest

from lamplighter.bench import TaskFile, task_set_hash

FILES = [
    TaskFile("bench/split.toml", b'seed = "made-up"\n', executable=False, link=False),
    TaskFile(
        "bench/tasks/a/tests/test.sh", b"#!/bin/bash\n", executable=True, link=False
    ),
    TaskFile(
        "bench/tasks/a/latest.txt", b"/opt/made-up.txt", executable=False, link=True
    ),
]
BASE = task_set_hash(FILES)


def test_the_hash_is_of_one_entry_per_file_ordered_by_path() -> None:
    # Worked out here from the spec's words, with the standard library.
    entries = [
        {
            "executable": f.executable,
            "link": f.link,
            "path": f.path,
            "sha256": hashlib.sha256(f.data).hexdigest(),
        }
        for f in sorted(FILES, key=lambda f: f.path)
    ]
    text = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == BASE


def test_the_order_given_does_not_change_it() -> None:
    assert task_set_hash(reversed(FILES)) == BASE


@pytest.mark.parametrize(
    "change",
    [
        {"data": b"#!/bin/bash \n"},
        {"executable": False},
        {"path": "bench/tasks/a/tests/check.sh"},
    ],
)
def test_a_changed_byte_mode_or_name_gives_a_new_hash(
    change: dict[str, object],
) -> None:
    # The field names and values are the record's own, so replace can take them.
    changed = [FILES[0], dataclasses.replace(FILES[1], **change), FILES[2]]  # pyright: ignore[reportArgumentType]
    assert task_set_hash(changed) != BASE


def test_a_new_file_gives_a_new_hash() -> None:
    new = TaskFile(
        "bench/tasks/a/tests/expected.txt", b"", executable=False, link=False
    )
    assert task_set_hash([*FILES, new]) != BASE


def test_a_link_with_another_target_gives_a_new_hash() -> None:
    moved = dataclasses.replace(FILES[2], data=b"/opt/other.txt")
    assert task_set_hash([*FILES[:2], moved]) != BASE


def test_a_link_and_a_file_holding_its_target_are_not_the_same() -> None:
    as_file = dataclasses.replace(FILES[2], link=False)
    assert task_set_hash([*FILES[:2], as_file]) != BASE


def test_a_link_is_never_entered_as_executable() -> None:
    marked = dataclasses.replace(FILES[2], executable=True)
    assert task_set_hash([*FILES[:2], marked]) == BASE
