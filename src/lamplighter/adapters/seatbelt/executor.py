"""The wall on macOS: every command runs under sandbox-exec, after a self-test."""

import asyncio
import os
import platform
import stat
import sys
from pathlib import Path

from lamplighter.adapters.command_run import kill_every_command, run_command
from lamplighter.adapters.seatbelt.profile import PROFILES, profiles_sha256
from lamplighter.ports import (
    Clock,
    Command,
    ExecResult,
    Failure,
    JsonObject,
    Profile,
    refused_command,
)

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_SOURCE = "Executor/seatbelt"
SELF_TEST_MS = 5_000
_TEST_CAPTURE = 65_536
_DETAIL_BYTES = 500


class SeatbeltExecutor:
    """Runs each command under Apple's sandbox. Satisfies Executor.

    The three folders are resolved once, because a profile names real
    paths and on macOS `/tmp` and `/var` are links into `/private`. The
    first call runs the self-test, and its answer is kept for the life of
    the process: a wall that failed once is not trusted again.
    """

    def __init__(self, work: str, scratch: str, state: str, clock: Clock) -> None:
        """Wall commands into `work` and `scratch`, and away from `state`."""
        self._work = os.path.realpath(work)
        self._scratch = os.path.realpath(scratch)
        self._state = os.path.realpath(state)
        self._clock = clock
        self._wall: JsonObject = {
            "adapter": "seatbelt",
            "profile_sha256": profiles_sha256(),
            "os_version": platform.mac_ver()[0],
            "kernel": platform.version(),
        }
        self._testing = asyncio.Lock()
        self._tested = False
        self._test_failure: Failure | None = None

    async def run(self, command: Command) -> ExecResult | Failure:
        """Run the command behind the wall, or give why the wall is not up."""
        refused = refused_command(command, Path(self._work), _SOURCE)
        if refused is not None:
            return refused
        return await run_command(
            [*self._prefix(command.profile), *command.argv],
            command.cwd,
            command.env,
            command.wall_ms,
            command.capture_bytes,
            self._clock,
            self._wall_is_up,
        )

    def wall(self) -> JsonObject:
        """Give the record that names this wall, the same for the process's life."""
        return dict(self._wall)

    def kill_all(self) -> None:
        """Kill every command still running, at once."""
        kill_every_command()

    def _prefix(self, profile: Profile) -> list[str]:
        return [
            SANDBOX_EXEC,
            "-p",
            PROFILES[profile],
            f"-DWORK={self._work}",
            f"-DSCRATCH={self._scratch}",
            f"-DSTATE={self._state}",
            "--",
        ]

    async def _wall_is_up(self) -> Failure | None:
        # The runner awaits this behind its shield, so no cancel reaches the
        # self-test. A killed self-test command would read as a failed wall.
        async with self._testing:
            if not self._tested:
                self._test_failure = await self._self_test()
                self._tested = True
        return self._test_failure

    async def _self_test(self) -> Failure | None:
        if sys.platform != "darwin":
            return _unavailable(f"The wall needs macOS, and this is {sys.platform}.")
        if not os.path.isfile(SANDBOX_EXEC):
            return _unavailable(f"The wall needs {SANDBOX_EXEC}, and it is not there.")
        name = f".lamplighter-wall-test-{os.getpid()}"
        allowed = Path(self._scratch, "tmp", name)
        denied = Path(self._state, name)
        # A command may write in the scratch folder, so it may have left
        # something at the test's name.
        if not _cleared(allowed):
            return _unavailable(
                f"The wall's self-test could not run: {allowed} is in the way."
                " Remove it, then start again."
            )
        wrote = await self._test_run("/usr/bin/touch", str(allowed))
        if isinstance(wrote, Failure):
            return wrote
        made = allowed.is_file()
        _cleared(allowed)
        if wrote.exit_code != 0 or not made:
            return _failed_test(f"a write to {allowed.parent} was refused", wrote)
        into_state = await self._test_run("/usr/bin/touch", str(denied))
        if isinstance(into_state, Failure):
            return into_state
        leaked = denied.exists()
        denied.unlink(missing_ok=True)
        # A command that did not end refused nothing.
        if into_state.exit_code in (0, None) or leaked:
            return _failed_test(f"a write to {self._state} was not refused", into_state)
        listed = await self._test_run("/bin/ls", self._state)
        if isinstance(listed, Failure):
            return listed
        if listed.exit_code in (0, None):
            return _failed_test(f"a listing of {self._state} was not refused", listed)
        return None

    async def _test_run(self, *argv: str) -> ExecResult | Failure:
        ran = await run_command(
            [*self._prefix("workspace_write"), *argv],
            self._work,
            {},
            SELF_TEST_MS,
            _TEST_CAPTURE,
            self._clock,
            _tested_already,
        )
        if isinstance(ran, Failure):
            return _unavailable(f"The wall could not be started: {ran.message}")
        return ran


def _cleared(path: Path) -> bool:
    """Remove a file, a link or an empty folder at `path`, and say if none is left."""
    try:
        found = path.lstat()
    except FileNotFoundError:
        return True
    try:
        if stat.S_ISDIR(found.st_mode):
            path.rmdir()
        else:
            path.unlink()
    except OSError:
        return False
    return True


async def _tested_already() -> Failure | None:
    return None


def _failed_test(what: str, result: ExecResult) -> Failure:
    # What sandbox-exec printed goes to the log, not into the message.
    return Failure(
        kind="unavailable",
        effect="none",
        message=(
            f"The wall's self-test failed: {what}. No command runs until the"
            " harness starts again."
        ),
        source=_SOURCE,
        detail={
            "exit_code": result.exit_code,
            "signal": result.signal,
            "output": result.output[:_DETAIL_BYTES].decode("utf-8", "replace"),
        },
    )


def _unavailable(message: str) -> Failure:
    return Failure(kind="unavailable", effect="none", message=message, source=_SOURCE)
