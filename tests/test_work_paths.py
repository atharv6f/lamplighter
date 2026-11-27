"""The path rule in text alone, and the walk that follows no link at any depth."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from lamplighter.adapters.builtin_tools.work_paths import (
    Refused,
    WorkFolder,
    make_folders,
    open_existing,
    open_work_folder,
    path_parts,
    read_up_to,
)

ROOT = "/home/you/work"
LEAVES = "The path {} leaves the work folder. File tools work only inside it."


@pytest.mark.parametrize(
    ("path", "parts"),
    [
        ("todo.md", ("todo.md",)),
        ("notes/../todo.md", ("todo.md",)),
        ("./a//b/./c.txt", ("a", "b", "c.txt")),
        ("/home/you/work/todo.md", ("todo.md",)),
        ("/home/you/work", ()),
        (".", ()),
        ("a/../a/b/c.txt", ("a", "b", "c.txt")),
        ("/home/you/work/../work/x", ("x",)),
    ],
)
def test_a_path_inside_becomes_its_parts(path: str, parts: tuple[str, ...]) -> None:
    assert path_parts(path, ROOT) == parts


@pytest.mark.parametrize(
    "path",
    [
        "../x",
        "/etc/passwd",
        "a/../../outside",
        "/home/you/workshop/x",
        "/home/you",
        "/",
        "/home/you/work/../other/x",
    ],
)
def test_a_path_that_leaves_is_refused(path: str) -> None:
    assert path_parts(path, ROOT) == Refused(LEAVES.format(path))


@pytest.mark.parametrize("path", ["a\0b", "a\ud800b"])
def test_a_path_that_is_not_text_a_drive_takes_is_refused(path: str) -> None:
    assert path_parts(path, ROOT) == Refused("That is not a valid path.")


@pytest.fixture
def work(tmp_path: Path) -> Path:
    made = tmp_path / "work"
    made.mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "id_rsa").write_text("decoy")
    return made.resolve()


@pytest.fixture
def folder(work: Path) -> WorkFolder:
    return open_work_folder(str(work))


def opened(folder: WorkFolder, path: str) -> bytes | None | Refused:
    parts = path_parts(path, folder.root)
    assert not isinstance(parts, Refused), parts
    found = open_existing(folder, parts, path)
    return read_up_to(found, 100) if isinstance(found, int) else found


def opened_soon(folder: WorkFolder, pipe: Path) -> bytes | None | Refused:
    # An open of a named pipe that waits for a writer would wait for ever.
    # After a second the test opens the other end, so it fails and does not hang.
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(opened, folder, pipe.name)
        try:
            return result.result(timeout=1)
        except TimeoutError:
            os.close(os.open(pipe, os.O_WRONLY | os.O_NONBLOCK))
            raise AssertionError("the open waited for a writer") from None


def link_text(path: str) -> Refused:
    return Refused(
        f"The path {path} goes through a symbolic link, which this tool does"
        " not follow."
    )


def test_a_file_is_read_by_its_relative_and_its_absolute_path(
    folder: WorkFolder, work: Path
) -> None:
    (work / "a" / "b").mkdir(parents=True)
    (work / "a" / "b" / "c.txt").write_text("found")
    assert opened(folder, "a/b/c.txt") == b"found"
    assert opened(folder, str(work / "a" / "b" / "c.txt")) == b"found"


def test_a_missing_file_or_folder_is_none(folder: WorkFolder, work: Path) -> None:
    (work / "a").mkdir()
    assert opened(folder, "a/none.txt") is None
    assert opened(folder, "none/none.txt") is None


def test_a_link_at_the_end_is_refused_wherever_it_points(
    folder: WorkFolder, work: Path
) -> None:
    (work / "inside.txt").write_text("inside")
    (work / "to-decoy").symlink_to(work.parent / "outside" / "id_rsa")
    (work / "to-inside").symlink_to(work / "inside.txt")
    (work / "dangling").symlink_to(work / "none")
    for path in ("to-decoy", "to-inside", "dangling"):
        assert opened(folder, path) == link_text(path)


def test_a_link_to_a_folder_on_the_way_is_refused(
    folder: WorkFolder, work: Path
) -> None:
    (work / "deep").mkdir()
    (work / "deep" / "out").symlink_to(work.parent / "outside")
    assert opened(folder, "deep/out/id_rsa") == link_text("deep/out/id_rsa")


def test_a_folder_a_pipe_and_a_file_used_as_a_folder_are_refused(
    folder: WorkFolder, work: Path
) -> None:
    (work / "d").mkdir()
    os.mkfifo(work / "pipe")
    (work / "plain").write_text("x")
    assert opened(folder, "d") == Refused("d is a folder, not a file.")
    assert opened(folder, ".") == Refused(". is a folder, not a file.")
    assert opened_soon(folder, work / "pipe") == Refused("pipe is not a regular file.")
    assert opened(folder, "plain/x") == Refused(
        "plain/x could not be opened: Not a directory."
    )


def test_folders_are_made_with_their_mode_and_no_link_is_followed(
    folder: WorkFolder, work: Path
) -> None:
    made = make_folders(folder, ("a", "b"), "a/b/c")
    assert isinstance(made, int)
    os.close(made)
    assert (work / "a" / "b").is_dir()
    assert (work / "a" / "b").stat().st_mode & 0o777 == 0o755 & ~_umask()
    (work / "out").symlink_to(work.parent / "outside")
    assert make_folders(folder, ("out", "new"), "out/new/c") == link_text("out/new/c")
    assert not (work.parent / "outside" / "new").exists()


def test_the_walk_starts_from_the_folder_opened_first(
    folder: WorkFolder, work: Path, tmp_path: Path
) -> None:
    # A work folder moved away and replaced by a link is still reached by
    # its handle, and the link is never followed.
    (work / "note.txt").write_text("the real one")
    work.rename(tmp_path / "moved")
    work.symlink_to(tmp_path / "outside")
    assert opened(folder, "note.txt") == b"the real one"
    assert opened(folder, "id_rsa") is None


def test_a_read_stops_one_byte_past_the_most(work: Path, folder: WorkFolder) -> None:
    (work / "big").write_bytes(b"x" * 50)
    parts = path_parts("big", folder.root)
    assert not isinstance(parts, Refused)
    found = open_existing(folder, parts, "big")
    assert isinstance(found, int)
    assert read_up_to(found, 10) == b"x" * 11


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask
