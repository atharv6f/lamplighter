"""shell: each status line, the output as one text, a stop, and the environment."""

import asyncio
import signal
from pathlib import Path
from typing import Any

import pytest

from lamplighter.adapters import FakeExecutor
from lamplighter.core.arguments_check import problems
from lamplighter.ports import (
    Command,
    ExecResult,
    Failure,
    JsonObject,
    Plan,
    ToolResult,
)
from tests.builtin_kit import settings_for, tools_on

MB8 = 8_388_608


@pytest.fixture
def work(tmp_path: Path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder.resolve()


def shell(work: Path, line: str, **more: Any) -> ToolResult:
    given: JsonObject = {"command": line, **more}
    tools = tools_on(work)

    async def scene() -> ToolResult | Failure:
        plan = await tools.plan("shell", given)
        assert isinstance(plan, Plan)
        assert plan == Plan(target=".", probe=None, undo=None)
        return await tools.apply("shell", given, plan)

    result = asyncio.run(scene())
    assert isinstance(result, ToolResult), result
    assert result.effect == "done"
    return result


def on_fake(work: Path, answer: ExecResult | Failure, **more: Any) -> object:
    fake = FakeExecutor(work, lambda command: answer)
    given: JsonObject = {"command": "true", **more}
    plan = Plan(target=".", probe=None, undo=None)
    return asyncio.run(tools_on(work, executor=fake).apply("shell", given, plan))


def ran(text: str, is_error: bool) -> ToolResult:
    return ToolResult(text, is_error, "done")


@pytest.mark.parametrize(
    ("answer", "text", "is_error"),
    [
        (
            ExecResult(None, 9, b"so far", False, False, True),
            "[stopped before it ended, and was killed]\nso far",
            True,
        ),
        (
            ExecResult(None, 9, b"", False, True, False),
            "[timed out after 7 seconds and was killed]\n(no output)",
            True,
        ),
        (
            ExecResult(None, 9, b"y\n", True, False, False),
            f"[stopped at {MB8} bytes of output and was killed]\ny\n",
            True,
        ),
        (
            ExecResult(0, None, b"z", True, False, False),
            f"[exit code 0, and the output after {MB8} bytes was left out]\nz",
            False,
        ),
        (
            ExecResult(1, None, b"z", True, False, False),
            f"[exit code 1, and the output after {MB8} bytes was left out]\nz",
            True,
        ),
        (
            ExecResult(None, 15, b"bye", False, False, False),
            "[killed by signal 15]\nbye",
            True,
        ),
        (
            ExecResult(0, None, b"ok\n", False, False, False),
            "[exit code 0]\nok\n",
            False,
        ),
        (
            ExecResult(3, None, b"", False, False, False),
            "[exit code 3]\n(no output)",
            True,
        ),
    ],
)
def test_each_status_line(
    work: Path, answer: ExecResult, text: str, is_error: bool
) -> None:
    assert on_fake(work, answer, timeout_s=7) == ran(text, is_error)


def test_the_command_runs_in_bash_in_the_work_folder_and_may_write_there(
    work: Path,
) -> None:
    fake = FakeExecutor(
        work, lambda command: ExecResult(0, None, b"", False, False, False)
    )
    given: JsonObject = {"command": "echo $0 > who"}
    plan = Plan(target=".", probe=None, undo=None)
    asyncio.run(tools_on(work, executor=fake).apply("shell", given, plan))
    (command,) = fake.commands
    assert command == Command(
        argv=("/bin/bash", "-c", "echo $0 > who"),
        cwd=str(work),
        env=command.env,
        profile="workspace_write",
        wall_ms=120_000,
        capture_bytes=MB8,
    )


def test_real_commands_give_both_streams_as_one_and_bytes_replaced(work: Path) -> None:
    assert shell(work, "echo one; echo two >&2; echo three") == ran(
        "[exit code 0]\none\ntwo\nthree\n", False
    )
    assert shell(work, "printf 'caf\\351'") == ran("[exit code 0]\ncaf�", False)
    assert shell(work, "true") == ran("[exit code 0]\n(no output)", False)
    assert shell(work, "exit 4") == ran("[exit code 4]\n(no output)", True)
    assert shell(work, "kill -TERM $$") == ran(
        f"[killed by signal {signal.SIGTERM}]\n(no output)", True
    )
    assert shell(work, "sleep 5", timeout_s=1) == ran(
        "[timed out after 1 seconds and was killed]\n(no output)", True
    )
    assert shell(work, "echo made > made.txt").text == "[exit code 0]\n(no output)"
    assert (work / "made.txt").read_text() == "made\n"


def test_the_command_sees_the_seven_variables_and_none_of_the_harness(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a made-up key that must not be seen")
    scratch = settings_for(work).scratch_dir
    seen = shell(work, "/usr/bin/env").text.split("\n")[1:-1]
    given = {
        f"HOME={scratch}/home",
        "LANG=en_US.UTF-8",
        "NO_COLOR=1",
        "PATH=/usr/bin:/bin",
        "TERM=dumb",
        f"TMPDIR={scratch}/tmp",
        f"TMPPREFIX={scratch}/tmp/zsh",
    }
    assert given <= set(seen)
    # bash itself sets these three for the programs it starts.
    added = {line.split("=", 1)[0] for line in set(seen) - given}
    assert added <= {"PWD", "SHLVL", "_"}


def test_a_stop_during_a_command_gives_the_output_so_far(work: Path) -> None:
    async def scene() -> ToolResult | Failure:
        tools = tools_on(work)
        given: JsonObject = {"command": "echo started; sleep 30"}
        plan = Plan(target=".", probe=None, undo=None)
        task = asyncio.create_task(tools.apply("shell", given, plan))
        await asyncio.sleep(0.3)
        task.cancel()
        return await task

    assert asyncio.run(scene()) == ran(
        "[stopped before it ended, and was killed]\nstarted\n", True
    )


def test_a_failure_of_the_executor_is_passed_on_with_no_effect(work: Path) -> None:
    gone = Failure(kind="unavailable", effect="none", message="No wall.", source="t")
    assert on_fake(work, gone) == gone


def test_a_time_limit_above_the_setting_does_not_fit_the_schema(work: Path) -> None:
    (entry,) = [e for e in tools_on(work).list_tools() if e.spec.name == "shell"]
    assert entry.action_class == "irreversible"
    schema = entry.spec.arguments_schema
    assert problems(schema, {"command": "true", "timeout_s": 600}) == ()
    assert problems(schema, {"command": "true", "timeout_s": 601}) != ()
    assert problems(schema, {"command": "true", "timeout_s": 0}) != ()
    assert on_fake(
        work, ExecResult(0, None, b"", False, False, False), timeout_s=601
    ) == (
        ToolResult(
            "shell needs a command, and a timeout_s from 1 to 600.", True, "none"
        )
    )


def test_a_zero_byte_in_the_command_is_refused_and_raises_nothing(work: Path) -> None:
    given: JsonObject = {"command": "echo made\0up > marker"}
    result = asyncio.run(tools_on(work).apply("shell", given, Plan(".", None, None)))
    assert isinstance(result, Failure), result
    assert (result.kind, result.effect) == ("invalid_input", "none")
    assert not (work / "marker").exists()
