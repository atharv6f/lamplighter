"""The move table and "what a reply leads to", one row at a time, as tables."""

import dataclasses
from collections.abc import Mapping
from typing import Any

import pytest

from lamplighter.core.harness_texts import (
    CONFIG_CHANGED_NOTICE,
    EMPTY_REPLY_NOTICE,
    REFUSED_NOTICE,
    STOPPED_NOTICE,
    limit_notice,
    model_failed_notice,
    repeated_call_notice,
)
from lamplighter.core.limits import LimitHit
from lamplighter.core.next_move import (
    AnswerCall,
    CallModel,
    CloseRun,
    DropStops,
    EndRun,
    FinishRouting,
    Idle,
    Move,
    RunCall,
    SettleEffect,
    StartRun,
    TakeMessages,
    next_move,
)
from lamplighter.core.run_spec import RunSpec
from lamplighter.core.run_view import RunView, view_of
from lamplighter.ports import (
    Failure,
    Inbound,
    InboxEntry,
    TextPart,
    ToolEntry,
    Usage,
)
from tests.model_samples import REASONING
from tests.run_history import (
    SPEC,
    TOOLS,
    History,
    call,
    inbound,
    row,
    stop,
    unparsed,
)

TIMEOUT = Failure(kind="timeout", effect="none", message="Too slow.", source="m")
BROKEN = Failure(kind="transient", effect="none", message="Cut.", source="m")
DENIED = Failure(kind="denied", effect="none", message="Refused key.", source="m")


def view(history: History) -> RunView:
    made = view_of(history.events)
    assert isinstance(made, RunView), made
    return made


def move(
    history: History,
    rows: tuple[InboxEntry, ...] = (),
    spec: RunSpec = SPEC,
    entries: Mapping[str, ToolEntry] = TOOLS,
) -> Move:
    return next_move(view(history), rows, spec, entries)


def interrupted(answered: Move, call_id: str = "call_a") -> bool:
    return (
        isinstance(answered, AnswerCall)
        and answered.harness == "interrupted"
        and answered.call.call.id == call_id
    )


def read_cut_off(**reply: Any) -> History:
    """A run whose read of `wait` has one intent and no result."""
    history = History().start(config_id=reply.pop("config_id", SPEC.config_id))
    history.request()
    history.reply(call("call_a"), reason="tool_calls", **reply)
    history.intent(call("call_a"), "read")
    return history


def ended(reason: str, detail: str, text: str, limit: LimitHit | None = None) -> EndRun:
    kind = "reply" if reason == "done" else "notice"
    return EndRun(reason, detail, kind, text, limit)  # type: ignore[arg-type]


def opened() -> History:
    """A run that has started and has its message in the log."""
    return History().start()


def first_open(history: History) -> object:
    return view(history).open_calls[0]


# A pending message that is already in the log.


def test_a_message_already_in_the_log_is_routed_to_its_seq() -> None:
    history = opened()
    assert move(history, (row(7, inbound("m1")),)) == FinishRouting(((7, 3),))


def test_routing_comes_before_a_stop_and_before_starting_anything() -> None:
    history = opened()
    history.answered()
    rows = (row(7, stop("s1")), row(8, inbound("m1")), row(9, inbound("m2")))
    assert move(history, rows) == FinishRouting(((8, 3),))


# No open run: idle, drop the stops, or start a run.


def test_with_no_run_and_no_row_there_is_nothing_to_do() -> None:
    assert move(History()) == Idle()
    done = opened()
    done.answered()
    assert move(done) == Idle()


def test_with_no_run_the_stops_at_the_front_are_dropped() -> None:
    rows = (
        row(1, stop("s1")),
        row(2, stop("s2")),
        row(3, inbound("m3")),
        row(4, stop("s4")),
    )
    assert move(History(), rows) == DropStops(rows[:2], new_session=True)
    done = opened()
    done.answered()
    assert move(done, rows) == DropStops(rows[:2], new_session=False)


def test_with_no_run_the_messages_at_the_front_start_one() -> None:
    rows = (
        row(1, inbound("n1")),
        row(2, inbound("n2")),
        row(3, stop("s3")),
        row(4, inbound("n4")),
    )
    assert move(History(), rows) == StartRun(rows[:2], new_session=True)
    done = opened()
    done.answered()
    assert move(done, rows) == StartRun(rows[:2], new_session=False)


