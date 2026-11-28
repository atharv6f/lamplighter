"""The container executor against a real container: bytes, names, attacks and the sweep.

Each attack is a command an agent could send. The command runs as a user
that is not root, as in a trial.
"""

import asyncio
import os
import statistics
import time
from collections.abc import Generator, Iterator
from pathlib import Path

import pytest

from lamplighter.adapters import SystemIds
from lamplighter.adapters.container import ContainerExecutor
from lamplighter.ports import Command, ExecResult, Failure
from tests.container_kit import (
    RunningContainer,
    command_user,
    open_to_command_user,
    started_container,
)

pytestmark = pytest.mark.container

PATH = {"PATH": "/usr/local/bin:/usr/bin:/bin"}
# Each generation forks the next and ends, so no process id lives longer
# than a fork. Every 200 generations it writes the time, to show it lives.
CHAIN = r"""
import os, sys, time
stamp = sys.argv[1]
os.setsid()
n = 0
while True:
    if os.fork():
        os._exit(0)
    n += 1
    if n % 200 == 0:
        with open(stamp, "w") as f:
            f.write(str(time.time_ns()))
"""
# Freezes every process that is not root's, again and again so that a
# chain cannot outrun it, and then kills them all.
_FREEZE_AND_KILL = (
    "for r in $(seq 40); do for p in /proc/[0-9]*; do "
    '[ "$(stat -c %u $p 2>/dev/null)" != 0 ] && kill -STOP ${p#/proc/} 2>/dev/null;'
    " done; done; for p in /proc/[0-9]*; do "
    '[ "$(stat -c %u $p 2>/dev/null)" != 0 ] && kill -KILL ${p#/proc/} 2>/dev/null;'
    " done; true"
)
# The same for every process but the container's first two and this line's.
_FREEZE_ALL_BUT_THE_FIRST = (
    'keep=" 1 $(cat /proc/1/task/1/children) $$ "; for r in $(seq 40); do'
    ' for p in /proc/[0-9]*; do p=${p#/proc/}; case "$keep" in *" $p "*) continue;;'
    " esac; kill -STOP $p; done; done 2>/dev/null; for p in /proc/[0-9]*; do"
    ' p=${p#/proc/}; case "$keep" in *" $p "*) continue;; esac; kill -KILL $p;'
    " done 2>/dev/null; true"
)


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.getbasetemp()


@pytest.fixture(scope="module")
def shared(base: Path) -> Iterator[RunningContainer]:
    with started_container(base) as container:
        yield container


@pytest.fixture
def box(shared: RunningContainer) -> Generator[RunningContainer]:
    yield shared
    shared.inside(_FREEZE_AND_KILL)


@pytest.fixture
def own(base: Path) -> Iterator[RunningContainer]:
    with started_container(base) as container:
        yield container


@pytest.fixture
def work(tmp_path: Path, base: Path) -> Path:
    made = (tmp_path / "work").resolve()
    made.mkdir()
    open_to_command_user(made, base)
    return made


def contained(
    container: RunningContainer, work: Path, user: tuple[int, int] | None = None
) -> ContainerExecutor:
    user_id, group_id = user or command_user()
    return ContainerExecutor(container.as_root(), user_id, group_id, work, SystemIds())


def shell(work: Path, line: str, **changes: int | dict[str, str]) -> Command:
    return command(work, "/bin/bash", "-c", line, **changes)


def command(work: Path, *argv: str, **changes: int | dict[str, str]) -> Command:
    env = changes.get("env", PATH)
    wall_ms = changes.get("wall_ms", 10_000)
    cap = changes.get("cap", 100_000)
    assert isinstance(env, dict) and isinstance(wall_ms, int) and isinstance(cap, int)
    return Command(argv, str(work), env, "workspace_write", wall_ms, cap)


def result_of(outcome: ExecResult | Failure) -> ExecResult:
    assert isinstance(outcome, ExecResult), outcome
    return outcome


def ran(executor: ContainerExecutor, made: Command) -> ExecResult:
    return result_of(asyncio.run(executor.run(made)))


def open_descriptors() -> int:
    return len(os.listdir("/dev/fd"))


# Bytes and limits.


def test_every_byte_value_comes_back_exactly(box: RunningContainer, work: Path) -> None:
    every_byte = bytes(range(256)) * 3
    (work / "bytes").write_bytes(every_byte)
    result = ran(contained(box, work), command(work, "/bin/cat", "bytes"))
    assert (result.exit_code, result.output) == (0, every_byte)


def test_a_command_too_long_for_one_argument_is_refused_and_does_not_start(
    box: RunningContainer, work: Path
) -> None:
    long = shell(work, "touch marker; : " + "x" * 120_000)
    refused = asyncio.run(contained(box, work).run(long))
    assert isinstance(refused, Failure)
    assert (refused.kind, refused.effect) == ("invalid_input", "none")
    assert not (work / "marker").exists()


def test_single_quotes_count_after_quoting(box: RunningContainer, work: Path) -> None:
    executor = contained(box, work)
    fits = ran(executor, command(work, "/usr/bin/printf", "%s", "'" * 23_000))
    assert fits.output == b"'" * 23_000
    refused = asyncio.run(
        executor.run(command(work, "/usr/bin/printf", "%s", "'" * 25_000))
    )
    assert isinstance(refused, Failure) and refused.kind == "invalid_input"


# Names.


@pytest.mark.parametrize("name", ["--chdir", "-uPATH"])
def test_a_variable_named_like_an_option_is_only_a_variable(
    box: RunningContainer, work: Path, name: str
) -> None:
    # First, since `env` stops reading options at the first variable.
    env = {name: "/", **PATH}
    executor = contained(box, work)
    listed = ran(executor, command(work, "/usr/bin/env", env=env))
    assert sorted(listed.output.decode().split()) == sorted(
        [f"{name}=/", f"PATH={PATH['PATH']}"]
    )
    folder = ran(executor, command(work, "/bin/pwd", env=env))
    assert folder.output == f"{work}\n".encode()


@pytest.mark.parametrize("program", ["-S", "A=B"])
def test_a_program_named_like_an_option_or_a_variable_does_not_exist(
    box: RunningContainer, work: Path, program: str
) -> None:
    result = ran(contained(box, work), command(work, program, "/usr/bin/env"))
    assert result.exit_code not in (None, 0)
    assert b"PATH=" not in result.output


# The command against its own result.

_TOKEN_OF_PARENT = (
    "tok=$(tr '\\0' '\\n' </proc/$PPID/cmdline | grep -A3 '^/tmp/.run-' | sed -n 4p)"
)


def test_a_command_cannot_write_an_answer_line_of_its_own(
    box: RunningContainer, work: Path
) -> None:
    forge = (
        f"{_TOKEN_OF_PARENT}\n"
        '{ echo "$tok exited 0 6"; printf forged | base64; } >/proc/$PPID/fd/1\n'
        "echo the real output; exit 3"
    )
    result = ran(contained(box, work), shell(work, forge))
    assert result.exit_code == 3
    assert result.output.endswith(b"Permission denied\nthe real output\n")


def test_a_command_cannot_rewrite_the_captured_output(
    box: RunningContainer, work: Path
) -> None:
    rewrite = (
        f"{_TOKEN_OF_PARENT}\n"
        "echo real\n"
        'setsid bash -c "sleep 0.05; printf rewritten > /tmp/.run-$tok/out"'
        " >/dev/null 2>&1 &\n"
        "sleep 0.4; exit 3"
    )
    result = ran(contained(box, work), shell(work, rewrite))
    assert (result.exit_code, result.output) == (3, b"real\n")


