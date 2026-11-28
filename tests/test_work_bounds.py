"""The bounds on what the agent left in its work folder, each at its edge."""

import os
from collections.abc import Callable
from pathlib import Path

from lamplighter.app.work_bounds import (
    MOST_DEPTH,
    MOST_ENTRIES,
    MOST_STATED_BYTES,
    out_of_bounds,
)
from lamplighter.bench import BoundWord

# A user with no right over a folder that is not its own, for a test run as
# root, whom no folder's mode stops.
_NOBODY = 65534


def stated(folder: Path, name: str, size: int) -> None:
    with (folder / name).open("wb") as made:
        made.truncate(size)


def nested(folder: Path, depth: int) -> None:
    place = folder
    for _ in range(depth):
        place = place / "d"
    place.mkdir(parents=True)


def test_an_ordinary_folder_is_inside_every_bound(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.txt").write_text("made up\n")
    (tmp_path / "link").symlink_to("/")
    assert out_of_bounds(tmp_path) is None


def test_stated_sizes_are_added_up_to_the_byte(tmp_path: Path) -> None:
    stated(tmp_path, "half", MOST_STATED_BYTES // 2)
    stated(tmp_path, "rest", MOST_STATED_BYTES - MOST_STATED_BYTES // 2)
    assert out_of_bounds(tmp_path) is None
    stated(tmp_path, "one", 1)
    assert out_of_bounds(tmp_path) == "too_large"


def test_files_folders_and_links_are_counted_together(tmp_path: Path) -> None:
    (tmp_path / "folder").mkdir()
    (tmp_path / "link").symlink_to("folder")
    for n in range(MOST_ENTRIES - 2):
        (tmp_path / "folder" / str(n)).touch()
    assert out_of_bounds(tmp_path) is None
    (tmp_path / "one more").touch()
    assert out_of_bounds(tmp_path) == "too_many_entries"


def test_folders_are_counted_to_their_depth(tmp_path: Path) -> None:
    nested(tmp_path / "ok", MOST_DEPTH - 1)
    assert out_of_bounds(tmp_path) is None
    nested(tmp_path / "deep", MOST_DEPTH)
    assert out_of_bounds(tmp_path) == "too_deep"


def test_a_link_is_never_followed(tmp_path: Path) -> None:
    stated(tmp_path, "large", MOST_STATED_BYTES)
    inside = tmp_path / "inside"
    inside.mkdir()
    (inside / "again").symlink_to(tmp_path)
    assert out_of_bounds(inside) is None


def as_someone_else(check: Callable[[], BoundWord | None], folder: Path) -> str:
    if os.getuid() != 0:
        return str(check())
    for place in (folder, *folder.parents):
        os.chmod(place, os.stat(place).st_mode | 0o711)
    read, write = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read)
        os.setgid(_NOBODY)
        os.setuid(_NOBODY)
        os.write(write, str(check()).encode())
        os._exit(0)
    os.close(write)
    os.waitpid(child, 0)
    with os.fdopen(read) as answer:
        return answer.read()


def test_a_folder_that_may_not_be_listed_is_unreadable(tmp_path: Path) -> None:
    hidden = tmp_path / "work" / "hidden"
    hidden.mkdir(parents=True)
    stated(hidden, "large", MOST_STATED_BYTES + 1)
    hidden.chmod(0o000)
    try:
        found = as_someone_else(lambda: out_of_bounds(tmp_path / "work"), tmp_path)
    finally:
        hidden.chmod(0o700)
    assert found == "unreadable"
