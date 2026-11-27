"""Reads the facts a reply and a tool result were saved with, for spans and the report.

A payload may be from an older version of the loop, so a fact that is
missing or of another type is given as None, never guessed.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from lamplighter.ports import JsonObject


@dataclass(frozen=True, slots=True)
class TokenCounts:
    """The four token counts of one reply."""

    uncached: int
    read: int
    written: int
    output: int

    @property
    def input(self) -> int:
        """Give every input token: not cached, read from the cache and written to it."""
        return self.uncached + self.read + self.written


def token_counts(reply: JsonObject) -> TokenCounts | None:
    """Give the four token counts of a reply, or None when one is missing."""
    usage = _object(reply, "usage")
    counts = [
        _whole(usage, key)
        for key in ("input_uncached", "cache_read", "cache_write", "output")
    ]
    uncached, read, written, output = counts
    if uncached is None or read is None or written is None or output is None:
        return None
    return TokenCounts(uncached, read, written, output)


def saved_cost(reply: JsonObject) -> int | None:
    """Give the cost a reply was saved with, in micro-dollars, or None."""
    return _whole(reply, "cost_micro_dollars")


def reply_failure(reply: JsonObject) -> str | None:
    """Give the failure kind of a reply that ended in an error, or None."""
    end = _object(reply, "end")
    if _text(end, "kind") != "error":
        return None
    return _text(_object(end, "failure"), "kind")


def tool_error(result: JsonObject) -> str | None:
    """Give the error word of a tool result, or None when the call went well."""
    # Tried in this order: the tool's own failure, the harness's answer, and
    # a tool that ran and said it failed.
    kind = _text(result, "failure_kind")
    if kind is not None:
        return kind
    harness = _text(result, "harness")
    if harness is not None:
        return harness
    return "tool_error" if result.get("is_error") is True else None


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
