"""Picks the one next move of a session from its view and its waiting messages.

The checks run in a fixed order, and the first whose condition holds wins.
Checks that finish something a kill may have cut in half come before every
check that ends a run, and a stop comes before every check that spends.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lamplighter.core.arguments_check import problems
from lamplighter.core.harness_texts import (
    CONFIG_CHANGED_NOTICE,
    CUT_REPLY_LINE,
    EMPTY_REPLY_NOTICE,
    REFUSED_NOTICE,
    RETRYABLE_KINDS,
    STOPPED_NOTICE,
    HarnessKind,
    limit_notice,
    model_failed_notice,
    repeated_call_notice,
)
from lamplighter.core.limits import (
    LimitHit,
    first_limit_reached,
    seconds_hit,
    time_is_up,
)
from lamplighter.core.model_choice import Signal, has_model_above
from lamplighter.core.repeat_rule import ends_the_run
from lamplighter.core.reply_shape import (
    final_text,
    is_empty,
    is_refused,
    is_whole,
    tool_calls,
)
from lamplighter.core.route_signals import ending_signal
from lamplighter.core.route_step import is_private, profiles
from lamplighter.core.run_spec import RunSpec
from lamplighter.core.run_view import (
    Ending,
    MessageKind,
    OpenCall,
    RunReason,
    RunView,
)
from lamplighter.ports import (
    ErrorEvent,
    Failure,
    Inbound,
    InboxEntry,
    ToolEntry,
    inbound_event_id,
)

STOP_COMMAND = "stop"


@dataclass(frozen=True, slots=True)
class FinishRouting:
    """Mark each inbox row whose message is already in the log: (inbox id, seq)."""

    routes: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class Idle:
    """Nothing to do: no open run and no waiting row."""


@dataclass(frozen=True, slots=True)
class DropStops:
    """Use up the stops at the front of the queue, with no run to stop."""

    stops: tuple[InboxEntry, ...]
    new_session: bool


@dataclass(frozen=True, slots=True)
class StartRun:
    """Start a run with the messages at the front of the queue."""

    messages: tuple[InboxEntry, ...]
    new_session: bool


@dataclass(frozen=True, slots=True)
class CloseRun:
    """Use up the pending stops, enqueue the ending, and save `run_end`."""

    ending: Ending
    stops: tuple[InboxEntry, ...]


@dataclass(frozen=True, slots=True)
class AnswerCall:
    """Save a result for the call that the loop writes itself."""

    call: OpenCall
    harness: HarnessKind


@dataclass(frozen=True, slots=True)
class EndRun:
    """Save the end batch of a run: why it ended, and what you are told."""

    reason: RunReason
    detail: str
    kind: MessageKind
    text: str
    limit: LimitHit | None = None


@dataclass(frozen=True, slots=True)
class SettleEffect:
    """Answer a change whose intent is saved from its ledger row, without a run."""

    call: OpenCall


@dataclass(frozen=True, slots=True)
class RunCall:
    """Run one tool call: for the first time, or a read once more after a kill."""

    call: OpenCall


@dataclass(frozen=True, slots=True)
class TakeMessages:
    """Add the waiting messages to the open run."""

    messages: tuple[InboxEntry, ...]


@dataclass(frozen=True, slots=True)
class CallModel:
    """Ask the model for the next reply."""


@dataclass(frozen=True, slots=True)
class Escalate:
    """Move the run up its ladder on this signal, or end it as `end` if none is up."""

    signal: Signal
    end: EndRun


type Move = (
    FinishRouting
    | Idle
    | DropStops
    | StartRun
    | CloseRun
    | AnswerCall
    | EndRun
    | SettleEffect
    | RunCall
    | TakeMessages
    | CallModel
    | Escalate
)


def is_stop(inbound: Inbound) -> bool:
    """Say whether an inbox row is a stop: the command named `stop`."""
    return inbound.kind == "command" and inbound.command == STOP_COMMAND


def next_move(
    view: RunView,
    rows: Sequence[InboxEntry],
    spec: RunSpec,
    entries: Mapping[str, ToolEntry],
) -> Move:
    """Pick the next move. `rows` are the session's pending inbox rows, oldest first.

    `entries` are the tools listed now, by name.

    Raises ValueError for a row that is neither a message nor a stop,
    because the intake lets nothing else in, so such a row is a bug.
    """
    for row in rows:
        if row.inbound.kind != "message" and not is_stop(row.inbound):
            raise ValueError(
                f"inbox row {row.inbox_id} is neither a message nor a stop"
            )
    routes = _already_saved(view, rows)
    if routes:
        return FinishRouting(routes)
    if view.run_id is None:
        return _with_no_run(view, rows)
    if view.ending is not None:
        stops = tuple(row for row in rows if is_stop(row.inbound))
        return CloseRun(view.ending, stops)
    return _in_open_run(view, rows, spec, entries)


def _already_saved(
    view: RunView, rows: Sequence[InboxEntry]
) -> tuple[tuple[int, int], ...]:
    # A stop is never saved as an event, so only a message can be found.
    routes: list[tuple[int, int]] = []
    for row in rows:
        seq = view.event_ids.get(_event_id(row.inbound))
        if seq is not None:
            routes.append((row.inbox_id, seq))
    return tuple(routes)


def _event_id(inbound: Inbound) -> str:
    return inbound_event_id(inbound.channel, inbound.native_id)


def _with_no_run(view: RunView, rows: Sequence[InboxEntry]) -> Move:
    if not rows:
        return Idle()
    leading_stop = is_stop(rows[0].inbound)
    front: list[InboxEntry] = []
    for row in rows:
        if is_stop(row.inbound) != leading_stop:
            break
        front.append(row)
    if leading_stop:
        return DropStops(tuple(front), new_session=not view.session_exists)
    return StartRun(tuple(front), new_session=not view.session_exists)


def _in_open_run(
    view: RunView,
    rows: Sequence[InboxEntry],
    spec: RunSpec,
    entries: Mapping[str, ToolEntry],
) -> Move:
    first = view.open_calls[0] if view.open_calls else None
    stop_pending = any(is_stop(row.inbound) for row in rows)
    if first is not None and first.intents:
        return _for_started_call(view, first, spec, entries, stop_pending)
    if first is None:
        ending = _what_the_reply_leads_to(view, spec)
        if ending is not None:
            return _or_escalate(view, spec, ending, stop_pending)
    if view.config_id != spec.config_id:
        return EndRun("failed", "config_changed", "notice", CONFIG_CHANGED_NOTICE)
    if stop_pending:
        return EndRun("cancelled", "", "notice", STOPPED_NOTICE)
    if first is not None:
        return _for_open_call(view, first, spec, entries)
    hit = first_limit_reached(view, spec)
    if hit is not None:
        return _limit_end(hit)
    messages = tuple(row for row in rows if not is_stop(row.inbound))
    if messages:
        return TakeMessages(messages)
    return CallModel()


def _for_started_call(
    view: RunView,
    first: OpenCall,
    spec: RunSpec,
    entries: Mapping[str, ToolEntry],
    stop_pending: bool,
) -> Move:
    # A change that had its intent saved is settled before anything can end
    # the run: a stop, new settings or the time.
    if first.intents[0].action_class not in (None, "read"):
        return SettleEffect(first)
    if _may_read_again(view, first, spec, entries, stop_pending):
        return RunCall(first)
    return AnswerCall(first, "interrupted")


def _may_read_again(
    view: RunView,
    first: OpenCall,
    spec: RunSpec,
    entries: Mapping[str, ToolEntry],
    stop_pending: bool,
) -> bool:
    # One intent only, so a read that kills the harness each time it runs
    # is started twice and then answered. The saved class says what the
    # tool was, and today's entry says what it is.
    entry = entries.get(first.call.name)
    return (
        len(first.intents) == 1
        and first.intents[0].action_class == "read"
        and view.config_id == spec.config_id
        and entry is not None
        and entry.action_class == "read"
        and not stop_pending
        and not time_is_up(view, spec)
    )


def _for_open_call(
    view: RunView, first: OpenCall, spec: RunSpec, entries: Mapping[str, ToolEntry]
) -> Move:
    if time_is_up(view, spec):
        return _limit_end(seconds_hit(view, spec))
    if ends_the_run(view, first.index):
        notice = repeated_call_notice(first.call.name)
        return EndRun("failed", "repeated_call", "notice", notice)
    if first.cut:
        return AnswerCall(first, "cut_off")
    if first.call.arguments is None:
        return AnswerCall(first, "arguments_unparsed")
    entry = entries.get(first.call.name)
    if entry is None:
        return AnswerCall(first, "unknown_tool")
    if problems(entry.spec.arguments_schema, first.call.arguments):
        return AnswerCall(first, "arguments_invalid")
    return RunCall(first)


def _limit_end(hit: LimitHit) -> EndRun:
    notice = limit_notice(hit.limit, hit.allowed)
    return EndRun("limit", hit.limit, "notice", notice, limit=hit)


def _or_escalate(
    view: RunView, spec: RunSpec, ending: EndRun, stop_pending: bool
) -> EndRun | Escalate:
    # Only a run under its own settings moves up, so no ladder of other
    # settings is read. A stop or a limit ends it as it would have ended.
    signal = ending_signal(ending.detail)
    if (
        signal is None
        or view.route is None
        or view.config_id != spec.config_id
        or stop_pending
        or first_limit_reached(view, spec) is not None
    ):
        return ending
    private = is_private(view, spec)
    if not has_model_above(spec.routes, profiles(spec), private, view.route.position):
        return ending
    return Escalate(signal, ending)


def _what_the_reply_leads_to(view: RunView, spec: RunSpec) -> EndRun | None:
    last = view.last_reply
    if last is None:
        return None
    if view.route is not None and last.seq < view.route.seq:
        # The run moved to another model since this reply, which leads to
        # nothing now.
        return None
    reply = last.reply
    if isinstance(reply.end, ErrorEvent):
        some_arrived = bool(reply.message.parts)
        return _after_a_failed_call(view, spec, reply.end.failure, some_arrived)
    if is_whole(reply):
        if tool_calls(reply):
            return None
        text = final_text(reply)
        if reply.end.reason in ("max_output", "other"):
            text = f"{text}\n\n{CUT_REPLY_LINE}"
        return EndRun("done", "", "reply", text)
    if is_refused(reply):
        return EndRun("failed", "refused", "notice", REFUSED_NOTICE)
    if is_empty(reply) and view.bad_streak >= 2:
        return EndRun("failed", "empty_reply", "notice", EMPTY_REPLY_NOTICE)
    return None


def _after_a_failed_call(
    view: RunView, spec: RunSpec, failure: Failure, some_arrived: bool
) -> EndRun | None:
    if failure.kind == "timeout" and time_is_up(view, spec):
        # The model call ran out of time because the run did.
        return _limit_end(seconds_hit(view, spec))
    if failure.kind in RETRYABLE_KINDS and some_arrived and view.bad_streak == 1:
        return None
    notice = model_failed_notice(failure.kind, failure.message)
    return EndRun("failed", f"model_{failure.kind}", "notice", notice)
