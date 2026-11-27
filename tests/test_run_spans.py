"""The spans one run gives, on runs made up by hand."""

from collections.abc import Callable
from typing import cast

import pytest

from lamplighter.core import span_names as names
from lamplighter.core.run_spans import SPAN_LAYOUT, WholeSpan, model_name, spans_of_run
from lamplighter.ports import (
    Failure,
    JsonValue,
    Span,
    TextPart,
    Usage,
    span_id_for,
    trace_id_for,
)
from tests.span_checks import span_texts, spans_wrong
from tests.span_count import promised_spans
from tests.span_kit import (
    CONFIG,
    START_MS,
    STEP_MS,
    MadeUpLog,
    MadeUpRun,
    plain_run,
    tool_call,
)

# Text the model or a provider could write. A space keeps it from passing
# as a model's name.
MARKER = "secret marker"
FAILED = Failure(kind="timeout", effect="none", message=MARKER, source="t")


def spans(run: MadeUpRun) -> tuple[WholeSpan, ...]:
    return spans_of_run(run.events)


def of_kind(run: MadeUpRun, kind: str) -> list[Span]:
    return [made.span for made in spans(run) if made.span.kind == kind]


def one(run: MadeUpRun, kind: str) -> Span:
    [made] = of_kind(run, kind)
    return made


def at(pos: int) -> int:
    """The time of the event at `pos`, when no event of the log set its own."""
    return START_MS + STEP_MS * pos


def ends(span: Span) -> tuple[int, int]:
    return span.start_ms, span.start_ms + span.duration_ms


def no_marker(run: MadeUpRun) -> None:
    for made in spans(run):
        assert not any(MARKER in said for said in span_texts(made.span)), made.span


# The tree.


def test_the_plain_run_gives_six_spans_each_whole_at_the_event_that_ends_it() -> None:
    whole = spans(plain_run())

    assert [(made.pos, made.span.kind, made.span.name) for made in whole] == [
        (5, "model", "chat made-up-1"),
        (7, "tool", "execute_tool note"),
        (8, "step", "step 1"),
        (9, "model", "chat made-up-1"),
        (11, "step", "step 2"),
        (11, "run", "invoke_agent lamplighter"),
    ]


def test_each_span_hangs_under_the_right_parent_with_ids_from_its_own_events() -> None:
    run = plain_run()
    started, _, first, _, _, result, second, *_ = run.events
    model_1, tool, step_1, model_2, step_2, whole_run = (m.span for m in spans(run))
    trace = trace_id_for(SPAN_LAYOUT, "sess-a", "run-1")

    assert {made.span.trace_id for made in spans(run)} == {trace}
    assert whole_run.span_id == span_id_for(
        SPAN_LAYOUT, "run-1", "run", started.event_id
    )
    assert step_1.span_id == span_id_for(SPAN_LAYOUT, "run-1", "step", first.event_id)
    assert model_1.span_id == span_id_for(SPAN_LAYOUT, "run-1", "model", first.event_id)
    assert tool.span_id == span_id_for(SPAN_LAYOUT, "run-1", "tool", result.event_id)
    assert step_2.span_id == span_id_for(SPAN_LAYOUT, "run-1", "step", second.event_id)
    assert model_2.span_id == span_id_for(
        SPAN_LAYOUT, "run-1", "model", second.event_id
    )
    assert whole_run.parent_id is None
    assert step_1.parent_id == step_2.parent_id == whole_run.span_id
    assert model_1.parent_id == tool.parent_id == step_1.span_id
    assert model_2.parent_id == step_2.span_id


def test_the_plain_run_has_the_times_its_events_give() -> None:
    model_1, tool, step_1, model_2, step_2, whole_run = (
        made.span for made in spans(plain_run())
    )

    assert ends(model_1) == (at(5) - 80, at(5))
    assert ends(tool) == (at(7) - 30, at(7))
    # A step holds its request and its children, and ends at neither of the
    # events that only made it whole.
    assert ends(step_1) == (at(4), at(7))
    assert ends(model_2) == (at(9) - 80, at(9))
    assert ends(step_2) == (at(8), at(9))
    assert ends(whole_run) == (at(2), at(11))


