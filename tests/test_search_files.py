"""search_files: what grep and find give the model, and the commands they run."""

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

from lamplighter.adapters import FakeExecutor
from lamplighter.ports import Command, ExecResult, Failure, JsonObject, ToolResult
from tests.builtin_kit import settings_for, tools_on


@pytest.fixture
def work(tmp_path: Path) -> Path:
    folder = tmp_path / "work"
    (folder / "sub").mkdir(parents=True)
    (folder / ".git").mkdir()
    (folder / "a:b.txt").write_text("needle one\nplain\nneedle two\n")
    (folder / "sub" / "c.py").write_text("x = 'needle'\n")
    (folder / ".git" / "config").write_text("needle in git\n")
    (folder / "bin.dat").write_bytes(b"needle\x00\x01\n")
    return folder.resolve()


def search(work: Path, **arguments: Any) -> ToolResult:
    given: JsonObject = dict(arguments)
    result = asyncio.run(tools_on(work).call("search_files", given))
    assert isinstance(result, ToolResult), result
    assert result.effect == "none"
    return result


def said(text: str, is_error: bool = False) -> ToolResult:
    return ToolResult(text, is_error, "none")


def test_matches_come_with_their_counts_sorted_by_path_and_line(work: Path) -> None:
    (work / "sub" / "b.md").write_text("no\nneedle\n")
    assert search(work, pattern="needle") == said(
        "4 matching lines in 3 files.\n"
        "a:b.txt:1: needle one\n"
        "a:b.txt:3: needle two\n"
        "sub/b.md:2: needle\n"
        "sub/c.py:1: x = 'needle'"
    )


def test_a_glob_a_path_and_an_extended_pattern_narrow_the_search(work: Path) -> None:
    assert search(work, pattern="needle", glob="*.py") == said(
        "1 matching lines in 1 files.\nsub/c.py:1: x = 'needle'"
    )
    assert search(work, pattern="ne+dle (one|two)", path="a:b.txt") == said(
        "2 matching lines in 1 files.\na:b.txt:1: needle one\na:b.txt:3: needle two"
    )
    assert search(work, pattern="needle", path=str(work / "sub")).text.startswith(
        "1 matching lines in 1 files."
    )


def test_no_match_and_a_pattern_grep_refuses(work: Path) -> None:
    assert search(work, pattern="no-such-text") == said("No matches.")
    refused = search(work, pattern="(")
    assert refused.is_error
    assert refused.text.startswith("The search failed: ")
    assert "(" in refused.text.removeprefix("The search failed: ")


def test_a_long_line_is_given_up_to_five_hundred_characters(work: Path) -> None:
    (work / "long.txt").write_text("needle" + "x" * 1_000 + "\n")
    lines = search(work, pattern="needle", path="long.txt").text.split("\n")
    assert lines[1] == "long.txt:1: " + ("needle" + "x" * 1_000)[:500]


def test_names_are_listed_sorted_and_star_lists_a_folder(work: Path) -> None:
    assert search(work, pattern="*.py", target="names") == said("1 files.\nsub/c.py")
    assert search(work, pattern="*", target="names", path="sub") == said(
        "1 files.\nsub/c.py"
    )
    assert search(work, pattern="*", target="names") == said(
        "3 files.\na:b.txt\nbin.dat\nsub/c.py"
    )
    assert search(work, pattern="*.md", target="names") == said("No files match.")


def test_refusals_of_the_arguments(work: Path) -> None:
    assert search(work, pattern="x", target="names", glob="*.py") == said(
        "glob is for the target content only. Put the name pattern in pattern.", True
    )
    assert search(work, pattern="x", path="../x") == said(
        "The path ../x leaves the work folder. File tools work only inside it.", True
    )
    assert search(work, pattern=7).is_error


DONE = ExecResult(1, None, b"", False, False, False)


def recorded(work: Path, answer: ExecResult = DONE, **arguments: Any) -> Command:
    fake = FakeExecutor(work, lambda command: answer)
    given: JsonObject = dict(arguments)
    asyncio.run(tools_on(work, executor=fake).call("search_files", given))
    (command,) = fake.commands
    return command


