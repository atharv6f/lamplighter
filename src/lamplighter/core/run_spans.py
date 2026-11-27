"""Turns the saved events of one run into the spans that are whole. Pure.

A run gives one span for itself, one for each step, and under each step
one for the model call and one for each tool call. Each span becomes whole
at the log position of the one event that ends what it describes, and it
never changes after that. So a span may be sent as soon as it is whole.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast

from lamplighter.core import span_names as names
from lamplighter.core.event_types import (
    MODEL_REQUEST,
    MODEL_RESPONSE,
    TOOL_INTENT,
    TOOL_RESULT,
)
from lamplighter.core.saved_facts import (
    reply_failure,
    saved_cost,
    token_counts,
    tool_error,
)
from lamplighter.ports import (
    RUN_END,
    RUN_STARTED,
    Event,
    JsonObject,
    JsonValue,
    Span,
    SpanKind,
    span_id_for,
    trace_id_for,
)

# The number of the rules in this file: which spans a run gives and what
# each holds. A change to them needs a new number, so that new spans never
# meet old ones in a store that keeps the first copy of an id.
SPAN_LAYOUT = 1

# A model name as a provider writes one. It keeps out a sentence, a line
# break and the control characters a terminal acts on.
_MODEL_NAME = re.compile(r"[A-Za-z0-9._:/@-]{1,64}")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_STEP_TYPES = (MODEL_REQUEST, MODEL_RESPONSE, TOOL_INTENT, TOOL_RESULT)


@dataclass(frozen=True, slots=True)
class WholeSpan:
    """A span, and the log position of the event that made it whole."""

    span: Span
    pos: int


def spans_of_run(events: Sequence[Event]) -> tuple[WholeSpan, ...]:
    """Give each span of one run that is whole in `events`, ordered by position.

    `events` are the run's own events so far, in log order. The same events
    give the same spans, ids included, and a span that is whole in a list is
    the same span, at the same position, in every longer list of that run.
    A list with no `run_started` gives nothing. Reads no clock, no file and
    no setting.
    """
    started = next((event for event in events if event.type == RUN_STARTED), None)
    if started is None:
        return ()
    run = _RunFacts.of(started)
    steps = _steps(events)
    whole: list[WholeSpan] = []
    step_spans: list[WholeSpan] = []
    for step in steps:
        children = _children(run, step, events)
        whole.extend(children)
        made = _step_span(run, step, children, events)
        if made is not None:
            step_spans.append(made)
    whole.extend(step_spans)
    ended = _run_span(run, events, steps, step_spans)
    if ended is not None:
        whole.append(ended)
    return tuple(sorted(whole, key=lambda made: made.pos))


def model_name(reported: JsonValue) -> str | None:
    """Give a reported model name when it has the shape of one, or None.

    The name comes from the provider's server, so it is text from outside
    this repo. Only letters a to z and A to Z, digits 0 to 9 and the marks
    `.`, `_`, `:`, `/`, `@` and `-` pass, 1 to 64 of them.
    """
    if isinstance(reported, str) and _MODEL_NAME.fullmatch(reported):
        return reported
    return None


@dataclass(frozen=True, slots=True)
class _RunFacts:
    """What every span of one run shares."""

    started: Event
    run_id: str
    trace_id: str
    span_id: str
    common: JsonObject

    @staticmethod
    def of(started: Event) -> "_RunFacts":
        run_id = started.run_id
        if run_id is None:
            raise ValueError("a run_started event always has its run id")
        common: dict[str, JsonValue] = {
            names.CONVERSATION_ID: started.session_id,
            names.RUN_ID: run_id,
            names.LAYOUT: SPAN_LAYOUT,
        }
        _put(common, names.CONFIG_ID, _text(started.payload, "config_id"))
        return _RunFacts(
            started=started,
            run_id=run_id,
            trace_id=trace_id_for(SPAN_LAYOUT, started.session_id, run_id),
            span_id=span_id_for(SPAN_LAYOUT, run_id, "run", started.event_id),
            common=common,
        )

    def span_for(self, role: SpanKind, made_from: Event) -> str:
        return span_id_for(SPAN_LAYOUT, self.run_id, role, made_from.event_id)


@dataclass(slots=True)
class _Step:
    """The events of one step: its request, its reply, its intents and results."""

    number: int
    request: Event | None = None
    reply: Event | None = None
    entries: list[Event] = field(default_factory=list[Event])


@dataclass(frozen=True, slots=True)
class _Call:
    """One tool result, with the intents that were written for it."""

    result: Event
    intents: tuple[Event, ...]


def _steps(events: Sequence[Event]) -> list[_Step]:
    """Sort the events into steps by their step number. A step needs its request."""
    by_number: dict[int, _Step] = {}
    for event in events:
        if event.step is None or event.type not in _STEP_TYPES:
            continue
        step = by_number.setdefault(event.step, _Step(event.step))
        if event.type == MODEL_REQUEST:
            step.request = event
        elif event.type == MODEL_RESPONSE:
            step.reply = event
        else:
            step.entries.append(event)
    # A step with no request was never written by the loop, so it and the
    # results under it give no span.
    kept = [step for step in by_number.values() if step.request is not None]
    return sorted(kept, key=lambda step: _request(step).pos)


def _calls(step: _Step) -> list[_Call]:
    """Pair each result with the intents of its call id since the previous result.

    A model can give two calls of one reply the same id. Each result then
    gets the intents written for it, and never the other call's.
    """
    waiting: dict[str | None, list[Event]] = {}
    calls: list[_Call] = []
    for event in step.entries:
        call_id = _text(event.payload, "call_id")
        if event.type == TOOL_INTENT:
            waiting.setdefault(call_id, []).append(event)
        else:
            calls.append(_Call(event, tuple(waiting.pop(call_id, []))))
    return calls


def _children(run: _RunFacts, step: _Step, events: Sequence[Event]) -> list[WholeSpan]:
    """Give the step's model call and tool calls that are whole."""
    step_id = run.span_for("step", _request(step))
    children = [_tool_span(run, step, call, step_id) for call in _calls(step)]
    model = _model_span(run, step, step_id, events)
    return children if model is None else [model, *children]


