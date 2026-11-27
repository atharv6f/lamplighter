"""An executor that starts no process and answers each command from a function."""

from collections.abc import Callable
from pathlib import Path

from lamplighter.ports import (
    Command,
    ExecResult,
    Failure,
    JsonObject,
    refused_command,
)

_SOURCE = "Executor/fake"

type Answer = Callable[[Command], ExecResult | Failure]


class FakeExecutor:
    """Gives each command the answer `answer` makes for it. Satisfies Executor.

    It refuses a command the port says is never started before it asks
    `answer`, and notes every command it was given in `commands`.
    """

    def __init__(self, work: Path, answer: Answer) -> None:
        """Take `work` as the work folder, and answer commands with `answer`."""
        self._work = work.resolve()
        self._answer = answer
        self.commands: list[Command] = []

    async def run(self, command: Command) -> ExecResult | Failure:
        """Note the command, then refuse it or give its answer."""
        self.commands.append(command)
        refused = refused_command(command, self._work, _SOURCE)
        if refused is not None:
            return refused
        return self._answer(command)

    def wall(self) -> JsonObject:
        """Say that there is no wall."""
        return {"adapter": "fake"}
