"""The readers of a saved reply and tool result: only facts of the right type count."""

import pytest

from lamplighter.core.saved_facts import (
    TokenCounts,
    reply_failure,
    saved_cost,
    token_counts,
    tool_error,
)
from lamplighter.ports import JsonObject, JsonValue

USAGE: JsonObject = {
    "input_uncached": 100,
    "cache_read": 40,
    "cache_write": 10,
    "output": 20,
}


def test_the_four_counts_of_a_reply_are_read() -> None:
    counts = token_counts({"usage": USAGE})
    assert counts == TokenCounts(uncached=100, read=40, written=10, output=20)
    assert counts is not None and counts.input == 150


@pytest.mark.parametrize("key", list(USAGE))
@pytest.mark.parametrize("value", [None, True, 1.5, "100"])
def test_a_count_that_is_missing_or_not_a_whole_number_gives_no_counts(
    key: str, value: JsonValue
) -> None:
    usage: dict[str, JsonValue] = dict(USAGE)
    if value is None:
        del usage[key]
    else:
        usage[key] = value
    assert token_counts({"usage": usage}) is None


@pytest.mark.parametrize(("saved", "read"), [(1_234, 1_234), (0, 0), (True, None)])
def test_a_cost_is_read_only_as_a_whole_number(
    saved: JsonValue, read: int | None
) -> None:
    assert saved_cost({"cost_micro_dollars": saved}) == read
    assert saved_cost({}) is None


@pytest.mark.parametrize(
    ("end", "kind"),
    [
        ({"kind": "error", "failure": {"kind": "timeout"}}, "timeout"),
        ({"kind": "stop", "failure": {"kind": "timeout"}}, None),
        ({"kind": "made-up", "failure": {"kind": "timeout"}}, None),
        ({"failure": {"kind": "timeout"}}, None),
        ({"kind": "error"}, None),
    ],
)
def test_only_a_reply_that_ended_in_an_error_gives_a_failure_kind(
    end: JsonObject, kind: str | None
) -> None:
    assert reply_failure({"end": end}) == kind


@pytest.mark.parametrize(
    ("result", "word"),
    [
        ({"is_error": False}, None),
        ({"is_error": True}, "tool_error"),
        ({"is_error": "yes"}, None),
        ({"is_error": 1}, None),
        ({"is_error": True, "harness": "arguments_invalid"}, "arguments_invalid"),
        (
            {"is_error": True, "harness": "made_up", "failure_kind": "timeout"},
            "timeout",
        ),
    ],
)
def test_a_tool_results_error_word_is_its_failure_then_the_harness_then_a_flag(
    result: JsonObject, word: str | None
) -> None:
    assert tool_error(result) == word