def test_a_command_cannot_signal_its_supervisor(
    box: RunningContainer, work: Path
) -> None:
    line = "for s in TERM USR1 STOP KILL; do kill -$s $PPID; done; echo after"
    result = ran(contained(box, work), shell(work, line))
    assert result.exit_code == 0 and not result.cancelled
    assert result.output.count(b"Operation not permitted") == 4
    assert result.output.endswith(b"after\n")


def test_a_kill_of_every_process_it_may_is_its_own_result(
    box: RunningContainer, work: Path
) -> None:
    executor = contained(box, work)
    result = ran(executor, shell(work, "kill -9 -1; echo survived; exit 4"))
    assert (result.exit_code, result.output) == (4, b"survived\n")
    assert ran(executor, shell(work, "echo next")).output == b"next\n"


def test_a_process_left_to_signal_everything_cancels_no_later_command(
    box: RunningContainer, work: Path
) -> None:
    (work / "pester.sh").write_text(
        "trap '' TERM USR1; while :; do for p in /proc/[0-9]*; do"
        " kill -TERM ${p#/proc/}; kill -USR1 ${p#/proc/}; done 2>/dev/null; done\n"
    )
    executor = contained(box, work)
    ran(executor, shell(work, "setsid bash pester.sh >/dev/null 2>&1 &"))
    for _ in range(5):
        # The later command is the same user's, so the signals may end it,
        # but they never reach its supervisor.
        later = ran(executor, command(work, "/bin/sleep", "0.3"))
        assert not later.cancelled
    assert processes_of(box, command_user()[0]) > 0


# A lost answer.


def until_the_script_is_up(container: RunningContainer) -> None:
    deadline = time.monotonic() + 5
    while not container.inside("cat /tmp/.run-*/supervisor 2>/dev/null").strip():
        assert time.monotonic() < deadline
        time.sleep(0.02)


def test_an_answer_cut_by_a_kill_of_the_supervisor_is_unavailable(
    box: RunningContainer, work: Path
) -> None:
    executor = contained(box, work)

    async def scene() -> ExecResult | Failure:
        running = asyncio.ensure_future(executor.run(command(work, "/bin/sleep", "5")))
        await asyncio.to_thread(until_the_script_is_up, box)
        box.inside('kill -KILL "$(cat /tmp/.run-*/supervisor)"')
        return await running

    failed = asyncio.run(scene())
    assert isinstance(failed, Failure)
    assert (failed.kind, failed.effect) == ("unavailable", "unknown")
    assert executor.broken == failed


@pytest.mark.parametrize("ends", ["removed", "stopped"])
def test_a_container_that_goes_away_mid_command_is_unavailable_and_leaves_nothing(
    own: RunningContainer, work: Path, ends: str
) -> None:
    executor = contained(own, work)

    async def scene() -> ExecResult | Failure:
        before = open_descriptors()
        running = asyncio.ensure_future(executor.run(command(work, "/bin/sleep", "30")))
        await asyncio.to_thread(until_the_script_is_up, own)
        await asyncio.to_thread(own.remove if ends == "removed" else own.stop)
        outcome = await asyncio.wait_for(running, 20)
        assert open_descriptors() == before
        assert asyncio.all_tasks() == {asyncio.current_task()}
        return outcome

    failed = asyncio.run(scene())
    assert isinstance(failed, Failure)
    assert (failed.kind, failed.effect) == ("unavailable", "unknown")


# The script's timer, which a lost wake-up would stall.


def test_a_time_limit_and_a_cancel_end_the_command_soon_twenty_times_each(
    box: RunningContainer, work: Path
) -> None:
    executor = contained(box, work)

    async def cancelled() -> ExecResult | Failure:
        running = asyncio.ensure_future(executor.run(command(work, "/bin/sleep", "30")))
        await asyncio.sleep(0.1)
        running.cancel()
        return await running

    for _ in range(20):
        began = time.monotonic()
        timed = ran(executor, command(work, "/bin/sleep", "30", wall_ms=200))
        assert timed.timed_out and time.monotonic() - began < 1.5
        began = time.monotonic()
        stopped = result_of(asyncio.run(cancelled()))
        assert stopped.cancelled and time.monotonic() - began < 1.5


