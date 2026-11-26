"""Carries out one tool call, or answers it in the harness's own words."""

from lamplighter.core import harness_texts
from lamplighter.core.event_types import TOOL_INTENT, TOOL_RESULT
from lamplighter.core.harness_texts import HarnessKind, with_repeat_warning
from lamplighter.core.repeat_rule import deserves_warning
from lamplighter.core.run_deps import Deps
from lamplighter.core.run_events import elapsed_ms, in_run
from lamplighter.core.run_view import OpenCall, RunView
from lamplighter.ports import (
    Failure,
    JsonObject,
    ToolResult,
    ToolSpec,
    event_id_for,
    utc_text,
)


async def run_call(
    session_id: str, view: RunView, call: OpenCall, deps: Deps
) -> Failure | None:
    """Save `tool_intent`, run the tool, and save its result.

    The intent is saved first, so after a kill the call is answered as
    interrupted and never run a second time.
    """
    part = call.call
    intent = in_run(
        view,
        utc_text(deps.clock.now()),
        TOOL_INTENT,
        {"call_id": part.id, "name": part.name},
        cause=call.reply_id,
    )
    intent_seq = await deps.log.append(session_id, (intent,), view.last_seq)
    if isinstance(intent_seq, Failure):
        return intent_seq
    if part.arguments is None:
        raise ValueError("a call whose arguments did not parse is never run")
    start = deps.clock.monotonic()
    outcome = await deps.tools.call(part.name, part.arguments)
    duration_ms = elapsed_ms(start, deps.clock.monotonic())
    if isinstance(outcome, Failure):
        outcome = ToolResult(
            text=harness_texts.tool_failed(part.name, outcome.kind, outcome.message),
            is_error=True,
        )
    text = _maybe_warned(view, call, outcome.text)
    result = in_run(
        view,
        utc_text(deps.clock.now()),
        TOOL_RESULT,
        {"call_id": part.id, "text": text, "is_error": outcome.is_error},
        cause=event_id_for(session_id, intent_seq),
        duration_ms=duration_ms,
    )
    saved = await deps.log.append(session_id, (result,), intent_seq)
    return saved if isinstance(saved, Failure) else None


async def answer_call(
    session_id: str,
    view: RunView,
    call: OpenCall,
    harness: HarnessKind,
    tools: tuple[ToolSpec, ...],
    deps: Deps,
) -> Failure | None:
    """Save the harness's own result for a call that is not run."""
    text = _harness_text(call, harness, tools)
    if harness in ("arguments_unparsed", "unknown_tool", "cut_off"):
        # A model that sends the same broken call again and again is
        # warned and then stopped, as one that repeats a working call is.
        text = _maybe_warned(view, call, text)
    result = in_run(
        view,
        utc_text(deps.clock.now()),
        TOOL_RESULT,
        harness_result(call, harness, text),
        cause=call.intent_id or call.reply_id,
    )
    saved = await deps.log.append(session_id, (result,), view.last_seq)
    return saved if isinstance(saved, Failure) else None


def harness_result(call: OpenCall, harness: HarnessKind, text: str) -> JsonObject:
    """Give the payload of a result the harness writes. It is always an error."""
    return {"call_id": call.call.id, "text": text, "is_error": True, "harness": harness}


def _harness_text(
    call: OpenCall, harness: HarnessKind, tools: tuple[ToolSpec, ...]
) -> str:
    name = call.call.name
    match harness:
        case "arguments_unparsed":
            return harness_texts.arguments_unparsed(name, call.call.parse_error or "")
        case "unknown_tool":
            return harness_texts.unknown_tool(name, [tool.name for tool in tools])
        case "cut_off":
            return harness_texts.cut_off(name)
        case "interrupted":
            return harness_texts.interrupted(name)
        case "stopped":
            return harness_texts.stopped(name)
        case "out_of_time":
            return harness_texts.out_of_time(name)
        case "not_run":
            return harness_texts.not_run(name)


def _maybe_warned(view: RunView, call: OpenCall, text: str) -> str:
    if deserves_warning(view, call.index, text):
        return with_repeat_warning(text)
    return text
