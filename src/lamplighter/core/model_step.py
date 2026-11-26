"""Carries out one model call: save the request, ask the model, save the reply."""

from contextlib import aclosing
from dataclasses import replace
from typing import Any, cast

from lamplighter.core.cost import cost_micro_dollars
from lamplighter.core.event_types import MODEL_REQUEST, MODEL_RESPONSE
from lamplighter.core.limits import time_left_ms
from lamplighter.core.reply import Reply, fold_reply
from lamplighter.core.run_deps import Deps
from lamplighter.core.run_events import elapsed_ms, in_run
from lamplighter.core.run_spec import RunSpec
from lamplighter.core.run_view import RunView
from lamplighter.core.transcript import rebuild_messages
from lamplighter.ports import (
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
    before this move is picked.
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
    start = deps.clock.monotonic()
    reply = fold_reply(await _read_stream(deps.model, request))
    duration_ms = elapsed_ms(start, deps.clock.monotonic())
    response = in_run(
        asked,
        utc_text(deps.clock.now()),
        MODEL_RESPONSE,
        _reply_payload(reply, spec),
        cause=event_id_for(session_id, request_seq),
        duration_ms=duration_ms,
    )
    saved = await deps.log.append(session_id, (response,), request_seq)
    return saved if isinstance(saved, Failure) else None


async def _read_stream(model: ModelClient, request: ModelRequest) -> list[ModelEvent]:
    """Read the whole reply stream. Closing it, also on a cancel, ends the call."""
    got: list[ModelEvent] = []
    async with aclosing(model.stream(request)) as stream:
        async for event in stream:
            got.append(event)
    return got


def _reply_payload(reply: Reply, spec: RunSpec) -> JsonObject:
    """Give the saved form of a reply, with its cost when the cost is known."""
    payload = cast(dict[str, Any], to_plain(reply))
    if reply.usage is not None:
        cost = cost_micro_dollars(reply.usage, spec.prices)
        if cost is not None:
            payload["cost_micro_dollars"] = cost
    return payload
