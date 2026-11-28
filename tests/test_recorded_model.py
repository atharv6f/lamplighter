import asyncio
import dataclasses
import json
import random
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from lamplighter.adapters import (
    RecordingModel,
    ReplayedModel,
    ScriptedBreak,
    ScriptedModel,
    ScriptedReply,
)
from lamplighter.ports import (
    ErrorEvent,
    Failure,
    ModelEvent,
    ModelProfile,
    ModelRequest,
    TextEvent,
    TextPart,
)
from tests.model_samples import (
    EVENTS,
    PROFILE,
    REASONING,
    REQUEST,
    THROTTLED,
    TOOL_CALL,
    USAGE,
)
from tests.streams import collect, collect_first, count

REPLY = ScriptedReply(
    parts=(
        REASONING,
        TextPart(text="The kettle is on, and so is the lamp."),
        TOOL_CALL,
    ),
    reason="tool_calls",
    usage=USAGE,
)
BREAK = ScriptedBreak(parts=(TextPart(text="The kettle"),), failure=THROTTLED)
OTHER_REQUEST = dataclasses.replace(REQUEST, max_output=REQUEST.max_output - 1)


class OneShot:
    """A client that gives the same events for every call and counts by a fixed rule."""

    def __init__(self, events: tuple[ModelEvent, ...], size: int | Failure) -> None:
        self._events = events
        self._size = size
        self.closed = False

    @property
    def profile(self) -> ModelProfile:
        return PROFILE

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelEvent]:
        try:
            for event in self._events:
                yield event
        finally:
            self.closed = True

    async def count_tokens(self, request: ModelRequest) -> int | Failure:
        return self._size


def scripted(*turns: ScriptedReply | ScriptedBreak) -> ScriptedModel:
    return ScriptedModel(PROFILE, turns, random.Random(7))


def the_error(events: list[ModelEvent]) -> Failure:
    assert len(events) == 1
    end = events[0]
    assert isinstance(end, ErrorEvent)
    assert (end.model_reported, end.skipped) == (None, 0)
    return end.failure


