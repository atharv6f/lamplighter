"""write_file: the plan, the replace beside the target, its refusals and the check."""

import asyncio
import os
import stat
import threading
from pathlib import Path

import pytest

from lamplighter.adapters.builtin_tools.file_change import check_hash
from lamplighter.ports import Failure, Plan, ToolResult
from tests.builtin_kit import changed, made, planned, refused, tools_on

ONE = "baf2447e02f61f19e982d04232e322652b131ea72cc392a1a6b8ca61a12809d4"
TWO = "0efaeb62312d12c09164f2de757cfafe71d96c783c0262c582cd1cff96239fce"
ONE_BLOB = "2c8b08da5ce60398e1f19af0e5dccc744df274b826abe585eaba68c525434806"


@pytest.fixture
def work(tmp_path: Path) -> Path:
    made_folder = tmp_path / "work"
    made_folder.mkdir()
    return made_folder.resolve()


def done(text: str) -> ToolResult:
    return ToolResult(text, False, "done")


def error(text: str) -> ToolResult:
    return ToolResult(text, True, "none")


def test_the_check_hash_has_five_bytes_in_front() -> None:
    assert check_hash(b"one\n") == ONE
    assert check_hash(None) is None


def test_a_new_file_is_made_with_its_folders(work: Path) -> None:
    tools = tools_on(work)
    result = made(tools, "write_file", path="notes/deep/todo.md", content="two\n")
    assert result == done("Created notes/deep/todo.md with 4 bytes.")
    assert (work / "notes" / "deep" / "todo.md").read_text() == "two\n"
    assert stat.S_IMODE((work / "notes" / "deep" / "todo.md").stat().st_mode) == 0o644
    assert sorted(os.listdir(work / "notes" / "deep")) == ["todo.md"]


def test_an_existing_file_is_replaced_and_keeps_its_mode(work: Path) -> None:
    (work / "run.sh").write_text("one\n")
    (work / "run.sh").chmod(0o755)
    result = made(tools_on(work), "write_file", path="run.sh", content="two\n")
    assert result == done("Wrote 4 bytes to run.sh.")
    assert (work / "run.sh").read_text() == "two\n"
    assert stat.S_IMODE((work / "run.sh").stat().st_mode) == 0o755


def test_the_plan_names_its_target_probe_and_recipe(work: Path) -> None:
    (work / "notes").mkdir()
    (work / "notes" / "todo.md").write_text("one\n")
    tools = tools_on(work)
    plan = planned(
        tools, "write_file", path="./notes/../notes/todo.md", content="two\n"
    )
    root = str(work)
    assert plan == Plan(
        target="notes/todo.md",
        probe={
            "kind": "file_hash",
            "v": 1,
            "root": root,
            "path": "notes/todo.md",
            "before": ONE,
            "after": TWO,
        },
        undo={
            "kind": "restore_file",
            "v": 1,
            "root": root,
            "path": "notes/todo.md",
            "blob": {
                "sha256": ONE_BLOB,
                "size": 4,
                "media_type": "application/octet-stream",
            },
        },
    )
    assert (work / "notes" / "todo.md").read_text() == "one\n"
    new = planned(tools, "write_file", path="new.md", content="two\n")
    assert new.undo == {"kind": "remove_file", "v": 1, "root": root, "path": "new.md"}
    assert new.probe is not None and new.probe["before"] is None
    assert not (work / "new.md").exists()


def test_the_same_content_again_changes_nothing_and_has_no_plan(work: Path) -> None:
    (work / "a.txt").write_text("one\n")
    assert refused(tools_on(work), "write_file", path="a.txt", content="one\n") == (
        ToolResult(
            "a.txt already holds exactly this content. Nothing was changed.",
            False,
            "none",
        )
    )


def test_a_file_changed_after_the_plan_is_not_written(work: Path) -> None:
    (work / "a.txt").write_text("one\n")
    tools = tools_on(work)
    plan = planned(tools, "write_file", path="a.txt", content="two\n")
    (work / "a.txt").write_text("changed by hand\n")
    assert changed(tools, "write_file", plan, path="a.txt", content="two\n") == error(
        "The file a.txt changed since the plan was made. Nothing was written. Read"
        " it again and repeat the call."
    )
    assert (work / "a.txt").read_text() == "changed by hand\n"