def _model_span(
    run: _RunFacts, step: _Step, step_id: str, events: Sequence[Event]
) -> WholeSpan | None:
    request, reply = _request(step), step.reply
    # The loop writes a reply as the very next event of its session or never,
    # so any later event of the run means no reply can come.
    ender = reply if reply is not None else _next_after(events, request.pos)
    if ender is None:
        return None
    duration = reply.duration_ms if reply is not None else None
    start, end = _timed(_ms(ender.at), duration, _ms(request.at))
    model = _text(request.payload, "model")
    attributes = {**run.common, **_model_attributes(step)}
    attributes[names.SEQ] = request.seq
    error = _model_error(reply)
    _put(attributes, names.ERROR_TYPE, error)
    span = Span(
        trace_id=run.trace_id,
        span_id=run.span_for("model", request),
        parent_id=step_id,
        name=names.MODEL_SPAN if model is None else f"{names.MODEL_SPAN} {model}",
        kind="model",
        start_ms=start,
        duration_ms=end - start,
        error=error,
        attributes=attributes,
    )
    return WholeSpan(span, ender.pos)


def _model_attributes(step: _Step) -> dict[str, JsonValue]:
    request = _request(step).payload
    attributes: dict[str, JsonValue] = {
        names.OPERATION: names.CHAT,
        names.STEP: step.number,
    }
    _put(attributes, names.PROVIDER, _text(request, "provider"))
    _put(attributes, names.REQUEST_MODEL, _text(request, "model"))
    if step.reply is None:
        return attributes
    reply = step.reply.payload
    end = _object(reply, "end")
    _put(attributes, names.RESPONSE_MODEL, model_name(end.get("model_reported")))
    reason = _text(end, "reason")
    if _text(end, "kind") == "stop" and reason is not None:
        attributes[names.FINISH_REASONS] = [reason]
    attributes.update(_usage_attributes(_object(reply, "usage")))
    _put(attributes, names.COST, saved_cost(reply))
    attributes[names.TOOL_CALLS] = len(_tool_calls(step.reply))
    return attributes


def _usage_attributes(usage: JsonObject) -> dict[str, JsonValue]:
    uncached, read, written = (
        _whole(usage, key) for key in ("input_uncached", "cache_read", "cache_write")
    )
    attributes: dict[str, JsonValue] = {}
    if uncached is not None and read is not None and written is not None:
        attributes[names.INPUT_TOKENS] = uncached + read + written
    _put(attributes, names.OUTPUT_TOKENS, _whole(usage, "output"))
    if read is not None and read > 0:
        attributes[names.CACHE_READ_TOKENS] = read
    if written is not None and written > 0:
        attributes[names.CACHE_WRITE_TOKENS] = written
    return attributes


def _model_error(reply: Event | None) -> str | None:
    if reply is None:
        return "no_reply"
    return reply_failure(reply.payload)


