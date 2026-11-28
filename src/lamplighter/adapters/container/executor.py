"""The wall of a container: each command runs inside it, under a script run as root."""

import asyncio
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path

from lamplighter.adapters.container.supervisor import (
    read_answer,
    run_folder,
    script_line,
)
from lamplighter.ports import (
    Command,
    ExecResult,
    Failure,
    IdSource,
    JsonObject,
    refused_command,
)

type Answer = tuple[int, str]
# Runs one bash line in the container as root, in a folder, within some
# seconds. It gives the exit code and the output, or None when the call did
# not end in time.
type AsRoot = Callable[[str, str, int], Awaitable[Answer | None]]

_SOURCE = "Executor/container"
# Linux takes one argument of at most 131,072 bytes, and the line is one.
_LINE_LIMIT = 120_000
# The call's own limit, behind the script's, in seconds.
_BACKSTOP_SECONDS = 30
_SIGNAL_EVERY_SECONDS = 0.2
_SMALL_CALL_SECONDS = 20
_SWEEP_PASSES = 5
_LIST = "printf '%s ' $$; for p in /proc/[0-9]*; do printf '%s ' \"${p#/proc/}\"; done"
# Counts every process that `mark` did not keep and that is not a zombie,
# whether its kill arrived or it was gone a moment after it was listed, so
# a pass counts nothing only in a quiet container. A zombie has ended and
# cannot be killed, so counting it would never reach nothing.
_SWEEP = r"""keep=" KEPT "; n=0
for p in /proc/[0-9]*; do
  p=${p#/proc/}
  case "$keep" in *" $p "*) continue;; esac
  [ "$p" = "$$" ] && continue
  if ! read -r line 2>/dev/null <"/proc/$p/stat"; then n=$((n + 1)); continue; fi
  rest=${line##*) }
  [ "${rest%% *}" = Z ] && continue
  kill -KILL "$p" 2>/dev/null
  n=$((n + 1))
done
echo "swept $n"
"""