def test_a_file_made_after_the_plan_of_a_new_one_is_not_written(work: Path) -> None:
    tools = tools_on(work)
    plan = planned(tools, "write_file", path="a.txt", content="two\n")
    (work / "a.txt").write_text("made by hand\n")
    result = changed(tools, "write_file", plan, path="a.txt", content="two\n")
    assert isinstance(result, ToolResult) and result.is_error
    assert (work / "a.txt").read_text() == "made by hand\n"


def test_a_plan_from_another_work_folder_writes_nothing(
    work: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    plan = planned(tools_on(other.resolve()), "write_file", path="a.txt", content="x")
    result = changed(tools_on(work), "write_file", plan, path="a.txt", content="x")
    assert result == error(
        "The plan for a.txt was made in another work folder. Nothing was written."
        " Read the file again and repeat the call."
    )
    assert os.listdir(work) == []


def test_a_leftover_from_a_kill_is_removed_first(work: Path) -> None:
    (work / "a.txt").write_text("one\n")
    (work / "a.txt.lamplighter-new").write_text("half a write")
    assert made(tools_on(work), "write_file", path="a.txt", content="two\n") == done(
        "Wrote 4 bytes to a.txt."
    )
    assert sorted(os.listdir(work)) == ["a.txt"]


@pytest.mark.parametrize("kind", ["link", "hard link"])
def test_a_link_under_the_new_name_is_removed_and_not_written_through(
    work: Path, tmp_path: Path, kind: str
) -> None:
    decoy = tmp_path / "decoy"
    decoy.write_text("keep me")
    beside = work / "a.txt.lamplighter-new"
    if kind == "link":
        beside.symlink_to(decoy)
    else:
        os.link(decoy, beside)
    assert made(tools_on(work), "write_file", path="a.txt", content="two\n") == done(
        "Created a.txt with 4 bytes."
    )
    assert decoy.read_text() == "keep me"
    assert sorted(os.listdir(work)) == ["a.txt"]


@pytest.mark.parametrize("kind", ["link", "hard link"])
def test_a_link_planted_after_the_removal_makes_the_write_fail(
    work: Path, tmp_path: Path, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A process still alive in the work folder can guess the fixed name and
    # plant a link between the removal and the create.
    decoy = tmp_path / "decoy"
    decoy.write_text("keep me")
    real_unlink = os.unlink

    def unlink_and_plant(name: str, *, dir_fd: int) -> None:
        real_unlink(name, dir_fd=dir_fd)
        if kind == "link":
            os.symlink(decoy, name, dir_fd=dir_fd)
        else:
            os.link(decoy, name, dst_dir_fd=dir_fd)

    (work / "a.txt.lamplighter-new").write_text("leftover")
    tools = tools_on(work)
    plan = planned(tools, "write_file", path="a.txt", content="two\n")
    monkeypatch.setattr(os, "unlink", unlink_and_plant)
    result = changed(tools, "write_file", plan, path="a.txt", content="two\n")
    assert result == error("a.txt could not be written: File exists.")
    assert decoy.read_text() == "keep me"
    assert not (work / "a.txt").exists()


def test_a_folder_under_the_new_name_makes_the_write_fail(work: Path) -> None:
    (work / "a.txt.lamplighter-new").mkdir()
    result = made(tools_on(work), "write_file", path="a.txt", content="two\n")
    assert isinstance(result, ToolResult)
    assert result.is_error and result.effect == "none"
    assert result.text.startswith("a.txt could not be written: ")
    assert not (work / "a.txt").exists()


def test_names_this_tool_keeps_or_cannot_write_beside_are_refused(work: Path) -> None:
    tools = tools_on(work)
    for name in ("a.lamplighter-new", "A.LampLighter-New"):
        assert refused(tools, "write_file", path=name, content="x") == error(
            f"{name} ends in .lamplighter-new, a name this tool keeps for its own"
            " unfinished writes. Choose another name."
        )
    assert refused(tools, "write_file", path="d/" + "n" * 240, content="x") == error(
        f"The file name in d/{'n' * 240} is 240 bytes long. This tool writes names of"
        " up to 239 bytes."
    )
    assert made(tools, "write_file", path="n" * 239, content="x") == done(
        f"Created {'n' * 239} with 1 bytes."
    )


def test_content_that_cannot_be_saved_or_is_too_large_is_refused(work: Path) -> None:
    tools = tools_on(work)
    assert refused(tools, "write_file", path="a.txt", content="a\ud800") == error(
        "The content holds a character that cannot be saved as UTF-8."
    )
    too_large = error("write_file handles files of up to 8388608 bytes.")
    assert refused(tools, "write_file", path="a.txt", content="x" * 8_388_609) == (
        too_large
    )
    (work / "big").write_bytes(b"x" * 8_388_609)
    assert refused(tools, "write_file", path="big", content="small") == too_large


def test_a_path_the_file_tools_do_not_use_is_refused_by_the_plan(
    work: Path, tmp_path: Path
) -> None:
    (tmp_path / "outside").mkdir()
    (work / "out").symlink_to(tmp_path / "outside")
    tools = tools_on(work)
    assert refused(tools, "write_file", path="../x", content="x").is_error
    assert refused(tools, "write_file", path="out/x", content="x").is_error
    assert refused(tools, "write_file", path=".", content="x").is_error
    assert os.listdir(tmp_path / "outside") == []


def test_a_folder_that_became_a_link_after_the_plan_is_refused(
    work: Path, tmp_path: Path
) -> None:
    (tmp_path / "outside").mkdir()
    (work / "d").mkdir()
    tools = tools_on(work)
    plan = planned(tools, "write_file", path="d/a.txt", content="x")
    (work / "d").rmdir()
    (work / "d").symlink_to(tmp_path / "outside")
    result = changed(tools, "write_file", plan, path="d/a.txt", content="x")
    assert result == error(
        "The path d/a.txt goes through a symbolic link, which this tool does"
        " not follow."
    )
    assert os.listdir(tmp_path / "outside") == []


@pytest.mark.parametrize(
    ("there", "renamed", "on_the_drive", "expected"),
    [
        (True, True, None, done("Wrote 4 bytes to a.txt.")),
        (True, False, None, error("a.txt could not be written: Input/output error.")),
        (False, False, None, error("a.txt could not be written: Input/output error.")),
        (True, True, b"something else", None),
        (True, False, "gone", None),
    ],
)
def test_an_error_at_the_rename_is_settled_by_reading_the_file(
    work: Path,
    monkeypatch: pytest.MonkeyPatch,
    there: bool,
    renamed: bool,
    on_the_drive: bytes | str | None,
    expected: ToolResult | None,
) -> None:
    # A new file that is still not there was not written. An old file that
    # is gone, or holds neither content, leaves the outcome unknown.
    if there:
        (work / "a.txt").write_text("one\n")
    tools = tools_on(work)
    plan = planned(tools, "write_file", path="a.txt", content="two\n")
    real_rename = os.rename

    def failing_rename(source: str, target: str, **folders: int) -> None:
        if renamed:
            real_rename(source, target, **folders)
        if on_the_drive == "gone":
            (work / "a.txt").unlink()
        elif isinstance(on_the_drive, bytes):
            (work / "a.txt").write_bytes(on_the_drive)
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(os, "rename", failing_rename)
    result = changed(tools, "write_file", plan, path="a.txt", content="two\n")
    if expected is None:
        assert isinstance(result, Failure)
        assert (result.kind, result.effect) == ("unavailable", "unknown")
    else:
        assert result == expected
    if expected is not None and expected.effect == "none":
        # Not written, so nothing of the write is left.
        assert not (work / "a.txt.lamplighter-new").exists()


def test_a_write_that_fails_part_way_leaves_nothing_of_itself(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (work / "a.txt").write_text("one\n")
    tools = tools_on(work)
    plan = planned(tools, "write_file", path="a.txt", content="two\n")

    def full_drive(fd: int, data: object) -> int:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "write", full_drive)
    result = changed(tools, "write_file", plan, path="a.txt", content="two\n")
    monkeypatch.undo()
    assert result == ToolResult(
        "a.txt could not be written: No space left on device.", True, "none"
    )
    assert sorted(p.name for p in work.iterdir()) == ["a.txt"]
    assert (work / "a.txt").read_text() == "one\n"


def test_check_says_true_false_or_cannot_tell(work: Path, tmp_path: Path) -> None:
    (work / "a.txt").write_text("one\n")
    tools = tools_on(work)
    plan = planned(tools, "write_file", path="a.txt", content="two\n")
    new = planned(tools, "write_file", path="new.txt", content="two\n")
    assert plan.probe is not None and new.probe is not None
    assert asyncio.run(tools.check(plan.probe)) is False
    assert asyncio.run(tools.check(new.probe)) is False
    (work / "a.txt").write_text("a third content\n")
    assert asyncio.run(tools.check(plan.probe)) is None
    (work / "a.txt").write_text("two\n")
    assert asyncio.run(tools.check(plan.probe)) is True
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.txt").write_text("two\n")
    assert asyncio.run(tools_on(other.resolve()).check(plan.probe)) is None
    (work / "a.txt").unlink()
    assert asyncio.run(tools.check(plan.probe)) is None


def test_check_cannot_tell_while_the_write_is_at_work_and_a_cancel_waits_for_it(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (work / "a.txt").write_text("one\n")
    tools = tools_on(work)
    plan = planned(tools, "write_file", path="a.txt", content="two\n")
    assert plan.probe is not None
    started, release = threading.Event(), threading.Event()
    real_fsync = os.fsync

    def held_fsync(fd: int) -> None:
        started.set()
        release.wait(5)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", held_fsync)

    async def scene() -> tuple[bool | None, ToolResult | Failure, bool | None]:
        task = asyncio.create_task(
            tools.apply("write_file", {"path": "a.txt", "content": "two\n"}, plan)
        )
        await asyncio.to_thread(started.wait, 5)
        assert plan.probe is not None
        during = await tools.check(plan.probe)
        task.cancel()
        await asyncio.sleep(0.05)
        task.cancel()
        assert not task.done()
        release.set()
        result = await task
        return during, result, await tools.check(plan.probe)

    during, result, after = asyncio.run(scene())
    assert during is None
    assert result == done("Wrote 4 bytes to a.txt.")
    assert after is True


def test_a_second_change_to_a_file_is_refused_while_the_first_is_at_work(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both would write the same unfinished file beside a.txt, so the first
    # could rename the second's half-written bytes into place.
    (work / "a.txt").write_text("one\n")
    tools = tools_on(work)
    first = {"path": "a.txt", "content": "two\n"}
    second = {"path": "a.txt", "old_text": "one", "new_text": "three"}
    first_plan = planned(tools, "write_file", **first)
    second_plan = planned(tools, "edit_file", **second)
    started, release = threading.Event(), threading.Event()
    real_fsync = os.fsync

    def held_fsync(fd: int) -> None:
        started.set()
        release.wait(5)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", held_fsync)

    async def scene() -> tuple[ToolResult | Failure, ToolResult | Failure]:
        task = asyncio.create_task(tools.apply("write_file", first, first_plan))
        await asyncio.to_thread(started.wait, 5)
        try:
            refusal = await asyncio.wait_for(
                tools.apply("edit_file", second, second_plan), 2
            )
        finally:
            release.set()
        return refusal, await task

    refusal, result = asyncio.run(scene())
    assert refusal == ToolResult(
        f"An earlier change to {first_plan.target} is still being written."
        " Nothing was written. Read the file again and repeat the call.",
        True,
        "none",
    )
    assert result == done("Wrote 4 bytes to a.txt.")
    assert sorted(p.name for p in work.iterdir()) == ["a.txt"]
    assert (work / "a.txt").read_text() == "two\n"


def test_the_new_file_and_then_its_folder_are_forced_to_the_drive(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (work / "a.txt").write_text("one\n")
    tools = tools_on(work)
    plan = planned(tools, "write_file", path="a.txt", content="two\n")
    steps: list[str] = []
    real_fsync, real_rename = os.fsync, os.rename

    def fsync(fd: int) -> None:
        kind = "folder" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        steps.append(f"force {kind}")
        real_fsync(fd)

    def rename(source: str, target: str, **folders: int) -> None:
        steps.append("rename")
        real_rename(source, target, **folders)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "rename", rename)
    changed(tools, "write_file", plan, path="a.txt", content="two\n")
    assert steps == ["force file", "rename", "force folder"]
