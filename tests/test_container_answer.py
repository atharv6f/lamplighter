"""The container executor with no container: its answer, its refusals, its sweep."""

import asyncio
import base64
from pathlib import Path

import pytest

from lamplighter.adapters import CountingIds
from lamplighter.adapters.container import ContainerExecutor
from lamplighter.adapters.container.supervisor import read_answer, script_line
from lamplighter.ports import Command, ExecResult, Failure

TOKEN = "id-0001"
PATH = {"PATH": "/usr/bin:/bin"}


def answer(word: str, status: str, size: str, kept: bytes) -> str:
    packed = base64.b64encode(kept).decode()
    return f"{TOKEN} {word} {status} {size}\n{packed}\n"


def test_a_whole_answer_gives_the_exit_code_and_the_exact_bytes() -> None:
    every_byte = bytes(range(256))
    result = read_answer(TOKEN, answer("exited", "3", "256", every_byte), 1_000)
    assert result == ExecResult(3, None, every_byte, False, False, False)


@pytest.mark.parametrize(
    ("word", "status", "flags"),
    [
        ("time", "137", (True, False)),
        ("cancel", "137", (False, True)),
        ("leave", "137", (False, False)),
        ("full", "137", (False, False)),
        ("exited", "137", (False, False)),
    ],
)
def test_a_kill_gives_signal_nine_and_the_flag_of_its_reason(
    word: str, status: str, flags: tuple[bool, bool]
) -> None:
    result = read_answer(TOKEN, answer(word, status, "0", b""), 10)
    assert result is not None
    assert (result.exit_code, result.signal) == (None, 9)
    assert (result.timed_out, result.cancelled) == flags


def test_a_command_that_ended_by_itself_at_the_limit_keeps_its_exit_code() -> None:
    result = read_answer(TOKEN, answer("time", "0", "0", b""), 10)
    assert result == ExecResult(0, None, b"", False, False, False)


@pytest.mark.parametrize(("status", "signal"), [("129", 1), ("143", 15), ("192", 64)])
def test_a_status_from_129_to_192_is_a_signal(status: str, signal: int) -> None:
    result = read_answer(TOKEN, answer("exited", status, "0", b""), 10)
    assert result is not None and (result.exit_code, result.signal) == (None, signal)


@pytest.mark.parametrize("status", ["128", "193", "255"])
def test_a_status_outside_that_range_is_an_exit_code(status: str) -> None:
    result = read_answer(TOKEN, answer("exited", status, "0", b""), 10)
    assert result is not None and result.exit_code == int(status)


def test_more_bytes_than_the_cap_is_overflowed_and_the_cap_is_not() -> None:
    over = read_answer(TOKEN, answer("full", "137", "11", b"x" * 10), 10)
    at = read_answer(TOKEN, answer("exited", "0", "10", b"x" * 10), 10)
    assert over is not None and over.overflowed and over.output == b"x" * 10
    assert at is not None and not at.overflowed


@pytest.mark.parametrize(
    "text",
    [
        "",
        f"{TOKEN} exited  0\n\n",
        f"{TOKEN} exited 0 0\n",
        f"{TOKEN} exited 0 0",
        f"{TOKEN} gone 0 0\n\n",
        f"{TOKEN} exited 1000 0\n\n",
        f"{TOKEN} exited 0 0 extra\n\n",
        "id-0002 exited 0 0\n\n",
        f"{TOKEN}exited 0 0\n\n",
        f"{TOKEN} exited 0 5\naGVsbA==\n",
        f"{TOKEN} exited 0 5\naGVsbG8\n",
        f"{TOKEN} exited 0 5\naGVs bG8=\n",
        f"{TOKEN} exited 0 3\n4pyT!\n",
    ],
)
def test_an_answer_that_is_not_whole_is_none(text: str) -> None:
    assert read_answer(TOKEN, text, 100) is None


def test_the_answer_is_found_after_other_lines() -> None:
    text = "something else\n" + answer("exited", "0", "2", b"ok")
    assert read_answer(TOKEN, text, 100) == ExecResult(
        0, None, b"ok", False, False, False
    )


def test_the_line_names_the_folder_the_limits_and_the_user_in_order() -> None:
    line = script_line(TOKEN, ["/bin/echo", "hi"], {"A": "1"}, ["front", "--"], (7, 2))
    assert line.endswith(
        f" supervisor /tmp/.run-{TOKEN} 7 2000 {TOKEN} /usr/bin/env -i -- A=1"
        " front -- /bin/echo hi"
    )


