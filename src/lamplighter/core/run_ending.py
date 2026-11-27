"""Ends a run in two moves: save the end batch, then deliver it and close the run."""

from collections.abc import Sequence
from dataclasses import dataclass

from lamplighter.core import harness_texts
from lamplighter.core.effect_step import close_unstarted
from lamplighter.core.event_types import CANCELLED, LIMIT_HIT, REPLY_READY, TOOL_RESULT
from lamplighter.core.next_move import EndRun
from lamplighter.core.run_deps import Deps
from lamplighter.core.run_events import in_run, open_run_id
from lamplighter.core.run_view import Ending, RunReason, RunView
from lamplighter.core.tool_step import harness_result
from lamplighter.ports import (
    RUN_END,
    Failure,
    InboxEntry,
    NewEvent,
    Outbound,
    out_id_for,
    utc_text,
)


@dataclass(frozen=True, slots=True)
class TurnEnd:
    """The run that one call of the loop ended, the seq of its `run_end`, and why."""

    run_id: str
    last_seq: int
    reason: RunReason


async def end_run(
    session_id: str, view: RunView, move: EndRun, deps: Deps
) -> Failure | None:
    """Save the end batch: why the run ended, a `not_run` result for each open
    call, and `reply_ready` with what you are told.

    First the ledger row of each open call that was begun and never started
    is closed as failed, with the call's `not_run` payload. Nothing is sent
    and the run stays open, so a kill after this batch is carried on by
    closing the run.
    """
    at = utc_text(deps.clock.now())
    batch: list[NewEvent] = []
    if move.limit is not None:
        payload = {
            "limit": move.limit.limit,
            "used": move.limit.used,
            "allowed": move.limit.allowed,
        }
        batch.append(in_run(view, at, LIMIT_HIT, payload))
    if move.reason == "cancelled":
        batch.append(in_run(view, at, CANCELLED, {}))
    answered = [
        (call, harness_result(call, "not_run", harness_texts.not_run(call.call.name)))
        for call in view.open_calls
    ]
    closed = await close_unstarted(session_id, answered, deps)
    if isinstance(closed, Failure):
        return closed
    for call, result in answered:
        batch.append(in_run(view, at, TOOL_RESULT, result, cause=call.reply_id))
    ready = {
        "text": move.text,
        "kind": move.kind,
        "run_reason": move.reason,
        "run_detail": move.detail,
    }
    batch.append(in_run(view, at, REPLY_READY, ready))
    saved = await deps.log.append(session_id, batch, view.last_seq)
    return saved if isinstance(saved, Failure) else None


async def close_run(
    session_id: str,
    view: RunView,
    ending: Ending,
    stops: Sequence[InboxEntry],
    deps: Deps,
) -> TurnEnd | Failure:
    """Use up the pending stops, enqueue the reply, then save `run_end`.

    The enqueue comes before `run_end`, so a kill between them leaves an
    open run that the next pass closes, and never a closed run whose reply
    is in no outbox. Each write is a no-op when it is made a second time.
    """
    run_id = open_run_id(view)
    for stop in stops:
        marked = await deps.inbox.mark_routed(stop.inbox_id, session_id, ending.seq)
        if isinstance(marked, Failure):
            return marked
    out = Outbound(
        out_id=out_id_for(session_id, ending.seq),
        session_id=session_id,
        kind=ending.kind,
        text=ending.text,
    )
    queued = await deps.outbox.enqueue(out)
    if isinstance(queued, Failure):
        return queued
    payload = {"reason": ending.run_reason, "detail": ending.run_detail}
    end = in_run(view, utc_text(deps.clock.now()), RUN_END, payload)
    seq = await deps.log.append(session_id, (end,), view.last_seq)
    if isinstance(seq, Failure):
        return seq
    return TurnEnd(run_id=run_id, last_seq=seq, reason=ending.run_reason)
