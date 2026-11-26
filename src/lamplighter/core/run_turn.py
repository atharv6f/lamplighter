"""The loop: one call carries a session's run on, pass by pass, from what is saved."""

from collections.abc import Mapping

from lamplighter.core.model_step import call_model
from lamplighter.core.next_move import (
    AnswerCall,
    CallModel,
    CloseRun,
    DropStops,
    EndRun,
    FinishRouting,
    Idle,
    Move,
    RunCall,
    StartRun,
    TakeMessages,
    next_move,
)
from lamplighter.core.routing import (
    drop_stops,
    finish_routing,
    start_run,
    take_messages,
)
from lamplighter.core.run_deps import Deps
from lamplighter.core.run_ending import TurnEnd, close_run, end_run
from lamplighter.core.run_spec import RunSpec
from lamplighter.core.run_view import RunView, view_of
from lamplighter.core.tool_step import answer_call, run_call
from lamplighter.ports import Event, Failure, ToolEntry

_SOURCE = "run_turn"


async def run_turn(
    session_id: str, spec: RunSpec, deps: Deps
) -> TurnEnd | Failure | None:
    """Carry the session on until one run ends, or until there is nothing to do.

    Callers can rely on this:

    - With no open run and no pending inbox row for the session, it returns
      None and appends nothing.
    - It ends at most one run, and returns that run's `TurnEnd` as soon as
      its `run_end` is saved. If more messages wait, the caller calls again.
    - A `Failure` from a store ends the call and is returned as it is. What
      was saved stays saved, and the next call carries on from there.
    - After a kill at any moment, the next call carries on. A tool call that
      had started is never run again, and no message is lost or saved twice.
    - The same saved events, inbox rows, model replies, clock and ids give
      the same events, byte for byte.
    - A second call for a session this process is already running returns
      `conflict` and writes nothing.
    - A run uses one config id from its start to its end.
    - An exception out of it is a bug.
    """
    if not deps.running.begin(session_id):
        return Failure(
            kind="conflict",
            effect="none",
            message=f"Session {session_id} is already being run.",
            source=_SOURCE,
        )
    try:
        return await _passes(session_id, spec, deps)
    finally:
        deps.running.end(session_id)


async def _passes(
    session_id: str, spec: RunSpec, deps: Deps
) -> TurnEnd | Failure | None:
    entries = {entry.spec.name: entry for entry in deps.tools.list_tools()}
    tool_names = frozenset(entries)
    events: list[Event] = []
    while True:
        # Cleared before the inbox is read: a stop saved before this finds
        # its row in the read, and one saved after sets the flag again.
        deps.running.clear_stop(session_id)
        known = events[-1].seq if events else 0
        new = await deps.log.read(session_id, known)
        if isinstance(new, Failure):
            return new
        events.extend(new)
        pending = await deps.inbox.pending()
        if isinstance(pending, Failure):
            return pending
        rows = [row for row in pending if row.inbound.conversation == session_id]
        view = view_of(events)
        if isinstance(view, Failure):
            return view
        move = next_move(view, rows, spec, tool_names)
        if isinstance(move, Idle):
            return None
        if isinstance(move, CloseRun):
            return await close_run(session_id, view, move.ending, move.stops, deps)
        failed = await _carry_out(
            session_id, view, move, tuple(events), entries, spec, deps
        )
        if failed is not None:
            return failed


async def _carry_out(
    session_id: str,
    view: RunView,
    move: Move,
    events: tuple[Event, ...],
    entries: Mapping[str, ToolEntry],
    spec: RunSpec,
    deps: Deps,
) -> Failure | None:
    tools = tuple(entry.spec for entry in entries.values())
    match move:
        case FinishRouting():
            return await finish_routing(session_id, move.routes, deps)
        case DropStops():
            return await drop_stops(
                session_id, view, move.stops, move.new_session, deps
            )
        case StartRun():
            return await start_run(
                session_id, view, move.messages, move.new_session, spec, deps
            )
        case TakeMessages():
            return await take_messages(session_id, view, move.messages, deps)
        case CallModel():
            return await call_model(session_id, view, events, tools, spec, deps)
        case RunCall():
            entry = entries[move.call.call.name]
            return await run_call(session_id, view, move.call, entry, spec, deps)
        case AnswerCall():
            return await answer_call(
                session_id, view, move.call, move.harness, tools, deps
            )
        case EndRun():
            return await end_run(session_id, view, move, deps)
        case Idle() | CloseRun():
            raise ValueError("the loop itself handles this move")