# A run with an ending is closed.


def test_a_saved_ending_closes_the_run_and_uses_up_every_pending_stop() -> None:
    history = opened()
    history.request()
    history.reply(TextPart(text="Hi."))
    history.ready("Hi.")
    rows = (row(1, stop("s1")), row(2, inbound("m2")), row(3, stop("s3")))
    closed = move(history, rows)
    assert isinstance(closed, CloseRun)
    assert closed.ending.text == "Hi."
    assert closed.ending.seq == 6
    assert closed.stops == (rows[0], rows[2])


# A call that was in flight when a process died.


def test_a_call_with_an_intent_and_no_result_is_answered_interrupted() -> None:
    history = opened()
    history.step_with(call("call_a"), call("call_b", seconds=2))
    history.intent(call("call_a"))
    answered = move(history)
    assert isinstance(answered, AnswerCall)
    assert answered.harness == "interrupted"
    assert answered.call.call.id == "call_a"


def test_a_read_with_one_intent_is_run_once_more() -> None:
    history = read_cut_off()
    again = move(history)
    assert isinstance(again, RunCall)
    assert again.call.call.id == "call_a"
    assert [i.action_class for i in again.call.intents] == ["read"]


def test_a_read_with_two_intents_is_answered_interrupted() -> None:
    history = read_cut_off()
    history.intent(call("call_a"), "read")
    assert interrupted(move(history))


def test_an_intent_with_no_class_is_answered_interrupted() -> None:
    history = opened()
    history.step_with(call("call_a"))
    history.intent(call("call_a"))
    assert interrupted(move(history))


def test_a_read_is_not_run_again_with_a_stop_pending() -> None:
    assert interrupted(move(read_cut_off(), (row(2, stop("s2")),)))


def test_a_read_is_not_run_again_under_changed_settings() -> None:
    assert interrupted(move(read_cut_off(config_id="d" * 64)))


def test_a_read_is_not_run_again_once_the_time_is_up() -> None:
    assert interrupted(move(read_cut_off(duration_ms=1_200_000)))


def test_a_read_is_not_run_again_when_the_tool_is_now_a_change() -> None:
    changed = {
        **TOOLS,
        "wait": dataclasses.replace(TOOLS["wait"], action_class="irreversible"),
    }
    assert interrupted(move(read_cut_off(), entries=changed))


def test_a_read_is_not_run_again_when_the_tool_is_gone() -> None:
    gone = {name: entry for name, entry in TOOLS.items() if name != "wait"}
    assert interrupted(move(read_cut_off(), entries=gone))


def change_cut_off(**reply: Any) -> History:
    """A run whose change of `wait`, as it was listed then, has an intent."""
    history = History().start(config_id=reply.pop("config_id", SPEC.config_id))
    history.request()
    history.reply(call("call_a"), reason="tool_calls", **reply)
    history.intent(call("call_a"), "irreversible")
    return history


def settled(answered: Move) -> bool:
    return isinstance(answered, SettleEffect) and answered.call.call.id == "call_a"


def test_a_change_with_its_intent_saved_is_settled() -> None:
    assert settled(move(change_cut_off()))


def test_a_change_is_settled_before_a_stop() -> None:
    assert settled(move(change_cut_off(), (row(2, stop("s2")),)))


def test_a_change_is_settled_before_changed_settings_and_the_time_limit() -> None:
    history = change_cut_off(config_id="d" * 64, duration_ms=1_300_000)
    assert settled(move(history))


def test_a_change_is_settled_when_its_tool_is_no_longer_listed() -> None:
    gone = {name: entry for name, entry in TOOLS.items() if name != "wait"}
    assert settled(move(change_cut_off(), entries=gone))


# No call is open, and the newest reply ended the run.


def test_a_whole_text_reply_ends_the_run_done_with_its_visible_text() -> None:
    history = opened()
    history.request()
    history.reply(
        TextPart(text="One."), REASONING, TextPart(text=" "), TextPart(text="Two.")
    )
    assert move(history) == ended("done", "", "One.\n\nTwo.")


