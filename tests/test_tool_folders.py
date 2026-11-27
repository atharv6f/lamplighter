"""The tools' folders: made private, never reached through a link, and kept real."""

import os
import stat
from pathlib import Path

from lamplighter.adapters.builtin_tools import ToolSettings
from lamplighter.app.tool_folders import THROUGH_A_LINK, make_tool_folders


def settings(work: Path, scratch: Path) -> ToolSettings:
    return ToolSettings(
        work_dir=str(work),
        scratch_dir=str(scratch),
        shell_path="/usr/bin:/bin",
        shell_default_seconds=120,
        shell_max_seconds=600,
        output_cap_chars=30_000,
        fetch_max_bytes=5_242_880,
        fetch_seconds=30,
    )


def test_missing_folders_are_made_private_with_parents(tmp_path: Path) -> None:
    work, scratch = tmp_path / "a" / "work", tmp_path / "b" / "scratch"
    assert make_tool_folders(settings(work, scratch)) is None
    for folder in (work, scratch, scratch / "home", scratch / "tmp"):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700, folder


def test_a_link_or_a_file_where_home_or_tmp_should_be_is_replaced(
    tmp_path: Path,
) -> None:
    work, scratch, elsewhere = tmp_path / "work", tmp_path / "scratch", tmp_path / "x"
    for folder in (work, scratch, elsewhere):
        folder.mkdir()
    (elsewhere / "kept.txt").write_text("made-up")
    (scratch / "home").symlink_to(elsewhere)
    (scratch / "tmp").write_text("made-up")
    assert make_tool_folders(settings(work, scratch)) is None
    for name in ("home", "tmp"):
        found = os.lstat(scratch / name)
        assert stat.S_ISDIR(found.st_mode), name
        assert list((scratch / name).iterdir()) == []
    assert (elsewhere / "kept.txt").read_text() == "made-up"


def test_a_real_home_folder_keeps_what_it_holds(tmp_path: Path) -> None:
    work, scratch = tmp_path / "work", tmp_path / "scratch"
    (scratch / "home").mkdir(parents=True)
    (scratch / "home" / ".gitconfig").write_text("made-up")
    assert make_tool_folders(settings(work, scratch)) is None
    assert (scratch / "home" / ".gitconfig").read_text() == "made-up"


def test_a_scratch_folder_reached_through_a_link_is_refused(tmp_path: Path) -> None:
    work, real = tmp_path / "work", tmp_path / "real"
    real.mkdir()
    (tmp_path / "scratch").symlink_to(real)
    line = make_tool_folders(settings(work, tmp_path / "scratch"))
    assert line == THROUGH_A_LINK.format(
        setting="tools.scratch_dir", path=tmp_path / "scratch", real=real
    )
    assert not (real / "home").exists()


def test_a_folder_below_a_linked_folder_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "linked").symlink_to(real)
    work = tmp_path / "linked" / "work"
    line = make_tool_folders(settings(work, tmp_path / "scratch"))
    assert line == THROUGH_A_LINK.format(
        setting="tools.work_dir", path=work, real=real / "work"
    )
