"""The capture set's pure steps on made-up runs: rebuild, pass over, cap, fit, order."""

import dataclasses

import pytest

from lamplighter.app.capture_set import (
    MOST_FROM_ONE_RUN,
    SET_SIZE,
    Captured,
    CapturedRun,
    Rebuild,
    Source,
    capture_line,
    chosen,
    first_pass,
    read_capture,
    request_hash,
    run_requests,
    set_hash,
)
from lamplighter.core import rebuild_messages
from lamplighter.ports import Failure, Message, ModelRequest, TextPart, ToolSpec
from tests.run_history import History, call

TOOL = ToolSpec(
    name="wait",
    description="Wait a number of seconds.",
    arguments_schema={"type": "object", "properties": {"seconds": {"type": "number"}}},
)
REBUILD = Rebuild(
    system="You are a made-up helper.",
    tools=(TOOL,),
    max_output=16_000,
    deadline_ms=600_000,
    retries=2,
)
HELD_OUT_TASK = "Count the   made-up lines\nof the file kept.txt and write the total."


def made_up(text: str, source: Source = "chat") -> Captured:
    request = ModelRequest(
        system=("A made-up system text.",),
        messages=(Message(role="user", parts=(TextPart(text=text),)),),
        tools=(TOOL,),
        max_output=16_000,
        cache_hints=(),
        deadline_ms=600_000,
        retries=2,
    )
    return Captured(request, False, source)


def a_run(*texts: str, source: Source = "chat") -> CapturedRun:
    return CapturedRun(source, tuple(made_up(t, source) for t in texts))


def texts_of(requests: tuple[Captured, ...]) -> list[str]:
    found: list[str] = []
    for captured in requests:
        part = captured.request.messages[0].parts[0]
        assert isinstance(part, TextPart)
        found.append(part.text)
    return found


# Rebuilding a run's requests.


def test_each_request_is_rebuilt_from_the_events_before_it() -> None:
    history = History().start("Wait a second, please.")
    first = call("call_a", seconds=1)
    history.chose()
    history.step_with(first)
    history.ran(first)
    history.answered("Waited.")
    run = run_requests(history.events, "run-1", REBUILD, "chat")
    assert not isinstance(run, Failure), run
    assert [c.recorded_call for c in run.requests] == [True, False]
    asked = [e.seq for e in history.events if e.type == "model_request"]
    for captured, seq in zip(run.requests, asked, strict=True):
        before = rebuild_messages(history.events[: seq - 1])
        assert not isinstance(before, Failure)
        assert captured.request == ModelRequest(
            system=(REBUILD.system,),
            messages=before,
            tools=(TOOL,),
            max_output=16_000,
            cache_hints=(),
            deadline_ms=600_000,
            retries=2,
        )
    assert len(run.requests[1].request.messages) == 3


def test_a_request_with_no_saved_reply_is_left_out() -> None:
    history = History().start()
    history.chose()
    history.request()
    # Killed before the reply was saved, and asked again.
    history.answered()
    run = run_requests(history.events, "run-1", REBUILD, "chat")
    assert not isinstance(run, Failure)
    assert len(run.requests) == 1


def test_only_the_named_run_is_rebuilt_with_the_session_before_it() -> None:
    history = History().start("First.")
    history.chose()
    history.answered("One.")
    history.start("Second.", "m2", run_id="run-2")
    history.chose()
    history.answered("Two.")
    run = run_requests(history.events, "run-2", REBUILD, "trial")
    assert not isinstance(run, Failure)
    (only,) = run.requests
    assert only.source == "trial"
    texts = [
        p.text
        for m in only.request.messages
        for p in m.parts
        if isinstance(p, TextPart)
    ]
    assert texts == ["First.", "One.", "Second."]


def test_an_event_that_cannot_be_read_stops_the_rebuild() -> None:
    history = History().start()
    history.chose()
    history.add("user_message", {"words": "not a text"})
    history.answered()
    assert isinstance(run_requests(history.events, "run-1", REBUILD, "chat"), Failure)


# Steps 4 to 6.