def _tool_span(run: _RunFacts, step: _Step, call: _Call, step_id: str) -> WholeSpan:
    result = call.result
    opened = _ms(call.intents[-1].at) if call.intents else None
    start, end = _timed(_ms(result.at), result.duration_ms, opened)
    tool = _vetted_tool_name(call.intents, result, step.reply)
    error = tool_error(result.payload)
    attributes = {**run.common, **_tool_attributes(step, call, tool)}
    attributes[names.SEQ] = result.seq
    _put(attributes, names.ERROR_TYPE, error)
    span = Span(
        trace_id=run.trace_id,
        span_id=run.span_for("tool", result),
        parent_id=step_id,
        name=names.TOOL_SPAN if tool is None else f"{names.TOOL_SPAN} {tool}",
        kind="tool",
        start_ms=start,
        duration_ms=end - start,
        error=error,
        attributes=attributes,
    )
    return WholeSpan(span, result.pos)


def _tool_attributes(
    step: _Step, call: _Call, tool: str | None
) -> dict[str, JsonValue]:
    result = call.result.payload
    attributes: dict[str, JsonValue] = {
        names.OPERATION: names.EXECUTE_TOOL,
        names.TOOL_TYPE: names.FUNCTION,
        names.STARTS: len(call.intents),
        names.STEP: step.number,
    }
    _put(attributes, names.TOOL_NAME, tool)
    if call.intents:
        last = call.intents[-1].payload
        _put(attributes, names.ACTION_CLASS, _text(last, "action_class"))
        _put(attributes, names.EFFECT_ID, _whole(last, "effect_id"))
    text = result.get("text")
    if isinstance(text, str):
        attributes[names.RESULT_CHARS] = len(text)
    full = _object(result, "full_output")
    _put(attributes, names.FULL_OUTPUT_SHA256, _text(full, "sha256"))
    _put(attributes, names.FULL_OUTPUT_BYTES, _whole(full, "size"))
    if result.get("tainted") is True:
        attributes[names.OUTSIDE_TEXT] = True
    _put(attributes, names.HARNESS_KIND, _text(result, "harness"))
    return attributes


def _vetted_tool_name(
    intents: Sequence[Event], result: Event, reply: Event | None
) -> str | None:
    """Give a tool call's name when the harness looked the tool up, or None.

    The model writes a call's name, and a model can be made to write
    anything. An intent is written only for a tool the harness found. With
    no intent, the harness looked the tool up before it answered when the
    result has no harness word or the word `arguments_invalid`, and the name
    is taken from the reply only when exactly one call of it has the id.
    """
    if intents:
        return _text(intents[-1].payload, "name")
    harness = _text(result.payload, "harness")
    if harness not in (None, "arguments_invalid") or reply is None:
        return None
    call_id = _text(result.payload, "call_id")
    named = [part for part in _tool_calls(reply) if part.get("id") == call_id]
    if len(named) != 1:
        return None
    name = named[0].get("name")
    return name if isinstance(name, str) else None


def _step_span(
    run: _RunFacts, step: _Step, children: Sequence[WholeSpan], events: Sequence[Event]
) -> WholeSpan | None:
    request = _request(step)
    # No more children can come once the next request or the run's end is
    # saved. The step's last result is not its end.
    ender = next(
        (
            event
            for event in events
            if event.pos > request.pos and event.type in (MODEL_REQUEST, RUN_END)
        ),
        None,
    )
    if ender is None:
        return None
    held = [child.span for child in children if child.pos <= ender.pos]
    start, end = _holding(_ms(request.at), _ms(request.at), held)
    attributes: dict[str, JsonValue] = {
        **run.common,
        names.OPENINFERENCE_KIND: names.CHAIN,
        names.STEP: step.number,
        names.SEQ: request.seq,
    }
    span = Span(
        trace_id=run.trace_id,
        span_id=run.span_for("step", request),
        parent_id=run.span_id,
        name=f"{names.STEP_SPAN} {step.number}",
        kind="step",
        start_ms=start,
        duration_ms=end - start,
        error=None,
        attributes=attributes,
    )
    return WholeSpan(span, ender.pos)


