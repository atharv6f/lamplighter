"""The port that runs one command behind a wall, and its records."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from lamplighter.ports.failure import Failure
from lamplighter.ports.json_value import JsonObject

type Profile = Literal["read_only", "workspace_write"]


@dataclass(frozen=True, slots=True)
class Command:
    """One command to run: the program and its arguments, never one shell line.

    `cwd` is the work folder or a folder inside it. `env` is every variable
    the command gets. `wall_ms` is its time limit, and `capture_bytes` the
    most output that is kept.
    """

    argv: tuple[str, ...]
    cwd: str
    env: Mapping[str, str]
    profile: Profile
    wall_ms: int
    capture_bytes: int


@dataclass(frozen=True, slots=True)
class ExecResult:
    """How a command ended, and what it printed.

    `exit_code` is set when it ended by itself, and `signal` when a signal
    ended it, never both. `output` holds both output streams as one, up to
    the capture limit. `overflowed` says output was left out. `timed_out`
    and `cancelled` say the kill that the limit or the cancel brought is
    what ended it, so neither comes with an exit code.
    """

    exit_code: int | None
    signal: int | None
    output: bytes
    overflowed: bool
    timed_out: bool
    cancelled: bool


class Executor(Protocol):
    """Runs one command at a time, each behind the same wall.

    Callers can rely on this:

    - When `run` returns, the command and every process still in its group
      are dead. A child that left the group is not.
    - The command sees exactly the variables in `env`.
    - Under `workspace_write`, a write outside the work folder and the
      scratch folder fails. Under `read_only`, every write fails, but for
      `/dev/null`. A command has no network. The store's folder cannot be
      read or written from inside.
    - If the wall cannot be put up, nothing runs and `run` gives
      `unavailable`. No path runs a command without the wall.
    - Output is counted while the command runs, and at `capture_bytes` the
      command is killed. Standard input is closed.
    - A `run` that has begun and is then cancelled still returns a result,
      wherever the cancel lands. A second cancel changes nothing.
    - A non-zero exit, a time out and a flood are results. A `Failure` means
      the command was not started: `unavailable`, or `invalid_input` for a
      `cwd` outside the work folder, an empty `argv` or a limit below 1.
    - `wall()` gives the same record of plain values for the life of the
      process, saying which wall this is.
    """

    async def run(self, command: Command) -> ExecResult | Failure:
        """Run the command behind the wall, and give how it ended."""
        ...

    def wall(self) -> JsonObject:
        """Give the record of the wall the commands run behind."""
        ...


def refused_command(command: Command, work: Path, source: str) -> Failure | None:
    """Give `invalid_input` for a command the port says is never started.

    `work` is the resolved work folder. The `cwd` is resolved before it is
    compared, so a link inside the work folder that leads out is refused.
    """
    if not command.argv:
        return _invalid("A command needs a program to run.", source)
    if command.wall_ms < 1 or command.capture_bytes < 1:
        return _invalid("A command's limits must be 1 or more.", source)
    cwd = Path(os.path.realpath(command.cwd))
    if not cwd.is_relative_to(work):
        return _invalid(f"The folder {command.cwd} is outside the work folder.", source)
    return None


def _invalid(message: str, source: str) -> Failure:
    return Failure(kind="invalid_input", effect="none", message=message, source=source)