def test_every_span_holds_the_session_the_run_the_config_and_the_layout() -> None:
    run = plain_run()

    for made in spans(run):
        attributes = made.span.attributes
        assert attributes[names.CONVERSATION_ID] == "sess-a"
        assert attributes[names.RUN_ID] == "run-1"
        assert attributes[names.CONFIG_ID] == CONFIG
        assert attributes[names.LAYOUT] == SPAN_LAYOUT


def test_each_span_has_the_seq_of_the_event_its_id_is_made_from() -> None:
    run = plain_run()
    by_type = {
        "run": [e.seq for e in run.events if e.type == "run_started"],
        "step": [e.seq for e in run.events if e.type == "model_request"],
        "model": [e.seq for e in run.events if e.type == "model_request"],
        "tool": [e.seq for e in run.events if e.type == "tool_result"],
    }

    for kind, seqs in by_type.items():
        assert [span.attributes[names.SEQ] for span in of_kind(run, kind)] == seqs


def test_the_plain_run_has_every_attribute_of_the_table_with_its_value() -> None:
    model_1, tool, step_1, _, _, whole_run = (m.span for m in spans(plain_run()))
    common = {
        names.CONVERSATION_ID: "sess-a",
        names.RUN_ID: "run-1",
        names.CONFIG_ID: CONFIG,
        names.LAYOUT: SPAN_LAYOUT,
    }

    assert whole_run.attributes == {
        **common,
        names.SEQ: 2,
        names.OPERATION: "invoke_agent",
        names.AGENT_NAME: "lamplighter",
        names.RUN_TRIGGER: "message",
        names.RUN_END_REASON: "done",
        names.RUN_STEPS: 2,
        names.RUN_TOOL_CALLS: 1,
        names.RUN_INPUT_TOKENS: 300,
        names.RUN_CACHE_READ_TOKENS: 80,
        names.RUN_CACHE_WRITE_TOKENS: 20,
        names.RUN_OUTPUT_TOKENS: 40,
        names.RUN_COST: 2_468,
        names.RUN_CALLS_WITHOUT_COST: 0,
        names.RUN_CALLS_WITHOUT_USAGE: 0,
        names.RUN_CALLS_WITHOUT_REPLY: 0,
    }
    assert step_1.attributes == {
        **common,
        names.SEQ: 4,
        names.OPENINFERENCE_KIND: "CHAIN",
        names.STEP: 1,
    }
    assert model_1.attributes == {
        **common,
        names.SEQ: 4,
        names.OPERATION: "chat",
        names.STEP: 1,
        names.PROVIDER: "made-up",
        names.REQUEST_MODEL: "made-up-1",
        names.RESPONSE_MODEL: "made-up-1-2026",
        names.FINISH_REASONS: ["tool_calls"],
        names.INPUT_TOKENS: 150,
        names.OUTPUT_TOKENS: 20,
        names.CACHE_READ_TOKENS: 40,
        names.CACHE_WRITE_TOKENS: 10,
        names.COST: 1_234,
        names.TOOL_CALLS: 1,
    }
    assert tool.attributes == {
        **common,
        names.SEQ: 7,
        names.OPERATION: "execute_tool",
        names.TOOL_TYPE: "function",
        names.TOOL_NAME: "note",
        names.ACTION_CLASS: "read",
        names.STARTS: 1,
        names.STEP: 1,
        names.RESULT_CHARS: len("Done it."),
    }
    assert [model_1.error, tool.error, step_1.error, whole_run.error] == [None] * 4


# The names, and the rule that keeps a name the model wrote out of a span.

