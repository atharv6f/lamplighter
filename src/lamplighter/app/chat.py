"""The chat command: talk to one session in the terminal."""

import asyncio
import dataclasses
import os
import signal
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TextIO

from lamplighter.adapters import TerminalChannel
from lamplighter.app.config import Config
from lamplighter.app.ctrl_c import CtrlC
from lamplighter.app.progress import progress_lines
from lamplighter.app.single_instance import lock_store
from lamplighter.app.tool_folders import make_tool_folders
from lamplighter.core import (
    Deps,
    RunningSessions,
    RunSpec,
    Wake,
    config_id,
    drive,
    run_intake,
    take,
    unfinished_work,
)
from lamplighter.ports import (
    BlobStore,
    Clock,
    Command,
    EffectLedger,
    EventLog,
    Executor,
    Failure,
    IdSource,
    Inbox,
    ModelClient,
    Outbox,
    ToolProvider,
    is_id,
)

LOCK_HELD = "Another lamplighter process is using this store."
UNSETTLED = (
    "Actions whose outcome is not known: {n}."
    " List them with: lamplighter store sessions"
)
NO_WALL = (
    "The sandbox is not available: {message} Commands and searches will fail."
    " File tools and web_fetch still work."
)
WAITING = (
    "Session {id} has unfinished work."
    " Carry it on with: lamplighter chat --session {id}"
)
LEAVING = "Exiting now. The next start carries on from what is saved."
LEAVE_CODE = 130
# The command that brings on the wall's self-test before the first message.
_FIRST_COMMAND = ("/usr/bin/true",)
_FIRST_COMMAND_MS = 5_000


@dataclass(frozen=True)
class Stores:
    """The stores on one opened store file, and how to close that file."""

    log: EventLog
    inbox: Inbox
    outbox: Outbox
    ledger: EffectLedger
    blobs: BlobStore
    close: Callable[[], None]


@dataclass(frozen=True)
class Tooling:
    """The tools, and the executor that runs their commands behind the wall."""

    tools: ToolProvider
    executor: Executor


@dataclass(frozen=True)
class ChatParts:
    """What a chat is built from beside the settings. A test gives its own.

    The store, the tools and the terminal are given as functions, because
    the store is opened only once the folder is locked, the tools are made
    only once their folders are, with the store's blobs, and the terminal is
    made only once the session id is known.
    """

    model: ModelClient
    tooling: Callable[[BlobStore], Tooling]
    clock: Clock
    ids: IdSource
    open_stores: Callable[[Path], Stores | Failure]
    terminal: Callable[[str], TerminalChannel]
    writer: TextIO
    errors: TextIO


def chat(config: Config, session_id: str | None, parts: ChatParts) -> int:
    """Talk to one session until the input ends, and give the exit code.

    With no session id a new session is started. The store folder is made
    and locked before the store is opened, so a second process is refused
    before it touches anything.
    """
    if session_id is not None and not is_id(session_id):
        return _refused(f"The session id {session_id!r} breaks the id rule.", parts)
    folder = Path(config.store.dir)
    held = lock_store(folder)
    if held is None:
        return _refused(LOCK_HELD, parts)
    try:
        return _open_and_serve(config, folder, session_id, parts)
    finally:
        # The process ends straight after, which would free the lock too.
        # Freeing it here lets a test hold more than one chat in a process.
        os.close(held)


def _open_and_serve(
    config: Config, folder: Path, session_id: str | None, parts: ChatParts
) -> int:
    stores = parts.open_stores(folder)
    if isinstance(stores, Failure):
        return _refused(stores.message, parts)
    try:
        refused = make_tool_folders(config.tools)
        if refused is not None:
            return _refused(refused, parts)
        tooling = parts.tooling(stores.blobs)
        return asyncio.run(_serve(config, stores, tooling, session_id, parts))
    finally:
        stores.close()


async def _serve(
    config: Config,
    stores: Stores,
    tooling: Tooling,
    session_id: str | None,
    parts: ChatParts,
) -> int:
    waiting = await _waiting_elsewhere(stores, session_id)
    if isinstance(waiting, Failure):
        return _refused(waiting.message, parts)
    unsettled, others = waiting
    if session_id is None:
        session_id = parts.ids.new_id()
        parts.writer.write(f"Session {session_id}\n")
    terminal = parts.terminal(session_id)
    if unsettled:
        terminal.show_line(UNSETTLED.format(n=unsettled))
    for other in others:
        terminal.show_line(WAITING.format(id=other))
    no_wall = await _wall_is_down(config, tooling.executor)
    if no_wall is not None:
        terminal.show_line(NO_WALL.format(message=no_wall.message))
    running = RunningSessions()
    deps = Deps(
        log=stores.log,
        inbox=stores.inbox,
        outbox=stores.outbox,
        model=parts.model,
        tools=tooling.tools,
        ledger=stores.ledger,
        blobs=stores.blobs,
        clock=parts.clock,
        ids=parts.ids,
        observers=progress_lines(terminal.show_line),
        running=running,
    )
    failed = await _talk(session_id, run_spec(config, tooling), deps, terminal)
    return 0 if failed is None else _refused(failed.message, parts)


