"""read_file: the numbered lines, the line that says where they end, and each error."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from lamplighter.ports import JsonObject, ToolResult
from tests.builtin_kit import CAP, tools_on


@pytest.fixture
def work(tmp_path: Path) -> Path:
    made = tmp_path / "work"
    made.mkdir()
    return made.resolve()


def read(work: Path, cap: int = CAP, **arguments: Any) -> ToolResult:
    given: JsonObject = dict(arguments)
    result = asyncio.run(tools_on(work, cap).call("read_file", given))
    assert isinstance(result, ToolResult), result
    assert result.effect == "none"
    return result


def said(text: str, is_error: bool = False) -> ToolResult:
    return ToolResult(text, is_error, "none")


def test_the_lines_come_numbered_with_the_last_line_at_the_end(work: Path) -> None:
    (work / "notes.md").write_text("# Notes\nBuy bread.\n")
    assert read(work, path="notes.md") == said(
        "     1\t# Notes\n     2\tBuy bread.\n"
        "[Lines 1 to 2 of 2. That is the end of the file.]"
    )


def test_a_file_with_no_line_break_at_its_end_still_has_its_last_line(
    work: Path,
) -> None:
    (work / "a.txt").write_text("one\ntwo")
    assert read(work, path="a.txt").text == (
        "     1\tone\n     2\ttwo\n[Lines 1 to 2 of 2. That is the end of the file.]"
    )


def test_offset_and_limit_pick_lines_and_say_how_to_go_on(work: Path) -> None:
    (work / "a.txt").write_text("".join(f"line {n}\n" for n in range(1, 11)))
    assert read(work, path="a.txt", offset=4, limit=2).text == (
        "     4\tline 4\n     5\tline 5\n"
        "[Lines 4 to 5 of 10. Use offset=6 to continue.]"
    )
    assert read(work, path="a.txt", offset=10).text == (
        "    10\tline 10\n[Lines 10 to 10 of 10. That is the end of the file.]"
    )


def test_a_long_line_is_cut_at_two_thousand_characters(work: Path) -> None:
    (work / "a.txt").write_text("x" * 5_000 + "\n")
    assert read(work, path="a.txt").text == (
        "     1\t" + "x" * 2_000 + " [... 3000 more characters on this line]\n"
        "[Lines 1 to 1 of 1. That is the end of the file.]"
    )


def test_a_line_of_two_thousand_characters_is_whole_and_one_more_is_cut(
    work: Path,
) -> None:
    (work / "a.txt").write_text("a" * 2_000 + "\n" + "b" * 2_001 + "\n")
    lines = read(work, path="a.txt").text.split("\n")
    assert lines[0] == "     1\t" + "a" * 2_000
    assert (
        lines[1] == "     2\t" + "b" * 2_000 + " [... 1 more characters on this line]"
    )


def test_a_file_that_would_pass_the_cap_stops_before_it(work: Path) -> None:
    # Each numbered line is 7 + 99 characters, and a line break joins them.
    (work / "a.txt").write_text("y" * 99 + "\n" + ("z" * 99 + "\n") * 500)
    text = read(work, cap=26_000, path="a.txt").text
    body, last = text.rsplit("\n", 1)
    assert len(body) <= 26_000 - 500
    shown = body.count("\n") + 1
    assert shown == (26_000 - 500 + 1) // 107
    assert last == f"[Lines 1 to {shown} of 501. Use offset={shown + 1} to continue.]"


def test_one_line_is_given_even_past_the_room(work: Path) -> None:
    (work / "a.txt").write_text("x" * 1_990 + "\n" + "y\n")
    text = read(work, cap=2_000, path="a.txt").text
    assert text.endswith("[Lines 1 to 1 of 2. Use offset=2 to continue.]")


def test_bytes_that_are_not_utf8_are_replaced(work: Path) -> None:
    (work / "a.txt").write_bytes(b"caf\xe9\n")
    assert read(work, path="a.txt").text.startswith("     1\tcaf�\n")


def test_an_empty_file_says_so_and_is_not_an_error(work: Path) -> None:
    (work / "empty.txt").write_bytes(b"")
    assert read(work, path="empty.txt") == said("empty.txt is empty.")


def test_each_error_has_its_text(work: Path) -> None:
    (work / "bin").write_bytes(b"ab\0cd")
    (work / "big").write_bytes(b"x" * 8_388_609)
    (work / "two.txt").write_text("a\nb\n")
    assert read(work, path="none.txt") == said("There is no file at none.txt.", True)
    assert read(work, path="bin") == said(
        "bin is not a text file. read_file reads text only.", True
    )
    assert read(work, path="big") == said(
        "big is 8388609 bytes. read_file reads files of up to 8388608 bytes. Search"
        " it with search_files, or read part of it with a command.",
        True,
    )
    assert read(work, path="two.txt", offset=3) == said(
        "two.txt has 2 lines. offset=3 is past the end.", True
    )


def test_a_zero_byte_after_the_first_eight_kilobytes_is_text(work: Path) -> None:
    (work / "late").write_bytes(b"a" * 8_192 + b"\0")
    assert not read(work, path="late").is_error


def test_each_kind_of_path_is_refused_with_its_text(work: Path) -> None:
    outside = work.parent / "outside"
    outside.mkdir()
    (outside / "id_rsa").write_text("decoy")
    (work / "to-decoy").symlink_to(outside / "id_rsa")
    (work / "out").symlink_to(outside)
    (work / "d").mkdir()
    link = "goes through a symbolic link, which this tool does not follow."
    expected = {
        "../outside/id_rsa": "The path ../outside/id_rsa leaves the work folder."
        " File tools work only inside it.",
        str(outside / "id_rsa"): f"The path {outside / 'id_rsa'} leaves the work"
        " folder. File tools work only inside it.",
        "to-decoy": f"The path to-decoy {link}",
        "out/id_rsa": f"The path out/id_rsa {link}",
        "d": "d is a folder, not a file.",
        "a\0b": "That is not a valid path.",
    }
    for path, text in expected.items():
        assert read(work, path=path) == said(text, True), path


def test_arguments_out_of_their_bounds_are_an_error_and_not_a_raise(
    work: Path,
) -> None:
    (work / "a.txt").write_text("a\n")
    bad: tuple[JsonObject, ...] = (
        {"path": 7},
        {"path": "a.txt", "offset": 0},
        {"path": "a.txt", "limit": 2_001},
    )
    for arguments in bad:
        result = asyncio.run(tools_on(work).call("read_file", arguments))
        assert isinstance(result, ToolResult) and result.is_error, arguments