def test_a_replay_gives_the_events_the_recording_gave(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    recorder = RecordingModel(scripted(REPLY, BREAK), path)
    recorded = [collect(recorder, REQUEST), collect(recorder, OTHER_REQUEST)]
    assert len(recorded[0]) > 4
    assert isinstance(recorded[1][-1], ErrorEvent)

    replay = ReplayedModel(path)
    assert [collect(replay, REQUEST), collect(replay, OTHER_REQUEST)] == recorded


def test_every_kind_of_event_survives_a_recording(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    given = collect(RecordingModel(OneShot(EVENTS, 0), path), REQUEST)
    assert given == list(EVENTS)
    assert collect(ReplayedModel(path), REQUEST) == list(EVENTS)


def test_a_recording_passes_the_call_through_unchanged(tmp_path: Path) -> None:
    recorder = RecordingModel(scripted(REPLY), tmp_path / "calls.jsonl")
    assert collect(recorder, REQUEST) == collect(scripted(REPLY), REQUEST)
    assert recorder.profile == PROFILE


def test_a_replay_reports_the_profile_that_was_recorded(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    RecordingModel(scripted(), path)
    assert ReplayedModel(path).profile == PROFILE


@pytest.mark.parametrize("size", [0, 1_234, THROTTLED])
def test_a_count_is_saved_and_played_back(tmp_path: Path, size: int | Failure) -> None:
    path = tmp_path / "calls.jsonl"
    assert count(RecordingModel(OneShot((), size), path), REQUEST) == size
    assert count(ReplayedModel(path), REQUEST) == size


def test_a_request_that_differs_from_the_saved_one_is_fatal(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    collect(RecordingModel(scripted(REPLY), path), REQUEST)
    failure = the_error(collect(ReplayedModel(path), OTHER_REQUEST))
    assert (failure.kind, failure.effect) == ("fatal", "none")
    assert failure.source == "ModelClient/replay"


def test_a_count_for_a_request_that_differs_is_fatal(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    count(RecordingModel(scripted(), path), REQUEST)
    answer = count(ReplayedModel(path), OTHER_REQUEST)
    assert isinstance(answer, Failure)
    assert answer.kind == "fatal"


def test_a_call_past_the_end_of_the_recording_is_fatal(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    recorder = RecordingModel(scripted(REPLY), path)
    collect(recorder, REQUEST)
    replay = ReplayedModel(path)
    collect(replay, REQUEST)
    assert the_error(collect(replay, REQUEST)).kind == "fatal"
    answer = count(replay, REQUEST)
    assert isinstance(answer, Failure)
    assert answer.kind == "fatal"


def test_a_reply_is_not_given_where_a_count_was_saved(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    count(RecordingModel(scripted(), path), REQUEST)
    assert the_error(collect(ReplayedModel(path), REQUEST)).kind == "fatal"


def test_a_count_is_not_given_where_a_reply_was_saved(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    collect(RecordingModel(scripted(REPLY), path), REQUEST)
    answer = count(ReplayedModel(path), REQUEST)
    assert isinstance(answer, Failure)
    assert answer.kind == "fatal"


def test_calls_are_played_back_in_the_order_they_were_saved(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    recorder = RecordingModel(scripted(REPLY, REPLY), path)
    collect(recorder, REQUEST)
    collect(recorder, OTHER_REQUEST)
    replay = ReplayedModel(path)
    # The second saved call is asked for first, so it is not the next one.
    assert the_error(collect(replay, OTHER_REQUEST)).kind == "fatal"


def test_a_tuple_in_a_request_matches_the_list_it_was_saved_as(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    schema = {"type": "object", "required": ("path",)}
    tool = dataclasses.replace(REQUEST.tools[0], arguments_schema=schema)
    asked = dataclasses.replace(REQUEST, tools=(tool,))
    recorded = collect(RecordingModel(scripted(REPLY), path), asked)
    assert collect(ReplayedModel(path), asked) == recorded


def test_a_call_closed_early_is_saved_as_far_as_it_was_read(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    given = collect_first(RecordingModel(OneShot(EVENTS, 0), path), REQUEST, count=2)
    assert given == list(EVENTS[:2])
    assert collect(ReplayedModel(path), REQUEST) == given


def test_closing_a_recorded_call_closes_the_wrapped_one_before_it_returns(
    tmp_path: Path,
) -> None:
    inner = OneShot(EVENTS, 0)
    recorder = RecordingModel(inner, tmp_path / "calls.jsonl")

    async def read_one_then_close() -> bool:
        stream = recorder.stream(REQUEST)
        await anext(stream)
        await stream.aclose()
        # Read here, before the event loop gets a chance to tidy up by itself.
        return inner.closed

    assert asyncio.run(read_one_then_close())


def test_each_call_is_written_as_it_ends_one_line_for_each(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    recorder = RecordingModel(scripted(REPLY, REPLY), path)
    assert len(path.read_text().splitlines()) == 1
    collect(recorder, REQUEST)
    assert len(path.read_text().splitlines()) == 2
    count(recorder, REQUEST)
    lines = path.read_text().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["v"] == 2
    assert [json.loads(line)["kind"] for line in lines[1:]] == ["stream", "count"]


def test_a_new_recording_replaces_the_file_that_was_there(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    collect(RecordingModel(scripted(REPLY), path), REQUEST)
    RecordingModel(scripted(), path)
    assert the_error(collect(ReplayedModel(path), REQUEST)).kind == "fatal"


def test_a_file_of_another_version_is_not_read(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    RecordingModel(scripted(), path)
    header = json.loads(path.read_text())
    header["v"] = 3
    path.write_text(json.dumps(header) + "\n")
    with pytest.raises(ValueError, match="version 3"):
        ReplayedModel(path)


def test_a_recording_from_before_the_profile_had_a_window_is_not_read(
    tmp_path: Path,
) -> None:
    # The first version saved a profile with six fields. Its profile cannot
    # be read now, and the message names the version, not a missing field.
    path = tmp_path / "calls.jsonl"
    RecordingModel(scripted(), path)
    header = json.loads(path.read_text())
    header["v"] = 1
    for name in ("window", "concurrent_requests", "stays_on_mac"):
        del header["profile"][name]
    path.write_text(json.dumps(header) + "\n")
    with pytest.raises(ValueError, match="version 1, not 2"):
        ReplayedModel(path)


def test_a_file_that_is_not_a_recording_is_not_read(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    path.write_text('{"made": "up"}\n')
    with pytest.raises(ValueError):
        ReplayedModel(path)


def test_a_replayed_text_piece_is_the_piece_that_was_recorded(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    recorded = collect(RecordingModel(scripted(REPLY), path), REQUEST)
    pieces = [e.text for e in recorded if isinstance(e, TextEvent)]
    assert len(pieces) > 1
    replayed = collect(ReplayedModel(path), REQUEST)
    assert [e.text for e in replayed if isinstance(e, TextEvent)] == pieces


@pytest.mark.parametrize(
    "line", ['{"kind": "other"}', "[1, 2]", '{"request": {}}', '{"kind": "stream", "re']
)
def test_a_line_that_is_not_a_saved_call_is_refused(tmp_path: Path, line: str) -> None:
    path = tmp_path / "calls.jsonl"
    collect(RecordingModel(scripted(REPLY), path), REQUEST)
    with path.open("a") as file:
        file.write(line + "\n")
    with pytest.raises(ValueError):
        ReplayedModel(path)