UNVETTED = ("unknown_tool", "cut_off", "arguments_unparsed", "not_run")


def harness_answers() -> MadeUpRun:
    """One step whose calls the harness answered itself, and one more."""
    run = MadeUpLog().run()
    run.start()
    unvetted = [tool_call(f"c{n}", MARKER, n=1) for n, _ in enumerate(UNVETTED)]
    invalid = tool_call("c-invalid", "note", n="one")
    refused = tool_call("c-refused", "change_file", path="/made/up")
    ran = tool_call("c-ran", "read_file", path="/made/up")
    run.request()
    run.reply(*unvetted, invalid, refused, ran, reason="tool_calls")
    for call, word in zip(unvetted, UNVETTED, strict=True):
        run.result(call, text=f"The harness said {word}.", harness=word)
    run.result(invalid, text="Bad arguments.", harness="arguments_invalid")
    run.result(refused, text="The plan was refused.", is_error=True)
    run.ran(ran)
    run.request()
    run.reply(TextPart(text="All done."))
    run.end()
    return run


def test_a_tool_the_harness_did_not_look_up_has_no_name() -> None:
    run = harness_answers()
    tools = of_kind(run, "tool")

    for span, word in zip(tools, UNVETTED, strict=False):
        assert span.name == "execute_tool", word
        assert names.TOOL_NAME not in span.attributes
        assert span.attributes[names.HARNESS_KIND] == word
    no_marker(run)


def test_a_tool_the_harness_looked_up_is_named_from_the_reply_or_its_intent() -> None:
    tools = of_kind(harness_answers(), "tool")

    assert [span.name for span in tools[len(UNVETTED) :]] == [
        "execute_tool note",
        "execute_tool change_file",
        "execute_tool read_file",
    ]
    assert [span.attributes[names.TOOL_NAME] for span in tools[len(UNVETTED) :]] == [
        "note",
        "change_file",
        "read_file",
    ]


def test_the_name_comes_from_the_intent_and_not_from_the_reply() -> None:
    run = MadeUpLog().run()
    run.start()
    run.request()
    run.reply(tool_call("c1", MARKER), reason="tool_calls")
    run.ran(tool_call("c1", "note"))
    run.end()

    assert one(run, "tool").name == "execute_tool note"
    no_marker(run)


# Two calls of one reply with the same id.


def unknown_then_invalid() -> MadeUpRun:
    run = MadeUpLog().run()
    run.start()
    run.request()
    first, second = tool_call("c1", MARKER), tool_call("c1", "note", n="one")
    run.reply(first, second, reason="tool_calls")
    run.result(first, harness="unknown_tool")
    run.result(second, harness="arguments_invalid")
    run.end()
    return run


def unknown_then_refused() -> MadeUpRun:
    run = MadeUpLog().run()
    run.start()
    run.request()
    first, second = tool_call("c1", MARKER), tool_call("c1", "change_file")
    run.reply(first, second, reason="tool_calls")
    run.result(first, harness="unknown_tool")
    run.result(second, text="The plan was refused.", is_error=True)
    run.end()
    return run


def ran_then_invalid() -> MadeUpRun:
    run = MadeUpLog().run()
    run.start()
    run.request()
    first, second = tool_call("c1", "note", n=1), tool_call("c1", MARKER, n="one")
    run.reply(first, second, reason="tool_calls")
    run.ran(first)
    run.result(second, harness="arguments_invalid")
    run.end()
    return run


def invalid_then_ran() -> MadeUpRun:
    run = MadeUpLog().run()
    run.start()
    run.request()
    first, second = tool_call("c1", MARKER, n="one"), tool_call("c1", "note", n=1)
    run.reply(first, second, reason="tool_calls")
    run.result(first, harness="arguments_invalid")
    run.ran(second)
    run.end()
    return run


