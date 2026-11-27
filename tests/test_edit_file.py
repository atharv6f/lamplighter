"""edit_file: one exact match replaced, and each refusal with its text."""

from pathlib import Path

import pytest

from lamplighter.ports import ToolResult
from tests.builtin_kit import changed, made, planned, refused, tools_on

ONE = "baf2447e02f61f19e982d04232e322652b131ea72cc392a1a6b8ca61a12809d4"
TWO = "0efaeb62312d12c09164f2de757cfafe71d96c783c0262c582cd1cff96239fce"


@pytest.fixture
def work(tmp_path: Path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder.resolve()


def error(text: str) -> ToolResult:
    return ToolResult(text, True, "none")


def test_one_match_is_replaced(work: Path) -> None:
    (work / "a.txt").write_text("one\n")
    tools = tools_on(work)
    plan = planned(tools, "edit_file", path="a.txt", old_text="one", new_text="two")
    assert plan.target == "a.txt"
    assert plan.probe is not None
    assert (plan.probe["before"], plan.probe["after"]) == (ONE, TWO)
    assert plan.undo is not None and plan.undo["kind"] == "restore_file"
    result = changed(
        tools, "edit_file", plan, path="a.txt", old_text="one", new_text="two"
    )
    assert result == ToolResult(
        "Edited a.txt: replaced 3 characters with 3.", False, "done"
    )
    assert (work / "a.txt").read_text() == "two\n"


def test_the_new_text_is_worked_out_from_the_file_the_plan_saw(work: Path) -> None:
    (work / "a.txt").write_text("keep this; change that\n")
    result = made(
        tools_on(work), "edit_file", path="a.txt", old_text="that", new_text="it now"
    )
    assert isinstance(result, ToolResult) and not result.is_error
    assert (work / "a.txt").read_text() == "keep this; change it now\n"


def test_a_file_changed_after_the_plan_is_not_edited(work: Path) -> None:
    (work / "a.txt").write_text("one\n")
    tools = tools_on(work)
    plan = planned(tools, "edit_file", path="a.txt", old_text="one", new_text="two")
    (work / "a.txt").write_text("one, and more\n")
    result = changed(
        tools, "edit_file", plan, path="a.txt", old_text="one", new_text="two"
    )
    assert isinstance(result, ToolResult) and result.is_error
    assert result.effect == "none"
    assert (work / "a.txt").read_text() == "one, and more\n"


NOT_FOUND = (
    "old_text was not found in a.txt. It must match the file exactly, with its"
    " spaces and line breaks. Read the file again and copy the text from it."
)


@pytest.mark.parametrize(
    ("content", "old_text", "new_text", "text"),
    [
        ("one\n", "three", "x", NOT_FOUND),
        (
            "one\r\ntwo\r\n",
            "one\ntwo",
            "x",
            NOT_FOUND + " The file's lines end in a carriage return and a line"
            " feed, and old_text's do not.",
        ),
        ("one\r\ntwo\r\n", "three\nfour", "x", NOT_FOUND),
        ("a\r\r\nb", "a\r\nb", "x", NOT_FOUND),
        (
            "tea, tea\n",
            "tea",
            "x",
            "old_text occurs 2 times in a.txt. Add more of the text around it, so"
            " that it occurs once.",
        ),
        (
            "aaa",
            "aa",
            "b",
            "old_text occurs 2 times in a.txt. Add more of the text around it, so"
            " that it occurs once.",
        ),
        (
            "one\n",
            "one",
            "one",
            "old_text and new_text are the same. Nothing would change.",
        ),
    ],
)
def test_each_refusal_of_the_text_has_its_words(
    work: Path, content: str, old_text: str, new_text: str, text: str
) -> None:
    (work / "a.txt").write_bytes(content.encode())
    result = refused(
        tools_on(work), "edit_file", path="a.txt", old_text=old_text, new_text=new_text
    )
    assert result == error(text)
    assert (work / "a.txt").read_bytes() == content.encode()


def test_a_file_that_is_missing_not_utf8_or_too_large_is_refused(work: Path) -> None:
    (work / "latin.txt").write_bytes(b"caf\xe9\n")
    (work / "big.txt").write_bytes(b"x" * 1_048_577)
    tools = tools_on(work)

    def edit(path: str) -> ToolResult:
        return refused(tools, "edit_file", path=path, old_text="x", new_text="y")

    assert edit("none.txt") == error("There is no file at none.txt.")
    assert edit("latin.txt") == error(
        "latin.txt is not valid UTF-8 text, and saving it back would damage it."
        " edit_file did not touch it."
    )
    assert edit("big.txt") == error(
        "big.txt is 1048577 bytes. edit_file edits files of up to 1048576 bytes."
    )
    assert (work / "latin.txt").read_bytes() == b"caf\xe9\n"


def test_a_name_this_tool_keeps_and_a_bad_new_text_are_refused(work: Path) -> None:
    (work / "a.txt").write_text("one\n")
    tools = tools_on(work)
    kept = refused(
        tools, "edit_file", path="a.lamplighter-new", old_text="x", new_text="y"
    )
    assert kept.is_error
    assert refused(
        tools, "edit_file", path="a.txt", old_text="one", new_text="\ud800"
    ) == error("new_text holds a character that cannot be saved as UTF-8.")
    assert refused(tools, "edit_file", path="a.txt", old_text="", new_text="y").is_error
