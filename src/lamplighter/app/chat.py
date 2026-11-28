"""The chat command: talk to one session in the terminal."""

import asyncio
import dataclasses
import logging
import os
import signal
from collections.abc import Awaitable, Callable
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
    TraceProjector,
    Wake,
    config_id,
    drive,
    pass_to_the_end,
    portable_config_id,
    project_while_open,
    run_intake,
    take,
    unfinished_work,
    unsupported,
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
    TraceCursor,
    TraceSink,
    is_id,
)

_log = logging.getLogger(__name__)

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
# The setting is not shown: it failed the rule, so it may hold anything.
OFF_THIS_MAC = (
    "Traces go only to a trace store on this Mac. traces.phoenix_url must be"
    " http://127.0.0.1: and a port, such as http://127.0.0.1:6006."
)
NOT_SENT = (
    "Spans are not being sent: {message} Runs go on, and the spans are sent"
    " once this is cured."
)
SENDING_STOPPED = (
    "Sending spans stopped: {message} This chat sends no more spans."
    " Try: lamplighter traces export"
)
PROJECTOR_RAISED = (
    "Sending spans stopped after an error in the projector. The chat goes on."
)
# The signals that end the chat at once: the terminal closed, or a `kill`.
_ENDINGS = (signal.SIGHUP, signal.SIGTERM)
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
    made only once the session id is known. The chat sends the spans of
    the log to `sink` and keeps how far it got in `cursor`. `code_revision`
    names the code this process runs, and every run saves it.
    """

    model: ModelClient
    tooling: Callable[[BlobStore], Tooling]
    clock: Clock
    ids: IdSource
    open_stores: Callable[[Path], Stores | Failure]
    terminal: Callable[[str], TerminalChannel]
    writer: TextIO
    errors: TextIO
    sink: TraceSink
    cursor: TraceCursor
    code_revision: str


def chat(config: Config, session_id: str | None, parts: ChatParts) -> int:
    """Talk to one session until the input ends, and give the exit code.

    With no session id a new session is started. A trace store that is not
    on this Mac is refused before anything is made. The store folder is
    made and locked before the store is opened, so a second process is
    refused before it touches anything.
    """
    if session_id is not None and not is_id(session_id):
        return _refused(f"The session id {session_id!r} breaks the id rule.", parts)
    if parts.sink.leaves_this_mac:
        return _refused(OFF_THIS_MAC, parts)
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
        check_schemas(tooling.tools)
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
    projector = TraceProjector(stores.log, parts.sink, parts.cursor)
    failed = await _talk(
        session_id,
        run_spec(config, tooling, parts.code_revision),
        deps,
        terminal,
        tooling.executor,
        projector,
    )
    return 0 if failed is None else _refused(failed.message, parts)


async def _talk(
    session_id: str,
    spec: RunSpec,
    deps: Deps,
    terminal: TerminalChannel,
    executor: Executor,
    projector: TraceProjector,
) -> Failure | None:
    """Run the intake and the driver, with Ctrl-C handled, until both end.

    A closed terminal or a plain `kill` ends the process at once, as a
    second Ctrl-C does, once the commands still running are killed. The
    projector runs beside them, and makes its last passes while the
    handlers are still in place, so a signal then still ends the process
    the same way.
    """
    wake = Wake()

    async def stop() -> None:
        stopped = await take(
            terminal.stop_message(), deps.inbox, terminal, deps.running, wake.wake
        )
        if stopped is not None:
            terminal.show_line(stopped.message)

    keys = CtrlC(
        session_id,
        deps.running,
        stop,
        wake.end_input,
        lambda: leave_now(terminal, executor),
    )
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, keys.pressed)
    for ending in _ENDINGS:
        loop.add_signal_handler(ending, _ended_by, ending, executor)
    projecting = _start_projecting(projector, deps.clock, terminal.show_line)
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
        await _stop_projecting(projecting, projector, deps.clock, terminal.show_line)
    finally:
        projecting.cancel()
        for handled in (signal.SIGINT, *_ENDINGS):
            loop.remove_signal_handler(handled)
    if driven is not None:
        return driven
    return None if intake.cancelled() else intake.result()


def _start_projecting(
    projector: TraceProjector, clock: Clock, show_line: Callable[[str], None]
) -> asyncio.Task[None]:
    """Start sending spans for as long as the chat lives, each failure told once."""

    def first_failure(failure: Failure) -> None:
        show_line(NOT_SENT.format(message=failure.message))

    def ended(failure: Failure) -> None:
        show_line(SENDING_STOPPED.format(message=failure.message))

    loop = project_while_open(projector.run_pass, clock, first_failure, ended)
    return asyncio.ensure_future(_guarded(loop, show_line))


async def _stop_projecting(
    projecting: asyncio.Task[None],
    projector: TraceProjector,
    clock: Clock,
    show_line: Callable[[str], None],
) -> None:
    """Cancel the task, and if it was still alive, send what is left."""
    # A task that ended by itself ended on a failure or a bug, and a last
    # pass would meet the same again.
    alive = not projecting.done()
    projecting.cancel()
    await asyncio.wait([projecting])
    if alive:
        await _guarded(pass_to_the_end(projector.run_pass, clock), show_line)


async def _guarded(work: Awaitable[None], show_line: Callable[[str], None]) -> None:
    # The projector raising is a bug in it, and it must not reach a run.
    try:
        await work
    except Exception:
        _log.exception("The projector raised.")
        show_line(PROJECTOR_RAISED)


def leave_now(terminal: TerminalChannel, executor: Executor) -> NoReturn:
    """Say so, kill the commands, and leave at once, with no clean-up that could hang.

    The saved state needs none: leaving here is the same as a kill, and the
    next start carries on from what is saved. A command is in a session of
    its own, so nothing else would end it.
    """
    terminal.show_line(LEAVING)
    executor.kill_all()
    os._exit(LEAVE_CODE)


def _ended_by(ending: signal.Signals, executor: Executor) -> NoReturn:
    # The terminal may be gone, so nothing is shown.
    executor.kill_all()
    os._exit(128 + ending)


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


def check_schemas(tools: ToolProvider) -> None:
    """Raise ValueError if a tool's schema has a part the argument check cannot read.

    The tools are this repo's own code, so such a schema is a bug, and the
    chat does not start with a check that would pass what it never read.
    """
    for entry in tools.list_tools():
        found = unsupported(entry.spec.arguments_schema)
        if found:
            named = ", ".join(found)
            raise ValueError(
                f"the argument check cannot read the schema of {entry.spec.name}:"
                f" {named}"
            )


def run_spec(config: Config, tooling: Tooling, code_revision: str) -> RunSpec:
    """Build the spec every run of this process keeps, with its two config ids.

    `chat` and a trial of the bench both build their spec here, so one
    settings file gives one system prompt and one tool list in both.
    """
    run = config.run
    first = config.default_model()
    wall = tooling.executor.wall()
    tools = tooling.tools.list_tools()
    tool_settings = dataclasses.asdict(config.tools)
    return RunSpec(
        config_id=config_id(
            config.models, config.routes, run, tools, tool_settings, wall
        ),
        portable_config_id=portable_config_id(
            config.models, config.routes, run, tools, tool_settings
        ),
        code_revision=code_revision,
        model=first.profile.model,
        provider=first.profile.adapter,
        system=run.system,
        max_steps=run.max_steps,
        max_seconds=run.max_seconds,
        max_tokens=run.max_tokens,
        max_micro_dollars=run.max_micro_dollars,
        model_max_output=run.model_max_output,
        model_deadline_ms=run.model_deadline_ms,
        model_retries=run.model_retries,
        cancel_bound_ms=run.cancel_bound_ms,
        prices=first.prices,
        wall=wall,
    )


def _refused(message: str, parts: ChatParts) -> int:
    parts.errors.write(message + "\n")
    return 1
