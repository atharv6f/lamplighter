"""The promises of the executor port, in two parts: the records, and the processes.

The record laws run on every executor, the fake included. The process laws
run only on executors that start a process: the fake starts none, so a test
that looks for a leftover process or a variable inside a command could not
fail on it, and a test that cannot fail shows nothing.
"""

import asyncio
import os
import signal
import subprocess
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from lamplighter.adapters import FakeExecutor, SystemClock
from lamplighter.adapters.seatbelt import SeatbeltExecutor
from lamplighter.ports import (
    Command,
    ExecResult,
    Executor,
    Failure,
    canonical_json,
)
from tests.direct_executor import DirectExecutor

PATH = {"PATH": "/usr/bin:/bin"}
DONE = ExecResult(0, None, b"", False, False, False)


def run[T](call: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(call)


@dataclass(frozen=True)
class ExecutorKind:
    """How to make one executor on a work folder."""

    make: Callable[[Path], Executor]


def behind_the_wall(work: Path) -> Executor:
    """The real wall, with a scratch folder and a store folder beside `work`."""
    scratch, state = work.parent / "scratch", work.parent / "state"
    (scratch / "tmp").mkdir(parents=True, exist_ok=True)
    state.mkdir(exist_ok=True)
    return SeatbeltExecutor(str(work), str(scratch), str(state), SystemClock())


FAKE = ExecutorKind(lambda work: FakeExecutor(work, lambda command: DONE))
DIRECT = ExecutorKind(lambda work: DirectExecutor(work, SystemClock()))
WALLED = ExecutorKind(behind_the_wall)
SANDBOX = pytest.mark.sandbox
RECORD_KINDS = [
    pytest.param(FAKE, id="fake"),
    pytest.param(DIRECT, id="direct"),
    pytest.param(WALLED, id="seatbelt", marks=SANDBOX),
]
PROCESS_KINDS = [
    pytest.param(DIRECT, id="direct"),
    pytest.param(WALLED, id="seatbelt", marks=SANDBOX),
]


@pytest.fixture
def work(tmp_path: Path) -> Path:
    made = tmp_path / "work"
    made.mkdir()
    return made.resolve()


@pytest.fixture(params=RECORD_KINDS)
def any_executor(request: pytest.FixtureRequest, work: Path) -> Executor:
    kind: ExecutorKind = request.param
    return kind.make(work)


@pytest.fixture(params=PROCESS_KINDS)
def executor(request: pytest.FixtureRequest, work: Path) -> Executor:
    kind: ExecutorKind = request.param
    return kind.make(work)


def command(work: Path, *argv: str, **changes: Any) -> Command:
    made = Command(
        argv=argv,
        cwd=str(work),
        env=PATH,
        profile="workspace_write",
        wall_ms=10_000,
        capture_bytes=1_000_000,
    )
    return replace(made, **changes)


def result_of(outcome: ExecResult | Failure) -> ExecResult:
    assert isinstance(outcome, ExecResult), outcome
    # Every result has an exit code or a signal, never both, and the two
    # flags of a kill never come with an exit code.
    assert (outcome.exit_code is None) != (outcome.signal is None), outcome
    if outcome.timed_out or outcome.cancelled:
        assert outcome.exit_code is None, outcome
    return outcome


def leaves_the_group(then: str) -> str:
    """A shell line whose child starts a session of its own and then runs `then`.

    The line waits until the child has left the group and written its pid,
    so the kill at the end of the line cannot reach it first. Perl is in
    the same place on Linux and macOS, and macOS has no `setsid` program.
    """
    child = (
        f"use POSIX; POSIX::setsid(); open(F, '>pid'); print F \\$\\$; close F; {then}"
    )
    return f'/usr/bin/perl -e "{child}" & while [ ! -s pid ]; do sleep 0.01; done'


async def until_the_pid_or_the_end(task: asyncio.Future[Any], work: Path) -> None:
    """Wait until the command has written its pid, or the run has ended.

    A run that ended without starting the command, as a wall that is not up
    does, must fail its test and not hold it for ever.
    """
    pid = work / "pid"
    while not task.done() and not (pid.is_file() and pid.read_text()):
        await asyncio.sleep(0.01)


def open_descriptors() -> int:
    return len(os.listdir("/dev/fd"))


def is_alive(pid: int) -> bool:
    # A process that is gone may stay a zombie for a moment, until its new
    # parent reaps it. A zombie runs nothing. `ps` is asked, not /proc,
    # because macOS has no /proc and a check that cannot see a process
    # would pass there for ever.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if not state or state.startswith("Z"):
            return False
        time.sleep(0.01)
    return True


# The record laws.


def test_a_folder_outside_the_work_folder_is_refused_and_nothing_starts(
    any_executor: Executor, work: Path, tmp_path: Path
) -> None:
    marker = work / "marker"
    outside = command(work, "/usr/bin/touch", str(marker), cwd=str(tmp_path))
    refused = run(any_executor.run(outside))
    assert isinstance(refused, Failure) and refused.kind == "invalid_input"
    assert refused.effect == "none"
    assert not marker.exists()


@pytest.mark.parametrize(
    "where", ["an argument", "a variable", "a name", "text that is not UTF-8"]
)
def test_a_zero_byte_or_text_that_is_not_utf8_is_refused_and_nothing_starts(
    any_executor: Executor, work: Path, where: str
) -> None:
    marker = work / "marker"
    made = command(work, "/usr/bin/touch", str(marker))
    bad = {
        "an argument": replace(made, argv=(*made.argv, "made\0up")),
        "a variable": replace(made, env={"MADE_UP": "made\0up"}),
        "a name": replace(made, env={"MADE\0UP": "made-up"}),
        "text that is not UTF-8": replace(made, argv=(*made.argv, "made\udc80up")),
    }[where]
    before = open_descriptors()
    refused = run(any_executor.run(bad))
    assert isinstance(refused, Failure), refused
    assert (refused.kind, refused.effect) == ("invalid_input", "none")
    assert not marker.exists()
    assert open_descriptors() == before


def test_a_link_inside_the_work_folder_that_leads_out_is_refused(
    any_executor: Executor, work: Path, tmp_path: Path
) -> None:
    (work / "out").symlink_to(tmp_path)
    refused = run(any_executor.run(command(work, "/bin/true", cwd=str(work / "out"))))
    assert isinstance(refused, Failure) and refused.kind == "invalid_input"


def test_an_empty_command_and_limits_below_one_are_refused(
    any_executor: Executor, work: Path
) -> None:
    for bad in (
        command(work),
        command(work, "/bin/true", wall_ms=0),
        command(work, "/bin/true", capture_bytes=0),
    ):
        refused = run(any_executor.run(bad))
        assert isinstance(refused, Failure) and refused.kind == "invalid_input", bad


def test_a_folder_inside_the_work_folder_is_allowed(
    any_executor: Executor, work: Path
) -> None:
    (work / "inner").mkdir()
    result_of(
        run(any_executor.run(command(work, "/bin/true", cwd=str(work / "inner"))))
    )


def test_the_wall_is_the_same_record_of_plain_values_twice(
    any_executor: Executor,
) -> None:
    first, second = any_executor.wall(), any_executor.wall()
    assert first == second
    assert canonical_json(first) == canonical_json(second)


# The process laws.


def test_no_process_of_the_group_is_alive_after_a_cancel(
    executor: Executor, work: Path
) -> None:
    script = "sleep 30 & echo $! > pid; sleep 30"
    started = command(work, "/bin/sh", "-c", script)

    async def scene() -> ExecResult | Failure:
        task = asyncio.ensure_future(executor.run(started))
        await until_the_pid_or_the_end(task, work)
        task.cancel()
        return await task

    result = result_of(run(scene()))
    assert result.cancelled and result.signal == signal.SIGKILL
    assert not is_alive(int((work / "pid").read_text()))


def test_kill_all_kills_every_group_at_once_and_each_run_still_ends(
    executor: Executor, work: Path
) -> None:
    script = "sleep 30 & echo $! > pid; sleep 30"
    folders = (work / "a", work / "b")
    for folder in folders:
        folder.mkdir()

    async def scene() -> list[ExecResult | Failure]:
        runs = [
            executor.run(command(folder, "/bin/sh", "-c", script)) for folder in folders
        ]
        tasks = [asyncio.ensure_future(one) for one in runs]
        for task, folder in zip(tasks, folders, strict=True):
            await until_the_pid_or_the_end(task, folder)
        executor.kill_all()
        return await asyncio.wait_for(asyncio.gather(*tasks), 5)

    for ended, folder in zip(run(scene()), folders, strict=True):
        result = result_of(ended)
        assert result.signal == signal.SIGKILL
        assert not (result.cancelled or result.timed_out)
        assert not is_alive(int((folder / "pid").read_text()))


def test_no_process_of_the_group_is_alive_after_the_time_limit(
    executor: Executor, work: Path
) -> None:
    script = "sleep 30 & echo $! > pid; sleep 30"
    result = result_of(
        run(executor.run(command(work, "/bin/sh", "-c", script, wall_ms=500)))
    )
    assert result.timed_out and result.signal == signal.SIGKILL
    assert not is_alive(int((work / "pid").read_text()))


def test_a_marker_in_the_callers_environment_is_not_seen_inside(
    executor: Executor, work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAMPLIGHTER_TEST_MARKER", "seen")
    env = {**PATH, "GIVEN": "yes"}
    result = result_of(run(executor.run(command(work, "/usr/bin/env", env=env))))
    assert b"GIVEN=yes" in result.output
    assert b"LAMPLIGHTER_TEST_MARKER" not in result.output
    assert sorted(result.output.decode().split()) == ["GIVEN=yes", "PATH=/usr/bin:/bin"]


def test_a_flood_is_cut_at_the_limit_while_it_runs(
    executor: Executor, work: Path
) -> None:
    began = time.monotonic()
    result = result_of(
        run(executor.run(command(work, "/usr/bin/yes", capture_bytes=100_000)))
    )
    assert result.overflowed and len(result.output) == 100_000
    assert result.output[:4] == b"y\ny\n"
    assert result.signal == signal.SIGKILL
    assert not result.timed_out and not result.cancelled
    assert time.monotonic() - began < 5


def test_the_time_limit_kills_a_command_that_ignores_polite_signals(
    executor: Executor, work: Path
) -> None:
    script = "trap '' TERM INT HUP; sleep 30"
    result = result_of(
        run(executor.run(command(work, "/bin/sh", "-c", script, wall_ms=300)))
    )
    assert result.timed_out and result.signal == signal.SIGKILL


def test_one_byte_over_the_limit_is_overflowed_with_or_without_an_exit_code(
    executor: Executor, work: Path
) -> None:
    argv = ("/usr/bin/head", "-c", "1001", "/dev/zero")
    result = result_of(run(executor.run(command(work, *argv, capture_bytes=1_000))))
    assert result.overflowed and len(result.output) == 1_000
    assert result.exit_code == 0 or result.signal == signal.SIGKILL
    assert not result.timed_out and not result.cancelled


def test_output_at_the_limit_is_kept_whole_and_not_overflowed(
    executor: Executor, work: Path
) -> None:
    argv = ("/usr/bin/head", "-c", "1000", "/dev/zero")
    result = result_of(run(executor.run(command(work, *argv, capture_bytes=1_000))))
    assert (result.exit_code, result.overflowed) == (0, False)
    assert result.output == b"\0" * 1_000


def test_the_two_output_streams_come_as_one(executor: Executor, work: Path) -> None:
    script = "echo one; echo two >&2; echo three"
    result = result_of(run(executor.run(command(work, "/bin/sh", "-c", script))))
    assert result.output == b"one\ntwo\nthree\n"


def test_a_non_zero_exit_a_signal_and_closed_input_are_results(
    executor: Executor, work: Path
) -> None:
    exited = result_of(run(executor.run(command(work, "/bin/sh", "-c", "exit 3"))))
    assert (exited.exit_code, exited.signal) == (3, None)
    killed = result_of(
        run(executor.run(command(work, "/bin/sh", "-c", "kill -TERM $$")))
    )
    assert (killed.exit_code, killed.signal) == (None, signal.SIGTERM)
    assert not killed.timed_out and not killed.cancelled


def test_standard_input_is_closed_even_when_the_callers_is_open(
    executor: Executor, work: Path
) -> None:
    # The caller's input is a pipe that never ends, so a command that got
    # it would wait for the time limit.
    read_end, write_end = os.pipe()
    saved = os.dup(0)
    os.dup2(read_end, 0)
    try:
        read = result_of(run(executor.run(command(work, "/bin/cat", wall_ms=5_000))))
    finally:
        os.dup2(saved, 0)
        for descriptor in (saved, read_end, write_end):
            os.close(descriptor)
    assert (read.exit_code, read.output, read.timed_out) == (0, b"", False)


def test_a_program_that_cannot_start_is_unavailable(work: Path) -> None:
    # With no wall, the runner starts the program itself. Behind the wall it
    # starts sandbox-exec, which reports the missing program: see below.
    executor = DIRECT.make(work)
    before = open_descriptors()
    failed = run(executor.run(command(work, str(work / "no-such-program"))))
    assert isinstance(failed, Failure) and failed.kind == "unavailable"
    assert failed.effect == "none"
    assert open_descriptors() == before


@pytest.mark.sandbox
def test_behind_the_wall_a_program_that_does_not_exist_is_a_failed_result(
    work: Path,
) -> None:
    executor = WALLED.make(work)
    before = open_descriptors()
    ended = result_of(run(executor.run(command(work, str(work / "no-such-program")))))
    assert ended.exit_code not in (None, 0)
    assert open_descriptors() == before


def test_a_cancel_at_any_moment_of_the_start_gives_a_result_and_leaves_no_descriptor(
    executor: Executor, work: Path
) -> None:
    async def scene() -> None:
        before = open_descriptors()
        for moment in range(40):
            task = asyncio.ensure_future(
                executor.run(command(work, "/bin/sleep", "30"))
            )
            await asyncio.sleep(0)
            await asyncio.sleep(moment * 0.0005)
            task.cancel()
            result_of(await task)
        assert open_descriptors() == before

    run(scene())


def test_a_cancel_at_each_turn_around_the_end_of_the_work_gives_a_result(
    executor: Executor, work: Path
) -> None:
    async def scene() -> None:
        before = open_descriptors()
        for turns in range(1, 60):
            task = asyncio.ensure_future(
                executor.run(command(work, "/bin/sh", "-c", "echo done"))
            )
            for _ in range(turns):
                await asyncio.sleep(0)
            task.cancel()
            result = result_of(await task)
            assert result.output in (b"done\n", b""), result
        assert open_descriptors() == before

    run(scene())


def test_a_cancel_after_the_command_ended_gives_its_result(
    executor: Executor, work: Path
) -> None:
    async def scene() -> ExecResult | Failure:
        # The command ends at once, and a child out of the group holds the
        # pipe, so the cancel lands while the call waits for the pipe.
        script = leaves_the_group("sleep 5") + "; echo ended"
        task = asyncio.ensure_future(
            executor.run(command(work, "/bin/sh", "-c", script))
        )
        await until_the_pid_or_the_end(task, work)
        await asyncio.sleep(0.1)
        assert not task.done()
        task.cancel()
        return await task

    try:
        result = result_of(run(scene()))
    finally:
        os.kill(int((work / "pid").read_text()), signal.SIGKILL)
    assert (result.exit_code, result.output) == (0, b"ended\n")
    assert not result.cancelled


def test_a_second_cancel_changes_nothing(executor: Executor, work: Path) -> None:
    async def scene() -> ExecResult | Failure:
        before = open_descriptors()
        task = asyncio.ensure_future(executor.run(command(work, "/bin/sleep", "30")))
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.sleep(0.1)
        task.cancel()
        result = await task
        assert open_descriptors() == before
        return result

    assert result_of(run(scene())).cancelled


def test_a_child_that_left_the_group_does_not_hold_the_call(
    executor: Executor, work: Path
) -> None:
    began = time.monotonic()
    script = leaves_the_group("sleep 5") + "; echo parent-done"
    try:
        result = result_of(run(executor.run(command(work, "/bin/sh", "-c", script))))
        took = time.monotonic() - began
        # The known limit: a child that left the group lives on.
        assert is_alive(int((work / "pid").read_text()))
    finally:
        os.kill(int((work / "pid").read_text()), signal.SIGKILL)
    assert (result.exit_code, result.output) == (0, b"parent-done\n")
    assert took < 2


def test_output_that_comes_after_the_command_ended_counts_toward_the_limit(
    executor: Executor, work: Path
) -> None:
    script = leaves_the_group("select(undef, undef, undef, 0.1); print 'x' x 1001")
    result = result_of(
        run(executor.run(command(work, "/bin/sh", "-c", script, capture_bytes=1_000)))
    )
    assert (result.exit_code, result.output) == (0, b"x" * 1_000)
    assert result.overflowed


def test_the_next_program_of_a_killed_shell_line_never_starts(
    executor: Executor, work: Path
) -> None:
    # The kill of the first program must not wake the shell in time to
    # start the second.
    script = "sleep 30; touch marker"
    result = result_of(
        run(executor.run(command(work, "/bin/sh", "-c", script, wall_ms=500)))
    )
    assert result.timed_out
    time.sleep(0.3)
    assert not (work / "marker").exists()


def test_a_child_left_behind_by_a_command_that_ended_well_is_killed(
    executor: Executor, work: Path
) -> None:
    script = "sleep 30 & echo $! > pid"
    result = result_of(run(executor.run(command(work, "/bin/sh", "-c", script))))
    assert result.exit_code == 0
    assert not is_alive(int((work / "pid").read_text()))
