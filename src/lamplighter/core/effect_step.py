"""Carries out one change through the effect ledger, and settles one a kill cut off.

The ledger's row is written before the intent, and the intent before the
tool is called, so a kill at any point leaves enough on the drive for the
next pass to carry on without applying the change a second time.
"""

from collections.abc import Sequence
from typing import Literal

from lamplighter.core import harness_texts
from lamplighter.core.action import action_for
from lamplighter.core.event_types import TOOL_INTENT, TOOL_RESULT
from lamplighter.core.harness_texts import UNSETTLED_REASONS, UnsettledReason
from lamplighter.core.interruptible import (
    CutShort,
    Finished,
    NotStarted,
    Raced,
    interruptible,
)
from lamplighter.core.limits import time_left_ms
from lamplighter.core.observers import ToolCallEnded, ToolCallStarted, notify
from lamplighter.core.run_deps import Deps
from lamplighter.core.run_events import elapsed_ms, in_run, open_run_id
from lamplighter.core.run_spec import RunSpec
from lamplighter.core.run_view import OpenCall, RunView
from lamplighter.core.tool_step import harness_result, tool_payload, warned_if_repeated
from lamplighter.ports import (
    ActionClass,
    Effect,
    EffectResult,
    Failure,
    JsonObject,
    Outcome,
    Plan,
    ToolEntry,
    ToolResult,
    ToolResultPart,
    effect_key,
    event_id_for,
    from_stored,
    utc_text,
)

_CHANGE_CLASSES: tuple[ActionClass, ...] = (
    "reversible_write",
    "compensable",
    "irreversible",
    "external_message",
)


async def run_change(
    session_id: str,
    view: RunView,
    call: OpenCall,
    entry: ToolEntry,
    spec: RunSpec,
    deps: Deps,
) -> Failure | None:
    """Plan the change, begin its row, save the intent, apply it, and save how it ended.

    A plan that gives a result changes nothing: the result is saved with no
    intent and no new row. A row an earlier process began is the truth: its
    own target, probe and undo are used, and a row that is no longer
    intended gives its saved result with no tool call.
    """
    part = call.call
    if part.arguments is None:
        raise ValueError("a call whose arguments did not parse is never run")
    planned = await deps.tools.plan(part.name, part.arguments)
    if not isinstance(planned, Plan):
        return await _nothing_to_change(session_id, view, call, entry, planned, deps)
    action = action_for(session_id, call, entry.action_class, planned.target)
    begun = await deps.ledger.begin(action, planned.probe, planned.undo)
    if isinstance(begun, Failure):
        return begun
    row = begun.effect
    if row.state != "intended":
        return await settle_row(session_id, view, call, row, deps)
    intent = in_run(
        view,
        utc_text(deps.clock.now()),
        TOOL_INTENT,
        {
            "call_id": part.id,
            "name": part.name,
            "action_class": entry.action_class,
            "target": row.action.target,
            "effect_id": row.effect_id,
        },
        cause=call.reply_id,
    )
    intent_seq = await deps.log.append(session_id, (intent,), view.last_seq)
    if isinstance(intent_seq, Failure):
        return intent_seq
    run_id = open_run_id(view)
    started = ToolCallStarted(session_id, run_id, view.step, part)
    await notify(deps.observers.before_tool, started, deps.clock)
    plan = Plan(target=row.action.target, probe=row.probe, undo=row.undo)
    start = deps.clock.monotonic()
    raced = await interruptible(
        deps.tools.apply(part.name, part.arguments, plan),
        deps.running.signal_for(session_id),
        deps.clock,
        allowance_s=time_left_ms(view, spec) / 1000,
        bound_s=spec.cancel_bound_ms / 1000,
    )
    duration_ms = elapsed_ms(start, deps.clock.monotonic())
    payload = await _ended(view, call, entry, row, raced, deps)
    if isinstance(payload, Failure):
        return payload
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


async def settle_effect(
    session_id: str, view: RunView, call: OpenCall, deps: Deps
) -> Failure | None:
    """Answer a change whose intent is saved and whose result is not, from its row.

    The class and the target come from the saved intent, not from today's
    tools, so this works after the settings changed. The tool's `apply` is
    never called here.
    """
    intent = call.intents[0]
    target = intent.payload.get("target")
    if intent.action_class not in _CHANGE_CLASSES or not isinstance(target, str):
        raise ValueError("the intent of a change has a class and a target")
    action = action_for(session_id, call, intent.action_class, target)
    # The row is saved before its intent, so `begin` gives it back and
    # writes nothing. A new row means the store lost one: a bug, not a case.
    begun = await deps.ledger.begin(action, None, None)
    if isinstance(begun, Failure):
        return begun
    if begun.is_new:
        raise ValueError(f"the intent of {target} has no row in the ledger")
    return await settle_row(session_id, view, call, begun.effect, deps)


async def settle_row(
    session_id: str, view: RunView, call: OpenCall, row: Effect, deps: Deps
) -> Failure | None:
    """Save the result a row stands for, carrying on from where it was cut off."""
    payload: JsonObject | Failure
    match row.state:
        case "intended":
            payload = await _unknown(view, call, row, "interrupted", False, None, deps)
        case "unknown":
            reason, failure = _saved_reason(row)
            payload = await _checked(view, call, row, reason, False, failure, deps)
        case "resolved":
            reason, failure = _saved_reason(row)
            happened = row.resolution.happened if row.resolution else None
            payload = _unsettled(view, call, reason, happened, False, failure)
        case "done" | "failed" | "undone":
            payload = _saved_output(row)
    if isinstance(payload, Failure):
        return payload
    result = in_run(
        view,
        utc_text(deps.clock.now()),
        TOOL_RESULT,
        payload,
        cause=call.intent_id or call.reply_id,
    )
    saved = await deps.log.append(session_id, (result,), view.last_seq)
    return saved if isinstance(saved, Failure) else None