class ContainerExecutor:
    """Runs each command inside one container, as the command user. Satisfies Executor.

    Every call into the container is made as root through `as_root`, and the
    script it runs starts the command as `user_id` and `group_id` with no
    other groups. The work folder is at the same path inside and outside.
    `broken` holds the first time the adapter lost touch with the container.
    """

    def __init__(
        self, as_root: AsRoot, user_id: int, group_id: int, work: Path, ids: IdSource
    ) -> None:
        """Run commands in the container that `as_root` reaches, in `work`."""
        self._as_root = as_root
        self._is_root = user_id == 0
        self._front = [
            "/usr/bin/setpriv",
            f"--reuid={user_id}",
            f"--regid={group_id}",
            "--clear-groups",
            "--",
        ]
        self._work = work.resolve()
        self._ids = ids
        self._running: dict[str, asyncio.Future[Answer | None]] = {}
        self._signalling: set[asyncio.Future[None]] = set()
        self._kept: tuple[str, ...] = ()
        self.broken: Failure | None = None

    async def run(self, command: Command) -> ExecResult | Failure:
        """Run the command in the container, and give how it ended."""
        refused = refused_command(command, self._work, _SOURCE)
        if refused is not None:
            return refused
        if any(name == "" or "=" in name for name in command.env):
            return _invalid("A variable's name is empty or holds an equals sign.")
        token = self._ids.new_id()
        limits = (command.capture_bytes, command.wall_ms)
        line = script_line(token, command.argv, command.env, self._front, limits)
        # A single quote costs five bytes once quoted, so the line is
        # measured and not the command.
        if len(line.encode()) > _LINE_LIMIT:
            return _invalid("The command is too long to send into the container.")
        seconds = command.wall_ms // 1000 + _BACKSTOP_SECONDS
        answer = await self._call(run_folder(token), line, command.cwd, seconds)
        if answer is None:
            return self._unavailable("The container did not answer in time.")
        result = read_answer(token, answer[1], command.capture_bytes)
        if result is None:
            return self._unavailable(
                "The command's supervisor gave no whole answer"
                f" (exit code {answer[0]})."
            )
        return result

    def wall(self) -> JsonObject:
        """Give the record that names this wall."""
        return {"adapter": "container", "network": "none"}

    def kill_all(self) -> None:
        """Have every command that is still running killed, without waiting."""
        for folder, call in list(self._running.items()):
            sending = asyncio.ensure_future(
                self._signal_until_done(folder, call, "USR1")
            )
            self._signalling.add(sending)
            sending.add_done_callback(self._signalling.discard)

    async def mark(self) -> bool:
        """Keep the ids of the processes alive in the container now.

        The sweep spares them. It gives whether the container answered.
        """
        answer = await self._as_root(_LIST, "/", _SMALL_CALL_SECONDS)
        if answer is None or answer[0] != 0:
            return False
        words = answer[1].split()
        if not words or not all(word.isdigit() for word in words):
            return False
        own, *alive = words
        self._kept = tuple(word for word in alive if word != own)
        return True

    async def sweep(self) -> bool:
        """Kill every process the run left, and give whether nothing is left.

        One kill to every process of the command user goes first, sent as
        that user: the kernel delivers it in one step, so a process that
        forks without pause cannot slip between a listing and a kill. Sent
        as root it would stop the container. Then passes as root kill what
        is left, until one finds nothing.
        """
        if not self._is_root:
            everyone = shlex.join([*self._front, "/bin/bash", "-c", "kill -KILL -1"])
            if await self._as_root(everyone, "/", _SMALL_CALL_SECONDS) is None:
                return False
        line = _SWEEP.replace("KEPT", " ".join(self._kept))
        for _ in range(_SWEEP_PASSES):
            answer = await self._as_root(line, "/", _SMALL_CALL_SECONDS)
            if answer is None:
                return False
            words = answer[1].split()
            if len(words) != 2 or words[0] != "swept" or not words[1].isdigit():
                return False
            if words[1] == "0":
                return True
        return False

    async def _call(
        self, folder: str, line: str, cwd: str, seconds: int
    ) -> Answer | None:
        # The call is shielded, so a cancel cannot cut it: the cancel is
        # passed to the script instead, and its answer is still read. A
        # second cancel lands on the shield and changes nothing.
        call = asyncio.ensure_future(self._as_root(line, cwd, seconds))
        self._running[folder] = call
        stopping: asyncio.Future[None] | None = None
        try:
            while True:
                try:
                    answer = await asyncio.shield(call)
                    break
                except asyncio.CancelledError:
                    if call.cancelled():
                        raise
                    if stopping is None:
                        stopping = asyncio.ensure_future(
                            self._signal_until_done(folder, call, "TERM")
                        )
            if stopping is not None:
                await _until_done(stopping)
        finally:
            self._running.pop(folder, None)
        return answer

    async def _signal_until_done(
        self, folder: str, call: asyncio.Future[Answer | None], signal: str
    ) -> None:
        # Sent again until the call ends, because the script writes its
        # process id a moment after it starts.
        line = f'kill -{signal} "$(cat {folder}/supervisor)"'
        while not call.done():
            await self._as_root(line, "/", _SMALL_CALL_SECONDS)
            if not call.done():
                await asyncio.wait([call], timeout=_SIGNAL_EVERY_SECONDS)

    def _unavailable(self, message: str) -> Failure:
        failure = Failure(
            kind="unavailable", effect="unknown", message=message, source=_SOURCE
        )
        self.broken = self.broken or failure
        return failure


async def _until_done(task: asyncio.Future[None]) -> None:
    # Waits behind a shield of its own, so that no call is left running
    # when `run` returns, whatever cancels land meanwhile.
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise


def _invalid(message: str) -> Failure:
    return Failure(kind="invalid_input", effect="none", message=message, source=_SOURCE)
