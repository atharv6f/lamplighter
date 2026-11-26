"""The chat command: talk to one session in the terminal."""

import asyncio
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
    Clock,
    EffectLedger,
    EventLog,
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
    "The store holds an action whose outcome is not known."
    " This version cannot settle it, so it will not start."
)
WAITING = (
    "Session {id} has unfinished work."
    " Carry it on with: lamplighter chat --session {id}"
)
LEAVING = "Exiting now. The next start carries on from what is saved."
LEAVE_CODE = 130
_SOURCE = "app/chat"


@dataclass(frozen=True)
class Stores:
    """The stores on one opened store file, and how to close that file."""

    log: EventLog
    inbox: Inbox
    outbox: Outbox
    ledger: EffectLedger
    close: Callable[[], None]


@dataclass(frozen=True)
class ChatParts:
    """What a chat is built from beside the settings. A test gives its own.

    The store and the terminal are given as functions, because the store
    is opened only once the folder is locked, and the terminal is made
    only once the session id is known.
    """

    model: ModelClient
    tools: ToolProvider
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
        return asyncio.run(_serve(config, stores, session_id, parts))
    finally:
        stores.close()


async def _serve(
    config: Config, stores: Stores, session_id: str | None, parts: ChatParts
) -> int:
    others = await _waiting_elsewhere(stores, session_id)
    if isinstance(others, Failure):
        return _refused(others.message, parts)
    if session_id is None:
        session_id = parts.ids.new_id()
        parts.writer.write(f"Session {session_id}\n")
    terminal = parts.terminal(session_id)
    for other in others:
        terminal.show_line(WAITING.format(id=other))
    running = RunningSessions()
    deps = Deps(
        log=stores.log,
        inbox=stores.inbox,
        outbox=stores.outbox,
        model=parts.model,
        tools=parts.tools,
        clock=parts.clock,
        ids=parts.ids,
        observers=progress_lines(terminal.show_line),
        running=running,
    )
    failed = await _talk(session_id, run_spec(config, parts.tools), deps, terminal)
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
) -> list[str] | Failure:
    """Give the other sessions with work waiting, or why the start must stop.

    Work waits in a run that has not ended, a message not yet routed, or a
    reply not yet sent. An action whose outcome is not known stops the
    start, because this version cannot settle one.
    """
    work = await unfinished_work(stores.log, stores.ledger)
    if isinstance(work, Failure):
        return work
    if work.other_effects or any(open_.effects for open_ in work.sessions):
        return Failure(
            kind="needs_user", effect="none", message=UNSETTLED, source=_SOURCE
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
    return sorted(waiting - {session_id})


def run_spec(config: Config, tools: ToolProvider) -> RunSpec:
    """Build the spec every run of this process keeps, with its config id."""
    run = config.run
    return RunSpec(
        config_id=config_id(
            config.model,
            config.prices,
            run,
            tuple(entry.spec for entry in tools.list_tools()),
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
    )


def _refused(message: str, parts: ChatParts) -> int:
    parts.errors.write(message + "\n")
    return 1
