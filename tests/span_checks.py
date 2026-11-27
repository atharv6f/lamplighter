"""Checks the spans of a whole log: ids, tree, times, the shared ids, count, content.

These are the checks that hold on any log the loop writes, so the kill
tests run them on every log a kill leaves behind.
"""

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import cast

from lamplighter.core import span_names as names
from lamplighter.core.run_spans import WholeSpan, spans_of_run
from lamplighter.ports import Event, JsonObject, Span
from tests.span_count import promised_spans

_TRACE_ID = re.compile(r"[0-9a-f]{32}")
_SPAN_ID = re.compile(r"[0-9a-f]{16}")
_PARENT_KIND = {"step": "run", "model": "step", "tool": "step"}
# A shorter text could be a word a span holds for its own reasons, such as
# a tool's name or an end reason.
_SHORTEST_TEXT = 6


def log_spans(events: Sequence[Event]) -> list[WholeSpan]:
    """Give the whole spans of every run in a log, run by run."""
    runs: defaultdict[tuple[str, str], list[Event]] = defaultdict(list[Event])
    for event in events:
        if event.run_id is not None:
            runs[(event.session_id, event.run_id)].append(event)
    return [made for run in runs.values() for made in spans_of_run(run)]


def spans_wrong(events: Sequence[Event]) -> list[str]:
    """Give each rule the spans of a whole log break."""
    whole = log_spans(events)
    spans = [made.span for made in whole]
    wrong = _ids_wrong(spans) + _tree_wrong(spans, events) + _shared_wrong(spans)
    wrong += _times_wrong(spans) + _content_wrong(spans, events)
    if len(spans) != promised_spans(events):
        wrong.append(f"{len(spans)} spans, the log promises {promised_spans(events)}")
    return wrong


def _ids_wrong(spans: Sequence[Span]) -> list[str]:
    wrong = [
        f"span {span.name} has the ids {span.trace_id} {span.span_id}"
        for span in spans
        if not _TRACE_ID.fullmatch(span.trace_id)
        or not _SPAN_ID.fullmatch(span.span_id)
    ]
    shared = Counter(span.span_id for span in spans)
    wrong += [
        f"{count} spans share the id {key}"
        for key, count in shared.items()
        if count > 1
    ]
    return wrong


def _tree_wrong(spans: Sequence[Span], events: Sequence[Event]) -> list[str]:
    by_id = {span.span_id: span for span in spans}
    ended = {(e.session_id, e.run_id) for e in events if e.type == "run_end"}
    wrong: list[str] = []
    roots = Counter(span.trace_id for span in spans if span.parent_id is None)
    wrong += [
        f"trace {key} has {count} roots" for key, count in roots.items() if count > 1
    ]
    for span in spans:
        if span.kind == "run":
            if span.parent_id is not None:
                wrong.append(f"the run span {span.span_id} has a parent")
            continue
        parent = by_id.get(span.parent_id or "")
        if parent is None:
            run = (
                span.attributes[names.CONVERSATION_ID],
                span.attributes[names.RUN_ID],
            )
            if run in ended:
                wrong.append(f"{span.name} of an ended run has no parent")
        elif parent.kind != _PARENT_KIND[span.kind] or parent.trace_id != span.trace_id:
            wrong.append(f"{span.name} hangs under {parent.name}")
    return wrong


def _times_wrong(spans: Sequence[Span]) -> list[str]:
    by_id = {span.span_id: span for span in spans}
    wrong: list[str] = []
    for span in spans:
        if span.duration_ms < 0:
            wrong.append(f"{span.name} lasts {span.duration_ms} ms")
        parent = by_id.get(span.parent_id or "")
        if parent is not None and not _inside(span, parent):
            wrong.append(f"{span.name} is outside {parent.name}")
    return wrong


def _inside(child: Span, parent: Span) -> bool:
    child_end = child.start_ms + child.duration_ms
    return (
        parent.start_ms <= child.start_ms
        and child_end <= parent.start_ms + parent.duration_ms
    )


def _shared_wrong(spans: Sequence[Span]) -> list[str]:
    shared = (names.CONVERSATION_ID, names.RUN_ID, names.CONFIG_ID)
    return [
        f"{span.name} lacks {key}"
        for span in spans
        for key in shared
        if not isinstance(span.attributes.get(key), str)
    ]


def _content_wrong(spans: Sequence[Span], events: Sequence[Event]) -> list[str]:
    texts = {text for text in _texts(events) if len(text) >= _SHORTEST_TEXT}
    shown = [said for span in spans for said in span_texts(span)]
    return [
        f"a span holds the text {text!r}"
        for text in sorted(texts)
        if any(text in said for said in shown)
    ]


def span_texts(span: Span) -> list[str]:
    """Give every text a span holds: its name, and its keys and text values."""
    texts = [span.name, *span.attributes]
    for value in span.attributes.values():
        items = value if isinstance(value, list) else [value]
        texts += [item for item in items if isinstance(item, str)]
    return texts


def _texts(events: Sequence[Event]) -> list[str]:
    """Give the text of every message, result and reply in the log."""
    texts: list[str] = []
    for event in events:
        if event.type in ("user_message", "tool_result", "reply_ready"):
            texts.append(str(event.payload.get("text", "")))
        elif event.type == "model_response":
            texts += _reply_texts(event.payload)
    return texts


def _reply_texts(payload: JsonObject) -> list[str]:
    message = cast(JsonObject, payload.get("message") or {})
    parts = cast(list[JsonObject], message.get("parts") or [])
    return [str(part.get("text", "")) for part in parts]