def test_a_chat_request_holding_a_held_out_task_is_passed_over() -> None:
    pasted = (
        "Please:  Count the made-up lines of the file\nkept.txt and write the total."
    )
    runs = [
        a_run("Hello.", pasted),
        a_run("Count the made-up\tlines", pasted + " ", source="trial"),
    ]
    found = first_pass(runs, [HELD_OUT_TASK])
    assert found.held_out == 1
    assert [texts_of(r.requests) for r in found.runs] == [
        ["Hello."],
        ["Count the made-up\tlines", pasted + " "],
    ]


def test_the_held_out_text_is_compared_as_it_is_written() -> None:
    lower = "count the made-up lines of the file kept.txt and write the total."
    assert first_pass([a_run(lower)], [HELD_OUT_TASK]).held_out == 0


def test_an_empty_instruction_passes_nothing_over() -> None:
    found = first_pass([a_run("Hello.")], ["  \n"])
    assert (found.held_out, len(found.runs)) == (0, 1)


def test_an_exact_repeat_is_removed_wherever_it_is() -> None:
    runs = [a_run("One.", "Two.", "One."), a_run("Two.", "Three.")]
    found = first_pass(runs, [])
    assert found.repeats == 2
    assert [texts_of(r.requests) for r in found.runs] == [["One.", "Two."], ["Three."]]


def test_a_run_keeps_its_first_ten() -> None:
    texts = [f"Request {n}." for n in range(13)]
    found = first_pass([a_run(*texts), a_run("Other.")], [])
    assert found.over_the_cap == 3
    assert texts_of(found.runs[0].requests) == texts[:MOST_FROM_ONE_RUN]


def test_a_run_left_with_nothing_is_dropped() -> None:
    found = first_pass([a_run("One."), a_run("One.")], [])
    assert len(found.runs) == 1


# Steps 7 and 8.


def counted(runs: list[CapturedRun], size: int = 1_000) -> dict[str, list[int]]:
    return {request_hash(c.request): [size, size] for run in runs for c in run.requests}


def test_a_request_too_large_for_any_counting_model_is_left_out() -> None:
    runs = [a_run("Fits.", "Too large.", "Just fits.")]
    counts = counted(runs)
    counts[request_hash(runs[0].requests[1].request)] = [1_000, 49_537]
    counts[request_hash(runs[0].requests[2].request)] = [49_536, 1_000]
    found = chosen(runs, counts, 65_536, 16_000)
    assert found.too_large == 1
    assert texts_of(found.requests) == ["Fits.", "Just fits."]


def test_runs_go_by_the_hash_of_their_first_request_and_stay_together() -> None:
    runs = [a_run(f"Run {n} first.", f"Run {n} second.") for n in range(6)]
    found = chosen(runs, counted(runs), 65_536, 16_000)
    firsts = sorted(runs, key=lambda r: request_hash(r.requests[0].request))
    assert texts_of(found.requests) == [
        text for run in firsts for text in texts_of(run.requests)
    ]


def test_the_first_request_that_fits_orders_its_run() -> None:
    runs = [a_run(f"Dropped {n}.", f"Kept {n}.") for n in range(6)]
    counts = counted(runs)
    for run in runs:
        counts[request_hash(run.requests[0].request)] = [65_536, 0]
    found = chosen(runs, counts, 65_536, 16_000)
    kept = sorted(runs, key=lambda r: request_hash(r.requests[1].request))
    assert texts_of(found.requests) == [texts_of(r.requests)[1] for r in kept]


def test_the_set_stops_at_its_size() -> None:
    runs = [a_run(*(f"Run {n} request {m}." for m in range(9))) for n in range(30)]
    found = chosen(runs, counted(runs), 65_536, 16_000)
    assert len(found.requests) == SET_SIZE


@pytest.mark.parametrize("source", ["chat", "trial"])
def test_a_line_is_read_back_as_it_was_written(source: Source) -> None:
    made = dataclasses.replace(made_up("Hello. 1.5", source), recorded_call=True)
    line = capture_line(made)
    assert "\n" not in line
    assert read_capture(line + "\n" + line + "\n") == (made, made)


def test_the_same_runs_give_the_same_set_and_hash() -> None:
    def build() -> str:
        runs = [a_run(f"Run {n}.", f"Run {n} again.") for n in range(4)]
        kept = first_pass(runs, [HELD_OUT_TASK]).runs
        found = chosen(kept, counted(list(kept)), 65_536, 16_000)
        return "".join(capture_line(c) + "\n" for c in found.requests)

    assert set_hash(build()) == set_hash(build())
    assert set_hash("") == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