@pytest.mark.parametrize("reason", ["max_output", "other"])
def test_a_text_reply_that_was_cut_says_so(reason: str) -> None:
    history = opened()
    history.request()
    history.reply(TextPart(text="Half"), reason=reason)  # type: ignore[arg-type]
    assert move(history) == ended(
        "done", "", "Half\n\n[The reply was cut off before the model finished.]"
    )


def test_a_whole_reply_whose_calls_all_have_results_calls_the_model() -> None:
    history = opened()
    history.step_with(call("call_a"))
    history.ran(call("call_a"))
    assert move(history) == CallModel()


def test_a_refused_reply_ends_the_run() -> None:
    history = opened()
    history.request()
    history.reply(TextPart(text="No."), reason="refused")
    assert move(history) == ended("failed", "refused", REFUSED_NOTICE)


def test_a_first_empty_reply_asks_again_and_a_second_ends_the_run() -> None:
    history = opened()
    history.request()
    history.reply(REASONING)
    assert move(history) == CallModel()
    history.request()
    history.reply(TextPart(text="  "))
    assert move(history) == ended("failed", "empty_reply", EMPTY_REPLY_NOTICE)


def test_a_time_out_at_the_time_limit_is_reported_as_the_limit() -> None:
    history = opened()
    history.request()
    history.reply(error=TIMEOUT, duration_ms=1_200_000)
    hit = LimitHit("seconds", 1_200, 1_200)
    assert move(history) == ended(
        "limit", "seconds", limit_notice("seconds", 1_200), hit
    )


def test_a_time_out_before_the_time_limit_is_a_failure() -> None:
    history = opened()
    history.request()
    history.reply(error=TIMEOUT, duration_ms=600_000)
    notice = model_failed_notice("timeout", "Too slow.")
    assert move(history) == ended("failed", "model_timeout", notice)


@pytest.mark.parametrize("kind", ["transient", "throttled", "timeout", "unavailable"])
def test_a_call_that_broke_after_a_part_arrived_is_asked_once_more(kind: str) -> None:
    history = opened()
    history.request()
    history.reply(TextPart(text="Hal"), error=dataclasses.replace(BROKEN, kind=kind))  # type: ignore[arg-type]
    assert move(history) == CallModel()


def test_a_second_broken_reply_in_a_row_ends_the_run() -> None:
    history = opened()
    history.request()
    history.reply(TextPart(text="Hal"), error=BROKEN)
    history.request()
    history.reply(TextPart(text="Hal"), error=BROKEN)
    notice = model_failed_notice("transient", "Cut.")
    assert move(history) == ended("failed", "model_transient", notice)


def test_a_failed_call_with_no_part_ends_the_run_at_once() -> None:
    history = opened()
    history.request()
    history.reply(error=BROKEN)
    notice = model_failed_notice("transient", "Cut.")
    assert move(history) == ended("failed", "model_transient", notice)


def test_a_failure_a_retry_cannot_fix_ends_the_run_even_after_a_part() -> None:
    history = opened()
    history.request()
    history.reply(TextPart(text="Hal"), error=DENIED)
    notice = model_failed_notice("denied", "Refused key.")
    assert move(history) == ended("failed", "model_denied", notice)


def test_a_broken_reply_then_an_empty_one_ends_with_empty_reply() -> None:
    history = opened()
    history.request()
    history.reply(TextPart(text="Hal"), error=BROKEN)
    history.request()
    history.reply()
    assert move(history) == ended("failed", "empty_reply", EMPTY_REPLY_NOTICE)


def test_a_refused_key_on_the_last_allowed_step_is_the_failure_not_the_limit() -> None:
    history = opened()
    spec = dataclasses.replace(SPEC, max_steps=1)
    history.request()
    history.reply(error=DENIED)
    notice = model_failed_notice("denied", "Refused key.")
    assert move(history, spec=spec) == ended("failed", "model_denied", notice)


def test_a_lost_model_call_is_asked_again() -> None:
    history = opened()
    history.request()
    assert move(history) == CallModel()


# Changed settings, a pending stop, and the time limit with a call open.


def test_a_run_opened_under_other_settings_ends_config_changed() -> None:
    history = History().start(config_id="d" * 64)
    assert move(history) == ended("failed", "config_changed", CONFIG_CHANGED_NOTICE)


def test_a_pending_stop_ends_the_run_cancelled() -> None:
    history = opened()
    history.step_with(call("call_a"))
    assert move(history, (row(2, stop("s2")),)) == ended(
        "cancelled", "", STOPPED_NOTICE
    )