# `kill_all` in a command's first moments.


@pytest.mark.parametrize("delay_ms", [1, 5, 20])
def test_kill_all_just_after_the_start_still_kills_the_command(
    box: RunningContainer, work: Path, delay_ms: int
) -> None:
    executor = contained(box, work)

    async def scene() -> ExecResult | Failure:
        running = asyncio.ensure_future(
            executor.run(shell(work, "sleep 3; echo ran-to-its-end"))
        )
        await asyncio.sleep(delay_ms / 1000)
        executor.kill_all()
        return await asyncio.wait_for(running, 10)

    for _ in range(6):
        began = time.monotonic()
        result = result_of(asyncio.run(scene()))
        assert result.signal == 9 and b"ran-to-its-end" not in result.output
        assert not (result.cancelled or result.timed_out)
        assert time.monotonic() - began < 2.5


# `mark` and `sweep`.


def lives(stamp: Path) -> bool:
    first = stamp.read_text() if stamp.exists() else ""
    time.sleep(0.5)
    return (stamp.read_text() if stamp.exists() else "") != first


def any_lives(stamps: list[Path]) -> bool:
    first = [stamp.read_text() if stamp.exists() else "" for stamp in stamps]
    time.sleep(0.5)
    return [stamp.read_text() if stamp.exists() else "" for stamp in stamps] != first


def processes_of(container: RunningContainer, user: int) -> int:
    line = (
        "n=0; for p in /proc/[0-9]*; do read -r l <$p/stat 2>/dev/null || continue;"
        ' r=${l##*) }; [ "${r%% *}" = Z ] && continue;'
        f' [ "$(stat -c %u $p 2>/dev/null)" = {user} ] && n=$((n + 1));'
        " done 2>/dev/null; echo $n"
    )
    return int(container.inside(line).strip())


def test_the_sweep_kills_a_detached_writer(box: RunningContainer, work: Path) -> None:
    executor = contained(box, work)
    assert asyncio.run(executor.mark())
    writer = "while :; do echo x >> written; sleep 0.01; done"
    ran(executor, shell(work, f"setsid bash -c '{writer}' >/dev/null 2>&1 & sleep 0.2"))
    assert asyncio.run(executor.sweep())
    size = (work / "written").stat().st_size
    time.sleep(0.3)
    assert (work / "written").stat().st_size == size
    assert processes_of(box, command_user()[0]) == 0


def test_the_sweep_kills_a_process_that_keeps_starting_others(
    box: RunningContainer, work: Path
) -> None:
    executor = contained(box, work)
    assert asyncio.run(executor.mark())
    starter = "while :; do sleep 30 & sleep 0.005; done"
    ran(
        executor, shell(work, f"setsid bash -c '{starter}' >/dev/null 2>&1 & sleep 0.3")
    )
    assert processes_of(box, command_user()[0]) > 10
    assert asyncio.run(executor.sweep())
    assert processes_of(box, command_user()[0]) == 0


@pytest.mark.parametrize("chains", [1, 6])
def test_the_sweep_kills_fork_chains_and_says_so(
    own: RunningContainer, work: Path, chains: int
) -> None:
    (work / "chain.py").write_text(CHAIN)
    stamps = [work / f"stamp{i}" for i in range(chains)]
    start = " ".join(
        f"(python3 chain.py {stamp} >/dev/null 2>&1 &);" for stamp in stamps
    )
    for _ in range(12):
        executor = contained(own, work)
        assert asyncio.run(executor.mark())
        ran(executor, shell(work, f"{start} sleep 0.3"))
        assert all(lives(stamp) for stamp in stamps)
        assert asyncio.run(executor.sweep())
        assert not any(lives(stamp) for stamp in stamps)


