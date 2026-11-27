"""The wall's adapter on any system: what it runs, its self-test, and its record.

The real wall needs macOS and runs under the `sandbox` marker. Here a
stand-in for sandbox-exec takes its place: it writes down what it was
given and then runs the command, or refuses the way the test asks. So the
prefix, the self-test and what the adapter does with each answer are
tested on every push.
"""

import asyncio
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from lamplighter.adapters import SystemClock
from lamplighter.adapters.seatbelt import SeatbeltExecutor, executor
from lamplighter.adapters.seatbelt.profile import PROFILES, profiles_sha256
from lamplighter.ports import Command, ExecResult, Failure

# What the stand-in does: "faithful" refuses a command that names the store's
# folder and runs any other, "open" runs every command, "closed" refuses
# every one, "lists" is faithful but lets a listing through, "leaks" makes
# the file it was asked to refuse and then says it refused, "stalls" never
# ends a command that names the store, and "broken" fails the way a profile
# with an error does.
STAND_IN = """#!{python}
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
with open(os.path.join(here, "calls.jsonl"), "a") as calls:
    calls.write(json.dumps(args) + "\\n")
mode = open(os.path.join(here, "mode")).read().strip()
end = args.index("--")
params = dict(a[2:].split("=", 1) for a in args[:end] if a.startswith("-D"))
command = args[end + 1 :]
if mode == "broken":
    sys.stderr.write("sandbox-exec: syntax error\\n")
    sys.exit(65)
if mode == "lists" and command[0] == "/bin/ls":
    os.execv(command[0], command)
named_state = any(part.startswith(params["STATE"]) for part in command)
if mode == "leaks" and named_state and command[0] == "/usr/bin/touch":
    open(command[1], "w").close()
if mode == "stalls" and named_state:
    time.sleep(30)
if mode == "closed" or (mode != "open" and named_state):
    sys.stderr.write("Operation not permitted\\n")
    sys.exit(1)
os.execv(command[0], command)
"""


@dataclass
class Folders:
    """The three folders, and the stand-in's own folder."""

    work: Path
    scratch: Path
    state: Path
    stand_in: Path

    def mode(self, mode: str) -> None:
        (self.stand_in / "mode").write_text(mode)

    def calls(self) -> list[list[str]]:
        log = self.stand_in / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def executor(self) -> SeatbeltExecutor:
        return SeatbeltExecutor(
            str(self.work), str(self.scratch), str(self.state), SystemClock()
        )


@pytest.fixture
def folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Folders:
    made = Folders(
        tmp_path / "work", tmp_path / "scratch", tmp_path / "state", tmp_path / "wall"
    )
    for folder in (made.work, made.scratch / "tmp", made.state, made.stand_in):
        folder.mkdir(parents=True)
    program = made.stand_in / "sandbox-exec"
    program.write_text(STAND_IN.format(python=sys.executable))
    program.chmod(stat.S_IRWXU)
    made.mode("faithful")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(executor, "SANDBOX_EXEC", str(program))
    return made


def command(work: Path, *argv: str) -> Command:
    return Command(
        argv=argv,
        cwd=str(work),
        env={"PATH": "/usr/bin:/bin"},
        profile="read_only",
        wall_ms=10_000,
        capture_bytes=10_000,
    )


def test_on_linux_the_wall_is_unavailable_and_nothing_starts(tmp_path: Path) -> None:
    if sys.platform == "darwin":
        pytest.skip("this test is for systems with no sandbox-exec")
    marker = tmp_path / "marker"
    wall = SeatbeltExecutor(str(tmp_path), str(tmp_path), str(tmp_path), SystemClock())
    failed = asyncio.run(wall.run(command(tmp_path, "/usr/bin/touch", str(marker))))
    assert isinstance(failed, Failure)
    assert (failed.kind, failed.effect) == ("unavailable", "none")
    assert failed.message == f"The wall needs macOS, and this is {sys.platform}."
    assert not marker.exists()


