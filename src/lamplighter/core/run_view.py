"""Works out what the open run of a session has done, from its saved events alone."""

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from lamplighter.core.event_types import (
    MODEL_REQUEST,
    MODEL_RESPONSE,
    READ_VERSION,
    REPLY_READY,
    TOOL_INTENT,
    TOOL_RESULT,
)
from lamplighter.core.harness_texts import without_repeat_warning
from lamplighter.core.reply import Reply
from lamplighter.core.reply_shape import is_whole, tool_calls
from lamplighter.ports import (
    RUN_END,
    RUN_STARTED,
    Event,
    Failure,
    JsonObject,
    ToolCallPart,
    Usage,
    from_stored,
    saved_json,
)

_SOURCE = "run_view"

type RunReason = Literal["done", "limit", "cancelled", "failed"]
type MessageKind = Literal["reply", "notice"]

_REASONS: tuple[RunReason, ...] = ("done", "limit", "cancelled", "failed")
_KINDS: tuple[MessageKind, ...] = ("reply", "notice")


@dataclass(frozen=True, slots=True)
class CallRecord:
    """One tool call of the run's whole replies, for the repeat rule.

    `key` is the tool name with the arguments as saved text, or the raw text
    when they did not parse. `result` is the text of its result without the
    repeat warning, or None while it has none.
    """

    key: tuple[str, str, str]
    reply_seq: int
    result: str | None


@dataclass(frozen=True, slots=True)
class SavedIntent:
    """One saved `tool_intent`: its event id and its payload.

    `action_class` is None for an intent written before calls had a class.
    """

    event_id: str
    action_class: str | None
    payload: JsonObject


@dataclass(frozen=True, slots=True)
class OpenCall:
    """A call of the newest whole reply that has no result yet.

    `index` is its place among the run's calls, and `place` its place among
    the calls of its reply, whose event is at `reply_seq`. `intents` are the
    intents saved for it, oldest first: a call with one was started. `cut`
    says it is the last part of a reply that stopped at the output limit, so
    its arguments may be cut too.
    """

    call: ToolCallPart
    index: int
    reply_id: str
    reply_seq: int
    place: int
    intents: tuple[SavedIntent, ...]
    cut: bool

    @property
    def intent_id(self) -> str | None:
        """Give the event id of the newest intent, or None when it has none."""
        return self.intents[-1].event_id if self.intents else None


@dataclass(frozen=True, slots=True)
class LastReply:
    """The reply to the newest model request, and the event that holds it."""

    reply: Reply
    event_id: str


@dataclass(frozen=True, slots=True)
class Ending:
    """A saved `reply_ready` whose `run_end` is not saved yet."""

    seq: int
    text: str
    kind: MessageKind
    run_reason: RunReason
    run_detail: str


@dataclass(frozen=True, slots=True)
class RunView:
    """What the session's events say about its open run, and nothing else.

    With no open run, every field after `event_ids` is empty. What the run
    has used is summed over its saved events, so a resumed run keeps
    counting from where it was.
    """

    last_seq: int
    session_exists: bool
    event_ids: Mapping[str, int]
    run_id: str | None = None
    config_id: str | None = None
    step: int = 0
    used_ms: int = 0
    used_tokens: int = 0
    used_micro_dollars: int = 0
    open_calls: tuple[OpenCall, ...] = ()
    last_reply: LastReply | None = None
    bad_streak: int = 0
    calls: tuple[CallRecord, ...] = ()
    ending: Ending | None = None


@dataclass
class _Tally:
    run_id: str
    config_id: str | None
    step: int = 0
    used_ms: int = 0
    used_tokens: int = 0
    used_micro_dollars: int = 0
    last_reply: LastReply | None = None
    whole_id: str = ""
    whole_seq: int = 0
    whole: Reply | None = None
    bad_streak: int = 0
    first_call: int = 0
    ending: Ending | None = None


def view_of(events: Sequence[Event]) -> RunView | Failure:
    """Work out the view of a session's events, in order from seq 1.

    Gives `needs_user` for a type or version this code cannot read, and
    `fatal` for a payload that does not fit its type.
    """
    for event in events:
        if READ_VERSION.get(event.type) != event.v:
            return Failure(
                kind="needs_user",
                effect="none",
                message=f"This code cannot read a {event.type} event at version"
                f" {event.v}, at seq {event.seq}.",
                source=_SOURCE,
            )
    base = RunView(
        last_seq=events[-1].seq if events else 0,
        session_exists=bool(events),
        event_ids={event.event_id: event.seq for event in events},
    )
    start = _open_run_start(events)
    if start is None:
        return base
    try:
        return _run_view(base, events[start:])
    except ValueError:
        return Failure(
            kind="fatal",
            effect="none",
            message="An event of the open run does not fit its type.",
            source=_SOURCE,
        )


def _open_run_start(events: Sequence[Event]) -> int | None:
    for place in range(len(events) - 1, -1, -1):
        if events[place].type == RUN_END:
            return None
        if events[place].type == RUN_STARTED:
            return place
    return None