def test_an_open_call_with_the_time_used_up_ends_the_run_on_the_limit() -> None:
    history = opened()
    history.request()
    history.reply(call("call_a"), reason="tool_calls", duration_ms=1_200_000)
    hit = LimitHit("seconds", 1_200, 1_200)
    assert move(history) == ended(
        "limit", "seconds", limit_notice("seconds", 1_200), hit
    )


# The first open call: a repeat, a cut, bad arguments, no such tool, or run it.


def test_an_open_call_made_after_the_warning_ends_the_run() -> None:
    history = opened()
    for _ in range(3):
        history.step_with(call("call_a"))
        history.ran(call("call_a"), "same")
    history.step_with(call("call_a"))
    notice = repeated_call_notice("wait")
    assert move(history) == ended("failed", "repeated_call", notice)


def test_the_last_call_of_a_cut_reply_is_answered_cut_off() -> None:
    history = opened()
    history.step_with(call("call_a"), call("call_b"), reason="max_output")
    first = move(history)
    assert isinstance(first, RunCall)
    assert first.call.call.id == "call_a"
    history.ran(call("call_a"))
    second = move(history)
    assert isinstance(second, AnswerCall)
    assert (second.call.call.id, second.harness) == ("call_b", "cut_off")


def test_a_call_whose_arguments_did_not_parse_is_answered_so() -> None:
    history = opened()
    history.step_with(unparsed())
    answered = move(history)
    assert isinstance(answered, AnswerCall)
    assert answered.harness == "arguments_unparsed"


def test_a_call_to_a_tool_that_does_not_exist_is_answered_so() -> None:
    history = opened()
    history.step_with(call(name="fly"))
    answered = move(history)
    assert isinstance(answered, AnswerCall)
    assert answered.harness == "unknown_tool"


def test_a_call_whose_arguments_do_not_fit_the_schema_is_answered_so() -> None:
    history = opened()
    history.step_with(call(seconds="5"))
    answered = move(history)
    assert isinstance(answered, AnswerCall)
    assert answered.harness == "arguments_invalid"


def test_arguments_that_do_not_fit_a_tool_that_does_not_exist_are_unknown_tool() -> (
    None
):
    history = opened()
    history.step_with(call(name="fly", seconds="5"))
    answered = move(history)
    assert isinstance(answered, AnswerCall)
    assert answered.harness == "unknown_tool"


def test_arguments_that_do_not_fit_with_a_stop_pending_end_the_run() -> None:
    history = opened()
    history.step_with(call(seconds="5"))
    assert move(history, (row(2, stop("s2")),)) == ended(
        "cancelled", "", STOPPED_NOTICE
    )


def test_an_open_call_is_run() -> None:
    history = opened()
    history.step_with(call("call_a"))
    ran = move(history)
    assert isinstance(ran, RunCall)
    assert ran.call.call == call("call_a")


# Nothing open: a limit, the pending messages, or the model.


def test_a_limit_ends_the_run_before_another_model_call() -> None:
    history = opened()
    spec = dataclasses.replace(SPEC, max_steps=1)
    history.step_with(call("call_a"))
    history.ran(call("call_a"))
    hit = LimitHit("steps", 1, 1)
    assert move(history, spec=spec) == ended(
        "limit", "steps", limit_notice("steps", 1), hit
    )


@pytest.mark.parametrize(
    ("usage", "cost", "spec", "hit"),
    [
        (
            Usage(input_uncached=600, cache_read=None, cache_write=0, output=400),
            None,
            dataclasses.replace(SPEC, max_tokens=1_000),
            LimitHit("tokens", 1_000, 1_000),
        ),
        (
            None,
            250_000,
            dataclasses.replace(SPEC, max_micro_dollars=250_000),
            LimitHit("micro_dollars", 250_000, 250_000),
        ),
    ],
    ids=["tokens", "micro_dollars"],
)
def test_the_token_and_money_limits_end_the_run(
    usage: Usage | None, cost: int | None, spec: RunSpec, hit: LimitHit
) -> None:
    history = opened()
    history.request()
    history.reply(call("call_a"), reason="tool_calls", usage=usage, cost=cost)
    history.ran(call("call_a"))
    assert move(history, spec=spec) == ended(
        "limit", hit.limit, limit_notice(hit.limit, hit.allowed), hit
    )


