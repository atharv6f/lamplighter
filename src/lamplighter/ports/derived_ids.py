"""The ids worked out from where a thing sits, so a resumed step finds the same one."""

import hashlib

from lamplighter.ports.json_text import canonical_json
from lamplighter.ports.trace_sink import SpanKind


def event_id_for(session_id: str, seq: int) -> str:
    """Give the id of the event at `seq` in a session. Not for a user message."""
    return _hash(["event", session_id, seq])


def inbound_event_id(channel: str, native_id: str) -> str:
    """Give the id of the user message event made from one inbound message.

    The same message that arrives twice gets the same id, so the log's unique
    index refuses the second copy.
    """
    return _hash(["inbound", channel, native_id])


def effect_key(session_id: str, reply_seq: int, call_index: int, attempt: int) -> str:
    """Give the key of one run of one tool call.

    A resumed step works out the same key from what is saved, and so finds
    the intent it wrote before the kill.
    """
    return _hash(["effect", session_id, reply_seq, call_index, attempt])


def out_id_for(session_id: str, seq: int) -> str:
    """Give the id of the message to you made from the event at `seq`.

    The event is the run's reply_ready, so a close that runs again after a
    kill enqueues the same row and the outbox keeps one.
    """
    return _hash(["out", session_id, seq])


def trace_id_for(layout: int, session_id: str, run_id: str) -> str:
    """Give the trace id of one run: 32 hex characters.

    A run is known by its session and its run id together, because nothing
    stops two sessions from holding the same run id. `layout` is the number
    of the rules that make spans, so spans built under new rules never meet
    old ones in a store that keeps the first copy of an id.
    """
    return _hash(["trace", layout, session_id, run_id])[:32]


def span_id_for(layout: int, run_id: str, role: SpanKind, event_id: str) -> str:
    """Give the id of the span made from one event in one role: 16 hex characters.

    The role is there because a step and its model call are made from the
    same event. The run id is there because an event id comes back when a
    deleted session's id is used again, and a run id is new every time.
    """
    return _hash(["span", layout, run_id, role, event_id])[:16]


def _hash(items: list[str | int]) -> str:
    # The first item names the kind of id, so two kinds never share a hash.
    return hashlib.sha256(canonical_json(items).encode("utf-8")).hexdigest()
