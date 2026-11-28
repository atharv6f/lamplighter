"""The task files are what git lists, read as they are, with links never followed."""

import os
from pathlib import Path

from lamplighter.app.task_files import ignored_task_files, task_files
from lamplighter.bench import TaskFile
from lamplighter.ports import Failure
from tests.bench_kit import git


def _checkout(root: Path) -> Path:
    task = root / "bench" / "tasks" / "made-up"
    task.mkdir(parents=True)
    (task / "plain.txt").write_text("plain\n")
    (task / "run.sh").write_text("#!/bin/sh\n")
    os.chmod(task / "run.sh", 0o755)
    (task / "gone.txt").write_text("gone\n")
    (root / "bench" / "split.toml").write_text('seed = "s"\n')
    (root / "other.txt").write_text("not a task file\n")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Made up")
    return root


def _by_path(found: list[TaskFile] | Failure) -> dict[str, TaskFile]:
    assert isinstance(found, list)
    return {f.path: f for f in found}


def test_tracked_and_new_files_are_listed_with_their_bytes_and_bit(
    tmp_path: Path,
) -> None:
    root = _checkout(tmp_path)
    (root / "bench" / "tasks" / "made-up" / "new.txt").write_text("new\n")
    found = _by_path(task_files(root))
    assert sorted(found) == [
        "bench/split.toml",
        "bench/tasks/made-up/gone.txt",
        "bench/tasks/made-up/new.txt",
        "bench/tasks/made-up/plain.txt",
        "bench/tasks/made-up/run.sh",
    ]
    assert found["bench/tasks/made-up/plain.txt"] == TaskFile(
        "bench/tasks/made-up/plain.txt", b"plain\n", executable=False, link=False
    )
    assert found["bench/tasks/made-up/run.sh"].executable


def test_a_link_is_read_as_its_target_and_never_followed(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    os.symlink("../../../other.txt", root / "bench" / "tasks" / "made-up" / "link")
    link = _by_path(task_files(root))["bench/tasks/made-up/link"]
    assert link == TaskFile(
        "bench/tasks/made-up/link", b"../../../other.txt", executable=False, link=True
    )


def test_a_deleted_tracked_file_is_left_out(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    (root / "bench" / "tasks" / "made-up" / "gone.txt").unlink()
    assert "bench/tasks/made-up/gone.txt" not in _by_path(task_files(root))


def test_an_ignored_file_is_not_listed_and_is_named_as_ignored(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    (root / ".gitignore").write_text("*.db\n")
    (root / "bench" / "tasks" / "made-up" / "data.db").write_text("")
    assert "bench/tasks/made-up/data.db" not in _by_path(task_files(root))
    assert ignored_task_files(root) == ["bench/tasks/made-up/data.db"]


def test_a_folder_that_is_no_checkout_is_unavailable(tmp_path: Path) -> None:
    found = task_files(tmp_path)
    assert isinstance(found, Failure) and found.kind == "unavailable"
