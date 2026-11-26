"""Runs the code of the live tests against the scripted model.

The live tests call the real API, so the default run leaves them out. A
mistake in their own code would then first show on a call that costs
money. These tests run that code with made-up replies.
"""

import random

import pytest

from lamplighter.adapters import ScriptedBreak, ScriptedModel, ScriptedReply
from lamplighter.app.config import load_config
from lamplighter.ports import TextPart, ToolCallPart, Usage
from tests.live import test_anthropic as live
from tests.model_samples import PROFILE, REASONING, THROTTLED

CONFIG = load_config(live.SETTINGS)
ANSWER = TextPart(text="Milk, eggs and tea.")
PLAIN = ScriptedReply(parts=(ANSWER,), reason="end")
COLD = ScriptedReply(parts=(ANSWER,), reason="end", usage=Usage(10, 0, 5_000, 8))
WARM = ScriptedReply(parts=(ANSWER,), reason="end", usage=Usage(10, 5_000, 0, 8))
CALL = ToolCallPart(id="call_a", name="read_file", arguments={"path": "list.txt"})
ASKS_FOR_TOOL = ScriptedReply(parts=(REASONING, CALL), reason="tool_calls")


def scripted(*turns: ScriptedReply | ScriptedBreak) -> ScriptedModel:
    return ScriptedModel(PROFILE, turns, random.Random(0))


def test_the_cache_test_passes_when_the_second_call_reads_from_the_cache() -> None:
    cache_test = live.test_a_second_call_with_the_same_long_prompt_reads_from_the_cache
    cache_test(scripted(COLD, WARM), CONFIG)


def test_the_cache_test_fails_when_the_second_call_reads_nothing() -> None:
    cache_test = live.test_a_second_call_with_the_same_long_prompt_reads_from_the_cache
    with pytest.raises(AssertionError):
        cache_test(scripted(COLD, COLD), CONFIG)


def test_the_round_trip_test_passes_on_a_tool_call_and_then_an_answer() -> None:
    round_trip = live.test_a_tool_call_and_its_result_make_a_round_trip
    round_trip(scripted(ASKS_FOR_TOOL, PLAIN), CONFIG)


def test_the_round_trip_test_fails_when_the_model_calls_no_tool() -> None:
    round_trip = live.test_a_tool_call_and_its_result_make_a_round_trip
    with pytest.raises(AssertionError, match="called no tool"):
        round_trip(scripted(PLAIN, PLAIN), CONFIG)


def test_the_count_test_passes_when_the_count_is_the_real_size() -> None:
    count_test = (
        live.test_a_token_count_is_as_close_to_the_real_size_as_the_profile_says
    )
    count_test(scripted(PLAIN, PLAIN, PLAIN), CONFIG)


def test_the_count_test_fails_when_the_count_is_far_from_the_real_size() -> None:
    count_test = (
        live.test_a_token_count_is_as_close_to_the_real_size_as_the_profile_says
    )
    far_off = ScriptedReply(parts=(ANSWER,), reason="end", usage=Usage(9_000, 0, 0, 8))
    with pytest.raises(AssertionError):
        count_test(scripted(PLAIN, far_off, PLAIN), CONFIG)


def test_a_live_test_fails_when_the_call_fails() -> None:
    cache_test = live.test_a_second_call_with_the_same_long_prompt_reads_from_the_cache
    broken = ScriptedBreak(parts=(), failure=THROTTLED)
    with pytest.raises(pytest.fail.Exception, match="throttled"):
        cache_test(scripted(broken, WARM), CONFIG)