def test_a_missing_sandbox_exec_is_unavailable(
    folders: Folders, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(executor, "SANDBOX_EXEC", str(folders.stand_in / "missing"))
    failed = asyncio.run(folders.executor().run(command(folders.work, "/usr/bin/true")))
    assert isinstance(failed, Failure) and failed.kind == "unavailable"
    assert "and it is not there" in failed.message
    assert folders.calls() == []


def test_the_command_runs_behind_the_prefix_after_the_self_test(
    folders: Folders,
) -> None:
    wall = folders.executor()
    ran = asyncio.run(wall.run(command(folders.work, "/bin/echo", "made-up")))
    assert isinstance(ran, ExecResult)
    assert (ran.exit_code, ran.output) == (0, b"made-up\n")
    *test, real = folders.calls()
    head = [
        "-p",
        PROFILES["workspace_write"],
        f"-DWORK={folders.work.resolve()}",
        f"-DSCRATCH={folders.scratch.resolve()}",
        f"-DSTATE={folders.state.resolve()}",
        "--",
    ]
    name = f".lamplighter-wall-test-{os.getpid()}"
    assert test == [
        [*head, "/usr/bin/touch", f"{folders.scratch.resolve()}/tmp/{name}"],
        [*head, "/usr/bin/touch", f"{folders.state.resolve()}/{name}"],
        [*head, "/bin/ls", str(folders.state.resolve())],
    ]
    assert real == [
        "-p",
        PROFILES["read_only"],
        *head[2:],
        "/bin/echo",
        "made-up",
    ]
    assert list((folders.scratch / "tmp").iterdir()) == []
    assert list(folders.state.iterdir()) == []


def test_the_self_test_runs_once_for_many_commands(folders: Folders) -> None:
    wall = folders.executor()

    async def scene() -> None:
        await asyncio.gather(
            *(wall.run(command(folders.work, "/usr/bin/true")) for _ in range(3))
        )
        await wall.run(command(folders.work, "/usr/bin/true"))

    asyncio.run(scene())
    assert len(folders.calls()) == 3 + 4


@pytest.mark.parametrize(
    ("mode", "why"),
    [
        ("open", "a write to {state} was not refused"),
        ("leaks", "a write to {state} was not refused"),
        ("closed", "a write to {scratch}/tmp was refused"),
        ("broken", "a write to {scratch}/tmp was refused"),
    ],
)
def test_a_wall_that_fails_its_self_test_runs_nothing_ever_again(
    folders: Folders, mode: str, why: str
) -> None:
    folders.mode(mode)
    wall = folders.executor()
    marker = folders.work / "marker"
    first = asyncio.run(wall.run(command(folders.work, "/usr/bin/touch", str(marker))))
    folders.mode("faithful")
    second = asyncio.run(wall.run(command(folders.work, "/usr/bin/touch", str(marker))))
    reason = why.format(
        state=folders.state.resolve(), scratch=folders.scratch.resolve()
    )
    for failed in (first, second):
        assert isinstance(failed, Failure)
        assert (failed.kind, failed.effect) == ("unavailable", "none")
        assert failed.message == (
            f"The wall's self-test failed: {reason}. No command runs until the"
            " harness starts again."
        )
    assert not marker.exists()
    assert len(folders.calls()) <= 3
    assert all(str(marker) not in call for call in folders.calls())
    assert list(folders.state.iterdir()) == []


def test_a_failed_self_test_logs_what_sandbox_exec_said(folders: Folders) -> None:
    folders.mode("broken")
    failed = asyncio.run(folders.executor().run(command(folders.work, "/usr/bin/true")))
    assert isinstance(failed, Failure)
    assert failed.detail == {
        "exit_code": 65,
        "signal": None,
        "output": "sandbox-exec: syntax error\n",
    }
    assert "syntax" not in failed.message


def test_a_wall_that_lists_the_store_fails_its_self_test(folders: Folders) -> None:
    folders.mode("lists")
    failed = asyncio.run(folders.executor().run(command(folders.work, "/usr/bin/true")))
    assert isinstance(failed, Failure)
    assert f"a listing of {folders.state.resolve()} was not refused" in failed.message


def test_a_cancel_during_the_self_test_still_gives_a_result_and_keeps_the_wall(
    folders: Folders,
) -> None:
    wall = folders.executor()

    async def scene() -> tuple[ExecResult | Failure, ExecResult | Failure]:
        call = asyncio.ensure_future(
            wall.run(command(folders.work, "/bin/sleep", "30"))
        )
        # Long enough for the self-test to have begun, and too short for it
        # to have ended: each of its three commands starts a Python.
        while not folders.calls():
            await asyncio.sleep(0.001)
        call.cancel()
        stopped = await call
        after = await wall.run(command(folders.work, "/bin/echo", "after"))
        return stopped, after

    stopped, after = asyncio.run(scene())
    assert isinstance(stopped, ExecResult) and stopped.cancelled
    assert isinstance(after, ExecResult) and after.output == b"after\n"
    # The cancelled command may be killed before the stand-in writes it down.
    assert len(folders.calls()) in (4, 5)


def test_a_command_outside_the_work_folder_is_refused_before_the_self_test(
    folders: Folders, tmp_path: Path
) -> None:
    failed = asyncio.run(folders.executor().run(command(tmp_path, "/usr/bin/true")))
    assert isinstance(failed, Failure) and failed.kind == "invalid_input"
    assert folders.calls() == []


def test_the_wall_names_its_profiles_and_the_system(folders: Folders) -> None:
    wall = folders.executor()
    record = wall.wall()
    assert set(record) == {"adapter", "profile_sha256", "os_version", "kernel"}
    assert record["adapter"] == "seatbelt"
    assert record["profile_sha256"] == profiles_sha256()
    assert wall.wall() == record


def test_the_profiles_are_pinned_and_differ_only_in_writing() -> None:
    # A change to a profile changes the config id, so it must be on purpose:
    # this value changes with it.
    assert profiles_sha256() == (
        "913be26b7e055c79d7a41a79a9c5d960ab9765ccc3eddf580afbf575f556f6be"
    )
    read_only = PROFILES["read_only"].splitlines()
    added = [
        line
        for line in PROFILES["workspace_write"].splitlines()
        if line not in read_only
    ]
    assert added == [
        '(allow file-write* (subpath (param "WORK")) (subpath (param "SCRATCH")))',
        "(deny file-write-unlink",
        '  (require-all (literal (param "WORK")) (vnode-type DIRECTORY)))',
        "(deny file-write-unlink",
        '  (require-all (literal (param "SCRATCH")) (vnode-type DIRECTORY)))',
    ]
    for text in PROFILES.values():
        lines = text.splitlines()
        assert lines[:2] == ["(version 1)", "(deny default)"]
        assert lines[-1] == '(deny file-read* file-write* (subpath (param "STATE")))'
        assert "network" not in text


def test_a_wall_whose_store_command_never_ends_fails_its_self_test(
    folders: Folders, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A command killed at its time limit refused nothing.
    folders.mode("stalls")
    monkeypatch.setattr(executor, "SELF_TEST_MS", 500)
    failed = asyncio.run(folders.executor().run(command(folders.work, "/usr/bin/true")))
    assert isinstance(failed, Failure)
    assert f"a write to {folders.state.resolve()} was not refused" in failed.message


@pytest.mark.parametrize("left", ["file", "link", "empty folder"])
def test_what_a_command_left_at_the_test_name_is_cleared_first(
    folders: Folders, left: str
) -> None:
    name = folders.scratch / "tmp" / f".lamplighter-wall-test-{os.getpid()}"
    if left == "file":
        name.write_text("made-up")
    elif left == "link":
        name.symlink_to(folders.state)
    else:
        name.mkdir()
    ran = asyncio.run(folders.executor().run(command(folders.work, "/usr/bin/true")))
    assert isinstance(ran, ExecResult) and ran.exit_code == 0, ran
    assert list((folders.scratch / "tmp").iterdir()) == []
    assert list(folders.state.iterdir()) == []


def test_a_folder_with_files_at_the_test_name_stops_the_wall_and_raises_nothing(
    folders: Folders,
) -> None:
    name = folders.scratch / "tmp" / f".lamplighter-wall-test-{os.getpid()}"
    name.mkdir()
    (name / "inside").write_text("made-up")
    wall = folders.executor()
    for _ in range(2):
        failed = asyncio.run(wall.run(command(folders.work, "/usr/bin/true")))
        assert isinstance(failed, Failure)
        assert (failed.kind, failed.effect) == ("unavailable", "none")
        assert failed.message == (
            f"The wall's self-test could not run: {name.resolve()} is in the way."
            " Remove it, then start again."
        )
    assert folders.calls() == []
