"""Moves waiting messages and stops from the inbox into a session's log."""

from collections.abc import Sequence

from lamplighter.core.event_types import READ_VERSION, SESSION_STARTED
from lamplighter.core.run_deps import Deps
from lamplighter.core.run_events import in_run
from lamplighter.core.run_spec import RunSpec
from lamplighter.core.run_view import RunView
from lamplighter.ports import (
    RUN_STARTED,
    USER_MESSAGE,
    Failure,
    Inbound,
    InboxEntry,
    NewEvent,
    inbound_event_id,
    utc_text,
)


async def finish_routing(
    session_id: str, routes: Sequence[tuple[int, int]], deps: Deps
) -> Failure | None:
    """Mark each inbox row as routed to the seq where its message is saved."""
    for inbox_id, seq in routes:
        marked = await deps.inbox.mark_routed(inbox_id, session_id, seq)
        if isinstance(marked, Failure):
            return marked
    return None


async def drop_stops(
    session_id: str,
    view: RunView,
    stops: Sequence[InboxEntry],
    new_session: bool,
    deps: Deps,
) -> Failure | None:
    """Use up stops that came with no run to stop.

    A new session gets its `session_started` first, so each stop has a seq
    to be routed to.
    """
    seq = view.last_seq
    if new_session:
        started = _session_started(stops[0].inbound, utc_text(deps.clock.now()))
        saved = await deps.log.append(session_id, (started,), view.last_seq)
        if isinstance(saved, Failure):
            return saved
        seq = saved
    return await finish_routing(
        session_id, [(stop.inbox_id, seq) for stop in stops], deps
    )


async def start_run(
    session_id: str,
    view: RunView,
    messages: Sequence[InboxEntry],
    new_session: bool,
    spec: RunSpec,
    deps: Deps,
) -> Failure | None:
    """Save a new run with the messages that start it, in one batch.

    The inbox rows are marked by the next pass, which finds the messages
    in the log.
    """
    first = messages[0].inbound
    run_id = deps.ids.new_id()
    at = utc_text(deps.clock.now())
    batch: list[NewEvent] = []
    if new_session:
        batch.append(_session_started(first, at))
    batch.append(
        NewEvent(
            type=RUN_STARTED,
            v=READ_VERSION[RUN_STARTED],
            payload={
                "config_id": spec.config_id,
                "portable_config_id": spec.portable_config_id,
                "code_revision": spec.code_revision,
                "trigger": "message",
                "wall": spec.wall,
            },
            at=at,
            run_id=run_id,
            cause=_event_id(first),
        )
    )
    batch.extend(
        NewEvent(
            type=USER_MESSAGE,
            v=READ_VERSION[USER_MESSAGE],
            payload={"text": row.inbound.text},
            at=at,
            run_id=run_id,
            event_id=_event_id(row.inbound),
        )
        for row in messages
    )
    saved = await deps.log.append(session_id, batch, view.last_seq)
    return saved if isinstance(saved, Failure) else None


async def take_messages(
    session_id: str, view: RunView, messages: Sequence[InboxEntry], deps: Deps
) -> Failure | None:
    """Add messages that arrived during the open run, before its next model call."""
    at = utc_text(deps.clock.now())
    batch = [
        in_run(
            view,
            at,
            USER_MESSAGE,
            {"text": row.inbound.text},
            event_id=_event_id(row.inbound),
        )
        for row in messages
    ]
    saved = await deps.log.append(session_id, batch, view.last_seq)
    return saved if isinstance(saved, Failure) else None


def _session_started(inbound: Inbound, at: str) -> NewEvent:
    return NewEvent(
        type=SESSION_STARTED,
        v=READ_VERSION[SESSION_STARTED],
        payload={"channel": inbound.channel, "conversation": inbound.conversation},
        at=at,
    )


def _event_id(inbound: Inbound) -> str:
    return inbound_event_id(inbound.channel, inbound.native_id)