@pytest.mark.parametrize(
    ("build", "named"),
    [
        (unknown_then_invalid, ["execute_tool", "execute_tool"]),
        (unknown_then_refused, ["execute_tool", "execute_tool"]),
        (ran_then_invalid, ["execute_tool note", "execute_tool"]),
        (invalid_then_ran, ["execute_tool", "execute_tool note"]),
    ],
)
def test_a_result_of_one_of_two_calls_with_one_id_is_named_only_by_its_intent(
    build: Callable[[], MadeUpRun], named: list[str]
) -> None:
    run = build()
    tools = of_kind(run, "tool")

    assert [span.name for span in tools] == named
    assert [span.attributes[names.STARTS] for span in tools] == [
        int(name != "execute_tool") for name in named
    ]
    no_marker(run)


# The model a provider said it used.

LEFT_OUT: list[JsonValue] = [
    f"made-up {MARKER}",
    "made-up-1\x1b[2J",
    "made-up-1\n",
    "m" * 65,
    "",
    7,
]


def reported(name: JsonValue) -> MadeUpRun:
    run = MadeUpLog().run()
    run.start()
    run.request()
    run.reply(TextPart(text="All done."), reported=cast(str, name))
    run.end()
    return run


def test_a_reported_model_with_the_shape_of_a_name_is_kept() -> None:
    model = one(reported("Made-Up_1.5:beta/fast@2026"), "model")

    assert model.attributes[names.RESPONSE_MODEL] == "Made-Up_1.5:beta/fast@2026"
    assert model_name("m" * 64) == "m" * 64


@pytest.mark.parametrize("name", LEFT_OUT)
def test_a_reported_model_without_the_shape_of_a_name_is_left_out(
    name: JsonValue,
) -> None:
    run = reported(name)

    assert names.RESPONSE_MODEL not in one(run, "model").attributes
    assert model_name(name) is None
    no_marker(run)
    for made in spans(run):
        assert not any("\x1b" in said for said in span_texts(made.span))


# The errors.


def failed_tools() -> MadeUpRun:
    run = MadeUpLog().run()
    run.start()
    run.request()
    calls = [tool_call(f"c{n}", "note", n=n) for n in range(4)]
    run.reply(*calls, reason="tool_calls")
    run.intent(calls[0])
    run.result(calls[0], failure_kind="unavailable")
    run.intent(calls[1])
    run.result(calls[1], harness="interrupted")
    run.intent(calls[2])
    run.result(calls[2], is_error=True)
    run.intent(calls[3])
    run.result(calls[3])
    run.request()
    run.reply(TextPart(text="All done."), error=FAILED)
    run.end("failed", "model_error")
    return run


def test_each_failed_call_has_the_word_from_its_own_events() -> None:
    run = failed_tools()
    tools = of_kind(run, "tool")

    assert [span.error for span in tools] == [
        "unavailable",
        "interrupted",
        "tool_error",
        None,
    ]
    assert [span.attributes.get(names.ERROR_TYPE) for span in tools] == [
        "unavailable",
        "interrupted",
        "tool_error",
        None,
    ]
    assert [span.error for span in of_kind(run, "model")] == [None, "timeout"]
    assert [span.error for span in of_kind(run, "step")] == [None, None]
    assert one(run, "run").error == "model_error"
    no_marker(run)


def test_a_tool_failure_wins_over_the_harness_word_and_the_error_mark() -> None:
    run = MadeUpLog().run()
    run.start()
    run.request()
    first, second = tool_call("c1", "note"), tool_call("c2", "note")
    run.reply(first, second, reason="tool_calls")
    run.intent(first)
    run.result(first, failure_kind="denied", harness="interrupted")
    run.intent(second)
    run.result(second, harness="out_of_time", is_error=True)
    run.end()

    assert [span.error for span in of_kind(run, "tool")] == ["denied", "out_of_time"]


