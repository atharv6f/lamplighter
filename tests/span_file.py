"""A trace sink that adds every batch to a file before it answers, for a killed chat.

A kill can come at any moment, so the file is the record of what the sink
was given: one line of JSON per span, each batch written in one call that
reaches the operating system at once. A test reads the file back and
checks it against the spans of the log.
"""

import dataclasses
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from lamplighter.ports import Failure, JsonObject, Span, SpanKind, canonical_json

SPANS_SENT = "spans_sent.jsonl"


class SpanFileSink:
    """Keeps every span it is given, copies too, in a file. It never fails."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def leaves_this_mac(self) -> bool:
        return False

    async def export(self, spans: Sequence[Span]) -> Failure | None:
        if not spans:
            return None
        lines = "".join(canonical_json(dataclasses.asdict(s)) + "\n" for s in spans)
        held = os.open(self._path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(held, lines.encode())
        finally:
            os.close(held)
        return None


def spans_in(path: Path) -> list[Span]:
    """Give every span the file holds, copies too, in the order they came."""
    if not path.exists():
        return []
    spans: list[Span] = []
    for line in path.read_text().splitlines():
        data = cast(JsonObject, json.loads(line))
        spans.append(
            Span(
                trace_id=str(data["trace_id"]),
                span_id=str(data["span_id"]),
                parent_id=cast(str | None, data["parent_id"]),
                name=str(data["name"]),
                kind=cast(SpanKind, data["kind"]),
                start_ms=cast(int, data["start_ms"]),
                duration_ms=cast(int, data["duration_ms"]),
                error=cast(str | None, data["error"]),
                attributes=cast(JsonObject, data["attributes"]),
            )
        )
    return spans