def _run_view(base: RunView, run: Sequence[Event]) -> RunView:
    first = run[0]
    config_id = first.payload.get("config_id")
    tally = _Tally(
        run_id=first.run_id or "",
        config_id=config_id if isinstance(config_id, str) else None,
    )
    calls: list[CallRecord] = []
    intents: dict[int, list[SavedIntent]] = {}
    for event in run[1:]:
        _count(tally, calls, intents, event)
    return RunView(
        last_seq=base.last_seq,
        session_exists=base.session_exists,
        event_ids=base.event_ids,
        run_id=tally.run_id,
        config_id=tally.config_id,
        step=tally.step,
        used_ms=tally.used_ms,
        used_tokens=tally.used_tokens,
        used_micro_dollars=tally.used_micro_dollars,
        open_calls=_open_calls(tally, calls, intents),
        last_reply=tally.last_reply,
        bad_streak=tally.bad_streak,
        calls=tuple(calls),
        ending=tally.ending,
    )


def _count(
    tally: _Tally,
    calls: list[CallRecord],
    intents: dict[int, list[SavedIntent]],
    event: Event,
) -> None:
    if event.type == MODEL_REQUEST:
        tally.step += 1
        tally.last_reply = None
    elif event.type == MODEL_RESPONSE:
        _count_reply(tally, calls, intents, event)
    elif event.type == TOOL_INTENT:
        place = _unanswered(tally, calls, _text(event, "call_id"))
        if place is not None:
            intents.setdefault(place, []).append(_saved_intent(event))
    elif event.type == TOOL_RESULT:
        tally.used_ms += event.duration_ms or 0
        place = _unanswered(tally, calls, _text(event, "call_id"))
        if place is not None:
            calls[place] = dataclasses.replace(
                calls[place], result=without_repeat_warning(_text(event, "text"))
            )
    elif event.type == REPLY_READY:
        tally.ending = Ending(
            seq=event.seq,
            text=_text(event, "text"),
            kind=_one_of(_KINDS, event, "kind"),
            run_reason=_one_of(_REASONS, event, "run_reason"),
            run_detail=_text(event, "run_detail"),
        )


def _count_reply(
    tally: _Tally,
    calls: list[CallRecord],
    intents: dict[int, list[SavedIntent]],
    event: Event,
) -> None:
    reply = from_stored(Reply, event.payload)
    tally.used_ms += event.duration_ms or 0
    tally.used_tokens += _tokens(reply.usage)
    cost = event.payload.get("cost_micro_dollars")
    tally.used_micro_dollars += cost if type(cost) is int else 0
    tally.last_reply = LastReply(reply=reply, event_id=event.event_id)
    if not is_whole(reply):
        tally.bad_streak += 1
        return
    tally.bad_streak = 0
    tally.whole, tally.whole_id, tally.whole_seq = reply, event.event_id, event.seq
    tally.first_call = len(calls)
    # Call ids are only unique inside one reply, so the intents of an
    # earlier reply are forgotten.
    intents.clear()
    calls.extend(
        CallRecord(key=call_key(call), reply_seq=event.seq, result=None)
        for call in tool_calls(reply)
    )


def _unanswered(tally: _Tally, calls: list[CallRecord], call_id: str) -> int | None:
    # An intent or a result belongs to the first call of the newest whole
    # reply that has its id and no result yet, so two calls that share an
    # id are kept apart.
    if tally.whole is None:
        return None
    for offset, call in enumerate(tool_calls(tally.whole)):
        place = tally.first_call + offset
        if call.id == call_id and calls[place].result is None:
            return place
    return None


def _saved_intent(event: Event) -> SavedIntent:
    action_class = event.payload.get("action_class")
    if action_class is not None and not isinstance(action_class, str):
        raise ValueError("tool_intent has an action_class that is not a text")
    return SavedIntent(event.event_id, action_class, event.payload)


def _open_calls(
    tally: _Tally, calls: list[CallRecord], intents: dict[int, list[SavedIntent]]
) -> tuple[OpenCall, ...]:
    if tally.whole is None:
        return ()
    parts = tally.whole.message.parts
    cut_reply = tally.whole.end.kind == "stop" and tally.whole.end.reason in (
        "max_output",
        "other",
    )
    found: list[OpenCall] = []
    for offset, call in enumerate(tool_calls(tally.whole)):
        place = tally.first_call + offset
        if calls[place].result is not None:
            continue
        found.append(
            OpenCall(
                call=call,
                index=place,
                reply_id=tally.whole_id,
                reply_seq=tally.whole_seq,
                place=offset,
                intents=tuple(intents.get(place, ())),
                cut=cut_reply and parts[-1] == call,
            )
        )
    return tuple(found)


def call_key(call: ToolCallPart) -> tuple[str, str, str]:
    """Give what makes two calls the same: the name and the arguments as text."""
    if call.arguments is None:
        return (call.name, "raw", call.raw_arguments or "")
    return (call.name, "json", saved_json(call.arguments))


def _tokens(usage: Usage | None) -> int:
    if usage is None:
        return 0
    counts = (usage.input_uncached, usage.cache_read, usage.cache_write, usage.output)
    return sum(count or 0 for count in counts)


def _text(event: Event, name: str) -> str:
    value = event.payload.get(name)
    if not isinstance(value, str):
        raise ValueError(f"{event.type} has no text field {name}")
    return value


def _one_of[T: str](allowed: tuple[T, ...], event: Event, name: str) -> T:
    value = _text(event, name)
    for option in allowed:
        if value == option:
            return option
    raise ValueError(f"{event.type} has an unknown {name}")