@pytest.mark.parametrize(
    ("reason", "detail", "error"),
    [
        ("done", "", None),
        ("cancelled", "", None),
        ("failed", "", "failed"),
        ("failed", "model_error", "model_error"),
        ("limit", "steps", "limit_steps"),
        ("limit", "micro_dollars", "limit_micro_dollars"),
    ],
)
def test_a_run_has_an_error_only_when_it_failed_or_hit_a_limit(
    reason: str, detail: str, error: str | None
) -> None:
    run = MadeUpLog().run()
    run.start()
    run.answered()
    run.end(reason, detail)
    span = one(run, "run")

    assert span.error == error
    assert span.attributes.get(names.ERROR_TYPE) == error
    assert span.attributes[names.RUN_END_REASON] == reason
    assert span.attributes.get(names.RUN_END_DETAIL) == (detail or None)


# The times.


def test_a_reply_with_no_length_starts_at_its_request() -> None:
    run = MadeUpLog().run()
    run.start()
    run.request()
    run.reply(TextPart(text="All done."), duration_ms=None)
    run.end()

    assert ends(one(run, "model")) == (at(4), at(5))


def test_a_result_with_no_length_starts_at_its_last_intent_or_lasts_nothing() -> None:
    run = MadeUpLog().run()
    run.start()
    run.request()
    first, second = tool_call("c1", "note"), tool_call("c2", "note")
    run.reply(first, second, reason="tool_calls")
    run.intent(first)
    run.intent(first)
    run.result(first, duration_ms=None)
    run.result(second, duration_ms=None, harness="cut_off")
    run.end()

    assert [ends(span) for span in of_kind(run, "tool")] == [
        (at(7), at(8)),
        (at(9), at(9)),
    ]


@pytest.mark.parametrize("length", [80, None])
def test_a_clock_set_back_three_seconds_leaves_every_child_inside_its_parent(
    length: int | None,
) -> None:
    log = MadeUpLog()
    run = log.run()
    run.start()
    note = tool_call("c1", "note")
    run.request()
    back = log.now_ms - 3_000
    run.reply(note, reason="tool_calls", duration_ms=length, at_ms=back)
    run.ran(note)
    run.request()
    run.reply(TextPart(text="All done."), duration_ms=length)
    run.end()
    model, _, step, *_ = (made.span for made in spans(run))

    assert spans_wrong(log.events) == []
    assert all(made.span.duration_ms >= 0 for made in spans(run))
    assert step.start_ms == model.start_ms <= START_MS + back


def test_a_run_with_no_step_whose_end_is_stamped_first_lasts_nothing() -> None:
    log = MadeUpLog()
    run = log.run()
    run.start(at_ms=1_000)
    run.end(at_ms=500)

    assert ends(one(run, "run")) == (START_MS + 500, START_MS + 500)


# A call with no reply.


def test_a_request_and_then_the_end_is_a_call_with_no_reply() -> None:
    run = MadeUpLog().run()
    run.start()
    run.request()
    run.end("cancelled")
    whole = spans(run)

    assert [(made.pos, made.span.kind) for made in whole] == [
        (5, "model"),
        (5, "step"),
        (5, "run"),
    ]
    model = whole[0].span
    assert model.error == "no_reply"
    assert ends(model) == (at(4), at(5))
    assert names.TOOL_CALLS not in model.attributes
    assert whole[2].span.attributes[names.RUN_CALLS_WITHOUT_REPLY] == 1
    assert whole[2].span.attributes[names.RUN_STEPS] == 1


def test_a_request_and_then_a_cancel_is_whole_at_the_cancel() -> None:
    run = MadeUpLog().run()
    run.start()
    run.request()
    run.add("cancelled", {"by": "user"})

    assert [(made.pos, made.span.kind, made.span.error) for made in spans(run)] == [
        (5, "model", "no_reply")
    ]


def test_a_request_that_is_the_last_event_is_not_whole_yet() -> None:
    run = MadeUpLog().run()
    run.start()
    run.answered()
    run.ready()
    run.request()

    assert [made.span.kind for made in spans(run)] == ["model", "step"]


