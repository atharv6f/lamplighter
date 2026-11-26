"""The view of a run, worked out from its saved events alone."""

import dataclasses

import pytest

from lamplighter.core.run_view import CallRecord, RunView, call_key, view_of
from lamplighter.ports import Event, Failure, TextPart, Usage
from tests.run_history import CONFIG, History, call, unparsed


def view(history: History) -> RunView:
    made = view_of(history.events)
    assert isinstance(made, RunView), made
    return made


def test_with_no_events_there_is_no_session_and_no_run() -> None:
    made = view(History())
    assert (made.last_seq, made.session_exists, made.run_id) == (0, False, None)
    assert made.event_ids == {}


def test_a_run_that_ended_leaves_no_open_run_but_its_ids_stay() -> None:
    history = History().start()
    history.answered()
    made = view(history)
    assert made.run_id is None
    assert made.step == 0
    assert made.last_seq == 7
    assert made.event_ids[history.events[2].event_id] == 3


def test_the_open_run_has_its_id_its_config_and_its_step() -> None:
    history = History().start(run_id="run-7")
    history.request()
    history.request()
    made = view(history)
    assert (made.run_id, made.config_id, made.step) == ("run-7", CONFIG, 2)
    assert made.last_reply is None


def test_only_the_open_run_counts_toward_what_was_used() -> None:
    history = History().start()
    history.request()
    history.reply(TextPart(text="One."), cost=400, duration_ms=5_000)
    history.ready()
    history.end()
    history.start(native_id="m2", run_id="run-2")
    history.request()
    usage = Usage(input_uncached=100, cache_read=None, cache_write=20, output=None)
    history.reply(call("call_a"), reason="tool_calls", usage=usage, duration_ms=2_000)
    history.intent(call("call_a"))
    history.result(call("call_a"), duration_ms=300)
    made = view(history)
    assert (made.step, made.used_ms, made.used_tokens) == (1, 2_300, 120)
    assert made.used_micro_dollars == 0


def test_money_is_the_sum_of_the_known_costs() -> None:
    history = History().start()
    for cost in (100, None, 250):
        history.request()
        history.reply(TextPart(text="x"), cost=cost)
    assert view(history).used_micro_dollars == 350


def test_open_calls_are_the_unanswered_calls_of_the_newest_whole_reply() -> None:
    history = History().start()
    history.step_with(call("call_a"), call("call_b", seconds=2))
    history.intent(call("call_a"))
    made = view(history)
    assert [c.call.id for c in made.open_calls] == ["call_a", "call_b"]
    assert made.open_calls[0].intent_id == history.events[-1].event_id
    assert made.open_calls[1].intent_id is None
    assert made.open_calls[0].reply_id == history.events[-2].event_id
    history.result(call("call_a"))
    assert [c.call.id for c in view(history).open_calls] == ["call_b"]


def test_a_bad_reply_after_a_whole_one_opens_no_call() -> None:
    history = History().start()
    history.step_with(call("call_a"))
    history.ran(call("call_a"))
    history.request()
    history.reply()
    made = view(history)
    assert made.open_calls == ()
    assert made.bad_streak == 1


def test_only_the_last_part_of_a_cut_reply_is_marked_cut() -> None:
    history = History().start()
    history.step_with(call("call_a"), call("call_b"), reason="max_output")
    assert [c.cut for c in view(history).open_calls] == [False, True]
    other = History().start()
    other.step_with(call("call_a"), TextPart(text="and"), reason="other")
    assert [c.cut for c in view(other).open_calls] == [False]
    whole = History().start()
    whole.step_with(call("call_a"), reason="tool_calls")
    assert [c.cut for c in view(whole).open_calls] == [False]


def test_the_bad_streak_counts_bad_replies_in_a_row_up_to_the_newest() -> None:
    history = History().start()
    history.request()
    history.reply()
    history.request()
    history.reply(TextPart(text="Fine."))
    history.request()
    history.reply(TextPart(text=" "))
    assert view(history).bad_streak == 1
    history.request()
    history.reply(TextPart(text="No."), reason="refused")
    assert view(history).bad_streak == 2


def test_calls_carry_their_key_reply_and_result_without_the_warning() -> None:
    history = History().start()
    reply = history.step_with(call("call_a"), unparsed("call_b"))
    history.ran(
        call("call_a"),
        "12:00\n\nNote from the harness: this call and its"
        " result are the same as the two before it. Making the same call"
        " again will end the run.",
    )
    made = view(history)
    assert made.calls == (
        CallRecord(
            key=("wait", "json", '{"seconds":1}'), reply_seq=reply.seq, result="12:00"
        ),
        CallRecord(
            key=("wait", "raw", '{"seconds": '), reply_seq=reply.seq, result=None
        ),
    )
    assert made.open_calls[0].index == 1


def test_call_ids_may_repeat_across_replies_and_still_match_their_own_reply() -> None:
    history = History().start()
    history.step_with(call("call_a"))
    history.ran(call("call_a"), "first")
    history.step_with(call("call_a"))
    made = view(history)
    assert [c.result for c in made.calls] == ["first", None]
    assert made.open_calls[0].intent_id is None


def test_the_ending_is_the_saved_reply_ready() -> None:
    history = History().start()
    history.request()
    history.ready("Stopped.", "cancelled")
    ending = view(history).ending
    assert ending is not None
    assert (ending.seq, ending.text, ending.kind) == (5, "Stopped.", "notice")
    assert (ending.run_reason, ending.run_detail) == ("cancelled", "")


def test_the_key_of_a_call_is_its_name_and_its_saved_arguments() -> None:
    assert call_key(call(seconds=2)) == ("wait", "json", '{"seconds":2}')
    assert call_key(unparsed()) == ("wait", "raw", '{"seconds": ')


def test_an_event_this_code_cannot_read_gives_needs_user() -> None:
    history = History().start()
    newer = dataclasses.replace(history.events[-1], v=2)
    made = view_of([*history.events[:-1], newer])
    assert isinstance(made, Failure)
    assert made.kind == "needs_user"


@pytest.mark.parametrize(
    "payload",
    [{"call_id": 3, "name": "wait"}, {"name": "wait"}],
)
def test_a_payload_of_the_open_run_that_does_not_fit_is_fatal(payload: object) -> None:
    history = History().start()
    history.step_with(call())
    bad: Event = history.add("tool_intent", payload)
    assert bad.type == "tool_intent"
    made = view_of(history.events)
    assert isinstance(made, Failure)
    assert made.kind == "fatal"


@pytest.mark.parametrize(
    ("kind", "reason"), [("letter", "done"), ("reply", "finished"), ("reply", 3)]
)
def test_an_ending_with_an_unknown_kind_or_reason_is_fatal(
    kind: str, reason: object
) -> None:
    history = History().start()
    history.add(
        "reply_ready",
        {"text": "Done.", "kind": kind, "run_reason": reason, "run_detail": ""},
    )
    made = view_of(history.events)
    assert isinstance(made, Failure)
    assert made.kind == "fatal"


def test_a_new_model_request_leaves_no_last_reply_until_its_own_is_saved() -> None:
    history = History().start()
    history.request()
    history.reply(TextPart(text="One."))
    assert view(history).last_reply is not None
    history.request()
    assert view(history).last_reply is None