async def _talk(
    session_id: str, spec: RunSpec, deps: Deps, terminal: TerminalChannel
) -> Failure | None:
    """Run the intake and the driver, with Ctrl-C handled, until both end."""
    wake = Wake()

    async def stop() -> None:
        stopped = await take(
            terminal.stop_message(), deps.inbox, terminal, deps.running, wake.wake
        )
        if stopped is not None:
            terminal.show_line(stopped.message)

    keys = CtrlC(
        session_id, deps.running, stop, wake.end_input, lambda: leave_now(terminal)
    )
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, keys.pressed)
    try:
        intake = asyncio.ensure_future(
            run_intake(terminal, deps.inbox, deps.running, wake.wake, wake.end_input)
        )
        try:
            driven = await drive(
                session_id, spec, deps, terminal, wake, terminal.show_prompt
            )
        finally:
            # The input may still be open, as after Ctrl-C at the prompt.
            intake.cancel()
        await asyncio.wait([intake])
    finally:
        loop.remove_signal_handler(signal.SIGINT)
    if driven is not None:
        return driven
    return None if intake.cancelled() else intake.result()


def leave_now(terminal: TerminalChannel) -> NoReturn:
    """Say so and leave at once, with no clean-up that could hang.

    The saved state needs none: leaving here is the same as a kill, and the
    next start carries on from what is saved.
    """
    terminal.show_line(LEAVING)
    os._exit(LEAVE_CODE)


async def _waiting_elsewhere(
    stores: Stores, session_id: str | None
) -> tuple[int, list[str]] | Failure:
    """Give the count of unsettled actions and the other sessions with work.

    Work waits in a run that has not ended, a message not yet routed, or a
    reply not yet sent. An action whose outcome is not known no longer
    stops the start: the loop settles its own session's in its first
    passes, and the count tells the person about the rest.
    """
    work = await unfinished_work(stores.log, stores.ledger)
    if isinstance(work, Failure):
        return work
    unsettled = len(work.other_effects) + sum(
        len(open_.effects) for open_ in work.sessions
    )
    pending = await stores.inbox.pending()
    if isinstance(pending, Failure):
        return pending
    due = await stores.outbox.due()
    if isinstance(due, Failure):
        return due
    waiting = (
        {open_.session.session_id for open_ in work.sessions}
        | {row.inbound.conversation for row in pending}
        | {out.session_id for out in due}
    )
    return unsettled, sorted(waiting - {session_id})


async def _wall_is_down(config: Config, executor: Executor) -> Failure | None:
    """Run one command, which brings on the wall's self-test, and say if it failed."""
    first = Command(
        argv=_FIRST_COMMAND,
        cwd=config.tools.work_dir,
        env={},
        profile="read_only",
        wall_ms=_FIRST_COMMAND_MS,
        capture_bytes=1_000,
    )
    ran = await executor.run(first)
    if isinstance(ran, Failure) and ran.kind == "unavailable":
        return ran
    return None


def run_spec(config: Config, tooling: Tooling) -> RunSpec:
    """Build the spec every run of this process keeps, with its config id."""
    run = config.run
    wall = tooling.executor.wall()
    return RunSpec(
        config_id=config_id(
            config.model,
            config.prices,
            run,
            tooling.tools.list_tools(),
            dataclasses.asdict(config.tools),
            wall,
        ),
        system=run.system,
        max_steps=run.max_steps,
        max_seconds=run.max_seconds,
        max_tokens=run.max_tokens,
        max_micro_dollars=run.max_micro_dollars,
        model_max_output=run.model_max_output,
        model_deadline_ms=run.model_deadline_ms,
        model_retries=run.model_retries,
        cancel_bound_ms=run.cancel_bound_ms,
        prices=config.prices,
        wall=wall,
    )


def _refused(message: str, parts: ChatParts) -> int:
    parts.errors.write(message + "\n")
    return 1