# Events that older code wrote.


def test_a_request_with_no_model_gives_a_span_named_chat() -> None:
    run = MadeUpLog().run()
    run.start()
    run.request(model=None, provider=None)
    run.reply(TextPart(text="All done."))
    run.end()
    model = one(run, "model")

    assert model.name == "chat"
    assert names.REQUEST_MODEL not in model.attributes
    assert names.PROVIDER not in model.attributes


def test_a_run_with_no_config_id_has_none_on_its_spans() -> None:
    run = MadeUpLog().run()
    run.start(config_id=None)
    run.answered()
    run.end()

    assert all(names.CONFIG_ID not in m.span.attributes for m in spans(run))


def test_a_step_with_no_request_gives_no_span_and_is_not_counted() -> None:
    run = MadeUpLog().run()
    run.start()
    run.answered()
    lost = tool_call("c9", "note")
    run.step = 7
    run.ran(lost)
    run.step = 1
    run.end()

    assert [made.span.kind for made in spans(run)] == ["model", "step", "run"]
    assert one(run, "run").attributes[names.RUN_STEPS] == 1
    assert one(run, "run").attributes[names.RUN_TOOL_CALLS] == 0
    assert promised_spans(run.log.events) == 3


# The sums.


def three_replies() -> MadeUpRun:
    run = MadeUpLog().run()
    run.start()
    first, second = tool_call("c1", "note"), tool_call("c2", "note")
    run.request()
    run.reply(first, reason="tool_calls", usage=Usage(1_000, 200, 30, 4), cost=500)
    run.ran(first)
    run.request()
    run.reply(second, reason="tool_calls", usage=Usage(70, None, 5, 6), cost=None)
    run.ran(second)
    run.request()
    run.reply(TextPart(text="All done."), usage=None, cost=None)
    run.end()
    return run


def test_a_reply_with_a_count_missing_is_in_no_token_sum() -> None:
    attributes = one(three_replies(), "run").attributes

    assert attributes[names.RUN_INPUT_TOKENS] == 1_230
    assert attributes[names.RUN_CACHE_READ_TOKENS] == 200
    assert attributes[names.RUN_CACHE_WRITE_TOKENS] == 30
    assert attributes[names.RUN_OUTPUT_TOKENS] == 4
    assert attributes[names.RUN_COST] == 500
    assert attributes[names.RUN_CALLS_WITHOUT_COST] == 2
    assert attributes[names.RUN_CALLS_WITHOUT_USAGE] == 2
    assert attributes[names.RUN_CALLS_WITHOUT_REPLY] == 0
    assert attributes[names.RUN_STEPS] == 3
    assert attributes[names.RUN_TOOL_CALLS] == 2


def test_a_model_call_with_a_count_missing_has_no_input_count() -> None:
    first, second, third = (
        span.attributes for span in of_kind(three_replies(), "model")
    )

    assert first[names.INPUT_TOKENS] == 1_230
    assert first[names.CACHE_READ_TOKENS] == 200
    assert first[names.CACHE_WRITE_TOKENS] == 30
    assert names.INPUT_TOKENS not in second
    assert names.CACHE_READ_TOKENS not in second
    assert second[names.CACHE_WRITE_TOKENS] == 5
    assert second[names.OUTPUT_TOKENS] == 6
    assert names.COST not in second
    assert not any(name.startswith("gen_ai.usage.") for name in third)
    assert names.COST not in third


def test_a_run_whose_only_reply_has_no_usage_and_no_cost_has_no_sum() -> None:
    run = MadeUpLog().run()
    run.start()
    run.request()
    run.reply(TextPart(text="All done."), usage=None, cost=None)
    run.end()
    attributes = one(run, "run").attributes

    assert names.RUN_COST not in attributes
    assert not any(
        name in attributes
        for name in (
            names.RUN_INPUT_TOKENS,
            names.RUN_CACHE_READ_TOKENS,
            names.RUN_CACHE_WRITE_TOKENS,
            names.RUN_OUTPUT_TOKENS,
        )
    )
    assert attributes[names.RUN_CALLS_WITHOUT_COST] == 1
    assert attributes[names.RUN_CALLS_WITHOUT_USAGE] == 1


