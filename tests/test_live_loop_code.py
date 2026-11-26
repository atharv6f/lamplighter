"""Runs the code of the loop's live tests against a made-up model.

The live tests call the real API, so the default run leaves them out. A
mistake in their own code would then first show on a call that costs
money. These tests run that code with made-up replies, and check that
each fails when the model does not do what it should.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from lamplighter.adapters import ScriptedReply
from lamplighter.app.config import Config, load_config
from lamplighter.ports import TextPart, ToolCallPart, Usage
from tests.kill_kit import Turn, answers
from tests.live import test_loop as live
from tests.model_by_request import ModelByRequest
from tests.model_samples import PROFILE

USAGE = Usage(input_uncached=100, cache_read=0, cache_write=0, output=20)
ASKED = "What is the time now in UTC? Use the tool."
CLOCK = ScriptedReply(
    (ToolCallPart(id="call_1", name="current_time", arguments={}),),
    "tool_calls",
    USAGE,
)
ANSWER = ScriptedReply((TextPart(text="It is noon."),), "end", USAGE)


@pytest.fixture
def config() -> Config:
    config = load_config(live.SETTINGS)
    run = dataclasses.replace(config.run, model_max_output=1_000)
    return dataclasses.replace(config, model=PROFILE, run=run)


def model(script: Mapping[str, Sequence[Turn]]) -> ModelByRequest:
    return ModelByRequest(answers(script))


def test_the_whole_run_test_passes_when_the_model_uses_the_clock(
    tmp_path: Path, config: Config
) -> None:
    live.test_a_whole_run_calls_the_clock_tool_and_answers(
        tmp_path, config, model({ASKED: (CLOCK, ANSWER)})
    )


def test_the_whole_run_test_fails_when_the_model_does_not_use_the_clock(
    tmp_path: Path, config: Config
) -> None:
    with pytest.raises(AssertionError):
        live.test_a_whole_run_calls_the_clock_tool_and_answers(
            tmp_path, config, model({ASKED: (ANSWER,)})
        )


def test_the_repair_test_passes_when_the_model_answers_after_the_repair(
    tmp_path: Path, config: Config
) -> None:
    used = model({ASKED: (CLOCK, ANSWER)})
    live.test_a_call_cut_off_by_a_kill_is_answered_interrupted_and_the_api_accepts_it(
        tmp_path, config, used
    )
    assert len(used.requests) == 2


def test_the_repair_test_fails_when_no_tool_was_called(
    tmp_path: Path, config: Config
) -> None:
    with pytest.raises(AssertionError):
        live.test_a_call_cut_off_by_a_kill_is_answered_interrupted_and_the_api_accepts_it(
            tmp_path, config, model({ASKED: (ANSWER,)})
        )


def test_the_joined_message_test_passes_when_both_texts_are_in_one_run(
    tmp_path: Path, config: Config
) -> None:
    script = {ASKED: (CLOCK, ANSWER), "And say it in words, please.": (ANSWER,)}
    live.test_a_message_that_joins_a_run_is_accepted_beside_a_tool_result(
        tmp_path, config, model(script)
    )


def test_the_joined_message_test_fails_when_no_tool_was_called(
    tmp_path: Path, config: Config
) -> None:
    script = {ASKED: (ANSWER,), "And say it in words, please.": (ANSWER,)}
    with pytest.raises(AssertionError):
        live.test_a_message_that_joins_a_run_is_accepted_beside_a_tool_result(
            tmp_path, config, model(script)
        )