def test_a_waiting_message_joins_the_run_before_the_next_model_call() -> None:
    history = opened()
    history.step_with(call("call_a"))
    history.ran(call("call_a"))
    rows = (row(4, inbound("m4")), row(5, inbound("m5")))
    assert move(history, rows) == TakeMessages(rows)


def test_with_nothing_else_to_do_the_model_is_called() -> None:
    assert move(opened()) == CallModel()


# Pairs of rows whose order matters


def test_an_interrupted_call_is_answered_before_a_stop() -> None:
    history = opened()
    history.step_with(call("call_a"))
    history.intent(call("call_a"))
    answered = move(history, (row(2, stop("s2")),))
    assert isinstance(answered, AnswerCall)
    assert answered.harness == "interrupted"


def test_an_interrupted_call_is_answered_before_changed_settings_and_the_time() -> None:
    history = History().start(config_id="d" * 64)
    history.request()
    history.reply(call("call_a"), reason="tool_calls", duration_ms=1_300_000)
    history.intent(call("call_a"))
    answered = move(history)
    assert isinstance(answered, AnswerCall)
    assert answered.harness == "interrupted"


def test_a_saved_final_reply_wins_over_a_stop_and_changed_settings() -> None:
    history = History().start(config_id="d" * 64)
    history.request()
    history.reply(TextPart(text="Here it is."))
    assert move(history, (row(2, stop("s2")),)) == ended("done", "", "Here it is.")


def test_a_saved_ending_with_a_stop_pending_closes_with_no_second_ending() -> None:
    history = opened()
    history.request()
    history.ready("Stopped.", "cancelled")
    assert isinstance(move(history, (row(2, stop("s2")),)), CloseRun)


def test_a_limit_ends_the_run_and_a_waiting_message_stays_pending() -> None:
    history = opened()
    spec = dataclasses.replace(SPEC, max_steps=1)
    history.step_with(call("call_a"))
    history.ran(call("call_a"))
    ending = move(history, (row(4, inbound("m4")),), spec)
    assert isinstance(ending, EndRun)
    assert ending.reason == "limit"


def test_at_the_step_limit_the_calls_of_the_last_reply_still_run() -> None:
    history = opened()
    spec = dataclasses.replace(SPEC, max_steps=1)
    history.step_with(call("call_a"), call("call_b", seconds=2))
    history.ran(call("call_a"))
    ran = move(history, spec=spec)
    assert isinstance(ran, RunCall)
    assert ran.call.call.id == "call_b"


def test_changed_settings_win_over_a_stop() -> None:
    history = History().start(config_id="d" * 64)
    assert move(history, (row(2, stop("s2")),)) == ended(
        "failed", "config_changed", CONFIG_CHANGED_NOTICE
    )


def test_when_steps_and_tokens_are_both_reached_steps_is_named() -> None:
    history = opened()
    spec = dataclasses.replace(SPEC, max_steps=1, max_tokens=10)
    history.request()
    usage = Usage(input_uncached=10, cache_read=0, cache_write=0, output=5)
    history.reply(call("call_a"), reason="tool_calls", usage=usage)
    history.ran(call("call_a"))
    ending = move(history, spec=spec)
    assert isinstance(ending, EndRun)
    assert ending.limit == LimitHit("steps", 1, 1)


# A row the intake never lets in


@pytest.mark.parametrize(
    "odd",
    [
        dataclasses.replace(stop("s1"), command="pause"),
        dataclasses.replace(
            inbound("a1"), kind="approval_answer", approval_id="ap-1", answer="yes"
        ),
    ],
    ids=["another command", "an approval answer"],
)
def test_a_row_that_is_neither_a_message_nor_a_stop_is_a_bug(odd: Inbound) -> None:
    with pytest.raises(ValueError, match="neither a message nor a stop"):
        move(opened(), (row(1, odd),))


def test_used_seconds_on_the_limit_are_rounded_down() -> None:
    history = opened()
    history.request()
    history.reply(call("call_a"), reason="tool_calls", duration_ms=1_200_999)
    hit = LimitHit("seconds", 1_200, 1_200)
    assert move(history) == ended(
        "limit", "seconds", limit_notice("seconds", 1_200), hit
    )
