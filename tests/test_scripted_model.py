import dataclasses
import random

from lamplighter.adapters import ScriptedModel, ScriptedReply
from lamplighter.core import fold_reply
from lamplighter.ports import (
    CacheHint,
    ErrorEvent,
    StopEvent,
    TextEvent,
    TextPart,
    UsageEvent,
)
from tests.model_samples import PROFILE, REQUEST, USAGE
from tests.streams import collect, count

LONG_TEXT = "The quick brown fox jumps over the lazy dog, twice, and then goes home."
REPLY = ScriptedReply(parts=(TextPart(text=LONG_TEXT),), reason="end")


def _pieces(seed: int) -> list[str]:
    model = ScriptedModel(PROFILE, [REPLY], random.Random(seed))
    return [e.text for e in collect(model, REQUEST) if isinstance(e, TextEvent)]


def test_text_is_cut_differently_from_run_to_run() -> None:
    assert _pieces(1) != _pieces(2)


def test_the_text_is_the_same_however_it_is_cut() -> None:
    assert "".join(_pieces(1)) == LONG_TEXT
    assert "".join(_pieces(2)) == LONG_TEXT


def test_the_last_event_names_the_model_and_keeps_the_reason_as_its_own_word() -> None:
    cut = ScriptedReply(parts=(TextPart(text="The quick"),), reason="max_output")
    end = collect(ScriptedModel(PROFILE, [cut], random.Random(0)), REQUEST)[-1]
    assert end == StopEvent(
        reason="max_output",
        provider_reason="max_output",
        model_reported=PROFILE.model,
        skipped=0,
    )


def test_a_call_past_the_end_of_the_script_is_fatal() -> None:
    model = ScriptedModel(PROFILE, [REPLY], random.Random(0))
    collect(model, REQUEST)
    events = collect(model, REQUEST)
    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)
    assert events[0].failure.kind == "fatal"


def test_tools_are_refused_when_the_profile_takes_none() -> None:
    no_tools = dataclasses.replace(PROFILE, tool_calls=False)
    events = collect(ScriptedModel(no_tools, [REPLY], random.Random(0)), REQUEST)
    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)
    assert events[0].failure.kind == "invalid_input"
    assert events[0].failure.source == "ModelClient/scripted"


def test_cache_hints_are_ignored_when_the_profile_honours_none() -> None:
    wild = dataclasses.replace(REQUEST, cache_hints=(CacheHint("message", 99),))
    events = collect(ScriptedModel(PROFILE, [REPLY], random.Random(0)), wild)
    assert isinstance(events[-1], StopEvent)


def test_the_count_matches_the_prompt_size_the_stream_reports() -> None:
    model = ScriptedModel(PROFILE, [REPLY], random.Random(0))
    usage = fold_reply(collect(model, REQUEST)).usage
    assert usage is not None
    assert usage.input_uncached == count(model, REQUEST)
    assert (usage.cache_read, usage.cache_write) == (0, 0)


def test_usage_written_in_the_script_is_reported_as_written() -> None:
    reply = dataclasses.replace(REPLY, usage=USAGE)
    events = collect(ScriptedModel(PROFILE, [reply], random.Random(0)), REQUEST)
    assert UsageEvent(usage=USAGE) in events