def test_counts_and_a_cost_of_zero_are_summed_as_zero() -> None:
    run = MadeUpLog().run()
    run.start()
    run.request()
    run.reply(TextPart(text="All done."), usage=Usage(0, 0, 0, 0), cost=0)
    run.end()
    attributes = one(run, "run").attributes
    model = one(run, "model").attributes

    assert attributes[names.RUN_COST] == 0
    assert attributes[names.RUN_INPUT_TOKENS] == 0
    assert attributes[names.RUN_OUTPUT_TOKENS] == 0
    assert attributes[names.RUN_CALLS_WITHOUT_COST] == 0
    assert model[names.INPUT_TOKENS] == 0
    assert model[names.COST] == 0
    # The cache counts are left out at 0, as the conventions ask.
    assert names.CACHE_READ_TOKENS not in model
    assert names.CACHE_WRITE_TOKENS not in model


def test_a_model_call_counts_the_calls_of_its_reply_and_not_the_results() -> None:
    run = MadeUpLog().run()
    run.start()
    run.request()
    first, second = tool_call("c1", "note"), tool_call("c2", "note")
    run.reply(first, second, reason="tool_calls")
    run.ran(first)
    run.end("cancelled")

    assert one(run, "model").attributes[names.TOOL_CALLS] == 2
    assert one(run, "run").attributes[names.RUN_TOOL_CALLS] == 1


# What is never in a span.


def everything() -> MadeUpRun:
    """A run with outside text in every place a payload can hold it."""
    run = MadeUpLog().run()
    run.start(text=f"Please read {MARKER}.")
    read = tool_call("c1", "read_file", path=f"/made/up/{MARKER}")
    change = tool_call("c2", "change_file", path=f"/made/up/{MARKER}", text=MARKER)
    unknown = tool_call("c3", MARKER)
    run.request()
    run.reply(
        TextPart(text=f"I will read {MARKER}."),
        read,
        change,
        unknown,
        reason="tool_calls",
        reported=f"made-up {MARKER}",
    )
    run.intent(read, target=f"/made/up/{MARKER}")
    run.result(
        read,
        text=f"The file says {MARKER}.",
        tainted=True,
        full_output={"sha256": "a" * 64, "size": 90_000},
    )
    run.intent(change, action_class="change", effect_id=3, target=f"/made/{MARKER}")
    run.result(change, text=f"Could not: {MARKER}.", failure_kind="conflict")
    run.result(unknown, text=f"No tool named {MARKER}.", harness="unknown_tool")
    run.request()
    run.reply(TextPart(text=MARKER), error=FAILED, reported=MARKER)
    run.ready(f"It says {MARKER}.", "failed", "model_error")
    run.end("failed", "model_error")
    return run


def test_no_outside_text_reaches_a_span() -> None:
    run = everything()

    assert len(spans(run)) == 8
    no_marker(run)
    assert spans_wrong(run.log.events) == []