def _run_span(
    run: _RunFacts,
    events: Sequence[Event],
    steps: Sequence[_Step],
    step_spans: Sequence[WholeSpan],
) -> WholeSpan | None:
    ended = next((event for event in events if event.type == RUN_END), None)
    if ended is None:
        return None
    held = [step.span for step in step_spans if step.pos <= ended.pos]
    opened = min(_ms(run.started.at), _ms(ended.at))
    start, end = _holding(opened, _ms(ended.at), held)
    reason = _text(ended.payload, "reason")
    detail = _text(ended.payload, "detail") or ""
    error = _run_error(reason, detail)
    attributes: dict[str, JsonValue] = {
        **run.common,
        names.OPERATION: names.INVOKE_AGENT,
        names.AGENT_NAME: names.AGENT,
        names.SEQ: run.started.seq,
        **_run_sums(steps),
    }
    _put(attributes, names.RUN_TRIGGER, _text(run.started.payload, "trigger"))
    _put(attributes, names.RUN_END_REASON, reason)
    _put(attributes, names.RUN_END_DETAIL, detail or None)
    _put(attributes, names.ERROR_TYPE, error)
    span = Span(
        trace_id=run.trace_id,
        span_id=run.span_id,
        parent_id=None,
        name=names.RUN_SPAN,
        kind="run",
        start_ms=start,
        duration_ms=end - start,
        error=error,
        attributes=attributes,
    )
    return WholeSpan(span, ended.pos)


def _run_error(reason: str | None, detail: str) -> str | None:
    # A run you stopped, or one that ended well, is not a fault.
    if reason == "failed":
        return detail or "failed"
    if reason == "limit":
        return f"limit_{detail}"
    return None


def _run_sums(steps: Sequence[_Step]) -> dict[str, JsonValue]:
    """Count the run's steps and tool calls, and add up its replies.

    A reply that lacks one of the four token counts is in no token sum, and
    a sum with nothing to add is left out, so an unpriced run has no cost.
    """
    replies = [step.reply.payload for step in steps if step.reply is not None]
    whole = [c for c in map(token_counts, replies) if c is not None]
    costs = [cost for cost in map(saved_cost, replies) if cost is not None]
    sums: dict[str, JsonValue] = {
        names.RUN_STEPS: len(steps),
        names.RUN_TOOL_CALLS: sum(len(_calls(step)) for step in steps),
        names.RUN_CALLS_WITHOUT_COST: len(replies) - len(costs),
        names.RUN_CALLS_WITHOUT_USAGE: len(replies) - len(whole),
        names.RUN_CALLS_WITHOUT_REPLY: len(steps) - len(replies),
    }
    if costs:
        sums[names.RUN_COST] = sum(costs)
    if whole:
        sums[names.RUN_INPUT_TOKENS] = sum(counts.input for counts in whole)
        sums[names.RUN_CACHE_READ_TOKENS] = sum(counts.read for counts in whole)
        sums[names.RUN_CACHE_WRITE_TOKENS] = sum(counts.written for counts in whole)
        sums[names.RUN_OUTPUT_TOKENS] = sum(counts.output for counts in whole)
    return sums


def _timed(end: int, duration: int | None, opened: int | None) -> tuple[int, int]:
    """Give a call's start and end.

    A length measured on the steady clock beats the difference of two wall
    clock readings, so it is used when it was saved. Otherwise the call
    started when it was opened, or at its end when that is earlier.
    """
    if duration is not None:
        return end - duration, end
    if opened is None:
        return end, end
    return min(opened, end), end


def _holding(start: int, end: int, children: Sequence[Span]) -> tuple[int, int]:
    """Give bounds that hold `start`, `end` and every child.

    Rounding, or a wall clock set back during a run, can make a child stick
    out of what its parent's own events say. Taking the outermost times
    keeps every child inside its parent.
    """
    starts = [start, *(child.start_ms for child in children)]
    ends = [end, *(child.start_ms + child.duration_ms for child in children)]
    return min(starts), max(ends)


def _tool_calls(reply: Event) -> list[JsonObject]:
    message = _object(reply.payload, "message")
    parts = message.get("parts")
    if not isinstance(parts, list):
        return []
    return [
        part
        for part in parts
        if isinstance(part, dict) and part.get("kind") == "tool_call"
    ]


def _next_after(events: Sequence[Event], pos: int) -> Event | None:
    return next((event for event in events if event.pos > pos), None)


def _request(step: _Step) -> Event:
    if step.request is None:
        raise ValueError("only a step with its request is kept")
    return step.request


def _ms(at: str) -> int:
    """Give a saved time as whole milliseconds since 1970 in UTC."""
    return (datetime.fromisoformat(at) - _EPOCH) // timedelta(milliseconds=1)


def _put(attributes: dict[str, JsonValue], name: str, value: JsonValue) -> None:
    # A fact that is not known is left out, never sent as an empty value.
    if value is not None:
        attributes[name] = value


def _text(payload: JsonObject, key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _whole(payload: JsonObject, key: str) -> int | None:
    value = payload.get(key)
    # True and False are whole numbers to Python, and never a count here.
    return value if type(value) is int else None


def _object(payload: JsonObject, key: str) -> JsonObject:
    value = payload.get(key)
    return cast(JsonObject, value) if isinstance(value, Mapping) else {}