@pytest.mark.parametrize("pattern", ["-rf /", 'it\'s "quoted"', "$(touch x)"])
def test_the_pattern_is_one_item_of_the_argument_list(work: Path, pattern: str) -> None:
    command = recorded(work, pattern=pattern, path="sub")
    assert command.argv == (
        "/usr/bin/grep", "-rnHIE", "--null", "--exclude-dir=.git",
        "-e", pattern, "--", "./sub",
    )  # fmt: skip
    names = recorded(work, pattern=pattern, target="names")
    assert names.argv == (
        "/usr/bin/find", ".", "-name", ".git", "-prune", "-o",
        "-type", "f", "-name", pattern, "-print",
    )  # fmt: skip


def test_the_command_reads_only_in_the_work_folder_with_six_variables(
    work: Path,
) -> None:
    command = recorded(work, pattern="x", glob="*.md")
    settings = settings_for(work)
    assert "--include=*.md" in command.argv
    assert (command.cwd, command.profile) == (str(work), "read_only")
    assert (command.wall_ms, command.capture_bytes) == (30_000, 8_388_608)
    assert command.env == {
        "PATH": "/usr/bin:/bin",
        "HOME": f"{settings.scratch_dir}/home",
        "TMPDIR": f"{settings.scratch_dir}/tmp",
        "LANG": "en_US.UTF-8",
        "TERM": "dumb",
        "NO_COLOR": "1",
    }


def run_on_fake(work: Path, answer: ExecResult | Failure, **arguments: Any) -> object:
    fake = FakeExecutor(work, lambda command: answer)
    given: JsonObject = dict(arguments)
    return asyncio.run(tools_on(work, executor=fake).call("search_files", given))


def test_the_time_limit_and_the_capture_limit(work: Path) -> None:
    timed_out = ExecResult(None, 9, b"./a\x001:x\n", False, True, False)
    assert run_on_fake(work, timed_out, pattern="x") == said(
        "The search took longer than 30 seconds and was stopped. Narrow the path or"
        " the pattern.",
        True,
    )
    full = ExecResult(None, 9, b"./a\x001:one\n./b\x002:half a li", True, False, False)
    assert run_on_fake(work, full, pattern="x") == said(
        "1 matching lines in 1 files.\na:1: one\n[The search output passed 8388608"
        " bytes and was stopped. Narrow the path or the pattern.]"
    )
    names = ExecResult(None, 9, b"./a.md\n./b.md\n./half", True, False, False)
    assert run_on_fake(work, names, pattern="*", target="names") == said(
        "2 files.\na.md\nb.md\n[The search output passed 8388608 bytes and was"
        " stopped. Narrow the path or the pattern.]"
    )


def test_a_message_among_the_records_is_left_out(work: Path) -> None:
    output = b"./a\x001:one\ngrep: ./x: Permission denied\n./b\x002:two\n"
    answer = ExecResult(2, None, output, False, False, False)
    assert run_on_fake(work, answer, pattern="x") == said(
        "2 matching lines in 2 files.\na:1: one\nb:2: two\n"
        "[Some files could not be read.]"
    )
    failed = ExecResult(2, None, b"grep: ./x: No such file\n", False, False, False)
    assert run_on_fake(work, failed, pattern="x") == said(
        "The search failed: grep: ./x: No such file", True
    )


def test_a_failure_of_the_executor_is_passed_on(work: Path) -> None:
    gone = Failure(kind="unavailable", effect="none", message="No wall.", source="t")
    assert run_on_fake(work, gone, pattern="x") == gone


def test_a_cancelled_search_gives_no_value(work: Path) -> None:
    # grep waits on a named pipe that nobody writes to.
    os.mkfifo(work / "pipe")

    async def scene() -> None:
        task = asyncio.create_task(
            tools_on(work).call("search_files", {"pattern": "x", "path": "pipe"})
        )
        await asyncio.sleep(0.3)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scene())


def test_matches_are_sorted_by_path_and_then_by_line_number(work: Path) -> None:
    output = b"./b\x002:x\n./a\x0010:y\n./a\x009:z\n"
    answer = ExecResult(0, None, output, False, False, False)
    assert run_on_fake(work, answer, pattern="x") == said(
        "3 matching lines in 2 files.\na:9: z\na:10: y\nb:2: x"
    )


@pytest.mark.parametrize("where", ["pattern", "glob"])
def test_a_zero_byte_in_the_search_is_refused_and_raises_nothing(
    work: Path, where: str
) -> None:
    given: JsonObject = {"pattern": "made-up", where: "made\0up"}
    result = asyncio.run(tools_on(work).call("search_files", given))
    assert isinstance(result, Failure), result
    assert (result.kind, result.effect) == ("invalid_input", "none")