async def close_unstarted(
    session_id: str,
    answered: Sequence[tuple[OpenCall, JsonObject]],
    deps: Deps,
) -> Failure | None:
    """Close the row of each call that was begun and never started, as failed.

    `answered` holds each open call with the `not_run` payload the run's end
    gives it. Only calls with no intent are looked for, by the key worked
    out from where the call came from, so a row is closed also when its tool
    is no longer listed. The ledger is read only when there is such a call.
    """
    unstarted = [(call, payload) for call, payload in answered if not call.intents]
    if not unstarted:
        return None
    rows = await deps.ledger.unresolved()
    if isinstance(rows, Failure):
        return rows
    intended = {row.effect_key: row for row in rows if row.state == "intended"}
    for call, payload in unstarted:
        row = intended.get(effect_key(session_id, call.reply_seq, call.place, 0))
        if row is not None:
            closed = await _finished(row, "failed", payload, deps)
            if isinstance(closed, Failure):
                return closed
    return None


async def _nothing_to_change(
    session_id: str,
    view: RunView,
    call: OpenCall,
    entry: ToolEntry,
    planned: ToolResult | Failure,
    deps: Deps,
) -> Failure | None:
    # A kill after `begin` and before the intent may have left a row for
    # this call. Nothing was started, so that row is closed as failed.
    payload = await tool_payload(view, call, entry, planned, deps)
    closed = await close_unstarted(session_id, ((call, payload),), deps)
    if isinstance(closed, Failure):
        return closed
    result = in_run(
        view, utc_text(deps.clock.now()), TOOL_RESULT, payload, cause=call.reply_id
    )
    saved = await deps.log.append(session_id, (result,), view.last_seq)
    return saved if isinstance(saved, Failure) else None


async def _ended(
    view: RunView,
    call: OpenCall,
    entry: ToolEntry,
    row: Effect,
    raced: Raced[ToolResult | Failure],
    deps: Deps,
) -> JsonObject | Failure:
    # In the ledger, failed means "did not happen". A tool that ran and
    # reported an error is done.
    name = call.call.name
    match raced:
        case NotStarted():
            payload = harness_result(call, "not_run", harness_texts.not_run(name))
            return await _finished(row, "failed", payload, deps)
        case CutShort():
            reason = "stopped" if raced.reason == "stop" else "out_of_time"
            still = raced.still_running
            return await _unknown(view, call, row, reason, still, None, deps)
        case Finished():
            outcome = raced.value
            if isinstance(outcome, Failure) and outcome.effect == "unknown":
                failure = harness_texts.tool_failed(name, outcome.kind, outcome.message)
                return await _unknown(
                    view, call, row, "unconfirmed", False, failure, deps
                )
            payload = await tool_payload(view, call, entry, outcome, deps)
            state = "done" if outcome.effect == "done" else "failed"
            return await _finished(row, state, payload, deps)


async def _finished(
    row: Effect, outcome: Literal["done", "failed"], payload: JsonObject, deps: Deps
) -> JsonObject | Failure:
    finished = await deps.ledger.finish(row.effect_id, EffectResult(outcome, payload))
    return finished if isinstance(finished, Failure) else payload


async def _unknown(
    view: RunView,
    call: OpenCall,
    row: Effect,
    reason: UnsettledReason,
    still_running: bool,
    failure: str | None,
    deps: Deps,
) -> JsonObject | Failure:
    # `still_running` is a note for a person who reads the ledger. No rule
    # reads it back.
    output: dict[str, str | bool] = {"reason": reason, "still_running": still_running}
    if failure is not None:
        output["failure"] = failure
    finished = await deps.ledger.finish(row.effect_id, EffectResult("unknown", output))
    if isinstance(finished, Failure):
        return finished
    return await _checked(view, call, row, reason, still_running, failure, deps)


async def _checked(
    view: RunView,
    call: OpenCall,
    row: Effect,
    reason: UnsettledReason,
    still_running: bool,
    failure: str | None,
    deps: Deps,
) -> JsonObject | Failure:
    # A tool this pass saw keep running might still finish after a check,
    # so its row is left unknown.
    probe = row.probe
    if probe is None or still_running:
        return _unsettled(view, call, reason, None, still_running, failure)
    found = await deps.tools.check(probe)
    if found is not None:
        outcome = Outcome(happened=found, settled_by="probe", evidence=probe)
        resolved = await deps.ledger.resolve(row.effect_id, outcome)
        if isinstance(resolved, Failure):
            return resolved
    return _unsettled(view, call, reason, found, False, failure)


def _unsettled(
    view: RunView,
    call: OpenCall,
    reason: UnsettledReason,
    found: bool | None,
    still_running: bool,
    failure: str | None,
) -> JsonObject:
    text = harness_texts.unsettled(reason, call.call.name, found, failure)
    if still_running:
        text = f"{text} {harness_texts.DID_NOT_STOP}"
    return harness_result(call, reason, warned_if_repeated(view, call, text))


def _saved_reason(row: Effect) -> tuple[UnsettledReason, str | None]:
    output: JsonObject = row.result.output if row.result is not None else {}
    reason, failure = output.get("reason"), output.get("failure")
    for known in UNSETTLED_REASONS:
        if reason == known:
            return known, failure if isinstance(failure, str) else None
    raise ValueError(f"effect {row.effect_id} has no reason for its unknown outcome")


def _saved_output(row: Effect) -> JsonObject:
    if row.result is None:
        raise ValueError(f"effect {row.effect_id} has an outcome and no result")
    return row.result.output
