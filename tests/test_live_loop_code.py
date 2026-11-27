"""Runs the code of the loop's live tests against a made-up model.

The live tests call the real API, so the default run leaves them out. A
mistake in their own code would then first show on a call that costs
money. These tests run that code with made-up replies, on the executor
with no wall, and check that each fails when the model does not do what
it should.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from lamplighter.adapters import ScriptedReply, SystemClock
from lamplighter.app.config import Config, load_config
from lamplighter.ports import Executor, JsonObject, TextPart, ToolCallPart, Usage
from tests.builtin_kit import PAGE_URL
from tests.direct_executor import DirectExecutor
from tests.kill_kit import Turn, answers
from tests.live import test_loop as live
from tests.model_by_request import ModelByRequest
from tests.model_samples import PROFILE

USAGE = Usage(input_uncached=100, cache_read=0, cache_write=0, output=20)


def call(name: str, **arguments: str) -> ScriptedReply:
    given: JsonObject = dict(arguments)
    part = ToolCallPart(id=f"call_{name}", name=name, arguments=given)
    return ScriptedReply((part,), "tool_calls", USAGE)


def says(text: str) -> ScriptedReply:
    return ScriptedReply((TextPart(text=text),), "end", USAGE)


WRITE_BLUE = call("write_file", path="kettle.txt", content="The kettle is blue.\n")
READ = call("read_file", path="kettle.txt")
EDIT = call("edit_file", path="kettle.txt", old_text="blue", new_text="green")
WRITE_NOTE = call("write_file", path="note.txt", content="Water the plants.\n")
RUN = call("shell", command="echo made-up-$((6*7))")
FETCH = call("web_fetch", url=PAGE_URL)
CLOCK = call("current_time")
DONE = says("Done.")


@pytest.fixture
def config() -> Config:
    config = load_config(live.SETTINGS)
    run = dataclasses.replace(config.run, model_max_output=1_000)
    return dataclasses.replace(config, model=PROFILE, run=run)


def no_wall(config: Config) -> Executor:
    return DirectExecutor(Path(config.tools.work_dir), SystemClock())


def model(script: Mapping[str, Sequence[Turn]]) -> ModelByRequest:
    return ModelByRequest(answers(script))


def test_the_file_test_passes_when_the_model_writes_reads_and_edits(
    tmp_path: Path, config: Config
) -> None:
    used = model({live.FILE_ASK: (WRITE_BLUE, READ, EDIT, DONE)})
    live.test_the_model_writes_reads_and_edits_a_file(tmp_path, config, used, no_wall)


@pytest.mark.parametrize(
    "turns",
    [(WRITE_BLUE, READ, DONE), (WRITE_BLUE, DONE), (DONE,)],
    ids=["no edit", "no read", "no tool"],
)
def test_the_file_test_fails_when_a_tool_is_left_out(
    tmp_path: Path, config: Config, turns: tuple[Turn, ...]
) -> None:
    with pytest.raises(AssertionError):
        live.test_the_model_writes_reads_and_edits_a_file(
            tmp_path, config, model({live.FILE_ASK: turns}), no_wall
        )


def test_the_command_test_passes_when_the_model_runs_it_and_says_the_output(
    tmp_path: Path, config: Config
) -> None:
    used = model({live.COMMAND_ASK: (RUN, says("It printed made-up-42."))})
    live.test_the_model_runs_a_command_behind_the_wall_and_says_what_it_printed(
        tmp_path, config, used, no_wall
    )


@pytest.mark.parametrize(
    "turns",
    [(RUN, says("It printed something.")), (says("made-up-42"),)],
    ids=["wrong answer", "no command"],
)
def test_the_command_test_fails_when_the_output_is_not_said_or_not_run(
    tmp_path: Path, config: Config, turns: tuple[Turn, ...]
) -> None:
    with pytest.raises(AssertionError):
        live.test_the_model_runs_a_command_behind_the_wall_and_says_what_it_printed(
            tmp_path, config, model({live.COMMAND_ASK: turns}), no_wall
        )


def test_the_page_test_passes_when_the_model_answers_from_the_page(
    tmp_path: Path, config: Config
) -> None:
    used = model({live.PAGE_ASK: (FETCH, says("The word is periwinkle."))})
    live.test_the_model_answers_from_a_fetched_page_given_as_outside_text(
        tmp_path, config, used, no_wall
    )


@pytest.mark.parametrize(
    "turns",
    [(FETCH, says("I could not tell.")), (says("periwinkle"),)],
    ids=["wrong answer", "no fetch"],
)
def test_the_page_test_fails_when_the_answer_is_not_from_the_page(
    tmp_path: Path, config: Config, turns: tuple[Turn, ...]
) -> None:
    with pytest.raises(AssertionError):
        live.test_the_model_answers_from_a_fetched_page_given_as_outside_text(
            tmp_path, config, model({live.PAGE_ASK: turns}), no_wall
        )


def test_the_settle_test_passes_when_the_model_writes_again(
    tmp_path: Path, config: Config
) -> None:
    used = model({live.NOTE_ASK: (WRITE_NOTE, WRITE_NOTE, DONE)})
    live.test_a_write_cut_off_after_its_intent_is_settled_and_written_again(
        tmp_path, config, used, no_wall
    )


def test_the_settle_test_fails_when_the_model_does_not_write_again(
    tmp_path: Path, config: Config
) -> None:
    with pytest.raises(AssertionError):
        live.test_a_write_cut_off_after_its_intent_is_settled_and_written_again(
            tmp_path, config, model({live.NOTE_ASK: (WRITE_NOTE, DONE)}), no_wall
        )


def test_the_joined_message_test_passes_when_both_texts_are_in_one_run(
    tmp_path: Path, config: Config
) -> None:
    script = {live.TIME_ASK: (CLOCK, DONE), live.MORE_ASK: (DONE,)}
    live.test_a_message_that_joins_a_run_is_accepted_beside_a_tool_result(
        tmp_path, config, model(script), no_wall
    )


def test_the_joined_message_test_fails_when_no_tool_was_called(
    tmp_path: Path, config: Config
) -> None:
    script = {live.TIME_ASK: (DONE,), live.MORE_ASK: (DONE,)}
    with pytest.raises(AssertionError):
        live.test_a_message_that_joins_a_run_is_accepted_beside_a_tool_result(
            tmp_path, config, model(script), no_wall
        )
