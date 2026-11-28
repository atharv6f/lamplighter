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
    - What a command may touch is set by the wall, and `wall()` names it.
      Behind the macOS sandbox: under `workspace_write`, a write outside the
      work folder and the scratch folder fails. Under `read_only`, every
      write fails, but for `/dev/null`. Behind a container: a command sees
      the container's own files, the work folder and the scratch folder,
      and nothing else of the machine the harness runs on. It runs as a
      user that is not root where the harness itself is not root. A write
      outside the work folder lands in the container and is deleted with
      it. `read_only` is not enforced there. Behind both: a command has no
      network, and the store's folder cannot be read or written from inside.
    - If the wall cannot be put up, nothing runs and `run` gives
      `unavailable`. No path runs a command without the wall.
    - Output is counted while the command runs, and at `capture_bytes` the
      command is killed. Standard input is closed.
    - A `run` that has begun and is then cancelled still returns a result,
      wherever the cancel lands. A second cancel changes nothing.
    - A non-zero exit, a time out and a flood are results. A `Failure` means
      the command was not started: `unavailable`, or `invalid_input` for a
      `cwd` outside the work folder, an empty `argv`, a limit below 1, or an
      argument or variable that holds a zero byte or is not UTF-8. The one
      exception is `unavailable` with `effect` `unknown`: the adapter lost
      touch with the wall and cannot say whether the command ran.
    - `wall()` gives the same record of plain values for the life of the
      process, saying which wall this is.
    - After `kill_all()`, no command that a `run` in this process started is
      alive. It does not wait, so a harness may call it just before it
      leaves at once. Behind a container the kill is sent and not waited
      for: each `run` that is going then ends with a result that says
      killed. For a harness that leaves in the same instant, the end of the
      container is the backstop.
    """

    async def run(self, command: Command) -> ExecResult | Failure:
        """Run the command behind the wall, and give how it ended."""
        ...

    def wall(self) -> JsonObject:
        """Give the record of the wall the commands run behind."""
        ...

    def kill_all(self) -> None:
        """Kill every command still running, at once."""
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
    texts = (*command.argv, *command.env.keys(), *command.env.values())
    if not all(_passable(text) for text in texts):
        return _invalid(
            "A command's arguments and variables cannot hold a zero byte or"
            " text that is not UTF-8.",
            source,
        )
    return None


def _passable(text: str) -> bool:
    # A program gets its arguments as C strings, which end at a zero byte.
    if "\0" in text:
        return False
    try:
        text.encode()
    except UnicodeEncodeError:
        return False
    return True


def _invalid(message: str, source: str) -> Failure:
    return Failure(kind="invalid_input", effect="none", message=message, source=source)
