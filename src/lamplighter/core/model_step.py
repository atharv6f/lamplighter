"""Carries out one model call: save the request, ask the model, save the reply."""

from contextlib import aclosing
from dataclasses import replace
from typing import Any, cast

from lamplighter.core.cost import cost_micro_dollars
from lamplighter.core.event_types import MODEL_REQUEST, MODEL_RESPONSE
from lamplighter.core.harness_texts import MODEL_OUT_OF_TIME
from lamplighter.core.interruptible import CutShort, Finished, NotStarted, interruptible
from lamplighter.core.limits import time_left_ms
from lamplighter.core.observers import ModelCallEnded, ModelCallStarted, notify
from lamplighter.core.reply import Reply, fold_reply
from lamplighter.core.run_deps import Deps
from lamplighter.core.run_events import elapsed_ms, in_run, open_run_id
from lamplighter.core.run_spec import RunSpec
from lamplighter.core.run_view import RunView
from lamplighter.core.transcript import rebuild_messages
from lamplighter.ports import (
    ErrorEvent,
    Event,
    Failure,
    JsonObject,
    ModelClient,
    ModelEvent,
    ModelRequest,
    ToolSpec,
    event_id_for,
    to_plain,
    utc_text,
)

_SOURCE = "run_turn"

_OUT_OF_TIME = ErrorEvent(
    failure=Failure(
        kind="timeout", effect="none", message=MODEL_OUT_OF_TIME, source=_SOURCE
    ),
    model_reported=None,
    skipped=0,
)


async def call_model(
    session_id: str,
    view: RunView,
    events: tuple[Event, ...],
    tools: tuple[ToolSpec, ...],
    spec: RunSpec,
    deps: Deps,
) -> Failure | None:
    """Save `model_request`, call the model, and save its reply.

    The request is saved first, so a call lost to a kill still counts as a
    step. Its deadline is the smaller of the setting and the time the run
    has left, which is at least 1 ms because the time limit is checked
    before this move is picked. A stop cuts the call short and nothing more
    is saved. A call still running one cancel bound after its deadline is
    cut, and saved as a `timeout` failure with what had arrived.
    """
    messages = rebuild_messages(events)
    if isinstance(messages, Failure):
        return messages
    deadline_ms = min(spec.model_deadline_ms, time_left_ms(view, spec))
    # The request and its reply carry the step the request begins.
    asked = replace(view, step=view.step + 1)
    request_event = in_run(
        asked, utc_text(deps.clock.now()), MODEL_REQUEST, {"deadline_ms": deadline_ms}
    )
    request_seq = await deps.log.append(session_id, (request_event,), view.last_seq)
    if isinstance(request_seq, Failure):
        return request_seq
    request = ModelRequest(
        system=(spec.system,),
        messages=messages,
        tools=tools,
        max_output=spec.model_max_output,
        cache_hints=(),
        deadline_ms=deadline_ms,
        retries=spec.model_retries,
    )
    run_id, step = open_run_id(view), view.step + 1
    started = ModelCallStarted(session_id, run_id, step, request)
    await notify(deps.observers.before_model, started, deps.clock)
    start = deps.clock.monotonic()
    reply = await _ask(session_id, request, spec, deps)
    duration_ms = elapsed_ms(start, deps.clock.monotonic())
    if reply is None:
        # Nothing more is saved. The request stays, and counts as a step.
        return None
    response = in_run(
        asked,
        utc_text(deps.clock.now()),
        MODEL_RESPONSE,
        _reply_payload(reply, spec),
        cause=event_id_for(session_id, request_seq),
        duration_ms=duration_ms,
    )
    saved = await deps.log.append(session_id, (response,), request_seq)
    if isinstance(saved, Failure):
        return saved
    ended = ModelCallEnded(session_id, run_id, step, reply, duration_ms)
    await notify(deps.observers.after_model, ended, deps.clock)
    return None


async def _ask(
    session_id: str, request: ModelRequest, spec: RunSpec, deps: Deps
) -> Reply | None:
    # None when a stop kept the call from starting or cut it short.
    got: list[ModelEvent] = []
    raced = await interruptible(
        _read_stream(deps.model, request, got),
        deps.running.signal_for(session_id),
        deps.clock,
        # The adapter keeps the deadline itself. This is the backstop behind
        # it, so no rule of the loop rests on an adapter behaving well.
        allowance_s=(request.deadline_ms + spec.cancel_bound_ms) / 1000,
        bound_s=spec.cancel_bound_ms / 1000,
    )
    match raced:
        case NotStarted() | CutShort(reason="stop"):
            return None
        case CutShort():
            return fold_reply([*got, _OUT_OF_TIME])
        case Finished():
            return fold_reply(got)


async def _read_stream(
    model: ModelClient, request: ModelRequest, got: list[ModelEvent]
) -> None:
    # The events are kept outside the task, so the part of a reply that
    # arrived before a cut is not lost. Closing the stream, which a cancel
    # does here, ends the call.
    async with aclosing(model.stream(request)) as stream:
        async for event in stream:
            got.append(event)


def _reply_payload(reply: Reply, spec: RunSpec) -> JsonObject:
    """Give the saved form of a reply, with its cost when the cost is known."""
    payload = cast(dict[str, Any], to_plain(reply))
    if reply.usage is not None:
        cost = cost_micro_dollars(reply.usage, spec.prices)
        if cost is not None:
            payload["cost_micro_dollars"] = cost
    return payload