class FakeRoot:
    """Answers every call into the container from a list, and keeps the lines."""

    def __init__(self, answers: list[tuple[int, str] | None]) -> None:
        self.answers = answers
        self.lines: list[str] = []

    async def __call__(
        self, line: str, cwd: str, seconds: int
    ) -> tuple[int, str] | None:
        self.lines.append(line)
        return self.answers.pop(0)


def contained(root: FakeRoot, work: Path, user: int = 1001) -> ContainerExecutor:
    return ContainerExecutor(root, user, user, work, CountingIds())


def command(work: Path, *argv: str, env: dict[str, str] = PATH) -> Command:
    return Command(argv, str(work), env, "workspace_write", 1_000, 1_000)


@pytest.mark.parametrize("name", ["", "A=B"])
def test_a_name_that_env_would_read_as_something_else_is_refused(
    tmp_path: Path, name: str
) -> None:
    root = FakeRoot([])
    refused = asyncio.run(
        contained(root, tmp_path).run(command(tmp_path, "/bin/true", env={name: "x"}))
    )
    assert isinstance(refused, Failure)
    assert (refused.kind, refused.effect) == ("invalid_input", "none")
    assert root.lines == []


def test_a_line_over_the_limit_after_quoting_is_refused_and_nothing_is_sent(
    tmp_path: Path,
) -> None:
    root = FakeRoot([])
    quotes = command(tmp_path, "/bin/echo", "'" * 25_000)
    refused = asyncio.run(contained(root, tmp_path).run(quotes))
    assert isinstance(refused, Failure)
    assert (refused.kind, refused.effect) == ("invalid_input", "none")
    assert root.lines == []


def test_a_call_that_did_not_answer_is_unavailable_and_the_adapter_is_broken(
    tmp_path: Path,
) -> None:
    executor = contained(FakeRoot([None, (1, "")]), tmp_path)
    first = asyncio.run(executor.run(command(tmp_path, "/bin/true")))
    second = asyncio.run(executor.run(command(tmp_path, "/bin/true")))
    for failed in (first, second):
        assert isinstance(failed, Failure)
        assert (failed.kind, failed.effect) == ("unavailable", "unknown")
    assert executor.broken == first


@pytest.mark.parametrize(
    "listing", [None, (1, "7 1 9"), (0, ""), (0, "7 1 x"), (0, "seven")]
)
def test_mark_is_false_when_the_listing_is_not_whole(
    tmp_path: Path, listing: tuple[int, str] | None
) -> None:
    assert not asyncio.run(contained(FakeRoot([listing]), tmp_path).mark())


def test_the_sweep_spares_what_mark_kept_but_its_own_listing(tmp_path: Path) -> None:
    root = FakeRoot([(0, "40 1 7 40 "), (0, ""), (0, "swept 0\n")])
    executor = contained(root, tmp_path)
    assert asyncio.run(executor.mark())
    assert asyncio.run(executor.sweep())
    assert 'keep=" 1 7 "' in root.lines[2]


def test_the_sweep_kills_as_the_command_user_first_and_never_as_root(
    tmp_path: Path,
) -> None:
    root = FakeRoot([(0, ""), (0, "swept 0\n")])
    assert asyncio.run(contained(root, tmp_path).sweep())
    assert root.lines[0] == (
        "/usr/bin/setpriv --reuid=1001 --regid=1001 --clear-groups --"
        " /bin/bash -c 'kill -KILL -1'"
    )
    as_root = FakeRoot([(0, "swept 0\n")])
    assert asyncio.run(contained(as_root, tmp_path, user=0).sweep())
    assert len(as_root.lines) == 1 and "kill -KILL -1" not in as_root.lines[0]


def test_the_sweep_is_true_on_the_first_pass_that_counts_nothing(
    tmp_path: Path,
) -> None:
    passes: list[tuple[int, str] | None] = [(0, "swept 3\n"), (0, "swept 1\n")]
    root = FakeRoot([(0, ""), *passes, (0, "swept 0\n")])
    assert asyncio.run(contained(root, tmp_path).sweep())
    assert len(root.lines) == 4


@pytest.mark.parametrize(
    "passes",
    [
        [(0, "swept 1\n")] * 5,
        [None],
        [(0, "")],
        [(0, "swept\n")],
        [(0, "swept 0 more\n")],
        [(0, "swept x\n")],
    ],
)
def test_the_sweep_is_false_when_something_is_left_or_the_answer_is_not_whole(
    tmp_path: Path, passes: list[tuple[int, str] | None]
) -> None:
    assert not asyncio.run(contained(FakeRoot([(0, ""), *passes]), tmp_path).sweep())


def test_the_sweep_is_false_when_the_kill_to_every_process_got_no_answer(
    tmp_path: Path,
) -> None:
    assert not asyncio.run(contained(FakeRoot([None]), tmp_path).sweep())
