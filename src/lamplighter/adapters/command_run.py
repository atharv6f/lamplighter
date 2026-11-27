"""Starts one process, captures its output, and kills its whole group, whatever happens.

This is the only code in `src/` that starts a process for a tool, and only
the wall adapter calls it. It puts up no wall of its own: the caller puts
the wall's program in front of the command, and its self-test in
`first_check`.
"""

import asyncio
import contextlib
import os
import signal
import subprocess
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from lamplighter.ports import Clock, ExecResult, Failure

_SOURCE = "command_run"
# How long the pipe may take to reach its end after the kill. A child that
# left the group keeps it open for ever, so the wait must have an end.
_DRAIN_S = 0.3
_CHUNK = 65_536

type _Why = Literal["exited", "full", "time", "cancel"]
type FirstCheck = Callable[[], Awaitable[Failure | None]]


@dataclass
class _Capture:
    """What the pipe gave: the kept bytes, and how many were left out."""

    limit: int
    kept: bytearray
    dropped: int = 0

    def take(self, data: bytes) -> None:
        room = self.limit - len(self.kept)
        self.kept += data[: max(room, 0)]
        self.dropped += max(len(data) - room, 0)


async def run_command(
    argv: Sequence[str],
    cwd: str,
    env: Mapping[str, str],
    wall_ms: int,
    capture_bytes: int,
    clock: Clock,
    first_check: FirstCheck,
) -> ExecResult | Failure:
    """Run a command to its end, its limit, or a cancel, and give how it ended.

    The work runs in a task of its own behind a shield. A cancel of this
    call only asks the work to kill the command, and the call goes on
    waiting, so the kill, the reaping and the closing of the pipe always
    happen and the caller always gets a result. A second cancel changes
    nothing. `Failure` is `unavailable` when the program could not be
    started.

    `first_check` is awaited first, behind the same shield, so no cancel
    reaches it: the wall's self-test runs there. A `Failure` from it is
    given back and nothing is started. A cancel that came during it starts
    the command and kills it at once.

    Only the event loop's shutdown cancels the work itself, because it
    cancels every task. Then the group is killed, and the cancel goes on
    to the caller, since no result will come.
    """
    stop = asyncio.Event()
    work = asyncio.ensure_future(
        _work(argv, cwd, env, wall_ms, capture_bytes, clock, first_check, stop)
    )
    while True:
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            if work.cancelled():
                raise
            # If the work ended in the same turn as the cancel arrived, the
            # next wait gives its result at once, so the cancel is dropped.
            stop.set()


async def _work(
    argv: Sequence[str],
    cwd: str,
    env: Mapping[str, str],
    wall_ms: int,
    capture_bytes: int,
    clock: Clock,
    first_check: FirstCheck,
    stop: asyncio.Event,
) -> ExecResult | Failure:
    refused = await first_check()
    if refused is not None:
        return refused
    loop = asyncio.get_running_loop()
    # The pipe is ours, not asyncio's: on some Python versions `wait()` does
    # not return while a grandchild holds asyncio's own pipe open.
    read_end, write_end = os.pipe()
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=write_end,
            stderr=write_end,
            start_new_session=True,
        )
    except OSError as error:
        os.close(read_end)
        return Failure(
            kind="unavailable",
            effect="none",
            message=f"The command could not be started: {error.strerror}.",
            source=_SOURCE,
        )
    finally:
        os.close(write_end)
    capture = _Capture(limit=capture_bytes, kept=bytearray())
    full, ended = asyncio.Event(), asyncio.Event()
    os.set_blocking(read_end, False)
    loop.add_reader(read_end, _read, read_end, capture, full, ended)
    killed = False
    try:
        exited = asyncio.ensure_future(process.wait())
        why = await _first_of(exited, full, wall_ms, clock, stop)
        _kill_group(process)
        killed = True
        await exited
        await _until_set(ended, _DRAIN_S, clock)
    finally:
        # A cancel of the work itself still kills the group. Once it is
        # killed and reaped, its id may belong to another group.
        if not killed:
            _kill_group(process)
        loop.remove_reader(read_end)
        os.close(read_end)
    return _result(process.returncode, why, capture)


def _read(
    fd: int, capture: _Capture, full: asyncio.Event, ended: asyncio.Event
) -> None:
    # Bytes past the limit are read and dropped, so the pipe still reaches
    # its end and the child is never stuck on a full pipe.
    try:
        data = os.read(fd, _CHUNK)
    except BlockingIOError:
        return
    if not data:
        ended.set()
        asyncio.get_running_loop().remove_reader(fd)
        return
    capture.take(data)
    if capture.dropped:
        full.set()


async def _first_of(
    exited: asyncio.Future[int],
    full: asyncio.Event,
    wall_ms: int,
    clock: Clock,
    stop: asyncio.Event,
) -> _Why:
    waits = {
        asyncio.ensure_future(full.wait()): "full",
        asyncio.ensure_future(clock.sleep(wall_ms / 1000)): "time",
        asyncio.ensure_future(stop.wait()): "cancel",
    }
    try:
        await asyncio.wait({exited, *waits}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for wait in waits:
            wait.cancel()
    if exited.done():
        return "exited"
    for wait, why in waits.items():
        if wait.done() and not wait.cancelled():
            return why  # type: ignore[return-value]
    raise ValueError("a wait ended with nothing done")


def _kill_group(process: asyncio.subprocess.Process) -> None:
    # The group, because a shell line starts other programs. macOS signals
    # the members one by one, so a killed child can wake its shell before
    # the shell is signalled and let `a; b` start b. Stopping the group
    # first narrows that gap, and SIGKILL also ends a stopped process. A
    # group that is gone is not an error. macOS refuses to signal a group
    # that holds only zombies, and then the child alone is killed, if it
    # has not been reaped.
    for sent in (signal.SIGSTOP, signal.SIGKILL):
        try:
            os.killpg(process.pid, sent)
        except ProcessLookupError:
            return
        except PermissionError:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            return


async def _until_set(event: asyncio.Event, seconds: float, clock: Clock) -> None:
    waited = asyncio.ensure_future(event.wait())
    timer = asyncio.ensure_future(clock.sleep(seconds))
    try:
        await asyncio.wait({waited, timer}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        waited.cancel()
        timer.cancel()


def _result(returncode: int | None, why: _Why, capture: _Capture) -> ExecResult:
    if returncode is None:
        raise ValueError("a reaped process has a return code")
    # The flags follow the kill: a command that exits by itself in the
    # instant between a limit and the kill keeps its exit code.
    killed = returncode == -signal.SIGKILL and why != "exited"
    return ExecResult(
        exit_code=returncode if returncode >= 0 else None,
        signal=-returncode if returncode < 0 else None,
        output=bytes(capture.kept),
        overflowed=capture.dropped > 0,
        timed_out=killed and why == "time",
        cancelled=killed and why == "cancel",
    )
