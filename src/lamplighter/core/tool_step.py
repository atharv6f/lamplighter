"""Carries out one tool call, or answers it in the harness's own words."""

from lamplighter.core import harness_texts
from lamplighter.core.event_types import TOOL_INTENT, TOOL_RESULT
from lamplighter.core.harness_texts import HarnessKind, with_repeat_warning
from lamplighter.core.interruptible import CutShort, Finished, NotStarted, interruptible
from lamplighter.core.limits import time_left_ms
from lamplighter.core.observers import ToolCallEnded, ToolCallStarted, notify
from lamplighter.core.repeat_rule import deserves_warning
from lamplighter.core.run_deps import Deps
from lamplighter.core.run_events import elapsed_ms, in_run, open_run_id
from lamplighter.core.run_spec import RunSpec
from lamplighter.core.run_view import OpenCall, RunView
from lamplighter.ports import (
    Failure,
    JsonObject,
    ToolResult,
    ToolResultPart,
    ToolSpec,
    event_id_for,
    from_stored,
    utc_text,
)


async def run_call(
    session_id: str, view: RunView, call: OpenCall, spec: RunSpec, deps: Deps
) -> Failure | None:
    """Save `tool_intent`, run the tool, and save its result.

    The intent is saved first, so after a kill the call is answered as
    interrupted and never run a second time. A stop that is already known
    keeps the tool from starting. A stop, or the end of the run's time, cuts
    a running tool short. A tool that ends with a result inside the cancel
    bound has that result saved.
    """
    part = call.call
    if part.arguments is None:
        raise ValueError("a call whose arguments did not parse is never run")
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
    run_id = open_run_id(view)
    started = ToolCallStarted(session_id, run_id, view.step, part)
    await notify(deps.observers.before_tool, started, deps.clock)
    start = deps.clock.monotonic()
    raced = await interruptible(
        deps.tools.call(part.name, part.arguments),
        deps.running.signal_for(session_id),
        deps.clock,
        allowance_s=time_left_ms(view, spec) / 1000,
        bound_s=spec.cancel_bound_ms / 1000,
    )
    duration_ms = elapsed_ms(start, deps.clock.monotonic())
    match raced:
        case NotStarted():
            payload = harness_result(call, "not_run", harness_texts.not_run(part.name))
        case CutShort():
            payload = _cut_short(call, raced)
        case Finished():
            payload = _tool_result(view, call, raced.value)
    ran = not isinstance(raced, NotStarted)
    result = in_run(
        view,
        utc_text(deps.clock.now()),
        TOOL_RESULT,
        payload,
        cause=event_id_for(session_id, intent_seq),
        duration_ms=duration_ms if ran else None,
    )
    saved = await deps.log.append(session_id, (result,), intent_seq)
    if isinstance(saved, Failure):
        return saved
    if ran:
        part_result = from_stored(ToolResultPart, payload)
        ended = ToolCallEnded(
            session_id, run_id, view.step, part, part_result, duration_ms
        )
        await notify(deps.observers.after_tool, ended, deps.clock)
    return None


async def answer_call(
    session_id: str,
    view: RunView,
    call: OpenCall,
    harness: HarnessKind,
    tools: tuple[ToolSpec, ...],
    deps: Deps,
) -> Failure | None:
    """Save the harness's own result for a call that is not run.

    The result is warned as a tool's result is, so a model that sends the
    same broken call again and again is warned and then stopped. The same
    holds for a call answered `interrupted`: the run goes on after it, and
    a run that ends a repeat must first have warned of it.
    """
    text = _maybe_warned(view, call, _harness_text(call, harness, tools))
    result = in_run(
        view,
        utc_text(deps.clock.now()),
        TOOL_RESULT,
        harness_result(call, harness, text),
        cause=call.intent_id or call.reply_id,
    )
    saved = await deps.log.append(session_id, (result,), view.last_seq)
    return saved if isinstance(saved, Failure) else None


def _tool_result(
    view: RunView, call: OpenCall, outcome: ToolResult | Failure
) -> JsonObject:
    name = call.call.name
    if isinstance(outcome, Failure):
        outcome = ToolResult(
            text=harness_texts.tool_failed(name, outcome.kind, outcome.message),
            is_error=True,
            effect="none",
        )
    text = _maybe_warned(view, call, outcome.text)
    return {"call_id": call.call.id, "text": text, "is_error": outcome.is_error}


def _cut_short(call: OpenCall, cut: CutShort) -> JsonObject:
    name = call.call.name
    if cut.reason == "stop":
        harness, text = "stopped", harness_texts.stopped(name)
    else:
        harness, text = "out_of_time", harness_texts.out_of_time(name)
    if cut.still_running:
        text = f"{text} {harness_texts.DID_NOT_STOP}"
    return harness_result(call, harness, text)


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
