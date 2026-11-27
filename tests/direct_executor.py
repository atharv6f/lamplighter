"""An executor with no wall, so the process rules are tested on Linux too.

It runs the same process code as the adapter for macOS, without the
prefix that puts up the wall. It lives under `tests/`, so the chat command
can never be built with it.
"""

from pathlib import Path

from lamplighter.adapters.command_run import kill_every_command, run_command
from lamplighter.ports import (
    Clock,
    Command,
    ExecResult,
    Failure,
    JsonObject,
    refused_command,
)

_SOURCE = "Executor/direct"


class DirectExecutor:
    """Runs each command as it is, with no wall. Satisfies Executor's record laws."""

    def __init__(self, work: Path, clock: Clock) -> None:
        self._work = work.resolve()
        self._clock = clock

    async def run(self, command: Command) -> ExecResult | Failure:
        refused = refused_command(command, self._work, _SOURCE)
        if refused is not None:
            return refused
        return await run_command(
            command.argv,
            command.cwd,
            command.env,
            command.wall_ms,
            command.capture_bytes,
            self._clock,
            _no_wall,
        )

    def wall(self) -> JsonObject:
        return {"adapter": "direct"}

    def kill_all(self) -> None:
        """Kill every command still running, at once."""
        kill_every_command()


async def _no_wall() -> Failure | None:
    return None