TABLE = [
    "error.type",
    "gen_ai.agent.name",
    "gen_ai.conversation.id",
    "gen_ai.operation.name",
    "gen_ai.provider.name",
    "gen_ai.request.model",
    "gen_ai.response.finish_reasons",
    "gen_ai.response.model",
    "gen_ai.tool.name",
    "gen_ai.tool.type",
    "gen_ai.usage.cache_read.input_tokens",
    "gen_ai.usage.cache_write.input_tokens",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "lamp.action_class",
    "lamp.config_id",
    "lamp.cost_micro_dollars",
    "lamp.effect_id",
    "lamp.full_output_bytes",
    "lamp.full_output_sha256",
    "lamp.harness_kind",
    "lamp.layout",
    "lamp.outside_text",
    "lamp.result_chars",
    "lamp.run.cache_read_tokens",
    "lamp.run.cache_write_tokens",
    "lamp.run.calls_without_cost",
    "lamp.run.calls_without_reply",
    "lamp.run.calls_without_usage",
    "lamp.run.cost_micro_dollars",
    "lamp.run.end_detail",
    "lamp.run.end_reason",
    "lamp.run.input_tokens",
    "lamp.run.output_tokens",
    "lamp.run.steps",
    "lamp.run.tool_calls",
    "lamp.run.trigger",
    "lamp.run_id",
    "lamp.seq",
    "lamp.starts",
    "lamp.step",
    "lamp.tool_calls",
    "openinference.span.kind",
]


def test_the_names_file_holds_exactly_the_names_of_the_table() -> None:
    held = [
        value
        for key, value in vars(names).items()
        if key.isupper() and isinstance(value, str) and "." in value
    ]

    assert sorted(held) == TABLE


def test_the_spans_use_every_name_of_the_table_and_no_other() -> None:
    written = {
        name
        for build in LOGS.values()
        for made in spans(build())
        for name in made.span.attributes
    }

    assert sorted(written) == TABLE


# The rules every run keeps.

LOGS: dict[str, Callable[[], MadeUpRun]] = {
    "plain": plain_run,
    "harness answers": harness_answers,
    "unknown then invalid": unknown_then_invalid,
    "unknown then refused": unknown_then_refused,
    "ran then invalid": ran_then_invalid,
    "invalid then ran": invalid_then_ran,
    "failed tools": failed_tools,
    "three replies": three_replies,
    "everything": everything,
    **{f"reported {name!r}": (lambda n=name: reported(n)) for name in LEFT_OUT},
}


@pytest.mark.parametrize("build", LOGS.values(), ids=LOGS.keys())
def test_the_same_events_give_the_same_spans(build: Callable[[], MadeUpRun]) -> None:
    assert spans(build()) == spans(build())


@pytest.mark.parametrize("build", LOGS.values(), ids=LOGS.keys())
def test_a_span_whole_in_a_prefix_is_the_same_span_in_the_whole_list(
    build: Callable[[], MadeUpRun],
) -> None:
    events = build().events
    full = spans_of_run(events)

    for size in range(len(events) + 1):
        last = events[size - 1].pos if size else 0
        assert spans_of_run(events[:size]) == tuple(
            made for made in full if made.pos <= last
        ), size


@pytest.mark.parametrize("build", LOGS.values(), ids=LOGS.keys())
def test_the_count_from_the_log_is_the_number_of_spans(
    build: Callable[[], MadeUpRun],
) -> None:
    run = build()

    assert promised_spans(run.log.events) == len(spans(run))
    assert spans_wrong(run.log.events) == []


def test_a_list_with_no_run_started_gives_nothing() -> None:
    events = plain_run().events

    assert spans_of_run([e for e in events if e.type != "run_started"]) == ()
    assert spans_of_run([]) == ()


def test_two_sessions_with_one_run_id_give_two_traces() -> None:
    log = MadeUpLog()
    plain_run(log)
    other = log.run(session="sess-b")
    other.start()
    other.answered()
    other.end()

    first, second = spans(log.run()), spans(other)
    assert promised_spans(log.events) == len(first) + len(second) == 9
    assert {m.span.trace_id for m in first}.isdisjoint(m.span.trace_id for m in second)


def test_a_request_is_not_whole_while_only_another_session_goes_on() -> None:
    log = MadeUpLog()
    waiting = log.run()
    waiting.start()
    waiting.request()
    other = log.run(session="sess-b")
    other.start()
    other.answered()
    other.end()

    assert spans(waiting) == ()
    assert promised_spans(log.events) == len(spans(other)) == 3