# A chain slips past a pass only when its new process id wraps around past
# the pass's listing, so each run asks for several sweeps while it lives.
@pytest.mark.parametrize("chains", [1, 6])
def test_with_root_as_the_command_user_the_sweep_is_never_true_with_a_chain_alive(
    own: RunningContainer, work: Path, chains: int
) -> None:
    (work / "chain.py").write_text(CHAIN)
    stamps = [work / f"stamp{i}" for i in range(chains)]
    start = " ".join(
        f"(python3 chain.py {stamp} >/dev/null 2>&1 &);" for stamp in stamps
    )
    for _ in range(20):
        executor = contained(own, work, user=(0, 0))
        assert asyncio.run(executor.mark())
        ran(executor, shell(work, f"{start} sleep 0.3"))
        for _ in range(5):
            if asyncio.run(executor.sweep()):
                assert not any_lives(stamps)
                break
        if any_lives(stamps):
            own.inside(_FREEZE_ALL_BUT_THE_FIRST)


def test_the_sweep_skips_zombies_in_a_container_with_no_init(
    base: Path, work: Path
) -> None:
    with started_container(base, init=False) as plain:
        executor = contained(plain, work)
        assert asyncio.run(executor.mark())
        # Each child ends at once, and its parent, which never collects it,
        # is handed to the first process, which never collects either.
        zombies = "for i in $(seq 20); do sleep 0.01 & done; exec sleep 30"
        ran(
            executor,
            shell(work, f"setsid bash -c '{zombies}' >/dev/null 2>&1 & sleep 0.3"),
        )
        assert asyncio.run(executor.sweep())
        assert (
            int(plain.inside("grep -l '^[0-9]* ([^)]*) Z' /proc/[0-9]*/stat | wc -l"))
            > 0
        )


def test_the_sweep_spares_the_processes_alive_at_the_mark(
    box: RunningContainer, work: Path
) -> None:
    # A process of root's that runs before the mark stands for those of
    # the container's own, such as the one that keeps it running.
    roots = box.inside("setsid sleep 300 >/dev/null 2>&1 & echo $!").strip()
    first = box.inside("cat /proc/1/task/1/children").split()
    executor = contained(box, work)
    assert asyncio.run(executor.mark())
    ran(executor, shell(work, "setsid sleep 30 >/dev/null 2>&1 & echo left"))
    assert asyncio.run(executor.sweep())
    assert all(box.is_alive(int(pid)) for pid in (roots, "1", *first))
    assert box.inside("echo still-running").strip() == "still-running"
    box.kill(int(roots))


# The time one command takes.


def test_fifty_commands_each_answer_and_their_median_time_is_printed(
    box: RunningContainer, work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    executor = contained(box, work)
    times: list[float] = []
    for n in range(50):
        began = time.monotonic()
        result = ran(executor, shell(work, f"echo {n}"))
        times.append(time.monotonic() - began)
        assert (result.exit_code, result.output) == (0, f"{n}\n".encode())
    # Printed past the capture, so that a run with -q shows the number.
    with capsys.disabled():
        print(f"\nmedian of 50 commands: {statistics.median(times) * 1000:.0f} ms")


# The shared folder.


def test_a_file_each_side_wrote_can_be_changed_by_the_other(
    box: RunningContainer, work: Path
) -> None:
    executor = contained(box, work, user=(os.getuid(), os.getgid()))
    ran(executor, shell(work, "echo from-the-command > made-inside"))
    inside = work / "made-inside"
    inside.write_text(inside.read_text() + "edited outside\n")
    (work / "made-outside").write_text("from the tools\n")
    changed = ran(
        executor, shell(work, "echo changed >> made-outside; cat made-inside")
    )
    assert changed.output == b"from-the-command\nedited outside\n"
    assert (work / "made-outside").read_text() == "from the tools\nchanged\n"
